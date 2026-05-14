import json
import logging
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from bot.config import DATA_BETA_DIR, WATCHLIST_CONFIG

logger = logging.getLogger(__name__)

WATCHLIST_PATH = DATA_BETA_DIR / "watchlist.json"

SECTOR_TICKERS: dict[str, list[str]] = {
    "XLK": [  # Technology
        "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "ORCL", "ADBE", "CRM", "CSCO", "INTC",
        "QCOM", "TXN", "NOW", "INTU", "IBM", "AMAT", "LRCX", "MU", "KLAC", "ADI",
        "SNPS", "CDNS", "FTNT", "PANW", "CRWD", "NET", "DDOG", "ZS", "MRVL", "HPE",
    ],
    "XLV": [  # Healthcare
        "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN",
        "SYK", "BSX", "ISRG", "MDT", "CVS", "CI", "HUM", "ELV", "VRTX", "REGN",
        "GILD", "BIIB", "IQV", "ZBH", "BDX", "BAX", "DXCM", "HOLX", "MTD", "A",
    ],
    "XLY": [  # Consumer Discretionary
        "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG",
        "ORLY", "AZO", "GM", "F", "ROST", "DHI", "LEN", "PHM", "NVR", "TOL",
        "EXPE", "ABNB", "LVS", "MGM", "WYNN", "HLT", "MAR", "H", "DRI", "YUM",
    ],
    "XLI": [  # Industrials
        "GE", "RTX", "HON", "UPS", "BA", "CAT", "DE", "LMT", "NOC", "GD",
        "MMM", "EMR", "ETN", "PH", "ROK", "FDX", "CSX", "NSC", "UNP", "WM",
        "RSG", "FAST", "GWW", "CTAS", "SWK", "IR", "XYL", "OTIS", "CARR", "TT",
    ],
    "XLE": [  # Energy
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "DVN",
        "FANG", "APA", "HAL", "BKR", "OKE", "WMB", "KMI",
        "TRGP", "LNG", "CVI", "SM", "RRC", "AR", "EQT", "CNX", "CTRA", "PR",
    ],
}

