"""
candidates.py — Tabela mestra: 1 linha por (ticker, dia) + outcomes futuros.

Invariante de correção (zero look-ahead):
  Features (rsi_14, ema50, vol_ratio, regime) usam dados ESTRITAMENTE até ao
  dia da linha (inclusive). Outcomes (final_return, max_profit, etc.) usam
  ESTRITAMENTE as H barras seguintes — nunca o dia de entrada.

Definições de outcome idênticas a backtest.py:_evaluate_outcome para garantir
comparabilidade com as observações da Bonnie.

A tabela é construída UMA vez e cacheada em data/cache/candidates.parquet.
O sweep de parâmetros opera sobre esta tabela (máscara + groupby).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from bot.config import BASE_DIR
from bot.calibration.cache    import load_ohlcv, cached_tickers
from bot.calibration.indicators import add_indicators, MIN_HISTORY_BARS
from bot.calibration.regime   import build_regime_series

_CACHE_PATH = BASE_DIR / "data" / "cache" / "candidates.parquet"


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def build_candidate_table(
    tickers:  list[str],
    start:    str,
    end:      str,
    horizons: list[int],
    force:    bool = False,
) -> pd.DataFrame:
    """
    Constrói (ou carrega de cache) a tabela mestra de candidatos.

    Colunas base: ticker, date, close,
                  rsi_14, ema50_above_200, ema50_dist_pct, vol_ratio, regime
    Por horizonte H: out_<H>_final_pct, out_<H>_max_profit_pct,
                     out_<H>_max_drawdown_pct, out_<H>_success
    """
    if not force and _CACHE_PATH.exists():
        print("[candidates] A carregar tabela de cache...")
        df = pd.read_parquet(_CACHE_PATH)
        # Verificar se os horizontes pedidos já estão calculados
        missing_h = [h for h in horizons
                     if f"out_{h}_final_pct" not in df.columns]
        if not missing_h:
            return df
        print(f"[candidates] Horizontes {missing_h} em falta — a recalcular.")

    regime_series = build_regime_series()
    print(f"[candidates] Regime calculado ({len(regime_series)} dias).")

    parts: list[pd.DataFrame] = []
    available = set(cached_tickers())

    for i, ticker in enumerate(tickers, 1):
        if ticker not in available:
            continue
        df_raw = load_ohlcv(ticker)
        if df_raw is None or len(df_raw) < MIN_HISTORY_BARS:
            continue

        # Filtrar janela de avaliação (com calço para indicadores)
        df_ind = add_indicators(df_raw)

        # Alinhar regime por data
        df_ind["regime"] = regime_series.reindex(df_ind.index).fillna("unknown")

        # Restringir às datas de avaliação [start, end]
        mask = (df_ind.index >= start) & (df_ind.index <= end)
        df_eval = df_ind[mask].copy()
        if df_eval.empty:
            continue

        # Calcular outcomes futuros para cada horizonte
        for H in horizons:
            _add_outcomes(df_ind, df_eval, H)

        df_eval["ticker"] = ticker
        df_eval.index.name = "date"
        parts.append(df_eval.reset_index())

        if i % 50 == 0:
            print(f"  [{i}/{len(tickers)}] tickers processados...", flush=True)

    if not parts:
        raise RuntimeError("Nenhum ticker com dados suficientes.")

    table = pd.concat(parts, ignore_index=True)
    table["date"] = pd.to_datetime(table["date"])

    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(_CACHE_PATH, index=False)
    print(f"[candidates] {len(table):,} linhas guardadas em cache.")
    return table


# ---------------------------------------------------------------------------
# Outcomes futuros vetorizados
# ---------------------------------------------------------------------------

def _add_outcomes(df_full: pd.DataFrame, df_eval: pd.DataFrame, H: int) -> None:
    """
    Adiciona colunas out_<H>_* a df_eval usando df_full (série completa, sem
    restrição de data — necessário para aceder às barras futuras).

    Zero look-ahead: shift(-k) com k >= 1 — nunca o dia de entrada.
    As últimas H barras ficam NaN (sem futuro suficiente).
    """
    close  = df_full["close"]
    high   = df_full["high"]
    low    = df_full["low"]

    # final_return: close(t+H) vs close(t)
    final_close = close.shift(-H)
    final_ret   = (final_close - close) / close * 100.0

    # max_profit:   max(high[t+1..t+H]) vs close(t)
    future_highs = pd.concat(
        [high.shift(-k) for k in range(1, H + 1)], axis=1
    ).max(axis=1)
    max_profit = (future_highs - close) / close * 100.0

    # max_drawdown: min(low[t+1..t+H]) vs close(t)
    future_lows  = pd.concat(
        [low.shift(-k)  for k in range(1, H + 1)], axis=1
    ).min(axis=1)
    max_dd = (future_lows - close) / close * 100.0

    # success: close(t+H) > close(t)
    success = final_close > close

    # Projetar no subconjunto df_eval (por index/data)
    df_eval[f"out_{H}_final_pct"]       = final_ret.reindex(df_eval.index).values
    df_eval[f"out_{H}_max_profit_pct"]  = max_profit.reindex(df_eval.index).values
    df_eval[f"out_{H}_max_drawdown_pct"]= max_dd.reindex(df_eval.index).values
    df_eval[f"out_{H}_success"]         = success.reindex(df_eval.index).values
