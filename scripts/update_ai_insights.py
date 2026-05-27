"""
Gera narrativas AI por ticker via Gemini e escreve em data/beta/ai_insights.json.

Princípios (CLAUDE.md):
- Apenas dados estáticos vão ao prompt (ticker, nome). Zero chamadas Finnhub/yfinance.
- TTL 24h por ticker — re-gera só o que está stale.
- Falhas por ticker são isoladas; um erro não derruba o batch.
- Output é JSON committed — frontend (GH Pages) lê o ficheiro estático.

Uso:
    PYTHONPATH=. python scripts/update_ai_insights.py
    PYTHONPATH=. python scripts/update_ai_insights.py --tickers VOO SPY AAPL
    PYTHONPATH=. python scripts/update_ai_insights.py --force   # ignora TTL
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT          = Path(__file__).resolve().parent.parent
OUT           = ROOT / "data" / "beta" / "ai_insights.json"
WATCHLIST     = ROOT / "data" / "beta" / "watchlist.json"
USER_UNIVERSE = ROOT / "data" / "beta" / "user_universe.json"
SYMBOL_CACHE  = ROOT / "symbol_cache.json"

TTL_HOURS     = 24
MODEL         = "gemini-2.5-flash"
MAX_TICKERS   = 40       # custo por execução — hard cap
SLEEP_BETWEEN = 1.5      # segundos entre chamadas (rate-limit defensivo)


# ── helpers ──────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"[warn] falha a ler {path.name}: {e}", flush=True)
        return default


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ── ticker collection ─────────────────────────────────────────

def _collect_tickers() -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()

    wl = _load_json(WATCHLIST, {})
    for cand in wl.get("candidates", []):
        t = str(cand.get("ticker", "")).upper().strip()
        if t and t not in seen:
            seen.add(t)
            tickers.append(t)

    uu = _load_json(USER_UNIVERSE, {})
    for t in uu.get("tickers", []):
        tu = str(t).upper().strip()
        if tu and tu not in seen:
            seen.add(tu)
            tickers.append(tu)

    return tickers[:MAX_TICKERS]


def _static_meta(ticker: str, cache: dict) -> dict:
    for _, v in cache.items():
        if str(v.get("ticker_display", "")).upper() == ticker or \
           str(v.get("yf_ticker", "")).upper() == ticker:
            return {
                "name":     v.get("display_name", ticker),
                "currency": v.get("currency", "USD"),
            }
    return {"name": ticker, "currency": "USD"}


def _is_fresh(entry: dict | None) -> bool:
    if not entry:
        return False
    dt = _parse_iso(entry.get("generated_at", ""))
    if not dt:
        return False
    return _now_utc() - dt < timedelta(hours=TTL_HOURS)


# ── Gemini ───────────────────────────────────────────────────

def _build_prompt(ticker: str, meta: dict) -> str:
    return f"""Resume em PORTUGUÊS de Portugal o contexto de mercado para o ativo abaixo.

Ticker: {ticker}
Nome: {meta['name']}
Moeda: {meta['currency']}

Devolve estritamente um objecto JSON com estas chaves (strings curtas, 1-2 frases cada, sem markdown, sem listas, sem emojis):
- "sentiment": sentimento geral do mercado nos últimos meses sobre este ativo (com base no teu conhecimento até à data de treino).
- "history": breve enquadramento histórico ou de longo prazo (papel no índice, característica estrutural, marcos relevantes).
- "social": perspectivas tipicamente discutidas em fóruns e comunidade de investidores sobre este ativo.

Regras obrigatórias:
- Sê neutro, factual e prudente. Não dês recomendação de compra/venda.
- Se não tens informação fiável, escreve "Informação limitada." nesse campo.
- Devolve APENAS JSON válido, sem blocos ```json.
"""


def _call_gemini(client, ticker: str, meta: dict) -> dict | None:
    from google.genai import types  # importação local para falha não cascatear

    prompt = _build_prompt(ticker, meta)
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
                max_output_tokens=600,
            ),
        )
        text = (resp.text or "").strip()
        if not text:
            print(f"[warn] resposta vazia para {ticker}", flush=True)
            return None
        data = json.loads(text)
        return {
            "sentiment": str(data.get("sentiment", "")).strip()[:600],
            "history":   str(data.get("history",   "")).strip()[:600],
            "social":    str(data.get("social",    "")).strip()[:600],
        }
    except json.JSONDecodeError as e:
        print(f"[error] JSON inválido de Gemini para {ticker}: {e}", flush=True)
        return None
    except Exception as e:
        print(f"[error] Gemini falhou para {ticker}: {e}", flush=True)
        return None


# ── main ─────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Gerar AI insights via Gemini")
    parser.add_argument("--tickers", nargs="*", metavar="TICKER",
                        help="Tickers específicos (por defeito usa watchlist + user_universe)")
    parser.add_argument("--force", action="store_true",
                        help="Ignorar TTL e regenerar mesmo entradas frescas")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[fatal] GEMINI_API_KEY não está definido no ambiente", flush=True)
        return 1

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except ImportError:
        print("[fatal] google-genai não instalado. Correr: pip install google-genai", flush=True)
        return 1

    cache    = _load_json(SYMBOL_CACHE, {})
    existing = _load_json(OUT, {"tickers": {}})
    by_ticker: dict = dict(existing.get("tickers", {}))

    if args.tickers:
        tickers = [t.upper().strip() for t in args.tickers]
    else:
        tickers = _collect_tickers()

    print(f"[{_iso(_now_utc())}] === update_ai_insights START — {len(tickers)} candidatos ===", flush=True)

    refreshed = 0
    skipped   = 0
    failed    = 0

    for tk in tickers:
        if not args.force and _is_fresh(by_ticker.get(tk)):
            skipped += 1
            continue

        print(f"[info] a gerar insight para {tk}…", flush=True)
        meta   = _static_meta(tk, cache)
        result = _call_gemini(client, tk, meta)

        if not result:
            failed += 1
            continue

        by_ticker[tk] = {
            "ticker":       tk,
            "name":         meta["name"],
            "generated_at": _iso(_now_utc()),
            "model":        MODEL,
            **result,
        }
        refreshed += 1
        print(f"[ok] {tk} gerado", flush=True)
        time.sleep(SLEEP_BETWEEN)

    payload = {
        "generated_at": _iso(_now_utc()),
        "ttl_hours":    TTL_HOURS,
        "model":        MODEL,
        "tickers":      by_ticker,
    }

    try:
        _save_json(OUT, payload)
        print(f"[info] escrito em {OUT}", flush=True)
    except Exception as e:
        print(f"[error] falha a guardar {OUT}: {e}", flush=True)
        return 1

    print(
        f"[{_iso(_now_utc())}] === update_ai_insights END — "
        f"refreshed={refreshed} skipped(fresh)={skipped} failed={failed} total={len(by_ticker)} ===",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
