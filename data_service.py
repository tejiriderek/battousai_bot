"""Twelve Data OHLC client with throttle, backoff, and candle validation."""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import requests

import config

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("datetime", "open", "high", "low", "close")


class DataServiceError(RuntimeError):
    pass


class TwelveDataClient:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._last_request_at = 0.0

    def fetch_ohlc(self, pair: str, timeframe: str) -> pd.DataFrame:
        symbol = config.TWELVE_DATA_SYMBOLS[pair]
        interval = config.TIMEFRAMES[timeframe]
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": config.LOOKBACK_CANDLES,
            "apikey": config.TWELVE_DATA_API_KEY,
            "format": "JSON",
            "timezone": "UTC",
        }
        payload = self._request("/time_series", params)
        frame = self._to_dataframe(payload, pair, timeframe)
        frame = validate_ohlc(frame, pair, timeframe)
        return drop_incomplete_candle(frame, timeframe)

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not config.TWELVE_DATA_API_KEY:
            raise DataServiceError("TWELVE_DATA_API_KEY is not set")

        url = f"{config.TWELVE_DATA_BASE_URL}{path}"
        last_error: Exception | None = None

        for attempt in range(config.API_MAX_RETRIES):
            self._throttle()
            try:
                response = self._session.get(
                    url, params=params, timeout=config.API_TIMEOUT_SECONDS
                )
            except requests.RequestException as exc:
                last_error = exc
                wait = _backoff(attempt)
                log.warning("Twelve Data network error (%r); retry in %.1fs", exc, wait)
                time.sleep(wait)
                continue

            if response.status_code == 429:
                wait = _retry_after(response, attempt)
                log.warning("Twelve Data rate limited; backing off %.1fs", wait)
                time.sleep(wait)
                last_error = DataServiceError("HTTP 429")
                continue

            if response.status_code >= 500:
                wait = _backoff(attempt)
                log.warning(
                    "Twelve Data HTTP %s; retry in %.1fs", response.status_code, wait
                )
                time.sleep(wait)
                last_error = DataServiceError(f"HTTP {response.status_code}")
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                raise DataServiceError("Twelve Data returned non-JSON") from exc

            status = str(payload.get("status", "")).lower()
            message = str(payload.get("message", "")).lower()
            if response.status_code != 200 or status == "error":
                combined = f"{payload.get('message', response.text)}"
                if "limit" in message or "credits" in message or response.status_code == 429:
                    wait = _backoff(attempt, floor=15.0)
                    log.warning("Twelve Data credit/limit error (%s); wait %.1fs", combined, wait)
                    time.sleep(wait)
                    last_error = DataServiceError(combined)
                    continue
                raise DataServiceError(combined or f"HTTP {response.status_code}")

            return payload

        raise DataServiceError(f"exhausted retries: {last_error}")

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = config.REQUEST_GAP_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _to_dataframe(self, payload: dict[str, Any], pair: str, timeframe: str) -> pd.DataFrame:
        values = payload.get("values")
        if not isinstance(values, list) or not values:
            raise DataServiceError(f"no OHLC values for {pair} {timeframe}")

        frame = pd.DataFrame(values)
        missing = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            raise DataServiceError(f"{pair} {timeframe} missing columns: {missing}")

        for col in ("open", "high", "low", "close"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if "volume" in frame.columns:
            frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")

        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
        frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        frame = frame.reset_index(drop=True)
        return frame


def validate_ohlc(frame: pd.DataFrame, pair: str, timeframe: str) -> pd.DataFrame:
    if len(frame) < config.MIN_CANDLES:
        raise DataServiceError(
            f"{pair} {timeframe} has {len(frame)} candles; need {config.MIN_CANDLES}"
        )

    high_ok = frame["high"] >= frame[["open", "close"]].max(axis=1) - 1e-12
    low_ok = frame["low"] <= frame[["open", "close"]].min(axis=1) + 1e-12
    range_ok = frame["high"] >= frame["low"]
    positive = (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
    valid_mask = high_ok & low_ok & range_ok & positive
    dropped = int((~valid_mask).sum())
    if dropped:
        log.warning("Dropped %d anomalous %s %s candles", dropped, pair, timeframe)
        frame = frame.loc[valid_mask].reset_index(drop=True)

    if len(frame) < config.MIN_CANDLES:
        raise DataServiceError(f"{pair} {timeframe} too few valid candles after OHLC checks")

    deltas = frame["datetime"].diff().dropna()
    if deltas.empty:
        return frame
    median = deltas.median()
    if median.total_seconds() <= 0:
        return frame
    large_gaps = deltas > median * config.OHLC_GAP_TOLERANCE
    if large_gaps.any():
        log.warning(
            "%s %s has %d unusually large candle gaps",
            pair,
            timeframe,
            int(large_gaps.sum()),
        )
    return frame


def drop_incomplete_candle(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Never evaluate the still-forming bar."""
    if frame.empty:
        return frame
    interval = pd.Timedelta(hours=4) if timeframe == "H4" else pd.Timedelta(days=1)
    last = frame.iloc[-1]
    period_end = last["datetime"] + interval
    now = pd.Timestamp.now(tz="UTC")
    if now < period_end:
        return frame.iloc[:-1].reset_index(drop=True)
    return frame


def _backoff(attempt: int, floor: float = 0.5) -> float:
    return min(60.0, floor * (2**attempt))


def _retry_after(response: requests.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(float(header), 1.0)
        except ValueError:
            pass
    return _backoff(attempt, floor=15.0)
