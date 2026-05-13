"""
Data layer: aggregates T212 portfolio state with technical indicators,
and provides read access to the data/beta/ JSON files.

Technical indicators are computed in pure Python to avoid heavy dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import api_client
from .config import DATA_BETA_DIR, RISK_CONFIG


# ---------------------------------------------------------------------------
# Portfolio state
# ---------------------------------------------------------------------------

def get_full_portfolio_state() -> dict | None:
    """Fetches T212 demo portfolio and enriches each position with a market snapshot.

    Returns None if the T212 API is unavailable (conservative default: do nothing).
    """
    state = api_client.get_portfolio_state_demo()
    if state is None:
        return None

    tickers = [p.get("ticker") for p in state.get("positions", []) if p.get("ticker")]
    if tickers:
        snapshot = api_client.get_market_snapshot(tickers)
        for pos in state["positions"]:
            ticker = pos.get("ticker")
            if ticker and ticker in snapshot:
                pos["market_data"] = snapshot[ticker]

    return state


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

        closes = [bar["close"] for bar in history]
        volumes = [bar["volume"] for bar in history]

        ema50 = compute_ema(closes, 50)
        ema200 = compute_ema(closes, 200)
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        last_vol = volumes[-1] if volumes else None

        pos["technicals"] = {
            "rsi_14": compute_rsi(closes),
            "ema50": ema50,
            "ema200": ema200,
            "ema50_above_ema200": (ema50 > ema200) if (ema50 is not None and ema200 is not None) else None,
            "volume_ratio_vs_avg": round(last_vol / avg_vol, 2) if (last_vol and avg_vol) else None,
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
