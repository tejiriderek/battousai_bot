"""Telegram alerts via Bot API sendMessage (same contract as the Laravel/JS client)."""

from __future__ import annotations

import logging

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

    def send_html(self, text: str) -> bool:
        url = f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
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
