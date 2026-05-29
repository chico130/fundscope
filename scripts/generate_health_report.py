"""
Generate docs/project-health.md + data/beta/project_health.json after each cycle.

Reads only local JSONs and runs lightweight shell commands — no bot/ imports,
no network calls except the optional weekly Telegram summary.

Usage:
    python scripts/generate_health_report.py
    python scripts/generate_health_report.py --weekly   # force weekly Telegram summary
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_BETA = ROOT / "data" / "beta"
LOGS_ERRORS = ROOT / "logs" / "errors"
LOGS_TRADES = ROOT / "logs" / "trades"
DOCS = ROOT / "docs"
HEALTH_JSON = DATA_BETA / "project_health.json"
HEALTH_MD = DOCS / "project-health.md"
DAILY_FLAGS = ROOT / "data" / "daily_flags.json"
BOT_DIR = ROOT / "bot"
TESTS_DIR = ROOT / "tests"

WEIGHTS = {"performance": 0.40, "technical_health": 0.35, "code_quality": 0.25}

# error type → severity (anything else → "warning")
_SEV: dict[str, str] = {
    "state_repair_daily_flags": "warning",
    "state_repair_beta_trades": "warning",
    "circuit_closed": "warning",
    "circuit_open": "serious",
    "api_timeout": "serious",
    "api_rate_limit": "serious",
    "watchdog_quarantine": "critical",
    "uncaught_exception": "critical",
    "fatal": "critical",
}
_PEN = {"warning": 5, "serious": 15, "critical": 30}


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(max(lo, min(hi, round(v))))


def _in_bot_window() -> bool:
    """True if the bot's scheduled window is currently active (Mon-Fri 13:00-21:00 UTC)."""
    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and 13 <= now.hour < 21


# ── performance ───────────────────────────────────────────────────────────────

def _score_performance() -> dict:
    raw_summary = _read_json(DATA_BETA / "beta_summary.json")
    metrics = _read_json(DATA_BETA / "account_metrics.json")

    s: dict = (raw_summary.get("summary", raw_summary)
               if isinstance(raw_summary, dict) else {})

    n_trades: int = s.get("n_trades", 0) or 0
    total_gain_pct: float = s.get("total_gain_pct", 0.0) or 0.0
    avg_win: float = s.get("avg_win_eur", 0.0) or 0.0
    avg_loss: float = abs(s.get("avg_loss_eur", 0.0) or 0.0)
    wr: float = ((metrics.get("win_rate_pct") or s.get("win_rate_pct") or 50.0) / 100)
    dd: float = abs((metrics.get("max_drawdown_pct") or s.get("max_drawdown_pct") or 0.0))
    sharpe = metrics.get("sharpe_ratio")

    # sub-scores
    pnl_score = _clamp(50 + total_gain_pct * 5)  # 0% → 50, ±10% → 100/0
    drawdown_score = (100 if dd <= 5
                      else _clamp(100 - (dd - 5) / (30 - 5) * 100))
    if n_trades > 0 and (avg_win > 0 or avg_loss > 0):
        exp = wr * avg_win - (1 - wr) * avg_loss
        expectancy_score = _clamp(50 + exp / 20 * 50)
    else:
        exp = 0.0
        expectancy_score = 50
    sharpe_score = _clamp(50 + sharpe * 25) if sharpe is not None else None

    if sharpe_score is not None:
        raw = 0.35 * pnl_score + 0.25 * drawdown_score + 0.20 * expectancy_score + 0.20 * sharpe_score
    else:
        raw = 0.35 * pnl_score + 0.30 * drawdown_score + 0.35 * expectancy_score

    # confidence damping — pull toward 50 with few closed trades
    trades_data = _read_json(DATA_BETA / "beta_trades.json")
    trades: list = (trades_data.get("trades", [])
                    if isinstance(trades_data, dict) else [])
    n_closed = sum(1 for t in trades if t.get("closed_at"))
    conf = min(1.0, n_closed / 20)
    score = round(50 * (1 - conf) + raw * conf)

    notes: list[str] = []
    if conf < 1.0:
        notes.append(f"amostra insuficiente ({n_closed} trade(s) fechado(s) de 20): score amortecido")
    if sharpe_score is None:
        notes.append("Sharpe indisponível")

    return {
        "score": score,
        "confidence": round(conf, 2),
        "n_closed_trades": n_closed,
        "components": {
            "pnl_pct": round(total_gain_pct, 4),
            "pnl_score": pnl_score,
            "drawdown_pct": round(-dd, 4),
            "drawdown_score": drawdown_score,
            "expectancy_eur": round(exp, 2),
            "expectancy_score": expectancy_score,
            "win_rate_pct": round(wr * 100, 1),
            "sharpe": sharpe,
            "sharpe_score": sharpe_score,
        },
        "notes": notes,
    }


