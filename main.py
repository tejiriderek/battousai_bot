"""Entry point: background scanner loop plus FastAPI status server."""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone

import uvicorn

import config
from data_service import DataServiceError, TwelveDataClient
from state_manager import StateManager
from strategy import StrategyEngine
from telegram_service import TelegramService
from web_server import create_app

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("battoujutsu")

_running = threading.Event()
_states = StateManager()
_telegram = TelegramService()
_client = TwelveDataClient()
_strategy = StrategyEngine(_states)


def scanner_loop() -> None:
    _running.set()
    _states.set_meta(scanner_running=True)
    log.info("Scanner started; interval %ss", config.SCAN_INTERVAL_SECONDS)
    while _running.is_set():
        cycle_started = time.monotonic()
        try:
            _scan_once()
        except Exception:
            log.exception("Scan cycle crashed; continuing")
        _states.set_meta(
            scanner_running=True,
            last_scan_at=datetime.now(timezone.utc).isoformat(),
        )
        elapsed = time.monotonic() - cycle_started
        sleep_for = max(config.SCAN_INTERVAL_SECONDS - elapsed, 5.0)
        _sleep(sleep_for)


def _scan_once() -> None:
    if not config.TWELVE_DATA_API_KEY:
        log.error("TWELVE_DATA_API_KEY is empty; skipping cycle")
        return
    for pair in config.PAIRS:
        if not _running.is_set():
            return
        try:
            daily = _client.fetch_ohlc(pair, "D1")
            h4 = _client.fetch_ohlc(pair, "H4")
            result = _strategy.evaluate(pair, daily, h4)
            if result and result.alert:
                sent = _telegram.send_alert(result)
                log.info(
                    "%s FINAL signal %s sent=%s",
                    pair,
                    result.direction,
                    sent,
                )
        except DataServiceError as exc:
            log.warning("%s data error: %s", pair, exc)
        except Exception:
            log.exception("Unhandled error scanning %s", pair)


def _sleep(seconds: float) -> None:
    end = time.monotonic() + seconds
    while _running.is_set() and time.monotonic() < end:
        time.sleep(min(0.5, end - time.monotonic()))


def main() -> int:
    missing = [
        name
        for name, value in (
            ("TWELVE_DATA_API_KEY", config.TWELVE_DATA_API_KEY),
            ("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", config.TELEGRAM_CHAT_ID),
        )
        if not value
    ]
    if missing:
        log.warning("Missing environment variables: %s", ", ".join(missing))

    worker = threading.Thread(target=scanner_loop, name="scanner", daemon=True)
    worker.start()

    app = create_app(_states.snapshot, _running.is_set)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
    _running.clear()
    return 0


if __name__ == "__main__":
    sys.exit(main())
