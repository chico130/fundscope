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
import requests.exceptions as req_exc

from .config import (
    T212_DEMO_KEY,
    T212_BASE_URL_DEMO,
    LIVE_TRADING,
    REQUEST_DELAY_SECONDS,
)
from .retry_util import backoff_delay

# T212 ticker suffix → yfinance market suffix
_T212_MARKET_SUFFIX: dict[str, str] = {
    "US": "",
    "GBP": ".L",
    "GBX": ".L",
    "DE": ".DE",
    "FR": ".PA",
    "NL": ".AS",
    "IT": ".MI",
    "ES": ".MC",
    "SE": ".ST",
    "DK": ".CO",
    "NO": ".OL",
    "FI": ".HE",
    "PT": ".LS",
}

# T212 opaque prefix → correct yfinance symbol (for codes where parts[0] is garbage)
_T212_OPAQUE_TO_YF: dict[str, str] = {
    "MTEd":  "MU",
    "49Vd":  "VST",
    "0V6d":  "VRT",
    "CJ6d":  "CCJ",
    "ASMLa": "ASML.AS",  # Euronext Amsterdam (EUR), não NASDAQ
}


def _t212_to_yfinance(ticker: str) -> str:
    """Convert T212 ticker (e.g. GOOGL_US_EQ, VUSA_GBP_ETF) to yfinance symbol."""
    parts = ticker.split("_")
    clean = parts[0]
    if clean in _T212_OPAQUE_TO_YF:
        return _T212_OPAQUE_TO_YF[clean]
    market = parts[1] if len(parts) >= 2 else "US"
    return f"{clean}{_T212_MARKET_SUFFIX.get(market, '')}"


_session = requests.Session()
_session.headers.update({
    "Authorization": T212_DEMO_KEY,
    "Content-Type": "application/json",
})

