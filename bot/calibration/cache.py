"""
cache.py — Download yfinance em lote → cache Parquet em disco.

Idempotente: reutiliza a cache se cobrir [start, end].
Usa ThreadPoolExecutor para paralelismo de I/O (nunca chama Finnhub).

Estrutura em disco:
  data/cache/ohlcv/<TICKER>.parquet   — OHLCV diário (auto_adjust=True)
  data/cache/_meta.json              — {ticker: {first, last, fetched_at}}
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from bot.config import BASE_DIR

_OHLCV_DIR  = BASE_DIR / "data" / "cache" / "ohlcv"
_META_PATH  = BASE_DIR / "data" / "cache" / "_meta.json"
_WORKERS    = 8
_TIMEOUT    = 60          # segundos por download
_BATCH_PAUSE = 1.5        # pausa entre lotes (cortesia de rate-limit)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def ensure_ohlcv_cache(
    tickers:  list[str],
    start:    str,
    end:      str,
    refresh:  bool = False,
) -> None:
    """
    Garante que cada ticker em `tickers` tem Parquet em cache cobrindo [start, end].

    Tickers com cache válida são ignorados (a menos de refresh=True).
    Falhas individuais são registadas e ignoradas (semântica de sucesso parcial).
    """
    _OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    meta = _load_meta()

    to_download = []
    for ticker in tickers:
        if not refresh and _cache_covers(meta, ticker, start, end):
            continue
        to_download.append(ticker)

    if not to_download:
        return

    print(f"[cache] {len(to_download)} tickers para descarregar "
          f"(de {len(tickers)} total)...")

    # Download em lotes de 50 para não sobrecarregar o yfinance
    batch_size = 50
    batches    = [to_download[i:i + batch_size]
                  for i in range(0, len(to_download), batch_size)]

    # Calço extra para que EMA-200 e regime sejam fiáveis no primeiro dia avaliável
    dl_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=300)).strftime("%Y-%m-%d")
    dl_end   = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    for b_idx, batch in enumerate(batches, 1):
        print(f"  lote {b_idx}/{len(batches)} ({len(batch)} tickers)...", flush=True)
        updated = _download_batch(batch, dl_start, dl_end, meta)
        _save_meta(meta)
        if updated:
            print(f"    {updated} tickers guardados.", flush=True)
        if b_idx < len(batches):
            time.sleep(_BATCH_PAUSE)

    print(f"[cache] concluído.")


def load_ohlcv(ticker: str) -> pd.DataFrame | None:
    """
    Lê o Parquet de um ticker. Devolve None se não existir.

    Index: DatetimeIndex tz-naive (meia-noite).
    Colunas: open, high, low, close, volume.
    """
    path = _ticker_path(ticker)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception:
        return None


def cached_tickers() -> list[str]:
    """Devolve lista de tickers com Parquet disponível em cache."""
    return [p.stem for p in _OHLCV_DIR.glob("*.parquet")]


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _ticker_path(ticker: str) -> Path:
    return _OHLCV_DIR / f"{ticker}.parquet"


def _cache_covers(meta: dict, ticker: str, start: str, end: str) -> bool:
    info = meta.get(ticker)
    if not info or not _ticker_path(ticker).exists():
        return False
    return info.get("first", "9999") <= start and info.get("last", "0000") >= end


def _load_meta() -> dict:
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _save_meta(meta: dict) -> None:
    _META_PATH.parent.mkdir(parents=True, exist_ok=True)
    _META_PATH.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def _download_one(ticker: str, dl_start: str, dl_end: str) -> tuple[str, pd.DataFrame | None]:
    """Download individual de um ticker via yf.Ticker (robusto, sem MultiIndex)."""
    try:
        df = yf.Ticker(ticker).history(
            start=dl_start, end=dl_end,
            interval="1d", auto_adjust=True,
        )
        if df is None or df.empty:
            return ticker, None

        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]
        return ticker, df
    except Exception as exc:
        print(f"    WARN {ticker}: {exc}", flush=True)
        return ticker, None


def _download_batch(
    tickers:  list[str],
    dl_start: str,
    dl_end:   str,
    meta:     dict,
) -> int:
    """
    Descarrega um lote de tickers em paralelo e guarda cada um em Parquet.
    Devolve o número de tickers efetivamente guardados.
    """
    saved = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {pool.submit(_download_one, t, dl_start, dl_end): t
                   for t in tickers}
        for future in as_completed(futures):
            ticker, df = future.result()
            if df is None or df.empty:
                continue
            try:
                df.to_parquet(_ticker_path(ticker), index=True)
                meta[ticker] = {
                    "first":      df.index.min().strftime("%Y-%m-%d"),
                    "last":       df.index.max().strftime("%Y-%m-%d"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                saved += 1
            except Exception as exc:
                print(f"    WARN {ticker} (save): {exc}", flush=True)
    return saved
