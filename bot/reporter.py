"""
Reporter module — Fase 2.

Reads live portfolio state from T212 demo and rewrites the four
data/beta/ JSON files consumed by the front-end.

All writes are atomic (write to .tmp, then rename) to avoid partial
reads by the site during a running update cycle.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import api_client
from .config import DATA_BETA_DIR, RISK_CONFIG, STRATEGY_VERSION
from .data_layer import read_beta_equity, read_beta_summary, read_beta_trades
from .logger import log_decision, log_error


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def update_beta_summary() -> bool:
    """Reads T212 demo account and rewrites data/beta/beta_summary.json.

    Preserves initial_capital, risk_limits, and historical trade stats
    from the previous version of the file so cumulative metrics stay correct.
    Returns True on success.
    """
    state = api_client.get_portfolio_state_demo()
    if state is None:
        log_error("reporter_no_data", {"fn": "update_beta_summary"})
        return False

    positions = state.get("positions", [])
    free_cash = (state.get("cash", {}).get("free") or 0)
    eurusd = _get_eurusd()
    total_value = round(
        sum(_position_value_eur(p, eurusd) for p in positions) + free_cash, 2
    )

    existing = read_beta_summary() or {}
    prev_summary = existing.get("summary", {})
    initial_capital = prev_summary.get("initial_capital", total_value)

    total_gain_eur = round(total_value - initial_capital, 2)
    total_gain_pct = round(total_gain_eur / initial_capital * 100, 2) if initial_capital else 0.0

    trades_data = read_beta_trades()
    trades  = trades_data.get("trades", []) if trades_data else []
    closed  = [t for t in trades if t.get("result_eur") is not None]
    wins    = [t for t in closed if t.get("result_eur", 0) >= 0]
    losses  = [t for t in closed if t.get("result_eur", 0) < 0]
    win_rate  = round(len(wins) / len(closed) * 100, 1) if closed else 0.0
    avg_win   = round(sum(t["result_eur"] for t in wins)   / len(wins),   2) if wins   else 0.0
    avg_loss  = round(sum(t["result_eur"] for t in losses) / len(losses), 2) if losses else 0.0
    best_eur  = max((t.get("result_eur") or 0 for t in closed), default=0.0)
    worst_eur = min((t.get("result_eur") or 0 for t in closed), default=0.0)
    max_dd    = _compute_max_drawdown(read_beta_equity())

    data = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "env": "demo",
        "strategy_version": STRATEGY_VERSION,
        "summary": {
            "initial_capital":  initial_capital,
            "current_value":    total_value,
            "total_gain_eur":   total_gain_eur,
            "total_gain_pct":   total_gain_pct,
            "max_drawdown_pct": max_dd,
            "n_trades":         len(trades),
            "win_rate_pct":     win_rate,
            "avg_win_eur":      avg_win,
            "avg_loss_eur":     avg_loss,
            "best_trade_eur":   best_eur,
            "worst_trade_eur":  worst_eur,
        },
        "risk_limits": existing.get("risk_limits", {
            "max_position_pct":   RISK_CONFIG["max_position_pct"],
            "max_daily_loss_pct": RISK_CONFIG["max_daily_loss_pct"],
            "max_trades_per_day": RISK_CONFIG["max_trades_per_day"],
        }),
    }

    _write_json(DATA_BETA_DIR / "beta_summary.json", data)
    log_decision("reporter", "beta_summary_written", {"current_value": total_value})
    return True


def update_beta_positions() -> bool:
    """Reads T212 demo positions and rewrites data/beta/beta_positions.json."""
    state = api_client.get_portfolio_state_demo()
    if state is None:
        log_error("reporter_no_data", {"fn": "update_beta_positions"})
        return False

    positions = state.get("positions", [])
    free_cash = (state.get("cash", {}).get("free") or 0)
    eurusd = _get_eurusd()
    total_value = sum(_position_value_eur(p, eurusd) for p in positions) + free_cash

    formatted = []
    for p in positions:
        val      = _position_value_eur(p, eurusd)
        invested = _position_invested_eur(p, eurusd)
        gain_eur = round(p.get("ppl") or 0.0, 2)  # T212 already converts ppl to EUR
        gain_pct = round(gain_eur / invested * 100, 2) if invested else 0.0
        alloc    = round(val / total_value * 100, 1) if total_value else 0.0

        formatted.append({
            "ticker":       p.get("ticker", ""),
            "display_name": p.get("ticker", "").split("_")[0],
            "quantity":     p.get("quantity", 0),
            "avg_price":    p.get("averagePrice", 0),
            "last_price":   p.get("currentPrice", 0),
            "invested":     round(invested, 2),
            "value":        round(val, 2),
            "gain_eur":     round(gain_eur, 2),
            "gain_pct":     gain_pct,
            "change_pct":   p.get("change_pct", 0),
            "allocation_pct": alloc,
        })

    _write_json(DATA_BETA_DIR / "beta_positions.json", {
        "updated": datetime.now(timezone.utc).isoformat(),
        "positions": formatted,
    })
    log_decision("reporter", "beta_positions_written", {"n": len(formatted)})
    return True


def update_beta_equity() -> bool:
    """Appends the current equity snapshot to data/beta/beta_equity.json."""
    state = api_client.get_portfolio_state_demo()
    if state is None:
        log_error("reporter_no_data", {"fn": "update_beta_equity"})
        return False

    positions  = state.get("positions", [])
    free_cash  = (state.get("cash", {}).get("free") or 0)
    eurusd = _get_eurusd()
    current_eq = round(
        sum(_position_value_eur(p, eurusd) for p in positions) + free_cash, 2
    )

    existing = read_beta_equity() or {"history": []}
    history  = existing.get("history", [])
    history.append({
        "datetime": datetime.now(timezone.utc).isoformat(),
        "equity":   current_eq,
    })

    _write_json(DATA_BETA_DIR / "beta_equity.json", {"history": history})
    log_decision("reporter", "beta_equity_appended", {"equity": current_eq})
    return True


def update_beta_trades() -> bool:
    """Checks all open trades in beta_trades.json for stop-loss / take-profit hits.

    Closes any hit trades with result, closed_at, and a postmortem note.
    Writes back only if at least one trade was closed.
    Returns True (even if no trades were touched).
    """
    state = api_client.get_portfolio_state_demo()
    if state is None:
        log_error("reporter_no_data", {"fn": "update_beta_trades"})
        return False

    trades_data = read_beta_trades()
    if not trades_data:
        return True

    price_map = {
        p.get("ticker"): p.get("currentPrice")
        for p in state.get("positions", [])
    }

    changed = False
    for trade in trades_data.get("trades", []):
        if trade.get("closed_at") is not None:
            continue

        entry = trade.get("price") or 0
        current = price_map.get(trade.get("ticker"))
        if not current or not entry:
            continue

        pct = (current - entry) / entry * 100
        hit_tp = pct >=  RISK_CONFIG["take_profit_pct"]
        hit_sl = pct <= -RISK_CONFIG["stop_loss_pct"]

        if hit_tp or hit_sl:
            label = "Take profit atingido" if hit_tp else "Stop loss activado"
            result_eur = round(trade.get("qty", 0) * (current - entry), 2)
            trade["result_eur"]  = result_eur
            trade["result_pct"]  = round(pct, 2)
            trade["closed_at"]   = datetime.now(timezone.utc).isoformat()
            trade["postmortem"]  = (
                f"{label} ao preço {current:.2f}€ "
                f"(entrada: {entry:.2f}€, variação: {pct:+.2f}%)."
            )
            changed = True
            log_decision("trade_auto_closed", label.lower().replace(" ", "_"), {
                "ticker": trade.get("ticker"),
                "result_eur": result_eur,
                "result_pct": round(pct, 2),
            })

    if changed:
        _write_json(DATA_BETA_DIR / "beta_trades.json", trades_data)

    return True


def write_account_metrics() -> bool:
    """Computes aggregate account metrics and writes data/beta/account_metrics.json."""
    state = api_client.get_portfolio_state_demo()
    if state is None:
        log_error("reporter_no_data", {"fn": "write_account_metrics"})
        return False

    positions  = state.get("positions", [])
    free_cash  = float(state.get("cash", {}).get("free") or 0.0)
    eurusd     = _get_eurusd()
    total_equity = round(
        sum(_position_value_eur(p, eurusd) for p in positions) + free_cash, 2
    )

    unrealized_pnl = round(sum(float(p.get("ppl") or 0.0) for p in positions), 2)

    trades_data = read_beta_trades()
    trades   = (trades_data or {}).get("trades", [])
    closed   = [t for t in trades if t.get("result_eur") is not None]
    realized_pnl = round(sum(t.get("result_eur", 0.0) for t in closed), 2)
    wins     = [t for t in closed if (t.get("result_eur") or 0) >= 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0.0
    max_dd   = _compute_max_drawdown(read_beta_equity())

    open_count = len(positions)
    total_val_for_alloc = total_equity or 1.0
    largest_exposure_pct = round(
        max((_position_value_eur(p, eurusd) / total_val_for_alloc * 100
             for p in positions), default=0.0), 1
    )

    days_since_last_trade: int | None = None
    all_dated = [t for t in trades if t.get("datetime")]
    if all_dated:
        last_dt_str = max(t["datetime"] for t in all_dated)
        try:
            from datetime import date
            last_date = datetime.fromisoformat(last_dt_str.replace("Z", "+00:00")).date()
            days_since_last_trade = (date.today() - last_date).days
        except Exception:
            pass

    sharpe: float | None = None
    equity_data = read_beta_equity() or {}
    history_vals = [h["equity"] for h in equity_data.get("history", [])]
    if len(history_vals) >= 30:
        import math
        daily_returns = [
            (history_vals[i] - history_vals[i - 1]) / history_vals[i - 1]
            for i in range(1, len(history_vals))
            if history_vals[i - 1]
        ]
        if len(daily_returns) >= 2:
            mean_r = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
            std_r = math.sqrt(variance)
            if std_r > 0:
                sharpe = round(mean_r / std_r * math.sqrt(252), 2)

    _write_json(DATA_BETA_DIR / "account_metrics.json", {
        "updated":               datetime.now(timezone.utc).isoformat(),
        "total_equity_eur":      total_equity,
        "free_cash_eur":         round(free_cash, 2),
        "unrealized_pnl_eur":    unrealized_pnl,
        "realized_pnl_eur":      realized_pnl,
        "win_rate_pct":          win_rate,
        "max_drawdown_pct":      max_dd,
        "open_position_count":   open_count,
        "largest_exposure_pct":  largest_exposure_pct,
        "days_since_last_trade": days_since_last_trade,
        "sharpe_ratio":          sharpe,
    })
    log_decision("reporter", "account_metrics_written", {"equity": total_equity})
    return True


def run_all() -> None:
    """Updates all four BETA JSON files in the correct order."""
    update_beta_trades()     # close any triggered trades first
    update_beta_positions()  # then refresh positions
    update_beta_equity()     # append equity snapshot
    update_beta_summary()    # recompute aggregate stats last
    write_account_metrics()  # aggregate dashboard metrics
    log_decision("reporter", "run_all_complete")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    """Atomic JSON write via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
    except OSError as exc:
        log_error("reporter_write_error", {"path": str(path), "error": str(exc)})
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _get_eurusd() -> float:
    """Fetch EUR/USD rate via yfinance. Returns 1.0 on failure."""
    try:
        import yfinance as yf
        rate = getattr(yf.Ticker("EURUSD=X").fast_info, "last_price", None)
        return float(rate) if rate else 1.0
    except Exception:
        return 1.0


def _position_value_eur(p: dict, eurusd: float) -> float:
    """Compute current position value in EUR from raw T212 API fields.

    T212 exposes currentPrice in the instrument's native currency.
    US equities (_US_) are in USD; everything else is assumed EUR.
    """
    curr_price = p.get("currentPrice") or 0.0
    qty = p.get("quantity") or 0.0
    native = curr_price * qty
    if "_US_" in (p.get("ticker") or ""):
        return native / eurusd if eurusd else native
    return native


def _position_invested_eur(p: dict, eurusd: float) -> float:
    """Compute amount invested in EUR from raw T212 API fields."""
    avg_price = p.get("averagePrice") or 0.0
    qty = p.get("quantity") or 0.0
    native = avg_price * qty
    if "_US_" in (p.get("ticker") or ""):
        return native / eurusd if eurusd else native
    return native


def _compute_max_drawdown(equity_data: dict | None) -> float:
    if not equity_data:
        return 0.0
    values = [h["equity"] for h in equity_data.get("history", [])]
    if len(values) < 2:
        return 0.0
    peak, max_dd = values[0], 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return round(max_dd, 2)
