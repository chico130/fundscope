"""
Centralized API rate limiter — per-day and per-minute quotas.

Persists counters in data/beta/rate_limits.json so GitHub Actions runs share
quota tracking across cycles. Atomic writes (.tmp → rename) prevent corruption.

Companion to circuit_breaker.py:
  circuit_breaker  — reacts to successive API failures
  rate_limiter     — prevents quota exhaustion before calls are made

Usage at each call site:
    from . import rate_limiter
    if not rate_limiter.check_and_consume("finnhub"):
        return None   # skip call; caller degrades gracefully
    # ... make the actual API call ...

Thresholds (from API_RATE_LIMITS in config.py):
  80% daily  → one-off Telegram warning (silencioso)
  95% daily  → one-off Telegram warning + calls blocked for rest of day
  95% per-min → calls blocked for rest of that minute
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_BETA_DIR, API_RATE_LIMITS

_STATE_PATH = DATA_BETA_DIR / "rate_limits.json"
_WARN_PCT   = 0.80   # daily warning threshold
_BLOCK_PCT  = 0.95   # block threshold (daily and per-minute)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _minute(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def _load() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except OSError as exc:
        print(f"[rate_limiter] state save failed: {exc}", flush=True)


def _hydrate(entry: dict, now: datetime) -> dict:
    """Return entry with stale day/minute buckets reset to current window."""
    today  = _day(now)
    bucket = _minute(now)

    if entry.get("day") != today:
        # Day rolled over — reset all counters and alert flags
        return {
            "day":        today,
            "day_count":  0,
            "min_bucket": bucket,
            "min_count":  0,
            "alerted_80": False,
            "alerted_95": False,
        }

    if entry.get("min_bucket") != bucket:
        entry = dict(entry)  # avoid mutating caller's reference
        entry["min_bucket"] = bucket
        entry["min_count"]  = 0

    return entry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_and_consume(api: str, cost: int = 1) -> bool:
    """Atomically check limits and consume quota if allowed.

    Returns True  → quota available; counters incremented; proceed with call.
    Returns False → at ≥95% of a limit; counters NOT incremented; skip call.

    Fires a one-off Telegram alert at 80% and 95% daily usage (only for APIs
    with a known per_day limit; direct HTTP to avoid recursion with notifier).
    Never raises — any internal failure defaults to True (fail-open).
    """
    try:
        limits = API_RATE_LIMITS.get(api)
        if not limits:
            return True

        now   = _now_utc()
        state = _load()
        entry = _hydrate(state.get(api, {}), now)

        per_day = limits.get("per_day")
        per_min = limits.get("per_min")

        # ── Block at 95% ──────────────────────────────────────────────────
        if per_day and entry["day_count"] + cost > int(per_day * _BLOCK_PCT):
            print(
                f"[rate_limiter] {api} daily ≥95%"
                f" ({entry['day_count']}/{per_day}) — call blocked",
                flush=True,
            )
            return False

        if per_min and entry["min_count"] + cost > int(per_min * _BLOCK_PCT):
            print(
                f"[rate_limiter] {api} per-minute ≥95%"
                f" ({entry['min_count']}/{per_min}) — call blocked",
                flush=True,
            )
            return False

        # ── Consume quota ────────────────────────────────────────────────
        entry["day_count"] += cost
        entry["min_count"]  += cost

        # Determine if an alert should fire this save
        alert_pct: int | None = None
        if per_day:
            pct = entry["day_count"] / per_day
            if pct >= _BLOCK_PCT and not entry.get("alerted_95"):
                entry["alerted_95"] = True
                alert_pct = 95
            elif pct >= _WARN_PCT and not entry.get("alerted_80"):
                entry["alerted_80"] = True
                alert_pct = 80

        state[api] = entry
        _save(state)

        if alert_pct is not None:
            _fire_alert(api, entry["day_count"], per_day, alert_pct)

        return True

    except Exception as exc:
        # Rate limiter failure must never abort the bot
        print(f"[rate_limiter] check_and_consume failed ({api}): {exc}", flush=True)
        return True


def remaining(api: str) -> dict:
    """Return current usage snapshot for diagnostics and logging."""
    try:
        limits = API_RATE_LIMITS.get(api, {})
        now   = _now_utc()
        state = _load()
        entry = _hydrate(state.get(api, {}), now)
        per_day = limits.get("per_day")
        per_min = limits.get("per_min")
        return {
            "day_used":      entry.get("day_count", 0),
            "day_limit":     per_day,
            "day_remaining": (per_day - entry["day_count"]) if per_day else None,
            "min_used":      entry.get("min_count", 0),
            "min_limit":     per_min,
            "min_remaining": (per_min - entry["min_count"]) if per_min else None,
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Alert — direct HTTP to avoid circular import with notifier
# ---------------------------------------------------------------------------

def _fire_alert(api: str, used: int, limit: int, pct: int) -> None:
    """Send Telegram rate-limit warning via direct requests — never touches notifier."""
    try:
        import requests as _req
        token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":              chat_id,
                "text": (
                    f"⚠️ Rate limit {api.upper()} — {pct}%\n\n"
                    f"{used}/{limit} chamadas hoje ({pct}% do limite diário).\n"
                    f"{ts}"
                ),
                "disable_notification": True,
            },
            timeout=8,
        )
    except Exception as exc:
        print(f"[rate_limiter] Telegram alert failed: {exc}", flush=True)
