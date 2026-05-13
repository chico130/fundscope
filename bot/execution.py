"""
Execution module — Fase 1.

Wraps api_client order placement with:
  - pre-flight risk check (strategy.check_risk_limits)
  - structured trade record creation
  - dual-write: logs/trades/YYYY-MM-DD.json  +  data/beta/beta_trades.json

LIVE_TRADING must be False; api_client enforces this but we double-check here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import api_client
from .config import DATA_BETA_DIR, LIVE_TRADING, STRATEGY_VERSION
from .logger import log_decision, log_error, log_trade
from .strategy import ProposedTrade, check_risk_limits


def execute_trade(proposed: ProposedTrade, portfolio_state: dict) -> dict | None:
    """Executes a proposed trade after risk validation.

    Returns the trade record dict on success, None on failure or risk block.
    Side-effects: writes to daily log + appends to beta_trades.json.
    """
    if LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING é True — abortar para proteger a conta real.")

    if not check_risk_limits(proposed, portfolio_state):
        return None

    now = datetime.now(timezone.utc)
    trade_id = f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}_{proposed.ticker}_{proposed.side}"

    log_decision("pre_execution", "place_order", {
        "id": trade_id,
        "ticker": proposed.ticker,
        "side": proposed.side,
        "qty": proposed.qty,
        "type": proposed.order_type,
    })

    response = api_client.place_order_demo(
        ticker=proposed.ticker,
        side=proposed.side,
        qty=proposed.qty,
        order_type=proposed.order_type,
        price=proposed.price,
    )

    if response is None:
        log_error("execution_failed", {"id": trade_id, "ticker": proposed.ticker, "side": proposed.side})
        return None

    trade_record: dict = {
        "id": trade_id,
        "datetime": now.isoformat(),
        "ticker": proposed.ticker,
        "side": proposed.side,
        "qty": proposed.qty,
        "price": proposed.price or _fill_price(response),
        "env": "demo",
        "strategy_version": STRATEGY_VERSION,
        "reason": proposed.reason,
        "context": proposed.context,
        "result_eur": None,
        "result_pct": None,
        "result_after_minutes": 1440,
        "closed_at": None,
        "postmortem": None,
    }

    log_trade(trade_record)
    _append_to_beta_trades(trade_record)
    return trade_record


def execute_exit(ticker: str, position: dict, reason: str) -> dict | None:
    """Closes an entire position at market price."""
    qty = position.get("quantity", 0)
    if qty <= 0:
        return None
    proposed = ProposedTrade(
        ticker=ticker, side="SELL", qty=qty,
        order_type="MARKET", price=None,
        reason=reason, context={}, signal_strength=1.0,
    )
    # Minimal portfolio_state — exit always passes the size check
    return execute_trade(proposed, {"positions": [position], "cash": {"free": 0}})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fill_price(response: dict) -> float | None:
    if not isinstance(response, dict):
        return None
    return response.get("fillPrice") or response.get("limitPrice") or response.get("price")


def _append_to_beta_trades(trade_record: dict) -> None:
    """Appends a new trade to data/beta/beta_trades.json (atomic write)."""
    path = DATA_BETA_DIR / "beta_trades.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {"trades": []}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {"trades": []}

    data["trades"].append(trade_record)

    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
    except OSError as exc:
        log_error("beta_trades_write_error", {"error": str(exc)})
        if tmp.exists():
            tmp.unlink(missing_ok=True)
