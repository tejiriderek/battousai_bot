import unittest
from unittest.mock import patch

import pandas as pd

import main
from telegram_service import _format_provider_divergence


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
                "connected": True,
                "stale": False,
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
                "connected": True,
                "stale": False,
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

    def test_stale_binance_falls_back_to_twelve_data(self):
        with patch("config.CRYPTO_STRATEGY_PRIMARY_PROVIDER", "binance"):
            _, _, source = main._select_strategy_frames(
                "BTCUSDT",
                self.daily,
                self.h4,
                {},
                {"binance": {"connected": False, "stale": True, "symbols": {}}},
            )

        self.assertEqual(source, "twelve_data")

    def test_shadow_insufficient_history_is_explicit(self):
        main._shadow_status.clear()
        with patch("config.STRATEGY_SHADOW_MODE", True):
            main._run_shadow_comparison(
                "EURUSD",
                "fxcm",
                {"pairs": {"EURUSD": {"history": {"D1": candles(1), "H4": candles(1)}}}},
                {},
                self.daily,
                self.h4,
                None,
            )

        self.assertEqual(main._shadow_status["EURUSD"]["status"], "INSUFFICIENT_HISTORY")
        self.assertEqual(
            main._shadow_status["EURUSD"]["shadow_history_counts"], {"D1": 1, "H4": 1}
        )

    def test_strategy_divergence_message_is_plain_english(self):
        text = _format_provider_divergence(
            {
                "pair": "EURUSD",
                "strategy": {
                    "live_provider": "twelve_data",
                    "live_state": "H4_WAITING",
                    "live_direction": "BEARISH",
                    "live_daily_level": 1.159,
                    "live_h4_level": 1.15274,
                    "shadow_provider": "fxcm",
                    "shadow_state": "WATCHING",
                    "shadow_daily_level": 1.158,
                    "shadow_h4_level": None,
                    "status": "STATE_DIVERGENCE",
                },
            }
        )

        self.assertIn("SHADOW DIVERGENCE", text)
        self.assertIn("STATE_DIVERGENCE", text)
        self.assertIn("no live setup state or alert was changed", text)


if __name__ == "__main__":
    unittest.main()
