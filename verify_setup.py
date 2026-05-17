#!/usr/bin/env python3
"""
verify_setup.py — Diagnóstico mecânico do FundScope.

Valida importações, pastas, fallbacks de parâmetros e um pulso dry-run completo
sem enviar alertas reais, sem colocar ordens, sem git.

Uso:
    python verify_setup.py
"""
from __future__ import annotations

import sys
import traceback

# Força UTF-8 no stdout (necessário no Windows com cp1252 por defeito)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Infraestrutura de testes
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def check(label: str, fn) -> None:
    """Corre fn(); regista ✅ ou ❌ com detalhe do erro se falhar."""
    try:
        fn()
        _results.append((label, True, ""))
        print(f"  ✅  {label}")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        _results.append((label, False, detail))
        print(f"  ❌  {label}")
        print(f"       {detail}")
        # Traceback completo apenas se var de ambiente DEBUG=1
        import os
        if os.getenv("DEBUG"):
            traceback.print_exc()


def section(title: str) -> None:
    print(f"\n── {title} {'─' * max(0, 52 - len(title))}")


# ---------------------------------------------------------------------------
# Dados sintéticos reutilizados nos testes de pulso
# ---------------------------------------------------------------------------

def _dummy_bars(n: int = 220) -> list[dict]:
    """220 barras OHLCV sintéticas — suficientes para EMA-200, RSI-14, ATR-14."""
    price = 150.0
    bars  = []
    for i in range(n):
        # Oscilação ligeira para RSI ~50 (neutro) e EMA50 > EMA200
        delta = 0.002 if (i % 7) < 4 else -0.001
        close = round(price * (1 + delta), 4)
        bars.append({
            "date":   f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
            "open":   price,
            "high":   round(close * 1.005, 4),
            "low":    round(close * 0.995, 4),
            "close":  close,
            "volume": 2_000_000 + i * 500,
        })
        price = close
    return bars


_DUMMY_PORTFOLIO = {
    "positions": [],
    "cash":      {"free": 5000.0, "total": 5000.0},
}

_DUMMY_BARS = _dummy_bars()


# ===========================================================================
# TESTE 1 — Importações e Sintaxe
# ===========================================================================
section("Teste 1: Importações e Sintaxe")


def _t1_strategy():
    import bot.strategy as m
    assert callable(m.generate_signals),  "generate_signals em falta"
    assert callable(m.propose_trades),    "propose_trades em falta"
    assert hasattr(m, "_PC"),             "_PC (clyde params) em falta"
    assert hasattr(m, "_PB"),             "_PB (bonnie params) em falta"


def _t1_learner():
    import bot.learner as m
    assert callable(m.get_active_params),   "get_active_params em falta"
    assert callable(m.run_learner_cycle),   "run_learner_cycle em falta"
    assert isinstance(m._DEFAULT_PARAMS, dict), "_DEFAULT_PARAMS em falta"
    assert isinstance(m._PARAM_SPACE, dict),    "_PARAM_SPACE em falta"


def _t1_cro():
    import bot.cro as m
    assert hasattr(m, "CRO"),  "classe CRO em falta"


def _t1_notifier():
    import bot.notifier as m
    assert callable(m.enviar_alerta),       "enviar_alerta em falta"
    assert callable(m.enviar_oportunidade), "enviar_oportunidade em falta"


def _t1_watchdog():
    import bot.watchdog as m
    assert callable(m.check_quarantine_and_abort), "check_quarantine_and_abort em falta"
    assert callable(m.quarantine),                 "quarantine em falta"
    assert callable(m.retry_on_network_error),     "retry_on_network_error em falta"
    assert hasattr(m, "EMERGENCY_LOCK_PATH"),       "EMERGENCY_LOCK_PATH em falta"


check("bot.strategy — generate_signals, propose_trades, _PC, _PB", _t1_strategy)
check("bot.learner  — get_active_params, run_learner_cycle, _DEFAULT_PARAMS", _t1_learner)
check("bot.cro      — classe CRO", _t1_cro)
check("bot.notifier — enviar_alerta, enviar_oportunidade", _t1_notifier)
check("bot.watchdog — quarantine, retry, EMERGENCY_LOCK_PATH", _t1_watchdog)