# Erros de rede transitórios — elegíveis para retry em operações idempotentes
_RETRIABLE = (
    req_exc.ConnectTimeout,
    req_exc.ReadTimeout,
    req_exc.ConnectionError,
)
_MAX_RETRY = 3
# Espera entre tentativas calculada por retry_util.backoff_delay (5s → 10s → 20s).


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, req_exc.ConnectTimeout):
        return "connection_timeout"
    if isinstance(exc, req_exc.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, req_exc.ConnectionError):
        return "connection_refused"
    return "unknown"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(endpoint: str) -> dict | list | None:
    """GET idempotente com retry em erros de rede transitórios (máx. 3 tentativas)."""
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    from .logger import log_error

    for attempt in range(_MAX_RETRY):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            resp = _session.get(f"{T212_BASE_URL_DEMO}{endpoint}", timeout=30)
            if resp.status_code == 429:
                wait = 30
                print(f"[api] GET {endpoint} — 429 rate-limit, a aguardar {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except _RETRIABLE as exc:
            if attempt < _MAX_RETRY - 1:
                wait = backoff_delay(attempt)
                print(f"[api] GET {endpoint} — {type(exc).__name__}, retry {attempt + 1}/{_MAX_RETRY} em {wait:.0f}s")
                time.sleep(wait)
            else:
                log_error("api_get_failed", {
                    "endpoint":   endpoint,
                    "error":      str(exc),
                    "error_type": _classify_error(exc),
                    "attempts":   _MAX_RETRY,
                })
                return None
        except Exception as exc:
            log_error("api_get_failed", {
                "endpoint":   endpoint,
                "error":      str(exc),
                "error_type": _classify_error(exc),
            })
            return None
    return None


def _post(endpoint: str, payload: dict) -> dict | None:
    """POST de ordens — SEM retry deliberado para evitar ordens duplicadas.

    Uma falha de rede no momento exacto de uma ordem é ambígua: a ordem pode
    ter chegado ao servidor ou não. Retentar arriscaria executar a mesma ordem
    duas vezes. Retorna None e deixa o ciclo seguinte reconciliar o estado.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    from .logger import log_error

    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        resp = _session.post(f"{T212_BASE_URL_DEMO}{endpoint}", json=payload, timeout=30)
        # Captura body antes do raise_for_status — T212 devolve detalhe do erro em JSON
        body_preview = resp.text[:600] if resp.text else ""
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        err_str = str(exc)
        status_code: int | None = getattr(getattr(exc, "response", None), "status_code", None)
        body_preview = locals().get("body_preview", "")
        log_error("api_post_failed", {
            "endpoint":     endpoint,
            "payload":      payload,
            "error":        err_str,
            "error_type":   _classify_error(exc),
            "status_code":  status_code,
            "response_body": body_preview,
        })
        print(
            f"[T212] POST {endpoint} falhou (HTTP {status_code}): {err_str}\n"
            f"       payload={payload}\n"
            f"       response={body_preview}",
            flush=True,
        )
        return None


def _delete(endpoint: str) -> bool:
    """DELETE idempotente com retry em erros de rede transitórios (máx. 3 tentativas)."""
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    from .logger import log_error

    for attempt in range(_MAX_RETRY):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            resp = _session.delete(f"{T212_BASE_URL_DEMO}{endpoint}", timeout=30)
            resp.raise_for_status()
            return True
        except _RETRIABLE as exc:
            if attempt < _MAX_RETRY - 1:
                wait = backoff_delay(attempt)
                print(f"[api] DELETE {endpoint} — {type(exc).__name__}, retry {attempt + 1}/{_MAX_RETRY} em {wait:.0f}s")
                time.sleep(wait)
            else:
                log_error("api_delete_failed", {
                    "endpoint":   endpoint,
                    "error":      str(exc),
                    "error_type": _classify_error(exc),
                    "attempts":   _MAX_RETRY,
                })
                return False
        except Exception as exc:
            status_code: int | None = getattr(getattr(exc, "response", None), "status_code", None)
            log_error("api_delete_failed", {
                "endpoint":    endpoint,
                "error":       str(exc),
                "error_type":  _classify_error(exc),
                "status_code": status_code,
            })
            print(f"[T212] DELETE {endpoint} falhou (HTTP {status_code}): {exc}", flush=True)
            return False
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

    for attempt in range(_MAX_RETRY):
        try:
            df = yf.Ticker(_t212_to_yfinance(ticker)).history(period=f"{days}d", interval="1d")
            if df.empty:
                return []
            return [
                {
                    "date":   dt.strftime("%Y-%m-%d"),
                    "open":   round(float(row["Open"]),   4),
                    "high":   round(float(row["High"]),   4),
                    "low":    round(float(row["Low"]),    4),
                    "close":  round(float(row["Close"]),  4),
                    "volume": int(row["Volume"]),
                }
                for dt, row in df.iterrows()
            ]
        except Exception as exc:
            if attempt < _MAX_RETRY - 1:
                time.sleep(backoff_delay(attempt))
            else:
                from .logger import log_error
                log_error("historical_data_failed", {
                    "ticker": ticker, "days": days,
                    "error": str(exc), "attempts": _MAX_RETRY,
                })
                return []
    return []


def place_order_demo(
    ticker: str,
    side: str,
    qty: float,
    order_type: str,
    price: float | None = None,
) -> dict | None:
    """Places a BUY MARKET order on T212 demo account.

    ticker:     T212 instrument ticker (e.g. "AAPL_US_EQ")
    side:       "BUY" (SELL is handled by close_position_demo)
    qty:        absolute quantity (positive); fractional allowed for MARKET
    order_type: "MARKET" (LIMIT orders for fractional shares are rejected by T212)
    price:      ignored for MARKET orders (kept for API compatibility)

    Returns the T212 order response dict, or None on failure.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    side = side.upper()
    order_type = order_type.upper()

    if order_type == "MARKET":
        # Schema mínimo documentado (https://docs.trading212.com/api/orders):
        # apenas `ticker` + `quantity` (positiva). Campos como `timeValidity`
        # NÃO são aceites pelo endpoint /market — devolvem 400 "Invalid payload"
        # silenciosamente. Confirmado empiricamente a 2026-05-23.
        return _post("/equity/orders/market", {
            "ticker":   ticker,
            "quantity": abs(qty),
        })

    if order_type == "LIMIT":
        if price is None:
            from .logger import log_error
            log_error("place_order_missing_price", {"ticker": ticker, "side": side})
            return None
        abs_qty = abs(qty)
        # T212 rejeita LIMIT com frações de acções. Se qty não é inteiro, faz
        # fallback para MARKET — preferimos executar com slippage mínimo a
        # falhar a ordem inteiramente.
        if abs_qty != int(abs_qty):
            from .logger import log_decision
            log_decision("limit_fractional_fallback", "market_order", {
                "ticker": ticker,
                "qty": abs_qty,
                "wanted_limit_price": round(price, 2),
            })
            return _post("/equity/orders/market", {
                "ticker":   ticker,
                "quantity": abs_qty,
            })
        # LIMIT tem schema DIFERENTE do MARKET:
        #   - MARKET rejeita `timeValidity` (400 "Invalid payload")
        #   - LIMIT  exige `timeValidity:"DAY"` (único valor aceite; "GTC" / "timeInForce" são 400)
        # Confirmado empiricamente a 2026-05-23 contra demo.trading212.com.
        return _post("/equity/orders/limit", {
            "ticker":       ticker,
            "quantity":     int(abs_qty),
            "limitPrice":   round(price, 2),
            "timeValidity": "DAY",
        })

    from .logger import log_error
    log_error("place_order_unknown_type", {"order_type": order_type, "ticker": ticker})
    return None


def cancel_pending_orders_demo(ticker: str) -> int:
    """Cancela todas as ordens pendentes para o ticker. Devolve número de ordens canceladas.

    Usa GET /equity/orders para listar ordens activas e DELETE /equity/orders/{id}
    para cancelar cada uma filtrada pelo ticker.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    from .logger import log_decision, log_error

    orders = _get("/equity/orders")
    if not orders:
        return 0

    cancelled = 0
    for order in orders:
        if order.get("ticker") != ticker:
            continue
        order_id = order.get("id") or order.get("orderId")
        if not order_id:
            log_error("cancel_pending_order_no_id", {"ticker": ticker, "order": order})
            continue
        if _delete(f"/equity/orders/{order_id}"):
            cancelled += 1
            log_decision("cancel_pending_order", "cancelled", {
                "ticker": ticker,
                "order_id": order_id,
                "type": order.get("type"),
                "quantity": order.get("quantity"),
            })
        else:
            log_error("cancel_pending_order_failed", {
                "ticker": ticker,
                "order_id": order_id,
            })
    return cancelled


def close_position_demo(ticker: str, quantity: float) -> bool:
    """Fecha a posição de um ticker na conta demo T212.

    Convenção documentada (https://docs.trading212.com/api/orders): para vender,
    POST /equity/orders/market com quantity NEGATIVA. Não existe endpoint
    DELETE /equity/positions/{ticker} no T212 (a tentativa anterior devolvia
    2xx idempotente para DELETEs desconhecidos, dando falso positivo).

    Cancela ordens pendentes para o ticker antes da venda — evita que ordens
    BUY pendentes (ex: criadas por falhas de fechamento anteriores) executem
    em paralelo com este SELL e reabram a posição.

    Devolve True em caso de sucesso, False em qualquer erro.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    from .logger import log_decision, log_error

    n_cancelled = cancel_pending_orders_demo(ticker)
    if n_cancelled:
        log_decision("close_position_cancelled_pending", str(n_cancelled), {"ticker": ticker})

    qty = abs(float(quantity))
    if qty <= 0:
        log_error("close_position_invalid_qty", {"ticker": ticker, "quantity": quantity})
        return False

    # Schema mínimo confirmado a 2026-05-23: apenas `ticker` + `quantity` (negativa).
    # Adicionar `timeValidity` faz com que T212 responda 400 "Invalid payload".
    response = _post("/equity/orders/market", {
        "ticker":   ticker,
        "quantity": -qty,             # negative quantity = SELL
    })
    if response is None:
        log_error("close_position_sell_failed", {
            "ticker":   ticker,
            "quantity": qty,
            "note":     "POST /equity/orders/market com quantity negativa falhou",
        })
        return False

    log_decision("close_position_sell_placed", "market_sell", {
        "ticker":     ticker,
        "quantity":   qty,
        "order_id":   response.get("id") or response.get("orderId"),
    })
    return True


def reconcile_orphan_buy_orders(held_tickers: set[str]) -> int:
    """Cancela ordens BUY pendentes para tickers que já temos em carteira.

    Estas ordens aparecem quando um fechamento anterior falhou e o broker criou
    inadvertidamente uma ordem de COMPRA com a quantidade da posição. Sem este
    reconcile, a ordem fica pendente indefinidamente e dispara na próxima
    abertura, dobrando a posição.

    held_tickers: conjunto de tickers (no formato T212, ex: "ARM_US_EQ") que
    estão actualmente em carteira segundo a fonte de verdade T212.

    Devolve o número de ordens canceladas.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING is True — aborting to protect live account.")

    if not held_tickers:
        return 0

    from .logger import log_decision

    orders = _get("/equity/orders")
    if not orders:
        return 0

    cancelled = 0
    for order in orders:
        ticker = order.get("ticker")
        if not ticker or ticker not in held_tickers:
            continue
        qty = order.get("quantity") or 0
        if qty <= 0:                       # só BUY (qty > 0) — SELLs legítimos têm qty < 0
            continue
        order_id = order.get("id") or order.get("orderId")
        if not order_id:
            continue
        if _delete(f"/equity/orders/{order_id}"):
            cancelled += 1
            log_decision("orphan_buy_order_cancelled", "reconciled", {
                "ticker":   ticker,
                "order_id": order_id,
                "qty":      qty,
            })
    return cancelled


def cancel_order_demo(order_id: str | int) -> bool:
    """Cancels an active order by ID on T212 demo account. Returns True on success."""
    return _delete(f"/equity/orders/{order_id}")
