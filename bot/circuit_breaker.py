"""Circuit breaker em memória por API externa (T212, Finnhub, yfinance).

Conta falhas consecutivas por API. Ao atingir o limiar (3) "abre" o circuito:
envia um alerta Telegram (uma única vez) e passa a recusar chamadas via
``allow() == False``, para o bot continuar a correr SEM essa API em vez de a
martelar a cada ticker. Um sucesso fecha o circuito e repõe o contador.

O estado é por-processo: cada run do GitHub Actions é um único ciclo, por isso
isto curto-circuita uma API morta a meio do ciclo (ex: 100 fetches yfinance) sem
poluir ciclos futuros nem duplicar alertas entre runs.

Nunca lança excepção — alertas e logs são best-effort.
"""
from __future__ import annotations

from datetime import datetime, timezone

THRESHOLD = 3


class _Breaker:
    __slots__ = ("name", "failures", "is_open", "alerted")

    def __init__(self, name: str) -> None:
        self.name = name
        self.failures = 0
        self.is_open = False
        self.alerted = False


_breakers: dict[str, _Breaker] = {}


def _get(name: str) -> _Breaker:
    br = _breakers.get(name)
    if br is None:
        br = _Breaker(name)
        _breakers[name] = br
    return br


def allow(name: str) -> bool:
    """False se o circuito desta API está aberto — o chamador deve saltar a chamada."""
    return not _get(name).is_open


def is_open(name: str) -> bool:
    return _get(name).is_open


def record_success(name: str) -> None:
    """Repõe o contador. Fecha o circuito se estava aberto (recuperação)."""
    br = _get(name)
    br.failures = 0
    if br.is_open:
        br.is_open = False
        br.alerted = False
        _log("circuit_closed", name, {})


def record_failure(name: str, error: str = "") -> None:
    """Incrementa falhas consecutivas; abre o circuito (+ alerta) ao atingir THRESHOLD."""
    br = _get(name)
    br.failures += 1
    if br.failures >= THRESHOLD and not br.is_open:
        br.is_open = True
        _trip_alert(name, br.failures, error)


def reset(name: str | None = None) -> None:
    """Repõe um circuito (ou todos, se name=None). Útil em testes."""
    if name is None:
        _breakers.clear()
    else:
        _breakers.pop(name, None)


# ---------------------------------------------------------------------------
# Side-effects (best-effort, nunca lançam)
# ---------------------------------------------------------------------------

def _trip_alert(name: str, failures: int, error: str) -> None:
    _log("circuit_open", name, {"failures": failures, "error": error[:200]})
    print(f"[circuit] {name.upper()} ABERTO — {failures} falhas consecutivas. Bot continua sem esta API.", flush=True)
    try:
        from .notifier import enviar_alerta
        enviar_alerta(
            f"⚠️ Circuit breaker — {name.upper()}\n"
            f"\n"
            f"{failures} falhas consecutivas. O bot continua SEM esta API neste ciclo.\n"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            silencioso=False,
        )
    except Exception as exc:
        print(f"[circuit] falha ao enviar alerta Telegram: {exc}", flush=True)


def _log(event: str, name: str, detail: dict) -> None:
    try:
        from .logger import log_decision
        log_decision("circuit_breaker", event, {"api": name, **detail})
    except Exception:
        pass
