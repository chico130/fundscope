"""
validate_social_pipeline.py — Valida o output do Social Crawler.

Verifica que data/beta/social_sentiment.json existe, tem o schema esperado,
não está obsoleto (TTL) e que os scores estão dentro dos limites sãos.
Não faz chamadas de rede — só inspeciona o ficheiro já escrito.

Saída: lista de problemas. Exit code 1 se houver falhas críticas.

Uso:
  python scripts/validate_social_pipeline.py
  python scripts/validate_social_pipeline.py --max-age-min 480   # tolerância maior
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SENTIMENT_PATH = REPO_ROOT / "data" / "beta" / "social_sentiment.json"

EXPECTED_KEYS = {"schema_version", "generated_at_utc", "ttl_minutes", "tickers", "anomalies"}


def validate(max_age_min: float | None) -> list[str]:
    """Devolve a lista de problemas encontrados (vazia = tudo OK)."""
    problems: list[str] = []

    if not SENTIMENT_PATH.exists():
        return [f"ficheiro ausente: {SENTIMENT_PATH}"]

    try:
        data = json.loads(SENTIMENT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"JSON ilegível: {exc}"]

    # Schema de topo
    missing = EXPECTED_KEYS - data.keys()
    if missing:
        problems.append(f"chaves em falta no topo: {sorted(missing)}")

    # Idade (usa o ttl do próprio ficheiro, salvo override)
    ttl = max_age_min if max_age_min is not None else data.get("ttl_minutes", 240)
    age_min = (time.time() - SENTIMENT_PATH.stat().st_mtime) / 60
    if age_min > ttl:
        problems.append(f"obsoleto: {age_min:.0f} min > ttl {ttl} min")

    # Sanidade dos tickers
    tickers = data.get("tickers", {})
    if not isinstance(tickers, dict) or not tickers:
        problems.append("secção 'tickers' vazia ou inválida")
        return problems

    valid_vetos = {None, "social_panic", "analyst_divergence"}
    for tk, entry in tickers.items():
        if not isinstance(entry, dict):
            problems.append(f"{tk}: entrada não é objeto")
            continue
        if entry.get("veto") not in valid_vetos:
            problems.append(f"{tk}: veto desconhecido {entry.get('veto')!r}")

        a = entry.get("analyst")
        if a is not None:
            br = a.get("bull_ratio")
            if br is None or not (-1.0 <= br <= 1.0):
                problems.append(f"{tk}: bull_ratio fora de [-1,1]: {br}")

        r = entry.get("reddit")
        if r is not None:
            mean = r.get("mean")
            if mean is None or not (-1.0 <= mean <= 1.0):
                problems.append(f"{tk}: reddit.mean fora de [-1,1]: {mean}")

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida o output do Social Crawler")
    parser.add_argument("--max-age-min", type=float, default=None,
                        help="override do TTL em minutos (default: ttl_minutes do ficheiro)")
    args = parser.parse_args()

    problems = validate(args.max_age_min)
    if problems:
        print(f"❌ {len(problems)} problema(s) em social_sentiment.json:")
        for p in problems:
            print(f"  · {p}")
        sys.exit(1)

    print("✅ social_sentiment.json válido (schema, idade e scores OK)")


if __name__ == "__main__":
    main()
