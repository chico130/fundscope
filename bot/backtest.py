"""
bot/backtest.py — Laboratório de Backtesting (Anti-Overfitting).

Zero look-ahead bias: todos os indicadores são calculados com dados
disponíveis estritamente até target_date. O bot não "vê" o futuro.

Observações guardadas em: data/backtest/bonnie_observations.json
NUNCA em data/beta/beta_trades.json (histórico do Clyde é imaculado).

CLI:
    python -m bot.backtest --ticker AMD --date 2026-01-10 --horizon 10
"""
from __future__ import annotations

import argparse
import json
import sys

# Força UTF-8 no terminal Windows (evita "?" em vez de ç, ã, é, etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from .config import BASE_DIR
from .data_layer import compute_rsi, compute_ema

OBSERVATIONS_PATH = BASE_DIR / "data" / "backtest" / "bonnie_observations.json"

# Limites do sinal BUY — ajustar aqui para afinar antes de activar ML
RSI_BUY_MAX   = 35.0  # RSI <= 35: zona de entrada (ajustável)
VOL_RATIO_MIN = 0.8   # vol_ratio mínimo (0 = sem filtro de volume)

MIN_HISTORY_BARS = 210  # barras necessárias para EMA-200 fiável

# Cache de regimes: evita descarregar SPY repetidamente para a mesma data
_regime_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Fetch de dados (modo individual — usado pelo CLI)
# ---------------------------------------------------------------------------

def _fetch_up_to(ticker: str, target_date: str) -> list[dict]:
    """Descarrega OHLCV até target_date (inclusive), 550 dias de janela."""
    end_dt   = datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)
    start_dt = end_dt - timedelta(days=550)
    df = yf.Ticker(ticker).history(
        start=start_dt.strftime("%Y-%m-%d"),
        end=end_dt.strftime("%Y-%m-%d"),
        interval="1d", auto_adjust=True,
    )
    return _df_to_ohlcv(df) if not df.empty else []


def _fetch_future(ticker: str, after_date: str, horizon_days: int) -> list[dict]:
    """Descarrega os horizon_days dias de trading após after_date."""
    start_dt = datetime.strptime(after_date, "%Y-%m-%d") + timedelta(days=1)
    end_dt   = start_dt + timedelta(days=horizon_days + 15)
    df = yf.Ticker(ticker).history(
        start=start_dt.strftime("%Y-%m-%d"),
        end=end_dt.strftime("%Y-%m-%d"),
        interval="1d", auto_adjust=True,
    )
    if df.empty:
        return []
    return _df_to_future(df)[:horizon_days]


# ---------------------------------------------------------------------------
# Utilitários de conversão DataFrame → lista
# ---------------------------------------------------------------------------

def _df_to_ohlcv(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "date":   dt.strftime("%Y-%m-%d"),
            "open":   round(float(row["Open"]),   4),
            "high":   round(float(row["High"]),   4),
            "low":    round(float(row["Low"]),    4),
            "close":  round(float(row["Close"]),  4),
            "volume": int(row["Volume"]),
        }
        for dt, row in df.iterrows()
    ]


def _df_to_future(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "date":  dt.strftime("%Y-%m-%d"),
            "high":  round(float(row["High"]),  4),
            "low":   round(float(row["Low"]),   4),
            "close": round(float(row["Close"]), 4),
        }
        for dt, row in df.iterrows()
    ]


def _slice_history(df: pd.DataFrame, target_date: str) -> list[dict]:
    """Fatia o DataFrame pré-carregado até target_date (inclusive)."""
    cutoff = datetime.strptime(target_date, "%Y-%m-%d").date()
    mask   = df.index.date <= cutoff
    return _df_to_ohlcv(df[mask])


def _slice_future(df: pd.DataFrame, after_date: str, horizon_days: int) -> list[dict]:
    """Fatia o DataFrame pré-carregado para os N dias após after_date."""
    cutoff = datetime.strptime(after_date, "%Y-%m-%d").date()
    mask   = df.index.date > cutoff
    return _df_to_future(df[mask].head(horizon_days))


# ---------------------------------------------------------------------------
# Regime na target_date (zero look-ahead, com cache)
# ---------------------------------------------------------------------------

