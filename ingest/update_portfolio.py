#!/usr/bin/env python3
"""
update_portfolio.py — FundScope Portfolio
Symbol resolution flow:
  1. T212 /equity/instruments metadata (isin, currencyCode, name, type)
  2. Static T212_TO_YF map (known symbols)
  3. symbol_cache.json  (previous Gemini resolutions, persisted in repo)
  4. Gemini 2.0 Flash   (last resort — only for unknown symbols)
"""

import json, os, re, sys, time, datetime, requests, base64
import numpy as np
import pandas as pd
import yfinance as yf

# Força UTF-8 no terminal Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass  # GitHub Actions injeta as variáveis directamente; dotenv só é necessário localmente

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[AVISO] google-genai não instalado")

try:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from bot import rate_limiter as _rl
    _RL_AVAILABLE = True
except Exception:
    _RL_AVAILABLE = False

# Trading 212 API: HTTP Basic Auth — Authorization: Basic base64(API_ID:API_SECRET).
# Confirmado contra demo.trading212.com: só o esquema id:secret autentica (200).
#   T212_API_ID (key ID) + T212_API_KEY (secret) — igual ao GitHub Actions.
_t212_id     = os.getenv("T212_API_ID", "")
_t212_secret = os.getenv("T212_API_KEY", "")
_missing = [n for n, v in (("T212_API_ID", _t212_id), ("T212_API_KEY", _t212_secret)) if not v]
if _missing:
    print(f"[ERRO FATAL] Secrets em falta: {', '.join(_missing)} — verifica GitHub Actions secrets ou .env local", flush=True)
    raise SystemExit(1)
print(f"[init] T212_API_ID len={len(_t212_id)} | T212_API_KEY len={len(_t212_secret)}", flush=True)
_creds    = base64.b64encode(f"{_t212_id}:{_t212_secret}".encode()).decode()
T212_AUTH = f"Basic {_creds}"

FH_TOKEN   = os.getenv("FINNHUB_TOKEN") or os.getenv("FINNHUB_API_KEY") or ""
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
FH_BASE    = "https://finnhub.io/api/v1"
T212_BASE  = "https://demo.trading212.com/api/v0"   # Demo — NUNCA apontar para live

gemini_client = None
if GEMINI_AVAILABLE and GEMINI_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_KEY)
        print("[OK] Gemini client inicializado")
    except Exception as e:
        print(f"[AVISO] Gemini init falhou: {e}")

# ===========================================================
# MAPA ESTÁTICO  T212 clean key → yfinance ticker
# Chave = ticker_t212 sem sufixo (_EQ, _US_EQ, etc.)
# ===========================================================
T212_TO_YF = {
    # — Portfólio actual: códigos opacos T212 → yfinance —
    "MTEd":  "MU",
    "49Vd":  "VST",
    "0V6d":  "VRT",
    "CJ6d":  "CCJ",
    "ASMLa": "ASML.AS",   # Euronext Amsterdam (EUR), não NASDAQ
    # Tickers directos US (sem código especial)
    "AAPLd": "AAPL",   "MSFTd": "MSFT",   "TSLAd": "TSLA",
    "AMZNd": "AMZN",   "GOOGLd":"GOOGL",  "AMDd":  "AMD",
    "AVGOd": "AVGO",   "NVDAd": "NVDA",   "METAd": "META",
    "JPMd":  "JPM",    "Vd":    "V",       "MAd":   "MA",
    "LLYd":  "LLY",    "UNHd":  "UNH",    "XOMd":  "XOM",
    "NEEd":  "NEE",    "CEGd":  "CEG",     "NRGd":  "NRG",
    "CCJd":  "CCJ",
    "ARM":   "ARM",        # ARM Holdings (NASDAQ)
    # ETFs europeus frequentes
    "VWCE":  "VWCE.DE", "VWRA": "VWRA.L",  "VUAA": "VUAA.DE",
    "VUSA":  "VUSA.AS", "VEUR": "VEUR.AS",  "VFEM": "VFEM.AS",
    "CSPX":  "CSPX.L",  "IWDA": "IWDA.AS",  "EUNL": "EUNL.DE",
    "SXR8":  "SXR8.DE", "IUSQ": "IUSQ.DE",  "SPPW": "SPPW.DE",
    "XDWD":  "XDWD.DE", "EQQQ": "EQQQ.L",   "MEUD": "MEUD.PA",
    "VHYL":  "VHYL.AS", "VDIV": "VDIV.AS",  "VAGP": "VAGP.L",
    "IMAE":  "IMAE.AS", "IQQQ": "IQQQ.DE",  "EMIM": "EMIM.L",
    "AGGH":  "AGGH.L",  "SSAC": "SSAC.L",   "CNDX": "CNDX.L",
    "SPYY":  "SPYY.DE", "SPYW": "SPYW.DE",  "SMEA": "SMEA.DE",
}

