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
    def __init__(self, state_manager=None, validation_provider=None):
        self.state_manager = state_manager
        self.validation_provider = validation_provider

    def send_alert(self, result: ScanResult) -> bool:
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            log.error("Telegram token or chat id missing; alert not sent")
            return False
        validation = self.validation_provider() if self.validation_provider else None
        return self.send_html(format_alert(result, validation))

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
        if event_type == "fxcm_conflict":
            return self.send_html(_format_fxcm_ohlc_mismatch(event))
        if event_type == "fxcm_ohlc_mismatch":
            return self.send_html(_format_fxcm_ohlc_mismatch(event))
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
            if active and event.get("setup_id"):
                markup = _decision_markup("gap", pair, str(event["setup_id"]))
                if self.state_manager:
                    self.state_manager.record_warning_sent(pair, "gap")
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
            if event.get("setup_id"):
                markup = _decision_markup(warning_type, pair, str(event["setup_id"]))
                if self.state_manager:
                    self.state_manager.record_warning_sent(pair, warning_type)
            return self.send_html(text, markup)

        if event_type == "rule_decision":
            pair = str(event["pair"])
            rule_name = str(event.get("warning_type", "rule")).replace("_", " ").title()
            text = (
                f"<b>⚙️ {pair} - {rule_name.upper()} OPTION</b>\n"
                f"This setup is considering {rule_name.lower()} logic.\n"
                f"Do you want to allow it for this pair only, or skip it?"
            )
            markup = None
            if event.get("setup_id"):
                markup = _decision_markup(str(event.get("warning_type", "rule")), pair, str(event["setup_id"]))
                if self.state_manager:
                    self.state_manager.record_warning_sent(pair, str(event.get("warning_type", "rule")))
            return self.send_html(text, markup)

        if event_type == "tradingview_alert":
            pair = str(event["pair"])
            alert_type = str(event.get("alert_type", "ALERT"))
            direction = event.get("direction") or "N/A"
            tv_price = event.get("tradingview_price", "N/A")
            twelve_price = event.get("twelve_data_price", "N/A")
            difference = event.get("difference", "N/A")
            state = event.get("scanner_state", "UNKNOWN")
            text = (
                f"<b>TRADINGVIEW EMAIL ALERT: {pair}</b>\n"
                f"Type: {_html(alert_type)}\n"
                f"Direction: {_html(str(direction))}\n"
                f"TradingView: <code>{_html(str(tv_price))}</code>\n"
                f"Twelve Data: <code>{_html(str(twelve_price))}</code>\n"
                f"Difference: <code>{_html(str(difference))}</code>\n"
                f"Twelve Data updated: {_html(str(event.get('twelve_data_at') or 'N/A'))}\n"
                f"Timeframe: {_html(str(event.get('timeframe') or 'N/A'))}\n"
                f"Alert time: {_html(str(event.get('timestamp') or 'N/A'))}\n"
                f"Scanner state: <b>{_html(str(state))}</b>\n"
                f"Daily setup: {_html(str(event.get('daily_setup') or 'N/A'))}\n"
                f"Daily level: <code>{_html(str(event.get('daily_level_price') or 'N/A'))}</code>\n"
                f"H4 level: <code>{_html(str(event.get('h4_level_price') or 'N/A'))}</code>\n"
                f"Retest: {_html(str(event.get('retest_status') or 'N/A'))}\n"
                f"Breakout: {_html(str(event.get('breakout_status') or 'N/A'))}\n"
                "Source: TradingView email\n"
                "Twelve Data scanner remains independent."
            )
            markup = None
            if event.get("setup_id") and event.get("warning_type") and not event.get("warning_acknowledged"):
                markup = _decision_markup(str(event["warning_type"]), pair, str(event["setup_id"]))
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
                ending = "SETUP SKIPPED" if event.get("warning_type") == "fxcm_conflict" else "SETUP ENDED"
                text = f"<b>{pair} - {ending}</b>\nYou chose not to continue after the {event.get('warning_type')} warning."
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


