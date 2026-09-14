import tempfile
import unittest
from pathlib import Path

from state_manager import StateManager


class StateManagerDecisionTests(unittest.TestCase):
    def test_yes_override_is_setup_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            states = StateManager(Path(directory) / "state.json")
            states.update("GBPUSD", state="WAITING_FOR_RETEST", setup_id="setup-1")

            self.assertTrue(states.apply_warning_decision("GBPUSD", "setup-1", "gap", "yes"))
            self.assertTrue(states.get("GBPUSD")["gap_override"])
            self.assertEqual(states.get("GBPUSD")["state"], "WAITING_FOR_RETEST")
            self.assertFalse(states.apply_warning_decision("GBPUSD", "old-setup", "gap", "yes"))

    def test_no_ends_only_the_current_setup_and_persists_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            states = StateManager(path)
            states.update("GBPUSD", state="WAITING_FOR_RETEST", setup_id="setup-2")

            self.assertTrue(states.apply_warning_decision("GBPUSD", "setup-2", "news", "no"))
            ended = states.get("GBPUSD")
            self.assertEqual(ended["state"], "WATCHING")
            self.assertEqual(ended["last_reset_reason"], "user_ended_news")
            self.assertEqual(ended["last_reset_details"]["setup_id"], "setup-2")

            restored = StateManager(path)
            self.assertEqual(restored.get("GBPUSD")["last_reset_reason"], "user_ended_news")


if __name__ == "__main__":
    unittest.main()
