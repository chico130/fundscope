#!/usr/bin/env python3
"""
update_news.py — FundScope
Fontes: GNews (geopolitica/global) + Finnhub market-news (financeiro)
Gera: news.json
"""

import json, os, time, datetime, requests, re

GNEWS_TOKEN = os.environ.get("GNEWS_TOKEN", "")
FH_TOKEN    = os.environ.get("FINNHUB_TOKEN", "")
FH_BASE     = "https://finnhub.io/api/v1"
GN_BASE     = "https://gnews.io/api/v4"

# Mapeamento keyword -> impacto em tickers/setores
IMPACT_MAP = [
    {"keywords": ["oil","crude","opec","petroleum","petróleo"],      "tickers": ["XOM","CVX","COP","OXY","SLB"],  "sector": "Energia"},
    {"keywords": ["fed","federal reserve","interest rate","inflation","taxa de juro"], "tickers": ["JPM","GS","BAC","TLT"],  "sector": "Finanças"},
    {"keywords": ["china","tariff","trade war","pautas","tarifas"],  "tickers": ["AAPL","NVDA","TSLA","AMD"],       "sector": "Tecnologia"},
    {"keywords": ["nvidia","semiconductor","chip","ai","artificial intelligence"], "tickers": ["NVDA","AMD","INTC","AVGO"], "sector": "Tecnologia"},
    {"keywords": ["gold","silver","copper","ouro","prata","cobre"],  "tickers": ["NEM","FCX","GOLD","WPM"],        "sector": "Commodities"},
    {"keywords": ["ukraine","russia","war","guerra","nato","otan"],  "tickers": ["LMT","RTX","NOC"],              "sector": "Defesa"},
    {"keywords": ["amazon","retail","consumer","ecommerce"],         "tickers": ["AMZN","WMT","TGT","COST"],      "sector": "Consumo"},
    {"keywords": ["pharma","drug","fda","vaccine","cancer","medicamento"], "tickers": ["PFE","LLY","JNJ","MRK"], "sector": "Saúde"},
    {"keywords": ["bank","banking","credit","loan","mortgage"],      "tickers": ["JPM","BAC","WFC","C"],          "sector": "Finanças"},
    {"keywords": ["tesla","electric vehicle","ev","elon"],            "tickers": ["TSLA","RIVN","GM"],             "sector": "Automóvel"},
    {"keywords": ["apple","iphone","mac","ios"],                     "tickers": ["AAPL"],                         "sector": "Tecnologia"},
    {"keywords": ["microsoft","azure","openai","copilot"],           "tickers": ["MSFT"],                         "sector": "Tecnologia"},
    {"keywords": ["energy","renewables","solar","wind","nuclear"],   "tickers": ["NEE","ENPH","FSLR","CEG"],     "sector": "Energia"},
    {"keywords": ["iran","middle east","israel","sanctions"],        "tickers": ["XOM","CVX","LMT","RTX"],       "sector": "Geopolítica"},
    {"keywords": ["dollar","euro","currency","forex","yuan"],       "tickers": ["GS","MS","JPM"],               "sector": "Finanças"},
]

CATEGORY_MAP = [
    {"keywords": ["war","guerra","military","nato","iran","ukraine","russia","israel","sanctions","missile","attack","troops"], "cat": "Geopolítica", "icon": "🌍"},
    {"keywords": ["fed","ecb","central bank","inflation","gdp","recession","interest rate","cpi","unemployment","pib","recessão"], "cat": "Macro", "icon": "📊"},
    {"keywords": ["oil","gas","opec","crude","energy","solar","wind","nuclear","petróleo"], "cat": "Energia", "icon": "⚡"},
    {"keywords": ["tariff","trade","wto","export","import","supply chain","china trade","tarifas"], "cat": "Comércio", "icon": "🚢"},
    {"keywords": ["fed","rate","bond","bank","finance","stock","market","wall street","nasdaq","s&p"], "cat": "Mercados", "icon": "💰"},
    {"keywords": ["ai","tech","chip","semiconductor","nvidia","apple","microsoft","google","amazon"], "cat": "Tecnologia", "icon": "💻"},
    {"keywords": ["climate","carbon","cop","environment","floods","drought","wildfire"], "cat": "Clima", "icon": "🌱"},
]

DEFAULT_IMAGES = [
    "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=800&q=80",
    "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
    "https://images.unsplash.com/photo-1526304640581-d334cdbbf45e?w=800&q=80",
    "https://images.unsplash.com/photo-1569025743873-ea3a9ade89f9?w=800&q=80",
    "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&q=80",
]

def classify(text):
    """Devolve (categoria, icon) com base no texto."""
    low = text.lower()
    for rule in CATEGORY_MAP:
        if any(k in low for k in rule["keywords"]):
            return rule["cat"], rule["icon"]
    return "Global", "🌐"

