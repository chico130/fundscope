"""
bot/stress_test_10x.py — Auditoria Anti-Overfitting: Legado vs Elite 10/10

Corre um backtest comparativo lado-a-lado em 4 janelas históricas OOS.
SEGURO: zero I/O em data/beta/, logs/, ou qualquer ficheiro de produção.
Todos os dados vêm exclusivamente do yfinance.

Correr:
    python bot/stress_test_10x.py
    python -m bot.stress_test_10x
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import pandas as pd
import yfinance as yf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─── Configuração ────────────────────────────────────────────────────────────

INITIAL_EQUITY  = 10_000.0
MAX_POSITIONS   = 5
BASE_SIZE_PCT   = 0.15   # 15 % por posição (base)
MAX_SIZE_PCT    = 0.20   # cap absoluto
ATR_RISK_BUDGET = 0.015  # Elite: max 1.5 % equity arriscado por ATR unit

TICKERS: list[str] = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL",
    "META", "AMD",  "TSLA", "JPM",  "BAC",
    "GS",   "V",    "MA",   "XOM",  "CVX",
    "UNH",  "JNJ",  "HD",   "COST", "NFLX",
    "CRM",  "ADBE", "QCOM", "TXN",  "INTC",
]

TIME_WINDOWS: list[tuple[str, str, str]] = [
    ("Bear 2022",     "2022-01-03", "2022-06-30"),
    ("Lateral 2023",  "2023-02-01", "2023-07-31"),
    ("Bull 2024",     "2024-01-02", "2024-06-28"),
    ("Recente 25-26", "2025-10-01", "2026-03-31"),
]

_BEAR_REGIMES     = {"bear_correction", "bear_capitulation"}
_REGIME_SIZE_MULT = {
    "bull_trending":     1.0,
    "bull_lateral":      0.6,
    "bear_correction":   0.0,
    "bear_capitulation": 0.0,
}

# ─── SimConfig ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SimConfig:
    name:             str
    rs_gate:          bool   # Gate RS_Bullish obrigatório (MOMENTUM)
    bonnie_vol_floor: float  # vol_ratio mínimo (0.0 = sem filtro)
    regime_aware:     bool   # bloqueia entradas em bear + sizing dinâmico
    atr_sizing:       bool   # cap de posição por volatilidade ATR


LEGACY = SimConfig(
    name="Legado",
    rs_gate=False, bonnie_vol_floor=0.0,
    regime_aware=False, atr_sizing=False,
)
ELITE = SimConfig(
    name="Elite 10/10",
    rs_gate=True, bonnie_vol_floor=1.0,
    regime_aware=True, atr_sizing=True,
)

# ─── Data types ──────────────────────────────────────────────────────────────

@dataclass
class Position:
    ticker:      str
    entry_date:  str
    entry_price: float
    qty:         float
    style:       Literal["VALUE", "MOMENTUM"]
    peak_high:   float = 0.0


@dataclass
class ClosedTrade:
    ticker:      str
    entry_date:  str
    exit_date:   str
    entry_price: float
    exit_price:  float
    result_pct:  float


@dataclass
class SimResult:
    config_name:      str
    window_name:      str
    total_return_pct: float
    max_drawdown_pct: float
    win_rate_pct:     float
    calmar_ratio:     float
    total_trades:     int
    equity_curve:     list[float] = field(default_factory=list)


# ─── Indicadores vectorizados (zero lookahead) ────────────────────────────────

def _build_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula indicadores rolling para cada barra. EWM/rolling não usa dados futuros."""
    out = df[["Open", "High", "Low", "Close", "Volume"]].copy()

    out["ema20"]  = out["Close"].ewm(span=20,  min_periods=20).mean()
    out["ema50"]  = out["Close"].ewm(span=50,  min_periods=50).mean()
    out["ema200"] = out["Close"].ewm(span=200, min_periods=200).mean()

    delta    = out["Close"].diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-9)
    out["rsi"] = 100.0 - 100.0 / (1.0 + rs)

    vol_avg20      = out["Volume"].rolling(20).mean()
    out["vol_ratio"] = out["Volume"] / vol_avg20.replace(0, 1.0)

    prev_close = out["Close"].shift(1)
    tr = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - prev_close).abs(),
        (out["Low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()

    out["ema50_above_ema200"] = out["ema50"] > out["ema200"]
    out["ema20_above_ema50"]  = out["ema20"] > out["ema50"]
    out["price_above_ema20"]  = out["Close"] > out["ema20"]

    return out


def _build_rs_bullish(ticker_ind: pd.DataFrame, spy_df: pd.DataFrame) -> pd.Series:
    """True quando RS ratio (Close/SPY) está acima da sua própria EMA-20."""
    close, spy_close = ticker_ind["Close"].align(spy_df["Close"], join="inner")
    rs = (close / spy_close.replace(0, float("nan"))).dropna()
    if len(rs) < 21:
        return pd.Series(dtype=bool)
    rs_ema20 = rs.ewm(span=20, min_periods=21).mean()
    return (rs > rs_ema20).reindex(ticker_ind.index)


def _build_regime_series(spy_df: pd.DataFrame, trade_dates: pd.DatetimeIndex) -> dict[str, str]:
    """Classifica o regime para cada dia de trading da janela."""
    spy_close = spy_df["Close"].dropna()
    ema200    = spy_close.ewm(span=200, min_periods=200).mean()
    ret20d    = spy_close.pct_change(20)

    regimes: dict[str, str] = {}
    for date in trade_dates:
        date_str = date.strftime("%Y-%m-%d")
        if date not in spy_close.index or pd.isna(ema200.get(date)):
            regimes[date_str] = "unknown"
            continue

        spy_val  = float(spy_close.loc[date])
        ema200_v = float(ema200.loc[date])
        pct      = (spy_val - ema200_v) / ema200_v * 100.0
        ret20    = float(ret20d.get(date) or 0.0)

        if pct <= -5.0:
            r = "bear_capitulation" if ret20 < -0.10 else "bear_correction"
        elif pct < 0.0:
            r = "bull_lateral"
        else:
            r = "bull_trending"

        regimes[date_str] = r

    return regimes


# ─── Lógica de sinais ─────────────────────────────────────────────────────────

def _entry_signal(
    row:     pd.Series,
    rs_val:  bool | None,
    regime:  str,
    config:  SimConfig,
) -> tuple[str, Literal["VALUE", "MOMENTUM"]]:
    """Retorna (signal, style). signal in {'BUY', 'HOLD'}."""
    if pd.isna(row.get("rsi")) or pd.isna(row.get("ema200")):
        return "HOLD", "VALUE"

    rsi       = float(row["rsi"])
    ema50_ab  = bool(row.get("ema50_above_ema200", False))
    ema20_ab  = bool(row.get("ema20_above_ema50",  False))
    p_ab_e20  = bool(row.get("price_above_ema20",  False))
    vol_ratio = float(row.get("vol_ratio") or 1.0)

    if config.regime_aware and regime in _BEAR_REGIMES:
        return "HOLD", "VALUE"

    # Regra A — VALUE sobrevendido em tendência ascendente
    if rsi <= 35 and ema50_ab and vol_ratio >= 0.8:
        return "BUY", "VALUE"

    # Regra B — VALUE neutro + surge de volume
    if 40 <= rsi <= 55 and ema50_ab and vol_ratio >= 2.0:
        return "BUY", "VALUE"

    # Regra M — MOMENTUM breakout (alinhamento total)
    if rsi >= 58 and ema50_ab and ema20_ab and p_ab_e20 and vol_ratio >= 1.5:
        if config.rs_gate and rs_val is not True:
            return "HOLD", "MOMENTUM"
        if config.bonnie_vol_floor > 0 and vol_ratio < config.bonnie_vol_floor:
            return "HOLD", "MOMENTUM"
        return "BUY", "MOMENTUM"

    return "HOLD", "VALUE"


def _should_exit(row: pd.Series, pos: Position) -> bool:
    """True quando a posição deve ser fechada."""
    if pd.isna(row.get("rsi")) or pd.isna(row.get("ema50_above_ema200")):
        return False

    if pos.style == "MOMENTUM":
        atr = row.get("atr")
        if atr and not pd.isna(atr) and pos.peak_high > 0:
            trailing_stop = pos.peak_high - 2.5 * float(atr)
            return float(row["Close"]) < trailing_stop
        return False
    else:
        if float(row["rsi"]) >= 70:
            return True
        if not bool(row.get("ema50_above_ema200", True)):
            return True
        return False


# ─── Position sizing ──────────────────────────────────────────────────────────

def _position_size(equity: float, atr: float | None, last_price: float, regime: str, config: SimConfig) -> float:
    regime_mult = _REGIME_SIZE_MULT.get(regime, 1.0) if config.regime_aware else 1.0
    size_pct    = BASE_SIZE_PCT

    if config.atr_sizing and atr and last_price > 0:
        atr_pct = atr / last_price
        if atr_pct > 0:
            atr_cap  = ATR_RISK_BUDGET / atr_pct
            size_pct = min(size_pct, atr_cap)

    return equity * min(size_pct * regime_mult, MAX_SIZE_PCT)


# ─── Simulação de portfólio ───────────────────────────────────────────────────

def _pos_value(positions: dict[str, Position], indicators: dict[str, pd.DataFrame], date) -> float:
    total = 0.0
    for tkr, pos in positions.items():
        ind = indicators.get(tkr)
        if ind is None or date not in ind.index:
            total += pos.qty * pos.entry_price
        else:
            total += pos.qty * float(ind.loc[date, "Close"])
    return total


def simulate_window(
    tickers:    list[str],
    start_date: str,
    end_date:   str,
    config:     SimConfig,
    indicators: dict[str, pd.DataFrame],
    rs_bullish: dict[str, pd.Series],
    spy_df:     pd.DataFrame,
    regimes:    dict[str, str],
    window_name: str,
) -> SimResult:
    spy_close   = spy_df["Close"].dropna()
    trade_dates = spy_close.loc[start_date:end_date].index

    if len(trade_dates) == 0:
        return SimResult(config.name, window_name, 0.0, 0.0, 0.0, 0.0, 0)

    cash:          float                   = INITIAL_EQUITY
    positions:     dict[str, Position]     = {}
    closed_trades: list[ClosedTrade]       = []
    equity_curve:  list[float]             = []

    for date in trade_dates:
        date_str = date.strftime("%Y-%m-%d")
        regime   = regimes.get(date_str, "unknown")

        # ── Saídas ──────────────────────────────────────────────────────────
        for tkr in list(positions.keys()):
            ind = indicators.get(tkr)
            if ind is None or date not in ind.index:
                continue
            row        = ind.loc[date]
            pos        = positions[tkr]
            last_price = float(row["Close"])
            if last_price > pos.peak_high:
                pos.peak_high = last_price

            if _should_exit(row, pos):
                result_pct = (last_price - pos.entry_price) / pos.entry_price * 100.0
                closed_trades.append(ClosedTrade(
                    ticker=tkr, entry_date=pos.entry_date, exit_date=date_str,
                    entry_price=pos.entry_price, exit_price=last_price,
                    result_pct=result_pct,
                ))
                cash += pos.qty * last_price
                del positions[tkr]

        # ── Entradas ────────────────────────────────────────────────────────
        for tkr in tickers:
            if tkr in positions or len(positions) >= MAX_POSITIONS:
                continue
            ind = indicators.get(tkr)
            if ind is None or date not in ind.index:
                continue
            row = ind.loc[date]
            if pd.isna(row.get("ema200")):
                continue  # histórico insuficiente para EMA-200

            rs_series = rs_bullish.get(tkr, pd.Series(dtype=bool))
            rs_val: bool | None = None
            if date in rs_series.index and not pd.isna(rs_series.get(date)):
                rs_val = bool(rs_series.loc[date])

            signal, style = _entry_signal(row, rs_val, regime, config)
            if signal != "BUY":
                continue

            last_price = float(row["Close"])
            atr_raw    = row.get("atr")
            atr        = float(atr_raw) if atr_raw and not pd.isna(atr_raw) else None
            total_eq   = cash + _pos_value(positions, indicators, date)
            size_eur   = _position_size(total_eq, atr, last_price, regime, config)
            size_eur   = min(size_eur, cash * 0.95)

            if size_eur < 50 or cash < size_eur:
                continue

            qty  = size_eur / last_price
            cash -= size_eur
            positions[tkr] = Position(
                ticker=tkr, entry_date=date_str, entry_price=last_price,
                qty=qty, style=style, peak_high=last_price,
            )

        equity_curve.append(cash + _pos_value(positions, indicators, date))

    # Força fecho de posições abertas no último dia da janela
    last_date = trade_dates[-1]
    for tkr, pos in list(positions.items()):
        ind = indicators.get(tkr)
        if ind is None:
            continue
        avail = ind.loc[:last_date]["Close"]
        if avail.empty:
            continue
        exit_price = float(avail.iloc[-1])
        result_pct = (exit_price - pos.entry_price) / pos.entry_price * 100.0
        closed_trades.append(ClosedTrade(
            ticker=tkr, entry_date=pos.entry_date,
            exit_date=last_date.strftime("%Y-%m-%d"),
            entry_price=pos.entry_price, exit_price=exit_price,
            result_pct=result_pct,
        ))

    return _compute_metrics(config.name, window_name, equity_curve, closed_trades)


def _compute_metrics(
    config_name: str,
    window_name: str,
    equity_curve: list[float],
    closed_trades: list[ClosedTrade],
) -> SimResult:
    if not equity_curve:
        return SimResult(config_name, window_name, 0.0, 0.0, 0.0, 0.0, 0)

    final   = equity_curve[-1]
    total_r = (final - INITIAL_EQUITY) / INITIAL_EQUITY * 100.0

    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak * 100.0)

    wins     = sum(1 for t in closed_trades if t.result_pct > 0)
    win_rate = wins / len(closed_trades) * 100.0 if closed_trades else 0.0

    n_days  = len(equity_curve)
    ann_ret = ((final / INITIAL_EQUITY) ** (252 / max(n_days, 1)) - 1) * 100.0
    calmar  = ann_ret / max_dd if max_dd > 0.001 else (99.0 if ann_ret > 0 else 0.0)

    return SimResult(
        config_name=config_name,
        window_name=window_name,
        total_return_pct=round(total_r, 2),
        max_drawdown_pct=round(max_dd,  2),
        win_rate_pct=round(win_rate, 1),
        calmar_ratio=round(min(calmar, 99.0), 2),
        total_trades=len(closed_trades),
        equity_curve=equity_curve,
    )


# ─── Carregamento de dados ────────────────────────────────────────────────────

def load_window_data(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Download em batch com 420 dias de warmup para EMA-200."""
    warmup_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=420)).strftime("%Y-%m-%d")
    end_excl     = (datetime.strptime(end,   "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    all_tkrs     = tickers + ["SPY"]

    raw = yf.download(
        all_tkrs, start=warmup_start, end=end_excl,
        interval="1d", progress=False, auto_adjust=True,
    )

    result: dict[str, pd.DataFrame] = {}
    for tkr in all_tkrs:
        try:
            df = pd.DataFrame({
                "Open":   raw["Open"][tkr],
                "High":   raw["High"][tkr],
                "Low":    raw["Low"][tkr],
                "Close":  raw["Close"][tkr],
                "Volume": raw["Volume"][tkr],
            }).dropna(subset=["Close"])
            if not df.empty:
                result[tkr] = df
        except (KeyError, TypeError):
            pass

    ok = sum(1 for k in result if k != "SPY")
    print(f"OK ({ok}/{len(tickers)} tickers + SPY)")
    return result


# ─── Output ASCII ─────────────────────────────────────────────────────────────

def _row(a: str, b: str, c: str, d: str, e: str, f: str, g: str) -> str:
    return f"║  {a:<20} │ {b:<13} │ {c:>8} │ {d:>7} │ {e:>8} │ {f:>7} │ {g:>5} ║"


_INNER_W = len(_row("", "", "", "", "", "", "")) - 2  # largura interior


def _winner(v_elite: float, v_legacy: float, higher_is_better: bool = True) -> str:
    better = v_elite >= v_legacy if higher_is_better else v_elite <= v_legacy
    return "▲ ELITE " if better else "▼ LEGADO"


def print_results_table(all_results: list[tuple[SimResult, SimResult]]) -> None:
    H   = "═" * _INNER_W
    sep = "╠" + H + "╣"

    print()
    print(f"╔{H}╗")
    title = "FundScope Stress Test 10x — Auditoria Anti-Overfitting (OOS)"
    print(f"║{title:^{_INNER_W}}║")
    print(sep)
    print(_row("Janela", "Modo", "Retorno", "MaxDD", "WinRate", "Calmar", "Trades"))
    print(sep)

    elite_score = 0
    total_score = 0

    for legacy, elite in all_results:
        def fr(v: float) -> str: return f"{v:>+7.2f}%"
        def fd(v: float) -> str: return f"{v:>6.1f}%"
        def fw(v: float) -> str: return f"{v:>7.1f}%"
        def fc(v: float) -> str: return f"{v:>7.2f}"

        print(_row(
            legacy.window_name, legacy.config_name,
            fr(legacy.total_return_pct), fd(legacy.max_drawdown_pct),
            fw(legacy.win_rate_pct),     fc(legacy.calmar_ratio),
            str(legacy.total_trades),
        ))
        print(_row(
            "", elite.config_name,
            fr(elite.total_return_pct), fd(elite.max_drawdown_pct),
            fw(elite.win_rate_pct),     fc(elite.calmar_ratio),
            str(elite.total_trades),
        ))

        w_ret = _winner(elite.total_return_pct, legacy.total_return_pct, True)
        w_dd  = _winner(elite.max_drawdown_pct, legacy.max_drawdown_pct, False)
        w_wr  = _winner(elite.win_rate_pct,     legacy.win_rate_pct,     True)
        w_cal = _winner(elite.calmar_ratio,      legacy.calmar_ratio,     True)
        print(_row("", "▼ vs ▲", w_ret, w_dd, w_wr, w_cal, ""))
        print(sep)

        for w in [w_ret, w_dd, w_wr, w_cal]:
            total_score += 1
            if "ELITE" in w:
                elite_score += 1

    print(f"╚{H}╝")

    pct = elite_score / max(total_score, 1) * 100
    print(f"\n  Score Elite 10/10 : {elite_score}/{total_score} métricas ({pct:.0f}%)")
    if pct >= 70:
        verdict = "ROBUSTO — Elite 10/10 demonstra vantagem Out-of-Sample consistente"
    elif pct >= 50:
        verdict = "INCONCLUSIVO — vantagem parcial, revisar parametrização"
    else:
        verdict = "ATENCAO — Elite 10/10 nao demonstra vantagem clara OOS"
    print(f"  Veredicto         : {verdict}\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sep = "═" * 62
    print(f"\n{sep}")
    print("  FundScope Stress Test 10x — Anti-Overfitting Audit")
    print("  Isolado: zero I/O em data/beta/ ou logs/")
    print(f"{sep}\n")

    all_results: list[tuple[SimResult, SimResult]] = []

    for window_name, start, end in TIME_WINDOWS:
        print(f"▶  {window_name}  ({start} → {end})")
        print(f"   Downloading data...       ", end="", flush=True)

        raw_data = load_window_data(TICKERS, start, end)
        spy_df   = raw_data.pop("SPY", None)

        if spy_df is None or spy_df.empty:
            print(f"   ERRO: dados SPY em falta — janela ignorada\n")
            continue

        print(f"   Computing indicators...   ", end="", flush=True)
        indicators: dict[str, pd.DataFrame] = {}
        for tkr, df in raw_data.items():
            try:
                indicators[tkr] = _build_indicators(df)
            except Exception:
                pass
        print(f"OK ({len(indicators)} tickers)")

        print(f"   Computing RS_Bullish...   ", end="", flush=True)
        rs_bullish: dict[str, pd.Series] = {}
        for tkr, ind in indicators.items():
            try:
                rs_bullish[tkr] = _build_rs_bullish(ind, spy_df)
            except Exception:
                pass
        print("OK")

        print(f"   Computing regimes...      ", end="", flush=True)
        spy_close   = spy_df["Close"].dropna()
        trade_dates = spy_close.loc[start:end].index
        regimes     = _build_regime_series(spy_df, trade_dates)
        bear_days   = sum(1 for r in regimes.values() if r in _BEAR_REGIMES)
        print(f"OK ({bear_days}/{len(regimes)} dias em bear)")

        print(f"   Simulating Legado...      ", end="", flush=True)
        legacy = simulate_window(
            TICKERS, start, end, LEGACY, indicators, rs_bullish, spy_df, regimes, window_name
        )
        print(f"{legacy.total_trades:>3} trades | retorno {legacy.total_return_pct:>+7.2f}%")

        print(f"   Simulating Elite 10/10... ", end="", flush=True)
        elite = simulate_window(
            TICKERS, start, end, ELITE, indicators, rs_bullish, spy_df, regimes, window_name
        )
        print(f"{elite.total_trades:>3} trades | retorno {elite.total_return_pct:>+7.2f}%\n")

        all_results.append((legacy, elite))

    if all_results:
        print_results_table(all_results)
    else:
        print("Sem resultados — verifica a ligação à internet e os tickers.")


if __name__ == "__main__":
    main()
