"""
Price feed: Finnhub REST (primary) + yfinance (fallback).

Finnhub free tier: 60 req/min, real-time US stocks + international.
yfinance: quasi-real-time (~1-2 min delay), no API key required.

Rate limiting: 1 req/sec (stays well under Finnhub's 60/min limit).
Cache TTL: 60 s during market hours, 1 h otherwise (weekends/after-hours).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests
import requests.exceptions as req_exc

from .config import FINNHUB_API_KEY
from .logger import log_error
from .retry_util import backoff_delay
from . import circuit_breaker

_FINNHUB_RETRIES = 3
_RETRIABLE = (req_exc.ConnectTimeout, req_exc.ReadTimeout, req_exc.ConnectionError)

_FINNHUB_BASE = "https://finnhub.io/api/v1"
_MIN_INTERVAL = 1.05          # 1 req/sec → ~57/min (safe margin)
_CACHE_TTL_MARKET = 60        # seconds during market hours
_CACHE_TTL_OFFHOURS = 3_600   # 1 hour outside market hours

_last_request: float = 0.0
_cache: dict[str, dict] = {}  # symbol → quote dict with "_cached_at"


# ---------------------------------------------------------------------------
# Market hours
# ---------------------------------------------------------------------------

def is_market_hours() -> bool:
    """True on weekdays between 14:30 and 21:00 UTC (US session)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    hour = now.hour + now.minute / 60.0
    return 14.5 <= hour <= 21.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_quote(symbol: str) -> dict | None:
    """
    Returns {price, prev_close, change_pct, source} or None on failure.
    Source is 'finnhub' or 'yfinance'.
    """
    cached = _cache.get(symbol)
    if cached:
        ttl = _CACHE_TTL_MARKET if is_market_hours() else _CACHE_TTL_OFFHOURS
        if time.monotonic() - cached["_cached_at"] < ttl:
            return cached

    result = _from_finnhub(symbol) or _from_yfinance(symbol)
    if result:
        result["_cached_at"] = time.monotonic()
        _cache[symbol] = result
    return result


def get_quotes(symbols: list[str]) -> dict[str, dict]:
    """Returns {symbol: quote} for all symbols. Missing ones are omitted."""
    out: dict[str, dict] = {}
    for sym in symbols:
        q = get_quote(sym)
        if q:
            out[sym] = q
    return out


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _rate_limit() -> None:
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _from_finnhub(symbol: str) -> dict | None:
    """Cotação Finnhub com retry + backoff exponencial (2s → 4s) em erros de rede.

    Erros transitórios (timeout/conexão) e 429 são retentados; respostas HTTP de
    lógica (símbolo inválido) ou payload vazio devolvem None de imediato para o
    fallback yfinance entrar sem demora. Base curta porque há fallback a seguir.
    """
    if not FINNHUB_API_KEY:
        return None
    if not circuit_breaker.allow("finnhub"):
        return None
    for attempt in range(_FINNHUB_RETRIES):
        try:
            _rate_limit()
            resp = requests.get(
                f"{_FINNHUB_BASE}/quote",
                params={"symbol": symbol, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            if resp.status_code == 429:
                if attempt < _FINNHUB_RETRIES - 1:
                    time.sleep(backoff_delay(attempt, base=2.0))
                    continue
                circuit_breaker.record_failure("finnhub", "429 rate-limit")
                log_error("price_feed_finnhub_failed", {"symbol": symbol, "error": "429 rate-limit", "attempts": _FINNHUB_RETRIES})
                return None
            resp.raise_for_status()
            d = resp.json()
            current = d.get("c")
            prev = d.get("pc")
            circuit_breaker.record_success("finnhub")
            if not current:  # Finnhub returns 0 for unavailable symbols
                return None
            return {
                "price": round(float(current), 4),
                "prev_close": round(float(prev), 4) if prev else None,
                "change_pct": round((current - prev) / prev * 100, 2) if prev else None,
                "timestamp": d.get("t"),
                "source": "finnhub",
            }
        except _RETRIABLE as exc:
            if attempt < _FINNHUB_RETRIES - 1:
                time.sleep(backoff_delay(attempt, base=2.0))
            else:
                circuit_breaker.record_failure("finnhub", str(exc))
                log_error("price_feed_finnhub_failed", {"symbol": symbol, "error": str(exc), "attempts": _FINNHUB_RETRIES})
                return None
        except Exception as exc:
            log_error("price_feed_finnhub_failed", {"symbol": symbol, "error": str(exc)})
            return None
    return None


def _from_yfinance(symbol: str) -> dict | None:
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).fast_info
        last = getattr(info, "last_price", None)
        prev = getattr(info, "previous_close", None)
        if not last:
            return None
        return {
            "price": round(float(last), 4),
            "prev_close": round(float(prev), 4) if prev else None,
            "change_pct": round((last - prev) / prev * 100, 2) if prev else None,
            "timestamp": None,
            "source": "yfinance",
        }
    except Exception as exc:
        log_error("price_feed_yfinance_failed", {"symbol": symbol, "error": str(exc)})
        return None
