"""
Bot Fase 0 — Só leitura, análise técnica e sugestões em texto.
Nenhuma ordem é submetida nesta fase.

Uso: python -m bot.phase0   (a partir da raiz do projecto)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone

from .data_layer import get_full_portfolio_state, enrich_with_technicals, read_beta_trades
from .throttler import WatchlistThrottler
from .logger import log_decision, log_error
from .config import DATA_BETA_DIR, RISK_CONFIG, STRATEGY_VERSION, PHASE1_EXECUTION
from .regime_detector import get_current_regime, load_cached_regime, load_regime_metrics
from .watchlist_manager import build_watchlist
from .strategy import generate_signals, propose_trades, ProposedTrade
from .cro import CRO, _atr_size_eur as _cro_atr_size, _atr_stop_loss_pct as _cro_stop_pct
from .config import CRO_CONFIG
from .execution import execute_trade, execute_exit
from .learner import run_learner_cycle
from . import exit_manager, position_ledger

POSITION_META_PATH    = DATA_BETA_DIR / "position_meta.json"
_LAST_WAKE_PATH       = DATA_BETA_DIR / "last_wake.txt"
SOCIAL_SENTIMENT_PATH = DATA_BETA_DIR / "social_sentiment.json"

_BEAR_REGIMES = {"bear_correction", "bear_capitulation"}
_LATERAL_SIZE_FACTOR = 0.6   # redução de posição sugerida em bull_lateral (secção 4, FASE-1.md)

# Fase 1 — reverse ticker map (yfinance → T212) para os tickers opacos da T212
_YF_TO_T212: dict[str, str] = {
    "MU":   "MTEd",
    "VST":  "49Vd",
    "VRT":  "0V6d",
    "CCJ":  "CJ6d",
    "ASML": "ASMLa",
}
# Fator de alocação por regime: 0.0 bloqueia entradas em bear
_REGIME_ENTRY_FACTOR: dict[str, float] = {
    "bull_trending":     1.0,
    "bull_lateral":      0.6,
    "bear_correction":   0.0,
    "bear_capitulation": 0.0,
}


# ---------------------------------------------------------------------------
# Position meta — persiste style e peak_high por ticker entre ciclos
# ---------------------------------------------------------------------------

def _load_position_meta() -> dict:
    """Carrega {t212_ticker: {style, peak_high, entry_date}} de position_meta.json."""
    if not POSITION_META_PATH.exists():
        return {}
    try:
        with open(POSITION_META_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_position_meta(meta: dict) -> None:
    """Escrita atómica de position_meta.json."""
    tmp = POSITION_META_PATH.with_suffix(".tmp")
    try:
        POSITION_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        tmp.replace(POSITION_META_PATH)
    except OSError as exc:
        log_error("position_meta_save_error", {"error": str(exc)})


def _update_position_peaks(
    positions: list[dict], position_meta: dict, position_styles: dict
) -> None:
    """Actualiza peak_high em position_meta para posições MOMENTUM (in-place)."""
    for pos in positions:
        ticker = pos.get("ticker")
        if ticker and position_styles.get(ticker) == "MOMENTUM":
            t       = pos.get("technicals") or {}
            current = t.get("last_price")
            if current:
                meta = position_meta.setdefault(
                    ticker, {"style": "MOMENTUM", "peak_high": 0.0}
                )
                if current > meta.get("peak_high", 0.0):
                    meta["peak_high"] = current


def _check_momentum_exits(
    positions: list[dict],
    position_meta: dict,
    position_styles: dict,
    position_peaks: dict,
    regime: str,
) -> list:
    """Gera propostas de saída ATR Trailing Stop para posições MOMENTUM detidas."""
    momentum_pos = [
        p for p in positions if position_styles.get(p.get("ticker")) == "MOMENTUM"
    ]
    if not momentum_pos:
        return []

    market_data_held: dict = {
        pos["ticker"]: {"technicals": pos.get("technicals") or {}}
        for pos in momentum_pos
        if pos.get("ticker")
    }
    portfolio = {"positions": positions, "cash": {"free": 0}}
    signals   = generate_signals(
        market_data_held, portfolio, regime=regime,
        position_styles=position_styles, position_peaks=position_peaks,
    )
    exit_signals = [s for s in signals if s.signal_type in ("EXIT", "REDUCE")]
    return propose_trades(exit_signals, portfolio, regime=regime)


def _apply_bonnie_filter(opportunities: list[dict]) -> list[dict]:
    """Filtra buy_opportunities através de bonnie.filter_proposals(). Fail-open."""
    if not opportunities:
        return opportunities
    try:
        from .bonnie import filter_proposals
        from .learner import get_active_params

        bonnie_params = get_active_params().get("monthly", {}).get("bonnie", {})

        proposals = [
            ProposedTrade(
                ticker=opp["ticker"], side="BUY", qty=1,
                order_type="MARKET", price=opp.get("last_price"), reason="",
                context=opp.get("technicals", {}),
                signal_strength=opp.get("signal_strength", 0.5),
                style=opp.get("style", "VALUE"),
            )
            for opp in opportunities
        ]
        market_data = {
            opp["ticker"]: {
                "technicals": opp.get("technicals", {}),
                "last_price": opp.get("last_price"),
            }
            for opp in opportunities
        }

        approved_trades, vetoed = filter_proposals(proposals, market_data, bonnie_params)
        approved_tickers = {t.ticker for t in approved_trades}

        for trade, reason in vetoed:
            log_decision("bonnie_veto", "opportunity_filtered",
                         {"ticker": trade.ticker, "reason": reason})

        return [opp for opp in opportunities if opp["ticker"] in approved_tickers]

    except Exception as exc:
        log_error("bonnie_filter_failed", {"error": str(exc)})
        return opportunities  # fail-open


def _load_social_sentiment() -> dict:
    """Lê social_sentiment.json com failsafe de TTL. Ausente/velho → {} (sem veto).

    O crawler corre num processo separado (cron); se estiver em baixo ou o ficheiro
    for mais velho que o seu próprio ttl_minutes, o bot comporta-se como se não
    houvesse dados sociais — nunca bloqueia por dados obsoletos.
    """
    if not SOCIAL_SENTIMENT_PATH.exists():
        return {}
    try:
        with open(SOCIAL_SENTIMENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    ttl_min = data.get("ttl_minutes", 240)
    age_min = (time.time() - SOCIAL_SENTIMENT_PATH.stat().st_mtime) / 60
    if age_min > ttl_min:
        log_decision("social_sentiment_stale", "ignored",
                     {"age_min": round(age_min, 1), "ttl_min": ttl_min})
        return {}
    return data


def _apply_social_veto(opportunities: list[dict]) -> list[dict]:
    """Remove oportunidades com veto social (pânico Reddit / divergência de analistas).

    Espelha _apply_bonnie_filter: fail-open e loga cada veto. Tickers usam o símbolo
    yfinance, a mesma chave do social_sentiment.json (ambos vêm da watchlist).
    """
    if not opportunities:
        return opportunities
    try:
        social = _load_social_sentiment().get("tickers", {})
        if not social:
            return opportunities

        kept: list[dict] = []
        for opp in opportunities:
            veto = (social.get(opp["ticker"]) or {}).get("veto")
            if veto:
                log_decision("social_veto", "opportunity_filtered",
                             {"ticker": opp["ticker"], "reason": veto})
            else:
                kept.append(opp)
        return kept

    except Exception as exc:
        log_error("social_veto_failed", {"error": str(exc)})
        return opportunities  # fail-open


# ---------------------------------------------------------------------------
# Phase 1 helpers
# ---------------------------------------------------------------------------

def _yf_to_t212(symbol: str) -> str:
    """Converte símbolo yfinance para ticker T212 (inclui tickers opacos)."""
    return _YF_TO_T212.get(symbol, f"{symbol}_US_EQ")


def _fetch_eurusd() -> float:
    """Taxa EUR/USD via yfinance. Fallback conservador 1.12 em caso de erro."""
    try:
        import yfinance as yf
        rate = getattr(yf.Ticker("EURUSD=X").fast_info, "last_price", None)
        return float(rate) if rate else 1.12
    except Exception:
        return 1.12


def _execute_phase1(
    buy_opportunities: list[dict],
    barrier_exits: list,
    signals: list[dict],
    positions: list[dict],
    state: dict,
    regime: str,
    cro_verdict,
    position_meta: dict | None = None,
) -> list[dict]:
    """Executa ordens de compra e venda de forma puramente matemática (Fase 1).

    Ordem de prioridade: (1) saídas urgentes ATR, (2) saídas RSI sobrecomprado,
    (3) entradas da watchlist. Respeita os limites de risco configurados.
    """
    executed: list[dict] = []

    regime_factor = _REGIME_ENTRY_FACTOR.get(regime, 0.0)

    _meta = position_meta if isinstance(position_meta, dict) else {}

    # ── Saídas urgentes (barrier_exits vêm já como ProposedTrade) ─────────
    for be in barrier_exits:
        try:
            result = execute_trade(be, state)
            if result:
                executed.append(result)
                _meta.pop(be.ticker, None)
                log_decision("phase1_exit", "barrier_exit", {
                    "ticker": be.ticker, "reason": be.reason,
                })
        except Exception as exc:
            log_error("phase1_barrier_exit_failed", {"ticker": be.ticker, "error": str(exc)})

    # ── Saídas por RSI sobrecomprado (signals com action==reduce_watch) ───
    position_map: dict[str, dict] = {}
    for p in positions:
        position_map[p.get("ticker", "")] = p
        sym = p.get("price_symbol") or p.get("ticker", "").split("_")[0]
        position_map[sym] = p

    for sig in signals:
        if sig.get("action") != "reduce_watch":
            continue
        ticker = sig["ticker"]
        position = position_map.get(ticker)
        if not position:
            continue
        tech = sig.get("technicals", {})
        rsi  = tech.get("rsi_14")
        pos_for_exit = {**position, "current_price": position.get("currentPrice")}
        reason = f"RSI sobrecomprado ({rsi:.1f})" if rsi else "reduce_watch"
        try:
            result = execute_exit(ticker, pos_for_exit, reason, rsi)
            if result:
                executed.append(result)
                _meta.pop(ticker, None)
                log_decision("phase1_exit", "rsi_overbought", {"ticker": ticker, "rsi": rsi})
        except Exception as exc:
            log_error("phase1_rsi_exit_failed", {"ticker": ticker, "error": str(exc)})

    # ── Entradas — gated por regime e style (CRO authority) ──────────────
    eurusd    = _fetch_eurusd()
    cash_free = state.get("cash", {}).get("free", 0.0)

    equity = cash_free
    for p in positions:
        curr   = p.get("currentPrice", 0.0) or 0.0
        qty    = p.get("quantity", 0.0) or 0.0
        native = curr * qty
        if "_US_" in (p.get("ticker") or ""):
            equity += native / eurusd if eurusd else native
        else:
            equity += native

    if equity <= 0:
        log_error("phase1_no_equity", {"equity": equity})
        return executed

    max_trades    = RISK_CONFIG["max_trades_per_day"]
    min_order_eur = 50.0
    max_pos_pct   = RISK_CONFIG["max_position_pct"] / 100.0

    for opp in buy_opportunities:
        if len(executed) >= max_trades:
            break

        price_usd = opp.get("last_price")
        if not price_usd or price_usd <= 0:
            log_error("phase1_no_price", {"ticker": opp["ticker"]})
            continue

        opp_style = opp.get("style", "VALUE")

        # CRO regime gate: bear bloqueia MOMENTUM; VALUE passa a 0.25× (defensivo)
        eff_regime = regime_factor
        if regime_factor == 0.0:
            if opp_style == "VALUE":
                eff_regime = CRO_CONFIG.get("bear_value_multiplier", 0.25)
            else:
                log_decision("phase1_skip", "bear_momentum_blocked", {
                    "ticker": opp["ticker"], "regime": regime,
                })
                continue

        # ATR-based position sizing (CRO) — equaliza risco financeiro entre activos
        tech    = opp.get("technicals", {})
        atr_14  = tech.get("atr_14") or 0.0
        atr_pct = atr_14 / price_usd if atr_14 > 0 and price_usd > 0 else 0.0

        size_eur = _cro_atr_size(
            atr_pct, equity,
            CRO_CONFIG.get("atr_risk_target_pct", 1.0),
            RISK_CONFIG["max_position_pct"],
            opp_style,
        ) * cro_verdict.risk_factor * eff_regime
        size_eur = min(size_eur, equity * max_pos_pct)

        stop_loss_pct = _cro_stop_pct(atr_pct, opp_style)

        if size_eur < min_order_eur:
            log_decision("phase1_skip_small", "order_below_minimum", {
                "ticker":   opp["ticker"],
                "size_eur": round(size_eur, 2),
                "min_eur":  min_order_eur,
            })
            continue

        price_eur = price_usd / eurusd if eurusd else price_usd
        qty = round(size_eur / price_eur, 4)
        if qty <= 0:
            continue

        t212_ticker = _yf_to_t212(opp["ticker"])
        signal_str  = float(opp.get("signal_strength", 0.5))
        proposed    = ProposedTrade(
            ticker=t212_ticker,
            side="BUY",
            qty=qty,
            order_type="LIMIT",
            price=round(price_usd, 4),
            reason=" | ".join(opp.get("reasons", ["phase1_auto"])),
            context={
                "rsi_14":              tech.get("rsi_14"),
                "atr_14":              tech.get("atr_14"),
                "volume_ratio_vs_avg": tech.get("volume_ratio_vs_avg"),
                "ema20_above_ema50":   tech.get("ema20_above_ema50"),
                "price_above_ema20":   tech.get("price_above_ema20"),
                "regime":              regime,
                "watchlist_score":     opp.get("watchlist_score"),
                "signal_strength":     signal_str,
                "style":               opp_style,
                "stop_loss_pct":       stop_loss_pct,
            },
            signal_strength=signal_str,
            style=opp_style,
        )

        try:
            result = execute_trade(proposed, state)
            if result:
                executed.append(result)
                _meta[t212_ticker] = {
                    "style":      opp_style,
                    "peak_high":  price_usd,
                    "entry_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                log_decision("phase1_entry", "order_placed", {
                    "ticker":         t212_ticker,
                    "qty":            qty,
                    "price":          price_usd,
                    "size_eur":       round(size_eur, 2),
                    "style":          opp_style,
                    "risco_assumido": round(size_eur * stop_loss_pct / 100, 2),
                    "mult_regime":    round(eff_regime, 4),
                    "stop_loss_pct":  round(stop_loss_pct, 2),
                    "atr_pct":        round(atr_pct * 100, 3),
                })
        except Exception as exc:
            log_error("phase1_entry_failed", {"ticker": t212_ticker, "error": str(exc)})

    return executed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(*, git_sync: bool = True) -> dict:
    """Corre a análise Fase 0. Devolve o relatório e guarda-o em data/beta/beta_analysis.json.

    git_sync=False em CI (GitHub Actions) — o workflow YAML trata do commit/push.
    """
    log_decision("phase0_start", "read_portfolio")

    # Regime e watchlist correm sempre
    regime         = _get_regime_safe()
    regime_payload = load_regime_metrics()  # full metrics written by regime_detector
    watchlist      = _get_watchlist_safe()

    # Position meta — persiste style e peak_high por ticker entre ciclos
    position_meta   = _load_position_meta()
    position_styles = {t: m.get("style", "VALUE") for t, m in position_meta.items()}
    position_peaks  = {t: float(m.get("peak_high", 0.0)) for t, m in position_meta.items()}

    # Portfolio: ledger local + Finnhub prices (T212 sync tentado em background)
    state     = get_full_portfolio_state()
    positions = state.get("positions", [])
    cash      = state.get("cash", {})

    sync_status = position_ledger.get_sync_status()
    if positions:
        log_decision("phase0_enrich", "compute_technicals", {"n_positions": len(positions)})
        positions = enrich_with_technicals(positions)

    # Actualizar peak_high para posições MOMENTUM (in-place no position_meta)
    _update_position_peaks(positions, position_meta, position_styles)
    position_peaks = {t: float(m.get("peak_high", 0.0)) for t, m in position_meta.items()}

    # ATR barrier monitor — fires Telegram Whisper and flags break-even updates
    barrier_exits = []
    try:
        barrier_exits = exit_manager.check_exit_barriers(positions)
    except Exception as exc:
        log_error("exit_manager_failed", {"error": str(exc)})

    # MOMENTUM ATR Trailing Stop exits para posições detidas
    try:
        momentum_exits = _check_momentum_exits(
            positions, position_meta, position_styles, position_peaks, regime
        )
        barrier_exits = barrier_exits + momentum_exits
    except Exception as exc:
        log_error("momentum_exit_check_failed", {"error": str(exc)})

    # Tickers já possuídos (símbolo puro, ex: "VRT" e não "VRT_US_EQ")
    held_symbols = {p.get("price_symbol", p.get("ticker", "").split("_")[0]) for p in positions}

    signals                        = _analyse_all(positions, regime)
    buy_opportunities, near_misses = _scan_watchlist_candidates(watchlist, held_symbols, regime)

    # Filtro Bonnie: remove oportunidades que não passam os limiares por estilo
    buy_opportunities = _apply_bonnie_filter(buy_opportunities)

    # Filtro social: veto por pânico (Reddit) ou divergência extrema (analistas)
    buy_opportunities = _apply_social_veto(buy_opportunities)

    risk_status = _risk_snapshot(positions, cash)
    open_trades = _count_open_trades()

    cro         = CRO()
    cro.observe(DATA_BETA_DIR / "beta_trades.json", state)
    cro_verdict = cro.interpret(state, regime=regime)

    # ── Fase 1: execução automática matemática ────────────────────────────────
    executed_trades: list[dict] = []
    if PHASE1_EXECUTION:
        executed_trades = _execute_phase1(
            buy_opportunities, barrier_exits, signals,
            positions, state, regime, cro_verdict,
            position_meta=position_meta,
        )

    _save_position_meta(position_meta)

    report = {
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "mode":               "phase1_auto" if PHASE1_EXECUTION else "phase0_readonly",
        "strategy_version":   STRATEGY_VERSION,
        "note":               (
            "Fase 1 — execução automática activa. LIVE_TRADING=False (conta demo)."
            if PHASE1_EXECUTION else
            "Fase 0 — apenas leitura e sugestão. Nenhuma ordem foi submetida."
        ),
        "regime":             regime,
        "regime_alert":       regime in _BEAR_REGIMES,
        "regime_details":     regime_payload.get("metrics", {}) if regime_payload else {},
        "watchlist_top5":     watchlist[:5],
        "barrier_exits": [
            {"ticker": p.ticker, "reason": p.reason, "qty": p.qty, "price": p.price}
            for p in barrier_exits
        ],
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
        "cro": {
            "risk_factor":  cro_verdict.risk_factor,
            "win_rate_7d":  cro_verdict.win_rate_7d,
            "drawdown_pct": cro_verdict.drawdown_pct,
            "insights":     cro_verdict.insights,
        },
        "executed_trades": executed_trades,
    }

    _save_report(report)
    log_decision("phase0_complete", "report_generated", {
        "regime":    regime,
        "n_signals": len(signals),
        "risk_ok":   risk_status.get("ok", True),
        "positions": _build_positions_context(positions, signals),
    })
    _print_report(report)
    cro.speak()
    _run_learner_safe()
    _notify_opportunities(report)
    if git_sync:
        _git_sync(report["timestamp"])
    return report


# ---------------------------------------------------------------------------
# Safe wrappers — falhas não abortam o ciclo principal
# ---------------------------------------------------------------------------

def _run_learner_safe() -> None:
    """Corre o Learner em modo silencioso — nunca interrompe o ciclo principal."""
    try:
        run_learner_cycle()
    except Exception as exc:
        log_error("learner_cycle_failed", {"error": str(exc)})


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

    Usa WatchlistThrottler para distribuir os fetches ao longo do ciclo em vez
    de disparar todos em paralelo (burst). O Clyde avalia cada ticker logo que
    os dados chegam, sem esperar pelo batch completo. O cursor persiste entre
    ciclos para garantir cobertura uniforme de todos os tickers.
    """
    from .config import LOOP_INTERVAL_SECONDS

    if regime in _BEAR_REGIMES:
        log_decision("watchlist_scan_skipped", "bear_regime_no_entries", {"regime": regime})
        return [], []

    # Todos os não-possuídos — sem cap SCAN_TOP_N (o throttling substitui o limite)
    candidates = [c for c in watchlist if c.get("ticker") not in held_symbols]
    if not candidates:
        return [], []

    tickers    = [c["ticker"] for c in candidates]
    score_map  = {c["ticker"]: c.get("score", 0)   for c in candidates}
    sector_map = {c["ticker"]: c.get("sector", "?") for c in candidates}
    mom1m_map  = {c["ticker"]: c.get("mom_1m", 0)  for c in candidates}
    mom3m_map  = {c["ticker"]: c.get("mom_3m", 0)  for c in candidates}

    log_decision("watchlist_scan_start", "throttled_stream", {
        "n": len(tickers), "regime": regime,
    })

    budget = max(60.0, LOOP_INTERVAL_SECONDS - 60)  # deixa 60 s de folga no ciclo
    throttler = WatchlistThrottler(tickers)

    market_data:    dict[str, dict] = {}
    signal_tickers: set[str]        = set()
    opportunities:  list[dict]      = []

    for ticker, data in throttler.stream(budget_seconds=budget):
        if data is None:
            continue
        market_data[ticker] = data

        # Avaliação imediata — o Clyde não espera pelo batch completo
        sigs = generate_signals({ticker: data}, {"positions": []}, regime)
        for sig in sigs:
            if sig.signal_type != "ENTRY":
                continue
            signal_tickers.add(sig.ticker)
            opportunities.append({
                "ticker":          sig.ticker,
                "sector":          sector_map.get(sig.ticker, "?"),
                "watchlist_score": round(score_map.get(sig.ticker, 0), 4),
                "mom_1m":          round(mom1m_map.get(sig.ticker, 0), 4),
                "mom_3m":          round(mom3m_map.get(sig.ticker, 0), 4),
                "signal_strength": round(sig.strength, 3),
                "style":           sig.style,
                "reasons":         sig.reasons,
                "technicals":      sig.context,
                "last_price":      data.get("last_price"),
            })

    opportunities.sort(key=lambda x: x["signal_strength"], reverse=True)

    # ── Near-misses: todos os tickers acumulados no stream ────────────────────
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

        rule: str | None = None
        blocking: str    = ""
        proximity: float = 0.0

        if 35 < rsi <= 45 and vol_ratio >= 1.0:
            rule      = "A"
            delta     = round(rsi - 35, 1)
            blocking  = f"RSI-14={rsi:.1f} — faltam {delta:.1f} pts para ≤35"
            proximity = max(0.0, 1.0 - (rsi - 35) / 10)

        elif rsi <= 35 and 0.8 <= vol_ratio < 1.2:
            rule      = "A"
            blocking  = f"Volume {vol_ratio:.1f}× — falta {round(1.2 - vol_ratio, 2):.2f}× para ≥1.2"
            proximity = vol_ratio / 1.2

        elif 40 <= rsi <= 55 and 1.3 <= vol_ratio < 1.8:
            rule      = "B"
            blocking  = f"Volume {vol_ratio:.1f}× — falta {round(1.8 - vol_ratio, 2):.2f}× para ≥1.8"
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
        "tickers_fetched": len(market_data),
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