def get_impact(text):
    """Devolve lista de tickers afetados, setor e sentimento (positive/negative/neutral)."""
    low = text.lower()
    matches = []
    for rule in IMPACT_MAP:
        if any(k in low for k in rule["keywords"]):
            matches.append(rule)
    if not matches:
        return {"tickers": [], "sector": "", "sentiment": "neutral"}
    tickers = []
    sector  = matches[0]["sector"]
    for m in matches:
        tickers.extend(m["tickers"])
    # dedup
    seen = set()
    tickers = [t for t in tickers if not (t in seen or seen.add(t))]
    tickers = tickers[:5]
    # sentimento por keywords negativas/positivas
    neg = ["war","crash","recession","sanction","ban","fall","drop","decline","crisis","threat","attack","cut","loss"]
    pos = ["deal","growth","surge","rise","rally","record","beat","approve","expand","boom","invest","profit"]
    neg_score = sum(1 for w in neg if w in low)
    pos_score = sum(1 for w in pos if w in low)
    if neg_score > pos_score:
        sentiment = "negative"
    elif pos_score > neg_score:
        sentiment = "positive"
    else:
        sentiment = "neutral"
    return {"tickers": tickers, "sector": sector, "sentiment": sentiment}

def heat_score(impact, tickers_count):
    """1-3: quantos setores/tickers afeta."""
    n = len(impact["tickers"])
    if n >= 4: return 3
    if n >= 2: return 2
    return 1

def fetch_gnews():
    """Busca notícias geopolíticas e globais via GNews."""
    if not GNEWS_TOKEN:
        print("  [WARN] GNEWS_TOKEN nao definido")
        return []
    queries = [
        "geopolitics war sanctions trade",
        "federal reserve inflation economy",
        "oil energy markets global",
    ]
    articles = []
    seen_titles = set()
    for q in queries:
        try:
            url = f"{GN_BASE}/search"
            params = {
                "q": q, "lang": "en", "country": "us",
                "max": 5, "token": GNEWS_TOKEN,
                "sortby": "publishedAt"
            }
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            for a in data.get("articles", []):
                title = a.get("title", "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                full_text = title + " " + (a.get("description") or "")
                cat, icon = classify(full_text)
                impact = get_impact(full_text)
                img = a.get("image") or ""
                # fallback image
                if not img or "placeholder" in img.lower():
                    img = DEFAULT_IMAGES[len(articles) % len(DEFAULT_IMAGES)]
                articles.append({
                    "id":        abs(hash(title)) % (10**9),
                    "source":    a.get("source", {}).get("name", "GNews"),
                    "title":     title[:180],
                    "summary":   (a.get("description") or "")[:320],
                    "content":   (a.get("content") or a.get("description") or "")[:900],
                    "url":       a.get("url", ""),
                    "image":     img,
                    "publishedAt": a.get("publishedAt", ""),
                    "category":  cat,
                    "icon":      icon,
                    "impact":    impact,
                    "heat":      heat_score(impact, len(impact["tickers"])),
                    "feed":      "global"
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"  [WARN GNews] {q}: {e}")
    return articles

def fetch_finnhub_news():
    """Busca notícias de mercado via Finnhub general news."""
    if not FH_TOKEN:
        print("  [WARN] FINNHUB_TOKEN nao definido")
        return []
    try:
        r = requests.get(f"{FH_BASE}/news", params={"category": "general", "token": FH_TOKEN}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [WARN Finnhub news]: {e}")
        return []

    articles = []
    seen = set()
    for a in data[:40]:
        title = (a.get("headline") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        full_text = title + " " + (a.get("summary") or "")
        cat, icon  = classify(full_text)
        impact     = get_impact(full_text)
        img        = a.get("image") or ""
        if not img:
            img = DEFAULT_IMAGES[len(articles) % len(DEFAULT_IMAGES)]
        ts = a.get("datetime", 0)
        iso = datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else ""
        articles.append({
            "id":          abs(hash(title)) % (10**9),
            "source":      a.get("source", "Finnhub"),
            "title":       title[:180],
            "summary":     (a.get("summary") or "")[:320],
            "content":     (a.get("summary") or "")[:900],
            "url":         a.get("url", ""),
            "image":       img,
            "publishedAt": iso,
            "category":    cat,
            "icon":        icon,
            "impact":      impact,
            "heat":        heat_score(impact, len(impact["tickers"])),
            "feed":        "markets"
        })
        if len(articles) >= 20:
            break
    return articles

def merge_and_sort(gnews, fhnews):
    """Merge, deduplica por titulo, ordena por data desc."""
    all_articles = gnews + fhnews
    seen = set()
    deduped = []
    for a in all_articles:
        key = a["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(a)
    # ordena por publishedAt desc
    def parse_dt(a):
        try:
            return datetime.datetime.fromisoformat(a["publishedAt"].replace("Z","+00:00"))
        except:
            return datetime.datetime.min
    deduped.sort(key=parse_dt, reverse=True)
    return deduped[:30]  # max 30 artigos

def main():
    print("A buscar noticias...")
    gnews  = fetch_gnews()
    print(f"  GNews: {len(gnews)} artigos")
    fhnews = fetch_finnhub_news()
    print(f"  Finnhub: {len(fhnews)} artigos")

    articles = merge_and_sort(gnews, fhnews)
    print(f"  Total apos merge: {len(articles)} artigos")

    out = {
        "updated":  datetime.datetime.utcnow().isoformat() + "Z",
        "articles": articles
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("news.json OK")

if __name__ == "__main__":
    main()
