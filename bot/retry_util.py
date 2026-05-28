"""Backoff exponencial partilhado para retries de chamadas de rede idempotentes.

Usado por api_client (T212, yfinance) e price_feed (Finnhub). Centraliza a
fórmula para que todas as chamadas externas tenham a mesma política de espera.
"""
from __future__ import annotations


def backoff_delay(
    attempt: int,
    base: float = 5.0,
    factor: float = 2.0,
    cap: float = 60.0,
) -> float:
    """Atraso (segundos) para a tentativa `attempt` (0-indexed).

    attempt=0 → base; attempt=1 → base·factor; attempt=2 → base·factor² …
    Limitado a `cap`. Determinístico (sem jitter) para ser fácil de raciocinar
    e de testar.
    """
    if attempt < 0:
        attempt = 0
    return min(cap, base * (factor ** attempt))
