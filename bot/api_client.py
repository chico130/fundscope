"""
Trading212 demo API client + yfinance fallback for market/historical data.

T212 API base: https://demo.trading212.com/api/v0
Auth header:   Authorization: <T212_API_KEY_DEMO>

Historical OHLCV and market snapshots use yfinance because T212's public API
does not expose a price-quote or candlestick endpoint for arbitrary tickers.
"""
from __future__ import annotations

import time
import requests

from .config import (
    T212_API_KEY_DEMO,
    T212_BASE_URL_DEMO,
    LIVE_TRADING,
    REQUEST_DELAY_SECONDS,
)

_session = requests.Session()
_session.headers.update({
    "Authorization": T212_API_KEY_DEMO,
    "Content-Type": "application/json",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(endpoint: str) -> dict | list | None:
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        resp = _session.get(f"{T212_BASE_URL_DEMO}{endpoint}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        from .logger import log_error
        log_error("api_get_failed", {"endpoint": endpoint, "error": str(exc)})
        return None


def _post(endpoint: str, payload: dict) -> dict | None:
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        resp = _session.post(f"{T212_BASE_URL_DEMO}{endpoint}", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        from .logger import log_error
        log_error("api_post_failed", {"endpoint": endpoint, "payload": payload, "error": str(exc)})
        return None


def _delete(endpoint: str) -> bool:
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        resp = _session.delete(f"{T212_BASE_URL_DEMO}{endpoint}", timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        from .logger import log_error
        log_error("api_delete_failed", {"endpoint": endpoint, "error": str(exc)})
        return False


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_portfolio_state_demo() -> dict | None:
    """Returns combined positions + cash balance from T212 demo account.

    Shape: {"positions": [...], "cash": {...}}
    Returns None if either call fails (conservative: treat as no data).
    """
    positions = _get("/equity/portfolio")
    cash = _get("/equity/account/cash")
    if positions is None or cash is None:
        return None
    return {"positions": positions, "cash": cash}


def get_market_snapshot(tickers: list[str]) -> dict[str, dict]:
    """Returns {ticker: {last_price, previous_close, change_pct}} via yfinance.

    Any ticker that fails is omitted from the result rather than crashing.
    """
    try:
        import yfinance as yf
    except ImportError:
        from .logger import log_error
        log_error("missing_dependency", {"package": "yfinance", "pip": "pip install yfinance"})
        return {}

    result: dict[str, dict] = {}
    for ticker in tickers:
        try:
            time.sleep(0.25)
            info = yf.Ticker(ticker).fast_info
            last = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)
            change_pct = None
            if last is not None and prev:
                change_pct = round((last - prev) / prev * 100, 2)
            result[ticker] = {
                "last_price": last,
                "previous_close": prev,
                "change_pct": change_pct,
            }
        except Exception as exc:
            from .logger import log_error
            log_error("market_snapshot_ticker_failed", {"ticker": ticker, "error": str(exc)})

    return result


def get_historical_data(ticker: str, days: int = 60) -> list[dict]:
    """Returns daily OHLCV bars for ticker via yfinance.

    Shape per bar: {date, open, high, low, close, volume}
    Returns [] on failure so callers can check len() against min_data_points_required.
    """
    try:
        import yfinance as yf
    except ImportError:
        from .logger import log_error
        log_error("missing_dependency", {"package": "yfinance", "pip": "pip install yfinance"})
        return []

    try:
        df = yf.Ticker(ticker).history(period=f"{days}d", interval="1d")
        if df.empty:
            return []
        records = []
        for dt, row in df.iterrows():
            records.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        return records
    except Exception as exc:
        from .logger import log_error
        log_error("historical_data_failed", {"ticker": ticker, "days": days, "error": str(exc)})
        return []


def place_order_demo(
    ticker: str,
    side: str,
    qty: float,
    order_type: str,
    price: float | None = None,
) -> dict | None:
    """Places a BUY or SELL order on T212 demo account.

    ticker:     T212 instrument ticker (e.g. "AAPL_US_EQ")
    side:       "BUY" or "SELL"
    qty:        absolute quantity (positive)
    order_type: "MARKET" or "LIMIT"
    price:      required for LIMIT orders

    T212 convention: positive qty = buy, negative qty = sell.
    Returns the T212 order response dict, or None on failure.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    side = side.upper()
    order_type = order_type.upper()
    signed_qty = abs(qty) if side == "BUY" else -abs(qty)

    if order_type == "MARKET":
        return _post("/equity/orders/market", {
            "ticker": ticker,
            "quantity": signed_qty,
            "timeValidity": "DAY",
        })

    if order_type == "LIMIT":
        if price is None:
            from .logger import log_error
            log_error("place_order_missing_price", {"ticker": ticker, "side": side})
            return None
        return _post("/equity/orders/limit", {
            "ticker": ticker,
            "quantity": signed_qty,
            "limitPrice": price,
            "timeValidity": "DAY",
        })

    from .logger import log_error
    log_error("place_order_unknown_type", {"order_type": order_type, "ticker": ticker})
    return None


def cancel_order_demo(order_id: str | int) -> bool:
    """Cancels an active order by ID on T212 demo account. Returns True on success."""
    return _delete(f"/equity/orders/{order_id}")
