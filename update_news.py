#!/usr/bin/env python3
"""
update_news.py — FundScope
Fontes: GNews (geopolitica/global) + Finnhub market-news (financeiro)
Gera: news.json
"""

import json, os, time, datetime, requests

GNEWS_TOKEN = os.environ.get("GNEWS_TOKEN", "")
FH_TOKEN    = os.environ.get("FINNHUB_TOKEN", "")
FH_BASE     = "https://finnhub.io/api/v1"
GN_BASE     = "https://gnews.io/api/v4"

# Imagens fallback por categoria (Unsplash)
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

IMPACT_MAP = [
    {"keywords":["oil","crude","opec","petroleum","petróleo"],       "tickers":["XOM","CVX","COP","OXY","SLB"],  "sector":"Energia"},
    {"keywords":["fed","federal reserve","interest rate","inflation","taxa de juro","ecb","rate cut","rate hike"],"tickers":["JPM","GS","BAC","TLT"],  "sector":"Finanças"},
    {"keywords":["china","tariff","trade war","pautas","tarifas","customs"],"tickers":["AAPL","NVDA","TSLA","AMD"],"sector":"Tecnologia"},
    {"keywords":["nvidia","semiconductor","chip","artificial intelligence","machine learning","ai model"],"tickers":["NVDA","AMD","INTC","AVGO"],"sector":"Tecnologia"},
    {"keywords":["gold","silver","copper","ouro","prata","cobre","mining","metals"],"tickers":["NEM","FCX","GOLD","WPM"],"sector":"Commodities"},
    {"keywords":["ukraine","russia","war","guerra","nato","otan","missile","troops","ceasefire"],"tickers":["LMT","RTX","NOC","XOM"],"sector":"Defesa"},
    {"keywords":["amazon","retail","consumer spending","ecommerce","e-commerce"],"tickers":["AMZN","WMT","TGT","COST"],"sector":"Consumo"},
    {"keywords":["pharma","drug","fda","vaccine","cancer","clinical trial","biotech"],"tickers":["PFE","LLY","JNJ","MRK","AMGN"],"sector":"Saúde"},
    {"keywords":["bank","banking","credit","loan","mortgage","financial crisis","bailout"],"tickers":["JPM","BAC","WFC","C"],"sector":"Finanças"},
    {"keywords":["tesla","electric vehicle"," ev ","elon musk","battery"],"tickers":["TSLA","RIVN","GM"],"sector":"Automóvel"},
    {"keywords":["apple","iphone","mac ","ios","app store"],"tickers":["AAPL"],"sector":"Tecnologia"},
    {"keywords":["microsoft","azure","openai","copilot","windows"],"tickers":["MSFT"],"sector":"Tecnologia"},
    {"keywords":["solar","wind energy","nuclear","renewables","clean energy","green energy"],"tickers":["NEE","ENPH","FSLR","CEG"],"sector":"Energia"},
    {"keywords":["iran","middle east","israel","sanctions","gaza","hamas","hezbollah"],"tickers":["XOM","CVX","LMT","RTX"],"sector":"Geopolítica"},
    {"keywords":["dollar","euro","yuan","yen","forex","currency war","devaluation"],"tickers":["GS","MS","JPM"],"sector":"Finanças"},
    {"keywords":["recession","gdp","unemployment","cpi","inflation data","economic growth"],"tickers":["SPY","QQQ","JPM","GS"],"sector":"Macro"},
]

CATEGORY_MAP = [
    {"keywords":["war","guerra","military","nato","iran","ukraine","russia","israel","sanctions","missile","attack","troops","ceasefire","geopolit"],"cat":"Geopolítica","icon":"🌍"},
    {"keywords":["fed","ecb","central bank","inflation","gdp","recession","interest rate","cpi","unemployment","pib","recessão","monetary policy"],"cat":"Macro","icon":"📊"},
    {"keywords":["oil","gas","opec","crude","energy","solar","wind","nuclear","petróleo","lng","pipeline"],"cat":"Energia","icon":"⚡"},
    {"keywords":["tariff","trade","wto","export","import","supply chain","china trade","tarifas","customs","protectionism"],"cat":"Comércio","icon":"🚢"},
    {"keywords":["stock market","wall street","nasdaq","s&p","dow jones","bond","yield","earnings","ipo","hedge fund","rally","selloff"],"cat":"Mercados","icon":"💰"},
    {"keywords":["ai","chip","semiconductor","nvidia","apple","microsoft","google","amazon","tech","software","cyber","quantum"],"cat":"Tecnologia","icon":"💻"},
    {"keywords":["climate","carbon","cop","environment","floods","drought","wildfire","global warming","emissions"],"cat":"Clima","icon":"🌱"},
]

def classify(text):
    low = text.lower()
    for rule in CATEGORY_MAP:
        if any(k in low for k in rule["keywords"]):
            return rule["cat"], rule["icon"]
    return "Global", "🌐"

def get_impact(text):
    low = text.lower()
    matches = []
    for rule in IMPACT_MAP:
        if any(k in low for k in rule["keywords"]):
            matches.append(rule)
    if not matches:
        return {"tickers":[], "sector":"", "sentiment":"neutral"}
    tickers, seen = [], set()
    sector = matches[0]["sector"]
    for m in matches:
        for t in m["tickers"]:
            if t not in seen:
                seen.add(t); tickers.append(t)
    tickers = tickers[:5]
    neg=["war","crash","recession","sanction","ban","fall","drop","decline","crisis","threat","attack","cut","loss","default","collapse"]
    pos=["deal","growth","surge","rise","rally","record","beat","approve","expand","boom","invest","profit","agreement","recovery"]
    neg_score=sum(1 for w in neg if w in low)
    pos_score=sum(1 for w in pos if w in low)
    sentiment="negative" if neg_score>pos_score else ("positive" if pos_score>neg_score else "neutral")
    return {"tickers":tickers,"sector":sector,"sentiment":sentiment}

