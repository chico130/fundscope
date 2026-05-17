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
    for o in oportunidades[:3]:   # máximo 3 para não spam
        tech  = o.get("technicals", {})
        rsi   = tech.get("rsi_14")
        vol   = tech.get("volume_ratio_vs_avg")
        price = o.get("last_price")
        forca = round(o.get("signal_strength", 0) * 100)

        linha = f"· {o['ticker']} [{o.get('sector','?')}]"
        if price:
            linha += f"  ${price:.2f}"
        linha += f"  forca={forca}%"
        if rsi is not None:
            linha += f"  RSI={rsi:.1f}"
        if vol is not None:
            linha += f"  Vol={vol:.1f}x"
        linhas.append(linha)
        for r in o.get("reasons", [])[:1]:
            linhas.append(f"  {r}")

    n     = len(oportunidades)
    extra = f" (+{n - 3} mais)" if n > 3 else ""

    texto = (
        f"SINAL DE ENTRADA DETECTADO\n"
        f"──────────────────────────\n"
        f"Clyde encontrou {n} oportunidade(s){extra}:\n"
        f"\n"
        f"{chr(10).join(linhas)}\n"
        f"\n"
        f"Regime: {regime} | Fase 0 - so leitura, sem ordens."
    )
    enviar_alerta(texto, silencioso=False)   # com som


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

    opps_str = f"Oportunidades detectadas: {opps}" if opps else "Sem sinais de entrada neste momento."
    alerta   = "ENTRADAS BLOQUEADAS (regime bear)" if bloqueado else "Entradas abertas"

    texto = (
        f"BOM DIA, FRANCISCO\n"
        f"──────────────────────────\n"
        f"Clyde acordou · Mercados EUA abrem em ~30 min\n"
        f"\n"
        f"Estado inicial:\n"
        f"· Regime: {regime_label}\n"
        f"· {alerta}\n"
        f"· Posicoes: {n_pos}  |  Equity Demo: EUR {equity:,.2f}\n"
        f"· {opps_str}\n"
        f"\n"
        f"Ciclos automaticos a cada 30 min ate as 21:00 UTC."
    )
    enviar_alerta(texto)


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

    status = "Sessao sem incidentes." if risk_ok else f"AVISOS de risco:\n" + "\n".join(f"  · {w}" for w in warnings)

    texto = (
        f"BOA NOITE, FRANCISCO\n"
        f"──────────────────────────\n"
        f"Clyde vai dormir · Resumo da sessao de hoje:\n"
        f"\n"
        f"Estado final:\n"
        f"· Regime: {regime_label}\n"
        f"· Posicoes activas: {n_pos}\n"
        f"· Oportunidades detectadas: {opps}\n"
        f"· Near-misses monitorizados: {nm}\n"
        f"\n"
        f"CRO - Factor de risco: {rf:.2f}x  |  Win rate 7d: {wr:.1f}%\n"
        f"\n"
        f"{status}\n"
        f"Ate amanha as 13:00 UTC."
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
    saldo = str(dados_resumo.get("saldo", "N/D"))
    variacao = str(dados_resumo.get("variacao", "N/D"))
    sinais = dados_resumo.get("sinais_contagem", 0)
    ordens = dados_resumo.get("ordens_contagem", 0)
    vetos = dados_resumo.get("vetos_contagem", 0)
    poupanca = str(dados_resumo.get("poupanca", "0.00"))
    regime = str(dados_resumo.get("regime", "desconhecido"))

    sep = "─" * 26

    texto = (
        f"\U0001f933 WHISPER • RELATÓRIO DIÁRIO DO MERCADO \U0001f933\n"
        f"{sep}\n"
        f"💰 EVOLUÇÃO DO PORTFÓLIO\n"
        f"• Saldo Atual: ${saldo}\n"
        f"• Performance Hoje: {variacao}%\n"
        f"\n"
        f"📈 ATIVIDADE DO CLYDE\n"
        f"• Sinais Identificados: {sinais}\n"
        f"• Ordens Executadas: {ordens}\n"
        f"\n"
        f"🛡 ESCUDO DA BONNIE\n"
        f"• Vetos de Risco Aplicados: {vetos}\n"
        f"• Prejuízo Estimado Evitado: ${poupanca}\n"
        f"{sep}\n"
        f"Motor FundScope • regime: {regime}"
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
