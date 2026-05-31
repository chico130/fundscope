#!/usr/bin/env python3
"""
Relatório de Segurança Semanal — em linguagem simples para o Francisco.
Corre às sextas-feiras ~21:30 UTC via GitHub Actions.
Verifica: chaves de acesso, erros, dados actualizados, estado geral.

Dedup via data/daily_flags.json — 1 envio por dia UTC.
Fail-open: erros nunca suprimem o relatório sem aviso.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=True)
except ImportError:
    pass


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _check_api_keys() -> dict[str, bool]:
    """Verifica quais chaves de acesso estão configuradas (presença, não conteúdo)."""
    return {
        "T212 (corretora)": bool(
            os.environ.get("T212_API_ID") and os.environ.get("T212_API_KEY")
        ),
        "Finnhub (dados de mercado)": bool(
            os.environ.get("FINNHUB_TOKEN") or os.environ.get("FINNHUB_API_KEY")
        ),
        "Telegram (alertas)": bool(
            os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
        ),
        "Gemini (análise IA)": bool(os.environ.get("GEMINI_API_KEY")),
    }


def _count_errors_in_days(n_days: int, offset: int = 0) -> int:
    """Conta entradas nos logs de erros num intervalo de dias."""
    errors_dir = _ROOT / "logs" / "errors"
    today = _now_utc().date()
    total = 0
    for i in range(offset, offset + n_days):
        d = today - timedelta(days=i)
        path = errors_dir / f"{d.isoformat()}.json"
        data = _read_json(path, [])
        if isinstance(data, list):
            total += len(data)
    return total


def _count_active_problems() -> int:
    """Conta circuit breakers e kill switches activos hoje via daily_flags.json."""
    flags = _read_json(_ROOT / "data" / "daily_flags.json", {})
    if not isinstance(flags, dict):
        return 0
    return sum(
        1 for k in flags
        if k.startswith("circuit_") or k.startswith("macro_kill_")
    )


def _check_data_freshness() -> tuple[float, bool]:
    """Devolve (horas_desde_ultimo_ciclo, está_fresco)."""
    status = _read_json(_ROOT / "data" / "beta" / "status.json", {})
    last_check = status.get("last_check", "") if isinstance(status, dict) else ""
    if not last_check:
        return 999.0, False
    try:
        dt = datetime.fromisoformat(last_check.replace("Z", "+00:00"))
        hours = (_now_utc() - dt).total_seconds() / 3600
        return round(hours, 1), hours < 3.0
    except (ValueError, TypeError):
        return 999.0, False


def build_security_report() -> str:
    # Chaves de acesso
    keys = _check_api_keys()
    all_present = all(keys.values())

    # Erros esta semana vs semana anterior
    errors_this_week = _count_errors_in_days(7, offset=0)
    errors_last_week = _count_errors_in_days(7, offset=7)

    if errors_last_week == 0 and errors_this_week == 0:
        errors_trend = "nenhum ✅"
    elif errors_this_week <= errors_last_week:
        errors_trend = f"{errors_this_week} (↓ melhor que semana passada)"
    else:
        diff = errors_this_week - errors_last_week
        errors_trend = f"{errors_this_week} (↑ {diff} a mais que semana passada)"

    # Problemas activos hoje
    problems_today = _count_active_problems()

    # Frescura dos dados
    hours_ago, is_fresh = _check_data_freshness()
    if hours_ago >= 999:
        freshness_str = "dados não encontrados ⚠️"
    elif is_fresh:
        freshness_str = f"{hours_ago:.1f} horas atrás ✅"
    else:
        freshness_str = f"{hours_ago:.0f} horas atrás ⚠️"

    # Conclusão geral
    problems: list[str] = []
    if not all_present:
        missing = [k for k, v in keys.items() if not v]
        problems.append(f"chaves em falta: {', '.join(missing)}")
    if errors_this_week > errors_last_week + 5:
        problems.append("erros críticos a aumentar")
    if problems_today > 0:
        problems.append(f"protecção activa ({problems_today}×)")
    if not is_fresh:
        problems.append(f"dados com {hours_ago:.0f}h sem actualizar")

    if not problems:
        conclusion = "Tudo em ordem ✅"
    elif all(p not in ("chaves em falta", "protecção activa") for p in [problems[0]]):
        conclusion = f"Atenção necessária ⚠️ — {'; '.join(problems)}"
    else:
        conclusion = f"Problema activo 🔴 — {'; '.join(problems)}"

    # Bloco de chaves
    keys_lines = [
        f"  {'✅' if v else '❌'} {k}" for k, v in keys.items()
    ]

    linhas = [
        "🔒 Estado de Segurança — FundScope",
        "",
        "Chaves de acesso:",
        *keys_lines,
        "",
        f"Erros críticos esta semana: {errors_trend}",
        f"Protecções activadas hoje: {problems_today}",
        f"Dados actualizados há: {freshness_str}",
        "",
        f"Conclusão: {conclusion}",
    ]
    return "\n".join(linhas)


def main() -> None:
    print(f"[{_ts()}] === Relatório de Segurança START ===", flush=True)
    try:
        from bot.notifier import _already_sent_today, _mark_sent_today, enviar_alerta

        if _already_sent_today("security_report_sent_date"):
            print(f"[{_ts()}] Relatório de segurança já enviado hoje — a saltar.", flush=True)
        else:
            msg = build_security_report()
            print(f"[{_ts()}] A enviar relatório de segurança via Telegram...", flush=True)
            enviar_alerta(msg, silencioso=False)
            _mark_sent_today("security_report_sent_date")
            print(f"[{_ts()}] Enviado com sucesso.", flush=True)

    except Exception as exc:
        import traceback
        print(f"[{_ts()}] ERRO no relatório de segurança: {exc}", flush=True)
        traceback.print_exc()

    print(f"[{_ts()}] === Relatório de Segurança END ===", flush=True)


if __name__ == "__main__":
    main()
