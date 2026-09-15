import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import config
from state_manager import StateManager
from tradingview_email import normalize_symbol, parse_alert


class TradingViewEmailParserTests(unittest.TestCase):
    def test_parses_valid_breakout(self):
        alert = parse_alert(
            "TV_ALERT|symbol=OANDA:EURUSD|price=1.08534|time=2026-09-15T06:30:00Z|interval=240|direction=LONG|type=BREAKOUT",
            "message-1",
        )
        self.assertEqual(alert.symbol, "EURUSD")
        self.assertEqual(alert.alert_type, "BREAKOUT")
        self.assertEqual(alert.price, 1.08534)

    def test_parses_valid_retest(self):
        alert = parse_alert(
            "TV_ALERT|symbol=USD/JPY|price=147.250|time=2026-09-15T06:31:00Z|interval=240|direction=SHORT|type=RETEST"
        )
        self.assertEqual(alert.symbol, "USDJPY")
        self.assertEqual(alert.direction, "SHORT")

    def test_rejects_malformed_and_unknown_alerts(self):
        with self.assertRaisesRegex(ValueError, "missing or invalid price"):
            parse_alert("TV_ALERT|symbol=EURUSD|direction=LONG|type=BREAKOUT")
        with self.assertRaises(ValueError):
            parse_alert("TV_ALERT|symbol=EURUSD|price=abc|type=BREAKOUT|time=2026-09-15T06:30:00Z")
        with self.assertRaisesRegex(ValueError, "unknown TradingView symbol"):
            parse_alert("TV_ALERT|symbol=UNKNOWNPAIR|price=1.2345|type=BREAKOUT|time=2026-09-15T06:30:00Z")

    def test_normalizes_supported_prefixes(self):
        self.assertEqual(normalize_symbol("FX:EURUSD"), "EURUSD")
        self.assertEqual(normalize_symbol("BINANCE:BTCUSDT"), "BTCUSDT")

    def test_processed_message_ids_are_persistent_and_deduplicated(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            states = StateManager(path)
            states.mark_tradingview_email_processed("message-1")
            self.assertTrue(states.has_processed_tradingview_email("message-1"))
            states.mark_tradingview_email_processed("message-1")
            reloaded = StateManager(path)
            self.assertTrue(reloaded.has_processed_tradingview_email("message-1"))


if __name__ == "__main__":
    unittest.main()