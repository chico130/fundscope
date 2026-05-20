"""
regime.py — Série de regime de mercado vetorizada (SPY/RSP), zero look-ahead.

Replica a lógica de backtest.py:prime_regime_cache mas como Series diária
sobre o histórico completo, calculada uma única vez.

Regimes:
  bull_trending      — SPY acima da EMA-200, breadth saudável
  bull_lateral       — SPY acima da EMA-200 mas breadth fraca, ou ligeiramente abaixo
  bear_correction    — SPY > 5% abaixo da EMA-200, queda moderada
  bear_capitulation  — SPY > 5% abaixo da EMA-200, queda > 10% em 20 dias
  unknown            — histórico insuficiente

BEAR_REGIMES exportado para usar como constante nos filtros do sweep.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bot.calibration.cache import load_ohlcv
from bot.calibration.indicators import _ema_series

BEAR_REGIMES = frozenset({"bear_correction", "bear_capitulation"})
_MIN_SPY_BARS = 210


def build_regime_series(dates: pd.DatetimeIndex | None = None) -> pd.Series:
    """
    Devolve pd.Series(index=DatetimeIndex, data=str) com o regime de cada dia.

    `dates` — se fornecido, restringe o resultado a essas datas (inner join).
    Requer SPY e RSP em cache (ensure_ohlcv_cache deve ter corrido antes).
    """
    spy_df = load_ohlcv("SPY")
    rsp_df = load_ohlcv("RSP")

    if spy_df is None or spy_df.empty:
        raise RuntimeError("SPY não está em cache. Corre ensure_ohlcv_cache primeiro.")

    spy = spy_df["close"].sort_index()
    rsp = rsp_df["close"].sort_index() if rsp_df is not None else None

    ema200   = _ema_series(spy, 200)
    pct_ema  = (spy - ema200) / ema200 * 100.0   # % acima/abaixo da EMA-200
    ret_20d  = spy.pct_change(20)                 # retorno 20 dias (look-back apenas)

    if rsp is not None:
        rsp_aligned = rsp.reindex(spy.index)
        rs_ratio    = rsp_aligned / spy
        breadth_ok  = rs_ratio.pct_change(20) >= -0.02
    else:
        breadth_ok = pd.Series(True, index=spy.index)

    regime = _classify(pct_ema, ret_20d, breadth_ok)

    if dates is not None:
        regime = regime.reindex(dates)

    return regime


# ---------------------------------------------------------------------------
# Classificação vetorizada
# ---------------------------------------------------------------------------

def _classify(
    pct_ema:    pd.Series,
    ret_20d:    pd.Series,
    breadth_ok: pd.Series,
) -> pd.Series:
    """Aplica as mesmas regras de backtest.py com np.select."""
    conditions = [
        pct_ema.isna(),                                            # histórico insuficiente
        pct_ema <= -5.0,                                          # abaixo 5% da EMA-200
        (pct_ema <= -5.0) & (ret_20d < -0.10),                   # capitulação
        (pct_ema > -5.0) & (pct_ema < 0.0),                      # ligeiramente abaixo
        pct_ema >= 0.0,                                            # acima da EMA-200
    ]
    # np.select com condições mais específicas primeiro
    out = np.select(
        [
            pct_ema.isna(),
            (pct_ema <= -5.0) & (ret_20d < -0.10),
            (pct_ema <= -5.0),
            (pct_ema <  0.0),
            breadth_ok,
        ],
        [
            "unknown",
            "bear_capitulation",
            "bear_correction",
            "bull_lateral",
            "bull_trending",
        ],
        default="bull_lateral",
    )
    return pd.Series(out, index=pct_ema.index, dtype=str)
