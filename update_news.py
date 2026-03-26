"""
FundScope — Atualização automática de notícias via RSS (Yahoo Finance + Google News)
Corre pelo GitHub Actions de segunda a sexta.
Gratuito, sem API keys.
"""
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from html import escape

# --- CONFIGURAÇÃO ---
HTML_FILE = "index.html"

TICKERS = {
    "MU":    "Micron Technology",
    "NVDA":  "NVIDIA",
    "AVGO":  "Broadcom",
    "ANET":  "Arista Networks",
    "GOOGL": "Alphabet Google",
    "ETN":   "Eaton Corporation",
    "ISRG":  "Intuitive Surgical",
    "VST":   "Vistra Corp",
    "CCJ":   "Cameco uranium",
    "CRT":   "Cross Timbers Royalty Trust",
    "RKLB":  "Rocket Lab",
    "OUST":  "Ouster lidar",
    "SUUN":  "PowerBank Corporation SUUN",
}

# Palavras-chave que indicam notícias materiais (earnings, M&A, regulação, etc.)
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
    """Busca e parseia um feed RSS."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (FundScope News Bot)"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return ET.fromstring(resp.read())
    except Exception as e:
        print(f"  AVISO: Falha ao buscar {url[:60]}... — {e}")
        return None

def get_yahoo_news(ticker):
    """Busca notícias do Yahoo Finance RSS para um ticker."""
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    root = fetch_rss(url)
    if root is None:
        return []
    
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        source = item.findtext("source", "Yahoo Finance")
        
        # Parsear data
        try:
            dt = datetime.strptime(pub_date[:25], "%a, %d %b %Y %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        except:
            dt = datetime.now(timezone.utc)
        
        items.append({
            "title": title,
            "link": link,
            "date": dt,
            "source": source if source else "Yahoo Finance",
        })
    
    return items

def get_google_news(query):
    """Busca notícias do Google News RSS."""
    encoded = urllib.request.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}+when:7d&hl=en-US&gl=US&ceid=US:en"
    root = fetch_rss(url)
    if root is None:
        return []
    
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        source = item.findtext("source", "")
        
        try:
            dt = datetime.strptime(pub_date[:25], "%a, %d %b %Y %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        except:
            dt = datetime.now(timezone.utc)
        
        items.append({
            "title": title,
            "link": link,
            "date": dt,
            "source": source if source else "Google News",
        })
    
    return items

def is_material(title):
    """Verifica se o título contém palavras-chave de eventos materiais."""
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in MATERIAL_KEYWORDS)

def days_ago(dt):
    """Retorna há quantos dias foi a data."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    return delta.days

