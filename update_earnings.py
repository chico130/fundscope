#!/usr/bin/env python3
"""
update_earnings.py — Gera earnings.json com dados das próximas 2 semanas.
Usa yfinance para ir buscar calendário de earnings e histórico de surpresas.
Após gerar o ficheiro, faz git add/commit/push automaticamente.
"""
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

WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD",
    "NFLX", "CRM", "ASML", "SAP", "UBER", "SHOP", "COIN", "PLTR",
    "SQ", "PYPL", "SNOW", "NET",
]

OUTPUT_PATH = Path(__file__).parent / "earnings.json"


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
            if ts.hour < 12:
                return "BMO"
            elif ts.hour >= 16:
                return "AMC"
    except Exception:
        pass
    return "N/D"


def _mean_surprise(ticker_obj) -> float | None:
    """Calcula surpresa média (%) das últimas 4 épocas."""
    try:
        hist = ticker_obj.earnings_history
        if hist is None or (hasattr(hist, "empty") and hist.empty):
            return None
        for col in ("surprisePercent", "Surprise(%)", "epsSurprisePct"):
            if col in hist.columns:
                vals = hist[col].dropna().head(4).tolist()
                if vals:
                    return round(sum(float(v) for v in vals) / len(vals), 2)
    except Exception:
        pass
    return None


def _prev_eps(ticker_obj) -> float | None:
    """EPS realizado da época mais recente."""
    try:
        hist = ticker_obj.earnings_history
        if hist is None or (hasattr(hist, "empty") and hist.empty):
            return None
        for col in ("epsActual", "EPS Actual"):
            if col in hist.columns:
                val = hist[col].dropna()
                if not val.empty:
                    return float(val.iloc[0])
    except Exception:
        pass
    return None


def fetch_earnings(sym: str) -> dict | None:
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=14)
    try:
        t = yf.Ticker(sym)
        info = t.info or {}
        name = info.get("longName") or info.get("shortName") or sym

        cal = t.calendar
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

        hora = _infer_hora(first_ts)
        eps_est = cal.get("EPS Estimate") or cal.get("EPS Average")
        rev_est = cal.get("Revenue Average") or cal.get("Revenue Low")

        return {
            "ticker": sym,
            "nome": name,
            "data": date_str,
            "hora": hora,
            "eps_estimado": float(eps_est) if eps_est is not None else None,
            "eps_anterior": _prev_eps(t),
            "revenue_estimado": int(rev_est) if rev_est is not None else None,
            "revenue_anterior": None,
            "surpresa_media_pct": _mean_surprise(t),
        }
    except Exception as e:
        print(f"    ERRO {sym}: {e}")
        return None


def main() -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[update_earnings] {now_str}")
    print(f"[update_earnings] A processar {len(WATCHLIST)} tickers...")

    results = []
    for sym in WATCHLIST:
        print(f"  {sym}...", end=" ", flush=True)
        entry = fetch_earnings(sym)
        if entry:
            results.append(entry)
            print(f"OK  {entry['data']}  {entry['hora']}")
        else:
            print("-- sem earnings nos proximos 14 dias")

    results.sort(key=lambda x: x["data"])

    payload = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "earnings": results,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[update_earnings] OK {len(results)} empresas escritas em {OUTPUT_PATH.name}")

    try:
        subprocess.run(["git", "add", OUTPUT_PATH.name], check=True, cwd=str(OUTPUT_PATH.parent))
        subprocess.run(
            ["git", "commit", "-m",
             f"chore: update earnings.json [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}]"],
            check=True, cwd=str(OUTPUT_PATH.parent),
        )
        subprocess.run(["git", "push"], check=True, cwd=str(OUTPUT_PATH.parent))
        print("[update_earnings] git push concluído.")
    except subprocess.CalledProcessError as e:
        print(f"[update_earnings] git: {e}")


if __name__ == "__main__":
    main()
