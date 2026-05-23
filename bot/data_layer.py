"""
Data layer: aggregates portfolio state with technical indicators,
and provides read access to the data/beta/ JSON files.

Price data: Finnhub REST (real-time) → yfinance (fallback). No T212 dependency.
T212 API: called opportunistically for position sync; never blocks the cycle.
Technical indicators: pure Python from yfinance historical bars.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import api_client, position_ledger
from .config import DATA_BETA_DIR, RISK_CONFIG
from .logger import log_decision

# SPY closes cached for the process lifetime (one fetch per cycle).
_SPY_CLOSES: list[float] | None = None


def _get_spy_closes(days: int = 230) -> list[float] | None:
    """Fetch SPY historical closes, cached for the process lifetime.

    Returns None silently when SPY data is unavailable (network error, etc.).
    """
    global _SPY_CLOSES
    if _SPY_CLOSES is not None:
        return _SPY_CLOSES
    try:
        history = api_client.get_historical_data("SPY", days=days)
        if len(history) < 21:
            return None
        _SPY_CLOSES = [bar["close"] for bar in history]
        return _SPY_CLOSES
    except Exception:
        return None


def _compute_rs_bullish(closes: list[float], spy_closes: list[float]) -> bool | None:
    """True if the stock's RS ratio (Close/SPY) is above its own EMA-20.

    Aligns both series by their most recent N bars to handle length mismatches.
    Returns None when there are insufficient bars to compute the EMA-20.
    """
    n = min(len(closes), len(spy_closes))
    if n < 21:
        return None
    rs = [
        closes[-n + i] / spy_closes[-n + i]
        for i in range(n)
        if spy_closes[-n + i] != 0
    ]
    if len(rs) < 21:
        return None
    rs_ema20 = compute_ema(rs, 20)
    if rs_ema20 is None:
        return None
    return rs[-1] > rs_ema20


# ---------------------------------------------------------------------------
# Portfolio state
# ---------------------------------------------------------------------------

def get_full_portfolio_state() -> dict:
    """Returns portfolio state usando T212 como FONTE DE VERDADE.

    Cada ciclo faz GET /equity/portfolio + /equity/account/cash à T212 e reconcilia
    o ledger local (remove posições que já não existem na conta). Se a chamada
    T212 falhar, devolve o último estado conhecido com a flag `t212_sync_failed=True`
    para que o caller saiba que os dados podem estar desactualizados.

    Always returns a dict (never None); positions list may be empty.
    """
    sync_ok = _sync_from_t212_strict()

    positions, cash = position_ledger.get_positions_with_prices()

    stale = [p["ticker"] for p in positions if p.get("price_stale")]
    if stale:
        log_decision("price_feed_stale", "some_prices_unavailable", {"tickers": stale})

    return {
        "positions":         positions,
        "cash":              cash,
        "t212_sync_failed":  not sync_ok,
    }


def _sync_from_t212_strict() -> bool:
    """Sincroniza o ledger com o T212. Devolve True se sucesso, False se falha.

    Ao contrário da versão antiga, NÃO salta em fim-de-semana / fora de horas:
    a fonte de verdade tem de ser consultada sempre, mesmo que devolva o
    snapshot da última sessão. Falhar silenciosamente leva a decisões com base
    em dados velhos (posições-fantasma, cash incorrecto).
    """
    from .logger import log_error
    try:
        state = api_client.get_portfolio_state_demo()
        if state is None:
            log_error("t212_sync_no_response", {
                "note": "get_portfolio_state_demo retornou None — usar ledger cached",
            })
            return False
        t212_positions = state.get("positions", [])
        t212_cash      = state.get("cash", {})
        position_ledger.sync_from_t212(t212_positions, t212_cash)
        log_decision("t212_sync_ok", "ledger_reconciled",
                     {"n_positions": len(t212_positions)})
        return True
    except Exception as exc:
        log_error("t212_sync_exception", {"error": str(exc)})
        return False


def enrich_with_technicals(positions: list[dict], days: int = 60) -> list[dict]:
    """Adds a 'technicals' dict to each position with RSI-14, EMA-50, EMA-200,
    and volume_ratio_vs_avg (last bar vs 20-day average).

    Requests at least 210 days of history to compute EMA-200 reliably.
    Sets technicals=None when there are fewer than min_data_points_required bars.
    """
    min_pts = RISK_CONFIG["min_data_points_required"]
    fetch_days = max(days, 210)

    for pos in positions:
        ticker = pos.get("ticker")
        if not ticker:
            pos["technicals"] = None
            continue

        history = api_client.get_historical_data(ticker, days=fetch_days)
        if len(history) < min_pts:
            pos["technicals"] = None
            continue

        highs   = [bar["high"]   for bar in history]
        lows    = [bar["low"]    for bar in history]
        closes  = [bar["close"]  for bar in history]
        volumes = [bar["volume"] for bar in history]

        ema20  = compute_ema(closes, 20)
        ema50  = compute_ema(closes, 50)
        ema200 = compute_ema(closes, 200)
        avg_vol    = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_sma_10 = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else None
        last_vol   = volumes[-1] if volumes else None

        spy_closes = _get_spy_closes(fetch_days)
        rs_bullish = _compute_rs_bullish(closes, spy_closes) if spy_closes else None

        pos["technicals"] = {
            "rsi_14":              compute_rsi(closes),
            "ema_20":              ema20,
            "ema50":               ema50,
            "ema200":              ema200,
            "ema20_above_ema50":   (ema20 > ema50) if (ema20 is not None and ema50 is not None) else None,
            "ema50_above_ema200":  (ema50 > ema200) if (ema50 is not None and ema200 is not None) else None,
            "price_above_ema20":   (closes[-1] > ema20) if (ema20 is not None and closes) else None,
            "volume_ratio_vs_avg": round(last_vol / avg_vol, 2) if (last_vol and avg_vol) else None,
            "volume_sma_10":       round(vol_sma_10, 0) if vol_sma_10 is not None else None,
            "volume_ratio":        round(last_vol / vol_sma_10, 2) if (last_vol and vol_sma_10) else None,
            "atr_14":              compute_atr(highs, lows, closes),
            "last_price":          closes[-1] if closes else None,
            "rs_bullish":          rs_bullish,
        }

    return positions


# ---------------------------------------------------------------------------
# Technical indicators (pure Python, no external dependencies)
# ---------------------------------------------------------------------------

def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns None when there are fewer than period+1 data points."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_ema(closes: list[float], period: int) -> float | None:
    """Exponential Moving Average. Returns None when there are fewer than period bars."""
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 4)


def compute_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Wilder's ATR (Average True Range). Requires at least period+1 bars."""
    if len(highs) < period + 1:
        return None
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


