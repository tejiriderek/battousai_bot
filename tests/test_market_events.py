import unittest

import pandas as pd

from market_events import detect_weekend_gap, gap_candle_times


class WeekendGapTests(unittest.TestCase):
    def _frame(self, monday_open):
        return pd.DataFrame(
            [
                {
                    "datetime": "2026-09-04T16:00:00+00:00",
                    "open": 1.3400,
                    "high": 1.3420,
                    "low": 1.3390,
                    "close": 1.3400,
                },
                {
                    "datetime": "2026-09-07T00:00:00+00:00",
                    "open": monday_open,
                    "high": monday_open + 0.0020,
                    "low": monday_open - 0.0010,
                    "close": monday_open + 0.0005,
                },
            ]
        )

    def test_small_gap_is_ignored(self):
        self.assertIsNone(detect_weekend_gap(self._frame(1.3410), "GBPUSD", "D1"))

    def test_large_gap_is_detected(self):
        gap = detect_weekend_gap(self._frame(1.3440), "GBPUSD", "D1")
        self.assertIsNotNone(gap)
        self.assertAlmostEqual(gap.gap_pips, 40.0)
        self.assertEqual(gap.candle_time, "2026-09-07T00:00:00+00:00")
        self.assertEqual(
            gap_candle_times(self._frame(1.3440), "GBPUSD", "D1"),
            {"2026-09-07T00:00:00+00:00"},
        )

    def test_crypto_weekend_data_is_not_classified_as_forex_gap(self):
        self.assertIsNone(detect_weekend_gap(self._frame(1.3440), "BTCUSDT", "D1"))


if __name__ == "__main__":
    unittest.main()
