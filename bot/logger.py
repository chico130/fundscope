"""
Structured logger for all bot activity.

Log files:
  logs/trades/YYYY-MM-DD.json       — trades + decisions, one list per day
  logs/errors/YYYY-MM-DD.json       — errors and anomalies, one list per day
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import LOGS_TRADES_DIR, LOGS_ERRORS_DIR, DATA_BETA_DIR


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
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


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


def log_info(info_type: str, detail: dict | None = None) -> None:
    """Logs an informational event (start, stop, milestone) to today's trade log."""
    entry = {
        "datetime": _now_iso(),
        "type": info_type,
        "detail": detail or {},
    }
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


def log_shadow_rejected(
    rejected_by: str,
    rejection_reason: str,
    features: dict,
    signal: dict,
) -> None:
    """Appends a shadow_rejected record to data/beta/shadow_ledger.json.

    I/O lateral — never raises (R3). All veto points call this so every
    signal the Clyde generated but the system discarded is preserved with
    its full 8-feature vector for future ML training.
    """
    record = {
        "datetime":         _now_iso(),
        "type":             "shadow_rejected",
        "execution_type":   "shadow_rejected",
        "rejected_by":      rejected_by,
        "rejection_reason": rejection_reason,
        "features":         features or {},
        "signal":           signal or {},
        "shadow_result":    None,
    }
    path = DATA_BETA_DIR / "shadow_ledger.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, dict):
                    existing = {}
            except (json.JSONDecodeError, OSError):
                existing = {}
        trades = existing.get("shadow_trades", [])
        if not isinstance(trades, list):
            trades = []
        trades.append(record)
        payload = {
            "shadow_trades": trades,
            "last_updated":  _now_iso(),
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
    except Exception as exc:
        log_error("shadow_ledger_write", {"error": str(exc)})