def _wake_already_sent_today(now: datetime) -> bool:
    try:
        return _LAST_WAKE_PATH.read_text(encoding="utf-8").strip() == now.strftime("%Y-%m-%d")
    except OSError:
        return False


def _mark_wake_sent_today(now: datetime) -> None:
    try:
        _LAST_WAKE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_WAKE_PATH.write_text(now.strftime("%Y-%m-%d"), encoding="utf-8")
    except OSError as exc:
        log_error("last_wake_write_failed", {"error": str(exc)})


def _notify_opportunities(report: dict) -> None:
    """Envia alerta sonoro ao Francisco quando o Clyde detecta sinais de entrada."""
    opps = report.get("buy_opportunities", [])
    if not opps:
        return
    try:
        from bot.notifier import enviar_oportunidade
        executed = report.get("executed_trades", [])
        regime   = report.get("regime", "?")
        label    = f"{regime} | Fase 1 — {len(executed)} ordem(ns) enviada(s)" if PHASE1_EXECUTION else f"{regime} | Fase 0 — só leitura"
        enviar_oportunidade(opps, label)
    except Exception as exc:
        log_error("notify_opportunity_failed", {"error": str(exc)})


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

    # Barreiras ATR — saídas urgentes detectadas
    bexits = report.get("barrier_exits", [])
    if bexits:
        print(f"\n🚨 BARREIRAS ATR — {len(bexits)} saída(s) urgente(s):")
        for b in bexits:
            price_str = f" @ ${b['price']:.2f}" if b.get("price") else ""
            print(f"  ⚡ {b['ticker']}: {b['reason']}{price_str}  (qty={b['qty']})")

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

    # Fase 1 — ordens executadas neste ciclo
    executed = report.get("executed_trades", [])
    if executed:
        print(f"\nFase 1 — {len(executed)} ordem(ns) executada(s) neste ciclo:")
        for t in executed:
            side  = t.get("side", "?")
            tkr   = t.get("ticker", "?")
            qty   = t.get("qty", 0)
            price = t.get("price")
            price_str = f" @ ${price:.4f}" if price else ""
            print(f"  · {side} {qty} × {tkr}{price_str}  [{t.get('reason','')[:60]}]")
    elif report.get("mode") == "phase1_auto":
        print("\nFase 1: nenhuma ordem executada neste ciclo (critérios não cumpridos).")

    print(f"\n{report['note']}")
    print(f"{sep}\n")


