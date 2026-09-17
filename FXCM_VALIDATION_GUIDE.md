# Scanner and FXCM Validation Guide

## What the system does

The scanner uses Twelve Data as its primary source for the trading strategy. FXCM is a separate reference source used to check whether the market candles agree.

The flow is:

1. The FXCM bridge reads prices and completed candles from FXCM.
2. The bridge sends them to `/fxcm/market-data` every 30 seconds.
3. The scanner stores the latest FXCM data in memory.
4. The scanner compares FXCM candles with the latest Twelve Data candles.
5. The comparison appears in `/status` and can create Telegram warnings.
6. The trading strategy continues to use Twelve Data. FXCM does not replace it.

## What is working

### FXCM connection

A successful log entry looks like:

```text
POST /fxcm/market-data HTTP/1.1 200 OK
```

This means the scanner accepted the bridge payload.

### Redis and scanner

These messages mean the scanner started normally and restored its state:

```text
Redis connection established
State loaded from Redis
Scanner started
```

Redis preserves active setups, alert history, and reset history across restarts.

### Completed FXCM candles

The bridge now ignores a candle that is still forming. It sends the latest completed candle and its actual candle timestamp.

For example, an H4 candle may be reported as:

```text
13:00 to 17:00 UTC
```

The bridge will not send the `17:00 to 21:00` candle until it has completed.

## Reading `/status`

Each FXCM pair contains:

```json
"ohlc_comparison": {
  "D1": { ... },
  "H4": { ... }
}
```

### `OK`

The two providers are looking at the same candle period and every OHLC field is within the configured tolerance.

This is the cleanest comparison result.

### `WARN`

The two providers are looking at the same candle period, but one or more OHLC fields differ by more than the normal tolerance and remain below the mismatch threshold.

This means:

- the comparison is valid;
- the difference is worth watching;
- it is not automatically proof that one provider is wrong.

### `MISMATCH`

The two providers are looking at the same candle period, but one or more fields differ substantially.

The scanner records a warning event in `snapshot.meta.events` and sends an FXCM OHLC warning to Telegram when Telegram is available.

A mismatch is a data-quality warning. It does not change the trading strategy or automatically cancel a setup.

### `BOUNDARY_MISMATCH`

The providers are reporting different time periods. Their OHLC numbers must not be compared as though they came from the same candle.

For example:

```text
FXCM:       13:00 to 17:00 UTC
TwelveData: 17:00 to 21:00 UTC
```

This is a candle-selection problem, not necessarily a price disagreement.

### `UNAVAILABLE`

One provider does not have a usable candle for that timeframe. This is normal during startup or when a provider has not returned data yet.

### Stale or disconnected provider

The live price section may show statuses such as:

```text
OK
STALE
DISCONNECTED
NO_PRIMARY
```

These describe the live provider connection and price comparison. They are separate from OHLC candle status.

## Daily and H4 candle boundaries

### H4

The bridge now selects completed FXCM H4 candles. A healthy H4 comparison should show matching periods, for example:

```text
FXCM:       2026-09-17 13:00 to 17:00 UTC
TwelveData: 2026-09-17 13:00 to 17:00 UTC
```

If the periods match, an H4 `WARN` or `MISMATCH` represents a genuine OHLC difference for the same candle.

### D1

FXCM and Twelve Data use different daily boundaries:

```text
FXCM:       21:00 to 21:00 UTC
TwelveData: 00:00 to 00:00 UTC
```

Therefore, D1 commonly shows:

```text
status: BOUNDARY_MISMATCH
period_status: DIFFERENT_PERIOD
```

This is expected unless both sources provide candles built from the same daily boundary.

Do not interpret this as proof that FXCM prices are wrong. It means the daily candles cover different trading sessions.

## Crypto pairs: BTCUSDT and ETHUSDT

Crypto pairs are still part of the project. The supported crypto pairs are:

```text
BTCUSDT
ETHUSDT
```

Crypto uses three data sources:

