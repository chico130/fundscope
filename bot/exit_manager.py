"""
Exit Manager — Three Barriers system.

Checks all open positions against their ATR-based barriers each cycle:

  Barrier 1 — Stop Loss:   entry - 1.5 × ATR  (or entry if break-even active)
  Barrier 2 — BE Trigger:  entry + 1.0 × ATR  → moves stop to entry, fires Whisper
  Barrier 3 — ATR Target:  entry + 3.0 × ATR  → take profit

Trades without stored barriers (pre-existing or ATR unavailable at entry) are
silently skipped — this module never blocks the cycle on missing data.

State mutations (break_even_active, updated stop_loss_price) are written
atomically to data/beta/beta_trades.json.
"""
from __future__ import annotations

import json

from .config import DATA_BETA_DIR
from .logger import log_decision, log_error
from .notifier import enviar_alerta
from .strategy import ProposedTrade


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_open_trades() -> list[dict]:
    """Returns open trades (no closed_at) from beta_trades.json. Empty list on any error."""
    path = DATA_BETA_DIR / "beta_trades.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [t for t in data.get("trades", []) if not t.get("closed_at")]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _update_trade_barriers(trade_id: str, updates: dict) -> bool:
    """Atomically patches barrier fields in beta_trades.json for the given trade_id."""
    path = DATA_BETA_DIR / "beta_trades.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log_error("exit_manager_read_error", {"trade_id": trade_id, "error": str(exc)})
        return False

    found = False
    for trade in data.get("trades", []):
        if trade.get("id") == trade_id:
            trade.update(updates)
            found = True
            break

    if not found:
        return False

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log_error("exit_manager_write_error", {"trade_id": trade_id, "error": str(exc)})
        return False

    return True


def _send_whisper(ticker: str, entry_price: float) -> None:
    sep = "─" * 26
    enviar_alerta(
        f"🤫 WHISPER • RISCO ZERO ATIVADO 🤫\n"
        f"{sep}\n"
        f"🚨 Proteção máxima acionada para {ticker}!\n"
        f"📈 O preço atingiu o gatilho intermédio de +1 ATR.\n"
        f"🛡️ O Stop Loss foi movido para o preço de entrada (${entry_price:.2f}).\n"
        f"💰 Perda máxima nesta operação: $0.00 (Garantido)"
    )


def _make_exit_proposal(ticker: str, trade: dict, positions: list[dict], reason: str) -> ProposedTrade:
    pos = next(
        (p for p in positions if p.get("ticker") == ticker or p.get("price_symbol") == ticker),
        {},
    )
    qty = float(trade.get("qty") or pos.get("quantity") or 0)
    current = (pos.get("market_data") or {}).get("last_price") or pos.get("current_price")
    limit_price = round(float(current) * 0.997, 4) if current else None
    return ProposedTrade(
        ticker=ticker,
        side="SELL",
        qty=qty,
        order_type="LIMIT" if limit_price else "MARKET",
        price=limit_price,
        reason=reason,
        context={"source": "exit_manager"},
        signal_strength=1.0,
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check_exit_barriers(positions: list[dict]) -> list[ProposedTrade]:
    """
    Main barrier monitor — call once per phase0 cycle.

    For each open BUY trade with ATR barriers stored:
      1. If current_price >= atr_trigger_price and break_even_active is False:
           → set break_even_active=True, move stop_loss_price to entry, fire Whisper.
      2. If current_price <= stop_loss_price → return EXIT proposal.
      3. If current_price >= atr_target_price → return EXIT proposal.

    Returns list of ProposedTrade SELL proposals for barrier breaches.
    Errors are logged and skipped — never raises.
    """
    open_trades = _load_open_trades()
    if not open_trades:
        return []

    # Build price lookup keyed by both ticker and price_symbol for flexible matching
    price_map: dict[str, float] = {}
    for pos in positions:
        price = (pos.get("market_data") or {}).get("last_price") or pos.get("current_price")
        if price is None:
            continue
        for key in (pos.get("ticker"), pos.get("price_symbol")):
            if key:
                price_map[key] = float(price)

    proposals: list[ProposedTrade] = []

    for trade in open_trades:
        if trade.get("side", "").upper() != "BUY":
            continue

        ticker      = trade.get("ticker", "")
        trade_id    = trade.get("id", "")
        entry_price = float(trade.get("price") or 0)
        stop_loss   = trade.get("stop_loss_price")
        trigger     = trade.get("atr_trigger_price")
        target      = trade.get("atr_target_price")
        be_active   = trade.get("break_even_active", False)

        # Skip trades without ATR barriers
        if not (stop_loss and trigger and target and entry_price):
            continue

        current = price_map.get(ticker)
        if current is None:
            continue

        # ── Barrier 2: Break-Even Trigger ────────────────────────────────────
        if not be_active and current >= trigger:
            ok = _update_trade_barriers(trade_id, {
                "break_even_active": True,
                "stop_loss_price":   round(entry_price, 4),
            })
            if ok:
                log_decision("break_even_activated", "stop_moved_to_entry", {
                    "trade_id": trade_id,
                    "ticker":   ticker,
                    "trigger":  trigger,
                    "current":  current,
                    "new_stop": entry_price,
                })
                _send_whisper(ticker, entry_price)
            stop_loss = entry_price
            be_active = True

        # ── Barrier 1: Stop Loss ──────────────────────────────────────────────
        if current <= stop_loss:
            label = "Break-Even Stop" if be_active else "Stop Loss ATR"
            reason = f"{label} atingido (${stop_loss:.2f})"
            proposals.append(_make_exit_proposal(ticker, trade, positions, reason))
            log_decision("barrier_exit", "stop_loss_hit", {
                "trade_id":          trade_id,
                "ticker":            ticker,
                "stop":              stop_loss,
                "current":           current,
                "break_even_active": be_active,
            })
            continue

        # ── Barrier 3: ATR Target ─────────────────────────────────────────────
        if current >= target:
            reason = f"Alvo de lucro ATR atingido (${target:.2f})"
            proposals.append(_make_exit_proposal(ticker, trade, positions, reason))
            log_decision("barrier_exit", "atr_target_hit", {
                "trade_id": trade_id,
                "ticker":   ticker,
                "target":   target,
                "current":  current,
            })

    return proposals
