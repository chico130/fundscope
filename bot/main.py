"""
Bot main loop — executa phase0 a cada 15 minutos e faz push para GitHub.

Uso:
  python -m bot.main
"""
from __future__ import annotations

import time
import requests

from datetime import datetime, timezone

from .config import STRATEGY_VERSION
from .logger import log_decision, log_error
from . import phase0

LOOP_INTERVAL_SECONDS = 900  # 15 minutos


def run() -> None:
    print(f"[FundScope Bot] Iniciado — estratégia: {STRATEGY_VERSION}")
    print(f"[FundScope Bot] Ciclo a cada {LOOP_INTERVAL_SECONDS // 60} minutos. Ctrl+C para parar.\n")
    log_decision("bot_start", "phase0_loop", {"strategy_version": STRATEGY_VERSION})

    cycle = 0
    while True:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts} UTC] Ciclo {cycle + 1} — a iniciar análise...")
        try:
            phase0.run()

        except KeyboardInterrupt:
            log_decision("bot_stop", "keyboard_interrupt", {"cycle": cycle})
            print("\n[FundScope Bot] Interrompido pelo utilizador.")
            break

        except requests.exceptions.ConnectionError as exc:
            log_error("network_error", {"cycle": cycle, "error": str(exc)})
            print(f"  Erro de rede — a tentar novamente no próximo ciclo. ({exc})")

        except requests.exceptions.Timeout as exc:
            log_error("network_timeout", {"cycle": cycle, "error": str(exc)})
            print(f"  Timeout de rede — a tentar novamente no próximo ciclo. ({exc})")

        except Exception as exc:
            log_error("cycle_unhandled_exception", {"cycle": cycle, "error": str(exc)})
            print(f"  Erro inesperado no ciclo {cycle + 1}: {exc}")

        cycle += 1
        next_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{next_ts} UTC] Próximo ciclo em {LOOP_INTERVAL_SECONDS // 60} minutos.\n")
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