| Provider | Role | Symbol used by the provider |
| --- | --- | --- |
| Twelve Data | Primary strategy source | BTC/USD or ETH/USD |
| Binance | Independent live-price and candle reference | BTCUSDT or ETHUSDT |
| Coinbase | Independent live-price and candle reference | BTC-USD or ETH-USD |

The crypto provider feeds do not replace Twelve Data and do not create setups. The strategy continues to calculate the daily setup, H4 breakout, retest, continuation, and final signal from Twelve Data.

### What Binance and Coinbase validate

For each crypto pair, Binance and Coinbase provide:

- live price;
- live-feed timestamp;
- completed D1 candle;
- completed H4 candle;
- price difference from Twelve Data;
- OHLC comparison with Twelve Data.

The candle comparison checks the same four fields:

```text
Open
High
Low
Close
```

Crypto candles use UTC boundaries. A healthy same-period result looks like:

```json
"H4": {
  "status": "COMPARED",
  "timestamp_difference_seconds": 0,
  "ohlc_difference": {
    "open": 0.0,
    "high": 0.0,
    "low": 0.0,
    "close": 0.0
  }
}
```

The crypto comparison is diagnostic. A Binance or Coinbase difference does not change the Twelve Data strategy state and does not automatically cancel a crypto setup.

### Crypto live-price statuses

In `/status`, each crypto provider can show:

```text
OK
PRICE_DISCREPANCY_WARNING
STALE
DISCONNECTED
NO_PRIMARY
DISABLED
```

Meaning:

- `OK`: the provider is connected and its live price is within the configured percentage threshold of Twelve Data.
- `PRICE_DISCREPANCY_WARNING`: the provider is connected, but its live price differs from Twelve Data beyond the configured percentage threshold.
- `STALE`: the provider has not delivered a recent live message.
- `DISCONNECTED`: no active live connection is available.
- `NO_PRIMARY`: the provider has data, but the scanner does not currently have the corresponding Twelve Data price.
- `DISABLED`: that provider was not enabled in the environment.

### Crypto candle statuses

Crypto candle comparisons are separate from live-price status. A provider may have cached candles while its live ticker is stale or disconnected.

For example:

```text
Coinbase live status: STALE
Coinbase D1 candle: available
Coinbase H4 candle: available
```

This means the stored Coinbase candles can still be displayed for reference, but the current Coinbase live price should not be treated as fresh.

### Crypto Telegram display

When a final BTCUSDT or ETHUSDT setup alert is sent, the provider section includes all three crypto sources when validation data is available:

```text
DATA SOURCE COMPARISON

Binance: CONNECTED
  Price: ...
  D1: O=... H=... L=... C=...
  H4: O=... H=... L=... C=...

Coinbase: CONNECTED
  Price: ...
  D1: O=... H=... L=... C=...
  H4: O=... H=... L=... C=...

Twelve Data (Primary): ACTIVE
  D1: O=... H=... L=... C=...
  H4: O=... H=... L=... C=...
```

The final alert shows the providers as separate blocks, not as a fixed-width side-by-side table. A provider that is unavailable is shown as `DISCONNECTED`, `STALE`, or `DISABLED` rather than being silently omitted.

Crypto validation data is not currently used to block the final strategy alert. Treat it as a cross-check of market data quality.

### How a trader should interpret crypto data

For a crypto setup:

1. Read the Twelve Data strategy state first. It determines whether the setup exists.
2. Check whether Binance and Coinbase are connected and fresh.
3. Compare the live prices across providers.
4. Check D1 and H4 candle timestamps before judging OHLC differences.
5. If one provider is stale or disconnected, treat that provider as unavailable evidence rather than as confirmation of direction.
6. If providers disagree materially while the periods match, pause and verify the chart or exchange feed before relying heavily on the comparison.

The safest interpretation is:

```text
Twelve Data = strategy decision
Binance/Coinbase = independent market-data checks
```

## What to expect on Telegram

### Stage alerts

During setup detection, Telegram can send short progress messages such as:

