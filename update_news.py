#!/usr/bin/env python3
"""
update_news.py — FundScope
Fontes: GNews + NewsAPI + Finnhub
Gera: news.json com conteudo completo (scraping leve do artigo original)
"""

import json, os, re, time, datetime, requests
from html.parser import HTMLParser

GNEWS_TOKEN   = os.environ.get("GNEWS_TOKEN", "")
NEWSAPI_TOKEN = os.environ.get("NEWSAPI_TOKEN", "")
FH_TOKEN      = os.environ.get("FINNHUB_TOKEN", "")
FH_BASE       = "https://finnhub.io/api/v1"
GN_BASE       = "https://gnews.io/api/v4"
NA_BASE       = "https://newsapi.org/v2"

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
    {"keywords":["nvidia","semiconductor","chip","artificial intelligence","ai model","machine learning"],
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
    {"keywords":["war","military","nato","iran","ukraine","russia","israel","sanctions","missile","troops","ceasefire","geopolit","conflict","pentagon","troops","diplomat"],
     "cat":"Geopol\u00edtica","icon":"\uD83C\uDF0D"},
    {"keywords":["fed","ecb","central bank","inflation","gdp","recession","interest rate","cpi","unemployment","monetary policy","fomc","jerome powell","lagarde"],
     "cat":"Macro","icon":"\uD83D\uDCCA"},
    {"keywords":["oil","gas","opec","crude","energy","solar","wind","nuclear","lng","pipeline","petrol"],
     "cat":"Energia","icon":"\u26A1"},
    {"keywords":["tariff","trade","wto","export","import","supply chain","china trade","customs","protectionism","trade deal"],
     "cat":"Com\u00e9rcio","icon":"\uD83D\uDEA2"},
    {"keywords":["stock market","wall street","nasdaq","s&p","dow jones","bond","yield","earnings","ipo","hedge fund","rally","selloff","shares","dividends","market cap"],
     "cat":"Mercados","icon":"\uD83D\uDCB0"},
    {"keywords":["ai ","artificial intelligence","chip","semiconductor","nvidia","apple","microsoft","google","amazon","tech","software","cyber","quantum","openai","llm"],
     "cat":"Tecnologia","icon":"\uD83D\uDCBB"},
    {"keywords":["climate","carbon","cop","environment","floods","drought","wildfire","global warming","emissions","renewable"],
     "cat":"Clima","icon":"\uD83C\uDF31"},
]

# ── Classificação e impacto ──────────────────────────────────────────────

def classify(text):
    low = text.lower()
    for rule in CATEGORY_MAP:
        if any(k in low for k in rule["keywords"]):
            return rule["cat"], rule["icon"]
    return "Global", "\uD83C\uDF10"

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
    neg = ["war","crash","recession","sanction","ban","fall","drop","decline","crisis","threat","attack","loss","default","collapse","sell"]
    pos = ["deal","growth","surge","rise","rally","record","beat","approve","expand","boom","invest","profit","agreement","recovery","buy"]
    neg_s = sum(1 for w in neg if w in low)
    pos_s = sum(1 for w in pos if w in low)
    sent = "negative" if neg_s > pos_s else ("positive" if pos_s > neg_s else "neutral")
    return {"tickers": tickers[:5], "sector": matches[0]["sector"], "sentiment": sent}

def heat_score(impact):
    n = len(impact["tickers"])
    return 3 if n >= 4 else (2 if n >= 2 else 1)

def fallback_img(cat):
    return FALLBACK_IMAGES.get(cat, FALLBACK_IMAGES["Global"])

def clean_text(text):
    """Remove artefactos do GNews/NewsAPI e limpa o texto."""
    if not text: return ""
    text = re.sub(r'\.\.\.\s*\[\d+ chars\]$', '', text)   # GNews truncation
    text = re.sub(r'\[\+\d+ chars\]$', '', text)            # NewsAPI truncation
    text = re.sub(r'<[^>]+>', ' ', text)                    # HTML tags residuais
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ── Scraping leve do artigo original ─────────────────────────────────────────

class TextExtractor(HTMLParser):
    """Extrai texto de paragrafos <p> de uma página HTML."""
    def __init__(self):
        super().__init__()
        self._in_p = False
        self._paras = []
        self._buf = ""
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
            self._buf = ""
            self._in_p = False

    def handle_data(self, data):
        if self._in_p and self._skip == 0: self._buf += data

