import unittest
from unittest.mock import patch

import config
import main
from state_manager import StateManager
from telegram_service import TelegramService, _format_validation_provider


class TelegramConfirmationTests(unittest.TestCase):
    def test_prompt_count_increments_and_resets_for_new_warning_type(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            states = StateManager(Path(directory) / "state.json")
            states.update("USDJPY", state="WAITING_FOR_RETEST", setup_id="setup-1")
            states.record_warning_sent("USDJPY", "retest")
            states.record_warning_sent("USDJPY", "retest")
            self.assertEqual(states.get("USDJPY")["confirmation_prompt_count"], 2)

            states.update("USDJPY", warning_acknowledged=True)
            states.record_warning_sent("USDJPY", "second_chance")
            self.assertEqual(states.get("USDJPY")["confirmation_prompt_count"], 1)

    def test_confirmation_reminders_use_supported_event_types(self):
        state = {"state": "WAITING_FOR_RETEST", "h4_bars_since_breakout": 3}

        self.assertEqual(
            main._confirmation_event("USDJPY", state, "retest", "setup-1")["type"],
            "rule_decision",
        )
        self.assertEqual(
            main._confirmation_event("USDJPY", state, "second_chance", "setup-1")["type"],
            "rule_decision",
        )
        self.assertEqual(
            main._confirmation_event("USDJPY", state, "gap", "setup-1")["type"],
            "weekend_gap",
        )
        self.assertEqual(
            main._confirmation_event("USDJPY", state, "aging", "setup-1")["type"],
            "aging_warning",
        )
        self.assertEqual(
            main._confirmation_event("USDJPY", state, "news", "setup-1")["type"],
            "news_warning",
        )

    def test_every_confirmation_event_includes_yes_and_no_buttons(self):
        service = TelegramService()
        events = [
            {"type": "rule_decision", "pair": "USDJPY", "setup_id": "setup-1", "warning_type": "retest"},
            {"type": "weekend_gap", "pair": "USDJPY", "setup_id": "setup-1", "active_setup": True, "gap_pips": 20, "friday_close": 1, "monday_open": 1.2},
            {"type": "news_warning", "pair": "USDJPY", "setup_id": "setup-1", "warning_type": "news", "event_name": "CPI", "currency": "USD", "event_time_utc": "now", "state": "WAITING_FOR_RETEST"},
            {"type": "aging_warning", "pair": "USDJPY", "setup_id": "setup-1", "bars": 12, "state": "WAITING_FOR_RETEST"},
        ]

        with patch.object(service, "send_html", return_value=True) as send_html:
            for event in events:
                self.assertTrue(service.send_event(event))

        for call in send_html.call_args_list:
            markup = call.args[1]
            self.assertEqual(len(markup["inline_keyboard"][0]), 2)
            self.assertIn("decision|yes|", markup["inline_keyboard"][0][0]["callback_data"])
            self.assertIn("decision|no|", markup["inline_keyboard"][0][1]["callback_data"])

    def test_callback_uses_chat_id_when_authorized_user_id_is_not_configured(self):
        original_user_id = config.TELEGRAM_AUTHORIZED_USER_ID
        original_chat_id = config.TELEGRAM_CHAT_ID
        original_states = main._states
        try:
            config.TELEGRAM_AUTHORIZED_USER_ID = ""
            config.TELEGRAM_CHAT_ID = "12345"

            class States:
                def apply_warning_decision(self, pair, setup_id, warning_type, decision):
                    return (pair, setup_id, warning_type, decision) == ("USDJPY", "setup-1", "retest", "yes")

                def drain_events(self):
                    return []

            main._states = States()
            with patch.object(main._telegram, "answer_callback") as answer_callback:
                main._handle_telegram_update(
                    {
                        "callback_query": {
                            "id": "callback-1",
                            "from": {"id": 9876},
                            "message": {"chat": {"id": 12345}},
                            "data": "decision|yes|retest|USDJPY|setup-1",
                        }
                    }
                )

            self.assertEqual(answer_callback.call_args_list[-1].args[1], "Decision saved")
        finally:
            config.TELEGRAM_AUTHORIZED_USER_ID = original_user_id
            config.TELEGRAM_CHAT_ID = original_chat_id
            main._states = original_states

    def test_fxcm_mismatch_event_is_sent_as_comparison_table(self):
        service = TelegramService()
        event = {
            "type": "fxcm_ohlc_mismatch",
            "pair": "EURUSD",
            "timeframe": "D1",
            "fields": ["open"],
            "tolerance_pips": 2.0,
            "ohlc_difference_pips": {"open": 5.0},
            "twelve_data": {"open": 1.15, "high": 1.16, "low": 1.14, "close": 1.15},
            "fxcm": {"open": 1.1495, "high": 1.16, "low": 1.14, "close": 1.15},
        }
        with patch.object(service, "send_html", return_value=True) as send_html:
            self.assertTrue(service.send_event(event))
        text = send_html.call_args.args[0]
        self.assertIn("FXCM OHLC MISMATCH: EURUSD D1", text)
        self.assertIn("TwelveData", text)
        self.assertIn("FXCM", text)

    def test_fxcm_conflict_is_informational_and_has_no_confirmation(self):
        service = TelegramService()
        event = {
            "type": "fxcm_conflict",
            "pair": "NZDCAD",
            "timeframe": "H4",
            "direction": "BULLISH",
            "setup_id": "setup-1",
            "repeated_observations": 3,
            "fields": ["close"],
            "tolerance_pips": 2.0,
            "ohlc_difference_pips": {"close": 6.8},
            "twelve_data": {"open": 0.80129, "high": 0.80216, "low": 0.80128, "close": 0.8021},
            "fxcm": {"open": 0.80104, "high": 0.80176, "low": 0.80091, "close": 0.80142},
        }
        with patch.object(service, "send_html", return_value=True) as send_html:
            self.assertTrue(service.send_event(event))
        text = send_html.call_args.args[0]
        self.assertIn("same candle period, but their prices differ", text)
        self.assertIn("Twelve Data is guiding the BUY decision", text)
        self.assertIn("informational discrepancy alert", text)
        self.assertIn("does not request a decision, decline, or invalidate the setup", text)
        self.assertNotIn("If unanswered", text)
        self.assertNotIn("declined", text.lower())
        self.assertEqual(len(send_html.call_args.args), 1)

    def test_crypto_provider_discrepancy_is_labeled_without_controls(self):
        validation = {
            "market_data": {
                "BTCUSDT": {
                    "H4": {
                        "open": 100000,
                        "high": 101000,
                        "low": 99000,
                        "close": 100000,
                    }
                }
            }
        }
        provider = {
            "enabled": True,
            "connected": True,
            "stale": False,
            "symbols": {
                "BTCUSDT": {
                    "price": 104000,
                    "price_difference_pct": 4.0,
                    "ohlc": {"H4": None},
                    "ohlc_comparison": {
                        "H4": {
                            "status": "COMPARED",
                            "ohlc_difference": {"close": 4000},
                        }
                    },
                }
            },
        }

        text = _format_validation_provider("binance", provider, "BTCUSDT", validation)

        self.assertIn("Discrepancy: <b>HIGH</b>", text)
        self.assertNotIn("YES", text)
        self.assertNotIn("NO", text)


if __name__ == "__main__":
    unittest.main()