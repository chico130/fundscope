"""Sentimento de retalho do Reddit via praw — zero tokens.

Pesquisa menções de cada ticker em subreddits financeiros, pontua o texto
localmente com VADER e agrega. Tickers curtos (S, D, V, AR...) geram ruído,
por isso filtramos por cashtag/word-boundary antes de pontuar.
"""

from __future__ import annotations

import logging
import os
import re

from ..nlp.sentiment import aggregate, score_text

logger = logging.getLogger("crawler.reddit")

DEFAULT_SUBREDDITS = ("stocks", "wallstreetbets")
POSTS_PER_SUB = 50  # limite de pesquisa por subreddit por ticker


def _build_client():
    """Cria o cliente praw a partir das env vars; ``None`` se faltarem credenciais."""
    cid = os.getenv("PRAW_CLIENT_ID")
    secret = os.getenv("PRAW_CLIENT_SECRET")
    if not (cid and secret):
        logger.warning("Credenciais PRAW ausentes — a saltar fonte Reddit")
        return None
    import praw  # import tardio: só carrega se a fonte for usada

    return praw.Reddit(
        client_id=cid,
        client_secret=secret,
        user_agent=os.getenv("PRAW_USER_AGENT", "fundscope-crawler/0.1"),
        check_for_async=False,
    )


def _mentions_ticker(text: str, ticker: str) -> bool:
    """True se o texto menciona o ticker como cashtag (``$AAPL``) ou token isolado.

    Reduz falsos positivos de tickers que são palavras comuns (S, D, V, KO...).
    """
    if not text:
        return False
    # Cashtag em qualquer caso: $aapl, $AAPL
    if re.search(rf"\${re.escape(ticker)}\b", text, re.IGNORECASE):
        return True
    # Token isolado em MAIÚSCULAS (evita apanhar a palavra "s"/"d" minúsculas)
    return re.search(rf"\b{re.escape(ticker)}\b", text) is not None


def fetch_reddit_sentiment(
    tickers: list[str],
    subreddits: tuple[str, ...] = DEFAULT_SUBREDDITS,
) -> dict[str, dict | None]:
    """Para cada ticker devolve o agregado de sentimento + volume de menções.

    Estrutura por ticker::

        {"mean": float, "n": int, "panic": bool, "mentions": int, "upvotes": int}

    Devolve ``None`` se a fonte não estiver disponível (sem credenciais).
    """
    reddit = _build_client()
    if reddit is None:
        return {t: None for t in tickers}

    out: dict[str, dict | None] = {}
    for ticker in tickers:
        scores: list[float] = []
        mentions = 0
        upvotes = 0
        try:
            for sub in subreddits:
                query = f"${ticker}"  # cashtag dá melhores resultados de pesquisa
                for post in reddit.subreddit(sub).search(
                    query, time_filter="day", limit=POSTS_PER_SUB
                ):
                    text = f"{post.title} {getattr(post, 'selftext', '') or ''}"
                    if not _mentions_ticker(text, ticker):
                        continue
                    scores.append(score_text(text))
                    mentions += 1
                    upvotes += int(getattr(post, "score", 0) or 0)
            agg = aggregate(scores)
            out[ticker] = {**agg, "mentions": mentions, "upvotes": upvotes}
        except Exception as exc:  # noqa: BLE001 — robustez por ticker
            logger.warning("reddit falhou para %s: %s", ticker, exc)
            out[ticker] = None
    return out
