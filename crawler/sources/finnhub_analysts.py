"""Consenso de analistas via Finnhub — endpoint /stock/recommendation.

Reutiliza a env var ``FINNHUB_API_KEY`` (a mesma que bot/config.py lê) e o
padrão HTTP de bot/price_feed.py (raw requests ao finnhub.io/api/v1).
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("crawler.finnhub")

_BASE = "https://finnhub.io/api/v1"

# Peso mínimo de cada campo (touro/urso) para considerar divergência genuína.
DIVERGENCE_CAMP_RATIO = 0.20


def fetch_analyst_consensus(tickers: list[str], timeout: float = 10.0) -> dict[str, dict | None]:
    """Para cada ticker, devolve o consenso de analistas mais recente.

    Estrutura por ticker::

        {"bull_ratio": float[-1,1], "n_analysts": int, "period": str, "divergence": bool}

    Devolve ``None`` para tickers sem dados. Falhas individuais não abortam o lote.
    """
    # bot/config.py usa FINNHUB_API_KEY; o .env do VPS usa FINNHUB_TOKEN. Aceitar ambos.
    key = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_TOKEN") or ""
    if not key:
        logger.warning("FINNHUB_API_KEY/FINNHUB_TOKEN ausente — a saltar fonte de analistas")
        return {t: None for t in tickers}

    out: dict[str, dict | None] = {}
    for ticker in tickers:
        try:
            resp = requests.get(
                f"{_BASE}/stock/recommendation",
                params={"symbol": ticker, "token": key},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            out[ticker] = _parse_recommendation(data)
        except Exception as exc:  # noqa: BLE001 — robustez por ticker
            logger.warning("finnhub falhou para %s: %s", ticker, exc)
            out[ticker] = None
    return out


def _parse_recommendation(data: list[dict]) -> dict | None:
    """Converte a resposta do Finnhub no nosso formato compacto."""
    if not data:
        return None
    latest = data[0]  # Finnhub devolve ordenado do período mais recente para o mais antigo
    strong_buy = latest.get("strongBuy", 0)
    buy = latest.get("buy", 0)
    hold = latest.get("hold", 0)
    sell = latest.get("sell", 0)
    strong_sell = latest.get("strongSell", 0)

    total = strong_buy + buy + hold + sell + strong_sell
    if total == 0:
        return None

    bull_camp = strong_buy + buy
    bear_camp = sell + strong_sell
    bull = bull_camp - bear_camp
    # Divergência GENUÍNA: ambos os campos têm peso material (≥20% cada).
    # Evita falsos positivos em large-caps onde 1 urso entre 50+ analistas é normal.
    divergence = (bull_camp / total) >= DIVERGENCE_CAMP_RATIO and (
        bear_camp / total
    ) >= DIVERGENCE_CAMP_RATIO
    return {
        "bull_ratio": round(bull / total, 3),          # [-1, +1]
        "n_analysts": total,
        "period": latest.get("period", ""),
        "divergence": divergence,
    }
