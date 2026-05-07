#!/usr/bin/env python3
"""
update_markets.py
Corre 3x/dia via GitHub Actions: 08:00, 12:00, 16:30 UTC
Produz markets.json com:
  - top 5 gainers / losers por setor
  - sentimento Finnhub por setor
  - posts Reddit (r/wallstreetbets, r/investing)
  - posts StockTwits por ticker representativo
"""

import json, os, time, datetime, requests
from collections import defaultdict

FH_TOKEN = os.environ.get("FINNHUB_TOKEN", "")
HEADERS_REDDIT = {"User-Agent": "FundScope/1.0 (educational project)"}

SECTORS = {
    "Tecnologia": {
        "icon": "💻",
        "color": "#4f98a3",
        "image": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
        "tickers": ["AAPL","MSFT","NVDA","GOOGL","META","AMD","INTC","TSLA","ASML","AVGO","MU","QCOM","ORCL","CRM","ADBE"],
        "reddit": ["wallstreetbets","investing","stocks"],
        "sentiment_tickers": ["AAPL","NVDA","MSFT"]
    },
    "Finanças": {
        "icon": "🏦",
        "color": "#437a22",
        "image": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
        "tickers": ["JPM","BAC","GS","MS","WFC","C","BLK","AXP","V","MA","SCHW","BRK-B","USB","PNC","TFC"],
        "reddit": ["investing","stocks","ValueInvesting"],
        "sentiment_tickers": ["JPM","GS","BAC"]
    },
    "Energia": {
        "icon": "⚡",
        "color": "#c47d1a",
        "image": "https://images.unsplash.com/photo-1509391366360-2e959784a276?w=800&q=80",
        "tickers": ["XOM","CVX","COP","SLB","EOG","PSX","MPC","VLO","OXY","HAL","BP","SHEL","NEE","D","DUK"],
        "reddit": ["investing","energy","stocks"],
        "sentiment_tickers": ["XOM","CVX","NEE"]
    },
    "Saúde": {
        "icon": "🏥",
        "color": "#a13544",
        "image": "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=800&q=80",
        "tickers": ["JNJ","UNH","PFE","ABBV","MRK","LLY","TMO","ABT","DHR","BMY","AMGN","GILD","MDT","CVS","ISRG"],
        "reddit": ["investing","biotech","stocks"],
        "sentiment_tickers": ["JNJ","LLY","PFE"]
    },
    "Consumo": {
        "icon": "🛒",
        "color": "#7a5c9e",
        "image": "https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=800&q=80",
        "tickers": ["AMZN","WMT","HD","MCD","SBUX","NKE","TGT","COST","LOW","TJX","PG","KO","PEP","PM","CL"],
        "reddit": ["wallstreetbets","investing","stocks"],
        "sentiment_tickers": ["AMZN","WMT","MCD"]
    },
    "Commodities": {
        "icon": "🪙",
        "color": "#8a7340",
        "image": "https://images.unsplash.com/photo-1610375461246-83df859d849d?w=800&q=80",
        "tickers": ["GLD","SLV","USO","UNG","CORN","WEAT","FCX","NEM","GOLD","AA","CLF","X","MP","VALE","RIO"],
        "reddit": ["investing","Gold","Silver"],
        "sentiment_tickers": ["GLD","USO","FCX"]
    }
}

def fh_quote(ticker):
    try:
        r = requests.get(
            f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FH_TOKEN}",
            timeout=6
        )
        d = r.json()
        price = d.get("c") or d.get("pc") or 0
        pc = d.get("pc") or price
        chg = ((price - pc) / pc * 100) if pc else 0
        return {"ticker": ticker, "price": round(price, 2), "changePct": round(chg, 2), "pc": round(pc, 2)}
    except:
        return None

def fh_sentiment(ticker):
    try:
        today = datetime.date.today()
        frm = (today - datetime.timedelta(days=3)).isoformat()
        r = requests.get(
            f"https://finnhub.io/api/v1/news-sentiment?symbol={ticker}&token={FH_TOKEN}",
            timeout=6
        )
        d = r.json()
        score = d.get("sentiment", {}).get("bullishPercent", None)
        buzz = d.get("buzz", {}).get("articlesInLastWeek", 0)
        return {"bullish": round(score * 100, 1) if score else None, "articles": buzz}
    except:
        return {"bullish": None, "articles": 0}

