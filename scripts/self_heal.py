"""
Self-Healing Semanal FundScope — sugestões de ajuste via Gemini.

Corre após o auditor semanal (sábados ~06:05 UTC via weekly-audit.yml).
Lê audit_weekly.json + config_risco.json, pede sugestão ao Gemini,
valida rigorosamente e escreve data/suggested_config.json.

NUNCA escreve directamente em config_risco.json.
A promoção é sempre manual via workflow 'apply-suggested-config'.

Jaula de validação (3 camadas):
  1. Allowlist  — apenas params em PARAM_BOUNDS podem ser alterados
  2. Range      — [min, max] por parâmetro, nunca ultrapassado
  3. Sanity     — anti-alucinação de baseline, magnitude máxima, ordenação VIX
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_AUDIT_PATH     = _ROOT / "data" / "audit_weekly.json"
_CONFIG_PATH    = _ROOT / "config_risco.json"
_SUGGESTED_PATH = _ROOT / "data" / "suggested_config.json"
_STATE_PATH     = _ROOT / "data" / "beta" / "self_heal_state.json"

GEMINI_MODEL     = "gemini-2.0-flash-lite"
MIN_TRADES       = 3    # amostra mínima para chamar Gemini
MIN_DAYS_BETWEEN = 6    # gate semanal: mínimo 6 dias entre execuções
MAX_ADJUSTMENTS  = 3    # máximo de parâmetros alterados por sessão

# ── Jaula hardcoded — imutável, Gemini nunca pode sugerir fora destes limites ──
PARAM_BOUNDS: dict[str, dict] = {
    "tamanho_maximo_posicao":    {"min": 0.40, "max": 1.00, "step_max": 0.15},
    "vix_kill_switch_threshold": {"min": 30.0, "max": 40.0, "step_max": 3.0},
    "vix_total_kill_threshold":  {"min": 42.0, "max": 50.0, "step_max": 3.0},
    "vix_caution_threshold":     {"min": 15.0, "max": 25.0, "step_max": 3.0},
    "cash_is_king_multiplier":   {"min": 0.10, "max": 0.50, "step_max": 0.10},
    "mean_reversion_rsi_max":    {"min": 25.0, "max": 40.0, "step_max": 5.0},
    "mean_reversion_max_vix":    {"min": 15.0, "max": 25.0, "step_max": 3.0},
}

# Invariante de ordenação (validada após simulação de aplicação)
_VIX_ORDER = ("vix_caution_threshold", "vix_kill_switch_threshold", "vix_total_kill_threshold")


# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", text.strip()).strip()


# ── Gate semanal ──────────────────────────────────────────────────────────────

def _check_weekly_gate() -> tuple[bool, str]:
    """Devolve (pode_correr, motivo)."""
    state = _load_json(_STATE_PATH, {})
    last_run_str = state.get("last_run")
    if not last_run_str:
        return True, "primeira execução"
    try:
        last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
        days_since = (datetime.now(timezone.utc) - last_run).days
        if days_since < MIN_DAYS_BETWEEN:
            return False, f"última execução há {days_since} dias (mínimo {MIN_DAYS_BETWEEN})"
        return True, f"última execução há {days_since} dias"
    except (ValueError, TypeError):
        return True, "estado inválido — a resetar"


def _update_state(status: str, applied: list, rejected: list) -> None:
    try:
        _write_atomic(_STATE_PATH, {
            "last_run":        _ts(),
            "last_status":     status,
            "applied_count":   len(applied),
            "rejected_count":  len(rejected),
        })
    except Exception as exc:
        print(f"[self_heal] AVISO: falha ao escrever estado: {exc}", flush=True)


# ── Construção do prompt ──────────────────────────────────────────────────────

def _build_param_table(config: dict) -> str:
    lines = []
    for param, b in PARAM_BOUNDS.items():
        current = config.get(param, "N/A")
        lines.append(
            f"  - {param}: atual={current}"
            f" | permitido [{b['min']}, {b['max']}]"
            f" | variação máx ±{b['step_max']}"
        )
    return "\n".join(lines)


def _build_findings(patterns: list) -> str:
    lines = [
        f"  - [{p.get('id','?')}] {p['finding'].strip()}"
        for p in patterns
        if p.get("finding", "").strip()
    ]
    return "\n".join(lines) if lines else "  - Sem padrões relevantes detectados."


def _build_prompt(audit: dict, config: dict) -> str:
    s   = audit.get("summary", {})
    w   = audit.get("window", {})
    wr  = round(s.get("win_rate", 0.0) * 100, 1)
    sh  = s.get("sharpe_weekly")
    dd  = s.get("max_drawdown_pct")

    return (
        "És um assistente de afinação de parâmetros de risco de um bot de trading.\n"
        "NÃO és um estratega. NÃO podes inventar estratégias, criar parâmetros novos,\n"
        "nem contradizer princípios de gestão de risco. Apenas sugeres pequenos ajustes\n"
        "a parâmetros existentes, sempre dentro dos limites rígidos indicados abaixo.\n\n"
        "== RESUMO DA SEMANA (conta demo) ==\n"
        f"Janela: {w.get('start','?')} a {w.get('end','?')}"
        f" ({w.get('trading_days','?')} dias úteis)\n"
        f"Regime dominante: {s.get('regime_dominant','?')}\n"
        f"Trades fechados: {s.get('trades_closed', 0)}"
        f"  (vencedores: {s.get('wins', 0)} | perdedores: {s.get('losses', 0)})\n"
        f"Win rate: {wr}%\n"
        f"P&L: {s.get('pnl_eur', 0.0):.2f}€\n"
        f"Sharpe semanal: {f'{sh:.2f}' if sh is not None else 'N/A'}\n"
        f"Max drawdown: {f'{dd:.2f}%' if dd is not None else 'N/A'}\n\n"
        "Padrões observados pelo auditor:\n"
        f"{_build_findings(audit.get('patterns', []))}\n\n"
        "== PARÂMETROS AJUSTÁVEIS (com valor actual e limites rígidos) ==\n"
        f"{_build_param_table(config)}\n\n"
        "== REGRAS OBRIGATÓRIAS ==\n"
        "1. Só podes sugerir ajustes aos parâmetros listados acima. Qualquer outro nome é inválido.\n"
        "2. Cada valor sugerido TEM de estar dentro do intervalo [min, max] indicado.\n"
        "3. Cada alteração não pode exceder a variação máxima semanal (±step_max) face ao valor actual.\n"
        f"4. No máximo {MAX_ADJUSTMENTS} parâmetros podem ser alterados. "
        "Se não há justificação sólida, devolve lista vazia.\n"
        "5. Mantém a ordem: vix_caution_threshold < vix_kill_switch_threshold < vix_total_kill_threshold.\n"
        "6. Sê conservador. Uma única semana é uma amostra pequena. Na dúvida, não alteres.\n"
        "7. NÃO recomendes compras/vendas. NÃO proponhas lógica nova.\n\n"
        "== FORMATO DE RESPOSTA ==\n"
        "Responde APENAS com este objecto JSON, sem texto antes ou depois, sem markdown:\n"
        "{\n"
        '  "adjustments": [\n'
        '    {"param": "<nome exacto da lista>", "current": <valor actual>,'
        ' "suggested": <valor novo>, "reason": "<1 frase>"}\n'
        "  ],\n"
        '  "overall_reasoning": "<2 frases, factual>"\n'
        "}\n"
        'Se nenhum ajuste se justifica: {"adjustments": [], "overall_reasoning": "..."}.\n'
    )


# ── Validação ─────────────────────────────────────────────────────────────────

def _validate_adjustments(raw_adjs: list, config: dict) -> tuple[list, list]:
    """Valida cada adjustment individualmente. Rejeições não abortam o batch."""
    valid: list[dict] = []
    rejected: list[dict] = []

    for adj in raw_adjs:
        param           = adj.get("param", "")
        suggested_raw   = adj.get("suggested")
        current_claimed = adj.get("current")

        # Schema mínimo
        if not param or suggested_raw is None:
            rejected.append({"adj": adj, "reason": "schema_incompleto"})
            continue

        # Allowlist
        if param not in PARAM_BOUNDS:
            rejected.append({"adj": adj, "reason": f"param_nao_permitido: {param}"})
            continue

        # O parâmetro tem de existir no config real
        real_current = config.get(param)
        if real_current is None:
            rejected.append({"adj": adj, "reason": f"param_ausente_em_config: {param}"})
            continue

        # Anti-alucinação: Gemini declarou baseline diferente do real
        try:
            if abs(float(current_claimed) - float(real_current)) > 1e-9:
                rejected.append({
                    "adj": adj,
                    "reason": f"baseline_alucinado: declarou {current_claimed}, real={real_current}",
                })
                continue
        except (TypeError, ValueError):
            rejected.append({"adj": adj, "reason": "current_nao_numerico"})
            continue

        # Converter para float
        try:
            suggested = float(suggested_raw)
        except (TypeError, ValueError):
            rejected.append({"adj": adj, "reason": "suggested_nao_numerico"})
            continue

        b = PARAM_BOUNDS[param]

        # Range
        if not (b["min"] <= suggested <= b["max"]):
            rejected.append({
                "adj": adj,
                "reason": f"fora_de_limites: {suggested} not in [{b['min']}, {b['max']}]",
            })
            continue

        # Magnitude máxima semanal
        delta = abs(suggested - float(real_current))
        if delta > b["step_max"]:
            rejected.append({
                "adj": adj,
                "reason": f"variacao_excessiva: {delta:.4f} > step_max={b['step_max']}",
            })
            continue

        # No-op
        if delta < 1e-9:
            rejected.append({"adj": adj, "reason": "no_op: valor igual ao actual"})
            continue

        valid.append({
            "param":     param,
            "current":   float(real_current),
            "suggested": suggested,
            "reason":    str(adj.get("reason", ""))[:200],
        })

    return valid, rejected


def _sanity_batch(valid: list, config: dict) -> tuple[list, list]:
    """Sanity checks sobre o batch completo. Pode rejeitar ajustes adicionais."""
    if not valid:
        return valid, []

    # Simular estado após aplicação
    simulated = dict(config)
    for adj in valid:
        simulated[adj["param"]] = adj["suggested"]

    # Invariante de ordenação VIX
    vix_vals = [simulated.get(f) for f in _VIX_ORDER]
    if all(v is not None for v in vix_vals):
        try:
            if not (float(vix_vals[0]) < float(vix_vals[1]) < float(vix_vals[2])):
                vix_set = set(_VIX_ORDER)
                good = [a for a in valid if a["param"] not in vix_set]
                bad  = [
                    {**a, "reason": "vix_ordem_invalida_apos_aplicacao"}
                    for a in valid if a["param"] in vix_set
                ]
                return good, bad
        except (TypeError, ValueError):
            pass

    # Garantir máximo (já filtrado individualmente, mas defesa extra)
    if len(valid) > MAX_ADJUSTMENTS:
        return valid[:MAX_ADJUSTMENTS], [
            {**a, "reason": "batch_excede_max_adjustments"}
            for a in valid[MAX_ADJUSTMENTS:]
        ]

    return valid, []


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(msg: str) -> None:
    try:
        from bot.notifier import enviar_alerta
        enviar_alerta(msg, silencioso=False)
    except Exception as exc:
        print(f"[self_heal] Telegram falhou: {exc}", flush=True)


def _build_telegram_msg(valid: list, rejected: list, reasoning: str, audit: dict) -> str:
    s       = audit.get("summary", {})
    wr_pct  = round(s.get("win_rate", 0.0) * 100, 1)
    trades  = s.get("trades_closed", 0)

    lines = [
        "🤖 Self-Heal Semanal — Sugestão Gemini",
        f"Semana: {trades} trades | Win Rate: {wr_pct}%",
        "",
    ]

    if valid:
        lines.append("Parâmetros sugeridos:")
        for adj in valid:
            curr  = adj["current"]
            sugg  = adj["suggested"]
            pct   = ((sugg - curr) / curr * 100) if curr else 0
            arrow = "↑" if sugg > curr else "↓"
            lines.append(f"• {adj['param']}: {curr} → {sugg} ({arrow} {abs(pct):.1f}%)")
            if adj.get("reason"):
                lines.append(f"  {adj['reason']}")
    else:
        lines.append("Nenhum ajuste sugerido — parâmetros actuais são adequados.")

    if rejected:
        lines += [
            "",
            f"Rejeitadas: {len(rejected)} sugestão(ões) inválida(s)",
        ]
        for r in rejected[:3]:
            lines.append(f"  ✗ {r.get('adj', {}).get('param', '?')}: {r.get('reason', '?')}")

    if reasoning:
        lines += ["", f"Raciocínio: {reasoning}"]

    val_line = (
        "Validação: ✅ Todos dentro dos limites"
        if not rejected
        else f"Validação: ⚠️ {len(rejected)} rejeitada(s)"
    )
    lines += [
        "",
        val_line,
        "",
        "Para aplicar: acede ao workflow 'apply-suggested-config' e corre manualmente.",
        "⚠️ A aplicação NUNCA é automática — requer aprovação manual.",
    ]
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def run_self_healing() -> None:
    print(f"[{_ts()}] === Self-Heal START ===", flush=True)

    # GATE 0 — API key
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        print("[self_heal] GEMINI_API_KEY não definido — a ignorar", flush=True)
        return

    # GATE 1 — Rate semanal
    can_run, gate_reason = _check_weekly_gate()
    if not can_run:
        print(f"[self_heal] Gate semanal: {gate_reason} — a ignorar", flush=True)
        return
    print(f"[self_heal] Gate semanal: {gate_reason}", flush=True)

    # GATE 2 — Audit recente (máx 48h)
    audit = _load_json(_AUDIT_PATH, {})
    if not audit:
        print("[self_heal] audit_weekly.json não encontrado — a ignorar", flush=True)
        return

    generated_at = audit.get("generated_at", "")
    if generated_at:
        try:
            audit_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - audit_dt).total_seconds() / 3600
            if age_h > 48:
                print(f"[self_heal] Audit desactualizado ({age_h:.0f}h) — a ignorar", flush=True)
                _send_telegram(f"🤖 Self-Heal — ignorado\nAudit com {age_h:.0f}h de idade (máx 48h)")
                return
        except (ValueError, TypeError):
            pass

    # GATE 3 — Amostra mínima (evita afinar em ruído)
    trades_closed = audit.get("summary", {}).get("trades_closed", 0)
    if trades_closed < MIN_TRADES:
        msg = f"amostra insuficiente ({trades_closed} trades, mínimo {MIN_TRADES})"
        print(f"[self_heal] {msg} — a ignorar sem chamar Gemini", flush=True)
        _update_state("skipped_insufficient_data", [], [])
        return

    # GATE 4 — Config
    config = _load_json(_CONFIG_PATH, {})
    if not config:
        print("[self_heal] config_risco.json não encontrado — a ignorar", flush=True)
        return

    # GATE 5 — Rate limiter (quota diária Gemini partilhada)
    try:
        from bot import rate_limiter as _rl
        if not _rl.check_and_consume("gemini"):
            print("[self_heal] Gemini rate limit atingido — a ignorar", flush=True)
            _update_state("skipped_rate_limit", [], [])
            return
    except Exception as exc:
        print(f"[self_heal] rate_limiter falhou ({exc}) — fail-open, a continuar", flush=True)

    # ── Chamada Gemini ────────────────────────────────────────────────────────
    prompt   = _build_prompt(audit, config)
    raw_text = ""
    print(f"[self_heal] A chamar Gemini ({GEMINI_MODEL})...", flush=True)
    try:
        from google import genai
        from google.genai import types as _gt
        client = genai.Client(api_key=gemini_key)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=_gt.GenerateContentConfig(
                http_options=_gt.HttpOptions(timeout=20_000),
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=800,
            ),
        )
        raw_text = (resp.text or "").strip()
        print(f"[self_heal] Gemini respondeu: {raw_text[:200]}", flush=True)
    except Exception as exc:
        preview = raw_text[:200].replace("\n", "\\n") if raw_text else "<vazio>"
        print(f"[self_heal] Gemini falhou: {exc} | raw: {preview}", flush=True)
        _update_state("gemini_error", [], [])
        try:
            _send_telegram(f"🤖 Self-Heal — Gemini falhou\n{type(exc).__name__}: {str(exc)[:300]}")
        except Exception:
            pass
        return

    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        parsed = json.loads(_strip_fences(raw_text))
        if not isinstance(parsed, dict) or "adjustments" not in parsed:
            raise ValueError("resposta não tem campo 'adjustments'")
        raw_adjs = parsed.get("adjustments", [])
        if not isinstance(raw_adjs, list):
            raise ValueError("'adjustments' não é lista")
        reasoning = str(parsed.get("overall_reasoning", ""))[:400]
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[self_heal] Parse falhou: {exc} | raw: {raw_text[:300]}", flush=True)
        _update_state("parse_error", [], [])
        _send_telegram(
            f"🤖 Self-Heal — resposta inválida\n{exc}\nRaw: {raw_text[:200]}"
        )
        return

    # ── Validação ─────────────────────────────────────────────────────────────
    valid, rejected = _validate_adjustments(raw_adjs, config)
    valid, batch_bad = _sanity_batch(valid, config)
    rejected.extend(batch_bad)
    print(
        f"[self_heal] Validação: {len(valid)} válido(s), {len(rejected)} rejeitado(s)",
        flush=True,
    )

    # ── Escrever suggested_config.json ────────────────────────────────────────
    suggested = {
        "generated_at":       _ts(),
        "based_on_audit":     generated_at,
        "gemini_model":       GEMINI_MODEL,
        "trades_analysed":    trades_closed,
        "adjustments":        valid,
        "rejected":           rejected,
        "overall_reasoning":  reasoning,
        "validation_status":  "passed" if not rejected else "partial",
        "apply_instructions": (
            "Correr o workflow 'apply-suggested-config' manualmente no GitHub Actions. "
            "NUNCA aplicar automaticamente sem aprovação humana."
        ),
    }
    try:
        _write_atomic(_SUGGESTED_PATH, suggested)
        print(f"[self_heal] suggested_config.json escrito → {_SUGGESTED_PATH}", flush=True)
    except Exception as exc:
        print(f"[self_heal] Falha ao escrever suggested_config.json: {exc}", flush=True)
        _update_state("write_error", [], [])
        return

    _update_state("ok", valid, rejected)

    # ── Telegram ──────────────────────────────────────────────────────────────
    try:
        _send_telegram(_build_telegram_msg(valid, rejected, reasoning, audit))
    except Exception as exc:
        print(f"[self_heal] Telegram falhou: {exc}", flush=True)

    print(
        f"[{_ts()}] === Self-Heal END ==="
        f" | válidos={len(valid)} | rejeitados={len(rejected)}",
        flush=True,
    )


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    run_self_healing()
