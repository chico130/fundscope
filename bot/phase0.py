"""
Bot Fase 0 — Só leitura, análise técnica e sugestões em texto.
Nenhuma ordem é submetida nesta fase.

Uso: python -m bot.phase0   (a partir da raiz do projecto)
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from .data_layer import get_full_portfolio_state, enrich_with_technicals, read_beta_trades
from .logger import log_decision, log_error
from .config import DATA_BETA_DIR, RISK_CONFIG, STRATEGY_VERSION
from .regime_detector import get_current_regime, load_cached_regime
from .watchlist_manager import build_watchlist

_BEAR_REGIMES = {"bear_correction", "bear_capitulation"}
_LATERAL_SIZE_FACTOR = 0.6   # redução de posição sugerida em bull_lateral (secção 4, FASE-1.md)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> dict:
    """Corre a análise Fase 0. Devolve o relatório e guarda-o em data/beta/beta_analysis.json."""
    log_decision("phase0_start", "read_portfolio")

    # Regime e watchlist correm sempre — mesmo sem portfolio T212
    regime    = _get_regime_safe()
    watchlist = _get_watchlist_safe()

    # Portfolio T212 é opcional: bot corre "a frio" se a API estiver indisponível
    state = get_full_portfolio_state()
    positions: list[dict] = []
    cash: dict = {}

    if state is None:
        log_decision("phase0_t212_unavailable", "cold_run",
                     {"reason": "T212 API indisponível — análise sem portfolio"})
    else:
        positions = state.get("positions", [])
        cash      = state.get("cash", {})
        log_decision("phase0_enrich", "compute_technicals", {"n_positions": len(positions)})
        positions = enrich_with_technicals(positions)

    signals      = _analyse_all(positions, regime)
    risk_status  = _risk_snapshot(positions, cash)
    open_trades  = _count_open_trades()

    report = {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "mode":             "phase0_readonly",
        "strategy_version": STRATEGY_VERSION,
        "note":             "Fase 0 — apenas leitura e sugestão. Nenhuma ordem foi submetida.",
        "regime":           regime,
        "regime_alert":     regime in _BEAR_REGIMES,
        "watchlist_top5":   watchlist[:5],
        "n_positions":      len(positions),
        "open_trades":      open_trades,
        "risk_status":      risk_status,
        "signals":          signals,
    }

    _save_report(report)
    log_decision("phase0_complete", "report_generated", {
        "regime":    regime,
        "n_signals": len(signals),
        "risk_ok":   risk_status.get("ok", True),
    })
    _print_report(report)
    _git_sync(report["timestamp"])
    return report


# ---------------------------------------------------------------------------
# Safe wrappers — falhas não abortam o ciclo principal
# ---------------------------------------------------------------------------

def _get_regime_safe() -> str:
    try:
        return get_current_regime()
    except Exception as exc:
        log_error("regime_detection_failed", {"error": str(exc)})
        cached = load_cached_regime()
        if cached:
            log_decision("regime_fallback", "using_cached", {"regime": cached})
            return cached
        return "bull_lateral"   # fallback conservador


def _get_watchlist_safe() -> list[dict]:
    try:
        return build_watchlist()
    except Exception as exc:
        log_error("watchlist_build_failed", {"error": str(exc)})
        return []


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _analyse_all(positions: list[dict], regime: str) -> list[dict]:
    """Analisa posições abertas e aplica filtros de regime."""
    in_bear    = regime in _BEAR_REGIMES
    size_factor = _LATERAL_SIZE_FACTOR if regime == "bull_lateral" else 1.0

    results = []
    for pos in positions:
        ticker = pos.get("ticker", "?")
        t      = pos.get("technicals")

        if t is None:
            results.append({
                "ticker":      ticker,
                "signals":     ["Dados históricos insuficientes para análise técnica"],
                "action":      "watch",
                "size_factor": size_factor,
            })
            continue

        signals: list[str] = []
        action = "hold"

        rsi         = t.get("rsi_14")
        ema50_above = t.get("ema50_above_ema200")
        vol_ratio   = t.get("volume_ratio_vs_avg")

        if rsi is not None:
            if rsi >= 75:
                signals.append(f"RSI-14 sobrecomprado ({rsi:.1f}) — risco de correcção")
                action = "reduce_watch"
            elif rsi >= 65:
                signals.append(f"RSI-14 elevado ({rsi:.1f}) — monitorizar pressão de venda")
            elif rsi <= 25:
                signals.append(f"RSI-14 sobrevendido ({rsi:.1f}) — possível oportunidade de entrada")
                action = "entry_watch"
            elif rsi <= 35:
                signals.append(f"RSI-14 baixo ({rsi:.1f}) — zona de suporte, aguardar confirmação")
            else:
                signals.append(f"RSI-14 neutro ({rsi:.1f})")

        if ema50_above is True:
            signals.append("Tendência ascendente: EMA-50 > EMA-200")
        elif ema50_above is False:
            signals.append("Tendência descendente: EMA-50 < EMA-200 — cautela")
            if action == "hold":
                action = "caution"

        if vol_ratio is not None:
            if vol_ratio >= 2.0:
                signals.append(f"Volume excepcional: {vol_ratio:.1f}x acima da média — sinal de confirmação")
            elif vol_ratio >= 1.5:
                signals.append(f"Volume elevado: {vol_ratio:.1f}x acima da média")
            elif vol_ratio < 0.5:
                signals.append(f"Volume reduzido: {vol_ratio:.1f}x da média — movimento sem convicção")

        # Filtro de regime: bloquear entradas em mercado adverso
        if in_bear and action == "entry_watch":
            action = "hold"
            signals.append(f"ENTRY bloqueado — regime {regime} não permite novas entradas")

        results.append({
            "ticker":      ticker,
            "action":      action,
            "size_factor": size_factor,
            "signals":     signals,
            "technicals":  {
                "rsi_14":             rsi,
                "ema50_above_ema200": ema50_above,
                "volume_ratio_vs_avg": vol_ratio,
            },
        })
    return results


def _risk_snapshot(positions: list[dict], cash: dict) -> dict:
    cash_free = cash.get("free") or 0
    equity    = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + cash_free

    warnings: list[str] = []
    max_pos = RISK_CONFIG["max_position_pct"]

    for pos in positions:
        val = pos.get("value", pos.get("value_eur", 0))
        pct = (val / equity * 100) if equity else 0
        if pct > max_pos:
            warnings.append(
                f"{pos.get('ticker', '?')} representa {pct:.1f}% da carteira "
                f"(limite: {max_pos}%)"
            )

    return {
        "ok":               len(warnings) == 0,
        "total_equity_eur": round(equity, 2),
        "warnings":         warnings,
    }


def _count_open_trades() -> int:
    data = read_beta_trades()
    if not data:
        return 0
    return sum(1 for t in data.get("trades", []) if t.get("closed_at") is None)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _save_report(report: dict) -> None:
    path = DATA_BETA_DIR / "beta_analysis.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        log_error("phase0_save_error", {"path": str(path), "error": str(exc)})


def _print_report(report: dict) -> None:
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"FundScope Bot — Fase 0 — {report['timestamp']}")
    print(f"Estrategia: {report['strategy_version']} | Posicoes: {report['n_positions']} | Trades abertos: {report['open_trades']}")

    # Regime
    regime = report["regime"]
    regime_label = f"[ALERTA] {regime.upper()}" if report["regime_alert"] else regime
    print(f"\nRegime de mercado: {regime_label}")
    if report["regime_alert"]:
        print("  Novas entradas BLOQUEADAS neste regime.")
    elif regime == "bull_lateral":
        print(f"  Tamanho de posicao sugerido reduzido para {int(_LATERAL_SIZE_FACTOR * 100)}% do normal.")

    # Watchlist top 5
    wl = report.get("watchlist_top5", [])
    if wl:
        print(f"\nTop 5 Watchlist (score composto):")
        for i, c in enumerate(wl, 1):
            print(
                f"  {i}. {c['ticker']:<6} [{c['sector']}]  "
                f"score={c['score']:.3f}  "
                f"mom1M={c['mom_1m']:+.1%}  "
                f"mom3M={c['mom_3m']:+.1%}"
            )
    else:
        print("\nWatchlist: sem dados disponíveis.")

    # Risco
    rs = report["risk_status"]
    risk_label = "OK" if rs["ok"] else "AVISO"
    print(f"\nRisco: {risk_label} | Equity total: {rs['total_equity_eur']:.2f}EUR")
    for w in rs["warnings"]:
        print(f"  ! {w}")

    # Sinais por posicao
    signals_list = report["signals"]
    if signals_list:
        print(f"\nAnalise de sinais ({len(signals_list)} posicoes):")
        for s in signals_list:
            sf = s.get("size_factor", 1.0)
            sf_note = f" [size x{sf}]" if sf != 1.0 else ""
            print(f"\n  {s['ticker']} — accao sugerida: {s['action']}{sf_note}")
            for sig in s["signals"]:
                print(f"    · {sig}")
    else:
        print("\nSem posicoes abertas para analisar.")

    print(f"\n{report['note']}")
    print(f"{sep}\n")


def _git_sync(timestamp: str) -> None:
    """Commit all changes and push to origin/main. Skips if nothing to commit."""
    try:
        root   = DATA_BETA_DIR.parent.parent
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root, capture_output=True, text=True,
        )
        if not status.stdout.strip():
            print("Git: nada para commitar.")
            return

        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        msg = f"Auto-update {timestamp[:16].replace('T', ' ')} UTC"
        subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=root, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=root, check=True)
        print(f"Git: push efectuado — '{msg}'")
    except subprocess.CalledProcessError as exc:
        log_error("git_sync_failed", {"error": str(exc)})
        print(f"Git: erro no push — {exc}")


if __name__ == "__main__":
    run()
