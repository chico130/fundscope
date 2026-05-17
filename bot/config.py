import base64
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

# T212 usa HTTP Basic Auth: Authorization: Basic base64(API_ID:API_SECRET)
# Variáveis de ambiente: T212_API_ID + T212_API_KEY (GitHub Actions e .env local).
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

# Finnhub: feed de preços em tempo real (free tier: 60 req/min).
# Registo gratuito em https://finnhub.io/register
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# Demo first, always. Never flip to True without extensive testing in demo.
LIVE_TRADING = False

# Fase 1 — execução automática em conta demo. Nunca activar com LIVE_TRADING=True.
PHASE1_EXECUTION = True

RISK_CONFIG = {
    "max_position_pct": 20.0,
    "max_sector_pct": 40.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 10,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "no_trade_before_earnings_days": 2,
    "min_data_points_required": 20,
    "max_positions_per_sector": 2,
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

WATCHLIST_CONFIG = {
    "max_size": 25,
    "sectors": ["XLK", "XLV", "XLY", "XLI", "XLE"],
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
}
