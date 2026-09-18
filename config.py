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
STRATEGY_PRIMARY_PROVIDER = os.getenv("STRATEGY_PRIMARY_PROVIDER", "twelve_data").strip().lower()
FOREX_STRATEGY_PRIMARY_PROVIDER = os.getenv(
    "FOREX_STRATEGY_PRIMARY_PROVIDER", STRATEGY_PRIMARY_PROVIDER
).strip().lower()
CRYPTO_STRATEGY_PRIMARY_PROVIDER = os.getenv(
    "CRYPTO_STRATEGY_PRIMARY_PROVIDER", STRATEGY_PRIMARY_PROVIDER
).strip().lower()
STRATEGY_SHADOW_MODE = os.getenv("STRATEGY_SHADOW_MODE", "true").lower() in {
    "true",
    "1",
    "yes",
}
STRATEGY_SHADOW_LOG = os.getenv("STRATEGY_SHADOW_LOG", "true").lower() in {
    "true",
    "1",
    "yes",
}
LEVEL_DRIFT_TOLERANCE_FOREX_PIPS = float(
    os.getenv("LEVEL_DRIFT_TOLERANCE_FOREX_PIPS", "2")
)
LEVEL_DRIFT_TOLERANCE_CRYPTO_POINTS = float(
    os.getenv("LEVEL_DRIFT_TOLERANCE_CRYPTO_POINTS", "2")
)
STRATEGY_DIVERGENCE_RATE_LIMIT_SECONDS = int(
    os.getenv("STRATEGY_DIVERGENCE_RATE_LIMIT_SECONDS", "3600")
)
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
SHARP_ATR_MULT = 0.55
ATR_PERIOD = 14
RECENT_LEVELS = 4
DAILY_SETUP_LOOKBACK_BARS = 2
H4_SETUP_LOOKBACK_BARS = 3
MAX_H4_BARS_FOR_RETEST = 12
EMA_PULLBACK_MIN_BARS = int(os.getenv("EMA_PULLBACK_MIN_BARS", "5"))
EMA_PULLBACK_MAX_BARS = int(os.getenv("EMA_PULLBACK_MAX_BARS", "10"))
EMA_PULLBACK_SPAN = int(os.getenv("EMA_PULLBACK_SPAN", "20"))
MAX_DISTANCE_PIPS_FOR_INVALIDATION = float(
    os.getenv("MAX_DISTANCE_PIPS", "100")
)
GAP_THRESHOLD_PIPS = float(os.getenv("GAP_THRESHOLD_PIPS", "15"))
AGING_WARNING_H4_BARS = int(os.getenv("AGING_WARNING_H4_BARS", "12"))
NEWS_FILTER_ENABLED = os.getenv("NEWS_FILTER_ENABLED", "false").lower() in {
    "true",
    "1",
    "yes",
}
VOLUME_FILTER_ENABLED = os.getenv("VOLUME_FILTER_ENABLED", "false").lower() in {
    "true",
    "1",
    "yes",
}
VOLUME_FILTER_MIN_MULTIPLIER = float(
    os.getenv("VOLUME_FILTER_MIN_MULTIPLIER", "1.25")
)
RULE_PROMPT_ENABLED = os.getenv("RULE_PROMPT_ENABLED", "true").lower() in {
    "true",
    "1",
    "yes",
}
NEWS_BLACKOUT_BEFORE_MINUTES = 1440
NEWS_BLACKOUT_AFTER_MINUTES = 60
CALENDAR_REFRESH_MINUTES = 360
TRADING_ECONOMICS_API_KEY = os.getenv("TRADING_ECONOMICS_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
UPSTASH_REDIS_URL = os.getenv("UPSTASH_REDIS_URL", "")
ECONOMIC_EVENTS_PATH = Path(
    os.getenv("ECONOMIC_EVENTS_PATH", str(BASE_DIR / "data" / "economic_events.json"))
)
TELEGRAM_OVERRIDE_TIMEOUT_MINUTES = int(
    os.getenv("TELEGRAM_OVERRIDE_TIMEOUT_MINUTES", "120")
)
CONFIRMATION_REPROMPT_MINUTES = int(
    os.getenv("CONFIRMATION_REPROMPT_MINUTES", "5")
)
CONFIRMATION_MAX_PROMPTS = int(
    os.getenv("CONFIRMATION_MAX_PROMPTS", "5")
)
CRYPTO_BINANCE_VALIDATION_ENABLED = os.getenv(
    "CRYPTO_BINANCE_VALIDATION_ENABLED", "false"
).lower() in {"true", "1", "yes"}
CRYPTO_COINBASE_VALIDATION_ENABLED = os.getenv(
    "CRYPTO_COINBASE_VALIDATION_ENABLED", "false"
).lower() in {"true", "1", "yes"}
CRYPTO_BINANCE_WS_URL = os.getenv(
    "CRYPTO_BINANCE_WS_URL", "wss://data-stream.binance.vision/ws"
).strip()
CRYPTO_COINBASE_WS_URL = os.getenv(
    "CRYPTO_COINBASE_WS_URL", "wss://advanced-trade-ws.coinbase.com"
).strip()
CRYPTO_BINANCE_MAX_PRICE_DISCREPANCY_PCT = float(
    os.getenv("CRYPTO_BINANCE_MAX_PRICE_DISCREPANCY_PCT", "1.0")
)
CRYPTO_COINBASE_MAX_PRICE_DISCREPANCY_PCT = float(
    os.getenv("CRYPTO_COINBASE_MAX_PRICE_DISCREPANCY_PCT", "1.0")
)
CRYPTO_BINANCE_STALE_SECONDS = int(
    os.getenv("CRYPTO_BINANCE_STALE_SECONDS", "60")
)
CRYPTO_COINBASE_STALE_SECONDS = int(
    os.getenv("CRYPTO_COINBASE_STALE_SECONDS", "60")
)
CRYPTO_BINANCE_MAX_CONNECTION_SECONDS = int(
    os.getenv("CRYPTO_BINANCE_MAX_CONNECTION_SECONDS", str(23 * 60 * 60))
)
CRYPTO_VALIDATION_RECONNECT_SECONDS = int(
    os.getenv("CRYPTO_VALIDATION_RECONNECT_SECONDS", "5")
)
CRYPTO_VALIDATION_HTTP_TIMEOUT_SECONDS = float(
    os.getenv("CRYPTO_VALIDATION_HTTP_TIMEOUT_SECONDS", "10")
)
CRYPTO_VALIDATION_CANDLE_REFRESH_SECONDS = int(
    os.getenv("CRYPTO_VALIDATION_CANDLE_REFRESH_SECONDS", "60")
)
CRYPTO_BINANCE_REST_URL = os.getenv(
    "CRYPTO_BINANCE_REST_URL", "https://data-api.binance.vision"
).rstrip("/")
CRYPTO_COINBASE_REST_URL = os.getenv(
    "CRYPTO_COINBASE_REST_URL", "https://api.exchange.coinbase.com"
).rstrip("/")
FXCM_BRIDGE_ENABLED = os.getenv("FXCM_BRIDGE_ENABLED", "false").lower() in {
    "true", "1", "yes"
}
FXCM_BRIDGE_SHARED_SECRET = os.getenv("FXCM_BRIDGE_SHARED_SECRET", "")
FXCM_ACCOUNT_TYPE = os.getenv("FXCM_ACCOUNT_TYPE", "demo").strip()
FXCM_STALE_SECONDS = int(os.getenv("FXCM_STALE_SECONDS", "90"))
FXCM_MAX_PRICE_DISCREPANCY_PCT = float(
    os.getenv("FXCM_MAX_PRICE_DISCREPANCY_PCT", "0.1")
)
FXCM_OHLC_TOLERANCE_PIPS = float(
    os.getenv("FXCM_OHLC_TOLERANCE_PIPS", "2.0")
)
FXCM_OHLC_MISMATCH_MULTIPLIER = float(
    os.getenv("FXCM_OHLC_MISMATCH_MULTIPLIER", "3.0")
)
FXCM_CONFLICT_NEAR_LEVEL_PIPS = float(
    os.getenv("FXCM_CONFLICT_NEAR_LEVEL_PIPS", "10")
)
FXCM_LOW_ALERT_PIPS = float(os.getenv("FXCM_LOW_ALERT_PIPS", "0.5"))
FXCM_MEDIUM_ALERT_PIPS = float(os.getenv("FXCM_MEDIUM_ALERT_PIPS", "2.0"))
FXCM_HIGH_ALERT_PIPS = float(os.getenv("FXCM_HIGH_ALERT_PIPS", "4.0"))
RETIRE_LEGACY_ALERT_SENT = os.getenv(
    "RETIRE_LEGACY_ALERT_SENT", "false"
).lower() in {"true", "1", "yes"}
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
TRADINGVIEW_EMAIL_ENABLED = os.getenv("TRADINGVIEW_EMAIL_ENABLED", "false").lower() in {"true", "1", "yes"}
TRADINGVIEW_EMAIL_HOST = os.getenv("TRADINGVIEW_EMAIL_HOST", "imap.gmail.com").strip()
TRADINGVIEW_EMAIL_PORT = int(os.getenv("TRADINGVIEW_EMAIL_PORT", "993"))
TRADINGVIEW_EMAIL_USERNAME = os.getenv("TRADINGVIEW_EMAIL_USERNAME", "").strip()
TRADINGVIEW_EMAIL_PASSWORD = os.getenv("TRADINGVIEW_EMAIL_PASSWORD", "")
TRADINGVIEW_EMAIL_FOLDER = os.getenv("TRADINGVIEW_EMAIL_FOLDER", "INBOX").strip()
TRADINGVIEW_EMAIL_POLL_INTERVAL = float(os.getenv("TRADINGVIEW_EMAIL_POLL_INTERVAL", "30"))
TRADINGVIEW_EMAIL_DRY_RUN = os.getenv("TRADINGVIEW_EMAIL_DRY_RUN", "true").lower() in {"true", "1", "yes"}
TRADINGVIEW_PROCESS_EXISTING_ON_START = os.getenv("TRADINGVIEW_PROCESS_EXISTING_ON_START", "false").lower() in {"true", "1", "yes"}
TRADINGVIEW_ALLOWED_SENDER = os.getenv("TRADINGVIEW_ALLOWED_SENDER", "tradingview.com").strip().lower()
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
