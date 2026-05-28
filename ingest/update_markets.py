#!/usr/bin/env python3
"""
update_markets.py — FundScope v2.2

Fixes:
  - Holiday guard: detecta feriados NYSE e sai sem corromper markets.json
  - period="5d" em vez de "2d" para garantir dados mesmo ao inicio da semana
  - Slot detection corrigida: detecta pre-mercado de segunda-feira
  - updated timestamp mostra data do ultimo fecho, nao UTC now()
  - changePct calcula sempre fecho[-1] vs fecho[-2] (dias de mercado aberto)
"""

import json, os, sys, time, datetime, traceback, requests
import yfinance as yf

FH_TOKEN = os.environ.get("FINNHUB_TOKEN", "")
FH_BASE  = "https://finnhub.io/api/v1"

if not FH_TOKEN:
    print("[AVISO] FINNHUB_TOKEN ausente — sentiment/news ficam vazios mas o script continua",
          flush=True)

# ------------------------------------------------------------------ NYSE holidays
# Feriados NYSE fixos e flutuantes (calculados)
# Fonte: https://www.nyse.com/markets/hours-calendars

NYSE_FIXED_HOLIDAYS = {
    # (month, day): name
    (1, 1):   "New Year's Day",
    (6, 19):  "Juneteenth",
    (7, 4):   "Independence Day",
    (11, 11): "Veterans Day (observado NYSE apenas em anos especificos)",
    (12, 25): "Christmas Day",
}

def _nth_weekday(year, month, weekday, n):
    """Devolve a data do N-esimo dia-da-semana (0=Mon) do mes."""
    d = datetime.date(year, month, 1)
    delta = (weekday - d.weekday()) % 7
    d += datetime.timedelta(days=delta)
    return d + datetime.timedelta(weeks=n - 1)

def _last_weekday(year, month, weekday):
    """Devolve a data do ultimo dia-da-semana do mes."""
    # Vai ao primeiro dia do mes seguinte e recua
    if month == 12:
        next_month = datetime.date(year + 1, 1, 1)
    else:
        next_month = datetime.date(year, month + 1, 1)
    d = next_month - datetime.timedelta(days=1)
    delta = (d.weekday() - weekday) % 7
    return d - datetime.timedelta(days=delta)

def get_nyse_holidays(year):
    """Devolve set de datas de feriado NYSE para o ano dado."""
    holidays = set()

    # New Year's Day (observed)
    ny = datetime.date(year, 1, 1)
    if ny.weekday() == 6:  # domingo -> segunda
        holidays.add(datetime.date(year, 1, 2))
    elif ny.weekday() == 5:  # sabado -> sexta anterior (ano anterior)
        pass  # nao afeta ano corrente
    else:
        holidays.add(ny)

    # Martin Luther King Jr. Day: 3.a segunda de janeiro
    holidays.add(_nth_weekday(year, 1, 0, 3))

    # Presidents' Day: 3.a segunda de fevereiro
    holidays.add(_nth_weekday(year, 2, 0, 3))

    # Good Friday: sexta antes da Pascoa
    # Algoritmo de Meeus/Jones/Butcher para Pascoa
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day   = ((h + l - 7 * m + 114) % 31) + 1
    easter = datetime.date(year, month, day)
    good_friday = easter - datetime.timedelta(days=2)
    holidays.add(good_friday)

    # Memorial Day: ultima segunda de maio
    holidays.add(_last_weekday(year, 5, 0))

    # Juneteenth (desde 2022): 19 junho, observed
    if year >= 2022:
        jt = datetime.date(year, 6, 19)
        if jt.weekday() == 6:  # domingo -> segunda
            holidays.add(datetime.date(year, 6, 20))
        elif jt.weekday() == 5:  # sabado -> sexta
            holidays.add(datetime.date(year, 6, 18))
        else:
            holidays.add(jt)

    # Independence Day: 4 julho, observed
    ind = datetime.date(year, 7, 4)
    if ind.weekday() == 6:
        holidays.add(datetime.date(year, 7, 5))
    elif ind.weekday() == 5:
        holidays.add(datetime.date(year, 7, 3))
    else:
        holidays.add(ind)

    # Labor Day: 1.a segunda de setembro
    holidays.add(_nth_weekday(year, 9, 0, 1))

    # Thanksgiving: 4.a quinta de novembro
    holidays.add(_nth_weekday(year, 11, 3, 4))

    # Christmas: 25 dezembro, observed
    xmas = datetime.date(year, 12, 25)
    if xmas.weekday() == 6:
        holidays.add(datetime.date(year, 12, 26))
    elif xmas.weekday() == 5:
        holidays.add(datetime.date(year, 12, 24))
    else:
        holidays.add(xmas)

    return holidays

