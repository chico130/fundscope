"""
Throttler: pacing do round-robin de fetches da watchlist.

Substitui o burst por ciclo (N candidatos em paralelo via ThreadPoolExecutor)
por streaming ticker-a-ticker, suavizado por um token bucket.

O cursor round-robin persiste entre ciclos em data/beta/throttler_state.json,
para a cobertura transitar entre reinícios e ciclos curtos.

stdlib only. Não toca em bot/calibration/.

Nota de rate-limit: a técnica dos candidatos vem do yfinance
(api_client.get_historical_data), não do Finnhub. O Finnhub é usado apenas
em price_feed.py para quotes em tempo real, já serializado a ~57 req/min.
O refill default aqui é calibrado à régua Finnhub-safe (57/min = 1/1.05 req/s)
para protecção de dupla camada se a arquitectura evoluir.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from .config import DATA_BETA_DIR
from .logger import log_error

_STATE_PATH = DATA_BETA_DIR / "throttler_state.json"


class TokenBucket:
    """Token bucket monotónico. acquire() bloqueia até haver token disponível.

    capacity propositadamente pequena (default 1.0): um fetch lento não pode
    deixar tokens acumular e libertar depois uma rajada — o que anularia a
    suavização do throttling. refill_rate em tokens por segundo.
    """

    def __init__(self, refill_rate: float, capacity: float = 1.0) -> None:
        self.refill_rate = refill_rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity,
                    self._tokens + (now - self._last) * self.refill_rate,
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait = (n - self._tokens) / self.refill_rate
            time.sleep(wait)


class WatchlistThrottler:
    """Round-robin paginado da watchlist, ritmado por um token bucket.

    Uso:
        throttler = WatchlistThrottler(["AAPL", "MSFT", ...])
        for ticker, data in throttler.stream(budget_seconds=840):
            if data is None:
                continue
            # avaliar signals imediatamente
    """

    def __init__(
        self,
        watchlist: list[str],
        refill_rate: float = 1.0 / 1.05,   # ~57 req/min — régua Finnhub-safe
        state_path: Path = _STATE_PATH,
        max_age_seconds: float = 90.0,
    ) -> None:
        self.watchlist = list(watchlist)
        self.bucket = TokenBucket(refill_rate=refill_rate, capacity=1.0)
        self.state_path = state_path
        self.max_age_seconds = max_age_seconds
        self._cursor = self._load_cursor()

    # -- persistência -------------------------------------------------------

    def _wl_hash(self) -> str:
        """Hash estável do conteúdo da watchlist (order-independent)."""
        joined = "|".join(sorted(self.watchlist))
        return hashlib.sha1(joined.encode()).hexdigest()[:12]

    def _load_cursor(self) -> int:
        try:
            state = json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return 0
        if state.get("watchlist_hash") != self._wl_hash():
            return 0  # watchlist mudou — recomeça a passagem
        n = max(len(self.watchlist), 1)
        return int(state.get("cursor", 0)) % n

    def _save_cursor(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({
                "cursor":         self._cursor,
                "watchlist_hash": self._wl_hash(),
                "updated_at":     time.strftime("%Y-%m-%dT%H:%M:%S"),
            }))
        except OSError as exc:
            log_error("throttler_state_save_failed", {"error": str(exc)})

    # -- streaming ----------------------------------------------------------

    def stream(
        self, budget_seconds: float | None = None
    ) -> Iterator[tuple[str, dict | None]]:
        """Cede (ticker, market_data) um ticker de cada vez, ritmado pelo bucket.

        Round-robin a começar no cursor persistido. Pára após uma passagem
        completa ou quando budget_seconds esgota (o que vier primeiro). O
        cursor é persistido após cada ticker — a cobertura transita para o
        ciclo seguinte mesmo em paragem antecipada.

        market_data: dict com chaves "technicals", "last_price", "_fetched_at",
        "_fetch_secs"; ou None se o ticker falhou / dados insuficientes.
        """
        from .data_layer import fetch_single_ticker

        if not self.watchlist:
            return

        start = time.monotonic()
        n = len(self.watchlist)

        for _ in range(n):
            if budget_seconds is not None and (time.monotonic() - start) >= budget_seconds:
                break

            ticker = self.watchlist[self._cursor]
            self.bucket.acquire()

            t0 = time.monotonic()
            try:
                data = fetch_single_ticker(ticker)
            except Exception as exc:
                log_error("throttler_fetch_failed", {"ticker": ticker, "error": str(exc)})
                data = None

            if data is not None:
                data["_fetched_at"] = time.time()
                data["_fetch_secs"] = round(time.monotonic() - t0, 2)

            self._cursor = (self._cursor + 1) % n
            self._save_cursor()
            yield ticker, data


def is_fresh(record: dict, max_age_seconds: float = 90.0) -> bool:
    """True se o registo foi buscado há menos de max_age_seconds."""
    ts = record.get("_fetched_at")
    return ts is not None and (time.time() - ts) < max_age_seconds
