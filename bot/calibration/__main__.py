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
from bot.calibration.sweep      import run_sweep, DEFAULT_GRID
from bot.calibration.report     import write_report


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

    print(f"\n{_sep}")
    print("  Calibração concluída.")
    print(f"  → data/calibration/REPORT.md")
    print(f"  → data/calibration/sweep_results.csv")
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
    return p.parse_args()


if __name__ == "__main__":
    main()
