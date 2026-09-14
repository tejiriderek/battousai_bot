"""Telegram alerts via Bot API sendMessage (same contract as the Laravel/JS client)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import requests

import config
from strategy import ScanResult

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramService:
    def send_alert(self, result: ScanResult) -> bool:
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            log.error("Telegram token or chat id missing; alert not sent")
            return False
        return self.send_html(format_alert(result))

    def send_html(self, text: str, reply_markup: dict[str, Any] | None = None) -> bool:
        url = f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            response = requests.post(url, json=payload, timeout=15)
            try:
                data = response.json()
            except ValueError:
                data = {"ok": False, "description": response.text}
            if not response.ok or not data.get("ok"):
                log.error("Telegram send failed: %s", data)
                return False
            return True
        except requests.RequestException as exc:
            log.error("Telegram unreachable: %s", exc)
            return False

    def send_event(self, event: dict[str, Any]) -> bool:
        event_type = event.get("type")
        if event_type == "setup_invalidated":
            pair = str(event["pair"])
            previous_state = str(event.get("previous_state", "UNKNOWN"))
            reason = str(event.get("reason", "unknown"))
            details = event.get("details", {})
            pair_state = event.get("pair_state", {})
            
            heading = "SETUP ENDED" if reason.startswith("user_ended_") else "SETUP INVALIDATED"
            
            # Build detailed state information
            state_details = []
            if pair_state.get("direction"):
                state_details.append(f"Direction: {_html(pair_state['direction'])}")
            if pair_state.get("daily_setup"):
                state_details.append(f"Daily Setup: {_html(pair_state['daily_setup'])}")
            if pair_state.get("daily_level_type"):
                state_details.append(f"Daily Level Type: {_html(pair_state['daily_level_type'])}")
            if pair_state.get("daily_level_price"):
                state_details.append(f"Daily Level: <code>{pair_state['daily_level_price']:.5f}</code>".rstrip("0").rstrip("."))
            if pair_state.get("h4_level_type"):
                state_details.append(f"H4 Level Type: {_html(pair_state['h4_level_type'])}")
            if pair_state.get("h4_level_price"):
                state_details.append(f"H4 Level: <code>{pair_state['h4_level_price']:.5f}</code>".rstrip("0").rstrip("."))
            if pair_state.get("rejection_status") and pair_state["rejection_status"] != "NONE":
                state_details.append(f"Rejection: {_html(pair_state['rejection_status'])}")
            if pair_state.get("breakout_status") and pair_state["breakout_status"] != "NONE":
                state_details.append(f"Breakout: {_html(pair_state['breakout_status'])}")
            if pair_state.get("retest_status") and pair_state["retest_status"] != "NONE":
                state_details.append(f"Retest: {_html(pair_state['retest_status'])}")
            if pair_state.get("continuation_status") and pair_state["continuation_status"] != "NONE":
                state_details.append(f"Continuation: {_html(pair_state['continuation_status'])}")
            if pair_state.get("h4_bars_since_breakout"):
                state_details.append(f"Bars since breakout: {pair_state['h4_bars_since_breakout']}")
            if pair_state.get("setup_id"):
                state_details.append(f"Setup ID: <code>{pair_state['setup_id']}</code>")
            
            state_text = "\n".join(state_details) if state_details else "No active setup details"
            
            text = (
                f"<b>{heading}: {pair}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Previous state: {_html(previous_state)}\n"
                f"Reason: {_html(reason)}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{state_text}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Details: {_html(str(details) if details else 'N/A')}"
            )
            return self.send_html(text)

        if event_type == "weekend_gap":
            pair = str(event["pair"])
            active = bool(event.get("active_setup"))
            text = (
                f"<b>⚠️ WEEKEND GAP DETECTED: {pair}</b>\n"
                f"Gap: {float(event['gap_pips']):.1f} pips\n"
                f"Friday close: <code>{event['friday_close']}</code>\n"
                f"Monday open: <code>{event['monday_open']}</code>\n"
                "The gap does not count as a breakout.\n"
                + ("Existing setup remains active and will be monitored." if active else "No active setup was changed.")
            )
            markup = None
            if active and event.get("setup_id") and config.TELEGRAM_AUTHORIZED_USER_ID:
                markup = _decision_markup("gap", pair, str(event["setup_id"]))
            return self.send_html(text, markup)

        if event_type in {"aging_warning", "news_warning"}:
            pair = str(event["pair"])
            warning_type = "aging" if event_type == "aging_warning" else "news"
            if warning_type == "aging":
                text = (
                    f"<b>⏳ SETUP AGING: {pair}</b>\n"
                    f"This setup has been waiting for {event['bars']} H4 candles.\n"
                    "Age alone will not invalidate it."
                )
            else:
                text = (
                    f"<b>📰 HIGH-IMPACT NEWS WARNING: {pair}</b>\n"
                    f"Event: {_html(event.get('event_name', 'Unknown'))}\n"
                    f"Currency: {_html(event.get('currency', 'Unknown'))}\n"
                    f"Time: {_html(event.get('event_time_utc') or event.get('event_date_utc', 'unknown'))}\n"
                    f"Current state: {_html(event.get('state', 'unknown'))}"
                )
            markup = None
            if event.get("setup_id") and config.TELEGRAM_AUTHORIZED_USER_ID:
                markup = _decision_markup(warning_type, pair, str(event["setup_id"]))
            return self.send_html(text, markup)

        if event_type == "state_transition":
            if event.get("to_state") == "ALERT_SENT":
                return True
            pair = str(event.get("pair"))
            to_state = str(event.get("to_state"))
            reason = str(event.get("reason") or "state_transition")
            if to_state == "WATCHING":
                return True  # Already handled by setup_invalidated event
            else:
                labels = {
                    "DAILY_REJECTION_DETECTED": "🟢 DAILY STAGE PASSED",
                    "DAILY_BREAKOUT_CONFIRMED": "🟢 DAILY STAGE PASSED",
                    "H4_WAITING": "🟢 H4 STAGE PASSED",
                    "WAITING_FOR_RETEST": "⏳ WAITING FOR RETEST",
                    "RETEST_CONFIRMED": "🟢 RETEST CONFIRMED",
                    "CONTINUATION_CONFIRMED": "🟢 CONTINUATION CONFIRMED",
                }
                text = f"<b>{_html(labels.get(to_state, to_state))}: {pair}</b>"
            return self.send_html(text)

        if event_type == "warning_decision":
            decision = str(event.get("decision", "")).upper()
            pair = str(event.get("pair"))
            if decision == "YES":
                text = f"<b>{pair} - CONTINUE</b>\nThe {event.get('warning_type')} warning was accepted for this setup."
            else:
                text = f"<b>{pair} - SETUP ENDED</b>\nYou chose not to continue after the {event.get('warning_type')} warning."
            return self.send_html(text)

        return True

    def answer_callback(self, callback_id: str, text: str, show_alert: bool = False) -> bool:
        try:
            response = requests.post(
                f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text, "show_alert": show_alert},
                timeout=15,
            )
            return response.ok
        except requests.RequestException as exc:
            log.warning("Telegram callback response failed: %s", exc)
            return False

    def poll_updates(
        self,
        handler: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
    ) -> None:
        """Poll callbacks without making the scanner dependent on Telegram."""
        offset = 0
        while not stop_event.is_set() and config.TELEGRAM_BOT_TOKEN:
            try:
                response = requests.get(
                    f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates",
                    params={"timeout": 20, "offset": offset, "allowed_updates": '["callback_query"]'},
                    timeout=30,
                )
                updates = response.json().get("result", [])
                for update in updates:
                    offset = max(offset, int(update["update_id"]) + 1)
                    handler(update)
            except (requests.RequestException, ValueError, KeyError) as exc:
                log.warning("Telegram update polling failed: %s", exc)
                time.sleep(5)


def format_alert(result: ScanResult) -> str:
    price = f"{result.key_level_price:.5f}".rstrip("0").rstrip(".")
    arrow = "BULLISH" if result.direction == "BULLISH" else "BEARISH"
    return (
        "<b>BATTOJUTSU PRICE-ACTION ALERT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>PAIR:</b> {result.pair}\n"
        f"<b>TIMEFRAME:</b> {result.timeframe}\n"
        f"<b>DIRECTION:</b> {arrow}\n"
        f"<b>KEY LEVEL TYPE:</b> {_html(result.key_level_type)}\n"
        f"<b>KEY LEVEL PRICE:</b> <code>{price}</code>\n"
        f"<b>REJECTION STATUS:</b> {_html(result.rejection_status)}\n"
        f"<b>BREAKOUT/BOS STATUS:</b> {_html(result.breakout_status)}\n"
        f"<b>CANDLE CLOSE STATUS:</b> {_html(result.candle_close_status)}\n"
        f"<b>LEVEL FLIP:</b> {_html(result.level_flip)}\n"
        f"<b>RETEST STATUS:</b> {_html(result.retest_status)}\n"
        f"<b>DAILY CONFIRMATION:</b> {_html(result.daily_confirmation)}\n"
        f"<b>H4 CONFIRMATION:</b> {_html(result.h4_confirmation)}\n"
        f"<b>FINAL SIGNAL STATUS:</b> {_html(result.final_signal_status)}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<i>State: {result.state}</i>"
    )


def _html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _decision_markup(kind: str, pair: str, setup_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "YES - CONTINUE", "callback_data": f"decision|yes|{kind}|{pair}|{setup_id}"},
            {"text": "NO - END SETUP", "callback_data": f"decision|no|{kind}|{pair}|{setup_id}"},
        ]]
    }
