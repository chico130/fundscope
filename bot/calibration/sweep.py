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
from dataclasses import dataclass, asdict

import pandas as pd

from bot.calibration.metrics import compute_metrics_full
from bot.calibration.regime  import BEAR_REGIMES

# Parâmetros de produção (backtest.py:RSI_BUY_MAX + _clyde_signal)
# Actualizado em 2026-05-21: sweep completo S&P 500 (2022-2026, n=503 tickers).
# Candidato baseline: RSI<=34 + vol>=1.2 + ema50dist>=-3% → PF=2.16, n=158.
# Os ficheiros de produção (backtest.py, phase0.py, etc.) NÃO foram alterados —
# estes parâmetros reflectem apenas o resultado do calibrador offline.
PRODUCTION_PARAMS = {
    "rsi_buy_max":             34.0,
    "vol_ratio_min":           1.2,
    "require_ema50_above_200": True,
    "ema50_dist_min_pct":      -3.0,
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


# ---------------------------------------------------------------------------
# Protocolo de Validação Out-of-Sample (OOS)
# ---------------------------------------------------------------------------

def run_sweep_oos(
    cand: pd.DataFrame,
    horizons: list[int],
    train_end: str = "2024-12-31",
    val_start: str = "2025-01-01",
    grid: dict[str, list] | None = None,
    n_min: int = 30,
    n_folds: int = 1,
    pf_drop_threshold: float = 0.40,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Protocolo de validação Out-of-Sample (OOS).

    Parâmetros
    ----------
    cand : pd.DataFrame
        Tabela mestra de candidatos (deve conter coluna 'date').
    horizons : list[int]
        Horizontes em dias a avaliar.
    train_end : str
        Data de fim do período de treino (formato YYYY-MM-DD). Usado só com n_folds=1.
    val_start : str
        Data de início da validação (formato YYYY-MM-DD). Usado só com n_folds=1.
    grid : dict | None
        Grelha de parâmetros; None usa DEFAULT_GRID.
    n_min : int
        Mínimo de trades para considerar amostra suficiente.
    n_folds : int
        1 = divisão simples treino/validação.
        >1 = walk-forward com n_folds folds (o histórico é dividido em n_folds+1 blocos).
    pf_drop_threshold : float
        Queda máxima de PF aceitável (0.40 = 40%).

    Devolve
    -------
    sweep_train : pd.DataFrame  — sweep completo no período de treino
    sweep_val   : pd.DataFrame  — métricas de validação do melhor ParamSet por horizonte
    oos_report  : pd.DataFrame  — tabela OOS resumo com status e pontuação de robustez
    """
    if "date" not in cand.columns:
        raise ValueError(
            "cand não tem coluna 'date'. "
            "Garantir que build_candidate_table inclui a data antes de chamar run_sweep_oos."
        )
    if grid is None:
        grid = DEFAULT_GRID

    if n_folds <= 1:
        return _run_oos_simple(cand, horizons, train_end, val_start, grid, n_min, pf_drop_threshold)
    return _run_oos_walkforward(cand, horizons, grid, n_min, n_folds, pf_drop_threshold)


# ---------------------------------------------------------------------------
# Implementações internas OOS
# ---------------------------------------------------------------------------

def _run_oos_simple(
    cand: pd.DataFrame,
    horizons: list[int],
    train_end: str,
    val_start: str,
    grid: dict[str, list],
    n_min: int,
    pf_drop_threshold: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Divisão simples treino/validação."""
    dates = pd.to_datetime(cand["date"])
    cand_train = cand[dates <= pd.Timestamp(train_end)].copy()
    cand_val   = cand[dates >= pd.Timestamp(val_start)].copy()

    print(f"[oos] Treino: até {train_end} ({len(cand_train):,} linhas)")
    print(f"[oos] Validação: a partir de {val_start} ({len(cand_val):,} linhas)")

    print("[oos] A correr sweep no conjunto de treino...")
    sweep_train = run_sweep(cand_train, horizons, grid=grid, n_min=n_min)

    best_by_h = _find_best_per_horizon(sweep_train, horizons)
    val_rows, oos_rows = _eval_oos_rows(best_by_h, cand_val, horizons, n_min, pf_drop_threshold)

    return sweep_train, pd.DataFrame(val_rows), pd.DataFrame(oos_rows)


def _run_oos_walkforward(
    cand: pd.DataFrame,
    horizons: list[int],
    grid: dict[str, list],
    n_min: int,
    n_folds: int,
    pf_drop_threshold: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Walk-forward k-fold OOS: o histórico é dividido em n_folds+1 blocos."""
    blocks = _split_date_blocks(cand, n_folds + 1)
    print(f"[oos] Walk-forward com {n_folds} folds ({n_folds + 1} blocos de datas)")
    for i, (bs, be) in enumerate(blocks):
        print(f"  Bloco {i}: {bs} → {be}")

    dates = pd.to_datetime(cand["date"])
    fold_val_metrics: dict[int, list[dict]] = {H: [] for H in horizons}
    fold_best_params: dict[int, list[dict]] = {H: [] for H in horizons}

    for k in range(1, n_folds + 1):
        train_end_k          = blocks[k - 1][1]
        val_start_k, val_end_k = blocks[k]

        cand_train_k = cand[dates <= pd.Timestamp(train_end_k)].copy()
        cand_val_k   = cand[
            (dates >= pd.Timestamp(val_start_k)) & (dates <= pd.Timestamp(val_end_k))
        ].copy()

        print(f"\n[oos] Fold {k}/{n_folds}: treino até {train_end_k}, validação {val_start_k}→{val_end_k}")
        sweep_k      = run_sweep(cand_train_k, horizons, grid=grid, n_min=n_min)
        best_by_h_k  = _find_best_per_horizon(sweep_k, horizons)

        for H in horizons:
            if H not in best_by_h_k:
                continue
            best_k  = best_by_h_k[H]
            val_m_k = _eval_params_on_cand(cand_val_k, best_k, H, n_min)
            fold_val_metrics[H].append(val_m_k)
            fold_best_params[H].append(best_k)

    # Sweep final no conjunto de treino completo (todos os blocos excepto o último)
    last_train_end   = blocks[n_folds - 1][1]
    cand_train_final = cand[dates <= pd.Timestamp(last_train_end)].copy()
    print(f"\n[oos] Sweep final no conjunto de treino completo (até {last_train_end})...")
    sweep_train = run_sweep(cand_train_final, horizons, grid=grid, n_min=n_min)
    best_final  = _find_best_per_horizon(sweep_train, horizons)

    val_rows: list[dict] = []
    oos_rows: list[dict] = []

    for H in horizons:
        if not fold_val_metrics[H]:
            continue

        avg_val      = pd.DataFrame(fold_val_metrics[H]).mean(numeric_only=True).to_dict()
        n_val_trades = int(round(avg_val.get("n_trades", 0)))

        best = best_final.get(H) or (fold_best_params[H][-1] if fold_best_params[H] else None)
        if not best:
            continue

        train_pf = best.get("profit_factor", float("nan"))
        val_pf   = avg_val.get("profit_factor", float("nan"))

        status, pf_drop_pct = _compute_status(train_pf, val_pf, n_val_trades, n_min, pf_drop_threshold)
        robustness          = _robustness_score(pf_drop_pct, n_val_trades, n_min)

        val_rows.append({
            **asdict(ParamSet(
                rsi_buy_max=best["rsi_buy_max"],
                vol_ratio_min=best["vol_ratio_min"],
                require_ema50_above_200=bool(best["require_ema50_above_200"]),
                ema50_dist_min_pct=best.get("ema50_dist_min_pct"),
                apply_regime_veto=bool(best["apply_regime_veto"]),
                horizon=H,
            )),
            **avg_val,
        })
        oos_rows.append(_make_oos_row(H, best, train_pf, val_pf, avg_val,
                                      n_val_trades, pf_drop_pct, robustness, status))

    return sweep_train, pd.DataFrame(val_rows), pd.DataFrame(oos_rows)


# ---------------------------------------------------------------------------
# Funções auxiliares OOS
# ---------------------------------------------------------------------------

def _find_best_per_horizon(sweep: pd.DataFrame, horizons: list[int]) -> dict[int, dict]:
    """Para cada horizonte, encontra o melhor ParamSet (PF máximo, low_sample=False)."""
    best: dict[int, dict] = {}
    for H in horizons:
        sub = sweep[
            (sweep["horizon"] == H) & ~sweep["low_sample"].fillna(True)
        ].dropna(subset=["profit_factor"])
        if sub.empty:
            sub = sweep[sweep["horizon"] == H].dropna(subset=["profit_factor"])
        if sub.empty:
            continue
        best[H] = sub.loc[sub["profit_factor"].idxmax()].to_dict()
    return best


def _eval_params_on_cand(
    cand: pd.DataFrame,
    params: dict,
    H: int,
    n_min: int,
) -> dict:
    """Avalia um ParamSet específico sobre um subset de cand sem reoptimização."""
    p = ParamSet(
        rsi_buy_max=params["rsi_buy_max"],
        vol_ratio_min=params["vol_ratio_min"],
        require_ema50_above_200=bool(params["require_ema50_above_200"]),
        ema50_dist_min_pct=params.get("ema50_dist_min_pct"),
        apply_regime_veto=bool(params["apply_regime_veto"]),
        horizon=H,
    )
    mask = _build_mask(cand, p, H)
    return compute_metrics_full(cand, mask, H, n_min)


def _compute_status(
    train_pf: float,
    val_pf: float,
    n_val_trades: int,
    n_min: int,
    threshold: float,
) -> tuple[str, float]:
    """Calcula o status OOS e a queda de PF percentual."""
    if n_val_trades < n_min or pd.isna(train_pf) or train_pf <= 0:
        return "⚠️ DADOS INSUFICIENTES", float("nan")
    pf_drop = (train_pf - val_pf) / train_pf
    if pf_drop <= threshold:
        return "✅ VÁLIDO", pf_drop
    return "🚨 OVERFITTED", pf_drop


def _robustness_score(pf_drop_pct: float, n_val_trades: int, n_min: int) -> float:
    """Pontuação de Robustez: 100 × (1 − pf_drop_pct) × min(1, n_val_trades/n_min)."""
    if pd.isna(pf_drop_pct):
        return 0.0
    return max(0.0, 100.0 * (1.0 - pf_drop_pct) * min(1.0, n_val_trades / n_min))


def _make_oos_row(
    H: int,
    best: dict,
    train_pf: float,
    val_pf: float,
    val_m: dict,
    n_val_trades: int,
    pf_drop_pct: float,
    robustness: float,
    status: str,
) -> dict:
    return {
        "horizon":              H,
        "rsi_buy_max":          best["rsi_buy_max"],
        "vol_ratio_min":        best["vol_ratio_min"],
        "train_pf":             train_pf,
        "train_expectancy_pct": best.get("expectancy_pct", float("nan")),
        "train_maxdd_pct":      best.get("max_drawdown_pct", float("nan")),
        "val_pf":               val_pf,
        "val_expectancy_pct":   val_m.get("expectancy_pct", float("nan")),
        "val_maxdd_pct":        val_m.get("max_drawdown_pct", float("nan")),
        "n_val_trades":         n_val_trades,
        "pf_drop_pct":          pf_drop_pct,
        "robustness_score":     robustness,
        "status":               status,
    }


def _eval_oos_rows(
    best_by_h: dict[int, dict],
    cand_val: pd.DataFrame,
    horizons: list[int],
    n_min: int,
    pf_drop_threshold: float,
) -> tuple[list[dict], list[dict]]:
    """Avalia os melhores params de treino na validação; devolve (val_rows, oos_rows)."""
    val_rows: list[dict] = []
    oos_rows: list[dict] = []

    for H in horizons:
        if H not in best_by_h:
            continue
        best         = best_by_h[H]
        val_m        = _eval_params_on_cand(cand_val, best, H, n_min)
        train_pf     = best.get("profit_factor", float("nan"))
        val_pf       = val_m.get("profit_factor", float("nan"))
        n_val_trades = int(val_m.get("n_trades", 0))

        status, pf_drop_pct = _compute_status(train_pf, val_pf, n_val_trades, n_min, pf_drop_threshold)
        robustness          = _robustness_score(pf_drop_pct, n_val_trades, n_min)

        val_rows.append({
            **asdict(ParamSet(
                rsi_buy_max=best["rsi_buy_max"],
                vol_ratio_min=best["vol_ratio_min"],
                require_ema50_above_200=bool(best["require_ema50_above_200"]),
                ema50_dist_min_pct=best.get("ema50_dist_min_pct"),
                apply_regime_veto=bool(best["apply_regime_veto"]),
                horizon=H,
            )),
            **val_m,
        })
        oos_rows.append(_make_oos_row(H, best, train_pf, val_pf, val_m,
                                      n_val_trades, pf_drop_pct, robustness, status))

    return val_rows, oos_rows


def _split_date_blocks(cand: pd.DataFrame, n_blocks: int) -> list[tuple[str, str]]:
    """Divide o intervalo de datas de cand em n_blocks blocos aproximadamente iguais."""
    dates = pd.DatetimeIndex(
        sorted(pd.to_datetime(cand["date"]).dt.normalize().unique())
    )
    size = max(1, len(dates) // n_blocks)

    blocks: list[tuple[str, str]] = []
    for i in range(n_blocks):
        start_i = i * size
        end_i   = min((i + 1) * size - 1 if i < n_blocks - 1 else len(dates) - 1, len(dates) - 1)
        blocks.append((
            dates[start_i].strftime("%Y-%m-%d"),
            dates[end_i].strftime("%Y-%m-%d"),
        ))
    return blocks