```text
DAILY STAGE PASSED: EURUSD
H4 STAGE PASSED: EURUSD
RETEST CONFIRMED: EURUSD
CONTINUATION CONFIRMED: EURUSD
```

These messages describe the strategy state. They do not contain a provider comparison table.

### Final setup alert

When the full strategy sequence succeeds, Telegram sends the final price-action alert. It includes the setup details, such as:

- pair;
- timeframe;
- direction;
- key level;
- rejection status;
- breakout status;
- retest status;
- continuation status;
- daily and H4 confirmations.

The final alert also includes provider data:

- Forex: Twelve Data and FXCM;
- Crypto: Twelve Data, Binance, and Coinbase.

Provider blocks are shown one after another. They are not a true column table in the final setup alert.

### FXCM OHLC mismatch alert

When a same-period FXCM candle is classified as `MISMATCH`, Telegram sends a side-by-side fixed-width table similar to:

```text
FXCM OHLC MISMATCH: EURUSD H4

Field       TwelveData       FXCM          Delta (pips)
Open        1.14915          1.14757       15.8
High        1.14977          1.14785       19.2
Low         1.14743          1.14730       1.3
Close       1.14759          1.14783       2.4

Fields: open, high
Tolerance: 2.0 pips
```

The values come from the actual candles used in the comparison. They are not copied from Twelve Data.

### Invalidation and decision alerts

Telegram can also send:

- setup invalidation messages;
- news warnings;
- aging warnings;
- weekend-gap warnings;
- YES/NO rule-decision prompts;
- TradingView email alerts.

These messages describe the event that occurred. They do not normally include the full provider comparison table.

## How a trader should read the information

Use the strategy state and provider validation as two separate questions.

### Question 1: Is there a valid strategy setup?

Follow the state sequence:

```text
WATCHING
-> daily confirmation
-> H4 confirmation
-> WAITING_FOR_RETEST
-> RETEST_CONFIRMED
-> CONTINUATION_CONFIRMED
-> final alert
```

A provider warning does not itself confirm a trade. The strategy rules still determine whether a setup exists.

### Question 2: Are the data sources comparing the same market period?

Check `period_status` first:

- `SAME_PERIOD`: OHLC comparison is meaningful.
- `DIFFERENT_PERIOD`: do not judge the OHLC numbers against each other.
- `INVALID_PERIOD`: timestamp data is unusable.

Only interpret OHLC differences as meaningful after confirming `SAME_PERIOD`.

### Practical interpretation

- `SAME_PERIOD + OK`: providers agree within tolerance.
- `SAME_PERIOD + WARN`: monitor the difference; verify the chart or broker feed if the setup is important.
- `SAME_PERIOD + MISMATCH`: treat the candle data as conflicted and investigate before relying on that comparison.
- `DIFFERENT_PERIOD + large differences`: expected possibility; the candles are not equivalent.
- `UNAVAILABLE`, `STALE`, or `DISCONNECTED`: validation is incomplete, not automatically bearish or bullish.

The validator is a quality-control tool. It does not decide whether to buy or sell and does not replace chart, broker, risk, or execution checks.

## Setup lifecycle after a final alert

After a final alert is successfully sent:

1. The alert key and historical details are preserved.
2. The completed setup is archived as an event.
3. The active pair is reset to `WATCHING`.
4. The old setup ID is cleared.
5. A later setup receives a new setup ID.

This prevents an old completed setup from blocking a new one.

## Recommended checks after deployment

Open `/status` and check:

```text
scanner_running: true
fxcm_validation.connected: true
fxcm_validation.stale: false
```

For each Forex pair:

```text
ohlc_comparison.H4.period_status: SAME_PERIOD
```

For D1, expect the explicit FXCM/Twelve Data boundary difference unless the providers are configured to use the same daily session.

For a real same-period warning or mismatch, inspect:

```text
status
period_status
fxcm_period
 twelve_data_period
ohlc_difference_pips
mismatch_fields
```

For crypto, also check whether Coinbase and Binance are connected. A candle can be present while live ticker validation is stale or disconnected.