# ===========================================================================
# TESTE 2 — Estrutura de Pastas
# ===========================================================================
section("Teste 2: Estrutura de Pastas Críticas")

from pathlib import Path


def _check_dir(label: str, path: Path) -> None:
    def fn():
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            print(f"         (criada automaticamente: {path})")
        assert path.is_dir(), f"Não é um directório: {path}"
    check(label, fn)


try:
    from bot.config import DATA_BETA_DIR, LOGS_TRADES_DIR, LOGS_ERRORS_DIR, LOGS_DIR, BASE_DIR

    _check_dir("data/beta/",    DATA_BETA_DIR)
    _check_dir("logs/",         LOGS_DIR)
    _check_dir("logs/trades/",  LOGS_TRADES_DIR)
    _check_dir("logs/errors/",  LOGS_ERRORS_DIR)

    def _t2_beta_trades():
        p = DATA_BETA_DIR / "beta_trades.json"
        assert p.exists(), f"beta_trades.json ausente: {p}"
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "trades" in data, "beta_trades.json não tem chave 'trades'"
    check("data/beta/beta_trades.json — existe e é válido", _t2_beta_trades)

    def _t2_no_lock():
        lock = BASE_DIR / "EMERGENCY_LOCK.txt"
        assert not lock.exists(), (
            f"EMERGENCY_LOCK.txt encontrado — sistema em quarentena!\n"
            f"       Recovery: git rm EMERGENCY_LOCK.txt && git commit && git push"
        )
    check("EMERGENCY_LOCK.txt — ausente (sistema operacional)", _t2_no_lock)

except ImportError as exc:
    print(f"  ❌  bot.config — falha ao importar: {exc}")
    _results.append(("bot.config paths", False, str(exc)))


# ===========================================================================
# TESTE 3 — Fallback de Parâmetros (Learner)
# ===========================================================================
section("Teste 3: Fallback de Parâmetros do Learner")


def _t3_get_active_params():
    from bot.learner import get_active_params, _DEFAULT_PARAMS
    params = get_active_params()
    assert isinstance(params, dict), "devia devolver dict"
    for horizon in ("weekly", "monthly", "quarterly"):
        assert horizon in params, f"horizonte '{horizon}' em falta"
    # Todas as chaves default presentes
    for key in _DEFAULT_PARAMS["weekly"]["clyde"]:
        assert key in params["weekly"]["clyde"], f"clyde.{key} em falta"
    for key in _DEFAULT_PARAMS["monthly"]["bonnie"]:
        assert key in params["monthly"]["bonnie"], f"bonnie.{key} em falta"


def _t3_sanity_rejects_bad_values():
    from bot.learner import _sanity_check, _merge_with_defaults
    bad = _merge_with_defaults({})
    bad["weekly"]["clyde"]["rsi_oversold_ceiling"] = 999   # fora de [28, 45]
    assert not _sanity_check(bad), "devia rejeitar RSI=999"
    bad2 = _merge_with_defaults({})
    bad2["monthly"]["bonnie"]["size_factor_pct"] = -0.5    # negativo
    assert not _sanity_check(bad2), "devia rejeitar size_factor=-0.5"


def _t3_hash_round_trip():
    from bot.learner import _compute_hash, _verify_integrity, _merge_with_defaults
    params = _merge_with_defaults({})
    params.setdefault("_meta", {})
    params["_meta"]["integrity_hash"] = _compute_hash(params)
    assert _verify_integrity(params), "hash válido devia passar"
    # Mutação invalida o hash
    params["weekly"]["clyde"]["rsi_oversold_ceiling"] = 30
    assert not _verify_integrity(params), "hash devia falhar após mutação"