# ---------------------------------------------------------------------------
# Watchlist candidate technicals
# ---------------------------------------------------------------------------

def fetch_single_ticker(ticker: str) -> dict | None:
    """Fetch technical indicators for a single ticker via yfinance.

    Returns a dict compatible with strategy.generate_signals() market_data:
      {"technicals": {...}, "last_price": float}
    Returns None when data is insufficient or the fetch fails.
    """
    min_pts = RISK_CONFIG["min_data_points_required"]
    try:
        history = api_client.get_historical_data(ticker, days=210)
        if len(history) < min_pts:
            return None

        highs   = [bar["high"]   for bar in history]
        lows    = [bar["low"]    for bar in history]
        closes  = [bar["close"]  for bar in history]
        volumes = [bar["volume"] for bar in history]

        ema20  = compute_ema(closes, 20)
        ema50  = compute_ema(closes, 50)
        ema200 = compute_ema(closes, 200)
        avg_vol    = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_sma_10 = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else None
        last_vol   = volumes[-1] if volumes else None

        spy_closes = _get_spy_closes(210)
        rs_bullish = _compute_rs_bullish(closes, spy_closes) if spy_closes else None

        return {
            "technicals": {
                "rsi_14":              compute_rsi(closes),
                "ema_20":              ema20,
                "ema50":               ema50,
                "ema200":              ema200,
                "ema20_above_ema50":   (ema20 > ema50) if (ema20 is not None and ema50 is not None) else None,
                "ema50_above_ema200":  (ema50 > ema200) if (ema50 is not None and ema200 is not None) else None,
                "price_above_ema20":   (closes[-1] > ema20) if (ema20 is not None and closes) else None,
                "volume_ratio_vs_avg": round(last_vol / avg_vol, 2) if (last_vol and avg_vol) else None,
                "volume_sma_10":       round(vol_sma_10, 0) if vol_sma_10 is not None else None,
                "volume_ratio":        round(last_vol / vol_sma_10, 2) if (last_vol and vol_sma_10) else None,
                "atr_14":              compute_atr(highs, lows, closes),
                "last_price":          closes[-1] if closes else None,
                "rs_bullish":          rs_bullish,
            },
            "last_price": closes[-1] if closes else None,
        }
    except Exception:
        return None


def fetch_candidate_market_data(tickers: list[str]) -> dict[str, dict]:
    """Fetch technical indicators for watchlist candidate tickers (parallel).

    Returns a market_data dict compatible with strategy.generate_signals():
      {ticker: {"technicals": {...}, "last_price": float}}

    Uses ThreadPoolExecutor for parallel yfinance fetches. Tickers that fail or
    return insufficient data are silently omitted (partial-success semantics).

    Note: for throttled/streaming fetches use WatchlistThrottler.stream()
    with fetch_single_ticker instead.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .config import SCAN_WORKERS_YF

    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS_YF) as pool:
        futures = {pool.submit(fetch_single_ticker, t): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            data = future.result()
            if data is not None:
                result[ticker] = data

    return result


# ---------------------------------------------------------------------------
# Beta JSON readers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        from .logger import log_error
        log_error("json_parse_error", {"path": str(path)})
        return None


def read_beta_summary() -> dict | None:
    return _read_json(DATA_BETA_DIR / "beta_summary.json")


def read_beta_positions() -> dict | None:
    return _read_json(DATA_BETA_DIR / "beta_positions.json")


def read_beta_equity() -> dict | None:
    return _read_json(DATA_BETA_DIR / "beta_equity.json")


def read_beta_trades() -> dict | None:
    return _read_json(DATA_BETA_DIR / "beta_trades.json")
