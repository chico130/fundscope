"""
bot/feature_builder.py — Construção da matriz de features para a Bonnie.

Lê bonnie_observations.json e converte para (X, y) prontos para ML:
  - Features numéricas: rsi_14, ema50_above_200, vol_ratio, regime (ordinal)
  - Target y: 1 se outcome["success"] == true, 0 se false
"""
from __future__ import annotations

import json

import pandas as pd

from .config import BASE_DIR

OBSERVATIONS_PATH = BASE_DIR / "data" / "backtest" / "bonnie_observations.json"

# Codificação ordinal do regime: maior valor = condições mais favoráveis
REGIME_ENCODING: dict[str, int] = {
    "bull_trending":    3,
    "bull_lateral":     2,
    "bear_correction":  1,
    "bear_capitulation": 0,
    "unknown":         -1,
}

FEATURE_COLS = ["rsi_14", "ema50_above_200", "vol_ratio", "regime"]


def build_feature_matrix(
    observations: list[dict],
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Converte lista de observações em (X, y).

    X — DataFrame com colunas: rsi_14, ema50_above_200, vol_ratio, regime
    y — Series binária: 1 = sucesso, 0 = derrota
    """
    rows: list[dict] = []
    targets: list[int] = []

    for obs in observations:
        f = obs.get("features", {})
        rows.append({
            "rsi_14":          float(f.get("rsi_14") or 50.0),
            "ema50_above_200": 1 if f.get("ema50_above_200") else 0,
            "vol_ratio":       float(f.get("vol_ratio") or 1.0),
            "regime":          REGIME_ENCODING.get(f.get("regime", "unknown"), -1),
        })
        targets.append(1 if obs["outcome"].get("success") else 0)

    X = pd.DataFrame(rows, columns=FEATURE_COLS)
    y = pd.Series(targets, name="success", dtype=int)
    return X, y


def load_and_build() -> tuple[pd.DataFrame, pd.Series, list[dict]]:
    """
    Carrega bonnie_observations.json e devolve (X, y, observações_raw).
    Lança FileNotFoundError ou ValueError se os dados estiverem ausentes.
    """
    try:
        observations = json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Ficheiro não encontrado: {OBSERVATIONS_PATH}\n"
            "Corre primeiro: python -m bot.mass_backtest"
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Erro ao ler JSON: {exc}")

    if not observations:
        raise ValueError(
            "bonnie_observations.json está vazio. "
            "Corre python -m bot.mass_backtest para gerar observações."
        )

    X, y = build_feature_matrix(observations)
    return X, y, observations
