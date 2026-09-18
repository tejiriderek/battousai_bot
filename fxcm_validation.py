"""In-memory FXCM reference-feed receiver and Twelve Data comparison."""

from __future__ import annotations

import hmac
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

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
    def __init__(
        self,
        primary_snapshot_provider,
        event_recorder: Callable[[dict[str, Any]], None] | None = None,
    ):
        self._primary_snapshot_provider = primary_snapshot_provider
        self._event_recorder = event_recorder
        self._lock = threading.Lock()
        self._last_received_at: str | None = None
        self._last_error: str | None = None
        self._pairs: dict[str, dict[str, Any]] = {}
        self._ohlc_event_signatures: dict[tuple[str, str], tuple[Any, ...]] = {}

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
                    "history": _normalize_history(raw.get("history"), raw.get("ohlc")),
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
            ohlc_comparison = _compare_timeframes(
                pair, value.get("ohlc", {}), primary_market
            )
            self._record_ohlc_events(pair, ohlc_comparison, primary_pair)
            result_pairs[pair] = {
                **value,
                "age_seconds": age,
                "stale": age is None or age > config.FXCM_STALE_SECONDS,
                "price_difference_pct": price_difference_pct,
                "ohlc_comparison": ohlc_comparison,
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

    def _record_ohlc_events(
        self,
        pair: str,
        comparison: dict[str, Any],
        primary_pair: dict[str, Any],
    ) -> None:
        for timeframe, result in comparison.items():
            key = (pair, timeframe)
            if result.get("period_status") != "SAME_PERIOD":
                self._ohlc_event_signatures.pop(key, None)
                continue
            signature = (
                result.get("left_timestamp"),
                result.get("right_timestamp"),
                tuple(result.get("mismatch_fields", [])),
                tuple(result.get("ohlc_difference_pips", {}).items()),
            )
            differences = result.get("ohlc_difference_pips") or {}
            maximum_difference = max((float(value) for value in differences.values()), default=0.0)
            if maximum_difference < config.FXCM_LOW_ALERT_PIPS:
                continue
            if self._ohlc_event_signatures.get(key) == signature:
                continue
            if not _near_active_setup_level(pair, result, primary_pair):
                continue
            self._ohlc_event_signatures[key] = signature
            if self._event_recorder:
                severity, response = _difference_severity(maximum_difference)
                self._event_recorder(
                    {
                        "type": "fxcm_conflict",
                        "warning_type": "fxcm_conflict",
                        "severity": severity,
                        "review_response": response,
                        "pair": pair,
                        "timeframe": timeframe,
                        "fields": result.get("mismatch_fields", []),
                        "tolerance_pips": result.get("tolerance_pips"),
                        "ohlc_difference_pips": result.get("ohlc_difference_pips", {}),
                        "twelve_data": result.get("twelve_data"),
                        "fxcm": result.get("fxcm"),
                        "maximum_difference_pips": maximum_difference,
                        "requires_review": True,
                        "direction": primary_pair.get("direction"),
                        "state": primary_pair.get("state"),
                        "setup_id": primary_pair.get("setup_id"),
                        "daily_level_price": primary_pair.get("daily_level_price"),
                        "h4_level_price": primary_pair.get("h4_level_price"),
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                )


def _difference_severity(maximum_difference_pips: float) -> tuple[str, str]:
    if maximum_difference_pips >= config.FXCM_HIGH_ALERT_PIPS:
        return "HIGH", "DECLINE"
    if maximum_difference_pips >= config.FXCM_MEDIUM_ALERT_PIPS:
        return "MEDIUM", "APPROVE"
    return "LOW", "APPROVE"


def _near_active_setup_level(
    pair: str, result: dict[str, Any], state: dict[str, Any]
) -> bool:
    if state.get("state") == "WATCHING" or not state.get("setup_id"):
        return False
    levels = [state.get("h4_level_price"), state.get("daily_level_price")]
    levels = [float(level) for level in levels if level is not None]
    if not levels:
        return False
    values = [state.get("last_twelve_data_price")]
    for candle_name in ("twelve_data", "fxcm"):
        candle = result.get(candle_name) or {}
        values.extend(candle.get(field) for field in ("open", "high", "low", "close"))
    pip_size = config.pip_size(pair)
    return any(
        value is not None
        and abs(float(value) - level) / pip_size <= config.FXCM_CONFLICT_NEAR_LEVEL_PIPS
        for value in values
        for level in levels
    )


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


def _normalize_history(
    value: Any, fallback_ohlc: Any = None
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"D1": [], "H4": []}
    if isinstance(value, dict):
        for timeframe in result:
            candles = value.get(timeframe)
            if not isinstance(candles, list):
                continue
            for candle in candles:
                normalized = _normalize_candle(candle)
                if normalized:
                    result[timeframe].append(normalized)
    if not any(result.values()) and isinstance(fallback_ohlc, dict):
        for timeframe in result:
            normalized = _normalize_candle(fallback_ohlc.get(timeframe))
            if normalized:
                result[timeframe].append(normalized)
    return result


def _normalize_candle(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    required = ("timestamp", "open", "high", "low", "close")
    if any(field not in value for field in required):
        return None
    try:
        timestamp = _normalize_timestamp(value["timestamp"])
        if not timestamp:
            return None
        return {
            "timestamp": timestamp,
            "open": float(value["open"]),
            "high": float(value["high"]),
            "low": float(value["low"]),
            "close": float(value["close"]),
            "completed": bool(value.get("completed", True)),
        }
    except (TypeError, ValueError):
        return None


def _compare_timeframes(
    pair: str, fxcm: dict[str, Any], primary: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for timeframe in ("D1", "H4"):
        left = fxcm.get(timeframe)
        right = primary.get(timeframe)
        if not left or not right:
            result[timeframe] = {"status": "UNAVAILABLE"}
            continue
        periods = _period_comparison(left, right, timeframe)
        log.info(
            "FXCM OHLC periods for %s %s; FXCM=%s TwelveData=%s",
            pair,
            timeframe,
            periods["fxcm_period"],
            periods["twelve_data_period"],
        )
        if periods["status"] != "SAME_PERIOD":
            result[timeframe] = {
                "status": "BOUNDARY_MISMATCH",
                "period_status": periods["status"],
                "tolerance_pips": config.FXCM_OHLC_TOLERANCE_PIPS,
                "timestamp_difference_seconds": _timestamp_difference_seconds(
                    left.get("timestamp"), right.get("timestamp")
                ),
                "normalized_timestamp_difference_seconds": _normalized_timestamp_difference_seconds(
                    left.get("timestamp"), right.get("timestamp"), timeframe
                ),
                "fxcm_period": periods["fxcm_period"],
                "twelve_data_period": periods["twelve_data_period"],
                "fxcm": left,
                "twelve_data": right,
            }
            log.warning(
                "FXCM OHLC candle-boundary mismatch for %s %s; FXCM=%s TwelveData=%s",
                pair,
                timeframe,
                periods["fxcm_period"],
                periods["twelve_data_period"],
            )
            continue
        differences = {
            field: float(left[field]) - float(right[field])
            for field in ("open", "high", "low", "close")
        }
        tolerance_pips = config.FXCM_OHLC_TOLERANCE_PIPS
        difference_pips = {
            field: abs(difference) / config.pip_size(pair)
            for field, difference in differences.items()
        }
        disagreements = [
            field for field, difference in difference_pips.items()
            if difference > tolerance_pips
        ]
        mismatch_fields = [
            field for field, difference in difference_pips.items()
            if difference > tolerance_pips * config.FXCM_OHLC_MISMATCH_MULTIPLIER
        ]
        if mismatch_fields:
            status = "MISMATCH"
        elif disagreements:
            status = "WARN"
        else:
            status = "OK"
        result[timeframe] = {
            "status": status,
            "period_status": periods["status"],
            "tolerance_pips": tolerance_pips,
            "mismatch_multiplier": config.FXCM_OHLC_MISMATCH_MULTIPLIER,
            "timestamp_difference_seconds": 0,
            "normalized_timestamp_difference_seconds": 0,
            "left_timestamp": left.get("timestamp"),
            "right_timestamp": right.get("timestamp"),
            "fxcm_period": periods["fxcm_period"],
            "twelve_data_period": periods["twelve_data_period"],
            "fxcm": left,
            "twelve_data": right,
            "ohlc_difference": differences,
            "ohlc_difference_pips": difference_pips,
            "disagreement_fields": disagreements,
            "mismatch_fields": mismatch_fields,
        }
        if status in {"WARN", "MISMATCH"}:
            log.warning(
                "FXCM OHLC %s for %s %s; fields=%s tolerance_pips=%s differences_pips=%s",
                status,
                pair,
                timeframe,
                disagreements,
                tolerance_pips,
                difference_pips,
            )
    return result


def _period_comparison(
    fxcm: dict[str, Any], primary: dict[str, Any], timeframe: str
) -> dict[str, Any]:
    duration = timedelta(days=1 if timeframe == "D1" else 4 / 24)
    fxcm_start = _parse_utc_timestamp(fxcm.get("timestamp"))
    primary_start = _parse_utc_timestamp(primary.get("timestamp"))
    fxcm_period = _period_details(fxcm_start, duration)
    primary_period = _period_details(primary_start, duration)
    if not fxcm_start or not primary_start:
        return {
            "status": "INVALID_PERIOD",
            "fxcm_period": fxcm_period,
            "twelve_data_period": primary_period,
        }
    return {
        "status": "SAME_PERIOD" if fxcm_start == primary_start else "DIFFERENT_PERIOD",
        "fxcm_period": fxcm_period,
        "twelve_data_period": primary_period,
    }


def _period_details(start: datetime | None, duration: timedelta) -> dict[str, str | None]:
    if start is None:
        return {"start_utc": None, "end_utc": None}
    return {
        "start_utc": start.isoformat(),
        "end_utc": (start + duration).isoformat(),
    }


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _timestamp_difference_seconds(left: Any, right: Any) -> int | None:
    left_timestamp = _parse_utc_timestamp(left)
    right_timestamp = _parse_utc_timestamp(right)
    if not left_timestamp or not right_timestamp:
        return None
    return int((left_timestamp - right_timestamp).total_seconds())


def _normalized_timestamp_difference_seconds(
    left: Any, right: Any, timeframe: str
) -> int | None:
    left_timestamp = _parse_utc_timestamp(left)
    right_timestamp = _parse_utc_timestamp(right)
    if not left_timestamp or not right_timestamp:
        return None
    if timeframe == "D1" and left_timestamp.hour == 21:
        # FXCM's 21:00 UTC trading-day boundary is different from Twelve Data's
        # 00:00 UTC calendar-day boundary; this is diagnostic only, not equality.
        left_timestamp = left_timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((left_timestamp - right_timestamp).total_seconds())


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
