You are working on my existing Python Forex scanner bot.

1. PROJECT

Project path:

"C:\Users\USER\Desktop\Python\battoujutsu_bot"

The bot is deployed on Render.

It currently scans these 7 Forex pairs:

- EURUSD
- GBPUSD
- USDJPY
- USDCHF
- USDCAD
- AUDUSD
- NZDUSD

It scans:

- Daily timeframe
- H4 timeframe

The existing strategy/state flow is approximately:

"WATCHING → DAILY_REJECTION/BREAKOUT → H4_WAITING → WAITING_FOR_RETEST → RETEST_CONFIRMED → CONTINUATION_CONFIRMED → ALERT_SENT"

Important: do not rewrite the entire project from scratch.

First inspect the existing implementation and understand how everything currently works.

Relevant files include:

- "config.py"
- "data_service.py"
- "level_detector.py"
- "strategy.py"
- "state_manager.py"
- "telegram_service.py"
- "main.py"
- "web_server.py"
- "requirements.txt"
- tests
- Render/deployment configuration
- any persistence/state files
- any existing news/calendar implementation

Search the entire project for:

- state transitions
- reset functions
- setup deletion
- timeout/expiry logic
- weekend logic
- gap detection
- news/FOMC/NFP/CPI logic
- Telegram notifications
- Telegram callbacks/buttons
- persistence/load/save
- breakout detection
- rejection/sweep detection
- ATR calculations
- level calculations

Do not make assumptions based on filenames alone.

---

2. MOST IMPORTANT REQUIREMENT: INVESTIGATE THE EXISTING RESET BUG

There was a real observed issue involving setups such as:

- GBPUSD
- NZDCAD / any currently supported pair if present in the actual code

A setup was previously observed progressing into states such as:

"WAITING_FOR_RETEST"

or

"H4_WAITING"

and later returning to:

"WATCHING"

with important setup fields becoming "null".

Do NOT assume the cause.

Find the actual code path responsible.

Trace every possible path that can change:

"WAITING_FOR_RETEST → WATCHING"

and similar transitions.

Search for:

- "reset"
- "clear"
- "invalidate"
- "expire"
- "timeout"
- "new week"
- "Monday"
- "weekend"
- "gap"
- "48 hours"
- "ATR"
- "WATCHING"
- "state ="
- setup field clearing
- state reconstruction after restart
- stale state cleanup

For every reset path, determine:

1. What triggered it?
2. Which function performed it?
3. Why was the setup reset?
4. Was the reason price-based, time-based, news-based, weekend-based, restart-based, or something else?
5. Were the setup fields intentionally cleared?
6. Was the reset persisted?
7. Was Telegram notified?
8. Could the reset happen silently?

Fix the actual cause while implementing the requirements below.

Do not merely hide the symptom.

---

3. DO NOT USE TIME AS A HARD INVALIDATION RULE

Remove any rule that automatically kills a setup simply because a certain amount of time has passed.

For example, if there is currently a rule such as:

"if setup_age > 48 hours: reset"

remove that behavior.

Time can be used for:

- informational aging
- warnings
- diagnostics
- stale-state monitoring

but time alone must never invalidate a valid setup.

Instead use actual market conditions.

Aging warnings

You may warn when a setup becomes old, for example:

- after 12 H4 candles / approximately 48 hours
- after 24 H4 candles / approximately 96 hours

But these must be warnings only.

Example:

"⏳ GBPUSD — SETUP AGING"

"This setup has been waiting for 12 H4 candles."

Do NOT automatically reset it.

---

4. PRICE-BASED INVALIDATION

A setup should only be hard-invalidated when an actual strategy condition makes it invalid.

Implement/configure the following.

Rule A — Counter-close

If the setup is bullish and the market produces a valid H4/Daily candle that closes back through the broken level against the setup direction, invalidate it.

If bearish, apply the inverse rule.

Example bullish setup:

- broken level = resistance
- price closes back below that level on the relevant confirmation timeframe
- this can invalidate the setup

