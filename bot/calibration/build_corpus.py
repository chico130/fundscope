"""
build_corpus.py — Gerador offline de casos de estudo sintéticos para o Learner.

Problema: ~158 trades reais em 4 anos é pouco para o Coordinate Descent do
learner.py convergir sem overfitting. Solução: gerar "trades simulados" a partir
de 2 anos de OHLCV dos 500 tickers do S&P 500, aplicando as MESMAS regras de
entrada do Clyde sobre janelas deslizantes. Cada entrada vira um sample de treino.

Não reinventa o download nem os indicadores: assenta sobre a infra de calibração
já existente (cache → indicators → candidates), que garante PARIDADE com produção
e invariante de ZERO look-ahead (outcomes usam shift(-k), k≥1).

Saída: data/learner_corpus/corpus.jsonl  (um sample por linha)
       data/learner_corpus/manifest.json (metadados, contagens, params do sinal)
NUNCA escreve em data/beta/ (dados de produção).

Formato de cada linha — desenhado para ser consumido tal-e-qual pelas funções de
fitness do learner (_would_clyde_enter / _profit_factor_calmar):

    {
      "id": "AAPL_2024-03-12",
      "ticker": "AAPL",
      "datetime": "2024-03-12T00:00:00Z",
      "side": "BUY",
      "style": "VALUE",
      "signal_strength": 0.62,
      "context": {
        "rsi_14": 31.4,
        "volume_ratio_vs_avg": 1.43,     # ← vol_ratio renomeado p/ chave do learner
        "ema50_above_ema200": true,      # ← ema50_above_200 renomeado
        "ema50_dist_pct": -1.8,
        "regime": "bull_trending"
      },
      "labels": {"t5": 2.1, "t10": 3.4, "t20": -1.2},
      "result_eur": 3.4,                 # = labels[primary_horizon] * NOTIONAL/100
      "synthetic": true
    }

Uso:
    python -m bot.calibration.build_corpus                 # 2 anos, regras dos apontamentos
    python -m bot.calibration.build_corpus --years 3
    python -m bot.calibration.build_corpus --from-active-params   # usa thresholds vivos
    python -m bot.calibration.build_corpus --limit 50      # smoke test rápido
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from bot.config import BASE_DIR
from bot.calibration.cache import ensure_ohlcv_cache
from bot.calibration.candidates import build_candidate_table
from bot.calibration.universe import get_sp500_tickers

# ---------------------------------------------------------------------------
# tqdm com fallback (não está instalado neste ambiente)
# ---------------------------------------------------------------------------
try:
    from tqdm import tqdm as _tqdm

    def _progress(it: Iterable, total: int, desc: str):
        return _tqdm(it, total=total, desc=desc, unit="row")
except ImportError:
    def _progress(it: Iterable, total: int, desc: str):
        step = max(1, total // 20) if total else 1000
        for i, x in enumerate(it, 1):
            if i % step == 0 or i == total:
                pct = (i / total * 100) if total else 0
                print(f"  [{desc}] {i:,}/{total:,} ({pct:4.1f}%)", flush=True)
            yield x


CORPUS_DIR   = BASE_DIR / "data" / "learner_corpus"
CORPUS_PATH  = CORPUS_DIR / "corpus.jsonl"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"

HORIZONS = [5, 10, 20]
PRIMARY_HORIZON = 10          # horizonte que define result_eur
NOTIONAL_EUR = 100.0          # nocional fixo por trade simulado: result_eur ≈ retorno %

# Regra de entrada do Clyde (defaults = apontamentos).
# Nota: os apontamentos pedem RSI≤34 e ema50_dist≥−3%; o default vivo do Clyde é
# rsi_oversold_ceiling=35 e gate ema50_above_ema200. Mantemos os valores dos
# apontamentos como default e expomos --from-active-params para alinhar com produção.
DEFAULT_SIGNAL = {
    "rsi_ceiling":      35.0,   # espelho de rsi_oversold_ceiling (learner._DEFAULT_PARAMS)
    "vol_min":          1.2,    # espelho de vol_ratio_oversold_min
    "require_ema_up":   True,   # espelho do gate ema50_above_ema200 em _would_clyde_enter
    "ema50_dist_min":  -100.0,  # desactivado — é FEATURE no contexto, não gate de produção
}


# ---------------------------------------------------------------------------
# Sinal e força do sinal
# ---------------------------------------------------------------------------

def _passes_signal(row: dict, sig: dict) -> bool:
    """Replica a regra de entrada VALUE do Clyde sobre uma linha de candidatos."""
    rsi  = row.get("rsi_14")
    vol  = row.get("vol_ratio")
    dist = row.get("ema50_dist_pct")
    if rsi is None or vol is None or dist is None:
        return False
    if not (rsi <= sig["rsi_ceiling"] and vol >= sig["vol_min"] and dist >= sig["ema50_dist_min"]):
        return False
    if sig["require_ema_up"] and not bool(row.get("ema50_above_200")):
        return False
    return True


def _signal_strength(row: dict, sig: dict) -> float:
    """Proxy heurístico em [0,1] — quão "fundo" e confirmado está o setup.

    Combina profundidade do RSI abaixo do tecto com excesso de volume. Apenas um
    ordenador monotónico para que o corpus seja utilizável também no fitness da
    Bonnie (que filtra por signal_strength ≥ threshold). NÃO é uma probabilidade.
    """
    rsi = row.get("rsi_14", sig["rsi_ceiling"])
    vol = row.get("vol_ratio", sig["vol_min"])
    rsi_depth = max(0.0, (sig["rsi_ceiling"] - rsi) / 20.0)   # ~0..1 (20 pts de folga)
    vol_excess = min(1.0, max(0.0, (vol - sig["vol_min"]) / 1.0))
    score = 0.55 + 0.30 * rsi_depth + 0.15 * vol_excess
    return round(min(0.95, score), 4)


# ---------------------------------------------------------------------------
# Conversão de linha → sample JSONL
# ---------------------------------------------------------------------------

def _row_to_sample(row: dict, sig: dict) -> dict[str, Any] | None:
    """Converte uma linha-candidato aprovada num sample no formato do learner."""
    labels = {}
    for h in HORIZONS:
        val = row.get(f"out_{h}_final_pct")
        if val is None or (isinstance(val, float) and val != val):  # NaN → sem futuro
            return None
        labels[f"t{h}"] = round(float(val), 4)

    primary = labels.get(f"t{PRIMARY_HORIZON}")
    if primary is None:
        return None

    ts = row["date"]
    ts_iso = (ts.strftime("%Y-%m-%dT00:00:00Z")
              if hasattr(ts, "strftime") else str(ts)[:10] + "T00:00:00Z")

    ema_up = row.get("ema50_above_200")
    return {
        "id":              f"{row['ticker']}_{str(ts)[:10]}",
        "ticker":          row["ticker"],
        "datetime":        ts_iso,
        "side":            "BUY",
        "style":           "VALUE",
        "signal_strength": _signal_strength(row, sig),
        "context": {
            "rsi_14":               round(float(row["rsi_14"]), 2),
            "volume_ratio_vs_avg":  round(float(row["vol_ratio"]), 4),
            "ema50_above_ema200":   bool(ema_up) if ema_up is not None else None,
            "ema50_dist_pct":       round(float(row["ema50_dist_pct"]), 4),
            "regime":               row.get("regime", "unknown"),
        },
        "labels":     labels,
        "result_eur": round(primary * NOTIONAL_EUR / 100.0, 4),
        "synthetic":  True,
    }


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def build_corpus(
    years:             int = 2,
    limit:             int | None = None,
    from_active_params: bool = False,
    refresh:           bool = False,
) -> dict:
    """Gera o corpus e devolve o manifest. Não toca em data/beta/."""
    sig = dict(DEFAULT_SIGNAL)
    if from_active_params:
        # Alinha o sinal com os thresholds vivos do Clyde (sem importar a NN inexistente)
        from bot.learner import get_active_params
        clyde = get_active_params()["weekly"]["clyde"]
        sig["rsi_ceiling"] = float(clyde.get("rsi_oversold_ceiling", sig["rsi_ceiling"]))
        sig["vol_min"]     = float(clyde.get("vol_ratio_oversold_min", sig["vol_min"]))
        sig["require_ema_up"] = True   # produção usa o gate ema50_above_ema200

    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=int(years * 365))).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")

    tickers = get_sp500_tickers()
    if limit:
        tickers = tickers[:limit]
    print(f"[corpus] {len(tickers)} tickers · janela {start} → {end} · sinal={sig}")

    # 1) Garantir OHLCV em cache (reusa a infra; idempotente)
    ensure_ohlcv_cache(tickers, start, end, refresh=refresh)

    # 2) Tabela mestra com features + outcomes (zero look-ahead garantido a montante)
    table = build_candidate_table(tickers, start, end, horizons=HORIZONS, force=refresh)
    print(f"[corpus] tabela de candidatos: {len(table):,} linhas (ticker,dia)")

    # 3) Aplicar a regra do Clyde e emitir JSONL
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CORPUS_PATH.with_suffix(".tmp")

    n_emitted = 0
    n_signal  = 0
    pnl_sum   = 0.0
    by_regime: dict[str, int] = {}

    records = table.to_dict("records")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in _progress(records, total=len(records), desc="scan"):
            if not _passes_signal(row, sig):
                continue
            n_signal += 1
            sample = _row_to_sample(row, sig)
            if sample is None:
                continue  # sem futuro suficiente (últimas H barras)
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
            n_emitted += 1
            pnl_sum += sample["result_eur"]
            reg = sample["context"]["regime"]
            by_regime[reg] = by_regime.get(reg, 0) + 1

    tmp.replace(CORPUS_PATH)

    win_rate = round(_quick_win_rate(CORPUS_PATH) * 100, 1) if n_emitted else 0.0

    manifest = {
        "generated_at":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window":          {"start": start, "end": end, "years": years},
        "n_tickers":       len(tickers),
        "candidate_rows":  len(table),
        "signal_hits":     n_signal,
        "samples_emitted": n_emitted,
        "primary_horizon": PRIMARY_HORIZON,
        "notional_eur":    NOTIONAL_EUR,
        "signal_params":   sig,
        "avg_result_eur":  round(pnl_sum / n_emitted, 4) if n_emitted else 0.0,
        "win_rate_pct":    win_rate,
        "by_regime":       by_regime,
        "corpus_path":     str(CORPUS_PATH.relative_to(BASE_DIR)),
        "warnings": [
            "Viés de sobrevivência: universo = constituintes ACTUAIS do S&P 500.",
            "Janelas deslizantes → samples autocorrelacionados; split treino/val DEVE ser por tempo.",
            "result_eur é um proxy de %×nocional, não P&L de execução real.",
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[corpus] {n_emitted:,} samples ({n_signal:,} hits de sinal) "
          f"→ {CORPUS_PATH.relative_to(BASE_DIR)}")
    print(f"[corpus] win-rate (t+{PRIMARY_HORIZON}): {win_rate}%  ·  "
          f"avg result: {manifest['avg_result_eur']}€  ·  regimes: {by_regime}")
    if win_rate and win_rate > 65:
        print(f"[corpus] ⚠ win-rate {win_rate}% suspeitamente alto — verifica look-ahead/viés.")
    return manifest


def _quick_win_rate(path: Path) -> float:
    """Fracção de samples com result_eur ≥ 0 (segunda passagem leve sobre o ficheiro)."""
    wins = total = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            if json.loads(line).get("result_eur", 0) >= 0:
                wins += 1
    return wins / total if total else 0.0


def main() -> None:
    p = argparse.ArgumentParser(description="Gera corpus sintético para o Learner.")
    p.add_argument("--years", type=int, default=2, help="Anos de histórico (default 2).")
    p.add_argument("--limit", type=int, default=None, help="Limitar nº de tickers (smoke test).")
    p.add_argument("--from-active-params", action="store_true",
                   help="Usa os thresholds vivos do Clyde em vez dos apontamentos.")
    p.add_argument("--refresh", action="store_true", help="Força re-download e recálculo.")
    args = p.parse_args()
    build_corpus(years=args.years, limit=args.limit,
                 from_active_params=args.from_active_params, refresh=args.refresh)


if __name__ == "__main__":
    main()
