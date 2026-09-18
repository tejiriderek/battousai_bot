import unittest
from unittest.mock import patch

import pandas as pd

import main


def candles(count: int = 40) -> list[dict]:
    return [
        {
            "timestamp": f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
            "open": 1.1 + index * 0.0001,
            "high": 1.101 + index * 0.0001,
            "low": 1.099 + index * 0.0001,
            "close": 1.1005 + index * 0.0001,
            "completed": True,
        }
        for index in range(count)
    ]


class ProviderRolloutTests(unittest.TestCase):
    def setUp(self):
        self.daily = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC"),
                "open": [1.1] * 40,
                "high": [1.101] * 40,
                "low": [1.099] * 40,
                "close": [1.1005] * 40,
            }
        )
        self.h4 = self.daily.copy()

    def test_incomplete_primary_history_falls_back_to_twelve_data(self):
        with patch("config.FOREX_STRATEGY_PRIMARY_PROVIDER", "fxcm"):
            selected_daily, selected_h4, source = main._select_strategy_frames(
                "EURUSD",
                self.daily,
                self.h4,
                {"pairs": {"EURUSD": {"history": {"D1": candles(3), "H4": candles(3)}}}},
                {},
            )

        self.assertEqual(source, "twelve_data")
        self.assertIs(selected_daily, self.daily)
        self.assertIs(selected_h4, self.h4)

    def test_complete_binance_history_is_normalized_for_crypto(self):
        snapshot = {
            "binance": {
                "symbols": {
                    "BTCUSDT": {"history": {"D1": candles(), "H4": candles()}}
                }
            }
        }

        frames = main._provider_frames("binance", "BTCUSDT", {}, snapshot)

        self.assertIsNotNone(frames)
        self.assertEqual(len(frames[0]), 40)
        self.assertEqual(len(frames[1]), 40)
        self.assertTrue(pd.api.types.is_datetime64tz_dtype(frames[0]["datetime"]))

    def test_complete_fxcm_history_can_be_selected_for_forex(self):
        snapshot = {
            "pairs": {
                "EURUSD": {"history": {"D1": candles(), "H4": candles()}}
            }
        }
        with patch("config.FOREX_STRATEGY_PRIMARY_PROVIDER", "fxcm"):
            _, _, source = main._select_strategy_frames(
                "EURUSD", self.daily, self.h4, snapshot, {}
            )

        self.assertEqual(source, "fxcm")

    def test_default_rollout_keeps_twelve_data_live(self):
        with patch("config.CRYPTO_STRATEGY_PRIMARY_PROVIDER", "twelve_data"):
            _, _, source = main._select_strategy_frames(
                "BTCUSDT", self.daily, self.h4, {}, {}
            )

        self.assertEqual(source, "twelve_data")

    def test_forex_and_crypto_can_select_different_primaries(self):
        forex_snapshot = {
            "pairs": {
                "EURUSD": {"history": {"D1": candles(), "H4": candles()}}
            }
        }
        crypto_snapshot = {
            "binance": {
                "symbols": {
                    "BTCUSDT": {"history": {"D1": candles(), "H4": candles()}}
                }
            }
        }
        with patch("config.FOREX_STRATEGY_PRIMARY_PROVIDER", "fxcm"), patch(
            "config.CRYPTO_STRATEGY_PRIMARY_PROVIDER", "binance"
        ):
            _, _, forex_source = main._select_strategy_frames(
                "EURUSD", self.daily, self.h4, forex_snapshot, {}
            )
            _, _, crypto_source = main._select_strategy_frames(
                "BTCUSDT", self.daily, self.h4, {}, crypto_snapshot
            )

        self.assertEqual(forex_source, "fxcm")
        self.assertEqual(crypto_source, "binance")


if __name__ == "__main__":
    unittest.main()