Example notification:

"❌ GBPUSD — SETUP INVALIDATED"

"Reason: H4 counter-close back through the broken level."

Do not classify a wick alone as a counter-close.

Rule B — Maximum distance

If price moves excessively far away from the relevant H4 level before the retest/continuation process completes, invalidate it.

Use a configurable value such as:

"MAX_DISTANCE_PIPS = 100"

Do not hard-code it inside strategy logic.

Example:

"❌ GBPUSD — SETUP INVALIDATED"

"Reason: Price moved 117 pips away from the H4 level, exceeding the configured 100-pip maximum distance."

The exact calculation must respect pair pip size.

Rule C — Hard technical failure

Any existing genuine strategy condition that means the setup is no longer valid should continue to invalidate it.

But distinguish:

- genuine technical failure
- warning
- unusual market event

Do not convert warnings into automatic resets.

---

5. WEEKEND GAP LOGIC — IMPORTANT

A weekend gap is an event, NOT automatically a strategy failure.

The previous behavior of automatically invalidating a setup because Monday opened far away from Friday close must be removed.

If there is logic similar to:

"if Monday_open > 2 * ATR from Friday_close: reset"

REMOVE the automatic reset behavior.

Instead:

- detect the gap
- record it
- notify Telegram
- preserve the existing setup
- continue monitoring

A large gap can be flagged as abnormal, but it must not automatically destroy the setup.

---

6. WEEKEND GAP DETECTION

Implement a configurable threshold such as:

"GAP_THRESHOLD_PIPS = 15"

The threshold must be configurable.

Calculate the weekend gap using:

"Monday/current session open - previous Friday/session close"

(or the correct broker/data-provider equivalent based on how the existing candle data works).

Normalize the result into pips.

If the absolute gap exceeds the configured threshold:

mark the candle/event as a weekend gap.

For example:

"gap_candle = True"

But do not treat that gap as a technical breakout.

---

7. GAP MUST NOT COUNT AS BREAKOUT

This is extremely important.

Suppose Friday closed below a Daily resistance.

Monday opens above that resistance because of a gap.

That does NOT automatically mean the Daily level has been broken by a valid candle body.

The gap should not be interpreted as a normal candle-body breakout.

The scanner must wait for legitimate price action/candle confirmation.

Likewise, a gap through a level should not automatically satisfy the Daily/H4 breakout stage.

---

8. EXISTING SETUPS MUST SURVIVE THE WEEKEND

If a setup is active on Friday:

Example:

"WAITING_FOR_RETEST"

and Monday opens with a gap:

DO NOT reset it simply because a new trading week has started.

Preserve:

- state
- direction
- Daily level
- H4 level
- setup metadata
- timestamps
- confirmation information
- any other necessary state

The start of a new week must NOT mean:

"reset all setups"

unless an actual technical invalidation occurs.

---

9. WEEKEND GAP TELEGRAM WARNING

When a significant weekend gap occurs, send a Telegram warning.

Example:

"⚠️ GBPUSD — WEEKEND GAP DETECTED"

"Gap: 28 pips"

"Friday close: ..."

"Monday open: ..."

"The gap does NOT count as a breakout."

If an existing setup is active:

"Existing setup remains active and will continue to be monitored."

Then provide inline Telegram buttons:

"[ YES — CONTINUE ] [ NO — END SETUP ]"

---

10. WEEKEND GAP YES/NO OVERRIDE

The buttons apply to the specific affected setup only.

YES — CONTINUE

If the user presses YES:

- preserve the setup
- continue monitoring it
- mark that this setup has accepted/ignored the weekend-gap risk
- do NOT treat the gap itself as a breakout
- continue applying all normal technical rules
- future hard technical invalidations still apply

This does NOT mean:

"ignore all future invalidation rules"

It means:

"continue this particular setup despite the weekend gap."

Store something equivalent to:

"gap_override = True"

