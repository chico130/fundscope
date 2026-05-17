"""
Notifier — alertas Telegram em tempo real.

Envia mensagens para o chat do Francisco via Telegram Bot API.
Falhas de rede ou API são silenciadas para não crashar o bot principal.
"""
from __future__ import annotations

import requests

_TOKEN = "8656968418:AAF9JuNAJHOymk8f-wYzKF1aP6TK6DowDWk"
_CHAT_ID = "5862916306"
_BASE_URL = f"https://api.telegram.org/bot{_TOKEN}"
_COVER_URL = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500"


def enviar_alerta(mensagem: str, silencioso: bool = False) -> None:
    """Envia mensagem de texto para o Telegram.

    silencioso=True entrega a mensagem sem som/vibração — ideal para
    notificações de rotina que não requerem atenção imediata.
    Nunca lança excepção.
    """
    try:
        r = requests.post(
            f"{_BASE_URL}/sendMessage",
            json={
                "chat_id": _CHAT_ID,
                "text": mensagem,
                "disable_notification": silencioso,
            },
            timeout=5,
        )
        if not r.json().get("ok"):
            print(f"[notifier] API rejeitou alerta: {r.text}")
    except Exception as exc:  # noqa: BLE001
        print(f"[notifier] Falha ao enviar alerta Telegram: {exc}")


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

    try:
        r = requests.post(
            f"{_BASE_URL}/sendMessage",
            json={
                "chat_id": _CHAT_ID,
                "text": texto,
                "disable_notification": True,
            },
            timeout=10,
        )
        if not r.json().get("ok"):
            print(f"[notifier] API rejeitou resumo diário: {r.text}")
    except Exception as exc:
        print(f"[notifier] Falha ao enviar resumo diário: {exc}")
