"""Read-only FXCM ForexConnect API bridge for the main scanner."""

from __future__ import annotations

import logging
import os
import time
import threading
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()

log = logging.getLogger("fxcm_bridge")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
PAIRS = {"EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY", "EURAUD": "EUR/AUD", "NZDCAD": "NZD/CAD"}

# Flask app for health checks
app = Flask(__name__)
bridge_running = False
bridge_thread = None


@app.route("/health")
def health():
    """Health check endpoint for UptimeRobot."""
    return jsonify({"status": "ok", "bridge_running": bridge_running})


def bridge_worker():
    """Run the FXCM bridge in a background thread."""
    global bridge_running
    bridge_running = True
    try:
        main()
    except Exception as e:
        log.error("Bridge worker failed: %s", e)
        bridge_running = False


def main() -> None:
    if os.getenv("FXCM_ENABLED", "false").lower() not in {"true", "1", "yes"}:
        raise RuntimeError("FXCM_ENABLED is not true")
    
    username = os.getenv("FXCM_USERNAME", "")
    password = os.getenv("FXCM_PASSWORD", "")
    receiver = os.getenv("SCANNER_RECEIVER_URL", "").rstrip("/")
    secret = os.getenv("FXCM_BRIDGE_SHARED_SECRET", "")
    
    if not username or not password or not receiver or not secret:
        raise RuntimeError("FXCM_USERNAME, FXCM_PASSWORD, SCANNER_RECEIVER_URL, and FXCM_BRIDGE_SHARED_SECRET are required")

    from forexconnect import ForexConnect, SessionStatusListener, ResponseListener

    while True:
        try:
            session = ForexConnect()
            log.info("Connecting to FXCM ForexConnect API...")
            session.login(
                username,
                password,
                "https://www.fxcorporate.com/Hosts.jsp",
                os.getenv("FXCM_CONNECTION", "demo")
            )
            log.info("FXCM connected via ForexConnect API")
            
            while True:
                payload = collect_snapshot(session)
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


def collect_snapshot(session) -> dict:
    pairs = {}
    try:
        # Get the Offers table using TableManager
        table_manager = session.table_manager
        offers_table = table_manager.get_table("Offers")
        
        # Convert to pandas DataFrame for easier manipulation
        from forexconnect.common import Common
        df = Common.convert_table_to_dataframe(offers_table)
        
        for pair, instrument in PAIRS.items():
            try:
                # Find the row for this instrument
                instrument_rows = df[df['instrument'] == instrument]
                if instrument_rows.empty:
                    log.warning("No data found for %s", instrument)
                    continue
                
                row = instrument_rows.iloc[-1]
                bid = float(row['bid'])
                ask = float(row['ask'])
                history = {
                    timeframe: get_history_candles(session, instrument, timeframe)
                    for timeframe in ("D1", "H4")
                }
                
                pairs[pair] = {
                    "price": (bid + ask) / 2,
                    "bid": bid,
                    "ask": ask,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "ohlc": {
                        timeframe: candles[-1] if candles else None
                        for timeframe, candles in history.items()
                    },
                    "history": history,
                }
            except Exception as e:
                log.warning("Failed to get data for %s: %s", pair, e)
                continue
    except Exception as e:
        log.error("Failed to get offers table: %s", e)
    
    return {"source": "fxcm", "account_type": "demo", "sent_at": datetime.now(timezone.utc).isoformat(), "pairs": pairs}


def get_history_candle(session, instrument: str, timeframe: str) -> dict | None:
    candles = get_history_candles(session, instrument, timeframe)
    return candles[-1] if candles else None


def get_history_candles(session, instrument: str, timeframe: str) -> list[dict]:
    try:
        import pandas as pd

        period = timeframe if timeframe in {"D1", "H4"} else "D1"
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=100 if period == "D1" else 20)
        try:
            history = session.get_history(instrument, period, start, now)
        except AttributeError:
            from forexconnect import LiveHistoryCreator

            history = LiveHistoryCreator.create(session).get_history(instrument, period, 100)
        if history is None or len(history) == 0:
            return None

        frame = history if isinstance(history, pd.DataFrame) else pd.DataFrame(history)
        if frame.empty:
            return None
        frame = frame.sort_values(frame.columns[0]).reset_index(drop=True)

        def value(row, names):
            for name in names:
                if name in frame.columns and row[name] is not None:
                    return row[name]
            return None

        duration = pd.Timedelta(days=1 if period == "D1" else 4 / 24)
        completed = []
        for _, row in frame.iterrows():
            timestamp = _serialize_timestamp(value(row, ("Date", "date", "Time", "time")))
            parsed = pd.Timestamp(timestamp) if timestamp else None
            if parsed is not None and parsed.tzinfo is None:
                parsed = parsed.tz_localize("UTC")
            if parsed is not None and parsed.to_pydatetime() + duration.to_pytimedelta() <= now:
                completed.append((parsed, row))
        result = []
        for candle_time, candle in completed:
            open_value = value(candle, ("BidOpen", "Open", "open"))
            high_value = value(candle, ("BidHigh", "High", "high"))
            low_value = value(candle, ("BidLow", "Low", "low"))
            close_value = value(candle, ("BidClose", "Close", "close"))
            if any(value is None for value in (open_value, high_value, low_value, close_value)):
                values = list(candle.values)
                if len(values) < 5:
                    continue
                open_value, high_value, low_value, close_value = values[1:5]
            result.append(
                {
                    "timestamp": candle_time.isoformat(),
                    "open": float(open_value),
                    "high": float(high_value),
                    "low": float(low_value),
                    "close": float(close_value),
                    "completed": True,
                }
            )
        return result
    except Exception as e:
        log.warning("Failed to get history for %s %s: %s", instrument, timeframe, e)
        return None


def _serialize_timestamp(value) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        import pandas as pd

        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC").isoformat()
    except (TypeError, ValueError):
        log.warning("Could not parse FXCM candle timestamp: %r", value)
        return None


if __name__ == "__main__":
    # Start bridge worker in background thread
    bridge_thread = threading.Thread(target=bridge_worker, daemon=True)
    bridge_thread.start()
    
    # Start Flask server (Render provides PORT env var)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
