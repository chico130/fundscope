"""
Pré-gera AI insights (Gemini) para os tickers da watchlist + user_universe.

Reutiliza as funções de geração já existentes em `serve.py` — sem duplicar lógica.
Escreve o resultado em `data/beta/ai_insights.json`, que o frontend (stock.html)
lê como fallback estático quando o endpoint `/api/ai-insight` não está disponível
(ex: GitHub Pages).

Uso:
    GEMINI_API_KEY=... python scripts/update_ai_insights.py
    GEMINI_API_KEY=... python scripts/update_ai_insights.py --max 30
    GEMINI_API_KEY=... python scripts/update_ai_insights.py --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

# Garantir que importamos serve.py do root do projecto, não scripts/
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Limpa argv antes de importar serve.py (evita que serve.py tente int(argv[1]))
_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
import serve  # noqa: E402
sys.argv = _saved_argv

WATCHLIST_PATH = "data/beta/watchlist.json"
USER_UNIVERSE_PATH = "data/beta/user_universe.json"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _collect_tickers(max_n: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    # 1) User universe primeiro (prioridade do utilizador)
    try:
        with open(USER_UNIVERSE_PATH, "r", encoding="utf-8") as f:
            uu = json.load(f)
        for t in uu.get("tickers", []) or []:
            t = str(t).upper().strip()
            if t and t not in seen:
                seen.add(t)
                ordered.append(t)
    except FileNotFoundError:
        print(f"[{_ts()}] aviso: {USER_UNIVERSE_PATH} em falta", flush=True)
    except Exception as e:
        print(f"[{_ts()}] erro a ler {USER_UNIVERSE_PATH}: {e}", flush=True)

    # 2) Candidatos do bot (top-N por score)
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            wl = json.load(f)
        for c in wl.get("candidates", []) or []:
            t = str(c.get("ticker", "")).upper().strip()
            if t and t not in seen:
                seen.add(t)
                ordered.append(t)
            if len(ordered) >= max_n:
                break
    except FileNotFoundError:
        print(f"[{_ts()}] aviso: {WATCHLIST_PATH} em falta", flush=True)
    except Exception as e:
        print(f"[{_ts()}] erro a ler {WATCHLIST_PATH}: {e}", flush=True)

    return ordered[:max_n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=25,
                    help="Máximo de tickers a processar por execução (default: 25)")
    ap.add_argument("--force", action="store_true",
                    help="Ignora cache e regenera tudo")
    ap.add_argument("--sleep", type=float, default=1.5,
                    help="Pausa entre chamadas Gemini (segundos)")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print(f"[{_ts()}] ERRO: GEMINI_API_KEY não definido — a abortar", flush=True)
        return 2

    tickers = _collect_tickers(args.max)
    print(f"[{_ts()}] === update_ai_insights START === ({len(tickers)} tickers)", flush=True)
    if not tickers:
        print(f"[{_ts()}] nada a fazer — sem tickers", flush=True)
        return 0

    cache = serve._load_ai_cache()
    cache.setdefault("tickers", {})

    n_fresh = n_ok = n_fail = 0
    for ticker in tickers:
        entry = cache["tickers"].get(ticker)
        if not args.force and serve._is_insight_fresh(entry):
            n_fresh += 1
            print(f"[{_ts()}] {ticker}: fresh em cache — skip", flush=True)
            continue

        meta = serve._static_meta_from_symbol_cache(ticker)
        try:
            result, reason = serve._call_gemini_insight(ticker, meta)
        except Exception as e:
            print(f"[{_ts()}] {ticker}: excepção inesperada — {e}", flush=True)
            n_fail += 1
            continue

        if result is None:
            print(f"[{_ts()}] {ticker}: falha Gemini ({reason})", flush=True)
            n_fail += 1
            continue

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cache["tickers"][ticker] = {
            "ticker": ticker,
            "name": meta["name"],
            "generated_at": now_iso,
            "model": serve.AI_GEMINI_MODEL,
            **result,
        }
        cache["generated_at"] = now_iso
        n_ok += 1
        print(f"[{_ts()}] {ticker}: gerado", flush=True)

        # Persiste em cada iteração para sobreviver a crashes
        try:
            serve._save_ai_cache(cache)
        except Exception as e:
            print(f"[{_ts()}] aviso: falha a gravar cache após {ticker}: {e}", flush=True)

        if args.sleep > 0:
            time.sleep(args.sleep)

    print(
        f"[{_ts()}] === update_ai_insights END === "
        f"(ok={n_ok}, cached={n_fresh}, fail={n_fail})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
