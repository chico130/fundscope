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
        from .strategy import _REGIME_SIZE_FACTOR

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

        # Fórmula de risco contextual com alvo dinâmico
        wr_adj      = max(0.5, min(1.2, win_rate_7d / elastic_target if elastic_target > 0 else 1.0))
        dd_adj      = max(0.3, min(1.0, 1.0 - drawdown_pct / max_dd))
        reg_factor  = _REGIME_SIZE_FACTOR.get(regime, 1.0)
        risk_factor = round(wr_adj * dd_adj * reg_factor, 4)
        final_pct   = round(RISK_CONFIG["max_position_pct"] * risk_factor, 2)

        insights = _generate_insights(
            win_rate_7d, drawdown_pct, risk_factor, final_pct,
            elastic_target, max_dd, regime,
            self._state.get("sector_exposure", {}),
            all_closed, bonnie_thr,
        )

        approved       = True
        reason         = "advisory_only"
        final_size_eur = 0.0

        if proposed is not None:
            approved, reason, final_size_eur = _validate_proposal(
                proposed, portfolio_state, final_pct, trades_today
            )
            if not approved:
                insights.insert(0, f"VETO CRO: {reason} — proposta bloqueada para {proposed.ticker}.")

        self._insights    = insights
        self._risk_factor = risk_factor
        self._regime      = regime

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
        )

    # ------------------------------------------------------------------
    # 3. Speak — escreve cro_insights.json e envia para Telegram
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


def _validate_proposal(
    proposed:        "ProposedTrade",
    portfolio_state: dict,
    final_pct:       float,
    trades_today:    int,
) -> tuple[bool, str, float]:
    """Valida proposta concreta. Devolve (approved, reason, final_size_eur)."""
    positions    = portfolio_state.get("positions", [])
    free_cash    = portfolio_state.get("cash", {}).get("free") or 0
    total_equity = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + free_cash

    if total_equity <= 0:
        log_decision("cro_block", "zero_equity", {"ticker": proposed.ticker})
        return False, "zero_equity", 0.0

    max_pos_eur = total_equity * final_pct / 100

    if trades_today >= RISK_CONFIG["max_trades_per_day"]:
        log_decision("cro_block", "max_trades_per_day", {
            "today": trades_today, "limit": RISK_CONFIG["max_trades_per_day"]
        })
        return False, "max_trades_per_day", 0.0

    if proposed.side == "BUY":
        if max_pos_eur > free_cash * 0.95:
            log_decision("cro_block", "insufficient_cash", {
                "max_pos_eur": round(max_pos_eur, 2), "free_cash": round(free_cash, 2)
            })
            return False, "insufficient_cash", 0.0

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
                return False, f"sector_blocked_{sector}", 0.0

    return True, "approved", max_pos_eur


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

    if regime in {"bear_correction", "bear_capitulation"}:
        insights.append(f"Regime {regime}: novas entradas BLOQUEADAS pelo CRO.")

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
