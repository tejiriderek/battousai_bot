"""Optional public Binance and Coinbase crypto validation feeds."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import requests
import websockets

import config

log = logging.getLogger(__name__)

_SOURCE_DEFINITIONS = {
    "binance": {
        "url": config.CRYPTO_BINANCE_WS_URL,
        "symbols": {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT"},
        "stale_seconds": config.CRYPTO_BINANCE_STALE_SECONDS,
        "threshold_pct": config.CRYPTO_BINANCE_MAX_PRICE_DISCREPANCY_PCT,
    },
    "coinbase": {
        "url": config.CRYPTO_COINBASE_WS_URL,
        "symbols": {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"},
        "stale_seconds": config.CRYPTO_COINBASE_STALE_SECONDS,
        "threshold_pct": config.CRYPTO_COINBASE_MAX_PRICE_DISCREPANCY_PCT,
    },
}


class CryptoValidationService:
    def __init__(
        self,
        primary_snapshot_provider: Callable[[], dict[str, Any]],
        connection_callback: Callable[[str, bool, str | None], None] | None = None,
    ) -> None:
        self._primary_snapshot_provider = primary_snapshot_provider
        self._connection_callback = connection_callback
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._status = {
            source: {
                "enabled": _enabled(source),
                "connected": False,
                "last_message_at": None,
                "last_error": None,
                "symbols": {
                    product: {
                        "price": None,
                        "timestamp": None,
                        "ohlc": {"D1": None, "H4": None},
                        "history": {"D1": [], "H4": []},
                    }
                    for product in definition["symbols"].values()
                },
            }
            for source, definition in _SOURCE_DEFINITIONS.items()
        }

    def start(self) -> None:
        for source in _SOURCE_DEFINITIONS:
            if not _enabled(source):
                log.info("[%s] crypto validation disabled", source.title())
                continue
            thread = threading.Thread(
                target=self._run_source,
                args=(source,),
                name=f"crypto-{source}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()
            candle_thread = threading.Thread(
                target=self._run_candle_source,
                args=(source,),
                name=f"crypto-{source}-candles",
                daemon=True,
            )
            self._threads.append(candle_thread)
            candle_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=5)

    def snapshot(self) -> dict[str, Any]:
        primary = self._primary_snapshot_provider()
        pairs = primary.get("pairs", {})
        now = time.time()
        result: dict[str, Any] = {}
        with self._lock:
            status = json.loads(json.dumps(self._status))

        for source, definition in _SOURCE_DEFINITIONS.items():
            source_status = status[source]
            if not source_status["enabled"]:
                source_status["stale"] = False
                for symbol_status in source_status["symbols"].values():
                    symbol_status["validation_status"] = "DISABLED"
                result[source] = source_status
                continue
            source_status["stale"] = _is_source_stale(
                source_status.get("last_message_at"), definition["stale_seconds"], now
            )
            for pair, product in definition["symbols"].items():
                symbol_status = source_status["symbols"][product]
                primary_price = pairs.get(pair, {}).get("last_twelve_data_price")
                price = symbol_status.get("price")
                difference = None
                validation_status = "NO_PRIMARY"
                if source_status["stale"]:
                    validation_status = "STALE"
                elif not source_status["connected"]:
                    validation_status = "DISCONNECTED"
                elif price is not None and primary_price is not None:
                    difference = abs(float(price) - float(primary_price)) / float(primary_price) * 100
                    validation_status = (
                        "PRICE_DISCREPANCY_WARNING"
                        if difference > definition["threshold_pct"]
                        else "OK"
                    )
                symbol_status["price_difference_pct"] = difference
                symbol_status["validation_status"] = validation_status
                symbol_status["ohlc_comparison"] = _compare_ohlc(
                    symbol_status.get("ohlc", {}),
                    primary.get("market_data", {}).get(pair, {}),
                )
            result[source] = source_status
        return result

    def _run_source(self, source: str) -> None:
        while not self._stop_event.is_set():
            error_name = None
            try:
                asyncio.run(self._consume(source))
            except Exception as exc:
                error_name = type(exc).__name__
                self._set_error(source, exc)
                log.warning("[%s] validation feed disconnected: %s", source.title(), exc)
            self._set_connected(source, False, error_name)
            self._stop_event.wait(config.CRYPTO_VALIDATION_RECONNECT_SECONDS)

    def _run_candle_source(self, source: str) -> None:
        while not self._stop_event.is_set():
            self._safe_refresh_ohlc(source)
            self._stop_event.wait(config.CRYPTO_VALIDATION_CANDLE_REFRESH_SECONDS)

    async def _consume(self, source: str) -> None:
        if source == "binance":
            await self._consume_binance()
        else:
            await self._consume_coinbase()

    async def _consume_binance(self) -> None:
        definition = _SOURCE_DEFINITIONS["binance"]
        async with websockets.connect(
            definition["url"], ping_interval=20, ping_timeout=20, max_queue=1000
        ) as socket:
            await socket.send(
                json.dumps(
                    {
                        "method": "SUBSCRIBE",
                        "params": [f"{symbol.lower()}@ticker" for symbol in definition["symbols"].values()],
                        "id": int(time.time()),
                    }
                )
            )
            connected_at = time.monotonic()
            next_candle_refresh = 0.0
            while not self._stop_event.is_set():
                if time.monotonic() - connected_at >= config.CRYPTO_BINANCE_MAX_CONNECTION_SECONDS:
                    return
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=1)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    self._handle_binance_message(raw)

    async def _consume_coinbase(self) -> None:
        definition = _SOURCE_DEFINITIONS["coinbase"]
        async with websockets.connect(
            definition["url"], ping_interval=20, ping_timeout=20, max_queue=1000
        ) as socket:
            products = list(definition["symbols"].values())
            # Subscribe to ticker channel with correct format for Advanced Trade API
            subscribe_msg = {
                "type": "subscribe",
                "channel": "ticker",
                "product_ids": products
            }
            await socket.send(json.dumps(subscribe_msg))
            log.info("[Coinbase] subscribed to ticker channel for: %s", products)
            while not self._stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=1)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    self._handle_coinbase_message(raw)

    def _handle_binance_message(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
            symbol = str(payload.get("s", ""))
            price = float(payload["c"])
            event_time = datetime.fromtimestamp(int(payload["E"]) / 1000, timezone.utc)
            if symbol in _SOURCE_DEFINITIONS["binance"]["symbols"].values():
                self._record("binance", symbol, price, event_time)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            log.warning("[Binance] ignored malformed market-data message")

    def _handle_coinbase_message(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
            # Handle new Advanced Trade API format
            if payload.get("channel") == "ticker":
                for ticker in payload.get("tickers", []):
                    product = str(ticker.get("product_id", ""))
                    price = float(ticker.get("price", 0))
                    event_time = _parse_timestamp(ticker.get("time"))
                    if product in _SOURCE_DEFINITIONS["coinbase"]["symbols"].values() and price > 0:
                        self._record("coinbase", product, price, event_time or datetime.now(timezone.utc))
            # Handle legacy format (if any)
            elif "events" in payload:
                for event in payload.get("events", []):
                    for ticker in event.get("tickers", []):
                        product = str(ticker.get("product_id", ""))
                        price = float(ticker.get("price", 0))
                        event_time = _parse_timestamp(ticker.get("time"))
                        if product in _SOURCE_DEFINITIONS["coinbase"]["symbols"].values() and price > 0:
                            self._record("coinbase", product, price, event_time or datetime.now(timezone.utc))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            log.warning("[Coinbase] ignored malformed market-data message: %s", exc)

    def _record(self, source: str, product: str, price: float, timestamp: datetime) -> None:
        self._set_connected(source, True)
        with self._lock:
            self._status[source]["last_message_at"] = datetime.now(timezone.utc).isoformat()
            self._status[source]["last_error"] = None
            self._status[source]["symbols"][product].update(
                price=price,
                timestamp=timestamp.isoformat(),
            )

    def _refresh_ohlc(self, source: str) -> None:
        definition = _SOURCE_DEFINITIONS[source]
        if source == "binance":
            for product in definition["symbols"].values():
                for timeframe, interval in (("D1", "1d"), ("H4", "4h")):
                    response = requests.get(
                        f"{config.CRYPTO_BINANCE_REST_URL}/api/v3/klines",
                        params={
                            "symbol": product,
                            "interval": interval,
                            "limit": config.LOOKBACK_CANDLES,
                        },
                        timeout=config.CRYPTO_VALIDATION_HTTP_TIMEOUT_SECONDS,
                    )
                    response.raise_for_status()
                    rows = response.json()
                    history = _completed_binance_candles(rows)
                    if history:
                        self._record_ohlc_history(source, product, timeframe, history)
            return

        granularity = {"D1": 86400, "H4": 3600}
        for product in definition["symbols"].values():
            for timeframe, seconds in granularity.items():
                response = requests.get(
                    f"{config.CRYPTO_COINBASE_REST_URL}/products/{product}/candles",
                    params={
                        "granularity": seconds,
                        "limit": min(300, config.LOOKBACK_CANDLES),
                    },
                    timeout=config.CRYPTO_VALIDATION_HTTP_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                rows = response.json()
                history = (
                    _completed_coinbase_candles(rows, seconds)
                    if timeframe == "D1"
                    else _aggregate_coinbase_h4_history(rows)
                )
                if history:
                    self._record_ohlc_history(source, product, timeframe, history)

    def _safe_refresh_ohlc(self, source: str) -> None:
        try:
            self._refresh_ohlc(source)
        except Exception as exc:
            self._set_error(source, exc)
            log.warning("[%s] candle refresh failed: %s", source.title(), exc)

    def _record_ohlc(
        self, source: str, product: str, timeframe: str, candle: dict[str, Any]
    ) -> None:
        with self._lock:
            self._status[source]["symbols"][product]["ohlc"][timeframe] = candle
            self._status[source]["last_error"] = None

    def _record_ohlc_history(
        self,
        source: str,
        product: str,
        timeframe: str,
        history: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self._status[source]["symbols"][product]["history"][timeframe] = history
            self._status[source]["symbols"][product]["ohlc"][timeframe] = history[-1]
            self._status[source]["last_error"] = None

    def _set_connected(self, source: str, connected: bool, error: str | None = None) -> None:
        changed = False
        with self._lock:
            changed = self._status[source]["connected"] != connected
            self._status[source]["connected"] = connected
            if error:
                self._status[source]["last_error"] = error
        if changed and self._connection_callback:
            self._connection_callback(source, connected, error)

    def _set_error(self, source: str, error: Exception) -> None:
        with self._lock:
            self._status[source]["last_error"] = type(error).__name__


def _enabled(source: str) -> bool:
    return (
        config.CRYPTO_BINANCE_VALIDATION_ENABLED
        if source == "binance"
        else config.CRYPTO_COINBASE_VALIDATION_ENABLED
    )


def _is_source_stale(timestamp: str | None, stale_seconds: int, now: float) -> bool:
    if not timestamp:
        return True
    try:
        age = now - datetime.fromisoformat(timestamp).timestamp()
    except (TypeError, ValueError):
        return True
    return age > stale_seconds


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _latest_completed_binance_candle(rows: list[list[Any]]) -> dict[str, Any] | None:
    now_ms = int(time.time() * 1000)
    completed = [row for row in rows if len(row) >= 7 and int(row[6]) <= now_ms]
    if not completed:
        return None
    row = max(completed, key=lambda item: int(item[0]))
    return {
        "timestamp": datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).isoformat(),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "completed": True,
    }


def _completed_binance_candles(rows: list[list[Any]]) -> list[dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    completed = [row for row in rows if len(row) >= 7 and int(row[6]) <= now_ms]
    result = []
    for row in sorted(completed, key=lambda item: int(item[0])):
        result.append(
            {
                "timestamp": datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc).isoformat(),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "completed": True,
            }
        )
    return result


def _latest_completed_coinbase_candle(
    rows: list[list[Any]], granularity: int
) -> dict[str, Any] | None:
    now = int(time.time())
    completed = [row for row in rows if len(row) >= 6 and int(row[0]) + granularity <= now]
    if not completed:
        return None
    row = max(completed, key=lambda item: int(item[0]))
    return {
        "timestamp": datetime.fromtimestamp(int(row[0]), timezone.utc).isoformat(),
        "open": float(row[3]),
        "high": float(row[2]),
        "low": float(row[1]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "completed": True,
    }


def _completed_coinbase_candles(
    rows: list[list[Any]], granularity: int
) -> list[dict[str, Any]]:
    now = int(time.time())
    completed = [row for row in rows if len(row) >= 6 and int(row[0]) + granularity <= now]
    result = []
    for row in sorted(completed, key=lambda item: int(item[0])):
        result.append(
            {
                "timestamp": datetime.fromtimestamp(int(row[0]), timezone.utc).isoformat(),
                "open": float(row[3]),
                "high": float(row[2]),
                "low": float(row[1]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "completed": True,
            }
        )
    return result


def _aggregate_coinbase_h4(rows: list[list[Any]]) -> dict[str, Any] | None:
    now = int(time.time())
    hours = [
        row for row in rows
        if len(row) >= 6 and int(row[0]) + 3600 <= now
    ]
    if not hours:
        return None
    hours.sort(key=lambda item: int(item[0]))
    groups: dict[int, list[list[Any]]] = {}
    for row in hours:
        start = (int(row[0]) // 14400) * 14400
        groups.setdefault(start, []).append(row)
    complete_groups = [rows_for_group for rows_for_group in groups.values() if len(rows_for_group) == 4]
    if not complete_groups:
        return None
    group = max(complete_groups, key=lambda items: int(items[0][0]))
    group.sort(key=lambda item: int(item[0]))
    return {
        "timestamp": datetime.fromtimestamp(int(group[0][0]), timezone.utc).isoformat(),
        "open": float(group[0][3]),
        "high": max(float(row[2]) for row in group),
        "low": min(float(row[1]) for row in group),
        "close": float(group[-1][4]),
        "volume": sum(float(row[5]) for row in group),
        "completed": True,
    }


def _aggregate_coinbase_h4_history(rows: list[list[Any]]) -> list[dict[str, Any]]:
    now = int(time.time())
    hours = [row for row in rows if len(row) >= 6 and int(row[0]) + 3600 <= now]
    groups: dict[int, list[list[Any]]] = {}
    for row in hours:
        start = (int(row[0]) // 14400) * 14400
        groups.setdefault(start, []).append(row)
    result = []
    for start, group in sorted(groups.items()):
        if len(group) != 4:
            continue
        group.sort(key=lambda item: int(item[0]))
        result.append(
            {
                "timestamp": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                "open": float(group[0][3]),
                "high": max(float(row[2]) for row in group),
                "low": min(float(row[1]) for row in group),
                "close": float(group[-1][4]),
                "volume": sum(float(row[5]) for row in group),
                "completed": True,
            }
        )
    return result


def _compare_ohlc(
    provider_ohlc: dict[str, Any], primary_ohlc: dict[str, Any]
) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for timeframe in ("D1", "H4"):
        provider = provider_ohlc.get(timeframe)
        primary = primary_ohlc.get(timeframe)
        if not provider or not primary:
            comparison[timeframe] = {"status": "UNAVAILABLE"}
            continue
        differences = {
            field: float(provider[field]) - float(primary[field])
            for field in ("open", "high", "low", "close")
        }
        provider_time = datetime.fromisoformat(provider["timestamp"])
        primary_time = datetime.fromisoformat(primary["timestamp"])
        comparison[timeframe] = {
            "status": "COMPARED",
            "timestamp_difference_seconds": int(
                (provider_time - primary_time).total_seconds()
            ),
            "ohlc_difference": differences,
        }
    return comparison