Use the project's existing state model where appropriate rather than blindly adding duplicate fields.

NO — END SETUP

If the user presses NO:

- terminate the setup
- transition to WATCHING
- clear only fields that should be cleared by a genuine user-ended setup
- persist the decision
- send confirmation

Example:

"🛑 GBPUSD — SETUP ENDED"

"You chose not to continue after the weekend gap."

---

11. WICK VS BODY BREAKOUT — CRITICAL

Audit the existing breakout/rejection logic carefully.

A wick crossing a level is NOT automatically a breakout.

Bullish sweep/rejection

Something equivalent to:

"candle.low < level AND candle.close > level"

should represent a sweep/rejection where appropriate.

Bullish body breakout

Something equivalent to:

"candle.close > level"

combined with the correct candle/open/previous-close context should be used for a legitimate body breakout.

Bearish sweep/rejection

Something equivalent to:

"candle.high > level AND candle.close < level"

should represent a sweep/rejection.

Bearish body breakout

Something equivalent to:

"candle.close < level"

with the correct candle/open/previous-close context.

The exact implementation must be adapted to the existing strategy.

The key rule:

A huge wick through a level followed by a close back on the original side is a rejection/sweep, NOT a breakout.

Do not allow one detector to call it a breakout while another calls it a rejection.

Audit duplicate/conflicting detection logic.

Add clear logging such as:

"SWEEP_REJECTION"

or

"BODY_BREAKOUT"

so the behavior is easy to diagnose.

---

12. ECONOMIC NEWS FILTER — DO NOT GUESS DATES

The current bot must NOT rely on manually guessed/hard-coded dates for:

- FOMC
- NFP
- CPI
- other major economic events

Do not assume:

"NFP = first Friday"

or manually enter dates such as:

"FOMC September 15–16"

unless those dates/times have been obtained from an authoritative source.

The bot should retrieve scheduled events from authoritative online sources.

Prefer:

1. Official central-bank calendars
2. Official government economic-statistics calendars
3. Reputable structured economic-calendar API/feed if official machine-readable data is unavailable

Do not scrape random websites if a reliable structured source exists.

---

13. ECONOMIC CALENDAR SERVICE

Create or adapt an economic calendar service with functionality equivalent to:

- "refresh_calendar()"
- "is_news_blackout(now_utc, pair)"
- "get_upcoming_events()"
- "get_last_update_time()"

Use the project's existing architecture if there is already a suitable service.

Do not create duplicate calendar systems.

Each event should retain:

- event name
- currency
- impact
- event time UTC
- source
- source URL
- retrieved timestamp UTC

Normalize all timestamps to UTC internally.

---

14. IMPORTANT EVENTS

At minimum support:

- FOMC
- U.S. Employment Situation / NFP
- U.S. CPI
- other high-impact events that materially affect the currencies being scanned

Do not blindly classify every event as high impact.

The calendar data should determine impact where possible.

---

15. FOMC

Use the official Federal Reserve schedule/calendar as the authoritative source for FOMC dates.

Do not assume the entire two-day meeting is the exact news release window.

Where the authoritative source provides exact statement/press-conference timing, use that.

If exact release time cannot be reliably obtained:

- do not invent a time
- document the limitation
- use the safest verified interpretation available

---

16. NFP

Use the official U.S. Bureau of Labor Statistics Employment Situation release schedule.

Do not simply calculate:

"first Friday of the month"

because the official schedule is authoritative.

Use the verified release date/time.

---

17. CPI

Use the official U.S. Bureau of Labor Statistics CPI release schedule.

Do not manually guess CPI dates.

Use verified release date/time.

---

18. ECONOMIC CALENDAR CACHE

Do not make the scanner dependent on a successful network request every single scan cycle.

Use a local cache, for example:

"data/economic_events.json"

The architecture should be:

"online source → validate/parse → UTC normalize → local cache → scanner"

Refresh the calendar periodically, not every candle scan.

If the online source temporarily fails:

