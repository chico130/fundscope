"""
Bonnie — Fase 0 (Observação Passiva)

Corre em loop contínuo — audita a cada hora, sem precisar de ser relançada:
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
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

from .config import (
    DIARIO_TRADES_PATH,
    CONFIG_RISCO_PATH,
    NEWS_PATH,
    EARNINGS_PATH,
    PORTFOLIO_PATH,
    BONNIE_LOG_PATH,
    DATA_BETA_DIR,
    LOGS_DIR,
    RISK_CONFIG,
)
from .logger import log_decision, log_error

_DEFAULT_CONFIG_RISCO: dict = {
    "permite_comprar": True,
    "tamanho_maximo_posicao": 1.0,
    "motivo_bloqueio": "",
    "estado_emocional": "neutro",
    "vix_kill_switch_threshold": 35.0,
    "vix_total_kill_threshold":  45.0,
    "vix_caution_threshold":     20.0,
    "cash_is_king_multiplier":   0.25,
    "mean_reversion_rsi_max":    35.0,
    "mean_reversion_sma_period": 50,
    "mean_reversion_max_vix":    20.0,
    "mean_reversion_allow_bear_correction": True,
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


def calc_estado_emocional(stats: dict, config_risco: dict) -> str:
    """Deriva o estado_emocional com base no win rate dos últimos 7 dias."""
    if not config_risco.get("permite_comprar", True):
        return "defensivo"
    por_dia = stats.get("por_dia", {})
    recent = sorted(por_dia.keys())[-7:]
    wins  = sum(por_dia[d]["wins"]  for d in recent if d in por_dia)
    total = sum(por_dia[d]["total"] for d in recent if d in por_dia)
    if total < 3:
        return "neutro"
    wr = wins / total
    if wr >= 0.65:
        return "confiante"
    if wr >= 0.50:
        return "neutro"
    if wr >= 0.35:
        return "cauteloso"
    return "defensivo"


def get_vetos(trades: list[dict]) -> list[dict]:
    """Devolve os últimos 20 trades bloqueados pela Bonnie."""
    return [t for t in trades if t.get("tipo") == "bloqueado"][-20:]


def build_evolucao(stats: dict) -> list[dict]:
    """Série temporal de win rate por semana para gráfico de evolução."""
    por_semana = stats.get("por_semana", {})
    series = [
        {
            "semana": k,
            "win_rate_pct": v["win_rate_pct"],
            "total": v["total"],
            "wins": v["wins"],
        }
        for k, v in sorted(por_semana.items())
        if v["total"] >= 1
    ]
    # fallback para diário quando há menos de 2 semanas
    if len(series) < 2:
        por_dia = stats.get("por_dia", {})
        series = [
            {
                "semana": k,
                "win_rate_pct": v["win_rate_pct"],
                "total": v["total"],
                "wins": v["wins"],
            }
            for k, v in sorted(por_dia.items())
            if v["total"] >= 1
        ]
    return series


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
# 5. Regra Bonnie/Clyde — earnings_risk
# ---------------------------------------------------------------------------

def _load_portfolio_tickers() -> set[str]:
    """Devolve conjunto de tickers com posição aberta no portfólio."""
    if not PORTFOLIO_PATH.exists():
        return set()
    try:
        with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        tickers: set[str] = set()
        for pos in data.get("positions", []):
            for key in ("ticker", "ticker_display"):
                val = pos.get(key, "")
                if val:
                    tickers.add(val.upper())
        return tickers
    except (json.JSONDecodeError, OSError):
        return set()


def _load_earnings() -> list[dict]:
    """Lê earnings.json. Devolve lista vazia se ausente."""
    if not EARNINGS_PATH.exists():
        return []
    try:
        with open(EARNINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("earnings", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def generate_earnings_alerts(portfolio_tickers: set[str], earnings: list[dict]) -> list[dict]:
    """Gera alertas earnings_risk para posições abertas com earnings em menos de N dias."""
    threshold_days = RISK_CONFIG.get("no_trade_before_earnings_days", 2)
    today = datetime.now(timezone.utc).date()
    alerts: list[dict] = []

    for entry in earnings:
        ticker = (entry.get("ticker") or "").upper()
        if ticker not in portfolio_tickers:
            continue
        date_str = entry.get("data", "")
        try:
            ed = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_away = (ed - today).days
        if 0 <= days_away < threshold_days:
            alerts.append({
                "tipo": "earnings_risk",
                "ticker": ticker,
                "earnings_date": date_str,
                "days_away": days_away,
                "hora": entry.get("hora", "N/D"),
                "eps_estimado": entry.get("eps_estimado"),
                "severity": "warning",
                "mensagem": (
                    f"{ticker} tem earnings em {days_away} dia(s) ({date_str} {entry.get('hora','')}) "
                    f"e o Clyde tem posição aberta. Risco de volatilidade elevada."
                ),
            })

    return alerts


# ---------------------------------------------------------------------------
# 6. Escrita do log Bonnie
# ---------------------------------------------------------------------------

def write_bonnie_log(
    stats: dict,
    alerts: list[dict],
    config_risco: dict,
    earnings_alerts: list[dict] | None = None,
    vetos: list[dict] | None = None,
    evolucao: list[dict] | None = None,
) -> None:
    """Escreve logs/bonnie_log.json em formato consumível pelo site via fetch."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    earnings_alerts = earnings_alerts or []

    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fase": "fase0_observacao",
        "config_risco_atual": config_risco,
        "estatisticas": stats,
        "vetos": vetos or [],
        "evolucao_win_rate": evolucao or [],
        "alertas_noticias": alerts,
        "alertas_earnings": earnings_alerts,
        "total_alertas": len(alerts) + len(earnings_alerts),
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
# 7. Classe Bonnie — modo estático + modo observação ML
# ---------------------------------------------------------------------------

