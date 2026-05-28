"""
bot/model_trainer.py — Treino do modelo de IA da Bonnie.

Usa GradientBoostingClassifier (scikit-learn) com hiperparâmetros
conservadores (max_depth=2) adequados ao tamanho reduzido do dataset.

Modelo guardado em: data/models/bonnie_model.pkl
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import joblib
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score

from .config import BASE_DIR
from .feature_builder import FEATURE_COLS, load_and_build

MODEL_PATH = BASE_DIR / "data" / "models" / "bonnie_model.pkl"


def train(verbose: bool = True) -> "GradientBoostingClassifier":
    """
    Treina o modelo Bonnie e guarda-o em disco.
    Retorna o modelo treinado.
    """
    X, y, observations = load_and_build()

    n_total   = len(y)
    n_wins    = int(y.sum())
    n_losses  = n_total - n_wins

    if verbose:
        print(f"\n  Dataset:   {n_total} observações  |  {n_wins} vitórias  |  {n_losses} derrotas")
        print(f"  Features:  {FEATURE_COLS}")

    # GradientBoosting com parâmetros conservadores para dataset pequeno
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=2,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )

    # Validação cruzada estratificada (mantém proporção vitórias/derrotas)
    n_splits = min(5, n_losses)  # no máximo tantos folds quantas derrotas
    if n_splits >= 2:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_scores = cross_val_score(model, X, y, cv=cv, scoring="accuracy")
        if verbose:
            print(f"  CV ({n_splits}-fold): {cv_scores.mean():.1%} ± {cv_scores.std():.1%}  "
                  f"(scores: {[f'{s:.0%}' for s in cv_scores]})")
    else:
        if verbose:
            print("  CV: dataset demasiado pequeno para validação cruzada fiável.")

    # Treino no dataset completo
    model.fit(X, y)
    train_acc = model.score(X, y)
    if verbose:
        print(f"  Acurácia treino (in-sample): {train_acc:.1%}")

    # Importância das features
    if verbose:
        importances = dict(zip(FEATURE_COLS, model.feature_importances_))
        sorted_imp  = sorted(importances.items(), key=lambda x: -x[1])
        print("  Importância das features:")
        for feat, imp in sorted_imp:
            bar = "█" * int(imp * 30)
            print(f"    {feat:<20} {imp:.3f}  {bar}")

    # Guardar modelo
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    if verbose:
        print(f"\n  Modelo guardado em: {MODEL_PATH.relative_to(BASE_DIR)}")

    return model


if __name__ == "__main__":
    print("=" * 55)
    print("  Bonnie — Treino do Modelo")
    print("=" * 55)
    train(verbose=True)
    print("=" * 55)
