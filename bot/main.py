import time
import sys
import os
from .logger import log_info, log_error
from .phase0 import run_phase0_cycle
from .config import LOOP_INTERVAL_SECONDS

LOCK_FILE = "bot.lock"

def _acquire_lock():
    """Retorna True se conseguiu o lock, False se já há instância a correr."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            # Verifica se o processo ainda existe (Windows)
            import psutil
            if psutil.pid_exists(old_pid):
                return False
            # PID morto — lock fantasma, limpa e continua
            os.remove(LOCK_FILE)
        except Exception:
            os.remove(LOCK_FILE)  # corrompido, limpa
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def run():
    # ✅ Lock dentro do run() — protege independentemente de como é chamado
    if not _acquire_lock():
        print(f"❌ Bot já está a correr (bot.lock existe). Termina o processo anterior primeiro.")
        sys.exit(1)

    log_info("bot_start", {"action": "phase0_loop", "context": {"strategy_version": "v0.1.0"}})
    print("[FundScope Bot] A iniciar...")
    print(f"[FundScope Bot] Iniciado — estratégia: v0.1.0")
    print(f"[FundScope Bot] Ciclo a cada {LOOP_INTERVAL_SECONDS // 60} minutos. Ctrl+C para parar.\n")

    cycle = 1
    try:
        while True:
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp} UTC] Ciclo {cycle} — a iniciar análise...")
            try:
                run_phase0_cycle()
            except Exception as exc:
                log_error("main_cycle_failed", {"cycle": cycle, "error": str(exc)})
                print(f"Erro no ciclo {cycle}: {exc}")

            print(f"[{time.strftime('%H:%M:%S')} UTC] Próximo ciclo em {LOOP_INTERVAL_SECONDS // 60} minutos.\n")
            cycle += 1
            time.sleep(LOOP_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\nBot parado pelo utilizador.")
    finally:
        # ✅ Sempre limpa o lock — mesmo em crash
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)

if __name__ == "__main__":
    run()
