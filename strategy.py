"""Sweep, breakout, retest, continuation, and the per-pair state machine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pandas as pd

import config
from economic_calendar import EconomicCalendarService
from level_detector import KeyLevel, detect_levels, recent_levels
from market_events import detect_weekend_gap, gap_candle_times
from state_manager import StateManager

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    pair: str
    timeframe: str
    direction: str
    key_level_type: str
    key_level_price: float
    rejection_status: str
    breakout_status: str
    candle_close_status: str
    level_flip: str
    retest_status: str
    daily_confirmation: str
    h4_confirmation: str
    final_signal_status: str
    state: str
    alert: bool = False


class StrategyEngine:
    def __init__(
        self,
        states: StateManager,
        calendar: EconomicCalendarService | None = None,
    ) -> None:
        self.states = states
        self.calendar = calendar

    def evaluate(self, pair: str, daily: pd.DataFrame, h4: pd.DataFrame) -> ScanResult | None:
        if daily.empty or h4.empty:
            return None
        eps = config.price_epsilon(pair)
        daily_levels = detect_levels(daily)
        h4_levels = detect_levels(h4)
        state = self.states.get(pair)
        self._record_weekend_gap(pair, state, daily, h4)
        daily_gap_times = gap_candle_times(daily, pair, "D1")
        h4_gap_times = gap_candle_times(h4, pair, "H4")

        daily_bar = _iso(daily.iloc[-1]["datetime"])
        h4_bar = _iso(h4.iloc[-1]["datetime"])
        if state.get("last_daily_bar") == daily_bar and state.get("last_h4_bar") == h4_bar:
            return None

        self.states.update(pair, last_daily_bar=daily_bar, last_h4_bar=h4_bar)

        if self._invalidate_dead_setup(
            pair,
            state,
            daily,
            h4,
            eps,
            daily_gap_times,
            h4_gap_times,
        ):
            return None
        state = self.states.get(pair)

        self._record_aging_warning(pair, state, h4)
        self._record_news_warning(pair, state)

        if state.get("state") == "WATCHING" and self.calendar and self.calendar.blocks_new_setup(pair):
            log.warning("%s | NEWS_BLACKOUT | new setup detection blocked", pair)
            return None

        daily_event = _daily_setup(daily, daily_levels, eps, excluded_times=daily_gap_times)
        if daily_event and _opposes(state.get("direction"), daily_event["direction"]):
            log.info("%s daily direction flipped; resetting", pair)
            state = self.states.reset(
                pair,
                reason="opposing_daily_setup",
                details={"incoming_direction": daily_event["direction"]},
                last_alert_key=state.get("last_alert_key"),
                last_alert_at=state.get("last_alert_at"),
            )

        name = state.get("state") or "WATCHING"
        if name == "ALERT_SENT":
            if daily_event and daily_event["bar_time"] != state.get("daily_bar_time"):
                state = self.states.reset(
                    pair,
                    reason="new_daily_setup_after_alert",
                    details={"new_daily_bar": daily_event["bar_time"]},
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                name = "WATCHING"
            else:
                return None

        if name == "WATCHING":
            if not daily_event:
                return None
            state = self._enter_daily(pair, daily_event)
            name = state["state"]

        if name in {"DAILY_REJECTION_DETECTED", "DAILY_BREAKOUT_CONFIRMED"}:
            state = self.states.update(pair, state="H4_WAITING")
            name = "H4_WAITING"

        if name == "H4_WAITING":
            h4_event = self._h4_breakout(
                h4,
                h4_levels,
                state["direction"],
                eps,
                not_before=state.get("daily_bar_time"),
                excluded_times=h4_gap_times,
            )
            if not h4_event:
                return None
            state = self.states.update(
                pair,
                state="H4_BREAKOUT_CONFIRMED",
                h4_level_type=h4_event["level"].kind,
                h4_level_price=h4_event["level"].price,
                h4_breakout_bar_time=h4_event["bar_time"],
                h4_bars_since_breakout=0,
                h4_confirmed=True,
                breakout_status="CONFIRMED",
                candle_close_status=h4_event["close_status"],
                level_flip=h4_event["level_flip"],
            )
            name = "WAITING_FOR_RETEST"
            self.states.update(pair, state="WAITING_FOR_RETEST")

        if name in {"H4_BREAKOUT_CONFIRMED", "WAITING_FOR_RETEST"}:
            return self._handle_retest(pair, h4, eps)

        if name == "RETEST_CONFIRMED":
            return self._handle_continuation(pair, h4, eps)

        if name == "CONTINUATION_CONFIRMED":
            return self._emit_alert(pair)

        return None

    def _record_weekend_gap(
        self,
        pair: str,
        state: dict[str, Any],
        daily: pd.DataFrame,
        h4: pd.DataFrame,
    ) -> None:
        gap = detect_weekend_gap(daily, pair, "D1") or detect_weekend_gap(h4, pair, "H4")
        if gap is None or state.get("weekend_gap_candle_time") == gap.candle_time:
            return
        self.states.update(
            pair,
            weekend_gap_candle_time=gap.candle_time,
            weekend_gap_pips=round(gap.gap_pips, 2),
        )
        self.states.record_event(
            {
                "type": "weekend_gap",
                "pair": pair,
                "timeframe": gap.timeframe,
                "candle_time": gap.candle_time,
                "gap_pips": round(gap.gap_pips, 2),
                "friday_close": gap.friday_close,
                "monday_open": gap.monday_open,
                "active_setup": state.get("state") != "WATCHING",
                "setup_id": state.get("setup_id"),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        log.warning(
            "%s | WEEKEND_GAP | gap_pips=%.2f | setup_preserved=%s",
            pair,
            gap.gap_pips,
            state.get("state") != "WATCHING",
        )

    def _record_aging_warning(
        self,
        pair: str,
        state: dict[str, Any],
        h4: pd.DataFrame,
    ) -> None:
        breakout_time = state.get("h4_breakout_bar_time")
        if not breakout_time or state.get("state") not in {
            "H4_BREAKOUT_CONFIRMED",
            "WAITING_FOR_RETEST",
            "RETEST_CONFIRMED",
        }:
            return
        bars = len(_bars_after(h4, breakout_time))
        if bars < config.AGING_WARNING_H4_BARS or state.get("last_aging_warning_bars", 0) >= bars:
            return
        self.states.update(pair, last_aging_warning_bars=bars)
        self.states.record_event(
            {
                "type": "aging_warning",
                "pair": pair,
                "setup_id": state.get("setup_id"),
                "bars": bars,
                "state": state.get("state"),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _record_news_warning(self, pair: str, state: dict[str, Any]) -> None:
        if not self.calendar or state.get("state") == "WATCHING" or state.get("news_override"):
            return
        events = self.calendar.get_blackout_events(datetime.now(timezone.utc), pair)
        if not events:
            return
        event = events[0]
        key = "|".join(
            [
                str(event.get("event_name")),
                str(event.get("event_date_utc")),
                str(event.get("event_time_utc")),
            ]
        )
        if state.get("last_news_warning_key") == key:
            return
        self.states.update(pair, last_news_warning_key=key)
        self.states.record_event(
            {
                "type": "news_warning",
                "pair": pair,
                "setup_id": state.get("setup_id"),
                "state": state.get("state"),
                "event_name": event.get("event_name"),
                "currency": event.get("currency"),
                "event_time_utc": event.get("event_time_utc"),
                "event_date_utc": event.get("event_date_utc"),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _enter_daily(self, pair: str, event: dict[str, Any]) -> dict[str, Any]:
        next_state = (
            "DAILY_REJECTION_DETECTED"
            if event["kind"] == "REJECTION"
            else "DAILY_BREAKOUT_CONFIRMED"
        )
        return self.states.update(
            pair,
            state=next_state,
            direction=event["direction"],
            daily_setup=event["kind"],
            daily_level_type=event["level"].kind,
            daily_level_price=event["level"].price,
            daily_bar_time=event["bar_time"],
            setup_id=uuid4().hex,
            gap_override=False,
            news_override=False,
            last_reset_reason=None,
            last_reset_details=None,
            daily_confirmed=True,
            rejection_status="CONFIRMED" if event["kind"] == "REJECTION" else "NONE",
            breakout_status="CONFIRMED" if event["kind"] == "BREAKOUT" else "NONE",
            candle_close_status=event["close_status"],
        )

    def _invalidate_dead_setup(
        self,
        pair: str,
        state: dict[str, Any],
        daily: pd.DataFrame,
        h4: pd.DataFrame,
        eps: float,
        daily_gap_times: set[str] | None = None,
        h4_gap_times: set[str] | None = None,
    ) -> bool:
        direction = state.get("direction")
        if not direction:
            return False

        daily_level = state.get("daily_level_price")
        daily_is_gap = _iso(daily.iloc[-1]["datetime"]) in (daily_gap_times or set())
        if daily_level is not None and not daily_is_gap:
            daily_close = float(daily.iloc[-1]["close"])
            if direction == "BULLISH" and daily_close < float(daily_level) - eps:
                log.warning("%s invalidated: daily close %s fell below %s on bullish setup", pair, daily_close, daily_level)
                self.states.reset(
                    pair,
                    reason="daily_counter_close",
                    details={"direction": direction, "level": float(daily_level), "close": daily_close},
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True
            if direction == "BEARISH" and daily_close > float(daily_level) + eps:
                log.warning("%s invalidated: daily close %s rose above %s on bearish setup", pair, daily_close, daily_level)
                self.states.reset(
                    pair,
                    reason="daily_counter_close",
                    details={"direction": direction, "level": float(daily_level), "close": daily_close},
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True

        h4_level = state.get("h4_level_price")
        h4_is_gap = _iso(h4.iloc[-1]["datetime"]) in (h4_gap_times or set())
        if h4_level is not None and not h4_is_gap:
            h4_close = float(h4.iloc[-1]["close"])
            if direction == "BULLISH" and h4_close < float(h4_level) - eps:
                log.warning("%s invalidated: H4 close %s broke below %s on bullish setup", pair, h4_close, h4_level)
                self.states.reset(
                    pair,
                    reason="h4_counter_close",
                    details={"direction": direction, "level": float(h4_level), "close": h4_close},
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True
            if direction == "BEARISH" and h4_close > float(h4_level) + eps:
                log.warning("%s invalidated: H4 close %s broke above %s on bearish setup", pair, h4_close, h4_level)
                self.states.reset(
                    pair,
                    reason="h4_counter_close",
                    details={"direction": direction, "level": float(h4_level), "close": h4_close},
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True

        active_state = state.get("state")
        if h4_level is not None and not h4_is_gap and active_state in {"H4_BREAKOUT_CONFIRMED", "WAITING_FOR_RETEST", "RETEST_CONFIRMED", "CONTINUATION_CONFIRMED"}:
            current_price = float(h4.iloc[-1]["close"])
            distance = abs(current_price - float(h4_level))
            pips = distance * 10000.0 if not pair.endswith("JPY") else distance * 100.0
            if pips > config.MAX_DISTANCE_PIPS_FOR_INVALIDATION:
                log.warning(
                    "%s invalidated: price moved %.0f pips from breakout level %.5f; setup no longer likely to retest",
                    pair,
                    pips,
                    float(h4_level),
                )
                self.states.reset(
                    pair,
                    reason="price_distance",
                    details={
                        "distance_pips": round(pips, 2),
                        "max_distance_pips": config.MAX_DISTANCE_PIPS_FOR_INVALIDATION,
                        "level": float(h4_level),
                    },
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True

        breakout_time = state.get("h4_breakout_bar_time")
        if breakout_time and active_state in {"H4_WAITING", "H4_BREAKOUT_CONFIRMED", "WAITING_FOR_RETEST", "RETEST_CONFIRMED", "CONTINUATION_CONFIRMED"}:
            bars_after = _bars_after(h4, breakout_time)
            if len(bars_after) >= config.AGING_WARNING_H4_BARS:
                log.info("%s setup still active after %s H4 bars; waiting for retest without killing", pair, len(bars_after))

        return False

    def _handle_retest(self, pair: str, h4: pd.DataFrame, eps: float) -> ScanResult | None:
        state = self.states.get(pair)
        if state.get("retest_allowed") is False:
            self.states.reset(
                pair,
                reason="user_disabled_retest",
                details={"pair": pair, "setup_id": state.get("setup_id")},
                last_alert_key=state.get("last_alert_key"),
                last_alert_at=state.get("last_alert_at"),
            )
            return None
        if state.get("retest_allowed") is None:
            if config.RULE_PROMPT_ENABLED:
                self._request_rule_decision(pair, state, "retest")
                if state.get("setup_id"):
                    return None
            self.states.update(pair, retest_allowed=True)

        level_price = float(state["h4_level_price"])
        breakout_time = state.get("h4_breakout_bar_time")
        bars_after = _bars_after(h4, breakout_time)
        self.states.update(pair, h4_bars_since_breakout=len(bars_after))
        for _, candle in bars_after.iterrows():
            if _iso(candle["datetime"]) == breakout_time:
                continue
            if _is_retest(candle, state["direction"], level_price, eps):
                self.states.update(
                    pair,
                    state="RETEST_CONFIRMED",
                    retest_status="CONFIRMED",
                    retest_bar_time=_iso(candle["datetime"]),
                    candle_close_status=_close_status(candle, state["direction"], level_price),
                )
                return None

        second_chance = self._second_chance_ema_entry(pair, h4, state, level_price, eps)
        if second_chance is not None:
            return second_chance
        return None

    def _request_rule_decision(self, pair: str, state: dict[str, Any], rule_name: str) -> None:
        if state.get("state") == "WATCHING":
            return
        setup_id = state.get("setup_id")
        if not setup_id:
            return
        if state.get("warning_type") == rule_name and not state.get("warning_acknowledged"):
            return
        self.states.update(pair, warning_type=rule_name, warning_acknowledged=False)
        self.states.record_event(
            {
                "type": "rule_decision",
                "pair": pair,
                "setup_id": setup_id,
                "warning_type": rule_name,
                "state": state.get("state"),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _second_chance_ema_entry(
        self,
        pair: str,
        h4: pd.DataFrame,
        state: dict[str, Any],
        level_price: float,
        eps: float,
    ) -> ScanResult | None:
        direction = state.get("direction")
        if direction not in {"BULLISH", "BEARISH"}:
            return None
        if state.get("second_chance_allowed") is False:
            return None
        if state.get("second_chance_allowed") is None:
            if config.RULE_PROMPT_ENABLED:
                self._request_rule_decision(pair, state, "second_chance")
                if state.get("setup_id"):
                    return None
            self.states.update(pair, second_chance_allowed=True)

        breakout_time = state.get("h4_breakout_bar_time")
        bars_after = _bars_after(h4, breakout_time)
        if len(bars_after) < config.EMA_PULLBACK_MIN_BARS or len(bars_after) > config.EMA_PULLBACK_MAX_BARS:
            return None

        if state.get("retest_bar_time") is not None:
            return None

        closes = h4["close"].astype(float)
        ema_span = min(config.EMA_PULLBACK_SPAN, max(2, len(closes)))
        ema = closes.ewm(span=ema_span, adjust=False).mean()
        signal_bar = bars_after.iloc[-1]
        signal_close = float(signal_bar["close"])
        signal_low = float(signal_bar["low"])
        signal_high = float(signal_bar["high"])
        ema_value = float(ema.iloc[-1])
        ema_tolerance = 0.0001 if not pair.endswith("JPY") else 0.01

        if direction == "BULLISH":
            within_pullback = signal_low <= ema_value + ema_tolerance and signal_close > ema_value
            still_running = signal_close > level_price + eps and signal_high > level_price + eps
            if not (within_pullback and still_running):
                return None
        else:
            within_pullback = signal_high >= ema_value - ema_tolerance and signal_close < ema_value
            still_running = signal_close < level_price - eps and signal_low < level_price - eps
            if not (within_pullback and still_running):
                return None

        alert_key = "|".join(
            [
                pair,
                str(direction),
                str(level_price),
                "SECOND_CHANCE_PULLBACK",
                str(breakout_time),
            ]
        )
        if alert_key == state.get("last_alert_key"):
            self.states.update(pair, state="ALERT_SENT")
            return None

        result = ScanResult(
            pair=pair,
            timeframe="H4",
            direction=direction,
            key_level_type=state.get("h4_level_type") or state.get("daily_level_type") or "UNKNOWN",
            key_level_price=float(level_price),
            rejection_status=state.get("rejection_status") or "NONE",
            breakout_status=state.get("breakout_status") or "NONE",
            candle_close_status=_close_status(signal_bar, direction, level_price),
            level_flip=state.get("level_flip") or "NONE",
            retest_status="SECOND_CHANCE_PULLBACK",
            daily_confirmation="CONFIRMED" if state.get("daily_confirmed") else "PENDING",
            h4_confirmation="CONFIRMED" if state.get("h4_confirmed") else "PENDING",
            final_signal_status="SECOND_CHANCE_PULLBACK",
            state="ALERT_SENT",
            alert=True,
        )
        self.states.update(
            pair,
            state="ALERT_SENT",
            last_alert_key=alert_key,
            last_alert_at=datetime.now(timezone.utc).isoformat(),
        )
        return result

    def _h4_breakout(
        self,
        frame: pd.DataFrame,
        levels: list[KeyLevel],
        direction: str,
        eps: float,
        not_before: str | None = None,
        excluded_times: set[str] | None = None,
    ) -> dict[str, Any] | None:
        a_levels = recent_levels(levels, "A_SHAPE", "RESISTANCE")
        v_levels = recent_levels(levels, "V_SHAPE", "SUPPORT")
        start = max(1, len(frame) - config.H4_SETUP_LOOKBACK_BARS)
        for i in range(len(frame) - 1, start - 1, -1):
            candle = frame.iloc[i]
            prev = frame.iloc[i - 1]
            if _iso(candle["datetime"]) in (excluded_times or set()):
                log.info("%s | WEEKEND_GAP | candle_excluded_from_h4_breakout", _iso(candle["datetime"]))
                continue
            if not_before and pd.Timestamp(candle["datetime"]) < pd.Timestamp(not_before):
                continue
            if direction == "BULLISH":
                candidates = list(reversed(a_levels))
                recent_high = float(frame["high"].iloc[max(0, i - 30) : i].max())
                candidates.append(
                    KeyLevel("A_SHAPE", "RESISTANCE", recent_high, recent_high, recent_high, i - 1, _iso(prev["datetime"]))
                )
                for level in candidates:
                    if level.index >= i:
                        continue
                    if _bullish_breakout(candle, prev, level.price, eps):
                        window = frame.iloc[max(0, i - 9) : i + 1].copy()
                        if not self._volume_gate_passes(window, direction):
                            continue
                        log.info("BODY_BREAKOUT | direction=BULLISH | level=%.8f | candle=%s", level.price, _iso(candle["datetime"]))
                        return {
                            "level": level,
                            "bar_time": _iso(candle["datetime"]),
                            "close_status": _close_status(candle, "BULLISH", level.price),
                            "level_flip": "RBS",
                        }
            else:
                candidates = list(reversed(v_levels))
                recent_low = float(frame["low"].iloc[max(0, i - 30) : i].min())
                candidates.append(
                    KeyLevel("V_SHAPE", "SUPPORT", recent_low, recent_low, recent_low, i - 1, _iso(prev["datetime"]))
                )
                for level in candidates:
                    if level.index >= i:
                        continue
                    if _bearish_breakout(candle, prev, level.price, eps):
                        window = frame.iloc[max(0, i - 9) : i + 1].copy()
                        if not self._volume_gate_passes(window, direction):
                            continue
                        log.info("BODY_BREAKOUT | direction=BEARISH | level=%.8f | candle=%s", level.price, _iso(candle["datetime"]))
                        return {
                            "level": level,
                            "bar_time": _iso(candle["datetime"]),
                            "close_status": _close_status(candle, "BEARISH", level.price),
                            "level_flip": "SBR",
                        }
        return None

    def _volume_gate_passes(self, frame: pd.DataFrame, direction: str | None = None) -> bool:
        if not config.VOLUME_FILTER_ENABLED or frame.empty:
            return True
        if "volume" not in frame.columns:
            return True
        values = pd.to_numeric(frame["volume"], errors="coerce").dropna()
        if values.empty:
            return True
        recent = values.tail(min(10, len(values)))
        average = float(recent.mean())
        current = float(values.iloc[-1])
        if average <= 0:
            return True
        result = current >= average * config.VOLUME_FILTER_MIN_MULTIPLIER
        if not result:
            log.info(
                "VOLUME_GATE_BLOCKED | current=%.0f | avg=%.0f | multiplier=%.2f | direction=%s",
                current,
                average,
                config.VOLUME_FILTER_MIN_MULTIPLIER,
                direction or "UNKNOWN",
            )
        return result

    def _handle_continuation(self, pair: str, h4: pd.DataFrame, eps: float) -> ScanResult | None:
        state = self.states.get(pair)
        retest_time = state.get("retest_bar_time")
        next_bar = _next_bar(h4, retest_time)
        if next_bar is None:
            return None
        level_price = float(state["h4_level_price"])
        if _is_continuation(next_bar, state["direction"], level_price, eps):
            self.states.update(
                pair,
                state="CONTINUATION_CONFIRMED",
                continuation_status="CONFIRMED",
                candle_close_status=_close_status(next_bar, state["direction"], level_price),
            )
            return self._emit_alert(pair)

        log.info("%s continuation failed; resetting", pair)
        self.states.reset(
            pair,
            reason="continuation_failed",
            details={"direction": state.get("direction"), "level": level_price},
            last_alert_key=state.get("last_alert_key"),
            last_alert_at=state.get("last_alert_at"),
        )
        return None

    def _emit_alert(self, pair: str) -> ScanResult | None:
        state = self.states.get(pair)
        alert_key = "|".join(
            [
                pair,
                str(state.get("direction")),
                str(state.get("h4_level_price")),
                str(state.get("h4_breakout_bar_time")),
                str(state.get("retest_bar_time")),
            ]
        )
        if alert_key == state.get("last_alert_key"):
            self.states.update(pair, state="ALERT_SENT")
            return None

        result = ScanResult(
            pair=pair,
            timeframe="H4",
            direction=state["direction"],
            key_level_type=state.get("h4_level_type") or state.get("daily_level_type") or "UNKNOWN",
            key_level_price=float(state.get("h4_level_price") or state.get("daily_level_price") or 0),
            rejection_status=state.get("rejection_status") or "NONE",
            breakout_status=state.get("breakout_status") or "NONE",
            candle_close_status=state.get("candle_close_status") or "NONE",
            level_flip=state.get("level_flip") or "NONE",
            retest_status=state.get("retest_status") or "NONE",
            daily_confirmation="CONFIRMED" if state.get("daily_confirmed") else "PENDING",
            h4_confirmation="CONFIRMED" if state.get("h4_confirmed") else "PENDING",
            final_signal_status="CONTINUATION_CONFIRMED",
            state="ALERT_SENT",
            alert=True,
        )
        self.states.update(
            pair,
            state="ALERT_SENT",
            last_alert_key=alert_key,
            last_alert_at=datetime.now(timezone.utc).isoformat(),
        )
        return result


def _daily_setup(
    frame: pd.DataFrame,
    levels: list[KeyLevel],
    eps: float,
    excluded_times: set[str] | None = None,
) -> dict[str, Any] | None:
    a_levels = recent_levels(levels, "A_SHAPE", "RESISTANCE")
    v_levels = recent_levels(levels, "V_SHAPE", "SUPPORT")
    start = max(1, len(frame) - config.DAILY_SETUP_LOOKBACK_BARS)
    for i in range(len(frame) - 1, start - 1, -1):
        candle = frame.iloc[i]
        prev = frame.iloc[i - 1]
        if _iso(candle["datetime"]) in (excluded_times or set()):
            log.info("%s | WEEKEND_GAP | candle_excluded_from_daily_setup", _iso(candle["datetime"]))
            continue
        for level in reversed(v_levels):
            if level.index >= i:
                continue
            if _bullish_sweep(candle, level.price, eps):
                return _event("BULLISH", "REJECTION", level, candle)
            if _bearish_breakout(candle, prev, level.price, eps):
                return _event("BEARISH", "BREAKOUT", level, candle)
        for level in reversed(a_levels):
            if level.index >= i:
                continue
            if _bearish_sweep(candle, level.price, eps):
                return _event("BEARISH", "REJECTION", level, candle)
            if _bullish_breakout(candle, prev, level.price, eps):
                return _event("BULLISH", "BREAKOUT", level, candle)
        recent_high = float(frame["high"].iloc[max(0, i - 40) : i].max())
        recent_low = float(frame["low"].iloc[max(0, i - 40) : i].min())
        if _bullish_breakout(candle, prev, recent_high, eps):
            synthetic = KeyLevel("A_SHAPE", "RESISTANCE", recent_high, recent_high, recent_high, i - 1, _iso(prev["datetime"]))
            return _event("BULLISH", "BREAKOUT", synthetic, candle)
        if _bearish_breakout(candle, prev, recent_low, eps):
            synthetic = KeyLevel("V_SHAPE", "SUPPORT", recent_low, recent_low, recent_low, i - 1, _iso(prev["datetime"]))
            return _event("BEARISH", "BREAKOUT", synthetic, candle)
    return None




def _bullish_sweep(candle: pd.Series, level: float, eps: float) -> bool:
    return float(candle["low"]) < level - eps and float(candle["close"]) > level + eps


def _bearish_sweep(candle: pd.Series, level: float, eps: float) -> bool:
    return float(candle["high"]) > level + eps and float(candle["close"]) < level - eps


def _bullish_breakout(candle: pd.Series, prev: pd.Series, level: float, eps: float) -> bool:
    return float(candle["close"]) > level + eps and float(prev["close"]) <= level + eps


def _bearish_breakout(candle: pd.Series, prev: pd.Series, level: float, eps: float) -> bool:
    return float(candle["close"]) < level - eps and float(prev["close"]) >= level - eps


def _is_retest(candle: pd.Series, direction: str, level: float, eps: float) -> bool:
    if direction == "BULLISH":
        return float(candle["low"]) <= level + eps and float(candle["close"]) > level + eps
    return float(candle["high"]) >= level - eps and float(candle["close"]) < level - eps


def _is_continuation(candle: pd.Series, direction: str, level: float, eps: float) -> bool:
    if direction == "BULLISH":
        return float(candle["close"]) > level + eps
    return float(candle["close"]) < level - eps


def _close_status(candle: pd.Series, direction: str, level: float) -> str:
    close = float(candle["close"])
    if direction == "BULLISH":
        return "CLOSE_ABOVE_LEVEL" if close > level else "CLOSE_BELOW_LEVEL"
    return "CLOSE_BELOW_LEVEL" if close < level else "CLOSE_ABOVE_LEVEL"


def _event(direction: str, kind: str, level: KeyLevel, candle: pd.Series) -> dict[str, Any]:
    log.info(
        "%s | %s | direction=%s | level=%.8f | candle=%s",
        "SWEEP_REJECTION" if kind == "REJECTION" else "BODY_BREAKOUT",
        kind,
        direction,
        level.price,
        _iso(candle["datetime"]),
    )
    return {
        "direction": direction,
        "kind": kind,
        "level": level,
        "bar_time": _iso(candle["datetime"]),
        "close_status": _close_status(candle, direction, level.price),
    }


def _opposes(current: str | None, incoming: str) -> bool:
    return bool(current) and current != incoming


def _iso(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _bars_after(frame: pd.DataFrame, bar_time: str | None) -> pd.DataFrame:
    if not bar_time:
        return frame.iloc[0:0]
    frame = frame.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True, errors="coerce")
    stamp = pd.Timestamp(bar_time)
    if stamp.tzinfo is None:
        mask = frame["datetime"] >= pd.Timestamp(bar_time, tz="UTC")
    else:
        mask = frame["datetime"] >= stamp
    return frame.loc[mask]


def _next_bar(frame: pd.DataFrame, bar_time: str | None) -> pd.Series | None:
    if not bar_time:
        return None
    stamp = pd.Timestamp(bar_time)
    later = frame.loc[frame["datetime"] > stamp]
    if later.empty:
        return None
    return later.iloc[0]
