"""
bot/calibration/__main__.py — CLI do motor de calibração offline.

Uso:
  python -m bot.calibration [opções]

Exemplos:
  python -m bot.calibration --limit 20 --horizons 10
  python -m bot.calibration --start 2022-01-01 --end 2026-01-01 --horizons 5,10,15
  python -m bot.calibration --refresh-cache --n-min 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from bot.calibration.universe   import get_sp500_tickers
from bot.calibration.cache      import ensure_ohlcv_cache
from bot.calibration.candidates import build_candidate_table, _CACHE_PATH as CAND_CACHE
from bot.calibration.sweep      import run_sweep, run_sweep_oos, DEFAULT_GRID
from bot.calibration.report     import write_report, write_oos_report
from bot.calibration.adaptive   import AdaptiveCalibrator, DEFAULT_BASELINE_PATH, DEFAULT_LOG_PATH
from bot.calibration.regression import (
    run_regression_test,
    save_regression_log,
    update_baseline_from_regression,
    DEFAULT_LOG_PATH as REGRESSION_LOG_PATH,
)


def main() -> None:
    args = _parse_args()

    today  = date.today()
    end    = args.end   or (today - timedelta(days=15)).strftime("%Y-%m-%d")
    start  = args.start or (today - timedelta(days=4 * 365 + 15)).strftime("%Y-%m-%d")
    horizons = [int(h) for h in args.horizons.split(",")]

    _sep = "=" * 60
    print(_sep)
    print("  FundScope — Motor de Calibração Offline (S&P 500)")
    print(_sep)
    print(f"  Janela:     {start} → {end}")
    print(f"  Horizontes: {horizons} dias de trading")
    print(f"  N mínimo:   {args.n_min} trades por combinação")
    print(_sep)

    # 1. Universo
    print("\n[1] A obter universo S&P 500...")
    t0 = time.time()
    tickers = get_sp500_tickers(refresh=args.refresh_cache)
    if args.limit:
        tickers = tickers[:args.limit]
    print(f"    {len(tickers)} tickers ({time.time()-t0:.1f}s)")

    # 2. Cache yfinance → Parquet
    print(f"\n[2] A garantir cache OHLCV (yfinance)...")
    t0 = time.time()
    # SPY e RSP sempre necessários (regime)
    all_dl = list(dict.fromkeys(["SPY", "RSP"] + tickers))
    ensure_ohlcv_cache(all_dl, start, end, refresh=args.refresh_cache)
    print(f"    Cache pronta ({time.time()-t0:.1f}s)")

    # 3. Tabela mestra de candidatos
    print(f"\n[3] A construir tabela de candidatos...")
    t0 = time.time()
    force_cand = args.refresh_cache or not CAND_CACHE.exists()
    cand = build_candidate_table(tickers, start, end, horizons, force=force_cand)
    print(f"    {len(cand):,} linhas ({time.time()-t0:.1f}s)")

    # 3b. Modo adaptativo (recalibração automática) — ramo alternativo
    if args.adaptive:
        _run_adaptive(cand, args)
        return

    # 3c. Modo de regressão — ramo alternativo
    if args.regression:
        _run_regression(cand, tickers, horizons, args)
        return

    # 4. Grid customizado (opcional)
    grid = None
    if args.grid:
        try:
            grid = json.loads(args.grid.read_text(encoding="utf-8"))
            print(f"\n    Grid customizado: {args.grid}")
        except Exception as exc:
            print(f"\n    WARN: grid ignorado ({exc}) — a usar grid default.")

    # 5. Sweep de parâmetros
    print(f"\n[4] A correr sweep de parâmetros...")
    t0 = time.time()
    results = run_sweep(cand, horizons, grid=grid, n_min=args.n_min)
    print(f"    {len(results)} combinações avaliadas ({time.time()-t0:.1f}s)")

    # 6. Relatório
    print(f"\n[5] A gerar relatório...")
    write_report(results, universe_n=len(tickers),
                 start=start, end=end, horizons=horizons)

    # 7. Protocolo OOS (opcional)
    if args.oos:
        print(f"\n[6] A correr protocolo OOS (train_end={args.train_end}, val_start={args.val_start}, folds={args.folds})...")
        t0 = time.time()
        _, _, oos_report = run_sweep_oos(
            cand,
            horizons,
            train_end=args.train_end,
            val_start=args.val_start,
            grid=grid,
            n_min=args.n_min,
            n_folds=args.folds,
        )
        print(f"    OOS concluído ({time.time() - t0:.1f}s)")

        print(f"\n[7] A gerar relatório OOS...")
        write_oos_report(
            oos_report,
            horizons=horizons,
            train_end=args.train_end,
            val_start=args.val_start,
            n_folds=args.folds,
            n_min=args.n_min,
        )

        print("\n  Resultados OOS:")
        _print_oos_table(oos_report)

    print(f"\n{_sep}")
    print("  Calibração concluída.")
    print(f"  → data/calibration/REPORT.md")
    print(f"  → data/calibration/sweep_results.csv")
    if args.oos:
        print(f"  → data/calibration/OOS_REPORT.md")
        print(f"  → data/calibration/oos_report.csv")
    print(_sep)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="FundScope — Motor de Calibração Offline (S&P 500)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--start",         default=None,  help="Data inicial YYYY-MM-DD (default: hoje−4a)")
    p.add_argument("--end",           default=None,  help="Data final YYYY-MM-DD (default: hoje−15d)")
    p.add_argument("--horizons",      default="10",  help="Horizontes em dias, separados por vírgula (default: 10)")
    p.add_argument("--limit",         type=int, default=0, help="Limitar a N tickers (0=todos; para smoke test)")
    p.add_argument("--n-min",         type=int, default=30, help="Min. trades para entrar no ranking (default: 30)")
    p.add_argument("--refresh-cache", action="store_true",  help="Forçar re-download yfinance e recalcular candidatos")
    p.add_argument("--grid",          type=lambda p: __import__("pathlib").Path(p),
                   default=None,      help="Caminho para JSON com grelha de parâmetros customizada")
    # Flags OOS
    p.add_argument("--oos",           action="store_true",  help="Activar protocolo de validação Out-of-Sample")
    p.add_argument("--train-end",     default="2024-12-31", help="Data de fim do treino OOS (default: 2024-12-31)")
    p.add_argument("--val-start",     default="2025-01-01", help="Data de início da validação OOS (default: 2025-01-01)")
    p.add_argument("--folds",         type=int, default=1,  help="Nº de folds walk-forward (1=divisão simples; default: 1)")
    # Modo adaptativo
    p.add_argument("--adaptive",      action="store_true",  help="Avaliar performance actual e recalibrar se necessário (motor adaptativo)")
    # Modo de regressão
    p.add_argument("--regression",    action="store_true",  help="Comparar métricas actuais vs baseline guardado (regression test)")
    p.add_argument("--baseline",      type=lambda s: __import__("pathlib").Path(s),
                   default=DEFAULT_BASELINE_PATH, help="Caminho para strategy_baseline.json (default: bot/calibration/strategy_baseline.json)")
    p.add_argument("--recal-trigger-pf", type=float, default=0.9, help="Recalibrar se PF actual cair abaixo deste valor (default: 0.9)")
    p.add_argument("--lookback-days",    type=int, default=90, help="Janela deslizante de avaliação em dias (default: 90)")
    return p.parse_args()


def _run_adaptive(cand: "pd.DataFrame", args: argparse.Namespace) -> None:
    """Avalia a performance actual e recalibra se algum trigger disparar."""
    _sep = "=" * 60
    print(f"\n{_sep}")
    print("  MODO ADAPTATIVO — Recalibração automática")
    print(_sep)

    calibrator = AdaptiveCalibrator(
        baseline_path=args.baseline,
        recal_trigger_pf=args.recal_trigger_pf,
        lookback_days=args.lookback_days,
        min_trades_to_evaluate=args.n_min,
    )

    print(f"\n[A] Avaliação da performance actual (últimos {args.lookback_days} dias)...")
    perf = calibrator.evaluate_current_performance(cand)
    print(f"    Janela:     {perf['lookback_start']} → {perf['lookback_end']}")
    print(f"    PF actual:  {perf['pf_current']}")
    print(f"    Expectancy: {perf['expectancy_current']}")
    print(f"    Win rate:   {perf['win_rate']}")
    print(f"    N trades:   {perf['n_trades']}" + ("  ⚠️ abaixo do mínimo" if perf['low_sample'] else ""))
    print(f"    Regime:     {perf['regime']}")
    print(f"    Precisa recalibrar? {'SIM' if perf['needs_recal'] else 'NÃO'}")

    if perf["needs_recal"]:
        print(f"\n[B] A recalibrar (treino 2a + validação OOS {args.lookback_days}d)...")
        t0 = time.time()
        result = calibrator.recalibrate(cand)
        print(f"    Recalibração concluída ({time.time() - t0:.1f}s)")
        _print_adaptive_comparison(result)
        action = result["action"]
    else:
        print("\n[B] Parâmetros actuais ainda válidos — sem recalibração.")
        action = "NO_CHANGE"

    calibrator.log_run(perf, action, log_path=DEFAULT_LOG_PATH)

    print(f"\n{_sep}")
    print(f"  Acção: {action}")
    print(f"  → log: {DEFAULT_LOG_PATH}")
    if action == "UPDATED":
        print(f"  → baseline actualizado: {args.baseline}")
    print(_sep)


def _print_adaptive_comparison(result: dict) -> None:
    """Imprime comparação antes/depois da recalibração."""
    old, new = result["old_params"], result["new_params"]
    print(f"    Acção:    {result['action']}")
    print(f"    Melhoria: {result['improvement_pct']}% (PF)")
    print(f"    {'Parâmetro':<26} {'Activo':>12} {'Candidato':>12}")
    for key in ("rsi_buy_max", "vol_ratio_min", "require_ema50_above_200",
                "ema50_dist_min_pct", "apply_regime_veto"):
        ov = old.get(key) if old else None
        nv = new.get(key) if new else None
        print(f"    {key:<26} {str(ov):>12} {str(nv):>12}")


def _print_oos_table(oos_report: "pd.DataFrame") -> None:
    """Imprime tabela ASCII com resultados OOS no terminal."""
    import math

    def _fmt(v: object, fmt: str = "") -> str:
        try:
            f = float(v)  # type: ignore[arg-type]
            if math.isnan(f):
                return "—"
            return format(f, fmt)
        except (TypeError, ValueError):
            return str(v) if v is not None else "—"

    headers = ["Horizonte", "RSI≤", "Vol≥", "PF Treino", "PF Val", "Queda PF%", "Robustez", "Estado"]
    widths   = [len(h) for h in headers]

    rows_data: list[list[str]] = []
    for _, r in oos_report.iterrows():
        row = [
            f"{int(r['horizon'])}d",
            _fmt(r.get("rsi_buy_max"), ".0f"),
            _fmt(r.get("vol_ratio_min"), ".1f"),
            _fmt(r.get("train_pf"), ".3f"),
            _fmt(r.get("val_pf"), ".3f"),
            (_fmt(r.get("pf_drop_pct"), ".1%") if _fmt(r.get("pf_drop_pct")) != "—" else "—"),
            _fmt(r.get("robustness_score"), ".1f"),
            str(r.get("status", "—")),
        ]
        rows_data.append(row)
        for j, cell in enumerate(row):
            widths[j] = max(widths[j], len(cell))

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    print(sep)
    print("|" + "|".join(f" {h:<{w}} " for h, w in zip(headers, widths)) + "|")
    print(sep)
    for row in rows_data:
        print("|" + "|".join(f" {cell:<{w}} " for cell, w in zip(row, widths)) + "|")
    print(sep)


def _run_regression(
    cand: "pd.DataFrame",
    tickers: list[str],
    horizons: list[int],
    args: argparse.Namespace,
) -> None:
    """Corre o regression test e imprime tabela comparativa."""
    _sep = "=" * 60
    print(f"\n{_sep}")
    print("  MODO REGRESSÃO — Comparação vs Baseline")
    print(_sep)

    print(f"\n[R] A correr regression test ({len(tickers)} tickers, horizonte {horizons[0]}d)...")
    result = run_regression_test(
        tickers_new=tickers,
        params_new=None,
        baseline_path=args.baseline,
        horizons=horizons,
        _cand=cand,
    )

    print()
    _print_regression_table(result)

    verdict = result["delta"]["verdict"]
    print(f"\n  Veredicto: {verdict}")
    print(f"\n  {result['recommendation']}")

    save_regression_log(result, REGRESSION_LOG_PATH)
    print(f"\n  → log: {REGRESSION_LOG_PATH}")

    if verdict == "✅ MELHOROU":
        print("\n  Actualizar baseline com configuração actual? (s/N) ", end="", flush=True)
        try:
            resposta = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            resposta = ""
        if resposta == "s":
            update_baseline_from_regression(result, args.baseline)
            print(f"  ✅ Baseline actualizado: {args.baseline}")
        else:
            print("  Baseline mantido sem alterações.")

    print(f"\n{_sep}")
    print("  Regression test concluído.")
    print(_sep)


def _print_regression_table(result: dict) -> None:
    """Imprime tabela com box-drawing comparativa de regressão."""
    import math

    base  = result["baseline"]
    new   = result["new"]
    delta = result["delta"]

    def _f(v: object, fmt: str) -> str:
        try:
            f = float(v)  # type: ignore[arg-type]
            return "—" if math.isnan(f) else format(f, fmt)
        except (TypeError, ValueError):
            return "—"

    def _delta_cell(v: object, positive_good: bool = True) -> str:
        try:
            f = float(v)  # type: ignore[arg-type]
            if math.isnan(f):
                return "—"
            sign = "+" if f >= 0 else ""
            txt  = f"{sign}{f:.1f}%"
            if positive_good:
                if f > 0:
                    txt += " ✅"
                elif f < -5:
                    txt += " 🔴"
            return txt
        except (TypeError, ValueError):
            return "—"

    n_base = int(base["n_trades"])
    n_new  = int(new["n_trades"])
    n_delta_pct = (
        100.0 * (n_new - n_base) / n_base if n_base else float("nan")
    )
    n_delta_str = (
        "—" if math.isnan(n_delta_pct)
        else f"{'+'if n_delta_pct>=0 else ''}{n_delta_pct:.1f}%"
    )

    rows: list[tuple[str, str, str, str]] = [
        (
            "Profit Factor",
            _f(base["pf"],        ".2f"),
            _f(new["pf"],         ".2f"),
            _delta_cell(delta["pf_change_pct"]),
        ),
        (
            "Expectancy",
            _f(base["expectancy"], "+.2f") + "%",
            _f(new["expectancy"],  "+.2f") + "%",
            _delta_cell(delta["expectancy_change_pct"]),
        ),
        (
            "Max Drawdown",
            _f(base["max_dd"], ".1f") + "%",
            _f(new["max_dd"],  ".1f") + "%",
            _delta_cell(delta["max_dd_change_pct"]),
        ),
        (
            "Nº Trades",
            str(n_base),
            str(n_new),
            n_delta_str,
        ),
    ]

    headers = ["Métrica", "Baseline", "Actual", "Variação"]
    widths  = [
        max(len(headers[i]), max(len(r[i]) for r in rows))
        for i in range(len(headers))
    ]

    top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    mid = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    bot = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"

    print(top)
    print("│" + "│".join(f" {h:<{w}} " for h, w in zip(headers, widths)) + "│")
    print(mid)
    for row in rows:
        print("│" + "│".join(f" {cell:<{w}} " for cell, w in zip(row, widths)) + "│")
    print(bot)


if __name__ == "__main__":
    main()