# ── technical health ──────────────────────────────────────────────────────────

def _score_technical_health() -> dict:
    status = _read_json(DATA_BETA / "status.json")
    if not isinstance(status, dict):
        status = {}

    bot_status: str = status.get("bot_status", "unknown")
    last_check_str: str = status.get("last_check", "")
    heartbeat_age_min = None
    if last_check_str:
        try:
            last_dt = datetime.fromisoformat(last_check_str.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - last_dt).total_seconds()
            heartbeat_age_min = round(age_s / 60, 1)
        except ValueError:
            pass

    idle = not _in_bot_window()  # outside Mon-Fri 13-21 UTC: stale heartbeat is expected

    if heartbeat_age_min is not None:
        if heartbeat_age_min <= 15 and bot_status == "active":
            heartbeat_score = 100
        elif heartbeat_age_min <= 45:
            heartbeat_score = _clamp(100 - (heartbeat_age_min - 15) * 3)
        elif idle:
            heartbeat_score = 75  # gap is expected outside the trading window
        else:
            heartbeat_score = 0
    else:
        heartbeat_score = 75 if idle else 0
    if bot_status == "error":
        heartbeat_score = 0

    # errors: last 7 days
    today_utc = datetime.now(timezone.utc)
    errors_7d: list[dict] = []
    for i in range(7):
        day = (today_utc - timedelta(days=i)).strftime("%Y-%m-%d")
        raw = _read_json(LOGS_ERRORS / f"{day}.json")
        if isinstance(raw, list):
            errors_7d.extend(raw)

    # circuit events from today's trade log
    today_str = today_utc.strftime("%Y-%m-%d")
    trades_log = _read_json(LOGS_TRADES / f"{today_str}.json")
    circuit_events: list[dict] = []
    if isinstance(trades_log, list):
        circuit_events = [e for e in trades_log
                          if e.get("type") in ("circuit_open", "circuit_closed")]

    penalty = 0
    n_warn = n_ser = n_crit = 0
    for e in errors_7d:
        sev = _SEV.get(e.get("type", ""), "warning")
        penalty += _PEN[sev]
        if sev == "warning":
            n_warn += 1
        elif sev == "serious":
            n_ser += 1
        else:
            n_crit += 1
    for e in circuit_events:
        if e.get("type") == "circuit_open":
            penalty += _PEN["serious"]
            n_ser += 1

    error_score = _clamp(100 - penalty)

    # open circuits: circuit_open with no subsequent circuit_closed today
    open_circuits: list[str] = []
    by_api: dict[str, str] = {}
    for e in circuit_events:
        api = (e.get("context") or {}).get("api", "unknown")
        by_api[api] = e.get("type", "")
    open_circuits = [api for api, t in by_api.items() if t == "circuit_open"]
    api_score = _clamp(100 - 25 * len(open_circuits))

    # workflow score via gh CLI
    workflow_score = None
    workflow_window = 0
    try:
        result = subprocess.run(
            ["gh", "run", "list", "-w", "run-trading-bot.yml",
             "--limit", "20", "--json", "conclusion"],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            runs: list[dict] = json.loads(result.stdout)
            conclusions = [r.get("conclusion") for r in runs if r.get("conclusion")]
            workflow_window = len(conclusions)
            if conclusions:
                workflow_score = _clamp(
                    sum(1 for c in conclusions if c == "success") / workflow_window * 100
                )
    except Exception:
        pass

    if workflow_score is not None:
        raw = (0.40 * heartbeat_score + 0.25 * error_score
               + 0.15 * api_score + 0.20 * workflow_score)
    else:
        raw = 0.48 * heartbeat_score + 0.30 * error_score + 0.22 * api_score

    notes: list[str] = []
    if n_crit:
        notes.append(f"{n_crit} erro(s) CRÍTICO(S) nos últimos 7 dias")
    if n_ser:
        notes.append(f"{n_ser} erro(s) grave(s) nos últimos 7 dias")
    if n_warn:
        notes.append(f"{n_warn} aviso(s) nos últimos 7 dias")
    if open_circuits:
        notes.append(f"Circuitos abertos: {', '.join(open_circuits)}")
    if workflow_score is None:
        notes.append("gh CLI indisponível — workflow_score N/D")
    if (not idle and (heartbeat_age_min or 0) > 45
            and bot_status == "active"):
        notes.append("status.json pode estar desactualizado — considera git pull para reflectir ciclos recentes")

    return {
        "score": round(raw),
        "components": {
            "heartbeat_age_min": heartbeat_age_min,
            "heartbeat_score": heartbeat_score,
            "bot_status": bot_status,
            "idle": idle,
            "errors_7d": len(errors_7d),
            "errors_critical": n_crit,
            "errors_serious": n_ser,
            "errors_warning": n_warn,
            "error_score": error_score,
            "open_circuits": open_circuits,
            "api_score": api_score,
            "workflow_score": workflow_score,
            "workflow_window": workflow_window,
        },
        "notes": notes,
    }


# ── code quality ──────────────────────────────────────────────────────────────

def _score_code_quality() -> dict:
    # coverage (from pytest-cov JSON written by CI or main workflow)
    coverage_pct = None
    coverage_score = None
    coverage_age_days = None
    cov_path = DATA_BETA / "coverage.json"
    if cov_path.exists():
        try:
            cov = json.loads(cov_path.read_text(encoding="utf-8"))
            pct = cov.get("totals", {}).get("percent_covered")
            if pct is not None:
                coverage_pct = round(float(pct), 1)
                coverage_score = _clamp(coverage_pct)
            age = (datetime.now(timezone.utc).timestamp() - cov_path.stat().st_mtime) / 86400
            coverage_age_days = round(age, 1)
        except Exception:
            pass

    # test presence: filename match OR import reference in any test file
    bot_modules = sorted(f.stem for f in BOT_DIR.glob("*.py") if f.stem != "__init__")
    tested_set: set[str] = set()
    # 1. classic test_<module>.py filename
    for m in bot_modules:
        if (TESTS_DIR / f"test_{m}.py").exists():
            tested_set.add(m)
    # 2. scan test files for "import bot.<module>" or "from bot.<module>"
    import_pats = {m: re.compile(rf"\b(?:import|from)\s+(?:bot\.)?{re.escape(m)}\b")
                   for m in bot_modules}
    for tf in TESTS_DIR.glob("*.py"):
        try:
            content = tf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m, pat in import_pats.items():
            if pat.search(content):
                tested_set.add(m)
    tested = sorted(tested_set)
    test_presence_pct = round(len(tested) / len(bot_modules) * 100, 1) if bot_modules else 0.0
    test_presence_score = _clamp(test_presence_pct)

    # TODO/FIXME/XXX/HACK count — pure Python (no git grep quoting issues on Windows)
    todo_pat = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    todo_count = 0
    for search_dir in (BOT_DIR, ROOT / "scripts"):
        for py_file in search_dir.glob("*.py"):
            try:
                todo_count += len(todo_pat.findall(py_file.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                pass
    todo_score = _clamp(100 - min(todo_count, 50) / 50 * 100)

    # syntax check — ast.parse every bot/*.py
    syntax_ok = True
    syntax_errors: list[str] = []
    for py_file in BOT_DIR.glob("*.py"):
        try:
            ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            syntax_ok = False
            syntax_errors.append(f"{py_file.name}:{exc.lineno}")

    # compose
    if coverage_score is not None:
        raw = (0.55 * coverage_score + 0.15 * test_presence_score
               + 0.15 * todo_score + 0.15 * (100 if syntax_ok else 0))
        mode = "coverage"
    else:
        raw = 0.55 * test_presence_score + 0.20 * todo_score + 0.25 * (100 if syntax_ok else 0)
        mode = "proxy"

    untested = sorted(set(bot_modules) - set(tested))

    notes: list[str] = []
    if coverage_score is None:
        notes.append("pytest-cov não instalado ou cobertura não medida: a usar proxy test-presence")
    elif coverage_age_days and coverage_age_days > 2:
        notes.append(f"Cobertura medida há {coverage_age_days:.0f} dias — pode estar desactualizada")
    if not syntax_ok:
        notes.append(f"ERROS DE SINTAXE: {'; '.join(syntax_errors)}")

    return {
        "score": round(raw),
        "mode": mode,
        "components": {
            "coverage_pct": coverage_pct,
            "coverage_score": coverage_score,
            "coverage_age_days": coverage_age_days,
            "tested_modules": len(tested),
            "total_modules": len(bot_modules),
            "test_presence_pct": test_presence_pct,
            "test_presence_score": test_presence_score,
            "untested_modules": untested,
            "todo_count": todo_count,
            "todo_score": todo_score,
            "syntax_ok": syntax_ok,
            "syntax_errors": syntax_errors,
        },
        "notes": notes,
    }


# ── strengths & weaknesses ────────────────────────────────────────────────────

def _build_sw(perf: dict, tech: dict, qual: dict) -> tuple[dict, dict]:
    """
    Pool all non-null sub-scores.  strengths_score = mean of top tercile.
    weaknesses_score = mean of bottom tercile (high = fewer weaknesses).
    """
    pc = perf["components"]
    tc = tech["components"]
    qc = qual["components"]

    pool: list[tuple[str, int, str]] = [
        ("P&L acumulado",         pc["pnl_score"],           "performance"),
        ("Drawdown",               pc["drawdown_score"],      "performance"),
        ("Expectancy",             pc["expectancy_score"],    "performance"),
        ("Heartbeat",              tc["heartbeat_score"],     "technical_health"),
        ("Erros (7d)",             tc["error_score"],         "technical_health"),
        ("APIs / circuit breakers",tc["api_score"],           "technical_health"),
        ("Test presence",          qc["test_presence_score"], "code_quality"),
        ("TODOs activos",          qc["todo_score"],          "code_quality"),
        ("Sintaxe",                100 if qc["syntax_ok"] else 0, "code_quality"),
    ]
    if pc["sharpe_score"] is not None:
        pool.append(("Sharpe", pc["sharpe_score"], "performance"))
    if tc["workflow_score"] is not None:
        pool.append(("Workflows CI", tc["workflow_score"], "technical_health"))
    if qc["coverage_score"] is not None:
        pool.append(("Cobertura testes", qc["coverage_score"], "code_quality"))

    pool_sorted = sorted(pool, key=lambda x: x[1])
    n = len(pool_sorted)
    tercile = max(1, n // 3)
    bottom = pool_sorted[:tercile]
    top = pool_sorted[-tercile:]

    def _bullets(items: list[tuple[str, int, str]]) -> list[dict]:
        return [{"label": lbl, "score": s, "dimension": dim} for lbl, s, dim in items]

    return (
        {"score": round(sum(s for _, s, _ in top) / len(top)),
         "bullets": _bullets([x for x in top if x[1] >= 80])},
        {"score": round(sum(s for _, s, _ in bottom) / len(bottom)),
         "bullets": _bullets(bottom)},
    )


# ── overall ───────────────────────────────────────────────────────────────────

def _compute_overall(perf: int, tech: int, qual: int,
                     tech_dim: dict) -> tuple[int, str, str | None]:
    raw = round(WEIGHTS["performance"] * perf
                + WEIGHTS["technical_health"] * tech
                + WEIGHTS["code_quality"] * qual)

    capped_reason = None
    tc = tech_dim["components"]
    if (ROOT / "EMERGENCY_LOCK.txt").exists():
        raw = min(raw, 25)
        capped_reason = "quarantine"
    elif tc["bot_status"] == "error" or (
        not tc.get("idle", False)
        and (tc["heartbeat_age_min"] or 0) > 45
    ):
        # only cap when inside the trading window — outside it a stale heartbeat is normal
        raw = min(raw, 40)
        capped_reason = "heartbeat"

    grade = ("A" if raw >= 85 else "B" if raw >= 70 else
             "C" if raw >= 55 else "D" if raw >= 40 else "F")
    return raw, grade, capped_reason


# ── history ───────────────────────────────────────────────────────────────────

def _update_history(history: list[dict], entry: dict, keep: int = 50) -> list[dict]:
    minute_key = entry["ts"][:16]
    filtered = [e for e in history if e.get("ts", "")[:16] != minute_key]
    filtered.append(entry)
    return filtered[-keep:]


def _trend(history: list[dict], key: str) -> str:
    vals = [e[key] for e in history[-3:] if e.get(key) is not None]
    if len(vals) < 2:
        return "flat"
    d = vals[-1] - vals[-2]
    return "up" if d > 2 else "down" if d < -2 else "flat"


# ── markdown rendering ────────────────────────────────────────────────────────

def _ge(score: int) -> str:  # grade emoji
    return "🟢" if score >= 80 else "🟡" if score >= 55 else "🔴"


def _ta(t: str) -> str:  # trend arrow
    return {"up": "▲", "down": "▼", "flat": "▬"}.get(t, "▬")


def _render_md(data: dict) -> str:
    ov = data["overall"]
    d = data["dimensions"]
    perf = d["performance"]
    tech = d["technical_health"]
    qual = d["code_quality"]
    strengths = data["strengths"]
    weaknesses = data["weaknesses"]
    history = data.get("history", [])

    now_str = data["updated"][:16].replace("T", " ") + " UTC"
    ge = _ge(ov["score"])

    L: list[str] = [
        "# FundScope — Project Health Dashboard",
        "> Auto-gerado por `scripts/generate_health_report.py` a cada ciclo. **NÃO editar à mão.**",
        f"> Última avaliação: {now_str} · ciclo `{data['cycle_ts'][:16]}`",
        "",
        f"## {ge} Score Geral: {ov['score']}/100 (Nível {ov['grade']})",
    ]
    delta = ov.get("delta_vs_prev")
    if delta is not None:
        sign = "+" if delta >= 0 else ""
        arrow = "▲" if delta > 0 else "▼" if delta < 0 else "▬"
        L.append(f"_{arrow} {sign}{delta} vs ciclo anterior_")
    if ov.get("capped_reason"):
        L.append(f"\n> ⚠️ **Score limitado** por `{ov['capped_reason']}`")
    tc = tech["components"]
    L += ["", f"_Bot `{tc['bot_status']}` · regime `{data.get('regime', '?')}` · fase demo_", ""]

    # summary table
    rows = [
        ("Performance",               perf["score"],       perf.get("trend", "flat")),
        ("Saúde Técnica",             tech["score"],       tech.get("trend", "flat")),
        ("Qualidade do Código",        qual["score"],       qual.get("trend", "flat")),
        ("Pontos Fortes",              strengths["score"],  strengths.get("trend", "flat")),
        ("Pontos Fracos ↑=melhor",    weaknesses["score"], weaknesses.get("trend", "flat")),
    ]
    L += ["| Dimensão | Score | | Tendência |",
          "|---|---|---|---|"]
    for name, score, trend in rows:
        L.append(f"| {name} | {score} | {_ge(score)} | {_ta(trend)} |")
    L.append("")

    # performance
    pc = perf["components"]
    conf = round(perf["confidence"] * 100)
    n_closed = perf["n_closed_trades"]
    L += [
        f"### 📈 Performance — {perf['score']}/100  _(confiança {conf}%, {n_closed} trade(s) fechado(s))_",
        f"- P&L acumulado: `{pc['pnl_pct']:+.2f}%` → {_ge(pc['pnl_score'])} {pc['pnl_score']}",
        f"- Drawdown máximo: `{pc['drawdown_pct']:.2f}%` → {_ge(pc['drawdown_score'])} {pc['drawdown_score']}",
        f"- Expectancy: `{pc['expectancy_eur']:+.2f}€/trade` · win rate `{pc['win_rate_pct']:.0f}%` → {_ge(pc['expectancy_score'])} {pc['expectancy_score']}",
    ]
    if pc["sharpe_score"] is not None:
        L.append(f"- Sharpe: `{pc['sharpe']:.2f}` → {_ge(pc['sharpe_score'])} {pc['sharpe_score']}")
    else:
        L.append("- Sharpe: `N/D`")
    for n in perf.get("notes", []):
        L.append(f"- ⚠️ _{n}_")
    L.append("")

    # technical health
    idle_tag = " · _idle (fora janela de mercado)_" if tc.get("idle") else ""
    age_str = f"{tc['heartbeat_age_min']} min" if tc["heartbeat_age_min"] is not None else "N/D"
    L += [
        f"### 🔧 Saúde Técnica — {tech['score']}/100",
        f"- Heartbeat: `{age_str}` ({tc['bot_status']}){idle_tag} → {_ge(tc['heartbeat_score'])} {tc['heartbeat_score']}",
        f"- Erros (7d): `{tc['errors_7d']}` total"
        f" ({tc['errors_critical']} críticos · {tc['errors_serious']} graves · {tc['errors_warning']} avisos)"
        f" → {_ge(tc['error_score'])} {tc['error_score']}",
    ]
    if tc["open_circuits"]:
        L.append(f"- ⚡ Circuitos abertos: `{', '.join(tc['open_circuits'])}` → {_ge(tc['api_score'])} {tc['api_score']}")
    else:
        L.append(f"- Circuitos abertos: nenhum → {_ge(tc['api_score'])} {tc['api_score']}")
    if tc["workflow_score"] is not None:
        L.append(f"- Workflows (últimos {tc['workflow_window']}): `{tc['workflow_score']}%` sucesso → {_ge(tc['workflow_score'])} {tc['workflow_score']}")
    else:
        L.append("- Workflows: `N/D` (gh indisponível)")
    for n in tech.get("notes", []):
        L.append(f"- ℹ️ _{n}_")
    L.append("")

    # code quality
    qc = qual["components"]
    L += [f"### 🧪 Qualidade do Código — {qual['score']}/100"]
    if qc["coverage_score"] is not None:
        L.append(f"- Cobertura: `{qc['coverage_pct']:.1f}%` (medida há {qc['coverage_age_days']:.0f}d) → {_ge(qc['coverage_score'])} {qc['coverage_score']}")
    else:
        L.append("- Cobertura: `N/D` _(instalar pytest-cov e executar CI para medição real)_")
    L += [
        f"- Módulos com teste: `{qc['tested_modules']}/{qc['total_modules']}` ({qc['test_presence_pct']:.0f}%) → {_ge(qc['test_presence_score'])} {qc['test_presence_score']}",
        f"- TODOs activos: `{qc['todo_count']}` → {_ge(qc['todo_score'])} {qc['todo_score']}",
        f"- Sintaxe `bot/`: `{'OK' if qc['syntax_ok'] else 'ERROS'}` → {_ge(100 if qc['syntax_ok'] else 0)} {100 if qc['syntax_ok'] else 0}",
    ]
    if qc["untested_modules"]:
        sample = " · ".join(f"`{m}`" for m in qc["untested_modules"][:8])
        extra = f" _+{len(qc['untested_modules'])-8} mais_" if len(qc["untested_modules"]) > 8 else ""
        L.append(f"- Módulos sem teste: {sample}{extra}")
    for n in qual.get("notes", []):
        L.append(f"- ℹ️ _{n}_")
    L.append("")

    # strengths
    L += [f"## ✅ Pontos Fortes — {strengths['score']}/100"]
    if strengths["bullets"]:
        for b in sorted(strengths["bullets"], key=lambda x: -x["score"]):
            L.append(f"- **{b['label']}** — `{b['score']}/100` _{b['dimension'].replace('_', ' ')}_")
    else:
        L.append("- Nenhum sub-score ≥ 80 neste ciclo")
    L.append("")

    # weaknesses
    L += [f"## ⚠️ Pontos Fracos — {weaknesses['score']}/100 _(maior = melhor)_"]
    if weaknesses["bullets"]:
        for b in sorted(weaknesses["bullets"], key=lambda x: x["score"]):
            L.append(f"- **{b['label']}** — `{b['score']}/100` _{b['dimension'].replace('_', ' ')}_")
    else:
        L.append("- Sem pontos críticos identificados neste ciclo 🎉")
    L.append("")

    # history
    if history:
        recent = history[-7:]
        L += [
            "## 📊 Histórico (últimas 7 avaliações)",
            "| Ciclo UTC | Geral | Perf | Técnica | Código | Fortes | Fracos |",
            "|-----------|-------|------|---------|--------|--------|--------|",
        ]
        for e in recent:
            ts = e.get("ts", "")[:16].replace("T", " ")
            L.append(
                f"| {ts} "
                f"| {e.get('overall','?')} "
                f"| {e.get('performance','?')} "
                f"| {e.get('technical_health','?')} "
                f"| {e.get('code_quality','?')} "
                f"| {e.get('strengths','?')} "
                f"| {e.get('weaknesses','?')} |"
            )
        L.append("")

    L += [
        "---",
        "_Fontes: `account_metrics.json` · `beta_summary.json` · `status.json` · `logs/errors/` · `gh run list`_",
    ]
    return "\n".join(L) + "\n"


# ── weekly Telegram summary ───────────────────────────────────────────────────

def _send_weekly_summary(data: dict, force: bool = False) -> None:
    today = datetime.now(timezone.utc)
    if not force and today.weekday() != 4:  # 4 = Friday
        return

    flag = "weekly_health_sent_date"
    today_str = today.strftime("%Y-%m-%d")
    try:
        flags = _read_json(DAILY_FLAGS)
        if not isinstance(flags, dict):
            flags = {}
        if not force and flags.get(flag) == today_str:
            print("[health] Resumo semanal já enviado hoje.", flush=True)
            return
    except Exception:
        flags = {}

    ov = data["overall"]
    perf = data["dimensions"]["performance"]
    tech = data["dimensions"]["technical_health"]
    qual = data["dimensions"]["code_quality"]
    strengths = data["strengths"]
    weaknesses = data["weaknesses"]
    regime = data.get("regime", "?")
    bot_status = tech["components"]["bot_status"]

    grade_e = {"A": "🏆", "B": "✅", "C": "🟡", "D": "🟠", "F": "🔴"}.get(ov["grade"], "❓")

    lines = [
        f"📊 FundScope — Relatório Semanal",
        f"Semana de {today_str}",
        "",
        f"{grade_e} Score Geral: {ov['score']}/100 (Nível {ov['grade']})",
        "",
        f"📈 Performance:    {perf['score']}/100",
        f"🔧 Saúde Técnica:  {tech['score']}/100",
        f"🧪 Qualidade Cód:  {qual['score']}/100",
        "",
    ]
    if strengths["bullets"]:
        lines.append("✅ Pontos Fortes:")
        for b in strengths["bullets"][:3]:
            lines.append(f"  • {b['label']}: {b['score']}/100")
        lines.append("")
    if weaknesses["bullets"]:
        lines.append("⚠️ Prioridades:")
        for b in sorted(weaknesses["bullets"], key=lambda x: x["score"])[:3]:
            lines.append(f"  • {b['label']}: {b['score']}/100")
        lines.append("")
    lines.append(f"Bot: {bot_status} · Regime: {regime}")

    msg = "\n".join(lines)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        env_file = ROOT / ".env"
        if env_file.exists():
            try:
                from dotenv import dotenv_values
                vals = dotenv_values(env_file) or {}
                token = token or (vals.get("TELEGRAM_BOT_TOKEN") or "")
                chat_id = chat_id or (vals.get("TELEGRAM_CHAT_ID") or "")
            except ImportError:
                pass

    if not token or not chat_id:
        print("[health] Telegram não configurado — resumo semanal não enviado.", flush=True)
        return

    import urllib.request

    try:
        payload = json.dumps({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        print("[health] Resumo semanal enviado via Telegram.", flush=True)
        try:
            DAILY_FLAGS.parent.mkdir(parents=True, exist_ok=True)
            flags[flag] = today_str
            tmp = DAILY_FLAGS.with_suffix(".tmp")
            tmp.write_text(json.dumps(flags, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(DAILY_FLAGS)
        except Exception as exc:
            print(f"[health] Aviso: falha a marcar flag semanal: {exc}", flush=True)
    except Exception as exc:
        print(f"[health] Falha ao enviar resumo semanal: {exc}", flush=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main(weekly: bool = False) -> int:
    print("[health] A gerar relatório de saúde...", flush=True)

    now = _now()
    status = _read_json(DATA_BETA / "status.json")
    cycle_ts = status.get("last_check", now) if isinstance(status, dict) else now
    regime = status.get("regime", "unknown") if isinstance(status, dict) else "unknown"

    perf = _score_performance()
    tech = _score_technical_health()
    qual = _score_code_quality()
    strengths, weaknesses = _build_sw(perf, tech, qual)
    overall_score, grade, capped_reason = _compute_overall(
        perf["score"], tech["score"], qual["score"], tech
    )

    # load existing history
    existing = _read_json(HEALTH_JSON)
    history: list[dict] = existing.get("history", []) if isinstance(existing, dict) else []

    # compute trends
    prev = history[-1] if history else {}
    delta = (overall_score - prev.get("overall", overall_score)) if prev else None

    for dim, key in [(perf, "performance"), (tech, "technical_health"),
                     (qual, "code_quality"), (strengths, "strengths"),
                     (weaknesses, "weaknesses")]:
        prev_s = prev.get(key)
        if prev_s is not None:
            d = dim["score"] - prev_s
            dim["trend"] = "up" if d > 2 else "down" if d < -2 else "flat"
        else:
            dim["trend"] = "flat"

    ov_trend = ("up" if (delta or 0) > 2 else "down" if (delta or 0) < -2 else "flat")

    history = _update_history(history, {
        "ts": now,
        "overall": overall_score,
        "performance": perf["score"],
        "technical_health": tech["score"],
        "code_quality": qual["score"],
        "strengths": strengths["score"],
        "weaknesses": weaknesses["score"],
    })

    health_data = {
        "updated": now,
        "cycle_ts": cycle_ts,
        "regime": regime,
        "overall": {
            "score": overall_score,
            "grade": grade,
            "trend": ov_trend,
            "delta_vs_prev": delta,
            "capped_reason": capped_reason,
        },
        "dimensions": {
            "performance": perf,
            "technical_health": tech,
            "code_quality": qual,
        },
        "strengths": strengths,
        "weaknesses": weaknesses,
        "history": history,
    }

    # atomic writes
    DOCS.mkdir(exist_ok=True)
    DATA_BETA.mkdir(parents=True, exist_ok=True)

    tmp = HEALTH_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(health_data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(HEALTH_JSON)
    print(f"[health] JSON escrito: {HEALTH_JSON.relative_to(ROOT)}", flush=True)

    md = _render_md(health_data)
    tmp_md = HEALTH_MD.with_suffix(".tmp")
    tmp_md.write_text(md, encoding="utf-8")
    tmp_md.replace(HEALTH_MD)
    print(f"[health] Markdown escrito: {HEALTH_MD.relative_to(ROOT)}", flush=True)

    try:
        _send_weekly_summary(health_data, force=weekly)
    except Exception as exc:
        print(f"[health] Aviso: resumo semanal falhou: {exc}", flush=True)

    print(f"[health] Score geral: {overall_score}/100 (Nível {grade})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(weekly="--weekly" in sys.argv))
