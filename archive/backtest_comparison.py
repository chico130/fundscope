"""
backtest_comparison.py — Simulação histórica OOS de 4 setups concorrentes

Setups em paralelo na mesma série temporal (19 tickers fixos):
  A — Clyde Puro               : sinais técnicos estáticos originais
  B — Clyde + Bonnie           : filtro de veto preditivo (threshold estático 60%)
  C — Clyde + Bonnie + CRO     : controlo de risco e atenuação sem mutação de parâmetros
  D — Ecossistema Completo     : C + Learner Activo (Fase 3 · Coordinate Descent)
                                  Arranca com DEFAULT_PARAMS. Toda sexta-feira com ≥20 trades
                                  fechados: optimiza Clyde (semanal). Dia 1 do mês com ≥50:
                                  Bonnie (mensal). Trimestre com ≥100: CRO (trimestral).
                                  Zero look-ahead: optimizer só vê trades JÁ fechados.

Garantias Anti-Overfitting:
  • Indicadores calculados com dados estritamente até cada dia (rolling causal)
  • Setups A/B/C: hiperparâmetros congelados exactamente como em produção
  • Setup D: optimizer usa APENAS trades fechados antes do trigger — sem look-ahead
  • Regra Pessimista: se TP e SL batem no mesmo dia → SL tem precedência (bear pessimista)

CLI:
    python backtest_comparison.py
    python backtest_comparison.py --start 2022-01-01 --end 2022-12-31
    python backtest_comparison.py --start 2025-01-01 --end 2026-05-17
"""
from __future__ import annotations

import argparse
import copy
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date as date_t
from pathlib import Path
from typing import Literal

import pandas as pd
import yfinance as yf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from bot.data_layer import compute_ema


# ─────────────────────────────────────────────────────────────────────────────
# Constants — Setups A / B / C  (produção congelada, zero overfitting)
# ─────────────────────────────────────────────────────────────────────────────

TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMD",  "META",   # XLK — Technology
    "JNJ",  "UNH",  "LLY",  "PFE",  "AMGN",  # XLV — Healthcare
    "AMZN", "HD",   "NKE",  "MCD",            # XLY — Consumer
    "CAT",  "GE",   "BA",                      # XLI — Industrial
    "XOM",  "CVX",  "COP",                     # XLE — Energy
]

SIM_START         = "2025-05-01"
SIM_END           = "2026-05-17"
_DATA_WARMUP_DAYS = 420

INITIAL_CAPITAL = 10_000.0

RSI_A_MAX    = 35.0
RSI_B_MIN    = 40.0
RSI_B_MAX    = 55.0
VOL_A_MIN    = 1.2
VOL_B_MIN    = 1.8
BASE_POS_PCT = 0.15
MAX_POS_PCT  = 0.20
MIN_POS_USD  = 50.0

STOP_LOSS_PCT = 5.0
TP_PCT        = 10.0
MAX_HOLD_DAYS = 10

BONNIE_BASE_THRESH   = 0.60
BONNIE_STRICT_THRESH = 0.64

CRO_MAX_DD         = 15.0
CRO_MAX_TRADES     = 10
CRO_WINDOW_N       = 25
CRO_FALLBACK_WR    = 0.48
CRO_LOW_WR_TRIGGER = 0.45

_REGIME_SIZE: dict[str, float] = {
    "bull_trending":     1.0,
    "bull_lateral":      0.6,
    "bear_correction":   0.0,
    "bear_capitulation": 0.0,
    "unknown":           0.5,
}
_BEAR        = {"bear_correction", "bear_capitulation"}
MIN_EMA_BARS = 210


# ─────────────────────────────────────────────────────────────────────────────
# Setup D — Learner constants (auto-contidos, mirrored from bot/learner.py)
# ─────────────────────────────────────────────────────────────────────────────

_D_CLYDE_DEF: dict = {
    "rsi_oversold_ceiling":   35,
    "rsi_momentum_min":       40,
    "rsi_momentum_max":       55,
    "rsi_exit_floor":         72,
    "vol_ratio_oversold_min": 1.2,
    "vol_ratio_momentum_min": 1.8,
}
_D_BONNIE_DEF: dict = {
    "base_threshold":    0.60,
    "strict_threshold":  0.64,
    "strict_trigger_wr": 0.45,
    "size_factor_pct":   0.15,
}
_D_CRO_DEF: dict = {
    "max_drawdown_limit_pct":   15.0,
    "elastic_window_n":         25,
    "elastic_fallback_wr":      0.48,
    "stop_loss_pct":            5.0,
    "take_profit_pct":          10.0,
    "max_positions_per_sector": 2,
}

# Hard bounds idênticos ao bot/learner.py _PARAM_SPACE
_D_CLYDE_SP: dict = {
    "rsi_oversold_ceiling":   {"min": 28,  "max": 45,  "step": 1.0,  "kind": "int"},
    "rsi_momentum_min":       {"min": 35,  "max": 52,  "step": 1.0,  "kind": "int"},
    "rsi_momentum_max":       {"min": 50,  "max": 65,  "step": 1.0,  "kind": "int"},
    "vol_ratio_oversold_min": {"min": 1.0, "max": 2.0, "step": 0.1,  "kind": "float"},
    "vol_ratio_momentum_min": {"min": 1.4, "max": 2.8, "step": 0.1,  "kind": "float"},
    # rsi_exit_floor excluído (não afecta entradas — optimizer não converge nele)
}
_D_BONNIE_SP: dict = {
    "base_threshold":    {"min": 0.52, "max": 0.72, "step": 0.01, "kind": "float"},
    "strict_threshold":  {"min": 0.58, "max": 0.78, "step": 0.01, "kind": "float"},
    "strict_trigger_wr": {"min": 0.35, "max": 0.55, "step": 0.01, "kind": "float"},
    "size_factor_pct":   {"min": 0.08, "max": 0.22, "step": 0.01, "kind": "float"},
}
_D_CRO_SP: dict = {
    "max_drawdown_limit_pct": {"min": 10.0, "max": 20.0, "step": 0.5,  "kind": "float"},
    "elastic_window_n":       {"min": 15,   "max": 40,   "step": 1.0,  "kind": "int"},
    "elastic_fallback_wr":    {"min": 0.42, "max": 0.55, "step": 0.01, "kind": "float"},
    "stop_loss_pct":          {"min": 3.5,  "max": 8.0,  "step": 0.5,  "kind": "float"},
    "take_profit_pct":        {"min": 7.0,  "max": 18.0, "step": 0.5,  "kind": "float"},
    # max_positions_per_sector excluído (sem rastreio de sector no backtest)
}