def _t3_merge_preserves_defaults():
    from bot.learner import _merge_with_defaults, _DEFAULT_PARAMS
    # Merge com dict vazio → idêntico aos defaults
    merged = _merge_with_defaults({})
    assert merged["weekly"]["clyde"] == _DEFAULT_PARAMS["weekly"]["clyde"]
    # Merge com valor parcial → só sobrepõe a chave fornecida
    partial = {"weekly": {"clyde": {"rsi_oversold_ceiling": 32}}}
    merged2 = _merge_with_defaults(partial)
    assert merged2["weekly"]["clyde"]["rsi_oversold_ceiling"] == 32
    default_exit = _DEFAULT_PARAMS["weekly"]["clyde"]["rsi_exit_floor"]
    assert merged2["weekly"]["clyde"]["rsi_exit_floor"] == default_exit


check("get_active_params() devolve dict completo (com ou sem JSON)", _t3_get_active_params)
check("_sanity_check() rejeita valores fora de hard bounds",          _t3_sanity_rejects_bad_values)
check("SHA-256 integrity hash — round-trip e detecção de mutação",    _t3_hash_round_trip)
check("_merge_with_defaults() — preserva defaults e sobrepõe parcial",_t3_merge_preserves_defaults)


# ===========================================================================
# TESTE 4 — Simulação de 1 Pulso (Dry-Run Seguro)
# ===========================================================================
section("Teste 4: Simulação de 1 Pulso Dry-Run (sem rede, sem ordens)")

print("  (LIVE_TRADING =", end=" ")
try:
    from bot.config import LIVE_TRADING
    colour = "\033[92m" if not LIVE_TRADING else "\033[91m"
    reset  = "\033[0m"
    print(f"{colour}{LIVE_TRADING}{reset})")
except Exception:
    print("?)")


def _t4_clyde_pipeline():
    """strategy.generate_signals + propose_trades com dados técnicos sintéticos."""
    from bot.strategy import generate_signals, propose_trades
    from bot.data_layer import compute_rsi, compute_ema, compute_atr

    closes  = [b["close"]  for b in _DUMMY_BARS]
    highs   = [b["high"]   for b in _DUMMY_BARS]
    lows    = [b["low"]    for b in _DUMMY_BARS]
    volumes = [b["volume"] for b in _DUMMY_BARS]
    avg_vol = sum(volumes[-20:]) / 20

    ema50  = compute_ema(closes, 50)
    ema200 = compute_ema(closes, 200)

    market_data = {
        "AAPL_US_EQ": {
            "technicals": {
                "rsi_14":              compute_rsi(closes),
                "ema50_above_ema200":  (ema50 > ema200) if (ema50 and ema200) else True,
                "volume_ratio_vs_avg": round(volumes[-1] / avg_vol, 2),
                "atr_14":              compute_atr(highs, lows, closes),
            }
        },
        # Ticker com RSI sobrevendido (forced) para garantir sinal gerado
        "MSFT_US_EQ": {
            "technicals": {
                "rsi_14":              31.0,   # ≤ 35 → Rule A
                "ema50_above_ema200":  True,
                "volume_ratio_vs_avg": 1.5,    # ≥ 1.2 → confirmação
                "atr_14":              3.2,
            }
        },
    }

    signals   = generate_signals(market_data, _DUMMY_PORTFOLIO, regime="bull_trending")
    proposals = propose_trades(signals, _DUMMY_PORTFOLIO, regime="bull_trending")

    assert isinstance(signals,   list), "generate_signals devia devolver list"
    assert isinstance(proposals, list), "propose_trades devia devolver list"

    # Com RSI=31, EMA up, vol=1.5×, deve gerar pelo menos 1 sinal de entrada
    entry_signals = [s for s in signals if s.signal_type == "ENTRY"]
    assert len(entry_signals) >= 1, (
        f"Esperava ≥1 sinal ENTRY com RSI=31 — obteve {len(signals)} sinais"
    )


