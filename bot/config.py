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
