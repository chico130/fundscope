#!/usr/bin/env python3
"""
update_portfolio.py — FundScope Portfolio

Fluxo:
  1. Puxa posições da Trading 212 API (auto-detecta live vs demo)
  2. Enriquece com cotações via yfinance
  3. Busca notícias via Finnhub
  4. Busca earnings e dividendos via yfinance
  5. Gera análise em PT via Gemini API (google-genai SDK)
  6. Guarda portfolio.json
"""

import json, os, time, datetime, requests
import yfinance as yf

# ---------- Gemini (novo SDK google-genai) ----------
try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[AVISO] google-genai não instalado — análise Gemini desativada")

T212_KEY   = os.environ["T212_API_KEY"]
FH_TOKEN   = os.environ.get("FINNHUB_TOKEN", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
FH_BASE    = "https://finnhub.io/api/v1"

gemini_client = None
if GEMINI_AVAILABLE and GEMINI_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_KEY)
        print("[OK] Gemini client inicializado")
    except Exception as e:
        print(f"[AVISO] Gemini init falhou: {e}")

# ------------------------------------------------------------------ T212
def t212_get(base, path):
    headers = {"Authorization": T212_KEY}
    r = requests.get(f"{base}{path}", headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def detect_t212_base():
    """Tenta live primeiro, cai para demo se 401/403."""
    for base in [
        "https://live.trading212.com/api/v0",
        "https://demo.trading212.com/api/v0"
    ]:
        try:
            t212_get(base, "/equity/portfolio")
            print(f"    [T212] A usar: {base}")
            return base
        except requests.HTTPError as e:
            code = e.response.status_code
            print(f"    [T212] {base} → HTTP {code}")
            if code in (401, 403):
                continue
            raise
    raise RuntimeError(
        "T212 API key inválida para live E demo. "
        "Verifica o secret T212_API_KEY no GitHub."
    )

def fetch_t212_positions(base):
    data = t212_get(base, "/equity/portfolio")
    positions = []
    for p in data:
        quantity = float(p.get("quantity", 0))
        if quantity <= 0:
            continue
        positions.append({
            "ticker_t212":   p.get("ticker", ""),
            "quantity":      round(quantity, 6),
            "avg_price":     round(float(p.get("averagePrice", 0)), 4),
            "current_price": round(float(p.get("currentPrice", 0)), 4),
            "ppl":           round(float(p.get("ppl", 0)), 2),
            "fx_ppl":        round(float(p.get("fxPpl", 0)), 2),
        })
    return positions

def map_t212_ticker(t212_ticker):
    """
    T212 usa tickers como AAPL_US_EQ, VWCE_EQ, CSPX_EQ.
    Remove sufixos e mapeia ETFs europeus para ticker yfinance correto.
    """
    ticker = t212_ticker
    for suffix in ["_US_EQ", "_EQ", "_GBX_EQ", "_EUR_EQ", "_GBP_EQ"]:
        ticker = ticker.replace(suffix, "")

    eu_etfs = {
        # Vanguard
        "VWCE": "VWCE.DE",  "VWRA": "VWRA.L",   "VUAA": "VUAA.DE",
        "VUSA": "VUSA.AS",  "VEUR": "VEUR.AS",   "VFEM": "VFEM.AS",
        "VHYL": "VHYL.AS",  "VDIV": "VDIV.AS",   "VAGP": "VAGP.L",
        # iShares
        "CSPX": "CSPX.L",   "IWDA": "IWDA.AS",   "EUNL": "EUNL.DE",
        "SXR8": "SXR8.DE",  "IMAE": "IMAE.AS",   "IUSQ": "IUSQ.DE",
        "IQQQ": "IQQQ.DE",  "EMIM": "EMIM.L",    "AGGH": "AGGH.L",
        "SSAC": "SSAC.L",   "CNDX": "CNDX.L",
        # Amundi / SPDR
        "MEUD": "MEUD.PA",  "SPYY": "SPYY.DE",   "SPPW": "SPPW.DE",
        "SPYW": "SPYW.DE",  "XDWD": "XDWD.DE",   "EQQQ": "EQQQ.L",
        "SMEA": "SMEA.DE",
    }
    return eu_etfs.get(ticker, ticker)

# ------------------------------------------------------------------ yfinance
def fetch_quotes_yf(yf_tickers):
    if not yf_tickers:
        return {}
    print(f"  yfinance: {len(yf_tickers)} tickers...")
    quotes = {}
    # single ticker — yf.download comporta-se diferente
    tickers_str = " ".join(yf_tickers) if len(yf_tickers) > 1 else yf_tickers[0]
    try:
        data = yf.download(
            tickers_str, period="5d", interval="1d",
            auto_adjust=True, progress=False, threads=True, group_by="ticker"
        )
    except Exception as e:
        print(f"  [ERRO yfinance batch]: {e}")
        # fallback individual
        for t in yf_tickers:
            try:
                tk   = yf.Ticker(t)
                hist = tk.history(period="5d")
                if hist.empty:
                    continue
                price = float(hist["Close"].iloc[-1])
                pc    = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
                chg   = round((price - pc) / pc * 100, 2) if pc else 0.0
                quotes[t] = {"price": round(price, 4), "changePct": chg}
            except Exception as e2:
                print(f"  [SKIP individual] {t}: {e2}")
        return quotes

    for t in yf_tickers:
        try:
            if len(yf_tickers) == 1:
                col = data["Close"]
            else:
                col = data[t]["Close"] if t in data.columns.get_level_values(0) else None
            if col is None:
                continue
            vals = col.dropna()
            if len(vals) < 1:
                continue
            price = float(vals.iloc[-1])
            pc    = float(vals.iloc[-2]) if len(vals) >= 2 else price
            chg   = round((price - pc) / pc * 100, 2) if pc else 0.0
            quotes[t] = {"price": round(price, 4), "changePct": chg}
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")
    return quotes

def fetch_earnings(yf_ticker):
    try:
        tk   = yf.Ticker(yf_ticker)
        hist = tk.earnings_history
        if hist is None or hist.empty:
            return []
        records = []
        for _, row in hist.tail(8).iterrows():
            eps_est  = row.get("epsEstimate")     or row.get("EpsEstimate")
            eps_act  = row.get("epsActual")       or row.get("EpsActual")
            surprise = row.get("surprisePercent") or row.get("SurprisePercent")
            period   = str(row.get("period") or row.get("Period") or "")
            if eps_act is None:
                continue
            records.append({
                "period":   period,
                "estimate": round(float(eps_est), 3) if eps_est is not None else None,
                "actual":   round(float(eps_act), 3),
                "surprise": round(float(surprise), 2) if surprise is not None else None,
                "beat":     float(eps_act) >= float(eps_est) if eps_est is not None else None
            })
        return records
    except Exception as e:
        print(f"  [earnings] {yf_ticker}: {e}")
        return []

def fetch_dividends(yf_ticker):
    try:
        tk   = yf.Ticker(yf_ticker)
        divs = tk.dividends
        if divs is None or divs.empty:
            return []
        records = []
        for dt, val in divs.tail(4).items():
            records.append({"date": str(dt.date()), "amount": round(float(val), 4)})
        return list(reversed(records))
    except Exception:
        return []

# ------------------------------------------------------------------ Finnhub
def fh_news(ticker, frm, to, limit=5):
    base_symbol = ticker.split(".")[0]
    try:
        params = {"symbol": base_symbol, "from": frm, "to": to, "token": FH_TOKEN}
        r = requests.get(f"{FH_BASE}/company-news", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [FH news] {base_symbol}: {e}")
        return []
    out, seen = [], set()
    for item in (data or [])[:40]:
        h = (item.get("headline") or "").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append({
            "source":   item.get("source", base_symbol),
            "headline": h[:180],
            "summary":  (item.get("summary") or "")[:300],
            "url":      item.get("url", ""),
            "datetime": item.get("datetime", 0),
            "image":    item.get("image", "")
        })
        if len(out) >= limit:
            break
    return out

# ------------------------------------------------------------------ Gemini
def gemini_analyze(ticker, name, news_list, earnings_list, ppl, pct_change):
    if not gemini_client:
        return {
            "sentiment": "neutro",
            "news_comment": "Gemini indisponível.",
            "earnings_comment": "Gemini indisponível.",
            "watch_points": []
        }

    news_text = "\n".join([f"- {n['headline']}" for n in news_list]) or "Sem notícias recentes."
    earn_text = "\n".join([
        f"- {e['period']}: real={e['actual']} est={e['estimate']} {'BEAT' if e.get('beat') else 'MISS'}"
        for e in earnings_list
    ]) or "Sem dados de earnings."

    prompt = f"""Analisa o ativo {ticker} ({name}) em português de Portugal (PT-PT), de forma direta e concisa.

Desempenho: {'+' if ppl >= 0 else ''}{ppl:.2f}€ P&L total, variação hoje: {pct_change:+.2f}%

Notícias:
{news_text}

Earnings (EPS):
{earn_text}

Responde APENAS com JSON válido, sem markdown:
{{"sentiment": "positivo|negativo|neutro", "news_comment": "...", "earnings_comment": "...", "watch_points": ["...", "...", "..."]}}"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3
            )
        )
        return json.loads(response.text.strip())
    except Exception as e:
        print(f"  [Gemini] {ticker}: {e}")
        return {
            "sentiment": "neutro",
            "news_comment": "Análise indisponível.",
            "earnings_comment": "Análise indisponível.",
            "watch_points": []
        }

# ------------------------------------------------------------------ histórico
def load_history():
    try:
        with open("portfolio.json", "r", encoding="utf-8") as f:
            return json.load(f).get("history", [])
    except Exception:
        return []

def update_history(history, total_value):
    today = datetime.date.today().isoformat()
    history = [h for h in history if h["date"] != today]
    history.append({"date": today, "value": round(total_value, 2)})
    history.sort(key=lambda x: x["date"])
    return history[-365:]

# ------------------------------------------------------------------ main
def main():
    now   = datetime.datetime.utcnow()
    today = now.date()
    frm   = (today - datetime.timedelta(days=7)).isoformat()
    to    = today.isoformat()

    print("=== FundScope Portfolio Update ===")
    print(f"UTC: {now.isoformat()}")
    print(f"Gemini disponível: {gemini_client is not None}")

    # 1. Detectar T212 e buscar posições
    print("\n[1] A detectar T212 e buscar posições...")
    t212_base = detect_t212_base()
    positions = fetch_t212_positions(t212_base)
    print(f"    {len(positions)} posições encontradas")
    if not positions:
        print("    Nenhuma posição — a terminar.")
        return

    # 2. Mapear tickers e cotações yfinance
    print("\n[2] A enriquecer com cotações yfinance...")
    for p in positions:
        p["ticker"] = map_t212_ticker(p["ticker_t212"])

    yf_tickers = list(dict.fromkeys(p["ticker"] for p in positions))
    quotes = fetch_quotes_yf(yf_tickers)

    for p in positions:
        q = quotes.get(p["ticker"], {})
        if q.get("price"):
            p["current_price"] = q["price"]
        p["change_pct"] = q.get("changePct", 0.0)

    # 3. Calcular métricas
    for p in positions:
        invested   = p["avg_price"] * p["quantity"]
        curr_value = p["current_price"] * p["quantity"]
        gain_eur   = p["ppl"]
        gain_pct   = (gain_eur / invested * 100) if invested > 0 else 0
        p["invested"]  = round(invested, 2)
        p["value_eur"] = round(curr_value, 2)
        p["gain_eur"]  = round(gain_eur, 2)
        p["gain_pct"]  = round(gain_pct, 2)

    total_value    = sum(p["value_eur"] for p in positions)
    total_invested = sum(p["invested"]  for p in positions)
    total_gain     = sum(p["gain_eur"]  for p in positions)
    total_gain_pct = (total_gain / total_invested * 100) if total_invested > 0 else 0
    daily_gain     = sum(p["value_eur"] * p["change_pct"] / 100 for p in positions)

    for p in positions:
        p["allocation_pct"] = round(p["value_eur"] / total_value * 100, 2) if total_value > 0 else 0

    positions.sort(key=lambda x: x["value_eur"], reverse=True)

    # 4. Notícias + Earnings + Análise
    print("\n[3] A buscar notícias, earnings e análise Gemini...")
    for p in positions:
        ticker = p["ticker"]
        print(f"  → {ticker} (T212: {p['ticker_t212']})")
        p["news"]      = fh_news(ticker, frm, to)
        time.sleep(0.3)
        p["earnings"]  = fetch_earnings(ticker)
        p["dividends"] = fetch_dividends(ticker)
        p["analysis"]  = gemini_analyze(
            ticker, p.get("ticker_t212", ticker),
            p["news"], p["earnings"],
            p["gain_eur"], p["change_pct"]
        )
        time.sleep(1)

    # 5. Histórico
    print("\n[4] A atualizar histórico...")
    history = update_history(load_history(), total_value)

    # 6. Guardar JSON
    out = {
        "updated":   now.isoformat() + "Z",
        "t212_mode": "live" if "live" in t212_base else "demo",
        "summary": {
            "total_value":    round(total_value, 2),
            "total_invested": round(total_invested, 2),
            "total_gain_eur": round(total_gain, 2),
            "total_gain_pct": round(total_gain_pct, 2),
            "daily_gain_eur": round(daily_gain, 2),
            "n_positions":    len(positions)
        },
        "positions": positions,
        "history":   history
    }

    with open("portfolio.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n✅ portfolio.json guardado")
    print(f"   Modo T212:   {out['t212_mode']}")
    print(f"   Valor total: {total_value:.2f}€")
    print(f"   P&L total:   {total_gain:+.2f}€ ({total_gain_pct:+.2f}%)")
    print(f"   Posições:    {len(positions)}")

if __name__ == "__main__":
    main()
