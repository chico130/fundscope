"""
scripts/promote_model.py — Promoção automática de modelo treinado.

Compara Sharpe OOS do novo modelo vs modelo activo.
Critérios de promoção (todos obrigatórios):
  1. Gates passados (Sharpe mediana ≥ 0.5 E MaxDD pior fold ≤ 20%)
  2. Sharpe OOS novo > activo + 0.10

Se promovido:
  - Copia params → data/beta/optimized_backtest_params.json (escrita atómica)
  - Escreve data/beta/shadow_mode.json (Shadow Mode activo)
  - Telegram: 🚀 promovido

Se não promovido / rejeitado:
  - Guarda artefacto em models/ (imutável)
  - Telegram: 📊 não promovido / ❌ rejeitado

REGRAS:
  - NUNCA escreve em config_risco.json
  - Todos os ficheiros promovidos têm backup .vN-1.bak antes de serem sobrescritos
  - Escrita atómica (.tmp → rename) em todos os ficheiros
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from bot.config import BASE_DIR, DATA_BETA_DIR

MODELS_DIR          = BASE_DIR / "models"
REGISTRY_PATH       = MODELS_DIR / "registry.json"
OPT_PARAMS_PATH     = DATA_BETA_DIR / "optimized_backtest_params.json"
SHADOW_MODE_PATH    = DATA_BETA_DIR / "shadow_mode.json"

PROMOTION_THRESHOLD = 0.10   # delta mínimo de Sharpe OOS para promoção


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_version": None, "updated_at": None, "versions": []}


def _update_registry_status(version: int, status: str) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    reg = _read_registry()
    versions = reg.get("versions", [])
    for v in versions:
        if v.get("version") == version:
            v["status"] = status
            if status == "promoted":
                v["promoted_at"] = _ts()
                reg["active_version"] = version
                reg["updated_at"]     = _ts()
            elif status == "shadow":
                v["shadow_started"] = _ts()
            break
    reg["versions"] = versions
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(REGISTRY_PATH)


def _get_active_sharpe() -> Optional[float]:
    """Lê Sharpe do modelo activo em optimized_backtest_params.json."""
    if not OPT_PARAMS_PATH.exists():
        return None
    try:
        data = json.loads(OPT_PARAMS_PATH.read_text(encoding="utf-8"))
        # Suporta tanto o campo _meta.sharpe_oos (pipeline novo) como result.sharpe (legacy)
        meta_sharpe = data.get("_meta", {}).get("sharpe_oos")
        if meta_sharpe is not None:
            return float(meta_sharpe)
        return data.get("result", {}).get("sharpe")
    except Exception:
        return None


def _get_active_version() -> Optional[int]:
    """Lê a versão activa do registry."""
    reg = _read_registry()
    return reg.get("active_version")


def _send_telegram(msg: str) -> None:
    try:
        import requests
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            print(f"[promote] Telegram não configurado", flush=True)
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[promote] Telegram falhou: {exc}", flush=True)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _backup_active(path: Path, active_version: Optional[int]) -> None:
    """Faz backup do ficheiro activo antes de sobrescrever."""
    if not path.exists():
        return
    suffix = f".v{active_version}.bak" if active_version else ".bak"
    bak = path.with_suffix(suffix)
    try:
        bak.write_bytes(path.read_bytes())
    except Exception as exc:
        print(f"[promote] backup {path.name} falhou: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Promoção principal
# ---------------------------------------------------------------------------

def promote(version: int) -> str:
    """Promove ou rejeita o modelo v{version}. Devolve status: 'promoted'|'shadow'|'rejected'."""
    params_path = MODELS_DIR / f"bonnie_params_v{version}.json"
    if not params_path.exists():
        print(f"[promote] bonnie_params_v{version}.json não encontrado", flush=True)
        return "rejected"

    data = json.loads(params_path.read_text(encoding="utf-8"))
    oos  = data["oos_metrics"]

    new_sharpe   = float(oos.get("sharpe_median", 0.0))
    gates_passed = bool(oos.get("gates", {}).get("passed", False))
    active_sharpe  = _get_active_sharpe()
    active_version = _get_active_version()
    active_str     = f"{active_sharpe:.2f}" if active_sharpe is not None else "N/A"

    print(
        f"\n[promote] v{version} | Sharpe OOS: {new_sharpe:.2f} | "
        f"Activo ({active_str}) | Gates: {'OK' if gates_passed else 'FALHOU'}",
        flush=True,
    )

    # --- Gates duros ---
    if not gates_passed:
        msg = (
            f"❌ <b>Treino Semanal — v{version} REJEITADO pelos gates</b>\n\n"
            f"Sharpe OOS: {new_sharpe:.2f} (mínimo 0.5)\n"
            f"MaxDD pior fold: {oos.get('max_dd_worst_fold', 0):.1f}% (máximo 20%)\n"
            f"Fitness: {oos.get('fitness', 0):.3f}\n"
            f"Folds OK: {oos.get('folds_passing', 0)}/{oos.get('folds_total', 0)}"
        )
        _send_telegram(msg)
        _update_registry_status(version, "rejected")
        print(f"[promote] v{version} REJEITADO — gates falharam", flush=True)
        return "rejected"

    # --- Delta de Sharpe ---
    delta = new_sharpe - (active_sharpe or 0.0)
    if delta < PROMOTION_THRESHOLD:
        msg = (
            f"📊 <b>Treino Semanal — v{version} Não Promovido</b>\n\n"
            f"Modelo treinado e arquivado, sem melhoria suficiente.\n"
            f"Sharpe OOS: {new_sharpe:.2f}\n"
            f"Activo: {active_str}\n"
            f"Delta: {delta:+.2f} (mínimo +{PROMOTION_THRESHOLD:.2f})\n"
            f"Fitness: {oos.get('fitness', 0):.3f}\n\n"
            f"Arquivo: <code>models/bonnie_params_v{version}.json</code>"
        )
        _send_telegram(msg)
        _update_registry_status(version, "rejected")
        print(f"[promote] v{version} não promovido: delta {delta:+.2f} < +{PROMOTION_THRESHOLD:.2f}", flush=True)
        return "rejected"

    # --- Promoção ---
    _do_promote(version, data, new_sharpe, active_sharpe, active_version)
    return "promoted"


def _do_promote(
    version: int,
    data: dict,
    new_sharpe: float,
    active_sharpe: Optional[float],
    active_version: Optional[int],
) -> None:
    oos    = data["oos_metrics"]
    hparams = data["hyperparams"]

    # Backup do activo
    _backup_active(OPT_PARAMS_PATH, active_version)

    # Escreve optimized_backtest_params.json no formato de load_optimized_params()
    new_params_payload = {
        "_meta": {
            "saved_at":      _ts(),
            "source":        f"train_bonnie.py v{version}",
            "model_version": version,
            "fitness":       oos.get("fitness", 0),
            "sharpe_oos":    oos.get("sharpe_median", 0),
        },
        "params": {
            "atr_stop_mult_value":    hparams.get("atr_stop_mult_value",    1.75),
            "atr_stop_mult_momentum": hparams.get("atr_stop_mult_momentum", 2.0),
            "atr_tp_mult":            hparams.get("atr_tp_mult",            4.25),
            "value_trail_activation": hparams.get("value_trail_activation", 3.0),
            "value_trail_distance":   hparams.get("value_trail_distance",   3.5),
            "max_position_pct":       hparams.get("max_position_pct",       11.0),
            "bonnie_threshold":       hparams.get("bonnie_threshold",       0.60),
            # Manter defaults do add-logic (não optimizados)
            "add_max_existing_pct":  0.06,
            "add_target_total_pct":  0.10,
            "add_max_increment_pct": 0.05,
            "add_min_increment_pct": 0.02,
        },
        "result": {
            "sharpe":        oos.get("sharpe_median", 0),
            "max_dd_pct":    oos.get("max_dd_worst_fold", 0),
            "win_rate_pct":  round(oos.get("win_rate_median", 0) * 100, 2),
        },
    }
    _atomic_write(OPT_PARAMS_PATH, json.dumps(new_params_payload, indent=2, ensure_ascii=False))
    print(f"[promote] → {OPT_PARAMS_PATH.relative_to(BASE_DIR)}", flush=True)

    # Shadow mode activo
    _backup_active(SHADOW_MODE_PATH, active_version)
    shadow_payload = {
        "active":      True,
        "model":       f"v{version}",
        "version":     version,
        "start":       _ts(),
        "sharpe_oos":  oos.get("sharpe_median", 0),
        "model_pkl":   data["model_artifacts"].get("pkl", ""),
        "thresholds":  data["model_artifacts"].get("thresholds", ""),
    }
    _atomic_write(SHADOW_MODE_PATH, json.dumps(shadow_payload, indent=2, ensure_ascii=False))
    print(f"[promote] shadow_mode.json activo — v{version}", flush=True)

    _update_registry_status(version, "promoted")

    active_str = f"{active_sharpe:.2f}" if active_sharpe is not None else "N/A"
    msg = (
        f"🚀 <b>Novo modelo PROMOVIDO: v{version}</b>\n\n"
        f"Sharpe OOS: {active_str} → {new_sharpe:.2f} "
        f"({new_sharpe - (active_sharpe or 0):+.2f})\n"
        f"MaxDD: {oos.get('max_dd_worst_fold', 0):.1f}%\n"
        f"Win Rate: {oos.get('win_rate_median', 0):.1%}\n"
        f"Fitness: {oos.get('fitness', 0):.3f}\n\n"
        f"<i>Shadow mode activo — a monitorizar v{version} em paralelo com o bot.</i>"
    )
    _send_telegram(msg)
    print(f"[promote] v{version} PROMOVIDO com sucesso", flush=True)


# ---------------------------------------------------------------------------
# CLI standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Promove um modelo treinado")
    ap.add_argument("version", type=int, help="Versão a promover (ex: 2)")
    args = ap.parse_args()

    result = promote(args.version)
    print(f"[promote] status final: {result}", flush=True)
