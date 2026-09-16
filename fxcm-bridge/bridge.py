"""Read-only FXCM ForexConnect bridge for the main scanner."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("fxcm_bridge")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
PAIRS = {"EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY", "EURAUD": "EUR/AUD", "NZDCAD": "NZD/CAD"}


def main() -> None:
    if os.getenv("FXCM_ENABLED", "false").lower() not in {"true", "1", "yes"}:
        raise RuntimeError("FXCM_ENABLED is not true")
    username = os.getenv("FXCM_USERNAME", "")
    password = os.getenv("FXCM_PASSWORD", "")
    receiver = os.getenv("SCANNER_RECEIVER_URL", "").rstrip("/")
    secret = os.getenv("FXCM_BRIDGE_SHARED_SECRET", "")
    if not username or not password or not receiver or not secret:
        raise RuntimeError("FXCM_USERNAME, FXCM_PASSWORD, SCANNER_RECEIVER_URL, and FXCM_BRIDGE_SHARED_SECRET are required")

    from forexconnect import ForexConnect

    while True:
        try:
            with ForexConnect() as fx:
                fx.login(
                    username,
                    password,
                    os.getenv("FXCM_SERVER", ""),
                    os.getenv("FXCM_CONNECTION", "demo"),
                    "",
                    "",
                    None,
                )
                log.info("FXCM authenticated")
                while True:
                    payload = collect_snapshot(fx)
                    response = requests.post(
                        receiver,
                        json=payload,
                        headers={"X-FXCM-Bridge-Secret": secret},
                        timeout=10,
                    )
                    response.raise_for_status()
                    time.sleep(max(5, int(os.getenv("FXCM_POLL_SECONDS", "30"))))
        except Exception as exc:
            log.warning("FXCM bridge disconnected: %s; retrying", type(exc).__name__)
            time.sleep(10)


def collect_snapshot(fx) -> dict:
    offers = {row.instrument: row for row in fx.get_table(ForexConnect.OFFERS)}
    pairs = {}
    for pair, instrument in PAIRS.items():
        row = offers.get(instrument)
        if row is None:
            continue
        bid = float(row.bid)
        ask = float(row.ask)
        pairs[pair] = {
            "price": (bid + ask) / 2,
            "bid": bid,
            "ask": ask,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ohlc": {
                "D1": history_candle(fx, instrument, "D1"),
                "H4": history_candle(fx, instrument, "H4"),
            },
        }
    return {"source": "fxcm", "account_type": "demo", "sent_at": datetime.now(timezone.utc).isoformat(), "pairs": pairs}


def history_candle(fx, instrument: str, timeframe: str) -> dict | None:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=5 if timeframe == "D1" else 2)
    rows = fx.get_history(instrument, timeframe, start, now, 10)
    if not rows:
        return None
    row = rows[-1]
    return {
        "timestamp": datetime.fromisoformat(str(row["Date"])).astimezone(timezone.utc).isoformat(),
        "open": float(row["BidOpen"]),
        "high": float(row["BidHigh"]),
        "low": float(row["BidLow"]),
        "close": float(row["BidClose"]),
        "completed": True,
    }


if __name__ == "__main__":
    main()
