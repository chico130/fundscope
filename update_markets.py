#!/usr/bin/env python3
"""
update_markets.py — FundScope v2.0

Causa raiz do problema:
  O Finnhub free tier tem limite de 60 req/min.
  Com 15 tickers x 6 setores = 90 chamadas so de quotes, o token era
  throttled e devolvia {"c":0,"pc":0} para Consumo e Commodities
  (os ultimos setores a correr).

Solucao:
  - Cotacoes: yfinance (sem rate limit, batch download)
  - Sentimento: Finnhub /stock/recommendation (3 req/setor = 18 total, ok)
  - Noticias:   Finnhub company-news (5 req/setor = 30 total, ok)
"""

import json, os, time, datetime, requests
import yfinance as yf

FH_TOKEN = os.environ.get("FINNHUB_TOKEN", "")
FH_BASE  = "https://finnhub.io/api/v1"

SECTORS = {
    "Tecnologia": {
        "icon": "\U0001f4bb",
        "color": "#4f98a3",
        "image": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
        "tickers": ["AAPL","MSFT","NVDA","GOOGL","META","AMD","INTC","TSLA","AVGO","MU","QCOM","ORCL","CRM","ADBE","NOW"],
        "sentiment_tickers": ["AAPL","NVDA","MSFT"],
        "news_tickers": ["AAPL","NVDA","MSFT","AMD","META"]
    },
    "Finan\u00e7as": {
        "icon": "\U0001f3e6",
        "color": "#437a22",
        "image": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
        "tickers": ["JPM","BAC","GS","MS","WFC","C","BLK","AXP","V","MA","SCHW","USB","PNC","TFC","COF"],
        "sentiment_tickers": ["JPM","GS","BAC"],
        "news_tickers": ["JPM","GS","BAC","MS","V"]
    },
    "Energia": {
        "icon": "\u26a1",
        "color": "#c47d1a",
        "image": "https://images.unsplash.com/photo-1509391366360-2e959784a276?w=800&q=80",
        "tickers": ["XOM","CVX","COP","SLB","EOG","PSX","MPC","VLO","OXY","HAL","NEE","D","DUK","SO","AEP"],
        "sentiment_tickers": ["XOM","CVX","NEE"],
        "news_tickers": ["XOM","CVX","COP","OXY","NEE"]
    },
    "Sa\u00fade": {
        "icon": "\U0001f3e5",
        "color": "#a13544",
        "image": "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=800&q=80",
        "tickers": ["JNJ","UNH","PFE","ABBV","MRK","LLY","TMO","ABT","DHR","BMY","AMGN","GILD","MDT","CVS","ISRG"],
        "sentiment_tickers": ["JNJ","LLY","PFE"],
        "news_tickers": ["JNJ","LLY","PFE","ABBV","MRK"]
    },
    "Consumo": {
        "icon": "\U0001f6d2",
        "color": "#7a5c9e",
        "image": "https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=800&q=80",
        "tickers": ["AMZN","WMT","HD","MCD","SBUX","NKE","TGT","COST","LOW","TJX","PG","KO","PEP","PM","CL"],
        "sentiment_tickers": ["AMZN","WMT","MCD"],
        "news_tickers": ["AMZN","WMT","MCD","COST","KO"]
    },
    "Commodities": {
        "icon": "\U0001fa99",
        "color": "#8a7340",
        "image": "https://images.unsplash.com/photo-1610375461246-83df859d849d?w=800&q=80",
        "tickers": ["FCX","NEM","GOLD","AA","CLF","X","MP","WPM","AEM","CF","MOS","NUE","STLD","RS","ATI"],
        "sentiment_tickers": ["FCX","NEM","GOLD"],
        "news_tickers": ["FCX","NEM","GOLD","AA","CLF"]
    }
}

# ------------------------------------------------------------------ yfinance
def fetch_all_quotes():
    """
    Faz um unico batch download de todos os tickers via yfinance.
    Devolve dict: {TICKER: {"price": x, "changePct": y, "pc": z}}
    """
    all_tickers = []
    for cfg in SECTORS.values():
        all_tickers.extend(cfg["tickers"])
    all_tickers = list(dict.fromkeys(all_tickers))  # dedup, preserva ordem

    print(f"A descarregar {len(all_tickers)} cotacoes via yfinance...")
    try:
        data = yf.download(
            all_tickers,
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True
        )
    except Exception as e:
        print(f"  [ERRO yfinance download]: {e}")
        return {}

    quotes = {}
    close = data["Close"] if "Close" in data.columns else data.xs("Close", axis=1, level=0) if hasattr(data.columns, 'levels') else None

    if close is None:
        print("  [ERRO] nao foi possivel extrair Close do dataframe")
        return {}

    for t in all_tickers:
        try:
            col = close[t] if t in close.columns else None
            if col is None or col.dropna().empty:
                print(f"  [SKIP] {t}: sem dados")
                continue
            vals = col.dropna()
            if len(vals) < 1:
                continue
            price = float(vals.iloc[-1])
            pc    = float(vals.iloc[-2]) if len(vals) >= 2 else price
            chg   = round((price - pc) / pc * 100, 2) if pc else 0.0
            quotes[t] = {"ticker": t, "price": round(price, 2), "changePct": chg, "pc": round(pc, 2)}
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")

    print(f"  {len(quotes)}/{len(all_tickers)} cotacoes obtidas")
    return quotes