# Ordem das features DEVE coincidir com o treino de bonnie_champion.pkl
_ML_FEATURE_COLS = [
    "rsi_14", "volume_ratio", "atr_pct", "price_vs_ema20",
    "price_vs_ema50", "price_vs_ema200", "momentum_1m", "momentum_3m",
]


class Bonnie:
    """Gestão do modo ML da Bonnie.

    Modos (config_risco.json → "bonnie_ml_mode"):
      "static"  — apenas regras estáticas; sem inferência ML (padrão seguro)
      "observe" — corre predict_proba e loga, mas não veta
      "active"  — ML também pode vetar se proba < threshold (fase futura)
    """

    def __init__(self) -> None:
        self._mode: str = "static"
        self._model = None
        self._threshold: float = 0.30
        self._loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            cfg = json.loads(CONFIG_RISCO_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        self._mode      = cfg.get("bonnie_ml_mode", "static")
        self._threshold = cfg.get("bonnie_ml_threshold", 0.30)

        if self._mode not in ("observe", "active"):
            return

        try:
            from .config import BASE_DIR
            champion = BASE_DIR / "models" / "bonnie_champion.pkl"
            if champion.exists():
                import joblib
                self._model = joblib.load(champion)
                print(f"[BONNIE-ML] Modelo carregado: {champion.name}", flush=True)
            else:
                # champion.pkl ausente — fallback silencioso para static
                self._mode = "static"
        except Exception as exc:
            print(f"[BONNIE-ML] Falha ao carregar modelo: {exc}", flush=True)
            self._mode = "static"

    def observe(self, ticker: str, features: dict, static_approved: bool) -> None:
        """Corre predict_proba e loga o resultado sem bloquear."""
        self._ensure_loaded()
        if self._mode not in ("observe", "active") or self._model is None:
            return
        try:
            import numpy as np
            row = [float(features.get(f, 0.0)) for f in _ML_FEATURE_COLS]
            proba = float(self._model.predict_proba([row])[0][1])
            verdict = "approved" if static_approved else "vetoed"
            print(
                f"[BONNIE-ML] ticker={ticker} proba={proba:.3f} "
                f"static_verdict={verdict} threshold={self._threshold:.2f}",
                flush=True,
            )
        except Exception as exc:
            print(f"[BONNIE-ML] Erro em observe({ticker}): {exc}", flush=True)


# Singleton — instanciado uma vez por processo
_bonnie = Bonnie()


# ---------------------------------------------------------------------------
# 8. Filtro activo de propostas em tempo real
# ---------------------------------------------------------------------------

def _earnings_window_map() -> dict[str, tuple[int, str]]:
    """Devolve {TICKER_BASE: (days_away, date_str)} para todas as empresas
    com earnings dentro da janela `no_trade_before_earnings_days` (inclusive).

    Lê earnings.json a cada chamada — leve, e mantém a regra alinhada com o ficheiro
    publicado pela tarefa diária `update_earnings.py`.
    """
    threshold_days = RISK_CONFIG.get("no_trade_before_earnings_days", 2)
    today = datetime.now(timezone.utc).date()
    out: dict[str, tuple[int, str]] = {}
    for entry in _load_earnings():
        ticker = (entry.get("ticker") or "").split("_")[0].upper()
        if not ticker:
            continue
        date_str = entry.get("data", "")
        try:
            ed = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_away = (ed - today).days
        if 0 <= days_away <= threshold_days:
            # Mantém a entrada mais próxima caso o ticker apareça duplicado
            if ticker not in out or days_away < out[ticker][0]:
                out[ticker] = (days_away, date_str)
    return out


def filter_proposals(
    proposals:     list,
    market_data:   dict[str, dict],
    bonnie_params: dict,
) -> tuple[list, list]:
    """Separa propostas de entrada em aprovadas e vetadas.

    Apenas filtra BUY — SELL/REDUCE passam sempre (saídas nunca são vetadas).

    EARNINGS:  veta se o ticker tiver earnings em ≤ `no_trade_before_earnings_days`
               (config.RISK_CONFIG). A regra precede VALUE/MOMENTUM por ser de risco
               sistémico, não de estratégia.
    VALUE:     veta se signal_strength < base_strength_threshold.
    MOMENTUM:  veta apenas por volume seco (vol_ratio < momentum_vol_floor)
               ou gap down acentuado (last_price < prev × (1 - gap_down_pct/100)).
               RSI elevado nunca veta MOMENTUM — é pré-condição da regra M.

    Retorna (approved: list[ProposedTrade], vetoed: list[tuple[ProposedTrade, str]]).
    """
    base_threshold        = bonnie_params.get("base_threshold", 0.60)
    vol_floor             = bonnie_params.get("momentum_vol_floor", 1.0)
    gap_pct               = bonnie_params.get("momentum_gap_down_pct", 3.0)
    smart_money_min_ratio = bonnie_params.get("smart_money_vol_ratio", 1.2)

    earnings_map   = _earnings_window_map()
    threshold_days = RISK_CONFIG.get("no_trade_before_earnings_days", 2)

    approved: list        = []
    vetoed:   list        = []

    for trade in proposals:
        if getattr(trade, "side", "BUY") != "BUY":
            approved.append(trade)
            continue

        ticker = getattr(trade, "ticker", "")
        ticker_base = ticker.split("_")[0].upper() if ticker else ""

        # ── Veto por earnings (regra sistémica, corre antes de VALUE/MOMENTUM) ──
        if ticker_base in earnings_map:
            days_away, date_str = earnings_map[ticker_base]
            reason = (
                f"EARNINGS: {ticker_base} reporta em {days_away}d ({date_str}) "
                f"<= janela de {threshold_days}d - compra vetada"
            )
            print(f"[BONNIE VETO] {ticker}: {reason}", flush=True)
            vetoed.append((trade, reason))
            continue

        data     = market_data.get(ticker, {})
        features = data.get("features", {})
        t        = data.get("technicals", {}) or {}
        vol      = t.get("volume_ratio_vs_avg") or 1.0
        last     = t.get("last_price") or data.get("last_price")
        prev     = data.get("previous_close")
        style    = getattr(trade, "style", "VALUE")

        # Smart Money gate — universal para todos os BUY (VALUE e MOMENTUM)
        # Usa volume_ratio (SMA-10) e cai para volume_ratio_vs_avg (SMA-20) se ausente
        vol_ratio = t.get("volume_ratio") or t.get("volume_ratio_vs_avg") or 1.0
        if vol_ratio < smart_money_min_ratio:
            log_decision("bonnie_rejected_low_volume", "fakeout_vetado", {
                "ticker":       ticker,
                "volume_ratio": round(vol_ratio, 2),
                "threshold":    smart_money_min_ratio,
                "style":        style,
            })
            reason = (
                f"FAKEOUT: volume_ratio {vol_ratio:.2f}× < {smart_money_min_ratio}× "
                f"— sem força institucional ({style})"
            )
            print(f"[BONNIE VETO] {ticker}: {reason}", flush=True)
            vetoed.append((trade, reason))
            continue

        if style == "MOMENTUM":
            if vol < vol_floor:
                reason = f"MOMENTUM: volume {vol:.2f}× < mínimo {vol_floor}× — liquidez insuficiente"
                print(f"[BONNIE VETO] {ticker}: {reason}", flush=True)
                vetoed.append((trade, reason))
                continue
            if last and prev and prev > 0:
                gap = (prev - last) / prev * 100
                if gap > gap_pct:
                    reason = f"MOMENTUM: gap down {gap:.1f}% > {gap_pct}% — entrada suspensa"
                    print(f"[BONNIE VETO] {ticker}: {reason}", flush=True)
                    vetoed.append((trade, reason))
                    continue

        else:  # VALUE (ou fallback)
            strength = getattr(trade, "signal_strength", 0.0)
            if strength < base_threshold:
                reason = f"VALUE: força {strength:.2f} < limiar {base_threshold:.2f}"
                print(f"[BONNIE VETO] {ticker}: {reason}", flush=True)
                vetoed.append((trade, reason))
                continue

        _bonnie.observe(ticker, features, True)
        approved.append(trade)

    return approved, vetoed


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

_LOCK_FILE = "bonnie.lock"
_SLEEP_SECONDS = 60 * 60  # 60 minutos


def _acquire_lock() -> bool:
    if os.path.exists(_LOCK_FILE):
        try:
            with open(_LOCK_FILE) as f:
                old_pid = int(f.read().strip())
        except Exception:
            os.remove(_LOCK_FILE)
        else:
            try:
                os.kill(old_pid, 0)
                return False  # processo ainda vivo
            except OSError:
                os.remove(_LOCK_FILE)  # PID morto — lock fantasma
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _run_audit() -> None:
    print(f"[Bonnie] Fase-0 — {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")

    config_risco = ensure_config_risco()
    print(f"[Bonnie] config_risco.json: permite_comprar={config_risco['permite_comprar']}, "
          f"fator={config_risco['tamanho_maximo_posicao']}")

    trades = read_diario_trades()
    print(f"[Bonnie] {len(trades)} entradas no diário")

    stats = calc_stats(trades)
    print(f"[Bonnie] Trades fechados: {stats['total_closed']}")

    # Actualiza estado_emocional com base no win rate recente
    novo_estado = calc_estado_emocional(stats, config_risco)
    if config_risco.get("estado_emocional") != novo_estado:
        config_risco["estado_emocional"] = novo_estado
        try:
            with open(CONFIG_RISCO_PATH, "w", encoding="utf-8") as f:
                json.dump(config_risco, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            log_error("bonnie_estado_write", {"error": str(exc)})
    print(f"[Bonnie] Estado emocional: {novo_estado}")

    vetos = get_vetos(trades)
    evolucao = build_evolucao(stats)
    print(f"[Bonnie] {len(vetos)} veto(s) registados")

    news = _load_news()
    alerts = generate_news_alerts(trades, news)
    print(f"[Bonnie] {len(alerts)} alertas de notícias gerados")

    earnings = _load_earnings()
    portfolio_tickers = _load_portfolio_tickers()
    earnings_alerts = generate_earnings_alerts(portfolio_tickers, earnings)
    if earnings_alerts:
        print(f"[Bonnie] {len(earnings_alerts)} alerta(s) earnings_risk: "
              + ", ".join(a["ticker"] for a in earnings_alerts))
    else:
        print("[Bonnie] Sem alertas earnings_risk")

    write_bonnie_log(stats, alerts, config_risco, earnings_alerts, vetos=vetos, evolucao=evolucao)


def main() -> None:
    if not _acquire_lock():
        print("[Bonnie] Já está a correr (bonnie.lock existe). Termina o processo anterior primeiro.")
        sys.exit(1)

    print("[Bonnie] Iniciada — auditoria a cada 60 minutos. Ctrl+C para parar.\n")
    try:
        while True:
            try:
                _run_audit()
            except Exception as exc:
                log_error("bonnie_audit_failed", {"error": str(exc)})
                print(f"[Bonnie] Erro na auditoria: {exc}")

            print(f"[Bonnie] Próxima auditoria em 60 minutos...\n")
            time.sleep(_SLEEP_SECONDS)

    except KeyboardInterrupt:
        print("\n[Bonnie] Parada pelo utilizador.")
    finally:
        if os.path.exists(_LOCK_FILE):
            os.remove(_LOCK_FILE)


if __name__ == "__main__":
    main()
