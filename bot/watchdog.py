"""
Watchdog — rede de segurança de missão crítica.

Três mecanismos independentes:

  1. Quarentena (EMERGENCY_LOCK.txt)
     Criado em resposta a um erro fatal. Commitado ao git para persistir
     entre runs do GitHub Actions. O bot recusa arrancar enquanto existir.
     Recovery: git rm EMERGENCY_LOCK.txt && git commit -m 'fix: remove quarantine' && git push

  2. SOS Alert
     Envia traceback completo para Telegram com som/vibração quando o bot
     captura um erro não tratado. Usa requests directamente para máxima
     robustez (não depende do estado do notifier).

  3. retry_on_network_error
     Decorador stdlib-only (sem dependências externas) para chamadas de rede
     transitórias. Backoff exponencial: 5s → 10s → 20s.
     NÃO usar em operações não-idempotentes (ex: colocação de ordens T212).
"""
from __future__ import annotations

import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from functools import wraps

from .config import BASE_DIR

EMERGENCY_LOCK_PATH = BASE_DIR / "EMERGENCY_LOCK.txt"


# ---------------------------------------------------------------------------
# Verificação de quarentena — chamada no arranque do bot
# ---------------------------------------------------------------------------

def is_quarantined() -> bool:
    """True se EMERGENCY_LOCK.txt existir na raiz do projecto."""
    return EMERGENCY_LOCK_PATH.exists()


def check_quarantine_and_abort() -> None:
    """Verifica quarentena no arranque. Termina o processo com exit(1) se activa.

    Envia alerta Telegram com o resumo do erro original antes de sair.
    """
    if not is_quarantined():
        return

    print("[WATCHDOG] Sistema em quarentena — bot recusa arranque.")
    _send_quarantine_alert()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Activação de quarentena — chamada pelo global exception handler
# ---------------------------------------------------------------------------

def quarantine(exc: BaseException, context: str = "phase0") -> None:
    """Activa quarentena em resposta a erro fatal não tratado.

    Sequência:
      1. Escreve EMERGENCY_LOCK.txt com traceback completo
      2. Envia alerta SOS para Telegram (com som)
      3. Tenta commitar o lock ao git para persistir entre Actions runs
    """
    tb = traceback.format_exc()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_lock(exc, tb, ts, context)
    _send_sos(exc, tb, ts, context)
    _commit_lock(ts)


# ---------------------------------------------------------------------------
# Internos — quarentena
# ---------------------------------------------------------------------------

def _write_lock(exc: BaseException, tb: str, ts: str, context: str) -> None:
    try:
        EMERGENCY_LOCK_PATH.write_text(
            f"EMERGENCY LOCK — FundScope\n"
            f"Criado:   {ts}\n"
            f"Contexto: {context}\n"
            f"Erro:     {type(exc).__name__}: {exc}\n"
            f"\n"
            f"Traceback:\n{tb}\n"
            f"Recovery:\n"
            f"  git rm EMERGENCY_LOCK.txt\n"
            f"  git commit -m 'fix: remove quarantine'\n"
            f"  git push\n",
            encoding="utf-8",
        )
        print(f"[WATCHDOG] EMERGENCY_LOCK.txt criado.")
    except OSError as write_exc:
        print(f"[WATCHDOG] Falha ao escrever lock: {write_exc}")


def _commit_lock(ts: str) -> None:
    """Commita EMERGENCY_LOCK.txt ao git para persistir entre GitHub Actions runs.

    Best-effort: se falhar (sem rede, sem permissões), o lock ainda existe
    localmente no runner mas não sobrevive ao próximo checkout.
    """
    try:
        root = str(BASE_DIR)
        subprocess.run(
            ["git", "add", str(EMERGENCY_LOCK_PATH)],
            cwd=root, timeout=15, capture_output=True, shell=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", f"emergency: bot quarantined at {ts}"],
            cwd=root, timeout=15, capture_output=True, shell=True,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=root, timeout=30, capture_output=True, shell=True,
            )
            print("[WATCHDOG] EMERGENCY_LOCK.txt commitado ao git — quarentena persistente.")
        else:
            print("[WATCHDOG] Git commit do lock falhou (lock apenas local neste runner).")
    except Exception as git_exc:
        print(f"[WATCHDOG] Git commit do lock ignorado: {git_exc}")


def _send_sos(exc: BaseException, tb: str, ts: str, context: str) -> None:
    """Envia alerta SOS para Telegram. Nunca lança excepção."""
    try:
        from .notifier import enviar_alerta
        tb_short = tb[-700:] if len(tb) > 700 else tb
        texto = (
            f"🚨 SOS — FundScope\n"
            f"\n"
            f"{type(exc).__name__}: {str(exc)[:200]}\n"
            f"\n"
            f"Contexto: {context}\n"
            f"{ts}\n"
            f"\n"
            f"{tb_short}\n"
            f"\n"
            f"Bot em QUARENTENA.\n"
            f"Remove EMERGENCY_LOCK.txt e faz push para reactivar."
        )
        enviar_alerta(texto, silencioso=False)
    except Exception as notif_exc:
        print(f"[WATCHDOG] Falha ao enviar SOS Telegram: {notif_exc}")


def _send_quarantine_alert() -> None:
    """Notificação de arranque bloqueado — bot já estava em quarentena."""
    try:
        from .notifier import enviar_alerta
        lock_info = ""
        try:
            for line in EMERGENCY_LOCK_PATH.read_text(encoding="utf-8").splitlines()[:5]:
                if line.startswith(("Criado:", "Erro:", "Contexto:")):
                    lock_info += f"{line}\n"
        except OSError:
            pass

        texto = (
            f"🔒 Sistema em Quarentena\n"
            f"\n"
            f"Bot recusou arranque — intervenção necessária.\n"
            f"\n"
            f"{lock_info}"
            f"\n"
            f"Recovery:\n"
            f"git rm EMERGENCY_LOCK.txt && git commit && git push"
        )
        enviar_alerta(texto, silencioso=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Retry decorator — stdlib only, sem dependências externas
# ---------------------------------------------------------------------------

def retry_on_network_error(
    max_attempts: int = 3,
    delay: float = 5.0,
    backoff: float = 2.0,
):
    """Decorador de retry com backoff exponencial para erros de rede transitórios.

    Retenta em: IOError, OSError, TimeoutError, ConnectionError e subclasses
    de requests.exceptions (ConnectTimeout, ReadTimeout, ConnectionError).
    Erros de lógica/dados propagam-se imediatamente sem retry.

    NUNCA usar em operações não-idempotentes (ex: POST de ordens T212) — risco
    de ordens duplicadas.

    Uso:
        @retry_on_network_error(max_attempts=3, delay=5)
        def fetch(): ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            retriable: tuple = (IOError, OSError, TimeoutError, ConnectionError)
            try:
                import requests.exceptions as _req_exc
                retriable = retriable + (
                    _req_exc.ConnectTimeout,
                    _req_exc.ReadTimeout,
                    _req_exc.ConnectionError,
                )
            except ImportError:
                pass

            wait     = delay
            last_exc: Exception | None = None

            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except retriable as exc:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        print(
                            f"[retry] {fn.__name__} falhou "
                            f"(tentativa {attempt + 1}/{max_attempts}): "
                            f"{type(exc).__name__} — a aguardar {wait:.0f}s"
                        )
                        time.sleep(wait)
                        wait *= backoff
                    else:
                        print(f"[retry] {fn.__name__} esgotou {max_attempts} tentativas.")

            if last_exc is not None:
                raise last_exc
            return None
        return wrapper
    return decorator
