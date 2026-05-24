"""
CRO — Chief Risk Officer (Fase 0: Observação e Narrativa)

Cadeia de comando:
  Clyde propõe → Bonnie audita qualidade → CRO dita alocação + narrativa

Fase 0: observe() lê beta_trades.json, interpret() gera lições autodidatas,
        speak() persiste cro_insights.json e envia narrativa via Telegram (Whisper).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import CRO_CONFIG, DATA_BETA_DIR, RISK_CONFIG
from .logger import log_decision, log_error


# ---------------------------------------------------------------------------
# Tipos de dados
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    """Resultado do interpret() — aprovação, métricas e lições autodidatas."""
    approved:          bool
    final_size_eur:    float
    risk_factor:       float
    reason:            str
    insights:          list[str] = field(default_factory=list)
    win_rate_7d:       float     = 0.0
    drawdown_pct:      float     = 0.0
    elastic_target_wr: float     = 0.48  # alvo dinâmico calculado neste ciclo
    bonnie_threshold:  float     = 0.60  # threshold Bonnie ditado pelo CRO
    regime_multiplier: float     = 1.0   # multiplicador de regime aplicado (0.0–1.0)
    stop_loss_pct:     float     = 5.0   # stop loss em % do preço de entrada (ATR-based)
    atr_pct:           float     = 0.0   # ATR como % do preço (0 = não disponível)
    assumed_risk_eur:  float     = 0.0   # risco assumido em EUR se stop for atingido


# ---------------------------------------------------------------------------
# CRO
# ---------------------------------------------------------------------------

class CRO:
    """Chief Risk Officer — valida alocação dinâmica e gera narrativa cognitiva."""

    def __init__(self) -> None:
        self._state:       dict      = {}
        self._insights:    list[str] = []
        self._risk_factor: float     = 1.0
        self._regime:      str       = "bull_lateral"

    # ------------------------------------------------------------------
    # 1. Observe — lê trades fechados e estado do portfólio
    # ------------------------------------------------------------------

    def observe(
        self,
        beta_trades_path: Path | None = None,
        portfolio_state:  dict | None = None,
    ) -> None:
        """Constrói estado interno a partir de beta_trades.json + portfólio actual."""
        path   = beta_trades_path or (DATA_BETA_DIR / "beta_trades.json")
        trades = _load_beta_trades(path)
        closed = [t for t in trades if t.get("closed_at") and t.get("result_eur") is not None]

        now    = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=7)

        recent   = [t for t in closed if _parse_dt(t.get("closed_at", "")) >= cutoff]
        wins_7d  = sum(1 for t in recent if (t.get("result_eur") or 0) > 0)
        win_rate = wins_7d / len(recent) if recent else 0.5

        drawdown = _max_drawdown(_build_cumulative(closed))

        today_str    = now.strftime("%Y-%m-%d")
        trades_today = sum(
            1 for t in trades
            if t.get("datetime", "").startswith(today_str) and t.get("side") == "BUY"
        )

        positions = (portfolio_state or {}).get("positions", [])

        self._state = {
            "closed_count":    len(closed),
            "recent_count":    len(recent),
            "wins_7d":         wins_7d,
            "win_rate_7d":     round(win_rate, 4),
            "drawdown_pct":    drawdown,
            "trades_today":    trades_today,
            "sector_exposure": _sector_exposure(positions),
            "all_closed":      closed,
        }

        log_decision("cro_observe", "state_built", {
            "closed":       len(closed),
            "win_rate_7d":  round(win_rate, 4),
            "drawdown_pct": drawdown,
        })

    # ------------------------------------------------------------------
    # 2. Interpret — calcula risk_factor, gera lições, valida proposta
    # ------------------------------------------------------------------

    def interpret(
        self,
        portfolio_state: dict,
        proposed:        "ProposedTrade | None" = None,
        regime:          str = "bull_trending",
    ) -> Verdict:
        """Gera lições autodidatas e, opcionalmente, valida uma proposta de trade.

        Fase 0: proposed=None → só narrativa, sem bloqueio de ordens.
        Fase 1+: proposed != None → também valida alocação e sector.
        """
        if not self._state:
            self.observe(portfolio_state=portfolio_state)

        win_rate_7d  = self._state["win_rate_7d"]
        drawdown_pct = self._state["drawdown_pct"]
        trades_today = self._state["trades_today"]
        all_closed   = self._state.get("all_closed", [])

        max_dd = CRO_CONFIG["max_drawdown_limit_pct"]

        # Janela Deslizante Adaptativa — alvo elástico
        elastic_target = _elastic_target_wr(all_closed)
        bonnie_thr     = _dynamic_bonnie_threshold(all_closed)

        # Factores de desempenho (wr_adj × dd_adj)
        wr_adj = max(0.5, min(1.2, win_rate_7d / elastic_target if elastic_target > 0 else 1.0))
        dd_adj = max(0.3, min(1.0, 1.0 - drawdown_pct / max_dd))

        # Multiplicador de Regime — autoridade exclusiva CRO
        proposed_style = (proposed.style if proposed is not None else None) or "MOMENTUM"
        regime_mults   = CRO_CONFIG.get("regime_multiplier", {})
        reg_factor     = regime_mults.get(regime, 1.0)
        if reg_factor == 0.0 and proposed_style == "VALUE":
            reg_factor = CRO_CONFIG.get("bear_value_multiplier", 0.25)

        risk_factor = round(wr_adj * dd_adj * reg_factor, 4)

        # ATR-based Stop Loss dinâmico (por proposta, quando disponível)
        atr_pct       = 0.0
        stop_loss_pct = CRO_CONFIG.get("atr_fallback_stop_pct", 5.0)
        if proposed is not None:
            ctx    = proposed.context or {}
            atr_14 = ctx.get("atr_14") or 0.0
            price  = proposed.price   or 0.0
            if atr_14 > 0 and price > 0:
                atr_pct       = atr_14 / price
                stop_loss_pct = _atr_stop_loss_pct(atr_pct, proposed_style)

        insights = _generate_insights(
            win_rate_7d, drawdown_pct, risk_factor,
            round(RISK_CONFIG["max_position_pct"] * risk_factor, 2),
            elastic_target, max_dd, regime,
            self._state.get("sector_exposure", {}),
            all_closed, bonnie_thr,
            regime_mult=reg_factor, stop_loss_pct=stop_loss_pct,
        )

        approved         = True
        reason           = "advisory_only"
        final_size_eur   = 0.0
        assumed_risk_eur = 0.0

        if proposed is not None:
            approved, reason, final_size_eur, assumed_risk_eur = _validate_proposal(
                proposed, portfolio_state, risk_factor, trades_today,
                atr_pct=atr_pct, regime_mult=reg_factor,
                all_closed=all_closed,
            )
            if not approved:
                insights.insert(0, f"VETO CRO: {reason} — proposta bloqueada para {proposed.ticker}.")

        self._insights    = insights
        self._risk_factor = risk_factor
        self._regime      = regime

        log_decision("cro_interpret", "risk_verdict", {
            "regime":            regime,
            "regime_multiplier": round(reg_factor, 4),
            "risk_factor":       risk_factor,
            "stop_loss_pct":     round(stop_loss_pct, 2),
            "atr_pct":           round(atr_pct * 100, 3),
            "assumed_risk_eur":  round(assumed_risk_eur, 2),
            "ticker":            proposed.ticker if proposed is not None else None,
        })

        return Verdict(
            approved=approved,
            final_size_eur=final_size_eur,
            risk_factor=risk_factor,
            reason=reason,
            insights=insights,
            win_rate_7d=win_rate_7d,
            drawdown_pct=drawdown_pct,
            elastic_target_wr=elastic_target,
            bonnie_threshold=bonnie_thr,
            regime_multiplier=reg_factor,
            stop_loss_pct=stop_loss_pct,
            atr_pct=atr_pct,
            assumed_risk_eur=assumed_risk_eur,
        )

    # ------------------------------------------------------------------
    # 3. Speak — escreve cro_insights.json e envia para Telegram
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 4. Analyze Gains — produz snapshot para a UI (data/gains_analysis.json)
    # ------------------------------------------------------------------

    def analyze_gains(self, beta_trades_path: Path | None = None) -> dict:
        """Gera análise consolidada de gains para a aba Gains do portfolio.html.

        Consome beta_trades.json (fonte canónica dos trades fechados) e produz
        o schema descrito em data/gains_analysis.json. Não escreve ficheiro —
        o caller decide quando persistir (update_portfolio.py faz isso após
        detectar uma nova posição fechada).
        """
        path   = beta_trades_path or (DATA_BETA_DIR / "beta_trades.json")
        trades = _load_beta_trades(path)
        closed = [t for t in trades if t.get("closed_at") and t.get("result_eur") is not None]
        closed_sorted = sorted(closed, key=lambda x: x.get("closed_at", ""))

        summary           = _gains_summary(closed_sorted)
        patterns          = _gains_patterns(closed_sorted)
        recurring_errors  = _gains_recurring_errors(closed_sorted)
        sector_perf       = _gains_sector_performance(closed_sorted)
        cro_verdict       = _gains_cro_verdict(summary, sector_perf)

        return {
            "generated_at":           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trades_analysed":        len(closed_sorted),
            "last_closed_trade_id":   closed_sorted[-1].get("id") if closed_sorted else None,
            "summary":                summary,
            "patterns":               patterns,
            "recurring_errors":       recurring_errors,
            "sector_performance":     sector_perf,
            "cro_verdict":            cro_verdict,
        }

    # ------------------------------------------------------------------
    # 5. Speak — escreve cro_insights.json e envia para Telegram
    # ------------------------------------------------------------------

    def speak(self) -> None:
        """Persiste cro_insights.json e envia narrativa cognitiva via Whisper (Telegram).

        O _whisper() só é chamado UMA vez por dia — guard baseado na data do ficheiro
        existente antes de o sobreescrever.
        """
        if not self._state:
            log_error("cro_speak", {"error": "observe() não foi chamado antes de speak()"})
            return

        today              = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        already_whispered  = _already_whispered_today(today)

        payload = {
            "generated_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "regime":            self._regime,
            "risk_factor":       self._risk_factor,
            "win_rate_7d":       self._state.get("win_rate_7d", 0.0),
            "drawdown_atual":    self._state.get("drawdown_pct", 0.0),
            "trades_analisados": self._state.get("closed_count", 0),
            "trades_recentes":   self._state.get("recent_count", 0),
            "insights":          self._insights,
        }

        _write_insights(payload)
        if self._insights and not already_whispered:
            _whisper(payload)


# ---------------------------------------------------------------------------
# Helpers — funções puras
# ---------------------------------------------------------------------------

def _elastic_target_wr(closed: list[dict]) -> float:
    """
    Alvo de win rate dinâmico: média dos últimos N trades fechados.
    Zero look-ahead — olha apenas para o histórico disponível até este momento.
    Com < N trades usa o fallback como base temporária.
    """
    cfg      = CRO_CONFIG
    n        = cfg.get("elastic_window_n", 25)
    fallback = cfg.get("elastic_fallback_wr", 0.48)
    if len(closed) < n:
        return fallback
    recent = sorted(closed, key=lambda x: x.get("closed_at", ""))[-n:]
    wins   = sum(1 for t in recent if (t.get("result_eur") or 0) > 0)
    return round(wins / n, 4)


def _dynamic_bonnie_threshold(closed: list[dict]) -> float:
    """
    CRO controla o threshold de veto da Bonnie baseado na WR rolante.
    WR(N) < 45%  →  threshold 64%  (filtragem selectiva — mercado adverso)
    WR(N) ≥ 45%  →  threshold 60%  (standard)
    """
    cfg     = CRO_CONFIG
    trigger = cfg.get("bonnie_strict_trigger_wr", 0.45)
    wr      = _elastic_target_wr(closed)
    return cfg.get("bonnie_strict_threshold", 0.64) if wr < trigger else cfg.get("bonnie_base_threshold", 0.60)


def _load_beta_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("trades", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError) as exc:
        log_error("cro_load_trades", {"path": str(path), "error": str(exc)})
        return []


def _parse_dt(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _build_cumulative(closed: list[dict]) -> list[float]:
    total, series = 0.0, []
    for t in sorted(closed, key=lambda x: x.get("closed_at", "")):
        total += t.get("result_eur", 0) or 0
        series.append(total)
    return series


def _max_drawdown(series: list[float]) -> float:
    if not series:
        return 0.0
    peak, max_dd = series[0], 0.0
    for val in series:
        if val > peak:
            peak = val
        if peak > 0:
            max_dd = max(max_dd, (peak - val) / peak * 100)
    return round(max_dd, 2)


def _sector_exposure(positions: list[dict]) -> dict[str, int]:
    try:
        from .watchlist_manager import _TICKER_TO_SECTOR as _T2S
    except ImportError:
        return {}
    exposure: dict[str, int] = {}
    for pos in positions:
        symbol = pos.get("ticker", "").split("_")[0]
        sector = _T2S.get(symbol, "UNKNOWN")
        if sector != "UNKNOWN":
            exposure[sector] = exposure.get(sector, 0) + 1
    return exposure


def _kelly_size_factor(closed: list[dict], n: int = 50, fraction: float = 0.25,
                       max_pos_pct: float = 10.0) -> float:
    """Quarter-Kelly position size multiplier from last N closed trades.

    Returns a scale in [0.5, 1.0].  Reads result_eur from beta_trades dicts.
    """
    recent = closed[-n:]
    if len(recent) < 10:
        return 1.0
    wins   = [(t.get("result_eur") or 0.0) for t in recent if (t.get("result_eur") or 0.0) > 0]
    losses = [abs(t.get("result_eur") or 0.0) for t in recent if (t.get("result_eur") or 0.0) < 0]
    if not wins or not losses:
        return 1.0
    W      = len(wins) / len(recent)
    R      = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    f_star = W - (1.0 - W) / R
    if f_star <= 0.0:
        return 0.5
    return min(1.0, fraction * f_star / (max_pos_pct / 100.0))


def _validate_proposal(
    proposed:        "ProposedTrade",
    portfolio_state: dict,
    risk_factor:     float,
    trades_today:    int,
    atr_pct:         float = 0.0,
    regime_mult:     float = 1.0,
    all_closed:      "list[dict] | None" = None,
) -> tuple[bool, str, float, float]:
    """Valida proposta concreta. Devolve (approved, reason, final_size_eur, assumed_risk_eur)."""
    positions    = portfolio_state.get("positions", [])
    free_cash    = portfolio_state.get("cash", {}).get("free") or 0
    total_equity = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + free_cash

    if total_equity <= 0:
        log_decision("cro_block", "zero_equity", {"ticker": proposed.ticker})
        return False, "zero_equity", 0.0, 0.0

    style   = getattr(proposed, "style", "VALUE")
    max_pos = RISK_CONFIG["max_position_pct"]

    # ATR-based sizing: equaliza risco financeiro entre activos de diferente volatilidade
    if atr_pct > 0:
        raw_size    = _atr_size_eur(atr_pct, total_equity,
                                    CRO_CONFIG.get("atr_risk_target_pct", 1.0), max_pos, style)
        max_pos_eur = min(raw_size * risk_factor, total_equity * max_pos / 100)
    else:
        max_pos_eur = total_equity * max_pos * risk_factor / 100

    # Kelly fractional sizing (quarter-Kelly, enabled via CRO_CONFIG)
    if CRO_CONFIG.get("enable_kelly_sizing", False) and all_closed:
        kf = _kelly_size_factor(all_closed, n=50, fraction=0.25, max_pos_pct=max_pos)
        max_pos_eur *= kf

    # Risco assumido (perda máxima se stop for atingido)
    stop_key     = "atr_stop_mult_momentum" if style == "MOMENTUM" else "atr_stop_mult_value"
    stop_mult    = CRO_CONFIG.get(stop_key, 2.0)
    stop_dist    = atr_pct * stop_mult if atr_pct > 0 else CRO_CONFIG.get("atr_fallback_stop_pct", 5.0) / 100
    assumed_risk = round(max_pos_eur * stop_dist, 2)

    if trades_today >= RISK_CONFIG["max_trades_per_day"]:
        log_decision("cro_block", "max_trades_per_day", {
            "today": trades_today, "limit": RISK_CONFIG["max_trades_per_day"]
        })
        return False, "max_trades_per_day", 0.0, 0.0

    if proposed.side == "BUY":
        if max_pos_eur > free_cash * 0.95:
            log_decision("cro_block", "insufficient_cash", {
                "max_pos_eur": round(max_pos_eur, 2), "free_cash": round(free_cash, 2)
            })
            return False, "insufficient_cash", 0.0, 0.0

        try:
            from .watchlist_manager import _TICKER_TO_SECTOR as _T2S
        except ImportError:
            _T2S = {}

        symbol = proposed.ticker.split("_")[0]
        sector = _T2S.get(symbol, "UNKNOWN")
        if sector != "UNKNOWN":
            in_sector = sum(
                1 for p in positions
                if _T2S.get(p.get("ticker", "").split("_")[0], "UNKNOWN") == sector
            )
            if in_sector >= RISK_CONFIG["max_positions_per_sector"]:
                log_decision("cro_block", "sector_concentration", {
                    "ticker": proposed.ticker, "sector": sector,
                    "count": in_sector, "limit": RISK_CONFIG["max_positions_per_sector"],
                })
                return False, f"sector_blocked_{sector}", 0.0, 0.0

    log_decision("cro_approve", "position_sized", {
        "ticker":           proposed.ticker,
        "risco_assumido":   assumed_risk,
        "mult_regime":      round(regime_mult, 4),
        "stop_loss_atr":    round(stop_dist * 100, 2),
        "size_eur":         round(max_pos_eur, 2),
        "atr_based":        atr_pct > 0,
    })

    return True, "approved", max_pos_eur, assumed_risk


def _atr_size_eur(
    atr_pct:         float,
    equity:          float,
    risk_target_pct: float,
    max_pos_pct:     float,
    style:           str = "VALUE",
) -> float:
    """ATR position sizing — equaliza o risco financeiro entre activos.

    Calcula o tamanho da posição tal que, se o stop ATR for atingido,
    a perda seja exactamente risk_target_pct% da equity.
    """
    if atr_pct <= 0 or equity <= 0:
        return equity * max_pos_pct / 100
    stop_key  = "atr_stop_mult_momentum" if style == "MOMENTUM" else "atr_stop_mult_value"
    stop_mult = CRO_CONFIG.get(stop_key, 2.0)
    stop_dist = atr_pct * stop_mult
    risk_eur  = equity * risk_target_pct / 100
    return min(risk_eur / stop_dist, equity * max_pos_pct / 100)


def _atr_stop_loss_pct(atr_pct: float, style: str) -> float:
    """Stop loss dinâmico em % do preço de entrada, baseado no ATR e estilo do trade."""
    stop_key  = "atr_stop_mult_momentum" if style == "MOMENTUM" else "atr_stop_mult_value"
    stop_mult = CRO_CONFIG.get(stop_key, 2.0)
    if atr_pct <= 0:
        return CRO_CONFIG.get("atr_fallback_stop_pct", 5.0)
    return round(stop_mult * atr_pct * 100, 2)


def _generate_insights(
    win_rate_7d:     float,
    drawdown_pct:    float,
    risk_factor:     float,
    final_pct:       float,
    elastic_target:  float,
    max_dd:          float,
    regime:          str,
    sector_exposure: dict[str, int],
    all_closed:      list[dict],
    bonnie_thr:      float = 0.60,
    regime_mult:     float = 1.0,
    stop_loss_pct:   float = 5.0,
) -> list[str]:
    insights: list[str] = []
    wr_pct = round(win_rate_7d * 100, 1)
    tg_pct = round(elastic_target * 100, 1)
    rf_pct = round(risk_factor * 100)

    # Win rate vs alvo elástico
    if wr_pct < 40:
        insights.append(
            f"Win rate 7d em {wr_pct}% — abaixo do limiar crítico (40%). "
            f"Alvo elástico: {tg_pct}%. Factor de risco reduzido para {rf_pct}%. Cautela máxima."
        )
    elif wr_pct < tg_pct:
        insights.append(
            f"Win rate 7d em {wr_pct}% — abaixo do alvo elástico ({tg_pct}%). "
            f"Factor de risco: {rf_pct}%."
        )
    else:
        insights.append(
            f"Win rate 7d em {wr_pct}% — acima do alvo elástico ({tg_pct}%). "
            f"Factor de risco positivo: {rf_pct}%."
        )

    # Hierarquia CRO→Bonnie
    if bonnie_thr > 0.60:
        insights.append(
            f"CRO → Bonnie: threshold apertado para {int(bonnie_thr * 100)}% "
            f"(WR histórica abaixo de 45% — mercado adverso)."
        )

    # Drawdown
    if drawdown_pct > max_dd * 0.7:
        insights.append(
            f"Drawdown actual {drawdown_pct:.1f}% — próximo do limite ({max_dd}%). "
            "Novas entradas devem ser conservadoras."
        )
    elif drawdown_pct > 0:
        insights.append(f"Drawdown actual: {drawdown_pct:.1f}% (limite: {max_dd}%).")

    # Concentração sectorial
    limit = RISK_CONFIG["max_positions_per_sector"]
    for sector, count in sector_exposure.items():
        if count >= limit:
            insights.append(
                f"Sector {sector}: {count}/{limit} posições abertas — bloqueado para novas entradas."
            )

    # Lições dos trades fechados
    insights.extend(_trade_lessons(all_closed))

    # Resumo de alocação
    insights.append(
        f"Tamanho máximo CRO: {final_pct:.1f}% da equity "
        f"(base {RISK_CONFIG['max_position_pct']}% × factor {rf_pct}%)."
    )

    # Multiplicador de Regime
    if regime_mult == 0.0:
        insights.append(
            f"Regime {regime}: multiplicador CRO 0× — entradas MOMENTUM VETADAS. "
            "VALUE pode entrar a 0.25× (defensivo)."
        )
    elif regime_mult <= 0.25:
        insights.append(
            f"Regime {regime}: multiplicador CRO {regime_mult:.2f}× — modo defensivo extremo (só value)."
        )
    elif regime_mult < 1.0:
        insights.append(
            f"Regime {regime}: multiplicador CRO {regime_mult:.2f}× — alocação reduzida."
        )

    # Stop Loss ATR
    if stop_loss_pct > 0:
        source = "ATR dinâmico" if stop_loss_pct != CRO_CONFIG.get("atr_fallback_stop_pct", 5.0) else "fixo (fallback)"
        insights.append(f"Stop Loss ({source}): {stop_loss_pct:.1f}% abaixo da entrada.")

    return insights


def _trade_lessons(closed: list[dict]) -> list[str]:
    if not closed:
        return ["Sem trades fechados — sem lições disponíveis ainda."]

    lessons: list[str] = []

    # Duração média
    win_times = [
        t.get("result_after_minutes", 0)
        for t in closed
        if (t.get("result_eur") or 0) > 0 and t.get("result_after_minutes")
    ]
    all_times = [t.get("result_after_minutes", 0) for t in closed if t.get("result_after_minutes")]
    if win_times and all_times:
        lessons.append(
            f"Duração média dos trades vencedores: {sum(win_times)/len(win_times):.0f}min "
            f"(todos: {sum(all_times)/len(all_times):.0f}min)."
        )

    # Sequência de perdas
    results = [(t.get("result_eur") or 0) for t in sorted(closed, key=lambda x: x.get("closed_at", ""))]
    streak  = _max_loss_streak(results)
    if streak >= 3:
        lessons.append(
            f"Sequência máxima de perdas consecutivas: {streak} trades. "
            "Considera reduzir o tamanho após 2 perdas seguidas."
        )

    # Melhor / pior ticker
    by_ticker: dict[str, float] = {}
    for t in closed:
        k = t.get("ticker", "?")
        by_ticker[k] = by_ticker.get(k, 0.0) + (t.get("result_eur") or 0)
    if by_ticker:
        best  = max(by_ticker, key=lambda k: by_ticker[k])
        worst = min(by_ticker, key=lambda k: by_ticker[k])
        if by_ticker[best] > 0:
            lessons.append(f"Melhor ticker acumulado: {best} (+{by_ticker[best]:.2f}€).")
        if by_ticker[worst] < 0:
            lessons.append(f"Pior ticker acumulado: {worst} ({by_ticker[worst]:.2f}€) — rever a tese.")

    return lessons


def _max_loss_streak(results: list[float]) -> int:
    max_s = cur_s = 0
    for r in results:
        if r < 0:
            cur_s += 1
            max_s  = max(max_s, cur_s)
        else:
            cur_s  = 0
    return max_s


def _already_whispered_today(today: str) -> bool:
    """Devolve True se o cro_insights.json já foi gerado hoje (guard anti-spam)."""
    path = CRO_CONFIG["cro_insights_path"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("generated_at", "").startswith(today)
    except (OSError, json.JSONDecodeError):
        return False


def _write_insights(payload: dict) -> None:
    path = CRO_CONFIG["cro_insights_path"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
        print(f"[CRO] Insights escritos: {path}")
    except OSError as exc:
        log_error("cro_insights_write", {"error": str(exc)})


def _whisper(payload: dict) -> None:
    """Formata e envia a narrativa CRO para Telegram via notifier (Whisper)."""
    from .notifier import enviar_alerta

    wr_pct = round(payload["win_rate_7d"] * 100, 1)
    dd_pct = payload["drawdown_atual"]
    n      = payload["trades_analisados"]
    ts     = payload["generated_at"][:16].replace("T", " ")
    regime = payload.get("regime", "?")
    rf_pct = round(payload.get("risk_factor", 1.0) * 100)

    regime_label = {
        "bull_trending":     "Bull Trending",
        "bull_lateral":      "Bull Lateral",
        "bear_correction":   "Bear Correction",
        "bear_capitulation": "Bear Capitulation",
    }.get(regime, regime)

    linhas = [
        "🧠 Relatório Cognitivo CRO",
        "",
        f"Regime: {regime_label}  ·  Factor de risco: {rf_pct}%",
        f"Win Rate 7d: {wr_pct}%  ·  Drawdown: {dd_pct:.1f}%",
        f"Trades analisados: {n}",
        "",
    ]
    for insight in payload.get("insights", []):
        linhas.append(f"• {insight}")
    linhas += ["", f"FundScope · {ts} UTC"]

    enviar_alerta("\n".join(linhas), silencioso=True)
    print(f"[CRO] Narrativa enviada para Telegram ({len(payload.get('insights', []))} insights).")


# ---------------------------------------------------------------------------
# Gains analysis helpers — alimentam analyze_gains() / gains_analysis.json
# ---------------------------------------------------------------------------

def _gains_summary(closed: list[dict]) -> dict:
    """Sumário agregado: P&L total, win rate, melhor/pior trade."""
    if not closed:
        return {
            "total_pnl_eur": 0.0,
            "total_pnl_pct": 0.0,
            "win_rate":      0.0,
            "best_trade":    None,
            "worst_trade":   None,
        }

    total_pnl = sum((t.get("result_eur") or 0) for t in closed)
    wins      = sum(1 for t in closed if (t.get("result_eur") or 0) > 0)
    pct_vals  = [t.get("result_pct") for t in closed if t.get("result_pct") is not None]
    avg_pct   = (sum(pct_vals) / len(pct_vals)) if pct_vals else 0.0

    best  = max(closed, key=lambda t: (t.get("result_eur") or 0))
    worst = min(closed, key=lambda t: (t.get("result_eur") or 0))

    def _trade_brief(t: dict) -> dict:
        return {
            "ticker":   t.get("ticker"),
            "gain_pct": round(t.get("result_pct") or 0, 2),
            "gain_eur": round(t.get("result_eur") or 0, 2),
        }

    return {
        "total_pnl_eur": round(total_pnl, 2),
        "total_pnl_pct": round(avg_pct, 2),
        "win_rate":      round(wins / len(closed), 4),
        "best_trade":    _trade_brief(best),
        "worst_trade":   _trade_brief(worst),
    }


def _gains_patterns(closed: list[dict]) -> list[str]:
    """Padrões positivos identificados (estilo análise post-mortem)."""
    if not closed:
        return ["Sem trades fechados — sem padrões identificados ainda."]

    patterns: list[str] = []

    # Duração média dos vencedores vs todos
    win_times = [t.get("result_after_minutes", 0) for t in closed
                 if (t.get("result_eur") or 0) > 0 and t.get("result_after_minutes")]
    all_times = [t.get("result_after_minutes", 0) for t in closed if t.get("result_after_minutes")]
    if win_times and all_times:
        avg_win = sum(win_times) / len(win_times)
        avg_all = sum(all_times) / len(all_times)
        if avg_win < avg_all * 0.8:
            patterns.append(
                f"Vencedores fecham em média {avg_win:.0f}min — significativamente mais "
                f"rápido que a média global ({avg_all:.0f}min). Saídas técnicas funcionam."
            )

    # Win rate por regime
    by_regime: dict[str, list[int]] = {}
    for t in closed:
        regime = (t.get("context") or {}).get("regime") or "desconhecido"
        by_regime.setdefault(regime, []).append(1 if (t.get("result_eur") or 0) > 0 else 0)
    for regime, outcomes in by_regime.items():
        if len(outcomes) >= 3:
            wr = sum(outcomes) / len(outcomes)
            if wr >= 0.7:
                patterns.append(
                    f"Regime {regime}: win rate de {wr*100:.0f}% ({len(outcomes)} trades) "
                    "— manter exposição neste regime."
                )

    # Estilo (MOMENTUM vs VALUE) que funciona melhor
    by_style: dict[str, list[float]] = {}
    for t in closed:
        style = (t.get("context") or {}).get("style") or "MOMENTUM"
        by_style.setdefault(style, []).append(t.get("result_eur") or 0)
    if len(by_style) >= 2:
        best_style = max(by_style, key=lambda k: sum(by_style[k]))
        total      = sum(by_style[best_style])
        if total > 0:
            patterns.append(
                f"Estilo {best_style} acumula +{total:.2f}€ "
                f"({len(by_style[best_style])} trades) — preferir nas próximas entradas."
            )

    if not patterns:
        patterns.append("Histórico ainda curto — padrões consolidados requerem >10 trades.")

    return patterns


def _gains_recurring_errors(closed: list[dict]) -> list[str]:
    """Erros recorrentes detectados (perdas com causa identificável)."""
    if not closed:
        return []

    errors: list[str] = []
    losers  = [t for t in closed if (t.get("result_eur") or 0) < 0]

    # Sequência de perdas consecutivas
    results = [(t.get("result_eur") or 0) for t in closed]
    streak  = _max_loss_streak(results)
    if streak >= 3:
        errors.append(
            f"Sequência máxima de {streak} perdas consecutivas — "
            "filtro Bonnie pode estar a deixar passar setups fracos."
        )

    # Tickers reincidentes em perdas
    loss_count: dict[str, int] = {}
    loss_total: dict[str, float] = {}
    for t in losers:
        k = t.get("ticker", "?")
        loss_count[k] = loss_count.get(k, 0) + 1
        loss_total[k] = loss_total.get(k, 0.0) + (t.get("result_eur") or 0)
    repeat_losers = [(k, c, loss_total[k]) for k, c in loss_count.items() if c >= 2]
    for ticker, count, total in sorted(repeat_losers, key=lambda x: x[2])[:3]:
        errors.append(
            f"{ticker}: {count} perdas ({total:.2f}€ acumulado) — "
            "rever a tese ou excluir da watchlist."
        )

    # Saídas por stop loss
    stop_exits = [t for t in losers if "stop" in (t.get("postmortem") or "").lower()]
    if stop_exits and len(stop_exits) / max(len(closed), 1) > 0.3:
        errors.append(
            f"{len(stop_exits)} saídas por stop loss "
            f"({len(stop_exits)/len(closed)*100:.0f}% do total) — "
            "entradas podem estar a ser feitas tarde no movimento."
        )

    return errors


def _gains_sector_performance(closed: list[dict]) -> list[dict]:
    """Performance agregada por sector (usa _TICKER_TO_SECTOR do watchlist_manager)."""
    if not closed:
        return []

    try:
        from .watchlist_manager import _TICKER_TO_SECTOR as _T2S
    except ImportError:
        _T2S = {}

    by_sector: dict[str, list[dict]] = {}
    for t in closed:
        symbol = (t.get("ticker") or "").split("_")[0]
        sector = _T2S.get(symbol, "Outros")
        by_sector.setdefault(sector, []).append(t)

    out = []
    for sector, ts in by_sector.items():
        wins  = sum(1 for t in ts if (t.get("result_eur") or 0) > 0)
        pcts  = [t.get("result_pct") for t in ts if t.get("result_pct") is not None]
        out.append({
            "sector":    sector,
            "n_trades":  len(ts),
            "win_rate":  round(wins / len(ts), 4),
            "pnl_pct":   round(sum(pcts) / len(pcts), 2) if pcts else 0.0,
        })
    out.sort(key=lambda x: x["pnl_pct"], reverse=True)
    return out


def _gains_cro_verdict(summary: dict, sector_perf: list[dict]) -> str:
    """Veredicto sintético do CRO — uma linha accionável."""
    if not summary or summary.get("total_pnl_eur") is None:
        return "Sem dados suficientes para veredicto."

    pnl = summary["total_pnl_eur"]
    wr  = summary["win_rate"]

    if pnl > 0 and wr >= 0.6:
        base = f"Performance positiva (P&L +{pnl:.2f}€, WR {wr*100:.0f}%). Manter estratégia."
    elif pnl > 0:
        base = f"P&L positivo (+{pnl:.2f}€) com WR {wr*100:.0f}% — vencedores compensam perdas."
    elif wr >= 0.5:
        base = f"WR {wr*100:.0f}% mas P&L {pnl:.2f}€ — perdas maiores que ganhos, apertar stop loss."
    else:
        base = f"P&L {pnl:.2f}€, WR {wr*100:.0f}% — rever filtros de entrada da Bonnie."

    if sector_perf:
        worst = min(sector_perf, key=lambda s: s["pnl_pct"])
        if worst["pnl_pct"] < -2 and worst["n_trades"] >= 2:
            base += f" Reduzir exposição a {worst['sector']} ({worst['pnl_pct']:.1f}% médio)."

    return base
