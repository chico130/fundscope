"""Sentimento de retalho do Stocktwits — zero tokens.

Usa o endpoint público ``/streams/symbol/{ticker}.json`` (sem auth). Cada
mensagem traz uma classificação Bullish/Bearish opcional, feita pelo próprio
autor; agregamos contagens e convertemos o ratio para escala ``[-10, +10]``.

A API pública é rate-limited (200 req/hora por IP, sem aviso prévio), por isso
o caller deve respeitar ``REQUEST_DELAY_S`` entre chamadas.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger("crawler.stocktwits")

API_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
REQUEST_TIMEOUT_S = 10
REQUEST_DELAY_S = 1.0  # respeitar rate limit público (~200 req/h)
USER_AGENT = "fundscope-crawler/0.1"


def _score_from_counts(bullish: int, bearish: int) -> float:
    """Converte contagens Bullish/Bearish em score ``[-10.0, +10.0]``.

    Ignora mensagens sem classificação (``total`` aqui = só classificadas).
    """
    total = bullish + bearish
    if total == 0:
        return 0.0
    return round((bullish - bearish) / total * 10.0, 3)


def _fetch_one(ticker: str) -> dict | None:
    """Faz uma chamada à API e devolve agregado ou ``None`` em caso de falha."""
    try:
        resp = requests.get(
            API_URL.format(ticker=ticker),
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning("stocktwits HTTP falhou para %s: %s", ticker, exc)
        return None

    if resp.status_code == 429:
        logger.warning("stocktwits rate-limit (429) em %s — a saltar", ticker)
        return None
    if resp.status_code != 200:
        logger.warning("stocktwits HTTP %s em %s", resp.status_code, ticker)
        return None

    try:
        payload = resp.json()
    except ValueError as exc:
        logger.warning("stocktwits JSON inválido em %s: %s", ticker, exc)
        return None

    messages = payload.get("messages") or []
    bullish = 0
    bearish = 0
    for msg in messages:
        sentiment = (msg.get("entities") or {}).get("sentiment") or {}
        basic = sentiment.get("basic")
        if basic == "Bullish":
            bullish += 1
        elif basic == "Bearish":
            bearish += 1

    total = bullish + bearish
    return {
        "bullish": bullish,
        "bearish": bearish,
        "total": total,
        "score": _score_from_counts(bullish, bearish),
    }


def fetch_stocktwits_sentiment(tickers: list[str]) -> dict[str, dict | None]:
    """Para cada ticker devolve ``{bullish, bearish, total, score}`` ou ``None``.

    ``score`` está em ``[-10.0, +10.0]`` para alimentar a combinação ponderada
    com o sinal do Reddit no runner. Sleep de ``REQUEST_DELAY_S`` entre tickers
    para respeitar o rate limit público.
    """
    out: dict[str, dict | None] = {}
    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(REQUEST_DELAY_S)
        out[ticker] = _fetch_one(ticker)
    return out
