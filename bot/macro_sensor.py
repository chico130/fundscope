"""
Macro Sensor — VIX + SPY SMA-200 + market ATR.

Devolve macro_context a cada ciclo com cache de 15 min em data/macro_cache.json.
Se yfinance falhar: usa último cache + alerta Telegram (máximo 1×/hora).
Fail-open por design: falha de dados nunca bloqueia o ciclo.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from .config import CONFIG_RISCO_PATH, DATA_BETA_DIR
from .logger import log_error

_MACRO_CACHE_PATH: Path = DATA_BETA_DIR.parent / "macro_cache.json"
_CACHE_TTL_SECONDS: int = 900  # 15 minutos

_THRESHOLD_DEFAULTS: dict = {
    "vix_kill_switch_threshold": 35.0,
    "vix_total_kill_threshold":  45.0,
    "vix_caution_threshold":     20.0,
    "cash_is_king_multiplier":   0.25,
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_fresh_cache() -> dict | None:
    """Cache dentro do TTL. None se ausente ou expirado."""
    try:
        data = json.loads(_MACRO_CACHE_PATH.read_text(encoding="utf-8"))
        if time.time() - data.get("fetched_at_ts", 0) < _CACHE_TTL_SECONDS:
            return data
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _load_stale_cache() -> dict | None:
    """Último cache guardado, independente da idade — só para fallback."""
    try:
        return json.loads(_MACRO_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(data: dict) -> None:
    try:
        _MACRO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {**data, "fetched_at_ts": time.time(),
                   "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        tmp = _MACRO_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_MACRO_CACHE_PATH)
    except OSError as exc:
        log_error("macro_cache_write", {"error": str(exc)})


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _read_thresholds() -> dict:
    try:
        raw = json.loads(CONFIG_RISCO_PATH.read_text(encoding="utf-8"))
        return {k: float(raw.get(k, v)) for k, v in _THRESHOLD_DEFAULTS.items()}
    except (OSError, json.JSONDecodeError):
        return dict(_THRESHOLD_DEFAULTS)


# ---------------------------------------------------------------------------
# Live fetch
# ---------------------------------------------------------------------------

def _fetch_live() -> dict:
    """Download SPY + ^VIX via yfinance e calcula SMA-200 e ATR-14."""
    start = (datetime.now(timezone.utc) - pd.Timedelta(days=300)).strftime("%Y-%m-%d")
    raw = yf.download(
        ["SPY", "^VIX"],
        start=start,
        interval="1d",
        progress=False,
        auto_adjust=True,
    )

    spy_close: pd.Series = raw["Close"]["SPY"].dropna()
    spy_high:  pd.Series = raw["High"]["SPY"].dropna()
    spy_low:   pd.Series = raw["Low"]["SPY"].dropna()
    vix_close: pd.Series = raw["Close"]["^VIX"].dropna()

    if spy_close.empty:
        raise RuntimeError("SPY data returned empty from yfinance")

    spy_last = float(spy_close.iloc[-1])

    # SMA-200 — média simples dos últimos 200 dias (ou o máximo disponível ≥ 50)
    n_sma = min(len(spy_close), 200)
    if n_sma < 50:
        raise RuntimeError(f"Dados insuficientes para SMA: {n_sma} dias (mínimo 50)")
    sma200 = float(spy_close.iloc[-n_sma:].mean())
    spy_vs_sma200_pct = round((spy_last - sma200) / sma200 * 100.0, 2)

    # ATR-14 do SPY — proxy de volatilidade intradiária do mercado
    tr = pd.concat([
        spy_high - spy_low,
        (spy_high - spy_close.shift()).abs(),
        (spy_low  - spy_close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr14 = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])
    market_atr_pct = round(atr14 / spy_last * 100.0, 3) if spy_last > 0 else 0.0

    vix = round(float(vix_close.iloc[-1]), 2) if not vix_close.empty else None

    return {
        "vix":               vix,
        "spy_vs_sma200_pct": spy_vs_sma200_pct,
        "market_atr_pct":    market_atr_pct,
        "spy_last":          round(spy_last, 2),
        "sma200":            round(sma200, 2),
    }


# ---------------------------------------------------------------------------
# Verdict builder
# ---------------------------------------------------------------------------

def _build_verdict(
    data:         dict,
    cfg:          dict,
    from_cache:   bool,
    cache_age_s:  float | None,
) -> dict:
    vix            = data.get("vix")
    spy_vs_sma200  = data.get("spy_vs_sma200_pct")
    market_atr_pct = data.get("market_atr_pct", 0.0)

    kill_thr    = cfg["vix_kill_switch_threshold"]   # 35
    total_thr   = cfg["vix_total_kill_threshold"]    # 45
    caution_thr = cfg["vix_caution_threshold"]       # 20

    spy_below_sma200 = (spy_vs_sma200 is not None) and (spy_vs_sma200 < 0.0)
    total_kill       = (vix is not None) and (vix >= total_thr)
    kill_switch      = (vix is not None) and (vix >= kill_thr) and not total_kill
    cash_is_king     = kill_switch  # alias para legibilidade

    if total_kill:
        macro_mode = "total_kill"
    elif kill_switch:
        macro_mode = "cash_is_king"
    elif (vix is not None) and (vix >= caution_thr):
        macro_mode = "caution"
    else:
        macro_mode = "normal"

    return {
        "vix":                    vix,
        "spy_vs_sma200_pct":      spy_vs_sma200,
        "market_atr_pct":         market_atr_pct,
        "kill_switch":            kill_switch,
        "total_kill":             total_kill,
        "cash_is_king":           cash_is_king,
        "spy_below_sma200":       spy_below_sma200,
        "macro_mode":             macro_mode,
        "cash_is_king_multiplier": cfg["cash_is_king_multiplier"],
        "from_cache":             from_cache,
        "cache_age_s":            round(cache_age_s, 1) if cache_age_s is not None else None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_macro_context() -> dict:
    """
    Contexto macro para o CRO — VIX, SPY vs SMA-200, ATR, kill switches.

    Fluxo:
      1. Cache fresco (< 15 min)  → sem rede, retorno imediato
      2. yfinance live             → guarda em cache, retorna frescos
      3. Cache stale (qualquer idade) → alerta Telegram 1×/hora + usa antigos
      4. Sem nada                  → fail-open: macro_mode="offline", kill_switch=False

    Schema do retorno:
      vix               : float | None
      spy_vs_sma200_pct : float | None    — % acima/abaixo do SMA-200 (negativo = abaixo)
      market_atr_pct    : float           — ATR-14 SPY como % do preço
      kill_switch       : bool            — VIX ≥ vix_kill_switch_threshold (35)
      total_kill        : bool            — VIX ≥ vix_total_kill_threshold (45)
      cash_is_king      : bool            — alias kill_switch
      spy_below_sma200  : bool
      macro_mode        : "normal"|"caution"|"cash_is_king"|"total_kill"|"offline"
      from_cache        : bool
      cache_age_s       : float | None
    """
    # 1. Cache fresco
    fresh = _load_fresh_cache()
    if fresh:
        cfg = _read_thresholds()
        age = time.time() - fresh.get("fetched_at_ts", 0)
        return _build_verdict(fresh, cfg, from_cache=True, cache_age_s=age)

    # 2. Fetch live
    try:
        data = _fetch_live()
        _save_cache(data)
        cfg  = _read_thresholds()
        return _build_verdict(data, cfg, from_cache=False, cache_age_s=0.0)
    except Exception as exc:
        log_error("macro_sensor_fetch_failed", {"error": str(exc)})

    # 3. Cache stale (qualquer idade)
    stale = _load_stale_cache()
    if stale:
        cache_age = time.time() - stale.get("fetched_at_ts", 0)
        try:
            from .notifier import _already_sent_this_hour, _mark_sent_this_hour, enviar_alerta
            if not _already_sent_this_hour("macro_sensor_offline"):
                enviar_alerta(
                    "⚠️ Macro sensor offline\n\n"
                    f"yfinance indisponível — dados de cache (idade: {cache_age/60:.0f} min)\n"
                    f"VIX cached: {stale.get('vix', '?')}  ·  "
                    f"SPY vs SMA-200: {stale.get('spy_vs_sma200_pct', '?')}%",
                    silencioso=True,
                )
                _mark_sent_this_hour("macro_sensor_offline")
        except Exception:
            pass
        cfg = _read_thresholds()
        return _build_verdict(stale, cfg, from_cache=True, cache_age_s=cache_age)

    # 4. Fail-open — sem qualquer cache disponível
    try:
        from .notifier import _already_sent_this_hour, _mark_sent_this_hour, enviar_alerta
        if not _already_sent_this_hour("macro_sensor_no_cache"):
            enviar_alerta(
                "⚠️ Macro sensor offline — sem cache\n\n"
                "yfinance falhou e não existe cache local.\n"
                "Modo: neutral (fail-open). Kill switch inactivo.",
                silencioso=True,
            )
            _mark_sent_this_hour("macro_sensor_no_cache")
    except Exception:
        pass

    return {
        "vix":               None,
        "spy_vs_sma200_pct": None,
        "market_atr_pct":    None,
        "kill_switch":       False,
        "total_kill":        False,
        "cash_is_king":      False,
        "spy_below_sma200":  False,
        "macro_mode":        "offline",
        "from_cache":        False,
        "cache_age_s":       None,
    }
