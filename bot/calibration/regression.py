"""
regression.py — Testes de regressão de performance do Clyde (offline).

Compara métricas novas (parâmetros/watchlist actuais) contra o baseline
guardado, para validar se uma alteração de configuração representa
uma melhoria estatisticamente relevante.

Parede de fogo
--------------
Não importa nada do bot de produção (price_feed, phase0, api_client,
strategy, learner). Importa exclusivamente de bot.calibration.*.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from bot.calibration.sweep import ParamSet, _build_mask
from bot.calibration.metrics import compute_metrics_full
from bot.calibration.cache import ensure_ohlcv_cache
from bot.calibration.candidates import build_candidate_table

_BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_PATH = Path(__file__).with_name("strategy_baseline.json")
DEFAULT_LOG_PATH = _BASE_DIR / "data" / "calibration" / "regression_log.jsonl"

_PARAM_KEYS: tuple[str, ...] = (
    "rsi_buy_max",
    "vol_ratio_min",
    "require_ema50_above_200",
    "ema50_dist_min_pct",
    "apply_regime_veto",
)

# Limiares do veredicto (§ Regras de veredicto na especificação)
_THRESHOLD_PF_IMPROVED = 5.0    # PF subiu > 5%  → possível MELHOROU
_THRESHOLD_EXP_OK      = -10.0  # Expectancy não piorou > 10%
_THRESHOLD_PF_WORSENED = -5.0   # PF caiu > 5%   → PIOROU
_THRESHOLD_DD_WORSENED = -20.0  # Max DD piorou > 20% → PIOROU


def run_regression_test(
    tickers_new: list[str],
    params_new: dict | None = None,
    baseline_path: Path | None = None,
    horizons: list[int] | None = None,
    start: str = "2022-01-01",
    end: str | None = None,
    *,
    _cand: pd.DataFrame | None = None,
) -> dict:
    """
    Compara métricas da configuração nova vs baseline guardado.

    Parâmetros
    ----------
    tickers_new : list[str]
        Watchlist nova (ou actual) para o backtest de comparação.
    params_new : dict | None
        Parâmetros novos a avaliar. None = usar parâmetros do baseline.
    baseline_path : Path | None
        Caminho para strategy_baseline.json. None = caminho padrão.
    horizons : list[int] | None
        Horizontes de holding period em dias. None = [10].
    start : str
        Data inicial YYYY-MM-DD do backtest (default: 2022-01-01).
    end : str | None
        Data final YYYY-MM-DD. None = hoje − 15 dias.
    _cand : pd.DataFrame | None
        Tabela de candidatos pré-construída (uso interno via __main__.py).
        Se fornecida, evita re-download e reconstrução de candidatos.

    Devolve
    -------
    dict com:
        "baseline": {pf, expectancy, max_dd, n_trades, params, tickers_n}
        "new":      {pf, expectancy, max_dd, n_trades, params, tickers_n}
        "delta":    {pf_change_pct, expectancy_change_pct, max_dd_change_pct, verdict}
        "recommendation": str — texto explicativo em português
    """
    if horizons is None:
        horizons = [10]
    if baseline_path is None:
        baseline_path = DEFAULT_BASELINE_PATH
    baseline_path = Path(baseline_path)

    if end is None:
        end = (date.today() - pd.Timedelta(days=15)).strftime("%Y-%m-%d")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    b_params  = baseline.get("best_params", {})
    b_metrics = baseline.get("metrics", {})

    if params_new is None:
        params_new = b_params

    if _cand is None:
        all_dl = list(dict.fromkeys(["SPY", "RSP"] + tickers_new))
        ensure_ohlcv_cache(all_dl, start, end)
        cand = build_candidate_table(tickers_new, start, end, horizons, force=False)
    else:
        cand = _cand

    H_main = horizons[0]
    new_m = _eval_params(cand, params_new, H_main)

    b_pf  = _to_float(b_metrics.get("profit_factor"))
    b_exp = _to_float(b_metrics.get("expectancy_pct"))
    b_dd  = _to_float(b_metrics.get("avg_max_dd_pct"))
    b_n   = int(baseline.get("n_trades", 0))

    new_pf  = _to_float(new_m.get("profit_factor"))
    new_exp = _to_float(new_m.get("expectancy_pct"))
    new_dd  = _to_float(new_m.get("avg_max_dd_pct"))
    new_n   = int(new_m.get("n_trades", 0))

    pf_chg  = _pct_change(b_pf, new_pf)
    exp_chg = _pct_change(b_exp, new_exp)
    dd_chg  = _dd_improvement(b_dd, new_dd)

    verdict = _verdict(pf_chg, exp_chg, dd_chg)

    return {
        "baseline": {
            "pf":        b_pf,
            "expectancy": b_exp,
            "max_dd":    b_dd,
            "n_trades":  b_n,
            "params":    b_params,
            "tickers_n": _parse_universe_n(baseline),
        },
        "new": {
            "pf":        new_pf,
            "expectancy": new_exp,
            "max_dd":    new_dd,
            "n_trades":  new_n,
            "params":    params_new,
            "tickers_n": len(tickers_new),
        },
        "delta": {
            "pf_change_pct":         _round2(pf_chg),
            "expectancy_change_pct": _round2(exp_chg),
            "max_dd_change_pct":     _round2(dd_chg),
            "verdict":               verdict,
        },
        "recommendation": _make_recommendation(
            b_params, params_new, pf_chg, exp_chg, dd_chg, verdict
        ),
    }


def save_regression_log(result: dict, log_path: Path = DEFAULT_LOG_PATH) -> None:
    """Acrescenta o resultado de um regression test ao log JSONL."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **result,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_baseline_from_regression(
    result: dict,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
) -> None:
    """Actualiza strategy_baseline.json com os parâmetros e métricas do regression test."""
    baseline_path = Path(baseline_path)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    new = result["new"]
    baseline["best_params"] = new["params"]
    baseline["metrics"] = {
        "profit_factor":   _round4(new["pf"]),
        "expectancy_pct":  _round4(new["expectancy"]),
        "avg_max_dd_pct":  _round4(new["max_dd"]),
    }
    baseline["n_trades"]        = new["n_trades"]
    baseline["date"]            = date.today().strftime("%Y-%m-%d")
    baseline["recalibrated_by"] = "regression_test"
    baseline["previous_params"] = result["baseline"]["params"]

    baseline_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Auxiliares internos
