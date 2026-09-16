"""Optional public Binance and Coinbase crypto validation feeds."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

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
    def __init__(self, primary_snapshot_provider: Callable[[], dict[str, Any]]) -> None:
        self._primary_snapshot_provider = primary_snapshot_provider
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
            result[source] = source_status
        return result

    def _run_source(self, source: str) -> None:
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._consume(source))
            except Exception as exc:
                self._set_error(source, exc)
                log.warning("[%s] validation feed disconnected: %s", source.title(), exc)
            self._set_connected(source, False)
            self._stop_event.wait(config.CRYPTO_VALIDATION_RECONNECT_SECONDS)

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
            self._set_connected("binance", True)
            connected_at = time.monotonic()
            while not self._stop_event.is_set():
                if time.monotonic() - connected_at >= config.CRYPTO_BINANCE_MAX_CONNECTION_SECONDS:
                    return
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                self._handle_binance_message(raw)

    async def _consume_coinbase(self) -> None:
        definition = _SOURCE_DEFINITIONS["coinbase"]
        async with websockets.connect(
            definition["url"], ping_interval=20, ping_timeout=20, max_queue=1000
        ) as socket:
            products = list(definition["symbols"].values())
            await socket.send(json.dumps({"type": "subscribe", "product_ids": products, "channel": "ticker"}))
            await socket.send(json.dumps({"type": "subscribe", "channel": "heartbeats", "product_ids": products}))
            self._set_connected("coinbase", True)
            while not self._stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=1)
                except asyncio.TimeoutError:
                    continue
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
            for event in payload.get("events", []):
                for ticker in event.get("tickers", []):
                    product = str(ticker.get("product_id", ""))
                    price = float(ticker["price"])
                    event_time = _parse_timestamp(ticker.get("time"))
                    if product in _SOURCE_DEFINITIONS["coinbase"]["symbols"].values() and event_time:
                        self._record("coinbase", product, price, event_time)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            log.warning("[Coinbase] ignored malformed market-data message")

    def _record(self, source: str, product: str, price: float, timestamp: datetime) -> None:
        with self._lock:
            self._status[source]["connected"] = True
            self._status[source]["last_message_at"] = datetime.now(timezone.utc).isoformat()
            self._status[source]["last_error"] = None
            self._status[source]["symbols"][product].update(
                price=price,
                timestamp=timestamp.isoformat(),
            )

    def _set_connected(self, source: str, connected: bool) -> None:
        with self._lock:
            self._status[source]["connected"] = connected

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
