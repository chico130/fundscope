"""
scripts/retrain_bonnie.py — Retreina o modelo Bonnie ML (v2).

Mudancas vs v1:
  * Labels ATR-based: 1 se atingiu entry + 1.5xATR ANTES de entry - 1.0xATR
    em janela de 20 dias uteis. Substitui o "success em 10 dias" do v1.
  * 8 features (eram 4):
    rsi_14, ema50_above_200, vol_ratio, regime,
    adx_14, price_vs_ema20, atr_percentile_60d, days_since_earnings
  * Modelo: GradientBoosting max_depth=3, n_estimators=200 (era 2/100)
  * Train 2016-2024 / Validate 2025-2026 (split temporal)
  * TimeSeriesSplit 5 folds para CV no treino
  * Per-regime thresholds optimizados via F1 no validation set
  * Persiste:
    - data/models/bonnie_model_v2.pkl
    - data/beta/bonnie_thresholds.json

Uso:
  PYTHONPATH=. python scripts/retrain_bonnie.py
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Silencia loggers do bot antes de importar
import bot.logger as _bot_logger
_bot_logger._append_to_json_list = lambda *a, **k: None  # type: ignore

from bot.config import BASE_DIR
from scripts.backtest import (
    fetch_ticker_history, precompute_indicators, WATCHLIST, MIN_BARS,
    build_earnings_calendar, days_since_last_earnings, REGIME_ENCODING
)
from bot.backtest import prime_regime_cache, _regime_cache


MODEL_OUT      = BASE_DIR / "data" / "models" / "bonnie_model_v2.pkl"
THRESHOLDS_OUT = BASE_DIR / "data" / "beta" / "bonnie_thresholds.json"
CORPUS_OUT     = BASE_DIR / "data" / "backtest" / "bonnie_observations_v2.json"

# Parametros de geracao do corpus
TRAIN_START = datetime(2018, 1, 1)
TRAIN_END   = datetime(2025, 1, 1)  # exclusive
VAL_START   = datetime(2025, 1, 1)
VAL_END     = datetime(2026, 5, 23)
LABEL_HORIZON_DAYS = 20
TP_ATR_MULT  = 1.5
SL_ATR_MULT  = 1.0

# Clyde-equivalent entry rule (matches strategy.py defaults para gerar candidatos)
RSI_CEILING       = 35.0
VOL_RATIO_MIN     = 1.2
REQUIRE_EMA_UP    = True

FEATURE_COLS = [
    "rsi_14", "ema50_above_200", "vol_ratio", "regime",
    "adx_14", "price_vs_ema20", "atr_percentile_60d", "days_since_earnings",
]


# --------------------------------------------------------------------------
# Compute ADX-14 (Wilder)
# --------------------------------------------------------------------------

def compute_adx_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                       period: int = 14) -> np.ndarray:
    n = len(closes)
    adx = np.full(n, np.nan)
    if n < period * 2 + 1:
        return adx

    tr = np.zeros(n)
    pdm = np.zeros(n)
    ndm = np.zeros(n)
    for i in range(1, n):
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm[i] = up if (up > down and up > 0) else 0.0
        ndm[i] = down if (down > up and down > 0) else 0.0
        tr[i]  = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))

    # Wilder smoothing
    atr_w = np.full(n, np.nan)
    pdm_w = np.full(n, np.nan)
    ndm_w = np.full(n, np.nan)
    atr_w[period] = float(np.sum(tr[1:period + 1]))
    pdm_w[period] = float(np.sum(pdm[1:period + 1]))
    ndm_w[period] = float(np.sum(ndm[1:period + 1]))
    for i in range(period + 1, n):
        atr_w[i] = atr_w[i - 1] - (atr_w[i - 1] / period) + tr[i]
        pdm_w[i] = pdm_w[i - 1] - (pdm_w[i - 1] / period) + pdm[i]
        ndm_w[i] = ndm_w[i - 1] - (ndm_w[i - 1] / period) + ndm[i]

    pdi = np.full(n, np.nan)
    ndi = np.full(n, np.nan)
    dx  = np.full(n, np.nan)
    for i in range(period, n):
        if atr_w[i] > 0:
            pdi[i] = 100.0 * pdm_w[i] / atr_w[i]
            ndi[i] = 100.0 * ndm_w[i] / atr_w[i]
            denom = pdi[i] + ndi[i]
            if denom > 0:
                dx[i] = 100.0 * abs(pdi[i] - ndi[i]) / denom

    # ADX = Wilder of DX
    first_dx_idx = period * 2
    if first_dx_idx < n and not np.isnan(dx[period:first_dx_idx]).all():
        seed = float(np.nanmean(dx[period:first_dx_idx]))
        adx[first_dx_idx] = seed
        for i in range(first_dx_idx + 1, n):
            if not np.isnan(dx[i]):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
            else:
                adx[i] = adx[i - 1]
    return adx


# --------------------------------------------------------------------------
# Corpus generation
# --------------------------------------------------------------------------

def compute_atr_percentile_series(atr: np.ndarray, window: int = 60) -> np.ndarray:
    """Per-day percentile rank of ATR in trailing window (0 to 1)."""
    n = len(atr)
    out = np.full(n, np.nan)
    for i in range(window, n):
        win = atr[i - window:i]
        valid = win[~np.isnan(win)]
        if len(valid) >= 10 and not np.isnan(atr[i]):
            out[i] = float(np.sum(valid <= atr[i]) / len(valid))
    return out


def label_for_observation(df: pd.DataFrame, idx: int, atr_at_entry: float,
                          horizon: int = LABEL_HORIZON_DAYS) -> Optional[int]:
    """1 se atingiu entry+1.5xATR antes de entry-1.0xATR em <=20 dias uteis."""
    if atr_at_entry <= 0 or idx + 1 >= len(df):
        return None
    entry_price = float(df["Close"].iloc[idx])
    tp_level = entry_price + TP_ATR_MULT * atr_at_entry
    sl_level = entry_price - SL_ATR_MULT * atr_at_entry
    end_idx = min(idx + 1 + horizon, len(df))
    for i in range(idx + 1, end_idx):
        lo = float(df["Low"].iloc[i])
        hi = float(df["High"].iloc[i])
        # Conservative: se ambos tocam mesmo dia, assume SL primeiro (worst-case)
        if lo <= sl_level:
            return 0
        if hi >= tp_level:
            return 1
    return 0   # 20 dias sem resolver → label 0


def generate_corpus(verbose: bool = True) -> list[dict]:
    print(f"\n[1/3] A gerar corpus com {len(WATCHLIST)} tickers, "
          f"{TRAIN_START.date()} -> {VAL_END.date()} ({TP_ATR_MULT}xATR TP / {SL_ATR_MULT}xATR SL / {LABEL_HORIZON_DAYS}d)...")

    fetch_start = TRAIN_START - timedelta(days=400)
    earnings_cal = build_earnings_calendar(TRAIN_START, VAL_END)

    # Pre-cache regimes para todo o periodo (uma so chamada SPY/RSP)
    spy_raw = fetch_ticker_history("SPY", fetch_start, VAL_END)
    if spy_raw is None:
        raise RuntimeError("SPY data unavailable")
    spy_closes = spy_raw["Close"].astype(float).to_numpy()
    spy_index  = spy_raw.index

    print(f"      A pre-carregar regimes (cobertura {TRAIN_START.date()} -> {VAL_END.date()})...")
    calendar_dates = [d.strftime("%Y-%m-%d") for d in spy_index
                      if TRAIN_START <= d.to_pydatetime() <= VAL_END]
    prime_regime_cache(calendar_dates)
    print(f"      {len(_regime_cache)} regimes em cache")

    rows: list[dict] = []
    t0 = time.time()

    for i, ticker in enumerate(WATCHLIST, 1):
        if i % 30 == 0:
            elapsed = time.time() - t0
            print(f"      ({i}/{len(WATCHLIST)})  rows={len(rows)}  {elapsed:.0f}s")
        raw = fetch_ticker_history(ticker, fetch_start, VAL_END)
        if raw is None or len(raw) < MIN_BARS:
            continue

        ind = precompute_indicators(raw, spy_closes=spy_closes, spy_index=spy_index)
        highs   = ind["High"].to_numpy()
        lows    = ind["Low"].to_numpy()
        closes  = ind["Close"].to_numpy()
        volumes = ind["Volume"].to_numpy()
        rsi     = ind["rsi_14"].to_numpy()
        ema20   = ind["ema20"].to_numpy()
        ema50   = ind["ema50"].to_numpy()
        ema200  = ind["ema200"].to_numpy()
        atr     = ind["atr_14"].to_numpy()
        vsma20  = ind["vol_sma20"].to_numpy()

        adx_series       = compute_adx_series(highs, lows, closes)
        atr_pctl_series  = compute_atr_percentile_series(atr, window=60)

        for idx in range(len(ind)):
            d = ind.index[idx]
            d_py = d.to_pydatetime()
            if d_py < TRAIN_START or d_py >= VAL_END:
                continue
            # filtros de qualidade dos indicadores
            if np.isnan(rsi[idx]) or np.isnan(ema50[idx]) or np.isnan(ema200[idx]) or np.isnan(atr[idx]):
                continue
            if vsma20[idx] <= 0 or np.isnan(vsma20[idx]):
                continue
            vol_ratio = volumes[idx] / vsma20[idx]
            ema_up = ema50[idx] > ema200[idx]

            # Clyde-equivalent rule de geracao de candidatos
            if not (rsi[idx] <= RSI_CEILING and vol_ratio >= VOL_RATIO_MIN and (ema_up or not REQUIRE_EMA_UP)):
                continue

            regime = _regime_cache.get(d.strftime("%Y-%m-%d"), "unknown")
            if regime == "unknown":
                continue

            label = label_for_observation(ind, idx, atr_at_entry=float(atr[idx]))
            if label is None:
                continue

            px_vs_ema20 = (closes[idx] - ema20[idx]) / ema20[idx] * 100 if not np.isnan(ema20[idx]) else 0.0
            d_since = days_since_last_earnings(ticker, d_py, earnings_cal)

            rows.append({
                "ticker":          ticker,
                "date":            d.strftime("%Y-%m-%d"),
                "regime":          regime,
                "features": {
                    "rsi_14":              float(rsi[idx]),
                    "ema50_above_200":     1 if ema_up else 0,
                    "vol_ratio":           float(vol_ratio),
                    "regime":              REGIME_ENCODING.get(regime, -1),
                    "adx_14":              float(adx_series[idx]) if not np.isnan(adx_series[idx]) else 20.0,
                    "price_vs_ema20":      float(px_vs_ema20),
                    "atr_percentile_60d":  float(atr_pctl_series[idx]) if not np.isnan(atr_pctl_series[idx]) else 0.5,
                    "days_since_earnings": float(d_since) if d_since is not None else 60.0,
                },
                "label": int(label),
            })

    print(f"      Total observacoes: {len(rows)}  em {time.time()-t0:.0f}s")
    return rows


# --------------------------------------------------------------------------
# Train + validate
# --------------------------------------------------------------------------

def train_and_evaluate(corpus: list[dict]) -> tuple:
    print(f"\n[2/3] A treinar GradientBoosting (max_depth=3, n_estimators=200)...")
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score

    df = pd.DataFrame([
        # Ordem importa: features inclui 'regime' (encoded int). regime_name fica separado.
        {**r["features"], "date": r["date"], "regime_name": r["regime"], "label": r["label"]}
        for r in corpus
    ])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    train_df = df[df["date"] < pd.Timestamp(VAL_START)].copy()
    val_df   = df[df["date"] >= pd.Timestamp(VAL_START)].copy()

    print(f"      Train: {len(train_df)}  Val: {len(val_df)}")
    print(f"      Label balance (train): {train_df['label'].mean():.1%} positivos")
    print(f"      Label balance (val):   {val_df['label'].mean():.1%} positivos")
    if len(val_df) < 100:
        raise RuntimeError("Validation set demasiado pequeno (<100). Aumenta TRAIN_END.")

    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]
    X_val   = val_df[FEATURE_COLS]
    y_val   = val_df["label"]

    model = GradientBoostingClassifier(
        n_estimators=200, max_depth=3,
        learning_rate=0.05, subsample=0.8,
        random_state=42,
    )

    # TimeSeriesSplit CV no treino (5 folds)
    tscv = TimeSeriesSplit(n_splits=5)
    print("      CV TimeSeriesSplit (5 folds, accuracy)...")
    cv_scores = cross_val_score(model, X_train, y_train, cv=tscv, scoring="accuracy", n_jobs=-1)
    print(f"      CV accuracy:   {cv_scores.mean():.1%} +/- {cv_scores.std():.1%}")

    # Treina no full train set
    model.fit(X_train, y_train)

    # Importances
    print("      Feature importances:")
    for feat, imp in sorted(zip(FEATURE_COLS, model.feature_importances_), key=lambda x: -x[1]):
        bar = "#" * int(imp * 40)
        print(f"        {feat:<22s} {imp:.3f}  {bar}")

    # Validacao OOS
    y_proba_val = model.predict_proba(X_val)[:, 1]
    y_pred_val  = (y_proba_val >= 0.5).astype(int)
    print(f"\n      Validation @ threshold=0.50:")
    print(f"        Accuracy:  {accuracy_score(y_val, y_pred_val):.1%}")
    print(f"        Precision: {precision_score(y_val, y_pred_val, zero_division=0):.1%}")
    print(f"        Recall:    {recall_score(y_val, y_pred_val, zero_division=0):.1%}")
    print(f"        F1:        {f1_score(y_val, y_pred_val, zero_division=0):.3f}")

    # Quebra por ano
    val_df["proba"] = y_proba_val
    val_df["pred"]  = y_pred_val
    print("\n      Validation por ano (threshold=0.50):")
    for year in sorted(val_df["date"].dt.year.unique()):
        sub = val_df[val_df["date"].dt.year == year]
        if len(sub) < 20: continue
        prec = precision_score(sub["label"], sub["pred"], zero_division=0)
        rec  = recall_score(sub["label"], sub["pred"], zero_division=0)
        f1   = f1_score(sub["label"], sub["pred"], zero_division=0)
        wr_base = sub["label"].mean()
        wr_filt = sub[sub["pred"] == 1]["label"].mean() if (sub["pred"] == 1).any() else 0
        print(f"        {year}: n={len(sub):4d}  WR_base={wr_base:.1%}  "
              f"WR_filtered={wr_filt:.1%}  P={prec:.1%}  R={rec:.1%}  F1={f1:.3f}")

    # Per-regime threshold optimizado por F1 no validation set
    print("\n      Per-regime threshold optimization (maximiza F1 no validation):")
    regime_thresholds: dict[str, float] = {}
    regime_names = {
        3: "bull_trending", 2: "bull_lateral",
        1: "bear_correction", 0: "bear_capitulation",
    }
    for reg_code, reg_name in regime_names.items():
        sub = val_df[val_df["regime_name"].map(REGIME_ENCODING) == reg_code]
        if len(sub) < 30:
            regime_thresholds[reg_name] = 0.50
            print(f"        {reg_name:<22s}: n={len(sub):3d} <30 -> default 0.50")
            continue
        # Sweep thresholds
        best_thr, best_f1 = 0.50, -1.0
        for thr in np.arange(0.30, 0.85, 0.02):
            pred = (sub["proba"] >= thr).astype(int)
            f1 = f1_score(sub["label"], pred, zero_division=0)
            if f1 > best_f1:
                best_f1, best_thr = f1, float(thr)
        pred = (sub["proba"] >= best_thr).astype(int)
        prec = precision_score(sub["label"], pred, zero_division=0)
        rec  = recall_score(sub["label"], pred, zero_division=0)
        regime_thresholds[reg_name] = round(best_thr, 2)
        n_pass = int(pred.sum())
        print(f"        {reg_name:<22s}: n={len(sub):3d}  best_thr={best_thr:.2f}  F1={best_f1:.3f}  P={prec:.1%}  R={rec:.1%}  passes={n_pass}/{len(sub)}")

    return model, regime_thresholds, val_df


# --------------------------------------------------------------------------
# Save artifacts
# --------------------------------------------------------------------------

def save_artifacts(model, regime_thresholds, corpus) -> None:
    import joblib
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    THRESHOLDS_OUT.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_OUT.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(model, MODEL_OUT)
    print(f"\n[3/3] Modelo escrito: {MODEL_OUT.relative_to(BASE_DIR)}")

    THRESHOLDS_OUT.write_text(json.dumps(regime_thresholds, indent=2), encoding="utf-8")
    print(f"      Thresholds escritos: {THRESHOLDS_OUT.relative_to(BASE_DIR)}")

    CORPUS_OUT.write_text(json.dumps(corpus, indent=2), encoding="utf-8")
    print(f"      Corpus escrito: {CORPUS_OUT.relative_to(BASE_DIR)} ({len(corpus)} obs)")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

if __name__ == "__main__":
    corpus = generate_corpus()
    if len(corpus) < 500:
        print(f"\nCorpus demasiado pequeno ({len(corpus)}) — verifica criterios de geracao.")
        sys.exit(1)

    pos_pct = sum(1 for r in corpus if r["label"] == 1) / len(corpus)
    print(f"\nLabel balance no corpus inteiro: {pos_pct:.1%} positivos ({sum(1 for r in corpus if r['label']==1)}/{len(corpus)})")

    model, thresholds, _val_df = train_and_evaluate(corpus)
    save_artifacts(model, thresholds, corpus)
    print("\nDone.")
