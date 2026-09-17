import unittest
from pathlib import Path

import pandas as pd

import config
from market_events import detect_weekend_gap
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
            retest_allowed=True,
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

    def test_weekend_gap_is_not_reported_after_monday(self):
        frame = pd.DataFrame([
            {"datetime": "2026-09-11T00:00:00+00:00", "open": 1.1000, "high": 1.1010, "low": 1.0990, "close": 1.1000},
            {"datetime": "2026-09-14T00:00:00+00:00", "open": 1.1030, "high": 1.1040, "low": 1.1020, "close": 1.1035},
            {"datetime": "2026-09-15T00:00:00+00:00", "open": 1.1035, "high": 1.1050, "low": 1.1025, "close": 1.1040},
        ])

        self.assertIsNone(detect_weekend_gap(frame, "EURUSD", "D1"))

    def test_second_chance_ema_pullback_triggers_when_retest_does_not_happen(self):
        self.states.update(
            "GBPUSD",
            state="WAITING_FOR_RETEST",
            direction="BULLISH",
            h4_level_price=1.3500,
            h4_breakout_bar_time="2026-09-13T17:00:00+00:00",
            second_chance_allowed=True,
        )

        candles = []
        closes = [1.3512, 1.3519, 1.3526, 1.3518, 1.3521, 1.3510, 1.3514, 1.3509, 1.3513, 1.3518]
        lows = [1.3508, 1.3512, 1.3517, 1.3510, 1.3515, 1.3508, 1.3510, 1.3509, 1.3512, 1.3511]
        for idx, (close, low) in enumerate(zip(closes, lows)):
            candles.append({
                "datetime": pd.Timestamp("2026-09-13T17:00:00+00:00") + pd.Timedelta(hours=int(idx * 4)),
                "open": close - 0.0004,
                "high": close + 0.0006,
                "low": low,
                "close": close,
            })

        result = self.engine._handle_retest("GBPUSD", pd.DataFrame(candles), 0.000005)

        self.assertIsNotNone(result)
        self.assertTrue(result.alert)
        self.assertEqual(result.final_signal_status, "SECOND_CHANCE_PULLBACK")
        self.assertEqual(self.states.get("GBPUSD")["state"], "CONTINUATION_CONFIRMED")
        self.assertTrue(self.states.get("GBPUSD")["alert_pending"])

    def test_volume_gate_is_off_by_default_and_blocks_weak_breakout_when_enabled(self):
        self.states.update(
            "GBPUSD",
            state="WAITING_FOR_RETEST",
            direction="BULLISH",
            h4_level_price=1.3500,
            h4_breakout_bar_time="2026-09-13T17:00:00+00:00",
        )
        config.VOLUME_FILTER_ENABLED = False
        candles = []
        for idx in range(10):
            candles.append({
                "datetime": pd.Timestamp("2026-09-13T17:00:00+00:00") + pd.Timedelta(hours=int(idx * 4)),
                "open": 1.3500 + idx * 0.0001,
                "high": 1.3515 + idx * 0.0001,
                "low": 1.3492 + idx * 0.0001,
                "close": 1.3508 + idx * 0.0001,
                "volume": 1500 + idx * 100,
            })
        self.assertTrue(self.engine._volume_gate_passes(pd.DataFrame(candles), "BULLISH"))

        config.VOLUME_FILTER_ENABLED = True
        weak = []
        for idx in range(10):
            weak.append({
                "datetime": pd.Timestamp("2026-09-13T17:00:00+00:00") + pd.Timedelta(hours=int(idx * 4)),
                "open": 1.3500 + idx * 0.0001,
                "high": 1.3515 + idx * 0.0001,
                "low": 1.3492 + idx * 0.0001,
                "close": 1.3508 + idx * 0.0001,
                "volume": 100 + idx * 5,
            })
        self.assertFalse(self.engine._volume_gate_passes(pd.DataFrame(weak), "BULLISH"))

    def test_retest_can_be_opted_out_for_a_pair_setup(self):
        self.states.update(
            "GBPUSD",
            state="WAITING_FOR_RETEST",
            direction="BULLISH",
            h4_level_price=1.3500,
            h4_breakout_bar_time="2026-09-13T17:00:00+00:00",
            retest_allowed=False,
        )
        candles = pd.DataFrame([
            {
                "datetime": "2026-09-13T21:00:00+00:00",
                "open": 1.3502,
                "high": 1.3510,
                "low": 1.3496,
                "close": 1.3504,
            }
        ])
        result = self.engine._handle_retest("GBPUSD", candles, 0.000005)
        self.assertIsNone(result)
        self.assertEqual(self.states.get("GBPUSD")["state"], "WATCHING")
        self.assertEqual(self.states.get("GBPUSD")["last_reset_reason"], "user_disabled_retest")


if __name__ == "__main__":
    unittest.main()