- retain the last valid calendar
- do NOT overwrite valid cached data with an empty result
- log the failure
- mark the calendar as stale
- retry later

For a risk-sensitive system, if there is no valid calendar at all, fail safely rather than pretending there are no news events.

Prefer:

"do not detect NEW setups while calendar status is unknown"

rather than silently allowing new setups through an unverified news filter.

Existing setups should not automatically be reset merely because the calendar service is temporarily unavailable.

---

19. NEWS BLACKOUT WINDOW

Make the blackout configurable.

For example:

"NEWS_FILTER_ENABLED = True"

"NEWS_BLACKOUT_BEFORE_MINUTES = 1440"

"NEWS_BLACKOUT_AFTER_MINUTES = 60"

Do not hard-code these values inside strategy logic.

The filter should apply to the relevant currency.

For example:

- USD event affects EURUSD
- USD event affects GBPUSD
- USD event affects USDJPY
- USD event affects USDCHF
- USD event affects USDCAD
- USD event affects AUDUSD
- USD event affects NZDUSD

Do not block every pair for every event.

Use currency relevance.

---

20. NEWS FILTER BEHAVIOR

The economic news filter should primarily prevent new setup detection during the blackout.

It should NOT automatically destroy an existing setup.

If an existing setup enters a news blackout:

- preserve it
- warn the user
- optionally request a YES/NO decision

---

21. NEWS TELEGRAM WARNING

If an active setup is approaching or enters a high-impact news blackout:

send:

"📰 GBPUSD — HIGH-IMPACT NEWS WARNING"

Include:

- event
- currency
- event time
- time until event / time since event where useful
- current setup state

Example:

"Event: FOMC"

"Currency: USD"

"Time: 18:00 UTC"

"Current state: WAITING_FOR_RETEST"

Then:

"Continue monitoring this setup despite the news risk?"

Buttons:

"[ YES — CONTINUE ] [ NO — END SETUP ]"

---

22. NEWS YES/NO OVERRIDE

The override is PER SETUP.

YES

Set something equivalent to:

"news_override = True"

and continue monitoring this particular setup.

The global news filter must remain enabled for NEW setups.

So:

- this existing setup can continue
- unrelated new setups remain blocked by the news filter

Hard technical invalidation rules still apply.

NO

End the setup.

Transition to:

"WATCHING"

Persist the result.

Send:

"🛑 GBPUSD — SETUP ENDED"

"You chose not to continue during the news-risk period."

---

23. HARD FAILURES MUST NOT HAVE YES/NO BUTTONS

This distinction is mandatory.

If the setup genuinely violates a hard technical strategy rule:

DO NOT ask:

"Continue?"

Do not provide an override button.

Automatically invalidate it and explain exactly why.

Examples:

"❌ SETUP INVALIDATED"

"Reason: H4 counter-close through broken level."

or:

"❌ SETUP INVALIDATED"

"Reason: Price moved 127 pips away from the H4 level, exceeding MAX_DISTANCE_PIPS=100."

The user should only be allowed to override warnings/risk conditions, not actual technical invalidations.

---

24. AGING WARNING

Aging is also a warning, not a failure.

Optionally provide:

"[ YES — CONTINUE ] [ NO — END SETUP ]"

If YES:

- continue monitoring

If NO:

- user ends the setup

If the user does nothing:

- do not automatically invalidate purely because of age

Use a configurable timeout for the button if appropriate.

---

25. TELEGRAM AUTHORIZATION

Interactive buttons must only work for the authorized Telegram user/chat.

Validate:

- Telegram user ID
- chat ID where appropriate
- setup still exists
- setup still has the expected state
- callback is still relevant
- callback is not stale

Do NOT allow someone else to press the button and control a setup.

---

26. STALE BUTTON PROTECTION

A Telegram button may remain visible after the setup has changed.

Therefore when a callback is received:

verify:

1. Pair
2. Setup ID or unique setup identifier
3. Expected state
4. Warning type
5. Current setup status
6. Authorized user