_TICKER_TO_SECTOR = {
    ticker: sector
    for sector, tickers in SECTOR_TICKERS.items()
    for ticker in tickers
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_stale() -> bool:
    if not WATCHLIST_PATH.exists():
        return True
    try:
        data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        last_updated = datetime.fromisoformat(data["last_updated"])
        age_days = (datetime.now(timezone.utc) - last_updated).days
        return age_days >= WATCHLIST_CONFIG["update_frequency_days"]
    except (KeyError, ValueError, OSError):
        return True


def _minmax_normalize(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.5, index=series.index)
    return (series - lo) / (hi - lo)


def _fetch_price_volume(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Batch download 3 months of daily Close and Volume for all tickers."""
    raw = yf.download(
        tickers,
        period="3mo",
        interval="1d",
        progress=False,
        auto_adjust=True,
        group_by="column",
    )
    # yfinance returns MultiIndex (field, ticker) for multiple tickers
    closes  = raw["Close"].dropna(how="all")
    volumes = raw["Volume"].dropna(how="all")
    return closes, volumes


def filter_quality(closes: pd.DataFrame, volumes: pd.DataFrame) -> list[str]:
    """Remove tickers below min price or min avg daily volume in USD."""
    min_price   = WATCHLIST_CONFIG["min_price_usd"]
    min_vol_usd = WATCHLIST_CONFIG["min_avg_volume_usd"]

    last_price   = closes.iloc[-1]
    avg_vol_usd  = (volumes * closes).mean()

    mask = (last_price >= min_price) & (avg_vol_usd >= min_vol_usd)
    return list(last_price[mask].dropna().index)


def _fetch_fundamentals(tickers: list[str]) -> dict[str, dict]:
    """Fetch ROE, D/E and revenue growth from yfinance.info — one request per ticker."""
    result: dict[str, dict] = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            result[ticker] = {
                "returnOnEquity":   info.get("returnOnEquity"),
                "debtToEquity":     info.get("debtToEquity"),
                "revenueGrowth":    info.get("revenueGrowth"),
                "targetMeanPrice":  info.get("targetMeanPrice"),
                "targetMedianPrice":info.get("targetMedianPrice"),
                "targetHighPrice":  info.get("targetHighPrice"),
                "targetLowPrice":   info.get("targetLowPrice"),
            }
        except Exception:
            result[ticker] = {}
        time.sleep(0.05)
    return result


def _quality_score(fund_data: dict[str, dict], tickers: list[str]) -> pd.Series:
    """Composite quality sub-score [0,1]: avg of ROE, inverted D/E, revenue growth."""
    roe = pd.Series({t: fund_data.get(t, {}).get("returnOnEquity") for t in tickers}, dtype=float)
    de  = pd.Series({t: fund_data.get(t, {}).get("debtToEquity")   for t in tickers}, dtype=float)
    rg  = pd.Series({t: fund_data.get(t, {}).get("revenueGrowth")  for t in tickers}, dtype=float)

    # Fill missing with median so no ticker is penalised for absent data
    for s in (roe, de, rg):
        s.fillna(s.median(), inplace=True)

    roe_norm = _minmax_normalize(roe.clip(-0.5, 0.5))
    de_norm  = _minmax_normalize(-de.clip(0.0, 3.0))   # lower D/E is better → negate before normalise
    rg_norm  = _minmax_normalize(rg.clip(-0.5, 0.5))

    return (roe_norm + de_norm + rg_norm) / 3


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_candidates(
    closes: pd.DataFrame,
    volumes: pd.DataFrame,
    fund_data: dict[str, dict],
    tickers: list[str],
) -> pd.DataFrame:
    """Calculate composite score = 0.4*mom1M + 0.3*mom3M + 0.2*liquidity + 0.1*quality."""
    weights = WATCHLIST_CONFIG["score_weights"]

    last_price = closes.iloc[-1][tickers]

    # ~21 and ~63 trading days for 1M and 3M momentum
    offset_1m = min(21, len(closes) - 1)
    offset_3m = min(63, len(closes) - 1)
    price_1m_ago = closes.iloc[-offset_1m][tickers]
    price_3m_ago = closes.iloc[-offset_3m][tickers]

    mom_1m = ((last_price - price_1m_ago) / price_1m_ago).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    mom_3m = ((last_price - price_3m_ago) / price_3m_ago).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    liq_usd = (volumes[tickers] * closes[tickers]).mean()

    mom_1m_norm = _minmax_normalize(mom_1m)
    mom_3m_norm = _minmax_normalize(mom_3m)
    liq_norm    = _minmax_normalize(liq_usd)
    qual_norm   = _quality_score(fund_data, tickers)

    composite = (
        weights["momentum_1m"] * mom_1m_norm
        + weights["momentum_3m"] * mom_3m_norm
        + weights["liquidity"]   * liq_norm
        + weights["quality"]     * qual_norm
    )

    target_mean   = [fund_data.get(t, {}).get("targetMeanPrice")   for t in tickers]
    target_median = [fund_data.get(t, {}).get("targetMedianPrice") for t in tickers]
    target_high   = [fund_data.get(t, {}).get("targetHighPrice")   for t in tickers]
    target_low    = [fund_data.get(t, {}).get("targetLowPrice")    for t in tickers]

    return pd.DataFrame({
        "ticker":         tickers,
        "sector":         [_TICKER_TO_SECTOR.get(t, "UNKNOWN") for t in tickers],
        "price":          last_price.round(2).values,
        "mom_1m":         mom_1m.round(4).values,
        "mom_3m":         mom_3m.round(4).values,
        "liq_usd_avg":    liq_usd.round(0).values,
        "score":          composite.round(4).values,
        "target_mean":    target_mean,
        "target_median":  target_median,
        "target_high":    target_high,
        "target_low":     target_low,
    }).sort_values("score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Earnings summary (yfinance fallback for watchlist.html Earnings AI tab)
# ---------------------------------------------------------------------------

def _compute_earnings_summary(tickers: list[str]) -> dict:
    result: dict = {}
    for ticker in tickers:
        try:
            t     = yf.Ticker(ticker)
            edf   = t.earnings_dates
            if edf is None or edf.empty:
                result[ticker] = None
                continue

            reported_col  = next((c for c in edf.columns if "reported" in c.lower()), None)
            estimate_col  = next((c for c in edf.columns if "estimate" in c.lower()), None)
            surprise_col  = next((c for c in edf.columns if "surprise" in c.lower()), None)

            if reported_col and estimate_col:
                edf = edf.dropna(subset=[reported_col]).sort_index(ascending=False).head(4)
            else:
                result[ticker] = None
                continue

            if edf.empty:
                result[ticker] = None
                continue

            beats  = int((edf[reported_col] > edf[estimate_col]).sum())
            total  = len(edf)
            misses = total - beats

            last_reported = float(edf.iloc[0][reported_col])
            last_est      = float(edf.iloc[0][estimate_col]) if pd.notna(edf.iloc[0][estimate_col]) else None
            last_surprise = float(edf.iloc[0][surprise_col]) if surprise_col and pd.notna(edf.iloc[0][surprise_col]) else None

            correu_bem: list[str] = []
            correu_mal: list[str] = []
            vigiar: list[str]    = ["Acompanhar próximos earnings para confirmação de tendência"]

            if beats >= 3:
                correu_bem.append(f"Bateu as estimativas em {beats} dos últimos {total} trimestres")
            if last_surprise is not None and last_surprise > 0:
                correu_bem.append(f"Último earnings {last_surprise:+.1f}% acima da estimativa")
            elif last_surprise is not None and last_surprise < -5:
                correu_mal.append(f"Último earnings {last_surprise:+.1f}% abaixo da estimativa")
            if misses >= 2:
                correu_mal.append(f"Falhou as estimativas em {misses} dos últimos {total} trimestres")

            tone  = "maioritariamente positivo" if beats >= 3 else ("misto" if beats >= 2 else "maioritariamente fraco")
            resumo = f"Histórico de earnings {tone} ({beats}/{total} beats)"

            result[ticker] = {
                "analysis": {
                    "resumo":     resumo,
                    "correu_bem": correu_bem or ["Dados insuficientes para análise"],
                    "correu_mal": correu_mal,
                    "vigiar":     vigiar,
                }
            }
        except Exception:
            result[ticker] = None
        time.sleep(0.05)
    return result


def _save_earnings_ai(data: dict) -> None:
    DATA_BETA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_BETA_DIR / "earnings_ai.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_watchlist(candidates: list[dict]) -> None:
    DATA_BETA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "count":        len(candidates),
        "candidates":   candidates,
    }
    WATCHLIST_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_watchlist() -> list[dict]:
    """Return watchlist candidates. Loads from cache if file is fresh; rebuilds otherwise."""
    if not _is_stale():
        logger.info("Watchlist is fresh — loading from cache.")
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))["candidates"]

    logger.info("Rebuilding watchlist...")
    all_tickers = [t for tickers in SECTOR_TICKERS.values() for t in tickers]

    closes, volumes = _fetch_price_volume(all_tickers)

    eligible = filter_quality(closes, volumes)
    logger.info("After quality filter: %d/%d tickers eligible", len(eligible), len(all_tickers))

    fund_data = _fetch_fundamentals(eligible)

    scored    = score_candidates(closes, volumes, fund_data, eligible)
    top       = scored.head(WATCHLIST_CONFIG["max_size"])
    candidates = top.to_dict(orient="records")

    _save_watchlist(candidates)
    logger.info("Watchlist saved: %d candidates → %s", len(candidates), WATCHLIST_PATH)

    earnings = _compute_earnings_summary([c["ticker"] for c in candidates])
    _save_earnings_ai(earnings)
    logger.info("Earnings AI summary saved for %d tickers", len(earnings))

    return candidates
