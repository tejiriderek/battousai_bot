import unittest
from pathlib import Path

import pandas as pd

from state_manager import StateManager
from strategy import StrategyEngine


class StrategyInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.state_path = Path("data/test_state.json")
        if self.state_path.exists():
            self.state_path.unlink()
        self.states = StateManager(self.state_path)
        self.engine = StrategyEngine(self.states)

    def tearDown(self):
        if self.state_path.exists():
            self.state_path.unlink()

    def test_daily_close_against_direction_invalidates_setup(self):
        self.states.update(
            "GBPUSD",
            state="WAITING_FOR_RETEST",
            direction="BEARISH",
            daily_setup="REJECTION",
            daily_level_type="A_SHAPE",
            daily_level_price=1.35583,
            h4_level_type="V_SHAPE",
            h4_level_price=1.353,
            h4_breakout_bar_time="2026-09-13T17:00:00+00:00",
            daily_bar_time="2026-09-10T00:00:00+00:00",
            h4_confirmed=True,
            daily_confirmed=True,
        )

        daily = pd.DataFrame(
            [{
                "datetime": "2026-09-13T00:00:00+00:00",
                "open": 1.3552,
                "high": 1.3565,
                "low": 1.3548,
                "close": 1.3562,
            }]
        )
        h4 = pd.DataFrame(
            [{
                "datetime": "2026-09-13T17:00:00+00:00",
                "open": 1.3538,
                "high": 1.3547,
                "low": 1.3527,
                "close": 1.3535,
            }]
        )

        result = self.engine.evaluate("GBPUSD", daily, h4)

        self.assertIsNone(result)
        self.assertEqual(self.states.get("GBPUSD")["state"], "WATCHING")
        self.assertEqual(self.states.get("GBPUSD")["last_reset_reason"], "daily_counter_close")

    def test_price_running_far_from_level_invalidates_setup(self):
        self.states.update(
            "GBPUSD",
            state="WAITING_FOR_RETEST",
            direction="BEARISH",
            daily_setup="REJECTION",
            daily_level_type="A_SHAPE",
            daily_level_price=1.35583,
            h4_level_type="V_SHAPE",
            h4_level_price=1.353,
            h4_breakout_bar_time="2026-09-13T17:00:00+00:00",
            daily_bar_time="2026-09-10T00:00:00+00:00",
            h4_confirmed=True,
            daily_confirmed=True,
        )

        daily = pd.DataFrame(
            [{
                "datetime": "2026-09-15T00:00:00+00:00",
                "open": 1.3520,
                "high": 1.3560,
                "low": 1.3510,
                "close": 1.3542,
            }]
        )
        h4 = pd.DataFrame(
            [{
                "datetime": "2026-09-15T17:00:00+00:00",
                "open": 1.3420,
                "high": 1.3430,
                "low": 1.3410,
                "close": 1.3420,
            }]
        )

        result = self.engine.evaluate("GBPUSD", daily, h4)

        self.assertIsNone(result)
        self.assertEqual(self.states.get("GBPUSD")["state"], "WATCHING")
        self.assertEqual(self.states.get("GBPUSD")["last_reset_reason"], "price_distance")

    def test_old_waiting_setup_is_not_killed_by_age(self):
        self.states.update(
            "GBPUSD",
            state="WAITING_FOR_RETEST",
            direction="BEARISH",
            h4_level_price=1.3530,
            h4_breakout_bar_time="2026-09-10T00:00:00+00:00",
        )
        candles = []
        for index in range(13):
            candles.append(
                {
                    "datetime": pd.Timestamp(f"2026-09-{10 + index // 6:02d}T{(index % 6) * 4:02d}:00+00:00"),
                    "open": 1.3525,
                    "high": 1.3528,
                    "low": 1.3520,
                    "close": 1.3524,
                }
            )
        self.engine._handle_retest("GBPUSD", pd.DataFrame(candles), 0.000005)

        self.assertEqual(self.states.get("GBPUSD")["state"], "WAITING_FOR_RETEST")
        self.assertEqual(self.states.get("GBPUSD")["h4_bars_since_breakout"], 13)

    def test_wick_through_level_close_back_is_rejection(self):
        candle = pd.Series({
            "open": 1.3500,
            "high": 1.3520,
            "low": 1.3480,
            "close": 1.3510,
        })
        from strategy import _bullish_sweep
        self.assertTrue(_bullish_sweep(candle, 1.3500, 0.000005))

    def test_body_close_through_level_is_breakout(self):
        candle = pd.Series({
            "open": 1.3490,
            "high": 1.3510,
            "low": 1.3485,
            "close": 1.3510,
        })
        prev = pd.Series({
            "close": 1.3490,
        })
        from strategy import _bullish_breakout
        self.assertTrue(_bullish_breakout(candle, prev, 1.3500, 0.000005))

    def test_gap_candle_excluded_from_breakout_detection(self):
        self.states.update(
            "GBPUSD",
            state="H4_WAITING",
            direction="BULLISH",
            daily_level_price=1.3500,
            daily_bar_time="2026-09-10T00:00:00+00:00",
        )
        
        daily = pd.DataFrame([{
            "datetime": "2026-09-13T00:00:00+00:00",
            "open": 1.3520,
            "high": 1.3530,
            "low": 1.3515,
            "close": 1.3525,
        }])
        
        h4 = pd.DataFrame([{
            "datetime": "2026-09-13T17:00:00+00:00",
            "open": 1.3520,
            "high": 1.3530,
            "low": 1.3515,
            "close": 1.3525,
        }])
        
        result = self.engine.evaluate("GBPUSD", daily, h4)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
