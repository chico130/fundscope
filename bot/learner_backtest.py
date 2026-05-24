"""
bot/learner_backtest.py — Learner in-the-loop com fitness adaptativa ao regime.

Diferenca arquitectural vs bot/learner.py (Fase 3):
  * Em vez de optimizar contra trades reais fechados (beta_trades.json),
    optimiza contra o backtest sintetico completo (scripts.backtest.run_event_loop).
  * Coordinate descent sobre BacktestParams.
  * Fitness varia conforme regime predominante do periodo de backtest.
  * Persiste data/beta/optimized_backtest_params.json.

Uso:
  PYTHONPATH=. python bot/learner_backtest.py --cycles 10 --since 2023-01-01

Tempo estimado: ~6s por evaluation. 6 params x 5 valores x N cycles x 6s.
  N=5  →  ~15 min
  N=10 →  ~30 min
  N=20 →  ~60 min
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import bot.logger as _bot_logger
_bot_logger._append_to_json_list = lambda *a, **k: None  # type: ignore

from bot.config import BASE_DIR
from scripts.backtest import (
    BacktestConfig, BacktestParams, BacktestResult,
    BonnieML, load_data_for_backtest, prime_regimes,
    build_earnings_calendar, run_event_loop,
)
from bot.backtest import _regime_cache


OPT_PARAMS_PATH = BASE_DIR / "data" / "beta" / "optimized_backtest_params.json"

# Espaco de parametros (start, stop, step)
PARAM_SPACE = {
    "atr_stop_mult_value":    (1.5,  3.0,  0.25),
    "atr_tp_mult":            (2.0,  4.5,  0.25),
    "value_trail_activation": (1.5,  3.0,  0.25),
    "value_trail_distance":   (1.5,  3.5,  0.25),
    "max_position_pct":       (8.0,  14.0, 1.0),
    "bonnie_threshold":       (0.44, 0.56, 0.02),
}


def values_for(name: str) -> list[float]:
    lo, hi, step = PARAM_SPACE[name]
    n = int(round((hi - lo) / step)) + 1
    return [round(lo + i * step, 4) for i in range(n)]


# --------------------------------------------------------------------------
# Fitness adaptativa ao regime
# --------------------------------------------------------------------------

def _regime_pcts(result: BacktestResult) -> tuple[float, float]:
    """Devolve (bull_pct, bear_pct) baseado em dias com regime conhecido."""
    bull = result.regime_days_bull
    bear = result.regime_days_bear
    total = bull + bear
    if total == 0:
        return 0.0, 0.0
    return bull / total, bear / total


def dominant_regime(result: BacktestResult) -> str:
    """Devolve o regime dominante baseado em % de dias.
    bull > 60%  → bull_trending
    bear > 20%  → weighted (média ponderada bull+bear)
    else        → lateral
    """
    bull_pct, bear_pct = _regime_pcts(result)
    if bull_pct > 0.60:
        return "bull_trending"
    if bear_pct > 0.20:
        return "weighted"
    return "lateral"


def compute_spy_return(spy_closes: np.ndarray, spy_index, start, end) -> float:
    """Total return % do SPY no periodo do backtest."""
    import pandas as pd
    mask = (spy_index >= start) & (spy_index <= end)
    series = spy_closes[mask]
    if len(series) < 2:
        return 0.0
    return float((series[-1] - series[0]) / series[0] * 100)


def _bull_fitness(result: BacktestResult, spy_return: float) -> float:
    alpha = result.total_return_pct - spy_return
    sharpe_floor = max(0.5, result.sharpe_annual)
    return alpha * sharpe_floor


def _bear_fitness(result: BacktestResult) -> float:
    dd_factor = max(0.4, 1 - abs(result.max_drawdown_pct) / 15.0)
    return result.calmar * dd_factor


def _lateral_fitness(result: BacktestResult) -> float:
    return result.sharpe_annual * result.profit_factor


def fitness(result: BacktestResult, spy_return: float) -> tuple[float, str]:
    """Devolve (fitness, regime_used) — fitness adaptativa ao regime dominante."""
    regime = dominant_regime(result)
    if regime == "bull_trending":
        f = _bull_fitness(result, spy_return)
    elif regime == "weighted":
        bull_pct, bear_pct = _regime_pcts(result)
        f = bull_pct * _bull_fitness(result, spy_return) + bear_pct * _bear_fitness(result)
    else:  # lateral / bull_weak
        f = _lateral_fitness(result)
    return f, regime


# --------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------

class Evaluator:
    def __init__(self, start, end, capital, calendar, histories,
                 spy_closes, spy_index, bonnie_ml, earnings_cal):
        self.start = start
        self.end = end
        self.capital = capital
        self.calendar = calendar
        self.histories = histories
        self.spy_closes = spy_closes
        self.spy_index = spy_index
        self.bonnie_ml = bonnie_ml
        self.earnings_cal = earnings_cal
        self.spy_return = compute_spy_return(spy_closes, spy_index, start, end)
        # Variante full por defeito (3 bots todos activos)
        self.config = BacktestConfig(
            "learner-eval",
            enable_bonnie_ml=True, enable_earnings_gate=True, enable_rs_bullish=True,
            enable_value_trail=True, enable_adds=True,
        )
        self.n_evals = 0
        self.total_time = 0.0

    def evaluate(self, params: BacktestParams) -> tuple[float, BacktestResult, str]:
        t0 = time.time()
        # Bonnie threshold injection: override the params.bonnie_threshold AND patch the BonnieML
        # The BonnieML uses regime_thresholds.json — so this param tweaks the global floor only
        self.bonnie_ml.params = params
        result = run_event_loop(
            self.config, params, self.calendar, self.histories,
            self.capital, self.bonnie_ml, self.earnings_cal,
        )
        elapsed = time.time() - t0
        self.n_evals += 1
        self.total_time += elapsed
        f, regime = fitness(result, self.spy_return)
        return f, result, regime


# --------------------------------------------------------------------------
# Coordinate descent
# --------------------------------------------------------------------------

def coordinate_descent(evaluator: Evaluator, cycles: int) -> tuple[BacktestParams, dict]:
    """Runs N cycles of coordinate descent. Returns (best_params, history)."""
    # Start point: defaults
    best_params = BacktestParams()
    print(f"\n[Eval] Baseline (defaults)...")
    best_f, best_result, regime = evaluator.evaluate(best_params)
    print(f"       fitness={best_f:+.3f}  regime={regime}  spy_return={evaluator.spy_return:+.1f}%  "
          f"return={best_result.total_return_pct:+.1f}%  sharpe={best_result.sharpe_annual:.2f}  "
          f"PF={best_result.profit_factor:.2f}  trades={len(best_result.trades)}  DD=-{best_result.max_drawdown_pct:.1f}%")

    history = {"baseline": {"params": asdict(best_params), "fitness": best_f, "result": _result_brief(best_result)}}
    history["cycles"] = []

    for cycle in range(1, cycles + 1):
        cycle_start = time.time()
        improved = False
        cycle_log: list = []

        for pname in PARAM_SPACE:
            curr_val = getattr(best_params, pname)
            cands = values_for(pname)
            # Inclui sempre o valor actual (se ja estiver na grade, sem duplicar)
            tested = []
            for cv in cands:
                if abs(cv - curr_val) < 1e-6:
                    continue
                test_params = replace(best_params, **{pname: cv})
                f, result, regime = evaluator.evaluate(test_params)
                tested.append((cv, f, result))
                if f > best_f:
                    best_f = f
                    best_params = test_params
                    best_result = result
                    improved = True

            tested.sort(key=lambda x: -x[1])
            if tested:
                best_alt_val, best_alt_f, _ = tested[0]
                marker = "X" if abs(best_alt_val - getattr(best_params, pname)) < 1e-6 else "."
                cycle_log.append(f"  [{marker}] {pname:25s} cur={curr_val:>6}  best_alt={best_alt_val:>6}  f={best_alt_f:+.3f}")

        # Checkpoint apos cada ciclo
        _save_checkpoint(best_params, best_f, best_result, cycle, evaluator.spy_return)

        ct = time.time() - cycle_start
        avg_eval = evaluator.total_time / max(1, evaluator.n_evals)
        eta = (cycles - cycle) * sum(len(values_for(p)) - 1 for p in PARAM_SPACE) * avg_eval / 60
        print(f"\n[Cycle {cycle}/{cycles}]  {ct:.0f}s  evals_total={evaluator.n_evals}  avg={avg_eval:.1f}s/eval  ETA~{eta:.0f}min")
        for line in cycle_log:
            print(line)
        print(f"Ciclo {cycle}/{cycles} | fitness: {best_f:.2f} | retorno: {best_result.total_return_pct:+.1f}% "
              f"| sharpe: {best_result.sharpe_annual:.2f} | trades: {len(best_result.trades)} "
              f"| atr_tp: {best_params.atr_tp_mult:.1f}")

        history["cycles"].append({
            "cycle":     cycle,
            "improved":  improved,
            "fitness":   best_f,
            "params":    asdict(best_params),
            "result":    _result_brief(best_result),
        })
        if not improved:
            print(f"  [stop] No improvement this cycle — convergence detected.")
            break

    return best_params, history


def _result_brief(r: BacktestResult) -> dict:
    return {
        "return_pct":     r.total_return_pct,
        "annual_pct":     r.annual_return_pct,
        "max_dd_pct":     r.max_drawdown_pct,
        "sharpe":         r.sharpe_annual,
        "calmar":         r.calmar,
        "profit_factor":  r.profit_factor,
        "win_rate_pct":   r.win_rate_pct,
        "trades":         len(r.trades),
        "deployed_pct":   r.avg_deployed_pct,
        "n_adds":         r.n_adds,
    }


def _save_checkpoint(params: BacktestParams, fitness_val: float, result: BacktestResult,
                     cycle: int, spy_return: float) -> None:
    OPT_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_meta": {
            "saved_at":    datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cycle":       cycle,
            "fitness":     round(fitness_val, 4),
            "spy_return":  round(spy_return, 2),
        },
        "params":  asdict(params),
        "result":  _result_brief(result),
    }
    OPT_PARAMS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Learner in-the-loop (coordinate descent sobre backtest)")
    p.add_argument("--cycles",  type=int, default=10, help="Numero de ciclos de coordinate descent (default 10)")
    p.add_argument("--since",   default=None, help="Data inicial YYYY-MM-DD (default: -2 anos)")
    p.add_argument("--until",   default=None, help="Data final YYYY-MM-DD (default: hoje)")
    p.add_argument("--capital", type=float, default=5000.0)
    args = p.parse_args()

    end_dt   = datetime.strptime(args.until, "%Y-%m-%d") if args.until else datetime.now()
    start_dt = datetime.strptime(args.since, "%Y-%m-%d") if args.since else end_dt - timedelta(days=730)

    print(f"\n=== LEARNER IN-THE-LOOP ===")
    print(f"Periodo:   {start_dt.date()} -> {end_dt.date()}")
    print(f"Capital:   EUR {args.capital:,.0f}")
    print(f"Cycles:    {args.cycles}")
    print(f"Params:    {len(PARAM_SPACE)} x {sum(len(values_for(p)) for p in PARAM_SPACE) // len(PARAM_SPACE)} vals avg")

    # Load data once
    calendar, histories, spy_closes, spy_index = load_data_for_backtest(start_dt, end_dt)
    prime_regimes(calendar)
    earnings_cal = build_earnings_calendar(start_dt, end_dt)

    # Pre-instantiate BonnieML (model_v2 se existir)
    base_params = BacktestParams()
    bonnie_ml = BonnieML(base_params)
    if bonnie_ml.available:
        ftype = "v2" if "v2" in str(bonnie_ml.model_path) else "v1"
        print(f"Bonnie ML: {ftype} loaded ({bonnie_ml.model_path.name})")
    else:
        print(f"Bonnie ML: NAO disponivel — fitness usara pass-through")

    evaluator = Evaluator(start_dt, end_dt, args.capital,
                          calendar, histories, spy_closes, spy_index,
                          bonnie_ml, earnings_cal)

    print(f"SPY return no periodo: {evaluator.spy_return:+.1f}%")
    print(f"Regime dominante esperado (vai ser computado por run):")

    t0 = time.time()
    best, history = coordinate_descent(evaluator, args.cycles)
    elapsed = time.time() - t0

    print(f"\n=== DONE ===  ({elapsed/60:.1f} min total, {evaluator.n_evals} evaluations)")
    print(f"Optimized params:")
    for k, v in asdict(best).items():
        default = getattr(BacktestParams(), k)
        marker = "" if v == default else f"  (was {default})"
        print(f"  {k:28s} = {v}{marker}")
    print(f"\nWritten to: {OPT_PARAMS_PATH.relative_to(BASE_DIR)}")
    print(f"Run: PYTHONPATH=. python scripts/backtest.py --use-optimized")
