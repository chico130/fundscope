"""
Bot Fase 0 — Só leitura, análise técnica e sugestões em texto.
Nenhuma ordem é submetida nesta fase.

Uso: python -m bot.phase0   (a partir da raiz do projecto)
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone

from .data_layer import get_full_portfolio_state, enrich_with_technicals, read_beta_trades, fetch_candidate_market_data
from .logger import log_decision, log_error
from .config import DATA_BETA_DIR, RISK_CONFIG, STRATEGY_VERSION
from .regime_detector import get_current_regime, load_cached_regime, load_regime_metrics
from .watchlist_manager import build_watchlist
from .strategy import generate_signals
from . import position_ledger

_BEAR_REGIMES = {"bear_correction", "bear_capitulation"}
_LATERAL_SIZE_FACTOR = 0.6   # redução de posição sugerida em bull_lateral (secção 4, FASE-1.md)
_WATCHLIST_CANDIDATES_TO_SCAN = 10  # max candidatos da watchlist a analisar por ciclo


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> dict:
    """Corre a análise Fase 0. Devolve o relatório e guarda-o em data/beta/beta_analysis.json."""
    log_decision("phase0_start", "read_portfolio")

    # Regime e watchlist correm sempre
    regime         = _get_regime_safe()
    regime_payload = load_regime_metrics()  # full metrics written by regime_detector
    watchlist      = _get_watchlist_safe()

    # Portfolio: ledger local + Finnhub prices (T212 sync tentado em background)
    state     = get_full_portfolio_state()
    positions = state.get("positions", [])
    cash      = state.get("cash", {})

    sync_status = position_ledger.get_sync_status()
    if positions:
        log_decision("phase0_enrich", "compute_technicals", {"n_positions": len(positions)})
        positions = enrich_with_technicals(positions)

    # Tickers já possuídos (símbolo puro, ex: "VRT" e não "VRT_US_EQ")
    held_symbols = {p.get("price_symbol", p.get("ticker", "").split("_")[0]) for p in positions}

    signals                      = _analyse_all(positions, regime)
    buy_opportunities, near_misses = _scan_watchlist_candidates(watchlist, held_symbols, regime)
    risk_status                  = _risk_snapshot(positions, cash)
    open_trades     = _count_open_trades()

    report = {
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "mode":               "phase0_readonly",
        "strategy_version":   STRATEGY_VERSION,
        "note":               "Fase 0 — apenas leitura e sugestão. Nenhuma ordem foi submetida.",
        "regime":             regime,
        "regime_alert":       regime in _BEAR_REGIMES,
        "regime_details":     regime_payload.get("metrics", {}) if regime_payload else {},
        "watchlist_top5":     watchlist[:5],
        "buy_opportunities":  buy_opportunities,
        "near_misses":        near_misses,
        "n_positions":        len(positions),
        "open_trades":        open_trades,
        "risk_status":        risk_status,
        "signals":            signals,
        "data_sources": {
            "prices":       "finnhub+yfinance",
            "t212_sync":    sync_status.get("last_t212_sync"),
            "stale_prices": [p["ticker"] for p in positions if p.get("price_stale")],
        },
    }

    _save_report(report)
    log_decision("phase0_complete", "report_generated", {
        "regime":    regime,
        "n_signals": len(signals),
        "risk_ok":   risk_status.get("ok", True),
        "positions": _build_positions_context(positions, signals),
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


def _scan_watchlist_candidates(
    watchlist: list[dict], held_symbols: set[str], regime: str
) -> tuple[list[dict], list[dict]]:
    """Gera sinais de entrada e near-misses para candidatos da watchlist não possuídos.

    Retorna (opportunities, near_misses) — ambas ordenadas por relevância.
    Em regimes Bear, retorna ([], []) pois entradas estão bloqueadas.
    """
    if regime in _BEAR_REGIMES:
        log_decision("watchlist_scan_skipped", "bear_regime_no_entries", {"regime": regime})
        return [], []

    candidates = [c for c in watchlist if c.get("ticker") not in held_symbols]
    candidates = candidates[:_WATCHLIST_CANDIDATES_TO_SCAN]

    if not candidates:
        return [], []

    tickers = [c["ticker"] for c in candidates]
    log_decision("watchlist_scan_start", "fetching_technicals", {"n": len(tickers), "regime": regime})

    try:
        market_data = fetch_candidate_market_data(tickers)
    except Exception as exc:
        log_error("watchlist_scan_failed", {"error": str(exc)})
        return [], []

    if not market_data:
        return [], []

    signals = generate_signals(market_data, {"positions": []}, regime)

    score_map  = {c["ticker"]: c.get("score", 0)   for c in candidates}
    sector_map = {c["ticker"]: c.get("sector", "?") for c in candidates}
    mom1m_map  = {c["ticker"]: c.get("mom_1m", 0)  for c in candidates}
    mom3m_map  = {c["ticker"]: c.get("mom_3m", 0)  for c in candidates}

    # ── Oportunidades de entrada ───────────────────────────────────────────────
    signal_tickers: set[str] = set()
    opportunities: list[dict] = []
    for sig in signals:
        if sig.signal_type != "ENTRY":
            continue
        signal_tickers.add(sig.ticker)
        md = market_data.get(sig.ticker, {})
        opportunities.append({
            "ticker":          sig.ticker,
            "sector":          sector_map.get(sig.ticker, "?"),
            "watchlist_score": round(score_map.get(sig.ticker, 0), 4),
            "mom_1m":          round(mom1m_map.get(sig.ticker, 0), 4),
            "mom_3m":          round(mom3m_map.get(sig.ticker, 0), 4),
            "signal_strength": round(sig.strength, 3),
            "reasons":         sig.reasons,
            "technicals":      sig.context,
            "last_price":      md.get("last_price"),
        })
    opportunities.sort(key=lambda x: x["signal_strength"], reverse=True)

    # ── Near-misses: candidatos que quase ativaram sinal ──────────────────────
    near_misses: list[dict] = []
    for ticker, md in market_data.items():
        if ticker in signal_tickers:
            continue
        tech      = md.get("technicals", {})
        rsi       = tech.get("rsi_14")
        ema_above = tech.get("ema50_above_ema200")
        vol_ratio = tech.get("volume_ratio_vs_avg") or 1.0

        if rsi is None or not ema_above:
            # A tendência tem de ser ascendente para ser "quase lá"
            continue

        rule: str | None    = None
        blocking: str       = ""
        proximity: float    = 0.0

        if 35 < rsi <= 45 and vol_ratio >= 1.0:
            # Rule A near-miss: RSI ligeiramente acima do limiar ≤35
            rule     = "A"
            delta    = round(rsi - 35, 1)
            blocking = f"RSI-14={rsi:.1f} — faltam {delta:.1f} pts para ≤35"
            proximity = max(0.0, 1.0 - (rsi - 35) / 10)

        elif rsi <= 35 and 0.8 <= vol_ratio < 1.2:
            # Rule A': RSI ok, volume insuficiente
            rule     = "A"
            blocking = f"Volume {vol_ratio:.1f}× — falta {round(1.2 - vol_ratio, 2):.2f}× para ≥1.2"
            proximity = vol_ratio / 1.2

        elif 40 <= rsi <= 55 and 1.3 <= vol_ratio < 1.8:
            # Rule B near-miss: momentum mas volume ainda abaixo de 1.8×
            rule     = "B"
            blocking = f"Volume {vol_ratio:.1f}× — falta {round(1.8 - vol_ratio, 2):.2f}× para ≥1.8"
            proximity = vol_ratio / 1.8

        if rule:
            near_misses.append({
                "ticker":          ticker,
                "sector":          sector_map.get(ticker, "?"),
                "watchlist_score": round(score_map.get(ticker, 0), 4),
                "rule":            rule,
                "blocking_reason": blocking,
                "proximity_pct":   round(proximity * 100),
                "technicals":      tech,
                "last_price":      md.get("last_price"),
            })

    near_misses.sort(key=lambda x: x["proximity_pct"], reverse=True)
    near_misses = near_misses[:5]

    log_decision("watchlist_scan_done", "results", {
        "opportunities": len(opportunities),
        "near_misses":   len(near_misses),
        "regime":        regime,
    })
    return opportunities, near_misses


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


def _build_positions_context(positions: list[dict], signals: list[dict]) -> list[dict]:
    """Builds the per-position technical context array for log_decision."""
    signal_by_ticker = {s["ticker"]: s.get("action", "hold") for s in signals}
    result = []
    for pos in positions:
        ticker = pos.get("ticker", "?")
        t = pos.get("technicals") or {}
        md = pos.get("market_data") or {}
        price = md.get("last_price") or pos.get("currentPrice") or pos.get("averagePrice")
        result.append({
            "ticker":               ticker,
            "rsi_14":               t.get("rsi_14"),
            "ema50_above_ema200":   t.get("ema50_above_ema200"),
            "volume_ratio_vs_avg":  t.get("volume_ratio_vs_avg"),
            "signal":               signal_by_ticker.get(ticker, "hold"),
            "price":                round(price, 4) if price is not None else None,
            "ema_50":               t.get("ema50"),
            "ema_200":              t.get("ema200"),
        })
    return result


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
    return sum(1 for t in data.get("trades", []) if not t.get("closed_at"))


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

    ds = report.get("data_sources", {})
    t212_sync = ds.get("t212_sync") or "sem sync"
    stale = ds.get("stale_prices", [])
    print(f"Precos: {ds.get('prices', '?')} | T212 sync: {t212_sync[:16] if t212_sync != 'sem sync' else t212_sync}")
    if stale:
        print(f"  ! Precos indisponiveis: {', '.join(stale)}")

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

    # Oportunidades de compra (candidatos da watchlist com sinal de entrada)
    opps = report.get("buy_opportunities", [])
    if opps:
        print(f"\nOportunidades de compra detectadas ({len(opps)}):")
        for i, o in enumerate(opps, 1):
            tech = o.get("technicals", {})
            price_str = f"  ${o['last_price']:.2f}" if o.get("last_price") else ""
            print(
                f"\n  {i}. {o['ticker']:<6} [{o['sector']}]{price_str}  "
                f"forca={o['signal_strength']:.2f}  score_watchlist={o['watchlist_score']:.3f}"
            )
            for r in o["reasons"]:
                print(f"     · {r}")
            rsi = tech.get("rsi_14")
            vr  = tech.get("volume_ratio_vs_avg")
            if rsi is not None:
                print(f"     RSI-14={rsi:.1f}  vol_ratio={vr:.1f}x" if vr else f"     RSI-14={rsi:.1f}")
    elif report["regime"] not in _BEAR_REGIMES:
        print("\nOportunidades de compra: nenhum candidato da watchlist cumpre os critérios de entrada.")

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
            cwd=root, capture_output=True, text=True, shell=True,
        )
        if not status.stdout.strip():
            print("Git: nada para commitar.")
            return

        subprocess.run(["git", "add", "-A"], cwd=root, check=True, shell=True)
        msg = f"Auto-update {timestamp[:16].replace('T', ' ')} UTC"
        subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True, shell=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=root, check=True, shell=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=root, check=True, shell=True)
        print(f"Git: push efectuado — '{msg}'")
    except FileNotFoundError:
        print("Aviso: Git não encontrado localmente, o sync foi ignorado.")
    except subprocess.CalledProcessError as exc:
        log_error("git_sync_failed", {"error": str(exc)})
        print(f"Git: erro no push — {exc}")


run_phase0_cycle = run  # alias used by main.py

if __name__ == "__main__":
    run()
