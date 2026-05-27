import time
import sys
import os
from datetime import datetime, timezone, timedelta

# Força UTF-8 no terminal Windows (evita "?" em vez de ç, ã, é, etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from .logger import log_info, log_error
from .phase0 import run_phase0_cycle
from .reporter import run_all as reporter_run_all
from .config import LOOP_INTERVAL_SECONDS
from .market_hours import market_hours_utc
from .notifier import enviar_resumo_diario, enviar_despertar, enviar_boa_noite

try:
    import psutil as _psutil
except ImportError:
    _psutil = None

LOCK_FILE = "bot.lock"


def is_market_open() -> bool:
    """Devolve True se o mercado NYSE está aberto agora (sem feriados)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Sábado=5, Domingo=6
        return False
    (open_h, open_m), (close_h, close_m) = market_hours_utc(now)
    open_time  = now.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    close_time = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return open_time <= now <= close_time


def _seconds_until_next_open() -> int:
    """Calcula segundos até à próxima abertura do mercado."""
    now = datetime.now(timezone.utc)
    (open_h, open_m), _ = market_hours_utc(now)

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
        except Exception:
            os.remove(LOCK_FILE)  # corrompido, limpa
        else:
            # Se psutil não estiver instalado, assume que o processo ainda está vivo (seguro)
            if _psutil is None or _psutil.pid_exists(old_pid):
                return False
            os.remove(LOCK_FILE)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _coletar_dados_resumo() -> dict:
    """Agrega métricas do dia para o relatório Telegram de fecho de mercado."""
    from datetime import date
    from .data_layer import read_beta_summary, read_beta_equity
    from .bonnie import read_diario_trades

    hoje = date.today().isoformat()

    summary_data = read_beta_summary() or {}
    summary = summary_data.get("summary", {})
    saldo = f"{summary.get('current_value', 0):.2f}"

    # Performance de hoje: primeira vs. última entrada no histórico de equity
    equity_data = read_beta_equity() or {"history": []}
    history = [h for h in equity_data.get("history", []) if h.get("datetime", "").startswith(hoje)]
    if len(history) >= 2:
        inicio, fim = history[0]["equity"], history[-1]["equity"]
        variacao = f"{(fim - inicio) / inicio * 100:+.2f}" if inicio else "+0.00"
    else:
        variacao = "+0.00"

    # Contagens de hoje a partir do diário público
    trades = read_diario_trades()
    hoje_trades = [t for t in trades if t.get("timestamp", "").startswith(hoje)]
    sinais = len([t for t in hoje_trades if t.get("tipo") in ("entrada", "bloqueado")])
    ordens = len([t for t in hoje_trades if t.get("tipo") == "entrada"])
    vetos = len([t for t in hoje_trades if t.get("tipo") == "bloqueado"])

    # Prejuízo estimado evitado: vetos × |avg_loss| histórico; fallback €50/veto
    avg_loss = abs(summary.get("avg_loss_eur") or 0)
    estimativa = vetos * (avg_loss if avg_loss else 50.0)
    poupanca = f"{estimativa:.2f}"

    # Regime actual (yfinance — pode falhar fora de horas)
    try:
        from .regime_detector import get_current_regime
        regime = get_current_regime()
    except Exception:
        regime = "desconhecido"

    return {
        "saldo": saldo,
        "variacao": variacao,
        "sinais_contagem": sinais,
        "ordens_contagem": ordens,
        "vetos_contagem": vetos,
        "poupanca": poupanca,
        "regime": regime,
    }


def run():
    if not _acquire_lock():
        print("❌ Bot já está a correr (bot.lock existe). Termina o processo anterior primeiro.")
        sys.exit(1)

    log_info("bot_start", {"action": "phase0_loop", "context": {"strategy_version": "v0.1.0"}})
    print("[FundScope Bot] A iniciar...")
    print(f"[FundScope Bot] Iniciado — estratégia: v0.1.0")
    print(f"[FundScope Bot] Ciclo a cada {LOOP_INTERVAL_SECONDS // 60} minutos. Ctrl+C para parar.\n")

    cycle = 1
    mercado_estava_aberto = False
    ultimo_report = None
    try:
        while True:
            if not is_market_open():
                # Transição aberto → fechado: dispara resumo diário + boa noite uma única vez
                if mercado_estava_aberto:
                    mercado_estava_aberto = False
                    print(f"[{time.strftime('%H:%M:%S')} UTC] Mercado fechou — a enviar resumo diário...")
                    try:
                        enviar_resumo_diario(_coletar_dados_resumo())
                    except Exception as exc:
                        log_error("resumo_diario_failed", {"error": str(exc)})
                        print(f"[Notifier] Erro ao enviar resumo diário: {exc}")
                    if ultimo_report is not None:
                        try:
                            enviar_boa_noite(ultimo_report)
                        except Exception as exc:
                            log_error("boa_noite_failed", {"error": str(exc)})
                            print(f"[Notifier] Erro ao enviar boa noite: {exc}")

                secs = _seconds_until_next_open()
                h, m = divmod(secs // 60, 60)
                print(f"[{time.strftime('%H:%M:%S')} UTC] Mercado fechado — a dormir {h}h{m:02d}m até à próxima abertura...")
                time.sleep(min(secs, 3600))  # acorda de hora em hora para re-verificar
                continue

            # Transição fechado → aberto: despertar (Bom dia) após o 1.º ciclo do dia
            despertar_pendente = not mercado_estava_aberto
            mercado_estava_aberto = True
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp} UTC] Ciclo {cycle} — a iniciar análise...")
            try:
                ultimo_report = run_phase0_cycle()
                try:
                    reporter_run_all()
                except Exception as exc:
                    log_error("reporter_failed", {"error": str(exc)})
                    print(f"[Reporter] Erro ao actualizar ficheiros beta: {exc}")
            except Exception as exc:
                log_error("main_cycle_failed", {"cycle": cycle, "error": str(exc)})
                print(f"Erro no ciclo {cycle}: {exc}")

            # Despertar — dedup é feita dentro de enviar_despertar via data/daily_flags.json
            if despertar_pendente and ultimo_report is not None:
                try:
                    enviar_despertar(ultimo_report)
                except Exception as exc:
                    log_error("despertar_failed", {"error": str(exc)})
                    print(f"[Notifier] Erro ao enviar despertar: {exc}")

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
