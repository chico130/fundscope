#!/usr/bin/env python3
"""
update_news.py — FundScope
Fontes: RSS (Yahoo/CNBC/MarketWatch/Investing/FT/Economist) + MarketAux + Alpha Vantage + Finnhub
Gera: news.json
"""

import json, os, re, sys, time, datetime, traceback
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("[ERRO] Instala requests: pip install requests")
    sys.exit(1)

# ─── Tokens ─────────────────────────────────────────────────────────────────
MARKETAUX_TOKEN    = os.environ.get("MARKETAUX_TOKEN",    "").strip()
ALPHAVANTAGE_TOKEN = os.environ.get("ALPHAVANTAGE_TOKEN", "").strip()
FH_TOKEN           = os.environ.get("FINNHUB_TOKEN",      "").strip()
NEWSAPI_TOKEN      = os.environ.get("NEWSAPI_TOKEN",      "").strip()

print(f"Tokens: MarketAux={'sim' if MARKETAUX_TOKEN else 'NAO'} | "
      f"AlphaVantage={'sim' if ALPHAVANTAGE_TOKEN else 'NAO'} | "
      f"Finnhub={'sim' if FH_TOKEN else 'NAO'} | "
      f"NewsAPI={'sim' if NEWSAPI_TOKEN else 'NAO'}")

