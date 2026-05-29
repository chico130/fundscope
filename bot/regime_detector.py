import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

import pandas as pd
import yfinance as yf

from bot.config import DATA_BETA_DIR, REGIME_CONFIG
from bot.logger import log_error

logger = logging.getLogger(__name__)

REGIME_PATH        = DATA_BETA_DIR / "regime.json"
BETA_ANALYSIS_PATH = DATA_BETA_DIR / "beta_analysis.json"

_BEAR_REGIMES = {"bear_correction", "bear_capitulation"}

Regime = Literal["bull_trending", "bull_lateral", "bear_correction", "bear_capitulation"]


def _alert_regime_fallback(regime: str, cause: str, source: str) -> None:
    """Log + Telegram alert when regime falls back to cache or hard default."""
    log_error("regime_detector_fallback", {
        "regime":  regime,
        "source":  source,  # "cache" or "default"
        "cause":   cause,
    })
    try:
        from bot.notifier import enviar_alerta
        label = {"cache": "último regime em cache", "default": "padrão conservador"}.get(source, source)
        enviar_alerta(
            f"⚠️ Regime Detector — Fallback Activado\n\n"
            f"yfinance falhou: {cause[:200]}\n"
            f"Regime usado: {regime} ({label})\n"
            f"{'⛔ Entradas bloqueadas.' if regime in _BEAR_REGIMES else '⚠️ Entradas com cautela.'}",
            silencioso=False,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Technical helpers
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(
    spy_pct_from_ema200: float,
    ema50_above_ema200: bool,
    breadth_healthy: bool,
    atr_ratio: float,
    spy_return_20d: float,
) -> Regime:
    """
    Regime classification via dual-EMA structural health test.

    Threshold:
      • SPY > EMA-200  →  Saúde Estrutural (Bull zone)
      • SPY < EMA-200  →  Modo Protecção de Capital (Bear zone)

    Within each zone, EMA-50 vs EMA-200 (golden/death cross) refines the state:
      Bull zone:
        – EMA-50 > EMA-200 + breadth healthy + low vol  →  bull_trending
        – anything else                                  →  bull_lateral
      Bear zone:
        – death cross (EMA-50 < EMA-200) OR sharp drop OR extreme vol  →  bear_capitulation
        – golden cross still intact (EMA-50 > EMA-200)                 →  bear_correction

    lateral_atr_multiplier (0.8) is used inverted: ATR ratio above 1/0.8 = 1.25
    signals elevated volatility → lateral or worse.
    """
    atr_chop_thresh = 1.0 / REGIME_CONFIG["lateral_atr_multiplier"]  # 1.25

    # ── Capital Protection (Bear) ────────────────────────────────────────────
    # Triggered as soon as SPY crosses below its EMA-200 (threshold = 0.0)
    if spy_pct_from_ema200 < REGIME_CONFIG["bear_threshold_spy_ema200_pct"]:
        # Death cross (EMA-50 < EMA-200), fast drop, or extreme volatility → capitulation
        if not ema50_above_ema200 or spy_return_20d < -0.10 or atr_ratio > 2.0:
            return "bear_capitulation"
        # Golden cross still intact → early/shallow correction, not full collapse
        return "bear_correction"

    # ── Structural Health (Bull) ──────────────────────────────────────────────
    # SPY above its EMA-200; further classified by EMA-50, breadth, and volatility
    if ema50_above_ema200 and breadth_healthy and atr_ratio <= atr_chop_thresh:
        return "bull_trending"
    return "bull_lateral"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_market_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Download 1 year of daily OHLCV for SPY and RSP.

    RSP (Invesco S&P 500 Equal Weight ETF) is the breadth proxy:
    when RSP keeps up with cap-weighted SPY, participation is broad.
    Falls back to SPY-only breadth (breadth assumed neutral) if RSP download fails.
    """
    # 300 calendar days ≈ 210 trading days — enough warmup for EMA-200
    _start = (datetime.now(timezone.utc) - timedelta(days=300)).strftime("%Y-%m-%d")
    raw = yf.download(
        ["SPY", "RSP"],
        start=_start,
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    spy = pd.DataFrame({
        "Close":  raw["Close"]["SPY"],
        "High":   raw["High"]["SPY"],
        "Low":    raw["Low"]["SPY"],
    }).dropna()

    if spy.empty:
        raise RuntimeError("SPY data returned empty from yfinance — cannot classify regime.")

    rsp_close = raw["Close"]["RSP"].dropna()
    if rsp_close.empty:
        # RSP unavailable: use SPY itself so ratio stays flat (breadth = neutral)
        logger.warning("RSP download returned empty — using SPY as fallback breadth proxy.")
        rsp_close = spy["Close"].copy()

    return spy, rsp_close


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_current_regime() -> Regime:
    """
    Classify current market regime into one of four states.

    Inputs (all via yfinance, no extra APIs):
      - SPY Close/High/Low for EMA-200, ATR, and 20-day return
      - RSP/SPY ratio trend as breadth proxy
        (RSP = equal-weight S&P 500; rising ratio = broad participation)

    bull_breadth_threshold_pct from REGIME_CONFIG represents healthy breadth.
    Here it maps to RSP/SPY ratio not losing more than 2% relative to SPY
    over the last 20 days (qualitative proxy, not the raw 60% figure).

    Falls back to the last cached regime (or bear_correction as safety default)
    if yfinance download fails completely.
    """
    try:
        spy, rsp_close = _fetch_market_data()
    except Exception as exc:
        logger.error("_fetch_market_data failed (%s) — activating safe fallback.", exc)
        cached = load_cached_regime()
        if cached:
            logger.warning("Using last cached regime: %s", cached)
            _alert_regime_fallback(regime=cached, cause=str(exc), source="cache")
            return cached
        logger.warning("No cached regime available — defaulting to bear_correction (safe mode).")
        _alert_regime_fallback(regime="bear_correction", cause=str(exc), source="default")
        return "bear_correction"

    spy_close = spy["Close"]
    spy_last  = float(spy_close.iloc[-1])

    # SPY vs its EMA-200 and EMA-50
    ema200 = float(_ema(spy_close, 200).iloc[-1])
    spy_pct_from_ema200 = (spy_last - ema200) / ema200 * 100.0

    ema50 = float(_ema(spy_close, 50).iloc[-1])
    ema50_above_ema200 = ema50 > ema200

    # ATR ratio: current ATR-14 vs its own 60-day rolling mean
    atr_series   = _atr(spy["High"], spy["Low"], spy_close)
    atr_current  = float(atr_series.iloc[-1])
    atr_mean_60d = float(atr_series.iloc[-60:].mean())
    atr_ratio    = atr_current / atr_mean_60d if atr_mean_60d > 0 else 1.0

    # SPY 20-day return
    spy_return_20d = (
        (spy_last - float(spy_close.iloc[-20])) / float(spy_close.iloc[-20])
        if len(spy_close) >= 20
        else 0.0
    )

    # Breadth proxy: RSP/SPY ratio change over 20 days
    aligned       = pd.concat([spy_close, rsp_close], axis=1, keys=["SPY", "RSP"]).dropna()
    ratio         = aligned["RSP"] / aligned["SPY"]
    ratio_now     = float(ratio.iloc[-1])
    ratio_20d_ago = float(ratio.iloc[-20]) if len(ratio) >= 20 else ratio_now
    ratio_chg_20d = (ratio_now - ratio_20d_ago) / ratio_20d_ago

    # RSP is healthy if it hasn't lost more than 2% relative to SPY over 20 days
    breadth_healthy = ratio_chg_20d >= -0.02

    regime = _classify(spy_pct_from_ema200, ema50_above_ema200, breadth_healthy, atr_ratio, spy_return_20d)

    metrics = {
        "spy_price":              round(spy_last, 2),
        "ema200":                 round(ema200, 2),
        "ema50":                  round(ema50, 2),
        "ema50_above_ema200":     ema50_above_ema200,
        "spy_pct_from_ema200":    round(spy_pct_from_ema200, 2),
        "atr_current":            round(atr_current, 4),
        "atr_ratio_vs_60d_mean":  round(atr_ratio, 4),
        "spy_return_20d":         round(spy_return_20d, 4),
        "rsp_spy_ratio_chg_20d":  round(ratio_chg_20d, 4),
        "breadth_healthy":        breadth_healthy,
    }
    _save_regime(regime, metrics)

    logger.info(
        "Regime: %s | SPY %.1f%% from EMA-200 | ATR ratio %.2f | breadth_healthy=%s",
        regime, spy_pct_from_ema200, atr_ratio, breadth_healthy,
    )
    return regime


def load_cached_regime() -> Regime | None:
    """Return the last persisted regime string without any network calls."""
    try:
        data = json.loads(REGIME_PATH.read_text(encoding="utf-8"))
        return data["regime"]
    except (OSError, KeyError, ValueError):
        return None


def load_regime_metrics() -> dict | None:
    """Return the full regime payload (regime + metrics) from the last cached run.

    Callers (e.g. phase0) use this to embed regime_details into beta_analysis.json
    without triggering a new network download.
    """
    try:
        return json.loads(REGIME_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _patch_beta_analysis_regime(regime: Regime, metrics: dict) -> None:
    """Merge regime verdict into beta_analysis.json without clobbering other fields.

    Uses read-modify-write so that phase0's position/signal data is preserved.
    Falls back silently if the file is locked or corrupt.
    """
    try:
        existing: dict = {}
        if BETA_ANALYSIS_PATH.exists():
            try:
                existing = json.loads(BETA_ANALYSIS_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                existing = {}

        existing["regime"]       = regime
        existing["regime_alert"] = regime in _BEAR_REGIMES
        existing["regime_details"] = {
            "last_updated":        datetime.now(timezone.utc).isoformat(),
            "spy_price":           metrics.get("spy_price"),
            "ema200":              metrics.get("ema200"),
            "ema50":               metrics.get("ema50"),
            "spy_pct_from_ema200": metrics.get("spy_pct_from_ema200"),
            "breadth_healthy":     metrics.get("breadth_healthy"),
            "atr_ratio_vs_60d_mean": metrics.get("atr_ratio_vs_60d_mean"),
        }

        tmp = BETA_ANALYSIS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(BETA_ANALYSIS_PATH)
    except OSError as exc:
        logger.warning("Could not patch beta_analysis.json with regime data: %s", exc)


def _save_regime(regime: Regime, metrics: dict) -> None:
    DATA_BETA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "regime":       regime,
        "metrics":      metrics,
    }
    REGIME_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _patch_beta_analysis_regime(regime, metrics)
