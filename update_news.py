#!/usr/bin/env python3
"""
update_news.py — FundScope
Fontes: GNews + NewsAPI + Finnhub
Gera: news.json
"""

import json, os, re, sys, time, datetime, traceback, requests
from html.parser import HTMLParser

GNEWS_TOKEN   = os.environ.get("GNEWS_TOKEN", "").strip()
NEWSAPI_TOKEN = os.environ.get("NEWSAPI_TOKEN", "").strip()
FH_TOKEN      = os.environ.get("FINNHUB_TOKEN", "").strip()

print(f"Tokens presentes: GNews={'sim' if GNEWS_TOKEN else 'NAO'} | NewsAPI={'sim' if NEWSAPI_TOKEN else 'NAO'} | Finnhub={'sim' if FH_TOKEN else 'NAO'}")

FH_BASE = "https://finnhub.io/api/v1"
GN_BASE = "https://gnews.io/api/v4"
NA_BASE = "https://newsapi.org/v2"

FALLBACK_IMAGES = {
    "Geopol\u00edtica": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&q=80",
    "Macro":          "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
    "Energia":        "https://images.unsplash.com/photo-1509391366360-2e959784a276?w=800&q=80",
    "Com\u00e9rcio":  "https://images.unsplash.com/photo-1578575437130-527eed3abbec?w=800&q=80",
    "Mercados":       "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?w=800&q=80",
    "Tecnologia":     "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
    "Clima":          "https://images.unsplash.com/photo-1569025743873-ea3a9ade89f9?w=800&q=80",
    "Global":         "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800&q=80",
}

IMPACT_MAP = [
    {"keywords":["oil","crude","opec","petroleum"],            "tickers":["XOM","CVX","COP","OXY","SLB"],    "sector":"Energia"},
    {"keywords":["fed","federal reserve","interest rate","inflation","ecb","rate cut","rate hike","fomc"],
                                                               "tickers":["JPM","GS","BAC","TLT"],           "sector":"Finan\u00e7as"},
    {"keywords":["china","tariff","trade war","customs"],      "tickers":["AAPL","NVDA","TSLA","AMD"],       "sector":"Tecnologia"},
    {"keywords":["nvidia","semiconductor","chip","artificial intelligence","ai model"],
                                                               "tickers":["NVDA","AMD","INTC","AVGO"],       "sector":"Tecnologia"},
    {"keywords":["gold","silver","copper","mining","metals"], "tickers":["NEM","FCX","GOLD","WPM"],         "sector":"Commodities"},
    {"keywords":["ukraine","russia","war","nato","missile","troops","ceasefire"],
                                                               "tickers":["LMT","RTX","NOC","XOM"],          "sector":"Defesa"},
    {"keywords":["amazon","retail","consumer spending","ecommerce"],"tickers":["AMZN","WMT","TGT","COST"],  "sector":"Consumo"},
    {"keywords":["pharma","drug","fda","vaccine","cancer","biotech"],"tickers":["PFE","LLY","JNJ","MRK","AMGN"],"sector":"Sa\u00fade"},
    {"keywords":["bank","banking","credit","mortgage"],        "tickers":["JPM","BAC","WFC","C"],            "sector":"Finan\u00e7as"},
    {"keywords":["tesla","electric vehicle","elon musk","battery"],"tickers":["TSLA","RIVN","GM"],          "sector":"Autom\u00f3vel"},
    {"keywords":["apple","iphone","ios","app store"],          "tickers":["AAPL"],                           "sector":"Tecnologia"},
    {"keywords":["microsoft","azure","openai","copilot"],      "tickers":["MSFT"],                           "sector":"Tecnologia"},
    {"keywords":["solar","wind energy","nuclear","renewables","clean energy"],
                                                               "tickers":["NEE","ENPH","FSLR","CEG"],        "sector":"Energia"},
    {"keywords":["iran","middle east","israel","sanctions","gaza"],
                                                               "tickers":["XOM","CVX","LMT","RTX"],          "sector":"Geopol\u00edtica"},
    {"keywords":["dollar","euro","yuan","forex","currency"],  "tickers":["GS","MS","JPM"],                  "sector":"Finan\u00e7as"},
    {"keywords":["recession","gdp","unemployment","cpi","economic growth"],
                                                               "tickers":["SPY","QQQ","JPM","GS"],           "sector":"Macro"},
]

