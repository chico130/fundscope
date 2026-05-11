#!/usr/bin/env python3
"""
update_markets.py — FundScope v2.1

Fixes:
  - period="5d" em vez de "2d" para garantir dados mesmo ao inicio da semana
  - Slot detection corrigida: detecta pre-mercado de segunda-feira
  - updated timestamp mostra data do ultimo fecho, nao UTC now()
  - changePct calcula sempre fecho[-1] vs fecho[-2] (dias de mercado aberto)
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

# ------------------------------------------------------------------ slot
def get_slot(now_utc):
    """
    Determina o slot com base na hora UTC e dia da semana.
    Mercado NYSE: 14:30-21:00 UTC
    Pre-mercado segunda (08:00 UTC = 09:00 WEST): dados ainda sao de sexta
    """
    weekday = now_utc.weekday()  # 0=Monday
    hour    = now_utc.hour
    minute  = now_utc.minute

    # Antes da abertura do mercado (14:30 UTC) -> abertura (dados pre-mercado/fecho anterior)
    if hour < 8:
        return "fecho"          # corrida noturna improvavel mas segura
    if hour < 12:
        return "abertura"       # snapshot matinal (09:00 / 10:00 WEST)
    if hour < 15 or (hour == 15 and minute < 30):
        return "meio-dia"       # snapshot meio-dia (13:00 WEST)
    return "fecho"              # snapshot fecho (16:30 WEST)

def is_market_open(now_utc):
    """NYSE esta aberto: dias uteis, 14:30-21:00 UTC"""
    if now_utc.weekday() >= 5:
        return False
    minutes = now_utc.hour * 60 + now_utc.minute
    return 870 <= minutes < 1260  # 14:30=870, 21:00=1260

# ------------------------------------------------------------------ yfinance
def fetch_all_quotes():
    """
    Batch download de todos os tickers.
    Usa period=5d para garantir pelo menos 2 dias de mercado aberto
    mesmo em inicio de semana ou apos feriados.
    """
    all_tickers = []
    for cfg in SECTORS.values():
        all_tickers.extend(cfg["tickers"])
    all_tickers = list(dict.fromkeys(all_tickers))

    print(f"A descarregar {len(all_tickers)} cotacoes via yfinance (period=5d)...")
    try:
        data = yf.download(
            all_tickers,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True
        )
    except Exception as e:
        print(f"  [ERRO yfinance download]: {e}")
        return {}, None

    # Extrair Close
    try:
        if hasattr(data.columns, 'levels'):
            close = data.xs("Close", axis=1, level=0)
        elif "Close" in data.columns:
            close = data["Close"]
        else:
            print("  [ERRO] coluna Close nao encontrada")
            return {}, None
    except Exception as e:
        print(f"  [ERRO] extrair Close: {e}")
        return {}, None

    quotes = {}
    last_close_date = None

    for t in all_tickers:
        try:
            col = close[t] if t in close.columns else None
            if col is None:
                print(f"  [SKIP] {t}: ticker ausente")
                continue
            vals = col.dropna()
            if len(vals) < 1:
                print(f"  [SKIP] {t}: sem dados")
                continue
            price = float(vals.iloc[-1])
            pc    = float(vals.iloc[-2]) if len(vals) >= 2 else price
            chg   = round((price - pc) / pc * 100, 2) if pc else 0.0
            quotes[t] = {"ticker": t, "price": round(price, 2), "changePct": chg, "pc": round(pc, 2)}
            # guardar data do ultimo fecho disponivel
            if last_close_date is None:
                try:
                    last_close_date = vals.index[-1].date().isoformat()
                except Exception:
                    pass
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")

    print(f"  {len(quotes)}/{len(all_tickers)} cotacoes obtidas")
    if last_close_date:
        print(f"  Ultimo fecho: {last_close_date}")
    return quotes, last_close_date

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
    now_utc = datetime.datetime.utcnow()
    slot    = get_slot(now_utc)
    market_open = is_market_open(now_utc)

    today = now_utc.date()
    # janela de noticias: ultimos 5 dias para apanhar fim-de-semana + hoje
    frm = (today - datetime.timedelta(days=5)).isoformat()
    to  = today.isoformat()

    print(f"UTC: {now_utc.strftime('%Y-%m-%d %H:%M')} | slot: {slot} | mercado aberto: {market_open}")

    # 1. Batch download cotacoes
    all_quotes, last_close_date = fetch_all_quotes()

    # updated: se mercado ainda nao abriu usa data do ultimo fecho, caso contrario now
    if market_open or last_close_date is None:
        updated_ts = now_utc.isoformat() + "Z"
    else:
        # dados sao do fecho anterior (ex: sexta-feira)
        # usa a data do ultimo fecho + hora de fecho NYSE (21:00 UTC)
        updated_ts = f"{last_close_date}T21:00:00Z"

    # 2. Construir setores
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

    out = {
        "updated": updated_ts,
        "slot": slot,
        "marketOpen": market_open,
        "lastCloseDate": last_close_date,
        "sectors": sectors
    }
    with open("markets.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nmarkets.json OK | slot={slot} | updated={updated_ts}")

if __name__ == "__main__":
    main()
