"""
Generates and persists Gemini post-trade insights for realised gains.

Called from ingest/update_portfolio.py step [8b] after each portfolio update.
Reads beta_trades.json, generates Gemini insights for newly closed positive
trades, and stores them in data/beta/gains_insights.json with a 63-day TTL.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_BETA_DIR

GAINS_INSIGHTS_PATH = DATA_BETA_DIR / "gains_insights.json"
GEMINI_MODEL        = "gemini-2.0-flash-lite"
RETENTION_DAYS      = 63
MAX_PER_RUN         = 5


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", text.strip()).strip()


def _load() -> dict:
    try:
        return json.loads(GAINS_INSIGHTS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"generated_at": _ts(), "model": GEMINI_MODEL, "insights": {}}


def _save(data: dict) -> None:
    GAINS_INSIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = GAINS_INSIGHTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(GAINS_INSIGHTS_PATH)


def _clean_ticker(raw: str) -> str:
    return raw.replace("_US_EQ", "").upper()


def _display_name_from_cache(ticker_raw: str, symbol_cache: dict) -> str:
    ticker_up = ticker_raw.upper()
    for v in symbol_cache.values():
        if (
            str(v.get("ticker_display", "")).upper() == ticker_up
            or str(v.get("ticker_t212", "")).upper() == ticker_up
        ):
            return v.get("display_name") or _clean_ticker(ticker_raw)
    return _clean_ticker(ticker_raw)


def _build_prompt(trade: dict, display_name: str) -> str:
    ticker      = _clean_ticker(trade.get("ticker", ""))
    entry_price = trade.get("price") or 0
    result_pct  = trade.get("result_pct") or 0
    result_eur  = trade.get("result_eur") or 0
    entry_date  = (trade.get("datetime") or "")[:10]
    exit_date   = (trade.get("closed_at") or "")[:10]
    postmortem  = (trade.get("postmortem") or "").strip()

    return (
        "Analisa este trade de acções concluído com ganho e devolve insights em PORTUGUÊS de Portugal.\n\n"
        f"Ticker: {ticker}\nNome: {display_name}\n"
        f"Data de entrada: {entry_date}  |  Preço de entrada: {entry_price:.2f}\n"
        f"Data de saída:   {exit_date}\n"
        f"Resultado: +{result_pct:.2f}%  (+{result_eur:.2f}€)\n"
        + (f"Nota: {postmortem}\n" if postmortem else "")
        + "\nDevolve um objecto JSON com exactamente estas três chaves "
        "(cada valor: string curta, máximo 2 frases, sem markdown, sem listas, sem emojis):\n"
        '{"what_went_well": "...", "what_could_improve": "...", "pattern": "..."}\n\n'
        "Definições:\n"
        '- "what_went_well": o que correu bem neste trade (sinal, timing, gestão de risco).\n'
        '- "what_could_improve": o que podia ter sido melhor ou um risco não identificado.\n'
        '- "pattern": padrão técnico ou comportamental identificado neste trade.\n\n'
        "Regras obrigatórias:\n"
        "- Sê neutro, factual e prudente. Não faças recomendações de compra/venda.\n"
        "- Se não tens informação suficiente, usa 'Informação limitada.' nesse campo.\n"
        "- Responde APENAS com o objecto JSON — sem texto antes, sem texto depois.\n"
    )


def _make_base(trade: dict, display_name: str) -> dict:
    ticker_raw = trade.get("ticker", "")
    closed_raw = trade.get("closed_at") or ""
    entry_raw  = trade.get("datetime") or ""
    return {
        "trade_id":     trade["id"],
        "ticker":       _clean_ticker(ticker_raw),
        "display_name": display_name,
        "entry_date":   entry_raw[:19] + "Z" if entry_raw else "",
        "exit_date":    closed_raw[:19] + "Z" if closed_raw else "",
        "entry_price":  trade.get("price") or 0,
        "gain_pct":     round(trade.get("result_pct") or 0, 2),
        "gain_eur":     round(trade.get("result_eur") or 0, 2),
        "generated_at": _ts(),
        "model":        GEMINI_MODEL,
    }


def generate_for_closed_trades(
    trades: list[dict],
    gemini_client,
    symbol_cache: dict | None = None,
) -> None:
    """Generate Gemini insights for newly closed positive trades (max MAX_PER_RUN per call).

    trades:         list of trade dicts from beta_trades.json
    gemini_client:  initialised google.genai.Client, or None (no-op)
    symbol_cache:   optional ticker→meta mapping for display names
    """
    if not gemini_client:
        print("   [gains_insights] gemini_client não disponível — skip", flush=True)
        return

    rl_available = False
    try:
        from . import rate_limiter as _rl
        rl_available = True
    except Exception:
        pass

    now = datetime.now(timezone.utc)

    store = _load()
    store.setdefault("insights", {})

    # Prune entries past their 63-day window
    now_iso = now.isoformat().replace("+00:00", "Z")
    expired = [
        tid for tid, e in store["insights"].items()
        if (e.get("expires_at") or "") < now_iso
    ]
    for tid in expired:
        del store["insights"][tid]
    if expired:
        print(f"   [gains_insights] {len(expired)} entradas expiradas removidas", flush=True)

    # Candidates: closed, positive, within retention window, no ok insight yet
    candidates = []
    for t in trades:
        if not (
            t.get("closed_at")
            and t.get("result_eur") is not None
            and t.get("result_eur", 0) > 0
            and t.get("id")
        ):
            continue
        if store["insights"].get(t["id"], {}).get("status") == "ok":
            continue
        try:
            closed_dt = datetime.fromisoformat(t["closed_at"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if closed_dt + timedelta(days=RETENTION_DAYS) > now:
            candidates.append(t)

    if not candidates:
        print("   [gains_insights] sem novos candidatos positivos — skip", flush=True)
        return

    print(f"   [gains_insights] {len(candidates)} candidato(s) encontrado(s)", flush=True)

    sc = symbol_cache or {}
    n_ok = n_fail = n_limited = 0

    for trade in candidates[:MAX_PER_RUN]:
        trade_id = trade["id"]
        dname    = _display_name_from_cache(trade.get("ticker", ""), sc)
        base     = _make_base(trade, dname)

        # Rate-limit gate
        if rl_available:
            try:
                if not _rl.check_and_consume("gemini_gains"):
                    print(
                        f"   [gains_insights] {base['ticker']}: rate limit atingido — skip",
                        flush=True,
                    )
                    store["insights"][trade_id] = {**base, "status": "rate_limited"}
                    n_limited += 1
                    continue
            except Exception:
                pass

        # Gemini call
        raw_text = ""
        try:
            from google.genai import types as _gt
            resp = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=_build_prompt(trade, dname),
                config=_gt.GenerateContentConfig(
                    http_options=_gt.HttpOptions(timeout=20_000),
                    response_mime_type="application/json",
                    temperature=0.4,
                    max_output_tokens=600,
                ),
            )
            raw_text = (resp.text or "").strip()
            parsed = json.loads(_strip_fences(raw_text))
            if not isinstance(parsed, dict):
                raise ValueError("resposta não é dict")

            closed_dt = datetime.fromisoformat(trade["closed_at"].replace("Z", "+00:00"))
            expires_at = (closed_dt + timedelta(days=RETENTION_DAYS)).isoformat().replace("+00:00", "Z")

            store["insights"][trade_id] = {
                **base,
                "expires_at": expires_at,
                "status": "ok",
                "gemini_insight": {
                    "what_went_well":     str(parsed.get("what_went_well", "")).strip()[:400],
                    "what_could_improve": str(parsed.get("what_could_improve", "")).strip()[:400],
                    "pattern":            str(parsed.get("pattern", "")).strip()[:400],
                },
            }
            n_ok += 1
            print(f"   [gains_insights] {base['ticker']} ({trade_id}): insight gerado", flush=True)

        except Exception as exc:
            preview = raw_text[:200].replace("\n", "\\n") if raw_text else "<vazio>"
            print(
                f"   [gains_insights] {base['ticker']}: falha — {exc} | raw: {preview}",
                flush=True,
            )
            store["insights"][trade_id] = {**base, "status": "failed"}
            n_fail += 1

        # Persist after every entry (crash-safe)
        try:
            store["generated_at"] = _ts()
            _save(store)
        except Exception as exc:
            print(f"   [gains_insights] aviso: falha ao gravar: {exc}", flush=True)

    print(f"   [gains_insights] ok={n_ok} limitados={n_limited} fail={n_fail}", flush=True)