def scrape_article(url, existing_content="", min_len=400):
    """
    Se o conteudo existente e curto (<min_len chars), tenta extrair
    paragrafos completos do artigo original.
    Devolve o melhor texto disponivel (scraping ou existente).
    """
    if len(existing_content) >= min_len:
        return existing_content  # ja e suficiente
    if not url or not url.startswith("http"):
        return existing_content
    # Dominios que bloqueiam scrapers — evitar timeout
    BLOCKED = ["wsj.com","ft.com","bloomberg.com","nytimes.com",
               "washingtonpost.com","economist.com","barrons.com"]
    if any(b in url for b in BLOCKED):
        return existing_content
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; FundScopeBot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type",""):
            return existing_content
        parser = TextExtractor()
        parser.feed(r.text[:80000])   # limita a 80 KB para velocidade
        paras = parser._paras
        if not paras:
            return existing_content
        # Pega nos 6 primeiros paragrafos nao-triviais
        good = [p for p in paras if len(p) > 80][:6]
        if not good:
            return existing_content
        scraped = "\n\n".join(good)
        # So usa o scraping se for substancialmente maior
        return scraped if len(scraped) > len(existing_content) + 100 else existing_content
    except Exception as e:
        print(f"    [scrape skip] {url[:60]} — {e}")
        return existing_content

# ── GNews ────────────────────────────────────────────────────────────────

def fetch_gnews():
    if not GNEWS_TOKEN:
        print("  [SKIP] GNEWS_TOKEN nao definido")
        return []
    # 3 queries por execucao (10 pedidos/dia, workflow 3x) — nao excede limite
    queries = [
        "geopolitics war sanctions economy",
        "federal reserve inflation oil energy markets",
        "artificial intelligence trade tariffs technology",
    ]
    articles, seen = [], set()
    for q in queries:
        try:
            r = requests.get(f"{GN_BASE}/search", params={
                "q": q, "lang": "en", "max": 10,
                "token": GNEWS_TOKEN, "sortby": "publishedAt"
            }, timeout=10)
            r.raise_for_status()
            for a in r.json().get("articles", []):
                title = (a.get("title") or "").strip()
                if not title or title in seen: continue
                seen.add(title)
                desc    = clean_text(a.get("description") or "")
                content = clean_text(a.get("content") or "")
                # Junta description + content evitando repeticao
                if content and content[:80] != desc[:80]:
                    raw_content = desc + "\n\n" + content
                else:
                    raw_content = desc or content
                full_text = title + " " + raw_content
                cat, icon = classify(full_text)
                impact    = get_impact(full_text)
                img = a.get("image") or ""
                if not img or len(img) < 12: img = fallback_img(cat)
                # Tenta enriquecer com o artigo original se o texto for curto
                enriched = scrape_article(a.get("url",""), raw_content)
                articles.append({
                    "id":          abs(hash(title)) % (10**9),
                    "source":      (a.get("source") or {}).get("name", "GNews"),
                    "title":       title[:200],
                    "summary":     desc[:500],
                    "content":     enriched[:3000],
                    "url":         a.get("url", ""),
                    "image":       img,
                    "publishedAt": a.get("publishedAt", ""),
                    "category":    cat, "icon": icon,
                    "impact":      impact, "heat": heat_score(impact),
                    "feed":        "gnews"
                })
            time.sleep(0.3)
        except Exception as e:
            print(f"  [WARN GNews] {q}: {e}")
    print(f"  GNews: {len(articles)} artigos")
    return articles

# ── NewsAPI ────────────────────────────────────────────────────────────────

def fetch_newsapi():
    if not NEWSAPI_TOKEN:
        print("  [SKIP] NEWSAPI_TOKEN nao definido")
        return []
    # NewsAPI gratuito: 100 pedidos/dia, 100 artigos por pedido
    # Usamos /v2/top-headlines (business) + /v2/everything com queries financeiras
    articles, seen = [], set()

    # 1. Top headlines de negocios
    try:
        r = requests.get(f"{NA_BASE}/top-headlines", params={
            "category": "business", "language": "en",
            "pageSize": 30, "apiKey": NEWSAPI_TOKEN
        }, timeout=10)
        r.raise_for_status()
        for a in r.json().get("articles", []):
            _ingest_newsapi(a, articles, seen, "business-headlines")
    except Exception as e:
        print(f"  [WARN NewsAPI headlines]: {e}")

    # 2. Queries especializadas em /everything
    queries_na = [
        "stock market earnings Wall Street",
        "geopolitics war sanctions diplomacy",
        "inflation Federal Reserve interest rates",
        "oil OPEC energy commodities",
        "artificial intelligence semiconductor technology",
        "trade war tariffs China exports",
        "climate change renewable energy environment",
    ]
    for q in queries_na:
        if len(articles) >= 80: break
        try:
            r = requests.get(f"{NA_BASE}/everything", params={
                "q": q, "language": "en", "sortBy": "publishedAt",
                "pageSize": 15, "apiKey": NEWSAPI_TOKEN
            }, timeout=10)
            r.raise_for_status()
            for a in r.json().get("articles", []):
                _ingest_newsapi(a, articles, seen, "newsapi")
            time.sleep(0.2)
        except Exception as e:
            print(f"  [WARN NewsAPI] {q}: {e}")

    print(f"  NewsAPI: {len(articles)} artigos")
    return articles

