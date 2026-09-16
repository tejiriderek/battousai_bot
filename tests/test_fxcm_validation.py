import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fxcm_validation import FXCMValidationStore


class FXCMValidationTests(unittest.TestCase):
    def setUp(self):
        self.primary = {
            "pairs": {
                "EURUSD": {"last_twelve_data_price": 1.1000},
            },
            "market_data": {
                "EURUSD": {
                    "D1": {
                        "timestamp": "2026-09-16T00:00:00+00:00",
                        "open": 1.09,
                        "high": 1.11,
                        "low": 1.08,
                        "close": 1.10,
                    },
                    "H4": None,
                }
            },
        }
        self.store = FXCMValidationStore(lambda: self.primary)

    def test_secret_and_data_are_required(self):
        with patch("config.FXCM_BRIDGE_SHARED_SECRET", "secret"):
            accepted, _ = self.store.accept({"pairs": {}}, "wrong")
            self.assertFalse(accepted)
            accepted, _ = self.store.accept(
                {
                    "pairs": {
                        "EURUSD": {
                            "price": 1.1001,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    }
                },
                "secret",
            )
            self.assertTrue(accepted)

    def test_snapshot_is_diagnostic_only(self):
        before = dict(self.primary["pairs"]["EURUSD"])
        with patch("config.FXCM_BRIDGE_SHARED_SECRET", "secret"), patch(
            "config.FXCM_BRIDGE_ENABLED", True
        ):
            self.store.accept(
                {
                    "pairs": {
                        "EURUSD": {
                            "price": 1.1001,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "ohlc": {
                                "D1": {
                                    "timestamp": "2026-09-16T00:00:00+00:00",
                                    "open": 1.09,
                                    "high": 1.11,
                                    "low": 1.08,
                                    "close": 1.10,
                                }
                            },
                        }
                    }
                },
                "secret",
            )
            snapshot = self.store.snapshot()
            self.assertEqual(snapshot["pairs"]["EURUSD"]["validation_status"], "OK")
        self.assertEqual(self.primary["pairs"]["EURUSD"], before)


if __name__ == "__main__":
    unittest.main()
