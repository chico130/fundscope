#!/usr/bin/env python3
"""
update_news.py — FundScope
Fontes: RSS (Yahoo/CNBC/Reuters/Investing) + MarketAux + Alpha Vantage + Finnhub
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

print(f"Tokens: MarketAux={'sim' if MARKETAUX_TOKEN else 'NAO'} | "
      f"AlphaVantage={'sim' if ALPHAVANTAGE_TOKEN else 'NAO'} | "
      f"Finnhub={'sim' if FH_TOKEN else 'NAO'}")

# ─── RSS Feeds (sem API key, sem limites) ────────────────────────────────────
RSS_FEEDS = [
    # Yahoo Finance
    {"url": "https://finance.yahoo.com/news/rssindex",              "source": "Yahoo Finance"},
    {"url": "https://finance.yahoo.com/rss/topstories",             "source": "Yahoo Finance"},
    # CNBC
    {"url": "https://feeds.nbcnews.com/nbcnews/public/business",     "source": "CNBC"},
    {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "source": "CNBC"},
    {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",  "source": "CNBC Markets"},
    # Reuters
    {"url": "https://feeds.reuters.com/reuters/businessNews",        "source": "Reuters"},
    {"url": "https://feeds.reuters.com/reuters/technologyNews",      "source": "Reuters Tech"},
    # MarketWatch
    {"url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",   "source": "MarketWatch"},
    {"url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",  "source": "MarketWatch"},
    # Investing.com
    {"url": "https://www.investing.com/rss/news.rss",               "source": "Investing.com"},
    {"url": "https://www.investing.com/rss/news_25.rss",             "source": "Investing.com"},
    # Seeking Alpha
    {"url": "https://seekingalpha.com/market_currents.xml",          "source": "Seeking Alpha"},
    # Financial Times
    {"url": "https://www.ft.com/rss/home/uk",                       "source": "Financial Times"},
    # Barron's / Dow Jones
    {"url": "https://www.barrons.com/xml/rss/3_7431.xml",            "source": "Barron's"},
    # The Economist
    {"url": "https://www.economist.com/finance-and-economics/rss.xml", "source": "The Economist"},
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

# ─── Mapeamentos de impacto e categoria ─────────────────────────────────────
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
    return datetime.datetime.now(datetime.timezone.utc)

def ts_to_iso(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).isoformat()
    except:
        return ""

def parse_dt(a):
    try:
        s = a.get("publishedAt", "")
        if not s: return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

def make_id(title):
    return abs(hash(title)) % (10**9)

def parse_rss_date(s):
    """Converte datas RSS (RFC 2822 e ISO 8601) para ISO 8601."""
    if not s: return ""
    s = s.strip()
    # ISO 8601
    if re.match(r"\d{4}-\d{2}-\d{2}T", s):
        return s.replace("Z", "+00:00")
    # RFC 2822: "Mon, 10 May 2026 14:30:00 +0000"
    try:
        import email.utils
        tt = email.utils.parsedate_to_datetime(s)
        return tt.isoformat()
    except:
        return s

def strip_cdata(s):
    if not s: return ""
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.DOTALL).strip()

def rss_get_text(elem, *tags):
    """Procura o primeiro tag disponivel num elemento XML e retorna o texto limpo."""
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
            # Tentar parsear XML
            try:
                root = ET.fromstring(r.content)
            except ET.ParseError as e:
                print(f"  [RSS {source}] XML parse error: {e}")
                continue

            # Suporte RSS 2.0 e Atom
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            count_new = 0
            for item in items[:20]:
                title = rss_get_text(item, "title")
                if not title or title in seen: continue
                seen.add(title)

                link  = rss_get_text(item, "link", "guid")
                if not link or not link.startswith("http"):
                    # Atom usa <link href=...>
                    link_el = item.find("{http://www.w3.org/2005/Atom}link")
                    if link_el is not None:
                        link = link_el.get("href", "")

                desc    = rss_get_text(item, "description", "summary", "content:encoded", "content")
                pub     = parse_rss_date(rss_get_text(item, "pubDate", "published", "updated", "dc:date"))

                # Imagem: tentar media:thumbnail / enclosure
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
                    # Tentar extrair do HTML da descrição
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
                "api_token":  MARKETAUX_TOKEN,
                "language":   "en",
                "limit":      50,
                "published_after": (now_utc() - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            },
            timeout=12,
        )
        if r.status_code == 422 or r.status_code == 429:
            print(f"  [MarketAux] HTTP {r.status_code} — limite atingido ou erro")
            return []
        r.raise_for_status()
        data = r.json()
        for a in data.get("data", []):
            title = sanitize((a.get("title") or "").strip())
            if not title or title in seen: continue
            seen.add(title)

            desc     = clean_text(a.get("description") or a.get("snippet") or "")
            link     = sanitize(a.get("url", ""))
            img      = sanitize(a.get("image_url") or "")
            pub      = sanitize(a.get("published_at") or "")
            source   = sanitize(a.get("source") or "MarketAux")

            # MarketAux já devolve tickers e sentimento
            raw_ents = a.get("entities") or []
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
    # Cada chamada devolve até 50 artigos de alta qualidade (Reuters, Bloomberg, etc.)
    topics = ["financial_markets", "technology", "economy_macro", "energy_transportation"]
    for topic in topics:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function":  "NEWS_SENTIMENT",
                    "topics":    topic,
                    "limit":     50,
                    "sort":      "LATEST",
                    "apikey":    ALPHAVANTAGE_TOKEN,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()

            if "Note" in data or "Information" in data:
                msg = data.get("Note") or data.get("Information", "")
                print(f"  [AlphaVantage {topic}] {msg[:100]}")
                break

            for a in data.get("feed", []):
                title = sanitize((a.get("title") or "").strip())
                if not title or title in seen: continue
                seen.add(title)

                desc   = clean_text(a.get("summary") or "")
                link   = sanitize(a.get("url", ""))
                img    = sanitize(a.get("banner_image") or "")
                pub    = sanitize(a.get("time_published") or "")
                # AV usa formato YYYYMMDDTHHMMSS
                if pub and re.match(r"\d{8}T\d{6}", pub):
                    pub = f"{pub[:4]}-{pub[4:6]}-{pub[6:8]}T{pub[9:11]}:{pub[11:13]}:{pub[13:15]}+00:00"
                source = sanitize(a.get("source") or "Alpha Vantage")

                # Sentimento do AV
                av_sent_label = (a.get("overall_sentiment_label") or "").lower()
                av_sentiment  = "positive" if "bullish" in av_sent_label else ("negative" if "bearish" in av_sent_label else "neutral")

                # Tickers do AV
                av_tickers = [t["ticker"] for t in (a.get("ticker_sentiment") or []) if t.get("ticker") and not t["ticker"].startswith("CRYPTO")][:5]

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

            print(f"  [AlphaVantage {topic}]: {len([a for a in articles if a['feed']=='alphavantage'])} artigos acumulados")
            time.sleep(12)  # AV free: 5 req/min
        except Exception:
            print(f"  [WARN AlphaVantage {topic}]:\n{traceback.format_exc()}")

    total_av = len([a for a in articles if a["feed"] == "alphavantage"])
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
        pub     = ts_to_iso(a.get("datetime", 0))
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


# ─── Merge & Sort ────────────────────────────────────────────────────────────

def merge_and_sort(*sources):
    all_a = [a for src in sources for a in src]
    seen, deduped = set(), []
    for a in all_a:
        key = a["title"][:70].lower()
        if key not in seen:
            seen.add(key); deduped.append(a)
    deduped.sort(key=parse_dt, reverse=True)
    return deduped[:60]


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=== FundScope News Update ===")
    rss_arts = av_arts = ma_arts = fh_arts = []

    try:    rss_arts = fetch_rss()
    except: print(traceback.format_exc())

    try:    ma_arts  = fetch_marketaux()
    except: print(traceback.format_exc())

    try:    av_arts  = fetch_alphavantage()
    except: print(traceback.format_exc())

    try:    fh_arts  = fetch_finnhub()
    except: print(traceback.format_exc())

    articles = merge_and_sort(rss_arts, ma_arts, av_arts, fh_arts)
    print(f"\nTotal final: {len(articles)} artigos")

    from collections import Counter
    cats  = Counter(a["category"] for a in articles)
    feeds = Counter(a["feed"]     for a in articles)
    for cat, n in sorted(cats.items(),  key=lambda x: -x[1]):
        print(f"  {cat}: {n} artigos")
    print("  Feeds:", dict(feeds))

    out = {"updated": now_utc().isoformat(), "articles": articles}
    try:
        payload = json.dumps(out, ensure_ascii=True, indent=2)
    except Exception as e:
        print(f"[ERRO json.dumps] {e}")
        payload = json.dumps(out, ensure_ascii=True, indent=2, default=lambda o: repr(o))

    with open("news.json", "w", encoding="utf-8") as f:
        f.write(payload)

    print("\nnews.json escrito com sucesso.")

    # Avisos de cotas
    if not MARKETAUX_TOKEN:
        print("\n[AVISO] MARKETAUX_TOKEN em falta — regista-te em marketaux.com (plano gratuito: 100 req/dia)")
    if not ALPHAVANTAGE_TOKEN:
        print("[AVISO] ALPHAVANTAGE_TOKEN em falta — regista-te em alphavantage.co (plano gratuito: 25 req/dia)")

    sys.exit(0)

if __name__ == "__main__":
    main()
