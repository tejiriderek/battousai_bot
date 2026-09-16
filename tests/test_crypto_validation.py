import unittest
import time
from datetime import datetime, timezone
from unittest.mock import patch

from crypto_validation import CryptoValidationService, _aggregate_coinbase_h4


class CryptoValidationTests(unittest.TestCase):
    def setUp(self):
        self.enabled_patches = [
            patch("config.CRYPTO_BINANCE_VALIDATION_ENABLED", True),
            patch("config.CRYPTO_COINBASE_VALIDATION_ENABLED", True),
        ]
        for enabled_patch in self.enabled_patches:
            enabled_patch.start()
        self.addCleanup(self._stop_enabled_patches)
        self.primary = {
            "pairs": {
                "BTCUSDT": {"last_twelve_data_price": 100000},
                "ETHUSDT": {"last_twelve_data_price": 4000},
            }
        }
        self.service = CryptoValidationService(lambda: self.primary)

    def _stop_enabled_patches(self):
        for enabled_patch in reversed(self.enabled_patches):
            enabled_patch.stop()

    def test_binance_and_coinbase_use_explicit_products(self):
        self.service._record("binance", "BTCUSDT", 100100, datetime.now(timezone.utc))
        self.service._record("coinbase", "BTC-USD", 100200, datetime.now(timezone.utc))

        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["binance"]["symbols"]["BTCUSDT"]["price"], 100100)
        self.assertEqual(snapshot["coinbase"]["symbols"]["BTC-USD"]["price"], 100200)

    def test_discrepancy_is_diagnostic_only(self):
        before = dict(self.primary["pairs"]["BTCUSDT"])
        self.service._record("binance", "BTCUSDT", 110000, datetime.now(timezone.utc))

        snapshot = self.service.snapshot()
        self.assertEqual(
            snapshot["binance"]["symbols"]["BTCUSDT"]["validation_status"],
            "PRICE_DISCREPANCY_WARNING",
        )
        self.assertEqual(self.primary["pairs"]["BTCUSDT"], before)

    def test_malformed_messages_are_ignored(self):
        self.service._handle_binance_message("not-json")
        self.service._handle_coinbase_message('{"events": [{"tickers": [{}]}]}')
        snapshot = self.service.snapshot()
        self.assertIsNone(snapshot["binance"]["symbols"]["BTCUSDT"]["price"])
        self.assertIsNone(snapshot["coinbase"]["symbols"]["BTC-USD"]["price"])

    def test_coinbase_h4_is_aggregated_from_four_completed_utc_hours(self):
        start = (int(time.time()) // 14400 - 2) * 14400
        rows = [
            [start + offset * 3600, 99, 101 + offset, 100 + offset, 100.5 + offset, 10]
            for offset in range(4)
        ]
        candle = _aggregate_coinbase_h4(rows)
        self.assertIsNotNone(candle)
        self.assertEqual(candle["open"], 100)
        self.assertEqual(candle["high"], 104)
        self.assertEqual(candle["low"], 99)
        self.assertEqual(candle["close"], 103.5)


if __name__ == "__main__":
    unittest.main()
