"""Official-source economic calendar cache for risk-sensitive setup filtering."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import config

log = logging.getLogger(__name__)

SOURCES = (
    (
        "FOMC",
        "USD",
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    ),
    (
        "NFP",
        "USD",
        "https://www.bls.gov/schedule/news_release/empsit.htm",
    ),
    (
        "CPI",
        "USD",
        "https://www.bls.gov/schedule/news_release/cpi.htm",
    ),
)


class EconomicCalendarService:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.ECONOMIC_EVENTS_PATH
        self._events: list[dict[str, Any]] = []
        self._last_update: str | None = None
        self._stale = True
        self._load()

    def refresh_calendar(self, force: bool = False) -> bool:
        if not force and self._last_update:
            age = datetime.now(timezone.utc) - _parse_timestamp(self._last_update)
            if age < timedelta(minutes=config.CALENDAR_REFRESH_MINUTES):
                return bool(self._events)

        events: list[dict[str, Any]] = []
        retrieved_at = datetime.now(timezone.utc).isoformat()
        for name, currency, url in SOURCES:
            events.extend(self._fetch_from_source(url, name))

        if not events:
            self._stale = True
            log.error("No valid economic events retrieved; retaining existing calendar cache")
            return bool(self._events)

        self._events = _dedupe(events)
        self._last_update = retrieved_at
        self._stale = False
        self._save()
        log.info("Economic calendar refreshed: events=%d", len(self._events))
        return True

    def is_news_blackout(self, now_utc: datetime, pair: str) -> bool:
        if not config.NEWS_FILTER_ENABLED or self._stale:
            return False
        currencies = _pair_currencies(pair)
        return bool(self.get_blackout_events(now_utc, pair))

    def get_blackout_events(
        self,
        now_utc: datetime,
        pair: str,
    ) -> list[dict[str, Any]]:
        if not config.NEWS_FILTER_ENABLED or self._stale:
            return []
        currencies = _pair_currencies(pair)
        matches: list[dict[str, Any]] = []
        for event in self._events:
            if event.get("impact") != "HIGH" or event.get("currency") not in currencies:
                continue
            if event.get("event_time_utc"):
                event_time = _parse_timestamp(event["event_time_utc"])
                start = event_time - timedelta(minutes=config.NEWS_BLACKOUT_BEFORE_MINUTES)
                end = event_time + timedelta(minutes=config.NEWS_BLACKOUT_AFTER_MINUTES)
                if start <= now_utc <= end:
                    matches.append(dict(event))
            elif event.get("event_date_utc") == now_utc.date().isoformat():
                matches.append(dict(event))
        return matches

    def blocks_new_setup(self, pair: str, now_utc: datetime | None = None) -> bool:
        if not config.NEWS_FILTER_ENABLED:
            return False
        if self._stale or not self._events:
            log.warning("Calendar stale; allowing new setups without news filter")
            return False
        return self.is_news_blackout(now_utc or datetime.now(timezone.utc), pair)

    def get_upcoming_events(self, now_utc: datetime | None = None) -> list[dict[str, Any]]:
        now = now_utc or datetime.now(timezone.utc)
        upcoming: list[dict[str, Any]] = []
        for event in self._events:
            timestamp = event.get("event_time_utc") or event.get("event_date_utc")
            if timestamp and timestamp >= now.isoformat()[:10]:
                upcoming.append(dict(event))
        return upcoming

    def get_last_update_time(self) -> str | None:
        return self._last_update

    @property
    def stale(self) -> bool:
        return self._stale

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            events = payload.get("events", [])
            if isinstance(events, list) and events:
                self._events = events
                self._last_update = payload.get("retrieved_at")
                self._stale = False
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            log.warning("Economic calendar cache unreadable: %s", exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"retrieved_at": self._last_update, "events": self._events},
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _fetch_from_source(self, url: str, source_name: str) -> list[dict[str, Any]]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            events = _parse_source(source_name, "USD", url, response.text, datetime.now(timezone.utc).isoformat())
            log.info("Retrieved %d events from %s", len(events), source_name)
            return events
        except requests.RequestException as exc:
            log.warning("Calendar source failed: %s (%s)", url, exc)
            return []


def _parse_source(
    name: str,
    currency: str,
    source_url: str,
    html: str,
    retrieved_at: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        tables = pd.read_html(StringIO(html))
    except (ValueError, ImportError):
        return events
    for table in tables:
        for _, row in table.iterrows():
            text = " ".join(str(value) for value in row.tolist() if str(value) != "nan")
            date_match = re.search(
                r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:-\d{1,2})?,?\s+\d{4}",
                text,
                re.IGNORECASE,
            )
            if not date_match:
                continue
            parsed_date = pd.to_datetime(date_match.group(0).replace("-", " "), errors="coerce")
            if pd.isna(parsed_date):
                continue
            event_date = parsed_date.date().isoformat()
            time_match = re.search(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b", text, re.IGNORECASE)
            event_time = None
            if time_match:
                parsed_time = pd.to_datetime(
                    f"{event_date} {time_match.group(0)}", errors="coerce", utc=True
                )
                if not pd.isna(parsed_time):
                    event_time = parsed_time.isoformat()
            events.append(
                {
                    "event_name": name,
                    "currency": currency,
                    "impact": "HIGH",
                    "event_date_utc": event_date,
                    "event_time_utc": event_time,
                    "source": "Federal Reserve" if name == "FOMC" else "U.S. Bureau of Labor Statistics",
                    "source_url": source_url,
                    "retrieved_at_utc": retrieved_at,
                }
            )
    return events


def _dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for event in events:
        key = (event.get("event_name"), event.get("currency"), event.get("event_date_utc"), event.get("event_time_utc"))
        unique[key] = event
    return list(unique.values())


def _pair_currencies(pair: str) -> set[str]:
    if len(pair) < 6:
        return set()
    return {pair[:3], pair[3:6]}


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