_D_MIN_WEEKLY    = 20
_D_MIN_MONTHLY   = 50
_D_MIN_QUARTERLY = 100
_D_EMA_ALPHA     = 0.30
_D_MIN_GAIN      = 0.05
_D_LAMBDA_L2     = 0.15
_D_VAL_SPLIT     = 0.15
_D_N_ITER: dict  = {"weekly": 60, "monthly": 80, "quarterly": 40}

_D_RNG = random.Random(42)   # RNG isolado — seed fixo para reprodutibilidade


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    ticker:      str
    entry_date:  str
    entry_price: float
    qty:         float
    stop_price:  float
    tp_price:    float
    days_open:   int  = 0
    entry_ind:   dict = field(default_factory=dict)  # populado apenas pelo Setup D


@dataclass
class ClosedTrade:
    ticker:      str
    setup:       str
    entry_date:  str
    close_date:  str
    entry_price: float
    exit_price:  float
    qty:         float
    pnl:         float
    reason:      Literal["take_profit", "stop_loss", "time_exit"]


@dataclass
class SimState:
    setup:          str
    cash:           float              = INITIAL_CAPITAL
    positions:      list[OpenPosition] = field(default_factory=list)
    closed:         list[ClosedTrade]  = field(default_factory=list)
    equity_series:  list[float]        = field(default_factory=list)

    signals_fired:             int = 0
    bonnie_vetoes:             int = 0
    cro_reductions:            int = 0
    bonnie_strict_activations: int = 0
    trades_today:              int = 0

    # Setup D only — ignorados em A/B/C
    clyde_params:   dict = field(default_factory=dict)
    bonnie_params:  dict = field(default_factory=dict)
    cro_params:     dict = field(default_factory=dict)
    virtual_trades: list = field(default_factory=list)
    mutation_count: int  = 0

    def mark_equity(self, close_prices: dict[str, float]) -> None:
        pos_val = sum(p.qty * close_prices.get(p.ticker, p.entry_price) for p in self.positions)
        self.equity_series.append(self.cash + pos_val)

    def current_equity(self) -> float:
        return self.equity_series[-1] if self.equity_series else self.cash

    def drawdown_pct(self) -> float:
        if not self.equity_series:
            return 0.0
        peak    = max(self.equity_series)
        current = self.equity_series[-1]
        return max(0.0, (peak - current) / peak * 100.0) if peak > 0 else 0.0

    def win_rate_7d(self, today: date_t) -> float:
        cutoff = today - timedelta(days=7)
        recent = [
            t for t in self.closed
            if datetime.strptime(t.close_date, "%Y-%m-%d").date() >= cutoff
        ]
        if not recent:
            return 0.5
        return sum(1 for t in recent if t.pnl > 0) / len(recent)


# ─────────────────────────────────────────────────────────────────────────────
# Download de dados
# ─────────────────────────────────────────────────────────────────────────────

def _data_start() -> str:
    sim = datetime.strptime(SIM_START, "%Y-%m-%d")
    return (sim - timedelta(days=_DATA_WARMUP_DAYS)).strftime("%Y-%m-%d")


