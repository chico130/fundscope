#!/usr/bin/env python3
"""
update_portfolio.py — FundScope Portfolio
"""

import json, os, time, datetime, requests, base64
import yfinance as yf

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[AVISO] google-genai não instalado")

T212_KEY_ID  = os.environ.get("T212_API_ID", "")
T212_SECRET  = os.environ["T212_API_KEY"]
FH_TOKEN     = os.environ.get("FINNHUB_TOKEN", "")
GEMINI_KEY   = os.environ.get("GEMINI_API_KEY", "")
FH_BASE      = "https://finnhub.io/api/v1"
T212_BASE    = "https://live.trading212.com/api/v0"

_creds      = base64.b64encode(f"{T212_KEY_ID}:{T212_SECRET}".encode()).decode()
T212_AUTH   = f"Basic {_creds}"

gemini_client = None
if GEMINI_AVAILABLE and GEMINI_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_KEY)
        print("[OK] Gemini client inicializado")
    except Exception as e:
        print(f"[AVISO] Gemini init falhou: {e}")

def t212_get(path):
    r = requests.get(f"{T212_BASE}{path}", headers={"Authorization": T212_AUTH}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_t212_positions():
    data = t212_get("/equity/portfolio")
    positions = []
    for p in data:
        quantity = float(p.get("quantity", 0))
        if quantity <= 0:
            continue
        positions.append({
            "ticker_t212":   p.get("ticker", ""),
            "quantity":      round(quantity, 6),
            "avg_price":     round(float(p.get("averagePrice", 0)), 4),
            "current_price": round(float(p.get("currentPrice", 0)), 4),
            "ppl":           round(float(p.get("ppl", 0)), 2),
            "fx_ppl":        round(float(p.get("fxPpl", 0) or 0), 2),
        })
    return positions

def map_t212_ticker(t212_ticker):
    ticker = t212_ticker
    for suffix in ["_US_EQ", "_EQ", "_GBX_EQ", "_EUR_EQ", "_GBP_EQ"]:
        ticker = ticker.replace(suffix, "")
    eu_etfs = {
        "VWCE": "VWCE.DE", "VWRA": "VWRA.L",  "VUAA": "VUAA.DE",
        "VUSA": "VUSA.AS", "VEUR": "VEUR.AS",  "VFEM": "VFEM.AS",
        "VHYL": "VHYL.AS", "VDIV": "VDIV.AS",  "VAGP": "VAGP.L",
        "CSPX": "CSPX.L",  "IWDA": "IWDA.AS",  "EUNL": "EUNL.DE",
        "SXR8": "SXR8.DE", "IMAE": "IMAE.AS",  "IUSQ": "IUSQ.DE",
        "IQQQ": "IQQQ.DE", "EMIM": "EMIM.L",   "AGGH": "AGGH.L",
        "SSAC": "SSAC.L",  "CNDX": "CNDX.L",
        "MEUD": "MEUD.PA", "SPYY": "SPYY.DE",  "SPPW": "SPPW.DE",
        "SPYW": "SPYW.DE", "XDWD": "XDWD.DE",  "EQQQ": "EQQQ.L",
        "SMEA": "SMEA.DE",
    }
    return eu_etfs.get(ticker, ticker)

def fetch_quotes_yf(yf_tickers):
    if not yf_tickers:
        return {}
    print(f"  yfinance: {len(yf_tickers)} tickers...")
    quotes = {}
    tickers_str = " ".join(yf_tickers) if len(yf_tickers) > 1 else yf_tickers[0]
    try:
        data = yf.download(tickers_str, period="5d", interval="1d",
                           auto_adjust=True, progress=False, threads=True, group_by="ticker")
    except Exception as e:
        print(f"  [ERRO yfinance batch]: {e}")
        data = None
    for t in yf_tickers:
        try:
            if data is not None:
                col = data["Close"] if len(yf_tickers) == 1 else data[t]["Close"]
                vals = col.dropna()
                price = float(vals.iloc[-1])
                pc    = float(vals.iloc[-2]) if len(vals) >= 2 else price
            else:
                tk    = yf.Ticker(t)
                hist  = tk.history(period="5d")
                price = float(hist["Close"].iloc[-1])
                pc    = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
            chg = round((price - pc) / pc * 100, 2) if pc else 0.0
            quotes[t] = {"price": round(price, 4), "changePct": chg}
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")
    return quotes

def fetch_earnings(yf_ticker):
    try:
        hist = yf.Ticker(yf_ticker).earnings_history
        if hist is None or hist.empty:
            return []
        records = []
        for _, row in hist.tail(8).iterrows():
            eps_est  = row.get("epsEstimate") or row.get("EpsEstimate")
            eps_act  = row.get("epsActual")   or row.get("EpsActual")
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
    try:
        divs = yf.Ticker(yf_ticker).dividends
        if divs is None or divs.empty:
            return []
        return list(reversed([{"date": str(dt.date()), "amount": round(float(v), 4)}
                               for dt, v in divs.tail(4).items()]))
    except Exception:
        return []

def fh_news(ticker, frm, to, limit=5):
    base_symbol = ticker.split(".")[0]
    try:
        r = requests.get(f"{FH_BASE}/company-news",
                         params={"symbol": base_symbol, "from": frm, "to": to, "token": FH_TOKEN},
                         timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [FH news] {base_symbol}: {e}")
        return []
    out, seen = [], set()
    for item in (data or [])[:40]:
        h = (item.get("headline") or "").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append({"source": item.get("source", base_symbol), "headline": h[:180],
                    "summary": (item.get("summary") or "")[:300],
                    "url": item.get("url", ""), "datetime": item.get("datetime", 0),
                    "image": item.get("image", "")})
        if len(out) >= limit:
            break
    return out

def gemini_analyze(ticker, name, news_list, earnings_list, ppl, pct_change):
    if not gemini_client:
        return {"sentiment": "neutro", "news_comment": "Gemini indisponível.",
                "earnings_comment": "Gemini indisponível.", "watch_points": []}
    news_text = "\n".join([f"- {n['headline']}" for n in news_list]) or "Sem notícias."
    earn_text = "\n".join([
        f"- {e['period']}: real={e['actual']} est={e['estimate']} {'BEAT' if e.get('beat') else 'MISS'}"
        for e in earnings_list]) or "Sem dados."
    prompt = f"""Analisa {ticker} ({name}) em PT-PT.
P&L: {'+' if ppl>=0 else ''}{ppl:.2f}€, hoje: {pct_change:+.2f}%
Notícias:\n{news_text}\nEarnings:\n{earn_text}
Responde APENAS JSON: {{"sentiment":"positivo|negativo|neutro","news_comment":"...","earnings_comment":"...","watch_points":["...","...","..."]}}"""
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash", contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.3))
        return json.loads(response.text.strip())
    except Exception as e:
        print(f"  [Gemini] {ticker}: {e}")
        return {"sentiment": "neutro", "news_comment": "Indisponível.",
                "earnings_comment": "Indisponível.", "watch_points": []}

def load_history():
    try:
        with open("portfolio.json", "r", encoding="utf-8") as f:
            return json.load(f).get("history", [])
    except Exception:
        return []

def update_history(history, total_value):
    today = datetime.date.today().isoformat()
    history = [h for h in history if h["date"] != today]
    history.append({"date": today, "value": round(total_value, 2)})
    return sorted(history, key=lambda x: x["date"])[-365:]

def main():
    now   = datetime.datetime.utcnow()
    today = now.date()
    frm   = (today - datetime.timedelta(days=7)).isoformat()
    to    = today.isoformat()

    print("=== FundScope Portfolio Update ===")
    print(f"UTC: {now.isoformat()}")

    print("\n[1] A buscar posições T212 (live)...")
    positions = fetch_t212_positions()
    print(f"    {len(positions)} posições encontradas")
    if not positions:
        print("    Nenhuma posição — a terminar.")
        return

    print("\n[2] A enriquecer com yfinance...")
    for p in positions:
        p["ticker"] = map_t212_ticker(p["ticker_t212"])
    yf_tickers = list(dict.fromkeys(p["ticker"] for p in positions))
    quotes = fetch_quotes_yf(yf_tickers)
    for p in positions:
        q = quotes.get(p["ticker"], {})
        if q.get("price"):
            p["current_price"] = q["price"]
        p["change_pct"] = q.get("changePct", 0.0)

    for p in positions:
        invested   = p["avg_price"] * p["quantity"]
        curr_value = p["current_price"] * p["quantity"]
        gain_eur   = p["ppl"]
        p["invested"]  = round(invested, 2)
        p["value_eur"] = round(curr_value, 2)
        p["gain_eur"]  = round(gain_eur, 2)
        p["gain_pct"]  = round((gain_eur / invested * 100) if invested > 0 else 0, 2)

    total_value    = sum(p["value_eur"] for p in positions)
    total_invested = sum(p["invested"]  for p in positions)
    total_gain     = sum(p["gain_eur"]  for p in positions)
    total_gain_pct = (total_gain / total_invested * 100) if total_invested > 0 else 0
    daily_gain     = sum(p["value_eur"] * p["change_pct"] / 100 for p in positions)
    for p in positions:
        p["allocation_pct"] = round(p["value_eur"] / total_value * 100, 2) if total_value > 0 else 0
    positions.sort(key=lambda x: x["value_eur"], reverse=True)

    print("\n[3] Notícias + Earnings + Gemini...")
    for p in positions:
        ticker = p["ticker"]
        print(f"  → {ticker}")
        p["news"]      = fh_news(ticker, frm, to)
        time.sleep(0.3)
        p["earnings"]  = fetch_earnings(ticker)
        p["dividends"] = fetch_dividends(ticker)
        p["analysis"]  = gemini_analyze(ticker, p.get("ticker_t212", ticker),
                                         p["news"], p["earnings"],
                                         p["gain_eur"], p["change_pct"])
        time.sleep(1)

    print("\n[4] Histórico...")
    history = update_history(load_history(), total_value)

    out = {
        "updated":   now.isoformat() + "Z",
        "t212_mode": "live",
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

    print(f"\n✅ Concluído!")
    print(f"   Valor: {total_value:.2f}€ | P&L: {total_gain:+.2f}€ | Posições: {len(positions)}")

if __name__ == "__main__":
    main()
