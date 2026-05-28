"""
Actualiza as seccoes dinamicas do CLAUDE.md apos cada ciclo do bot:
  - Estado Actual: le data/beta/status.json, beta_positions.json, beta_trades.json
  - Ultimas Alteracoes: le os ultimos commits do git log (exclui commits automaticos)

Uso: python scripts/update_claude_md.py
     Chamado pelo workflow run-trading-bot.yml apos cada ciclo.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
DATA_BETA = ROOT / "data" / "beta"

_AUTO_COMMIT_PREFIXES = (
    "chore: auto-update CLAUDE.md",
    "bot: cycle",
    "chore: portfolio sync",
    "chore: refresh AI insights",
    "Auto-update",
)


def _git_log(n: int = 10) -> list[dict]:
    result = subprocess.run(
        ["git", "log", "--format=%h|%ad|%s", "--date=format:%Y-%m-%d", "-50"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    rows: list[dict] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        msg = parts[2]
        if any(msg.startswith(p) for p in _AUTO_COMMIT_PREFIXES):
            continue
        rows.append({"hash": parts[0], "date": parts[1], "msg": msg})
        if len(rows) >= n:
            break
    return rows


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _build_estado_actual() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    status = _read_json(DATA_BETA / "status.json")
    positions_data = _read_json(DATA_BETA / "beta_positions.json")
    trades_data = _read_json(DATA_BETA / "beta_trades.json")

    bot_status = status.get("bot_status", "unknown")
    last_check = (status.get("last_check") or "—")[:16]
    regime = status.get("regime", "—")
    mode = status.get("mode", "—")

    positions = positions_data.get("positions", []) if isinstance(positions_data, dict) else []
    n_positions = len(positions)

    trades = trades_data.get("trades", []) if isinstance(trades_data, dict) else []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades_today = sum(
        1 for t in trades
        if t.get("datetime", "").startswith(today) and t.get("side", "").upper() == "BUY"
    )
    open_trades = sum(1 for t in trades if not t.get("closed_at"))

    lines = [
        f"**Actualizado em:** {now}",
        "",
        f"- **Bot status:** `{bot_status}` | Ultimo ciclo: `{last_check}Z`",
        f"- **Regime:** `{regime}` | Modo: `{mode}`",
        f"- **Posicoes abertas:** {n_positions} | **Trades abertos:** {open_trades} | **Trades hoje:** {trades_today}",
        "- **Fase:** Fase 1 — execucao automatica em conta demo (`PHASE1_EXECUTION=True`, `LIVE_TRADING=False`)",
        "- **Modelo activo:** Bonnie v4-clean (`bonnie_model_v4.pkl`) — thresholds 0.30 por regime",
        "- **Parametros:** `atr_stop_mult=1.75` | `atr_tp_mult=4.25` | `max_position_pct=11%`",
        "- **OOS ref (run-007):** +62.2% vs SPY +45.2% | Alpha +17pp | Sharpe 2.09 | DD -10.8% | WR 38% | R:R 2.5:1",
        "- **Proximo passo:** Aguardar 30 dias de validacao real com v4-clean. **Sem optimizacoes adicionais.**",
    ]
    return "\n".join(lines)


def _build_ultimas_alteracoes() -> str:
    commits = _git_log(10)
    if not commits:
        return "_(sem commits recentes)_"
    rows = ["| Data | Hash | Descricao |", "|---|---|---|"]
    for c in commits:
        msg = c["msg"][:80].replace("|", "\\|")
        rows.append(f"| {c['date']} | `{c['hash']}` | {msg} |")
    return "\n".join(rows)


def _replace_section(content: str, marker: str, new_body: str) -> str:
    start_tag = f"<!-- {marker}-START -->"
    end_tag = f"<!-- {marker}-END -->"
    pattern = re.compile(
        re.escape(start_tag) + r".*?" + re.escape(end_tag),
        re.DOTALL,
    )
    replacement = f"{start_tag}\n{new_body}\n{end_tag}"
    new_content, n = pattern.subn(replacement, content)
    if n == 0:
        print(f"[WARN] Marcador '{marker}' nao encontrado no CLAUDE.md — seccao nao actualizada")
    return new_content


def main() -> int:
    if not CLAUDE_MD.exists():
        print(f"[ERROR] CLAUDE.md nao encontrado em {CLAUDE_MD}")
        return 1

    content = CLAUDE_MD.read_text(encoding="utf-8")
    original = content

    content = _replace_section(content, "ESTADO-ACTUAL", _build_estado_actual())
    content = _replace_section(content, "ULTIMAS-ALTERACOES", _build_ultimas_alteracoes())

    if content == original:
        print("[OK] CLAUDE.md sem alteracoes.")
        return 0

    CLAUDE_MD.write_text(content, encoding="utf-8")
    print(f"[OK] CLAUDE.md actualizado em {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
