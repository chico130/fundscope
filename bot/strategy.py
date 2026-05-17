"""
Strategy module — Fase 1 rule-based signals com injecção de parâmetros do Learner.

Pipeline:
  market_data  ──► generate_signals()  ──► list[Signal]
  signals      ──► propose_trades()    ──► list[ProposedTrade]

Parâmetros técnicos injectados via learner.get_active_params() com fallback
automático aos defaults hardcoded em caso de ausência, corrupção ou bounds violados.
O módulo nunca crasha por falha do Learner — a rede de segurança é sempre os defaults.

Parâmetros aprendíveis (Fase 3):
  _PC  ← weekly.clyde   → limiares RSI e volume de entrada/saída
  _PB  ← monthly.bonnie → size_factor_pct e thresholds de aprovação
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .config import RISK_CONFIG, STRATEGY_VERSION

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
    strategy_version: str = STRATEGY_VERSION


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    market_data:     dict[str, dict],
    portfolio_state: dict,
    regime:          str = "bull_trending",
) -> list[Signal]:
    """Gera sinais BUY / EXIT / REDUCE a partir de indicadores técnicos.

    Regras de entrada (uma deve ser satisfeita):
      A. RSI ≤ rsi_oversold_ceiling  AND  EMA-50 > EMA-200  AND  vol ≥ vol_ratio_oversold_min
      B. rsi_momentum_min ≤ RSI ≤ rsi_momentum_max  AND  EMA-50 > EMA-200
         AND  vol ≥ vol_ratio_momentum_min

    Saídas / reduções (apenas em posições detidas):
      C. RSI ≥ rsi_exit_floor              → EXIT
      D. EMA-50 < EMA-200 numa posição     → REDUCE (vende metade)
    """
    signals: list[Signal] = []
    held = {p.get("ticker") for p in portfolio_state.get("positions", [])}

    for ticker, data in market_data.items():
        t = data.get("technicals")
        if t is None:
            continue

        rsi         = t.get("rsi_14")
        ema50_above = t.get("ema50_above_ema200")
        vol_ratio   = t.get("volume_ratio_vs_avg") or 1.0
        atr         = t.get("atr_14")

        if rsi is None or ema50_above is None:
            continue

        base_ctx = {
            "rsi_14":              rsi,
            "ema50_above_ema200":  ema50_above,
            "volume_ratio_vs_avg": vol_ratio,
            "atr_14":              atr,
        }

        # ── Saídas / reduções em posições detidas ───────────────────────
        if ticker in held:
            exit_floor = _PC["rsi_exit_floor"]
            if rsi >= exit_floor:
                signals.append(Signal(
                    ticker=ticker,
                    signal_type="EXIT",
                    direction="LONG",
                    strength=min(1.0, (rsi - exit_floor) / max(1, 100 - exit_floor)),
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
                    reasons=["EMA-50 abaixo de EMA-200 — tendência invertida, reduzir exposição"],
                    context=base_ctx,
                ))
                continue

        # ── Entradas (posição nova ou add abaixo do meio do máximo) ─────
        if ticker not in held or _below_half_max(ticker, portfolio_state):
            if regime in _BEAR_REGIMES:
                continue
            sig = _entry_signal(ticker, rsi, ema50_above, vol_ratio, atr=atr)
            if sig:
                signals.append(sig)

    return signals


def _entry_signal(
    ticker:      str,
    rsi:         float,
    ema50_above: bool,
    vol_ratio:   float,
    atr:         float | None = None,
) -> Signal | None:
    """Avalia regras A e B com parâmetros injectados pelo Learner."""
    reasons:  list[str] = []
    strength: float     = 0.0

    rsi_ceil  = _PC["rsi_oversold_ceiling"]
    vol_os    = _PC["vol_ratio_oversold_min"]
    rsi_m_min = _PC["rsi_momentum_min"]
    rsi_m_max = _PC["rsi_momentum_max"]
    vol_mom   = _PC["vol_ratio_momentum_min"]

    # Regra A: sobrevendido em tendência ascendente
    if rsi <= rsi_ceil and ema50_above and vol_ratio >= vol_os:
        reasons.append(f"RSI-14 sobrevendido ({rsi:.1f} ≤ {rsi_ceil}) — zona de entrada")
        reasons.append("Tendência ascendente: EMA-50 > EMA-200")
        reasons.append(f"Volume {vol_ratio:.1f}× acima da média — confirmação presente")
        strength = min(1.0, 0.70 + (rsi_ceil - rsi) / 100)

    # Regra B: RSI neutro + surge de volume (momentum)
    elif rsi_m_min <= rsi <= rsi_m_max and ema50_above and vol_ratio >= vol_mom:
        reasons.append(f"RSI-14 neutro ({rsi:.1f}) em tendência ascendente")
        reasons.append(f"Volume excepcional {vol_ratio:.1f}× — sinal de momentum")
        strength = min(1.0, 0.55 + (vol_ratio - vol_mom) / 10)

    if not reasons:
        return None

    return Signal(
        ticker=ticker,
        signal_type="ENTRY",
        direction="LONG",
        strength=strength,
        reasons=reasons,
        context={
            "rsi_14":              rsi,
            "ema50_above_ema200":  ema50_above,
            "volume_ratio_vs_avg": vol_ratio,
            "atr_14":              atr,
        },
    )


def _below_half_max(ticker: str, portfolio_state: dict) -> bool:
    """True se a posição existente está abaixo de metade do max_position_pct."""
    positions = portfolio_state.get("positions", [])
    cash_free = (portfolio_state.get("cash", {}).get("free") or 0)
    total     = sum(p.get("value", p.get("value_eur", 0)) for p in positions) + cash_free
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

    max_pos_eur  = total_equity * RISK_CONFIG["max_position_pct"] / 100
    size_factor  = _PB["size_factor_pct"]          # learnable (default 0.15)
    regime_mult  = _REGIME_SIZE_FACTOR.get(regime, 1.0)
    proposals:   list[ProposedTrade] = []

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
