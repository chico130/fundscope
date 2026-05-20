"""
indicators.py — RSI14, EMA50/200, vol_ratio e distância EMA50 vetorizados.

Cada função recebe e devolve pd.Series/DataFrame indexados por data.
A computação é feita UMA vez por ticker (série completa) — não por (ticker, data).

Paridade com produção:
  - RSI14:  mesma semente e suavização Wilder que bot/data_layer.py:compute_rsi
  - EMA N:  mesma semente SMA dos primeiros N valores que bot/data_layer.py:compute_ema
  Tolerância para teste de paridade: 1e-4 (arredondamento ao round(x,2/4) da produção).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_HISTORY_BARS = 210   # igual a backtest.py — EMA-200 requer este mínimo


# ---------------------------------------------------------------------------
# Primitivas escalares que replicam produção (usadas para paridade e aqui)
# ---------------------------------------------------------------------------

def _ema_series(close: pd.Series, period: int) -> pd.Series:
    """
    EMA com semente SMA dos primeiros `period` valores — replica compute_ema.

    Devolve Series com NaN nas primeiras period-1 posições.
    """
    k   = 2.0 / (period + 1)
    arr = close.to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)

    if len(arr) < period:
        return pd.Series(out, index=close.index)

    out[period - 1] = np.mean(arr[:period])          # semente SMA
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)

    return pd.Series(out, index=close.index)


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI de Wilder sobre a série completa — replica compute_rsi barra a barra.

    Devolve Series com NaN até à barra `period` (inclusive).
    """
    arr    = close.to_numpy(dtype=float)
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas,  0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    out    = np.full(len(arr), np.nan)

    if len(arr) < period + 1:
        return pd.Series(out, index=close.index)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i + 1] = round(100.0 - 100.0 / (1.0 + rs), 2)

    return pd.Series(out, index=close.index)


# ---------------------------------------------------------------------------
# Função principal — adiciona todas as features a um DataFrame OHLCV
# ---------------------------------------------------------------------------

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recebe df com colunas [open, high, low, close, volume].
    Devolve df com colunas adicionais:

      rsi_14            — RSI de Wilder 14 períodos
      ema50             — EMA 50 com semente SMA
      ema200            — EMA 200 com semente SMA
      ema50_above_200   — bool: ema50 > ema200 (gate de produção)
      ema50_dist_pct    — (close - ema50) / ema50 * 100  (feature nova)
      vol_ratio         — volume / volume.rolling(20).mean()

    As primeiras MIN_HISTORY_BARS barras ficam com NaN nos indicadores dependentes
    do histórico longo — são excluídas pela tabela de candidatos a jusante.
    """
    close  = df["close"]
    volume = df["volume"]

    df = df.copy()
    df["rsi_14"]  = _rsi_series(close, 14)
    df["ema50"]   = _ema_series(close, 50)
    df["ema200"]  = _ema_series(close, 200)

    valid = df["ema50"].notna() & df["ema200"].notna()
    df["ema50_above_200"] = np.where(valid, df["ema50"] > df["ema200"], np.nan)
    df["ema50_dist_pct"]  = np.where(
        valid,
        (close - df["ema50"]) / df["ema50"] * 100.0,
        np.nan,
    )

    avg_vol_20       = volume.rolling(20, min_periods=20).mean()
    df["vol_ratio"]  = np.where(avg_vol_20 > 0, volume / avg_vol_20, np.nan)

    return df