def _regime_at(target_date: str) -> str:
    """
    Classifica o regime de mercado em target_date usando SPY/RSP histórico.
    Resultados em cache: o mesmo dia não descarrega SPY duas vezes.
    """
    if target_date in _regime_cache:
        return _regime_cache[target_date]

    try:
        end_dt   = datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)
        start_dt = end_dt - timedelta(days=420)

        raw = yf.download(
            ["SPY", "RSP"],
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            interval="1d", progress=False, auto_adjust=True,
        )
        spy_close = raw["Close"]["SPY"].dropna()
        if len(spy_close) < 210:
            return "unknown"

        closes  = list(spy_close.astype(float))
        spy_last = closes[-1]
        ema200   = compute_ema(closes, 200)
        if ema200 is None:
            return "unknown"

        pct_from_ema200 = (spy_last - ema200) / ema200 * 100.0
        ret_20d = (spy_last - closes[-20]) / closes[-20] if len(closes) >= 20 else 0.0

        rsp_close = raw["Close"]["RSP"].dropna()
        if len(rsp_close) >= 20:
            ratio_now = float(rsp_close.iloc[-1])  / float(spy_close.iloc[-1])
            ratio_20d = float(rsp_close.iloc[-20]) / float(spy_close.iloc[-20])
            breadth_healthy = (ratio_now - ratio_20d) / ratio_20d >= -0.02
        else:
            breadth_healthy = True

        if pct_from_ema200 <= -5.0:
            regime = "bear_capitulation" if ret_20d < -0.10 else "bear_correction"
        elif pct_from_ema200 < 0.0:
            regime = "bull_lateral"
        else:
            regime = "bull_trending" if breadth_healthy else "bull_lateral"

    except Exception:
        regime = "unknown"

    _regime_cache[target_date] = regime
    return regime


def prime_regime_cache(dates: list[str]) -> None:
    """
    Pré-carrega regimes para uma lista de datas numa única chamada ao yfinance.
    Usado pelo mass_backtest para evitar N downloads de SPY.
    """
    if not dates:
        return
    dates_sorted = sorted(dates)
    earliest = dates_sorted[0]
    latest   = dates_sorted[-1]

    try:
        end_dt   = datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
        start_dt = datetime.strptime(earliest, "%Y-%m-%d") - timedelta(days=420)

        raw = yf.download(
            ["SPY", "RSP"],
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            interval="1d", progress=False, auto_adjust=True,
        )
        spy_full = raw["Close"]["SPY"].dropna()
        rsp_full = raw["Close"]["RSP"].dropna()

        for target_date in dates_sorted:
            if target_date in _regime_cache:
                continue
            cutoff    = datetime.strptime(target_date, "%Y-%m-%d").date()
            spy_close = spy_full[spy_full.index.date <= cutoff]
            rsp_close = rsp_full[rsp_full.index.date <= cutoff]

            if len(spy_close) < 210:
                _regime_cache[target_date] = "unknown"
                continue

            closes   = list(spy_close.astype(float))
            spy_last = closes[-1]
            ema200   = compute_ema(closes, 200)
            if ema200 is None:
                _regime_cache[target_date] = "unknown"
                continue

            pct  = (spy_last - ema200) / ema200 * 100.0
            ret20 = (spy_last - closes[-20]) / closes[-20] if len(closes) >= 20 else 0.0

            if len(rsp_close) >= 20:
                rn = float(rsp_close.iloc[-1])  / float(spy_close.iloc[-1])
                r20 = float(rsp_close.iloc[-20]) / float(spy_close.iloc[-20])
                breadth = (rn - r20) / r20 >= -0.02
            else:
                breadth = True

            if pct <= -5.0:
                r = "bear_capitulation" if ret20 < -0.10 else "bear_correction"
            elif pct < 0.0:
                r = "bull_lateral"
            else:
                r = "bull_trending" if breadth else "bull_lateral"

            _regime_cache[target_date] = r

    except Exception:
        for d in dates_sorted:
            _regime_cache.setdefault(d, "unknown")


# ---------------------------------------------------------------------------
# Sinal do Clyde (replica _analyse_all do phase0.py)
# ---------------------------------------------------------------------------