def get_holiday_name(date):
    """Devolve o nome do feriado NYSE ou None se nao for feriado."""
    year = date.year
    names = {
        _nth_weekday(year, 1, 0, 3):   "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3):   "Presidents' Day",
        _last_weekday(year, 5, 0):     "Memorial Day",
        _nth_weekday(year, 9, 0, 1):   "Labor Day",
        _nth_weekday(year, 11, 3, 4):  "Thanksgiving Day",
    }
    # Good Friday
    a = year % 19
    b = year // 100; c = year % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day   = ((h + l - 7 * m + 114) % 31) + 1
    easter = datetime.date(year, month, day)
    names[easter - datetime.timedelta(days=2)] = "Good Friday"

    # Fixed / observed
    for d_check, name in [
        (datetime.date(year, 1, 1),  "New Year's Day"),
        (datetime.date(year, 6, 19), "Juneteenth"),
        (datetime.date(year, 7, 4),  "Independence Day"),
        (datetime.date(year, 12, 25),"Christmas Day"),
    ]:
        obs = d_check
        if obs.weekday() == 6: obs = obs + datetime.timedelta(days=1)
        elif obs.weekday() == 5: obs = obs - datetime.timedelta(days=1)
        names[obs] = name

    return names.get(date)

def is_nyse_holiday(date):
    return date in get_nyse_holidays(date.year)

# ------------------------------------------------------------------ sectors
SECTORS = {
    "Tecnologia": {
        "icon": "\U0001f4bb",
        "color": "#4f98a3",
        "image": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&q=80",
        "tickers": ["AAPL","MSFT","NVDA","GOOGL","META","AMD","INTC","TSLA","AVGO","MU","QCOM","ORCL","CRM","ADBE","NOW"],
        "sentiment_tickers": ["AAPL","NVDA","MSFT"],
        "news_tickers": ["AAPL","NVDA","MSFT","AMD","META"]
    },
    "Finan\u00e7as": {
        "icon": "\U0001f3e6",
        "color": "#437a22",
        "image": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800&q=80",
        "tickers": ["JPM","BAC","GS","MS","WFC","C","BLK","AXP","V","MA","SCHW","USB","PNC","TFC","COF"],
        "sentiment_tickers": ["JPM","GS","BAC"],
        "news_tickers": ["JPM","GS","BAC","MS","V"]
    },
    "Energia": {
        "icon": "\u26a1",
        "color": "#c47d1a",
        "image": "https://images.unsplash.com/photo-1509391366360-2e959784a276?w=800&q=80",
        "tickers": ["XOM","CVX","COP","SLB","EOG","PSX","MPC","VLO","OXY","HAL","NEE","D","DUK","SO","AEP"],
        "sentiment_tickers": ["XOM","CVX","NEE"],
        "news_tickers": ["XOM","CVX","COP","OXY","NEE"]
    },
    "Sa\u00fade": {
        "icon": "\U0001f3e5",
        "color": "#a13544",
        "image": "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=800&q=80",
        "tickers": ["JNJ","UNH","PFE","ABBV","MRK","LLY","TMO","ABT","DHR","BMY","AMGN","GILD","MDT","CVS","ISRG"],
        "sentiment_tickers": ["JNJ","LLY","PFE"],
        "news_tickers": ["JNJ","LLY","PFE","ABBV","MRK"]
    },
    "Consumo": {
        "icon": "\U0001f6d2",
        "color": "#7a5c9e",
        "image": "https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=800&q=80",
        "tickers": ["AMZN","WMT","HD","MCD","SBUX","NKE","TGT","COST","LOW","TJX","PG","KO","PEP","PM","CL"],
        "sentiment_tickers": ["AMZN","WMT","MCD"],
        "news_tickers": ["AMZN","WMT","MCD","COST","KO"]
    },
    "Commodities": {
        "icon": "\U0001fa99",
        "color": "#8a7340",
        "image": "https://images.unsplash.com/photo-1610375461246-83df859d849d?w=800&q=80",
        "tickers": ["FCX","NEM","GOLD","AA","CLF","X","MP","WPM","AEM","CF","MOS","NUE","STLD","RS","ATI"],
        "sentiment_tickers": ["FCX","NEM","GOLD"],
        "news_tickers": ["FCX","NEM","GOLD","AA","CLF"]
    }
}

# ------------------------------------------------------------------ slot
def get_slot(now_utc):
    hour   = now_utc.hour
    minute = now_utc.minute
    if hour < 8:
        return "fecho"
    if hour < 12:
        return "abertura"
    if hour < 15 or (hour == 15 and minute < 30):
        return "meio-dia"
    return "fecho"

