#!/usr/bin/env python3
"""
Scout — Daily Briefing
Resumo diário da watchlist: top 5 tickers, notícias Finnhub, earnings próximos,
eventos macro e estado do mercado. Enviado por email às 13:30 UTC dias úteis.

Credenciais (.env ou GitHub Secrets):
  SMTP_HOST        gmail.com (default: smtp.gmail.com)
  SMTP_PORT        587 (default)
  SMTP_USER        remetente Gmail
  SMTP_PASS        App Password do Gmail
  BRIEFING_EMAIL   destinatário
  FINNHUB_TOKEN    token Finnhub (partilhado com bot)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  para alerta de falha (opcional)
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env", override=True)
except ImportError:
    pass

import requests

# --- Credenciais ---
_FINNHUB_TOKEN  = os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_TOKEN", "")
_SMTP_HOST      = os.environ.get("SMTP_HOST", "smtp.gmail.com")
_SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER      = os.environ.get("SMTP_USER", "")
_SMTP_PASS      = os.environ.get("SMTP_PASS", "")
_BRIEFING_EMAIL = os.environ.get("BRIEFING_EMAIL", "")

# --- Paths ---
_BETA_DIR      = _ROOT / "data" / "beta"
_ANALYSIS_PATH = _BETA_DIR / "beta_analysis.json"
_REGIME_PATH   = _BETA_DIR / "regime.json"
_EARNINGS_PATH = _ROOT / "earnings.json"
_BLOCKED_PATH  = _ROOT / "data" / "blocked_tickers.json"
_CAL_FALLBACK  = _ROOT / "data" / "macro_calendar.json"

# Rate limit Finnhub news: máximo 5 chamadas por briefing (1 por ticker top 5)
_MAX_NEWS_CALLS = 5


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[briefing] AVISO: falha a ler {path.name}: {exc}", flush=True)
        return {}


# ---------------------------------------------------------------------------
# Fontes de dados externas
# ---------------------------------------------------------------------------

def _fetch_finnhub_news(ticker: str) -> dict | None:
    """Notícia mais recente das últimas 24h via Finnhub company-news.
    Devolve dict {title, url, source, datetime} ou None em falha.
    Chamado no máximo _MAX_NEWS_CALLS vezes por run.
    """
    if not _FINNHUB_TOKEN:
        return None
    try:
        today     = _now_utc().strftime("%Y-%m-%d")
        yesterday = (_now_utc() - timedelta(days=1)).strftime("%Y-%m-%d")
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": yesterday, "to": today, "token": _FINNHUB_TOKEN},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        articles = r.json()
        if not isinstance(articles, list) or not articles:
            return None
        articles.sort(key=lambda a: a.get("datetime", 0), reverse=True)
        a = articles[0]
        return {
            "title":    a.get("headline", "")[:120],
            "url":      a.get("url", ""),
            "source":   a.get("source", ""),
            "datetime": a.get("datetime", 0),
        }
    except Exception as exc:
        print(f"[briefing] news fetch falhou para {ticker}: {exc}", flush=True)
        return None


def _fetch_macro_events() -> list[dict]:
    """Eventos macro das próximas 48h via Finnhub /calendar/economic.
    Filtra impacto alto (Fed/FOMC/CPI/NFP/PCE/GDP).
    Fallback: data/macro_calendar.json se endpoint falhar.
    """
    if not _FINNHUB_TOKEN:
        return _macro_calendar_fallback()
    try:
        today = _now_utc().strftime("%Y-%m-%d")
        in_2d = (_now_utc() + timedelta(days=2)).strftime("%Y-%m-%d")
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": today, "to": in_2d, "token": _FINNHUB_TOKEN},
            timeout=8,
        )
        if r.status_code != 200:
            return _macro_calendar_fallback()
        data   = r.json()
        events = (data.get("economicCalendar", []) if isinstance(data, dict) else []) or []
        if not isinstance(events, list):
            return _macro_calendar_fallback()

        _HIGH    = {"high", "3"}
        _KW      = ("fed", "fomc", "cpi", "nfp", "pce", "gdp", "interest rate", "payroll", "inflation")
        filtered = []
        for ev in events:
            impact = str(ev.get("impact", "")).lower()
            name   = str(ev.get("event", "")).lower()
            if impact in _HIGH or any(k in name for k in _KW):
                filtered.append({
                    "event":   ev.get("event", ""),
                    "date":    str(ev.get("time", ""))[:10],
                    "country": ev.get("country", ""),
                })
        return filtered[:5]
    except Exception as exc:
        print(f"[briefing] macro calendar falhou: {exc}", flush=True)
        return _macro_calendar_fallback()


def _macro_calendar_fallback() -> list[dict]:
    """Lê data/macro_calendar.json como fallback de eventos macro."""
    try:
        if not _CAL_FALLBACK.exists():
            return []
        cal    = json.loads(_CAL_FALLBACK.read_text(encoding="utf-8"))
        events = cal.get("events", [])
        now    = _now_utc()
        out    = []
        for ev in events:
            try:
                ev_dt = datetime.fromisoformat(ev.get("date", ""))
                delta = ev_dt.replace(tzinfo=timezone.utc) - now
                if timedelta(0) <= delta <= timedelta(days=2):
                    out.append(ev)
            except (ValueError, AttributeError):
                pass
        return out[:5]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Dados locais
# ---------------------------------------------------------------------------

def _load_blocked_active() -> list[dict]:
    """Lê blocked_tickers.json e devolve apenas entradas não-expiradas. Fail-open."""
    data = _read_json(_BLOCKED_PATH)
    if not isinstance(data, dict):
        return []
    blocked = data.get("blocked", [])
    if not isinstance(blocked, list):
        return []
    now = _now_utc()
    active = []
    for entry in blocked:
        expires = entry.get("expires_at")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if now >= exp_dt:
                    continue
            except (ValueError, TypeError):
                pass
        active.append(entry)
    return active


def _load_upcoming_earnings(days: int = 3) -> list[dict]:
    """Earnings dentro de N dias a partir de hoje (inclusive)."""
    data = _read_json(_EARNINGS_PATH)
    if not isinstance(data, dict):
        return []
    today  = _now_utc().date()
    cutoff = today + timedelta(days=days)
    result = []
    for e in data.get("earnings", []):
        try:
            e_date = datetime.strptime(e.get("data", ""), "%Y-%m-%d").date()
            if today <= e_date <= cutoff:
                result.append({
                    "ticker":      e.get("ticker", ""),
                    "nome":        e.get("nome", ""),
                    "data":        e.get("data", ""),
                    "days_until":  (e_date - today).days,
                })
        except (ValueError, TypeError):
            pass
    return sorted(result, key=lambda x: x["days_until"])


# ---------------------------------------------------------------------------
# Compilação do digest
# ---------------------------------------------------------------------------

def build_digest() -> dict:
    """Lê todos os dados e devolve um dict renderizável. Não lança excepções."""
    print(f"[{_ts()}] Scout — build_digest START", flush=True)

    analysis = _read_json(_ANALYSIS_PATH)
    if not isinstance(analysis, dict):
        analysis = {}

    # Regime
    regime_raw = analysis.get("regime", "")
    if not regime_raw:
        rj = _read_json(_REGIME_PATH)
        regime_raw = (rj.get("regime", "unknown") if isinstance(rj, dict) else "unknown")

    # Macro context — reutiliza macro_sensor.py do bot
    macro: dict = {}
    try:
        from bot.macro_sensor import get_macro_context
        macro = get_macro_context()
        print(f"[{_ts()}] macro: VIX={macro.get('vix')} mode={macro.get('macro_mode')}", flush=True)
    except Exception as exc:
        print(f"[{_ts()}] macro_sensor falhou: {exc}", flush=True)

    # Top 5 watchlist (já ordenada por score em beta_analysis)
    watchlist_top5: list[dict] = analysis.get("watchlist_top5", [])[:5]

    # Enriquecimento com technicals de near_misses/buy_opportunities
    techmap: dict[str, dict] = {}
    for entry in analysis.get("near_misses", []) + analysis.get("buy_opportunities", []):
        t = entry.get("ticker", "")
        if t and "technicals" in entry:
            techmap[t] = entry["technicals"]

    tickers: list[dict] = []
    for wl in watchlist_top5:
        ticker = wl.get("ticker", "")
        tech   = techmap.get(ticker, {})
        tickers.append({
            "ticker":    ticker,
            "sector":    wl.get("sector", ""),
            "price":     wl.get("price"),
            "score":     round(wl.get("score", 0) * 100),
            "mom_1m":    round(wl.get("mom_1m", 0) * 100, 1),
            "mom_3m":    round(wl.get("mom_3m", 0) * 100, 1),
            "rsi":       tech.get("rsi_14"),
            "vol_ratio": tech.get("volume_ratio_vs_avg"),
            "news":      None,
        })

    # Finnhub news — máximo _MAX_NEWS_CALLS chamadas
    print(f"[{_ts()}] a buscar notícias para {len(tickers)} tickers...", flush=True)
    for i, t in enumerate(tickers):
        if i >= _MAX_NEWS_CALLS:
            break
        time.sleep(0.6)  # espaçamento conservador (60 req/min free tier)
        t["news"] = _fetch_finnhub_news(t["ticker"])
        status = "ok" if t["news"] else "sem news"
        print(f"[{_ts()}]   {t['ticker']}: {status}", flush=True)

    upcoming_earnings = _load_upcoming_earnings(days=3)
    macro_events      = _fetch_macro_events()
    blocked_active    = _load_blocked_active()

    # Evitar hoje: earnings ≤2 dias + bloqueados manualmente
    earnings_set = {e["ticker"] for e in upcoming_earnings}
    avoid: list[dict] = []
    for e in upcoming_earnings:
        if e["days_until"] <= 2:
            suffix = {0: "hoje", 1: "amanhã"}.get(e["days_until"], f"em {e['days_until']} dias")
            avoid.append({"ticker": e["ticker"], "reason": f"Earnings {suffix} — {e['nome'][:40]}"})
    for b in blocked_active:
        if b["ticker"] not in earnings_set:
            avoid.append({"ticker": b["ticker"], "reason": b.get("reason", "bloqueado manualmente")})

    print(
        f"[{_ts()}] Scout — build_digest END: "
        f"{len(tickers)} tickers | {len(avoid)} evitar | {len(macro_events)} macro",
        flush=True,
    )
    return {
        "generated_at":      _now_utc().strftime("%Y-%m-%d %H:%M UTC"),
        "date_label":        _now_utc().strftime("%A, %d %b %Y"),
        "regime":            regime_raw,
        "macro":             macro,
        "tickers":           tickers,
        "upcoming_earnings": upcoming_earnings,
        "macro_events":      macro_events,
        "avoid":             avoid,
        "blocked_active":    blocked_active,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _regime_display(regime: str) -> tuple[str, str]:
    """(label legível, cor hex)"""
    return {
        "bull_trending":     ("Bull Trending",     "#2ea043"),
        "bull_lateral":      ("Bull Lateral",       "#d29922"),
        "bear_correction":   ("Bear Correction",    "#f85149"),
        "bear_capitulation": ("Bear Capitulation",  "#ff6e6e"),
    }.get(regime, (regime or "—", "#8b949e"))


def _macro_badge_html(macro: dict) -> str:
    mode = macro.get("macro_mode", "")
    if mode == "total_kill":
        return '<span style="background:#3d0f0f;color:#ff6e6e;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;">KILL SWITCH</span>'
    if mode in ("cash_is_king", "caution"):
        return '<span style="background:#2d2208;color:#f0b429;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;">CAUTELA</span>'
    return '<span style="background:#0e2116;color:#2ea043;padding:2px 8px;border-radius:4px;font-size:11px;">NORMAL</span>'


def _news_block_html(news: dict | None) -> str:
    if not news or not news.get("title"):
        return '<div style="font-size:12px;color:#6e7681;margin-top:6px;font-style:italic;">Sem notícias nas últimas 24h</div>'
    title   = news["title"]
    url     = news.get("url", "#") or "#"
    src     = news.get("source", "")
    ts_raw  = news.get("datetime", 0)
    age_str = ""
    if ts_raw:
        try:
            dt = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            h  = int((_now_utc() - dt).total_seconds() / 3600)
            age_str = f" · {h}h atrás" if h < 48 else ""
        except (ValueError, OSError):
            pass
    return (
        f'<div style="font-size:13px;margin-top:6px;padding:8px 10px;background:#161b22;'
        f'border-radius:6px;border-left:3px solid #2ea043;">'
        f'📰 <a href="{url}" style="color:#58a6ff;text-decoration:none;">{title}</a>'
        f'<span style="color:#6e7681;font-size:11px;"> — {src}{age_str}</span>'
        f'</div>'
    )


def render_html(digest: dict) -> str:
    regime_lbl, regime_color = _regime_display(digest.get("regime", ""))
    macro     = digest.get("macro", {})
    vix       = macro.get("vix")
    spy_vs    = macro.get("spy_vs_sma200_pct")

    vix_str  = f"{vix:.1f}" if vix is not None else "N/D"
    spy_str  = (f"+{spy_vs:.1f}%" if spy_vs and spy_vs >= 0 else f"{spy_vs:.1f}%") if spy_vs is not None else "N/D"
    spy_col  = "#2ea043" if (spy_vs or 0) >= 0 else "#f85149"
    mode_str = macro.get("macro_mode", "offline")

    # ---- ticker rows ----
    ticker_rows_html = ""
    for t in digest.get("tickers", []):
        ticker   = t["ticker"]
        price    = f"${t['price']:.2f}" if t.get("price") else ""
        rsi_str  = f"RSI {t['rsi']:.1f}" if t.get("rsi") is not None else ""
        vol_str  = f"Vol {t['vol_ratio']:.1f}×" if t.get("vol_ratio") is not None else ""
        mom_str  = f"mom1M +{t['mom_1m']:.1f}%" if t.get("mom_1m") else ""
        meta     = " · ".join(x for x in [rsi_str, vol_str, mom_str] if x)
        block_url = (
            "https://github.com/chico130/fundscope/issues/new"
            f"?title=block%3A{ticker}&labels=ticker-block"
            f"&body=Raz%C3%A3o%3A+%28preencher+antes+de+submeter%29%0A%0ATicker%3A+{ticker}"
        )
        ticker_rows_html += f"""
  <tr><td style="padding:12px 20px;border-bottom:1px solid #21262d;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-size:16px;font-weight:700;color:#e6edf3;">{ticker}
        <span style="font-size:12px;color:#8b949e;font-weight:400;"> {t.get('sector','')} · {price}</span>
      </td>
      <td align="right" style="white-space:nowrap;">
        <span style="background:#1f6feb33;color:#58a6ff;border-radius:4px;padding:2px 8px;font-size:12px;">força {t['score']}%</span>
      </td>
    </tr></table>
    <div style="font-size:12px;color:#8b949e;margin-top:4px;">{meta}</div>
    {_news_block_html(t.get("news"))}
    <div style="margin-top:8px;">
      <a href="{block_url}" style="font-size:11px;color:#f85149;text-decoration:none;border:1px solid #f8514944;border-radius:4px;padding:3px 10px;">⛔ Bloquear {ticker}</a>
    </div>
  </td></tr>"""

    # ---- earnings rows ----
    earnings_html = ""
    for e in digest.get("upcoming_earnings", [])[:6]:
        d = e["days_until"]
        icon = "🔴" if d == 0 else ("🟡" if d == 1 else "📅")
        suffix = {0: "HOJE", 1: "amanhã"}.get(d, f"em {d} dias")
        earnings_html += f'<div style="margin-bottom:4px;">{icon} {suffix} — <strong>{e["ticker"]}</strong> ({e["nome"][:45]})</div>'
    if not earnings_html:
        earnings_html = '<div style="color:#6e7681;font-style:italic;">Nenhum earnings nos próximos 3 dias</div>'

    # ---- macro events ----
    macro_section_html = ""
    if digest.get("macro_events"):
        rows = "".join(
            f'<div style="margin-bottom:4px;">📊 <strong>{ev["event"]}</strong>'
            f'<span style="color:#8b949e;"> — {ev.get("date","")}</span></div>'
            for ev in digest["macro_events"]
        )
        macro_section_html = f"""
  <tr><td style="padding:12px 20px 4px;background:#0d1117;">
    <div style="font-size:15px;font-weight:700;color:#e6edf3;">📊 Eventos Macro (48h)</div>
  </td></tr>
  <tr><td style="padding:4px 20px 14px;background:#0d1117;font-size:13px;color:#c9d1d9;border-bottom:1px solid #21262d;">
    {rows}
  </td></tr>"""

    # ---- avoid today ----
    avoid_html = "".join(
        f'<div style="margin-bottom:3px;">• <strong>{a["ticker"]}</strong> — {a["reason"]}</div>'
        for a in digest.get("avoid", [])
    ) or '<span style="color:#2ea043;">Nenhum ticker a evitar hoje</span>'

    # ---- blocked active ----
    blocked_parts = []
    for b in digest.get("blocked_active", []):
        exp = b.get("expires_at", "")
        suffix = f" (até {str(exp)[:10]})" if exp else ""
        blocked_parts.append(f'<strong>{b["ticker"]}</strong>{suffix}')
    blocked_inline = " · ".join(blocked_parts) if blocked_parts else "nenhum"
    edit_url = "https://github.com/chico130/fundscope/edit/main/data/blocked_tickers.json"

    return f"""<!DOCTYPE html>
