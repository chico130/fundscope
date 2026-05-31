"""
Code Heal — Ciclo de Castigo.

Chamado pelo workflow auto-debug.yml quando um workflow de produção falha.
Captura logs do run falhado, lê contexto de git, pede diagnóstico ao Gemini
e cria (ou comenta) uma GitHub Issue com a sugestão de correcção.

NUNCA escreve, commita ou faz push de código.
NUNCA cria PRs. Apenas cria/comenta issues com label 'auto-debug'.
Máximo 3 tentativas por fingerprint de erro.

Variáveis de ambiente obrigatórias (injectadas pelo workflow):
  GH_TOKEN           — token do GitHub Actions (issues: write)
  GEMINI_API_KEY     — chave Gemini (opcional; sem ela só cria issue sem sugestão LLM)
  TELEGRAM_BOT_TOKEN — token Telegram (opcional)
  TELEGRAM_CHAT_ID   — chat id Telegram (opcional)
  FAILED_RUN_ID      — ID do run falhado
  FAILED_WORKFLOW    — nome do workflow falhado
  FAILED_HEAD_SHA    — SHA do commit em que correu
  REPO               — "owner/repo"
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_STATE_PATH = _ROOT / "data" / "beta" / "code_heal_state.json"

MAX_ATTEMPTS = 3           # tentativas por fingerprint
LOG_TAIL_LINES = 80        # últimas N linhas do log de erro
GIT_DIFF_LINES = 150       # máx de linhas de diff a enviar ao LLM
CONTEXT_LINES_AROUND = 30  # linhas em volta de cada ficheiro do traceback
GEMINI_MODEL = "gemini-2.0-flash-lite"

# Padrões de sanitização — nunca enviar segredos ao LLM
_REDACT_PATTERNS = [
    (re.compile(r"T212_API(?:_KEY|_ID)[=:\s]+\S+",     re.I), "T212_API=***"),
    (re.compile(r"TELEGRAM_BOT_TOKEN[=:\s]+\S+",       re.I), "TELEGRAM_BOT_TOKEN=***"),
    (re.compile(r"TELEGRAM_CHAT_ID[=:\s]+\S+",         re.I), "TELEGRAM_CHAT_ID=***"),
    (re.compile(r"GEMINI_API_KEY[=:\s]+\S+",           re.I), "GEMINI_API_KEY=***"),
    (re.compile(r"FINNHUB_TOKEN[=:\s]+\S+",            re.I), "FINNHUB_TOKEN=***"),
    (re.compile(r"ANTHROPIC_API_KEY[=:\s]+\S+",        re.I), "ANTHROPIC_API_KEY=***"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}",               re.I), "ghp_***"),
    (re.compile(r"ghs_[A-Za-z0-9]{36}",               re.I), "ghs_***"),
    (re.compile(r"Bearer\s+\S+",                       re.I), "Bearer ***"),
    (re.compile(r"\*{3,}",                             re.I), "***"),      # já mascarado pelo Actions
]


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── state ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"fingerprints": {}}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception as exc:
        print(f"[code_heal] AVISO: falha a gravar estado: {exc}", flush=True)


# ── fingerprint ───────────────────────────────────────────────────────────────

def _normalize_error(raw: str) -> str:
    """Remove números, paths absolutos e hashes para tornar o fingerprint estável."""
    s = re.sub(r"/home/runner/work/[^/]+/[^/]+/", "", raw)
    s = re.sub(r"\b\d{4,}\b", "N", s)          # números longos (pids, ports, timestamps)
    s = re.sub(r"0x[0-9a-f]+", "0xHEX", s, flags=re.I)
    s = re.sub(r"\b[0-9a-f]{40}\b", "SHA", s)  # SHA git
    lines = s.splitlines()
    # Pegar as últimas 5 linhas não-vazias (onde geralmente está a excepção)
    relevant = [l.strip() for l in lines if l.strip()][-5:]
    return " | ".join(relevant)


def _fingerprint(workflow: str, step: str, error_text: str) -> str:
    normalized = _normalize_error(error_text)
    raw = f"{workflow}|{step}|{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _gh(args: list[str], *, capture: bool = True) -> str:
    token = os.environ.get("GH_TOKEN", "")
    env = {**os.environ, "GH_TOKEN": token}
    try:
        r = subprocess.run(
            ["gh", *args],
            capture_output=capture,
            text=True,
            env=env,
            timeout=30,
        )
        if capture:
            return (r.stdout or "").strip()
        return ""
    except Exception as exc:
        print(f"[code_heal] gh falhou: {exc}", flush=True)
        return ""


def _fetch_failed_logs(run_id: str, repo: str) -> tuple[str, str]:
    """
    Devolve (step_name, log_tail) do step falhado.
    Usa `gh run view --log-failed` — só devolve os steps com falha.
    """
    raw = _gh(["run", "view", run_id, "--repo", repo, "--log-failed"])
    if not raw:
        return "unknown", ""

    lines = raw.splitlines()
    # Extrair nome do step falhado da primeira linha de log (formato: STEP_NAME\tMESSAGE)
    step_name = "unknown"
    for line in lines[:20]:
        parts = line.split("\t", 2)
        if len(parts) >= 2 and parts[0].strip():
            step_name = parts[0].strip()
            break

    # Tail das últimas N linhas
    tail = "\n".join(lines[-LOG_TAIL_LINES:])
    return step_name, _sanitize(tail)


def _find_files_in_traceback(log: str) -> list[str]:
    """Extrai caminhos de ficheiro Python do traceback."""
    pattern = re.compile(r'File "([^"]+\.py)"')
    seen: list[str] = []
    for m in pattern.finditer(log):
        path = m.group(1)
        # Normalizar: remover prefixo absoluto do runner
        path = re.sub(r"^.*/fundscope/", "", path)
        if path not in seen and not path.startswith("/"):
            seen.append(path)
    return seen[:5]  # no máximo 5 ficheiros


def _git_context(files: list[str]) -> str:
    """Últimos 3 commits que tocaram nos ficheiros + diff dos hunks relevantes."""
    if not files:
        return ""
    parts: list[str] = []
    for f in files:
        if not Path(f).exists():
            continue
        log = subprocess.run(
            ["git", "log", "--oneline", "-5", "--", f],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if log:
            parts.append(f"# git log {f}\n{log}")
        diff = subprocess.run(
            ["git", "diff", "HEAD~3..HEAD", "--", f],
            capture_output=True, text=True, timeout=10,
        ).stdout
        if diff:
            diff_lines = diff.splitlines()[:GIT_DIFF_LINES]
            parts.append(f"# diff {f}\n" + "\n".join(diff_lines))
    return "\n\n".join(parts)


def _file_context(files: list[str], log: str) -> str:
    """Para cada ficheiro do traceback, extrai ±30 linhas à volta da linha referenciada."""
    parts: list[str] = []
    line_re = re.compile(r'File "([^"]+\.py)", line (\d+)')
    file_lines: dict[str, list[int]] = {}
    for m in line_re.finditer(log):
        path = re.sub(r"^.*/fundscope/", "", m.group(1))
        lineno = int(m.group(2))
        file_lines.setdefault(path, []).append(lineno)

    for f, linenos in list(file_lines.items())[:3]:
        p = Path(f)
        if not p.exists():
            continue
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for lineno in linenos[:2]:
            start = max(0, lineno - CONTEXT_LINES_AROUND - 1)
            end   = min(len(all_lines), lineno + CONTEXT_LINES_AROUND)
            snippet = "\n".join(
                f"{i+1:4d}: {l}" for i, l in enumerate(all_lines[start:end], start=start)
            )
            parts.append(f"# {f} (linhas {start+1}–{end})\n{snippet}")
    return "\n\n".join(parts)


# ── LLM ───────────────────────────────────────────────────────────────────────

def _build_prompt(workflow: str, step: str, log: str, git_ctx: str, file_ctx: str) -> str:
    return (
        "És um assistente de diagnóstico de erros em CI/CD para um bot de trading Python.\n"
        "Devolve APENAS JSON. Sem markdown, sem texto fora do JSON.\n\n"
        f"== WORKFLOW FALHADO ==\n{workflow} | step: {step}\n\n"
        f"== LOG DE ERRO (últimas linhas) ==\n{log}\n\n"
        + (f"== CONTEXTO GIT (commits + diff) ==\n{git_ctx}\n\n" if git_ctx else "")
        + (f"== FICHEIROS RELEVANTES ==\n{file_ctx}\n\n" if file_ctx else "")
        + "== INSTRUÇÕES ==\n"
        "1. Identifica a causa-raiz em 1-2 frases.\n"
        "2. Propõe a correcção mínima necessária (diff unificado ou pseudocódigo curto).\n"
        "3. NÃO reescreva ficheiros inteiros. Apenas o patch mínimo.\n"
        "4. NÃO sugiras estratégias de trading novas.\n"
        "5. Se não tiveres contexto suficiente, diz 'contexto insuficiente'.\n\n"
        "== FORMATO DE RESPOSTA ==\n"
        '{"root_cause": "...", "fix_suggestion": "...", "confidence": "low|medium|high"}'
    )


def _call_gemini(prompt: str) -> dict:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"root_cause": "GEMINI_API_KEY não definido.", "fix_suggestion": "", "confidence": "low"}
    try:
        from google import genai
        from google.genai import types as _gt
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=_gt.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=600,
            ),
        )
        raw = (resp.text or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw).strip()
        return json.loads(raw)
    except Exception as exc:
        return {"root_cause": f"Gemini falhou: {exc}", "fix_suggestion": "", "confidence": "low"}


# ── GitHub Issues ──────────────────────────────────────────────────────────────

def _find_open_issue(repo: str, fingerprint: str) -> str | None:
    """Procura issue aberta com o fingerprint no corpo. Devolve issue number ou None."""
    raw = _gh([
        "issue", "list",
        "--repo", repo,
        "--label", "auto-debug",
        "--state", "open",
        "--json", "number,body",
        "--limit", "20",
    ])
    if not raw:
        return None
    try:
        issues = json.loads(raw)
        for issue in issues:
            if fingerprint in (issue.get("body") or ""):
                return str(issue["number"])
    except Exception:
        pass
    return None


def _create_issue(repo: str, workflow: str, step: str, fingerprint: str,
                  attempt: int, diagnosis: dict, log_tail: str) -> str:
    root_cause = diagnosis.get("root_cause", "Diagnóstico não disponível.")
    fix = diagnosis.get("fix_suggestion", "")
    confidence = diagnosis.get("confidence", "low")

    body = (
        f"## Auto-Debug — Ciclo de Castigo\n\n"
        f"**Workflow:** `{workflow}`  \n"
        f"**Step falhado:** `{step}`  \n"
        f"**Tentativa:** {attempt}/{MAX_ATTEMPTS}  \n"
        f"**Fingerprint:** `{fingerprint}`  \n"
        f"**Confiança do diagnóstico:** {confidence}\n\n"
        f"---\n\n"
        f"## Causa Raiz (Gemini)\n\n{root_cause}\n\n"
        + (f"## Correcção Sugerida\n\n```\n{fix}\n```\n\n" if fix else "")
        + f"## Log de Erro\n\n<details>\n<summary>Últimas {LOG_TAIL_LINES} linhas</summary>\n\n"
        f"```\n{log_tail[:3000]}\n```\n</details>\n\n"
        f"---\n"
        f"⚠️ **Nunca aplicar automaticamente.** Rever e corrigir manualmente.\n"
        f"🤖 Gerado por `scripts/code_heal.py` em {_ts()}"
    )

    num = _gh([
        "issue", "create",
        "--repo", repo,
        "--title", f"[Auto-Debug] {workflow} falhou — {step}",
        "--body", body,
        "--label", "auto-debug",
    ])
    return num.strip() if num else ""


def _comment_issue(repo: str, issue_number: str, attempt: int,
                   diagnosis: dict, log_tail: str) -> None:
    root_cause = diagnosis.get("root_cause", "Diagnóstico não disponível.")
    fix = diagnosis.get("fix_suggestion", "")
    confidence = diagnosis.get("confidence", "low")

    if attempt >= MAX_ATTEMPTS:
        body = (
            f"## ❌ Tentativa {attempt}/{MAX_ATTEMPTS} — Limite atingido\n\n"
            f"O auto-debug esgotou as {MAX_ATTEMPTS} tentativas para este erro.\n"
            f"**Intervenção manual obrigatória.**\n\n"
            f"Último diagnóstico ({confidence} confiança): {root_cause}\n\n"
            + (f"Correcção sugerida:\n```\n{fix}\n```\n" if fix else "")
        )
    else:
        body = (
            f"## Tentativa {attempt}/{MAX_ATTEMPTS}\n\n"
            f"**Diagnóstico ({confidence} confiança):** {root_cause}\n\n"
            + (f"**Correcção sugerida:**\n```\n{fix}\n```\n" if fix else "")
            + f"\n_Actualizado em {_ts()}_"
        )

    _gh([
        "issue", "comment",
        "--repo", repo,
        "--body", body,
        issue_number,
    ], capture=False)


# ── Telegram ───────────────────────────────────────────────────────────────────

def _telegram(msg: str) -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "disable_notification": False},
            timeout=8,
        )
    except Exception as exc:
        print(f"[code_heal] Telegram falhou: {exc}", flush=True)


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    workflow  = os.environ.get("FAILED_WORKFLOW", "unknown")
    run_id    = os.environ.get("FAILED_RUN_ID", "")
    head_sha  = os.environ.get("FAILED_HEAD_SHA", "")
    repo      = os.environ.get("REPO", "")

    print(f"[{_ts()}] === Code Heal START ===", flush=True)
    print(f"[code_heal] workflow={workflow} run_id={run_id} sha={head_sha[:8]}", flush=True)

    if not run_id or not repo:
        print("[code_heal] FAILED_RUN_ID ou REPO não definidos — a sair.", flush=True)
        sys.exit(0)

    # 1. Capturar logs
    step_name, log_tail = _fetch_failed_logs(run_id, repo)
    if not log_tail:
        print("[code_heal] Log vazio — a sair.", flush=True)
        sys.exit(0)

    # 2. Fingerprint e estado
    fp = _fingerprint(workflow, step_name, log_tail)
    state = _load_state()
    fp_data = state.setdefault("fingerprints", {}).setdefault(fp, {
        "attempts": 0,
        "first_seen": _ts(),
        "last_seen": _ts(),
        "issue_number": None,
        "status": "open",
        "workflow": workflow,
        "step": step_name,
    })
    fp_data["last_seen"] = _ts()
    fp_data["attempts"] += 1
    attempt = fp_data["attempts"]

    print(f"[code_heal] fingerprint={fp} attempt={attempt}/{MAX_ATTEMPTS}", flush=True)

    # 3. Limite de tentativas atingido → escala e sai
    if attempt > MAX_ATTEMPTS:
        print(f"[code_heal] Limite {MAX_ATTEMPTS} já atingido para {fp} — sem nova chamada LLM.", flush=True)
        _save_state(state)
        _telegram(
            f"⚠️ Auto-debug: limite {MAX_ATTEMPTS} tentativas já ultrapassado\n"
            f"Workflow: {workflow}\n"
            f"Intervenção manual necessária. Fecha a issue #{fp_data.get('issue_number', '?')} quando resolveres."
        )
        sys.exit(0)

    # 4. Contexto git + ficheiros
    files_in_tb = _find_files_in_traceback(log_tail)
    print(f"[code_heal] ficheiros no traceback: {files_in_tb}", flush=True)
    git_ctx  = _git_context(files_in_tb)
    file_ctx = _file_context(files_in_tb, log_tail)

    # 5. Diagnóstico LLM
    prompt    = _build_prompt(workflow, step_name, log_tail, git_ctx, file_ctx)
    diagnosis = _call_gemini(prompt)
    print(f"[code_heal] diagnóstico: {str(diagnosis)[:200]}", flush=True)

    # 6. Criar ou comentar issue
    existing_issue = _find_open_issue(repo, fp)
    if existing_issue:
        fp_data["issue_number"] = existing_issue
        _comment_issue(repo, existing_issue, attempt, diagnosis, log_tail)
        issue_ref = f"#{existing_issue}"
    else:
        issue_num = _create_issue(repo, workflow, step_name, fp, attempt, diagnosis, log_tail)
        if issue_num:
            fp_data["issue_number"] = issue_num
        issue_ref = f"#{issue_num}" if issue_num else "(issue não criada)"

    # 7. Actualizar status
    if attempt >= MAX_ATTEMPTS:
        fp_data["status"] = "escalated"

    _save_state(state)

    # 8. Telegram
    if attempt >= MAX_ATTEMPTS:
        _telegram(
            f"⚠️ Auto-debug esgotou {MAX_ATTEMPTS} tentativas\n"
            f"Workflow: {workflow} | Step: {step_name}\n"
            f"Issue: {issue_ref}\n"
            f"Intervenção manual necessária."
        )
    else:
        confidence = diagnosis.get("confidence", "?")
        _telegram(
            f"🔧 Auto-Debug activado\n"
            f"Workflow: {workflow}\n"
            f"Step: {step_name}\n"
            f"Tentativa: {attempt}/{MAX_ATTEMPTS} | Confiança: {confidence}\n"
            f"Issue: {issue_ref}"
        )

    print(f"[{_ts()}] === Code Heal END === issue={issue_ref} attempt={attempt}", flush=True)


if __name__ == "__main__":
    main()
