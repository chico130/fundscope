"""
stress_test_2023_2024.py — Ultimate Stress Test: Bear Recovery → AI Bull Market

Setup D only: Clyde + Bonnie + CRO + Learner Activo com Dual-Engine (VALUE + MOMENTUM)

Período : 2023-01-01 → 2024-12-31
Capital : €5.000 (falso)
Universe: 28 tickers — mix VALUE Blue-Chips + MOMENTUM Race Horses

CLI:
    python stress_test_2023_2024.py
"""
from __future__ import annotations

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
# Config
# ─────────────────────────────────────────────────────────────────────────────

TICKERS: list[str] = [
    # MOMENTUM race horses — AI / semis / high-beta
    "NVDA", "AMD", "META", "PLTR", "SMCI", "MSTR", "CRWD", "NET",
    # VALUE large-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN",
    # VALUE healthcare
    "JNJ", "LLY", "UNH", "PFE",
    # VALUE consumer
    "HD", "COST", "MCD", "NKE",
    # VALUE industrial
    "CAT", "GE",
    # VALUE energy
    "XOM", "CVX",
    # VALUE financial
    "JPM", "V", "MA", "BAC",
]

SIM_START         = "2023-01-01"
SIM_END           = "2024-12-31"
_DATA_WARMUP_DAYS = 420

INITIAL_CAPITAL_EUR = 5_000.0

VALUE_SL_PCT   = 5.0
VALUE_TP_PCT   = 10.0
VALUE_MAX_HOLD = 10

MOM_SL_PCT   = 7.0
MOM_TP_PCT   = 20.0
MOM_MAX_HOLD = 20

BASE_POS_PCT = 0.15
MAX_POS_PCT  = 0.20
MIN_POS_EUR  = 25.0

CRO_MAX_DD          = 15.0
CRO_MAX_TRADES      = 10
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
# Learner defaults + param spaces (auto-contidos, sem imports do bot)
# ─────────────────────────────────────────────────────────────────────────────

_D_CLYDE_DEF: dict = {
    "rsi_oversold_ceiling":   35,
    "rsi_momentum_min":       40,
    "rsi_momentum_max":       55,
    "vol_ratio_oversold_min": 1.2,
    "vol_ratio_momentum_min": 1.8,
}
_D_MOMENTUM_DEF: dict = {
    "momentum_rsi_floor":      58,
    "momentum_vol_min":        1.5,
    "momentum_atr_multiplier": 2.5,
}
_D_BONNIE_DEF: dict = {
    "base_threshold":    0.60,
    "strict_threshold":  0.64,
    "strict_trigger_wr": 0.45,
    "size_factor_pct":   0.15,
}
_D_CRO_DEF: dict = {
    "max_drawdown_limit_pct": 15.0,
    "elastic_window_n":       25,
    "elastic_fallback_wr":    0.48,
    "stop_loss_pct":          5.0,
    "take_profit_pct":        10.0,
}

_D_CLYDE_SP: dict = {
    "rsi_oversold_ceiling":   {"min": 28,  "max": 45,  "step": 1.0, "kind": "int"},
    "rsi_momentum_min":       {"min": 35,  "max": 52,  "step": 1.0, "kind": "int"},
    "rsi_momentum_max":       {"min": 50,  "max": 65,  "step": 1.0, "kind": "int"},
    "vol_ratio_oversold_min": {"min": 1.0, "max": 2.0, "step": 0.1, "kind": "float"},
    "vol_ratio_momentum_min": {"min": 1.4, "max": 2.8, "step": 0.1, "kind": "float"},
}
_D_MOMENTUM_SP: dict = {
    "momentum_rsi_floor":      {"min": 50,   "max": 70,   "step": 1.0,  "kind": "int"},
    "momentum_vol_min":        {"min": 1.2,  "max": 2.5,  "step": 0.1,  "kind": "float"},
    "momentum_atr_multiplier": {"min": 1.5,  "max": 4.0,  "step": 0.25, "kind": "float"},
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
}

_D_MIN_WEEKLY       = 20
_D_MIN_MONTHLY      = 50
_D_MIN_QUARTERLY    = 100
_D_MIN_STYLE_TRADES = 5     # min trades por estilo antes de toggle
_D_EMA_ALPHA        = 0.30
_D_MIN_GAIN         = 0.05
_D_LAMBDA_L2        = 0.15
_D_VAL_SPLIT        = 0.15
_D_N_ITER: dict     = {"weekly": 60, "monthly": 80, "quarterly": 40}

_D_RNG = random.Random(42)


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
    max_hold:    int
    style:       str   = "VALUE"
    peak_high:   float = 0.0
    days_open:   int   = 0
    entry_ind:   dict  = field(default_factory=dict)


@dataclass
class ClosedTrade:
    ticker:      str
    entry_date:  str
    close_date:  str
    entry_price: float
    exit_price:  float
    qty:         float
    pnl:         float
    reason:      str
    style:       str = "VALUE"


@dataclass
class SimState:
    cash:           float              = INITIAL_CAPITAL_EUR
    positions:      list[OpenPosition] = field(default_factory=list)
    closed:         list[ClosedTrade]  = field(default_factory=list)
    equity_series:  list[float]        = field(default_factory=list)

    signals_fired:             int  = 0
    bonnie_vetoes:             int  = 0
    cro_reductions:            int  = 0
    bonnie_strict_activations: int  = 0
    trades_today:              int  = 0

    clyde_params:    dict = field(default_factory=dict)
    bonnie_params:   dict = field(default_factory=dict)
    cro_params:      dict = field(default_factory=dict)
    momentum_params: dict = field(default_factory=dict)
    enabled_styles:  list = field(default_factory=lambda: ["VALUE", "MOMENTUM"])

    virtual_trades:  list = field(default_factory=list)

    mutation_clyde:    int = 0
    mutation_momentum: int = 0
    mutation_bonnie:   int = 0
    mutation_cro:      int = 0
    mutation_style:    int = 0

    style_log:            list = field(default_factory=list)
    weeks_value_only:     int  = 0
    weeks_momentum_only:  int  = 0
    weeks_both:           int  = 0
    _last_week_key:       str  = ""

    @property
    def mutation_count(self) -> int:
        return (self.mutation_clyde + self.mutation_momentum
                + self.mutation_bonnie + self.mutation_cro + self.mutation_style)

    def mark_equity(self, close_prices: dict[str, float]) -> None:
        pos_val = sum(p.qty * close_prices.get(p.ticker, p.entry_price) for p in self.positions)
        self.equity_series.append(self.cash + pos_val)

    def current_equity(self) -> float:
        return self.equity_series[-1] if self.equity_series else self.cash

    def drawdown_pct(self) -> float:
        if not self.equity_series:
            return 0.0
        peak = max(self.equity_series)
        cur  = self.equity_series[-1]
        return max(0.0, (peak - cur) / peak * 100.0) if peak > 0 else 0.0

    def win_rate_7d(self, today: date_t) -> float:
        cutoff = today - timedelta(days=7)
        recent = [t for t in self.closed
                  if datetime.strptime(t.close_date, "%Y-%m-%d").date() >= cutoff]
        if not recent:
            return 0.5
        return sum(1 for t in recent if t.pnl > 0) / len(recent)