def heat_score(impact):
    n=len(impact["tickers"])
    return 3 if n>=4 else (2 if n>=2 else 1)

def get_fallback_image(category):
    return FALLBACK_IMAGES.get(category, FALLBACK_IMAGES["Global"])

def clean_content(text):
    """Remove o sufixo '... [X chars]' que o GNews por vezes adiciona."""
    if not text:
        return ""
    import re
    text = re.sub(r'\.\.\.\s*\[\d+ chars\]$', '', text).strip()
    return text

def fetch_gnews():
    if not GNEWS_TOKEN:
        print("  [WARN] GNEWS_TOKEN nao definido")
        return []
    # Mais queries e mais artigos por query para cobrir mais categorias
    queries = [
        "geopolitics war conflict sanctions",
        "federal reserve inflation interest rates economy",
        "oil energy OPEC markets",
        "China US trade tariffs technology",
        "climate change environment COP",
        "stock market Wall Street earnings",
        "artificial intelligence semiconductor nvidia",
    ]
    articles = []
    seen_titles = set()
    for q in queries:
        try:
            params = {
                "q": q, "lang": "en", "country": "us",
                "max": 6, "token": GNEWS_TOKEN,
                "sortby": "publishedAt"
            }
            r = requests.get(f"{GN_BASE}/search", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            for a in data.get("articles", []):
                title = a.get("title", "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                full_text = title + " " + (a.get("description") or "") + " " + (a.get("content") or "")
                cat, icon = classify(full_text)
                impact = get_impact(full_text)
                img = a.get("image") or ""
                if not img or "placeholder" in img.lower() or len(img) < 10:
                    img = get_fallback_image(cat)
                # Conteudo mais rico: junta description + content, limpa e trunca a 1200 chars
                description = (a.get("description") or "").strip()
                content_raw = clean_content(a.get("content") or "")
                # Se o content e apenas o description repetido, usa so o description
                if content_raw and content_raw[:80] != description[:80]:
                    full_content = description + "\n\n" + content_raw
                else:
                    full_content = description
                articles.append({
                    "id":          abs(hash(title)) % (10**9),
                    "source":      a.get("source", {}).get("name", "GNews"),
                    "title":       title[:180],
                    "summary":     description[:400],
                    "content":     full_content[:1200],
                    "url":         a.get("url", ""),
                    "image":       img,
                    "publishedAt": a.get("publishedAt", ""),
                    "category":    cat,
                    "icon":        icon,
                    "impact":      impact,
                    "heat":        heat_score(impact),
                    "feed":        "global"
                })
            time.sleep(0.4)
        except Exception as e:
            print(f"  [WARN GNews] {q}: {e}")
    return articles

def fetch_finnhub_news():
    if not FH_TOKEN:
        print("  [WARN] FINNHUB_TOKEN nao definido")
        return []
    try:
        r = requests.get(f"{FH_BASE}/news", params={"category":"general","token":FH_TOKEN}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [WARN Finnhub news]: {e}")
        return []
    articles = []
    seen = set()
    for a in data[:50]:
        title = (a.get("headline") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        summary = (a.get("summary") or "").strip()
        full_text = title + " " + summary
        cat, icon = classify(full_text)
        impact = get_impact(full_text)
        img = a.get("image") or ""
        if not img or len(img) < 10:
            img = get_fallback_image(cat)
        ts = a.get("datetime", 0)
        iso = datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else ""
        articles.append({
            "id":          abs(hash(title)) % (10**9),
            "source":      a.get("source", "Finnhub"),
            "title":       title[:180],
            "summary":     summary[:400],
            "content":     summary[:1200],  # Finnhub so tem summary, usamos como content
            "url":         a.get("url", ""),
            "image":       img,
            "publishedAt": iso,
            "category":    cat,
            "icon":        icon,
            "impact":      impact,
            "heat":        heat_score(impact),
            "feed":        "markets"
        })
        if len(articles) >= 20:
            break
    return articles

def merge_and_sort(gnews, fhnews):
    all_articles = gnews + fhnews
    seen = set()
    deduped = []
    for a in all_articles:
        key = a["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(a)
    def parse_dt(a):
        try:
            return datetime.datetime.fromisoformat(a["publishedAt"].replace("Z","+00:00"))
        except:
            return datetime.datetime.min
    deduped.sort(key=parse_dt, reverse=True)
    return deduped[:35]

def main():
    print("A buscar noticias...")
    gnews  = fetch_gnews()
    print(f"  GNews: {len(gnews)} artigos")
    fhnews = fetch_finnhub_news()
    print(f"  Finnhub: {len(fhnews)} artigos")
    articles = merge_and_sort(gnews, fhnews)
    print(f"  Total: {len(articles)} artigos")
    # Estatisticas por categoria
    from collections import Counter
    cats = Counter(a["category"] for a in articles)
    for cat, n in sorted(cats.items()):
        print(f"    {cat}: {n}")
    out = {"updated": datetime.datetime.utcnow().isoformat()+"Z", "articles": articles}
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("news.json OK")

if __name__ == "__main__":
    main()
