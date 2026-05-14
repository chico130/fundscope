import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

T212_KEY_ID = os.getenv("T212_KEY_ID", "")
T212_API_KEY_DEMO = os.getenv("T212_API_KEY_DEMO", "")
T212_BASE_URL_DEMO = "https://demo.trading212.com/api/v0"

# Demo first, always. Never flip to True without extensive testing in demo.
LIVE_TRADING = False

RISK_CONFIG = {
    "max_position_pct": 20.0,
    "max_sector_pct": 40.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 10,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "no_trade_before_earnings_days": 2,
    "min_data_points_required": 20,
}

STRATEGY_VERSION = "v0.1.0"

# T212 rate limits: ~1 req/s on demo
REQUEST_DELAY_SECONDS = 1.2

DATA_BETA_DIR = BASE_DIR / "data" / "beta"
DATA_ALPHA_DIR = BASE_DIR / "data" / "alpha"
LOGS_DIR = BASE_DIR / "logs"
LOGS_TRADES_DIR = LOGS_DIR / "trades"
LOGS_ERRORS_DIR = LOGS_DIR / "errors"

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
    "bear_threshold_spy_ema200_pct": -5.0,
    "bull_breadth_threshold_pct": 60.0,
    "lateral_atr_multiplier": 0.8,
}
