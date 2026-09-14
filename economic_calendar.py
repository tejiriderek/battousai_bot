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

    def refresh_calendar(self) -> None:
        """Refresh calendar from API sources with fallback chain."""
        # Economic calendar feature disabled due to lack of reliable free APIs for global forex events
        # Finnhub: Premium only
        # FMP: US-market data only on free tier
        # FRED: Historical data, not forward-looking calendar
        # Web scraping: Blocked by most sites (403 errors)
        log.info("Economic calendar feature disabled; no reliable free API available for global forex events")
        self._events = []
        self._last_update = datetime.now(timezone.utc).isoformat()
        self._stale = False
        self._save()
        log.info("Economic calendar refreshed: events=%d", len(self._events))

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

    def _fetch_from_trading_economics(self) -> list[dict[str, Any]]:
        try:
            url = f"https://api.tradingeconomics.com/calendar?c={config.TRADING_ECONOMICS_API_KEY}"
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            events: list[dict[str, Any]] = []
            high_impact_events = ["FOMC", "NFP", "CPI", "GDP", "Retail Sales", "ISM Manufacturing", "ISM Services"]
            
            for item in data:
                event_name = item.get("Event", "")
                if not any(keyword in event_name.upper() for keyword in high_impact_events):
                    continue
                
                country = item.get("Country", "")
                currency_map = {"United States": "USD", "Euro Area": "EUR", "United Kingdom": "GBP", "Japan": "JPY", "Canada": "CAD", "Australia": "AUD", "New Zealand": "NZD"}
                currency = currency_map.get(country, "")
                
                if not currency:
                    continue
                
                event_date = item.get("Date", "")
                event_time = item.get("Time", "")
                
                if event_date and event_time:
                    try:
                        dt = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M:%S")
                        event_time_utc = dt.replace(tzinfo=timezone.utc).isoformat()
                    except ValueError:
                        event_time_utc = None
                else:
                    event_time_utc = None
                
                events.append({
                    "event_name": event_name,
                    "currency": currency,
                    "impact": "HIGH",
                    "event_date_utc": event_date,
                    "event_time_utc": event_time_utc,
                    "source": "Trading Economics",
                    "source_url": "https://tradingeconomics.com/calendar",
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                })
            
            log.info("Retrieved %d high-impact events from Trading Economics API", len(events))
            return events
        except requests.RequestException as exc:
            log.warning("Trading Economics API failed: %s", exc)
            return []
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("Trading Economics API parsing failed: %s", exc)
            return []

    def _fetch_from_fred(self) -> list[dict[str, Any]]:
        try:
            events: list[dict[str, Any]] = []
            # FRED release IDs (numeric, not series IDs)
            high_impact_releases = {
                "10": "Employment Situation (NFP)",
                "9": "Consumer Price Index (CPI)",
                "53": "Gross Domestic Product (GDP)",
                "50": "Federal Open Market Committee (FOMC)",
                "35": "Retail Sales",
                "20": "ISM Manufacturing PMI",
                "24": "ISM Services PMI",
            }
            
            # Get release dates for high-impact releases
            for release_id, release_name in high_impact_releases.items():
                try:
                    dates_url = "https://api.stlouisfed.org/fred/release/dates"
                    dates_params = {
                        "api_key": config.FRED_API_KEY,
                        "file_type": "json",
                        "release_id": release_id,
                        "limit": 10,
                        "order_by": "release_date",
                        "sort_order": "desc"
                    }
                    dates_response = requests.get(dates_url, params=dates_params, timeout=15)
                    dates_response.raise_for_status()
                    dates_data = dates_response.json()
                    
                    if "release_dates" in dates_data:
                        for date_info in dates_data["release_dates"]:
                            event_date = date_info.get("release_date", "")
                            if not event_date:
                                continue
                            
                            # FOMC typically at 2:00 PM EST, NFP at 8:30 AM EST
                            if release_id == "50":  # FOMC
                                event_time = "14:00:00"
                            else:
                                event_time = "08:30:00"
                            
                            try:
                                dt = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M:%S")
                                event_time_utc = dt.replace(tzinfo=timezone.utc).isoformat()
                            except ValueError:
                                event_time_utc = None
                            
                            events.append({
                                "event_name": release_name,
                                "currency": "USD",
                                "impact": "HIGH",
                                "event_date_utc": event_date,
                                "event_time_utc": event_time_utc,
                                "source": "FRED",
                                "source_url": "https://fred.stlouisfed.org",
                                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                            })
                except requests.RequestException as exc:
                    log.warning("FRED release %s failed: %s", release_id, exc)
                    continue
            
            log.info("Retrieved %d high-impact events from FRED API", len(events))
            return events
        except requests.RequestException as exc:
            log.warning("FRED API failed: %s", exc)
            return []
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("FRED API parsing failed: %s", exc)
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
