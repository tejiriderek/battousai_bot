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

## Optional TradingView email bridge

TradingView email alerts are an optional, independent confirmation path. The bridge is disabled by default and uses only Python's standard library IMAP client. It never replaces Twelve Data, changes the strategy, or places trades. When enabled, it reads new TradingView emails in a background thread, validates and deduplicates them in the existing Redis-backed state, compares the alert price with the latest Twelve Data H4 close, and sends a separate Telegram message containing both sources and the current scanner state.

Add these variables to `.env` only when you are ready to test the bridge:

```
TRADINGVIEW_EMAIL_ENABLED=true
TRADINGVIEW_EMAIL_HOST=imap.gmail.com
TRADINGVIEW_EMAIL_PORT=993
TRADINGVIEW_EMAIL_USERNAME=your-address@gmail.com
TRADINGVIEW_EMAIL_PASSWORD=your-google-app-password
TRADINGVIEW_EMAIL_FOLDER=INBOX
TRADINGVIEW_EMAIL_POLL_INTERVAL=30
TRADINGVIEW_EMAIL_DRY_RUN=true
TRADINGVIEW_PROCESS_EXISTING_ON_START=false
TRADINGVIEW_ALLOWED_SENDER=tradingview.com
```

For Gmail, enable IMAP if your account offers that setting and use a Google App Password when required by your account. Do not use or commit your normal Gmail password. The connection uses SSL/TLS and credentials are never logged. Start with `TRADINGVIEW_EMAIL_DRY_RUN=true`; emails are parsed and marked as processed but are not sent to Telegram. Set it to `false` only after the parser has been verified.

In TradingView, create an alert for an indicator or condition, enable the **Email notification** option, and paste this exact custom message into the alert's **Message** field:

```
TV_ALERT|symbol={{exchange}}:{{ticker}}|price={{close}}|time={{time}}|interval={{interval}}|direction=LONG|type=BREAKOUT
```

TradingView replaces these placeholders in alert messages: `{{exchange}}`, `{{ticker}}`, `{{close}}`, `{{time}}`, and `{{interval}}`. The direction and type are literal values, so make separate alert conditions/messages for SHORT, RETEST, REJECTION, LEVEL_FLIP, SETUP, or INVALIDATION as needed. The parser accepts broker prefixes such as `OANDA:EURUSD` and normalizes them to the configured pair. TradingView plan availability for email alerts can change; email delivery is slower and less direct than a webhook.

Setup checklist:

1. Create the TradingView alert and enable email notification; do not enable webhook.
2. Confirm the custom message is in the email alert's Message field.
3. Send or wait for one alert and confirm it reaches the mailbox.
4. Set the bridge variables in Render, deploy, and check `/status` for `tradingview_email`.
5. Keep dry-run enabled while checking logs and parser behavior.
6. Set `TRADINGVIEW_EMAIL_DRY_RUN=false` and redeploy only when ready for Telegram messages.

The email bridge sends a separate Telegram message such as `TRADINGVIEW EMAIL ALERT`, including TradingView price, Twelve Data price, their difference, timeframe, alert time, scanner state, setup ID, and the existing YES/NO prompt when that pair has an unresolved setup decision. It does not silently overwrite Twelve Data state.

## TradingView webhook

`POST /tradingview-webhook` accepts `pair`, `event`, `tv_price`, `tf`, and an ISO-8601 `time`. It fetches the matching Twelve Data candle for comparison, stores `tv_price` as the official level for that pair, and sends both prices plus their difference to Telegram. A Twelve Data failure does not reject the webhook. The existing 30-minute Twelve Data scanner continues running as a backup.

For a free TradingView account, use one Pine script on one chart with `request.security()` calls for the seven configured symbols, then create one alert using **Any alert() function call** and point its webhook URL to:

`https://<your-render-service>.onrender.com/tradingview-webhook`

Use exchange-qualified symbols that match the feed you want, such as `OANDA:EURUSD` for forex and `BINANCE:BTCUSDT` / `BINANCE:ETHUSDT` for crypto. TradingView plan limits and alert quotas can change, so confirm the current limit in the account UI. A single multi-symbol script is preferable to creating multiple accounts.

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

- State is loaded from Redis key `battousai:scanner:state` when `UPSTASH_REDIS_URL` is configured, with `data/state.json` as a fallback. Keep the same Redis URL across redeploys to preserve in-flight setups.
- Weekend gaps are checked only while the latest closed forex candle is from Monday; they are not treated as ongoing gaps later in the week.
- `/status` reports the optional TradingView email bridge connection and last alert status when the application is running.
- JPY pairs use 0.01 pip size for epsilon only; the retest still targets the stored broken price.
- This bot does not place trades. It only scans and alerts.
- Unanswered Telegram confirmations are re-sent with YES/NO buttons every five minutes; after five unanswered prompts, the bot auto-approves with YES.
