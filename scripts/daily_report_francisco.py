#!/usr/bin/env python3
"""
Relatório Diário em linguagem simples para o Francisco.
Enviado via Telegram após o fecho da sessão NYSE (~21:15 UTC, dias úteis).

Dedup via data/daily_flags.json — 1 envio por dia UTC.
Fail-open: erros nunca chegam ao utilizador sem mensagem.
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


def _regime_to_market_state(regime: str, macro_mode: str) -> str:
    if macro_mode == "total_kill":
        return "Muito agitado"
    if macro_mode in ("cash_is_king", "caution"):
        return "Agitado"
    return {
        "bull_trending":     "Calmo",
        "bull_lateral":      "Lateral (sem tendência clara)",
        "bear_correction":   "Em queda",
        "bear_capitulation": "Em queda forte",
    }.get(regime, "Desconhecido")


def _regime_to_bot_state(regime: str, macro_mode: str, bot_status: str) -> str:
    if bot_status == "error":
        return "Com problema — verificar erros"
    if macro_mode == "total_kill":
        return "Em pausa (mercado muito volátil)"
    if macro_mode in ("cash_is_king", "caution"):
        return "Em modo cauteloso"
    if "bear" in regime:
        return "Em modo cauteloso (sem novas compras)"
    if bot_status == "active":
        return "A funcionar normalmente"
    return "Estado desconhecido"


def _get_portfolio_value(portfolio: dict) -> float | None:
    try:
        for key in ("total", "totalValue", "total_value", "equity", "total_equity_eur"):
            v = portfolio.get(key)
            if v is not None:
                return float(v)
        free = float(portfolio.get("free", 0) or 0)
        invested = float(portfolio.get("invested", 0) or 0)
        if free or invested:
            return free + invested
    except (TypeError, ValueError):
        pass
    return None


def _get_equity_yesterday(equity_history: list) -> float | None:
    try:
        yesterday = (_now_utc() - timedelta(days=1)).date()
        candidates = []
        for e in equity_history:
            dt_s = e.get("datetime", "")
            if not dt_s:
                continue
            dt = datetime.fromisoformat(str(dt_s).replace("Z", "+00:00"))
            if dt.date() <= yesterday:
                candidates.append((dt, float(e["equity"])))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    except Exception:
        return None


def _load_today_trades() -> list[dict]:
    data = _read_json(_ROOT / "diario_trades.json", None)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("trades", [])
    return []


def _count_trades(trades: list[dict]) -> tuple[int, int, float]:
    today_str = _now_utc().strftime("%Y-%m-%d")
    buys = sells = 0
    pnl = 0.0
    for t in trades:
        ts = str(t.get("datetime", t.get("timestamp", t.get("closed_at", ""))))
        if today_str not in ts:
            continue
        side = str(t.get("side", "")).upper()
        if side == "BUY":
            buys += 1
        elif side == "SELL":
            sells += 1
            result = t.get("result_eur") or t.get("pnl") or 0
            try:
                pnl += float(result)
            except (TypeError, ValueError):
                pass
    return buys, sells, pnl


def _build_notable_events(analysis: dict, macro: dict) -> list[str]:
    events = []
    mode = macro.get("macro_mode", "normal")
    if mode == "total_kill":
        events.append("O bot está parado porque o mercado está muito volátil")
    elif mode in ("cash_is_king", "caution"):
        events.append("Bot em modo cauteloso — volatilidade elevada")
    regime = analysis.get("regime", "")
    if "bear" in regime and mode not in ("total_kill", "cash_is_king", "caution"):
        events.append("Mercado em queda — sem novas compras até recuperar")
    return events


def build_message() -> str:
    now_str = _now_utc().strftime("%d/%m/%Y")

    # Portfolio value
    portfolio = _read_json(_ROOT / "portfolio.json")
    if not portfolio:
        portfolio = _read_json(_ROOT / "data" / "beta" / "portfolio.json")
    pv = _get_portfolio_value(portfolio) if isinstance(portfolio, dict) else None

    # Daily variation vs yesterday
    equity_raw = _read_json(_ROOT / "data" / "beta" / "beta_equity.json")
    equity_history = equity_raw.get("history", []) if isinstance(equity_raw, dict) else []
    prev_val = _get_equity_yesterday(equity_history)

    if pv is not None and prev_val is not None and prev_val > 0:
        diff_eur = pv - prev_val
        diff_pct = diff_eur / prev_val * 100
        arrow = "↑" if diff_eur >= 0 else "↓"
        sinal = "+" if diff_eur >= 0 else ""
        var_str = f"{arrow} {sinal}{diff_pct:.1f}% ({sinal}€{abs(diff_eur):.2f})"
        carteira_str = f"€{pv:,.2f} ({var_str})"
    elif pv is not None:
        carteira_str = f"€{pv:,.2f}"
    else:
        carteira_str = "N/D"

    # Bot status + regime
    status = _read_json(_ROOT / "data" / "beta" / "status.json")
    if not isinstance(status, dict):
        status = {}
    regime = status.get("regime", "")
    bot_status = status.get("bot_status", "")

    # Macro context
    macro: dict = {}
    try:
        from bot.macro_sensor import get_macro_context
        macro = get_macro_context()
    except Exception:
        cached = _read_json(_ROOT / "data" / "macro_cache.json")
        macro = cached if isinstance(cached, dict) else {}

    macro_mode = macro.get("macro_mode", "normal")

    # Today's trades
    trades = _load_today_trades()
    buys, sells, day_pnl = _count_trades(trades)

    if buys == 0 and sells == 0:
        trades_str = "Nenhum trade hoje"
    else:
        parts = []
        if buys > 0:
            parts.append(f"{buys} compra{'s' if buys > 1 else ''}")
        if sells > 0:
            parts.append(f"{sells} venda{'s' if sells > 1 else ''}")
        trades_str = " e ".join(parts)

    if sells > 0:
        sinal_pnl = "+" if day_pnl >= 0 else ""
        resultado_str = f"{sinal_pnl}€{day_pnl:.2f}"
    else:
        resultado_str = "—"

    mercado = _regime_to_market_state(regime, macro_mode)
    bot_state = _regime_to_bot_state(regime, macro_mode, bot_status)

    # Notable events
    analysis = _read_json(_ROOT / "data" / "beta" / "beta_analysis.json")
    notable = _build_notable_events(
        analysis if isinstance(analysis, dict) else {},
        macro,
    )

    linhas = [
        "📋 Resumo do Dia — FundScope",
        "",
        f"💰 Carteira: {carteira_str}",
        f"📈 Trades hoje: {trades_str}",
        f"✅ Resultado do dia: {resultado_str}",
        "",
        f"Estado do mercado: {mercado}",
        f"O bot está: {bot_state}",
    ]

    if notable:
        linhas.append("")
        for ev in notable[:2]:
            linhas.append(f"⚠️ {ev}")

    return "\n".join(linhas)


def main() -> None:
    print(f"[{_ts()}] === Relatório Diário Francisco START ===", flush=True)
    try:
        from bot.notifier import _already_sent_today, _mark_sent_today, enviar_alerta

        if _already_sent_today("daily_report_francisco_sent_date"):
            print(f"[{_ts()}] Relatório já enviado hoje — a saltar.", flush=True)
        else:
            msg = build_message()
            print(f"[{_ts()}] A enviar relatório via Telegram...", flush=True)
            enviar_alerta(msg, silencioso=False)
            _mark_sent_today("daily_report_francisco_sent_date")
            print(f"[{_ts()}] Enviado com sucesso.", flush=True)

    except Exception as exc:
        import traceback
        print(f"[{_ts()}] ERRO no relatório diário: {exc}", flush=True)
        traceback.print_exc()

    print(f"[{_ts()}] === Relatório Diário Francisco END ===", flush=True)


if __name__ == "__main__":
    main()
