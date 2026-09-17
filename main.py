"""Entry point: background scanner loop plus FastAPI status server."""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from datetime import datetime, timezone

import uvicorn

import config
from data_service import DataServiceError, TwelveDataClient
from economic_calendar import EconomicCalendarService
from fxcm_validation import FXCMValidationStore
from crypto_validation import CryptoValidationService
from state_manager import StateManager
from strategy import StrategyEngine
from telegram_service import TelegramService
from tradingview_email import TradingViewAlert, TradingViewEmailBridge
from web_server import create_app

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("battoujutsu")

_running = threading.Event()
_states = StateManager()
_client = TwelveDataClient()
_calendar = EconomicCalendarService()
_strategy = StrategyEngine(_states, _calendar)
_twelve_market_lock = threading.Lock()
_twelve_market_data: dict[str, dict] = {}
_fxcm_validation = FXCMValidationStore(
    lambda: _primary_snapshot(), _states.record_event
)
_crypto_validation = CryptoValidationService(
    lambda: _primary_snapshot(),
    lambda source, connected, error: _handle_crypto_provider_status(
        source, connected, error
    ),
)
_telegram = TelegramService(_states, _crypto_validation.snapshot)
_telegram_stop = threading.Event()
_tradingview_email_bridge: TradingViewEmailBridge | None = None


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
            _record_twelve_market_data(pair, daily, h4)
            if not h4.empty:
                _states.update(
                    pair,
                    last_twelve_data_price=float(h4.iloc[-1]["close"]),
                    last_twelve_data_at=datetime.now(timezone.utc).isoformat(),
                )
            result = _strategy.evaluate(pair, daily, h4)
            if result and result.alert:
                sent = _telegram.send_alert(result)
                log.info(
                    "%s FINAL signal %s sent=%s",
                    pair,
                    result.direction,
                    sent,
                )
                if sent:
                    completed = _states.get(pair)
                    _states.reset(
                        pair,
                        reason="setup_completed",
                        details={
                            "alert_key": completed.get("last_alert_key"),
                            "alert_at": completed.get("last_alert_at"),
                        },
                        last_alert_key=completed.get("last_alert_key"),
                        last_alert_at=completed.get("last_alert_at"),
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


def _record_twelve_market_data(pair: str, daily, h4) -> None:
    with _twelve_market_lock:
        _twelve_market_data[pair] = {
            "D1": _frame_candle(daily),
            "H4": _frame_candle(h4),
        }


def _frame_candle(frame) -> dict | None:
    if frame.empty:
        return None
    candle = frame.iloc[-1]
    result = {
        "timestamp": candle["datetime"].isoformat(),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "completed": True,
    }
    if "volume" in candle and candle["volume"] == candle["volume"]:
        result["volume"] = float(candle["volume"])
    return result


def _primary_snapshot() -> dict:
    snapshot = _states.snapshot()
    with _twelve_market_lock:
        snapshot["market_data"] = {
            pair: dict(timeframes) for pair, timeframes in _twelve_market_data.items()
        }
    return snapshot


def _handle_crypto_provider_status(source: str, connected: bool, error: str | None) -> None:
    if source == "binance":
        return
    state = "RECOVERED" if connected else "UNAVAILABLE"
    detail = "public market-data connection restored" if connected else (error or "connection lost")
    
    # Get current prices from validation snapshot
    validation = _crypto_validation.snapshot()
    source_data = validation.get(source, {})
    symbols = source_data.get("symbols", {})
    
    # Add price data if available
    price_info = ""
    if connected and symbols:
        prices = []
        for symbol, data in symbols.items():
            price = data.get("price")
            if price:
                prices.append(f"{symbol}: <code>{price}</code>")
        if prices:
            price_info = "\n" + "\n".join(prices)
    
    _telegram.send_html(
        f"<b>{source.upper()} VALIDATION {state}</b>\n"
        f"{detail}. Twelve Data strategy remains authoritative and unchanged.{price_info}"
    )


def _handle_tradingview_webhook(payload: dict) -> dict:
    pair = str(payload.get("pair", "")).upper().strip()
    event = str(payload.get("event", "")).lower().strip()
    timeframe = str(payload.get("tf", "H4")).upper().strip()
    raw_price = payload.get("tv_price")
    event_time = str(payload.get("time", "")).strip()
    if pair not in config.PAIRS:
        raise ValueError("pair must be one of the configured pairs")
    if event not in {"breakout", "retest", "continuation", "rejection"}:
        raise ValueError("event must be breakout, retest, continuation, or rejection")
    if raw_price is None:
        raise ValueError("tv_price is required")
    try:
        tv_price = float(raw_price)
    except (TypeError, ValueError) as exc:
        raise ValueError("tv_price must be numeric") from exc
    if not math.isfinite(tv_price) or tv_price <= 0:
        raise ValueError("tv_price must be a positive finite number")
    if timeframe not in config.TIMEFRAMES:
        raise ValueError("tf must be D1 or H4")
    if not event_time:
        raise ValueError("time is required")
    try:
        event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00")).isoformat()
    except ValueError as exc:
        raise ValueError("time must be an ISO-8601 timestamp") from exc

    twelve_price: float | None = None
    try:
        candles = _client.fetch_ohlc(pair, timeframe)
        if not candles.empty:
            twelve_price = float(candles.iloc[-1]["close"])
    except Exception as exc:
        log.warning("TradingView comparison unavailable for %s: %s", pair, exc)

    state = _states.get(pair)
    changes = {"h4_level_price": tv_price}
    if event == "breakout":
        changes["h4_breakout_bar_time"] = event_time
    elif event == "retest":
        changes["retest_bar_time"] = event_time
    _states.update(pair, **changes)
    state = _states.get(pair)

    difference = abs(tv_price - twelve_price) if twelve_price is not None else None
    if twelve_price is None:
        diff_text = "N/A"
        large_divergence = False
    elif pair.endswith("USDT"):
        diff_text = f"${difference:.2f} / {difference / tv_price * 100:.2f}%"
        large_divergence = difference > (50 if pair == "BTCUSDT" else 10)
    else:
        pips = difference * (100 if pair.endswith("JPY") else 10000)
        diff_text = f"{pips:.1f} pips"
        large_divergence = pips > 15

    text = (
        f"<b>TRADINGVIEW {pair} {event.upper()} | {timeframe}</b>\n\n"
        f"TradingView: <code>{tv_price}</code>\n"
        f"TwelveData: <code>{twelve_price if twelve_price is not None else 'N/A'}</code>\n"
        f"Diff: {diff_text}\n\n"
        "<b>Using TradingView price as official level.</b>\n"
        f"Setup ID: <code>{state.get('setup_id') or 'N/A'}</code>\n"
        f"Daily Level: <code>{state.get('daily_level_price') or 'N/A'}</code>"
    )
    if large_divergence:
        text += "\n\n<b>WARNING: Large feed divergence - check broker!</b>"
    sent = _telegram.send_html(text)
    log.info(
        "TRADINGVIEW_WEBHOOK pair=%s event=%s tv_price=%s twelve_price=%s diff=%s sent=%s",
        pair,
        event,
        tv_price,
        twelve_price if twelve_price is not None else "N/A",
        diff_text,
        sent,
    )
    return {
        "status": "accepted",
        "pair": pair,
        "event": event,
        "tv_price": tv_price,
        "twelve_price": twelve_price if twelve_price is not None else "N/A",
        "diff": diff_text,
        "setup_id": state.get("setup_id"),
        "telegram_sent": sent,
    }


def _handle_tradingview_email(alert: TradingViewAlert) -> bool:
    state = _states.get(alert.symbol)
    twelve_price = state.get("last_twelve_data_price")
    if twelve_price is None:
        try:
            candles = _client.fetch_ohlc(alert.symbol, "H4")
            if not candles.empty:
                twelve_price = float(candles.iloc[-1]["close"])
        except Exception as exc:
            log.warning("TradingView email comparison unavailable for %s: %s", alert.symbol, exc)

    difference = abs(alert.price - twelve_price) if twelve_price is not None else None
    if twelve_price is None:
        difference_text = "N/A"
    elif alert.symbol.endswith("USDT"):
        difference_text = f"${difference:.2f} / {difference / alert.price * 100:.2f}%"
    else:
        multiplier = 100 if alert.symbol.endswith("JPY") else 10000
        difference_text = f"{difference * multiplier:.1f} pips"

    event = {
        "type": "tradingview_alert",
        "pair": alert.symbol,
        "alert_type": alert.alert_type,
        "direction": alert.direction,
        "tradingview_price": alert.price,
        "twelve_data_price": twelve_price if twelve_price is not None else "N/A",
        "twelve_data_at": state.get("last_twelve_data_at"),
        "difference": difference_text,
        "timeframe": alert.interval,
        "timestamp": alert.timestamp,
        "scanner_state": state.get("state"),
        "daily_setup": state.get("daily_setup"),
        "daily_level_price": state.get("daily_level_price"),
        "h4_level_price": state.get("h4_level_price"),
        "retest_status": state.get("retest_status"),
        "breakout_status": state.get("breakout_status"),
        "setup_id": state.get("setup_id"),
        "warning_type": state.get("warning_type"),
        "warning_acknowledged": state.get("warning_acknowledged"),
    }
    _states.record_event(event)
    _send_pending_events()
    log.info(
        "TradingView email alert processed: %s %s tv=%s twelve=%s diff=%s",
        alert.symbol,
        alert.alert_type,
        alert.price,
        twelve_price if twelve_price is not None else "N/A",
        difference_text,
    )
    return True


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


def _status_snapshot() -> dict:
    fxcm_snapshot = _fxcm_validation.snapshot()
    snapshot = _primary_snapshot()
    snapshot["crypto_validation"] = _crypto_validation.snapshot()
    snapshot["fxcm_validation"] = fxcm_snapshot
    return snapshot


def _handle_fxcm_market_data(payload: dict, secret: str | None) -> dict:
    accepted, message = _fxcm_validation.accept(payload, secret)
    if not accepted:
        raise ValueError(message)
    return {"status": "accepted"}


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
    global _tradingview_email_bridge
    _tradingview_email_bridge = TradingViewEmailBridge(_states, _handle_tradingview_email)
    _tradingview_email_bridge.start()
    _crypto_validation.start()
    if config.TELEGRAM_BOT_TOKEN:
        threading.Thread(
            target=_telegram.poll_updates,
            args=(_handle_telegram_update, _telegram_stop),
            name="telegram-callbacks",
            daemon=True,
        ).start()

    app = create_app(
        _status_snapshot,
        _running.is_set,
        _handle_tradingview_webhook,
        _tradingview_email_bridge.status if _tradingview_email_bridge else None,
        _handle_fxcm_market_data,
    )
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
    _running.clear()
    _telegram_stop.set()
    _crypto_validation.stop()
    if _tradingview_email_bridge:
        _tradingview_email_bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
