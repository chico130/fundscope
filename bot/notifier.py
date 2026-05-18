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

_PROJECT_ROOT = Path(__file__).parent.parent
_TELEGRAM_ERROR_LOG = _PROJECT_ROOT / "logs" / "errors" / "telegram_errors.json"


def _load_credentials() -> tuple[str | None, str | None]:
    """Resolve TOKEN/CHAT_ID a partir de env vars, com fallback .env opcional.

    Devolve (None, None) silenciosamente quando alguma das duas falta — o
    notifier passa a no-op nesse caso, sem crashar nem registar erro.
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
                pass  # python-dotenv não instalado — segue só com env vars

    return (token or None), (chat_id or None)


_TOKEN, _CHAT_ID = _load_credentials()
_BASE_URL = f"https://api.telegram.org/bot{_TOKEN}" if _TOKEN else None


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
        pass  # nunca pode crashar o bot


def enviar_alerta(mensagem: str, silencioso: bool = False) -> None:
    """Envia mensagem de texto para o Telegram.

    silencioso=True entrega a mensagem sem som/vibração — ideal para
    notificações de rotina que não requerem atenção imediata.

    Retry automático (3 tentativas, 5s entre cada) em falhas de rede.
    Rejeições da API Telegram (token inválido, chat_id errado) não são
    retentadas — são erros de configuração, não transitórios.
    Todas as falhas são persistidas em logs/errors/telegram_errors.json.
    Nunca lança excepção.

    No-op silencioso se TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não estiverem
    configurados — permite correr o bot localmente sem credenciais.
    """
    if not _BASE_URL or not _CHAT_ID:
        return

    import time as _time

    for attempt in range(3):
        try:
            r = requests.post(
                f"{_BASE_URL}/sendMessage",
                json={
                    "chat_id":              _CHAT_ID,
                    "text":                 mensagem,
                    "disable_notification": silencioso,
                },
                timeout=8,
            )
            data = r.json()
            if not data.get("ok"):
                # Rejeição da API — não retentar (problema de configuração)
                detail = r.text[:500]
                print(f"[notifier] API rejeitou alerta: {detail}")
                _log_telegram_error("api_rejection", detail)
            return
        except Exception as exc:  # noqa: BLE001
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
    """Notificação de início de sessão — primeiro ciclo do dia (≈13:00 UTC)."""
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
        f"Ciclos a cada 15 min até às 21:00 UTC."
    )
    enviar_alerta(texto, silencioso=True)


def enviar_boa_noite(report: dict) -> None:
    """Notificação de fim de sessão — último ciclo do dia (≈21:00 UTC)."""
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
        f"Até amanhã às 13:00 UTC."
    )
    enviar_alerta(texto)


def enviar_resumo_diario(dados_resumo: dict) -> None:
    """Envia o relatório diário formatado em Markdown.

    dados_resumo esperado:
        saldo            str  "10234.56"
        variacao         str  "+1.23" ou "-0.45"
        sinais_contagem  int
        ordens_contagem  int
        vetos_contagem   int
        poupanca         str  "150.00"
        regime           str  "bull_trending"
    """
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