# ------------------------------------------------------------------ Finnhub
def fh_get(endpoint, params):
    params["token"] = FH_TOKEN
    try:
        r = requests.get(f"{FH_BASE}/{endpoint}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN Finnhub] {endpoint} {params.get('symbol','')}: {e}")
        return None

def fh_recommendation(ticker):
    d = fh_get("stock/recommendation", {"symbol": ticker})
    if not d or not isinstance(d, list) or not d:
        return {"bullish": None, "articles": 0, "sb": 0, "b": 0, "h": 0, "s": 0, "ss": 0}
    latest = d[0]
    sb = latest.get("strongBuy", 0) or 0
    b  = latest.get("buy", 0) or 0
    h  = latest.get("hold", 0) or 0
    s  = latest.get("sell", 0) or 0
    ss = latest.get("strongSell", 0) or 0
    total = sb + b + h + s + ss
    if total == 0:
        return {"bullish": None, "articles": 0, "sb": 0, "b": 0, "h": 0, "s": 0, "ss": 0}
    score = sb * 2 + b * 1 + s * (-1) + ss * (-2)
    pct   = round((score + 2 * total) / (4 * total) * 100, 1)
    return {"bullish": pct, "articles": total, "sb": sb, "b": b, "h": h, "s": s, "ss": ss}

def fh_news(ticker, frm, to, limit=2):
    d = fh_get("company-news", {"symbol": ticker, "from": frm, "to": to})
    if not d or not isinstance(d, list):
        return []
    out, seen = [], set()
    for item in d[:40]:
        headline = (item.get("headline") or "").strip()
        if not headline or headline in seen:
            continue
        seen.add(headline)
        out.append({
            "source":   item.get("source", ticker),
            "ticker":   ticker,
            "headline": headline[:160],
            "summary":  (item.get("summary") or "")[:220],
            "url":      item.get("url", ""),
            "datetime": item.get("datetime", 0),
            "image":    item.get("image", "")
        })
        if len(out) >= limit:
            break
    return out

# ------------------------------------------------------------------ builder
def build_sector(name, cfg, all_quotes, frm, to):
    quotes = [all_quotes[t] for t in cfg["tickers"] if t in all_quotes]
    print(f"  {len(quotes)}/{len(cfg['tickers'])} quotes")

    quotes_sorted = sorted(quotes, key=lambda x: x["changePct"], reverse=True)
    gainers = quotes_sorted[:5]
    losers  = list(reversed(quotes_sorted[-5:])) if len(quotes_sorted) >= 5 else list(reversed(quotes_sorted))

    # Sentimento via Finnhub (poucos pedidos)
    sentiments = []
    for t in cfg["sentiment_tickers"]:
        rec = fh_recommendation(t)
        sentiments.append({
            "ticker": t, "bullish": rec["bullish"], "articles": rec["articles"],
            "sb": rec["sb"], "b": rec["b"], "h": rec["h"], "s": rec["s"], "ss": rec["ss"]
        })
        time.sleep(0.25)

    valid    = [s["bullish"] for s in sentiments if s["bullish"] is not None]
    avg_bull = round(sum(valid) / len(valid), 1) if valid else None

    # Noticias via Finnhub
    news, seen_h = [], set()
    for t in cfg["news_tickers"]:
        for item in fh_news(t, frm, to, limit=2):
            if item["headline"] not in seen_h:
                seen_h.add(item["headline"])
                news.append(item)
        time.sleep(0.25)
        if len(news) >= 5:
            break
    news.sort(key=lambda x: x["datetime"], reverse=True)
    news = news[:5]

    avg_chg = round(sum(q["changePct"] for q in quotes) / len(quotes), 2) if quotes else 0

    return {
        "name": name, "icon": cfg["icon"], "color": cfg["color"], "image": cfg["image"],
        "avgChange": avg_chg, "gainers": gainers, "losers": losers,
        "sentiment": {"bullishPct": avg_bull, "details": sentiments},
        "news": news
    }

# ------------------------------------------------------------------ main
def main():
    now   = datetime.datetime.utcnow()
    slot  = "abertura" if now.hour < 10 else ("meio-dia" if now.hour < 14 else "fecho")
    today = now.date()
    frm   = (today - datetime.timedelta(days=4)).isoformat()
    to    = today.isoformat()

    # 1. Batch download de todas as cotacoes (1 unica chamada yfinance)
    all_quotes = fetch_all_quotes()

    # 2. Construir cada setor
    sectors = {}
    for name, cfg in SECTORS.items():
        print(f"\n=== {name} ===")
        try:
            sectors[name] = build_sector(name, cfg, all_quotes, frm, to)
            g = len(sectors[name]["gainers"])
            l = len(sectors[name]["losers"])
            print(f"  gainers={g} losers={l} avgChg={sectors[name]['avgChange']}%")
        except Exception as e:
            print(f"  ERRO: {e}")
            sectors[name] = {
                "name": name, "icon": cfg["icon"], "color": cfg["color"], "image": cfg["image"],
                "avgChange": 0, "gainers": [], "losers": [],
                "sentiment": {"bullishPct": None, "details": []}, "news": []
            }

    out = {"updated": now.isoformat() + "Z", "slot": slot, "sectors": sectors}
    with open("markets.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nmarkets.json OK ({slot})")

if __name__ == "__main__":
    main()
