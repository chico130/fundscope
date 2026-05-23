"""Orquestrador do Social Crawler — entrypoint do cron.

Fluxo: carregar watchlist -> buscar fontes -> agregar -> aplicar vetos ->
escrever data/beta/social_sentiment.json. Tudo determinístico, zero tokens.

Uso::

    python -m crawler.runner
    python -m crawler.runner --limit 10   # só os primeiros 10 tickers (debug)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib

from dotenv import load_dotenv

from .sources.finnhub_analysts import fetch_analyst_consensus
from .sources.reddit_praw import fetch_reddit_sentiment
from .sources.stocktwits import fetch_stocktwits_sentiment
from .sources.twitter_stub import fetch_twitter_sentiment
from .writer import REPO_ROOT, write_sentiment

logger = logging.getLogger("crawler.runner")

WATCHLIST_PATH = REPO_ROOT / "data" / "beta" / "watchlist.json"
LOG_DIR = REPO_ROOT / "logs"

SCHEMA_VERSION = 2  # adicionado bloco stocktwits + combined_score
TTL_MINUTES = 240  # 4h — alinhado com a cadência do timer systemd

# Veto de divergência de analistas: exige consenso significativo.
ANALYST_DIVERGENCE_MIN = 20

# Pesos da média ponderada do sinal social (somam 1.0).
WEIGHT_REDDIT = 0.4
WEIGHT_STOCKTWITS = 0.6


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "crawler.log", encoding="utf-8"),
        ],
    )


def load_watchlist(limit: int | None = None) -> list[str]:
    """Extrai os tickers de data/beta/watchlist.json (chave ``candidates``)."""
    data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    tickers = [c["ticker"] for c in data.get("candidates", []) if c.get("ticker")]
    if limit:
        tickers = tickers[:limit]
    return tickers


def _decide_veto(analyst: dict | None, reddit: dict | None) -> str | None:
    """Regra de veto consumida pela Bonnie. Primeiro match ganha."""
    if reddit and reddit.get("panic"):
        return "social_panic"
    if (
        analyst
        and analyst.get("divergence")
        and analyst.get("n_analysts", 0) >= ANALYST_DIVERGENCE_MIN
    ):
        return "analyst_divergence"
    return None


def _combined_score(reddit: dict | None, stocktwits: dict | None) -> float | None:
    """Média ponderada Reddit (×10) + Stocktwits no espaço ``[-10, +10]``.

    Reddit ``mean`` vem em ``[-1, +1]`` (VADER compound), por isso é escalado ×10.
    Stocktwits ``score`` já vem em ``[-10, +10]``. Se uma das fontes não tiver
    amostras (``n`` ou ``total`` == 0) é descartada e o peso da outra é
    promovido a 1.0. Devolve ``None`` se nenhuma das duas tem amostras.
    """
    r_ok = bool(reddit) and reddit.get("n", 0) > 0
    s_ok = bool(stocktwits) and stocktwits.get("total", 0) > 0

    if r_ok and s_ok:
        r_score = reddit["mean"] * 10.0
        s_score = stocktwits["score"]
        return round(WEIGHT_REDDIT * r_score + WEIGHT_STOCKTWITS * s_score, 3)
    if r_ok:
        return round(reddit["mean"] * 10.0, 3)
    if s_ok:
        return round(stocktwits["score"], 3)
    return None


def build_payload(tickers: list[str]) -> dict:
    """Corre todas as fontes e monta o payload final."""
    logger.info("A processar %d tickers", len(tickers))
    analysts = fetch_analyst_consensus(tickers)
    reddit = fetch_reddit_sentiment(tickers)
    stocktwits = fetch_stocktwits_sentiment(tickers)
    twitter = fetch_twitter_sentiment(tickers)

    out_tickers: dict[str, dict] = {}
    veto_count = 0
    for t in tickers:
        a = analysts.get(t)
        r = reddit.get(t)
        s = stocktwits.get(t)
        veto = _decide_veto(a, r)
        if veto:
            veto_count += 1
        out_tickers[t] = {
            "analyst": a,
            "reddit": r,
            "stocktwits": s,
            "twitter": twitter.get(t),
            "combined_score": _combined_score(r, s),
            "veto": veto,
        }

    logger.info("Concluído: %d tickers, %d vetos", len(tickers), veto_count)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_minutes": TTL_MINUTES,
        "tickers": out_tickers,
        "anomalies": [],  # preenchido pela Fase 2 (supervisor Claude)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="FundScope Social Crawler")
    parser.add_argument("--limit", type=int, default=None, help="limitar nº de tickers (debug)")
    args = parser.parse_args()

    _setup_logging()
    load_dotenv(REPO_ROOT / ".env")  # mesmas credenciais que o bot

    tickers = load_watchlist(limit=args.limit)
    payload = build_payload(tickers)
    path = write_sentiment(payload)
    logger.info("Escrito %s", path)


if __name__ == "__main__":
    main()