def build_news_html(all_news):
    """Gera o bloco HTML com as notícias organizadas por ticker."""
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%d %b %Y %H:%M UTC")
    
    html_parts = []
    html_parts.append(f'<div class="section-title">Eventos Materiais Recentes · Atualizado {now_str}</div>')
    
    tickers_with_news = 0
    tickers_without_news = []
    
    for ticker, company in TICKERS.items():
        news = all_news.get(ticker, [])
        
        # Filtrar só notícias materiais e das últimas 2 semanas
        material = [n for n in news if is_material(n["title"]) and days_ago(n["date"]) <= 14]
        
        # Remover duplicados por título similar
        seen_titles = set()
        unique = []
        for n in material:
            title_key = n["title"][:60].lower()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique.append(n)
        
        # Ordenar por data (mais recente primeiro)
        unique.sort(key=lambda x: x["date"], reverse=True)
        unique = unique[:5]  # Máximo 5 por ticker
        
        if not unique:
            tickers_without_news.append(ticker)
            continue
        
        tickers_with_news += 1
        
        # Gerar HTML para este ticker
        html_parts.append(f'''
<div class="analyst-card">
  <div class="analyst-header" onclick="toggleAnalyst(this)">
    <div class="ah-left">
      <div class="ah-ticker">{escape(ticker)}</div>
      <span style="font-size:11px;color:var(--text2);font-family:var(--mono);">{escape(company.split()[0])}</span>
      <span style="font-size:10px;color:var(--green);font-family:var(--mono);">{len(unique)} evento(s)</span>
    </div>
    <div class="ah-right">
      <div style="color:var(--text3);font-size:16px;">▸</div>
    </div>
  </div>
  <div class="analyst-body">''')
        
        for n in unique:
            age = days_ago(n["date"])
            if age == 0:
                age_str = "Hoje"
                age_color = "var(--green)"
            elif age == 1:
                age_str = "Ontem"
                age_color = "var(--green)"
            elif age <= 3:
                age_str = f"Há {age} dias"
                age_color = "var(--yellow)"
            else:
                age_str = n["date"].strftime("%d %b")
                age_color = "var(--text3)"
            
            source_name = escape(str(n["source"])[:30])
            title_text = escape(n["title"][:120])
            link = escape(n["link"])
            
            html_parts.append(f'''
    <div style="padding:8px 0;border-bottom:1px solid var(--border);">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
        <span style="font-family:var(--mono);font-size:10px;color:{age_color};">{age_str}</span>
        <span style="font-family:var(--mono);font-size:9px;color:var(--text3);">{source_name}</span>
      </div>
      <a href="{link}" target="_blank" rel="noopener" style="font-size:12px;color:var(--text);text-decoration:none;line-height:1.5;">{title_text}</a>
    </div>''')
        
        html_parts.append('''
  </div>
</div>''')
    
    # Tickers sem notícias
    if tickers_without_news:
        no_news_list = ", ".join(tickers_without_news)
        html_parts.append(f'''
<div style="font-family:var(--mono);font-size:11px;color:var(--text3);padding:14px;text-align:center;border:1px solid var(--border);border-radius:6px;margin-top:8px;">
  Sem eventos materiais nos últimos 14 dias: {no_news_list}
</div>''')
    
    # Nota de rodapé
    html_parts.append(f'''
<div style="font-family:var(--mono);font-size:9px;color:var(--text3);text-align:center;margin-top:16px;">
  Fontes: Yahoo Finance RSS · Google News RSS · Filtro automático por eventos materiais (earnings, M&A, regulação, guidance)<br>
  Última recolha: {now_str} · Próxima: amanhã ~08:00 UTC
</div>''')
    
    return "\n".join(html_parts)

def update_html(news_html):
    """Substitui o placeholder de notícias no HTML."""
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    
    # Substituir o conteúdo entre o id="news-placeholder" e o próximo </div>
    # Padrão: <div ... id="news-placeholder">...CONTEÚDO...</div>
    pattern = r'(<div[^>]*id="news-placeholder"[^>]*>)(.*?)(</div>\s*</div><!-- end news tab -->)'
    replacement = rf'\1\n{news_html}\n\3'
    
    new_html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    
    if new_html == html:
        # Fallback: tentar outro padrão
        pattern2 = r'(id="news-placeholder"[^>]*>)(.*?)(</div>\s*</div><!-- end news tab -->)'
        new_html = re.sub(pattern2, rf'\1\n{news_html}\n\3', html, flags=re.DOTALL)
    
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)
    
    print("✓ HTML atualizado com notícias")

def main():
    print("=" * 50)
    print("FundScope — Atualização de Notícias")
    print("=" * 50)
    
    all_news = {}
    
    for ticker, company in TICKERS.items():
        print(f"\n  A buscar notícias para {ticker} ({company})...")
        
        # Combinar fontes
        yahoo = get_yahoo_news(ticker)
        google = get_google_news(f"{company} stock")
        
        combined = yahoo + google
        print(f"    → {len(yahoo)} Yahoo + {len(google)} Google = {len(combined)} total")
        
        # Filtrar materiais
        material = [n for n in combined if is_material(n["title"])]
        print(f"    → {len(material)} eventos materiais identificados")
        
        all_news[ticker] = combined
    
    print("\n" + "=" * 50)
    print("A gerar HTML...")
    
    news_html = build_news_html(all_news)
    update_html(news_html)
    
    print("✓ Concluído!")

if __name__ == "__main__":
    main()
