"""Optional TradingView email bridge. It is independent from the market scanner."""

from __future__ import annotations

import email
import imaplib
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from typing import Any, Callable

import config

log = logging.getLogger(__name__)

ALERT_TYPES = {"BREAKOUT", "RETEST", "REJECTION", "LEVEL_FLIP", "SETUP", "INVALIDATION"}


@dataclass(frozen=True)
class TradingViewAlert:
    symbol: str
    price: float
    timestamp: str
    interval: str
    direction: str | None
    alert_type: str
    message_id: str
    source: str = "TradingView Email"


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().split(":")[-1].replace("/", "").replace("-", "")
    return symbol


def parse_alert(text: str, message_id: str = "local-test") -> TradingViewAlert:
    fields: dict[str, str] = {}
    for part in text.replace("\r", "").split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key.strip().lower()] = value.strip()
    if fields.get("tv_alert") is None and not text.lstrip().startswith("TV_ALERT|"):
        raise ValueError("not a TradingView alert")

    symbol = normalize_symbol(fields.get("symbol", ""))
    if symbol not in config.PAIRS:
        raise ValueError("unknown TradingView symbol")
    try:
        price = float(fields["price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("missing or invalid price") from exc
    if price <= 0:
        raise ValueError("price must be positive")

    alert_type = fields.get("type", "").upper()
    if alert_type not in ALERT_TYPES:
        raise ValueError("unknown alert type")
    direction = fields.get("direction")
    if direction:
        direction = direction.upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("unknown direction")
    timestamp = fields.get("time", "")
    if not timestamp:
        raise ValueError("missing timestamp")
    try:
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    return TradingViewAlert(symbol, price, timestamp, fields.get("interval", ""), direction, alert_type, message_id)


def _body(message: Message) -> str:
    parts: list[str] = []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() == "multipart":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        elif isinstance(payload, str):
            parts.append(payload)
    return "\n".join(parts)


class TradingViewEmailBridge:
    def __init__(self, states: Any, handler: Callable[[TradingViewAlert], bool]) -> None:
        self.states = states
        self.handler = handler
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._baseline_uid = 0
        self._status: dict[str, Any] = {"enabled": config.TRADINGVIEW_EMAIL_ENABLED, "connected": False, "last_poll_time": None, "last_alert_time": None, "last_alert_symbol": None, "last_error": None}

    def start(self) -> None:
        if not config.TRADINGVIEW_EMAIL_ENABLED:
            log.info("TradingView email bridge disabled")
            return
        self._thread = threading.Thread(target=self.run, name="tradingview-email", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def run(self) -> None:
        if not config.TRADINGVIEW_EMAIL_USERNAME or not config.TRADINGVIEW_EMAIL_PASSWORD:
            self._status["last_error"] = "email credentials are not configured"
            log.error("TradingView email bridge enabled but credentials are missing")
            return
        log.info("TradingView email bridge starting")
        while not self.stop_event.is_set():
            mailbox: imaplib.IMAP4_SSL | None = None
            try:
                mailbox = imaplib.IMAP4_SSL(config.TRADINGVIEW_EMAIL_HOST, config.TRADINGVIEW_EMAIL_PORT)
                mailbox.login(config.TRADINGVIEW_EMAIL_USERNAME, config.TRADINGVIEW_EMAIL_PASSWORD)
                select_status, _ = mailbox.select(config.TRADINGVIEW_EMAIL_FOLDER, readonly=True)
                if select_status != "OK":
                    raise imaplib.IMAP4.error("mailbox selection failed")
                self._status.update(connected=True, last_error=None)
                if self._baseline_uid == 0:
                    self._baseline(mailbox)
                log.info("Watching %s for TradingView alerts", config.TRADINGVIEW_EMAIL_FOLDER)
                while not self.stop_event.is_set():
                    try:
                        self._poll(mailbox)
                    except Exception as exc:
                        self._status.update(connected=False, last_error=type(exc).__name__)
                        log.warning("TradingView email poll failed; reconnecting")
                        break
                    self._status["last_poll_time"] = datetime.now(timezone.utc).isoformat()
                    self.stop_event.wait(max(5.0, config.TRADINGVIEW_EMAIL_POLL_INTERVAL))
            except (imaplib.IMAP4.error, OSError) as exc:
                self._status.update(connected=False, last_error=type(exc).__name__)
                log.warning("TradingView email connection failed; retrying")
                self.stop_event.wait(30.0)
            finally:
                if mailbox is not None:
                    try:
                        mailbox.logout()
                    except OSError:
                        pass

    def _baseline(self, mailbox: imaplib.IMAP4_SSL) -> None:
        if config.TRADINGVIEW_PROCESS_EXISTING_ON_START:
            return
        status, data = mailbox.uid("search", None, "ALL")
        if status == "OK" and data and data[0]:
            self._baseline_uid = max(int(value) for value in data[0].split())

    def _poll(self, mailbox: imaplib.IMAP4_SSL) -> None:
        status, data = mailbox.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return
        for raw_uid in data[0].split():
            uid = int(raw_uid)
            if uid <= self._baseline_uid:
                continue
            self._baseline_uid = max(self._baseline_uid, uid)
            status, message_data = mailbox.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not message_data:
                continue
            raw_message = next((item[1] for item in message_data if isinstance(item, tuple)), None)
            if not isinstance(raw_message, bytes):
                continue
            message = email.message_from_bytes(raw_message)
            sender = str(message.get("From", "")).lower()
            if config.TRADINGVIEW_ALLOWED_SENDER and config.TRADINGVIEW_ALLOWED_SENDER not in sender:
                continue
            try:
                alert = parse_alert(_body(message), message.get("Message-ID") or f"imap-uid-{uid}")
            except ValueError as exc:
                log.warning("Malformed TradingView alert ignored: %s", exc)
                continue
            if self.states.has_processed_tradingview_email(alert.message_id):
                log.info("Duplicate TradingView alert ignored")
                continue
            if config.TRADINGVIEW_EMAIL_DRY_RUN or self.handler(alert):
                self.states.mark_tradingview_email_processed(alert.message_id)
                self._status.update(last_alert_time=datetime.now(timezone.utc).isoformat(), last_alert_symbol=alert.symbol)