# ---------------------------------------------------------------------------

def _eval_params(cand: pd.DataFrame, params: dict, H: int, n_min: int = 30) -> dict:
    """Avalia um conjunto de parâmetros sobre `cand` para o horizonte H."""
    if cand.empty:
        return {
            "n_trades": 0,
            "profit_factor": float("nan"),
            "expectancy_pct": float("nan"),
            "avg_max_dd_pct": float("nan"),
            "low_sample": True,
        }
    p = ParamSet(
        rsi_buy_max=float(params.get("rsi_buy_max", 35)),
        vol_ratio_min=float(params.get("vol_ratio_min", 0.8)),
        require_ema50_above_200=bool(params.get("require_ema50_above_200", True)),
        ema50_dist_min_pct=params.get("ema50_dist_min_pct"),
        apply_regime_veto=bool(params.get("apply_regime_veto", True)),
        horizon=H,
    )
    mask = _build_mask(cand, p, H)
    return compute_metrics_full(cand, mask, H, n_min)


def _to_float(v: object) -> float:
    """Converte para float; nan se inválido ou None."""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def _pct_change(old: float, new: float) -> float:
    """Variação percentual de old para new; nan se dados inválidos."""
    if math.isnan(old) or math.isnan(new) or old == 0:
        return float("nan")
    return 100.0 * (new - old) / abs(old)


def _dd_improvement(old_dd: float, new_dd: float) -> float:
    """Melhoria do drawdown em % — positivo significa drawdown reduziu (melhor)."""
    if math.isnan(old_dd) or math.isnan(new_dd) or old_dd == 0:
        return float("nan")
    return 100.0 * (abs(old_dd) - abs(new_dd)) / abs(old_dd)


def _verdict(pf_chg: float, exp_chg: float, dd_chg: float) -> str:
    """Classifica a alteração em MELHOROU / PIOROU / NEUTRO."""
    pf_valid  = not math.isnan(pf_chg)
    exp_valid = not math.isnan(exp_chg)
    dd_valid  = not math.isnan(dd_chg)

    # PIOROU: PF caiu > 5% OU max_dd piorou > 20%
    if pf_valid and pf_chg < _THRESHOLD_PF_WORSENED:
        return "🔴 PIOROU"
    if dd_valid and dd_chg < _THRESHOLD_DD_WORSENED:
        return "🔴 PIOROU"

    # MELHOROU: PF subiu > 5% E expectancy não piorou > 10%
    if pf_valid and pf_chg > _THRESHOLD_PF_IMPROVED:
        if not exp_valid or exp_chg > _THRESHOLD_EXP_OK:
            return "✅ MELHOROU"

    return "➡️ NEUTRO"


def _make_recommendation(
    old_params: dict,
    new_params: dict,
    pf_chg: float,
    exp_chg: float,
    dd_chg: float,
    verdict: str,
) -> str:
    """Gera texto explicativo em português sobre o que mudou e o que recomenda."""
    parts: list[str] = []

    changed = [k for k in _PARAM_KEYS if old_params.get(k) != new_params.get(k)]
    if changed:
        parts.append(f"Parâmetros alterados: {', '.join(changed)}.")
    else:
        parts.append("Parâmetros idênticos ao baseline (comparação de watchlist ou janela temporal).")

    if verdict == "✅ MELHOROU":
        parts.append(
            f"PF melhorou {pf_chg:+.1f}% face ao baseline — recomenda-se actualizar o baseline."
        )
    elif verdict == "🔴 PIOROU":
        reasons: list[str] = []
        if not math.isnan(pf_chg) and pf_chg < _THRESHOLD_PF_WORSENED:
            reasons.append(f"PF caiu {abs(pf_chg):.1f}%")
        if not math.isnan(dd_chg) and dd_chg < _THRESHOLD_DD_WORSENED:
            reasons.append(f"drawdown piorou {abs(dd_chg):.1f}%")
        parts.append(
            f"Configuração nova piorou ({'; '.join(reasons)}) — não actualizar o baseline."
        )
    else:
        exp_note = (
            f" (expectancy variou {exp_chg:+.1f}%)" if not math.isnan(exp_chg) else ""
        )
        parts.append(
            f"Diferenças dentro dos limiares de neutralidade{exp_note} — monitorizar e re-avaliar."
        )

    return " ".join(parts)


def _parse_universe_n(baseline: dict) -> int | str:
    """Extrai o nº de tickers do campo universe do baseline; int se possível."""
    universe_str = baseline.get("data_window", {}).get("universe", "")
    m = re.search(r"\((\d+)\s*tickers?\)", universe_str, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return universe_str or "?"


def _round2(v: float) -> float | None:
    """Arredonda para 2 casas decimais; None se NaN."""
    return None if math.isnan(v) else round(v, 2)


def _round4(v: object) -> float | None:
    """Arredonda para 4 casas decimais; None se inválido/NaN."""
    try:
        f = float(v)  # type: ignore[arg-type]
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None