If the setup has already been reset or completed:

do not resurrect it.

Reply with something like:

"This setup is no longer active; the action cannot be applied."

---

27. TELEGRAM CALLBACK AUDIT TRAIL

Record decisions.

For each interactive warning record:

- pair
- setup identifier
- warning type
- state when warning was issued
- warning timestamp
- Telegram message ID
- user ID
- decision
- decision timestamp
- resulting state
- override applied

Use the existing persistence architecture if suitable.

Do not create unnecessary duplicate databases/packages.

---

28. BUTTON TIMEOUT

Make the timeout configurable:

"TELEGRAM_OVERRIDE_TIMEOUT_MINUTES"

If the user does not respond within the configured period:

Use a safe behavior.

Prefer:

"NO / END SETUP"

for risk warnings if the project architecture requires a definite decision.

However, ensure this does not violate the requirement that time alone must never technically invalidate a setup.

The timeout is a response-to-warning policy, not a market-based technical invalidation.

Document the behavior clearly.

---

29. TELEGRAM LIFECYCLE NOTIFICATIONS

The bot must stop silently losing setups.

Telegram should communicate the lifecycle.

Use these categories:

🟡 NEW SETUP

"🟡 GBPUSD — NEW SETUP DETECTED"

Include only useful initial information.

🟢 DAILY STAGE PASSED

"🟢 GBPUSD — DAILY STAGE PASSED"

🟢 H4 STAGE PASSED

"🟢 GBPUSD — H4 STAGE PASSED"

⏳ WAITING FOR RETEST

"⏳ GBPUSD — WAITING FOR RETEST"

🟢 RETEST CONFIRMED

"🟢 GBPUSD — RETEST CONFIRMED"

⏳ WAITING FOR CONTINUATION

"⏳ GBPUSD — WAITING FOR CONTINUATION"

🟢 CONTINUATION CONFIRMED

"🟢 GBPUSD — CONTINUATION CONFIRMED"

🚨 FINAL SETUP

When the setup is completely confirmed:

send the full available trade/setup details.

Include only values actually calculated by the existing strategy.

Potential fields:

- Pair
- Direction
- Entry
- Stop Loss
- Take Profit
- Daily level
- H4 level
- Retest information
- Confirmation information
- Risk/reward
- Relevant candle information
- Timestamp

Do not invent values.

---

30. EVERY FAILURE/RESET MUST BE EXPLAINED

No setup should disappear silently.

Every transition to WATCHING caused by:

- counter-close
- excessive distance
- invalidation
- user ending setup
- any other genuine reset

must produce a Telegram message explaining why.

Example:

"❌ GBPUSD — SETUP INVALIDATED"

"Previous state: WAITING_FOR_RETEST"

"Reason: H4 candle closed back below the broken resistance."

If there are multiple possible reset paths, make the reason explicit.

---

31. CENTRALIZE RESET/INVALIDATION

If practical within the existing architecture, create/use a central function equivalent to:

"reset_setup(pair, reason, details)"

It should:

1. Capture previous state
2. Capture relevant setup details
3. Determine reason
4. Persist the reset
5. Send Telegram notification
6. Log the event

Avoid scattered silent state resets.

If the existing architecture has a better equivalent, use it instead.

The important requirement is centralized traceability.

---

32. STATE PERSISTENCE

The state machine is the source of truth.

Telegram is only the notification/control interface.

Persist state before attempting to send Telegram where appropriate.

If Telegram fails:

- do not lose the state transition
- retain the event
- allow retry/recovery

After Render restart/redeploy:

active setups should be restored correctly.

Do not reconstruct active setups as WATCHING simply because the process restarted.

---

33. SETUP IDENTITY

If the existing state model does not already have a unique setup identifier, consider adding one.

For example:

"setup_id"

This helps distinguish:

- old Telegram messages
- new setups on the same pair
- stale callback buttons
- historical events

