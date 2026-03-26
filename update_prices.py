"""
FundScope — Atualização automática de preços via Yahoo Finance
Este script é executado pelo GitHub Actions todos os dias às 22:00 UTC.
Lê o ficheiro index.html, atualiza os preços e faz commit+push.
"""
import yfinance as yf
import re
import sys
from datetime import datetime, timezone

# --- CONFIGURAÇÃO ---
TICKERS = ["MU", "CRT", "CCJ", "GOOGL", "VST", "NVDA", "AVGO", "ANET", "ETN", "ISRG", "SUUN", "RKLB", "OUST"]
HTML_FILE = "index.html"

def get_prices(tickers):
    """Busca os preços de fecho mais recentes via yfinance."""
    prices = {}
    for t in tickers:
        try:
            stock = yf.Ticker(t)
            info = stock.fast_info
            price = info.get("lastPrice", None) or info.get("regularMarketPrice", None)
            if price:
                prices[t] = round(price, 2)
                print(f"  {t}: ${prices[t]}")
            else:
                print(f"  {t}: ERRO — sem preço disponível")
        except Exception as e:
            print(f"  {t}: ERRO — {e}")
    return prices

def update_html(prices):
    """Atualiza todos os preços no ficheiro HTML."""
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    changes = 0
    for ticker, price in prices.items():
        # Formato: $XXX.XX ou $X.XX (com ponto decimal)
        # Atualizar nos summary cards: <div class="sc-price">$XXX.XX</div>
        pattern_sc = rf'(id="{ticker}"[^>]*>.*?<div class="sc-price">\$)[\d,.]+(<)'
        # Mais simples: procurar pelo padrão nos summary cards usando o ticker como âncora
        # Summary cards usam sc-ticker seguido de sc-price
        
        price_str = f"{price:,.2f}" if price >= 1000 else f"{price:.2f}"
        
        # Padrão 1: dc-price (detail cards) — procurar dentro do card com id=TICKER
        old_html = html
        # Atualizar o dc-price dentro do card com o id correto
        pattern = rf'(id="{ticker}".*?<div class="dc-price">\$)[\d,.]+(</div>)'
        html = re.sub(pattern, rf'\g<1>{price_str}\2', html, count=1, flags=re.DOTALL)
        if html != old_html:
            changes += 1
            old_html = html

        # Padrão 2: Preços na tabela — linhas com o ticker
        # As linhas da tabela têm <span class="t">TICKER</span> seguido de <td>$PREÇO</td>
        pattern_table = rf'(<span class="t">{ticker}</span>.*?<td>\$)[\d,.]+(</td>)'
        html = re.sub(pattern_table, rf'\g<1>{price_str}\2', html, count=1, flags=re.DOTALL)
        if html != old_html:
            changes += 1
            old_html = html

        # Padrão 3: Summary cards — sc-ticker seguido de sc-price
        pattern_summary = rf'(<div class="sc-ticker">{ticker}</div>.*?<div class="sc-price">\$)[\d,.]+(</div>)'
        html = re.sub(pattern_summary, rf'\g<1>{price_str}\2', html, count=1, flags=re.DOTALL)
        if html != old_html:
            changes += 1
            old_html = html

    # Atualizar a data de última actualização
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    html = re.sub(
        r'Última actualização:.*?</span>',
        f'Última actualização: <span>{now}</span>',
        html
    )
    
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"\n✓ {changes} preços atualizados no HTML")
    print(f"✓ Data atualizada para: {now}")

def main():
    print("=" * 50)
    print("FundScope — Atualização de Preços")
    print("=" * 50)
    print(f"\nA buscar preços para {len(TICKERS)} tickers...")
    
    prices = get_prices(TICKERS)
    
    if not prices:
        print("\n✗ Nenhum preço obtido. A abortar.")
        sys.exit(1)
    
    print(f"\n{len(prices)}/{len(TICKERS)} preços obtidos. A atualizar HTML...")
    update_html(prices)
    print("\n✓ Concluído!")

if __name__ == "__main__":
    main()
