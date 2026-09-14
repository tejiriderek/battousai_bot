"""Scanner configuration. Secrets come from the environment, never from source."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PAIRS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "EURAUD",
    "NZDCAD",
    "BTCUSDT",
    "ETHUSDT",
)

# Twelve Data symbols use a slash; crypto symbols use USD on this endpoint.
TWELVE_DATA_SYMBOLS: dict[str, str] = {
    pair: f"{pair[:3]}/{pair[3:]}" for pair in PAIRS
}
TWELVE_DATA_SYMBOLS.update(
    {
        "BTCUSDT": "BTC/USD",
        "ETHUSDT": "ETH/USD",
    }
)

TIMEFRAMES: dict[str, str] = {
    "D1": "1day",
    "H4": "4h",
}

LOOKBACK_CANDLES = 80
MIN_CANDLES = 40
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
SHARP_ATR_MULT = 0.55
ATR_PERIOD = 14
RECENT_LEVELS = 4
DAILY_SETUP_LOOKBACK_BARS = 2
H4_SETUP_LOOKBACK_BARS = 3
MAX_H4_BARS_FOR_RETEST = 12
MAX_DISTANCE_PIPS_FOR_INVALIDATION = float(
    os.getenv("MAX_DISTANCE_PIPS", "100")
)
GAP_THRESHOLD_PIPS = float(os.getenv("GAP_THRESHOLD_PIPS", "15"))
AGING_WARNING_H4_BARS = int(os.getenv("AGING_WARNING_H4_BARS", "12"))
NEWS_FILTER_ENABLED = os.getenv("NEWS_FILTER_ENABLED", "true").lower() in {
    "true",
    "1",
    "yes",
}
NEWS_BLACKOUT_BEFORE_MINUTES = 1440
NEWS_BLACKOUT_AFTER_MINUTES = 60
CALENDAR_REFRESH_MINUTES = 360
TRADING_ECONOMICS_API_KEY = os.getenv("TRADING_ECONOMICS_API_KEY", "")
ECONOMIC_EVENTS_PATH = Path(
    os.getenv("ECONOMIC_EVENTS_PATH", str(BASE_DIR / "data" / "economic_events.json"))
)
TELEGRAM_OVERRIDE_TIMEOUT_MINUTES = int(
    os.getenv("TELEGRAM_OVERRIDE_TIMEOUT_MINUTES", "120")
)
REQUEST_GAP_SECONDS = 15.0
API_TIMEOUT_SECONDS = 20.0
API_MAX_RETRIES = 5
OHLC_GAP_TOLERANCE = 2.5

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TWELVE_DATA_API_KEY_SECOND = os.getenv("TWELVE_DATA_API_KEY_SECOND", "").strip()
TWELVE_DATA_API_KEYS = tuple(
    key for key in (TWELVE_DATA_API_KEY, TWELVE_DATA_API_KEY_SECOND) if key
)
TWELVE_DATA_BASE_URL = os.getenv(
    "TWELVE_DATA_BASE_URL", "https://api.twelvedata.com"
).rstrip("/")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_AUTHORIZED_USER_ID = os.getenv("TELEGRAM_AUTHORIZED_USER_ID", "").strip()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "1800"))
PORT = int(os.getenv("PORT", "8080"))
HOST = os.getenv("HOST", "0.0.0.0")
STATE_PATH = Path(os.getenv("STATE_PATH", str(BASE_DIR / "data" / "state.json")))

STATES = (
    "WATCHING",
    "DAILY_REJECTION_DETECTED",
    "DAILY_BREAKOUT_CONFIRMED",
    "H4_WAITING",
    "H4_BREAKOUT_CONFIRMED",
    "WAITING_FOR_RETEST",
    "RETEST_CONFIRMED",
    "CONTINUATION_CONFIRMED",
    "ALERT_SENT",
)


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def price_epsilon(pair: str) -> float:
    """Float-noise only. Retests still use the exact broken level."""
    return pip_size(pair) * 0.05