CATEGORY_MAP = [
    {"keywords":["war","military","nato","iran","ukraine","russia","israel","sanctions","missile","troops","ceasefire","geopolit","conflict","diplomat"],
     "cat":"Geopol\u00edtica","icon":"\U0001f30d"},
    {"keywords":["fed","ecb","central bank","inflation","gdp","recession","interest rate","cpi","unemployment","monetary policy","fomc","jerome powell","lagarde"],
     "cat":"Macro","icon":"\U0001f4ca"},
    {"keywords":["oil","gas","opec","crude","energy","solar","wind","nuclear","lng","pipeline","petrol"],
     "cat":"Energia","icon":"\u26a1"},
    {"keywords":["tariff","trade","wto","export","import","supply chain","china trade","customs","protectionism","trade deal"],
     "cat":"Com\u00e9rcio","icon":"\U0001f6a2"},
    {"keywords":["stock market","wall street","nasdaq","s&p","dow jones","bond","yield","earnings","ipo","hedge fund","rally","selloff","shares","dividends"],
     "cat":"Mercados","icon":"\U0001f4b0"},
    {"keywords":["ai ","artificial intelligence","chip","semiconductor","nvidia","apple","microsoft","google","amazon","tech","software","cyber","quantum","openai","llm"],
     "cat":"Tecnologia","icon":"\U0001f4bb"},
    {"keywords":["climate","carbon","cop","environment","floods","drought","wildfire","global warming","emissions","renewable"],
     "cat":"Clima","icon":"\U0001f331"},
]

# ---------------------------------------------------------------------------

def sanitize(text):
    """Remove surrogates e caracteres invalidos que quebram json.dump."""
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    # encode com surrogateescape e decode ignorando erros
    return text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')

def classify(text):
    low = text.lower()
    for rule in CATEGORY_MAP:
        if any(k in low for k in rule["keywords"]):
            return rule["cat"], rule["icon"]
    return "Global", "\U0001f310"

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
    neg = ["war","crash","recession","sanction","ban","fall","drop","decline","crisis","threat","attack","loss","default","collapse"]
    pos = ["deal","growth","surge","rise","rally","record","beat","approve","expand","boom","invest","profit","agreement","recovery"]
    ns = sum(1 for w in neg if w in low)
    ps = sum(1 for w in pos if w in low)
    sent = "negative" if ns > ps else ("positive" if ps > ns else "neutral")
    return {"tickers": tickers[:5], "sector": matches[0]["sector"], "sentiment": sent}

def heat_score(impact):
    n = len(impact["tickers"])
    return 3 if n >= 4 else (2 if n >= 2 else 1)

def fallback_img(cat):
    return FALLBACK_IMAGES.get(cat, FALLBACK_IMAGES["Global"])

def clean_text(text):
    if not text: return ""
    text = re.sub(r'\.\.\.\s*\[\d+ chars\]$', '', text)
    text = re.sub(r'\[\+\d+ chars\]$', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return sanitize(text)

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def ts_to_iso(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).isoformat()
    except:
        return ""

# --- scraping leve ----------------------------------------------------------

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_p = False; self._paras = []; self._buf = ""
        self._skip_tags = {"script","style","nav","header","footer","aside","figure","figcaption"}
        self._skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags: self._skip += 1
        if tag == "p" and self._skip == 0: self._in_p = True
    def handle_endtag(self, tag):
        if tag in self._skip_tags and self._skip > 0: self._skip -= 1
        if tag == "p" and self._in_p:
            t = re.sub(r'\s+', ' ', self._buf).strip()
            if len(t) > 60: self._paras.append(t)
            self._buf = ""; self._in_p = False
    def handle_data(self, data):
        if self._in_p and self._skip == 0: self._buf += data

BLOCKED_DOMAINS = ["wsj.com","ft.com","bloomberg.com","nytimes.com",
                   "washingtonpost.com","economist.com","barrons.com",
                   "reuters.com","apnews.com"]

def scrape_article(url, existing="", min_len=400):
    if len(existing) >= min_len: return existing
    if not url or not url.startswith("http"): return existing
    if any(b in url for b in BLOCKED_DOMAINS): return existing
    try:
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; FundScopeBot/1.0)",
            "Accept": "text/html"
        }, timeout=7, allow_redirects=True)
        if r.status_code != 200: return existing
        if "text/html" not in r.headers.get("Content-Type", ""): return existing
        parser = TextExtractor()
        parser.feed(r.text[:80000])
        good = [p for p in parser._paras if len(p) > 80][:6]
        if not good: return existing
        scraped = sanitize("\n\n".join(good))
        return scraped if len(scraped) > len(existing) + 100 else existing
    except Exception as ex:
        print(f"    [scrape skip] {str(url)[:60]} — {ex}")
        return existing

