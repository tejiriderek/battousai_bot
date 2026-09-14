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
                "open": 1.3650,
                "high": 1.3695,
                "low": 1.3648,
                "close": 1.3687,
            }]
        )

        result = self.engine.evaluate("GBPUSD", daily, h4)

        self.assertIsNone(result)
        self.assertEqual(self.states.get("GBPUSD")["state"], "WATCHING")


if __name__ == "__main__":
    unittest.main()