def _fetch_end() -> str:
    return (datetime.strptime(SIM_END, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")


def load_ticker_data() -> dict[str, pd.DataFrame]:
    ds = _data_start()
    print(f"[DATA] A descarregar {len(TICKERS)} tickers ({ds} → {SIM_END})...")
    end = _fetch_end()
    result: dict[str, pd.DataFrame] = {}
    for ticker in TICKERS:
        try:
            df = yf.Ticker(ticker).history(
                start=ds, end=end, interval="1d", auto_adjust=True
            ).dropna(subset=["Close"])
            if not df.empty:
                result[ticker] = df
        except Exception as exc:
            print(f"  [WARN] {ticker}: {exc}")
    print(f"[DATA] {len(result)}/{len(TICKERS)} tickers carregados.")
    return result


def load_spy_rsp() -> tuple[pd.DataFrame, pd.DataFrame]:
    print("[DATA] A descarregar SPY/RSP para regime...")
    ds  = _data_start()
    end = _fetch_end()
    spy = yf.Ticker("SPY").history(start=ds, end=end, interval="1d", auto_adjust=True).dropna(subset=["Close"])
    rsp = yf.Ticker("RSP").history(start=ds, end=end, interval="1d", auto_adjust=True).dropna(subset=["Close"])
    return spy, rsp


def get_trading_days(spy_df: pd.DataFrame) -> list[str]:
    start = datetime.strptime(SIM_START, "%Y-%m-%d").date()
    end   = datetime.strptime(SIM_END,   "%Y-%m-%d").date()
    return sorted(
        dt.strftime("%Y-%m-%d")
        for dt in spy_df.index
        if start <= dt.date() <= end
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pré-computação de indicadores e regime
# ─────────────────────────────────────────────────────────────────────────────

def precompute_indicators(
    all_dfs:      dict[str, pd.DataFrame],
    trading_days: list[str],
) -> dict[str, dict[str, dict]]:
    print(f"[PREP] A pré-calcular indicadores ({len(all_dfs)} tickers × {len(trading_days)} dias)...")

    cache: dict[str, dict[str, dict]] = {}

    for ticker, df in all_dfs.items():
        cache[ticker] = {}

        close = df["Close"].astype(float)
        vol   = df["Volume"].astype(float)

        ema50  = close.ewm(span=50,  adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        delta    = close.diff()
        avg_gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        avg_loss = (-delta).clip(lower=0).ewm(com=13, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0.0, float("nan"))
        rsi      = (100.0 - (100.0 / (1.0 + rs))).round(2)

        avg_vol   = vol.rolling(20).mean()
        vol_ratio = (vol / avg_vol).round(3)

        bar_count = pd.Series(range(1, len(df) + 1), index=df.index, dtype=int)

        for day_str in trading_days:
            target = datetime.strptime(day_str, "%Y-%m-%d").date()
            row    = df[df.index.date == target]
            if row.empty:
                continue
            idx = row.index[0]
            if int(bar_count.loc[idx]) < MIN_EMA_BARS:
                continue

            rsi_val = rsi.get(idx)
            e50     = ema50.get(idx)
            e200    = ema200.get(idx)
            vr      = vol_ratio.get(idx)

            if any(pd.isna(v) for v in (rsi_val, e50, e200, vr)):
                continue

            cache[ticker][day_str] = {
                "rsi_14":          float(rsi_val),
                "ema50_above_200": float(e50) > float(e200),
                "vol_ratio":       float(vr),
                "close":           float(row["Close"].iloc[0]),
                "high":            float(row["High"].iloc[0]),
                "low":             float(row["Low"].iloc[0]),
            }

    return cache


def precompute_regimes(
    spy_df:       pd.DataFrame,
    rsp_df:       pd.DataFrame,
    trading_days: list[str],
) -> dict[str, str]:
    print(f"[PREP] A calcular regime para {len(trading_days)} dias...")

    spy_close = spy_df["Close"].astype(float)
    rsp_close = rsp_df["Close"].astype(float)
    regimes:  dict[str, str] = {}

    for day_str in trading_days:
        target = datetime.strptime(day_str, "%Y-%m-%d").date()

        spy = spy_close[spy_close.index.date <= target]
        rsp = rsp_close[rsp_close.index.date <= target]

        if len(spy) < MIN_EMA_BARS:
            regimes[day_str] = "unknown"
            continue

        closes   = list(spy.astype(float))
        spy_last = closes[-1]
        ema200   = compute_ema(closes, 200)

        if ema200 is None:
            regimes[day_str] = "unknown"
            continue

        pct_from_ema200 = (spy_last - ema200) / ema200 * 100.0
        ret_20d = (spy_last - closes[-20]) / closes[-20] if len(closes) >= 20 else 0.0

        breadth_ok = True
        if len(rsp) >= 20 and len(spy) >= 20:
            rn  = float(rsp.iloc[-1])  / float(spy.iloc[-1])
            r20 = float(rsp.iloc[-20]) / float(spy.iloc[-20])
            breadth_ok = (rn - r20) / r20 >= -0.02

        if pct_from_ema200 <= -5.0:
            r = "bear_capitulation" if ret_20d < -0.10 else "bear_correction"
        elif pct_from_ema200 < 0.0:
            r = "bull_lateral"
        else:
            r = "bull_trending" if breadth_ok else "bull_lateral"

        regimes[day_str] = r

    return regimes


def build_ohlc_cache(
    all_dfs:      dict[str, pd.DataFrame],
    trading_days: list[str],
) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for ticker, df in all_dfs.items():
        result[ticker] = {}
        for day_str in trading_days:
            target = datetime.strptime(day_str, "%Y-%m-%d").date()
            row    = df[df.index.date == target]
            if not row.empty:
                result[ticker][day_str] = {
                    "high":  float(row["High"].iloc[0]),
                    "low":   float(row["Low"].iloc[0]),
                    "close": float(row["Close"].iloc[0]),
                }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Sinais — Clyde (Setups A/B/C — congelados)
# ─────────────────────────────────────────────────────────────────────────────

def clyde_signal(ind: dict, regime: str) -> tuple[str, float]:
    rsi       = ind["rsi_14"]
    ema_above = ind["ema50_above_200"]
    vol       = ind["vol_ratio"]

    if regime in _BEAR:
        return "HOLD", 0.0

    if rsi <= RSI_A_MAX and ema_above and vol >= VOL_A_MIN:
        return "BUY", round(min(1.0, 0.70 + (RSI_A_MAX - rsi) / 100), 4)

    if RSI_B_MIN <= rsi <= RSI_B_MAX and ema_above and vol >= VOL_B_MIN:
        return "BUY", round(min(1.0, 0.55 + (vol - VOL_B_MIN) / 10), 4)

    return "HOLD", 0.0


def clyde_signal_d(ind: dict, regime: str, cp: dict) -> tuple[str, float]:
    """Versão do Setup D: usa parâmetros dinâmicos do learner em vez de constantes."""
    rsi       = ind["rsi_14"]
    ema_above = ind["ema50_above_200"]
    vol       = ind["vol_ratio"]

    if regime in _BEAR:
        return "HOLD", 0.0

    rsi_a = cp.get("rsi_oversold_ceiling",   35)
    vo_a  = cp.get("vol_ratio_oversold_min", 1.2)
    rb_lo = cp.get("rsi_momentum_min",       40)
    rb_hi = cp.get("rsi_momentum_max",       55)
    vo_b  = cp.get("vol_ratio_momentum_min", 1.8)

    if rsi <= rsi_a and ema_above and vol >= vo_a:
        return "BUY", round(min(1.0, 0.70 + (rsi_a - rsi) / 100), 4)

    if rb_lo <= rsi <= rb_hi and ema_above and vol >= vo_b:
        return "BUY", round(min(1.0, 0.55 + (vol - vo_b) / 10), 4)

    return "HOLD", 0.0


# ─────────────────────────────────────────────────────────────────────────────
# CRO — Setups A/B/C (funções estáticas)
# ─────────────────────────────────────────────────────────────────────────────

def elastic_target_wr(closed: list[ClosedTrade]) -> float:
    if len(closed) < CRO_WINDOW_N:
        return CRO_FALLBACK_WR
    recent = closed[-CRO_WINDOW_N:]
    return round(sum(1 for t in recent if t.pnl > 0) / CRO_WINDOW_N, 4)


def dynamic_bonnie_threshold(closed: list[ClosedTrade]) -> float:
    wr = elastic_target_wr(closed)
    return BONNIE_STRICT_THRESH if wr < CRO_LOW_WR_TRIGGER else BONNIE_BASE_THRESH


def cro_risk_factor(
    win_rate_7d:    float,
    drawdown_pct:   float,
    regime:         str,
    elastic_target: float,
) -> float:
    wr_adj = max(0.5, min(1.2, win_rate_7d / elastic_target if elastic_target > 0 else 1.0))
    dd_adj = max(0.3, min(1.0, 1.0 - drawdown_pct / CRO_MAX_DD))
    reg_f  = _REGIME_SIZE.get(regime, 0.5)
    return round(wr_adj * dd_adj * reg_f, 4)


# ─────────────────────────────────────────────────────────────────────────────
# CRO — Setup D (usa cro_params dinâmicos)
# ─────────────────────────────────────────────────────────────────────────────

def _d_elastic_target(closed: list[ClosedTrade], cro_p: dict) -> float:
    window   = int(cro_p.get("elastic_window_n", 25))
    fallback = cro_p.get("elastic_fallback_wr", 0.48)
    if len(closed) < window:
        return fallback
    return round(sum(1 for t in closed[-window:] if t.pnl > 0) / window, 4)


def _d_bonnie_thresh(closed: list[ClosedTrade], bonnie_p: dict, cro_p: dict) -> float:
    wr      = _d_elastic_target(closed, cro_p)
    trigger = bonnie_p.get("strict_trigger_wr", 0.45)
    return (bonnie_p.get("strict_threshold", 0.64)
            if wr < trigger else bonnie_p.get("base_threshold", 0.60))


# ─────────────────────────────────────────────────────────────────────────────
# Learner optimizer — Setup D  (auto-contido, sem I/O, sem imports do bot)
# ─────────────────────────────────────────────────────────────────────────────

def _d_ema(old: float, new: float) -> float:
    return old * (1 - _D_EMA_ALPHA) + new * _D_EMA_ALPHA


def _d_l2(params: dict, defaults: dict, space: dict) -> float:
    total_sq = count = 0
    for name, val in params.items():
        spec = space.get(name)
        if spec is None:
            continue
        r = spec["max"] - spec["min"]
        if r > 0:
            delta     = (float(val) - float(defaults.get(name, val))) / r
            total_sq += delta ** 2
            count    += 1
    return 1.0 if count == 0 else max(0.85, 1.0 - _D_LAMBDA_L2 * (total_sq / count))


def _d_maxdd(results: list[float]) -> float:
    peak = total = max_dd = 0.0
    for r in results:
        total += r
        if total > peak:
            peak = total
        if peak > 0:
            max_dd = max(max_dd, (peak - total) / peak * 100)
    return round(max_dd, 2)


def _d_pfc(trades: list[dict]) -> float:
    """Profit Factor × Calmar Factor — fitness para Clyde e Bonnie."""
    if not trades:
        return 0.5
    results  = [t.get("result_eur", 0) or 0 for t in trades]
    gain     = sum(r for r in results if r > 0)
    loss     = abs(sum(r for r in results if r < 0))
    pf       = gain / (loss + 0.01)
    calmar_f = max(0.4, 1.0 - _d_maxdd(results) / 15.0)
    return round(min(pf * calmar_f, 10.0), 4)


def _d_would_enter(trade: dict, cp: dict) -> bool:
    ctx    = trade.get("context", {})
    rsi    = ctx.get("rsi_14")
    vol    = ctx.get("volume_ratio_vs_avg", 1.0)
    ema_up = ctx.get("ema50_above_ema200", True)
    if rsi is None:
        return True
    ceil   = cp.get("rsi_oversold_ceiling",   35)
    vo_min = cp.get("vol_ratio_oversold_min", 1.2)
    m_min  = cp.get("rsi_momentum_min",       40)
    m_max  = cp.get("rsi_momentum_max",       55)
    vm_min = cp.get("vol_ratio_momentum_min", 1.8)
    return (
        (rsi <= ceil and ema_up and vol >= vo_min) or
        (m_min <= rsi <= m_max and ema_up and vol >= vm_min)
    )


def _d_fit_clyde(cp: dict, trades: list[dict]) -> float:
    return _d_pfc([t for t in trades if _d_would_enter(t, cp)])


def _d_fit_bonnie(bp: dict, trades: list[dict]) -> float:
    thr      = bp.get("base_threshold", 0.60)
    accepted = [t for t in trades if t.get("signal_strength", 0) >= thr]
    return _d_pfc(accepted)


def _d_fit_cro(cp: dict, trades: list[dict]) -> float:
    if not trades:
        return 0.5
    results   = [t.get("result_eur", 0) or 0 for t in trades]
    total_pnl = sum(results)
    drawdown  = _d_maxdd(results)
    max_dd    = cp.get("max_drawdown_limit_pct", 15.0)
    dd_pen    = max(0.3, 1.0 - drawdown / max_dd) if max_dd > 0 else 0.3
    ann       = total_pnl / max(1, len(trades)) * 52
    calmar    = ann / max(drawdown, 0.01)
    return max(0.01, min(calmar * dd_pen, 10.0))


def _d_split(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    s  = sorted(trades, key=lambda t: t.get("datetime", ""))
    at = max(1, int(len(s) * (1 - _D_VAL_SPLIT)))
    return s[:at], s[at:]


def _d_descent(
    names:    list[str],
    current:  dict,
    defaults: dict,
    space:    dict,
    train:    list[dict],
    val:      list[dict],
    fit_fn,
    n_iter:   int,
) -> tuple[dict, float]:
    if not train:
        return copy.deepcopy(current), 0.5

    best  = copy.deepcopy(current)
    f_tr  = fit_fn(best, train)
    f_val = fit_fn(best, val) if val else f_tr

    for _ in range(n_iter):
        name = _D_RNG.choice(names)
        spec = space.get(name)
        if spec is None:
            continue
        direction = _D_RNG.choice([-1, 1])
        raw       = float(best[name]) + direction * spec["step"]
        clamped   = max(spec["min"], min(spec["max"], raw))
        candidate = int(round(clamped)) if spec["kind"] == "int" else round(clamped, 6)

        trial       = copy.deepcopy(best)
        trial[name] = candidate
        f_tr2  = fit_fn(trial, train)
        f_val2 = fit_fn(trial, val) if val else f_tr2

        # Aceita se melhora treino em ≥5% E não degrada validação em >15%
        if f_tr2 > f_tr * (1 + _D_MIN_GAIN) and f_val2 > f_val * 0.85:
            smoothed   = _d_ema(float(best[name]), float(candidate))
            best[name] = int(round(smoothed)) if spec["kind"] == "int" else round(smoothed, 6)
            f_tr  = fit_fn(best, train)
            f_val = fit_fn(best, val) if val else f_tr

    f_final = fit_fn(best, train) * _d_l2(best, defaults, space)
    return best, f_final


def _d_optimize_clyde(virtual_trades: list[dict], cp: dict) -> tuple[dict, bool]:
    """Semanal: optimiza parâmetros de entrada do Clyde. Retorna (params, mutado)."""
    names      = list(_D_CLYDE_SP.keys())
    train, val = _d_split(virtual_trades)
    f_before   = _d_fit_clyde(cp, train)
    optimized, f_after = _d_descent(
        names, cp, _D_CLYDE_DEF, _D_CLYDE_SP,
        train, val, _d_fit_clyde, _D_N_ITER["weekly"],
    )
    if f_after > f_before * (1 + _D_MIN_GAIN):
        return optimized, True
    return cp, False


def _d_optimize_bonnie(virtual_trades: list[dict], bp: dict) -> tuple[dict, bool]:
    """Mensal: optimiza thresholds e size_factor da Bonnie."""
    names      = list(_D_BONNIE_SP.keys())
    train, val = _d_split(virtual_trades)
    f_before   = _d_fit_bonnie(bp, train)
    optimized, f_after = _d_descent(
        names, bp, _D_BONNIE_DEF, _D_BONNIE_SP,
        train, val, _d_fit_bonnie, _D_N_ITER["monthly"],
    )
    if f_after > f_before * (1 + _D_MIN_GAIN):
        return optimized, True
    return bp, False


def _d_optimize_cro(virtual_trades: list[dict], cro_p: dict) -> tuple[dict, bool]:
    """Trimestral: optimiza stop_loss_pct, take_profit_pct e limites de drawdown."""
    names      = list(_D_CRO_SP.keys())
    train, val = _d_split(virtual_trades)
    f_before   = _d_fit_cro(cro_p, train)
    optimized, f_after = _d_descent(
        names, cro_p, _D_CRO_DEF, _D_CRO_SP,
        train, val, _d_fit_cro, _D_N_ITER["quarterly"],
    )
    if f_after > f_before * (1 + _D_MIN_GAIN):
        return optimized, True
    return cro_p, False


# ─────────────────────────────────────────────────────────────────────────────
# Dimensionamento de posições
# ─────────────────────────────────────────────────────────────────────────────

def _position_size(
    equity:   float,
    strength: float,
    regime:   str,
    cash:     float,
    size_f:   float = BASE_POS_PCT,
    cro_rf:   float = 1.0,
) -> float:
    reg_f   = _REGIME_SIZE.get(regime, 0.0)
    base    = strength * equity * size_f * reg_f
    cro_cap = equity * MAX_POS_PCT * cro_rf
    size    = min(base, cro_cap, cash * 0.95)
    return size if size >= MIN_POS_USD else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Simulação — Setups A / B / C
# ─────────────────────────────────────────────────────────────────────────────

def process_day(
    state:      SimState,
    day_str:    str,
    regime:     str,
    ind_cache:  dict[str, dict[str, dict]],
    ohlc_cache: dict[str, dict[str, dict]],
) -> None:
    today              = datetime.strptime(day_str, "%Y-%m-%d").date()
    state.trades_today = 0

    close_prices: dict[str, float] = {
        t: d[day_str]["close"]
        for t, d in ohlc_cache.items()
        if day_str in d
    }

    # ── Passo 1: processar saídas ────────────────────────────────────────────
    still_open: list[OpenPosition] = []

    for pos in state.positions:
        day_ohlc = ohlc_cache.get(pos.ticker, {}).get(day_str)

        if day_ohlc is None:
            pos.days_open += 1
            still_open.append(pos)
            continue

        high  = day_ohlc["high"]
        low   = day_ohlc["low"]
        close = day_ohlc["close"]

        hit_tp = high >= pos.tp_price
        hit_sl = low  <= pos.stop_price

        if hit_sl:
            exit_price, reason = pos.stop_price, "stop_loss"
        elif hit_tp:
            exit_price, reason = pos.tp_price, "take_profit"
        elif pos.days_open >= MAX_HOLD_DAYS:
            exit_price, reason = close, "time_exit"
        else:
            pos.days_open += 1
            still_open.append(pos)
            continue

        pnl          = (exit_price - pos.entry_price) * pos.qty
        state.cash  += pos.qty * exit_price
        state.closed.append(ClosedTrade(
            ticker=pos.ticker, setup=state.setup,
            entry_date=pos.entry_date, close_date=day_str,
            entry_price=pos.entry_price, exit_price=exit_price,
            qty=pos.qty, pnl=round(pnl, 4), reason=reason,
        ))

    state.positions = still_open
    state.mark_equity(close_prices)

    # ── Passo 2: novas entradas ───────────────────────────────────────────────
    held = {p.ticker for p in state.positions}

    if state.setup == "C":
        bonnie_thr = dynamic_bonnie_threshold(state.closed)
        el_target  = elastic_target_wr(state.closed)
        if bonnie_thr > BONNIE_BASE_THRESH:
            state.bonnie_strict_activations += 1
    else:
        bonnie_thr = BONNIE_BASE_THRESH
        el_target  = CRO_FALLBACK_WR

    for ticker in TICKERS:
        if ticker in held:
            continue

        ind = ind_cache.get(ticker, {}).get(day_str)
        if ind is None:
            continue

        sig, strength = clyde_signal(ind, regime)
        if sig != "BUY":
            continue

        state.signals_fired += 1

        if state.setup in ("B", "C") and strength < bonnie_thr:
            state.bonnie_vetoes += 1
            continue

        cro_rf = 1.0
        if state.setup == "C":
            dd = state.drawdown_pct()
            if dd > CRO_MAX_DD:
                state.bonnie_vetoes += 1
                continue
            if state.trades_today >= CRO_MAX_TRADES:
                continue
            wr     = state.win_rate_7d(today)
            cro_rf = cro_risk_factor(wr, dd, regime, el_target)

        equity = state.current_equity()
        size   = _position_size(equity, strength, regime, state.cash, BASE_POS_PCT, cro_rf)

        if size <= 0:
            continue

        entry = ind["close"]
        if entry <= 0:
            continue

        qty = round(size / entry, 6)

        if state.setup == "C" and cro_rf < 1.0:
            reg_f      = _REGIME_SIZE.get(regime, 0.0)
            unmodified = min(strength * equity * BASE_POS_PCT * reg_f,
                             equity * MAX_POS_PCT, state.cash * 0.95)
            if size < unmodified - 0.01:
                state.cro_reductions += 1

        state.cash -= qty * entry
        state.positions.append(OpenPosition(
            ticker=ticker, entry_date=day_str, entry_price=entry, qty=qty,
            stop_price=round(entry * (1.0 - STOP_LOSS_PCT / 100.0), 4),
            tp_price=round(entry   * (1.0 + TP_PCT          / 100.0), 4),
        ))
        state.trades_today += 1
        held.add(ticker)


def run_simulation(
    setup:        str,
    trading_days: list[str],
    regimes:      dict[str, str],
    ind_cache:    dict[str, dict[str, dict]],
    ohlc_cache:   dict[str, dict[str, dict]],
) -> SimState:
    state = SimState(setup=setup)
    n     = len(trading_days)

    for i, day_str in enumerate(trading_days):
        regime = regimes.get(day_str, "unknown")
        process_day(state, day_str, regime, ind_cache, ohlc_cache)

        if (i + 1) % 60 == 0 or i == n - 1:
            eq = state.current_equity()
            print(f"  [{setup}] {day_str} ({i + 1}/{n}) | Equity: ${eq:,.0f} | "
                  f"Posições: {len(state.positions)} | Trades: {len(state.closed)}")

    last_day     = trading_days[-1]
    close_prices = {t: d[last_day]["close"] for t, d in ohlc_cache.items() if last_day in d}

    for pos in state.positions:
        ep  = close_prices.get(pos.ticker, pos.entry_price)
        pnl = (ep - pos.entry_price) * pos.qty
        state.cash += pos.qty * ep
        state.closed.append(ClosedTrade(
            ticker=pos.ticker, setup=setup,
            entry_date=pos.entry_date, close_date=last_day,
            entry_price=pos.entry_price, exit_price=ep,
            qty=pos.qty, pnl=round(pnl, 4), reason="time_exit",
        ))
    state.positions = []

    return state


# ─────────────────────────────────────────────────────────────────────────────
# Simulação — Setup D (Learner Activo)
# ─────────────────────────────────────────────────────────────────────────────

def process_day_d(
    state:      SimState,
    day_str:    str,
    regime:     str,
    ind_cache:  dict[str, dict[str, dict]],
    ohlc_cache: dict[str, dict[str, dict]],
) -> None:
    today              = datetime.strptime(day_str, "%Y-%m-%d").date()
    state.trades_today = 0

    close_prices: dict[str, float] = {
        t: d[day_str]["close"]
        for t, d in ohlc_cache.items()
        if day_str in d
    }

    # ── Passo 1: processar saídas ────────────────────────────────────────────
    still_open: list[OpenPosition] = []

    for pos in state.positions:
        day_ohlc = ohlc_cache.get(pos.ticker, {}).get(day_str)

        if day_ohlc is None:
            pos.days_open += 1
            still_open.append(pos)
            continue

        high  = day_ohlc["high"]
        low   = day_ohlc["low"]
        close = day_ohlc["close"]

        hit_tp = high >= pos.tp_price
        hit_sl = low  <= pos.stop_price

        if hit_sl:
            exit_price, reason = pos.stop_price, "stop_loss"
        elif hit_tp:
            exit_price, reason = pos.tp_price, "take_profit"
        elif pos.days_open >= MAX_HOLD_DAYS:
            exit_price, reason = close, "time_exit"
        else:
            pos.days_open += 1
            still_open.append(pos)
            continue

        pnl          = (exit_price - pos.entry_price) * pos.qty
        state.cash  += pos.qty * exit_price
        ct = ClosedTrade(
            ticker=pos.ticker, setup="D",
            entry_date=pos.entry_date, close_date=day_str,
            entry_price=pos.entry_price, exit_price=exit_price,
            qty=pos.qty, pnl=round(pnl, 4), reason=reason,
        )
        state.closed.append(ct)

        # Regista trade virtual para o optimizer (usa contexto capturado na entrada)
        state.virtual_trades.append({
            "side":            "BUY",
            "result_eur":      round(pnl, 4),
            "signal_strength": pos.entry_ind.get("strength", 0.7),
            "datetime":        pos.entry_date + "T00:00:00Z",
            "context": {
                "rsi_14":              pos.entry_ind.get("rsi_14"),
                "volume_ratio_vs_avg": pos.entry_ind.get("vol_ratio"),
                "ema50_above_ema200":  pos.entry_ind.get("ema50_above_200"),
            },
        })

    state.positions = still_open
    state.mark_equity(close_prices)

    # ── Passo 2: novas entradas ───────────────────────────────────────────────
    held = {p.ticker for p in state.positions}

    bonnie_thr = _d_bonnie_thresh(state.closed, state.bonnie_params, state.cro_params)
    if bonnie_thr > state.bonnie_params.get("base_threshold", BONNIE_BASE_THRESH):
        state.bonnie_strict_activations += 1

    el_target  = _d_elastic_target(state.closed, state.cro_params)
    max_dd_lim = state.cro_params.get("max_drawdown_limit_pct", CRO_MAX_DD)
    sl_pct     = state.cro_params.get("stop_loss_pct",   STOP_LOSS_PCT)
    tp_pct     = state.cro_params.get("take_profit_pct", TP_PCT)
    size_f     = state.bonnie_params.get("size_factor_pct", BASE_POS_PCT)

    for ticker in TICKERS:
        if ticker in held:
            continue

        ind = ind_cache.get(ticker, {}).get(day_str)
        if ind is None:
            continue

        sig, strength = clyde_signal_d(ind, regime, state.clyde_params)
        if sig != "BUY":
            continue

        state.signals_fired += 1

        # Bonnie: threshold controlado pelo learner
        if strength < bonnie_thr:
            state.bonnie_vetoes += 1
            continue

        dd = state.drawdown_pct()

        # CRO disjuntor 1 — max drawdown controlado pelo learner
        if dd > max_dd_lim:
            state.bonnie_vetoes += 1
            continue

        # CRO disjuntor 2 — limite diário de trades
        if state.trades_today >= CRO_MAX_TRADES:
            continue

        wr     = state.win_rate_7d(today)
        cro_rf = cro_risk_factor(wr, dd, regime, el_target)

        equity = state.current_equity()
        size   = _position_size(equity, strength, regime, state.cash, size_f, cro_rf)

        if size <= 0:
            continue

        entry = ind["close"]
        if entry <= 0:
            continue

        qty = round(size / entry, 6)

        if cro_rf < 1.0:
            reg_f  = _REGIME_SIZE.get(regime, 0.0)
            unmod  = min(strength * equity * size_f * reg_f,
                         equity * MAX_POS_PCT, state.cash * 0.95)
            if size < unmod - 0.01:
                state.cro_reductions += 1

        state.cash -= qty * entry
        state.positions.append(OpenPosition(
            ticker=ticker, entry_date=day_str, entry_price=entry, qty=qty,
            stop_price=round(entry * (1.0 - sl_pct / 100.0), 4),
            tp_price=round(entry   * (1.0 + tp_pct / 100.0), 4),
            entry_ind={
                "rsi_14":          ind["rsi_14"],
                "vol_ratio":       ind["vol_ratio"],
                "ema50_above_200": ind["ema50_above_200"],
                "strength":        strength,
            },
        ))
        state.trades_today += 1
        held.add(ticker)


def run_simulation_d(
    trading_days: list[str],
    regimes:      dict[str, str],
    ind_cache:    dict[str, dict[str, dict]],
    ohlc_cache:   dict[str, dict[str, dict]],
) -> SimState:
    state = SimState(
        setup         = "D",
        clyde_params  = copy.deepcopy(_D_CLYDE_DEF),
        bonnie_params = copy.deepcopy(_D_BONNIE_DEF),
        cro_params    = copy.deepcopy(_D_CRO_DEF),
    )
    n = len(trading_days)

    optimized_months   : set = set()   # (year, month) já processados
    optimized_quarters : set = set()   # (year, quarter) já processados

    for i, day_str in enumerate(trading_days):
        regime = regimes.get(day_str, "unknown")
        process_day_d(state, day_str, regime, ind_cache, ohlc_cache)

        today = datetime.strptime(day_str, "%Y-%m-%d").date()

        # ── Trigger semanal: Sexta-feira (sábado virtual = trigger) ──────────
        if today.weekday() == 4 and len(state.virtual_trades) >= _D_MIN_WEEKLY:
            new_cp, mutated = _d_optimize_clyde(state.virtual_trades, state.clyde_params)
            if mutated:
                state.clyde_params   = new_cp
                state.mutation_count += 1
                rsi = new_cp.get("rsi_oversold_ceiling", 35)
                vol = new_cp.get("vol_ratio_oversold_min", 1.2)
                print(f"  [D] Mut.semanal #{state.mutation_count} — "
                      f"RSI≤{rsi} vol≥{vol:.1f} ({day_str})")

        # ── Trigger mensal: 1.º dia útil do mês ──────────────────────────────
        month_key = (today.year, today.month)
        if month_key not in optimized_months and today.day <= 5:
            if len(state.virtual_trades) >= _D_MIN_MONTHLY:
                new_bp, mutated = _d_optimize_bonnie(state.virtual_trades, state.bonnie_params)
                if mutated:
                    state.bonnie_params  = new_bp
                    state.mutation_count += 1
                    thr = new_bp.get("base_threshold", 0.60)
                    sf  = new_bp.get("size_factor_pct", 0.15)
                    print(f"  [D] Mut.mensal #{state.mutation_count} — "
                          f"thresh={thr:.2f} size={sf:.2f} ({day_str})")
            optimized_months.add(month_key)

        # ── Trigger trimestral: 1.º dia útil do trimestre ────────────────────
        quarter_key = (today.year, (today.month - 1) // 3)
        if (quarter_key not in optimized_quarters
                and today.month in {1, 4, 7, 10} and today.day <= 5):
            if len(state.virtual_trades) >= _D_MIN_QUARTERLY:
                new_cro, mutated = _d_optimize_cro(state.virtual_trades, state.cro_params)
                if mutated:
                    state.cro_params     = new_cro
                    state.mutation_count += 1
                    sl  = new_cro.get("stop_loss_pct",          5.0)
                    tp  = new_cro.get("take_profit_pct",        10.0)
                    mdd = new_cro.get("max_drawdown_limit_pct", 15.0)
                    print(f"  [D] Mut.trimestral #{state.mutation_count} — "
                          f"SL={sl}% TP={tp}% MaxDD={mdd}% ({day_str})")
            optimized_quarters.add(quarter_key)

        if (i + 1) % 60 == 0 or i == n - 1:
            eq  = state.current_equity()
            rsi = state.clyde_params.get("rsi_oversold_ceiling", 35)
            print(f"  [D] {day_str} ({i + 1}/{n}) | Equity: ${eq:,.0f} | "
                  f"Posições: {len(state.positions)} | Trades: {len(state.closed)} | "
                  f"Mutações: {state.mutation_count} | RSI≤{rsi}")

    # Força fecho de todas as posições abertas ao fecho do último dia
    last_day     = trading_days[-1]
    close_prices = {t: d[last_day]["close"] for t, d in ohlc_cache.items() if last_day in d}

    for pos in state.positions:
        ep  = close_prices.get(pos.ticker, pos.entry_price)
        pnl = (ep - pos.entry_price) * pos.qty
        state.cash += pos.qty * ep
        state.closed.append(ClosedTrade(
            ticker=pos.ticker, setup="D",
            entry_date=pos.entry_date, close_date=last_day,
            entry_price=pos.entry_price, exit_price=ep,
            qty=pos.qty, pnl=round(pnl, 4), reason="time_exit",
        ))
    state.positions = []

    return state


# ─────────────────────────────────────────────────────────────────────────────
# Métricas
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(state: SimState) -> dict:
    closed = state.closed
    final  = state.cash

    wins   = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]

    gain = sum(t.pnl for t in wins)
    loss = abs(sum(t.pnl for t in losses))
    pf   = round(gain / loss, 2) if loss > 0 else (float("inf") if gain > 0 else 0.0)

    series = state.equity_series
    max_dd, peak = 0.0, (series[0] if series else INITIAL_CAPITAL)
    for v in series:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

    ret_pct = round((final / INITIAL_CAPITAL - 1.0) * 100.0, 2)
    max_dd  = round(max_dd, 2)
    calmar  = round(ret_pct / max_dd, 2) if max_dd > 0.01 else (99.0 if ret_pct > 0 else 0.0)

    return {
        "final_equity":              round(final, 2),
        "net_profit":                round(final - INITIAL_CAPITAL, 2),
        "return_pct":                ret_pct,
        "win_rate_pct":              round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0,
        "max_dd_pct":                max_dd,
        "profit_factor":             pf,
        "calmar_ratio":              calmar,
        "total_trades":              len(closed),
        "signals_fired":             state.signals_fired,
        "bonnie_vetoes":             state.bonnie_vetoes,
        "cro_reductions":            state.cro_reductions,
        "bonnie_strict_activations": state.bonnie_strict_activations,
        "mutation_count":            state.mutation_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Relatório — tabela Markdown
# ─────────────────────────────────────────────────────────────────────────────

def _pf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


def _sign(v: float) -> str:
    return f"${v:>+,.2f}"


def _cal(v: float) -> str:
    if v >= 99.0:
        return "∞"
    if v <= -99.0:
        return "-∞"
    return f"{v:.2f}"


def format_report(m: dict[str, dict]) -> str:
    a, b, c, d = m["A"], m["B"], m["C"], m["D"]
    sep = "─" * 115

    rows = [
        ("Capital Final ($)",          f"${a['final_equity']:>12,.2f}",      f"${b['final_equity']:>12,.2f}",      f"${c['final_equity']:>12,.2f}",      f"${d['final_equity']:>12,.2f}"),
        ("Lucro Líquido ($)",          _sign(a['net_profit']),                _sign(b['net_profit']),                _sign(c['net_profit']),                _sign(d['net_profit'])),
        ("Retorno Total (%)",          f"{a['return_pct']:>+.2f}%",           f"{b['return_pct']:>+.2f}%",           f"{c['return_pct']:>+.2f}%",           f"{d['return_pct']:>+.2f}%"),
        ("Win Rate (%)",               f"{a['win_rate_pct']:>.1f}%",          f"{b['win_rate_pct']:>.1f}%",          f"{c['win_rate_pct']:>.1f}%",          f"{d['win_rate_pct']:>.1f}%"),
        ("**Max Drawdown (%)**",       f"**{a['max_dd_pct']:.2f}%**",         f"**{b['max_dd_pct']:.2f}%**",         f"**{c['max_dd_pct']:.2f}%**",         f"**{d['max_dd_pct']:.2f}%**"),
        ("Profit Factor",              _pf(a['profit_factor']),               _pf(b['profit_factor']),               _pf(c['profit_factor']),               _pf(d['profit_factor'])),
        ("Calmar Ratio",               _cal(a['calmar_ratio']),               _cal(b['calmar_ratio']),               _cal(c['calmar_ratio']),               _cal(d['calmar_ratio'])),
        ("Sinais Clyde Disparados",    f"{a['signals_fired']:,}",             f"{b['signals_fired']:,}",             f"{c['signals_fired']:,}",             f"{d['signals_fired']:,}"),
        ("Trades Executados",          f"{a['total_trades']:,}",              f"{b['total_trades']:,}",              f"{c['total_trades']:,}",              f"{d['total_trades']:,}"),
        ("Vetos Bonnie",               "N/A",                                 f"{b['bonnie_vetoes']:,}",             f"{c['bonnie_vetoes']:,}",             f"{d['bonnie_vetoes']:,}"),
        ("Posições Atenuadas CRO",     "N/A",                                 "N/A",                                 f"{c['cro_reductions']:,}",             f"{d['cro_reductions']:,}"),
        ("Dias Bonnie Strict (64%+)",  "N/A",                                 "N/A",                                 f"{c['bonnie_strict_activations']:,}",  f"{d['bonnie_strict_activations']:,}"),
        ("**Mutações Learner**",       "N/A",                                 "N/A",                                 "N/A",                                 f"**{d['mutation_count']}**"),
    ]

    hdr  = (f"| {'Métrica':<30} | {'Setup A — Clyde':^20} "
            f"| {'Setup B — +Bonnie':^20} | {'Setup C — +CRO':^20} "
            f"| {'Setup D — +Learner':^20} |")
    sep2 = f"|{'-'*32}|{'-'*22}|{'-'*22}|{'-'*22}|{'-'*22}|"

    table = [hdr, sep2]
    for label, va, vb, vc, vd in rows:
        table.append(f"| {label:<30} | {va:^20} | {vb:^20} | {vc:^20} | {vd:^20} |")

    lines = [
        "",
        sep,
        f"  FUNDSCOPE — Stress Test Bear Market 2022  |  {SIM_START} → {SIM_END}",
        f"  Universe: {len(TICKERS)} tickers   Capital inicial: ${INITIAL_CAPITAL:,.0f} USD",
        f"  SL: {STOP_LOSS_PCT}%  TP: {TP_PCT}%  Horizonte máx: {MAX_HOLD_DAYS} dias",
        "  Regra Pessimista activa: se SL e TP batem no mesmo dia → SL tem precedência",
        "  Setup D: Learner activo · Optimizer semanal/mensal/trimestral (zero look-ahead)",
        sep,
        "",
        *table,
        "",
        sep,
        "  Resultados puramente OOS e sequenciais. Zero re-treino. Zero look-ahead.",
        "  Setup D arranca com DEFAULT_PARAMS — mutações apenas com trades suficientes.",
        sep,
        "",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    global SIM_START, SIM_END

    parser = argparse.ArgumentParser(
        description="FundScope — Backtest Comparativo de 4 Setups (OOS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  python backtest_comparison.py\n"
            "  python backtest_comparison.py --start 2022-01-01 --end 2022-12-31\n"
            "  python backtest_comparison.py --start 2025-01-01 --end 2026-05-17\n"
        ),
    )
    parser.add_argument("--start", default=SIM_START, help="Início da simulação YYYY-MM-DD")
    parser.add_argument("--end",   default=SIM_END,   help="Fim da simulação YYYY-MM-DD")
    args = parser.parse_args()

    SIM_START = args.start
    SIM_END   = args.end

    print("\n" + "=" * 65)
    print("  FUNDSCOPE — Backtest Comparativo de 4 Setups (OOS)")
    print("=" * 65)
    print(f"  Período  : {SIM_START} → {SIM_END}")
    print(f"  Universe : {len(TICKERS)} tickers")
    print(f"  Capital  : ${INITIAL_CAPITAL:,.0f} USD")
    print(f"  Setup D  : Learner Activo · Seed RNG=42 · Walk-forward 85/15")
    print()

    # 1. Download de dados
    all_dfs = load_ticker_data()
    if not all_dfs:
        print("[ERRO] Sem dados de mercado. Verifica a ligação à internet.")
        return

    spy_df, rsp_df = load_spy_rsp()
    trading_days   = get_trading_days(spy_df)
    print(f"[DATA] {len(trading_days)} dias de trading no período de simulação.\n")

    # 2. Pré-computação (uma vez, partilhada por todos os setups)
    ind_cache  = precompute_indicators(all_dfs, trading_days)
    regimes    = precompute_regimes(spy_df, rsp_df, trading_days)
    ohlc_cache = build_ohlc_cache(all_dfs, trading_days)

    regime_counts: dict[str, int] = {}
    for r in regimes.values():
        regime_counts[r] = regime_counts.get(r, 0) + 1
    print(f"\n[PREP] Regimes detectados: {regime_counts}")

    covered = sum(len(v) for v in ind_cache.values())
    print(f"[PREP] Pontos de indicadores válidos: {covered:,} "
          f"({covered / (len(TICKERS) * len(trading_days)) * 100:.1f}% do universo)\n")

    # 3. Simulações A / B / C / D
    metrics: dict[str, dict] = {}
    labels  = {
        "A": "Clyde Puro",
        "B": "Clyde + Bonnie",
        "C": "Clyde + Bonnie + CRO",
        "D": "Ecossistema Completo (+ Learner)",
    }

    for setup_name in ("A", "B", "C"):
        print(f"[SIM] Setup {setup_name} — {labels[setup_name]}")
        state = run_simulation(setup_name, trading_days, regimes, ind_cache, ohlc_cache)
        metrics[setup_name] = compute_metrics(state)
        m = metrics[setup_name]
        print(f"       Concluído. Lucro: ${m['net_profit']:>+,.2f}  |  "
              f"WR: {m['win_rate_pct']:.1f}%  |  "
              f"MaxDD: {m['max_dd_pct']:.2f}%  |  "
              f"Calmar: {_cal(m['calmar_ratio'])}  |  "
              f"Trades: {m['total_trades']}\n")

    print(f"[SIM] Setup D — {labels['D']}")
    print("       Learner: arranca com DEFAULT_PARAMS, muta nas sextas-feiras com ≥20 trades.")
    state_d = run_simulation_d(trading_days, regimes, ind_cache, ohlc_cache)
    metrics["D"] = compute_metrics(state_d)
    m = metrics["D"]
    print(f"       Concluído. Lucro: ${m['net_profit']:>+,.2f}  |  "
          f"WR: {m['win_rate_pct']:.1f}%  |  "
          f"MaxDD: {m['max_dd_pct']:.2f}%  |  "
          f"Calmar: {_cal(m['calmar_ratio'])}  |  "
          f"Mutações: {m['mutation_count']}\n")

    # 4. Relatório final
    print(format_report(metrics))


if __name__ == "__main__":
    main()
