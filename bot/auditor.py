"""
Auditor Semanal FundScope — observa padrões, sugere ajustes, nunca altera configs.

Corre via: python -m bot.auditor
Agendado: sábados 06:00 UTC via .github/workflows/weekly-audit.yml
Output:   data/audit_weekly.json  (escrita atómica)
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent
_DATA_BETA = _ROOT / "data" / "beta"
_AUDIT_PATH = _ROOT / "data" / "audit_weekly.json"

STRONG_SIGNAL_THRESHOLD = 0.70


# ── helpers ────────────────────────────────────────────────────────────────

def _ts_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _window_dates(days: int = 7) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return end - timedelta(days=days), end


# ── data loading ────────────────────────────────────────────────────────────

def _load_trade_logs(start: datetime, end: datetime) -> list[dict]:
    """Load all decision events from logs/trades/ within the window."""
    events: list[dict] = []
    logs_dir = _ROOT / "logs" / "trades"
    if not logs_dir.exists():
        return events
    current = start.date()
    while current <= end.date():
        data = _load_json(logs_dir / f"{current.isoformat()}.json", [])
        if isinstance(data, list):
            events.extend(e for e in data if isinstance(e, dict))
        current += timedelta(days=1)
    return events


def _closed_in_window(
    trades: list[dict], start: datetime, end: datetime
) -> list[dict]:
    """Filter beta_trades to trades closed within the window."""
    result = []
    for t in trades:
        closed_at = _parse_dt(t.get("closed_at"))
        if closed_at and start <= closed_at <= end and t.get("result_eur") is not None:
            result.append(t)
    return result


def _load_bonnie_thresholds() -> dict:
    for name in (
        "bonnie_thresholds_v4clean.json",
        "bonnie_thresholds_v4.json",
        "bonnie_thresholds_v3.json",
    ):
        d = _load_json(_DATA_BETA / name, {})
        if d:
            return d
    return {
        "bull_trending": 0.30,
        "bull_lateral": 0.30,
        "bear_correction": 0.30,
        "bear_capitulation": 0.30,
    }


# ── metrics ─────────────────────────────────────────────────────────────────

def _calc_sharpe(history: list[dict], start: datetime, end: datetime) -> float | None:
    """Annualised-weekly Sharpe from equity curve. Returns None if < 2 points."""
    try:
        entries = sorted(
            [
                (dt, float(e["equity"]))
                for e in history
                if (dt := _parse_dt(e.get("datetime")))
                and start <= dt <= end
                and e.get("equity") is not None
            ],
            key=lambda x: x[0],
        )
        if len(entries) < 2:
            return None
        returns = [
            (entries[i][1] - entries[i - 1][1]) / entries[i - 1][1]
            for i in range(1, len(entries))
        ]
        if len(returns) < 2:
            return None
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        if variance == 0:
            return None
        return round(mean_r / math.sqrt(variance) * math.sqrt(len(returns)), 2)
    except Exception:
        return None


def _calc_max_drawdown(history: list[dict], start: datetime, end: datetime) -> float | None:
    """Max drawdown % from equity curve within window."""
    try:
        equities = [
            float(e["equity"])
            for e in history
            if (dt := _parse_dt(e.get("datetime")))
            and start <= dt <= end
            and e.get("equity") is not None
        ]
        if len(equities) < 2:
            return None
        peak = equities[0]
        max_dd = 0.0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)
    except Exception:
        return None


# ── pattern helpers ──────────────────────────────────────────────────────────

def _reconstruct_signal_strength(trade: dict, events: list[dict]) -> float | None:
    """Match trade to nearest pre_execution event to recover signal_strength."""
    trade_dt = _parse_dt(trade.get("datetime"))
    ticker = trade.get("ticker", "")
    if not trade_dt:
        return None
    best: dict | None = None
    best_delta: float | None = None
    for e in events:
        if e.get("reason") != "pre_execution":
            continue
        ctx = e.get("context") or {}
        if ctx.get("ticker") != ticker:
            continue
        e_dt = _parse_dt(e.get("datetime"))
        if not e_dt:
            continue
        delta = abs((e_dt - trade_dt).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = e
    if best:
        ctx = best.get("context") or {}
        return ctx.get("signal_strength") or ctx.get("strength")
    return None


# ── patterns ────────────────────────────────────────────────────────────────

def _pattern_strong_signal_losers(
    closed_trades: list[dict], events: list[dict]
) -> dict:
    evidence = []
    data_gaps = 0
    for t in closed_trades:
        result_pct = t.get("result_pct")
        if result_pct is None or float(result_pct) >= 0:
            continue
        strength = _reconstruct_signal_strength(t, events)
        if strength is None:
            data_gaps += 1
            continue
        if strength > STRONG_SIGNAL_THRESHOLD:
            evidence.append(
                {
                    "ticker": t.get("ticker"),
                    "signal_strength": round(float(strength), 2),
                    "result_pct": round(float(result_pct), 2),
                    "regime": (t.get("context") or {}).get("regime"),
                    "exit_at": t.get("closed_at"),
                }
            )
    n = len(evidence)
    if n == 0 and data_gaps == 0:
        finding = "Nenhum trade com sinal forte (>70%) perdeu esta semana."
    elif n == 0:
        finding = (
            f"Sem dados de signal_strength em {data_gaps} trade(s) perdedor(es)"
            " — reconstrução de logs não encontrou correspondência."
        )
    else:
        tickers = ", ".join(e["ticker"] for e in evidence)
        finding = f"{n} trade(s) com sinal >70% perderam: {tickers}."
    return {
        "id": "strong_signal_losers",
        "title": "Sinais fortes (>70%) que perderam",
        "n_samples": n,
        "confidence": "low" if n < 5 else ("medium" if n < 10 else "high"),
        "evidence": evidence,
        "finding": finding,
        "suggestion": (
            f"Investigar contexto de regime/hora para esses {n} trade(s)." if n > 0 else None
        ),
        "data_gaps": data_gaps,
    }


def _pattern_bonnie_approved_losers(
    closed_trades: list[dict], events: list[dict]
) -> dict:
    """Trades executados (aprovados pela Bonnie) com resultado negativo + vetos da semana."""
    losers = [
        {
            "ticker": t.get("ticker"),
            "result_pct": round(float(t.get("result_pct") or 0), 2),
            "result_eur": t.get("result_eur"),
            "regime": (t.get("context") or {}).get("regime"),
            "exit_reason": t.get("reason"),
        }
        for t in closed_trades
        if (t.get("result_pct") or 0) < 0
    ]
    vetos = [
        {
            "ticker": (e.get("context") or {}).get("ticker"),
            "reason": (e.get("context") or {}).get("reason") or e.get("reason"),
            "datetime": e.get("datetime"),
        }
        for e in events
        if e.get("reason") in ("bonnie_veto", "bonnie_block", "opportunity_filtered")
    ]
    n = len(losers)
    if n == 0:
        finding = "Todos os trades Bonnie-aprovados desta semana foram positivos."
    else:
        tickers = ", ".join(l["ticker"] for l in losers)
        finding = f"{n} trade(s) aprovados pela Bonnie perderam: {tickers}."
        if vetos:
            finding += f" {len(vetos)} veto(s) registado(s) esta semana (ver bonnie_vetos_this_week)."
    return {
        "id": "bonnie_approved_losers",
        "title": "Bonnie aprovou mas resultado negativo",
        "n_samples": n,
        "confidence": "low" if n < 3 else ("medium" if n < 8 else "high"),
        "evidence": losers,
        "bonnie_vetos_this_week": vetos,
        "finding": finding,
        "suggestion": (
            f"Considerar aumentar threshold Bonnie no regime predominante"
            " se padrão se repetir ≥3 semanas."
            if n >= 3
            else None
        ),
    }


def _pattern_cro_vs_outcome(
    closed_trades: list[dict], events: list[dict]
) -> dict:
    """CRO risk_factor e regime_multiplier vs outcomes da semana."""
    cro_events = [e for e in events if e.get("reason") == "cro_interpret"]
    risk_factors = [
        float(ctx["risk_factor"])
        for e in cro_events
        if (ctx := e.get("context") or {}) and ctx.get("risk_factor") is not None
    ]
    bear_blocks = [
        e
        for e in cro_events
        if float((e.get("context") or {}).get("regime_multiplier") or 1.0) == 0.0
    ]
    avg_rf = round(sum(risk_factors) / len(risk_factors), 3) if risk_factors else None
    wins = [t for t in closed_trades if (t.get("result_pct") or 0) >= 0]
    losses = [t for t in closed_trades if (t.get("result_pct") or 0) < 0]
    if not closed_trades:
        finding = "Sem trades fechados para avaliar o CRO."
    else:
        wr_pct = len(wins) / len(closed_trades) * 100
        finding = f"CRO avg risk_factor esta semana: {avg_rf}. Win rate: {wr_pct:.0f}%."
        if bear_blocks:
            finding += f" {len(bear_blocks)} ciclo(s) bloqueados por regime_multiplier=0.0."
    return {
        "id": "cro_vs_outcome",
        "title": "CRO risk_factor vs resultado da semana",
        "n_samples": len(closed_trades),
        "confidence": "low" if len(closed_trades) < 5 else "medium",
        "avg_risk_factor": avg_rf,
        "regime_blocks": len(bear_blocks),
        "wins": len(wins),
        "losses": len(losses),
        "finding": finding,
        "suggestion": None,
    }


def _pattern_regime_accuracy(regime_data: dict, spy_data: dict) -> dict:
    """Regime declarado vs movimento real do SPY na semana."""
    declared = regime_data.get("regime", "unknown")
    try:
        spy_1s = (spy_data.get("history") or {}).get("1S", [])
        if len(spy_1s) < 2:
            raise ValueError("insufficient SPY data")
        spy_start = float(spy_1s[0]["v"])
        spy_end = float(spy_1s[-1]["v"])
        spy_weekly_pct = round((spy_end - spy_start) / spy_start * 100, 2)
        if declared in ("bull_trending", "bull_lateral"):
            aligned: bool | None = spy_weekly_pct > 0
        elif declared in ("bear_correction", "bear_capitulation"):
            aligned = spy_weekly_pct < 0
        else:
            aligned = None
        finding = (
            f"Regime '{declared}' declarado. SPY semana: {spy_weekly_pct:+.2f}%. "
            + ("Alinhado [OK]" if aligned is True else "Desalinhado [!]" if aligned is False else "N/A")
        )
    except Exception:
        spy_weekly_pct = None
        aligned = None
        finding = f"Regime '{declared}' declarado. Dados SPY insuficientes para validar."
    return {
        "id": "regime_accuracy",
        "title": "Regime detectado vs SPY real",
        "n_samples": 1,
        "confidence": "low",
        "declared_regime": declared,
        "spy_weekly_pct": spy_weekly_pct,
        "aligned": aligned,
        "finding": finding,
        "suggestion": (
            "Rever thresholds do regime_detector se desalinhamento persistir ≥3 semanas."
            if aligned is False
            else None
        ),
    }


def _pattern_hour_of_day(closed_trades: list[dict]) -> dict:
    """Hora UTC de entrada — distribuição vencedores vs perdedores."""
    buckets: dict[str, dict[str, int]] = {}
    for t in closed_trades:
        dt = _parse_dt(t.get("datetime"))
        if not dt:
            continue
        hour = str(dt.hour).zfill(2)
        b = buckets.setdefault(hour, {"wins": 0, "losses": 0})
        if (t.get("result_pct") or 0) >= 0:
            b["wins"] += 1
        else:
            b["losses"] += 1
    if not buckets:
        finding = "Sem trades com timestamp para análise por hora."
    else:
        worst = max(buckets.items(), key=lambda x: x[1]["losses"])
        if worst[1]["losses"] > 0:
            finding = (
                f"Hora com mais perdas: {worst[0]}h UTC ({worst[1]['losses']} perdas)."
            )
        else:
            finding = "Sem perdas registadas - sem padrao de hora a reportar."
    return {
        "id": "hour_of_day",
        "title": "Hora de entrada — vencedores vs perdedores",
        "n_samples": len(closed_trades),
        "confidence": "low" if len(closed_trades) < 10 else "medium",
        "buckets": buckets,
        "finding": finding,
        "suggestion": None,
    }


# ── param suggestions ────────────────────────────────────────────────────────

def _build_param_suggestions(
    patterns: list[dict], regime: str, thresholds: dict
) -> list[dict]:
    """Sugestões de parâmetros. auto_apply é sempre False."""
    suggestions: list[dict] = []

    approved_losers = next(
        (p for p in patterns if p["id"] == "bonnie_approved_losers"), None
    )
    if approved_losers and approved_losers["n_samples"] >= 3:
        current = thresholds.get(regime, 0.30)
        suggested = round(min(current + 0.05, 0.50), 2)
        suggestions.append(
            {
                "param": f"bonnie.threshold.{regime}",
                "current": current,
                "suggested": suggested,
                "direction": "increase",
                "rationale": (
                    f"{approved_losers['n_samples']} trades aprovados em '{regime}'"
                    " perderam — threshold mais alto reduziria falsos positivos."
                ),
                "confidence": "low" if approved_losers["n_samples"] < 5 else "medium",
                "based_on_pattern": "bonnie_approved_losers",
                "auto_apply": False,
            }
        )

    regime_pattern = next(
        (p for p in patterns if p["id"] == "regime_accuracy"), None
    )
    if regime_pattern and regime_pattern.get("aligned") is False:
        suggestions.append(
            {
                "param": "regime_detector.sensitivity",
                "current": "default",
                "suggested": "increase_ema200_margin",
                "direction": "tighten",
                "rationale": (
                    "Regime declarado bull mas SPY teve semana negativa"
                    " — considerar threshold mais conservador no detector."
                ),
                "confidence": "low",
                "based_on_pattern": "regime_accuracy",
                "auto_apply": False,
            }
        )

    return suggestions


# ── main audit ───────────────────────────────────────────────────────────────

def audit_week(days: int = 7) -> dict:
    """Corre a auditoria e devolve o relatório como dict. Não escreve ficheiros."""
    start, end = _window_dates(days)

    # Load sources — cada uma fail-safe
    beta_raw = _load_json(_DATA_BETA / "beta_trades.json", {})
    all_trades: list[dict] = (
        beta_raw.get("trades", []) if isinstance(beta_raw, dict) else []
    )
    regime_data = _load_json(_DATA_BETA / "regime.json", {})
    equity_raw = _load_json(_DATA_BETA / "beta_equity.json", {})
    equity_history: list[dict] = (
        equity_raw.get("history", []) if isinstance(equity_raw, dict) else []
    )
    thresholds = _load_bonnie_thresholds()
    data_json = _load_json(_ROOT / "data.json", {})
    spy_data: dict = (
        (data_json.get("stocks") or {}).get("SPY", {})
        if isinstance(data_json, dict)
        else {}
    )
    events = _load_trade_logs(start, end)
    closed = _closed_in_window(all_trades, start, end)
    regime = regime_data.get("regime", "bull_trending")

    # Metrics
    wins = [t for t in closed if (t.get("result_pct") or 0) >= 0]
    losses = [t for t in closed if (t.get("result_pct") or 0) < 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    pnl_eur = round(sum(float(t.get("result_eur") or 0) for t in closed), 2)
    sharpe = _calc_sharpe(equity_history, start, end)
    max_dd = _calc_max_drawdown(equity_history, start, end)

    patterns = [
        _pattern_strong_signal_losers(closed, events),
        _pattern_bonnie_approved_losers(closed, events),
        _pattern_cro_vs_outcome(closed, events),
        _pattern_regime_accuracy(regime_data, spy_data),
        _pattern_hour_of_day(closed),
    ]
    param_suggestions = _build_param_suggestions(patterns, regime, thresholds)

    data_gaps = [
        f"{p['id']}: {p['data_gaps']} trade(s) sem signal_strength nos logs"
        for p in patterns
        if p.get("data_gaps", 0) > 0
    ]
    trading_days = sum(
        1
        for i in range(days)
        if (start + timedelta(days=i)).weekday() < 5
    )

    return {
        "schema_version": 1,
        "generated_at": _ts_now(),
        "window": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "trading_days": trading_days,
        },
        "summary": {
            "trades_closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "pnl_eur": pnl_eur,
            "regime_dominant": regime,
            "sharpe_weekly": sharpe,
            "max_drawdown_pct": max_dd,
            "data_gaps": data_gaps,
        },
        "patterns": patterns,
        "param_suggestions": param_suggestions,
        "notes": [
            "Amostra semanal pequena — sugestões são indicativas, não accionáveis até acumular ≥20 trades.",
            "O auditor NUNCA escreve em config_risco.json — apenas sugere ajustes.",
            "param_suggestions[].auto_apply é sempre False — requer decisão manual.",
        ],
    }


def run_weekly_audit() -> None:
    """Entry point: audit → escreve JSON → envia Telegram."""
    print(f"[{_ts_now()}] === Auditor Semanal START ===", flush=True)

    try:
        report = audit_week()
    except Exception as exc:
        print(f"[auditor] ERRO crítico em audit_week(): {exc}", flush=True)
        return

    try:
        _write_atomic(_AUDIT_PATH, report)
        print(f"[auditor] Relatório escrito → {_AUDIT_PATH}", flush=True)
    except Exception as exc:
        print(f"[auditor] ERRO ao escrever audit_weekly.json: {exc}", flush=True)

    try:
        from .notifier import enviar_auditoria_semanal
        enviar_auditoria_semanal(report)
    except Exception as exc:
        print(f"[auditor] ERRO ao enviar Telegram: {exc}", flush=True)

    s = report.get("summary", {})
    print(
        f"[{_ts_now()}] === Auditor Semanal END ==="
        f" | trades={s.get('trades_closed', 0)}"
        f" | wins={s.get('wins', 0)}"
        f" | pnl={s.get('pnl_eur', 0.0):.2f}€"
        f" | sharpe={s.get('sharpe_weekly')}",
        flush=True,
    )


if __name__ == "__main__":
    run_weekly_audit()
