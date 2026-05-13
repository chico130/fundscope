"""
Learner module — Fase 2.

Reads trade logs to surface error patterns, compute performance stats,
and propose parameter adjustments. The bot never adjusts itself silently:
every suggestion is logged and requires explicit human approval before
being applied to config.py.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from .config import DATA_BETA_DIR, LOGS_TRADES_DIR, RISK_CONFIG
from .logger import log_decision, log_error


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyse_recent_trades(days: int = 7) -> dict:
    """Returns performance statistics for closed trades in the last `days` days."""
    trades = _load_trades(days)
    closed = [t for t in trades if t.get("result_eur") is not None]

    if not closed:
        return {"period_days": days, "n_closed": 0, "note": "Sem trades fechados no período."}

    wins   = [t for t in closed if t["result_eur"] >= 0]
    losses = [t for t in closed if t["result_eur"] <  0]
    total_pnl = sum(t["result_eur"] for t in closed)
    avg_win   = sum(t["result_eur"] for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum(t["result_eur"] for t in losses) / len(losses) if losses else 0.0
    win_rate  = len(wins) / len(closed) * 100

    best  = max(closed, key=lambda t: t["result_eur"])
    worst = min(closed, key=lambda t: t["result_eur"])

    by_ticker: dict[str, list[float]] = defaultdict(list)
    for t in closed:
        by_ticker[t.get("ticker", "?")].append(t["result_eur"])

    ticker_stats = {
        ticker: {
            "n_trades":     len(rs),
            "total_pnl":    round(sum(rs), 2),
            "win_rate_pct": round(sum(1 for r in rs if r >= 0) / len(rs) * 100, 1),
        }
        for ticker, rs in by_ticker.items()
    }

    return {
        "period_days":   days,
        "n_closed":      len(closed),
        "n_wins":        len(wins),
        "n_losses":      len(losses),
        "win_rate_pct":  round(win_rate, 1),
        "total_pnl_eur": round(total_pnl, 2),
        "avg_win_eur":   round(avg_win, 2),
        "avg_loss_eur":  round(avg_loss, 2),
        "best_trade":    {"ticker": best.get("ticker"), "result_eur": best["result_eur"]},
        "worst_trade":   {"ticker": worst.get("ticker"), "result_eur": worst["result_eur"]},
        "by_ticker":     ticker_stats,
    }


def detect_error_patterns() -> list[dict]:
    """Scans the last 30 days of closed trades for recurring loss patterns.

    Patterns detected:
      1. low_volume_entry    — volume_ratio < 1.0 at entry, resulted in loss
      2. high_rsi_entry      — RSI > 65 at entry, resulted in loss
      3. counter_trend_buy   — EMA-50 < EMA-200 at entry, resulted in loss
    """
    trades = _load_trades(30)
    closed = [t for t in trades if t.get("result_eur") is not None]
    patterns: list[dict] = []

    # Pattern 1: low-volume entries
    lv_losses = [
        t for t in closed
        if t.get("result_eur", 0) < 0
        and t.get("context", {}).get("volume_ratio_vs_avg", 1.0) < 1.0
    ]
    if len(lv_losses) >= 2:
        avg = sum(t["result_eur"] for t in lv_losses) / len(lv_losses)
        patterns.append({
            "pattern": "low_volume_entry",
            "description": (
                f"{len(lv_losses)} entradas com volume < 1.0× a média "
                f"resultaram em perda (média {avg:.2f}€)"
            ),
            "frequency": len(lv_losses),
            "avg_loss_eur": round(avg, 2),
            "suggestion": "Aumentar limiar mínimo de volume_ratio para 1.2 antes de entrar.",
            "affected_trades": [t.get("id") for t in lv_losses],
        })

    # Pattern 2: high-RSI entries
    hr_losses = [
        t for t in closed
        if t.get("result_eur", 0) < 0
        and (t.get("context", {}).get("rsi_14") or 0) > 65
    ]
    if len(hr_losses) >= 2:
        avg = sum(t["result_eur"] for t in hr_losses) / len(hr_losses)
        patterns.append({
            "pattern": "high_rsi_entry",
            "description": (
                f"{len(hr_losses)} entradas com RSI > 65 "
                f"resultaram em perda (média {avg:.2f}€)"
            ),
            "frequency": len(hr_losses),
            "avg_loss_eur": round(avg, 2),
            "suggestion": "Restringir entradas BUY a RSI < 60. Rever limiar de saída (actualmente 72).",
            "affected_trades": [t.get("id") for t in hr_losses],
        })

    # Pattern 3: counter-trend buys
    ct_losses = [
        t for t in closed
        if t.get("result_eur", 0) < 0
        and t.get("side") == "BUY"
        and t.get("context", {}).get("ema50_above_ema200") is False
    ]
    if len(ct_losses) >= 1:
        avg = sum(t["result_eur"] for t in ct_losses) / len(ct_losses)
        patterns.append({
            "pattern": "counter_trend_buy",
            "description": (
                f"{len(ct_losses)} compras com EMA-50 < EMA-200 "
                f"resultaram em perda (média {avg:.2f}€)"
            ),
            "frequency": len(ct_losses),
            "avg_loss_eur": round(avg, 2),
            "suggestion": "Bloquear BUY quando EMA-50 < EMA-200. Verificar se a regra está activa em strategy.py.",
            "affected_trades": [t.get("id") for t in ct_losses],
        })

    return patterns


def suggest_parameter_adjustments() -> list[dict]:
    """Proposes specific parameter changes based on recent performance data.

    Each suggestion includes the parameter, current value, proposed value,
    reason, and confidence level. None are applied automatically.
    """
    stats = analyse_recent_trades(days=14)
    patterns = detect_error_patterns()
    suggestions: list[dict] = []

    win_rate = stats.get("win_rate_pct", 50.0)
    if stats.get("n_closed", 0) >= 5 and win_rate < 45:
        suggestions.append({
            "parameter": "rsi_entry_ceiling",
            "current_value": 55,
            "proposed_value": 45,
            "reason": (
                f"Win rate de {win_rate:.1f}% abaixo do limiar aceitável (45%). "
                "Entradas mais conservadoras (RSI < 45) devem melhorar a selectividade."
            ),
            "confidence": "média",
        })

    avg_win  = stats.get("avg_win_eur", 0.0)
    avg_loss = abs(stats.get("avg_loss_eur", 0.0))
    if avg_win > 0 and avg_loss > avg_win * 1.5:
        suggestions.append({
            "parameter": "stop_loss_pct",
            "current_value": RISK_CONFIG["stop_loss_pct"],
            "proposed_value": round(max(2.0, RISK_CONFIG["stop_loss_pct"] - 1.0), 1),
            "reason": (
                f"Perda média ({avg_loss:.2f}€) é {avg_loss/avg_win:.1f}× o ganho médio "
                f"({avg_win:.2f}€). Reduzir stop loss para melhorar o rácio risco/recompensa."
            ),
            "confidence": "alta",
        })

    for p in patterns:
        if p["pattern"] == "low_volume_entry":
            suggestions.append({
                "parameter": "min_volume_ratio_entry",
                "current_value": 1.0,
                "proposed_value": 1.2,
                "reason": p["description"],
                "confidence": "alta",
            })

    return suggestions


def generate_weekly_report() -> str:
    """Generates and saves a human-readable weekly performance report.

    Saves to data/beta/beta_weekly_report.txt and logs a decision record.
    Returns the report as a string.
    """
    stats       = analyse_recent_trades(days=7)
    patterns    = detect_error_patterns()
    adjustments = suggest_parameter_adjustments()

    lines = [
        "=" * 60,
        "FundScope Bot — Relatório Semanal",
        f"Período: últimos 7 dias",
        "=" * 60,
        "",
    ]

    if stats.get("n_closed", 0) == 0:
        lines += ["Sem trades fechados neste período.", ""]
    else:
        lines += [
            "PERFORMANCE",
            f"  Trades fechados : {stats['n_closed']}",
            f"  Vitórias        : {stats['n_wins']} ({stats['win_rate_pct']}%)",
            f"  Derrotas        : {stats['n_losses']}",
            f"  P&L total       : {stats['total_pnl_eur']:+.2f}€",
            f"  Ganho médio     : +{stats['avg_win_eur']:.2f}€",
            f"  Perda média     : {stats['avg_loss_eur']:.2f}€",
            f"  Melhor trade    : {stats['best_trade']['ticker']} "
            f"{stats['best_trade']['result_eur']:+.2f}€",
            f"  Pior trade      : {stats['worst_trade']['ticker']} "
            f"{stats['worst_trade']['result_eur']:+.2f}€",
            "",
        ]
        if stats.get("by_ticker"):
            lines.append("POR TICKER")
            for tk, ts in stats["by_ticker"].items():
                lines.append(
                    f"  {tk:<8} {ts['n_trades']} trades · "
                    f"win {ts['win_rate_pct']}% · "
                    f"P&L {ts['total_pnl']:+.2f}€"
                )
            lines.append("")

    if patterns:
        lines.append("PADRÕES DE ERRO DETECTADOS")
        for p in patterns:
            lines.append(f"  ⚠ {p['description']}")
            lines.append(f"    → {p['suggestion']}")
        lines.append("")
    else:
        lines += ["PADRÕES DE ERRO: Nenhum padrão significativo detectado.", ""]

    if adjustments:
        lines.append("AJUSTES SUGERIDOS (requerem aprovação manual em config.py)")
        for a in adjustments:
            lines.append(f"  {a['parameter']}: {a['current_value']} → {a['proposed_value']}")
            lines.append(f"    Razão      : {a['reason']}")
            lines.append(f"    Confiança  : {a['confidence']}")
        lines.append("")
    else:
        lines += ["AJUSTES: Nenhum ajuste sugerido neste ciclo.", ""]

    lines += [
        "Nota: Todos os ajustes requerem aprovação explícita antes de serem aplicados.",
        "=" * 60,
    ]

    report = "\n".join(lines)
    _save_weekly_report(report)
    log_decision("learner_weekly_report", "generated", {
        "n_patterns": len(patterns),
        "n_suggestions": len(adjustments),
        "win_rate_pct": stats.get("win_rate_pct"),
    })
    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_trades(days: int) -> list[dict]:
    """Reads trade records from daily log files for the last `days` days."""
    all_trades: list[dict] = []
    today = date.today()
    for i in range(days):
        path = LOGS_TRADES_DIR / f"{(today - timedelta(days=i)).isoformat()}.json"
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
            if isinstance(records, list):
                all_trades.extend(r for r in records if r.get("ticker") and r.get("side"))
        except (json.JSONDecodeError, OSError):
            pass
    return all_trades


def _save_weekly_report(text: str) -> None:
    path = DATA_BETA_DIR / "beta_weekly_report.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        log_error("weekly_report_save_error", {"error": str(exc)})