def _git(root, *args, check: bool = True):
    """Wrapper fino de git com captura de output (cwd=root)."""
    return subprocess.run(
        ["git", *args], cwd=root, check=check, capture_output=True, text=True,
    )


def _rebase_in_progress(root) -> bool:
    """True se há um rebase/merge a meio — estado herdado de um ciclo anterior falhado."""
    git_dir = root / ".git"
    return any((git_dir / m).exists() for m in ("rebase-merge", "rebase-apply", "MERGE_HEAD"))


def _git_sync(timestamp: str) -> None:
    """Commit das alterações e push para origin/main. Salta se nada houver para commitar.

    Defensivo contra rebase encravado: se um ciclo anterior deixou um rebase/merge a
    meio, aborta-o antes de tentar de novo — evita o "already a rebase-merge directory"
    que paralisava todos os ciclos seguintes. Se o rebase deste ciclo entrar em conflito,
    aborta e adia apenas este push (o ciclo seguinte repete), sem nunca deixar a árvore
    de trabalho num estado encravado.
    """
    try:
        root = DATA_BETA_DIR.parent.parent

        # ── Pré-voo: limpa qualquer rebase/merge herdado de um ciclo anterior ──
        if _rebase_in_progress(root):
            log_error("git_sync_rebase_stale", {"action": "abort_inherited"})
            print("Git: rebase/merge a meio detectado — a abortar antes de continuar.")
            _git(root, "rebase", "--abort", check=False)
            _git(root, "merge", "--abort", check=False)

        status = _git(root, "status", "--porcelain", check=False)
        if not status.stdout.strip():
            print("Git: nada para commitar.")
            return

        _git(root, "add", "-A")
        msg = f"Auto-update {timestamp[:16].replace('T', ' ')} UTC"
        _git(root, "commit", "-m", msg)

        # ── Rebase sobre o remoto; em conflito, aborta e adia o push (não encrava) ──
        rebase = _git(root, "pull", "--rebase", "origin", "main", check=False)
        if rebase.returncode != 0:
            log_error("git_sync_rebase_conflict", {"stderr": rebase.stderr[-500:]})
            print(f"Git: conflito no rebase — a abortar e adiar push. {rebase.stderr[-200:]}")
            _git(root, "rebase", "--abort", check=False)
            return

        _git(root, "push", "origin", "main")
        print(f"Git: push efectuado — '{msg}'")
    except FileNotFoundError:
        print("Aviso: Git não encontrado localmente, o sync foi ignorado.")
    except subprocess.CalledProcessError as exc:
        log_error("git_sync_failed", {"error": str(exc), "stderr": getattr(exc, "stderr", "")})
        print(f"Git: erro no push — {exc}")