def is_market_open(now_utc):
    """NYSE esta aberto: dias uteis NAO feriado, 14:30-21:00 UTC"""
    if now_utc.weekday() >= 5:
        return False
    today = now_utc.date()
    if is_nyse_holiday(today):
        return False
    minutes = now_utc.hour * 60 + now_utc.minute
    return 870 <= minutes < 1260  # 14:30=870, 21:00=1260

# ------------------------------------------------------------------ yfinance
def fetch_all_quotes():
    all_tickers = []
    for cfg in SECTORS.values():
        all_tickers.extend(cfg["tickers"])
    all_tickers = list(dict.fromkeys(all_tickers))

    print(f"A descarregar {len(all_tickers)} cotacoes via yfinance (period=5d)...")
    try:
        data = yf.download(
            all_tickers,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True
        )
    except Exception as e:
        print(f"  [ERRO yfinance download]: {e}")
        return {}, None

    try:
        if hasattr(data.columns, 'levels'):
            close = data.xs("Close", axis=1, level=0)
        elif "Close" in data.columns:
            close = data["Close"]
        else:
            print("  [ERRO] coluna Close nao encontrada")
            return {}, None
    except Exception as e:
        print(f"  [ERRO] extrair Close: {e}")
        return {}, None

    quotes = {}
    last_close_date = None

    for t in all_tickers:
        try:
            col = close[t] if t in close.columns else None
            if col is None:
                print(f"  [SKIP] {t}: ticker ausente")
                continue
            vals = col.dropna()
            if len(vals) < 1:
                print(f"  [SKIP] {t}: sem dados")
                continue
            price = float(vals.iloc[-1])
            pc    = float(vals.iloc[-2]) if len(vals) >= 2 else price
            chg   = round((price - pc) / pc * 100, 2) if pc else 0.0
            quotes[t] = {"ticker": t, "price": round(price, 2), "changePct": chg, "pc": round(pc, 2)}
            if last_close_date is None:
                try:
                    last_close_date = vals.index[-1].date().isoformat()
                except Exception:
                    pass
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")

    print(f"  {len(quotes)}/{len(all_tickers)} cotacoes obtidas")
    if last_close_date:
        print(f"  Ultimo fecho: {last_close_date}")
    return quotes, last_close_date

