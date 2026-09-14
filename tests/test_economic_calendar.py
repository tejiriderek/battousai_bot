import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from economic_calendar import EconomicCalendarService


class EconomicCalendarTests(unittest.TestCase):
    def _cached_service(self, directory):
        path = Path(directory) / "economic_events.json"
        now = datetime.now(timezone.utc)
        path.write_text(
            json.dumps(
                {
                    "retrieved_at": now.isoformat(),
                    "events": [
                        {
                            "event_name": "CPI",
                            "currency": "USD",
                            "impact": "HIGH",
                            "event_date_utc": now.date().isoformat(),
                            "event_time_utc": (now + timedelta(minutes=10)).isoformat(),
                            "source": "BLS",
                            "source_url": "https://www.bls.gov/",
                            "retrieved_at_utc": now.isoformat(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return EconomicCalendarService(path), path

    def test_relevant_cached_event_enters_blackout(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _ = self._cached_service(directory)
            self.assertTrue(service.is_news_blackout(datetime.now(timezone.utc), "GBPUSD"))
            self.assertFalse(service.is_news_blackout(datetime.now(timezone.utc), "NZDCAD"))

    def test_failed_refresh_keeps_cache_and_blocks_new_setups(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _ = self._cached_service(directory)
            with patch(
                "economic_calendar.requests.get",
                side_effect=requests.RequestException("offline"),
            ):
                self.assertTrue(service.refresh_calendar(force=True))
            self.assertTrue(service.stale)
            self.assertTrue(service.get_upcoming_events())
            self.assertTrue(service.blocks_new_setup("GBPUSD"))


if __name__ == "__main__":
    unittest.main()
