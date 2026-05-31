"""
scripts/train_bonnie.py — Pipeline de treino offline: Walk-Forward + Optuna.

Fase 1: Optuna optimiza BacktestParams (RSI, vol, ATR stop/TP, trail, Bonnie threshold)
         usando Walk-Forward Validation (treino 36m / teste 6m / passo 6m, ~14 folds).
         Modelo Bonnie fixo em v4-clean durante esta fase.
         Objectivo: maximizar median(Sharpe OOS) − 0.5×std(Sharpe OOS).
         Gates duros: MaxDD>20% num fold → trial descartado. Sharpe mediana<0.5 → fitness=−10.

Fase 2: Retreina Bonnie ML com TP/SL vencedores da Fase 1 — evita o label mismatch
         documentado em retrain_bonnie.py (TP_ATR_MULT deve coincidir com atr_tp_mult activo).

Output (auto-versionado, nunca sobrescreve versões anteriores):
  models/bonnie_params_vN.json      hiperparâmetros + métricas OOS
  models/bonnie_train_report_vN.md  relatório WFO por fold
  models/registry.json              índice de todas as versões
  data/models/bonnie_model_vN.pkl   modelo sklearn retreinado
  data/beta/bonnie_thresholds_vN.json

Uso:
  PYTHONPATH=. python scripts/train_bonnie.py
  PYTHONPATH=. python scripts/train_bonnie.py --timeout-min 340 --n-trials 200
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import bot.logger as _bot_logger
_bot_logger._append_to_json_list = lambda *a, **k: None  # type: ignore

from bot.config import BASE_DIR
import bot.strategy as strategy
from bot.backtest import _regime_cache  # noqa: F401 (side-effect: populates cache)

from scripts.backtest import (
    BacktestConfig,
    BacktestParams,
    BonnieML,
    MODEL_PATH_V4CLEAN,
    OPT_BACKTEST_PARAMS,
    THRESHOLDS_PATH_V4CLEAN,
    build_earnings_calendar,
    load_data_for_backtest,
    prime_regimes,
    run_event_loop,
)
import scripts.retrain_bonnie as rb

# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------
MODELS_DIR    = BASE_DIR / "models"
REGISTRY_PATH = MODELS_DIR / "registry.json"

# ---------------------------------------------------------------------------
# Constantes WFO
# ---------------------------------------------------------------------------
WFO_TRAIN_MONTHS = 36
WFO_TEST_MONTHS  = 6
WFO_STEP_MONTHS  = 6
WFO_EMBARGO_DAYS = 30   # ≥ LABEL_HORIZON_DAYS (20 dias úteis) — sem label leakage
WFO_START        = datetime(2017, 1, 1)
CAPITAL_INIT     = 5000.0

# Backtest config idêntico ao de produção (Bonnie ML + Earnings gate + Value trail)
_BACKTEST_CFG = BacktestConfig(
    name="wfo_eval",
    enable_bonnie_ml=True,
    enable_earnings_gate=True,
    enable_rs_bullish=False,
    enable_value_trail=True,
    enable_adds=True,
    enable_kelly=False,
)


# ---------------------------------------------------------------------------
# Gestão de versões
# ---------------------------------------------------------------------------

def _read_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_version": None, "updated_at": None, "versions": []}


def _next_version() -> int:
    reg = _read_registry()
    versions = [v["version"] for v in reg.get("versions", []) if isinstance(v.get("version"), int)]
    return max(versions) + 1 if versions else 1


def update_registry(version: int, oos_metrics: dict, status: str) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    reg = _read_registry()
    entries = [v for v in reg.get("versions", []) if v.get("version") != version]
    entry: dict = {
        "version":    version,
        "status":     status,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sharpe_oos": round(oos_metrics.get("sharpe_median", 0), 4),
        "fitness":    round(oos_metrics.get("fitness", 0), 4),
    }
    if status == "promoted":
        entry["promoted_at"] = entry["created_at"]
        reg["active_version"] = version
        reg["updated_at"]     = entry["created_at"]
    entries.append(entry)
    entries.sort(key=lambda v: v["version"])
    reg["versions"] = entries
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(REGISTRY_PATH)


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Geração de folds WFO
# ---------------------------------------------------------------------------

def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year  = dt.year + month // 12
    month = month % 12 + 1
    day   = min(dt.day, [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def build_wfo_folds(wfo_end: datetime) -> list[dict]:
    folds: list[dict] = []
    train_start = WFO_START
    while True:
        train_end  = _add_months(train_start, WFO_TRAIN_MONTHS)
        test_start = train_end + timedelta(days=WFO_EMBARGO_DAYS)
        test_end   = _add_months(test_start, WFO_TEST_MONTHS)
        if test_end > wfo_end:
            break
        folds.append({
            "fold":        len(folds) + 1,
            "train_start": train_start,
            "train_end":   train_end,
            "test_start":  test_start,
            "test_end":    test_end,
        })
        train_start = _add_months(train_start, WFO_STEP_MONTHS)
    return folds


# ---------------------------------------------------------------------------
# Fase 1 — Optuna objective
# ---------------------------------------------------------------------------

def build_objective(
    full_calendar: list,
    histories: dict,
    earnings_cal: dict,
    bonnie_ml: BonnieML,
    folds: list[dict],
):
    import optuna

    def objective(trial: optuna.Trial) -> float:
        rsi_ceil = trial.suggest_int("rsi_oversold_ceiling", 28, 38)
        vol_min  = trial.suggest_float("vol_ratio_min", 1.0, 1.6, step=0.05)

        params = BacktestParams(
            atr_stop_mult_value    = trial.suggest_float("atr_stop_mult_value",    1.25, 2.5),
            atr_stop_mult_momentum = trial.suggest_float("atr_stop_mult_momentum", 1.5,  3.0),
            atr_tp_mult            = trial.suggest_float("atr_tp_mult",            3.0,  5.5),
            value_trail_activation = trial.suggest_float("value_trail_activation", 2.0,  4.0),
            value_trail_distance   = trial.suggest_float("value_trail_distance",   2.5,  4.0),
            bonnie_threshold       = trial.suggest_float("bonnie_threshold",       0.45, 0.75),
            max_position_pct       = trial.suggest_float("max_position_pct",       8.0, 14.0),
        )

        # Override strategy globals (single-threaded Optuna — seguro)
        orig_rsi = strategy._PC.get("rsi_oversold_ceiling", 34)
        orig_vol = strategy._PC.get("vol_ratio_oversold_min", 1.2)
        strategy._PC["rsi_oversold_ceiling"]   = rsi_ceil
        strategy._PC["vol_ratio_oversold_min"] = vol_min
        strategy._PC["vol_ratio_momentum_min"] = round(max(vol_min + 0.4, 1.5), 2)

        fold_sharpes: list[float] = []

        try:
            for i, fold in enumerate(folds):
                test_cal = [
                    d for d in full_calendar
                    if fold["test_start"] <= d.to_pydatetime() <= fold["test_end"]
                ]
                if len(test_cal) < 20:
                    continue

                try:
                    result = run_event_loop(
                        _BACKTEST_CFG, params, test_cal, histories,
                        CAPITAL_INIT, bonnie_ml, earnings_cal,
                    )
                except Exception:
                    continue

                if result.max_drawdown_pct > 20.0:
                    trial.report(-10.0, i)
                    raise optuna.exceptions.TrialPruned()

                fold_sharpes.append(result.sharpe_annual)
                trial.report(float(np.mean(fold_sharpes)), i)

                if trial.should_prune():
                    raise optuna.exceptions.TrialPruned()

        finally:
            strategy._PC["rsi_oversold_ceiling"]   = orig_rsi
            strategy._PC["vol_ratio_oversold_min"] = orig_vol

        if not fold_sharpes:
            return -10.0

        med = float(np.median(fold_sharpes))
        if med < 0.5:
            return -10.0

        return med - 0.5 * float(np.std(fold_sharpes))

    return objective


# ---------------------------------------------------------------------------
# Avaliação final com os melhores params (relatório por fold)
# ---------------------------------------------------------------------------

def evaluate_best_params(
    best_params_dict: dict,
    full_calendar: list,
    histories: dict,
    earnings_cal: dict,
    bonnie_ml: BonnieML,
    folds: list[dict],
) -> list[dict]:
    params = BacktestParams(
        atr_stop_mult_value    = best_params_dict["atr_stop_mult_value"],
        atr_stop_mult_momentum = best_params_dict["atr_stop_mult_momentum"],
        atr_tp_mult            = best_params_dict["atr_tp_mult"],
        value_trail_activation = best_params_dict["value_trail_activation"],
        value_trail_distance   = best_params_dict["value_trail_distance"],
        bonnie_threshold       = best_params_dict["bonnie_threshold"],
        max_position_pct       = best_params_dict["max_position_pct"],
    )
    strategy._PC["rsi_oversold_ceiling"]   = best_params_dict["rsi_oversold_ceiling"]
    strategy._PC["vol_ratio_oversold_min"] = best_params_dict["vol_ratio_min"]
    strategy._PC["vol_ratio_momentum_min"] = round(max(best_params_dict["vol_ratio_min"] + 0.4, 1.5), 2)

    fold_results: list[dict] = []
    for fold in folds:
        test_cal = [
            d for d in full_calendar
            if fold["test_start"] <= d.to_pydatetime() <= fold["test_end"]
        ]
        if len(test_cal) < 10:
            continue
        try:
            r = run_event_loop(_BACKTEST_CFG, params, test_cal, histories,
                               CAPITAL_INIT, bonnie_ml, earnings_cal)
            fold_results.append({
                "fold":        fold["fold"],
                "test_period": f"{fold['test_start'].date()}→{fold['test_end'].date()}",
                "sharpe":      round(r.sharpe_annual, 4),
                "max_dd":      round(r.max_drawdown_pct, 2),
                "win_rate":    round(r.win_rate_pct / 100, 4),
                "n_trades":    len(r.trades),
                "passed":      r.max_drawdown_pct <= 20.0 and r.sharpe_annual >= 0.0,
            })
        except Exception as exc:
            print(f"  [fold {fold['fold']}] erro na avaliação final: {exc}", flush=True)

    return fold_results


# ---------------------------------------------------------------------------
# Fase 2 — Retrain Bonnie com TP/SL vencedores
# ---------------------------------------------------------------------------

def retrain_bonnie_phase2(
    best_params: dict,
    version: int,
    data_start: datetime,
    data_end: datetime,
) -> tuple[Path, Path]:
    tp_mult = best_params["atr_tp_mult"]
    sl_mult = best_params["atr_stop_mult_value"]

    print(f"\n[Fase 2] Retrain Bonnie v{version}: TP={tp_mult:.2f}x SL={sl_mult:.2f}x", flush=True)

    # Alinha labels com os vencedores (evita o model pass-through documentado)
    rb.TP_ATR_MULT = tp_mult
    rb.SL_ATR_MULT = sl_mult
    rb.TRAIN_START = data_start
    rb.VAL_END     = data_end
    # TRAIN_END e VAL_START mantêm-se (gap anti-leakage do retrain_bonnie.py)

    model_out = BASE_DIR / "data" / "models" / f"bonnie_model_v{version}.pkl"
    thr_out   = BASE_DIR / "data" / "beta"   / f"bonnie_thresholds_v{version}.json"

    corpus = rb.generate_corpus(verbose=True)
    if len(corpus) < 500:
        raise RuntimeError(f"Corpus demasiado pequeno: {len(corpus)} obs")

    model, thresholds, _ = rb.train_and_evaluate(corpus)
    rb.save_artifacts(model, thresholds, corpus, model_out=model_out, thresholds_out=thr_out)

    return model_out, thr_out


# ---------------------------------------------------------------------------
# Guardar artefactos
# ---------------------------------------------------------------------------

def save_training_artifacts(
    version: int,
    best_params: dict,
    fold_results: list[dict],
    model_pkl: Path,
    thresholds_json: Path,
    fitness: float,
    wfo_folds: list[dict],
    n_trials: int,
) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    fold_sharpes = [f["sharpe"] for f in fold_results]
    fold_dds     = [f["max_dd"] for f in fold_results]
    fold_wrs     = [f["win_rate"] for f in fold_results]
    n_passed     = sum(1 for f in fold_results if f.get("passed", True))

    gates_passed = bool(
        fold_sharpes
        and float(np.median(fold_sharpes)) >= 0.5
        and float(max(fold_dds)) <= 20.0
    )

    oos_metrics = {
        "sharpe_median":     round(float(np.median(fold_sharpes)), 4) if fold_sharpes else 0.0,
        "sharpe_mean":       round(float(np.mean(fold_sharpes)),   4) if fold_sharpes else 0.0,
        "sharpe_std":        round(float(np.std(fold_sharpes)),    4) if fold_sharpes else 0.0,
        "max_dd_worst_fold": round(float(max(fold_dds)),           2) if fold_dds else 0.0,
        "max_dd_median":     round(float(np.median(fold_dds)),     2) if fold_dds else 0.0,
        "win_rate_median":   round(float(np.median(fold_wrs)),     4) if fold_wrs else 0.0,
        "folds_passing":     n_passed,
        "folds_total":       len(fold_results),
        "fitness":           round(fitness, 4),
        "gates": {
            "min_sharpe": 0.5,
            "max_dd":     20.0,
            "passed":     gates_passed,
        },
    }

    payload = {
        "version":    version,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_commit": _git_commit(),
        "wfo": {
            "train_months":    WFO_TRAIN_MONTHS,
            "test_months":     WFO_TEST_MONTHS,
            "step_months":     WFO_STEP_MONTHS,
            "embargo_days":    WFO_EMBARGO_DAYS,
            "n_folds":         len(wfo_folds),
            "n_trials_optuna": n_trials,
        },
        "hyperparams":  best_params,
        "oos_metrics":  oos_metrics,
        "fold_results": fold_results,
        "model_artifacts": {
            "pkl":        str(model_pkl.relative_to(BASE_DIR)),
            "thresholds": str(thresholds_json.relative_to(BASE_DIR)),
        },
    }

    params_path = MODELS_DIR / f"bonnie_params_v{version}.json"
    tmp = params_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(params_path)
    print(f"[Artefacto] {params_path.relative_to(BASE_DIR)}", flush=True)

    update_registry(version, oos_metrics, "candidate")
    _write_report(version, payload, fold_results)

    return params_path


def _write_report(version: int, payload: dict, fold_results: list[dict]) -> None:
    m = payload["oos_metrics"]
    w = payload["wfo"]
    hp = payload["hyperparams"]

    gates_str = "PASSOU" if m["gates"]["passed"] else "FALHOU"
    lines = [
        f"# Bonnie WFO Report — v{version}",
        f"",
        f"**Criado:** {payload['created_at']}  |  **Commit:** {payload['git_commit']}",
        f"",
        f"## Configuração WFO",
        f"- Treino: {w['train_months']}m | Teste: {w['test_months']}m | Passo: {w['step_months']}m",
        f"- Embargo: {w['embargo_days']} dias | Folds: {w['n_folds']} | Trials Optuna: {w['n_trials_optuna']}",
        f"",
        f"## Métricas OOS",
        f"| Métrica | Valor |",
        f"|---|---|",
        f"| Sharpe mediana | {m['sharpe_median']:.3f} |",
        f"| Sharpe std | {m['sharpe_std']:.3f} |",
        f"| MaxDD pior fold | {m['max_dd_worst_fold']:.1f}% |",
        f"| MaxDD mediana | {m['max_dd_median']:.1f}% |",
        f"| Win Rate mediana | {m['win_rate_median']:.1%} |",
        f"| Fitness | {m['fitness']:.3f} |",
        f"| Folds válidos | {m['folds_passing']}/{m['folds_total']} |",
        f"| Gates | **{gates_str}** |",
        f"",
        f"## Hiperparâmetros Vencedores",
        f"```json",
        json.dumps(hp, indent=2),
        f"```",
        f"",
        f"## Resultados por Fold",
        f"| Fold | Período Teste | Sharpe | MaxDD | WR | Trades | OK |",
        f"|---|---|---|---|---|---|---|",
    ]

    for fr in fold_results:
        ok = "✓" if fr.get("passed", False) else "✗"
        lines.append(
            f"| {fr.get('fold','?')} | {fr.get('test_period','?')} "
            f"| {fr.get('sharpe',0):.2f} | {fr.get('max_dd',0):.1f}% "
            f"| {fr.get('win_rate',0):.1%} | {fr.get('n_trades',0)} | {ok} |"
        )

    report_path = MODELS_DIR / f"bonnie_train_report_v{version}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[Relatório] {report_path.relative_to(BASE_DIR)}", flush=True)


# ---------------------------------------------------------------------------
# Relatório Telegram de fim de treino
# ---------------------------------------------------------------------------

def _send_training_report(version: int, oos_metrics: dict, status: str) -> None:
    try:
        import os
        import requests
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        m = oos_metrics
        msg = (
            f"🧠 <b>Treino Semanal Concluído</b>\n\n"
            f"Modelo: <code>bonnie_params_v{version}</code>\n"
            f"Sharpe OOS: {m.get('sharpe_median', 0):.2f} "
            f"(±{m.get('sharpe_std', 0):.2f})\n"
            f"Max Drawdown: {m.get('max_dd_worst_fold', 0):.1f}%\n"
            f"Win Rate OOS: {m.get('win_rate_median', 0):.1%}\n"
            f"Fitness: {m.get('fitness', 0):.3f}\n"
            f"Folds OK: {m.get('folds_passing', 0)}/{m.get('folds_total', 0)}\n\n"
            f"Status: <b>{status}</b>"
        )
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[train] Telegram relatório falhou: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import optuna

    ap = argparse.ArgumentParser(description="Treino WFO + Optuna para Bonnie")
    ap.add_argument("--timeout-min", type=int,   default=340, help="Timeout Optuna em minutos")
    ap.add_argument("--n-trials",    type=int,   default=0,   help="Nº máximo de trials (0=até timeout)")
    ap.add_argument("--capital",     type=float, default=CAPITAL_INIT)
    ap.add_argument("--wfo-end",     default=None, help="Data final WFO YYYY-MM-DD (default: hoje-30d)")
    ap.add_argument("--skip-phase2", action="store_true", help="Saltar retrain Bonnie (Fase 2)")
    args = ap.parse_args()

    wfo_end = (
        datetime.now() - timedelta(days=30)
        if args.wfo_end is None
        else datetime.strptime(args.wfo_end, "%Y-%m-%d")
    )

    t0 = time.time()
    print(f"[{datetime.now(timezone.utc).isoformat()[:19]}Z] === train_bonnie START ===", flush=True)
    print(f"  WFO: {WFO_START.date()} → {wfo_end.date()}  |  timeout={args.timeout_min}min", flush=True)

    version = _next_version()
    print(f"  Versão alvo: v{version}", flush=True)

    # ------------------------------------------------------------------
    # Carregar dados (uma só vez para todos os trials)
    # ------------------------------------------------------------------
    print(f"\n[1/4] A carregar dados históricos...", flush=True)
    full_calendar, histories, _spy_closes, _spy_index = load_data_for_backtest(
        WFO_START, wfo_end, verbose=True)
    prime_regimes(full_calendar, verbose=True)
    earnings_cal = build_earnings_calendar(WFO_START, wfo_end)

    # ------------------------------------------------------------------
    # Gerar folds WFO
    # ------------------------------------------------------------------
    wfo_folds = build_wfo_folds(wfo_end)
    print(f"\n[2/4] {len(wfo_folds)} folds WFO:", flush=True)
    for f in wfo_folds:
        print(
            f"  Fold {f['fold']:2d}: "
            f"treino {f['train_start'].date()}→{f['train_end'].date()}"
            f"  |  teste {f['test_start'].date()}→{f['test_end'].date()}",
            flush=True,
        )

    # Bonnie ML fixo (v4-clean) durante a Fase 1
    force_path = MODEL_PATH_V4CLEAN if MODEL_PATH_V4CLEAN.exists() else None
    bonnie_ml  = BonnieML(BacktestParams(), force_model_path=force_path)
    if not bonnie_ml.available:
        print("  [AVISO] Bonnie ML não disponível — backtest corre sem filtro ML", flush=True)

    # ------------------------------------------------------------------
    # Fase 1 — Optuna
    # ------------------------------------------------------------------
    print(f"\n[3/4] Fase 1: Optuna WFO (timeout={args.timeout_min}min)...", flush=True)
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, multivariate=True),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10, n_warmup_steps=3),
    )
    objective  = build_objective(full_calendar, histories, earnings_cal, bonnie_ml, wfo_folds)
    n_trials_arg = args.n_trials if args.n_trials > 0 else None

    def _log_progress(study, trial) -> None:
        if trial.state.name != "COMPLETE":
            return
        done = sum(1 for t in study.trials if t.state.name == "COMPLETE")
        if done % 10 == 0 or done <= 5:
            print(
                f"  [{datetime.now(timezone.utc).isoformat()[:19]}Z] "
                f"Trial {trial.number} | fitness={trial.value:.4f} | "
                f"best={study.best_value:.4f} | completos={done}",
                flush=True,
            )

    study.optimize(objective, timeout=args.timeout_min * 60, n_trials=n_trials_arg,
                   show_progress_bar=False, callbacks=[_log_progress])

    best    = study.best_params
    fitness = study.best_value
    n_done  = len([t for t in study.trials if t.state.name == "COMPLETE"])
    print(f"  Trials completos: {n_done}  |  Melhor fitness: {fitness:.4f}", flush=True)
    print(f"  Melhores params:\n{json.dumps(best, indent=4)}", flush=True)

    # Avaliação final em todos os folds com os melhores params
    print(f"\n  Avaliação final por fold...", flush=True)
    fold_results = evaluate_best_params(best, full_calendar, histories, earnings_cal, bonnie_ml, wfo_folds)

    # ------------------------------------------------------------------
    # Fase 2 — Retrain Bonnie
    # ------------------------------------------------------------------
    print(f"\n[4/4] Fase 2: Retrain Bonnie v{version}...", flush=True)
    if args.skip_phase2:
        print("  --skip-phase2 activo — a usar modelo v4-clean existente", flush=True)
        model_pkl       = MODEL_PATH_V4CLEAN
        thresholds_json = THRESHOLDS_PATH_V4CLEAN
    else:
        try:
            model_pkl, thresholds_json = retrain_bonnie_phase2(best, version, WFO_START, wfo_end)
        except Exception as exc:
            print(f"  [AVISO] Retrain Bonnie falhou ({exc}) — a usar modelo v4-clean existente", flush=True)
            model_pkl       = MODEL_PATH_V4CLEAN
            thresholds_json = THRESHOLDS_PATH_V4CLEAN

    # ------------------------------------------------------------------
    # Guardar artefactos
    # ------------------------------------------------------------------
    save_training_artifacts(
        version, best, fold_results, model_pkl, thresholds_json,
        fitness, wfo_folds, n_done,
    )

    dur = round(time.time() - t0)
    print(f"\n[{datetime.now(timezone.utc).isoformat()[:19]}Z] === train_bonnie END === {dur}s", flush=True)

    # ------------------------------------------------------------------
    # Promoção automática
    # ------------------------------------------------------------------
    from scripts.promote_model import promote
    oos = [f["sharpe"] for f in fold_results]
    gates_ok = bool(oos and float(np.median(oos)) >= 0.5)
    status = promote(version)
    _send_training_report(version, {
        "sharpe_median":     round(float(np.median(oos)), 4) if oos else 0.0,
        "sharpe_std":        round(float(np.std(oos)),    4) if oos else 0.0,
        "max_dd_worst_fold": max((f["max_dd"] for f in fold_results), default=0.0),
        "win_rate_median":   round(float(np.median([f["win_rate"] for f in fold_results])), 4) if fold_results else 0.0,
        "fitness":           round(fitness, 4),
        "folds_passing":     sum(1 for f in fold_results if f.get("passed")),
        "folds_total":       len(fold_results),
    }, status)


if __name__ == "__main__":
    main()
