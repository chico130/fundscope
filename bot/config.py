import base64
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

# Trading 212 API: HTTP Basic Auth — Authorization: Basic base64(API_ID:API_SECRET).
# Confirmado empiricamente contra demo.trading212.com: o esquema id:secret é o único
# que autentica (200). A key/secret sozinha dá 401.
#   T212_API_ID  = key ID    (ex.: 39448817...)
#   T212_API_KEY = secret    (ex.: P8Oq25...)
_t212_id     = os.getenv("T212_API_ID", "")
_t212_secret = os.getenv("T212_API_KEY", "")

if _t212_id and _t212_secret:
    _creds = base64.b64encode(f"{_t212_id}:{_t212_secret}".encode()).decode()
    T212_DEMO_KEY = f"Basic {_creds}"
else:
    # Fallback: chave única (formato legado) — produz 401 se vazia
    T212_DEMO_KEY = os.getenv("T212_DEMO_KEY") or os.getenv("T212_API_KEY_DEMO", "")

T212_API_KEY_DEMO  = T212_DEMO_KEY   # alias de retrocompatibilidade
T212_BASE_URL_DEMO = "https://demo.trading212.com/api/v0"

# T212 LIVE — auth e base URL para pre-flight check de Fase 3.
# NÃO é usado pelo bot em runtime (api_client está hardcoded a DEMO). Só serve
# para o contract test confirmar paridade de schema antes do flip LIVE_TRADING.
# Aceita T212_LIVE_API_ID / T212_LIVE_API_KEY (preferidos) ou cai para os mesmos
# T212_API_ID / T212_API_KEY se não houver credenciais separadas.
_t212_live_id     = os.getenv("T212_LIVE_API_ID", "")
_t212_live_secret = os.getenv("T212_LIVE_API_KEY", "")

if _t212_live_id and _t212_live_secret:
    _creds_live   = base64.b64encode(f"{_t212_live_id}:{_t212_live_secret}".encode()).decode()
    T212_LIVE_KEY = f"Basic {_creds_live}"
else:
    T212_LIVE_KEY = ""

T212_BASE_URL_LIVE = "https://live.trading212.com/api/v0"

# Finnhub: feed de preços em tempo real (free tier: 60 req/min).
# Registo gratuito em https://finnhub.io/register
# Aceita FINNHUB_API_KEY ou FINNHUB_TOKEN (o .env do VPS usa o segundo nome).
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_TOKEN", "")

# Demo first, always. Never flip to True without extensive testing in demo.
LIVE_TRADING = False

# Fase 1 — execução automática em conta demo. Nunca activar com LIVE_TRADING=True.
PHASE1_EXECUTION = True

RISK_CONFIG = {
    "max_position_pct": 11.0,
    "max_sector_pct": 40.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 10,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "no_trade_before_earnings_days": 2,
    "min_data_points_required": 20,
    "max_positions_per_sector": 3,
}

STRATEGY_VERSION = "v0.1.0"

# T212 rate limits: ~1 req/s on demo
REQUEST_DELAY_SECONDS = 1.2

LOOP_INTERVAL_SECONDS = 900  # 15 minutos entre ciclos

DATA_BETA_DIR = BASE_DIR / "data" / "beta"
DATA_ALPHA_DIR = BASE_DIR / "data" / "alpha"
LOGS_DIR = BASE_DIR / "logs"
LOGS_TRADES_DIR = LOGS_DIR / "trades"
LOGS_ERRORS_DIR = LOGS_DIR / "errors"

# Ficheiros na raiz — lidos pelo site via fetch
DIARIO_TRADES_PATH = BASE_DIR / "diario_trades.json"
CONFIG_RISCO_PATH = BASE_DIR / "config_risco.json"
NEWS_PATH = BASE_DIR / "news.json"
EARNINGS_PATH = BASE_DIR / "earnings.json"
PORTFOLIO_PATH = BASE_DIR / "portfolio.json"
BONNIE_LOG_PATH = LOGS_DIR / "bonnie_log.json"

# Scan de candidatos (phase0 / watchlist builder)
SCAN_TOP_N               = 25   # top N tickers a avançar para análise técnica
SCAN_WORKERS_YF          = 8    # threads paralelas no ThreadPoolExecutor
SCAN_TIMEOUT_PER_TICKER  = 12   # segundos máximos por ticker (yfinance)
SCAN_MIN_RESULTS         = 5    # mínimo de resultados válidos; falha se abaixo

WATCHLIST_CONFIG = {
    "max_size": 100,
    "sectors": ["XLK", "XLV", "XLY", "XLI", "XLE", "XLF", "XLC", "XLU", "XLP"],
    "min_avg_volume_usd": 10_000_000,
    "min_price_usd": 5.0,
    "score_weights": {"momentum_1m": 0.4, "momentum_3m": 0.3, "liquidity": 0.2, "quality": 0.1},
    "update_frequency_days": 1,          # momentum scores rebuilt daily
    "fundamentals_frequency_days": 7,    # yfinance.info per-ticker (ROE, D/E, targets) weekly
}

REGIME_CONFIG = {
    "bear_threshold_spy_ema200_pct": 0.0,
    "bull_breadth_threshold_pct": 60.0,
    "lateral_atr_multiplier": 0.8,
}

CRO_CONFIG = {
    "max_drawdown_limit_pct":    15.0,  # drawdown onde risk_factor atinge mínimo (0.3×)
    # Janela Deslizante Adaptativa — alvo dinâmico (substitui target_win_rate_pct fixo)
    "elastic_window_n":          25,    # N trades para calcular WR alvo da janela
    "elastic_fallback_wr":       0.48,  # WR base temporária quando < N trades disponíveis
    # Hierarquia CRO → Bonnie (controlo de frequência de entradas)
    "bonnie_base_threshold":     0.60,  # threshold standard de veto da Bonnie
    "bonnie_strict_threshold":   0.64,  # threshold apertado em mercado adverso
    "bonnie_strict_trigger_wr":  0.45,  # WR(N) abaixo da qual o CRO aperta a Bonnie
    "cro_insights_path":         DATA_BETA_DIR / "cro_insights.json",
    # Multiplicadores de Regime (autoridade exclusiva CRO — independente do Clyde)
    "regime_multiplier": {
        "bull_trending":     1.0,   # alocação normal
        "bull_lateral":      0.5,   # corte a metade — mercado indeciso
        "bear_correction":   0.0,   # veto de entradas
        "bear_capitulation": 0.0,   # veto de entradas
    },
    "bear_value_multiplier":      0.25,  # value em bear: 0.25× (defensivo — só sinais value)
    # ATR Position Sizing — equaliza risco financeiro entre activos
    "atr_risk_target_pct":        1.0,   # % da equity a arriscar por trade (via stop ATR)
    "atr_stop_mult_momentum":     2.0,   # stop = 2× ATR abaixo da entrada (momentum)
    "atr_stop_mult_value":        1.75,  # stop = 1.75× ATR abaixo da entrada (value — v3 params)
    "atr_tp_mult":                4.25,  # take-profit = 4.25× ATR acima da entrada (v3 params)
    "value_trail_activation":     3.0,   # trailing activa quando gain ≥ 3.0× ATR
    "value_trail_distance":       3.5,   # trailing stop a 3.5× ATR do peak (v3 params)
    "atr_fallback_stop_pct":      5.0,   # % de stop fixo quando ATR não disponível
}