# ─── RSS Feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Yahoo Finance
    {"url": "https://finance.yahoo.com/news/rssindex",              "source": "Yahoo Finance"},
    {"url": "https://finance.yahoo.com/rss/topstories",             "source": "Yahoo Finance"},
    # CNBC
    {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "source": "CNBC"},
    {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",  "source": "CNBC Markets"},
    {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",  "source": "CNBC Tech"},
    # MarketWatch
    {"url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",   "source": "MarketWatch"},
    {"url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",  "source": "MarketWatch"},
    # Reuters — novo dominio (feeds.reuters.com está morto)
    {"url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best", "source": "Reuters"},
    # Investing.com
    {"url": "https://www.investing.com/rss/news.rss",               "source": "Investing.com"},
    {"url": "https://www.investing.com/rss/news_25.rss",             "source": "Investing.com Tech"},
    # Seeking Alpha
    {"url": "https://seekingalpha.com/market_currents.xml",          "source": "Seeking Alpha"},
    # Financial Times
    {"url": "https://www.ft.com/rss/home/uk",                       "source": "Financial Times"},
    # The Economist
    {"url": "https://www.economist.com/finance-and-economics/rss.xml", "source": "The Economist"},
    # Business Insider
    {"url": "https://markets.businessinsider.com/rss/news",          "source": "Business Insider"},
    # Nasdaq News
    {"url": "https://www.nasdaq.com/feed/rssoutbound?category=Markets", "source": "Nasdaq"},
    {"url": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks",  "source": "Nasdaq"},
]

# ─── Imagens de fallback ─────────────────────────────────────────────────────
FALLBACK_IMAGES = {
    "Geopolítica": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&q=80",
    "Macro":       "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
    "Energia":     "https://images.unsplash.com/photo-1509391366360-2e959784a276?w=800&q=80",
    "Comércio":    "https://images.unsplash.com/photo-1578575437130-527eed3abbec?w=800&q=80",
    "Mercados":    "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?w=800&q=80",
    "Tecnologia":  "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
    "Clima":       "https://images.unsplash.com/photo-1569025743873-ea3a9ade89f9?w=800&q=80",
    "Global":      "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800&q=80",
}

# ─── Mapeamentos ───────────────────────────────────────────────────────────────
IMPACT_MAP = [
    {"keywords":["oil","crude","opec","petroleum"],             "tickers":["XOM","CVX","COP","OXY","SLB"],     "sector":"Energia"},
    {"keywords":["fed","federal reserve","interest rate","inflation","ecb","rate cut","rate hike","fomc"],
                                                                "tickers":["JPM","GS","BAC","TLT"],            "sector":"Finanças"},
    {"keywords":["china","tariff","trade war","customs"],       "tickers":["AAPL","NVDA","TSLA","AMD"],        "sector":"Tecnologia"},
    {"keywords":["nvidia","semiconductor","chip","artificial intelligence","ai model"],
                                                                "tickers":["NVDA","AMD","INTC","AVGO"],        "sector":"Tecnologia"},
    {"keywords":["gold","silver","copper","mining","metals"],  "tickers":["NEM","FCX","GOLD","WPM"],          "sector":"Commodities"},
    {"keywords":["ukraine","russia","war","nato","missile","troops","ceasefire"],
                                                                "tickers":["LMT","RTX","NOC","XOM"],           "sector":"Defesa"},
    {"keywords":["amazon","retail","consumer spending","ecommerce"],"tickers":["AMZN","WMT","TGT","COST"],   "sector":"Consumo"},
    {"keywords":["pharma","drug","fda","vaccine","cancer","biotech"],"tickers":["PFE","LLY","JNJ","MRK","AMGN"],"sector":"Saúde"},
    {"keywords":["bank","banking","credit","mortgage"],         "tickers":["JPM","BAC","WFC","C"],             "sector":"Finanças"},
    {"keywords":["tesla","electric vehicle","elon musk","battery"],"tickers":["TSLA","RIVN","GM"],            "sector":"Automóvel"},
    {"keywords":["apple","iphone","ios","app store"],           "tickers":["AAPL"],                            "sector":"Tecnologia"},
    {"keywords":["microsoft","azure","openai","copilot"],       "tickers":["MSFT"],                            "sector":"Tecnologia"},
    {"keywords":["solar","wind energy","nuclear","renewables","clean energy"],
                                                                "tickers":["NEE","ENPH","FSLR","CEG"],         "sector":"Energia"},
    {"keywords":["iran","middle east","israel","sanctions","gaza"],
                                                                "tickers":["XOM","CVX","LMT","RTX"],           "sector":"Geopolítica"},
    {"keywords":["dollar","euro","yuan","forex","currency"],   "tickers":["GS","MS","JPM"],                   "sector":"Finanças"},
    {"keywords":["recession","gdp","unemployment","cpi","economic growth"],
                                                                "tickers":["SPY","QQQ","JPM","GS"],            "sector":"Macro"},
]

CATEGORY_MAP = [
    {"keywords":["war","military","nato","iran","ukraine","russia","israel","sanctions","missile","troops","ceasefire","geopolit","conflict","diplomat"],
     "cat":"Geopolítica","icon":"🌍"},
    {"keywords":["fed","ecb","central bank","inflation","gdp","recession","interest rate","cpi","unemployment","monetary policy","fomc","jerome powell","lagarde"],
     "cat":"Macro","icon":"📊"},
    {"keywords":["oil","gas","opec","crude","energy","solar","wind","nuclear","lng","pipeline","petrol"],
     "cat":"Energia","icon":"⚡"},
    {"keywords":["tariff","trade","wto","export","import","supply chain","china trade","customs","protectionism","trade deal"],
     "cat":"Comércio","icon":"🚢"},
    {"keywords":["stock market","wall street","nasdaq","s&p","dow jones","bond","yield","earnings","ipo","hedge fund","rally","selloff","shares","dividends"],
     "cat":"Mercados","icon":"💰"},
    {"keywords":["ai ","artificial intelligence","chip","semiconductor","nvidia","apple","microsoft","google","amazon","tech","software","cyber","quantum","openai","llm"],
     "cat":"Tecnologia","icon":"💻"},
    {"keywords":["climate","carbon","cop","environment","floods","drought","wildfire","global warming","emissions","renewable"],
     "cat":"Clima","icon":"🌱"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

UTC = datetime.timezone.utc

# ─── Helpers ─────────────────────────────────────────────────────────────────

def sanitize(text):
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

def clean_text(text):
    if not text: return ""
    text = re.sub(r"\.{3}\s*\[\d+ chars\]$", "", text)
    text = re.sub(r"\[\+\d+ chars\]$", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return sanitize(text)

def classify(text):
    low = text.lower()
    for rule in CATEGORY_MAP:
        if any(k in low for k in rule["keywords"]):
            return rule["cat"], rule["icon"]
    return "Global", "🌐"

def get_impact(text):
    low = text.lower()
    matches = [r for r in IMPACT_MAP if any(k in low for k in r["keywords"])]
    if not matches:
        return {"tickers": [], "sector": "", "sentiment": "neutral"}
    tickers, seen = [], set()
    for m in matches:
        for t in m["tickers"]:
            if t not in seen:
                seen.add(t); tickers.append(t)
    neg_words = ["war","crash","recession","sanction","ban","fall","drop","decline","crisis","threat","attack","loss","default","collapse"]
    pos_words = ["deal","growth","surge","rise","rally","record","beat","approve","expand","boom","invest","profit","agreement","recovery"]
    ns = sum(1 for w in neg_words if w in low)
    ps = sum(1 for w in pos_words if w in low)
    sent = "negative" if ns > ps else ("positive" if ps > ns else "neutral")
    return {"tickers": tickers[:5], "sector": matches[0]["sector"], "sentiment": sent}

def heat_score(impact):
    n = len(impact["tickers"])
    return 3 if n >= 4 else (2 if n >= 2 else 1)

def fallback_img(cat):
    return FALLBACK_IMAGES.get(cat, FALLBACK_IMAGES["Global"])

def now_utc():
    return datetime.datetime.now(UTC)

def ts_to_iso(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts), UTC).isoformat()
    except:
        return ""

def make_id(title):
    return abs(hash(title)) % (10**9)

def parse_rss_date(s):
    """Converte datas RSS para ISO 8601 com fuso horário."""
    if not s: return ""
    s = s.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}T", s):
        if s.endswith("Z"):
            return s[:-1] + "+00:00"
        if "+" not in s[10:] and not s.endswith("+00:00"):
            return s + "+00:00"
        return s
    try:
        import email.utils
        tt = email.utils.parsedate_to_datetime(s)
        # Garante fuso horário
        if tt.tzinfo is None:
            tt = tt.replace(tzinfo=UTC)
        return tt.isoformat()
    except:
        return ""

def parse_dt(a):
    """Sempre devolve datetime aware (com timezone UTC) para ordenar sem TypeError."""
    fallback = datetime.datetime.min.replace(tzinfo=UTC)
    try:
        s = a.get("publishedAt", "")
        if not s:
            return fallback
        s = s.strip()
        # Normalizar formatos comuns
        s = s.replace("Z", "+00:00")
        # Alpha Vantage: YYYYMMDDTHHMMSS
        if re.match(r"\d{8}T\d{6}$", s):
            s = f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:{s[13:15]}+00:00"
        dt = datetime.datetime.fromisoformat(s)
        # Se naive, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except:
        return fallback

def strip_cdata(s):
    if not s: return ""
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.DOTALL).strip()

def rss_get_text(elem, *tags):
    ns_variants = [
        "",
        "{http://purl.org/rss/1.0/modules/content/}",
        "{http://www.w3.org/2005/Atom}",
        "{http://search.yahoo.com/mrss/}",
    ]
    for tag in tags:
        for ns in ns_variants:
            el = elem.find(ns + tag)
            if el is not None and el.text:
                return strip_cdata(clean_text(el.text))
    return ""

# ─── RSS ─────────────────────────────────────────────────────────────────────

def fetch_rss():
    articles, seen = [], set()
    for feed in RSS_FEEDS:
        url, source = feed["url"], feed["source"]
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                print(f"  [RSS {source}] HTTP {r.status_code}")
                continue
            try:
                root = ET.fromstring(r.content)
            except ET.ParseError as e:
                print(f"  [RSS {source}] XML parse error: {e}")
                continue

            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            count_new = 0
            for item in items[:20]:
                title = rss_get_text(item, "title")
                if not title or title in seen: continue
                seen.add(title)

                link = rss_get_text(item, "link", "guid")
                if not link or not link.startswith("http"):
                    link_el = item.find("{http://www.w3.org/2005/Atom}link")
                    if link_el is not None:
                        link = link_el.get("href", "")

                desc = rss_get_text(item, "description", "summary", "content:encoded", "content")
                pub  = parse_rss_date(rss_get_text(item, "pubDate", "published", "updated", "dc:date"))

                img = ""
                for ns_img in ["{http://search.yahoo.com/mrss/}", ""]:
                    th = item.find(ns_img + "thumbnail")
                    if th is not None:
                        img = th.get("url", "") or th.text or ""
                        break
                if not img:
                    enc = item.find("enclosure")
                    if enc is not None and "image" in (enc.get("type") or ""):
                        img = enc.get("url", "")
                if not img:
                    m = re.search(r'<img[^>]+src=["\']([^"\'>]+)', desc or "")
                    if m: img = m.group(1)

                full_text = title + " " + desc
                cat, icon = classify(full_text)
                impact    = get_impact(full_text)
                if not img or len(img) < 12: img = fallback_img(cat)

                articles.append({
                    "id":          make_id(title),
                    "source":      source,
                    "title":       title[:200],
                    "summary":     desc[:600],
                    "content":     desc[:5000],
                    "url":         link,
                    "image":       img,
                    "publishedAt": pub,
                    "category":    cat,
                    "icon":        icon,
                    "impact":      impact,
                    "heat":        heat_score(impact),
                    "feed":        "rss",
                })
                count_new += 1

            print(f"  [RSS] {source}: {count_new} artigos")
            time.sleep(0.3)

        except Exception:
            print(f"  [WARN RSS {source}]:\n{traceback.format_exc()}")

    print(f"  RSS total: {len(articles)} artigos")
    return articles


# ─── MarketAux ───────────────────────────────────────────────────────────────

def fetch_marketaux():
    if not MARKETAUX_TOKEN:
        print("  [SKIP] MARKETAUX_TOKEN nao definido")
        return []
    articles, seen = [], set()
    try:
        r = requests.get(
            "https://api.marketaux.com/v1/news/all",
            params={
                "api_token":       MARKETAUX_TOKEN,
                "language":        "en",
                "limit":           50,
                "published_after": (now_utc() - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            },
            timeout=12,
        )
        if r.status_code in (422, 429):
            print(f"  [MarketAux] HTTP {r.status_code} — limite ou erro")
            return []
        r.raise_for_status()
        data = r.json()
        for a in data.get("data", []):
            title = sanitize((a.get("title") or "").strip())
            if not title or title in seen: continue
            seen.add(title)

            desc   = clean_text(a.get("description") or a.get("snippet") or "")
            link   = sanitize(a.get("url", ""))
            img    = sanitize(a.get("image_url") or "")
            pub    = sanitize(a.get("published_at") or "")
            source = sanitize(a.get("source") or "MarketAux")

            raw_ents   = a.get("entities") or []
            ma_tickers = [e["symbol"] for e in raw_ents if e.get("symbol") and e.get("type") == "equity"][:5]
            ma_sentiment = "neutral"
            for e in raw_ents:
                s = (e.get("sentiment_score") or 0)
                if   s >  0.1: ma_sentiment = "positive"; break
                elif s < -0.1: ma_sentiment = "negative"; break

            full_text = title + " " + desc
            cat, icon = classify(full_text)
            impact    = get_impact(full_text)
            if ma_tickers: impact["tickers"] = ma_tickers
            impact["sentiment"] = ma_sentiment
            if not img or len(img) < 12: img = fallback_img(cat)

            articles.append({
                "id":          make_id(title),
                "source":      source,
                "title":       title[:200],
                "summary":     desc[:600],
                "content":     desc[:5000],
                "url":         link,
                "image":       img,
                "publishedAt": pub,
                "category":    cat,
                "icon":        icon,
                "impact":      impact,
                "heat":        heat_score(impact),
                "feed":        "marketaux",
            })
    except Exception:
        print(f"  [WARN MarketAux]:\n{traceback.format_exc()}")

    print(f"  MarketAux: {len(articles)} artigos")
    return articles


# ─── Alpha Vantage ───────────────────────────────────────────────────────────

def fetch_alphavantage():
    if not ALPHAVANTAGE_TOKEN:
        print("  [SKIP] ALPHAVANTAGE_TOKEN nao definido")
        return []
    articles, seen = [], set()
    topics = ["financial_markets", "technology", "economy_macro", "energy_transportation"]
    for topic in topics:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "topics":   topic,
                    "limit":    50,
                    "sort":     "LATEST",
                    "apikey":   ALPHAVANTAGE_TOKEN,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()

            if "Note" in data or "Information" in data:
                msg = data.get("Note") or data.get("Information", "")
                print(f"  [AlphaVantage {topic}] limite: {msg[:100]}")
                break

            for a in data.get("feed", []):
                title = sanitize((a.get("title") or "").strip())
                if not title or title in seen: continue
                seen.add(title)

                desc   = clean_text(a.get("summary") or "")
                link   = sanitize(a.get("url", ""))
                img    = sanitize(a.get("banner_image") or "")
                source = sanitize(a.get("source") or "Alpha Vantage")

                # AV usa YYYYMMDDTHHMMSS — converter para ISO com fuso
                raw_pub = a.get("time_published") or ""
                if raw_pub and re.match(r"\d{8}T\d{6}", raw_pub):
                    pub = f"{raw_pub[:4]}-{raw_pub[4:6]}-{raw_pub[6:8]}T{raw_pub[9:11]}:{raw_pub[11:13]}:{raw_pub[13:15]}+00:00"
                else:
                    pub = raw_pub

                av_sent_label = (a.get("overall_sentiment_label") or "").lower()
                av_sentiment  = "positive" if "bullish" in av_sent_label else ("negative" if "bearish" in av_sent_label else "neutral")
                av_tickers    = [t["ticker"] for t in (a.get("ticker_sentiment") or []) if t.get("ticker") and not t["ticker"].startswith("CRYPTO")][:5]

                full_text = title + " " + desc
                cat, icon = classify(full_text)
                impact    = get_impact(full_text)
                if av_tickers: impact["tickers"] = av_tickers
                impact["sentiment"] = av_sentiment
                if not img or len(img) < 12: img = fallback_img(cat)

                articles.append({
                    "id":          make_id(title),
                    "source":      source,
                    "title":       title[:200],
                    "summary":     desc[:600],
                    "content":     desc[:5000],
                    "url":         link,
                    "image":       img,
                    "publishedAt": pub,
                    "category":    cat,
                    "icon":        icon,
                    "impact":      impact,
                    "heat":        heat_score(impact),
                    "feed":        "alphavantage",
                })

            count_av = len([x for x in articles if x["feed"] == "alphavantage"])
            print(f"  [AlphaVantage {topic}]: {count_av} acumulados")
            time.sleep(13)  # AV free: 5 req/min
        except Exception:
            print(f"  [WARN AlphaVantage {topic}]:\n{traceback.format_exc()}")

    total_av = len([x for x in articles if x["feed"] == "alphavantage"])
    print(f"  AlphaVantage: {total_av} artigos")
    return articles


# ─── Finnhub ─────────────────────────────────────────────────────────────────

def fetch_finnhub():
    if not FH_TOKEN:
        print("  [SKIP] FINNHUB_TOKEN nao definido")
        return []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": FH_TOKEN},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        print(f"  [WARN Finnhub]:\n{traceback.format_exc()}")
        return []

    articles, seen = [], set()
    for a in data[:40]:
        title = sanitize((a.get("headline") or "").strip())
        if not title or title in seen: continue
        seen.add(title)
        summary = clean_text(a.get("summary") or "")
        link    = sanitize(a.get("url", ""))
        img     = sanitize(a.get("image") or "")
        pub     = ts_to_iso(a.get("datetime", 0))  # já devolve ISO com +00:00
        source  = sanitize(a.get("source", "Finnhub"))

        full_text = title + " " + summary
        cat, icon = classify(full_text)
        impact    = get_impact(full_text)
        if not img or len(img) < 12: img = fallback_img(cat)

        articles.append({
            "id":          make_id(title),
            "source":      source,
            "title":       title[:200],
            "summary":     summary[:600],
            "content":     summary[:5000],
            "url":         link,
            "image":       img,
            "publishedAt": pub,
            "category":    cat,
            "icon":        icon,
            "impact":      impact,
            "heat":        heat_score(impact),
            "feed":        "finnhub",
        })

    print(f"  Finnhub: {len(articles)} artigos")
    return articles


# ─── Finnhub company news ─────────────────────────────────────────────────────

def fetch_finnhub_company(tickers: list) -> list:
    if not FH_TOKEN:
        print("  [SKIP] FINNHUB_TOKEN nao definido (company news)")
        return []

    today     = datetime.date.today()
    from_date = (today - datetime.timedelta(days=7)).isoformat()
    to_date   = today.isoformat()

    articles, seen = [], set()
    for ticker in tickers:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": ticker, "from": from_date, "to": to_date, "token": FH_TOKEN},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            print(f"  [WARN Finnhub company {ticker}]:\n{traceback.format_exc()}")
            time.sleep(0.5)
            continue

        for a in data[:5]:
            title = sanitize((a.get("headline") or "").strip())
            if not title or title in seen:
                continue
            seen.add(title)
            summary = clean_text(a.get("summary") or "")
            link    = sanitize(a.get("url", ""))
            img     = sanitize(a.get("image") or "")
            pub     = ts_to_iso(a.get("datetime", 0))
            source  = sanitize(a.get("source", "Finnhub"))

            full_text = title + " " + summary
            cat, icon = classify(full_text)
            impact    = get_impact(full_text)

            if ticker not in impact["tickers"]:
                impact["tickers"].insert(0, ticker)

            if not img or len(img) < 12:
                img = fallback_img(cat)

            articles.append({
                "id":          make_id(title),
                "source":      source,
                "title":       title[:200],
                "summary":     summary[:600],
                "content":     summary[:5000],
                "url":         link,
                "image":       img,
                "publishedAt": pub,
                "category":    cat,
                "icon":        icon,
                "impact":      impact,
                "heat":        heat_score(impact),
                "feed":        "finnhub_company",
            })

        time.sleep(0.15)

    print(f"  Finnhub company: {len(articles)} artigos ({min(len(tickers),10)} tickers)")
    return articles


# ─── NewsAPI.org ─────────────────────────────────────────────────────────────

def fetch_newsapi(tickers: list) -> list:
    """
    Fetch company-specific news from NewsAPI.org.
    Batches tickers in groups of 5 → max 5 requests/run × 4 runs/day = 20 req/day
    (developer plan limit: 100 req/day).
    """
    if not NEWSAPI_TOKEN:
        print("  [SKIP] NEWSAPI_TOKEN não definido")
        return []

    articles, seen = [], set()
    batch_size = 5

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        query = " OR ".join(batch)
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q":        query,
                    "language": "en",
                    "sortBy":   "publishedAt",
                    "pageSize": 20,
                    "apiKey":   NEWSAPI_TOKEN,
                },
                timeout=12,
            )
            if r.status_code == 426:
                print("  [NewsAPI] 426 — plano developer requer query diferente, a tentar top-headlines")
                break
            if r.status_code == 429:
                print("  [NewsAPI] 429 — limite diário atingido")
                break
            r.raise_for_status()
            data = r.json()

            for a in (data.get("articles") or []):
                title = sanitize((a.get("title") or "").strip())
                if not title or title in seen or "[Removed]" in title:
                    continue
                seen.add(title)

                desc   = clean_text(a.get("description") or "")
                link   = sanitize(a.get("url") or "")
                img    = sanitize(a.get("urlToImage") or "")
                pub    = sanitize(a.get("publishedAt") or "")
                source = sanitize((a.get("source") or {}).get("name") or "NewsAPI")

                # Which tickers from this batch are mentioned?
                full_upper = (title + " " + desc).upper()
                matched = [t for t in batch if t.upper() in full_upper]

                full_text = title + " " + desc
                cat, icon = classify(full_text)
                impact    = get_impact(full_text)
                for t in matched:
                    if t not in impact["tickers"]:
                        impact["tickers"].insert(0, t)

                if not img or len(img) < 12:
                    img = fallback_img(cat)

                articles.append({
                    "id":          make_id(title),
                    "source":      source,
                    "title":       title[:200],
                    "summary":     desc[:600],
                    "content":     desc[:5000],
                    "url":         link,
                    "image":       img,
                    "publishedAt": pub,
                    "category":    cat,
                    "icon":        icon,
                    "impact":      impact,
                    "heat":        heat_score(impact),
                    "feed":        "newsapi",
                })

            batch_n = i // batch_size + 1
            batch_total = (len(tickers) + batch_size - 1) // batch_size
            print(f"  [NewsAPI batch {batch_n}/{batch_total}] query='{query[:40]}…': "
                  f"{data.get('totalResults',0)} resultados disponíveis")
            time.sleep(0.5)

        except Exception:
            print(f"  [WARN NewsAPI batch {batch}]:\n{traceback.format_exc()}")

    print(f"  NewsAPI: {len(articles)} artigos ({len(tickers)} tickers)")
    return articles


def _load_watchlist_tickers() -> list:
    try:
        p = os.path.join("data", "beta", "watchlist.json")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return [c["ticker"] for c in data.get("candidates", [])]
    except Exception:
        return []


# ─── Merge & Sort ────────────────────────────────────────────────────────────

def merge_and_sort(*sources):
    all_a = [a for src in sources for a in src]
    seen, deduped = set(), []
    for a in all_a:
        key = a["title"][:70].lower()
        if key not in seen:
            seen.add(key); deduped.append(a)
    # parse_dt garante sempre datetime aware — sem TypeError
    deduped.sort(key=parse_dt, reverse=True)
    return deduped[:60]


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=== FundScope News Update ===")
    rss_arts = ma_arts = av_arts = fh_arts = fhc_arts = na_arts = []

    try:    rss_arts = fetch_rss()
    except: print(traceback.format_exc())
    try:    ma_arts  = fetch_marketaux()
    except: print(traceback.format_exc())
    try:    av_arts  = fetch_alphavantage()
    except: print(traceback.format_exc())
    try:    fh_arts  = fetch_finnhub()
    except: print(traceback.format_exc())
    try:
        wl_tickers = _load_watchlist_tickers()
        if wl_tickers:
            fhc_arts = fetch_finnhub_company(wl_tickers)
            na_arts  = fetch_newsapi(wl_tickers)
    except: print(traceback.format_exc())

    articles = merge_and_sort(rss_arts, ma_arts, av_arts, fh_arts, fhc_arts, na_arts)
    print(f"\nTotal final: {len(articles)} artigos")

    from collections import Counter
    cats  = Counter(a["category"] for a in articles)
    feeds = Counter(a["feed"]     for a in articles)
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n}")
    print("  Feeds:", dict(feeds))

    out = {"updated": now_utc().isoformat(), "articles": articles}
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=True, indent=2, default=lambda o: repr(o))

    print("\nnews.json escrito com sucesso.")
    sys.exit(0)

if __name__ == "__main__":
    main()
