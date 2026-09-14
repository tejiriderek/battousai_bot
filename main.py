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
from economic_calendar import EconomicCalendarService
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
_telegram = TelegramService(_states)
_client = TwelveDataClient()
_calendar = EconomicCalendarService()
_strategy = StrategyEngine(_states, _calendar)
_telegram_stop = threading.Event()


def scanner_loop() -> None:
    _running.set()
    _states.set_meta(scanner_running=True)
    log.info("Scanner started; interval %ss", config.SCAN_INTERVAL_SECONDS)
    while _running.is_set():
        cycle_started = time.monotonic()
        try:
            _calendar.refresh_calendar()
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


def confirmation_reminder_loop() -> None:
    while not _telegram_stop.is_set():
        if _running.is_set():
            _check_warning_timeouts()
        _telegram_stop.wait(30.0)


def _check_warning_timeouts() -> None:
    """Re-prompt unresolved confirmations without changing their decision."""
    for pair in config.PAIRS:
        try:
            current = _states.get(pair)
            warning_type = current.get("warning_type")
            setup_id = current.get("setup_id")
            if (
                not warning_type
                or not setup_id
                or current.get("state") == "WATCHING"
                or current.get("warning_acknowledged")
            ):
                continue
            warning_sent_at = current.get("warning_sent_at")
            if warning_sent_at and not _states.should_reprompt_warning(
                pair, timeout_minutes=config.CONFIRMATION_REPROMPT_MINUTES
            ):
                continue

            prompt_count = int(current.get("confirmation_prompt_count", 0))
            if prompt_count >= config.CONFIRMATION_MAX_PROMPTS:
                applied = _states.apply_warning_decision(
                    pair, setup_id, warning_type, "yes"
                )
                if applied:
                    _send_pending_events()
                    log.info(
                        "Auto-approved %s confirmation for %s after %d prompts",
                        warning_type,
                        pair,
                        prompt_count,
                    )
                continue

            event = _confirmation_event(pair, current, warning_type, setup_id)
            _states.record_event(event)
            _send_pending_events()
            log.info("Sent confirmation prompt for %s warning on %s", warning_type, pair)
        except Exception:
            log.exception("Error checking warning timeout for %s", pair)


def _confirmation_event(
    pair: str, state: dict, warning_type: str, setup_id: str
) -> dict:
    if warning_type in {"retest", "second_chance"}:
        return {
            "type": "rule_decision",
            "pair": pair,
            "setup_id": setup_id,
            "warning_type": warning_type,
            "state": state.get("state"),
        }
    if warning_type == "gap":
        return {
            "type": "weekend_gap",
            "pair": pair,
            "setup_id": setup_id,
            "active_setup": True,
            "gap_pips": state.get("weekend_gap_pips", 0),
            "friday_close": "N/A",
            "monday_open": "N/A",
        }
    if warning_type == "aging":
        return {
            "type": "aging_warning",
            "pair": pair,
            "setup_id": setup_id,
            "bars": state.get("h4_bars_since_breakout", 0),
        }
    return {
        "type": "news_warning",
        "pair": pair,
        "setup_id": setup_id,
        "event_name": "Unknown",
        "currency": "Unknown",
        "event_time_utc": None,
        "state": state.get("state"),
    }


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
            _send_pending_events()
        except DataServiceError as exc:
            log.warning("%s data error: %s", pair, exc)
            if "HTTP 429" in str(exc) or "credit/limit error" in str(exc):
                log.warning("Rate limit detected; stopping this scan cycle")
                return
        except Exception:
            log.exception("Unhandled error scanning %s", pair)
    _send_pending_events()


def _send_pending_events() -> None:
    for event in _states.drain_events():
        try:
            _telegram.send_event(event)
        except Exception:
            log.exception("Unable to deliver Telegram event: %s", event.get("type"))


def _handle_telegram_update(update: dict) -> None:
    callback = update.get("callback_query") or {}
    callback_id = callback.get("id")
    data = str(callback.get("data", ""))
    sender_id = str((callback.get("from") or {}).get("id", ""))
    chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
    if callback_id:
        _telegram.answer_callback(callback_id, "Checking setup...")
    if config.TELEGRAM_AUTHORIZED_USER_ID:
        authorized = sender_id == config.TELEGRAM_AUTHORIZED_USER_ID
    else:
        authorized = bool(config.TELEGRAM_CHAT_ID) and chat_id == config.TELEGRAM_CHAT_ID
    if not authorized:
        log.warning("Unauthorized Telegram callback rejected: user=%s", sender_id)
        if callback_id:
            _telegram.answer_callback(callback_id, "Unauthorized", show_alert=True)
        return
    parts = data.split("|")
    if len(parts) != 5 or parts[0] != "decision":
        return
    _, decision, warning_type, pair, setup_id = parts
    applied = _states.apply_warning_decision(pair, setup_id, warning_type, decision)
    if callback_id:
        _telegram.answer_callback(
            callback_id,
            "Decision saved" if applied else "This setup is no longer active",
            show_alert=not applied,
        )
    _send_pending_events()


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
    threading.Thread(
        target=confirmation_reminder_loop,
        name="confirmation-reminders",
        daemon=True,
    ).start()
    if config.TELEGRAM_BOT_TOKEN:
        threading.Thread(
            target=_telegram.poll_updates,
            args=(_handle_telegram_update, _telegram_stop),
            name="telegram-callbacks",
            daemon=True,
        ).start()

    app = create_app(_states.snapshot, _running.is_set)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
    _running.clear()
    _telegram_stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
