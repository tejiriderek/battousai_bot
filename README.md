# Battoujutsu Forex Scanner

Production scanner for EURUSD, GBPUSD, USDJPY, EURAUD, NZDCAD, BTCUSDT, and ETHUSDT on Daily and H4. It waits for a full sequence before alerting:

Daily rejection or breakout → aligned H4 breakout → exact retest of the broken A/V level (RBS/SBR) → continuation close.

Alerts are sent only at `CONTINUATION_CONFIRMED`, then the pair moves to `ALERT_SENT` so the same setup cannot fire twice.

## Strategy (strict)

1. **Key levels** (last 80 closed candles)
   - **A-Shape** resistance: sharp swing high
   - **V-Shape** support: sharp swing low
   - **OCL**: open–close body of a reversal candle
2. **Sweep / rejection**
   - Bullish: `Low < V-level` and `Close > V-level`
   - Bearish: `High > A-level` and `Close < A-level`
3. **Daily** must print rejection or a close beyond a recent A/V (or recent high/low). Current or previous **closed** daily candle only.
4. **H4** must match Daily direction and close beyond a previous high / A-Shape (bullish) or previous low / V-Shape (bearish).
5. **Retest** must tap the **exact** broken H4 level and close back on the breakout side.
6. **Continuation** is the next H4 candle after that retest, closing still beyond the broken level.

Incomplete candles are dropped. The forming bar is never used.

## Telegram

Sending matches the existing Laravel/JS client:

`POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage`  
JSON body: `chat_id`, `text`, `parse_mode: HTML`, `disable_web_page_preview: true`

Do not hardcode the token or chat id. Copy them into `.env` from your Telegram bot (BotFather token + numeric chat id).

## Local setup

```powershell
cd C:\Users\USER\Desktop\Python\battoujutsu_bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:

```
TWELVE_DATA_API_KEY=your_twelve_data_key
TWELVE_DATA_API_KEY_SECOND=your_second_twelve_data_key
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
CONFIRMATION_REPROMPT_MINUTES=5
CONFIRMATION_MAX_PROMPTS=5
```

Twelve Data key: [twelvedata.com/apikey](https://twelvedata.com/apikey).  
Forex time series credits are consumed per pair per timeframe. The client spaces requests by 15 seconds. Configure a second key in `TWELVE_DATA_API_KEY_SECOND` for failover; each key is cooled down independently after a rate-limit or daily-credit response and becomes eligible again after its limit window or daily reset.

Run:

```powershell
python main.py
```

Open `http://127.0.0.1:8080/status`. You should see `"scanner_running": true`.

## Deploy on Render

1. Create a GitHub repository and push this project (`.env` is gitignored).
2. On [render.com](https://render.com) choose **New → Blueprint** and point it at the repo, or **New → Web Service** and use:
   - Runtime: Python
   - Build: `pip install -r requirements.txt`
   - Start: `python main.py`
   - Health check path: `/status`
3. In Environment, set:
   - `TWELVE_DATA_API_KEY`
   - `TWELVE_DATA_API_KEY_SECOND`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `SCAN_INTERVAL_SECONDS=1800` (30 minutes; keeps the default 14-request scan under 800 daily credits)
   - `LOG_LEVEL=INFO`
4. Deploy. Render injects `PORT`; `main.py` binds it automatically.
5. Confirm `https://<your-service>.onrender.com/status` returns `scanner_running: true`.
6. Free Render instances sleep after idle HTTP. Ping `/status` every few minutes (UptimeRobot or similar) if you need the loop to keep running, or use a paid instance.

## Files

| File | Role |
| --- | --- |
| `main.py` | Scanner thread + uvicorn |
| `config.py` | Pairs, lookbacks, env |
| `data_service.py` | Twelve Data + validation |
| `level_detector.py` | A-Shape, V-Shape, OCL |
| `strategy.py` | Sweep / BOS / retest / continuation |
| `state_manager.py` | Per-pair machine, disk persist |
| `telegram_service.py` | HTML `sendMessage` |
| `web_server.py` | `/status` and `/health` |

## Notes

- State lives in `data/state.json` (ephemeral on free Render; a restart can lose in-flight setups but will not re-send an alert if the file is still there).
- JPY pairs use 0.01 pip size for epsilon only; the retest still targets the stored broken price.
- This bot does not place trades. It only scans and alerts.
- Unanswered Telegram confirmations are re-sent with YES/NO buttons every five minutes; after five unanswered prompts, the bot auto-approves with YES.
