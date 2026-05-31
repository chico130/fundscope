"""
scripts/simulate_shadow_exits.py — Simula desfechos para sinais vetados no Shadow Ledger.

Para cada registo com shadow_result=null que já tenha passado o período de expiração:
  - Faz fetch do histórico yfinance desde o timestamp do sinal
  - Simula TP / SL / expirado bar a bar usando multiplicadores de
    data/beta/optimized_backtest_params.json (lidos sempre em runtime — nunca hardcoded)
  - Actualiza o campo shadow_result no registo
  - Escreve data/beta/shadow_ledger.json atomicamente

Limites:
  - Máximo 50 sombras por run (rate limit yfinance)
  - Só simula registos com entry_ts < agora − (EXPIRE_DAYS + 1) dias

Corre no workflow weekly-audit.yml (sábados) a seguir ao auditor.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR            = Path(__file__).parent.parent
SHADOW_LEDGER_PATH  = BASE_DIR / "data" / "beta" / "shadow_ledger.json"
PARAMS_PATH         = BASE_DIR / "data" / "beta" / "optimized_backtest_params.json"

_EXPIRE_DAYS  = 5   # dias sem bater TP/SL → resultado "expired"
_MAX_PER_RUN  = 50  # max fetches yfinance por execução


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _load_params() -> dict:
    """Carrega multiplicadores ATR de optimized_backtest_params.json."""
    try:
        data = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        return data.get("params", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _load_ledger() -> dict:
    """Carrega shadow_ledger.json. Devolve ledger vazio se ausente."""
    if not SHADOW_LEDGER_PATH.exists():
        return {"shadow_trades": [], "last_updated": None}
    try:
        data = json.loads(SHADOW_LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"shadow_trades": [], "last_updated": None}
    except (OSError, json.JSONDecodeError):
        return {"shadow_trades": [], "last_updated": None}


def _save_ledger(ledger: dict) -> None:
    """Escrita atómica de shadow_ledger.json (.tmp → replace)."""
    ledger["last_updated"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    tmp = SHADOW_LEDGER_PATH.with_suffix(".tmp")
    SHADOW_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)
    tmp.replace(SHADOW_LEDGER_PATH)


# ---------------------------------------------------------------------------
# Simulação de um registo
# ---------------------------------------------------------------------------

def _simulate_one(record: dict, params: dict) -> dict:
    """Simula desfecho TP/SL/expirado para um sinal vetado.

    Escaneia os bars dia a dia a partir do dia seguinte à entrada.
    O high do bar verifica TP, o low verifica SL (ordem conservadora:
    verifica SL primeiro quando ambos seriam atingidos no mesmo bar).
    """
    sig      = record.get("signal") or {}
    features = record.get("features") or {}

    ticker      = sig.get("ticker", "")
    entry_price = sig.get("price")
    style       = sig.get("style", "VALUE")
    entry_ts    = record.get("datetime", "")

    if not ticker or not entry_price or entry_price <= 0:
        return {"result": "no_data", "reason": "missing_price_or_ticker"}

    atr_pct = features.get("atr_pct")
    if not atr_pct or atr_pct <= 0:
        return {"result": "no_data", "reason": "missing_atr_pct"}

    # Multiplicadores dos params activos (nunca hardcodar — MEMORY_ERRORS.md)
    atr_tp_mult   = float(params.get("atr_tp_mult", 4.25))
    atr_stop_mult = float(
        params.get("atr_stop_mult_momentum", 2.0)
        if style == "MOMENTUM"
        else params.get("atr_stop_mult_value", 1.75)
    )

    tp_price = entry_price * (1.0 + atr_tp_mult   * atr_pct)
    sl_price = entry_price * (1.0 - atr_stop_mult * atr_pct)

    # Parse data de entrada
    try:
        dt_str = entry_ts.replace("Z", "+00:00") if entry_ts.endswith("Z") else entry_ts
        entry_dt   = datetime.fromisoformat(dt_str)
        entry_date = entry_dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return {"result": "no_data", "reason": "invalid_timestamp"}

    # Fetch histórico yfinance
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(start=entry_date, period="max")
        if hist.empty:
            return {"result": "no_data", "reason": "no_price_history"}
        bars = hist.iloc[1:].reset_index()   # salta o bar de entrada (look-ahead bias)
    except Exception as exc:
        return {"result": "no_data", "reason": f"yfinance_error: {str(exc)[:120]}"}

    expire_dt  = entry_dt + timedelta(days=_EXPIRE_DAYS)
    last_close: float | None = None

    for _, bar in bars.iterrows():
        # Verificar janela temporal
        bar_dt = bar.get("Date") or bar.get("Datetime")
        if bar_dt is not None:
            if hasattr(bar_dt, "to_pydatetime"):
                bar_dt = bar_dt.to_pydatetime()
            if getattr(bar_dt, "tzinfo", None) is None:
                bar_dt = bar_dt.replace(tzinfo=timezone.utc)
            if bar_dt > expire_dt:
                break

        high  = float(bar.get("High",  0) or 0)
        low   = float(bar.get("Low",   0) or 0)
        close = float(bar.get("Close", 0) or 0)

        if close > 0:
            last_close = close

        # SL verificado antes do TP (conservador — pior caso no mesmo bar)
        if low <= sl_price:
            return {
                "result":         "sl_hit",
                "entry_price":    round(entry_price, 4),
                "exit_price":     round(sl_price, 4),
                "exit_reason":    "sl_hit",
                "result_pct":     round(sl_price / entry_price - 1, 6),
                "tp_price":       round(tp_price, 4),
                "sl_price":       round(sl_price, 4),
                "would_have_won": False,
            }
        if high >= tp_price:
            return {
                "result":         "tp_hit",
                "entry_price":    round(entry_price, 4),
                "exit_price":     round(tp_price, 4),
                "exit_reason":    "tp_hit",
                "result_pct":     round(tp_price / entry_price - 1, 6),
                "tp_price":       round(tp_price, 4),
                "sl_price":       round(sl_price, 4),
                "would_have_won": True,
            }

    # Expirou sem bater em nenhuma barreira
    if last_close and last_close > 0:
        result_pct = round(last_close / entry_price - 1, 6)
        return {
            "result":         "expired",
            "entry_price":    round(entry_price, 4),
            "exit_price":     round(last_close, 4),
            "exit_reason":    "expired",
            "result_pct":     result_pct,
            "tp_price":       round(tp_price, 4),
            "sl_price":       round(sl_price, 4),
            "would_have_won": result_pct > 0,
        }

    return {"result": "no_data", "reason": "insufficient_bars"}


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def run() -> None:
    params = _load_params()
    ledger = _load_ledger()

    trades = ledger.get("shadow_trades", [])
    if not isinstance(trades, list):
        trades = []
        ledger["shadow_trades"] = trades

    # Só simula registos com entry_ts já expirado (>= EXPIRE_DAYS + 1 dias atrás)
    cutoff = datetime.now(timezone.utc) - timedelta(days=_EXPIRE_DAYS + 1)

    pending_expired: list[dict] = []
    for t in trades:
        if t.get("shadow_result") is not None:
            continue
        ts = t.get("datetime", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt < cutoff:
                pending_expired.append(t)
        except (ValueError, AttributeError):
            pass

    total_pending = sum(1 for t in trades if t.get("shadow_result") is None)

    if not pending_expired:
        print(
            f"[Shadow Exits] Nada a simular "
            f"({total_pending} pendentes, nenhum expirou ainda)."
        )
        return

    to_simulate = pending_expired[:_MAX_PER_RUN]
    print(
        f"[Shadow Exits] A simular {len(to_simulate)}/{total_pending} pendentes "
        f"(cap {_MAX_PER_RUN}/run)..."
    )

    updated = 0
    for record in to_simulate:
        ticker = (record.get("signal") or {}).get("ticker", "?")
        try:
            result = _simulate_one(record, params)
            record["shadow_result"] = result
            updated += 1
            pnl = result.get("result_pct")
            pnl_str = f"{pnl:+.2%}" if pnl is not None else "n/a"
            print(f"  {ticker:<8} {result.get('result', '?'):<12} pnl={pnl_str}")
        except Exception as exc:
            record["shadow_result"] = {"result": "error", "reason": str(exc)[:200]}
            print(f"  {ticker}: ERRO — {exc}")

    _save_ledger(ledger)
    print(
        f"[Shadow Exits] {updated} registos actualizados. "
        f"Total ledger: {len(trades)} registos."
    )


if __name__ == "__main__":
    run()