def _ingest_newsapi(a, articles, seen, feed):
    title = (a.get("title") or "").strip()
    if not title or title in seen or title == "[Removed]": return
    seen.add(title)
    desc    = clean_text(a.get("description") or "")
    content = clean_text(a.get("content") or "")  # NewsAPI trunca a ~200 chars no plano free
    if content and content[:80] != desc[:80]:
        raw_content = desc + "\n\n" + content
    else:
        raw_content = desc or content
    full_text = title + " " + raw_content
    cat, icon = classify(full_text)
    impact    = get_impact(full_text)
    img = a.get("urlToImage") or ""
    if not img or len(img) < 12: img = fallback_img(cat)
    url = a.get("url", "")
    # NewsAPI trunca o conteudo — tenta scraping se for curto
    enriched = scrape_article(url, raw_content)
    articles.append({
        "id":          abs(hash(title)) % (10**9),
        "source":      (a.get("source") or {}).get("name", "NewsAPI"),
        "title":       title[:200],
        "summary":     desc[:500],
        "content":     enriched[:3000],
        "url":         url,
        "image":       img,
        "publishedAt": a.get("publishedAt", ""),
        "category":    cat, "icon": icon,
        "impact":      impact, "heat": heat_score(impact),
        "feed":        feed
    })

# ── Finnhub ────────────────────────────────────────────────────────────────

def fetch_finnhub():
    if not FH_TOKEN:
        print("  [SKIP] FINNHUB_TOKEN nao definido")
        return []
    try:
        r = requests.get(f"{FH_BASE}/news",
            params={"category": "general", "token": FH_TOKEN}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [WARN Finnhub]: {e}")
        return []
    articles, seen = [], set()
    for a in data[:60]:
        title   = (a.get("headline") or "").strip()
        if not title or title in seen: continue
        seen.add(title)
        summary = clean_text(a.get("summary") or "")
        full_text = title + " " + summary
        cat, icon = classify(full_text)
        impact    = get_impact(full_text)
        img = a.get("image") or ""
        if not img or len(img) < 12: img = fallback_img(cat)
        ts  = a.get("datetime", 0)
        iso = datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else ""
        url = a.get("url", "")
        # Finnhub so tem summary — tenta sempre enriquecer com o artigo original
        enriched = scrape_article(url, summary, min_len=200)
        articles.append({
            "id":          abs(hash(title)) % (10**9),
            "source":      a.get("source", "Finnhub"),
            "title":       title[:200],
            "summary":     summary[:500],
            "content":     enriched[:3000],
            "url":         url,
            "image":       img,
            "publishedAt": iso,
            "category":    cat, "icon": icon,
            "impact":      impact, "heat": heat_score(impact),
            "feed":        "finnhub"
        })
        if len(articles) >= 25: break
    print(f"  Finnhub: {len(articles)} artigos")
    return articles

# ── Merge, dedup, sort ──────────────────────────────────────────────────────────

def parse_dt(a):
    try:
        return datetime.datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00"))
    except:
        return datetime.datetime.min

def merge_and_sort(*sources):
    all_a = [a for src in sources for a in src]
    seen, deduped = set(), []
    for a in all_a:
        key = a["title"][:70].lower()
        if key not in seen:
            seen.add(key); deduped.append(a)
    deduped.sort(key=parse_dt, reverse=True)
    return deduped[:50]  # guarda ate 50 artigos

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=== FundScope News Update ===")
    gnews   = fetch_gnews()
    newsapi = fetch_newsapi()
    fh      = fetch_finnhub()
    articles = merge_and_sort(gnews, newsapi, fh)
    print(f"\nTotal final: {len(articles)} artigos")
    from collections import Counter
    cats = Counter(a["category"] for a in articles)
    for cat, n in sorted(cats.items(), key=lambda x:-x[1]):
        avg_len = sum(len(a["content"]) for a in articles if a["category"]==cat) // max(n,1)
        print(f"  {cat}: {n} artigos (media {avg_len} chars de conteudo)")
    out = {"updated": datetime.datetime.utcnow().isoformat()+"Z", "articles": articles}
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nnews.json escrito com sucesso.")

if __name__ == "__main__":
    main()