def _t4_cro_verdict():
    """CRO.interpret() com estado injectado — sem I/O de ficheiros."""
    from bot.cro import CRO

    cro = CRO()
    cro._state = {
        "closed_count":    0,
        "recent_count":    0,
        "wins_7d":         0,
        "win_rate_7d":     0.5,
        "drawdown_pct":    0.0,
        "trades_today":    0,
        "sector_exposure": {},
        "all_closed":      [],
    }
    verdict = cro.interpret(_DUMMY_PORTFOLIO, proposed=None, regime="bull_trending")

    assert verdict.approved,               "CRO devia aprovar em estado limpo"
    assert 0.0 < verdict.risk_factor <= 2, f"risk_factor inválido: {verdict.risk_factor}"
    assert isinstance(verdict.insights, list), "insights devia ser list"


def _t4_watchdog_quarantine_inactive():
    """Watchdog: quarentena inactiva + retry recupera na 2ª tentativa."""
    from bot.watchdog import is_quarantined, retry_on_network_error

    assert not is_quarantined(), (
        "EMERGENCY_LOCK.txt existe — bot em quarentena! "
        "Remove o ficheiro e faz push para reactivar."
    )

    attempts: list[int] = []

    @retry_on_network_error(max_attempts=3, delay=0.01, backoff=1.0)
    def flaky_call():
        attempts.append(1)
        if len(attempts) < 2:
            raise ConnectionError("falha de rede simulada")
        return "recuperado"

    result = flaky_call()
    assert result == "recuperado",   f"Devia ter recuperado, obteve: {result}"
    assert len(attempts) == 2,       f"Devia ter tentado 2×, tentou {len(attempts)}×"


def _t4_learner_skip_with_zero_trades():
    """run_learner_cycle() com 0 trades — deve retornar silenciosamente."""
    from bot.learner import run_learner_cycle
    run_learner_cycle()   # 0 trades < 20 → todos os horizontes fazem skip


def _t4_strategy_params_injected():
    """Verifica que strategy.py carregou params do learner (não magic numbers)."""
    from bot.strategy import _PC, _PB
    from bot.learner import _PARAM_SPACE

    # Todos os valores _PC devem estar dentro dos hard bounds do _PARAM_SPACE
    for key, val in _PC.items():
        spec = _PARAM_SPACE.get(key)
        if spec:
            assert spec["min"] <= float(val) <= spec["max"], (
                f"_PC['{key}'] = {val} fora de bounds [{spec['min']}, {spec['max']}]"
            )
    # size_factor_pct injectado (não hardcoded 0.15 directamente)
    assert "size_factor_pct" in _PB, "size_factor_pct em falta em _PB"


check("Clyde — generate_signals() gera sinal ENTRY com RSI=31 (Rule A)",   _t4_clyde_pipeline)
check("CRO   — interpret() devolve Verdict aprovado com risk_factor válido", _t4_cro_verdict)
check("Watchdog — quarentena inactiva + retry recupera na 2ª tentativa",    _t4_watchdog_quarantine_inactive)
check("Learner — run_learner_cycle() com 0 trades termina em silêncio",     _t4_learner_skip_with_zero_trades)
check("Params — _PC e _PB injectados pelo learner, dentro dos hard bounds", _t4_strategy_params_injected)


# ===========================================================================
# Sumário final
# ===========================================================================
passed = sum(1 for _, ok, _ in _results if ok)
total  = len(_results)
failed = [(lbl, detail) for lbl, ok, detail in _results if not ok]

print(f"\n{'═' * 55}")
print(f"  Resultado: {passed}/{total} testes passaram")

if failed:
    print(f"\n  Problemas encontrados ({len(failed)}):")
    for lbl, detail in failed:
        print(f"    ❌ {lbl}")
        if detail:
            print(f"       {detail}")
    print(f"\n  O sistema tem {len(failed)} problema(s). Ver erros acima.")
else:
    print("  Todas as engrenagens estão oleadas.")
    print("  O sistema está pronto para o GitHub Actions assumir o controlo.")

print(f"{'═' * 55}\n")
sys.exit(0 if not failed else 1)
