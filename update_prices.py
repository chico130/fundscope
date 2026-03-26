"""
FundScope — Atualização automática de preços via Yahoo Finance
Actualiza: summary cards, tabela, detail cards E data no topbar.
"""

import yfinance as yf
import re
import sys
from datetime import datetime, timezone

TICKERS = ["MU", "CRT", "CCJ", "GOOGL", "VST", "NVDA", "AVGO", "ANET", "ETN", "ISRG", "SUUN", "RKLB", "OUST"]
HTML_FILE = "index.html"


def get_prices(tickers):
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
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    changes = 0

    for ticker, price in prices.items():
        price_str = f"${price:,.2f}"

        # 1. Summary cards — sc-price
        # Procura o bloco do ticker e substitui o sc-price a seguir
        pattern_sc = (
            r'(<div class="sc-ticker">' + re.escape(ticker) + r'</div>'
            r'.*?<div class="sc-price">)\$[\d,\.]+(<\/div>)'
        )
        new_sc = r'\g<1>' + price_str + r'\2'
        html_new, n = re.subn(pattern_sc, new_sc, html, flags=re.DOTALL)
        if n:
            html = html_new
            changes += n

        # 2. Tabela comparativa — coluna Preço
        # Procura a linha da tabela com o ticker e substitui o $preço
        pattern_tbl = (
            r'(<div class="ticker-cell"><span class="t">' + re.escape(ticker) + r'<\/span>'
            r'.*?<\/div><\/td>\s*<td[^>]*>)\$[\d,\.]+(<\/td>)'
        )
        new_tbl = r'\g<1>' + price_str + r'\2'
        html_new, n = re.subn(pattern_tbl, new_tbl, html, flags=re.DOTALL)
        if n:
            html = html_new
            changes += n

        # 3. Detail cards — dc-price (dentro do id=TICKER)
        # Procura o card com id="TICKER" e substitui o dc-price
        pattern_dc = (
            r'(<div class="detail-card[^"]*"[^>]*id="' + re.escape(ticker) + r'">'
            r'.*?<div class="dc-price">)\$[\d,\.]+(<\/div>)'
        )
        new_dc = r'\g<1>' + price_str + r'\2'
        html_new, n = re.subn(pattern_dc, new_dc, html, flags=re.DOTALL)
        if n:
            html = html_new
            changes += n

    # 4. Data no topbar — "Última actualização: <span>..."
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    pattern_date = r'(Última actualização:\s*<span>)[^<]+(</span>)'
    html_new, n = re.subn(pattern_date, r'\g<1>' + now_str + r'\2', html)
    if n:
        html = html_new
        changes += n
        print(f"  Topbar atualizado: {now_str}")

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Total de substituições: {changes}")
    return changes


def main():
    print("FundScope — A atualizar preços...")
    prices = get_prices(TICKERS)

    if not prices:
        print("  ERRO: Nenhum preço obtido. A abortar.")
        sys.exit(1)

    changes = update_html(prices)

    if changes == 0:
        print("  AVISO: Nenhuma substituição feita. Verifica os padrões no HTML.")
    else:
        print(f"  OK: {len(prices)} tickers atualizados com sucesso.")


if __name__ == "__main__":
    main()
