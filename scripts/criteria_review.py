"""
Criteria Review — "Romaria de fim de semana"

Corre aos sábados após o auditor semanal. Analisa correlações entre critérios
de entrada (RSI, volume, hora, regime) e resultados reais dos trades fechados.
Nunca escreve em config_risco.json — apenas gera data/criteria_insights.json
e envia relatório Telegram.

Execução: PYTHONPATH=. python scripts/criteria_review.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DATA_BETA = _ROOT / "data" / "beta"
_INSIGHTS_PATH = _ROOT / "data" / "criteria_insights.json"

_MAX_WINDOW_DAYS = 90   # janela de lookback para trades


# ── helpers ────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ── data loading ──────────────────────────────────────────────────────────────

def _load_closed_trades(days: int = _MAX_WINDOW_DAYS) -> list[dict]:
    """Carrega trades com resultado (fechados) dos últimos N dias."""
    raw = _load_json(_DATA_BETA / "beta_trades.json", {})
    trades: list[dict] = raw.get("trades", []) if isinstance(raw, dict) else []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for t in trades:
        if t.get("result_eur") is None:
            continue
        dt = _parse_dt(t.get("closed_at") or t.get("datetime"))
        if dt and dt >= cutoff:
            result.append(t)
    return result


def _load_entry_contexts(days: int = _MAX_WINDOW_DAYS) -> dict[str, dict]:
    """
    Lê logs/trades/*.json e constrói um índice ticker → contexto de entrada.
    Para cada ticker, guarda o contexto do evento 'phase0_complete' mais próximo
    da hora de entrada do trade (proxy mais rico que 'pre_execution').
    """
    logs_dir = _ROOT / "logs" / "trades"
    if not logs_dir.exists():
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    contexts: dict[str, dict] = {}
    for f in sorted(logs_dir.glob("*.json")):
        try:
            date_str = f.stem  # YYYY-MM-DD
            if date_str < str(cutoff):
                continue
        except Exception:
            continue
        events = _load_json(f, [])
        if not isinstance(events, list):
            continue
        for e in events:
            ctx = e.get("context") or {}
            # phase0_complete tem positions[] com técnicos ricos por ticker
            if e.get("reason") == "phase0_complete":
                for pos in ctx.get("positions", []):
                    ticker = pos.get("ticker", "")
                    if ticker and ticker not in contexts:
                        contexts[ticker] = pos
            # pre_execution: fallback para contextos por ticker/id específico
            elif e.get("reason") == "pre_execution":
                trade_id = ctx.get("id", "")
                ticker = ctx.get("ticker", "")
                if ticker and ticker not in contexts:
                    contexts[ticker] = ctx
    return contexts


# ── bucket helpers ─────────────────────────────────────────────────────────────

def _rsi_bucket(rsi: float | None) -> str | None:
    if rsi is None:
        return None
    if rsi < 30:
        return "<30"
    if rsi < 45:
        return "30-45"
    if rsi < 60:
        return "45-60"
    if rsi < 70:
        return "60-70"
    return ">=70"


def _vol_bucket(vol: float | None) -> str | None:
    if vol is None:
        return None
    if vol < 1.0:
        return "<1.0x"
    if vol < 1.5:
        return "1.0-1.5x"
    return ">=1.5x"


def _hour_bucket(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    h = dt.hour
    if h < 15:
        return "abertura (13-15h)"
    if h < 18:
        return "meio (15-18h)"
    return "fecho (18-21h)"


# ── correlation engine ─────────────────────────────────────────────────────────

def _correlation(
    trades: list[dict],
    ctx_index: dict[str, dict],
    bucket_fn,
    label: str,
) -> list[dict]:
    """
    Para cada bucket de `label`, agrega win rate e P&L médio.
    Devolve lista ordenada por win rate ascendente (piores primeiro).
    """
    buckets: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})

    for t in trades:
        ticker = t.get("ticker", "")
        result_eur = float(t.get("result_eur") or 0)

        # contexto: tentar no trade directamente, depois no índice de logs
        ctx = t.get("context") or ctx_index.get(ticker, {})

        if label == "rsi":
            val = ctx.get("rsi_14")
        elif label == "volume":
            val = ctx.get("volume_ratio_vs_avg")
        elif label == "hora":
            dt = _parse_dt(t.get("datetime"))
            val = dt  # hora de entrada
        elif label == "regime":
            val = ctx.get("regime") or t.get("regime")
        else:
            val = None

        bucket = bucket_fn(val)
        if bucket is None:
            continue

        b = buckets[bucket]
        b["pnl"] += result_eur
        if result_eur >= 0:
            b["wins"] += 1
        else:
            b["losses"] += 1

    rows = []
    for bucket, b in buckets.items():
        total = b["wins"] + b["losses"]
        if total == 0:
            continue
        wr = b["wins"] / total
        rows.append({
            "bucket": bucket,
            "trades": total,
            "wins": b["wins"],
            "losses": b["losses"],
            "win_rate": round(wr, 4),
            "win_rate_pct": round(wr * 100, 1),
            "avg_pnl_eur": round(b["pnl"] / total, 2),
            "total_pnl_eur": round(b["pnl"], 2),
        })
    return sorted(rows, key=lambda x: x["win_rate"])


def _regime_buckets(trades: list[dict], ctx_index: dict[str, dict]) -> list[dict]:
    """Regime é texto — não precisa de bucket_fn numérica."""
    return _correlation(
        trades, ctx_index,
        lambda v: v if v else None,
        "regime",
    )


# ── insight narrativo ──────────────────────────────────────────────────────────

def _finding_text(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"{label}: dados insuficientes."
    worst = rows[0]  # já ordenado por win_rate asc
    best  = rows[-1]
    if worst["win_rate"] == best["win_rate"]:
        return f"{label}: win rate uniforme ({worst['win_rate_pct']:.1f}%) em todos os buckets — sem padrão claro."
    gap = best["win_rate_pct"] - worst["win_rate_pct"]
    parts = [
        f"{label}: melhor bucket '{best['bucket']}' "
        f"({best['win_rate_pct']:.1f}% WR, {best['trades']} trades), "
        f"pior '{worst['bucket']}' ({worst['win_rate_pct']:.1f}% WR, {worst['trades']} trades). "
        f"Delta: {gap:.1f}pp.",
    ]
    if gap >= 20 and worst["trades"] >= 3:
        parts.append(f"Sinal relevante — evitar entradas no bucket '{worst['bucket']}'.")
    return " ".join(parts)


# ── entry point ───────────────────────────────────────────────────────────────

def run_criteria_review(days: int = _MAX_WINDOW_DAYS) -> dict:
    print(f"[{_ts()}] === Criteria Review START ===", flush=True)

    trades = _load_closed_trades(days)
    print(f"[criteria] {len(trades)} trade(s) fechado(s) nos últimos {days} dias", flush=True)

    ctx_index = _load_entry_contexts(days)
    print(f"[criteria] {len(ctx_index)} contexto(s) de entrada indexados", flush=True)

    if not trades:
        insights = {
            "schema_version": 1,
            "generated_at": _ts(),
            "window_days": days,
            "n_trades": 0,
            "note": "Sem trades fechados no período — análise não disponível.",
            "correlations": {},
        }
        _write_atomic(_INSIGHTS_PATH, insights)
        print("[criteria] Sem dados — ficheiro vazio escrito.", flush=True)
        return insights

    # ── correlações ──────────────────────────────────────────────────────────
    rsi_rows    = _correlation(trades, ctx_index, _rsi_bucket,   "rsi")
    vol_rows    = _correlation(trades, ctx_index, _vol_bucket,   "volume")
    hora_rows   = _correlation(trades, ctx_index, _hour_bucket,  "hora")
    regime_rows = _regime_buckets(trades, ctx_index)

    correlations = {
        "rsi_entry":    {"label": "RSI no momento da entrada",  "rows": rsi_rows,    "finding": _finding_text("RSI",    rsi_rows)},
        "volume_ratio": {"label": "Volume multiplier",           "rows": vol_rows,    "finding": _finding_text("Volume", vol_rows)},
        "hour_utc":     {"label": "Hora UTC da entrada",         "rows": hora_rows,   "finding": _finding_text("Hora",   hora_rows)},
        "regime":       {"label": "Regime no momento da entrada","rows": regime_rows, "finding": _finding_text("Regime", regime_rows)},
    }

    # ── métricas globais ──────────────────────────────────────────────────────
    wins = [t for t in trades if float(t.get("result_eur") or 0) >= 0]
    pnl  = sum(float(t.get("result_eur") or 0) for t in trades)

    insights = {
        "schema_version": 1,
        "generated_at":   _ts(),
        "window_days":    days,
        "n_trades":       len(trades),
        "summary": {
            "wins":          len(wins),
            "losses":        len(trades) - len(wins),
            "win_rate":      round(len(wins) / len(trades), 4),
            "win_rate_pct":  round(len(wins) / len(trades) * 100, 1),
            "total_pnl_eur": round(pnl, 2),
        },
        "correlations": correlations,
        "notes": [
            "Correlações baseadas em trades reais — sem backtesting.",
            "Amostras pequenas (<5 por bucket) têm baixa confiança.",
            "Nenhum ajuste é aplicado automaticamente. Apenas para análise.",
        ],
    }

    try:
        _write_atomic(_INSIGHTS_PATH, insights)
        print(f"[criteria] criteria_insights.json escrito → {_INSIGHTS_PATH}", flush=True)
    except Exception as exc:
        print(f"[criteria] ERRO ao escrever: {exc}", flush=True)

    # ── Telegram ──────────────────────────────────────────────────────────────
    try:
        _send_telegram(insights)
    except Exception as exc:
        print(f"[criteria] Telegram falhou: {exc}", flush=True)

    print(f"[{_ts()}] === Criteria Review END === | trades={len(trades)}", flush=True)
    return insights


def _send_telegram(insights: dict) -> None:
    from bot.notifier import enviar_alerta

    s    = insights.get("summary", {})
    corr = insights.get("correlations", {})
    n    = insights.get("n_trades", 0)
    days = insights.get("window_days", _MAX_WINDOW_DAYS)

    wr_pct = s.get("win_rate_pct", 0.0)
    pnl    = s.get("total_pnl_eur", 0.0)

    linhas = [
        "📐 Critério de Trades — Romaria Semanal",
        f"Janela: últimos {days} dias | {n} trade(s) analisado(s)",
        f"Win Rate global: {wr_pct:.1f}% | P&L: {pnl:+.2f}€",
        "",
        "Correlações encontradas:",
    ]

    for key, c in corr.items():
        finding = c.get("finding", "")
        if finding:
            linhas.append(f"• {finding}")

    # destaca o pior bucket de RSI se existir
    rsi_rows = corr.get("rsi_entry", {}).get("rows", [])
    if rsi_rows and rsi_rows[0].get("trades", 0) >= 3:
        worst = rsi_rows[0]
        linhas += [
            "",
            f"⚠️ Bucket mais tóxico: RSI {worst['bucket']} "
            f"({worst['win_rate_pct']:.1f}% WR em {worst['trades']} trades, "
            f"P&L total {worst['total_pnl_eur']:+.2f}€)",
        ]

    linhas += [
        "",
        "Nota: análise descritiva — sem ajustes automáticos.",
    ]

    enviar_alerta("\n".join(linhas), silencioso=False)


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    run_criteria_review()