def format_alert(result: ScanResult, validation: dict[str, Any] | None = None) -> str:
    price = f"{result.key_level_price:.5f}".rstrip("0").rstrip(".")
    arrow = "BULLISH" if result.direction == "BULLISH" else "BEARISH"
    text = (
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
    if validation:
        text += "\n━━━━━━━━━━━━━━━━━━\n<b>DATA SOURCE COMPARISON</b>\n"
        if result.pair.endswith("USDT"):
            text += _format_validation_provider(
                "binance", validation.get("binance"), result.pair, validation
            )
            text += _format_validation_provider(
                "coinbase", validation.get("coinbase"), result.pair, validation
            )
            text += _format_twelve_data_primary(validation, result.pair)
        else:
            text += _format_fxcm_validation(validation.get("fxcm_validation"), result.pair)
            text += _format_twelve_data_primary(validation, result.pair)
    return text


def _format_validation_provider(
    source: str,
    details: dict[str, Any] | None,
    pair: str,
    validation: dict[str, Any] | None = None,
) -> str:
    if not details or not details.get("enabled"):
        return f"<b>{source.title()}:</b> 🔴 DISABLED\n"
    if not details.get("connected") or details.get("stale"):
        status = "DISCONNECTED" if not details.get("connected") else "STALE"
        return f"<b>{source.title()}:</b> 🔴 {status}\n"
    
    # Map pair to symbol
    symbol_map = {"BTCUSDT": "BTC-USD" if source == "coinbase" else "BTCUSDT", 
                  "ETHUSDT": "ETH-USD" if source == "coinbase" else "ETHUSDT"}
    symbol = symbol_map.get(pair, pair)
    
    symbol_data = details.get("symbols", {}).get(symbol, {})
    price = symbol_data.get("price")
    ohlc = symbol_data.get("ohlc", {})
    
    lines = [f"<b>{source.title()}:</b> 🟢 CONNECTED"]
    if price:
        lines.append(f"  Price: <code>{price}</code>")

    discrepancy = _crypto_discrepancy_percent(source, details, pair, validation)
    if discrepancy is not None:
        severity = _crypto_discrepancy_severity(source, discrepancy)
        lines.append(
            f"  Discrepancy: <b>{severity}</b> ({discrepancy:.2f}% maximum)"
        )
    
    # Add OHLC data if available
    d1 = ohlc.get("D1")
    h4 = ohlc.get("H4")
    if d1:
        lines.append(f"  D1: O={d1.get('open')} H={d1.get('high')} L={d1.get('low')} C={d1.get('close')}")
    if h4:
        lines.append(f"  H4: O={h4.get('open')} H={h4.get('high')} L={h4.get('low')} C={h4.get('close')}")
    
    return "\n".join(lines) + "\n"


def _crypto_discrepancy_percent(
    source: str,
    details: dict[str, Any],
    pair: str,
    validation: dict[str, Any] | None,
) -> float | None:
    symbol = "BTC-USD" if source == "coinbase" and pair == "BTCUSDT" else pair
    if source == "coinbase" and pair == "ETHUSDT":
        symbol = "ETH-USD"
    symbol_data = details.get("symbols", {}).get(symbol, {})
    values: list[float] = []
    price_difference = symbol_data.get("price_difference_pct")
    if price_difference is not None:
        values.append(abs(float(price_difference)))

    primary = (validation or {}).get("market_data", {}).get(pair, {})
    for timeframe, comparison in (symbol_data.get("ohlc_comparison") or {}).items():
        primary_candle = primary.get(timeframe) or {}
        if comparison.get("status") != "COMPARED":
            continue
        for field, difference in (comparison.get("ohlc_difference") or {}).items():
            primary_value = primary_candle.get(field)
            if primary_value:
                values.append(abs(float(difference)) / abs(float(primary_value)) * 100)
    return max(values) if values else None


def _crypto_discrepancy_severity(source: str, discrepancy: float) -> str:
    threshold = (
        config.CRYPTO_BINANCE_MAX_PRICE_DISCREPANCY_PCT
        if source == "binance"
        else config.CRYPTO_COINBASE_MAX_PRICE_DISCREPANCY_PCT
    )
    if discrepancy >= threshold * 3:
        return "HIGH"
    if discrepancy >= threshold * 2:
        return "MEDIUM"
    return "LOW"


def _format_fxcm_validation(details: dict[str, Any] | None, pair: str) -> str:
    if not details or not details.get("enabled"):
        return "<b>FXCM:</b> 🔴 DISABLED\n"
    if not details.get("connected") or details.get("stale"):
        status = "DISCONNECTED" if not details.get("connected") else "STALE"
        return f"<b>FXCM:</b> 🔴 {status}\n"
    
    pair_data = details.get("pairs", {}).get(pair, {})
    price = pair_data.get("price")
    ohlc = pair_data.get("ohlc", {})
    
    lines = ["<b>FXCM:</b> 🟢 CONNECTED"]
    if price:
        lines.append(f"  Price: <code>{price}</code>")
    
    d1 = ohlc.get("D1")
    h4 = ohlc.get("H4")
    if d1:
        lines.append(f"  D1: O={d1.get('open')} H={d1.get('high')} L={d1.get('low')} C={d1.get('close')}")
    if h4:
        lines.append(f"  H4: O={h4.get('open')} H={h4.get('high')} L={h4.get('low')} C={h4.get('close')}")
    
    return "\n".join(lines) + "\n"


def _format_twelve_data_primary(validation: dict[str, Any] | None, pair: str) -> str:
    market_data = validation.get("market_data", {}).get(pair, {})
    d1 = market_data.get("D1")
    h4 = market_data.get("H4")
    
    lines = ["<b>Twelve Data (Primary):</b> 🟢 ACTIVE"]
    if d1:
        lines.append(f"  D1: O={d1.get('open')} H={d1.get('high')} L={d1.get('low')} C={d1.get('close')}")
    if h4:
        lines.append(f"  H4: O={h4.get('open')} H={h4.get('high')} L={h4.get('low')} C={h4.get('close')}")
    
    return "\n".join(lines) + "\n"


def _html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _format_fxcm_ohlc_mismatch(event: dict[str, Any]) -> str:
    pair = _html(str(event.get("pair") or "UNKNOWN"))
    timeframe = _html(str(event.get("timeframe") or "UNKNOWN"))
    fields = event.get("fields") or []
    differences = event.get("ohlc_difference_pips") or {}
    twelve = event.get("twelve_data") or {}
    fxcm = event.get("fxcm") or {}
    rows = ["Field       TwelveData       FXCM          Delta (pips)"]
    for field in ("open", "high", "low", "close"):
        twelve_value = twelve.get(field)
        fxcm_value = fxcm.get(field)
        delta = differences.get(field)
        rows.append(
            f"{field.title():<10} {str(twelve_value if twelve_value is not None else 'null'):<16} "
            f"{str(fxcm_value if fxcm_value is not None else 'null'):<13} "
            f"{str(round(delta, 2)) if delta is not None else 'null'}"
        )
    table = _html("\n".join(rows))
    repeated = event.get("repeated_observations")
    is_review = bool(event.get("requires_review"))
    is_informational = bool(
        event.get("informational")
        or event.get("setup_action") == "NONE"
        or (event.get("maximum_difference_pips") is not None and not is_review)
        or (event.get("repeated_observations") and not is_review)
    )
    if is_review:
        direction = str(event.get("direction") or "the current direction").replace("BULLISH", "BUY").replace("BEARISH", "SELL")
        explanation = (
            "The two providers are looking at the same candle period, but their prices differ.\n"
            f"Twelve Data is guiding the {direction} decision, but FXCM is the broker reference.\n"
            "The two feeds may not agree on whether the level was truly broken or respected.\n"
            "This is an informational discrepancy alert; the strategy setup continues using Twelve Data."
        )
        decision = ""
    elif is_informational:
        explanation = (
            "The providers are comparing the same completed candle, but their OHLC values differ beyond tolerance.\n"
            "FXCM is broker/reference data; Twelve Data remains the strategy feed.\n"
            "This is informational only. No user decision is required, and the setup state remains unchanged."
        )
        decision = ""
    else:
        explanation = "These are the actual candles received from both providers for comparison."
        decision = ""
    severity = str(event.get("severity") or "WARNING").upper()
    maximum_difference = event.get("maximum_difference_pips")
    if maximum_difference is not None:
        severity_line = (
            f"Alert level: {severity} ({float(maximum_difference):.1f} pips maximum difference)\n"
        )
    else:
        severity_line = ""
    timing_line = ""
    heading = "FXCM DATA NOTICE" if is_review or is_informational else "FXCM OHLC MISMATCH"
    return (
        f"<b>{heading}: {pair} {timeframe}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{_html(severity_line)}"
        f"{_html(explanation)}\n"
        f"{_html(decision)}\n"
        f"{_html(timing_line)}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"<pre>{table}</pre>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Fields: {_html(', '.join(str(field) for field in fields))}\n"
        f"Tolerance: {_html(str(event.get('tolerance_pips', 'null')))} pips\n"
        "Provider values are the actual candles used for comparison."
    )


def _decision_markup(kind: str, pair: str, setup_id: str) -> dict[str, Any]:
    no_text = "NO - SKIP SETUP" if kind == "fxcm_conflict" else "NO - END SETUP"
    return {
        "inline_keyboard": [[
            {"text": "YES - CONTINUE", "callback_data": f"decision|yes|{kind}|{pair}|{setup_id}"},
            {"text": no_text, "callback_data": f"decision|no|{kind}|{pair}|{setup_id}"},
        ]]
    }
