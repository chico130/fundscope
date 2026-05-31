"""
scripts/evaluate_challenger.py — O Juiz: compara Desafiante vs Campeão no OOS.

Fluxo:
  1. Encontra o desafiante mais recente: models/bonnie_challenger_vN.pkl
  2. Carrega o campeão actual: models/bonnie_champion.pkl (se existir)
  3. OOS = shadow trades dos últimos oos_window_days dias com shadow_result resolvido
     (estes registos NÃO foram usados no treino do desafiante — garantia de pureza)
  4. Avalia ambos: Win Rate, Profit Factor, Sharpe, Max Drawdown
  5. Promove se: passa gates absolutos E supera campeão em >= metrics_to_beat sem piorar nenhuma
  6. Envia Telegram em linguagem simples (sem jargão técnico)

Output:
  Promovido:  models/bonnie_champion.pkl + models/bonnie_champion_meta.json
  Rejeitado:  models/bonnie_challenger_vN_meta.json (arquivo)
  Sempre:     models/registry.json actualizado

Critérios e limites: config_risco.json/challenger_promotion_criteria
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR           = Path(__file__).parent.parent
MODELS_DIR         = BASE_DIR / "models"
ARCHIVE_DIR        = MODELS_DIR / "archive"
SHADOW_LEDGER_PATH = BASE_DIR / "data" / "beta" / "shadow_ledger.json"
CONFIG_RISCO_PATH  = BASE_DIR / "config_risco.json"
CHAMPION_PKL       = MODELS_DIR / "bonnie_champion.pkl"
CHAMPION_META_PATH = MODELS_DIR / "bonnie_champion_meta.json"
REGISTRY_PATH      = MODELS_DIR / "registry.json"

CANONICAL_FEATURE_COLS: list[str] = [
    "rsi_14", "volume_ratio", "atr_pct", "price_vs_ema20",
    "price_vs_ema50", "price_vs_ema200", "momentum_1m", "momentum_3m",
]

_DEFAULT_CRITERIA: dict = {
    "min_oos_trades":       20,
    "metrics_to_beat":      2,
    "min_win_rate":         0.45,
    "min_profit_factor":    1.1,
    "max_drawdown_ceiling": 0.25,
    "oos_window_days":      30,
}

_EPSILON = 0.02  # tolerância para comparação de métricas (evita ruído)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_RISCO_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_criteria() -> dict:
    cfg = _load_config()
    crit = cfg.get("challenger_promotion_criteria", {})
    return {**_DEFAULT_CRITERIA, **crit}


def _send_telegram(message: str) -> None:
    try:
        import requests
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[Juiz] Telegram falhou: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Carregar modelos
# ---------------------------------------------------------------------------

def _load_champion() -> tuple[Optional[object], Optional[dict]]:
    """Devolve (modelo, meta) do campeão, ou (None, None) se não existir."""
    if not CHAMPION_PKL.exists():
        return None, None
    try:
        import joblib
        model = joblib.load(CHAMPION_PKL)
        meta: dict = {}
        if CHAMPION_META_PATH.exists():
            meta = json.loads(CHAMPION_META_PATH.read_text(encoding="utf-8"))
        return model, meta
    except Exception as exc:
        print(f"[Juiz] Erro ao carregar campeão: {exc}", flush=True)
        return None, None


def _find_latest_challenger() -> tuple[Optional[Path], int]:
    """Devolve (path, versão) do bonnie_challenger_vN.pkl com maior N."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    best_path, best_ver = None, 0
    for p in MODELS_DIR.glob("bonnie_challenger_v*.pkl"):
        m = re.search(r"bonnie_challenger_v(\d+)\.pkl", p.name)
        if m:
            ver = int(m.group(1))
            if ver > best_ver:
                best_ver, best_path = ver, p
    return best_path, best_ver


