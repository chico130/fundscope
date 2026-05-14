import json
import logging
from datetime import datetime, timezone
from typing import Literal

import pandas as pd
import yfinance as yf

from bot.config import DATA_BETA_DIR, REGIME_CONFIG

logger = logging.getLogger(__name__)

REGIME_PATH = DATA_BETA_DIR / "regime.json"

Regime = Literal["bull_trending", "bull_lateral", "bear_correction", "bear_capitulation"]


# ---------------------------------------------------------------------------
# Technical helpers
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(
    spy_pct_from_ema200: float,
    breadth_healthy: bool,
    atr_ratio: float,
    spy_return_20d: float,
) -> Regime:
    """
    Decision tree based on REGIME_CONFIG thresholds.

    lateral_atr_multiplier (0.8) is used inverted: ATR ratio above 1/0.8 = 1.25
    signals elevated volatility → lateral or worse conditions.
    """
    bear_thresh    = REGIME_CONFIG["bear_threshold_spy_ema200_pct"]   # -5.0
    atr_chop_thresh = 1.0 / REGIME_CONFIG["lateral_atr_multiplier"]   # 1.0/0.8 = 1.25

    # --- Bear zone ---
    if spy_pct_from_ema200 <= bear_thresh:
        if atr_ratio > 2.0 or spy_return_20d < -0.10:
            return "bear_capitulation"
        return "bear_correction"

    # --- Borderline zone (between bear_thresh and 0%) ---
    if spy_pct_from_ema200 < 0.0:
        return "bull_lateral"

    # --- Bull zone (SPY above EMA-200) ---
    if breadth_healthy and atr_ratio <= atr_chop_thresh:
        return "bull_trending"
    return "bull_lateral"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_market_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Download 1 year of daily OHLCV for SPY and RSP.

    RSP (Invesco S&P 500 Equal Weight ETF) is the breadth proxy:
    when RSP keeps up with cap-weighted SPY, participation is broad.
    Falls back to SPY-only breadth (breadth assumed neutral) if RSP download fails.
    """
    raw = yf.download(
        ["SPY", "RSP"],
        period="1y",
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    spy = pd.DataFrame({
        "Close":  raw["Close"]["SPY"],
        "High":   raw["High"]["SPY"],
        "Low":    raw["Low"]["SPY"],
    }).dropna()

    if spy.empty:
        raise RuntimeError("SPY data returned empty from yfinance — cannot classify regime.")

    rsp_close = raw["Close"]["RSP"].dropna()
    if rsp_close.empty:
        # RSP unavailable: use SPY itself so ratio stays flat (breadth = neutral)
        logger.warning("RSP download returned empty — using SPY as fallback breadth proxy.")
        rsp_close = spy["Close"].copy()

    return spy, rsp_close


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_current_regime() -> Regime:
    """
    Classify current market regime into one of four states.

    Inputs (all via yfinance, no extra APIs):
      - SPY Close/High/Low for EMA-200, ATR, and 20-day return
      - RSP/SPY ratio trend as breadth proxy
        (RSP = equal-weight S&P 500; rising ratio = broad participation)

    bull_breadth_threshold_pct from REGIME_CONFIG represents healthy breadth.
    Here it maps to RSP/SPY ratio not losing more than 2% relative to SPY
    over the last 20 days (qualitative proxy, not the raw 60% figure).

    Falls back to the last cached regime (or bear_correction as safety default)
    if yfinance download fails completely.
    """
    try:
        spy, rsp_close = _fetch_market_data()
    except Exception as exc:
        logger.error("_fetch_market_data failed (%s) — activating safe fallback.", exc)
        cached = load_cached_regime()
        if cached:
            logger.warning("Using last cached regime: %s", cached)
            return cached
        logger.warning("No cached regime available — defaulting to bear_correction (safe mode).")
        return "bear_correction"

    spy_close = spy["Close"]
    spy_last  = float(spy_close.iloc[-1])

    # SPY vs its EMA-200
    ema200 = float(_ema(spy_close, 200).iloc[-1])
    spy_pct_from_ema200 = (spy_last - ema200) / ema200 * 100.0

    # ATR ratio: current ATR-14 vs its own 60-day rolling mean
    atr_series   = _atr(spy["High"], spy["Low"], spy_close)
    atr_current  = float(atr_series.iloc[-1])
    atr_mean_60d = float(atr_series.iloc[-60:].mean())
    atr_ratio    = atr_current / atr_mean_60d if atr_mean_60d > 0 else 1.0

    # SPY 20-day return
    spy_return_20d = (
        (spy_last - float(spy_close.iloc[-20])) / float(spy_close.iloc[-20])
        if len(spy_close) >= 20
        else 0.0
    )

    # Breadth proxy: RSP/SPY ratio change over 20 days
    aligned       = pd.concat([spy_close, rsp_close], axis=1, keys=["SPY", "RSP"]).dropna()
    ratio         = aligned["RSP"] / aligned["SPY"]
    ratio_now     = float(ratio.iloc[-1])
    ratio_20d_ago = float(ratio.iloc[-20]) if len(ratio) >= 20 else ratio_now
    ratio_chg_20d = (ratio_now - ratio_20d_ago) / ratio_20d_ago

    # RSP is healthy if it hasn't lost more than 2% relative to SPY over 20 days
    breadth_healthy = ratio_chg_20d >= -0.02

    regime = _classify(spy_pct_from_ema200, breadth_healthy, atr_ratio, spy_return_20d)

    metrics = {
        "spy_price":              round(spy_last, 2),
        "ema200":                 round(ema200, 2),
        "spy_pct_from_ema200":    round(spy_pct_from_ema200, 2),
        "atr_current":            round(atr_current, 4),
        "atr_ratio_vs_60d_mean":  round(atr_ratio, 4),
        "spy_return_20d":         round(spy_return_20d, 4),
        "rsp_spy_ratio_chg_20d":  round(ratio_chg_20d, 4),
        "breadth_healthy":        breadth_healthy,
    }
    _save_regime(regime, metrics)

    logger.info(
        "Regime: %s | SPY %.1f%% from EMA-200 | ATR ratio %.2f | breadth_healthy=%s",
        regime, spy_pct_from_ema200, atr_ratio, breadth_healthy,
    )
    return regime


def load_cached_regime() -> Regime | None:
    """Return the last persisted regime without any network calls."""
    try:
        data = json.loads(REGIME_PATH.read_text(encoding="utf-8"))
        return data["regime"]
    except (OSError, KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_regime(regime: Regime, metrics: dict) -> None:
    DATA_BETA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "regime":       regime,
        "metrics":      metrics,
    }
    REGIME_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