Do not add unnecessary complexity if an existing unique identity mechanism already exists.

---

34. NO DUPLICATE TELEGRAM SPAM

The scanner may run repeatedly.

Do NOT send:

"WAITING FOR RETEST"

every scan cycle.

Send lifecycle notifications only when:

- a state transition occurs
- a meaningful warning/event occurs
- a user decision occurs
- a setup is invalidated
- a final setup is confirmed

Avoid duplicate messages for the same event.

---

35. STRATEGY STATE TRANSITION SAFETY

Audit all transitions.

Every transition should have a clearly defined trigger.

Example:

"WATCHING → DAILY_REJECTION"

only when the Daily rejection condition is genuinely satisfied.

"DAILY_REJECTION → H4_WAITING"

only when the appropriate next-stage condition is satisfied.

etc.

Do not allow:

- random resets
- state skipping
- duplicate transitions
- stale candle processing
- repeated processing of the same candle

unless intentionally supported.

---

36. DATA QUALITY / CANDLE HANDLING

Audit candle handling around:

- market close
- weekend
- Monday open
- missing candles
- duplicate candles
- broker/server timezone
- UTC conversion

Do not mistake:

- missing data
- delayed data
- duplicate candles
- weekend gaps

for strategy failures.

If a candle is incomplete, do not use it as a completed confirmation candle.

---

37. CONFIGURATION

Put configurable values in the project's existing configuration system.

Examples:

"NEWS_FILTER_ENABLED"

"NEWS_BLACKOUT_BEFORE_MINUTES"

"NEWS_BLACKOUT_AFTER_MINUTES"

"GAP_THRESHOLD_PIPS"

"MAX_DISTANCE_PIPS"

"TELEGRAM_OVERRIDE_TIMEOUT_MINUTES"

"AGING_WARNING_H4_BARS"

Do not scatter magic numbers throughout the code.

Preserve the project's existing configuration style.

---

38. NO UNNECESSARY DEPENDENCIES

Before adding a package:

inspect "requirements.txt" and existing imports.

If the required functionality already exists through an installed dependency, use it.

Do not introduce a new dependency simply because it is convenient.

If an external economic calendar requires a package/API:

explain why it is necessary and keep the dependency minimal.

---

39. LOGGING

Add useful structured logs around:

- state transitions
- reset/invalidation
- weekend gaps
- news blackout
- calendar refresh
- calendar failures
- Telegram warnings
- Telegram button decisions
- stale callbacks
- final setup confirmation

Example:

"GBPUSD | WAITING_FOR_RETEST -> WATCHING | reason=counter_close | timeframe=H4"

Example:

"GBPUSD | WEEKEND_GAP | gap_pips=28 | setup_preserved=true"

Example:

"GBPUSD | NEWS_OVERRIDE | event=FOMC | decision=YES"

Logs should make future debugging possible without reproducing the entire issue manually.

---

40. TESTS

Do not finish after modifying the code.

Add/update tests for all important behavior.

At minimum test:

Weekend

1. No gap → normal behavior
2. Small gap → no warning if below threshold
3. Large gap → warning
4. Large gap does NOT reset active setup
5. Large gap does NOT count as breakout
6. YES button preserves setup
7. NO button ends setup
8. stale YES/NO callback cannot resurrect setup

Breakout/rejection

9. Wick through level + close back = rejection/sweep
10. Valid body close through level = breakout
11. Gap through level alone = not breakout

Time

12. Setup older than 48h remains active if technically valid
13. Aging warning can occur
14. Age alone cannot invalidate

Price invalidation

15. Counter-close invalidates
16. Excessive distance invalidates
17. Hard technical failure has no override button

News

18. News blackout blocks new setup detection
19. Existing setup is preserved during news
20. News warning appears
21. YES sets per-setup override
22. NO ends setup
23. Global news filter still blocks unrelated new setups after YES
24. Calendar failure does not erase valid cached events
25. Empty failed calendar response does not overwrite valid cache

Persistence

