#!/usr/bin/env python3
"""
update_portfolio.py — FundScope Portfolio

Fluxo:
  1. Puxa posições da Trading 212 API
  2. Enriquece com cotações via yfinance (suporte EUR e USD)
  3. Busca 5 notícias por ativo via Finnhub
  4. Busca earnings históricos via yfinance
  5. Gera análise em PT via Gemini API
  6. Guarda portfolio.json
"""

import json, os, time, datetime, requests
import yfinance as yf
import google.generativeai as genai

T212_KEY    = os.environ["T212_API_KEY"]
FH_TOKEN    = os.environ.get("FINNHUB_TOKEN", "")
GEMINI_KEY  = os.environ["GEMINI_API_KEY"]
T212_BASE   = "https://live.trading212.com/api/v0"
FH_BASE     = "https://finnhub.io/api/v1"

genai.configure(api_key=GEMINI_KEY)
gemini = genai.GenerativeModel("gemini-1.5-flash")

# ------------------------------------------------------------------ T212
def t212_get(path):
    headers = {"Authorization": T212_KEY}
    r = requests.get(f"{T212_BASE}{path}", headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_t212_positions():
    """Devolve lista de posições da T212."""
    data = t212_get("/equity/portfolio")
    positions = []
    for p in data:
        ticker_raw = p.get("ticker", "")
        quantity   = float(p.get("quantity", 0))
        avg_price  = float(p.get("averagePrice", 0))
        current    = float(p.get("currentPrice", 0))
        ppl        = float(p.get("ppl", 0))  # profit/loss em EUR
        fx_ppl     = float(p.get("fxPpl", 0))
        if quantity <= 0:
            continue
        positions.append({
            "ticker_t212": ticker_raw,
            "quantity":    round(quantity, 6),
            "avg_price":   round(avg_price, 4),
            "current_price": round(current, 4),
            "ppl":         round(ppl, 2),
            "fx_ppl":      round(fx_ppl, 2),
        })
    return positions

def map_t212_ticker(t212_ticker):
    """
    T212 usa tickers como AAPL_US_EQ, VWCE_EQ, CSPX_EQ.
    Mapeia para tickers yfinance/Finnhub.
    """
    # Remove sufixos comuns da T212
    ticker = t212_ticker
    for suffix in ["_US_EQ", "_EQ", "_GBX_EQ", "_EUR_EQ"]:
        ticker = ticker.replace(suffix, "")
    # ETFs europeus — adicionar sufixo de bolsa se necessário
    eu_etfs = {
        "VWCE": "VWCE.DE", "CSPX": "CSPX.L", "IWDA": "IWDA.AS",
        "EUNL": "EUNL.DE", "SXR8": "SXR8.DE", "IMAE": "IMAE.AS",
        "VUSA": "VUSA.AS", "MEUD": "MEUD.PA", "IUSQ": "IUSQ.DE",
        "SPYY": "SPYY.DE", "XDWD": "XDWD.DE", "EQQQ": "EQQQ.L",
        "VEUR": "VEUR.AS", "VFEM": "VFEM.AS", "AGGH": "AGGH.L",
    }
    return eu_etfs.get(ticker, ticker)

# ------------------------------------------------------------------ yfinance
def fetch_quotes_yf(yf_tickers):
    """Batch download de cotações. Devolve {yf_ticker: {price, changePct, currency}}"""
    if not yf_tickers:
        return {}
    print(f"  yfinance: {len(yf_tickers)} tickers...")
    try:
        data = yf.download(
            yf_tickers, period="5d", interval="1d",
            auto_adjust=True, progress=False, threads=True
        )
    except Exception as e:
        print(f"  [ERRO yfinance]: {e}")
        return {}

    quotes = {}
    try:
        close = data["Close"]
    except Exception:
        return {}

    for t in yf_tickers:
        try:
            col = close[t] if t in close.columns else None
            if col is None:
                continue
            vals = col.dropna()
            if len(vals) < 1:
                continue
            price = float(vals.iloc[-1])
            pc    = float(vals.iloc[-2]) if len(vals) >= 2 else price
            chg   = round((price - pc) / pc * 100, 2) if pc else 0.0
            quotes[t] = {"price": round(price, 4), "changePct": chg}
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")
    return quotes

def fetch_earnings(yf_ticker):
    """Busca histórico de earnings via yfinance."""
    try:
        tk = yf.Ticker(yf_ticker)
        hist = tk.earnings_history
        if hist is None or hist.empty:
            return []
        records = []
        for _, row in hist.tail(8).iterrows():
            eps_est = row.get("epsEstimate") or row.get("EpsEstimate")
            eps_act = row.get("epsActual")   or row.get("EpsActual")
            surprise = row.get("surprisePercent") or row.get("SurprisePercent")
            period   = str(row.get("period") or row.get("Period") or "")
            if eps_act is None:
                continue
            records.append({
                "period":   period,
                "estimate": round(float(eps_est), 3) if eps_est is not None else None,
                "actual":   round(float(eps_act), 3),
                "surprise": round(float(surprise), 2) if surprise is not None else None,
                "beat":     float(eps_act) >= float(eps_est) if eps_est is not None else None
            })
        return records
    except Exception as e:
        print(f"  [earnings] {yf_ticker}: {e}")
        return []

def fetch_dividends(yf_ticker):
    """Últimos 4 dividendos."""
    try:
        tk = yf.Ticker(yf_ticker)
        divs = tk.dividends
        if divs is None or divs.empty:
            return []
        records = []
        for dt, val in divs.tail(4).items():
            records.append({"date": str(dt.date()), "amount": round(float(val), 4)})
        return list(reversed(records))
    except Exception:
        return []

# ------------------------------------------------------------------ Finnhub
def fh_news(ticker, frm, to, limit=5):
    """Busca notícias via Finnhub. Para ETFs europeus usa ticker base."""
    base = ticker.split(".")[0]  # CSPX.L → CSPX
    try:
        params = {"symbol": base, "from": frm, "to": to, "token": FH_TOKEN}
        r = requests.get(f"{FH_BASE}/company-news", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [FH news] {base}: {e}")
        return []
    out, seen = [], set()
    for item in (data or [])[:40]:
        h = (item.get("headline") or "").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append({
            "source":   item.get("source", base),
            "headline": h[:180],
            "summary":  (item.get("summary") or "")[:300],
            "url":      item.get("url", ""),
            "datetime": item.get("datetime", 0),
            "image":    item.get("image", "")
        })
        if len(out) >= limit:
            break
    return out

# ------------------------------------------------------------------ Gemini
def gemini_analyze(ticker, name, news_list, earnings_list, ppl, pct_change):
    """Gera análise em PT: sentiment notícias + comentário earnings."""
    news_text = "\n".join([f"- {n['headline']}" for n in news_list]) or "Sem notícias recentes."
    earn_text = "\n".join([
        f"- {e['period']}: real={e['actual']} est={e['estimate']} {'BEAT' if e.get('beat') else 'MISS'}"
        for e in earnings_list
    ]) or "Sem dados de earnings."

    prompt = f"""Analisa o ativo {ticker} ({name}) em português de Portugal (PT-PT), de forma direta e concisa.

Desempenho recente: {'+' if ppl >= 0 else ''}{ppl:.2f}€ P&L, variação hoje: {pct_change:+.2f}%

Notícias recentes:
{news_text}

Earnings históricos (EPS):
{earn_text}

Responde em JSON com exatamente este formato:
{{
  "sentiment": "positivo" | "negativo" | "neutro",
  "news_comment": "2 frases sobre o tom das notícias e o que significam para o ativo",
  "earnings_comment": "2 frases sobre a tendência dos earnings e o que esperar",
  "watch_points": ["ponto 1", "ponto 2", "ponto 3"]
}}"""

    try:
        resp = gemini.generate_content(prompt)
        text = resp.text.strip()
        # Remove markdown code blocks se presentes
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        print(f"  [Gemini] {ticker}: {e}")
        return {
            "sentiment": "neutro",
            "news_comment": "Análise indisponível.",
            "earnings_comment": "Análise indisponível.",
            "watch_points": []
        }

# ------------------------------------------------------------------ histórico
def load_history():
    """Carrega histórico de valor do portfólio."""
    try:
        with open("portfolio.json", "r", encoding="utf-8") as f:
            old = json.load(f)
        return old.get("history", [])
    except Exception:
        return []

def update_history(history, total_value):
    """Adiciona entrada diária ao histórico (máx 365 dias)."""
    today = datetime.date.today().isoformat()
    # Não duplicar o mesmo dia
    history = [h for h in history if h["date"] != today]
    history.append({"date": today, "value": round(total_value, 2)})
    history.sort(key=lambda x: x["date"])
    return history[-365:]  # máx 1 ano

# ------------------------------------------------------------------ main
def main():
    now = datetime.datetime.utcnow()
    today = now.date()
    frm = (today - datetime.timedelta(days=5)).isoformat()
    to  = today.isoformat()

    print("=== FundScope Portfolio Update ===")
    print(f"UTC: {now.isoformat()}")

    # 1. Posições T212
    print("\n[1] A buscar posições T212...")
    positions = fetch_t212_positions()
    print(f"    {len(positions)} posições encontradas")

    # 2. Mapear tickers e buscar cotações
    print("\n[2] A enriquecer com cotações yfinance...")
    for p in positions:
        p["ticker"] = map_t212_ticker(p["ticker_t212"])

    yf_tickers = list(dict.fromkeys(p["ticker"] for p in positions))
    quotes = fetch_quotes_yf(yf_tickers)

    for p in positions:
        q = quotes.get(p["ticker"], {})
        # Usa cotação yfinance se disponível, senão mantém a da T212
        if q.get("price"):
            p["current_price"] = q["price"]
        p["change_pct"] = q.get("changePct", 0.0)

    # 3. Calcular métricas por posição (em EUR — T212 já devolve em EUR)
    for p in positions:
        invested    = p["avg_price"] * p["quantity"]
        curr_value  = p["current_price"] * p["quantity"]
        gain_eur    = p["ppl"]  # P&L real da T212 inclui FX
        gain_pct    = (gain_eur / invested * 100) if invested > 0 else 0
        p["invested"]   = round(invested, 2)
        p["value_eur"]  = round(curr_value, 2)
        p["gain_eur"]   = round(gain_eur, 2)
        p["gain_pct"]   = round(gain_pct, 2)

    # Totais
    total_value    = sum(p["value_eur"] for p in positions)
    total_invested = sum(p["invested"]  for p in positions)
    total_gain     = sum(p["gain_eur"]  for p in positions)
    total_gain_pct = (total_gain / total_invested * 100) if total_invested > 0 else 0
    daily_gain     = sum(p["value_eur"] * p["change_pct"] / 100 for p in positions)

    # Alocação %
    for p in positions:
        p["allocation_pct"] = round(p["value_eur"] / total_value * 100, 2) if total_value > 0 else 0

    # Ordenar por valor
    positions.sort(key=lambda x: x["value_eur"], reverse=True)

    # 4. Notícias + Earnings + Análise Gemini (por ativo)
    print("\n[3] A buscar notícias, earnings e análise Gemini...")
    for p in positions:
        ticker = p["ticker"]
        print(f"  {ticker}...")

        # Notícias
        p["news"] = fh_news(ticker, frm, to)
        time.sleep(0.3)

        # Earnings (só para stocks, não ETFs simples)
        p["earnings"] = fetch_earnings(ticker)

        # Dividendos
        p["dividends"] = fetch_dividends(ticker)

        # Análise Gemini
        p["analysis"] = gemini_analyze(
            ticker,
            p.get("ticker_t212", ticker),
            p["news"],
            p["earnings"],
            p["gain_eur"],
            p["change_pct"]
        )
        time.sleep(1)  # rate limit Gemini

    # 5. Histórico
    print("\n[4] A atualizar histórico...")
    history = load_history()
    history = update_history(history, total_value)

    # 6. Guardar
    out = {
        "updated": now.isoformat() + "Z",
        "summary": {
            "total_value":    round(total_value, 2),
            "total_invested": round(total_invested, 2),
            "total_gain_eur": round(total_gain, 2),
            "total_gain_pct": round(total_gain_pct, 2),
            "daily_gain_eur": round(daily_gain, 2),
            "n_positions":    len(positions)
        },
        "positions": positions,
        "history":   history
    }

    with open("portfolio.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n✅ portfolio.json guardado")
    print(f"   Valor total: {total_value:.2f}€")
    print(f"   P&L total:   {total_gain:+.2f}€ ({total_gain_pct:+.2f}%)")
    print(f"   Posições:    {len(positions)}")

if __name__ == "__main__":
    main()
