"""
metrics.py — Métricas de avaliação de uma combinação de parâmetros.

Profit Factor, Win Rate, Expectancy, etc.
Definições alinhadas com atom-profit-factor e MOC_CRO.
"""
from __future__ import annotations

import pandas as pd

_INF_MARKER = 999.0     # substitui +inf no CSV (PF quando não há perdas)


def compute_metrics(returns: pd.Series, n_min: int = 30) -> dict:
    """
    Calcula métricas sobre a Series de final_return_pct dos trades selecionados.

    Devolve dicionário com:
      n_trades, win_rate, profit_factor, expectancy_pct,
      median_return_pct, total_return_pct, avg_max_dd_pct (*),
      low_sample (bool — True se n_trades < n_min)

    (*) avg_max_dd_pct requer que `returns` tenha o mesmo index que o DataFrame
        de origem com a coluna max_drawdown_pct; calculado externamente e passado
        via `extra` se disponível.
    """
    returns = returns.dropna()
    n = len(returns)

    if n == 0:
        return _empty_metrics()

    wins   = returns[returns > 0]
    losses = returns[returns < 0]

    gross_profit = float(wins.sum())
    gross_loss   = float(losses.abs().sum())

    if gross_loss == 0:
        pf = _INF_MARKER if gross_profit > 0 else float("nan")
    else:
        pf = gross_profit / gross_loss

    return {
        "n_trades":          n,
        "win_rate":          round(float((returns > 0).mean()), 4),
        "profit_factor":     round(float(pf), 4),
        "expectancy_pct":    round(float(returns.mean()), 4),
        "median_return_pct": round(float(returns.median()), 4),
        "total_return_pct":  round(float(returns.sum()), 4),
        "low_sample":        n < n_min,
    }


def compute_metrics_full(
    cand:   pd.DataFrame,
    mask:   pd.Series,
    H:      int,
    n_min:  int = 30,
) -> dict:
    """
    Variante que recebe a tabela completa e a máscara, para calcular também
    avg_max_dd_pct e avg_max_profit_pct.
    """
    subset = cand.loc[mask].dropna(subset=[f"out_{H}_final_pct"])
    base   = compute_metrics(subset[f"out_{H}_final_pct"], n_min)

    if subset.empty:
        return base

    dd_col = f"out_{H}_max_drawdown_pct"
    pp_col = f"out_{H}_max_profit_pct"

    if dd_col in subset.columns:
        base["avg_max_dd_pct"]     = round(float(subset[dd_col].mean()), 4)
    if pp_col in subset.columns:
        base["avg_max_profit_pct"] = round(float(subset[pp_col].mean()), 4)

    return base


def _empty_metrics() -> dict:
    return {
        "n_trades":          0,
        "win_rate":          float("nan"),
        "profit_factor":     float("nan"),
        "expectancy_pct":    float("nan"),
        "median_return_pct": float("nan"),
        "total_return_pct":  float("nan"),
        "low_sample":        True,
    }
