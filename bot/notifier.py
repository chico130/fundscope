"""
Notifier — alertas Telegram em tempo real.

Envia mensagens para o chat do Francisco via Telegram Bot API.
Falhas de rede ou API são silenciadas para não crashar o bot principal.
Erros persistidos em logs/errors/telegram_errors.json para diagnóstico.

Credenciais (NUNCA hardcoded — apenas env vars):
  • TELEGRAM_BOT_TOKEN  — token do bot @BotFather
  • TELEGRAM_CHAT_ID    — chat_id do destinatário

São lidas em três fases:
  1. os.environ (definidas pelo runner — GitHub Actions, Task Scheduler, shell)
  2. Fallback .env na raiz do projecto (via python-dotenv, se disponível)
  3. Se ainda assim faltarem → notifier silencia (nenhum envio, sem crash)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .market_hours import market_close_label_utc, market_open_label_utc

_PROJECT_ROOT = Path(__file__).parent.parent
_TELEGRAM_ERROR_LOG = _PROJECT_ROOT / "logs" / "errors" / "telegram_errors.json"
_DAILY_FLAGS_PATH   = _PROJECT_ROOT / "data" / "daily_flags.json"


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_daily_flags() -> dict:
    try:
        raw = json.loads(_DAILY_FLAGS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _already_sent_today(flag: str) -> bool:
    """True se a flag foi marcada com a data UTC de hoje.

    Reset implícito à meia-noite UTC: se a data armazenada != hoje, devolve False.
    """
    return _read_daily_flags().get(flag) == _today_utc()


def _mark_sent_today(flag: str) -> None:
    """Marca a flag com a data UTC de hoje. Escrita atómica (tmp + rename)."""
    try:
        _DAILY_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        flags = _read_daily_flags()
        flags[flag] = _today_utc()
        tmp = _DAILY_FLAGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(flags, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_DAILY_FLAGS_PATH)
    except OSError as exc:
        print(f"[notifier] AVISO: falha a escrever daily_flags.json: {exc}")


def _load_credentials() -> tuple[str | None, str | None]:
    """Resolve TOKEN/CHAT_ID a partir de env vars, com fallback .env opcional.

    NOTA: chamada em cada envio (lazy) — garante que credenciais injectadas
    pelo GitHub Actions depois do import são sempre apanhadas.
    """
    token   = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")   or ""

    if not token or not chat_id:
        env_file = _PROJECT_ROOT / ".env"
        if env_file.exists():
            try:
                from dotenv import dotenv_values
                values = dotenv_values(env_file) or {}
                token   = token   or (values.get("TELEGRAM_BOT_TOKEN") or "")
                chat_id = chat_id or (values.get("TELEGRAM_CHAT_ID")   or "")
            except ImportError:
                pass

    if not token:
        print("[notifier] AVISO: TELEGRAM_BOT_TOKEN não configurado — envios desactivados.")
    if not chat_id:
        print("[notifier] AVISO: TELEGRAM_CHAT_ID não configurado — envios desactivados.")

    return (token or None), (chat_id or None)


def _log_telegram_error(kind: str, detail: str) -> None:
    """Persiste erros de Telegram em logs/errors/telegram_errors.json."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind":      kind,
        "detail":    detail,
    }
    try:
        _TELEGRAM_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if _TELEGRAM_ERROR_LOG.exists():
            try:
                existing = json.loads(_TELEGRAM_ERROR_LOG.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except (json.JSONDecodeError, OSError):
                existing = []
        existing.append(entry)
        _TELEGRAM_ERROR_LOG.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def enviar_alerta(mensagem: str, silencioso: bool = False) -> None:
    """Envia mensagem de texto para o Telegram.

    Credenciais lidas de forma lazy a cada chamada — funciona mesmo quando
    as env vars são injectadas após o import (GitHub Actions).

    Retry automático (3 tentativas, 5s entre cada) em falhas de rede.
    Rejeições da API (token/chat_id inválidos) não são retentadas.
    Nunca lança excepção.
    """
    try:
        from . import rate_limiter as _rl
        if not _rl.check_and_consume("telegram"):
            print(
                f"[notifier] Telegram rate limit reached — dropping: {mensagem[:80]}",
                flush=True,
            )
            return
    except Exception:
        pass  # rate_limiter failure must never suppress notifications

    token, chat_id = _load_credentials()
    if not token or not chat_id:
        return

    base_url = f"https://api.telegram.org/bot{token}"

    import time as _time

    for attempt in range(3):
        try:
            r = requests.post(
                f"{base_url}/sendMessage",
                json={
                    "chat_id":              chat_id,
                    "text":                 mensagem,
                    "disable_notification": silencioso,
                },
                timeout=8,
            )
            data = r.json()
            if not data.get("ok"):
                detail = r.text[:500]
                print(f"[notifier] API rejeitou alerta (HTTP {r.status_code}): {detail}")
                _log_telegram_error("api_rejection", detail)
            else:
                print(f"[notifier] Alerta enviado com sucesso (tentativa {attempt + 1}).")
            return
        except Exception as exc:
            if attempt < 2:
                print(f"[notifier] Rede falhou (tentativa {attempt + 1}/3): {exc} — a aguardar 5s")
                _time.sleep(5)
            else:
                detail = str(exc)
                print(f"[notifier] Falha ao enviar alerta Telegram após 3 tentativas: {detail}")
                _log_telegram_error("network_error", detail)


def enviar_oportunidade(oportunidades: list[dict], regime: str) -> None:
    """Alerta sonoro quando o Clyde detecta sinais de entrada na watchlist."""
    if not oportunidades:
        return

    linhas = []
    for o in oportunidades[:3]:
        tech  = o.get("technicals", {})
        rsi   = tech.get("rsi_14")
        vol   = tech.get("volume_ratio_vs_avg")
        price = o.get("last_price")
        forca = round(o.get("signal_strength", 0) * 100)

        meta = []
        if price:
            meta.append(f"${price:.2f}")
        meta.append(f"força {forca}%")
        if rsi is not None:
            meta.append(f"RSI={rsi:.1f}")
        if vol is not None:
            meta.append(f"vol={vol:.1f}×")

        linhas.append(f"• {o['ticker']}  {' · '.join(meta)}")
        for r in o.get("reasons", [])[:1]:
            linhas.append(f"  {r}")

    n     = len(oportunidades)
    extra = f"\n+{n - 3} mais sinais detectados" if n > 3 else ""

    texto = (
        f"🚀 Novo Sinal — Clyde\n"
        f"{regime}\n"
        f"\n"
        f"{chr(10).join(linhas)}"
        f"{extra}"
    )
    enviar_alerta(texto, silencioso=False)


def enviar_despertar(report: dict) -> None:
    """Notificação de início de sessão — primeiro ciclo do dia (≈13:00 UTC).

    Dedup persistente via data/daily_flags.json — só envia uma vez por dia UTC,
    independentemente de quantas vezes for chamada ou de restarts do processo.
    """
    if _already_sent_today("wake_sent_date"):
        return

    regime   = report.get("regime", "?")
    n_pos    = report.get("n_positions", 0)
    equity   = report.get("risk_status", {}).get("total_equity_eur", 0)
    opps     = len(report.get("buy_opportunities", []))
    bloqueado = report.get("regime_alert", False)

    regime_label = {
        "bull_trending":     "Bull Trending",
        "bull_lateral":      "Bull Lateral",
        "bear_correction":   "Bear Correction",
        "bear_capitulation": "Bear Capitulation",
    }.get(regime, regime)

    opps_str = f"{opps} oportunidade(s) detectada(s)" if opps else "Sem sinais de entrada agora"
    entradas = "⛔ Entradas bloqueadas — regime bear" if bloqueado else "Entradas abertas"

    texto = (
        f"☀️ Bom dia, Francisco\n"
        f"\n"
        f"Mercados EUA abrem em ~30 min.\n"
        f"\n"
        f"• Regime: {regime_label}\n"
        f"• Equity demo: €{equity:,.2f}\n"
        f"• Posições: {n_pos}\n"
        f"• {opps_str}\n"
        f"• {entradas}\n"
        f"\n"
        f"Ciclos a cada 15 min até às {market_close_label_utc()}."
    )
    enviar_alerta(texto, silencioso=True)
    _mark_sent_today("wake_sent_date")


def enviar_boa_noite(report: dict) -> None:
    """Notificação de fim de sessão — último ciclo do dia (fecho NYSE em UTC).

    Dedup persistente via data/daily_flags.json — só envia uma vez por dia UTC.
    """
    if _already_sent_today("sleep_sent_date"):
        return

    regime   = report.get("regime", "?")
    n_pos    = report.get("n_positions", 0)
    opps     = len(report.get("buy_opportunities", []))
    nm       = len(report.get("near_misses", []))
    cro_data = report.get("cro", {})
    rf       = cro_data.get("risk_factor", 1.0)
    wr       = cro_data.get("win_rate_7d", 0.0)
    risk_ok  = report.get("risk_status", {}).get("ok", True)
    warnings = report.get("risk_status", {}).get("warnings", [])

    regime_label = {
        "bull_trending":     "Bull Trending",
        "bull_lateral":      "Bull Lateral",
        "bear_correction":   "Bear Correction",
        "bear_capitulation": "Bear Capitulation",
    }.get(regime, regime)

    avisos = "\n" + "\n".join(f"⚠️ {w}" for w in warnings) if not risk_ok else ""

    texto = (
        f"🌙 Resumo da Sessão\n"
        f"\n"
        f"• Regime: {regime_label}\n"
        f"• Posições activas: {n_pos}\n"
        f"• Sinais detectados: {opps}\n"
        f"• Near-misses: {nm}\n"
        f"\n"
        f"CRO: risk factor {rf:.2f}×  ·  win rate {wr:.1f}%"
        f"{avisos}\n"
        f"\n"
        f"Até amanhã às {market_open_label_utc()}."
    )
    enviar_alerta(texto)
    _mark_sent_today("sleep_sent_date")


def enviar_resumo_diario(dados_resumo: dict) -> None:
    """Envia o relatório diário formatado em Markdown.

    Dedup persistente via data/daily_flags.json — só envia uma vez por dia UTC.

    dados_resumo esperado:
        saldo            str  "10234.56"
        variacao         str  "+1.23" ou "-0.45"
        sinais_contagem  int
        ordens_contagem  int
        vetos_contagem   int
        poupanca         str  "150.00"
        regime           str  "bull_trending"
    """
    if _already_sent_today("daily_summary_sent_date"):
        return

    saldo    = str(dados_resumo.get("saldo", "N/D"))
    variacao = str(dados_resumo.get("variacao", "N/D"))
    sinais   = dados_resumo.get("sinais_contagem", 0)
    ordens   = dados_resumo.get("ordens_contagem", 0)
    vetos    = dados_resumo.get("vetos_contagem", 0)
    poupanca = str(dados_resumo.get("poupanca", "0.00"))
    regime   = str(dados_resumo.get("regime", "desconhecido"))

    texto = (
        f"🤫 Whisper · Resumo Diário\n"
        f"\n"
        f"💰 Portfólio\n"
        f"• Saldo: €{saldo}\n"
        f"• Performance: {variacao}%\n"
        f"\n"
        f"📈 Clyde\n"
        f"• Sinais: {sinais}  ·  Ordens: {ordens}\n"
        f"\n"
        f"🛡 Bonnie\n"
        f"• Vetos: {vetos}  ·  Poupança estimada: €{poupanca}\n"
        f"\n"
        f"regime: {regime}"
    )

    enviar_alerta(texto, silencioso=True)
    _mark_sent_today("daily_summary_sent_date")


def enviar_healthcheck(minutes_until_next: int | None = None) -> None:
    """Health check diário (~12:50 UTC): confirma que o bot está vivo antes do
    primeiro ciclo, mesmo que não haja sinais.

    Dedup persistente via daily_flags.json — só envia uma vez por dia UTC.
    Não faz chamadas externas: lê apenas o último status.json em disco.
    """
    if _already_sent_today("healthcheck_sent_date"):
        return

    if minutes_until_next is None:
        prox = "em breve"
    else:
        prox = f"em {minutes_until_next} min"

    last_check = "?"
    try:
        status_path = _PROJECT_ROOT / "data" / "beta" / "status.json"
        raw = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            last_check = raw.get("last_check", "?")
    except (OSError, json.JSONDecodeError):
        pass

    texto = (
        f"✅ Bot activo — próximo ciclo {prox}\n"
        f"\n"
        f"FundScope vivo e a postos para a sessão de hoje.\n"
        f"Último ciclo registado: {last_check}"
    )
    enviar_alerta(texto, silencioso=True)
    _mark_sent_today("healthcheck_sent_date")


def enviar_trade_executada(result: dict, modo: str = "phase1_auto") -> None:
    """Alerta Telegram imediato após execução confirmada de um trade (BUY/SELL)."""
    try:
        side   = (result.get("side") or "?").upper()
        ticker = result.get("ticker") or "?"
        qty    = result.get("qty")
        price  = result.get("price")
        reason = (result.get("reason") or "").strip()
        ts_raw = result.get("datetime") or result.get("timestamp")

        if side == "BUY":
            side_emoji, arrow_emoji = "🟢", "📈"
        elif side == "SELL":
            side_emoji, arrow_emoji = "🔴", "📉"
        else:
            side_emoji, arrow_emoji = "⚪", "↔️"

        try:
            qty_s = f"{float(qty):g}" if qty is not None else "?"
        except (TypeError, ValueError):
            qty_s = str(qty)

        try:
            price_s = f" @ ${float(price):.2f}" if price is not None else ""
        except (TypeError, ValueError):
            price_s = ""

        ts_str = None
        if ts_raw:
            try:
                ts_dt = ts_raw if isinstance(ts_raw, datetime) else \
                        datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                ts_str = ts_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except (ValueError, TypeError):
                ts_str = None
        if ts_str is None:
            ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        linhas = [
            f"{side_emoji} {side} executado [{modo}]",
            f"{arrow_emoji} {ticker} — {qty_s} acções{price_s}",
        ]
        if reason:
            linhas.append(f"💡 {reason}")
        linhas.append(f"🕐 {ts_str}")

        enviar_alerta("\n".join(linhas), silencioso=False)

    except Exception as exc:
        print(f"[notifier] Falha em enviar_trade_executada: {exc}")
        _log_telegram_error("trade_format_error", f"{type(exc).__name__}: {exc}")
