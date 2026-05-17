"""
bot/mass_backtest.py — Motor de Varredura em Massa para a Bonnie.

Estratégia de eficiência:
  • Regimes (SPY/RSP) pré-calculados numa única chamada ao yfinance.
  • Histórico de cada ticker descarregado uma única vez e fatiado por data.
  • Timeout de 90s por download (ThreadPoolExecutor) — evita pendurar.
  • Save incremental após cada ticker — retoma de onde parou em caso de crash.

CLI:
    python -m bot.mass_backtest
    python -m bot.mass_backtest --horizon 15 --days 60
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, timedelta

import yfinance as yf

# Força UTF-8 no terminal Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from .config import DATA_BETA_DIR
from .backtest import (
    OBSERVATIONS_PATH,
    run_backtest_for_date,
    save_observations_batch,
    prime_regime_cache,
)

FALLBACK_TICKERS = [
    "NVDA", "AMD", "INTC", "MU", "MSFT", "AAPL",
    "TSLA", "META", "GOOGL", "AMZN",
]

_SEP            = "=" * 60
_DOWNLOAD_TIMEOUT = 90  # segundos máximos por download yfinance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_watchlist_tickers() -> list[str]:
    """Carrega tickers do watchlist.json; fallback para lista tech padrão."""
    try:
        data = json.loads((DATA_BETA_DIR / "watchlist.json").read_text(encoding="utf-8"))
        tickers = [c["ticker"] for c in data.get("candidates", []) if c.get("ticker")]
        if tickers:
            return tickers
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return FALLBACK_TICKERS


def _business_days(start: date, end: date) -> list[date]:
    """Gera lista de dias úteis (segunda a sexta) entre start e end (inclusive)."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _existing_keys() -> set[tuple[str, str]]:
    """Retorna conjunto de (ticker, date_observed) já gravados."""
    try:
        obs = json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8"))
        return {(o["ticker"], o["date_observed"]) for o in obs}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _download_with_timeout(ticker: str, dl_start: str, dl_end: str):
    """Download yfinance com timeout de _DOWNLOAD_TIMEOUT segundos."""
    def _fetch():
        return yf.Ticker(ticker).history(
            start=dl_start, end=dl_end,
            interval="1d", auto_adjust=True,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_fetch)
        return future.result(timeout=_DOWNLOAD_TIMEOUT)


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

def run_mass_backtest(horizon_days: int = 10, lookback_days: int = 3650) -> None:
    today      = date.today()
    end_date   = today - timedelta(days=horizon_days + 2)
    start_date = today - timedelta(days=lookback_days)

    if start_date >= end_date:
        print("Erro: janela de lookback demasiado pequena para o horizonte definido.")
        return

    dates   = _business_days(start_date, end_date)
    tickers = _load_watchlist_tickers()

    date_strs = [d.strftime("%Y-%m-%d") for d in dates]

    print(_SEP)
    print("  FundScope Mass Backtest — Motor da Bonnie")
    print(_SEP)
    print(f"  Período:      {date_strs[0]} → {date_strs[-1]}")
    print(f"  Dias úteis:   {len(dates)}")
    print(f"  Tickers:      {len(tickers)}")
    print(f"  Combinações:  {len(tickers) * len(dates)}")
    print(f"  Horizonte:    {horizon_days} dias de trading")
    print(_SEP)

    # 1. Pré-calcular regimes (1 download SPY/RSP)
    print("\n[0] A pré-calcular regimes de mercado (1 download SPY)...")
    prime_regime_cache(date_strs)
    print("    Regimes calculados.\n")

    # 2. Carregar chaves existentes para deduplicação
    existing_keys = _existing_keys()
    total_saved   = 0

    # 3. Loop por ticker — save incremental após cada um
    for t_idx, ticker in enumerate(tickers, 1):
        print(f"[{t_idx:>2}/{len(tickers)}] {ticker:<8} — a descarregar histórico...", flush=True)

        dl_start = (start_date - timedelta(days=560)).strftime("%Y-%m-%d")
        dl_end   = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            df = _download_with_timeout(ticker, dl_start, dl_end)
        except FuturesTimeoutError:
            print(f"         TIMEOUT ({_DOWNLOAD_TIMEOUT}s) — ticker ignorado.", flush=True)
            time.sleep(2)
            continue
        except Exception as exc:
            print(f"         ERRO ao descarregar: {exc}", flush=True)
            time.sleep(2)
            continue

        if df is None or df.empty:
            print("         Sem dados — ticker ignorado.", flush=True)
            time.sleep(1)
            continue

        ticker_obs: list[dict] = []
        for d in dates:
            date_str = d.strftime("%Y-%m-%d")
            if (ticker, date_str) in existing_keys:
                continue

            obs = run_backtest_for_date(
                ticker=ticker,
                target_date=date_str,
                horizon_days=horizon_days,
                preloaded_df=df,
            )
            if obs:
                ticker_obs.append(obs)
                existing_keys.add((ticker, date_str))

        # Save incremental — garante que este ticker fica gravado mesmo se o próximo falhar
        if ticker_obs:
            saved = save_observations_batch(ticker_obs)
            total_saved += saved
            print(f"         {saved} BUY(s)  [total acumulado: {total_saved}]", flush=True)
        else:
            print("         sem sinais BUY", flush=True)

        time.sleep(1)

    print(f"\n{_SEP}")
    print(f"  Mass Backtest concluído.")
    print(f"  {total_saved} observações novas geradas e guardadas para a Bonnie.")
    if total_saved > 0:
        print(f"  Ficheiro: {OBSERVATIONS_PATH}")
    print(_SEP)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FundScope Mass Backtest — Alimenta o cérebro da Bonnie",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemplo: python -m bot.mass_backtest --horizon 10 --days 3650",
    )
    parser.add_argument(
        "--horizon", type=int, default=10,
        help="Horizonte de avaliação em dias de trading (default: 10)",
    )
    parser.add_argument(
        "--days", type=int, default=3650,
        help="Quantos dias de histórico varrer (default: 3650)",
    )
    args = parser.parse_args()

    run_mass_backtest(horizon_days=args.horizon, lookback_days=args.days)