<html lang="pt">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0d1117;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#e6edf3;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;">
<tr><td align="center" style="padding:12px 0;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  <tr><td style="padding:20px 20px 14px;background:#161b22;border-bottom:2px solid #2ea043;border-radius:6px 6px 0 0;">
    <div style="font-size:20px;font-weight:700;color:#e6edf3;">📋 FundScope — Briefing Diário</div>
    <div style="font-size:13px;color:#8b949e;margin-top:4px;">{digest['date_label']} · abertura NYSE em ~60 min · {_macro_badge_html(macro)}</div>
  </td></tr>

  <tr><td style="padding:14px 20px 10px;background:#0d1117;border-bottom:1px solid #21262d;">
    <div style="font-size:14px;font-weight:700;color:#e6edf3;margin-bottom:8px;">🌍 Estado do Mercado</div>
    <table width="100%" cellpadding="3" cellspacing="0">
      <tr>
        <td style="font-size:13px;color:#8b949e;">Regime</td>
        <td align="right" style="font-weight:600;color:{regime_color};">{regime_lbl}</td>
      </tr><tr>
        <td style="font-size:13px;color:#8b949e;">VIX</td>
        <td align="right" style="font-weight:600;color:#e6edf3;">{vix_str} <span style="color:#8b949e;font-size:12px;">({mode_str})</span></td>
      </tr><tr>
        <td style="font-size:13px;color:#8b949e;">SPY vs SMA-200</td>
        <td align="right" style="font-weight:600;color:{spy_col};">{spy_str}</td>
      </tr>
    </table>
  </td></tr>

  <tr><td style="padding:12px 20px 4px;background:#0d1117;">
    <div style="font-size:15px;font-weight:700;color:#e6edf3;">🎯 Top 5 Watchlist hoje</div>
    <div style="font-size:11px;color:#6e7681;margin-top:2px;">Score = momentum 40% + 3M 30% + liquidez 20% + qualidade 10%</div>
  </td></tr>
  {ticker_rows_html}

  <tr><td style="padding:14px 20px 4px;background:#0d1117;border-top:1px solid #21262d;">
    <div style="font-size:15px;font-weight:700;color:#e6edf3;">📅 Earnings próximos (3 dias)</div>
  </td></tr>
  <tr><td style="padding:4px 20px 14px;background:#0d1117;font-size:13px;color:#c9d1d9;border-bottom:1px solid #21262d;">
    {earnings_html}
  </td></tr>

  {macro_section_html}

  <tr><td style="padding:12px 20px;background:#1a0e0e;border-bottom:1px solid #21262d;">
    <div style="font-size:14px;font-weight:700;color:#f85149;">⚠️ Evitar hoje</div>
    <div style="font-size:13px;color:#ffa198;margin-top:5px;">{avoid_html}</div>
  </td></tr>

  <tr><td style="padding:10px 20px;background:#0d1117;border-bottom:1px solid #21262d;">
    <div style="font-size:12px;color:#8b949e;">
      🔒 Bloqueados: {blocked_inline}
      &nbsp;·&nbsp;<a href="{edit_url}" style="color:#58a6ff;font-size:11px;text-decoration:none;">editar JSON</a>
    </div>
  </td></tr>

  <tr><td style="padding:14px 20px;background:#161b22;text-align:center;border-radius:0 0 6px 6px;">
    <div style="font-size:11px;color:#6e7681;">FundScope Scout · gerado {digest['generated_at']}</div>
    <div style="font-size:11px;color:#6e7681;margin-top:2px;">leitura informativa — não é ordem de execução · conta demo</div>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