def reddit_posts(subreddits, keywords, limit=3):
    posts = []
    seen = set()
    for sub in subreddits:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json?limit=25",
                headers=HEADERS_REDDIT, timeout=8
            )
            items = r.json().get("data", {}).get("children", [])
            for item in items:
                d = item["data"]
                title = d.get("title", "")
                if any(k.lower() in title.lower() for k in keywords):
                    uid = d.get("id")
                    if uid in seen:
                        continue
                    seen.add(uid)
                    ups = d.get("ups", 0)
                    posts.append({
                        "source": f"r/{sub}",
                        "title": title[:120],
                        "ups": ups,
                        "url": "https://reddit.com" + d.get("permalink", ""),
                        "author": d.get("author", "anonymous"),
                        "comments": d.get("num_comments", 0)
                    })
        except:
            pass
        time.sleep(0.5)
    posts.sort(key=lambda x: x["ups"], reverse=True)
    return posts[:limit]

def stocktwits_posts(ticker, limit=2):
    try:
        r = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json",
            timeout=8
        )
        msgs = r.json().get("messages", [])
        out = []
        for m in msgs[:limit]:
            sentiment = m.get("entities", {}).get("sentiment", {})
            label = sentiment.get("basic", "") if sentiment else ""
            body = m.get("body", "")[:140]
            user = m.get("user", {}).get("username", "?")
            out.append({"source": "StockTwits", "body": body, "user": user, "sentiment": label})
        return out
    except:
        return []

def build_sector(name, cfg):
    tickers = cfg["tickers"]
    quotes = []
    for t in tickers:
        q = fh_quote(t)
        if q and q["price"] > 0:
            quotes.append(q)
        time.sleep(0.15)

    quotes.sort(key=lambda x: x["changePct"], reverse=True)
    gainers = quotes[:5]
    losers = sorted(quotes, key=lambda x: x["changePct"])[:5]

    # Sentimento Finnhub
    sentiments = []
    for t in cfg["sentiment_tickers"]:
        s = fh_sentiment(t)
        s["ticker"] = t
        sentiments.append(s)
        time.sleep(0.2)

    avg_bull = None
    valid = [s["bullish"] for s in sentiments if s["bullish"] is not None]
    if valid:
        avg_bull = round(sum(valid) / len(valid), 1)

    # Reddit
    keywords = [t.replace("-"," ") for t in cfg["sentiment_tickers"]] + [name]
    reddit = reddit_posts(cfg["reddit"], keywords, limit=3)

    # StockTwits do ticker mais movimentado
    top_ticker = gainers[0]["ticker"] if gainers else tickers[0]
    if abs(losers[0]["changePct"]) > abs(gainers[0]["changePct"]):
        top_ticker = losers[0]["ticker"]
    twits = stocktwits_posts(top_ticker, limit=2)

    # Sector change médio
    avg_chg = round(sum(q["changePct"] for q in quotes) / len(quotes), 2) if quotes else 0

    return {
        "name": name,
        "icon": cfg["icon"],
        "color": cfg["color"],
        "image": cfg["image"],
        "avgChange": avg_chg,
        "gainers": gainers,
        "losers": losers,
        "sentiment": {"bullishPct": avg_bull, "details": sentiments},
        "reddit": reddit,
        "twits": twits
    }

def main():
    now = datetime.datetime.utcnow()
    hour = now.hour
    if hour < 10:
        slot = "abertura"
    elif hour < 14:
        slot = "meio-dia"
    else:
        slot = "fecho"

    sectors = {}
    for name, cfg in SECTORS.items():
        print(f"A processar setor: {name}")
        sectors[name] = build_sector(name, cfg)
        time.sleep(1)

    out = {
        "updated": now.isoformat() + "Z",
        "slot": slot,
        "sectors": sectors
    }

    with open("markets.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"markets.json gerado ({slot})")

if __name__ == "__main__":
    main()
