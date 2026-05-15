"""
Bonnie — Fase 0 (Observação Passiva)

Executa independentemente do Clyde, idealmente 1x/hora via Task Scheduler:
    python -m bot.bonnie

Responsabilidades desta fase:
  1. Garantir que config_risco.json existe com valores conservadores.
  2. Ler diario_trades.json e calcular estatísticas (win rate por dia/semana/sector).
  3. Comparar timestamps de entrada com notícias em news.json → alertas passivos.
  4. Escrever logs/bonnie_log.json em formato consumível pelo site via fetch.

Não bloqueia ordens nesta fase — apenas observa e regista.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import (
    DIARIO_TRADES_PATH,
    CONFIG_RISCO_PATH,
    NEWS_PATH,
    BONNIE_LOG_PATH,
    DATA_BETA_DIR,
    LOGS_DIR,
)
from .logger import log_error

_DEFAULT_CONFIG_RISCO: dict = {
    "permite_comprar": True,
    "tamanho_maximo_posicao": 1.0,
    "motivo_bloqueio": "",
    "estado_emocional": "neutro",
}

# Janela máxima para correlacionar notícia com trade (segundos)
_NEWS_WINDOW_SECONDS = 1800  # 30 minutos


# ---------------------------------------------------------------------------
# 1. Config risco — garante existência com defaults conservadores
# ---------------------------------------------------------------------------

def ensure_config_risco() -> dict:
    """Cria config_risco.json com valores por defeito se não existir.

    Nunca sobrescreve um ficheiro já existente — apenas preenche campos em falta.
    """
    existing: dict = {}
    if CONFIG_RISCO_PATH.exists():
        try:
            with open(CONFIG_RISCO_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    merged = {**_DEFAULT_CONFIG_RISCO, **existing}

    try:
        with open(CONFIG_RISCO_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        log_error("bonnie_config_risco_write", {"error": str(exc)})

    return merged


# ---------------------------------------------------------------------------
# 2. Leitura do diário
# ---------------------------------------------------------------------------

def read_diario_trades() -> list[dict]:
    """Lê diario_trades.json da raiz. Devolve lista vazia se ausente ou corrompido."""
    if not DIARIO_TRADES_PATH.exists():
        return []
    try:
        with open(DIARIO_TRADES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        log_error("bonnie_read_diario", {"error": str(exc)})
        return []


def _load_watchlist_sectors() -> dict[str, str]:
    """Devolve {ticker: sector} a partir de data/beta/watchlist.json."""
    path = DATA_BETA_DIR / "watchlist.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        candidates = data if isinstance(data, list) else data.get("candidates", [])
        return {
            item.get("ticker", ""): item.get("sector", "desconhecido")
            for item in candidates
            if item.get("ticker")
        }
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# 3. Cálculo de estatísticas
# ---------------------------------------------------------------------------

def calc_stats(trades: list[dict]) -> dict:
    """Calcula win rate por dia, semana e sector a partir do diário.

    Só conta trades fechados (resultado_eur não nulo).
    """
    closed = [
        t for t in trades
        if t.get("tipo") in ("entrada", "saida")
        and t.get("resultado_eur") is not None
    ]

    sectors = _load_watchlist_sectors()

    by_day: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})
    by_week: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})
    by_sector: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})

    for t in closed:
        ts_str = t.get("timestamp", "")
        resultado = t.get("resultado_eur", 0) or 0
        ativo = t.get("ativo", "")

        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            day_key = dt.strftime("%Y-%m-%d")
            week_key = dt.strftime("%Y-W%W")
        except (ValueError, AttributeError):
            day_key = "desconhecido"
            week_key = "desconhecido"

        sector = sectors.get(ativo, sectors.get(ativo.split("_")[0], "desconhecido"))

        bucket = "wins" if resultado > 0 else "losses"
        by_day[day_key][bucket] += 1
        by_week[week_key][bucket] += 1
        by_sector[sector][bucket] += 1

    def _rate(d: dict) -> dict:
        total = d["wins"] + d["losses"]
        return {
            "wins": d["wins"],
            "losses": d["losses"],
            "total": total,
            "win_rate_pct": round(d["wins"] / total * 100, 1) if total else None,
        }

    return {
        "total_closed": len(closed),
        "por_dia": {k: _rate(v) for k, v in sorted(by_day.items())},
        "por_semana": {k: _rate(v) for k, v in sorted(by_week.items())},
        "por_sector": {k: _rate(v) for k, v in sorted(by_sector.items())},
    }


# ---------------------------------------------------------------------------
# 4. Alertas passivos baseados em notícias
# ---------------------------------------------------------------------------

def _load_news() -> list[dict]:
    if not NEWS_PATH.exists():
        return []
    try:
        with open(NEWS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("articles", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def _ticker_base(ativo: str) -> str:
    """Extrai o símbolo base de um ticker T212 (ex: AAPL_US_EQ → AAPL)."""
    return ativo.split("_")[0] if ativo else ativo


def generate_news_alerts(trades: list[dict], news: list[dict]) -> list[dict]:
    """Gera alertas passivos comparando entradas com notícias próximas.

    Para cada trade de entrada, procura notícias publicadas até 30 minutos antes.
    Gera alerta quando detecta sentimento negativo antes de uma compra.
    """
    alerts: list[dict] = []
    entradas = [t for t in trades if t.get("tipo") == "entrada" and t.get("lado") == "BUY"]

    for trade in entradas:
        ts_str = trade.get("timestamp", "")
        try:
            trade_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        ticker_base = _ticker_base(trade.get("ativo", ""))
        resultado_eur = trade.get("resultado_eur")

        for article in news:
            pub_str = article.get("publishedAt", "")
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            delta_s = (trade_dt - pub_dt).total_seconds()
            if not (0 <= delta_s <= _NEWS_WINDOW_SECONDS):
                continue

            impact = article.get("impact", {})
            sentiment = impact.get("sentiment", "neutral")
            tickers_in_news = [str(t).upper() for t in impact.get("tickers", [])]

            ticker_match = (
                ticker_base.upper() in tickers_in_news
                or not tickers_in_news  # notícia genérica de mercado
            )

            if sentiment == "negative" and ticker_match:
                delta_min = int(delta_s // 60)
                if resultado_eur is not None and resultado_eur < 0:
                    msg = (
                        f"Compraste {ticker_base} às {trade_dt.strftime('%H:%M')} "
                        f"e perdeste {abs(resultado_eur):.2f}€ — "
                        f"notícia negativa detetada {delta_min}min antes: \"{article.get('title', '')[:80]}\""
                    )
                    severity = "warning"
                elif resultado_eur is not None and resultado_eur > 0:
                    msg = (
                        f"Compraste {ticker_base} às {trade_dt.strftime('%H:%M')} "
                        f"apesar de notícia negativa {delta_min}min antes e ganhaste {resultado_eur:.2f}€ — "
                        f"atenção ao risco assumido."
                    )
                    severity = "info"
                else:
                    msg = (
                        f"Compraste {ticker_base} às {trade_dt.strftime('%H:%M')} — "
                        f"notícia negativa detetada {delta_min}min antes: \"{article.get('title', '')[:80]}\""
                    )
                    severity = "info"

                alerts.append({
                    "timestamp_trade": ts_str,
                    "ativo": trade.get("ativo"),
                    "ticker_base": ticker_base,
                    "delta_min": delta_min,
                    "sentimento_noticia": sentiment,
                    "titulo_noticia": article.get("title", ""),
                    "resultado_eur": resultado_eur,
                    "severity": severity,
                    "mensagem": msg,
                })

    return alerts


# ---------------------------------------------------------------------------
# 5. Escrita do log Bonnie
# ---------------------------------------------------------------------------

def write_bonnie_log(stats: dict, alerts: list[dict], config_risco: dict) -> None:
    """Escreve logs/bonnie_log.json em formato consumível pelo site via fetch."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fase": "fase0_observacao",
        "config_risco_atual": config_risco,
        "estatisticas": stats,
        "alertas_noticias": alerts,
        "total_alertas": len(alerts),
    }

    tmp = BONNIE_LOG_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(BONNIE_LOG_PATH)
        print(f"[Bonnie] Log escrito: {BONNIE_LOG_PATH}")
    except OSError as exc:
        log_error("bonnie_log_write", {"error": str(exc)})


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[Bonnie] Fase-0 — {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")

    config_risco = ensure_config_risco()
    print(f"[Bonnie] config_risco.json: permite_comprar={config_risco['permite_comprar']}, "
          f"fator={config_risco['tamanho_maximo_posicao']}")

    trades = read_diario_trades()
    print(f"[Bonnie] {len(trades)} entradas no diário")

    stats = calc_stats(trades)
    print(f"[Bonnie] Trades fechados: {stats['total_closed']}")

    news = _load_news()
    alerts = generate_news_alerts(trades, news)
    print(f"[Bonnie] {len(alerts)} alertas de notícias gerados")

    write_bonnie_log(stats, alerts, config_risco)


if __name__ == "__main__":
    main()
