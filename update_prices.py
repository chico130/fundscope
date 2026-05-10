"""
FundScope — Atualização automática de preços via Yahoo Finance
Gera: data.json com preços, variações, volume, metadata e histórico de preços.
"""

import yfinance as yf
import json
import sys
from datetime import datetime, timezone

TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "VOO", "IWDA.AS", "QQQ", "SPY", "VT", "CSPX.L", "BRK-B"
]

TICKER_META = {
    "AAPL":   {"name": "Apple Inc.",                    "type": "Ação",  "exchange": "NASDAQ",             "sector": "Tecnologia"},
    "MSFT":   {"name": "Microsoft Corporation",         "type": "Ação",  "exchange": "NASDAQ",             "sector": "Tecnologia"},
    "NVDA":   {"name": "NVIDIA Corporation",            "type": "Ação",  "exchange": "NASDAQ",             "sector": "Semicondutores"},
    "TSLA":   {"name": "Tesla Inc.",                    "type": "Ação",  "exchange": "NASDAQ",             "sector": "Automóvel"},
    "AMZN":   {"name": "Amazon.com Inc.",               "type": "Ação",  "exchange": "NASDAQ",             "sector": "Consumo"},
    "GOOGL":  {"name": "Alphabet Inc.",                 "type": "Ação",  "exchange": "NASDAQ",             "sector": "Tecnologia"},
    "META":   {"name": "Meta Platforms",               "type": "Ação",  "exchange": "NASDAQ",             "sector": "Tecnologia"},
    "AMD":    {"name": "Advanced Micro Devices",        "type": "Ação",  "exchange": "NASDAQ",             "sector": "Semicondutores"},
    "VOO":    {"name": "Vanguard S&P 500 ETF",          "type": "ETF",   "exchange": "NYSE Arca",          "sector": "ETF — Large Blend"},
    "IWDA.AS":{"name": "iShares Core MSCI World UCITS", "type": "ETF",   "exchange": "Euronext Amsterdam", "sector": "ETF — Global Blend"},
    "QQQ":    {"name": "Invesco QQQ Trust",             "type": "ETF",   "exchange": "NASDAQ",             "sector": "ETF — Tech"},
    "SPY":    {"name": "SPDR S&P 500 ETF",              "type": "ETF",   "exchange": "NYSE Arca",          "sector": "ETF — Large Blend"},
    "VT":     {"name": "Vanguard Total World ETF",      "type": "ETF",   "exchange": "NYSE Arca",          "sector": "ETF — Global Blend"},
    "CSPX.L": {"name": "iShares Core S&P 500 UCITS",   "type": "ETF",   "exchange": "LSE",               "sector": "ETF — Large Blend"},
    "BRK-B":  {"name": "Berkshire Hathaway B",          "type": "Ação",  "exchange": "NYSE",              "sector": "Financeiro"},
}

# Períodos a guardar: chave -> (period, interval)
HISTORY_PERIODS = {
    "1D":  ("1d",  "2m"),
    "1S":  ("5d",  "1h"),
    "1M":  ("1mo", "1d"),
    "1A":  ("1y",  "1wk"),
    "3A":  ("3y",  "1wk"),
}

def fmt_large(n):
    if n is None: return "—"
    if n >= 1e12: return f"${n/1e12:.2f}T"
    if n >= 1e9:  return f"${n/1e9:.2f}B"
    if n >= 1e6:  return f"${n/1e6:.2f}M"
    return f"${n:.0f}"

def fmt_vol(n):
    if n is None: return "—"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.1f}K"
    return str(n)

def get_history(t_obj, period, interval):
    """Devolve lista de {t (unix timestamp), v (float)} ou lista vazia."""
    try:
        hist = t_obj.history(period=period, interval=interval, auto_adjust=True)
        if hist.empty:
            return []
        pts = []
        for ts, row in hist.iterrows():
            close = row.get("Close")
            if close is None or (hasattr(close, '__iter__')):
                # MultiIndex fallback
                try: close = float(row["Close"].iloc[0])
                except: continue
            else:
                close = float(close)
            if close != close:  # NaN
                continue
            # Converte para Unix timestamp int
            unix = int(ts.timestamp())
            pts.append({"t": unix, "v": round(close, 4)})
        return pts
    except Exception as e:
        print(f"    histórico {period}/{interval}: {e}")
        return []