# ---------------------------------------------------------------------------
# Envio de email
# ---------------------------------------------------------------------------

def send_email(html: str) -> bool:
    """Envia HTML via Gmail SMTP (TLS 587). Retorna True se enviado."""
    if not all([_SMTP_USER, _SMTP_PASS, _BRIEFING_EMAIL]):
        missing = [k for k, v in [("SMTP_USER", _SMTP_USER), ("SMTP_PASS", _SMTP_PASS), ("BRIEFING_EMAIL", _BRIEFING_EMAIL)] if not v]
        print(f"[{_ts()}] Credenciais em falta: {missing} — email não enviado.", flush=True)
        return False

    subject = f"[FundScope] Briefing {_now_utc().strftime('%d %b %Y')}"
    msg = MIMEMultipart("alternative")
    msg["From"]    = _SMTP_USER
    msg["To"]      = _BRIEFING_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(_SMTP_USER, _SMTP_PASS)
            server.sendmail(_SMTP_USER, _BRIEFING_EMAIL, msg.as_string())
        print(f"[{_ts()}] Email enviado → {_BRIEFING_EMAIL}", flush=True)
        return True
    except Exception as exc:
        print(f"[{_ts()}] Falha SMTP: {exc}", flush=True)
        return False


def _notify_failure(msg: str) -> None:
    """Alerta Telegram opcional em caso de falha total (fail-open)."""
    try:
        token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"⚠️ Scout Briefing falhou\n{msg[:400]}"},
            timeout=8,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[{_ts()}] === Scout Daily Briefing START ===", flush=True)
    try:
        digest = build_digest()
        html   = render_html(digest)
        ok     = send_email(html)
        status = "ENVIADO" if ok else "SEM CREDENCIAIS/ERRO SMTP"
        print(f"[{_ts()}] === Scout Daily Briefing END — {status} ===", flush=True)
        if not ok:
            _notify_failure(f"send_email falhou — status: {status}")
    except Exception as exc:
        import traceback
        msg = f"{type(exc).__name__}: {exc}"
        print(f"[{_ts()}] ERRO FATAL em Scout: {msg}", flush=True)
        traceback.print_exc()
        _notify_failure(msg)


if __name__ == "__main__":
    main()