26. Active setup survives restart
27. State fields are restored
28. Reset reason is persisted
29. Telegram failure does not destroy state transition

Telegram

30. State-transition messages are sent once
31. Duplicate scanner cycles do not spam Telegram
32. Unauthorized users cannot control setup
33. Stale callbacks are rejected
34. Final setup sends complete details

Use deterministic test data.

Do not rely on live Forex prices or live news APIs for unit tests.

---

41. DO NOT BREAK EXISTING FUNCTIONALITY

Before modifying code:

understand the existing strategy.

Preserve:

- pair list
- Daily/H4 workflow
- existing level detection
- existing entry/SL/TP calculations
- existing Telegram configuration
- existing Render deployment behavior
- existing data provider
- existing state format where possible

Only change behavior where required by this specification.

---

42. IMPORTANT: DO NOT MAKE UP MARKET DATA

Do not hard-code:

- current prices
- economic event dates
- event times
- Forex levels
- fake test values in production code

Test values are acceptable inside tests when clearly marked.

---

43. IMPLEMENTATION APPROACH

Before changing code:

Step 1

Inspect all relevant files.

Step 2

Map the current architecture.

Step 3

Trace all reset/state-transition paths.

Step 4

Identify the exact cause of the observed GBPUSD/NZDCAD reset behavior.

Step 5

Implement the corrected state/invalidation logic.

Step 6

Implement weekend-gap handling.

Step 7

Audit wick/body breakout detection.

Step 8

Implement/repair authoritative economic calendar retrieval and caching.

Step 9

Implement news blackout behavior.

Step 10

Implement Telegram lifecycle notifications.

Step 11

Implement interactive YES/NO overrides.

Step 12

Implement callback authorization/stale callback protection.

Step 13

Add tests.

Step 14

Run tests/lint/type checks available in the project.

Step 15

Review the final diff for unintended changes.

---

44. IMPORTANT DESIGN PRINCIPLE

The hierarchy should effectively be:

HARD TECHNICAL INVALIDATION

Automatically reset.

Examples:

- counter-close
- excessive price distance
- genuine strategy failure

NO override button.

WARNING / RISK EVENT

Ask the user.

Examples:

- weekend gap
- high-impact news
- aging setup

YES/NO may be provided.

USER YES

Continue this specific setup.

USER NO

End this specific setup.

TIME

Never independently invalidates a technically valid setup.

---

45. FINAL REPORT

After implementing everything, provide a concise report containing:

A. Root cause found

Explain exactly why the previous setup reset occurred.

Include:

- file
- function
- condition
- state transition

B. Changes made

List each modified file and what changed.

C. Strategy behavior

Explain:

- weekend gap
- wick vs body
- news
- price invalidation
- aging

D. Telegram behavior

Explain:

- lifecycle notifications
- warning buttons
- YES behavior
- NO behavior
- hard-failure behavior
- callback security

E. Economic calendar

Explain:

- authoritative sources used
- refresh frequency
- cache behavior
- failure behavior
- UTC handling

F. Tests

Report:

- tests added
- tests passed
- tests failed
- any limitations

G. Remaining risks

Clearly identify anything that could not be verified because of:

- external API limitations
- broker data limitations
- unavailable exact event times
- deployment/environment differences

Do not claim something works unless you actually verified it.

---

46. FINAL QUALITY REQUIREMENT

Do not give me pseudocode or a conceptual answer.

Actually inspect the existing project and implement the changes in the existing codebase.

Do not rewrite unrelated files.

Do not silently remove existing strategy behavior.

Do not invent missing information.

Do not use time alone as a hard invalidation.

Do not let a weekend gap automatically kill a setup.

Do not let a wick count as a body breakout.

Do not guess economic-event dates.

Do not silently reset setups.

Do not provide override buttons for genuine technical failures.

Every setup must have a traceable outcome:

"progressed → waiting → user ended → technically invalidated → or fully confirmed"

and Telegram should make that lifecycle visible.