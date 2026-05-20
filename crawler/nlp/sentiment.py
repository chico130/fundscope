"""Sentimento local via vaderSentiment — custo zero de tokens.

VADER é rule-based (léxico + heurísticas), corre na CPU em microssegundos e é
adequado para texto curto e informal (títulos de posts, comentários). Devolve
um score ``compound`` normalizado em [-1.0, +1.0].
"""

from __future__ import annotations

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Analisador é stateless e thread-safe após init — instanciar uma vez.
_ANALYZER = SentimentIntensityAnalyzer()

# Limiares de veto (consumidos pelo runner ao montar o JSON).
PANIC_MEAN_THRESHOLD = -0.5   # média compound abaixo disto = pânico
PANIC_MIN_SAMPLES = 10        # exige volume mínimo para evitar falsos positivos


def score_text(text: str) -> float:
    """Score compound de um texto em [-1.0, +1.0]. Custo: zero tokens."""
    if not text or not text.strip():
        return 0.0
    return _ANALYZER.polarity_scores(text)["compound"]


def aggregate(scores: list[float]) -> dict:
    """Agrega uma lista de scores compound num resumo numérico.

    Retorna ``mean`` (média), ``n`` (nº de amostras) e ``panic`` (bool de veto).
    """
    n = len(scores)
    if n == 0:
        return {"mean": 0.0, "n": 0, "panic": False}
    mean = sum(scores) / n
    return {
        "mean": round(mean, 3),
        "n": n,
        "panic": mean < PANIC_MEAN_THRESHOLD and n >= PANIC_MIN_SAMPLES,
    }
