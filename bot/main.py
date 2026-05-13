"""
Bot main loop — ties all modules together.

Cycle (every LOOP_INTERVAL_SECONDS):
  1. Read T212 demo portfolio state
  2. Enrich with technical indicators
  3. Generate signals  (strategy.py)
  4. [Fase 1] Check risk limits + execute trades  (execution.py)
  5. Update site JSON files  (reporter.py)
  6. Periodic learner report  (learner.py, ~once per day)

Usage:
  python -m bot.main            # Fase 1: full loop with execution
  python -m bot.main --phase0   # Fase 0: read-only, no orders
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from .config import LIVE_TRADING, STRATEGY_VERSION
from .data_layer import get_full_portfolio_state, enrich_with_technicals
from .logger import log_decision, log_error
from . import reporter

LOOP_INTERVAL_SECONDS   = 300   # 5-minute cycle
LEARNER_INTERVAL_CYCLES = 288   # ~once per 24 h (288 × 5 min)


def run(phase0_only: bool = False) -> None:
    if LIVE_TRADING:
        raise RuntimeError(
            "LIVE_TRADING é True — nunca activar sem testes extensivos em demo."
        )

    mode = "phase0_readonly" if phase0_only else "fase1_demo"
    log_decision("bot_start", mode, {"strategy_version": STRATEGY_VERSION})
    print(f"[FundScope Bot] Iniciado — modo: {mode} — estratégia: v{STRATEGY_VERSION}")
    print(f"[FundScope Bot] Ciclo a cada {LOOP_INTERVAL_SECONDS}s. Ctrl+C para parar.\n")

    cycle = 0
    while True:
        try:
            _run_cycle(phase0_only=phase0_only, cycle=cycle)
        except KeyboardInterrupt:
            log_decision("bot_stop", "keyboard_interrupt", {"cycle": cycle})
            print("\n[FundScope Bot] Interrompido pelo utilizador.")
            break
        except Exception as exc:
            log_error("cycle_unhandled_exception", {"cycle": cycle, "error": str(exc)})
            print(f"[FundScope Bot] Erro no ciclo {cycle}: {exc}")

        cycle += 1
        print(
            f"[FundScope Bot] Ciclo {cycle} completo "
            f"({datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC) — "
            f"próximo em {LOOP_INTERVAL_SECONDS}s"
        )
        time.sleep(LOOP_INTERVAL_SECONDS)


def _run_cycle(phase0_only: bool, cycle: int) -> None:
    log_decision("cycle_start", "begin", {
        "cycle": cycle,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # 1. Portfolio state
    state = get_full_portfolio_state()
    if state is None:
        log_decision("cycle_abort", "no_portfolio_data", {"cycle": cycle})
        print(f"  [ciclo {cycle}] T212 indisponível — a saltar.")
        return

    positions = state.get("positions", [])
    print(f"  [ciclo {cycle}] {len(positions)} posições carregadas.")

    # 2. Technical indicators
    positions = enrich_with_technicals(positions)
    market_data = {p["ticker"]: p for p in positions if p.get("ticker")}

    # 3. Signals
    from .strategy import generate_signals, propose_trades
    signals = generate_signals(market_data, state)
    log_decision("cycle_signals", "generated", {
        "n_signals": len(signals),
        "tickers": [s.ticker for s in signals],
    })
    if signals:
        print(f"  [ciclo {cycle}] {len(signals)} sinal(is): "
              + ", ".join(f"{s.ticker}({s.signal_type})" for s in signals))

    # 4. Execution (Fase 1 only)
    if not phase0_only and signals:
        proposals = propose_trades(signals, state)
        log_decision("cycle_proposals", "count", {"n": len(proposals)})

        from .execution import execute_trade
        executed = 0
        for proposal in proposals:
            result = execute_trade(proposal, state)
            if result:
                executed += 1
                print(f"  [ciclo {cycle}] TRADE: {proposal.side} {proposal.qty} {proposal.ticker}")
        if executed:
            log_decision("cycle_execution", "complete", {"executed": executed})

    # 5. Update site JSON files
    reporter.run_all()
    print(f"  [ciclo {cycle}] Ficheiros beta/ actualizados.")

    # 6. Weekly learner report (periodic)
    if cycle > 0 and cycle % LEARNER_INTERVAL_CYCLES == 0:
        from .learner import generate_weekly_report
        print(generate_weekly_report())

    log_decision("cycle_end", "complete", {"cycle": cycle})


if __name__ == "__main__":
    phase0 = "--phase0" in sys.argv
    run(phase0_only=phase0)