def get_stock_data(ticker):
    try:
        t = yf.Ticker(ticker)
        info = t.info
        fi = t.fast_info

        price      = fi.get("last_price") or info.get("regularMarketPrice")
        prev_close = fi.get("previous_close") or info.get("previousClose")
        open_p     = fi.get("open") or info.get("open")
        day_high   = fi.get("day_high") or info.get("dayHigh")
        day_low    = fi.get("day_low") or info.get("dayLow")
        week52h    = fi.get("year_high") or info.get("fiftyTwoWeekHigh")
        week52l    = fi.get("year_low") or info.get("fiftyTwoWeekLow")
        volume     = fi.get("three_month_average_volume") or info.get("volume")
        avg_vol    = info.get("averageVolume")
        mkt_cap    = fi.get("market_cap") or info.get("marketCap")
        currency   = info.get("currency", "USD")
        pe         = info.get("trailingPE")
        eps        = info.get("trailingEps")
        beta       = info.get("beta")
        div_rate   = info.get("dividendRate")
        div_yield  = info.get("dividendYield")
        about      = info.get("longBusinessSummary", "")

        if not price:
            print(f"  {ticker}: sem preço")
            return None

        change     = round(price - prev_close, 2) if prev_close else 0
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0
        sym        = "$" if currency == "USD" else ("€" if currency == "EUR" else "£" if currency == "GBP" else currency + " ")

        div_str = "—"
        if div_rate and div_yield:
            div_pct = div_yield * 100 if div_yield < 1 else div_yield
            div_str = f"{sym}{div_rate:.2f} ({div_pct:.2f}%)"

        display_ticker = ticker.replace(".AS", "").replace(".L", "")

        # Recolher histórico para todos os períodos
        print(f"    a buscar histórico…")
        history = {}
        for period_key, (period, interval) in HISTORY_PERIODS.items():
            pts = get_history(t, period, interval)
            history[period_key] = pts
            print(f"      {period_key}: {len(pts)} pontos")

        return {
            "ticker":      display_ticker,
            "yf_ticker":   ticker,
            "price":       round(price, 2),
            "prevClose":   round(prev_close, 2) if prev_close else None,
            "open":        round(open_p, 2) if open_p else None,
            "high":        round(day_high, 2) if day_high else None,
            "low":         round(day_low, 2) if day_low else None,
            "change":      change,
            "changePct":   change_pct,
            "volume":      fmt_vol(volume),
            "avgVolume":   fmt_vol(avg_vol),
            "marketCap":   fmt_large(mkt_cap),
            "pe":          round(pe, 1) if pe else "—",
            "eps":         round(eps, 2) if eps else "—",
            "beta":        round(beta, 2) if beta else "—",
            "dividend":    div_str,
            "week52High":  round(week52h, 2) if week52h else None,
            "week52Low":   round(week52l, 2) if week52l else None,
            "currency":    currency,
            "symbol":      sym,
            "about":       about[:400] if about else "",
            "history":     history,
            **TICKER_META.get(ticker, {"name": ticker, "type": "—", "exchange": "—", "sector": "—"})
        }
    except Exception as e:
        print(f"  {ticker}: ERRO — {e}")
        return None

def main():
    print("FundScope — A buscar preços e histórico em tempo real...")
    result = {}
    for ticker in TICKERS:
        print(f"  {ticker}…")
        data = get_stock_data(ticker)
        if data:
            display = data["ticker"]
            result[display] = data
            print(f"  ✓ {display}: {data['symbol']}{data['price']} ({'+' if data['changePct']>=0 else ''}{data['changePct']}%)")

    output = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stocks": result
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  data.json gerado com {len(result)} tickers (com histórico).")

if __name__ == "__main__":
    main()
