#!/usr/bin/env python3
"""
update_markets.py — FundScope v1.6
Corre 3x/dia via GitHub Actions: 08:00, 12:00, 15:30 UTC
Produz markets.json com:
  - top 5 gainers / losers por setor
  - sentimento Finnhub (news-sentiment) por setor
  - notícias recentes Finnhub company-news (substitui Reddit/StockTwits)
"""

import json, os, time, datetime, requests

FH_TOKEN = os.environ.get("FINNHUB_TOKEN", "")
FH_BASE  = "https://finnhub.io/api/v1"

SECTORS = {
    "Tecnologia": {
        "icon": "\U0001f4bb",
        "color": "#4f98a3",
        "image": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
        "tickers": ["AAPL","MSFT","NVDA","GOOGL","META","AMD","INTC","TSLA","ASML","AVGO","MU","QCOM","ORCL","CRM","ADBE"],
        "sentiment_tickers": ["AAPL","NVDA","MSFT"],
        "news_tickers": ["AAPL","NVDA","MSFT","AMD","META"]
    },
    "Finan\u00e7as": {
        "icon": "\U0001f3e6",
        "color": "#437a22",
        "image": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
        "tickers": ["JPM","BAC","GS","MS","WFC","C","BLK","AXP","V","MA","SCHW","BRK-B","USB","PNC","TFC"],
        "sentiment_tickers": ["JPM","GS","BAC"],
        "news_tickers": ["JPM","GS","BAC","MS","V"]
    },
    "Energia": {
        "icon": "\u26a1",
        "color": "#c47d1a",
        "image": "https://images.unsplash.com/photo-1509391366360-2e959784a276?w=800&q=80",
        "tickers": ["XOM","CVX","COP","SLB","EOG","PSX","MPC","VLO","OXY","HAL","BP","SHEL","NEE","D","DUK"],
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
        "tickers": ["GLD","SLV","USO","UNG","CORN","WEAT","FCX","NEM","GOLD","AA","CLF","X","MP","VALE","RIO"],
        "sentiment_tickers": ["GLD","USO","FCX"],
        "news_tickers": ["FCX","NEM","GOLD","VALE","AA"]
    }
}

def fh_get(endpoint, params):
    params["token"] = FH_TOKEN
    try:
        r = requests.get(f"{FH_BASE}/{endpoint}", params=params, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] {endpoint} {params.get('symbol','')}: {e}")
        return None

def fh_quote(ticker):
    d = fh_get("quote", {"symbol": ticker})
    if not d:
        return None
    price = d.get("c") or d.get("pc") or 0
    pc    = d.get("pc") or price
    chg   = ((price - pc) / pc * 100) if pc else 0
    return {"ticker": ticker, "price": round(price, 2), "changePct": round(chg, 2), "pc": round(pc, 2)}

def fh_sentiment(ticker):
    d = fh_get("news-sentiment", {"symbol": ticker})
    if not d:
        return {"bullish": None, "articles": 0}
    score = (d.get("sentiment") or {}).get("bullishPercent")
    buzz  = (d.get("buzz") or {}).get("articlesInLastWeek", 0)
    return {"bullish": round(score * 100, 1) if score else None, "articles": buzz}

def fh_news(ticker, frm, to, limit=2):
    """Devolve até `limit` notícias recentes do ticker."""
    d = fh_get("company-news", {"symbol": ticker, "from": frm, "to": to})
    if not d or not isinstance(d, list):
        return []
    out = []
    seen = set()
    for item in d[:40]:  # percorrer no máx 40 para evitar duplicados
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

def build_sector(name, cfg, frm, to):
    tickers = cfg["tickers"]

    # --- Cotações ---
    quotes = []
    for t in tickers:
        q = fh_quote(t)
        if q and q["price"] > 0:
            quotes.append(q)
        time.sleep(0.12)

    quotes_sorted = sorted(quotes, key=lambda x: x["changePct"], reverse=True)
    gainers = quotes_sorted[:5]
    losers  = quotes_sorted[-5:][::-1]

    # --- Sentimento ---
    sentiments = []
    for t in cfg["sentiment_tickers"]:
        s = fh_sentiment(t)
        s["ticker"] = t
        sentiments.append(s)
        time.sleep(0.2)

    valid   = [s["bullish"] for s in sentiments if s["bullish"] is not None]
    avg_bull = round(sum(valid) / len(valid), 1) if valid else None

    # --- Notícias (substitui Reddit/StockTwits) ---
    news = []
    seen_headlines = set()
    for t in cfg["news_tickers"]:
        for item in fh_news(t, frm, to, limit=2):
            if item["headline"] not in seen_headlines:
                seen_headlines.add(item["headline"])
                news.append(item)
        time.sleep(0.15)
        if len(news) >= 5:
            break
    # Ordenar por data desc, manter top 5
    news.sort(key=lambda x: x["datetime"], reverse=True)
    news = news[:5]

    avg_chg = round(sum(q["changePct"] for q in quotes) / len(quotes), 2) if quotes else 0

    return {
        "name":      name,
        "icon":      cfg["icon"],
        "color":     cfg["color"],
        "image":     cfg["image"],
        "avgChange": avg_chg,
        "gainers":   gainers,
        "losers":    losers,
        "sentiment": {"bullishPct": avg_bull, "details": sentiments},
        "news":      news
    }

def main():
    now  = datetime.datetime.utcnow()
    hour = now.hour
    slot = "abertura" if hour < 10 else ("meio-dia" if hour < 14 else "fecho")

    today = now.date()
    frm   = (today - datetime.timedelta(days=4)).isoformat()
    to    = today.isoformat()

    sectors = {}
    for name, cfg in SECTORS.items():
        print(f"Setor: {name}")
        try:
            sectors[name] = build_sector(name, cfg, frm, to)
        except Exception as e:
            print(f"  ERRO: {e}")
            sectors[name] = {
                "name": name, "icon": cfg["icon"], "color": cfg["color"],
                "image": cfg["image"], "avgChange": 0,
                "gainers": [], "losers": [],
                "sentiment": {"bullishPct": None, "details": []},
                "news": []
            }
        time.sleep(1)

    out = {"updated": now.isoformat() + "Z", "slot": slot, "sectors": sectors}
    with open("markets.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"markets.json OK ({slot})")

if __name__ == "__main__":
    main()
