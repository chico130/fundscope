#!/usr/bin/env python3
"""
update_earnings.py — Gera earnings.json com calendário dos próximos 14 dias.

Universo de tickers (deriva-se em runtime, NÃO é hardcoded):
  • data/beta/watchlist.json        — candidates[*].ticker
  • data/beta/beta_positions.json   — positions[*].ticker / display_name
  • data/alpha/portfolio.json       — positions[*].ticker / ticker_display
    (com fallback para ./portfolio.json na raiz quando o caminho alpha/
     ainda não existe — segue a arquitetura definida em FUNDSCOPE_CLAUDE_CODE_SPEC.md)

Para cada ticker extrai via yfinance:
  ticker, nome, data (YYYY-MM-DD), hora (BMO/AMC/N/D),
  eps_estimado, eps_anterior, revenue_estimado, revenue_anterior,
  surpresa_media_pct  (média das 4 últimas surpresas em %)

Após escrever earnings.json, faz git add + commit + push.

Modo read-only relativamente ao motor do bot — apenas ESCREVE earnings.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "yfinance", "-q"], check=True)
    import yfinance as yf

BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "earnings.json"

WATCHLIST_PATH       = BASE_DIR / "data" / "beta" / "watchlist.json"
BETA_POSITIONS_PATH  = BASE_DIR / "data" / "beta" / "beta_positions.json"
ALPHA_PORTFOLIO_PATH = BASE_DIR / "data" / "alpha" / "portfolio.json"
ROOT_PORTFOLIO_PATH  = BASE_DIR / "portfolio.json"

WINDOW_DAYS = 14


# ---------------------------------------------------------------------------
# Universo de tickers
# ---------------------------------------------------------------------------

def _norm(sym: str) -> str:
    """Normaliza um símbolo: remove sufixo T212 (_US_EQ, _EQ) e maiúsculas."""
    if not sym:
        return ""
    return sym.split("_")[0].strip().upper()


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def collect_tickers() -> list[str]:
    """Constrói o universo deduplicado a partir das três fontes da arquitetura."""
    tickers: set[str] = set()

    # 1. Watchlist master
    wl = _load_json(WATCHLIST_PATH)
    if isinstance(wl, dict):
        for item in wl.get("candidates", []) or []:
            t = _norm(item.get("ticker", ""))
            if t:
                tickers.add(t)

    # 2. Beta — posições do bot. `ticker` traz sufixo T212 (ex: GOOGL_US_EQ).
    bp = _load_json(BETA_POSITIONS_PATH)
    if isinstance(bp, dict):
        for pos in bp.get("positions", []) or []:
            t = _norm(pos.get("ticker", "")) or _norm(pos.get("ticker_display", ""))
            if t:
                tickers.add(t)

    # 3. Alpha — posições reais (caminho canónico + fallback raiz).
    # display_name aqui é o nome da empresa ("Alphabet"), por isso é ignorado.
    alpha = _load_json(ALPHA_PORTFOLIO_PATH) or _load_json(ROOT_PORTFOLIO_PATH)
    if isinstance(alpha, dict):
        for pos in alpha.get("positions", []) or []:
            t = (
                _norm(pos.get("ticker", ""))
                or _norm(pos.get("ticker_display", ""))
                or _norm(pos.get("ticker_t212", ""))
            )
            if t:
                tickers.add(t)

    return sorted(tickers)


# ---------------------------------------------------------------------------
# Helpers de parsing yfinance
# ---------------------------------------------------------------------------

def _parse_date(val) -> str | None:
    if val is None:
        return None
    try:
        if hasattr(val, "strftime"):
            return val.strftime("%Y-%m-%d")
        s = str(val)
        return s[:10] if len(s) >= 10 else None
    except Exception:
        return None


def _infer_hora(ts) -> str:
    """Infere BMO/AMC a partir da hora do timestamp se disponível."""
    try:
        if hasattr(ts, "hour"):
            h = int(ts.hour)
            if h < 12:
                return "BMO"
            if h >= 16:
                return "AMC"
    except Exception:
        pass
    return "N/D"


def _earnings_history(ticker_obj):
    """Devolve DataFrame de earnings_history ou None silenciosamente."""
    try:
        hist = ticker_obj.earnings_history
        if hist is None or (hasattr(hist, "empty") and hist.empty):
            return None
        return hist
    except Exception:
        return None


def _mean_surprise(ticker_obj) -> float | None:
    hist = _earnings_history(ticker_obj)
    if hist is None:
        return None
    for col in ("surprisePercent", "Surprise(%)", "epsSurprisePct"):
        if col in getattr(hist, "columns", []):
            try:
                vals = hist[col].dropna().head(4).tolist()
                if vals:
                    return round(sum(float(v) for v in vals) / len(vals), 2)
            except Exception:
                continue
    return None


def _prev_eps(ticker_obj) -> float | None:
    hist = _earnings_history(ticker_obj)
    if hist is None:
        return None
    for col in ("epsActual", "EPS Actual", "actualEPS"):
        if col in getattr(hist, "columns", []):
            try:
                vals = hist[col].dropna()
                if not vals.empty:
                    return float(vals.iloc[0])
            except Exception:
                continue
    return None


def _prev_revenue(ticker_obj) -> float | None:
    """Receita realizada da época anterior (via income_stmt trimestral)."""
    try:
        qis = ticker_obj.quarterly_income_stmt
        if qis is None or (hasattr(qis, "empty") and qis.empty):
            return None
        for label in ("Total Revenue", "TotalRevenue", "Revenue"):
            if label in qis.index:
                col = qis.loc[label].dropna()
                if not col.empty:
                    return float(col.iloc[0])
    except Exception:
        pass
    return None


def fetch_earnings(sym: str, today, cutoff) -> dict | None:
    try:
        t = yf.Ticker(sym)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        name = info.get("longName") or info.get("shortName") or sym

        cal = None
        try:
            cal = t.calendar
        except Exception:
            cal = None
        if not cal or not isinstance(cal, dict):
            return None

        raw_dates = cal.get("Earnings Date", [])
        if not raw_dates:
            return None

        first_ts = raw_dates[0] if isinstance(raw_dates, (list, tuple)) else raw_dates
        date_str = _parse_date(first_ts)
        if not date_str:
            return None

        try:
            ed = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None

        if not (today <= ed <= cutoff):
            return None

        eps_est = cal.get("EPS Estimate") or cal.get("EPS Average")
        rev_est = cal.get("Revenue Average") or cal.get("Revenue Low")

        try:
            eps_est_v = float(eps_est) if eps_est is not None else None
        except (TypeError, ValueError):
            eps_est_v = None
        try:
            rev_est_v = int(rev_est) if rev_est is not None else None
        except (TypeError, ValueError):
            rev_est_v = None

        return {
            "ticker": sym,
            "nome": name,
            "data": date_str,
            "hora": _infer_hora(first_ts),
            "eps_estimado": eps_est_v,
            "eps_anterior": _prev_eps(t),
            "revenue_estimado": rev_est_v,
            "revenue_anterior": _prev_revenue(t),
            "surpresa_media_pct": _mean_surprise(t),
        }
    except Exception as e:
        print(f"    ERRO {sym}: {e}")
        return None


# ---------------------------------------------------------------------------
# Git automation
# ---------------------------------------------------------------------------

def _git(*args: str) -> tuple[int, str]:
    res = subprocess.run(
        ["git", *args],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )
    return res.returncode, (res.stdout + res.stderr).strip()


def push_to_git() -> None:
    rc, out = _git("status", "--porcelain", OUTPUT_PATH.name)
    if rc != 0:
        print(f"[update_earnings] git status falhou: {out}")
        return
    if not out:
        print("[update_earnings] earnings.json sem alterações — sem commit.")
        return

    rc, out = _git("add", OUTPUT_PATH.name)
    if rc != 0:
        print(f"[update_earnings] git add falhou: {out}")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rc, out = _git("commit", "-m", f"data: update earnings.json [{stamp}]")
    if rc != 0:
        print(f"[update_earnings] git commit falhou: {out}")
        return

    rc, out = _git("push")
    if rc != 0:
        print(f"[update_earnings] git push falhou: {out}")
        return
    print("[update_earnings] git push concluído.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()
    cutoff = today + timedelta(days=WINDOW_DAYS)

    print(f"[update_earnings] {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    universe = collect_tickers()
    print(f"[update_earnings] Universo: {len(universe)} tickers "
          f"(watchlist + beta_positions + alpha/portfolio)")
    if not universe:
        print("[update_earnings] ATENÇÃO: universo vazio. Saída sem alterações.")
        return

    results: list[dict] = []
    for sym in universe:
        print(f"  {sym}...", end=" ", flush=True)
        entry = fetch_earnings(sym, today, cutoff)
        if entry:
            results.append(entry)
            print(f"OK  {entry['data']}  {entry['hora']}")
        else:
            print("--")

    results.sort(key=lambda x: (x["data"], x["ticker"]))

    payload = {
        "last_updated": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": WINDOW_DAYS,
        "universe_size": len(universe),
        "earnings": results,
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[update_earnings] OK {len(results)} empresas escritas em {OUTPUT_PATH.name}")

    push_to_git()


if __name__ == "__main__":
    main()