# --- GNews ------------------------------------------------------------------
# Plano gratuito: 10 pedidos/dia. Com workflow 3x/dia usa so 1 query por execucao.

def fetch_gnews():
    if not GNEWS_TOKEN:
        print("  [SKIP] GNEWS_TOKEN nao definido")
        return []
    # 1 unica query abrangente para nao exceder o limite diario
    query = "economy war inflation trade technology energy markets"
    articles, seen = [], set()
    try:
        r = requests.get(f"{GN_BASE}/search", params={
            "q": query, "lang": "en", "max": 10,
            "token": GNEWS_TOKEN, "sortby": "publishedAt"
        }, timeout=12)
        if r.status_code == 429:
            print("  [GNews] 429 — limite diario atingido, a saltar")
            return []
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            print(f"  [GNews error] {data['errors']}")
            return []
        for a in data.get("articles", []):
            title = sanitize((a.get("title") or "").strip())
            if not title or title in seen: continue
            seen.add(title)
            desc    = clean_text(a.get("description") or "")
            content = clean_text(a.get("content") or "")
            raw = (desc + "\n\n" + content) if (content and content[:80] != desc[:80]) else (desc or content)
            full_text = title + " " + raw
            cat, icon = classify(full_text)
            impact    = get_impact(full_text)
            img = sanitize(a.get("image") or "")
            if not img or len(img) < 12: img = fallback_img(cat)
            enriched = scrape_article(a.get("url",""), raw)
            articles.append({
                "id": abs(hash(title)) % (10**9),
                "source": sanitize((a.get("source") or {}).get("name", "GNews")),
                "title": title[:200], "summary": desc[:500],
                "content": enriched[:3000], "url": sanitize(a.get("url","")),
                "image": img, "publishedAt": sanitize(a.get("publishedAt","")),
                "category": cat, "icon": icon,
                "impact": impact, "heat": heat_score(impact), "feed": "gnews"
            })
    except Exception:
        print(f"  [WARN GNews]:\n{traceback.format_exc()}")
    print(f"  GNews: {len(articles)} artigos")
    return articles

# --- NewsAPI ----------------------------------------------------------------

def fetch_newsapi():
    if not NEWSAPI_TOKEN:
        print("  [SKIP] NEWSAPI_TOKEN nao definido")
        return []
    articles, seen = [], set()
    try:
        r = requests.get(f"{NA_BASE}/top-headlines", params={
            "category": "business", "language": "en",
            "pageSize": 30, "apiKey": NEWSAPI_TOKEN
        }, timeout=12)
        r.raise_for_status()
        resp = r.json()
        if resp.get("status") != "ok":
            print(f"  [NewsAPI headlines error] {resp.get('message')}")
        else:
            for a in resp.get("articles", []):
                _ingest_newsapi(a, articles, seen, "headlines")
    except Exception:
        print(f"  [WARN NewsAPI headlines]:\n{traceback.format_exc()}")

    queries_na = [
        "stock market earnings Wall Street",
        "geopolitics war sanctions",
        "inflation Federal Reserve interest rates",
        "oil OPEC energy",
        "artificial intelligence technology",
        "trade tariffs China",
        "climate renewable energy",
    ]
    for q in queries_na:
        if len(articles) >= 80: break
        try:
            r = requests.get(f"{NA_BASE}/everything", params={
                "q": q, "language": "en", "sortBy": "publishedAt",
                "pageSize": 15, "apiKey": NEWSAPI_TOKEN
            }, timeout=12)
            r.raise_for_status()
            resp = r.json()
            if resp.get("status") != "ok":
                print(f"  [NewsAPI error] {q}: {resp.get('message')}")
                continue
            for a in resp.get("articles", []):
                _ingest_newsapi(a, articles, seen, "newsapi")
            time.sleep(0.2)
        except Exception:
            print(f"  [WARN NewsAPI] {q}:\n{traceback.format_exc()}")

    print(f"  NewsAPI: {len(articles)} artigos")
    return articles

