"""Stub do Twitter/X — fora do MVP.

A API do X em 2026 é cara/instável; Reddit + Finnhub dão sinal suficiente.
Mantemos a assinatura estável para a Fase 2 poder substituir sem mexer no runner.
"""

from __future__ import annotations


def fetch_twitter_sentiment(tickers: list[str]) -> dict[str, None]:
    """Devolve sempre ``None`` por ticker (sem dados). Não faz I/O."""
    return {ticker: None for ticker in tickers}
