"""
universe.py — Constituintes do S&P 500 com snapshot em cache local.

Fonte: tabela da Wikipédia (pandas.read_html + lxml).
Cache: data/cache/sp500_constituents.json

⚠ Viés de sobrevivência: usa a composição atual do índice.
Empresas removidas (falências, quedas) não entram. Fase 2 corrige.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import io

import pandas as pd
import requests

from bot.config import BASE_DIR

_CACHE_PATH = BASE_DIR / "data" / "cache" / "sp500_constituents.json"
_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Tickers que o yfinance representa de forma diferente da Wikipédia
_TICKER_MAP = {
    "BRK.B": "BRK-B",
    "BF.B":  "BF-B",
    "BRK.A": "BRK-A",
    "BF.A":  "BF-A",
}


def get_sp500_tickers(refresh: bool = False) -> list[str]:
    """
    Devolve lista de tickers do S&P 500, normalizados para o formato yfinance.

    Lê o snapshot em cache se existir (e refresh=False).
    Caso contrário, descarrega da Wikipédia e atualiza a cache.
    """
    if not refresh and _CACHE_PATH.exists():
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            tickers = data.get("tickers", [])
            if tickers:
                return tickers
        except (json.JSONDecodeError, KeyError):
            pass

    tickers = _fetch_from_wikipedia()
    _save_snapshot(tickers)
    return tickers


def _fetch_from_wikipedia() -> list[str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; FundScope-Calibration/1.0; "
            "research bot; +https://github.com/chico130/fundscope)"
        )
    }
    resp = requests.get(_WIKI_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text), match="Symbol")
    df = tables[0]
    raw_tickers = df["Symbol"].tolist()
    return [_normalize(t) for t in raw_tickers if isinstance(t, str)]


def _normalize(ticker: str) -> str:
    ticker = ticker.strip().upper()
    return _TICKER_MAP.get(ticker, ticker.replace(".", "-"))


def _save_snapshot(tickers: list[str]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of":   datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "count":   len(tickers),
        "tickers": tickers,
    }
    _CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