def _clyde_signal(
    rsi: float | None,
    ema50_above_200: bool | None,
    vol_ratio: float | None,
    regime: str,
) -> str:
    if rsi is None:
        return "INSUFFICIENT_DATA"

    bear = {"bear_correction", "bear_capitulation"}

    if rsi <= RSI_BUY_MAX and ema50_above_200 is not False:
        if vol_ratio is not None and vol_ratio < VOL_RATIO_MIN:
            return "HOLD"
        if regime in bear:
            return "HOLD"
        return "BUY"

    if rsi >= 75:
        return "REDUCE"
    if rsi >= 65:
        return "CAUTION"
    return "HOLD"


# ---------------------------------------------------------------------------
# Avaliação do resultado (a "máquina do tempo")
# ---------------------------------------------------------------------------

def _evaluate_outcome(future_bars: list[dict], entry_price: float) -> dict:
    if not future_bars:
        return {"error": "sem_dados_futuros", "success": False}

    highs  = [b["high"]  for b in future_bars]
    lows   = [b["low"]   for b in future_bars]
    closes = [b["close"] for b in future_bars]

    return {
        "entry_price":         round(entry_price, 4),
        "max_profit_pct":      round((max(highs)  - entry_price) / entry_price * 100, 2),
        "max_drawdown_pct":    round((min(lows)   - entry_price) / entry_price * 100, 2),
        "final_return_pct":    round((closes[-1]  - entry_price) / entry_price * 100, 2),
        "success":             closes[-1] > entry_price,
        "actual_trading_days": len(future_bars),
    }


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def load_observations() -> list[dict]:
    try:
        return json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_observation(obs: dict) -> None:
    OBSERVATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_obs = load_observations()
    all_obs.append(obs)
    OBSERVATIONS_PATH.write_text(
        json.dumps(all_obs, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def save_observations_batch(new_obs: list[dict]) -> int:
    """
    Faz append em lote, evitando duplicados por (ticker, date_observed).
    Retorna o número de observações efectivamente adicionadas.
    """
    if not new_obs:
        return 0
    OBSERVATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing   = load_observations()
    seen       = {(o["ticker"], o["date_observed"]) for o in existing}
    to_add     = [o for o in new_obs if (o["ticker"], o["date_observed"]) not in seen]
    if to_add:
        OBSERVATIONS_PATH.write_text(
            json.dumps(existing + to_add, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return len(to_add)


# ---------------------------------------------------------------------------
# Função chamável pelo mass_backtest (sem prints, sem gravação individual)
# ---------------------------------------------------------------------------

def run_backtest_for_date(
    ticker: str,
    target_date: str,
    horizon_days: int = 10,
    *,
    preloaded_df: pd.DataFrame | None = None,
) -> dict | None:
    """
    Corre um backtest para uma única data.

    Se preloaded_df for fornecido (DataFrame yfinance com histórico completo),
    os dados são fatiados a partir daí — sem downloads adicionais.
    Retorna o dict de observação se sinal = BUY, ou None caso contrário.
    Não imprime nada. Não grava no ficheiro (gravação em lote pelo chamador).
    """
    if preloaded_df is not None:
        history = _slice_history(preloaded_df, target_date)
    else:
        history = _fetch_up_to(ticker, target_date)

    if len(history) < MIN_HISTORY_BARS:
        return None

    closes      = [b["close"]  for b in history]
    volumes     = [b["volume"] for b in history]
    entry_price = closes[-1]
    actual_date = history[-1]["date"]

    rsi    = compute_rsi(closes)
    ema50  = compute_ema(closes, 50)
    ema200 = compute_ema(closes, 200)
    ema50_above_200 = (ema50 > ema200) if (ema50 and ema200) else None
    avg_vol   = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol else None

    regime = _regime_at(target_date)
    signal = _clyde_signal(rsi, ema50_above_200, vol_ratio, regime)

    if signal != "BUY":
        return None

    if preloaded_df is not None:
        future_bars = _slice_future(preloaded_df, actual_date, horizon_days)
    else:
        future_bars = _fetch_future(ticker, actual_date, horizon_days)

    outcome = _evaluate_outcome(future_bars, entry_price)

    return {
        "ticker":              ticker,
        "date_observed":       actual_date,
        "features": {
            "rsi_14":          rsi,
            "ema50_above_200": ema50_above_200,
            "vol_ratio":       vol_ratio,
            "regime":          regime,
        },
        "clyde_signal":        signal,
        "target_horizon_days": horizon_days,
        "outcome":             outcome,
        "recorded_at":         datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entrada pública — modo CLI (verbose + gravação individual)
# ---------------------------------------------------------------------------

def run_backtest(ticker: str, target_date: str, horizon_days: int) -> dict | None:
    """Wrapper verbose para o CLI. Imprime detalhes e grava o resultado."""
    sep = "-" * 55
    print(f"\n{sep}")
    print(f"  FundScope Backtest — {ticker} @ {target_date}  (horizonte: {horizon_days}d)")
    print(sep)
    print("  [1/4] A descarregar histórico...")

    history = _fetch_up_to(ticker, target_date)
    if len(history) < MIN_HISTORY_BARS:
        print(f"  ERRO: Histórico insuficiente ({len(history)} barras; mínimo: {MIN_HISTORY_BARS})")
        print(sep)
        return None

    closes      = [b["close"]  for b in history]
    volumes     = [b["volume"] for b in history]
    entry_price = closes[-1]
    actual_date = history[-1]["date"]

    print("  [2/4] A calcular indicadores...")
    rsi    = compute_rsi(closes)
    ema50  = compute_ema(closes, 50)
    ema200 = compute_ema(closes, 200)
    ema50_above_200 = (ema50 > ema200) if (ema50 and ema200) else None
    avg_vol   = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol else None

    print(f"       Data efectiva:  {actual_date}  (pedida: {target_date})")
    print(f"       Preço entrada:  {entry_price}")
    print(f"       RSI-14:         {rsi}")
    print(f"       EMA-50 > 200:   {ema50_above_200}")
    print(f"       Volume ratio:   {vol_ratio}")

    print("  [3/4] A determinar regime de mercado...")
    regime = _regime_at(target_date)
    print(f"       Regime:         {regime}")

    signal = _clyde_signal(rsi, ema50_above_200, vol_ratio, regime)
    print(f"\n  Sinal Clyde: {signal}")

    if signal != "BUY":
        print(f"  Sem sinal BUY — observação não gravada no diário da Bonnie.")
        print(sep)
        return None

    print(f"  [4/4] BUY confirmado — a avaliar resultado a {horizon_days} dias...")
    future_bars = _fetch_future(ticker, actual_date, horizon_days)
    outcome     = _evaluate_outcome(future_bars, entry_price)

    observation = {
        "ticker":              ticker,
        "date_observed":       actual_date,
        "features": {
            "rsi_14":          rsi,
            "ema50_above_200": ema50_above_200,
            "vol_ratio":       vol_ratio,
            "regime":          regime,
        },
        "clyde_signal":        signal,
        "target_horizon_days": horizon_days,
        "outcome":             outcome,
        "recorded_at":         datetime.now(timezone.utc).isoformat(),
    }

    _save_observation(observation)

    print(f"\n  {'SUCESSO' if outcome.get('success') else 'INSUCESSO':>8} | Retorno final:  {outcome.get('final_return_pct', '?'):>+7.2f}%")
    print(f"           | Máx. profit:    {outcome.get('max_profit_pct', '?'):>+7.2f}%")
    print(f"           | Máx. drawdown:  {outcome.get('max_drawdown_pct', '?'):>+7.2f}%")
    print(f"           | Dias efectivos: {outcome.get('actual_trading_days', '?')}")
    print(f"\n  Observação gravada → {OBSERVATIONS_PATH.relative_to(BASE_DIR)}")
    print(sep)

    return observation


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FundScope Backtest — Laboratório da Bonnie",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemplo: python -m bot.backtest --ticker AMD --date 2026-01-10 --horizon 10",
    )
    parser.add_argument("--ticker",  required=True,        help="Ticker (ex: AMD, AAPL, NVDA)")
    parser.add_argument("--date",    required=True,        help="Data alvo YYYY-MM-DD")
    parser.add_argument("--horizon", type=int, default=10, help="Horizonte em dias de trading (default: 10)")
    args = parser.parse_args()

    run_backtest(
        ticker=args.ticker.upper(),
        target_date=args.date,
        horizon_days=args.horizon,
    )