YF_NAMES = {
    "MU":      "Micron Technology",
    "VST":     "Vistra Corp",
    "VRT":     "Vertiv Holdings",
    "CCJ":     "Cameco Corp",
    "ASML":    "ASML Holding",
    "AAPL":    "Apple Inc.",
    "MSFT":    "Microsoft",
    "TSLA":    "Tesla",
    "AMZN":    "Amazon",
    "GOOGL":   "Alphabet",
    "AMD":     "AMD",
    "AVGO":    "Broadcom",
    "NVDA":    "NVIDIA",
    "META":    "Meta Platforms",
    "JPM":     "JPMorgan Chase",
    "V":       "Visa",
    "MA":      "Mastercard",
    "LLY":     "Eli Lilly",
    "UNH":     "UnitedHealth",
    "XOM":     "ExxonMobil",
    "NEE":     "NextEra Energy",
    "CEG":     "Constellation Energy",
    "NRG":     "NRG Energy",
    "VWCE.DE": "Vanguard FTSE All-World Acc (Xetra)",
    "IWDA.AS": "iShares Core MSCI World (AMS)",
    "EUNL.DE": "iShares Core MSCI World (Xetra)",
    "CSPX.L":  "iShares Core S&P 500 (LSE)",
    "SXR8.DE": "iShares Core S&P 500 (Xetra)",
    "VUSA.AS": "Vanguard S&P 500 UCITS ETF",
    "SPPW.DE": "SPDR S&P 500 UCITS ETF",
    "XDWD.DE": "Xtrackers MSCI World",
    "VWRA.L":  "Vanguard FTSE All-World Acc (LSE)",
    "VUAA.DE": "Vanguard S&P 500 UCITS Acc (Xetra)",
}

# moeda nativa conhecida por ticker yfinance
YF_CURRENCY = {
    "MU":"USD","VST":"USD","VRT":"USD","CCJ":"USD","ASML":"EUR",
    "AAPL":"USD","MSFT":"USD","TSLA":"USD","AMZN":"USD","GOOGL":"USD",
    "AMD":"USD","AVGO":"USD","NVDA":"USD","META":"USD","JPM":"USD",
    "V":"USD","MA":"USD","LLY":"USD","UNH":"USD","XOM":"USD",
    "NEE":"USD","CEG":"USD","NRG":"USD","ARM":"USD",
    "VWCE.DE":"EUR","IWDA.AS":"USD","EUNL.DE":"USD","CSPX.L":"USD",
    "SXR8.DE":"USD","VUSA.AS":"USD","SPPW.DE":"USD","XDWD.DE":"USD",
    "VWRA.L":"USD","VUAA.DE":"USD",
}

SYMBOL_CACHE_FILE = "symbol_cache.json"


# ===========================================================
# SYMBOL CACHE  (persiste entre runs no repo)
# ===========================================================
def load_symbol_cache():
    try:
        with open(SYMBOL_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_symbol_cache(cache):
    with open(SYMBOL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ===========================================================
# T212 INSTRUMENTS  (metadados: isin, name, currencyCode, type)
# ===========================================================
def fetch_t212_instruments():
    """Devolve dict  ticker_t212 → {isin, name, currencyCode, type}."""
    try:
        r = requests.get(f"{T212_BASE}/equity/metadata/instruments",
                         headers={"Authorization": T212_AUTH}, timeout=20)
        r.raise_for_status()
        data = r.json()
        result = {}
        for item in (data if isinstance(data, list) else []):
            t = item.get("ticker") or item.get("symbol") or ""
            if t:
                result[t] = {
                    "isin":         item.get("isin", ""),
                    "name":         item.get("name", "") or item.get("fullName", ""),
                    "currencyCode": item.get("currencyCode", ""),
                    "type":         item.get("type", ""),
                    "exchange":     item.get("exchange", "") or item.get("marketName", ""),
                }
        print(f"  T212 instruments: {len(result)} entradas carregadas")
        return result
    except Exception as e:
        print(f"  [AVISO] fetch_t212_instruments falhou: {e}")
        return {}


# ===========================================================
# GEMINI SYMBOL RESOLVER
# ===========================================================
def gemini_resolve_symbol(ticker_t212, isin, t212_name, currency_code, exchange):
    """
    Usa Gemini para resolver um ticker T212 desconhecido.
    Devolve dict: {yf_ticker, display_name, currency, exchange_std}
    """
    if not gemini_client:
        return None
    if _RL_AVAILABLE and not _rl.check_and_consume("gemini"):
        print(f"  [Gemini] rate limit reached — skipping resolve for {ticker_t212}", flush=True)
        return None

    prompt = f"""Tens um instrumento financeiro da corretora Trading212 com os seguintes dados:
- Ticker T212: {ticker_t212}
- ISIN: {isin}
- Nome T212: {t212_name}
- Moeda nativa (T212): {currency_code}
- Bolsa/mercado (T212): {exchange}

A conta do investidor está cotada em EUR, mas o título pode ser cotado em USD, EUR, GBP ou outra moeda.

Responde APENAS com um objecto JSON válido com estes campos exactos:
{{
  "yf_ticker": "<ticker para usar no Yahoo Finance, ex: AMZN, META, VWCE.DE, CSPX.L>",
  "display_name": "<nome legível da empresa ou ETF, ex: Amazon, Meta Platforms, Vanguard FTSE All-World>",
  "currency": "<moeda nativa do título, ex: USD, EUR, GBP>",
  "exchange_std": "<bolsa padronizada, ex: NASDAQ, NYSE, XETRA, LSE, EURONEXT>"
}}
Se não souberes com certeza, faz a melhor estimativa com base no ISIN e no nome."""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.1))
        raw = response.text.strip()
        # limpar possível markdown
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'```$', '', raw).strip()
        result = json.loads(raw)
        if "yf_ticker" in result and "currency" in result:
            print(f"    [Gemini] {ticker_t212} → {result['yf_ticker']} ({result['currency']}) | {result.get('display_name','')}")
            return result
    except Exception as e:
        print(f"    [Gemini resolver] {ticker_t212}: {e}")
    return None


