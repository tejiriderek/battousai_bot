import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fxcm_validation import FXCMValidationStore, _compare_timeframes


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

    def test_ohlc_statuses_use_pips_and_normalize_daily_timestamp(self):
        with patch("config.FXCM_OHLC_TOLERANCE_PIPS", 2.0), patch(
            "config.FXCM_OHLC_MISMATCH_MULTIPLIER", 3.0
        ):
            base = {
                "D1": {
                    "timestamp": "2026-09-16T21:00:00+00:00",
                    "open": 1.09,
                    "high": 1.11,
                    "low": 1.08,
                    "close": 1.10,
                },
                "H4": None,
            }
            comparison = _compare_timeframes("EURUSD", base, self.primary["market_data"]["EURUSD"])
            self.assertEqual(comparison["D1"]["status"], "OK")
            self.assertEqual(comparison["D1"]["timestamp_difference_seconds"], 75600)
            self.assertEqual(comparison["D1"]["normalized_timestamp_difference_seconds"], 0)
            self.assertEqual(comparison["H4"]["status"], "UNAVAILABLE")

            warn = {**base["D1"], "high": 1.11025}
            comparison = _compare_timeframes(
                "EURUSD", {"D1": warn}, self.primary["market_data"]["EURUSD"]
            )
            self.assertEqual(comparison["D1"]["status"], "WARN")
            self.assertAlmostEqual(
                comparison["D1"]["ohlc_difference_pips"]["high"], 2.5
            )

            mismatch = {**base["D1"], "open": 1.091}
            comparison = _compare_timeframes(
                "EURUSD", {"D1": mismatch}, self.primary["market_data"]["EURUSD"]
            )
            self.assertEqual(comparison["D1"]["status"], "MISMATCH")
            self.assertEqual(comparison["D1"]["mismatch_fields"], ["open"])

    def test_mismatch_event_is_recorded_once_per_candle(self):
        events = []
        store = FXCMValidationStore(lambda: self.primary, events.append)
        with patch("config.FXCM_BRIDGE_SHARED_SECRET", "secret"), patch(
            "config.FXCM_BRIDGE_ENABLED", True
        ), patch("config.FXCM_OHLC_TOLERANCE_PIPS", 2.0), patch(
            "config.FXCM_OHLC_MISMATCH_MULTIPLIER", 3.0
        ):
            accepted, _ = store.accept(
                {
                    "pairs": {
                        "EURUSD": {
                            "price": 1.1001,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "ohlc": {
                                "D1": {
                                    "timestamp": "2026-09-16T21:00:00+00:00",
                                    "open": 1.091,
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
            self.assertTrue(accepted)
            first = store.snapshot()
            second = store.snapshot()

        self.assertEqual(first["pairs"]["EURUSD"]["ohlc_comparison"]["D1"]["status"], "MISMATCH")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "fxcm_ohlc_mismatch")
        self.assertEqual(second["pairs"]["EURUSD"]["ohlc_comparison"]["D1"]["status"], "MISMATCH")


if __name__ == "__main__":
    unittest.main()