# ------------------------------------------------------------------ Finnhub
def fh_get(endpoint, params, retries=2):
    if not FH_TOKEN:
        return None
    params = {**params, "token": FH_TOKEN}
    for attempt in range(retries + 1):
        try:
            r = requests.get(f"{FH_BASE}/{endpoint}", params=params, timeout=10)
            if r.status_code == 429 and attempt < retries:
                wait = 2 ** attempt
                print(f"  [Finnhub] 429 rate-limit em {endpoint} — retry em {wait}s", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  [WARN Finnhub] {endpoint} {params.get('symbol','')}: timeout", flush=True)
            return None
        except Exception as e:
            print(f"  [WARN Finnhub] {endpoint} {params.get('symbol','')}: {e}", flush=True)
            return None
    return None

def fh_recommendation(ticker):
    d = fh_get("stock/recommendation", {"symbol": ticker})
    if not d or not isinstance(d, list) or not d:
        return {"bullish": None, "articles": 0, "sb": 0, "b": 0, "h": 0, "s": 0, "ss": 0}
    latest = d[0]
    sb = latest.get("strongBuy", 0) or 0
    b  = latest.get("buy", 0) or 0
    h  = latest.get("hold", 0) or 0
    s  = latest.get("sell", 0) or 0
    ss = latest.get("strongSell", 0) or 0
    total = sb + b + h + s + ss
    if total == 0:
        return {"bullish": None, "articles": 0, "sb": 0, "b": 0, "h": 0, "s": 0, "ss": 0}
    score = sb * 2 + b * 1 + s * (-1) + ss * (-2)
    pct   = round((score + 2 * total) / (4 * total) * 100, 1)
    return {"bullish": pct, "articles": total, "sb": sb, "b": b, "h": h, "s": s, "ss": ss}

def fh_news(ticker, frm, to, limit=2):
    d = fh_get("company-news", {"symbol": ticker, "from": frm, "to": to})
    if not d or not isinstance(d, list):
        return []
    out, seen = [], set()
    for item in d[:40]:
        headline = (item.get("headline") or "").strip()
        if not headline or headline in seen:
            continue
        seen.add(headline)
        out.append({
            "source":   item.get("source", ticker),
            "ticker":   ticker,
            "headline": headline[:160],
            "summary":  (item.get("summary") or "")[:220],
            "url":      item.get("url", ""),
            "datetime": item.get("datetime", 0),
            "image":    item.get("image", "")
        })
        if len(out) >= limit:
            break
    return out

# ------------------------------------------------------------------ builder
def build_sector(name, cfg, all_quotes, frm, to):
    quotes = [all_quotes[t] for t in cfg["tickers"] if t in all_quotes]
    print(f"  {len(quotes)}/{len(cfg['tickers'])} quotes")

    quotes_sorted = sorted(quotes, key=lambda x: x["changePct"], reverse=True)
    gainers = quotes_sorted[:5]
    losers  = list(reversed(quotes_sorted[-5:])) if len(quotes_sorted) >= 5 else list(reversed(quotes_sorted))

    sentiments = []
    for t in cfg["sentiment_tickers"]:
        rec = fh_recommendation(t)
        sentiments.append({
            "ticker": t, "bullish": rec["bullish"], "articles": rec["articles"],
            "sb": rec["sb"], "b": rec["b"], "h": rec["h"], "s": rec["s"], "ss": rec["ss"]
        })
        time.sleep(0.25)

    valid    = [s["bullish"] for s in sentiments if s["bullish"] is not None]
    avg_bull = round(sum(valid) / len(valid), 1) if valid else None

    news, seen_h = [], set()
    for t in cfg["news_tickers"]:
        for item in fh_news(t, frm, to, limit=2):
            if item["headline"] not in seen_h:
                seen_h.add(item["headline"])
                news.append(item)
        time.sleep(0.25)
        if len(news) >= 5:
            break
    news.sort(key=lambda x: x["datetime"], reverse=True)
    news = news[:5]

    avg_chg = round(sum(q["changePct"] for q in quotes) / len(quotes), 2) if quotes else 0

    return {
        "name": name, "icon": cfg["icon"], "color": cfg["color"], "image": cfg["image"],
        "avgChange": avg_chg, "gainers": gainers, "losers": losers,
        "sentiment": {"bullishPct": avg_bull, "details": sentiments},
        "news": news
    }

# ------------------------------------------------------------------ main
def _ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main():
    t0 = time.monotonic()
    now_utc = datetime.datetime.utcnow()
    today   = now_utc.date()
    print(f"[{_ts()}] === update_markets START ===", flush=True)

    # --- HOLIDAY GUARD ---
    # Se for fim de semana ou feriado NYSE, nao correr nem sobrescrever markets.json
    if today.weekday() >= 5 or is_nyse_holiday(today):
        holiday_name = get_holiday_name(today)
        reason = "fim de semana" if today.weekday() >= 5 else f"feriado NYSE: {holiday_name}"
        print(f"[HOLIDAY GUARD] Mercado fechado ({reason}) — a sair sem alterar markets.json")
        # Actualizar apenas marketOpen e marketHoliday no ficheiro existente
        try:
            with open("markets.json", "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing["marketOpen"]    = False
            existing["marketHoliday"] = True
            if holiday_name:
                existing["holidayName"] = holiday_name
            with open("markets.json", "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print("  markets.json: marcado como feriado (sem alterar cotacoes)")
        except Exception as e:
            print(f"  [WARN] Nao foi possivel actualizar markets.json: {e}")
        return
    # --- END HOLIDAY GUARD ---

    slot        = get_slot(now_utc)
    market_open = is_market_open(now_utc)

    frm = (today - datetime.timedelta(days=5)).isoformat()
    to  = today.isoformat()

    print(f"UTC: {now_utc.strftime('%Y-%m-%d %H:%M')} | slot: {slot} | mercado aberto: {market_open}")

    all_quotes, last_close_date = fetch_all_quotes()

    if market_open or last_close_date is None:
        updated_ts = now_utc.isoformat() + "Z"
    else:
        updated_ts = f"{last_close_date}T21:00:00Z"

    sectors = {}
    for name, cfg in SECTORS.items():
        print(f"\n=== {name} ===")
        try:
            sectors[name] = build_sector(name, cfg, all_quotes, frm, to)
            g = len(sectors[name]["gainers"])
            l = len(sectors[name]["losers"])
            print(f"  gainers={g} losers={l} avgChg={sectors[name]['avgChange']}%")
        except Exception as e:
            print(f"  ERRO: {e}")
            sectors[name] = {
                "name": name, "icon": cfg["icon"], "color": cfg["color"], "image": cfg["image"],
                "avgChange": 0, "gainers": [], "losers": [],
                "sentiment": {"bullishPct": None, "details": []}, "news": []
            }

    out = {
        "updated":       updated_ts,
        "slot":          slot,
        "marketOpen":    market_open,
        "marketHoliday": False,
        "lastCloseDate": last_close_date,
        "sectors":       sectors
    }
    tmp = "markets.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, "markets.json")
    elapsed = time.monotonic() - t0
    print(f"\nmarkets.json OK | slot={slot} | updated={updated_ts} | elapsed={elapsed:.1f}s", flush=True)
    print(f"[{_ts()}] === update_markets END ===", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[{_ts()}] === update_markets CRASH ===", flush=True)
        print(f"[FATAL] {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        sys.exit(1)
