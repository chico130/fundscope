"""
scripts/backtest.py — Backtest realista reutilizando o codigo de producao (v2).

Simula os 3 bots em camadas e compara cada variante.
v2 adiciona:
  - VALUE trailing stop diferenciado (activa apos +2xATR, distancia 2.5xATR)
  - Adds em posicoes existentes do mesmo estilo (ate 10% equity)
  - BacktestParams para o Learner in-the-loop configurar todos os knobs
  - --use-optimized: carrega data/beta/optimized_backtest_params.json + bonnie_thresholds.json
  - Threshold da Bonnie ML por regime (em vez de um valor fixo)

Reutiliza:
  - bot.strategy.generate_signals / propose_trades
  - bot.cro.CRO.interpret
  - bot.learner.get_active_params
  - bot.data_layer.compute_rsi / compute_ema / compute_atr
  - bot.backtest.prime_regime_cache

LIMITACOES:
  * Spread 0.05% por execucao (T212 demo). Sem slippage intraday.
  * Calendario de earnings extrapolado por quarter (anchor em earnings.json).

Uso:
  PYTHONPATH=. python scripts/backtest.py
  PYTHONPATH=. python scripts/backtest.py --use-optimized
  PYTHONPATH=. python scripts/backtest.py --since 2025-01-01 --capital 10000
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------
# Silencia loggers de producao
# --------------------------------------------------------------------------
import bot.logger as _bot_logger
_bot_logger._append_to_json_list = lambda *a, **k: None  # type: ignore

from bot.config import BASE_DIR, RISK_CONFIG, CRO_CONFIG
from bot.data_layer import compute_ema
from bot.backtest import prime_regime_cache, _regime_cache
from bot.cro import CRO
import bot.strategy as strategy
import bot.learner as learner
from bot.watchlist_manager import SECTOR_TICKERS

strategy._earnings_days_ahead = lambda ticker: None  # type: ignore


CACHE_DIR              = BASE_DIR / "data" / "backtest_cache"
RESULTS_CSV            = BASE_DIR / "data" / "backtest_results.csv"
MODEL_PATH             = BASE_DIR / "data" / "models" / "bonnie_model.pkl"
MODEL_PATH_V2          = BASE_DIR / "data" / "models" / "bonnie_model_v2.pkl"
THRESHOLDS_PATH        = BASE_DIR / "data" / "beta" / "bonnie_thresholds.json"
OPT_BACKTEST_PARAMS    = BASE_DIR / "data" / "beta" / "optimized_backtest_params.json"
EARNINGS_PATH          = BASE_DIR / "earnings.json"
SPREAD_PCT             = 0.0005
MIN_BARS               = 210
WATCHLIST              = sorted(set(t for ts in SECTOR_TICKERS.values() for t in ts))
BONNIE_ML_THRESHOLD_DEFAULT = 0.60

REGIME_ENCODING: dict[str, int] = {
    "bull_trending":     3,
    "bull_lateral":      2,
    "bear_correction":   1,
    "bear_capitulation": 0,
    "unknown":          -1,
}


# --------------------------------------------------------------------------
# Tipos
# --------------------------------------------------------------------------

@dataclass
class Trade:
    ticker:                str
    entry_date:            str
    entry_price:           float
    qty:                   float
    style:                 str    = "VALUE"
    stop_loss:             float  = 0.0
    take_profit:           float  = 0.0
    entry_atr:             float  = 0.0
    peak_high:             float  = 0.0
    trail_active:          bool   = False   # NEW: tracks if VALUE trailing has activated
    exit_date:             str    = ""
    exit_price:            float  = 0.0
    exit_reason:           str    = ""
    result_eur:            float  = 0.0
    result_pct:            float  = 0.0
    n_adds:                int    = 0       # NEW: number of add operations on this position


@dataclass
class BacktestParams:
    """Knobs que o Learner pode optimizar."""
    atr_stop_mult_value:     float = 3.0
    atr_stop_mult_momentum:  float = 2.0
    atr_tp_mult:             float = 3.0
    value_trail_activation:  float = 2.0   # ATRs of profit before trailing kicks in (VALUE)
    value_trail_distance:    float = 2.5   # ATRs of trail distance (VALUE)
    max_position_pct:        float = 10.0  # % equity per position cap
    bonnie_threshold:        float = 0.60  # baseline; per-regime override via thresholds file
    add_max_existing_pct:    float = 0.06  # only add if existing pos < 6% of equity
    add_target_total_pct:    float = 0.10  # cap total position at 10% equity after add
    add_max_increment_pct:   float = 0.05  # max 5% equity per add
    add_min_increment_pct:   float = 0.02  # min 2% equity per add


@dataclass
class BacktestConfig:
    name:                 str
    enable_bonnie_ml:     bool
    enable_earnings_gate: bool
    enable_rs_bullish:    bool
    enable_value_trail:   bool = True   # NEW: V2 feature toggle
    enable_adds:          bool = True   # NEW: V2 feature toggle


@dataclass
class BacktestResult:
    name:               str
    capital_init:       float
    capital_final:      float
    total_return_pct:   float
    annual_return_pct:  float
    max_drawdown_pct:   float
    calmar:             float
    profit_factor:      float
    win_rate_pct:       float
    trades:             list[Trade]
    bonnie_rejected:    int   = 0
    bonnie_proposed:    int   = 0
    earnings_blocked:   int   = 0
    rs_blocked:         int   = 0
    regime_days_bull:   int   = 0
    regime_days_bear:   int   = 0
    sharpe_annual:      float = 0.0
    avg_deployed_pct:   float = 0.0   # NEW: % equity deployed (cash drag metric)
    n_adds:             int   = 0     # NEW: total add operations executed


# --------------------------------------------------------------------------
# Fetch OHLCV
# --------------------------------------------------------------------------

def fetch_ticker_history(ticker: str, start: datetime, end: datetime,
                         ttl_days: int = 1) -> Optional[pd.DataFrame]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{ticker}.pkl"

    if cache_path.exists():
        try:
            cached = pickle.loads(cache_path.read_bytes())
            if len(cached):
                cached_end = cached.index[-1].date()
                if (datetime.now().date() - cached_end).days <= ttl_days \
                   and cached.index[0].date() <= start.date():
                    return cached
        except Exception:
            pass

    try:
        df = yf.Ticker(ticker).history(
            start=(start - timedelta(days=400)).strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True,
        )
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        cache_path.write_bytes(pickle.dumps(df))
        return df
    except Exception as exc:
        print(f"  [WARN] fetch {ticker}: {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# Indicadores tecnicos pre-computados (matematica = bot.data_layer)
# --------------------------------------------------------------------------

def precompute_indicators(df: pd.DataFrame, spy_closes: Optional[np.ndarray] = None,
                          spy_index: Optional[pd.DatetimeIndex] = None) -> pd.DataFrame:
    closes  = df["Close"].astype(float).to_numpy()
    highs   = df["High"].astype(float).to_numpy()
    lows    = df["Low"].astype(float).to_numpy()
    volumes = df["Volume"].astype(float).to_numpy()
    n = len(closes)

    rsi    = np.full(n, np.nan)
    atr    = np.full(n, np.nan)
    vsma20 = np.full(n, np.nan)
    rs_bull = np.zeros(n, dtype=bool)

    period = 14
    if n >= period + 1:
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0,  deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_g = float(np.mean(gains[:period]))
        avg_l = float(np.mean(losses[:period]))
        for i in range(period, n - 1):
            if i > period:
                avg_g = (avg_g * (period - 1) + gains[i]) / period
                avg_l = (avg_l * (period - 1) + losses[i]) / period
            if avg_l == 0:
                rsi[i + 1] = 100.0
            else:
                rs = avg_g / avg_l
                rsi[i + 1] = round(100 - (100 / (1 + rs)), 2)

    def _ema(arr, p):
        out = np.full(n, np.nan)
        if n < p:
            return out
        seed = float(np.mean(arr[:p]))
        out[p - 1] = round(seed, 4)
        k = 2 / (p + 1)
        e = seed
        for i in range(p, n):
            e = arr[i] * k + e * (1 - k)
            out[i] = round(e, 4)
        return out

    ema20  = _ema(closes,  20)
    ema50  = _ema(closes,  50)
    ema200 = _ema(closes, 200)

    if n >= period + 1:
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i]  - closes[i - 1]),
            )
        a = float(np.mean(tr[1:period + 1]))
        atr[period] = round(a, 4)
        for i in range(period + 1, n):
            a = (a * (period - 1) + tr[i]) / period
            atr[i] = round(a, 4)

    if n >= 20:
        cs = np.cumsum(volumes, dtype=float)
        vsma20[19:] = (cs[19:] - np.concatenate(([0.0], cs[:-20]))) / 20

    if spy_closes is not None and spy_index is not None:
        spy_aligned = pd.Series(spy_closes, index=spy_index).reindex(df.index).ffill().to_numpy()
        valid = ~np.isnan(spy_aligned) & (spy_aligned > 0)
        rs_series = np.where(valid, closes / np.where(valid, spy_aligned, 1.0), np.nan)
        rs_ema20 = np.full(n, np.nan)
        first_valid = None
        for i in range(n):
            if not np.isnan(rs_series[i]):
                first_valid = i; break
        if first_valid is not None and n - first_valid >= 20:
            window = rs_series[first_valid:first_valid + 20]
            if not np.isnan(window).any():
                seed = float(np.mean(window))
                rs_ema20[first_valid + 19] = seed
                k = 2 / 21
                e = seed
                for i in range(first_valid + 20, n):
                    if not np.isnan(rs_series[i]):
                        e = rs_series[i] * k + e * (1 - k)
                        rs_ema20[i] = e
        for i in range(n):
            if not np.isnan(rs_series[i]) and not np.isnan(rs_ema20[i]):
                rs_bull[i] = rs_series[i] > rs_ema20[i]

    out = pd.DataFrame({
        "Open":     df["Open"].to_numpy(),
        "High":     highs, "Low": lows, "Close": closes, "Volume": volumes,
        "rsi_14":   rsi, "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "atr_14":   atr, "vol_sma20": vsma20,
        "rs_bull":  rs_bull,
    }, index=df.index)
    return out


def build_technicals_at(row, rs_override: Optional[bool] = None) -> Optional[dict]:
    rsi    = row["rsi_14"]
    ema20  = row["ema20"]
    ema50  = row["ema50"]
    ema200 = row["ema200"]
    atr    = row["atr_14"]
    vol    = row["Volume"]
    vsma   = row["vol_sma20"]
    last   = row["Close"]

    if pd.isna(rsi) or pd.isna(ema50) or pd.isna(ema200):
        return None

    ema50_above   = bool(ema50 > ema200)
    ema20_above50 = bool(ema20 > ema50) if not pd.isna(ema20) else None
    px_above_e20  = bool(last  > ema20) if not pd.isna(ema20) else None
    vratio        = round(float(vol / vsma), 2) if not pd.isna(vsma) and vsma > 0 else None

    rs = rs_override if rs_override is not None else bool(row["rs_bull"])

    return {
        "rsi_14":              float(rsi),
        "ema_20":              None if pd.isna(ema20)  else float(ema20),
        "ema50":               float(ema50),
        "ema200":              float(ema200),
        "ema20_above_ema50":   ema20_above50,
        "ema50_above_ema200":  ema50_above,
        "price_above_ema20":   px_above_e20,
        "volume_ratio_vs_avg": vratio,
        "atr_14":              None if pd.isna(atr) else float(atr),
        "last_price":          float(last),
        "rs_bullish":          rs,
    }


# --------------------------------------------------------------------------
# Bonnie ML (suporta modelo v1 — 4 features — e v2 — N features novas)
# --------------------------------------------------------------------------

class BonnieML:
    def __init__(self, params: BacktestParams) -> None:
        self.model = None
        self.available = False
        self.feature_names: list[str] = []
        self.params = params
        self.regime_thresholds: dict[str, float] = {}
        # Prefere v2 se existir
        path = MODEL_PATH_V2 if MODEL_PATH_V2.exists() else MODEL_PATH
        self.model_path = path
        if path.exists():
            try:
                import joblib
                self.model = joblib.load(path)
                self.feature_names = list(getattr(self.model, "feature_names_in_", []))
                self.available = True
            except Exception as exc:
                print(f"  [WARN] Bonnie ML load: {exc}", file=sys.stderr)
        # Per-regime thresholds (optional)
        if THRESHOLDS_PATH.exists():
            try:
                self.regime_thresholds = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.regime_thresholds = {}

    def threshold_for(self, regime: str) -> float:
        return self.regime_thresholds.get(regime, self.params.bonnie_threshold)

    def _build_features(self, tech: dict, regime: str, days_since_earn: Optional[int]) -> Optional[pd.DataFrame]:
        """Build feature row matching the loaded model's feature_names_in_."""
        if not self.feature_names:
            # v1 schema fallback
            self.feature_names = ["rsi_14", "ema50_above_200", "vol_ratio", "regime"]
        row = {}
        for name in self.feature_names:
            if name == "rsi_14":
                row[name] = float(tech.get("rsi_14") or 50.0)
            elif name == "ema50_above_200":
                row[name] = 1 if tech.get("ema50_above_ema200") else 0
            elif name == "vol_ratio":
                row[name] = float(tech.get("volume_ratio_vs_avg") or 1.0)
            elif name == "regime":
                row[name] = REGIME_ENCODING.get(regime, -1)
            elif name == "price_vs_ema20":
                ema20 = tech.get("ema_20")
                price = tech.get("last_price")
                row[name] = float((price - ema20) / ema20 * 100) if (ema20 and price) else 0.0
            elif name == "atr_pct":
                atr = tech.get("atr_14")
                price = tech.get("last_price")
                row[name] = float(atr / price * 100) if (atr and price) else 0.0
            elif name == "atr_percentile_60d":
                row[name] = float(tech.get("atr_percentile_60d") or 0.5)
            elif name == "adx_14":
                row[name] = float(tech.get("adx_14") or 20.0)
            elif name == "days_since_earnings":
                row[name] = float(days_since_earn) if days_since_earn is not None else 60.0
            else:
                row[name] = 0.0
        return pd.DataFrame([row], columns=self.feature_names)

    def approve(self, tech: dict, regime: str,
                days_since_earn: Optional[int] = None) -> tuple[bool, float]:
        if not self.available:
            return True, 1.0
        try:
            X = self._build_features(tech, regime, days_since_earn)
            p_success = float(self.model.predict_proba(X)[0, 1])
            return p_success >= self.threshold_for(regime), p_success
        except Exception:
            return True, 1.0


