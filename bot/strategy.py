"""
Strategy module — Dual-Engine rule-based signals com injecção de parâmetros do Learner.

Pipeline:
  market_data  ──► generate_signals()  ──► list[Signal]
  signals      ──► propose_trades()    ──► list[ProposedTrade]

Parâmetros técnicos injectados via learner.get_active_params() com fallback
automático aos defaults hardcoded em caso de ausência, corrupção ou bounds violados.
O módulo nunca crasha por falha do Learner — a rede de segurança é sempre os defaults.

Parâmetros aprendíveis (Fase 3):
  _PC  ← weekly.clyde   → limiares RSI e volume de entrada/saída (VALUE + MOMENTUM)
  _PB  ← monthly.bonnie → size_factor_pct e thresholds de aprovação
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from .config import RISK_CONFIG, STRATEGY_VERSION
from .logger import log_decision

_EARNINGS_PATH = Path(__file__).parent.parent / "data" / "beta" / "earnings_ai.json"

# Module-level cache — loaded once per process.
_earnings_calendar: dict | None = None


def _load_earnings_calendar() -> dict:
    global _earnings_calendar
    if _earnings_calendar is not None:
        return _earnings_calendar
    try:
        with open(_EARNINGS_PATH, "r", encoding="utf-8") as f:
            _earnings_calendar = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _earnings_calendar = {}
    return _earnings_calendar


def _business_days_until(date_str: str) -> int | None:
    """Business days (Mon–Fri) from today to date_str. None if unparseable or past."""
    try:
        target = date.fromisoformat(date_str[:10])
        today  = date.today()
        if target < today:
            return None
        count = 0
        d = today
        while d < target:
            d += timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return count
    except (ValueError, TypeError):
        return None


def _earnings_days_ahead(ticker: str) -> int | None:
    """Business days until the next earnings for ticker, or None if unknown.

    Supports earnings_ai.json values of: null, "YYYY-MM-DD", or {"data": "YYYY-MM-DD"}.
    """
    entry = _load_earnings_calendar().get(ticker)
    if entry is None:
        return None
    if isinstance(entry, str):
        date_str = entry
    elif isinstance(entry, dict):
        date_str = entry.get("data") or entry.get("date")
    else:
        return None
    return _business_days_until(date_str) if date_str else None

# ---------------------------------------------------------------------------
# Injecção defensiva de parâmetros optimizados
# ---------------------------------------------------------------------------
# Executado uma vez por processo (GitHub Actions = processo novo a cada 15 min).
# Em caso de qualquer falha, cai em silêncio para os defaults.

try:
    from .learner import get_active_params as _get_params
    _P = _get_params()
except Exception:
    from .learner import _DEFAULT_PARAMS
    _P = _DEFAULT_PARAMS  # type: ignore[assignment]

_PC = _P["weekly"]["clyde"]     # thresholds técnicos do Clyde
_PB = _P["monthly"]["bonnie"]   # size factor e thresholds da Bonnie
_ENABLED_STYLES: list[str] = _P.get("enabled_styles", ["VALUE", "MOMENTUM"])

# ---------------------------------------------------------------------------
# Constantes não aprendíveis (lógica de regime — estáveis por design)
# ---------------------------------------------------------------------------

_BEAR_REGIMES = {"bear_correction", "bear_capitulation"}

_REGIME_SIZE_FACTOR: dict[str, float] = {
    "bull_trending":     1.0,
    "bull_lateral":      0.6,
    "bear_correction":   0.0,
    "bear_capitulation": 0.0,
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    ticker:      str
    signal_type: Literal["ENTRY", "EXIT", "REDUCE"]
    direction:   Literal["LONG"]
    strength:    float
    reasons:     list[str]
    style:       Literal["VALUE", "MOMENTUM"] = "VALUE"
    context:     dict = field(default_factory=dict)


@dataclass
class ProposedTrade:
    ticker:           str
    side:             Literal["BUY", "SELL"]
    qty:              float
    order_type:       Literal["MARKET", "LIMIT"]
    price:            float | None
    reason:           str
    context:          dict
    signal_strength:  float
    style:            Literal["VALUE", "MOMENTUM"] = "VALUE"
    strategy_version: str = STRATEGY_VERSION


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    market_data:      dict[str, dict],
    portfolio_state:  dict,
    regime:           str = "bull_trending",
    position_styles:  dict[str, str] | None = None,
    position_peaks:   dict[str, float] | None = None,
) -> list[Signal]:
    """Gera sinais BUY / EXIT / REDUCE a partir de indicadores técnicos.

    Regras de entrada VALUE:
      A. RSI ≤ rsi_oversold_ceiling  AND  EMA-50 > EMA-200  AND  vol ≥ vol_ratio_oversold_min
      B. rsi_momentum_min ≤ RSI ≤ rsi_momentum_max  AND  EMA-50 > EMA-200
         AND  vol ≥ vol_ratio_momentum_min

    Regra de entrada MOMENTUM:
      M. RSI ≥ momentum_rsi_floor  AND  price > EMA-20 > EMA-50 > EMA-200 (alinhamento total)
         AND  vol ≥ momentum_vol_min  (anti-choppy: exige tendência limpa em todas as escalas)

    Saídas VALUE (posições com style=VALUE):
      C. RSI ≥ rsi_exit_floor              → EXIT
      D. EMA-50 < EMA-200                  → REDUCE (vende metade)

    Saídas MOMENTUM (posições com style=MOMENTUM):
      E. close < peak_high_since_entry − multiplier × ATR_14  → EXIT (trailing stop)
    """
    signals:    list[Signal] = []
    held        = {p.get("ticker") for p in portfolio_state.get("positions", [])}
    pos_styles  = position_styles or {}
    pos_peaks   = position_peaks  or {}

    for ticker, data in market_data.items():
        t = data.get("technicals")
        if t is None:
            continue

        rsi               = t.get("rsi_14")
        ema50_above       = t.get("ema50_above_ema200")
        ema20_above_ema50 = t.get("ema20_above_ema50")
        price_above_ema20 = t.get("price_above_ema20")
        vol_ratio         = t.get("volume_ratio_vs_avg") or 1.0
        atr               = t.get("atr_14")
        last_price        = t.get("last_price") or data.get("last_price")
        rs_bullish        = t.get("rs_bullish")

        if rsi is None or ema50_above is None:
            continue

        base_ctx = {
            "rsi_14":              rsi,
            "ema50_above_ema200":  ema50_above,
            "ema20_above_ema50":   ema20_above_ema50,
            "price_above_ema20":   price_above_ema20,
            "volume_ratio_vs_avg": vol_ratio,
            "atr_14":              atr,
            "rs_bullish":          rs_bullish,
        }

        # ── Saídas / reduções em posições detidas ───────────────────────
        if ticker in held:
            pos_style = pos_styles.get(ticker, "VALUE")

            if pos_style == "MOMENTUM":
                peak_high  = pos_peaks.get(ticker, 0.0)
                multiplier = _PC.get("momentum_atr_multiplier", 2.5)
                if atr and last_price and peak_high > 0:
                    trailing_stop = peak_high - multiplier * atr
                    if last_price < trailing_stop:
                        signals.append(Signal(
                            ticker=ticker,
                            signal_type="EXIT",
                            direction="LONG",
                            strength=1.0,
                            style="MOMENTUM",
                            reasons=[
                                f"ATR Trailing Stop atingido: preço {last_price:.2f} < "
                                f"stop {trailing_stop:.2f} "
                                f"(peak {peak_high:.2f} − {multiplier}×ATR {atr:.2f})"
                            ],
                            context={**base_ctx, "trailing_stop": trailing_stop, "peak_high": peak_high},
                        ))
                        continue

            else:  # VALUE (ou estilo desconhecido — fallback seguro)
                exit_floor = _PC["rsi_exit_floor"]
                if rsi >= exit_floor:
                    signals.append(Signal(
                        ticker=ticker,
                        signal_type="EXIT",
                        direction="LONG",
                        strength=min(1.0, (rsi - exit_floor) / max(1, 100 - exit_floor)),
                        style="VALUE",
                        reasons=[f"RSI-14 sobrecomprado ({rsi:.1f} ≥ {exit_floor}) — risco de correcção"],
                        context=base_ctx,
                    ))
                    continue

                if ema50_above is False:
                    signals.append(Signal(
                        ticker=ticker,
                        signal_type="REDUCE",
                        direction="LONG",
                        strength=0.5,
                        style="VALUE",
                        reasons=["EMA-50 abaixo de EMA-200 — tendência invertida, reduzir exposição"],
                        context=base_ctx,
                    ))
                    continue

        # ── Entradas (posição nova ou add abaixo do meio do máximo) ─────
        if ticker not in held or _below_half_max(ticker, portfolio_state):
            if regime in _BEAR_REGIMES:
                continue
            sig = _entry_signal(
                ticker, rsi, ema50_above, vol_ratio, atr=atr,
                ema20_above_ema50=ema20_above_ema50,
                price_above_ema20=price_above_ema20,
                rs_bullish=rs_bullish,
            )
            if sig:
                signals.append(sig)

    return signals


def _entry_signal(
    ticker:            str,
    rsi:               float,
    ema50_above:       bool,
    vol_ratio:         float,
    atr:               float | None = None,
    ema20_above_ema50: bool | None = None,
    price_above_ema20: bool | None = None,
    rs_bullish:        bool | None = None,
) -> Signal | None:
    """Avalia regras A, B (VALUE) e M (MOMENTUM) com parâmetros do Learner."""
    reasons: list[str] = []
    strength: float    = 0.0
    style: str         = "VALUE"

    rsi_ceil  = _PC["rsi_oversold_ceiling"]
    vol_os    = _PC["vol_ratio_oversold_min"]
    rsi_m_min = _PC["rsi_momentum_min"]
    rsi_m_max = _PC["rsi_momentum_max"]
    vol_mom   = _PC["vol_ratio_momentum_min"]

    # Regra A: sobrevendido em tendência ascendente (VALUE)
    if "VALUE" in _ENABLED_STYLES and rsi <= rsi_ceil and ema50_above and vol_ratio >= vol_os:
        reasons.append(f"RSI-14 sobrevendido ({rsi:.1f} ≤ {rsi_ceil}) — zona de entrada")
        reasons.append("Tendência ascendente: EMA-50 > EMA-200")
        reasons.append(f"Volume {vol_ratio:.1f}× acima da média — confirmação presente")
        strength = min(1.0, 0.70 + (rsi_ceil - rsi) / 100)

    # Regra B: RSI neutro + surge de volume (VALUE)
    elif "VALUE" in _ENABLED_STYLES and rsi_m_min <= rsi <= rsi_m_max and ema50_above and vol_ratio >= vol_mom:
        reasons.append(f"RSI-14 neutro ({rsi:.1f}) em tendência ascendente")
        reasons.append(f"Volume excepcional {vol_ratio:.1f}× — sinal de momentum")
        strength = min(1.0, 0.55 + (vol_ratio - vol_mom) / 10)

    # Regra M: breakout momentum (MOMENTUM) — alinhamento total obrigatório anti-choppy
    elif (
        "MOMENTUM" in _ENABLED_STYLES
        and rsi >= _PC.get("momentum_rsi_floor", 58)
        and ema50_above                 # EMA-50 > EMA-200 (tendência longa)
        and ema20_above_ema50           # EMA-20 > EMA-50  (aceleração média)
        and price_above_ema20           # price  > EMA-20  (breakout imediato)
        and vol_ratio >= _PC.get("momentum_vol_min", 1.5)
    ):
        # Gate 1 — Força Relativa vs SPY (obrigatório para MOMENTUM)
        if rs_bullish is not True:
            log_decision(
                "clyde_momentum_blocked", "relative_weakness_vs_spy",
                {"ticker": ticker, "rs_bullish": rs_bullish},
            )
            return None

        # Gate 2 — Radar de Earnings: bloqueia se resultados nos próximos 3 dias úteis
        _days_to_earn = _earnings_days_ahead(ticker)
        if _days_to_earn is not None and _days_to_earn <= 3:
            log_decision(
                "clyde_momentum_blocked", "earnings_imminent",
                {"ticker": ticker, "days_to_earnings": _days_to_earn},
            )
            return None

        style   = "MOMENTUM"
        m_floor = _PC.get("momentum_rsi_floor", 58)
        m_vol   = _PC.get("momentum_vol_min", 1.5)
        reasons.append(f"Breakout: RSI-14 {rsi:.1f} ≥ {m_floor} — momentum validado")
        reasons.append("Alinhamento total: price > EMA-20 > EMA-50 > EMA-200")
        reasons.append(f"Volume {vol_ratio:.1f}× ≥ {m_vol}× — confirmação de volume")
        strength = min(1.0, 0.65 + (vol_ratio - m_vol) / 10)

    if not reasons:
        return None

    return Signal(
        ticker=ticker,
        signal_type="ENTRY",
        direction="LONG",
        strength=strength,
        style=style,  # type: ignore[arg-type]
        reasons=reasons,
        context={
            "rsi_14":              rsi,
            "ema50_above_ema200":  ema50_above,
            "ema20_above_ema50":   ema20_above_ema50,
            "price_above_ema20":   price_above_ema20,
            "volume_ratio_vs_avg": vol_ratio,
            "atr_14":              atr,
            "rs_bullish":          rs_bullish,
        },
    )


def _below_half_max(ticker: str, portfolio_state: dict) -> bool:
    """True se a posição existente está abaixo de 60% do max_position_pct.

    Permite adds em posições com convicção repetida sem ultrapassar o cap.
    Threshold 6% para max=10% (60%) alinha com a logica de adds do backtest.
    """
    positions = portfolio_state.get("positions", [])
    cash_free = (portfolio_state.get("cash", {}).get("free") or 0)
    total     = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + cash_free
    if total == 0:
        return False
    for p in positions:
        if p.get("ticker") == ticker:
            pct = p.get("value", p.get("value_eur", 0)) / total * 100
            return pct < RISK_CONFIG["max_position_pct"] * 0.6
    return True


# ---------------------------------------------------------------------------
# Trade proposals
# ---------------------------------------------------------------------------

def propose_trades(
    signals:         list[Signal],
    portfolio_state: dict,
    regime:          str = "bull_trending",
) -> list[ProposedTrade]:
    """Converte sinais em propostas concretas com position sizing.

    ENTRY: signal_strength × size_factor_pct × equity × regime_factor
           capped em max_position_pct e 95% do free cash.
           size_factor_pct é aprendível (default 0.15 → monthly.bonnie).
    REDUCE: vende 50% da quantidade existente.
    EXIT:   vende 100% da quantidade existente.
    """
    positions    = portfolio_state.get("positions", [])
    free_cash    = (portfolio_state.get("cash", {}).get("free") or 0)
    total_equity = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + free_cash

    if total_equity == 0:
        return []

    max_pos_eur = total_equity * RISK_CONFIG["max_position_pct"] / 100
    size_factor = _PB["size_factor_pct"]
    regime_mult = _REGIME_SIZE_FACTOR.get(regime, 1.0)
    proposals:  list[ProposedTrade] = []

    for sig in signals:
        if sig.signal_type == "ENTRY":
            size_eur = min(
                sig.strength * total_equity * size_factor * regime_mult,
                max_pos_eur,
                free_cash * 0.95,
            )
            if size_eur < 50:
                continue
            price = _last_price(sig.ticker, portfolio_state)
            if not price:
                continue
            qty = round(size_eur / price, 4)
            if qty <= 0:
                continue
            proposals.append(ProposedTrade(
                ticker=sig.ticker, side="BUY", qty=qty,
                order_type="MARKET", price=None,
                reason=" | ".join(sig.reasons),
                context=sig.context, signal_strength=sig.strength,
                style=sig.style,
            ))

        elif sig.signal_type in ("EXIT", "REDUCE"):
            pos = next((p for p in positions if p.get("ticker") == sig.ticker), None)
            if not pos:
                continue
            qty = pos.get("quantity", 0)
            if sig.signal_type == "REDUCE":
                qty = round(qty * 0.5, 4)
            if qty <= 0:
                continue
            proposals.append(ProposedTrade(
                ticker=sig.ticker, side="SELL", qty=qty,
                order_type="MARKET", price=None,
                reason=" | ".join(sig.reasons),
                context=sig.context, signal_strength=sig.strength,
                style=sig.style,
            ))

    return proposals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_price(ticker: str, portfolio_state: dict) -> float | None:
    for p in portfolio_state.get("positions", []):
        if p.get("ticker") == ticker:
            return p.get("current_price") or p.get("last_price")
    snap = portfolio_state.get("market_snapshot", {}).get(ticker, {})
    return snap.get("last_price")