def _load_challenger_thresholds(version: int) -> dict:
    """Carrega thresholds por regime do desafiante vN."""
    candidates = [
        MODELS_DIR / f"bonnie_challenger_thresholds_v{version}.json",
        BASE_DIR / "data" / "beta" / f"bonnie_thresholds_v{version}.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {r: 0.30 for r in ("bull_trending", "bull_lateral", "bear_correction", "bear_capitulation")}


# ---------------------------------------------------------------------------
# Conjunto OOS
# ---------------------------------------------------------------------------

def _load_oos_shadow_trades(oos_window_days: int) -> list[dict]:
    """Shadow trades dos últimos N dias com shadow_result resolvido."""
    if not SHADOW_LEDGER_PATH.exists():
        return []
    try:
        data   = json.loads(SHADOW_LEDGER_PATH.read_text(encoding="utf-8"))
        trades = data.get("shadow_trades", [])
    except Exception:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=oos_window_days)
    valid: list[dict] = []
    for t in trades:
        result = t.get("shadow_result") or {}
        if not result or result.get("result") in {None, "no_data", "error"}:
            continue
        ts = t.get("datetime", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt >= cutoff:
                valid.append(t)
        except (ValueError, AttributeError):
            pass
    return valid


def _extract_feature_row(record: dict) -> Optional[np.ndarray]:
    """Extrai o vector de 8 features canónicas de um registo shadow."""
    feats = record.get("features") or {}
    row: list[float] = []
    for col in CANONICAL_FEATURE_COLS:
        v = feats.get(col)
        if v is None:
            return None
        try:
            row.append(float(v))
        except (TypeError, ValueError):
            return None
    return np.array(row, dtype=float)


# ---------------------------------------------------------------------------
# Métricas OOS
# ---------------------------------------------------------------------------

def _equity_curve_max_dd(result_pcts: list[float]) -> float:
    if not result_pcts:
        return 0.0
    equity = peak = 1.0
    max_dd = 0.0
    for r in result_pcts:
        equity *= (1.0 + r)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sharpe(result_pcts: list[float]) -> float:
    if len(result_pcts) < 2:
        return 0.0
    arr = np.array(result_pcts)
    std = float(np.std(arr))
    if std == 0.0:
        return 0.0
    return float(np.mean(arr) / std * np.sqrt(252))


def _evaluate_model(model: object, thresholds: dict, oos_records: list[dict]) -> dict:
    """Avalia um modelo no conjunto OOS. Devolve dict de métricas."""
    approved: list[float] = []
    n_total  = len(oos_records)

    for rec in oos_records:
        feat_row = _extract_feature_row(rec)
        if feat_row is None:
            continue
        result     = rec.get("shadow_result") or {}
        result_pct = result.get("result_pct")
        if result_pct is None:
            continue
        sig     = rec.get("signal") or {}
        regime  = sig.get("regime", "bull_trending")
        threshold = thresholds.get(regime, 0.30)
        try:
            proba = float(model.predict_proba([feat_row])[0][1])  # type: ignore[attr-defined]
        except Exception:
            continue
        if proba >= threshold:
            approved.append(float(result_pct))

    if not approved:
        return {
            "win_rate": 0.0, "profit_factor": 0.0,
            "sharpe": 0.0, "max_drawdown": 1.0,
            "n_approved": 0, "n_total": n_total,
        }

    wins   = [r for r in approved if r > 0]
    losses = [r for r in approved if r <= 0]
    win_rate      = len(wins) / len(approved)
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else 99.0
    return {
        "win_rate":      round(win_rate, 4),
        "profit_factor": round(min(profit_factor, 99.0), 4),
        "sharpe":        round(_sharpe(approved), 4),
        "max_drawdown":  round(_equity_curve_max_dd(approved), 4),
        "n_approved":    len(approved),
        "n_total":       n_total,
    }


# ---------------------------------------------------------------------------
# Comparação e promoção
# ---------------------------------------------------------------------------

def _compare_metrics(challenger: dict, champion: dict) -> dict:
    """Determina quais métricas o desafiante melhora, piora ou empata (±epsilon)."""
    better, worse, tied = [], [], []
    for m in ("win_rate", "profit_factor", "sharpe"):
        diff = challenger[m] - champion[m]
        if diff > _EPSILON:
            better.append(m)
        elif diff < -_EPSILON:
            worse.append(m)
        else:
            tied.append(m)
    # max_drawdown: menor = melhor
    dd_diff = challenger["max_drawdown"] - champion["max_drawdown"]
    if dd_diff < -_EPSILON:
        better.append("max_drawdown")
    elif dd_diff > _EPSILON:
        worse.append("max_drawdown")
    else:
        tied.append("max_drawdown")
    return {"metrics_better": better, "metrics_worse": worse, "metrics_tied": tied, "epsilon": _EPSILON}


def _update_registry(version: int, metrics: dict, status: str) -> None:
    try:
        reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8")) if REGISTRY_PATH.exists() else {}
    except Exception:
        reg = {}
    entries = [v for v in reg.get("versions", []) if v.get("version") != version]
    entries.append({
        "version":     version,
        "status":      status,
        "created_at":  _now_iso(),
        "win_rate_oos": round(metrics.get("win_rate", 0.0), 4),
    })
    entries.sort(key=lambda v: v.get("version", 0))
    reg["versions"] = entries
    if status == "champion":
        reg["active_version"] = version
        reg["updated_at"]     = _now_iso()
    _write_atomic(REGISTRY_PATH, reg)


def _promote(
    challenger_path: Path,
    version: int,
    thresholds: dict,
    metrics: dict,
    champion_meta: Optional[dict],
    bootstrap: bool,
    extra_meta: Optional[dict] = None,
) -> None:
    """Copia challenger → champion e escreve meta. Escrita atómica."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Arquivar campeão anterior
    if CHAMPION_PKL.exists() and champion_meta:
        old_ver  = champion_meta.get("version", "old")
        arch_pkl = ARCHIVE_DIR / f"bonnie_champion_v{old_ver}.pkl"
        if not arch_pkl.exists():
            shutil.copy2(CHAMPION_PKL, arch_pkl)

    # Challenger → champion (atómico)
    tmp = CHAMPION_PKL.with_suffix(".tmp")
    shutil.copy2(challenger_path, tmp)
    tmp.replace(CHAMPION_PKL)

    meta: dict = {
        "version":                  version,
        "model_file":               "bonnie_champion.pkl",
        "promoted_at":              _now_iso(),
        "promoted_from_challenger": f"challenger_v{version}",
        "previous_champion":        champion_meta.get("version") if champion_meta else None,
        "bootstrap":                bootstrap,
        "feature_set":              "production_A",
        "feature_cols":             CANONICAL_FEATURE_COLS,
        "regime_thresholds":        thresholds,
        "oos_metrics":              metrics,
    }
    if extra_meta:
        for k, v in extra_meta.items():
            meta.setdefault(k, v)

    _write_atomic(CHAMPION_META_PATH, meta)
    _update_registry(version, metrics, "champion")
    print(f"[Juiz] Campeão actualizado: models/bonnie_champion.pkl (v{version})", flush=True)


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def run() -> None:
    print(f"[{_now_iso()}] === evaluate_challenger START ===", flush=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    criteria        = _load_criteria()
    oos_window_days = int(criteria["oos_window_days"])

    # 1 — Encontrar desafiante
    challenger_path, challenger_ver = _find_latest_challenger()
    if challenger_path is None:
        print("[Juiz] Nenhum desafiante em models/ — nada a fazer.", flush=True)
        return
    print(f"[Juiz] Desafiante: bonnie_challenger_v{challenger_ver}.pkl", flush=True)

    try:
        import joblib
        challenger_model = joblib.load(challenger_path)
    except Exception as exc:
        print(f"[Juiz] Erro ao carregar desafiante: {exc}", flush=True)
        return
    challenger_thr = _load_challenger_thresholds(challenger_ver)

    # 2 — Conjunto OOS
    oos_records = _load_oos_shadow_trades(oos_window_days)
    n_usable = sum(
        1 for r in oos_records
        if _extract_feature_row(r) is not None
        and (r.get("shadow_result") or {}).get("result_pct") is not None
    )
    oos_start = (datetime.now(timezone.utc) - timedelta(days=oos_window_days)).strftime("%Y-%m-%d")
    oos_end   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(
        f"[Juiz] OOS: {oos_start} → {oos_end} | "
        f"{len(oos_records)} shadow trades | {n_usable} utilizáveis",
        flush=True,
    )

    if n_usable < criteria["min_oos_trades"]:
        print(
            f"[Juiz] OOS insuficiente ({n_usable} < {criteria['min_oos_trades']}) "
            "— arquivado sem comparação.",
            flush=True,
        )
        _write_atomic(
            MODELS_DIR / f"bonnie_challenger_v{challenger_ver}_meta.json",
            {
                "version": challenger_ver, "verdict": "insufficient_data",
                "n_oos_trades": n_usable, "min_required": criteria["min_oos_trades"],
                "evaluated_at": _now_iso(),
            },
        )
        _send_telegram(
            f"📊 <b>Desafiante v{challenger_ver} não avaliado</b>\n"
            f"Dados insuficientes para avaliação: {n_usable}/{criteria['min_oos_trades']} "
            "registos necessários.\nAguardar mais shadow trades."
        )
        return

    # 3 — Avaliar desafiante
    print("[Juiz] A avaliar desafiante...", flush=True)
    ch_metrics = _evaluate_model(challenger_model, challenger_thr, oos_records)
    print(
        f"  Desafiante: WR={ch_metrics['win_rate']:.1%} "
        f"PF={ch_metrics['profit_factor']:.2f} "
        f"Sharpe={ch_metrics['sharpe']:.2f} "
        f"DD={ch_metrics['max_drawdown']:.1%} "
        f"(aprovados={ch_metrics['n_approved']}/{n_usable})",
        flush=True,
    )

    # 4 — Gates absolutos
    failed_gate: Optional[str] = None
    if ch_metrics["win_rate"] < criteria["min_win_rate"]:
        failed_gate = f"win_rate {ch_metrics['win_rate']:.1%} < {criteria['min_win_rate']:.1%}"
    elif ch_metrics["profit_factor"] < criteria["min_profit_factor"]:
        failed_gate = (
            f"profit_factor {ch_metrics['profit_factor']:.2f} "
            f"< {criteria['min_profit_factor']:.2f}"
        )
    elif ch_metrics["max_drawdown"] > criteria["max_drawdown_ceiling"]:
        failed_gate = (
            f"max_drawdown {ch_metrics['max_drawdown']:.1%} "
            f"> {criteria['max_drawdown_ceiling']:.1%}"
        )

    # 5 — Carregar campeão
    champion_model, champion_meta = _load_champion()

    if champion_model is None:
        # Bootstrap: não existe campeão — promover se passar os gates
        if failed_gate:
            print(f"[Juiz] Bootstrap mas gate falhou: {failed_gate} → arquivado.", flush=True)
            _write_atomic(
                MODELS_DIR / f"bonnie_challenger_v{challenger_ver}_meta.json",
                {
                    "version": challenger_ver, "verdict": "rejected",
                    "rejection_reason": f"failed_gate:{failed_gate}",
                    "challenger_metrics": ch_metrics, "evaluated_at": _now_iso(),
                },
            )
            _send_telegram(
                f"📊 <b>Desafiante v{challenger_ver} rejeitado</b>\n"
                f"Gate falhou: {failed_gate}"
            )
            return

        print(
            f"[Juiz] Sem campeão — bootstrap: desafiante v{challenger_ver} é o novo campeão.",
            flush=True,
        )
        _promote(
            challenger_path, challenger_ver, challenger_thr, ch_metrics,
            champion_meta=None, bootstrap=True,
        )
        _send_telegram(
            f"🏆 <b>Novo Campeão (bootstrap): v{challenger_ver}</b>\n\n"
            f"Taxa de acerto: {ch_metrics['win_rate']:.1%}\n"
            f"Factor de lucro: {ch_metrics['profit_factor']:.2f}\n"
            f"Qualidade dos lucros: {ch_metrics['sharpe']:.2f}\n"
            f"Pior queda: {ch_metrics['max_drawdown']:.1%}"
        )
        return

    # 6 — Avaliar campeão no mesmo OOS
    print("[Juiz] A avaliar campeão no OOS...", flush=True)
    ca_thr     = champion_meta.get("regime_thresholds", {})
    ca_metrics = _evaluate_model(champion_model, ca_thr, oos_records)
    print(
        f"  Campeão:    WR={ca_metrics['win_rate']:.1%} "
        f"PF={ca_metrics['profit_factor']:.2f} "
        f"Sharpe={ca_metrics['sharpe']:.2f} "
        f"DD={ca_metrics['max_drawdown']:.1%} "
        f"(aprovados={ca_metrics['n_approved']}/{n_usable})",
        flush=True,
    )

    comparison = _compare_metrics(ch_metrics, ca_metrics)
    n_better   = len(comparison["metrics_better"])
    n_worse    = len(comparison["metrics_worse"])
    print(
        f"[Juiz] Desafiante vs Campeão: +{n_better} -{n_worse} "
        f"={len(comparison['metrics_tied'])}",
        flush=True,
    )

    # 7 — Veredicto
    if failed_gate:
        verdict, rejection_reason = "rejected", f"failed_gate:{failed_gate}"
        print(f"[Juiz] REJEITADO — gate: {failed_gate}", flush=True)
    elif n_better >= criteria["metrics_to_beat"] and n_worse == 0:
        verdict, rejection_reason = "promoted", None
        print(
            f"[Juiz] PROMOVIDO — supera campeão em {n_better} métricas, não piora nenhuma.",
            flush=True,
        )
    else:
        verdict = "rejected"
        if n_worse > 0:
            rejection_reason = f"piora {n_worse} métrica(s): {comparison['metrics_worse']}"
        else:
            rejection_reason = (
                f"supera só {n_better}/{criteria['metrics_to_beat']} "
                "métricas mínimas sem piorar"
            )
        print(f"[Juiz] REJEITADO — {rejection_reason}", flush=True)

    # 8 — Acção
    now_iso = _now_iso()
    meta: dict = {
        "version":     challenger_ver,
        "verdict":     verdict,
        "bootstrap":   False,
        "feature_cols": CANONICAL_FEATURE_COLS,
        "regime_thresholds": challenger_thr,
        "oos_evaluation": {
            "window_days": oos_window_days,
            "oos_start": oos_start, "oos_end": oos_end,
            "n_samples": n_usable,
            "champion":   ca_metrics,
            "challenger": ch_metrics,
            "comparison": comparison,
        },
        "gates": {
            "all_absolute_passed": failed_gate is None,
            "min_metrics_to_beat": criteria["metrics_to_beat"],
            "rejection_reason":    rejection_reason,
        },
        "evaluated_at": now_iso,
    }

    if verdict == "promoted":
        meta["promoted_at"]              = now_iso
        meta["previous_champion"]        = champion_meta.get("version")
        _promote(
            challenger_path, challenger_ver, challenger_thr, ch_metrics,
            champion_meta=champion_meta, bootstrap=False, extra_meta=meta,
        )
        _send_telegram(
            f"🏆 <b>Novo Campeão: v{challenger_ver}</b>\n\n"
            f"Taxa de acerto: {ch_metrics['win_rate']:.1%} "
            f"(era {ca_metrics['win_rate']:.1%})\n"
            f"Factor de lucro: {ch_metrics['profit_factor']:.2f} "
            f"(era {ca_metrics['profit_factor']:.2f})\n"
            f"Qualidade dos lucros: {ch_metrics['sharpe']:.2f} "
            f"(era {ca_metrics['sharpe']:.2f})\n"
            f"Pior queda: {ch_metrics['max_drawdown']:.1%} "
            f"(era {ca_metrics['max_drawdown']:.1%})\n\n"
            f"Melhorou em: {', '.join(comparison['metrics_better'])}"
        )
    else:
        # Arquivar desafiante rejeitado
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        arch = ARCHIVE_DIR / f"bonnie_challenger_v{challenger_ver}.pkl"
        if not arch.exists():
            shutil.copy2(challenger_path, arch)
        _write_atomic(MODELS_DIR / f"bonnie_challenger_v{challenger_ver}_meta.json", meta)
        _update_registry(challenger_ver, ch_metrics, "rejected")

        def _row(label: str, ch: float, ca: float, lower_is_better: bool = False) -> str:
            winner = "✓" if (ch < ca if lower_is_better else ch > ca) else "—"
            return f"{label:<16} {ch:.3f}   {ca:.3f}   {winner}"

        table = "\n".join([
            "Métrica          Desaf.  Campeão  Melhor",
            "─" * 42,
            _row("Win Rate",      ch_metrics["win_rate"],      ca_metrics["win_rate"]),
            _row("Factor lucro",  ch_metrics["profit_factor"], ca_metrics["profit_factor"]),
            _row("Qualid. lucros",ch_metrics["sharpe"],        ca_metrics["sharpe"]),
            _row("Pior queda",    ch_metrics["max_drawdown"],  ca_metrics["max_drawdown"], True),
        ])
        _send_telegram(
            f"📊 <b>Desafiante v{challenger_ver} não promovido</b>\n\n"
            f"<pre>{table}</pre>\n\n"
            f"Motivo: {rejection_reason}"
        )

    print(
        f"[{_now_iso()}] === evaluate_challenger END === verdict={verdict}",
        flush=True,
    )


if __name__ == "__main__":
    run()
