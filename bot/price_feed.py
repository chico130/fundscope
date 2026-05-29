"""
Price feed: Finnhub REST (primary) + yfinance (fallback) + disk cache (last resort).

Finnhub free tier: 60 req/min, real-time US stocks + international.
yfinance: quasi-real-time (~1-2 min delay), no API key required.
Disk cache: data/price_cache.json, max age 15 min — activated when both live sources fail.

Rate limiting: 1 req/sec (stays well under Finnhub's 60/min limit).
Cache TTL: 60 s during market hours, 1 h otherwise (weekends/after-hours).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import requests.exceptions as req_exc

from .config import FINNHUB_API_KEY
from .logger import log_error
from .retry_util import backoff_delay
from . import circuit_breaker, rate_limiter

_FINNHUB_RETRIES = 3
_RETRIABLE = (req_exc.ConnectTimeout, req_exc.ReadTimeout, req_exc.ConnectionError)

_FINNHUB_BASE = "https://finnhub.io/api/v1"
_MIN_INTERVAL = 1.05          # 1 req/sec → ~57/min (safe margin)
_CACHE_TTL_MARKET = 60        # seconds during market hours
_CACHE_TTL_OFFHOURS = 3_600   # 1 hour outside market hours

_PRICE_CACHE_PATH = Path(__file__).parent.parent / "data" / "price_cache.json"
_PRICE_CACHE_MAX_AGE = 900    # 15 minutes — max age for disk cache fallback

_last_request: float = 0.0
_cache: dict[str, dict] = {}  # symbol → quote dict with "_cached_at"
_disk_cache_alerted: bool = False  # rate-limit alert to at most one per process run


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
    Source is 'finnhub', 'yfinance', or 'disk_cache' (last resort, max 15 min old).
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
        _persist_price_cache(symbol, result)
        return result

    # Both live sources failed — try disk cache (max 15 min old)
    disk = _from_disk_cache(symbol)
    if disk:
        _on_disk_cache_fallback(symbol)
        disk["_cached_at"] = time.monotonic()
        _cache[symbol] = disk
        return disk

    return None


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
    if not rate_limiter.check_and_consume("finnhub"):
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


# ---------------------------------------------------------------------------
# Disk cache — persists last known good price; last resort when both live
# sources fail.  Writes are best-effort and never raise.
# ---------------------------------------------------------------------------

def _persist_price_cache(symbol: str, quote: dict) -> None:
    """Write latest successful quote to data/price_cache.json (best-effort)."""
    try:
        cache: dict = {}
        if _PRICE_CACHE_PATH.exists():
            try:
                cache = json.loads(_PRICE_CACHE_PATH.read_text(encoding="utf-8"))
                if not isinstance(cache, dict):
                    cache = {}
            except (json.JSONDecodeError, OSError):
                cache = {}
        entry = {k: v for k, v in quote.items() if k != "_cached_at"}
        entry["cached_at_iso"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache[symbol] = entry
        _PRICE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PRICE_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PRICE_CACHE_PATH)
    except Exception:
        pass


def _from_disk_cache(symbol: str) -> dict | None:
    """Return cached price for symbol if disk entry is < _PRICE_CACHE_MAX_AGE seconds old."""
    try:
        if not _PRICE_CACHE_PATH.exists():
            return None
        cache = json.loads(_PRICE_CACHE_PATH.read_text(encoding="utf-8"))
        entry = cache.get(symbol)
        if not entry:
            return None
        cached_at = datetime.fromisoformat(entry["cached_at_iso"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > _PRICE_CACHE_MAX_AGE:
            return None
        return {
            "price":      entry.get("price"),
            "prev_close": entry.get("prev_close"),
            "change_pct": entry.get("change_pct"),
            "timestamp":  entry.get("timestamp"),
            "source":     "disk_cache",
        }
    except Exception:
        return None


def _on_disk_cache_fallback(symbol: str) -> None:
    """Log + Telegram alert (once per process run) when disk cache is used."""
    global _disk_cache_alerted
    log_error("price_feed_disk_cache_fallback", {
        "symbol": symbol,
        "note":   "Both Finnhub and yfinance failed; using disk cache (max 15 min old)",
    })
    if not _disk_cache_alerted:
        _disk_cache_alerted = True
        try:
            from .notifier import enviar_alerta
            enviar_alerta(
                "⚠️ Price Feed — Disk Cache Fallback\n\n"
                f"Primeiro símbolo afectado: {symbol}\n"
                "Tanto Finnhub como yfinance falharam.\n"
                "A usar preços em cache (máx. 15 min). Verificar conectividade.",
                silencioso=False,
            )
        except Exception:
            pass