# ===========================================================
# MAPEAMENTO PRINCIPAL
# ===========================================================
def clean_t212_key(ticker_t212):
    """Remove sufixos de bolsa do ticker T212."""
    clean = ticker_t212
    for suffix in ["_US_EQ", "_GBX_EQ", "_EUR_EQ", "_GBP_EQ", "_EQ"]:
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]
            break
    return clean


def resolve_position(p, instruments_meta, symbol_cache):
    """
    Resolve yf_ticker, display_name, currency para uma posição T212.
    Modifica p in-place e devolve True se houve resolução por Gemini (cache updated).
    """
    t212 = p["ticker_t212"]
    clean = clean_t212_key(t212)
    cache_updated = False

    # ── 1. Mapa estático ────────────────────────────────────
    if clean in T212_TO_YF:
        yf = T212_TO_YF[clean]
        p["ticker"]         = yf
        p["ticker_display"] = yf.split(".")[0]
        p["display_name"]   = YF_NAMES.get(yf, YF_NAMES.get(yf.split(".")[0], yf.split(".")[0]))
        p["currency"]       = YF_CURRENCY.get(yf, YF_CURRENCY.get(yf.split(".")[0], "USD"))
        print(f"  [mapa] {t212} → {yf} ({p['currency']})")
        return False

    # ── 2. Cache Gemini ─────────────────────────────────────
    if t212 in symbol_cache:
        cached = symbol_cache[t212]
        yf = cached["yf_ticker"]
        p["ticker"]         = yf
        p["ticker_display"] = cached.get("ticker_display", yf.split(".")[0])
        p["display_name"]   = cached.get("display_name", yf.split(".")[0])
        p["currency"]       = cached.get("currency", "USD")
        print(f"  [cache] {t212} → {yf} ({p['currency']})")
        return False

    # ── 3. Metadados T212 + Gemini ───────────────────────────
    meta = instruments_meta.get(t212, {})
    isin     = meta.get("isin", "")
    t212_name= meta.get("name", clean)   # nome real da T212
    currency = meta.get("currencyCode", "USD")
    exchange = meta.get("exchange", "")

    # Tenta já usar o nome/currency da T212 sem Gemini
    # para casos simples (ticker limpo já é um símbolo válido)
    if re.match(r'^[A-Z]{1,5}$', clean):
        # parece um ticker US standard — tentar directamente
        p["ticker"]         = clean
        p["ticker_display"] = clean
        p["display_name"]   = t212_name or clean
        p["currency"]       = currency or "USD"
        print(f"  [auto] {t212} → {clean} ({p['currency']}) [sem Gemini]")
        # Guardar na cache para não repetir
        symbol_cache[t212] = {
            "yf_ticker":     clean,
            "ticker_display":clean,
            "display_name":  t212_name or clean,
            "currency":      currency or "USD",
            "source":        "auto"
        }
        return True

    # ── 4. Gemini (último recurso) ───────────────────────────
    print(f"  [Gemini] a resolver {t212} (isin={isin}, nome='{t212_name}', moeda={currency})...")
    resolved = gemini_resolve_symbol(t212, isin, t212_name, currency, exchange)

    if resolved:
        yf = resolved["yf_ticker"]
        p["ticker"]         = yf
        p["ticker_display"] = resolved.get("ticker_display", yf.split(".")[0])
        if not p["ticker_display"]:
            p["ticker_display"] = yf.split(".")[0]
        p["display_name"]   = resolved.get("display_name", yf.split(".")[0])
        p["currency"]       = resolved.get("currency", "USD")
        symbol_cache[t212] = {
            "yf_ticker":      yf,
            "ticker_display": p["ticker_display"],
            "display_name":   p["display_name"],
            "currency":       p["currency"],
            "exchange_std":   resolved.get("exchange_std", ""),
            "source":         "gemini"
        }
        cache_updated = True
    else:
        # fallback total: usar código T212 limpo
        p["ticker"]         = clean
        p["ticker_display"] = clean
        p["display_name"]   = t212_name or clean
        p["currency"]       = currency or "USD"
        print(f"  [fallback] {t212} → {clean}")

    return cache_updated


