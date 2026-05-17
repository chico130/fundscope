"""
backtest_comparison.py — Simulação histórica OOS de 3 setups concorrentes

Setups em paralelo:
  A — Clyde Puro         : todos os sinais técnicos, tamanho estático
  B — Clyde + Bonnie     : filtro de veto preditivo (threshold estático 60%)
  C — Clyde + Bonnie+CRO : atenuação contextual + disjuntores de segurança

Garantias Anti-Overfitting:
  • Indicadores calculados com dados estritamente até cada dia (rolling causal)
  • Hiperparâmetros congelados — exactamente como estão em produção
  • Sem re-treino. Sem look-ahead. Sequencial passo-a-passo.
  • Regra Pessimista: se TP e SL batem no mesmo dia → SL tem precedência

CLI:
    python backtest_comparison.py                         # período por defeito
    python backtest_comparison.py --start 2025-01-01     # período com bear correction
    python backtest_comparison.py --start 2022-01-01 --end 2023-12-31
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date as date_t
from pathlib import Path
from typing import Literal

import pandas as pd
import yfinance as yf

# Force UTF-8 on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Import pure indicator functions from bot
sys.path.insert(0, str(Path(__file__).parent))
from bot.data_layer import compute_ema

# ─────────────────────────────────────────────────────────────────────────────
# Constants — frozen at production values (zero overfitting)
# ─────────────────────────────────────────────────────────────────────────────

TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMD",  "META",   # XLK — Technology
    "JNJ",  "UNH",  "LLY",  "PFE",  "AMGN",  # XLV — Healthcare
    "AMZN", "HD",   "NKE",  "MCD",            # XLY — Consumer
    "CAT",  "GE",   "BA",                      # XLI — Industrial
    "XOM",  "CVX",  "COP",                     # XLE — Energy
]

SIM_START       = "2025-05-01"   # substituído por argparse se --start for passado
SIM_END         = "2026-05-17"   # substituído por argparse se --end for passado
_DATA_WARMUP_DAYS = 420          # dias de warmup antes do SIM_START para EMA-200

INITIAL_CAPITAL = 10_000.0       # USD

# Clyde — regras de sinal (congeladas de strategy.py)
RSI_A_MAX       = 35.0
RSI_B_MIN       = 40.0
RSI_B_MAX       = 55.0
VOL_A_MIN       = 1.2
VOL_B_MIN       = 1.8
BASE_POS_PCT    = 0.15           # 15% da equity × strength (strategy.py)
MAX_POS_PCT     = 0.20           # 20% hard cap (config.py RISK_CONFIG)
MIN_POS_USD     = 50.0

# Risco (congelado de config.py RISK_CONFIG)
STOP_LOSS_PCT   = 5.0
TP_PCT          = 10.0
MAX_HOLD_DAYS   = 10

# Bonnie thresholds (Setup B: sempre fixo; Setup C: controlado pelo CRO)
BONNIE_BASE_THRESH   = 0.60   # threshold standard
BONNIE_STRICT_THRESH = 0.64   # threshold apertado (CRO activa quando WR < 45%)

# CRO — Janela Deslizante Adaptativa (de config.py CRO_CONFIG)
CRO_MAX_DD         = 15.0
CRO_MAX_TRADES     = 10
CRO_WINDOW_N       = 25     # últimos N trades para alvo elástico
CRO_FALLBACK_WR    = 0.48   # WR base temporária quando < N trades
CRO_LOW_WR_TRIGGER = 0.45   # WR abaixo da qual o CRO aperta a Bonnie

# Factores de regime (de strategy.py _REGIME_SIZE_FACTOR)
_REGIME_SIZE: dict[str, float] = {
    "bull_trending":     1.0,
    "bull_lateral":      0.6,
    "bear_correction":   0.0,
    "bear_capitulation": 0.0,
    "unknown":           0.5,
}
_BEAR = {"bear_correction", "bear_capitulation"}

MIN_EMA_BARS = 210  # barras mínimas para EMA-200 fiável (backtest.py)


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
    days_open:   int = 0


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
    cash:           float             = INITIAL_CAPITAL
    positions:      list[OpenPosition] = field(default_factory=list)
    closed:         list[ClosedTrade]  = field(default_factory=list)
    equity_series:  list[float]        = field(default_factory=list)

    # Contadores para o relatório
    signals_fired:             int = 0
    bonnie_vetoes:             int = 0
    cro_reductions:            int = 0
    bonnie_strict_activations: int = 0   # vezes que CRO subiu Bonnie de 60% → 64%
    trades_today:              int = 0

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
            return 0.5   # assunção neutra quando não há histórico
        return sum(1 for t in recent if t.pnl > 0) / len(recent)


# ─────────────────────────────────────────────────────────────────────────────
# Download de dados (um download por ticker — robusto e sem ambiguidade MultiIndex)
# ─────────────────────────────────────────────────────────────────────────────

def _data_start() -> str:
    """Calcula DATA_START dinamicamente: SIM_START - warmup de 420 dias."""
    sim = datetime.strptime(SIM_START, "%Y-%m-%d")
    return (sim - timedelta(days=_DATA_WARMUP_DAYS)).strftime("%Y-%m-%d")


def _fetch_end() -> str:
    return (datetime.strptime(SIM_END, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")


def load_ticker_data() -> dict[str, pd.DataFrame]:
    """Download individual por ticker. Retorna {ticker: DataFrame OHLCV}."""
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
    """Download SPY + RSP para detecção de regime."""
    print("[DATA] A descarregar SPY/RSP para regime...")
    ds  = _data_start()
    end = _fetch_end()
    spy = yf.Ticker("SPY").history(
        start=ds, end=end, interval="1d", auto_adjust=True
    ).dropna(subset=["Close"])
    rsp = yf.Ticker("RSP").history(
        start=ds, end=end, interval="1d", auto_adjust=True
    ).dropna(subset=["Close"])
    return spy, rsp


def get_trading_days(spy_df: pd.DataFrame) -> list[str]:
    """Lista de dias de trading dentro de [SIM_START, SIM_END], derivada do SPY."""
    start = datetime.strptime(SIM_START, "%Y-%m-%d").date()
    end   = datetime.strptime(SIM_END,   "%Y-%m-%d").date()
    return sorted(
        dt.strftime("%Y-%m-%d")
        for dt in spy_df.index
        if start <= dt.date() <= end
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pré-computação (uma vez, fora do loop da simulação)
# ─────────────────────────────────────────────────────────────────────────────

def precompute_indicators(
    all_dfs: dict[str, pd.DataFrame],
    trading_days: list[str],
) -> dict[str, dict[str, dict]]:
    """
    Calcula RSI-14, EMA-50, EMA-200, vol_ratio para cada ticker × dia.

    Usa pandas rolling causal: o valor em t depende apenas de dados ≤ t.
    Equivalente a fatiar o histórico até cada data — sem look-ahead bias.
    """
    print(f"[PREP] A pré-calcular indicadores ({len(all_dfs)} tickers × {len(trading_days)} dias)...")

    # Data de corte: só precisamos de indicadores dentro do período de simulação
    sim_start_dt = datetime.strptime(SIM_START, "%Y-%m-%d").date()
    sim_end_dt   = datetime.strptime(SIM_END,   "%Y-%m-%d").date()

    cache: dict[str, dict[str, dict]] = {}

    for ticker, df in all_dfs.items():
        cache[ticker] = {}

        close = df["Close"].astype(float)
        vol   = df["Volume"].astype(float)
        high  = df["High"].astype(float)
        low   = df["Low"].astype(float)

        # EMAs — pandas ewm é idêntico à fórmula k=2/(n+1) de compute_ema
        ema50  = close.ewm(span=50,  adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        # RSI Wilder (alpha=1/14) — equivalente ao compute_rsi de data_layer.py
        delta    = close.diff()
        avg_gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        avg_loss = (-delta).clip(lower=0).ewm(com=13, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0.0, float("nan"))
        rsi      = (100.0 - (100.0 / (1.0 + rs))).round(2)

        # Volume ratio
        avg_vol   = vol.rolling(20).mean()
        vol_ratio = (vol / avg_vol).round(3)

        # Acumula contagem de barras por data para verificar warmup
        bar_count = pd.Series(
            range(1, len(df) + 1), index=df.index, dtype=int
        )

        # Indexa por date_str dentro do período de simulação
        for day_str in trading_days:
            target = datetime.strptime(day_str, "%Y-%m-%d").date()
            row    = df[df.index.date == target]
            if row.empty:
                continue

            idx = row.index[0]

            # Número de barras disponíveis até esta data — garante warmup EMA-200
            n_bars = int(bar_count.loc[idx])
            if n_bars < MIN_EMA_BARS:
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
    spy_df: pd.DataFrame,
    rsp_df: pd.DataFrame,
    trading_days: list[str],
) -> dict[str, str]:
    """
    Detecta regime de mercado por dia usando dados SPY/RSP estritamente até essa data.
    Lógica idêntica à de backtest.py _regime_at() e prime_regime_cache().
    """
    print(f"[PREP] A calcular regime para {len(trading_days)} dias...")

    spy_close = spy_df["Close"].astype(float)
    rsp_close = rsp_df["Close"].astype(float)

    regimes: dict[str, str] = {}

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
    all_dfs: dict[str, pd.DataFrame],
    trading_days: list[str],
) -> dict[str, dict[str, dict]]:
    """Constrói {ticker: {date_str: {high, low, close}}} para verificação SL/TP."""
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
# Geração de sinais — Clyde (congelado de strategy.py)
# ─────────────────────────────────────────────────────────────────────────────

def clyde_signal(ind: dict, regime: str) -> tuple[str, float]:
    """
    Retorna ("BUY" | "HOLD", strength).
    Regras A e B de strategy._entry_signal(), congeladas.
    """
    rsi       = ind["rsi_14"]
    ema_above = ind["ema50_above_200"]
    vol       = ind["vol_ratio"]

    if regime in _BEAR:
        return "HOLD", 0.0

    # Regra A — sobrevenda em tendência ascendente
    if rsi <= RSI_A_MAX and ema_above and vol >= VOL_A_MIN:
        return "BUY", round(min(1.0, 0.70 + (RSI_A_MAX - rsi) / 100), 4)

    # Regra B — RSI neutro + volume excepcional (momentum)
    if RSI_B_MIN <= rsi <= RSI_B_MAX and ema_above and vol >= VOL_B_MIN:
        return "BUY", round(min(1.0, 0.55 + (vol - VOL_B_MIN) / 10), 4)

    return "HOLD", 0.0


# ─────────────────────────────────────────────────────────────────────────────
# CRO — Janela Deslizante Adaptativa + Fórmula de Atenuação
# ─────────────────────────────────────────────────────────────────────────────

def elastic_target_wr(closed: list[ClosedTrade]) -> float:
    """
    Alvo de WR dinâmico: média dos últimos CRO_WINDOW_N trades fechados.
    Zero look-ahead — usa apenas o histórico disponível até este momento.
    """
    if len(closed) < CRO_WINDOW_N:
        return CRO_FALLBACK_WR
    recent = closed[-CRO_WINDOW_N:]   # ordenados cronologicamente (append sequencial)
    return round(sum(1 for t in recent if t.pnl > 0) / CRO_WINDOW_N, 4)


def dynamic_bonnie_threshold(closed: list[ClosedTrade]) -> float:
    """
    CRO controla o threshold de veto da Bonnie.
    WR(N) < 45%  →  64%  (mercado adverso — filtragem mais selectiva)
    WR(N) ≥ 45%  →  60%  (standard)
    """
    wr = elastic_target_wr(closed)
    return BONNIE_STRICT_THRESH if wr < CRO_LOW_WR_TRIGGER else BONNIE_BASE_THRESH


def cro_risk_factor(
    win_rate_7d:    float,
    drawdown_pct:   float,
    regime:         str,
    elastic_target: float,
) -> float:
    """
    Factor de risco contextual com alvo elástico.
    wr_adj compara WR_7d com o baseline histórico dos últimos 25 trades.
    """
    wr_adj = max(0.5, min(1.2, win_rate_7d / elastic_target if elastic_target > 0 else 1.0))
    dd_adj = max(0.3, min(1.0, 1.0 - drawdown_pct / CRO_MAX_DD))
    reg_f  = _REGIME_SIZE.get(regime, 0.5)
    return round(wr_adj * dd_adj * reg_f, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Simulação
# ─────────────────────────────────────────────────────────────────────────────

def _position_size(
    equity:   float,
    strength: float,
    regime:   str,
    cash:     float,
    cro_rf:   float = 1.0,
) -> float:
    """
    Calcula o tamanho da posição em USD.

    Setup A/B: strategy.propose_trades() — strength × 15% × equity × reg_factor
    Setup C  : CRO define novo tecto (max_pos_pct × risk_factor) sobre o mesmo base.
    """
    reg_f   = _REGIME_SIZE.get(regime, 0.0)
    base    = strength * equity * BASE_POS_PCT * reg_f
    cro_cap = equity * MAX_POS_PCT * cro_rf          # CRO ajusta o tecto máximo
    size    = min(base, cro_cap, cash * 0.95)
    return size if size >= MIN_POS_USD else 0.0


def process_day(
    state:      SimState,
    day_str:    str,
    regime:     str,
    ind_cache:  dict[str, dict[str, dict]],
    ohlc_cache: dict[str, dict[str, dict]],
) -> None:
    """Processa um dia de trading: fechar saídas → actualizar equity → abrir entradas."""
    today              = datetime.strptime(day_str, "%Y-%m-%d").date()
    state.trades_today = 0

    # Preços de fecho para o snapshot de equity deste dia
    close_prices: dict[str, float] = {
        t: d[day_str]["close"]
        for t, d in ohlc_cache.items()
        if day_str in d
    }

    # ── Passo 1: processar saídas de posições abertas ────────────────────────
    still_open: list[OpenPosition] = []

    for pos in state.positions:
        day_ohlc = ohlc_cache.get(pos.ticker, {}).get(day_str)

        if day_ohlc is None:
            # Mercado fechado ou sem dados para este ticker hoje
            pos.days_open += 1
            still_open.append(pos)
            continue

        high  = day_ohlc["high"]
        low   = day_ohlc["low"]
        close = day_ohlc["close"]

        hit_tp = high >= pos.tp_price
        hit_sl = low  <= pos.stop_price

        # Regra Pessimista: se TP e SL batem no mesmo dia → SL tem precedência
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

        pnl           = (exit_price - pos.entry_price) * pos.qty
        state.cash   += pos.qty * exit_price
        state.closed.append(ClosedTrade(
            ticker=pos.ticker,   setup=state.setup,
            entry_date=pos.entry_date, close_date=day_str,
            entry_price=pos.entry_price, exit_price=exit_price,
            qty=pos.qty, pnl=round(pnl, 4), reason=reason,
        ))

    state.positions = still_open
    state.mark_equity(close_prices)

    # ── Passo 2: procurar novas entradas ─────────────────────────────────────
    held = {p.ticker for p in state.positions}

    # Pré-calcular threshold dinâmico da Bonnie para Setup C (CRO controla Bonnie)
    if state.setup == "C":
        bonnie_thr    = dynamic_bonnie_threshold(state.closed)
        el_target     = elastic_target_wr(state.closed)
        # Registar activações do modo strict (CRO apertou a Bonnie neste dia)
        if bonnie_thr > BONNIE_BASE_THRESH:
            state.bonnie_strict_activations += 1
    else:
        bonnie_thr = BONNIE_BASE_THRESH
        el_target  = CRO_FALLBACK_WR   # não usado em B, mas declarado

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

        # Setup B — threshold fixo (60%); Setup C — threshold ditado pelo CRO
        if state.setup in ("B", "C") and strength < bonnie_thr:
            state.bonnie_vetoes += 1
            continue

        cro_rf = 1.0
        if state.setup == "C":
            dd = state.drawdown_pct()

            # Disjuntor 1 — drawdown ultrapassa limite CRO
            if dd > CRO_MAX_DD:
                state.bonnie_vetoes += 1   # conta como veto sistémico
                continue

            # Disjuntor 2 — limite de trades diários
            if state.trades_today >= CRO_MAX_TRADES:
                continue

            # Fórmula de Atenuação CRO com alvo elástico
            wr     = state.win_rate_7d(today)
            cro_rf = cro_risk_factor(wr, dd, regime, el_target)

        equity = state.current_equity()
        size   = _position_size(equity, strength, regime, state.cash, cro_rf)

        if size <= 0:
            continue

        entry = ind["close"]
        if entry <= 0:
            continue

        qty = round(size / entry, 6)

        # Rastreia atenuação CRO: posição seria maior sem o factor de risco
        if state.setup == "C" and cro_rf < 1.0:
            reg_f       = _REGIME_SIZE.get(regime, 0.0)
            unmodified  = min(strength * equity * BASE_POS_PCT * reg_f,
                               equity * MAX_POS_PCT, state.cash * 0.95)
            if size < unmodified - 0.01:
                state.cro_reductions += 1

        state.cash -= qty * entry
        state.positions.append(OpenPosition(
            ticker=ticker, entry_date=day_str, entry_price=entry, qty=qty,
            stop_price=round(entry * (1.0 - STOP_LOSS_PCT / 100.0), 4),
            tp_price=round(entry  * (1.0 + TP_PCT          / 100.0), 4),
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
    """Corre a simulação completa de 12 meses para um setup."""
    state = SimState(setup=setup)
    n     = len(trading_days)

    for i, day_str in enumerate(trading_days):
        regime = regimes.get(day_str, "unknown")
        process_day(state, day_str, regime, ind_cache, ohlc_cache)

        if (i + 1) % 60 == 0 or i == n - 1:
            eq = state.current_equity()
            print(f"  [{setup}] {day_str} ({i + 1}/{n}) | Equity: ${eq:,.0f} | "
                  f"Posições: {len(state.positions)} | Trades: {len(state.closed)}")

    # Força fecho de todas as posições ao fim do período (ao fecho do último dia)
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

    # Max drawdown a partir da série de equity diária
    series = state.equity_series
    max_dd, peak = 0.0, (series[0] if series else INITIAL_CAPITAL)
    for v in series:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

    return {
        "final_equity":              round(final, 2),
        "net_profit":                round(final - INITIAL_CAPITAL, 2),
        "return_pct":                round((final / INITIAL_CAPITAL - 1.0) * 100.0, 2),
        "win_rate_pct":              round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0,
        "max_dd_pct":                round(max_dd, 2),
        "profit_factor":             pf,
        "total_trades":              len(closed),
        "signals_fired":             state.signals_fired,
        "bonnie_vetoes":             state.bonnie_vetoes,
        "cro_reductions":            state.cro_reductions,
        "bonnie_strict_activations": state.bonnie_strict_activations,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Relatório — tabela Markdown
# ─────────────────────────────────────────────────────────────────────────────

def _pf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


def _sign(v: float) -> str:
    return f"${v:>+,.2f}" if v < 0 else f"${v:>+,.2f}"


def format_report(m: dict[str, dict]) -> str:
    a, b, c = m["A"], m["B"], m["C"]
    sep = "─" * 80

    rows = [
        ("Capital Final ($)",          f"${a['final_equity']:>12,.2f}",  f"${b['final_equity']:>12,.2f}",  f"${c['final_equity']:>12,.2f}"),
        ("Lucro Líquido ($)",          _sign(a['net_profit']),            _sign(b['net_profit']),            _sign(c['net_profit'])),
        ("Retorno Total (%)",          f"{a['return_pct']:>+.2f}%",       f"{b['return_pct']:>+.2f}%",       f"{c['return_pct']:>+.2f}%"),
        ("Win Rate (%)",               f"{a['win_rate_pct']:>.1f}%",      f"{b['win_rate_pct']:>.1f}%",      f"{c['win_rate_pct']:>.1f}%"),
        ("**Max Drawdown (%)**",       f"**{a['max_dd_pct']:.2f}%**",     f"**{b['max_dd_pct']:.2f}%**",    f"**{c['max_dd_pct']:.2f}%**"),
        ("Profit Factor",              _pf(a['profit_factor']),           _pf(b['profit_factor']),           _pf(c['profit_factor'])),
        ("Sinais Clyde Disparados",    f"{a['signals_fired']:,}",         f"{b['signals_fired']:,}",         f"{c['signals_fired']:,}"),
        ("Trades Executados",          f"{a['total_trades']:,}",          f"{b['total_trades']:,}",          f"{c['total_trades']:,}"),
        ("Vetos Bonnie (60%)",         "N/A",                             f"{b['bonnie_vetoes']:,}",         f"{c['bonnie_vetoes']:,}"),
        ("Posições Atenuadas CRO",     "N/A",                             "N/A",                             f"{c['cro_reductions']:,}"),
        ("Dias Bonnie Strict (64%)",   "N/A",                             "N/A",                             f"{c['bonnie_strict_activations']:,}"),
    ]

    hdr = (
        f"| {'Métrica':<30} | {'Setup A — Clyde':^22} "
        f"| {'Setup B — +Bonnie':^22} | {'Setup C — +CRO':^22} |"
    )
    sep2 = f"|{'-'*32}|{'-'*24}|{'-'*24}|{'-'*24}|"

    table = [hdr, sep2]
    for label, va, vb, vc in rows:
        table.append(f"| {label:<30} | {va:^22} | {vb:^22} | {vc:^22} |")

    lines = [
        "",
        sep,
        f"  FUNDSCOPE — Backtest Comparativo OOS  |  {SIM_START} → {SIM_END}",
        f"  Universe: {len(TICKERS)} tickers   Capital inicial: ${INITIAL_CAPITAL:,.0f} USD",
        f"  SL: {STOP_LOSS_PCT}%  TP: {TP_PCT}%  Horizonte máx: {MAX_HOLD_DAYS} dias",
        "  Regra Pessimista activa: se SL e TP batem no mesmo dia → SL tem precedência",
        sep,
        "",
        *table,
        "",
        sep,
        "  Resultados puramente OOS e sequenciais. Zero re-treino. Zero look-ahead.",
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
        description="FundScope — Backtest Comparativo de 3 Setups (OOS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  python backtest_comparison.py\n"
            "  python backtest_comparison.py --start 2025-01-01\n"
            "  python backtest_comparison.py --start 2022-01-01 --end 2023-12-31\n"
        ),
    )
    parser.add_argument("--start", default=SIM_START, help="Início da simulação YYYY-MM-DD")
    parser.add_argument("--end",   default=SIM_END,   help="Fim da simulação YYYY-MM-DD")
    args = parser.parse_args()

    SIM_START = args.start
    SIM_END   = args.end

    print("\n" + "=" * 60)
    print("  FUNDSCOPE — Backtest Comparativo de 3 Setups (OOS)")
    print("=" * 60)
    print(f"  Período  : {SIM_START} → {SIM_END}")
    print(f"  Universe : {len(TICKERS)} tickers")
    print(f"  Capital  : ${INITIAL_CAPITAL:,.0f} USD")
    print()

    # 1. Download de dados
    all_dfs = load_ticker_data()
    if not all_dfs:
        print("[ERRO] Sem dados de mercado. Verifica a ligação à internet.")
        return

    spy_df, rsp_df = load_spy_rsp()
    trading_days   = get_trading_days(spy_df)
    print(f"[DATA] {len(trading_days)} dias de trading no período de simulação.\n")

    # 2. Pré-computação (uma vez, fora do loop)
    ind_cache  = precompute_indicators(all_dfs, trading_days)
    regimes    = precompute_regimes(spy_df, rsp_df, trading_days)
    ohlc_cache = build_ohlc_cache(all_dfs, trading_days)

    # Resumo de regimes detectados
    regime_counts: dict[str, int] = {}
    for r in regimes.values():
        regime_counts[r] = regime_counts.get(r, 0) + 1
    print(f"\n[PREP] Regimes detectados: {regime_counts}")

    # Resumo de cobertura de indicadores
    covered = sum(len(v) for v in ind_cache.values())
    print(f"[PREP] Pontos de indicadores válidos: {covered:,} "
          f"({covered / (len(TICKERS) * len(trading_days)) * 100:.1f}% do universo)\n")

    # 3. Simulações
    metrics: dict[str, dict] = {}
    labels  = {"A": "Clyde Puro", "B": "Clyde + Bonnie", "C": "Clyde + Bonnie + CRO"}

    for setup_name in ("A", "B", "C"):
        print(f"[SIM] Setup {setup_name} — {labels[setup_name]}")
        state = run_simulation(setup_name, trading_days, regimes, ind_cache, ohlc_cache)
        metrics[setup_name] = compute_metrics(state)
        m = metrics[setup_name]
        print(
            f"       Concluído. Lucro: ${m['net_profit']:>+,.2f}  |  "
            f"WR: {m['win_rate_pct']:.1f}%  |  "
            f"MaxDD: {m['max_dd_pct']:.2f}%  |  "
            f"Trades: {m['total_trades']}\n"
        )

    # 4. Relatório final
    print(format_report(metrics))


if __name__ == "__main__":
    main()
