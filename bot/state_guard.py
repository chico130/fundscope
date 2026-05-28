"""Validação e auto-reparação de ficheiros de estado JSON no arranque do bot.

Verifica os dois ficheiros de estado críticos:
  • data/daily_flags.json      — dedup de notificações (perda é inócua)
  • data/beta/beta_trades.json — histórico de trades (perda é grave)

Se algum tiver JSON inválido (corrupção, marcadores de merge git, truncamento):
  1. Faz backup do ficheiro corrompido (.corrupt-<ts>) — preserva evidência
  2. beta_trades.json é restaurado do git HEAD se possível; daily_flags.json é
     recriado vazio
  3. Envia alerta Telegram

Chamado de phase0.__main__ antes de qualquer ciclo. Nunca lança excepção.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import BASE_DIR, DATA_BETA_DIR

_DAILY_FLAGS_PATH = BASE_DIR / "data" / "daily_flags.json"
_BETA_TRADES_PATH = DATA_BETA_DIR / "beta_trades.json"
_BETA_TRADES_GIT  = "data/beta/beta_trades.json"

# Marcadores de conflito de merge git — JSON que os contenha está corrompido.
_MERGE_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _alert(msg: str) -> None:
    try:
        from .notifier import enviar_alerta
        enviar_alerta(msg, silencioso=False)
    except Exception as exc:
        print(f"[state_guard] falha ao enviar alerta: {exc}", flush=True)


def _log(event: str, detail: dict) -> None:
    try:
        from .logger import log_error
        log_error(event, detail)
    except Exception:
        pass


def _is_valid(path: Path, validator) -> bool:
    """True se o ficheiro não existe (nada a reparar) ou tem JSON válido.

    Considera corrompido qualquer ficheiro com marcadores de conflito git, mesmo
    que tecnicamente faça parse (defensivo).
    """
    if not path.exists():
        return True
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if any(marker in raw for marker in _MERGE_MARKERS):
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return validator(data)


def _backup_corrupt(path: Path) -> Path | None:
    try:
        corrupt = path.with_name(f"{path.name}.corrupt-{_ts()}")
        path.rename(corrupt)
        return corrupt
    except OSError as exc:
        _log("state_repair_backup_failed", {"path": str(path), "error": str(exc)})
        return None


def _write_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _restore_beta_trades_from_git() -> dict | None:
    """Lê a última versão boa de beta_trades.json do git HEAD. None se indisponível."""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{_BETA_TRADES_GIT}"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        if isinstance(data, dict) and isinstance(data.get("trades"), list):
            return data
    except Exception:
        return None
    return None


def validate_and_repair_state() -> None:
    """Valida e repara daily_flags.json e beta_trades.json. Nunca lança."""
    try:
        _repair_daily_flags()
    except Exception as exc:
        _log("state_repair_daily_flags_error", {"error": str(exc)})
    try:
        _repair_beta_trades()
    except Exception as exc:
        _log("state_repair_beta_trades_error", {"error": str(exc)})


def _repair_daily_flags() -> None:
    if _is_valid(_DAILY_FLAGS_PATH, lambda d: isinstance(d, dict)):
        return
    corrupt = _backup_corrupt(_DAILY_FLAGS_PATH)
    _write_atomic(_DAILY_FLAGS_PATH, {})
    _log("state_repair_daily_flags", {"backup": str(corrupt) if corrupt else None})
    _alert(
        "🔧 daily_flags.json corrompido — recriado vazio.\n"
        "Algumas notificações de hoje podem repetir-se (inócuo)."
    )


def _repair_beta_trades() -> None:
    if _is_valid(_BETA_TRADES_PATH, lambda d: isinstance(d, dict) and isinstance(d.get("trades"), list)):
        return
    corrupt = _backup_corrupt(_BETA_TRADES_PATH)
    restored = _restore_beta_trades_from_git()
    if restored is not None:
        _write_atomic(_BETA_TRADES_PATH, restored)
        n = len(restored.get("trades", []))
        _log("state_repair_beta_trades", {
            "backup": str(corrupt) if corrupt else None,
            "restored_from": "git", "n_trades": n,
        })
        _alert(f"🔧 beta_trades.json corrompido — restaurado do git HEAD ({n} trades).")
    else:
        _write_atomic(_BETA_TRADES_PATH, {"trades": []})
        _log("state_repair_beta_trades", {
            "backup": str(corrupt) if corrupt else None,
            "restored_from": "default",
        })
        _alert(
            "🚨 beta_trades.json corrompido e sem versão git recuperável — "
            "recriado VAZIO. Histórico de trades perdido (backup em .corrupt-*)."
        )