run_phase0_cycle = run  # alias used by main.py

if __name__ == "__main__":
    import sys
    from bot.watchdog import check_quarantine_and_abort, quarantine

    # ── Gate 1: recusa arranque se quarentena activa ──────────────────────────
    check_quarantine_and_abort()

    parser = argparse.ArgumentParser(description="FundScope Bot — Fase 0")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execução única sem git sync (GitHub Actions CI mode)",
    )
    args = parser.parse_args()
    ci   = args.once or bool(os.getenv("CI"))  # GitHub Actions define CI=true automaticamente

    # Detecção de wake/sleep com base na hora UTC
    now      = datetime.now(timezone.utc)
    is_wake  = ci and now.hour == 13 and now.minute < 45   # primeiro ciclo do dia (janela alargada para absorver atrasos do GH Actions)
    is_sleep = ci and now.hour == 21                        # último ciclo do dia

    # ── Gate 2: handler global — qualquer excepção não tratada activa quarentena
    try:
        report = run(git_sync=not ci)

        if ci:
            from bot.notifier import enviar_despertar, enviar_boa_noite
            if is_wake and not _wake_already_sent_today(now):
                enviar_despertar(report)
                _mark_wake_sent_today(now)
            if is_sleep:
                enviar_boa_noite(report)

    except Exception as _fatal_exc:
        quarantine(_fatal_exc, context="phase0.run()")
        sys.exit(1)
