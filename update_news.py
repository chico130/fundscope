"""
FundScope — Atualização automática de notícias via RSS (Yahoo Finance + Google News)
Corre pelo GitHub Actions de segunda a sexta. Gratuito, sem API keys.
"""

import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import escape

HTML_FILE = "index.html"

TICKERS = {
    "MU":   "Micron Technology",
    "NVDA": "NVIDIA",
    "AVGO": "Broadcom",
    "ANET": "Arista Networks",
    "GOOGL": "Alphabet Google",
    "ETN":  "Eaton Corporation",
    "ISRG": "Intuitive Surgical",
    "VST":  "Vistra Corp",
    "CCJ":  "Cameco uranium",
    "CRT":  "Cross Timbers Royalty Trust",
    "RKLB": "Rocket Lab",
    "OUST": "Ouster lidar",
    "SUUN": "PowerBank Corporation SUUN",
}

MATERIAL_KEYWORDS = [
    "earnings", "revenue", "profit", "loss", "guidance", "forecast",
    "upgrade", "downgrade", "dividend", "acquisition", "merger",
    "SEC", "FDA", "regulation", "antitrust", "lawsuit", "settlement",
    "CEO", "CFO", "resign", "appoint", "layoff", "restructur",
    "contract", "deal", "partner", "launch", "IPO", "buyback",
    "recall", "investigat", "sanction", "tariff", "ban", "approval",
    "beat", "miss", "surprise", "record", "warning", "cut",
    "nuclear", "uranium", "HBM", "AI chip", "data center",
    "quarterly", "annual", "report", "filing", "10-K", "10-Q",
]


def fetch_rss(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FundScope News Bot)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return ET.fromstring(resp.read())
    except Exception as e:
        print(f"  AVISO: Falha ao buscar {url[:60]}... — {e}")
        return None


def get_yahoo_news(ticker):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    root = fetch_rss(url)
    if root is None:
        return []
    items = []
    for item in root.findall(".//item"):
        title    = item.findtext("title", "")
        link     = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        source   = item.findtext("source", "Yahoo Finance")
        try:
            dt = datetime.strptime(pub_date[:25], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        except:
            dt = datetime.now(timezone.utc)
        items.append({"title": title, "link": link, "date": dt,
                      "source": source if source else "Yahoo Finance"})
    return items


def get_google_news(query):
    encoded = urllib.request.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}+when:7d&hl=en-US&gl=US&ceid=US:en"
    root = fetch_rss(url)
    if root is None:
        return []
    items = []
    for item in root.findall(".//item"):
        title    = item.findtext("title", "")
        link     = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        source   = item.findtext("source", "")
        try:
            dt = datetime.strptime(pub_date[:25], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        except:
            dt = datetime.now(timezone.utc)
        items.append({"title": title, "link": link, "date": dt,
                      "source": source if source else "Google News"})
    return items


def is_material(title):
    t = title.lower()
    return any(kw.lower() in t for kw in MATERIAL_KEYWORDS)


def format_date_label(dt):
    now = datetime.now(timezone.utc)
    delta = now - dt
    if delta.days == 0:
        return "Hoje"
    elif delta.days == 1:
        return "Ontem"
    else:
        return dt.strftime("%d %b %Y")


def build_news_html(all_news):
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%d %b %Y %H:%M UTC")

    # Separar e ordenar: materiais primeiro, depois outras. Ambos do mais recente para o mais antigo.
    material = sorted([n for n in all_news if n["material"]], key=lambda x: x["date"], reverse=True)
    others   = sorted([n for n in all_news if not n["material"]], key=lambda x: x["date"], reverse=True)
    unified  = material + others

    p = []
    p.append('<div class="news-updated-bar">EVENTOS MATERIAIS RECENTES &middot; ATUALIZADO ' + escape(now_str) + '</div>\n')
    p.append('<div class="news-unified-list">\n')

    if not unified:
        p.append('  <div class="news-empty">Sem noticias nos ultimos 7 dias.</div>\n')
    else:
        sep_shown = False
        for news in unified:
            if not news["material"] and not sep_shown:
                p.append('  <div class="news-section-separator">&#8213; outras noticias &#8213;</div>\n')
                sep_shown = True

            ticker   = escape(news["ticker"])
            company  = escape(news["company"])
            title    = escape(news["title"])
            link     = escape(news["link"])
            source   = escape(news["source"])
            date_lbl = escape(format_date_label(news["date"]))
            css_cls  = "news-row material" if news["material"] else "news-row other"

            p.append('  <div class="' + css_cls + '">\n')
            p.append('    <div class="news-row-meta">')
            p.append('<span class="news-ticker-badge">' + ticker + '</span>')
            p.append('<span class="news-company-name">' + company + '</span>')
            p.append('<span class="news-date-label">' + date_lbl + '</span>')
            p.append('<span class="news-source-label">' + source + '</span>')
            p.append('</div>\n')
            p.append('    <a class="news-headline-link" href="' + link + '" target="_blank" rel="noopener noreferrer">' + title + '</a>\n')
            p.append('  </div>\n')

    p.append('</div>\n')
    return "".join(p)


def inject_html(html_content, new_block):
    pattern = r'<!-- NEWS-START -->.*?<!-- NEWS-END -->'
    replacement = '<!-- NEWS-START -->\n' + new_block + '<!-- NEWS-END -->'
    new_content, count = re.subn(pattern, replacement, html_content, flags=re.DOTALL)
    if count == 0:
        print("  AVISO: Marcadores NEWS-START / NEWS-END nao encontrados no index.html")
    return new_content


def main():
    print("FundScope — A recolher noticias...")

    all_news    = []
    seen_titles = set()

    for ticker, company in TICKERS.items():
        print(f"  [{ticker}] {company}")
        raw_items = get_yahoo_news(ticker) + get_google_news(company)
        for item in raw_items:
            key = item["title"].strip().lower()
            if not item["title"] or key in seen_titles:
                continue
            seen_titles.add(key)
            item["ticker"]   = ticker
            item["company"]  = company
            item["material"] = is_material(item["title"])
            all_news.append(item)

    total_mat = sum(1 for n in all_news if n["material"])
    print(f"  Total: {len(all_news)} noticias ({total_mat} materiais)")

    new_block = build_news_html(all_news)

    try:
        with open(HTML_FILE, "r", encoding="utf-8") as f:
            html_content = f.read()
    except FileNotFoundError:
        print(f"  ERRO: Ficheiro '{HTML_FILE}' nao encontrado.")
        sys.exit(1)

    new_html = inject_html(html_content, new_block)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)

    print("  OK: index.html atualizado com sucesso.")


if __name__ == "__main__":
    main()
