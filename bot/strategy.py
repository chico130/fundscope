"""
Strategy module — Fase 1 simple rule-based signals.

Pipeline:
  market_data  ──► generate_signals()  ──► list[Signal]
  signals      ──► propose_trades()    ──► list[ProposedTrade]
  proposal     ──► check_risk_limits() ──► bool

All functions are pure (no side-effects) except check_risk_limits,
which calls log_decision() when a limit is breached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .config import RISK_CONFIG, STRATEGY_VERSION

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
    ticker: str
    signal_type: Literal["ENTRY", "EXIT", "REDUCE"]
    direction: Literal["LONG"]          # bot is long-only in Fase 1
    strength: float                     # 0.0–1.0
    reasons: list[str]
    context: dict = field(default_factory=dict)


@dataclass
class ProposedTrade:
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: float
    order_type: Literal["MARKET", "LIMIT"]
    price: float | None
    reason: str
    context: dict
    signal_strength: float
    strategy_version: str = STRATEGY_VERSION


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    market_data: dict[str, dict],
    portfolio_state: dict,
    regime: str = "bull_trending",
) -> list[Signal]:
    """Generates BUY / EXIT / REDUCE signals from technical indicators.

    Entry rules (both must be satisfied):
      A. RSI-14 ≤ 35  AND  EMA-50 > EMA-200  AND  volume_ratio ≥ 1.2
      B. RSI-14 40–55  AND  EMA-50 > EMA-200  AND  volume_ratio ≥ 1.8  (momentum surge)

    Exit / Reduce rules (applied only to held positions):
      C. RSI-14 ≥ 72  →  EXIT
      D. EMA-50 < EMA-200 on a held position  →  REDUCE (sell half)
    """
    signals: list[Signal] = []
    held = {p.get("ticker") for p in portfolio_state.get("positions", [])}

    for ticker, data in market_data.items():
        t = data.get("technicals")
        if t is None:
            continue

        rsi = t.get("rsi_14")
        ema50_above = t.get("ema50_above_ema200")
        vol_ratio = t.get("volume_ratio_vs_avg") or 1.0
        atr = t.get("atr_14")

        if rsi is None or ema50_above is None:
            continue

        base_ctx = {
            "rsi_14": rsi,
            "ema50_above_ema200": ema50_above,
            "volume_ratio_vs_avg": vol_ratio,
            "atr_14": atr,
        }

        # ── Exit / Reduce on held positions ─────────────────────────────
        if ticker in held:
            if rsi >= 72:
                signals.append(Signal(
                    ticker=ticker,
                    signal_type="EXIT",
                    direction="LONG",
                    strength=min(1.0, (rsi - 72) / 28),
                    reasons=[f"RSI-14 sobrecomprado ({rsi:.1f} ≥ 72) — risco de correcção"],
                    context=base_ctx,
                ))
                continue

            if ema50_above is False:
                signals.append(Signal(
                    ticker=ticker,
                    signal_type="REDUCE",
                    direction="LONG",
                    strength=0.5,
                    reasons=["EMA-50 abaixo de EMA-200 — tendência invertida, reduzir exposição"],
                    context=base_ctx,
                ))
                continue

        # ── Entry (new position or add to existing) ──────────────────────
        if ticker not in held or _below_half_max(ticker, portfolio_state):
            if regime in _BEAR_REGIMES:
                continue
            sig = _entry_signal(ticker, rsi, ema50_above, vol_ratio, atr=atr)
            if sig:
                signals.append(sig)

    return signals


def _entry_signal(
    ticker: str, rsi: float, ema50_above: bool, vol_ratio: float, atr: float | None = None
) -> Signal | None:
    reasons: list[str] = []
    strength = 0.0

    # Rule A: oversold in uptrend
    if rsi <= 35 and ema50_above and vol_ratio >= 1.2:
        reasons.append(f"RSI-14 sobrevendido ({rsi:.1f} ≤ 35) — zona de entrada")
        reasons.append("Tendência ascendente: EMA-50 > EMA-200")
        reasons.append(f"Volume {vol_ratio:.1f}× acima da média — confirmação presente")
        strength = min(1.0, 0.70 + (35 - rsi) / 100)

    # Rule B: neutral RSI + volume surge (momentum)
    elif 40 <= rsi <= 55 and ema50_above and vol_ratio >= 1.8:
        reasons.append(f"RSI-14 neutro ({rsi:.1f}) em tendência ascendente")
        reasons.append(f"Volume excepcional {vol_ratio:.1f}× — sinal de momentum")
        strength = min(1.0, 0.55 + (vol_ratio - 1.8) / 10)

    if not reasons:
        return None

    return Signal(
        ticker=ticker,
        signal_type="ENTRY",
        direction="LONG",
        strength=strength,
        reasons=reasons,
        context={"rsi_14": rsi, "ema50_above_ema200": ema50_above, "volume_ratio_vs_avg": vol_ratio, "atr_14": atr},
    )


def _below_half_max(ticker: str, portfolio_state: dict) -> bool:
    """True if the existing position is below half of max_position_pct."""
    positions = portfolio_state.get("positions", [])
    cash_free = (portfolio_state.get("cash", {}).get("free") or 0)
    total = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + cash_free
    if total == 0:
        return False
    for p in positions:
        if p.get("ticker") == ticker:
            pct = p.get("value", p.get("value_eur", 0)) / total * 100
            return pct < RISK_CONFIG["max_position_pct"] / 2
    return True


# ---------------------------------------------------------------------------
# Trade proposals
# ---------------------------------------------------------------------------

def propose_trades(signals: list[Signal], portfolio_state: dict, regime: str = "bull_trending") -> list[ProposedTrade]:
    """Converts signals into concrete proposals with position sizing.

    Position size for ENTRY = signal_strength × 15% of equity,
    capped at max_position_pct and 95% of free cash.
    REDUCE = sell 50% of existing quantity.
    EXIT   = sell 100% of existing quantity.
    """
    positions = portfolio_state.get("positions", [])
    cash_data = portfolio_state.get("cash", {})
    free_cash = cash_data.get("free") or 0
    total_equity = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + free_cash

    if total_equity == 0:
        return []

    max_pos_eur = total_equity * RISK_CONFIG["max_position_pct"] / 100
    proposals: list[ProposedTrade] = []

    size_factor = _REGIME_SIZE_FACTOR.get(regime, 1.0)

    for sig in signals:
        if sig.signal_type == "ENTRY":
            size_eur = min(sig.strength * total_equity * 0.15 * size_factor, max_pos_eur, free_cash * 0.95)
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