# ===========================================================
# T212 / yfinance helpers
# ===========================================================
def t212_get(path):
    r = requests.get(f"{T212_BASE}{path}",
                     headers={"Authorization": T212_AUTH}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_t212_cash():
    """Devolve dict com dados de caixa da conta T212: free, invested, ppl."""
    try:
        data = t212_get("/equity/account/cash")
        return {
            "free":     round(float(data.get("free",     0)), 2),
            "invested": round(float(data.get("invested", 0)), 2),
            "ppl":      round(float(data.get("ppl",      0)), 2),
        }
    except Exception as e:
        print(f"  [AVISO] fetch_t212_cash falhou: {e}")
        return None


def _send_telegram_alert(msg: str) -> None:
    """Envia alerta Telegram via requests directo (sem depender do bot.notifier)."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print(f"  [Telegram] credenciais ausentes — alerta não enviado: {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"  [Telegram] falha ao enviar alerta: {e}")


_t212_last_error: str | None = None


def fetch_t212_positions():
    """Devolve lista de posições T212 ou None em caso de falha de API.

    None (falha de API) é distinto de [] (carteira genuinamente vazia):
    - None  → caller deve registar o erro e não actualizar portfolio.json
    - []    → carteira vazia, comportamento normal
    """
    global _t212_last_error
    try:
        data = t212_get("/equity/portfolio")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        body = ""
        if e.response is not None:
            body = (e.response.text or "")[:200].replace("\n", " ")
        _t212_last_error = f"HTTP {status}: {body or str(e)[:200]}"
        print(f"  [ERRO] T212 /equity/portfolio {_t212_last_error}", flush=True)
        return None
    except requests.exceptions.Timeout as e:
        _t212_last_error = f"timeout ({e})"
        print(f"  [ERRO] T212 /equity/portfolio timeout: {e}", flush=True)
        return None
    except requests.exceptions.ConnectionError as e:
        _t212_last_error = f"connection error ({type(e).__name__})"
        print(f"  [ERRO] T212 /equity/portfolio connection: {e}", flush=True)
        return None
    except Exception as e:
        _t212_last_error = f"{type(e).__name__}: {e}"
        print(f"  [ERRO] T212 /equity/portfolio inacessivel: {_t212_last_error}", flush=True)
        return None
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
            "fx_ppl":        round(float(p.get("fxPpl", 0) or 0), 2),
        })
    return positions

def fetch_quotes_yf(yf_tickers):
    if not yf_tickers:
        return {}
    print(f"  yfinance: {len(yf_tickers)} tickers...")
    quotes = {}
    tickers_str = " ".join(yf_tickers) if len(yf_tickers) > 1 else yf_tickers[0]
    try:
        data = yf.download(tickers_str, period="5d", interval="1d",
                           auto_adjust=True, progress=False, threads=True, group_by="ticker")
    except Exception as e:
        print(f"  [ERRO yfinance batch]: {e}")
        data = None
    for t in yf_tickers:
        try:
            if data is not None:
                col = data["Close"] if len(yf_tickers) == 1 else data[t]["Close"]
                vals = col.dropna()
                price = float(vals.iloc[-1])
                pc    = float(vals.iloc[-2]) if len(vals) >= 2 else price
            else:
                tk    = yf.Ticker(t)
                hist  = tk.history(period="5d")
                price = float(hist["Close"].iloc[-1])
                pc    = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
            chg = round((price - pc) / pc * 100, 2) if pc else 0.0
            quotes[t] = {"price": round(price, 4), "changePct": chg}
        except Exception as e:
            print(f"  [SKIP] {t}: {e}")
    return quotes

def fetch_earnings(yf_ticker):
    try:
        hist = yf.Ticker(yf_ticker).earnings_history
        if hist is None or hist.empty:
            return []
        records = []
        for _, row in hist.tail(8).iterrows():
            eps_est  = row.get("epsEstimate") or row.get("EpsEstimate")
            eps_act  = row.get("epsActual")   or row.get("EpsActual")
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
        divs = yf.Ticker(yf_ticker).dividends
        if divs is None or divs.empty:
            return []
        return list(reversed([{"date": str(dt.date()), "amount": round(float(v), 4)}
                               for dt, v in divs.tail(4).items()]))
    except Exception:
        return []

def get_display_name_yf(ticker_yf):
    """Tenta obter nome via yfinance se não estiver no mapa estático."""
    if ticker_yf in YF_NAMES:
        return YF_NAMES[ticker_yf]
    base = ticker_yf.split(".")[0]
    if base in YF_NAMES:
        return YF_NAMES[base]
    try:
        info = yf.Ticker(ticker_yf).info
        name = info.get("longName") or info.get("shortName") or ""
        if name:
            return name
    except Exception:
        pass
    return base

def _fh_get(endpoint, params, retries=3):
    """Finnhub GET with exponential backoff on 429 rate-limit responses."""
    if _RL_AVAILABLE and not _rl.check_and_consume("finnhub"):
        print(f"  [Finnhub] rate limit reached — skipping {endpoint}", flush=True)
        return None
    for attempt in range(retries):
        try:
            r = requests.get(f"{FH_BASE}{endpoint}", params={**params, "token": FH_TOKEN}, timeout=10)
            if r.status_code == 429:
                wait = 2 ** attempt   # 1s → 2s → 4s
                print(f"  [Finnhub] 429 rate-limit on {endpoint} — retry in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(1)
                continue
    return None


def fetch_ticker_info(yf_ticker):
    """Enriched metadata from yfinance.info — PE, beta, 52w range, short ratio, dividend yield."""
    try:
        info = yf.Ticker(yf_ticker).info
        hi52 = info.get("fiftyTwoWeekHigh")
        lo52 = info.get("fiftyTwoWeekLow")
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        pct_from_hi = round((price - hi52) / hi52 * 100, 2) if (price and hi52) else None
        return {
            "trailingPE":       info.get("trailingPE"),
            "forwardPE":        info.get("forwardPE"),
            "marketCap":        info.get("marketCap"),
            "beta":             info.get("beta"),
            "fiftyTwoWeekHigh": hi52,
            "fiftyTwoWeekLow":  lo52,
            "pctFromHigh":      pct_from_hi,
            "shortRatio":       info.get("shortRatio"),
            "dividendYield":    info.get("dividendYield"),
        }
    except Exception as e:
        print(f"  [ticker_info] {yf_ticker}: {e}")
        return {}


def fh_basic_financials(ticker_yf):
    """
    Finnhub /stock/metric?metric=all — fundamentais ricos disponíveis no Basic plan.
    Complementa fetch_ticker_info (yfinance) com dados mais fiáveis e granulares.
    """
    base = ticker_yf.split(".")[0]
    if not FH_TOKEN:
        return {}
    try:
        r = _fh_get("/stock/metric", {"symbol": base, "metric": "all"})
        if r is None:
            return {}
        m = r.json().get("metric") or {}
        return {
            "52WeekHigh":              m.get("52WeekHigh"),
            "52WeekLow":               m.get("52WeekLow"),
            "52WeekHighDate":          m.get("52WeekHighDate"),
            "52WeekLowDate":           m.get("52WeekLowDate"),
            "beta":                    m.get("beta"),
            "peAnnual":                m.get("peAnnual"),
            "peTTM":                   m.get("peTTM"),
            "pbAnnual":                m.get("pbAnnual"),
            "epsGrowthTTMYoy":         m.get("epsGrowthTTMYoy"),
            "epsGrowth3Y":             m.get("epsGrowth3Y"),
            "revenueGrowthTTMYoy":     m.get("revenueGrowthTTMYoy"),
            "revenueGrowth3Y":         m.get("revenueGrowth3Y"),
            "grossMarginTTM":          m.get("grossMarginTTM"),
            "netMarginTTM":            m.get("netMarginTTM"),
            "roeTTM":                  m.get("roeTTM"),
            "roaRfy":                  m.get("roaRfy"),
            "currentRatioAnnual":      m.get("currentRatioAnnual"),
            "debtToEquityAnnual":      m.get("totalDebt/totalEquityAnnual"),
            "dividendYieldIndicated":  m.get("dividendYieldIndicatedAnnual"),
            "marketCapitalization":    m.get("marketCapitalization"),
        }
    except Exception as e:
        print(f"  [FH metric] {base}: {e}")
    return {}


def fh_recommendation(ticker_yf):
    """Latest analyst recommendation counts from Finnhub."""
    base = ticker_yf.split(".")[0]
    if not FH_TOKEN:
        return {}
    try:
        r = _fh_get("/recommendation", {"symbol": base})
        if r is None:
            return {}
        data = r.json()
        if data:
            latest = data[0]
            return {
                "strongBuy":  latest.get("strongBuy", 0),
                "buy":        latest.get("buy", 0),
                "hold":       latest.get("hold", 0),
                "sell":       latest.get("sell", 0),
                "strongSell": latest.get("strongSell", 0),
                "period":     latest.get("period", ""),
            }
    except Exception as e:
        print(f"  [FH rec] {base}: {e}")
    return {}


def fh_insider_sentiment(ticker_yf):
    """Net insider buy/sell sentiment (MSPR) for the last 3 months from Finnhub."""
    base = ticker_yf.split(".")[0]
    if not FH_TOKEN:
        return {}
    try:
        today  = datetime.date.today()
        frm_3m = (today - datetime.timedelta(days=90)).isoformat()
        r = _fh_get("/stock/insider-sentiment", {"symbol": base, "from": frm_3m, "to": today.isoformat()})
        if r is None:
            return {}
        data   = r.json()
        points = [d.get("mspr", 0) for d in (data.get("data") or []) if d.get("mspr") is not None]
        if not points:
            return {}
        avg = sum(points) / len(points)
        return {
            "mspr_avg": round(avg, 4),
            "signal":   "buying" if avg > 0 else ("selling" if avg < 0 else "neutral"),
            "months":   len(points),
        }
    except Exception as e:
        print(f"  [FH insider] {base}: {e}")
    return {}


def fh_news(ticker_yf, frm, to, limit=5):
    base_symbol = ticker_yf.split(".")[0]
    try:
        r = _fh_get("/company-news", {"symbol": base_symbol, "from": frm, "to": to})
        if r is None:
            return []
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
        out.append({"source": item.get("source", base_symbol), "headline": h[:180],
                    "summary": (item.get("summary") or "")[:300],
                    "url": item.get("url", ""), "datetime": item.get("datetime", 0),
                    "image": item.get("image", "")})
        if len(out) >= limit:
            break
    return out

def gemini_analyze(ticker, name, news_list, earnings_list, ppl, pct_change):
    if not gemini_client:
        return {"sentiment": "neutro", "news_comment": "Gemini indisponível.",
                "earnings_comment": "Gemini indisponível.", "watch_points": []}
    if _RL_AVAILABLE and not _rl.check_and_consume("gemini"):
        print(f"  [Gemini] rate limit reached — skipping analysis for {ticker}", flush=True)
        return {"sentiment": "neutro", "news_comment": "Rate limit atingido.",
                "earnings_comment": "Rate limit atingido.", "watch_points": []}
    news_text = "\n".join([f"- {n['headline']}" for n in news_list]) or "Sem notícias."
    earn_text = "\n".join([
        f"- {e['period']}: real={e['actual']} est={e['estimate']} {'BEAT' if e.get('beat') else 'MISS'}"
        for e in earnings_list]) or "Sem dados."
    prompt = f"""Analisa {ticker} ({name}) em PT-PT.
P&L: {'+' if ppl>=0 else ''}{ppl:.2f}€, hoje: {pct_change:+.2f}%
Notícias:\n{news_text}\nEarnings:\n{earn_text}
Responde APENAS JSON: {{"sentiment":"positivo|negativo|neutro","news_comment":"...","earnings_comment":"...","watch_points":["...","...","..."]}}"""
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash", contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.3))
        return json.loads(response.text.strip())
    except Exception as e:
        print(f"  [Gemini] {ticker}: {e}")
        return {"sentiment": "neutro", "news_comment": "Indisponível.",
                "earnings_comment": "Indisponível.", "watch_points": []}

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
    return sorted(history, key=lambda x: x["date"])[-365:]


def calculate_benchmark_metrics(history: list) -> dict:
    """
    Calcula CAGR, Sharpe, Max Drawdown, Calmar e Alpha vs SPY a partir do histórico.
    Requer pelo menos 10 pontos. Devolve {} se insuficiente ou em caso de erro.
    """
    if len(history) < 10:
        return {}
    try:
        df = pd.DataFrame(history).sort_values("date")
        df["date"]  = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"]).reset_index(drop=True)
        if len(df) < 10:
            return {}

        start_dt  = df["date"].iloc[0]
        end_dt    = df["date"].iloc[-1]
        years     = max((end_dt - start_dt).days / 365.25, 1e-6)
        start_val = float(df["value"].iloc[0])
        end_val   = float(df["value"].iloc[-1])
        if start_val <= 0:
            return {}

        portfolio_cagr = (end_val / start_val) ** (1.0 / years) - 1.0

        daily_ret = df["value"].pct_change().dropna()
        std = float(daily_ret.std())
        sharpe = float(daily_ret.mean() * 252) / (std * float(np.sqrt(252))) if std > 0 else 0.0

        cummax = df["value"].cummax()
        max_dd = float((1 - df["value"] / cummax).max())
        calmar = (portfolio_cagr / max_dd) if max_dd > 0 else None

        spy_end_str = (end_dt + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        spy_raw = yf.download("SPY", start=start_dt.strftime("%Y-%m-%d"), end=spy_end_str,
                              auto_adjust=True, progress=False, threads=False)
        spy_cagr = alpha = None
        if spy_raw is not None and len(spy_raw) >= 2:
            close_arr = spy_raw["Close"].to_numpy().flatten()
            s0 = float(close_arr[0])
            s1 = float(close_arr[-1])
            if s0 > 0:
                spy_cagr = (s1 / s0) ** (1.0 / years) - 1.0
                alpha    = portfolio_cagr - spy_cagr

        def pct2(v):
            return round(float(v) * 100, 2) if v is not None else None

        return {
            "portfolio_cagr": pct2(portfolio_cagr),
            "spy_cagr":       pct2(spy_cagr),
            "alpha":          pct2(alpha),
            "sharpe_ratio":   round(sharpe, 2),
            "max_drawdown":   pct2(-max_dd),
            "calmar_ratio":   round(float(calmar), 2) if calmar is not None else None,
            "period_days":    int((end_dt - start_dt).days),
            "last_updated":   datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    except Exception as exc:
        print(f"  [benchmark] erro: {exc}")
        return {}


# ===========================================================
# MAIN
# ===========================================================
def main():
    now   = datetime.datetime.utcnow()
    today = now.date()
    frm   = (today - datetime.timedelta(days=7)).isoformat()
    to    = today.isoformat()

    print("=== FundScope Portfolio Update ===", flush=True)
    print(f"UTC: {now.isoformat()}", flush=True)
    print(f"[env] FINNHUB_TOKEN={'set' if FH_TOKEN else 'MISSING'} | "
          f"GEMINI_API_KEY={'set' if GEMINI_KEY else 'MISSING'} | "
          f"TELEGRAM={'set' if os.getenv('TELEGRAM_BOT_TOKEN') else 'MISSING'}", flush=True)

    # Carregar cache de símbolos
    symbol_cache = load_symbol_cache()
    print(f"[cache] {len(symbol_cache)} símbolos em cache")

    print("\n[1] A buscar posições T212 (live)...")
    positions = fetch_t212_positions()

    if positions is None:
        # Falha de API T212 — portfolio.json NÃO é actualizado para preservar os dados anteriores
        utc_str = now.strftime("%Y-%m-%d %H:%M UTC")
        cause = _t212_last_error or "erro desconhecido (sem detalhe capturado)"
        err_msg = (
            f"⚠️ <b>FundScope — T212 API FALHOU</b>\n"
            f"portfolio.json <b>não foi actualizado</b> neste ciclo ({utc_str}).\n"
            f"Causa: /equity/portfolio devolveu <code>{cause}</code>.\n"
            f"O ficheiro anterior é mantido até à próxima run bem-sucedida."
        )
        print(f"\n[ERRO CRÍTICO] {err_msg}", flush=True)
        _send_telegram_alert(err_msg)
        return

    print(f"    {len(positions)} posições encontradas")

    print("\n[1b] A buscar saldo de caixa T212...")
    cash_data = fetch_t212_cash()
    cash_available = cash_data["free"] if cash_data else None
    t212_invested_eur = cash_data["invested"] if cash_data else None
    print(f"    Caixa livre: {cash_available:.2f}€ | investido: {t212_invested_eur:.2f}€" if cash_data else "    Caixa: indisponível")

    print("\n[2] A buscar metadados de instrumentos T212...")
    instruments_meta = fetch_t212_instruments()

    print("\n[3] A resolver tickers (mapa → cache → Gemini)...")
    cache_dirty = False
    for p in positions:
        updated = resolve_position(p, instruments_meta, symbol_cache)
        if updated:
            cache_dirty = True

    # Guardar cache actualizada no repo (commit feito pelo workflow)
    if cache_dirty:
        save_symbol_cache(symbol_cache)
        print(f"  [cache] guardada ({len(symbol_cache)} entradas)")

    # Enriquecer display_name com yfinance se ainda for necessário
    for p in positions:
        if not p.get("display_name") or p["display_name"] == p.get("ticker"):
            p["display_name"] = get_display_name_yf(p["ticker"])

    print("\n[4] A buscar cotações yfinance + taxa EURUSD (display only)...")
    yf_tickers = list(dict.fromkeys(p["ticker"] for p in positions))
    quotes = fetch_quotes_yf(yf_tickers + ["EURUSD=X"])

    # EURUSD para display de preços unitários — não afecta P&L (que vem da T212 em EUR)
    eurusd = quotes.get("EURUSD=X", {}).get("price")
    if not eurusd:
        try:
            _fx_hist = yf.Ticker("EURUSD=X").history(period="5d")
            if not _fx_hist.empty:
                eurusd = float(_fx_hist["Close"].dropna().iloc[-1])
                print(f"  [EURUSD yfinance] {eurusd:.4f}")
        except Exception:
            pass
    if not eurusd:
        # Fallback: frankfurter.app (API pública, sem chave)
        try:
            _r = requests.get("https://api.frankfurter.app/latest?from=USD&to=EUR", timeout=5)
            if _r.ok:
                eurusd = 1.0 / float(_r.json()["rates"]["EUR"])
                print(f"  [EURUSD frankfurter] {eurusd:.4f}")
        except Exception:
            pass
    if not eurusd and t212_invested_eur:
        # Derivar implicitamente da contabilidade T212 (invested EUR / cost basis USD)
        _cost_usd = sum(p["avg_price"] * p["quantity"] for p in positions)
        if _cost_usd > 0:
            eurusd = _cost_usd / t212_invested_eur
            print(f"  [EURUSD T212-implied] {eurusd:.4f}")
    eurusd = eurusd or 1.12  # último recurso: estimativa conservadora
    print(f"  Taxa EURUSD: {eurusd:.4f}")

    for p in positions:
        q = quotes.get(p["ticker"], {})
        if q.get("price"):
            p["current_price"] = q["price"]
        p["change_pct"] = q.get("changePct", 0.0)

    # ── Cálculo EUR — fonte primária: T212 (ppl em EUR, invested alocado proporcionalmente) ──
    # Alocar t212_invested_eur por posição proporcional ao custo USD de cada uma.
    # Isto elimina a dependência de EURUSD para os valores financeiros fundamentais.
    total_cost_usd = sum(p["avg_price"] * p["quantity"] for p in positions)
    for p in positions:
        native_currency = p.get("currency", "USD")

        gain_eur = round(float(p["ppl"]), 2)
        p["gain_eur"] = gain_eur

        if t212_invested_eur is not None and total_cost_usd > 0:
            # Custo base EUR proporcional (T212 authoritative)
            weight = (p["avg_price"] * p["quantity"]) / total_cost_usd
            p["invested"] = round(t212_invested_eur * weight, 2)
        else:
            # Fallback: conversão via EURUSD
            fx = (1.0 / eurusd) if native_currency == "USD" else 1.0
            p["invested"] = round(p["avg_price"] * p["quantity"] * fx, 2)

        p["value_eur"] = round(p["invested"] + gain_eur, 2)
        p["gain_pct"]  = round(gain_eur / p["invested"] * 100 if p["invested"] > 0 else 0, 2)
        p["fx_rate"]   = round(1.0 / eurusd if native_currency == "USD" and eurusd else 1.0, 6)
        p["currency_native"] = native_currency

    total_value    = round(sum(p["value_eur"] for p in positions) + (cash_available or 0), 2)
    total_invested = sum(p["invested"]  for p in positions)
    total_gain     = sum(p["gain_eur"]  for p in positions)
    total_gain_pct = (total_gain / total_invested * 100) if total_invested > 0 else 0

    # BUG CORRIGIDO 4: a fórmula anterior (value_eur × change_pct / 100) usa o valor ATUAL
    # como base, mas change_pct é relativo ao fecho ANTERIOR (prev_close). Isso sobrestima o
    # ganho diário em ~change_pct%. Fórmula correta: prev_value × change_pct / 100, onde
    # prev_value = value_eur / (1 + change_pct/100) = value_eur × 100 / (100 + change_pct).
    daily_gain = sum(
        p["value_eur"] * p["change_pct"] / (100.0 + p["change_pct"])
        if (100.0 + p["change_pct"]) != 0 else 0.0
        for p in positions
    )
    for p in positions:
        p["allocation_pct"] = round(p["value_eur"] / total_value * 100, 2) if total_value > 0 else 0
        # Stub para previsões Clyde (UI aba Gains, Secção 3). Quando Clyde expuser
        # um forecast dedicado, substituir por {"estimate": int 0-100, "target_pct": float}.
        p["clyde_forecast"] = None
    positions.sort(key=lambda x: x["value_eur"], reverse=True)

    print("\n[5] Notícias + Earnings + Fundamentais + Gemini análise...")
    for p in positions:
        ticker    = p["ticker"]
        disp_name = p["display_name"]
        print(f"  → {ticker} ({disp_name})")
        p["news"]        = fh_news(ticker, frm, to)
        time.sleep(0.2)
        p["earnings"]    = fetch_earnings(ticker)
        p["dividends"]   = fetch_dividends(ticker)
        p["ticker_info"]      = fetch_ticker_info(ticker)
        p["fh_fundamentals"]  = fh_basic_financials(ticker)
        time.sleep(0.2)
        p["analysts"]    = fh_recommendation(ticker)
        p["insider"]     = fh_insider_sentiment(ticker)
        p["analysis"]    = gemini_analyze(
            p["ticker_display"], disp_name,
            p["news"], p["earnings"],
            p["gain_eur"], p["change_pct"])
        time.sleep(0.8)

    print("\n[6] Histórico...")
    history = update_history(load_history(), total_value)

    print("\n[7] Benchmark metrics (CAGR, Sharpe, DD, Alpha vs SPY)...")
    bm = calculate_benchmark_metrics(history)
    if bm:
        print(f"   CAGR: {bm.get('portfolio_cagr')}%  Sharpe: {bm.get('sharpe_ratio')}  "
              f"DD: {bm.get('max_drawdown')}%  Alpha: {bm.get('alpha')}pp  ({bm.get('period_days')}d)")
    else:
        print("   Histórico insuficiente (<10 pontos) — benchmark_metrics omitido")

    out = {
        "updated":   now.isoformat() + "Z",
        "t212_mode": "live",
        "summary": {
            "total_value":     round(total_value, 2),
            "total_invested":  round(total_invested, 2),
            "total_gain_eur":  round(total_gain, 2),
            "total_gain_pct":  round(total_gain_pct, 2),
            "daily_gain_eur":  round(daily_gain, 2),
            "cash_available":  cash_available,
            "n_positions":     len(positions)
        },
        "positions": positions,
        "history":   history,
        "benchmark_metrics": bm,
    }
    tmp = "portfolio.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, "portfolio.json")

    print("\n[8] Análise de Gains (CRO)...")
    _maybe_regenerate_gains_analysis()

    print(f"\n✅ Concluído!")
    print(f"   Valor: {total_value:.2f}€ | P&L: {total_gain:+.2f}€ | Posições: {len(positions)}")
    print("\n   Mapeamento final:")
    for p in positions:
        print(f"   {p['ticker_t212']} → {p['ticker']} | {p['display_name']} | {p.get('currency','?')}")


def _maybe_regenerate_gains_analysis():
    """Regenera data/gains_analysis.json se houver nova posição fechada.

    Comparação por trade id (campo `id` em beta_trades.json). Se o último
    trade fechado coincide com `last_closed_trade_id` da análise existente,
    o ficheiro não é tocado — UI continua a mostrar a análise anterior.
    """
    from pathlib import Path

    beta_path = Path("data/beta/beta_trades.json")
    if not beta_path.exists():
        print("   [gains] beta_trades.json não existe — skip")
        return

    try:
        with open(beta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"   [gains] erro a ler beta_trades.json: {exc}")
        return

    trades = data.get("trades", []) if isinstance(data, dict) else []
    closed = [t for t in trades
              if t.get("closed_at") and t.get("result_eur") is not None]
    if not closed:
        print("   [gains] sem trades fechados — skip")
        return

    last_id = sorted(closed, key=lambda x: x.get("closed_at", ""))[-1].get("id")

    out_path = Path("data/gains_analysis.json")
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("last_closed_trade_id") == last_id:
                print(f"   [gains] análise actualizada (last: {last_id}) — skip")
                return
        except (json.JSONDecodeError, OSError):
            pass

    try:
        from bot.cro import CRO
    except ImportError as exc:
        print(f"   [gains] não foi possível importar CRO: {exc}")
        return

    print(f"   [gains] nova posição fechada ({last_id}) — a regenerar análise...")
    try:
        analysis = CRO().analyze_gains()
    except Exception as exc:
        print(f"   [gains] falha em CRO.analyze_gains(): {exc}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
        os.replace(tmp, out_path)
        print(f"   [gains] análise escrita: {out_path} "
              f"({analysis.get('trades_analysed', 0)} trades)")
    except OSError as exc:
        print(f"   [gains] erro a escrever ficheiro: {exc}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