def _ingest_newsapi(a, articles, seen, feed):
    title = sanitize((a.get("title") or "").strip())
    if not title or title in seen or title == "[Removed]": return
    seen.add(title)
    desc    = clean_text(a.get("description") or "")
    content = clean_text(a.get("content") or "")
    raw = (desc + "\n\n" + content) if (content and content[:80] != desc[:80]) else (desc or content)
    full_text = title + " " + raw
    cat, icon = classify(full_text)
    impact    = get_impact(full_text)
    img = sanitize(a.get("urlToImage") or "")
    if not img or len(img) < 12: img = fallback_img(cat)
    url = sanitize(a.get("url", ""))
    enriched = scrape_article(url, raw)
    articles.append({
        "id": abs(hash(title)) % (10**9),
        "source": sanitize((a.get("source") or {}).get("name", "NewsAPI")),
        "title": title[:200], "summary": desc[:500],
        "content": enriched[:3000], "url": url,
        "image": img, "publishedAt": sanitize(a.get("publishedAt", "")),
        "category": cat, "icon": icon,
        "impact": impact, "heat": heat_score(impact), "feed": feed
    })

# --- Finnhub ----------------------------------------------------------------

def fetch_finnhub():
    if not FH_TOKEN:
        print("  [SKIP] FINNHUB_TOKEN nao definido")
        return []
    try:
        r = requests.get(f"{FH_BASE}/news",
            params={"category": "general", "token": FH_TOKEN}, timeout=12)
        r.raise_for_status()
        data = r.json()
    except Exception:
        print(f"  [WARN Finnhub]:\n{traceback.format_exc()}")
        return []
    articles, seen = [], set()
    for a in data[:60]:
        title = sanitize((a.get("headline") or "").strip())
        if not title or title in seen: continue
        seen.add(title)
        summary = clean_text(a.get("summary") or "")
        full_text = title + " " + summary
        cat, icon = classify(full_text)
        impact    = get_impact(full_text)
        img = sanitize(a.get("image") or "")
        if not img or len(img) < 12: img = fallback_img(cat)
        iso = ts_to_iso(a.get("datetime", 0))
        url = sanitize(a.get("url", ""))
        enriched = scrape_article(url, summary, min_len=200)
        articles.append({
            "id": abs(hash(title)) % (10**9),
            "source": sanitize(a.get("source", "Finnhub")),
            "title": title[:200], "summary": summary[:500],
            "content": enriched[:3000], "url": url,
            "image": img, "publishedAt": iso,
            "category": cat, "icon": icon,
            "impact": impact, "heat": heat_score(impact), "feed": "finnhub"
        })
        if len(articles) >= 25: break
    print(f"  Finnhub: {len(articles)} artigos")
    return articles

# --- merge ------------------------------------------------------------------

def parse_dt(a):
    try:
        s = a["publishedAt"]
        if not s: return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

def merge_and_sort(*sources):
    all_a = [a for src in sources for a in src]
    seen, deduped = set(), []
    for a in all_a:
        key = a["title"][:70].lower()
        if key not in seen:
            seen.add(key); deduped.append(a)
    deduped.sort(key=parse_dt, reverse=True)
    return deduped[:50]

# --- main -------------------------------------------------------------------

def main():
    print("=== FundScope News Update ===")
    try:    gnews   = fetch_gnews()
    except: print(traceback.format_exc()); gnews = []
    try:    newsapi = fetch_newsapi()
    except: print(traceback.format_exc()); newsapi = []
    try:    fh      = fetch_finnhub()
    except: print(traceback.format_exc()); fh = []

    articles = merge_and_sort(gnews, newsapi, fh)
    print(f"\nTotal final: {len(articles)} artigos")

    from collections import Counter
    cats = Counter(a["category"] for a in articles)
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        avg = sum(len(a["content"]) for a in articles if a["category"] == cat) // max(n,1)
        print(f"  {cat}: {n} artigos (media {avg} chars)")

    out = {
        "updated": now_utc().isoformat(),
        "articles": articles
    }

    # Serializa com sanitizacao final anti-surrogate
    try:
        payload = json.dumps(out, ensure_ascii=True, indent=2)
    except Exception as e:
        print(f"[ERRO json.dumps] {e} — a tentar com ensure_ascii=True")
        # ultimo recurso: substitui tudo que nao e ascii
        payload = json.dumps(out, ensure_ascii=True, indent=2,
                             default=lambda o: repr(o))

    with open("news.json", "w", encoding="utf-8") as f:
        f.write(payload)

    print("\nnews.json escrito com sucesso.")
    sys.exit(0)

if __name__ == "__main__":
    main()