# --------------------------------------------------------------------------
# Earnings gate
# --------------------------------------------------------------------------

def build_earnings_calendar(start: datetime, end: datetime) -> dict[str, list]:
    """{ticker: sorted list of datetime} de earnings extrapolados."""
    out: dict[str, list] = {}
    if not EARNINGS_PATH.exists():
        return out
    try:
        data = json.loads(EARNINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return out
    entries = data.get("earnings", []) if isinstance(data, dict) else []
    for entry in entries:
        ticker = (entry.get("ticker") or "").upper()
        date_s = entry.get("data") or ""
        if not ticker or not date_s:
            continue
        try:
            anchor = datetime.strptime(date_s, "%Y-%m-%d")
        except ValueError:
            continue
        dates: list = out.setdefault(ticker, [])
        d = anchor
        while d.date() >= start.date() - timedelta(days=30):
            dates.append(d)
            d -= timedelta(days=91)
        d = anchor + timedelta(days=91)
        while d.date() <= end.date() + timedelta(days=30):
            dates.append(d)
            d += timedelta(days=91)
    for ticker in out:
        out[ticker] = sorted(set(out[ticker]))
    return out


def is_in_earnings_window(ticker: str, today: datetime,
                          calendar: dict[str, list]) -> bool:
    earnings = calendar.get(ticker)
    if not earnings:
        return False
    for ed in earnings:
        delta = (ed - today).days
        if -1 <= delta <= 2:
            return True
    return False


def days_since_last_earnings(ticker: str, today: datetime,
                             calendar: dict[str, list]) -> Optional[int]:
    earnings = calendar.get(ticker)
    if not earnings:
        return None
    past = [ed for ed in earnings if ed <= today]
    if not past:
        return None
    return (today - past[-1]).days


# --------------------------------------------------------------------------
# Event loop
# --------------------------------------------------------------------------

def run_event_loop(
    config:           BacktestConfig,
    params:           BacktestParams,
    calendar:         list,
    histories:        dict[str, pd.DataFrame],
    capital_init:     float,
    bonnie_ml:        BonnieML,
    earnings_cal:     dict[str, list],
) -> BacktestResult:

    cash:           float                  = capital_init
    open_positions: dict[str, Trade]       = {}
    closed_trades:  list[Trade]            = []
    equity_curve:   list[tuple[str,float]] = []
    deployed_pcts:  list[float]            = []

    bonnie_rejected = 0
    bonnie_proposed = 0
    earnings_blocked = 0
    rs_blocked      = 0
    n_adds_total    = 0
    regime_bull_days = 0
    regime_bear_days = 0

    cro = CRO()

    # Override RISK_CONFIG.max_position_pct dynamically (the live config is 10)
    original_max_pos = RISK_CONFIG["max_position_pct"]
    RISK_CONFIG["max_position_pct"] = params.max_position_pct

    try:
        for di, today in enumerate(calendar):
            today_str = today.strftime("%Y-%m-%d")
            regime = _regime_cache.get(today_str, "unknown")
            if regime in ("bull_trending", "bull_lateral"):
                regime_bull_days += 1
            elif regime in ("bear_correction", "bear_capitulation"):
                regime_bear_days += 1

            # ---- INTRADAY: stop / take profit ---------------------------------
            for ticker in list(open_positions.keys()):
                tr = open_positions[ticker]
                df = histories.get(ticker)
                if df is None or today not in df.index:
                    continue
                bar = df.loc[today]
                lo, hi = float(bar["Low"]), float(bar["High"])

                # Track peak for both styles
                tr.peak_high = max(tr.peak_high, hi)

                if tr.style == "MOMENTUM":
                    mult = strategy._PC.get("momentum_atr_multiplier", 2.5)
                    if tr.entry_atr > 0:
                        tr.stop_loss = max(tr.stop_loss, tr.peak_high - mult * tr.entry_atr)
                elif tr.style == "VALUE" and config.enable_value_trail and tr.entry_atr > 0:
                    # VALUE trailing: activates only after +activation x ATR profit
                    activation_level = tr.entry_price + params.value_trail_activation * tr.entry_atr
                    if tr.peak_high >= activation_level:
                        tr.trail_active = True
                        new_stop = tr.peak_high - params.value_trail_distance * tr.entry_atr
                        tr.stop_loss = max(tr.stop_loss, new_stop)

                exit_px, reason = None, ""
                if lo <= tr.stop_loss:
                    if tr.style == "MOMENTUM":
                        reason = "trailing_stop"
                    elif tr.style == "VALUE" and tr.trail_active:
                        reason = "value_trailing_stop"
                    else:
                        reason = "stop_loss_atr"
                    exit_px = tr.stop_loss
                elif hi >= tr.take_profit:
                    exit_px, reason = tr.take_profit, "take_profit_atr"

                if exit_px is not None:
                    _close_trade(tr, today_str, exit_px, reason)
                    cash += tr.exit_price * tr.qty
                    closed_trades.append(tr)
                    del open_positions[ticker]

            if regime == "unknown":
                ev = cash + _portfolio_value(open_positions, histories, today)
                equity_curve.append((today_str, ev))
                deployed = (ev - cash) / ev * 100 if ev > 0 else 0
                deployed_pcts.append(deployed)
                continue

            # ---- market_data / portfolio_state --------------------------------
            market_data, market_snap = {}, {}
            for ticker, df in histories.items():
                if today not in df.index:
                    continue
                rs_override = None if config.enable_rs_bullish else True
                tech = build_technicals_at(df.loc[today], rs_override=rs_override)
                if tech is None:
                    continue
                market_data[ticker] = {"technicals": tech, "last_price": tech["last_price"]}
                market_snap[ticker] = {"last_price":  tech["last_price"]}

            positions_list:    list[dict]      = []
            position_styles:   dict[str, str]  = {}
            position_peaks:    dict[str, float] = {}
            for ticker, tr in open_positions.items():
                cur = market_data.get(ticker, {}).get("last_price") or tr.entry_price
                positions_list.append({
                    "ticker":         ticker,
                    "quantity":       tr.qty,
                    "averagePrice":   tr.entry_price,
                    "value":          cur * tr.qty,
                    "current_price":  cur,
                    "market_data":    {"last_price": cur},
                })
                position_styles[ticker] = tr.style
                position_peaks[ticker]  = tr.peak_high

            current_equity = cash + sum(p["value"] for p in positions_list)
            portfolio_state = {
                "positions":       positions_list,
                "cash":            {"free": cash},
                "market_snapshot": market_snap,
            }

            signals = strategy.generate_signals(
                market_data, portfolio_state, regime=regime,
                position_styles=position_styles, position_peaks=position_peaks,
            )
            proposals = strategy.propose_trades(signals, portfolio_state, regime=regime)

            _inject_cro_state(cro, closed_trades, open_positions, today, today_str)

            # ---- Processa propostas -------------------------------------------
            for prop in proposals:
                if prop.side == "BUY":
                    cur_px = market_data.get(prop.ticker, {}).get("last_price")
                    if not cur_px:
                        continue

                    # Earnings gate
                    if config.enable_earnings_gate and is_in_earnings_window(prop.ticker, today, earnings_cal):
                        earnings_blocked += 1
                        continue

                    tech = market_data[prop.ticker]["technicals"]

                    # rs_bullish gate explicito
                    if config.enable_rs_bullish and prop.style == "MOMENTUM" and not tech.get("rs_bullish"):
                        rs_blocked += 1
                        continue

                    # Bonnie ML filter (com per-regime threshold)
                    if config.enable_bonnie_ml:
                        bonnie_proposed += 1
                        d_since = days_since_last_earnings(prop.ticker, today, earnings_cal)
                        approved_ml, _p = bonnie_ml.approve(tech, regime, d_since)
                        if not approved_ml:
                            bonnie_rejected += 1
                            continue

                    # === SAME-TICKER ADD ===
                    if prop.ticker in open_positions:
                        if not config.enable_adds:
                            continue
                        pos = open_positions[prop.ticker]
                        if current_equity <= 0:
                            continue
                        pos_pct = (pos.qty * cur_px) / current_equity
                        if pos_pct >= params.add_max_existing_pct:
                            continue
                        if pos.style != prop.style:
                            continue
                        target_eur     = current_equity * params.add_target_total_pct
                        current_pos_eur = pos.qty * cur_px
                        add_size_eur = min(
                            target_eur - current_pos_eur,
                            current_equity * params.add_max_increment_pct,
                            cash * 0.95,
                        )
                        if add_size_eur < current_equity * params.add_min_increment_pct:
                            continue
                        if add_size_eur < 50:
                            continue
                        entry_px = cur_px * (1 + SPREAD_PCT / 2)
                        add_qty = round(add_size_eur / entry_px, 4)
                        if add_qty <= 0:
                            continue
                        new_total_qty   = pos.qty + add_qty
                        new_avg_price   = (pos.qty * pos.entry_price + add_qty * entry_px) / new_total_qty
                        pos.qty         = new_total_qty
                        pos.entry_price = round(new_avg_price, 4)
                        # Re-anchor stops on new average (more conservative)
                        if pos.entry_atr > 0:
                            new_stop = pos.entry_price - params.atr_stop_mult_value * pos.entry_atr
                            pos.stop_loss   = max(pos.stop_loss, new_stop)
                            pos.take_profit = pos.entry_price + params.atr_tp_mult * pos.entry_atr
                        pos.n_adds += 1
                        n_adds_total += 1
                        cash -= entry_px * add_qty
                        continue   # done with this proposal

                    # === NEW POSITION ===
                    prop.price = cur_px
                    verdict = cro.interpret(portfolio_state, proposed=prop, regime=regime)
                    if not verdict.approved:
                        continue

                    size_eur = min(verdict.final_size_eur, cash * 0.95)
                    if size_eur < 50:
                        continue

                    entry_px = cur_px * (1 + SPREAD_PCT / 2)
                    qty      = round(size_eur / entry_px, 4)
                    if qty <= 0:
                        continue

                    atr = tech.get("atr_14") or 0.0
                    stop_mult = (params.atr_stop_mult_momentum
                                 if prop.style == "MOMENTUM"
                                 else params.atr_stop_mult_value)
                    if atr > 0:
                        stop_px = entry_px - stop_mult * atr
                        tp_px   = entry_px + params.atr_tp_mult * atr
                    else:
                        stop_px = entry_px * (1 - CRO_CONFIG.get("atr_fallback_stop_pct", 5.0) / 100)
                        tp_px   = entry_px * (1 + RISK_CONFIG["take_profit_pct"] / 100)

                    tr = Trade(
                        ticker=prop.ticker, entry_date=today_str,
                        entry_price=round(entry_px, 4), qty=qty,
                        style=prop.style, entry_atr=atr,
                        stop_loss=round(stop_px, 4), take_profit=round(tp_px, 4),
                        peak_high=entry_px,
                    )
                    cash -= entry_px * qty
                    open_positions[prop.ticker] = tr

                elif prop.side == "SELL":
                    if prop.ticker not in open_positions:
                        continue
                    tr = open_positions[prop.ticker]
                    cur_px = market_data.get(prop.ticker, {}).get("last_price")
                    if not cur_px:
                        continue
                    exit_px  = cur_px * (1 - SPREAD_PCT / 2)
                    sell_qty = min(prop.qty, tr.qty)
                    cash    += exit_px * sell_qty
                    if sell_qty >= tr.qty * 0.999:
                        _close_trade(tr, today_str, exit_px, (prop.reason or "signal_exit")[:40])
                        closed_trades.append(tr)
                        del open_positions[prop.ticker]
                    else:
                        tr.qty -= sell_qty

            ev = cash + _portfolio_value(open_positions, histories, today)
            equity_curve.append((today_str, ev))
            deployed = (ev - cash) / ev * 100 if ev > 0 else 0
            deployed_pcts.append(deployed)

        # Mark-to-market posicoes abertas
        last = calendar[-1]
        for ticker, tr in list(open_positions.items()):
            df = histories.get(ticker)
            if df is None or last not in df.index:
                continue
            cur_px  = float(df.loc[last, "Close"])
            exit_px = cur_px * (1 - SPREAD_PCT / 2)
            cash   += exit_px * tr.qty
            _close_trade(tr, last.strftime("%Y-%m-%d"), exit_px, "backtest_end_mtm")
            closed_trades.append(tr)
            del open_positions[ticker]

    finally:
        RISK_CONFIG["max_position_pct"] = original_max_pos

    avg_deployed = sum(deployed_pcts) / len(deployed_pcts) if deployed_pcts else 0
    return _build_result(config, capital_init, cash, closed_trades, equity_curve,
                         bonnie_rejected, bonnie_proposed, earnings_blocked, rs_blocked,
                         regime_bull_days, regime_bear_days, avg_deployed, n_adds_total)


def _build_result(config, capital_init, cash, trades, equity,
                  bonnie_rejected, bonnie_proposed, earnings_blocked, rs_blocked,
                  regime_bull, regime_bear, avg_deployed, n_adds) -> BacktestResult:
    n = len(trades)
    wins   = [t for t in trades if t.result_eur > 0]
    losses = [t for t in trades if t.result_eur < 0]
    wr = len(wins) / n * 100 if n else 0
    gw = sum(t.result_eur for t in wins)
    gl = abs(sum(t.result_eur for t in losses))
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)

    peak, max_dd = (equity[0][1] if equity else capital_init), 0.0
    for _, eq in equity:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)

    days = len(equity)
    years = days / 252 if days > 0 else 1
    annual = ((cash / capital_init) ** (1 / years) - 1) * 100 if years > 0 else 0
    calmar = annual / max_dd if max_dd > 0 else 0

    rets = []
    for i in range(1, len(equity)):
        prev = equity[i - 1][1]
        if prev > 0:
            rets.append((equity[i][1] - prev) / prev)
    if rets:
        mean = sum(rets) / len(rets)
        var  = sum((r - mean) ** 2 for r in rets) / len(rets)
        std  = math.sqrt(var)
        sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0
    else:
        sharpe = 0

    return BacktestResult(
        name=config.name,
        capital_init=capital_init,
        capital_final=cash,
        total_return_pct=(cash - capital_init) / capital_init * 100,
        annual_return_pct=annual,
        max_drawdown_pct=max_dd,
        calmar=calmar,
        profit_factor=pf,
        win_rate_pct=wr,
        trades=trades,
        bonnie_rejected=bonnie_rejected,
        bonnie_proposed=bonnie_proposed,
        earnings_blocked=earnings_blocked,
        rs_blocked=rs_blocked,
        regime_days_bull=regime_bull,
        regime_days_bear=regime_bear,
        sharpe_annual=sharpe,
        avg_deployed_pct=avg_deployed,
        n_adds=n_adds,
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _close_trade(tr: Trade, exit_date: str, exit_px: float, reason: str) -> None:
    tr.exit_date   = exit_date
    tr.exit_price  = round(exit_px, 4)
    tr.exit_reason = reason
    tr.result_eur  = round((exit_px - tr.entry_price) * tr.qty, 2)
    tr.result_pct  = round((exit_px - tr.entry_price) / tr.entry_price * 100, 2)


def _portfolio_value(positions, histories, today) -> float:
    v = 0.0
    for ticker, tr in positions.items():
        df = histories.get(ticker)
        if df is not None and today in df.index:
            v += tr.qty * float(df.loc[today, "Close"])
        else:
            v += tr.qty * tr.entry_price
    return v


def _inject_cro_state(cro, closed, open_pos, today, today_str) -> None:
    cutoff = today - timedelta(days=7)
    recent = [t for t in closed
              if t.exit_date and datetime.strptime(t.exit_date, "%Y-%m-%d") >= cutoff]
    wins_7d = sum(1 for t in recent if t.result_eur > 0)
    wr      = wins_7d / len(recent) if recent else 0.5
    total, peak, max_dd = 0.0, 0.0, 0.0
    for t in closed:
        total += t.result_eur
        if total > peak: peak = total
        if peak > 0:
            max_dd = max(max_dd, (peak - total) / peak * 100)
    trades_today = sum(1 for p in open_pos.values() if p.entry_date == today_str)
    cro._state = {
        "closed_count":    len(closed),
        "recent_count":    len(recent),
        "wins_7d":         wins_7d,
        "win_rate_7d":     round(wr, 4),
        "drawdown_pct":    round(max_dd, 2),
        "trades_today":    trades_today,
        "sector_exposure": {},
        "all_closed": [
            {"id": f"{t.ticker}_{t.entry_date}", "ticker": t.ticker,
             "datetime": t.entry_date + "T15:00:00+00:00", "side": "BUY",
             "closed_at": t.exit_date + "T20:00:00+00:00",
             "result_eur": t.result_eur, "result_pct": t.result_pct,
             "context": {"style": t.style}}
            for t in closed
        ],
    }


# --------------------------------------------------------------------------
# Load optimized backtest params
# --------------------------------------------------------------------------

def load_optimized_params() -> Optional[BacktestParams]:
    if not OPT_BACKTEST_PARAMS.exists():
        return None
    try:
        data = json.loads(OPT_BACKTEST_PARAMS.read_text(encoding="utf-8"))
        p = BacktestParams()
        for k, v in data.get("params", {}).items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p
    except Exception as exc:
        print(f"  [WARN] load_optimized_params: {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# Data loading (extracted so learner_backtest can reuse)
# --------------------------------------------------------------------------

def load_data_for_backtest(start: datetime, end: datetime,
                           verbose: bool = True) -> tuple[list, dict, np.ndarray, pd.DatetimeIndex]:
    fetch_start = start - timedelta(days=400)
    spy_raw = fetch_ticker_history("SPY", fetch_start, end)
    if spy_raw is None:
        raise RuntimeError("Nao foi possivel obter calendario SPY")
    spy_closes = spy_raw["Close"].astype(float).to_numpy()
    spy_index  = spy_raw.index

    if verbose: print(f"[Data] A carregar OHLCV para {len(WATCHLIST)} tickers...")
    histories: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    for i, ticker in enumerate(WATCHLIST, 1):
        if verbose and i % 40 == 0:
            print(f"       ({i}/{len(WATCHLIST)})")
        raw = fetch_ticker_history(ticker, fetch_start, end)
        if raw is None or len(raw) < MIN_BARS:
            failed.append(ticker); continue
        histories[ticker] = precompute_indicators(raw, spy_closes=spy_closes, spy_index=spy_index)
    if verbose: print(f"       OK: {len(histories)}    Sem dados: {len(failed)}")

    calendar = [d for d in spy_index
                if start <= d.to_pydatetime() <= end]
    if verbose: print(f"       Calendario: {len(calendar)} dias entre {start.date()} e {end.date()}")
    return calendar, histories, spy_closes, spy_index


def prime_regimes(calendar: list, verbose: bool = True) -> None:
    if verbose: print("[Regime] A pre-carregar regimes (SPY/RSP)...")
    date_strs = [d.strftime("%Y-%m-%d") for d in calendar]
    prime_regime_cache(date_strs)
    if verbose: print(f"         {len(_regime_cache)} regimes em cache")


# --------------------------------------------------------------------------
# Orquestrador
# --------------------------------------------------------------------------

def run_all_variants(start: datetime, end: datetime, capital_init: float,
                     use_defaults: bool, use_optimized: bool, export_csv: bool) -> None:

    # Learner params (strategy._P etc)
    if use_defaults:
        print("\n[Setup] A forcar parametros DEFAULT do Learner.")
        strategy._P  = learner._DEFAULT_PARAMS
        strategy._PC = strategy._P["weekly"]["clyde"]
        strategy._PB = strategy._P["monthly"]["bonnie"]
        strategy._ENABLED_STYLES = strategy._P.get("enabled_styles", ["VALUE", "MOMENTUM"])
    else:
        print("\n[Setup] Parametros activos via bot.learner.get_active_params:")
    print(f"        RSI oversold ceiling: {strategy._PC['rsi_oversold_ceiling']}")
    print(f"        Vol ratio oversold:   {strategy._PC['vol_ratio_oversold_min']}")
    print(f"        Size factor (Bonnie): {strategy._PB['size_factor_pct']}")
    print(f"        Enabled styles:       {strategy._ENABLED_STYLES}")
    print(f"        max_position_pct:     {RISK_CONFIG['max_position_pct']}%")
    print(f"        max_positions_sector: {RISK_CONFIG['max_positions_per_sector']}")

    # BacktestParams (com possivel override de --use-optimized)
    params = BacktestParams()
    if use_optimized:
        opt = load_optimized_params()
        if opt:
            params = opt
            print(f"        BacktestParams: optimized loaded from {OPT_BACKTEST_PARAMS.name}")
        else:
            print(f"        BacktestParams: defaults (sem {OPT_BACKTEST_PARAMS.name})")
    print(f"        atr_stop_mult_value:  {params.atr_stop_mult_value}")
    print(f"        atr_tp_mult:          {params.atr_tp_mult}")
    print(f"        value_trail_active:   {params.value_trail_activation}xATR  dist {params.value_trail_distance}xATR")

    bonnie_ml = BonnieML(params)
    if bonnie_ml.available:
        ftype = "v2" if bonnie_ml.model_path == MODEL_PATH_V2 else "v1"
        thr = f"per-regime ({bonnie_ml.regime_thresholds})" if bonnie_ml.regime_thresholds else f"flat {params.bonnie_threshold}"
        print(f"        Bonnie ML:            {ftype} loaded ({bonnie_ml.model_path.name}, threshold={thr})")
    else:
        print(f"        Bonnie ML:            NAO disponivel - variantes +Bonnie/Full = PASS-THROUGH")

    earnings_cal = build_earnings_calendar(start, end)
    n_eps = sum(1 for v in earnings_cal.values() if v)
    print(f"        Earnings calendar:    {n_eps} tickers, {sum(len(v) for v in earnings_cal.values())} datas")

    # Data
    calendar, histories, _, _ = load_data_for_backtest(start, end)
    prime_regimes(calendar)

    # Variants
    variants = [
        BacktestConfig("Clyde-only",     enable_bonnie_ml=False, enable_earnings_gate=False, enable_rs_bullish=False),
        BacktestConfig("+Bonnie",        enable_bonnie_ml=True,  enable_earnings_gate=False, enable_rs_bullish=False),
        BacktestConfig("+Earnings",      enable_bonnie_ml=True,  enable_earnings_gate=True,  enable_rs_bullish=False),
        BacktestConfig("Full (3 bots)",  enable_bonnie_ml=True,  enable_earnings_gate=True,  enable_rs_bullish=True),
    ]

    results: list[BacktestResult] = []
    for v in variants:
        print(f"\n[Run] {v.name}  (bonnie_ml={v.enable_bonnie_ml}, earnings={v.enable_earnings_gate}, rs={v.enable_rs_bullish})")
        t0 = time.time()
        r = run_event_loop(v, params, calendar, histories, capital_init, bonnie_ml, earnings_cal)
        results.append(r)
        print(f"      Concluido em {time.time()-t0:.0f}s  |  "
              f"return {r.total_return_pct:+.1f}%  |  PF {r.profit_factor:.2f}  |  "
              f"trades {len(r.trades)}  |  WR {r.win_rate_pct:.1f}%  |  deployed {r.avg_deployed_pct:.1f}%  |  adds {r.n_adds}")
        if v.enable_bonnie_ml and bonnie_ml.available:
            pct = (r.bonnie_rejected / r.bonnie_proposed * 100) if r.bonnie_proposed else 0
            print(f"      Bonnie filtrou: {r.bonnie_rejected}/{r.bonnie_proposed} ({pct:.1f}%)")
        if v.enable_earnings_gate:
            print(f"      Bloqueios earnings: {r.earnings_blocked}")
        if v.enable_rs_bullish:
            print(f"      Bloqueios rs_bullish: {r.rs_blocked}")

    print_comparison_table(results)
    full = results[-1]
    print_full_summary(full, start, end)

    if export_csv:
        export_trades_csv(full.trades)


# --------------------------------------------------------------------------
# Tabela comparativa
# --------------------------------------------------------------------------

def print_comparison_table(results: list[BacktestResult]) -> None:
    print("\n" + "=" * 86)
    print("=== COMPARACAO ===")
    print("=" * 86)
    header = f"{'':24s}" + "".join(f"{r.name:>15s}" for r in results)
    print(header)
    print("-" * len(header))

    def row(label, vals):
        print(f"{label:24s}" + "".join(f"{v:>15s}" for v in vals))

    row("Trades",          [str(len(r.trades))                   for r in results])
    row("Win Rate",        [f"{r.win_rate_pct:.1f}%"             for r in results])
    row("Profit Factor",   [(f"{r.profit_factor:.2f}" if r.profit_factor != float('inf') else 'inf') for r in results])
    row("Calmar",          [f"{r.calmar:.2f}"                    for r in results])
    row("Sharpe (anual)",  [f"{r.sharpe_annual:.2f}"             for r in results])
    row("Retorno",         [f"{r.total_return_pct:+.1f}%"        for r in results])
    row("Anualizado",      [f"{r.annual_return_pct:+.1f}%"       for r in results])
    row("Max Drawdown",    [f"-{r.max_drawdown_pct:.1f}%"        for r in results])
    row("Capital final",   [f"EUR {r.capital_final:,.0f}"        for r in results])
    row("% deployed avg",  [f"{r.avg_deployed_pct:.1f}%"         for r in results])
    print("-" * len(header))
    row("Bonnie rejeitados",  [str(r.bonnie_rejected) if r.bonnie_proposed else "--" for r in results])
    row("Earnings blocked",   [str(r.earnings_blocked) for r in results])
    row("RS blocked",         [str(r.rs_blocked) for r in results])
    row("Adds executados",    [str(r.n_adds) for r in results])
    print("=" * 86)
    full = results[-1]
    print(f"\nRegime: {full.regime_days_bull} dias bullish, {full.regime_days_bear} dias bearish")


def print_full_summary(r: BacktestResult, start: datetime, end: datetime) -> None:
    print(f"\n--- Detalhe da variante Full ---")
    by_ticker: dict[str, dict] = {}
    for t in r.trades:
        bt = by_ticker.setdefault(t.ticker, {"pnl": 0.0, "n": 0, "wins": 0})
        bt["pnl"]  += t.result_eur
        bt["n"]    += 1
        bt["wins"] += 1 if t.result_eur > 0 else 0
    top5 = sorted(by_ticker.items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]
    bot3 = sorted(by_ticker.items(), key=lambda x: x[1]["pnl"])[:3]

    by_reason: dict[str, int] = {}
    by_style:  dict[str, int] = {}
    for t in r.trades:
        by_reason[t.exit_reason or "?"] = by_reason.get(t.exit_reason or "?", 0) + 1
        by_style [t.style]              = by_style.get (t.style, 0) + 1

    if top5:
        print(f"Top 5 tickers:")
        for ticker, st in top5:
            wr_t = st["wins"] / st["n"] * 100 if st["n"] else 0
            print(f"  {ticker:6s} EUR {st['pnl']:+9.2f}  ({st['n']:2d} trades, WR {wr_t:.0f}%)")
    if bot3 and bot3[0][1]["pnl"] < 0:
        print(f"Piores 3 tickers:")
        for ticker, st in bot3:
            if st["pnl"] >= 0: break
            wr_t = st["wins"] / st["n"] * 100 if st["n"] else 0
            print(f"  {ticker:6s} EUR {st['pnl']:+9.2f}  ({st['n']:2d} trades, WR {wr_t:.0f}%)")
    print(f"Razoes de saida:")
    for rsn, c in sorted(by_reason.items(), key=lambda x: -x[1])[:10]:
        print(f"  {c:3d}x  {rsn[:60]}")
    print(f"Estilos: " + ", ".join(f"{k}={v}" for k, v in by_style.items()))
    print(f"Adds: {r.n_adds} operacoes de reforco em posicoes existentes")


def export_trades_csv(trades: list[Trade]) -> None:
    import csv
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker","style","entry_date","entry_price","qty",
                    "exit_date","exit_price","exit_reason",
                    "stop_loss","take_profit","entry_atr","n_adds",
                    "result_eur","result_pct"])
        for t in trades:
            w.writerow([t.ticker, t.style, t.entry_date, t.entry_price, t.qty,
                        t.exit_date, t.exit_price, t.exit_reason,
                        t.stop_loss, t.take_profit, t.entry_atr, t.n_adds,
                        t.result_eur, t.result_pct])
    print(f"\nTrades (Full) exportados: {RESULTS_CSV.relative_to(BASE_DIR)}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="FundScope backtest realista v2 (4 variantes)")
    p.add_argument("--since",         default=None, help="Data inicial YYYY-MM-DD (default: -2 anos)")
    p.add_argument("--until",         default=None, help="Data final YYYY-MM-DD (default: hoje)")
    p.add_argument("--capital",       type=float, default=5000.0)
    p.add_argument("--use-defaults",  action="store_true", help="Forca defaults do Learner")
    p.add_argument("--use-optimized", action="store_true", help="Carrega optimized_backtest_params.json (v2)")
    p.add_argument("--csv",           action="store_true", help="Exporta trades da variante Full para CSV")
    args = p.parse_args()

    end_dt   = datetime.strptime(args.until, "%Y-%m-%d") if args.until else datetime.now()
    start_dt = datetime.strptime(args.since, "%Y-%m-%d") if args.since else end_dt - timedelta(days=730)

    run_all_variants(start_dt, end_dt, args.capital, args.use_defaults, args.use_optimized, args.csv)
