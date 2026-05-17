"""
Learner — Fase 3: Closed-Loop Evolution Engine

Duas responsabilidades:

  A. Infraestrutura de parâmetros — get_active_params() com 3 camadas defensivas:
       ficheiro ausente → defaults silenciosos
       hash inválido    → log_error + defaults
       bounds violados  → log_error + defaults
     strategy.py e cro.py importam esta função; nunca crasham por falha do Learner.

  B. Motor de optimização (Fase 3) — run_learner_cycle() com Coordinate Descent
     bounded e walk-forward validation. Activado apenas com trades suficientes.
     Três horizontes:
       Semanal    [≥ 20 trades] → Clyde: limiares RSI/volume (toda segunda-feira)
       Mensal     [≥ 50 trades] → Bonnie: thresholds e size factor (dia 1 do mês)
       Trimestral [≥100 trades] → CRO: drawdown, stops, sectores (dia 1 do trimestre)

Algoritmo: Coordinate Descent com perturbações aleatórias bounded + EMA smoothing (α=0.30).
Fitness:   Profit Factor × Calmar Factor × regularização L2 vs. defaults.
Anti-overfitting: walk-forward 85/15, EMA smooth, L2 penalty, mínimos amostrais por horizonte.

Fase 2 preservada: analyse_recent_trades(), detect_error_patterns(),
                   suggest_parameter_adjustments(), generate_weekly_report().
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DATA_BETA_DIR, LOGS_TRADES_DIR, RISK_CONFIG
from .logger import log_decision, log_error


# ===========================================================================
# A. INFRAESTRUTURA DE PARÂMETROS
# ===========================================================================

OPTIMIZED_PARAMS_PATH = DATA_BETA_DIR / "optimized_parameters.json"

# Limites de activação por horizonte
_MIN_TRADES_WEEKLY    = 20
_MIN_TRADES_MONTHLY   = 50
_MIN_TRADES_QUARTERLY = 100

# EMA smoothing: apenas 30% da mutação proposta é aplicada por ciclo
_EMA_ALPHA    = 0.30
# Ganho mínimo de fitness para aceitar mutação (5%)
_MIN_GAIN_PCT = 0.05
# Força da regularização L2 (penaliza afastamento dos defaults)
_LAMBDA_L2    = 0.15
# Fracção de holdout no walk-forward
_VAL_SPLIT    = 0.15
# Iterações de coordinate descent por horizonte
_N_ITER: dict[str, int] = {"weekly": 60, "monthly": 80, "quarterly": 40}

# ---------------------------------------------------------------------------
# Defaults — espelho fiel dos valores hardcoded actuais
# ---------------------------------------------------------------------------

_DEFAULT_PARAMS: dict[str, Any] = {
    "enabled_styles": ["VALUE", "MOMENTUM"],
    "weekly": {
        "clyde": {
            "rsi_oversold_ceiling":    35,
            "rsi_momentum_min":        40,
            "rsi_momentum_max":        55,
            "rsi_exit_floor":          72,
            "vol_ratio_oversold_min":  1.2,
            "vol_ratio_momentum_min":  1.8,
            # MOMENTUM engine params
            "momentum_rsi_floor":      65,
            "momentum_vol_min":        1.5,
            "momentum_atr_multiplier": 2.5,
        }
    },
    "monthly": {
        "bonnie": {
            "base_threshold":        0.60,
            "strict_threshold":      0.64,
            "strict_trigger_wr":     0.45,
            "size_factor_pct":       0.15,
            # MOMENTUM filter params
            "momentum_vol_floor":    1.0,
            "momentum_gap_down_pct": 3.0,
        }
    },
    "quarterly": {
        "cro": {
            "max_drawdown_limit_pct":   15.0,
            "elastic_window_n":         25,
            "elastic_fallback_wr":      0.48,
            "stop_loss_pct":            5.0,
            "take_profit_pct":          10.0,
            "max_positions_per_sector": 2,
        }
    },
}

# Hard bounds — nenhum valor pode sair daqui (sanity check E coordinate descent)
_PARAM_SPACE: dict[str, dict] = {
    # weekly.clyde — VALUE ─────────────────────────────────────────────
    "rsi_oversold_ceiling":     {"min": 28,   "max": 45,   "step": 1.0,  "kind": "int"},
    "rsi_momentum_min":         {"min": 35,   "max": 52,   "step": 1.0,  "kind": "int"},
    "rsi_momentum_max":         {"min": 50,   "max": 65,   "step": 1.0,  "kind": "int"},
    "rsi_exit_floor":           {"min": 65,   "max": 82,   "step": 1.0,  "kind": "int"},
    "vol_ratio_oversold_min":   {"min": 1.0,  "max": 2.0,  "step": 0.1,  "kind": "float"},
    "vol_ratio_momentum_min":   {"min": 1.4,  "max": 2.8,  "step": 0.1,  "kind": "float"},
    # weekly.clyde — MOMENTUM ──────────────────────────────────────────
    "momentum_rsi_floor":       {"min": 60,   "max": 75,   "step": 1.0,  "kind": "int"},
    "momentum_vol_min":         {"min": 1.2,  "max": 2.5,  "step": 0.1,  "kind": "float"},
    "momentum_atr_multiplier":  {"min": 1.5,  "max": 4.0,  "step": 0.25, "kind": "float"},
    # monthly.bonnie — VALUE ───────────────────────────────────────────
    "base_threshold":           {"min": 0.52, "max": 0.72, "step": 0.01, "kind": "float"},
    "strict_threshold":         {"min": 0.58, "max": 0.78, "step": 0.01, "kind": "float"},
    "strict_trigger_wr":        {"min": 0.35, "max": 0.55, "step": 0.01, "kind": "float"},
    "size_factor_pct":          {"min": 0.08, "max": 0.22, "step": 0.01, "kind": "float"},
    # monthly.bonnie — MOMENTUM ────────────────────────────────────────
    "momentum_vol_floor":       {"min": 0.8,  "max": 1.5,  "step": 0.1,  "kind": "float"},
    "momentum_gap_down_pct":    {"min": 1.5,  "max": 6.0,  "step": 0.5,  "kind": "float"},
    # quarterly.cro ────────────────────────────────────────────────────
    "max_drawdown_limit_pct":   {"min": 10.0, "max": 20.0, "step": 0.5,  "kind": "float"},
    "elastic_window_n":         {"min": 15,   "max": 40,   "step": 1.0,  "kind": "int"},
    "elastic_fallback_wr":      {"min": 0.42, "max": 0.55, "step": 0.01, "kind": "float"},
    "stop_loss_pct":            {"min": 3.5,  "max": 8.0,  "step": 0.5,  "kind": "float"},
    "take_profit_pct":          {"min": 7.0,  "max": 18.0, "step": 0.5,  "kind": "float"},
    "max_positions_per_sector": {"min": 1,    "max": 3,    "step": 1.0,  "kind": "int"},
}


# ---------------------------------------------------------------------------
# API pública — params
# ---------------------------------------------------------------------------

def get_active_params() -> dict[str, Any]:
    """
    Carrega parâmetros optimizados com 3 camadas defensivas. Nunca lança excepção.

    Retorna sempre um dict completo: optimizado se válido, defaults caso contrário.
    """
    try:
        if not OPTIMIZED_PARAMS_PATH.exists():
            return _merge_with_defaults({})

        with open(OPTIMIZED_PARAMS_PATH, encoding="utf-8") as f:
            stored = json.load(f)

        if not _verify_integrity(stored):
            log_error("learner_integrity", {
                "msg": "optimized_parameters.json tem hash inválido — a usar defaults",
            })
            return _merge_with_defaults({})

        merged = _merge_with_defaults(stored)

        if not _sanity_check(merged):
            log_error("learner_sanity", {
                "msg": "Parâmetros fora dos hard bounds — a usar defaults",
            })
            return _merge_with_defaults({})

        return merged

    except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
        log_error("learner_load", {"error": str(exc)})
        return _merge_with_defaults({})


# ---------------------------------------------------------------------------
# Integridade, validação e merge — privadas
# ---------------------------------------------------------------------------

def _compute_hash(data: dict) -> str:
    """SHA-256 do JSON canónico sem o campo integrity_hash."""
    payload = copy.deepcopy(data)
    payload.get("_meta", {}).pop("integrity_hash", None)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _verify_integrity(data: dict) -> bool:
    """True se hash correcto ou ausente (ficheiro editado manualmente é aceite)."""
    stored_hash = data.get("_meta", {}).get("integrity_hash", "")
    if not stored_hash:
        log_decision("learner_integrity", "no_hash",
                     {"msg": "Ficheiro sem hash — aceitando sem verificação"})
        return True
    return _compute_hash(data) == stored_hash


def _sanity_check(params: dict) -> bool:
    """Verifica que todos os valores estão dentro dos hard bounds do _PARAM_SPACE."""
    sections = [
        params.get("weekly",    {}).get("clyde",  {}),
        params.get("monthly",   {}).get("bonnie", {}),
        params.get("quarterly", {}).get("cro",    {}),
    ]
    for section in sections:
        for name, value in section.items():
            spec = _PARAM_SPACE.get(name)
            if spec is None:
                continue
            try:
                if not (spec["min"] <= float(value) <= spec["max"]):
                    log_error("learner_bounds_violated", {
                        "param": name, "value": value,
                        "min": spec["min"], "max": spec["max"],
                    })
                    return False
            except (TypeError, ValueError):
                log_error("learner_bounds_type_error", {"param": name, "value": value})
                return False
    return True


def _merge_with_defaults(stored: dict) -> dict[str, Any]:
    """Deep merge: stored sobrepõe defaults; chaves ausentes herdam defaults.

    Apenas aceita chaves conhecidas do _DEFAULT_PARAMS — previne injecção.
    enabled_styles é gerido ao nível raiz; failsafe: nunca desactiva ambos.
    """
    result = copy.deepcopy(_DEFAULT_PARAMS)

    # Top-level enabled_styles — validar antes de aceitar
    stored_styles = stored.get("enabled_styles")
    if isinstance(stored_styles, list):
        valid = [s for s in stored_styles if s in ("VALUE", "MOMENTUM")]
        if valid:  # nunca desactiva ambos os estilos
            result["enabled_styles"] = valid

    for horizon in ("weekly", "monthly", "quarterly"):
        stored_h = stored.get(horizon, {})
        for sub_key in result[horizon]:
            stored_sub = stored_h.get(sub_key, {})
            known_keys = result[horizon][sub_key].keys()
            result[horizon][sub_key].update(
                {k: v for k, v in stored_sub.items() if k in known_keys}
            )
    return result


def _save_params(params: dict, meta_extra: dict | None = None) -> None:
    """Escrita atómica de optimized_parameters.json com hash de integridade."""
    payload = copy.deepcopy(params)
    payload.setdefault("_meta", {})
    payload["_meta"].update({
        "schema_version": "3.0",
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **(meta_extra or {}),
    })
    payload["_meta"]["integrity_hash"] = _compute_hash(payload)

    OPTIMIZED_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OPTIMIZED_PARAMS_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(OPTIMIZED_PARAMS_PATH)
    except OSError as exc:
        log_error("learner_save", {"error": str(exc)})


# ===========================================================================
# B. MOTOR DE OPTIMIZAÇÃO — Coordinate Descent (Fase 3)
# ===========================================================================

def run_learner_cycle() -> None:
    """Ponto de entrada chamado pelo phase0.py no final de cada ciclo.

    Verifica a data actual e activa o(s) horizonte(s) correspondentes.
    Se não houver trades suficientes, retorna silenciosamente em < 1 ms.
    """
    trades = _load_beta_trades()
    today  = datetime.now(timezone.utc)

    ran_any = False

    # Semanal: toda segunda-feira
    if today.weekday() == 0 and len(trades) >= _MIN_TRADES_WEEKLY:
        _run_weekly(trades)
        ran_any = True

    # Mensal: primeiro dia de cada mês
    if today.day == 1 and len(trades) >= _MIN_TRADES_MONTHLY:
        _run_monthly(trades)
        ran_any = True

    # Trimestral: 1 Jan, 1 Abr, 1 Jul, 1 Out
    if today.day == 1 and today.month in {1, 4, 7, 10} and len(trades) >= _MIN_TRADES_QUARTERLY:
        _run_quarterly(trades)
        ran_any = True

    if not ran_any:
        log_decision("learner_cycle", "skipped", {
            "n_trades": len(trades),
            "min_weekly": _MIN_TRADES_WEEKLY,
            "weekday":    today.weekday(),
        })


# ---------------------------------------------------------------------------
# Horizontes
# ---------------------------------------------------------------------------

def _run_weekly(trades: list[dict]) -> None:
    stored  = get_active_params()
    current = stored["weekly"]["clyde"]
    params  = list(current.keys())

    train, val = _split_trades(trades)
    f_before   = _fitness_clyde(current, train)

    optimized, f_after = _coordinate_descent(
        param_names=params,
        current=current,
        defaults=_DEFAULT_PARAMS["weekly"]["clyde"],
        trades_train=train,
        trades_val=val,
        fitness_fn=_fitness_clyde,
        n_iter=_N_ITER["weekly"],
    )

    params_changed = f_after > f_before * (1 + _MIN_GAIN_PCT)
    if params_changed:
        stored["weekly"]["clyde"] = optimized
        stored["weekly"].update({
            "last_optimized": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generation":     stored.get("weekly", {}).get("generation", 0) + 1,
            "sample_size":    len(trades),
            "fitness_before": round(f_before, 4),
            "fitness_after":  round(f_after, 4),
        })
        _notify_mutation("Semanal · Clyde", current, optimized, f_before, f_after, len(trades))
        log_decision("learner_weekly", "params_updated", {
            "f_before": round(f_before, 4), "f_after": round(f_after, 4),
        })
    else:
        log_decision("learner_weekly", "no_improvement", {
            "f_before": round(f_before, 4), "f_after": round(f_after, 4),
        })

    # Avalia activação/desactivação por estilo (independente da optimização de params)
    style_changed = _evaluate_style_toggle(trades, stored)

    if params_changed or style_changed:
        _save_params(stored)


def _run_monthly(trades: list[dict]) -> None:
    stored  = get_active_params()
    current = stored["monthly"]["bonnie"]
    params  = list(current.keys())

    train, val = _split_trades(trades)
    f_before   = _fitness_bonnie(current, train)

    optimized, f_after = _coordinate_descent(
        param_names=params,
        current=current,
        defaults=_DEFAULT_PARAMS["monthly"]["bonnie"],
        trades_train=train,
        trades_val=val,
        fitness_fn=_fitness_bonnie,
        n_iter=_N_ITER["monthly"],
    )

    if f_after > f_before * (1 + _MIN_GAIN_PCT):
        stored["monthly"]["bonnie"] = optimized
        stored["monthly"].update({
            "last_optimized": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generation":     stored.get("monthly", {}).get("generation", 0) + 1,
            "sample_size":    len(trades),
            "fitness_before": round(f_before, 4),
            "fitness_after":  round(f_after, 4),
        })
        _save_params(stored)
        _notify_mutation("Mensal · Bonnie", current, optimized, f_before, f_after, len(trades))
    else:
        log_decision("learner_monthly", "no_improvement", {
            "f_before": round(f_before, 4), "f_after": round(f_after, 4),
        })


def _run_quarterly(trades: list[dict]) -> None:
    stored  = get_active_params()
    current = stored["quarterly"]["cro"]
    params  = list(current.keys())

    train, val = _split_trades(trades)
    f_before   = _fitness_cro(current, train)

    optimized, f_after = _coordinate_descent(
        param_names=params,
        current=current,
        defaults=_DEFAULT_PARAMS["quarterly"]["cro"],
        trades_train=train,
        trades_val=val,
        fitness_fn=_fitness_cro,
        n_iter=_N_ITER["quarterly"],
    )

    if f_after > f_before * (1 + _MIN_GAIN_PCT):
        stored["quarterly"]["cro"] = optimized
        stored["quarterly"].update({
            "last_optimized": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generation":     stored.get("quarterly", {}).get("generation", 0) + 1,
            "sample_size":    len(trades),
            "fitness_before": round(f_before, 4),
            "fitness_after":  round(f_after, 4),
        })
        _save_params(stored)
        _notify_mutation("Trimestral · CRO", current, optimized, f_before, f_after, len(trades))
    else:
        log_decision("learner_quarterly", "no_improvement", {
            "f_before": round(f_before, 4), "f_after": round(f_after, 4),
        })


# ---------------------------------------------------------------------------
# Motor: Coordinate Descent
# ---------------------------------------------------------------------------

def _coordinate_descent(
    param_names:  list[str],
    current:      dict[str, Any],
    defaults:     dict[str, Any],
    trades_train: list[dict],
    trades_val:   list[dict],
    fitness_fn,
    n_iter:       int,
) -> tuple[dict[str, Any], float]:
    """
    Optimiza um parâmetro de cada vez com perturbações aleatórias bounded.

    Para cada iteração:
      1. Escolhe param aleatório da lista
      2. Propõe p_new = p ± step (clamped aos hard bounds)
      3. Calcula fitness no treino
      4. Valida no holdout (walk-forward)
      5. Se melhorar ≥ MIN_GAIN_PCT: aplica EMA smooth e aceita
    """
    best    = copy.deepcopy(current)
    f_train = fitness_fn(best, trades_train)
    f_val   = fitness_fn(best, trades_val) if trades_val else f_train

    for _ in range(n_iter):
        name = random.choice(param_names)
        spec = _PARAM_SPACE.get(name)
        if spec is None:
            continue

        direction  = random.choice([-1, 1])
        step       = spec["step"]
        raw        = float(best[name]) + direction * step
        clamped    = max(spec["min"], min(spec["max"], raw))
        candidate  = int(round(clamped)) if spec["kind"] == "int" else round(clamped, 6)

        trial          = copy.deepcopy(best)
        trial[name]    = candidate
        f_trial_train  = fitness_fn(trial, trades_train)
        f_trial_val    = fitness_fn(trial, trades_val) if trades_val else f_trial_train

        improves_train = f_trial_train > f_train * (1 + _MIN_GAIN_PCT)
        passes_val     = f_trial_val   > f_val   * 0.85  # tolera 15% degradação

        if improves_train and passes_val:
            smoothed    = _ema_smooth(float(best[name]), float(candidate))
            smoothed_v  = int(round(smoothed)) if spec["kind"] == "int" else round(smoothed, 6)
            best[name]  = smoothed_v
            f_train     = fitness_fn(best, trades_train)
            f_val       = fitness_fn(best, trades_val) if trades_val else f_train

    # Regularização L2 final — penaliza desvio acumulado dos defaults
    f_final = fitness_fn(best, trades_train) * _l2_penalty(best, defaults)
    return best, f_final


def _ema_smooth(old: float, new_proposed: float, alpha: float = _EMA_ALPHA) -> float:
    """Blenda mutação com estado actual. Previne saltos bruscos de parâmetros."""
    return old * (1 - alpha) + new_proposed * alpha


def _l2_penalty(params: dict, defaults: dict) -> float:
    """Factor multiplicativo [0.85, 1.0] que penaliza afastamento dos defaults."""
    total_sq = 0.0
    count    = 0
    for name, val in params.items():
        spec = _PARAM_SPACE.get(name)
        if spec is None:
            continue
        param_range = spec["max"] - spec["min"]
        if param_range > 0:
            delta      = (float(val) - float(defaults.get(name, val))) / param_range
            total_sq  += delta ** 2
            count     += 1
    if count == 0:
        return 1.0
    return max(0.85, 1.0 - _LAMBDA_L2 * (total_sq / count))


# ---------------------------------------------------------------------------
# Funções de fitness por subsistema
# ---------------------------------------------------------------------------

def _fitness_clyde(params: dict, trades: list[dict]) -> float:
    """
    Profit Factor × Calmar Factor para trades que passariam os filtros do Clyde.

    Simula retroactivamente: usa o contexto guardado em cada trade para decidir
    se o Clyde teria entrado com os novos params. Não requer dados OHLCV.
    """
    accepted = [t for t in trades if _would_clyde_enter(t, params)]
    return _profit_factor_calmar(accepted)


def _fitness_bonnie(params: dict, trades: list[dict]) -> float:
    """Profit Factor × Calmar Factor para trades com signal_strength ≥ threshold."""
    threshold = params.get("base_threshold", 0.60)
    accepted  = [
        t for t in trades
        if (t.get("signal_strength") or t.get("context", {}).get("signal_strength", 0)) >= threshold
    ]
    return _profit_factor_calmar(accepted)


def _fitness_cro(params: dict, trades: list[dict]) -> float:
    """
    Calmar Ratio puro — horizonte trimestral foca na protecção de capital,
    não no profit factor. Penaliza drawdown acima do limite configurado.
    """
    if not trades:
        return 0.5
    results  = [t.get("result_eur", 0) or 0 for t in trades]
    total_pnl = sum(results)
    drawdown  = _max_drawdown_from_results(results)
    max_dd    = params.get("max_drawdown_limit_pct", 15.0)
    dd_penalty = max(0.3, 1.0 - drawdown / max_dd) if max_dd > 0 else 0.3
    annualised = total_pnl / max(1, len(trades)) * 52  # proxy semanal
    calmar     = annualised / max(drawdown, 0.01)
    return max(0.01, min(calmar * dd_penalty, 10.0))


def _calmar_from_trades(trades: list[dict]) -> float:
    """Calmar Ratio = total_return / max_drawdown. Retorna 0.0 com < 3 trades."""
    if len(trades) < 3:
        return 0.0
    results  = [t.get("result_eur", 0) or 0 for t in trades]
    total    = sum(results)
    drawdown = _max_drawdown_from_results(results)
    if drawdown < 0.01:
        return 1.0 if total >= 0 else -1.0
    return round(total / drawdown, 4)


def _would_momentum_enter(trade: dict, params: dict) -> bool:
    """True se o trade MOMENTUM teria sido gerado com os params dados."""
    if trade.get("side") != "BUY":
        return True
    ctx   = trade.get("context", {})
    style = trade.get("style", "VALUE")
    if style != "MOMENTUM":
        return False
    rsi               = ctx.get("rsi_14")
    vol               = ctx.get("volume_ratio_vs_avg", 1.0)
    ema20_above_ema50 = ctx.get("ema20_above_ema50", False)
    price_above_ema20 = ctx.get("price_above_ema20", False)
    if rsi is None:
        return True
    m_floor = params.get("momentum_rsi_floor", 65)
    m_vol   = params.get("momentum_vol_min", 1.5)
    return rsi >= m_floor and ema20_above_ema50 and price_above_ema20 and vol >= m_vol


def _fitness_momentum(params: dict, trades: list[dict]) -> float:
    """Profit Factor × Calmar Factor para trades MOMENTUM com trailing stop."""
    accepted = [t for t in trades if _would_momentum_enter(t, params)]
    return _profit_factor_calmar(accepted)


def _evaluate_style_toggle(trades: list[dict], stored: dict) -> bool:
    """Actualiza enabled_styles com base no Calmar Ratio por estilo.

    Desactiva um estilo se o seu Calmar for negativo (capital destruction).
    Failsafe: nunca desactiva ambos — mantém pelo menos um estilo activo.

    Retorna True se houve alteração em enabled_styles.
    """
    value_trades    = [t for t in trades if t.get("style", "VALUE") == "VALUE"]
    momentum_trades = [t for t in trades if t.get("style") == "MOMENTUM"]

    calmar_v = _calmar_from_trades(value_trades)    if len(value_trades)    >= 10 else None
    calmar_m = _calmar_from_trades(momentum_trades) if len(momentum_trades) >= 10 else None

    current_styles = stored.get("enabled_styles", ["VALUE", "MOMENTUM"])
    new_styles     = list(current_styles)

    if calmar_v is not None and calmar_v < 0 and "MOMENTUM" in new_styles:
        new_styles = [s for s in new_styles if s != "VALUE"]
    if calmar_m is not None and calmar_m < 0 and "VALUE" in new_styles:
        new_styles = [s for s in new_styles if s != "MOMENTUM"]

    if not new_styles:  # failsafe: nunca desactiva ambos
        new_styles = ["VALUE"]

    changed = sorted(new_styles) != sorted(current_styles)
    if changed:
        stored["enabled_styles"] = new_styles
        log_decision("learner_style_toggle", "updated", {
            "calmar_value":    calmar_v,
            "calmar_momentum": calmar_m,
            "old_styles":      current_styles,
            "new_styles":      new_styles,
        })
    return changed


def _would_clyde_enter(trade: dict, params: dict) -> bool:
    """True se o trade teria sido gerado com os params dados (baseado no contexto guardado)."""
    if trade.get("side") != "BUY":
        return True  # saídas são sempre incluídas
    ctx = trade.get("context", {})
    rsi      = ctx.get("rsi_14")
    vol      = ctx.get("volume_ratio_vs_avg", 1.0)
    ema_up   = ctx.get("ema50_above_ema200", True)
    if rsi is None:
        return True  # sem dados de contexto: aceitar por defeito

    ceil_os  = params.get("rsi_oversold_ceiling", 35)
    vol_os   = params.get("vol_ratio_oversold_min", 1.2)
    m_min    = params.get("rsi_momentum_min", 40)
    m_max    = params.get("rsi_momentum_max", 55)
    vol_mom  = params.get("vol_ratio_momentum_min", 1.8)

    rule_a = rsi <= ceil_os and ema_up and vol >= vol_os
    rule_b = m_min <= rsi <= m_max and ema_up and vol >= vol_mom
    return rule_a or rule_b


def _profit_factor_calmar(trades: list[dict]) -> float:
    """Profit Factor × Calmar Factor. Retorna 0.5 se sem trades aceitáveis."""
    if not trades:
        return 0.5
    results   = [t.get("result_eur", 0) or 0 for t in trades]
    gross_win = sum(r for r in results if r > 0)
    gross_los = abs(sum(r for r in results if r < 0))
    pf        = gross_win / (gross_los + 0.01)
    drawdown  = _max_drawdown_from_results(results)
    calmar_f  = max(0.4, 1.0 - drawdown / 15.0)
    return round(min(pf * calmar_f, 10.0), 4)


def _max_drawdown_from_results(results: list[float]) -> float:
    """Drawdown máximo (%) a partir de uma sequência de P&L por trade."""
    peak = total = max_dd = 0.0
    for r in results:
        total += r
        if total > peak:
            peak = total
        if peak > 0:
            max_dd = max(max_dd, (peak - total) / peak * 100)
    return round(max_dd, 2)


def _split_trades(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    """Divide trades em treino/validação (walk-forward 85/15). Ordenados por data."""
    sorted_t  = sorted(trades, key=lambda t: t.get("datetime", ""))
    split_at  = max(1, int(len(sorted_t) * (1 - _VAL_SPLIT)))
    return sorted_t[:split_at], sorted_t[split_at:]


# ---------------------------------------------------------------------------
# Notificação de mutação
# ---------------------------------------------------------------------------

def _notify_mutation(
    label:    str,
    old:      dict,
    new:      dict,
    f_before: float,
    f_after:  float,
    n_trades: int,
) -> None:
    """Envia resumo conciso das mutações ao Telegram. Silencioso se sem alterações."""
    changes = {
        k: (old[k], new[k])
        for k in old
        if abs(float(old[k]) - float(new[k])) > 1e-9
    }
    if not changes:
        return
    try:
        from .notifier import enviar_alerta
        ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        linhas = [
            f"⚙️ Learner — Mutação {label}",
            "",
            f"Fitness: {f_before:.2f} → {f_after:.2f}",
            "",
        ]
        for param, (old_v, new_v) in changes.items():
            linhas.append(f"• {param}: {old_v} → {new_v}")
        linhas += ["", f"Trades: {n_trades}  ·  FundScope · {ts} UTC"]
        enviar_alerta("\n".join(linhas), silencioso=True)
    except Exception as exc:
        log_error("learner_notify", {"error": str(exc)})


# ---------------------------------------------------------------------------
# Carregamento de beta_trades.json (fonte para o motor de optimização)
# ---------------------------------------------------------------------------

def _load_beta_trades() -> list[dict]:
    """Carrega trades fechados de data/beta/beta_trades.json."""
    path = DATA_BETA_DIR / "beta_trades.json"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        trades  = data.get("trades", []) if isinstance(data, dict) else []
        return [t for t in trades if t.get("result_eur") is not None]
    except (json.JSONDecodeError, OSError) as exc:
        log_error("learner_load_trades", {"error": str(exc)})
        return []


# ===========================================================================
# C. ANÁLISE E RELATÓRIOS (Fase 2 — preservado)
# ===========================================================================

def analyse_recent_trades(days: int = 7) -> dict:
    """Estatísticas de performance para trades fechados nos últimos `days` dias."""
    trades = _load_log_trades(days)
    closed = [t for t in trades if t.get("result_eur") is not None]

    if not closed:
        return {"period_days": days, "n_closed": 0, "note": "Sem trades fechados no período."}

    wins      = [t for t in closed if t["result_eur"] >= 0]
    losses    = [t for t in closed if t["result_eur"] <  0]
    total_pnl = sum(t["result_eur"] for t in closed)
    avg_win   = sum(t["result_eur"] for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum(t["result_eur"] for t in losses) / len(losses) if losses else 0.0
    win_rate  = len(wins) / len(closed) * 100

    best  = max(closed, key=lambda t: t["result_eur"])
    worst = min(closed, key=lambda t: t["result_eur"])

    by_ticker: dict[str, list[float]] = defaultdict(list)
    for t in closed:
        by_ticker[t.get("ticker", "?")].append(t["result_eur"])

    ticker_stats = {
        ticker: {
            "n_trades":     len(rs),
            "total_pnl":    round(sum(rs), 2),
            "win_rate_pct": round(sum(1 for r in rs if r >= 0) / len(rs) * 100, 1),
        }
        for ticker, rs in by_ticker.items()
    }

    return {
        "period_days":   days,
        "n_closed":      len(closed),
        "n_wins":        len(wins),
        "n_losses":      len(losses),
        "win_rate_pct":  round(win_rate, 1),
        "total_pnl_eur": round(total_pnl, 2),
        "avg_win_eur":   round(avg_win, 2),
        "avg_loss_eur":  round(avg_loss, 2),
        "best_trade":    {"ticker": best.get("ticker"), "result_eur": best["result_eur"]},
        "worst_trade":   {"ticker": worst.get("ticker"), "result_eur": worst["result_eur"]},
        "by_ticker":     ticker_stats,
    }


def detect_error_patterns() -> list[dict]:
    """Detecta padrões de erro recorrentes nos últimos 30 dias de trades."""
    trades = _load_log_trades(30)
    closed = [t for t in trades if t.get("result_eur") is not None]
    patterns: list[dict] = []

    lv_losses = [
        t for t in closed
        if t.get("result_eur", 0) < 0
        and t.get("context", {}).get("volume_ratio_vs_avg", 1.0) < 1.0
    ]
    if len(lv_losses) >= 2:
        avg = sum(t["result_eur"] for t in lv_losses) / len(lv_losses)
        patterns.append({
            "pattern": "low_volume_entry",
            "description": (
                f"{len(lv_losses)} entradas com volume < 1.0× a média "
                f"resultaram em perda (média {avg:.2f}€)"
            ),
            "frequency": len(lv_losses),
            "avg_loss_eur": round(avg, 2),
            "suggestion": "Aumentar limiar mínimo de volume_ratio para 1.2 antes de entrar.",
            "affected_trades": [t.get("id") for t in lv_losses],
        })

    hr_losses = [
        t for t in closed
        if t.get("result_eur", 0) < 0
        and (t.get("context", {}).get("rsi_14") or 0) > 65
    ]
    if len(hr_losses) >= 2:
        avg = sum(t["result_eur"] for t in hr_losses) / len(hr_losses)
        patterns.append({
            "pattern": "high_rsi_entry",
            "description": (
                f"{len(hr_losses)} entradas com RSI > 65 "
                f"resultaram em perda (média {avg:.2f}€)"
            ),
            "frequency": len(hr_losses),
            "avg_loss_eur": round(avg, 2),
            "suggestion": "Restringir entradas BUY a RSI < 60. Rever limiar de saída.",
            "affected_trades": [t.get("id") for t in hr_losses],
        })

    ct_losses = [
        t for t in closed
        if t.get("result_eur", 0) < 0
        and t.get("side") == "BUY"
        and t.get("context", {}).get("ema50_above_ema200") is False
    ]
    if len(ct_losses) >= 1:
        avg = sum(t["result_eur"] for t in ct_losses) / len(ct_losses)
        patterns.append({
            "pattern": "counter_trend_buy",
            "description": (
                f"{len(ct_losses)} compras com EMA-50 < EMA-200 "
                f"resultaram em perda (média {avg:.2f}€)"
            ),
            "frequency": len(ct_losses),
            "avg_loss_eur": round(avg, 2),
            "suggestion": "Bloquear BUY quando EMA-50 < EMA-200.",
            "affected_trades": [t.get("id") for t in ct_losses],
        })

    return patterns


def suggest_parameter_adjustments() -> list[dict]:
    """Propõe ajustes de parâmetros baseados em performance recente.

    Nenhum ajuste é aplicado automaticamente — requerem aprovação humana
    (ou são aplicados pelo run_learner_cycle() quando activo).
    """
    stats    = analyse_recent_trades(days=14)
    patterns = detect_error_patterns()
    suggestions: list[dict] = []

    win_rate = stats.get("win_rate_pct", 50.0)
    if stats.get("n_closed", 0) >= 5 and win_rate < 45:
        suggestions.append({
            "parameter":    "rsi_entry_ceiling",
            "current_value":  55,
            "proposed_value": 45,
            "reason":       (
                f"Win rate de {win_rate:.1f}% abaixo do limiar aceitável (45%). "
                "Entradas mais conservadoras (RSI < 45) devem melhorar a selectividade."
            ),
            "confidence": "média",
        })

    avg_win  = stats.get("avg_win_eur", 0.0)
    avg_loss = abs(stats.get("avg_loss_eur", 0.0))
    if avg_win > 0 and avg_loss > avg_win * 1.5:
        suggestions.append({
            "parameter":    "stop_loss_pct",
            "current_value":  RISK_CONFIG["stop_loss_pct"],
            "proposed_value": round(max(2.0, RISK_CONFIG["stop_loss_pct"] - 1.0), 1),
            "reason":       (
                f"Perda média ({avg_loss:.2f}€) é {avg_loss/avg_win:.1f}× o ganho médio. "
                "Reduzir stop loss para melhorar rácio risco/recompensa."
            ),
            "confidence": "alta",
        })

    for p in patterns:
        if p["pattern"] == "low_volume_entry":
            suggestions.append({
                "parameter":    "min_volume_ratio_entry",
                "current_value":  1.0,
                "proposed_value": 1.2,
                "reason":       p["description"],
                "confidence":   "alta",
            })

    return suggestions


def generate_weekly_report() -> str:
    """Gera e guarda o relatório semanal de performance em data/beta/beta_weekly_report.txt."""
    stats       = analyse_recent_trades(days=7)
    patterns    = detect_error_patterns()
    adjustments = suggest_parameter_adjustments()

    lines = [
        "=" * 60,
        "FundScope Bot — Relatório Semanal",
        "Período: últimos 7 dias",
        "=" * 60,
        "",
    ]

    if stats.get("n_closed", 0) == 0:
        lines += ["Sem trades fechados neste período.", ""]
    else:
        lines += [
            "PERFORMANCE",
            f"  Trades fechados : {stats['n_closed']}",
            f"  Vitórias        : {stats['n_wins']} ({stats['win_rate_pct']}%)",
            f"  Derrotas        : {stats['n_losses']}",
            f"  P&L total       : {stats['total_pnl_eur']:+.2f}€",
            f"  Ganho médio     : +{stats['avg_win_eur']:.2f}€",
            f"  Perda média     : {stats['avg_loss_eur']:.2f}€",
            f"  Melhor trade    : {stats['best_trade']['ticker']} "
            f"{stats['best_trade']['result_eur']:+.2f}€",
            f"  Pior trade      : {stats['worst_trade']['ticker']} "
            f"{stats['worst_trade']['result_eur']:+.2f}€",
            "",
        ]
        if stats.get("by_ticker"):
            lines.append("POR TICKER")
            for tk, ts in stats["by_ticker"].items():
                lines.append(
                    f"  {tk:<8} {ts['n_trades']} trades · "
                    f"win {ts['win_rate_pct']}% · P&L {ts['total_pnl']:+.2f}€"
                )
            lines.append("")

    if patterns:
        lines.append("PADRÕES DE ERRO DETECTADOS")
        for p in patterns:
            lines.append(f"  ⚠ {p['description']}")
            lines.append(f"    → {p['suggestion']}")
        lines.append("")
    else:
        lines += ["PADRÕES DE ERRO: Nenhum padrão significativo detectado.", ""]

    if adjustments:
        lines.append("AJUSTES SUGERIDOS (requerem aprovação manual ou run_learner_cycle)")
        for a in adjustments:
            lines.append(f"  {a['parameter']}: {a['current_value']} → {a['proposed_value']}")
            lines.append(f"    Razão     : {a['reason']}")
            lines.append(f"    Confiança : {a['confidence']}")
        lines.append("")
    else:
        lines += ["AJUSTES: Nenhum ajuste sugerido neste ciclo.", ""]

    lines += [
        "Nota: ajustes automáticos requerem trade suficientes para activar o Learner.",
        "=" * 60,
    ]

    report = "\n".join(lines)
    _save_weekly_report(report)
    log_decision("learner_weekly_report", "generated", {
        "n_patterns":    len(patterns),
        "n_suggestions": len(adjustments),
        "win_rate_pct":  stats.get("win_rate_pct"),
    })
    return report


# ---------------------------------------------------------------------------
# Helpers de I/O (Fase 2)
# ---------------------------------------------------------------------------

def _load_log_trades(days: int) -> list[dict]:
    """Lê registos de logs/trades/YYYY-MM-DD.json dos últimos `days` dias."""
    all_trades: list[dict] = []
    today = date.today()
    for i in range(days):
        path = LOGS_TRADES_DIR / f"{(today - timedelta(days=i)).isoformat()}.json"
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                records = json.load(f)
            if isinstance(records, list):
                all_trades.extend(r for r in records if r.get("ticker") and r.get("side"))
        except (json.JSONDecodeError, OSError):
            pass
    return all_trades


def _save_weekly_report(text: str) -> None:
    path = DATA_BETA_DIR / "beta_weekly_report.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        log_error("weekly_report_save_error", {"error": str(exc)})
