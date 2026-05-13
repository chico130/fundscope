"""
Structured logger for all bot activity.

Log files:
  logs/trades/YYYY-MM-DD.json       — trades + decisions, one list per day
  logs/errors/YYYY-MM-DD.json       — errors and anomalies, one list per day
  logs/strategy_versions.json       — append-only version history

Post-mortems are written back into data/beta/beta_trades.json when a
position closes or the result_after_minutes window expires.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_BETA_DIR, LOGS_DIR, LOGS_TRADES_DIR, LOGS_ERRORS_DIR


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_to_json_list(path: Path, entry: dict) -> None:
    """Reads the JSON array at path (creates it if absent), appends entry, writes back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    records: list = []
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
            if not isinstance(records, list):
                records = [records]
        except (json.JSONDecodeError, OSError):
            records = []
    records.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def log_trade(trade_dict: dict) -> None:
    """Appends a full trade record to today's trade log.

    Expected keys (from spec): id, datetime, ticker, side, qty, price, env,
    strategy_version, reason, context, result_eur, result_pct,
    result_after_minutes, closed_at, postmortem.
    """
    path = LOGS_TRADES_DIR / f"{_today()}.json"
    _append_to_json_list(path, trade_dict)


def log_decision(reason: str, action: str, context: dict | None = None) -> None:
    """Logs any bot decision (no-op choices, risk blocks, etc.) to today's trade log."""
    entry: dict = {
        "datetime": _now_iso(),
        "type": "decision",
        "reason": reason,
        "action": action,
    }
    if context:
        entry["context"] = context
    path = LOGS_TRADES_DIR / f"{_today()}.json"
    _append_to_json_list(path, entry)


def log_error(error_type: str, detail: dict | None = None) -> None:
    """Appends an error record to today's error log."""
    entry = {
        "datetime": _now_iso(),
        "type": error_type,
        "detail": detail or {},
    }
    path = LOGS_ERRORS_DIR / f"{_today()}.json"
    _append_to_json_list(path, entry)


def update_postmortem(
    trade_id: str,
    result_eur: float,
    result_pct: float,
    explanation: str,
) -> bool:
    """Fills in result_eur, result_pct, closed_at, and postmortem on a closed trade
    in data/beta/beta_trades.json.

    Returns True on success, False if the trade_id was not found or IO failed.
    """
    trades_path = DATA_BETA_DIR / "beta_trades.json"

    if not trades_path.exists():
        log_error("postmortem_file_missing", {"trade_id": trade_id, "path": str(trades_path)})
        return False

    try:
        with open(trades_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log_error("postmortem_read_error", {"trade_id": trade_id, "error": str(exc)})
        return False

    found = False
    for trade in data.get("trades", []):
        if trade.get("id") == trade_id:
            trade["result_eur"] = result_eur
            trade["result_pct"] = result_pct
            trade["closed_at"] = _now_iso()
            trade["postmortem"] = explanation
            found = True
            break

    if not found:
        log_error("postmortem_trade_not_found", {"trade_id": trade_id})
        return False

    try:
        with open(trades_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        log_error("postmortem_write_error", {"trade_id": trade_id, "error": str(exc)})
        return False

    return True


def log_strategy_version(version: str, changes: list[str], reason: str) -> None:
    """Records a strategy version bump in logs/strategy_versions.json.

    Every parameter or logic change must be documented here so the audit trail
    shows exactly what changed, when, and why.
    """
    path = LOGS_DIR / "strategy_versions.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    records: list = []
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
            if not isinstance(records, list):
                records = []
        except (json.JSONDecodeError, OSError):
            records = []

    records.append({
        "datetime": _now_iso(),
        "version": version,
        "changes": changes,
        "reason": reason,
    })

    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