# ─────────────────────────────────────────────────────────────────────────────
# Data download
# ─────────────────────────────────────────────────────────────────────────────

def _data_start() -> str:
    sim = datetime.strptime(SIM_START, "%Y-%m-%d")
    return (sim - timedelta(days=_DATA_WARMUP_DAYS)).strftime("%Y-%m-%d")


def _fetch_end() -> str:
    return (datetime.strptime(SIM_END, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")


def load_ticker_data() -> dict[str, pd.DataFrame]:
    ds  = _data_start()
    end = _fetch_end()
    print(f"[DATA] A descarregar {len(TICKERS)} tickers ({ds} → {SIM_END})...")
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
    ds = _data_start(); end = _fetch_end()
    spy = yf.Ticker("SPY").history(start=ds, end=end, interval="1d", auto_adjust=True).dropna(subset=["Close"])
    rsp = yf.Ticker("RSP").history(start=ds, end=end, interval="1d", auto_adjust=True).dropna(subset=["Close"])
    return spy, rsp


def get_trading_days(spy_df: pd.DataFrame) -> list[str]:
    start = datetime.strptime(SIM_START, "%Y-%m-%d").date()
    end   = datetime.strptime(SIM_END,   "%Y-%m-%d").date()
    return sorted(dt.strftime("%Y-%m-%d") for dt in spy_df.index if start <= dt.date() <= end)


# ─────────────────────────────────────────────────────────────────────────────
# Pré-computação de indicadores (estendida: EMA-20, ATR-14)
# ─────────────────────────────────────────────────────────────────────────────

def precompute_indicators(
    all_dfs: dict[str, pd.DataFrame],
    trading_days: list[str],
) -> dict[str, dict[str, dict]]:
    print(f"[PREP] Indicadores ({len(all_dfs)} tickers × {len(trading_days)} dias)...")
    cache: dict[str, dict[str, dict]] = {}

    for ticker, df in all_dfs.items():
        cache[ticker] = {}

        close = df["Close"].astype(float)
        high  = df["High"].astype(float)
        low   = df["Low"].astype(float)
        vol   = df["Volume"].astype(float)

        # RSI-14
        delta    = close.diff()
        avg_gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        avg_loss = (-delta).clip(lower=0).ewm(com=13, adjust=False).mean()
        rsi      = (100.0 - 100.0 / (1.0 + avg_gain / avg_loss.replace(0.0, float("nan")))).round(2)

        # EMAs
        ema20  = close.ewm(span=20,  adjust=False).mean()
        ema50  = close.ewm(span=50,  adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        # Volume ratio (20-day avg)
        vol_ratio = (vol / vol.rolling(20).mean()).round(3)

        # ATR-14 (Wilder smoothing = com=13)
        prev_close = close.shift(1)
        tr  = pd.concat([high - low,
                         (high - prev_close).abs(),
                         (low  - prev_close).abs()], axis=1).max(axis=1)
        atr14 = tr.ewm(com=13, adjust=False).mean()

        bar_count = pd.Series(range(1, len(df) + 1), index=df.index, dtype=int)

        for day_str in trading_days:
            target = datetime.strptime(day_str, "%Y-%m-%d").date()
            row    = df[df.index.date == target]
            if row.empty:
                continue
            idx = row.index[0]
            if int(bar_count.loc[idx]) < MIN_EMA_BARS:
                continue

            rsi_v = rsi.get(idx);  e20 = ema20.get(idx)
            e50   = ema50.get(idx); e200 = ema200.get(idx)
            vr    = vol_ratio.get(idx); atr_v = atr14.get(idx)
            cl    = close.get(idx); hi = high.get(idx); lo = low.get(idx)

            if any(pd.isna(v) for v in (rsi_v, e50, e200, vr)):
                continue

            e20_ok = (e20 is not None and not pd.isna(e20))
            cache[ticker][day_str] = {
                "rsi_14":            float(rsi_v),
                "ema50_above_200":   float(e50) > float(e200),
                "ema20_above_ema50": (float(e20) > float(e50))  if e20_ok else False,
                "price_above_ema20": (float(cl)  > float(e20))  if e20_ok else False,
                "vol_ratio":         float(vr),
                "atr_14":            float(atr_v) if (atr_v is not None and not pd.isna(atr_v)) else None,
                "close":             float(cl),
                "high":              float(hi),
                "low":               float(lo),
            }

    return cache


def precompute_regimes(
    spy_df: pd.DataFrame,
    rsp_df: pd.DataFrame,
    trading_days: list[str],
) -> dict[str, str]:
    print(f"[PREP] Regimes para {len(trading_days)} dias...")
    spy_close = spy_df["Close"].astype(float)
    rsp_close = rsp_df["Close"].astype(float)
    regimes: dict[str, str] = {}

    for day_str in trading_days:
        target = datetime.strptime(day_str, "%Y-%m-%d").date()
        spy = spy_close[spy_close.index.date <= target]
        rsp = rsp_close[rsp_close.index.date <= target]

        if len(spy) < MIN_EMA_BARS:
            regimes[day_str] = "unknown"; continue

        closes   = list(spy.astype(float))
        spy_last = closes[-1]
        ema200   = compute_ema(closes, 200)
        if ema200 is None:
            regimes[day_str] = "unknown"; continue

        pct   = (spy_last - ema200) / ema200 * 100.0
        ret20 = (spy_last - closes[-20]) / closes[-20] if len(closes) >= 20 else 0.0

        breadth_ok = True
        if len(rsp) >= 20 and len(spy) >= 20:
            rn  = float(rsp.iloc[-1])  / float(spy.iloc[-1])
            r20 = float(rsp.iloc[-20]) / float(spy.iloc[-20])
            breadth_ok = (rn - r20) / r20 >= -0.02

        if pct <= -5.0:
            r = "bear_capitulation" if ret20 < -0.10 else "bear_correction"
        elif pct < 0.0:
            r = "bull_lateral"
        else:
            r = "bull_trending" if breadth_ok else "bull_lateral"

        regimes[day_str] = r

    return regimes


def build_ohlc_cache(
    all_dfs: dict[str, pd.DataFrame],
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
# Sinal Dual-Engine
# ─────────────────────────────────────────────────────────────────────────────

def _d_signal_dual(
    ind:            dict,
    regime:         str,
    cp:             dict,
    mp:             dict,
    enabled_styles: list,
) -> tuple[str, float, str]:
    """Retorna (signal, strength, style). VALUE tem prioridade sobre MOMENTUM."""
    if regime in _BEAR:
        return "HOLD", 0.0, "VALUE"

    rsi       = ind["rsi_14"]
    ema_above = ind["ema50_above_200"]
    vol       = ind["vol_ratio"]

    if "VALUE" in enabled_styles:
        rsi_a = cp.get("rsi_oversold_ceiling",   35)
        vo_a  = cp.get("vol_ratio_oversold_min", 1.2)
        rb_lo = cp.get("rsi_momentum_min",       40)
        rb_hi = cp.get("rsi_momentum_max",       55)
        vo_b  = cp.get("vol_ratio_momentum_min", 1.8)

        if rsi <= rsi_a and ema_above and vol >= vo_a:
            return "BUY", round(min(1.0, 0.70 + (rsi_a - rsi) / 100), 4), "VALUE"
        if rb_lo <= rsi <= rb_hi and ema_above and vol >= vo_b:
            return "BUY", round(min(1.0, 0.55 + (vol - vo_b) / 10), 4), "VALUE"

    if "MOMENTUM" in enabled_styles:
        m_rsi     = mp.get("momentum_rsi_floor", 58)
        m_vol     = mp.get("momentum_vol_min",   1.5)
        ema50_ok  = ind.get("ema50_above_200",    False)   # long-term trend
        ema20_ok  = ind.get("ema20_above_ema50",  False)   # mid-term acceleration
        price_ok  = ind.get("price_above_ema20",  False)   # immediate breakout

        # Full EMA alignment: price > EMA-20 > EMA-50 > EMA-200 — anti-choppy
        if rsi >= m_rsi and ema50_ok and ema20_ok and price_ok and vol >= m_vol:
            return "BUY", round(min(1.0, 0.65 + (vol - m_vol) / 10), 4), "MOMENTUM"

    return "HOLD", 0.0, "VALUE"


# ─────────────────────────────────────────────────────────────────────────────
# CRO helpers
# ─────────────────────────────────────────────────────────────────────────────

def _d_elastic_target(closed: list[ClosedTrade], cro_p: dict) -> float:
    window   = int(cro_p.get("elastic_window_n", 25))
    fallback = cro_p.get("elastic_fallback_wr", 0.48)
    if len(closed) < window:
        return fallback
    return round(sum(1 for t in closed[-window:] if t.pnl > 0) / window, 4)


def _d_bonnie_thresh(closed: list[ClosedTrade], bp: dict, cro_p: dict) -> float:
    wr      = _d_elastic_target(closed, cro_p)
    trigger = bp.get("strict_trigger_wr", 0.45)
    return bp.get("strict_threshold", 0.64) if wr < trigger else bp.get("base_threshold", 0.60)


def _cro_risk_factor(wr: float, dd: float, regime: str, el_target: float) -> float:
    wr_adj = max(0.5, min(1.2, wr / el_target if el_target > 0 else 1.0))
    dd_adj = max(0.3, min(1.0, 1.0 - dd / CRO_MAX_DD))
    reg_f  = _REGIME_SIZE.get(regime, 0.5)
    return round(wr_adj * dd_adj * reg_f, 4)


def _position_size(equity, strength, regime, cash, size_f, cro_rf) -> float:
    reg_f   = _REGIME_SIZE.get(regime, 0.0)
    base    = strength * equity * size_f * reg_f
    cro_cap = equity * MAX_POS_PCT * cro_rf
    size    = min(base, cro_cap, cash * 0.95)
    return size if size >= MIN_POS_EUR else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Learner — funções de optimização (auto-contidas)
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
    if not trades:
        return 0.5
    results = [t.get("result_eur", 0) or 0 for t in trades]
    gain    = sum(r for r in results if r > 0)
    loss    = abs(sum(r for r in results if r < 0))
    pf      = gain / (loss + 0.01)
    calmar  = max(0.4, 1.0 - _d_maxdd(results) / 15.0)
    return round(min(pf * calmar, 10.0), 4)


def _d_split(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    s  = sorted(trades, key=lambda t: t.get("datetime", ""))
    at = max(1, int(len(s) * (1 - _D_VAL_SPLIT)))
    return s[:at], s[at:]


def _d_descent(names, current, defaults, space, train, val, fit_fn, n_iter):
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
        f_tr2       = fit_fn(trial, train)
        f_val2      = fit_fn(trial, val) if val else f_tr2

        if f_tr2 > f_tr * (1 + _D_MIN_GAIN) and f_val2 > f_val * 0.85:
            smoothed    = _d_ema(float(best[name]), float(candidate))
            best[name]  = int(round(smoothed)) if spec["kind"] == "int" else round(smoothed, 6)
            f_tr  = fit_fn(best, train)
            f_val = fit_fn(best, val) if val else f_tr

    return best, fit_fn(best, train) * _d_l2(best, defaults, space)


def _d_would_enter_value(trade: dict, cp: dict) -> bool:
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
    return (rsi <= ceil and ema_up and vol >= vo_min) or (m_min <= rsi <= m_max and ema_up and vol >= vm_min)


def _d_would_enter_momentum(trade: dict, mp: dict) -> bool:
    ctx    = trade.get("context", {})
    rsi    = ctx.get("rsi_14")
    vol    = ctx.get("volume_ratio_vs_avg", 1.0)
    ema50  = ctx.get("ema50_above_ema200", True)
    ema20  = ctx.get("ema20_above_ema50",  False)
    price  = ctx.get("price_above_ema20",  False)
    if rsi is None:
        return True
    return (rsi >= mp.get("momentum_rsi_floor", 58)
            and ema50 and ema20 and price and vol >= mp.get("momentum_vol_min", 1.5))


def _d_optimize_clyde(virtual_trades: list[dict], cp: dict) -> tuple[dict, bool]:
    value_trades = [t for t in virtual_trades if t.get("style") == "VALUE"]
    names        = list(_D_CLYDE_SP.keys())
    train, val   = _d_split(value_trades)
    f_before     = _d_pfc([t for t in train if _d_would_enter_value(t, cp)])
    fit_fn       = lambda p, tr: _d_pfc([t for t in tr if _d_would_enter_value(t, p)])
    optimized, f_after = _d_descent(names, cp, _D_CLYDE_DEF, _D_CLYDE_SP, train, val, fit_fn, _D_N_ITER["weekly"])
    if f_after > f_before * (1 + _D_MIN_GAIN):
        return optimized, True
    return cp, False


def _d_optimize_momentum(virtual_trades: list[dict], mp: dict) -> tuple[dict, bool]:
    mom_trades = [t for t in virtual_trades if t.get("style") == "MOMENTUM"]
    if not mom_trades:
        return mp, False
    names      = list(_D_MOMENTUM_SP.keys())
    train, val = _d_split(mom_trades)
    fit_fn     = lambda p, tr: _d_pfc([t for t in tr if _d_would_enter_momentum(t, p)])
    f_before   = fit_fn(mp, train)
    optimized, f_after = _d_descent(names, mp, _D_MOMENTUM_DEF, _D_MOMENTUM_SP, train, val, fit_fn, _D_N_ITER["weekly"])
    if f_after > f_before * (1 + _D_MIN_GAIN):
        return optimized, True
    return mp, False


def _d_optimize_bonnie(virtual_trades: list[dict], bp: dict) -> tuple[dict, bool]:
    names      = list(_D_BONNIE_SP.keys())
    train, val = _d_split(virtual_trades)
    fit_fn     = lambda p, tr: _d_pfc([t for t in tr if t.get("signal_strength", 0) >= p.get("base_threshold", 0.60)])
    f_before   = fit_fn(bp, train)
    optimized, f_after = _d_descent(names, bp, _D_BONNIE_DEF, _D_BONNIE_SP, train, val, fit_fn, _D_N_ITER["monthly"])
    if f_after > f_before * (1 + _D_MIN_GAIN):
        return optimized, True
    return bp, False


def _d_optimize_cro(virtual_trades: list[dict], cro_p: dict) -> tuple[dict, bool]:
    if not virtual_trades:
        return cro_p, False
    names      = list(_D_CRO_SP.keys())
    train, val = _d_split(virtual_trades)
    def fit_fn(p, tr):
        if not tr:
            return 0.5
        results   = [t.get("result_eur", 0) for t in tr]
        total_pnl = sum(results)
        dd        = _d_maxdd(results)
        max_dd    = p.get("max_drawdown_limit_pct", 15.0)
        dd_pen    = max(0.3, 1.0 - dd / max_dd) if max_dd > 0 else 0.3
        ann       = total_pnl / max(1, len(tr)) * 52
        calmar    = ann / max(dd, 0.01)
        return max(0.01, min(calmar * dd_pen, 10.0))
    f_before = fit_fn(cro_p, train)
    optimized, f_after = _d_descent(names, cro_p, _D_CRO_DEF, _D_CRO_SP, train, val, fit_fn, _D_N_ITER["quarterly"])
    if f_after > f_before * (1 + _D_MIN_GAIN):
        return optimized, True
    return cro_p, False


# ─────────────────────────────────────────────────────────────────────────────
# Learner — Style Toggle
# ─────────────────────────────────────────────────────────────────────────────

def _calmar_by_style(virtual_trades: list[dict], style: str) -> float | None:
    trades = [t for t in virtual_trades if t.get("style") == style]
    if len(trades) < _D_MIN_STYLE_TRADES:
        return None
    results = [t.get("result_eur", 0) for t in trades]
    total   = sum(results)
    maxdd   = _d_maxdd(results)
    if maxdd < 0.01:
        return 99.0 if total > 0 else 0.0
    return round(total / maxdd, 4)


def _evaluate_style_toggle(state: SimState) -> tuple[bool, float | None, float | None]:
    vc = _calmar_by_style(state.virtual_trades, "VALUE")
    mc = _calmar_by_style(state.virtual_trades, "MOMENTUM")

    new_styles = list(state.enabled_styles)
    changed    = False

    # Disable a style if Calmar < 0 (with data); re-enable if Calmar > 0
    if mc is not None:
        if mc < 0 and "MOMENTUM" in new_styles and "VALUE" in new_styles:
            new_styles.remove("MOMENTUM"); changed = True
        elif mc > 0 and "MOMENTUM" not in new_styles:
            new_styles.append("MOMENTUM"); changed = True

    if vc is not None:
        if vc < 0 and "VALUE" in new_styles and "MOMENTUM" in new_styles:
            new_styles.remove("VALUE"); changed = True
        elif vc > 0 and "VALUE" not in new_styles:
            new_styles.append("VALUE"); changed = True

    # Failsafe: never leave both disabled
    if not new_styles:
        new_styles = ["VALUE"]; changed = True

    if changed:
        state.enabled_styles = new_styles

    return changed, vc, mc


def _log_week_style(state: SimState, week_key: str, day_str: str, vc, mc) -> None:
    if week_key == state._last_week_key:
        return
    state._last_week_key = week_key
    styles = list(state.enabled_styles)
    state.style_log.append({
        "week": week_key, "date": day_str,
        "enabled_styles": styles, "value_calmar": vc, "momentum_calmar": mc,
    })
    if "VALUE" in styles and "MOMENTUM" in styles:
        state.weeks_both += 1
    elif "VALUE" in styles:
        state.weeks_value_only += 1
    else:
        state.weeks_momentum_only += 1


# ─────────────────────────────────────────────────────────────────────────────
# Simulação diária
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
        t: d[day_str]["close"] for t, d in ohlc_cache.items() if day_str in d
    }

    # ── Passo 1: saídas ──────────────────────────────────────────────────────
    still_open: list[OpenPosition] = []
    atr_mult   = state.momentum_params.get("momentum_atr_multiplier", 2.5)

    for pos in state.positions:
        day_ohlc = ohlc_cache.get(pos.ticker, {}).get(day_str)
        if day_ohlc is None:
            pos.days_open += 1; still_open.append(pos); continue

        high  = day_ohlc["high"]
        low   = day_ohlc["low"]
        close = day_ohlc["close"]

        exit_price, reason = None, None

        if pos.style == "MOMENTUM":
            # Update peak_high (using close — consistent com production)
            if close > pos.peak_high:
                pos.peak_high = close

            atr = ind_cache.get(pos.ticker, {}).get(day_str, {}).get("atr_14")
            # ATR Trailing Stop (primary)
            if atr and pos.peak_high > 0:
                trailing_stop = pos.peak_high - atr_mult * atr
                if close < trailing_stop:
                    exit_price, reason = close, "atr_trailing_stop"
            # Emergency SL
            if exit_price is None and low <= pos.stop_price:
                exit_price, reason = pos.stop_price, "stop_loss"
            # TP
            if exit_price is None and high >= pos.tp_price:
                exit_price, reason = pos.tp_price, "take_profit"
            # Time exit
            if exit_price is None and pos.days_open >= pos.max_hold:
                exit_price, reason = close, "time_exit"

        else:  # VALUE — Pessimistic rule: SL has priority
            hit_sl = low  <= pos.stop_price
            hit_tp = high >= pos.tp_price
            if hit_sl:
                exit_price, reason = pos.stop_price, "stop_loss"
            elif hit_tp:
                exit_price, reason = pos.tp_price, "take_profit"
            elif pos.days_open >= pos.max_hold:
                exit_price, reason = close, "time_exit"

        if exit_price is not None:
            pnl         = (exit_price - pos.entry_price) * pos.qty
            state.cash += pos.qty * exit_price
            state.closed.append(ClosedTrade(
                ticker=pos.ticker, entry_date=pos.entry_date, close_date=day_str,
                entry_price=pos.entry_price, exit_price=exit_price,
                qty=pos.qty, pnl=round(pnl, 4), reason=reason, style=pos.style,
            ))
            state.virtual_trades.append({
                "side":            "BUY",
                "result_eur":      round(pnl, 4),
                "signal_strength": pos.entry_ind.get("strength", 0.7),
                "datetime":        pos.entry_date + "T00:00:00Z",
                "style":           pos.style,
                "context": {
                    "rsi_14":              pos.entry_ind.get("rsi_14"),
                    "volume_ratio_vs_avg": pos.entry_ind.get("vol_ratio"),
                    "ema50_above_ema200":  pos.entry_ind.get("ema50_above_200"),
                    "ema20_above_ema50":   pos.entry_ind.get("ema20_above_ema50"),
                },
            })
        else:
            pos.days_open += 1
            still_open.append(pos)

    state.positions = still_open
    state.mark_equity(close_prices)

    # ── Passo 2: entradas ────────────────────────────────────────────────────
    held = {p.ticker for p in state.positions}

    bonnie_thr = _d_bonnie_thresh(state.closed, state.bonnie_params, state.cro_params)
    if bonnie_thr > state.bonnie_params.get("base_threshold", 0.60):
        state.bonnie_strict_activations += 1

    el_target  = _d_elastic_target(state.closed, state.cro_params)
    max_dd_lim = state.cro_params.get("max_drawdown_limit_pct", CRO_MAX_DD)
    size_f     = state.bonnie_params.get("size_factor_pct", BASE_POS_PCT)

    for ticker in TICKERS:
        if ticker in held:
            continue
        ind = ind_cache.get(ticker, {}).get(day_str)
        if ind is None:
            continue

        sig, strength, style = _d_signal_dual(
            ind, regime, state.clyde_params, state.momentum_params, state.enabled_styles
        )
        if sig != "BUY":
            continue

        state.signals_fired += 1

        if strength < bonnie_thr:
            state.bonnie_vetoes += 1; continue

        dd = state.drawdown_pct()
        if dd > max_dd_lim:
            state.bonnie_vetoes += 1; continue
        if state.trades_today >= CRO_MAX_TRADES:
            continue

        wr     = state.win_rate_7d(today)
        cro_rf = _cro_risk_factor(wr, dd, regime, el_target)
        equity = state.current_equity()
        size   = _position_size(equity, strength, regime, state.cash, size_f, cro_rf)

        if size <= 0:
            continue

        entry = ind["close"]
        if entry <= 0:
            continue

        qty = round(size / entry, 6)

        if cro_rf < 1.0:
            reg_f = _REGIME_SIZE.get(regime, 0.0)
            unmod = min(strength * equity * size_f * reg_f, equity * MAX_POS_PCT, state.cash * 0.95)
            if size < unmod - 0.01:
                state.cro_reductions += 1

        sl_pct   = MOM_SL_PCT    if style == "MOMENTUM" else state.cro_params.get("stop_loss_pct",   VALUE_SL_PCT)
        tp_pct   = MOM_TP_PCT    if style == "MOMENTUM" else state.cro_params.get("take_profit_pct", VALUE_TP_PCT)
        max_hold = MOM_MAX_HOLD  if style == "MOMENTUM" else VALUE_MAX_HOLD

        state.cash -= qty * entry
        state.positions.append(OpenPosition(
            ticker=ticker, entry_date=day_str, entry_price=entry, qty=qty,
            stop_price=round(entry * (1.0 - sl_pct / 100.0), 4),
            tp_price=round(entry   * (1.0 + tp_pct / 100.0), 4),
            max_hold=max_hold, style=style,
            peak_high=entry,
            entry_ind={
                "rsi_14":            ind["rsi_14"],
                "vol_ratio":         ind["vol_ratio"],
                "ema50_above_200":   ind["ema50_above_200"],
                "ema20_above_ema50": ind.get("ema20_above_ema50", False),
                "strength":          strength,
            },
        ))
        state.trades_today += 1
        held.add(ticker)


# ─────────────────────────────────────────────────────────────────────────────
# Loop principal de simulação
# ─────────────────────────────────────────────────────────────────────────────

def run_simulation_d(
    trading_days: list[str],
    regimes:      dict[str, str],
    ind_cache:    dict[str, dict[str, dict]],
    ohlc_cache:   dict[str, dict[str, dict]],
) -> SimState:
    state = SimState(
        clyde_params    = copy.deepcopy(_D_CLYDE_DEF),
        bonnie_params   = copy.deepcopy(_D_BONNIE_DEF),
        cro_params      = copy.deepcopy(_D_CRO_DEF),
        momentum_params = copy.deepcopy(_D_MOMENTUM_DEF),
        enabled_styles  = ["VALUE", "MOMENTUM"],
    )
    n = len(trading_days)
    optimized_months   : set = set()
    optimized_quarters : set = set()

    for i, day_str in enumerate(trading_days):
        regime = regimes.get(day_str, "unknown")
        process_day_d(state, day_str, regime, ind_cache, ohlc_cache)

        today    = datetime.strptime(day_str, "%Y-%m-%d").date()
        week_key = f"{today.year}-W{today.isocalendar()[1]:02d}"

        # ── Sexta-feira: optimização semanal ─────────────────────────────────
        if today.weekday() == 4:
            vc = _calmar_by_style(state.virtual_trades, "VALUE")
            mc = _calmar_by_style(state.virtual_trades, "MOMENTUM")
            _log_week_style(state, week_key, day_str, vc, mc)

            if len(state.virtual_trades) >= _D_MIN_WEEKLY:
                new_cp, mut_c = _d_optimize_clyde(state.virtual_trades, state.clyde_params)
                if mut_c:
                    state.clyde_params   = new_cp
                    state.mutation_clyde += 1
                    rsi = new_cp.get("rsi_oversold_ceiling", 35)
                    print(f"  [MUT] Clyde #{state.mutation_clyde} — RSI≤{rsi} "
                          f"vol≥{new_cp.get('vol_ratio_oversold_min', 1.2):.1f}  ({day_str})")

                new_mp, mut_m = _d_optimize_momentum(state.virtual_trades, state.momentum_params)
                if mut_m:
                    state.momentum_params   = new_mp
                    state.mutation_momentum += 1
                    mrf = new_mp.get("momentum_rsi_floor", 65)
                    atr_m = new_mp.get("momentum_atr_multiplier", 2.5)
                    print(f"  [MUT] Momentum #{state.mutation_momentum} — RSI≥{mrf} "
                          f"ATR×{atr_m:.2f}  ({day_str})")

                changed, vc2, mc2 = _evaluate_style_toggle(state)
                if changed:
                    state.mutation_style += 1
                    print(f"  [TOGGLE] #{state.mutation_style} → {state.enabled_styles}  "
                          f"(VALUE Calmar={vc2}, MOM Calmar={mc2})  ({day_str})")

        # ── 1.º dia útil do mês: Bonnie ──────────────────────────────────────
        month_key = (today.year, today.month)
        if month_key not in optimized_months and today.day <= 5:
            if len(state.virtual_trades) >= _D_MIN_MONTHLY:
                new_bp, mut_b = _d_optimize_bonnie(state.virtual_trades, state.bonnie_params)
                if mut_b:
                    state.bonnie_params  = new_bp
                    state.mutation_bonnie += 1
                    thr = new_bp.get("base_threshold", 0.60)
                    sf  = new_bp.get("size_factor_pct", 0.15)
                    print(f"  [MUT] Bonnie #{state.mutation_bonnie} — thresh={thr:.2f} "
                          f"size={sf:.2f}  ({day_str})")
            optimized_months.add(month_key)

        # ── 1.º dia útil do trimestre: CRO ───────────────────────────────────
        qkey = (today.year, (today.month - 1) // 3)
        if qkey not in optimized_quarters and today.month in {1, 4, 7, 10} and today.day <= 5:
            if len(state.virtual_trades) >= _D_MIN_QUARTERLY:
                new_cro, mut_r = _d_optimize_cro(state.virtual_trades, state.cro_params)
                if mut_r:
                    state.cro_params   = new_cro
                    state.mutation_cro += 1
                    sl  = new_cro.get("stop_loss_pct",   5.0)
                    tp  = new_cro.get("take_profit_pct", 10.0)
                    print(f"  [MUT] CRO #{state.mutation_cro} — SL={sl}% TP={tp}%  ({day_str})")
            optimized_quarters.add(qkey)

        if (i + 1) % 50 == 0 or i == n - 1:
            eq = state.current_equity()
            print(f"  [D] {day_str} ({i+1:>3}/{n}) | €{eq:>8,.0f} | "
                  f"Pos:{len(state.positions):>2} Trades:{len(state.closed):>3} | "
                  f"Muts:{state.mutation_count} | {state.enabled_styles}")

    # Força fecho no último dia
    last_day     = trading_days[-1]
    close_prices = {t: d[last_day]["close"] for t, d in ohlc_cache.items() if last_day in d}

    for pos in state.positions:
        ep  = close_prices.get(pos.ticker, pos.entry_price)
        pnl = (ep - pos.entry_price) * pos.qty
        state.cash += pos.qty * ep
        state.closed.append(ClosedTrade(
            ticker=pos.ticker, entry_date=pos.entry_date, close_date=last_day,
            entry_price=pos.entry_price, exit_price=ep,
            qty=pos.qty, pnl=round(pnl, 4), reason="time_exit", style=pos.style,
        ))
    state.positions = []

    # Log da última semana (se não foi sexta-feira)
    last_date = datetime.strptime(last_day, "%Y-%m-%d").date()
    wk = f"{last_date.year}-W{last_date.isocalendar()[1]:02d}"
    _log_week_style(state, wk + "_final", last_day,
                    _calmar_by_style(state.virtual_trades, "VALUE"),
                    _calmar_by_style(state.virtual_trades, "MOMENTUM"))

    return state


# ─────────────────────────────────────────────────────────────────────────────
# Métricas
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(state: SimState) -> dict:
    closed = state.closed
    final  = state.cash

    wins   = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    gain   = sum(t.pnl for t in wins)
    loss   = abs(sum(t.pnl for t in losses))
    pf     = round(gain / loss, 2) if loss > 0 else (float("inf") if gain > 0 else 0.0)

    series = state.equity_series
    max_dd, peak = 0.0, (series[0] if series else INITIAL_CAPITAL_EUR)
    for v in series:
        if v > peak: peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd: max_dd = dd

    ret_pct = round((final / INITIAL_CAPITAL_EUR - 1.0) * 100.0, 2)
    max_dd  = round(max_dd, 2)
    calmar  = round(ret_pct / max_dd, 2) if max_dd > 0.01 else (99.0 if ret_pct > 0 else 0.0)

    def _eng(style):
        trades = [t for t in closed if t.style == style]
        wins_e = [t for t in trades if t.pnl > 0]
        wr     = round(len(wins_e) / len(trades) * 100, 1) if trades else 0.0
        pnl    = round(sum(t.pnl for t in trades), 2)
        return {"trades": len(trades), "win_rate": wr, "pnl": pnl}

    reasons: dict[str, int] = {}
    for t in closed:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1

    total_sw = state.weeks_both + state.weeks_value_only + state.weeks_momentum_only

    return {
        "final_equity":        round(final, 2),
        "net_profit":          round(final - INITIAL_CAPITAL_EUR, 2),
        "return_pct":          ret_pct,
        "win_rate_pct":        round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0,
        "max_dd_pct":          max_dd,
        "profit_factor":       pf,
        "calmar_ratio":        calmar,
        "total_trades":        len(closed),
        "signals_fired":       state.signals_fired,
        "bonnie_vetoes":       state.bonnie_vetoes,
        "cro_reductions":      state.cro_reductions,
        "bonnie_strict":       state.bonnie_strict_activations,
        "mut_clyde":           state.mutation_clyde,
        "mut_momentum":        state.mutation_momentum,
        "mut_bonnie":          state.mutation_bonnie,
        "mut_cro":             state.mutation_cro,
        "mut_style":           state.mutation_style,
        "mut_total":           state.mutation_count,
        "value":               _eng("VALUE"),
        "momentum":            _eng("MOMENTUM"),
        "exit_reasons":        reasons,
        "weeks_both":          state.weeks_both,
        "weeks_value_only":    state.weeks_value_only,
        "weeks_momentum_only": state.weeks_momentum_only,
        "total_weeks":         total_sw,
        "final_styles":        state.enabled_styles,
        "final_clyde":         state.clyde_params,
        "final_momentum":      state.momentum_params,
        "final_bonnie":        state.bonnie_params,
        "final_cro":           state.cro_params,
        "style_log":           state.style_log,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Relatório Markdown
# ─────────────────────────────────────────────────────────────────────────────

def format_report(m: dict) -> str:
    SEP  = "═" * 72
    sep  = "─" * 72

    def _pf(v): return "∞" if v == float("inf") else f"{v:.2f}"
    def _cal(v): return "∞" if v >= 99.0 else f"{v:.2f}"
    def _eur(v, plus=False):
        s = "+" if (plus and v >= 0) else ("-" if v < 0 else "")
        return f"{s}€{abs(v):,.2f}"
    def _pct(v, plus=False):
        s = "+" if (plus and v >= 0) else ""
        return f"{s}{v:.2f}%"

    tw = m["total_weeks"] or 1
    pct_both = m["weeks_both"]          / tw * 100
    pct_val  = m["weeks_value_only"]    / tw * 100
    pct_mom  = m["weeks_momentum_only"] / tw * 100

    val = m["value"]; mom_e = m["momentum"]
    total_pnl = m["net_profit"] or 0.001
    val_contrib = round(val["pnl"] / total_pnl * 100, 1)
    mom_contrib = round(mom_e["pnl"] / total_pnl * 100, 1)

    # Param diff helper
    def _diff_row(label, default, final):
        d = final - default
        sign = "+" if d > 0 else ""
        d_str = f"{sign}{d:.2f}" if isinstance(d, float) else f"{sign}{int(d)}"
        return f"| {label:<32} | {str(default):^10} | {str(final):^10} | {d_str:^10} |"

    # Style toggle timeline (max 20 entries, only on change)
    toggle_lines = []
    prev = None
    for entry in m["style_log"]:
        styles = tuple(sorted(entry.get("enabled_styles", [])))
        if styles != prev:
            label = "+".join(sorted(entry["enabled_styles"])) if entry["enabled_styles"] else "(none)"
            vc    = entry.get("value_calmar")
            mc    = entry.get("momentum_calmar")
            vc_s  = f"V.Calmar={vc:.2f}" if vc is not None else ""
            mc_s  = f"M.Calmar={mc:.2f}" if mc is not None else ""
            extras = "  ".join(filter(None, [vc_s, mc_s]))
            toggle_lines.append(f"  {entry['date']}  →  [{label}]  {extras}")
            prev = styles
        if len(toggle_lines) >= 20:
            break

    # Pre-compute formatted values to avoid nested-f-string quote escaping
    r_ret   = "**" + _pct(m["return_pct"],  plus=True) + "**"
    r_wr    = str(m["win_rate_pct"]) + "%"
    r_mdd   = "**" + str(m["max_dd_pct"]) + "%**"
    r_mut   = "**" + str(m["mut_total"]) + "**"
    r_tog   = "**" + str(m["mut_style"]) + "**"
    r_tw    = "**" + str(tw) + "**"
    r_both  = str(round(pct_both, 1)) + "%"
    r_vonly = str(round(pct_val,  1)) + "%"
    r_monly = str(round(pct_mom,  1)) + "%"
    r_vwr   = str(val["win_rate"])   + "%"
    r_mwr   = str(mom_e["win_rate"]) + "%"
    r_vc    = str(round(val_contrib,   1)) + "%"
    r_mc    = str(round(mom_contrib,   1)) + "%"

    def R(label, val_str, w=32, col=22):
        return "| " + label.ljust(w) + " | " + val_str.center(col) + " |"

    lines = [
        "",
        SEP,
        f"  FUNDSCOPE — Ultimate Stress Test  |  {SIM_START} → {SIM_END}",
        f"  Universe: {len(TICKERS)} tickers   Capital: €{INITIAL_CAPITAL_EUR:,.0f}   Setup D — Dual-Engine",
        "  Bear Recovery 2023 → AI Bull Market 2024",
        "  Regra Pessimista: SL e TP no mesmo dia → SL tem precedência",
        SEP,
        "",
        "## Resultados Financeiros",
        "",
        R("Métrica", "Valor"),
        "|" + "-" * 34 + "|" + "-" * 24 + "|",
        R("Capital Final",            _eur(m["final_equity"])),
        R("Lucro Líquido",            _eur(m["net_profit"], plus=True)),
        R("**Retorno Total (%)**",    r_ret),
        R("Win Rate (%)",             r_wr),
        R("**Max Drawdown (%)**",     r_mdd),
        R("Profit Factor",            _pf(m["profit_factor"])),
        R("Calmar Ratio",             _cal(m["calmar_ratio"])),
        R("Trades Executados",        str(m["total_trades"])),
        R("Sinais Disparados",        str(m["signals_fired"])),
        R("Vetos Bonnie",             str(m["bonnie_vetoes"])),
        R("Posições Atenuadas CRO",   str(m["cro_reductions"])),
        R("Dias Bonnie Strict",       str(m["bonnie_strict"])),
        "",
        "---",
        "",
        "## Learner — Adaptação Temporal",
        "",
        R("Tipo de Mutação", "#"),
        "|" + "-" * 34 + "|" + "-" * 24 + "|",
        R("**Mutações Totais**",        r_mut),
        R("  Clyde (VALUE, semanal)",   str(m["mut_clyde"])),
        R("  Momentum (semanal)",       str(m["mut_momentum"])),
        R("  Bonnie (mensal)",          str(m["mut_bonnie"])),
        R("  CRO (trimestral)",         str(m["mut_cro"])),
        R("  **Toggle de Estilo**",     r_tog),
        "",
        "### Parâmetros Finais vs. DEFAULT",
        "",
        "| " + "Parâmetro".ljust(32) + " | " + "Default".center(10) + " | " + "Final".center(10) + " | " + "Δ".center(10) + " |",
        "|" + "-" * 34 + "|" + "-" * 12 + "|" + "-" * 12 + "|" + "-" * 12 + "|",
    ]

    for p, dv in _D_CLYDE_DEF.items():
        if p in _D_CLYDE_SP:
            fv = m["final_clyde"].get(p, dv)
            lines.append(_diff_row(p, dv, fv))
    for p, dv in _D_MOMENTUM_DEF.items():
        fv = m["final_momentum"].get(p, dv)
        lines.append(_diff_row(p, dv, fv))
    for p, dv in _D_BONNIE_DEF.items():
        if p in _D_BONNIE_SP:
            fv = m["final_bonnie"].get(p, dv)
            lines.append(_diff_row(p, dv, fv))
    for p, dv in list(_D_CRO_DEF.items())[:3]:
        if p in _D_CRO_SP:
            fv = m["final_cro"].get(p, dv)
            lines.append(_diff_row(p, dv, fv))

    def R3(a, b, c, w1=32, w2=10, w3=12):
        return "| " + a.ljust(w1) + " | " + b.center(w2) + " | " + c.center(w3) + " |"

    lines += [
        "",
        "---",
        "",
        "## Toggle de Estilos — Dual-Engine Adaptativo",
        "",
        R3("Estado", "Semanas", "% do Período"),
        "|" + "-" * 34 + "|" + "-" * 12 + "|" + "-" * 14 + "|",
        R3("AMBOS (VALUE + MOMENTUM)", str(m["weeks_both"]),         r_both),
        R3("Só VALUE",                str(m["weeks_value_only"]),    r_vonly),
        R3("Só MOMENTUM",             str(m["weeks_momentum_only"]), r_monly),
        R3("**Total semanas**",       r_tw,                          "**100%**"),
    ]

    if toggle_lines:
        lines += ["", "### Cronograma de Toggles (mudanças de estado)", ""] + toggle_lines

    def R5(a, b, c, d, e, w1=12, w2=8, w3=10, w4=14, w5=14):
        return ("| " + a.ljust(w1) + " | " + b.center(w2) + " | "
                + c.center(w3) + " | " + d.center(w4) + " | " + e.center(w5) + " |")

    lines += [
        "",
        "---",
        "",
        "## Breakdown por Motor",
        "",
        R5("Motor", "Trades", "Win Rate", "Lucro (€)", "Contribuição"),
        "|" + "-" * 14 + "|" + "-" * 10 + "|" + "-" * 12 + "|" + "-" * 16 + "|" + "-" * 16 + "|",
        R5("VALUE",    str(val["trades"]),    r_vwr, _eur(val["pnl"],    plus=True), r_vc),
        R5("MOMENTUM", str(mom_e["trades"]),  r_mwr, _eur(mom_e["pnl"], plus=True), r_mc),
        "",
        "## Causas de Saída",
        "",
        "| " + "Motivo".ljust(28) + " | " + "Trades".center(8) + " | " + "%".center(8) + " |",
        "|" + "-" * 30 + "|" + "-" * 10 + "|" + "-" * 10 + "|",
    ]

    total_t = m["total_trades"] or 1
    for reason, count in sorted(m["exit_reasons"].items(), key=lambda x: -x[1]):
        pct = round(count / total_t * 100, 1)
        lines.append(f"| {reason:<28} | {count:^8} | {f'{pct:.1f}%':^8} |")

    lines += [
        "",
        sep,
        f"  Estilos activos no final do período: {m['final_styles']}",
        "  Zero look-ahead. Optimizer usa apenas trades já fechados antes de cada trigger.",
        "  Arrancou com DEFAULT_PARAMS — todas as mutações são puramente OOS.",
        sep,
        "",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 72)
    print("  FUNDSCOPE — Ultimate Stress Test: Bear Recovery → AI Bull Market")
    print("=" * 72)
    print(f"  Período  : {SIM_START} → {SIM_END}")
    print(f"  Universe : {len(TICKERS)} tickers (VALUE Blue-Chips + MOMENTUM Race Horses)")
    print(f"  Capital  : €{INITIAL_CAPITAL_EUR:,.0f}")
    print(f"  Setup    : D — Dual-Engine (VALUE + MOMENTUM) + Learner Activo")
    print(f"  RNG Seed : 42  |  Walk-forward 85/15  |  L2 reg λ={_D_LAMBDA_L2}")
    print()

    all_dfs = load_ticker_data()
    if not all_dfs:
        print("[ERRO] Sem dados. Verifica a ligação à internet.")
        return

    spy_df, rsp_df = load_spy_rsp()
    trading_days   = get_trading_days(spy_df)
    print(f"[DATA] {len(trading_days)} dias de trading no período.\n")

    ind_cache  = precompute_indicators(all_dfs, trading_days)
    regimes    = precompute_regimes(spy_df, rsp_df, trading_days)
    ohlc_cache = build_ohlc_cache(all_dfs, trading_days)

    regime_counts: dict[str, int] = {}
    for r in regimes.values():
        regime_counts[r] = regime_counts.get(r, 0) + 1
    print(f"\n[PREP] Regimes: {regime_counts}")

    covered = sum(len(v) for v in ind_cache.values())
    print(f"[PREP] Indicadores válidos: {covered:,} "
          f"({covered / (len(TICKERS) * len(trading_days)) * 100:.1f}% do universo)\n")

    print("[SIM] A iniciar Setup D — Dual-Engine com Learner Activo...")
    print("      Aguarda: ~2 anos de dados × 28 tickers × EMA-20 + ATR-14\n")

    state   = run_simulation_d(trading_days, regimes, ind_cache, ohlc_cache)
    metrics = compute_metrics(state)

    tw = metrics["total_weeks"] or 1
    print(f"\n[SIM] Concluído.")
    print(f"      Lucro: {_eur(metrics['net_profit'], plus=True)}  |  "
          f"Retorno: {_pct(metrics['return_pct'], plus=True)}  |  "
          f"MaxDD: {metrics['max_dd_pct']:.2f}%  |  "
          f"Trades: {metrics['total_trades']}  |  "
          f"Mutações: {metrics['mut_total']}\n")

    print(format_report(metrics))


def _eur(v, plus=False):
    s = "+" if (plus and v >= 0) else ("-" if v < 0 else "")
    return f"{s}€{abs(v):,.2f}"


def _pct(v, plus=False):
    s = "+" if (plus and v >= 0) else ""
    return f"{s}{v:.2f}%"


if __name__ == "__main__":
    main()
