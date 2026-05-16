import time
import sys
import os
from datetime import datetime, timezone, timedelta
from .logger import log_info, log_error
from .phase0 import run_phase0_cycle
from .config import LOOP_INTERVAL_SECONDS

LOCK_FILE = "bot.lock"

# Horário NYSE em UTC (hora de verão: UTC-4, hora de inverno: UTC-5)
# Usamos sempre UTC e deixamos margem de 5 min na abertura/fecho
_MARKET_OPEN_UTC_SUMMER  = (13, 35)   # 09:35 ET (verão)
_MARKET_CLOSE_UTC_SUMMER = (19, 55)   # 15:55 ET (verão)
_MARKET_OPEN_UTC_WINTER  = (14, 35)   # 09:35 ET (inverno)
_MARKET_CLOSE_UTC_WINTER = (20, 55)   # 15:55 ET (inverno)


def _is_dst_us() -> bool:
    """Approxima se os EUA estão em horário de verão (DST).
    DST começa 2.º domingo de Março e termina 1.º domingo de Novembro.
    """
    now = datetime.now(timezone.utc)
    year = now.year
    # 2.º domingo de Março
    mar = datetime(year, 3, 8, 7, 0, tzinfo=timezone.utc)
    dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7)
    # 1.º domingo de Novembro
    nov = datetime(year, 11, 1, 6, 0, tzinfo=timezone.utc)
    dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return dst_start <= now < dst_end


def _market_hours_utc() -> tuple[tuple[int, int], tuple[int, int]]:
    if _is_dst_us():
        return _MARKET_OPEN_UTC_SUMMER, _MARKET_CLOSE_UTC_SUMMER
    return _MARKET_OPEN_UTC_WINTER, _MARKET_CLOSE_UTC_WINTER


def is_market_open() -> bool:
    """Devolve True se o mercado NYSE está aberto agora (sem feriados)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Sábado=5, Domingo=6
        return False
    open_h, open_m = _market_hours_utc()[0]
    close_h, close_m = _market_hours_utc()[1]
    open_time  = now.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    close_time = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return open_time <= now <= close_time


def _seconds_until_next_open() -> int:
    """Calcula segundos até à próxima abertura do mercado."""
    now = datetime.now(timezone.utc)
    open_h, open_m = _market_hours_utc()[0]

    # Próximo dia útil
    candidate = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    # Saltar fim de semana
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    return max(0, int((candidate - now).total_seconds()))


def _acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            import psutil
            if psutil.pid_exists(old_pid):
                return False
            os.remove(LOCK_FILE)
        except Exception:
            os.remove(LOCK_FILE)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def run():
    if not _acquire_lock():
        print("❌ Bot já está a correr (bot.lock existe). Termina o processo anterior primeiro.")
        sys.exit(1)

    log_info("bot_start", {"action": "phase0_loop", "context": {"strategy_version": "v0.1.0"}})
    print("[FundScope Bot] A iniciar...")
    print(f"[FundScope Bot] Iniciado — estratégia: v0.1.0")
    print(f"[FundScope Bot] Ciclo a cada {LOOP_INTERVAL_SECONDS // 60} minutos. Ctrl+C para parar.\n")

    cycle = 1
    try:
        while True:
            if not is_market_open():
                secs = _seconds_until_next_open()
                h, m = divmod(secs // 60, 60)
                print(f"[{time.strftime('%H:%M:%S')} UTC] Mercado fechado — a dormir {h}h{m:02d}m até à próxima abertura...")
                time.sleep(min(secs, 3600))  # acorda de hora em hora para re-verificar
                continue

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
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)


if __name__ == "__main__":
    run()
