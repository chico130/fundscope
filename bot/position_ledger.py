"""
Position ledger: tracks positions locally, independent of the T212 API.

Source of truth for position quantities and average prices.
P&L is calculated on-the-fly using price_feed (Finnhub → yfinance).

T212 sync: when the T212 API is reachable, positions are reconciled
(quantities and average prices updated). Never blocks the main cycle.

Ledger file: data/beta/positions_ledger.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_BETA_DIR
from . import price_feed
from .logger import log_error

LEDGER_PATH = DATA_BETA_DIR / "positions_ledger.json"
BETA_POSITIONS_PATH = DATA_BETA_DIR / "beta_positions.json"

# Maps T212 internal ticker prefix → Finnhub/yfinance symbol.
# Only needed for opaque T212 codes; standard tickers map to themselves.
_T212_OPAQUE: dict[str, str] = {
    "MTEd":  "MU",
    "49Vd":  "VST",
    "0V6d":  "VRT",
    "CJ6d":  "CCJ",
    "ASMLa": "ASML",
    "ARM":   "ARM",        # ARM Holdings (NASDAQ)
}

# European market suffix → Finnhub/yfinance suffix (same convention)
_MARKET_SUFFIX: dict[str, str] = {
    "GBP": ".L", "GBX": ".L",
    "DE":  ".DE", "FR": ".PA",
    "NL":  ".AS", "IT": ".MI",
    "ES":  ".MC", "PT": ".LS",
}


def _to_price_symbol(t212_ticker: str) -> str:
    """Convert a T212 ticker (or simplified ticker) to a Finnhub/yfinance symbol."""
    parts = t212_ticker.split("_")
    prefix = parts[0]
    if prefix in _T212_OPAQUE:
        return _T212_OPAQUE[prefix]
    market = parts[1] if len(parts) >= 2 else "US"
    return f"{prefix}{_MARKET_SUFFIX.get(market, '')}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_raw() -> dict:
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _seed_from_beta_positions()
    except json.JSONDecodeError:
        log_error("ledger_parse_error", {"path": str(LEDGER_PATH)})
        return {"last_updated": None, "last_t212_sync": None, "cash_eur": None, "positions": {}}


def _seed_from_beta_positions() -> dict:
    """Bootstrap ledger from beta_positions.json when no ledger exists yet."""
    ledger: dict = {
        "last_updated": None,
        "last_t212_sync": None,
        "cash_eur": None,
        "positions": {},
    }
    try:
        raw = json.loads(BETA_POSITIONS_PATH.read_text(encoding="utf-8"))
        for pos in raw.get("positions", []):
            ticker = pos.get("ticker", "")
            if not ticker:
                continue
            sym = _to_price_symbol(ticker)
            ledger["positions"][ticker] = {
                "ticker":        ticker,
                "price_symbol":  sym,
                "display_name":  pos.get("display_name", ticker),
                "quantity":      float(pos.get("quantity", 0)),
                "avg_price":     float(pos.get("avg_price", 0)),
                "currency":      "USD",
            }
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return ledger


def _save(ledger: dict) -> None:
    DATA_BETA_DIR.mkdir(parents=True, exist_ok=True)
    ledger["last_updated"] = datetime.now(timezone.utc).isoformat()
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sync_from_t212(t212_positions: list[dict], cash: dict | None = None) -> None:
    """Reconcilia o ledger com o portfolio T212 — T212 é a FONTE DE VERDADE.

    Substitui completamente o dict de positions: qualquer ticker que não venha
    no payload T212 é removido do ledger. Isto elimina posições-fantasma que
    persistiam quando uma venda externa não era reflectida no cache local.
    """
    ledger = _load_raw()

    new_positions: dict[str, dict] = {}
    for pos in t212_positions:
        ticker = pos.get("ticker", "")
        qty    = float(pos.get("quantity", 0) or pos.get("currentShares", 0))
        avg    = float(pos.get("averagePrice", 0) or pos.get("breakEvenPrice", 0))
        if not ticker or not qty:
            continue
        sym = _to_price_symbol(ticker)
        existing = ledger.get("positions", {}).get(ticker, {})
        new_positions[ticker] = {
            "ticker":       ticker,
            "price_symbol": sym,
            "display_name": existing.get("display_name") or pos.get("ticker", ticker),
            "quantity":     qty,
            "avg_price":    avg,
            "currency":     existing.get("currency", "USD"),
        }

    ledger["positions"] = new_positions

    if cash:
        ledger["cash_eur"] = float(cash.get("free", 0) or 0)

    ledger["last_t212_sync"] = datetime.now(timezone.utc).isoformat()
    _save(ledger)


def remove(ticker: str) -> None:
    """Remove a closed position from the ledger."""
    ledger = _load_raw()
    ledger["positions"].pop(ticker, None)
    _save(ledger)


def get_positions_with_prices() -> tuple[list[dict], dict]:
    """
    Returns (positions, cash) where each position has current price and P&L.

    Position shape (backward-compatible with phase0.py):
    {
        ticker, price_symbol, display_name, quantity, averagePrice,
        value, gain_eur, gain_pct,
        market_data: {last_price, previous_close, change_pct, source},
        price_stale: bool,
    }
    cash shape: {"free": <float>}
    """
    ledger = _load_raw()
    raw_positions = ledger.get("positions", {})

    if not raw_positions:
        cash = {"free": ledger.get("cash_eur") or 0.0}
        return [], cash

    symbols = [p["price_symbol"] for p in raw_positions.values()]
    quotes  = price_feed.get_quotes(symbols)

    positions: list[dict] = []
    for pos in raw_positions.values():
        sym      = pos["price_symbol"]
        quote    = quotes.get(sym)
        qty      = pos["quantity"]
        avg      = pos["avg_price"]
        cur      = quote["price"] if quote else None

        value    = round(cur * qty, 2)    if cur  is not None else None
        gain_eur = round((cur - avg) * qty, 2)      if cur is not None else None
        gain_pct = round((cur - avg) / avg * 100, 2) if cur and avg   else None

        positions.append({
            "ticker":       pos["ticker"],
            "price_symbol": sym,
            "display_name": pos.get("display_name", pos["ticker"]),
            "quantity":     qty,
            "averagePrice": avg,
            "value":        value,
            "gain_eur":     gain_eur,
            "gain_pct":     gain_pct,
            "market_data":  {
                "last_price":      cur,
                "previous_close":  quote.get("prev_close")  if quote else None,
                "change_pct":      quote.get("change_pct")  if quote else None,
                "source":          quote.get("source")       if quote else None,
            },
            "price_stale": quote is None,
        })

    cash = {"free": ledger.get("cash_eur") or 0.0}
    return positions, cash


def get_sync_status() -> dict:
    """Returns metadata about the last T212 sync."""
    ledger = _load_raw()
    return {
        "last_t212_sync": ledger.get("last_t212_sync"),
        "n_positions":    len(ledger.get("positions", {})),
        "cash_eur":       ledger.get("cash_eur"),
    }
