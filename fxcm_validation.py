"""In-memory FXCM reference-feed receiver and Twelve Data comparison."""

from __future__ import annotations

import hmac
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import config

log = logging.getLogger(__name__)

FXCM_PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "EURAUD", "NZDCAD")
FXCM_SYMBOLS = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "EURAUD": "EUR/AUD",
    "NZDCAD": "NZD/CAD",
}


class FXCMValidationStore:
    def __init__(self, primary_snapshot_provider):
        self._primary_snapshot_provider = primary_snapshot_provider
        self._lock = threading.Lock()
        self._last_received_at: str | None = None
        self._last_error: str | None = None
        self._pairs: dict[str, dict[str, Any]] = {}

    def enabled(self) -> bool:
        return config.FXCM_BRIDGE_ENABLED

    def accept(self, payload: dict[str, Any], secret: str | None) -> tuple[bool, str]:
        expected = config.FXCM_BRIDGE_SHARED_SECRET
        if not expected or not secret or not hmac.compare_digest(secret, expected):
            return False, "unauthorized"
        if not isinstance(payload.get("pairs"), dict):
            return False, "pairs must be an object"

        received_at = datetime.now(timezone.utc).isoformat()
        accepted: dict[str, dict[str, Any]] = {}
        for pair, raw in payload["pairs"].items():
            pair = str(pair).upper()
            if pair not in FXCM_PAIRS or not isinstance(raw, dict):
                continue
            try:
                price = float(raw["price"])
                if price <= 0:
                    continue
                record = {
                    "symbol": FXCM_SYMBOLS[pair],
                    "price": price,
                    "timestamp": _normalize_timestamp(raw.get("timestamp")),
                    "received_at": received_at,
                    "bid": _optional_float(raw.get("bid")),
                    "ask": _optional_float(raw.get("ask")),
                    "ohlc": _normalize_ohlc(raw.get("ohlc")),
                }
                accepted[pair] = record
            except (TypeError, ValueError):
                continue

        if not accepted:
            return False, "no supported valid pairs"
        with self._lock:
            self._pairs.update(accepted)
            self._last_received_at = received_at
            self._last_error = None
        return True, "accepted"

    def snapshot(self) -> dict[str, Any]:
        primary = self._primary_snapshot_provider()
        now = time.time()
        with self._lock:
            pairs = {pair: dict(value) for pair, value in self._pairs.items()}
            last_received_at = self._last_received_at
            last_error = self._last_error
        age = _age_seconds(last_received_at, now)
        connected = bool(pairs) and age is not None and age <= config.FXCM_STALE_SECONDS
        result_pairs: dict[str, Any] = {}
        for pair in FXCM_PAIRS:
            value = pairs.get(pair)
            if not config.FXCM_BRIDGE_ENABLED:
                result_pairs[pair] = {"symbol": FXCM_SYMBOLS[pair], "validation_status": "DISABLED"}
                continue
            if not value:
                result_pairs[pair] = {"symbol": FXCM_SYMBOLS[pair], "validation_status": "UNAVAILABLE"}
                continue
            primary_pair = primary.get("pairs", {}).get(pair, {})
            primary_market = primary.get("market_data", {}).get(pair, {})
            price_difference_pct = _percent_difference(
                value.get("price"), primary_pair.get("last_twelve_data_price")
            )
            result_pairs[pair] = {
                **value,
                "age_seconds": age,
                "stale": age is None or age > config.FXCM_STALE_SECONDS,
                "price_difference_pct": price_difference_pct,
                "ohlc_comparison": _compare_timeframes(value.get("ohlc", {}), primary_market),
                "validation_status": _validation_status(
                    age, price_difference_pct, config.FXCM_MAX_PRICE_DISCREPANCY_PCT
                ),
            }
        return {
            "enabled": config.FXCM_BRIDGE_ENABLED,
            "connected": connected,
            "account_type": config.FXCM_ACCOUNT_TYPE,
            "last_message_at": last_received_at,
            "last_error": last_error,
            "stale": age is None or age > config.FXCM_STALE_SECONDS,
            "pairs": result_pairs,
        }


def _normalize_timestamp(value: Any) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def _normalize_ohlc(value: Any) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {"D1": None, "H4": None}
    if not isinstance(value, dict):
        return result
    for timeframe in result:
        candle = value.get(timeframe)
        if not isinstance(candle, dict):
            continue
        required = ("timestamp", "open", "high", "low", "close")
        if any(field not in candle for field in required):
            continue
        result[timeframe] = {
            "timestamp": _normalize_timestamp(candle["timestamp"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "completed": bool(candle.get("completed", True)),
        }
    return result


def _compare_timeframes(fxcm: dict[str, Any], primary: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for timeframe in ("D1", "H4"):
        left = fxcm.get(timeframe)
        right = primary.get(timeframe)
        if not left or not right:
            result[timeframe] = {"status": "UNAVAILABLE"}
            continue
        result[timeframe] = {
            "status": "COMPARED",
            "timestamp_difference_seconds": int(
                (datetime.fromisoformat(left["timestamp"]) - datetime.fromisoformat(right["timestamp"])).total_seconds()
            ),
            "ohlc_difference": {
                field: float(left[field]) - float(right[field])
                for field in ("open", "high", "low", "close")
            },
        }
    return result


def _validation_status(age: float | None, difference: float | None, threshold: float) -> str:
    if age is None or age > config.FXCM_STALE_SECONDS:
        return "STALE"
    if difference is None:
        return "NO_PRIMARY"
    return "WARNING" if difference > threshold else "OK"


def _percent_difference(left: Any, right: Any) -> float | None:
    if left is None or right is None or float(right) == 0:
        return None
    return abs(float(left) - float(right)) / abs(float(right)) * 100


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _age_seconds(timestamp: str | None, now: float) -> float | None:
    if not timestamp:
        return None
    try:
        return max(0.0, now - datetime.fromisoformat(timestamp).timestamp())
    except (TypeError, ValueError):
        return None
