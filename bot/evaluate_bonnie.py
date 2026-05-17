"""
bot/evaluate_bonnie.py — Avaliação Comparativa: Clyde Puro vs Bonnie Filtrada.

Avaliação Out-of-Sample honesta:
  • Ordena todas as observações por data (mais antigo → mais recente).
  • Treina o modelo nos primeiros 80% (passado remoto — modelo nunca vê o futuro).
  • Testa nos 20% restantes (passado mais recente, nunca visto durante o treino).

CLI:
    python -m bot.evaluate_bonnie
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score

from .feature_builder import load_and_build, FEATURE_COLS, REGIME_ENCODING, build_feature_matrix
from .model_trainer import MODEL_PATH

BONNIE_THRESHOLD = 0.60  # Rejeita trade se P(sucesso) < 60%
TRAIN_RATIO      = 0.80  # 80% treino / 20% teste

_SEP  = "=" * 60
_SEP2 = "-" * 60


def _train_oos_model(
    observations_train: list[dict],
    verbose: bool = True,
) -> GradientBoostingClassifier:
    """Treina o modelo APENAS nas observações de treino (primeiros 80%)."""
    X_train, y_train = build_feature_matrix(observations_train)

    n_total  = len(y_train)
    n_wins   = int(y_train.sum())
    n_losses = n_total - n_wins

    if verbose:
        print(f"  Dataset treino: {n_total} obs  |  {n_wins} vitorias  |  {n_losses} derrotas")

    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=2,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )

    n_splits = min(5, n_losses)
    if n_splits >= 2:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_scores = cross_val_score(model, X_train, y_train, cv=cv, scoring="accuracy")
        if verbose:
            print(f"  CV ({n_splits}-fold) no treino: {cv_scores.mean():.1%} +/- {cv_scores.std():.1%}")
    else:
        if verbose:
            print("  CV: dataset de treino demasiado pequeno.")

    model.fit(X_train, y_train)

    if verbose:
        importances = dict(zip(FEATURE_COLS, model.feature_importances_))
        sorted_imp  = sorted(importances.items(), key=lambda x: -x[1])
        print("  Importancia das features:")
        for feat, imp in sorted_imp:
            bar = ">" * int(imp * 30)
            print(f"    {feat:<20} {imp:.3f}  {bar}")

    return model


def evaluate() -> None:
    # Carregar dados completos
    _, _, observations = load_and_build()

    # Ordenar por data (mais antigo primeiro)
    observations_sorted = sorted(observations, key=lambda o: o["date_observed"])

    n_total    = len(observations_sorted)
    split_idx  = int(n_total * TRAIN_RATIO)
    obs_train  = observations_sorted[:split_idx]
    obs_test   = observations_sorted[split_idx:]

    date_train_start = obs_train[0]["date_observed"]
    date_train_end   = obs_train[-1]["date_observed"]
    date_test_start  = obs_test[0]["date_observed"]
    date_test_end    = obs_test[-1]["date_observed"]

    print(f"\n{_SEP}")
    print("  AVALIACAO OUT-OF-SAMPLE — BONNIE vs CLYDE")
    print(_SEP)
    print(f"  Total de observacoes:    {n_total}")
    print(f"  Treino (80%):            {len(obs_train)} obs  [{date_train_start} -> {date_train_end}]")
    print(f"  Teste  (20%):            {len(obs_test)} obs  [{date_test_start} -> {date_test_end}]")
    print(f"  Threshold Bonnie:        P >= {BONNIE_THRESHOLD:.0%}")
    print(_SEP)

    # Treinar APENAS no conjunto de treino
    print("\n[1/2] Treino do modelo (dados de treino)...")
    model = _train_oos_model(obs_train, verbose=True)

    # Avaliar APENAS no conjunto de teste (dados nunca vistos)
    print(f"\n[2/2] Avaliacao no conjunto de TESTE (dados nunca vistos)...")
    X_test, y_test = build_feature_matrix(obs_test)
    proba_success  = model.predict_proba(X_test)[:, 1]

    # ---------------------------------------------------------------
    # Baseline: Clyde Puro no conjunto de teste
    # ---------------------------------------------------------------
    n_test       = len(y_test)
    n_wins_base  = int(y_test.sum())
    n_losses_base = n_test - n_wins_base
    wr_base      = n_wins_base / n_test if n_test > 0 else 0.0

    # ---------------------------------------------------------------
    # Com Filtro da Bonnie no conjunto de teste
    # ---------------------------------------------------------------
    approved        = proba_success >= BONNIE_THRESHOLD
    n_blocked       = int((~approved).sum())
    n_executed      = int(approved.sum())

    y_exec          = y_test[approved].reset_index(drop=True)
    n_wins_bonnie   = int(y_exec.sum())
    n_losses_bonnie = n_executed - n_wins_bonnie
    wr_bonnie       = n_wins_bonnie / n_executed if n_executed > 0 else 0.0
    improvement     = wr_bonnie - wr_base

    # ---------------------------------------------------------------
    # Relatorio
    # ---------------------------------------------------------------
    print(f"\n{_SEP}")
    print("  RESULTADOS NO GRUPO DE TESTE ISOLADO (dados nao vistos)")
    print(_SEP)

    print(f"\n  BASELINE (Clyde Puro — grupo de teste):")
    print(f"  {'Total de trades:':<26} {n_test}")
    print(f"  {'Vitorias:':<26} {n_wins_base}")
    print(f"  {'Derrotas:':<26} {n_losses_base}")
    print(f"  {'Win Rate:':<26} {wr_base:.1%}")

    print(f"\n{_SEP2}")
    print(f"  COM FILTRO DA BONNIE  (threshold: P >= {BONNIE_THRESHOLD:.0%})")
    print(_SEP2)
    print(f"  {'Trades bloqueados:':<26} {n_blocked}")
    print(f"  {'Trades executados:':<26} {n_executed}")
    print(f"  {'Vitorias:':<26} {n_wins_bonnie}")
    print(f"  {'Derrotas:':<26} {n_losses_bonnie}")
    print(f"  {'Win Rate filtrada (OOS):':<26} {wr_bonnie:.1%}")
    print(f"  {'Melhoria:':<26} {improvement:+.1%}")

    # Detalhe dos trades bloqueados
    print(f"\n{_SEP2}")
    print("  Trades bloqueados pela Bonnie (grupo de teste):")
    blocked_wins   = 0
    blocked_losses = 0
    for i, (ok, p) in enumerate(zip(approved, proba_success)):
        if not ok:
            obs    = obs_test[i]
            result = "DERROTA evitada" if not y_test.iloc[i] else "VITORIA bloqueada (falso negativo)"
            print(f"    {obs['ticker']:<6} {obs['date_observed']}  P={p:.2f}  [{result}]")
            if not y_test.iloc[i]:
                blocked_losses += 1
            else:
                blocked_wins += 1

    if n_blocked == 0:
        print("    (nenhum trade bloqueado)")

    print(f"\n  Derrotas evitadas (OOS):   {blocked_losses} / {n_losses_base}")
    print(f"  Vitorias perdidas (OOS):   {blocked_wins} / {n_wins_base}")
    print(_SEP)

    # Veredicto
    if n_executed == 0:
        print("\n  Bonnie bloqueou todos os trades — threshold demasiado alto?")
    elif wr_bonnie > wr_base:
        print(f"\n  A Bonnie MELHOROU o desempenho do Clyde (OOS honesto)!")
        print(f"  {wr_base:.1%} -> {wr_bonnie:.1%}  ({improvement:+.1%})")
    elif wr_bonnie == wr_base:
        print(f"\n  A Bonnie nao alterou o desempenho (win rate igual).")
    else:
        print(f"\n  A Bonnie piorou o desempenho — modelo precisa de mais dados.")
    print(_SEP)


if __name__ == "__main__":
    evaluate()
