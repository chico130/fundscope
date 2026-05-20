"""
sweep.py — Grelha de parâmetros e agregação de métricas sobre a tabela mestra.

Cada combinação (param set × horizonte) resulta num dicionário de métricas.
O sweep é barato porque opera sobre a tabela pré-computada (máscara + groupby).

Parâmetros de produção atuais (hardcoded para destaque no relatório):
  RSI_BUY_MAX = 35, VOL_RATIO_MIN = 0.8, EMA50_ABOVE_200 = True,
  REGIME_VETO = True, EMA50_DIST = None
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, asdict

import pandas as pd

from bot.calibration.metrics import compute_metrics_full
from bot.calibration.regime  import BEAR_REGIMES

# Parâmetros de produção (backtest.py:RSI_BUY_MAX + _clyde_signal)
PRODUCTION_PARAMS = {
    "rsi_buy_max":             35.0,
    "vol_ratio_min":           0.8,
    "require_ema50_above_200": True,
    "ema50_dist_min_pct":      None,
    "apply_regime_veto":       True,
}

DEFAULT_GRID: dict[str, list] = {
    "rsi_buy_max":             [30.0, 32.0, 34.0, 35.0, 36.0, 38.0, 40.0],
    "vol_ratio_min":           [0.0, 0.8, 1.0, 1.2],
    "require_ema50_above_200": [True, False],
    "ema50_dist_min_pct":      [None, -3.0, -1.0, 0.0, 2.0],
    "apply_regime_veto":       [True, False],
}


@dataclass
class ParamSet:
    rsi_buy_max:             float
    vol_ratio_min:           float
    require_ema50_above_200: bool
    ema50_dist_min_pct:      float | None
    apply_regime_veto:       bool
    horizon:                 int


def run_sweep(
    cand:     pd.DataFrame,
    horizons: list[int],
    grid:     dict[str, list] | None = None,
    n_min:    int = 30,
) -> pd.DataFrame:
    """
    Corre o sweep de parâmetros sobre a tabela de candidatos.

    Devolve DataFrame com uma linha por (combinação × horizonte), ordenado por
    profit_factor desc (filtrando combinações com low_sample=True).
    """
    if grid is None:
        grid = DEFAULT_GRID

    combos = list(itertools.product(*grid.values()))
    keys   = list(grid.keys())

    total = len(combos) * len(horizons)
    print(f"[sweep] {len(combos)} combinações × {len(horizons)} horizontes = {total} avaliações...")

    rows: list[dict] = []
    for i, (combo, H) in enumerate(
        itertools.product(combos, horizons), 1
    ):
        p = ParamSet(**dict(zip(keys, combo)), horizon=H)
        mask = _build_mask(cand, p, H)
        m    = compute_metrics_full(cand, mask, H, n_min)
        row  = {**asdict(p), **m}
        row["is_production"] = _is_production(p)
        rows.append(row)

        if i % 500 == 0:
            print(f"  {i}/{total} avaliações concluídas...", flush=True)

    print(f"[sweep] concluído.")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Construção da máscara de BUY
# ---------------------------------------------------------------------------

def _build_mask(cand: pd.DataFrame, p: ParamSet, H: int) -> pd.Series:
    """Máscara booleana que seleciona os dias com sinal BUY dado o ParamSet."""
    # Requer outcome calculado para este horizonte
    outcome_col = f"out_{H}_final_pct"
    if outcome_col not in cand.columns:
        return pd.Series(False, index=cand.index)

    mask = cand["rsi_14"] <= p.rsi_buy_max

    if p.require_ema50_above_200:
        # Replicar produção: ema50_above_200 is not False
        # (None = dados insuficientes → não veta)
        mask &= cand["ema50_above_200"].fillna(True).astype(bool)

    if p.ema50_dist_min_pct is not None:
        mask &= cand["ema50_dist_pct"] >= p.ema50_dist_min_pct

    # vol_ratio: em falta não veta (alinhado com backtest.py:258)
    mask &= (cand["vol_ratio"] >= p.vol_ratio_min) | cand["vol_ratio"].isna()

    if p.apply_regime_veto:
        mask &= ~cand["regime"].isin(BEAR_REGIMES)

    # Só contar barras com outcome válido
    mask &= cand[outcome_col].notna()

    return mask


def _is_production(p: ParamSet) -> bool:
    return (
        p.rsi_buy_max             == PRODUCTION_PARAMS["rsi_buy_max"]
        and p.vol_ratio_min       == PRODUCTION_PARAMS["vol_ratio_min"]
        and p.require_ema50_above_200 == PRODUCTION_PARAMS["require_ema50_above_200"]
        and p.ema50_dist_min_pct  == PRODUCTION_PARAMS["ema50_dist_min_pct"]
        and p.apply_regime_veto   == PRODUCTION_PARAMS["apply_regime_veto"]
    )
