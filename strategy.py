"""Sweep, breakout, retest, continuation, and the per-pair state machine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

import config
from level_detector import KeyLevel, detect_levels, recent_levels
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
    def __init__(self, states: StateManager) -> None:
        self.states = states

    def evaluate(self, pair: str, daily: pd.DataFrame, h4: pd.DataFrame) -> ScanResult | None:
        if daily.empty or h4.empty:
            return None
        eps = config.price_epsilon(pair)
        daily_levels = detect_levels(daily)
        h4_levels = detect_levels(h4)
        state = self.states.get(pair)

        daily_bar = _iso(daily.iloc[-1]["datetime"])
        h4_bar = _iso(h4.iloc[-1]["datetime"])
        if state.get("last_daily_bar") == daily_bar and state.get("last_h4_bar") == h4_bar:
            return None

        self.states.update(pair, last_daily_bar=daily_bar, last_h4_bar=h4_bar)

        if self._invalidate_dead_setup(pair, state, daily, h4, eps):
            return None
        state = self.states.get(pair)

        daily_event = _daily_setup(daily, daily_levels, eps)
        if daily_event and _opposes(state.get("direction"), daily_event["direction"]):
            log.info("%s daily direction flipped; resetting", pair)
            state = self.states.reset(
                pair,
                last_alert_key=state.get("last_alert_key"),
                last_alert_at=state.get("last_alert_at"),
            )

        name = state.get("state") or "WATCHING"
        if name == "ALERT_SENT":
            if daily_event and daily_event["bar_time"] != state.get("daily_bar_time"):
                state = self.states.reset(
                    pair,
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
            h4_event = _h4_breakout(
                h4,
                h4_levels,
                state["direction"],
                eps,
                not_before=state.get("daily_bar_time"),
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
    ) -> bool:
        direction = state.get("direction")
        if not direction:
            return False

        daily_level = state.get("daily_level_price")
        if daily_level is not None:
            daily_close = float(daily.iloc[-1]["close"])
            if direction == "BULLISH" and daily_close < float(daily_level) - eps:
                log.warning("%s invalidated: daily close %s fell below %s on bullish setup", pair, daily_close, daily_level)
                self.states.reset(
                    pair,
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True
            if direction == "BEARISH" and daily_close > float(daily_level) + eps:
                log.warning("%s invalidated: daily close %s rose above %s on bearish setup", pair, daily_close, daily_level)
                self.states.reset(
                    pair,
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True

        h4_level = state.get("h4_level_price")
        if h4_level is not None:
            h4_close = float(h4.iloc[-1]["close"])
            if direction == "BULLISH" and h4_close < float(h4_level) - eps:
                log.warning("%s invalidated: H4 close %s broke below %s on bullish setup", pair, h4_close, h4_level)
                self.states.reset(
                    pair,
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True
            if direction == "BEARISH" and h4_close > float(h4_level) + eps:
                log.warning("%s invalidated: H4 close %s broke above %s on bearish setup", pair, h4_close, h4_level)
                self.states.reset(
                    pair,
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True

        active_state = state.get("state")
        if h4_level is not None and active_state in {"H4_BREAKOUT_CONFIRMED", "WAITING_FOR_RETEST", "RETEST_CONFIRMED", "CONTINUATION_CONFIRMED"}:
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
                    last_alert_key=state.get("last_alert_key"),
                    last_alert_at=state.get("last_alert_at"),
                )
                return True

        breakout_time = state.get("h4_breakout_bar_time")
        if breakout_time and active_state in {"H4_WAITING", "H4_BREAKOUT_CONFIRMED", "WAITING_FOR_RETEST", "RETEST_CONFIRMED", "CONTINUATION_CONFIRMED"}:
            bars_after = _bars_after(h4, breakout_time)
            if len(bars_after) >= 12 and len(bars_after) < 36:
                log.info("%s setup still active after %s H4 bars; waiting for retest without killing", pair, len(bars_after))

        return False

    def _handle_retest(self, pair: str, h4: pd.DataFrame, eps: float) -> ScanResult | None:
        state = self.states.get(pair)
        level_price = float(state["h4_level_price"])
        breakout_time = state.get("h4_breakout_bar_time")
        bars_after = _bars_after(h4, breakout_time)
        self.states.update(pair, h4_bars_since_breakout=len(bars_after))
        if len(bars_after) > config.MAX_H4_BARS_FOR_RETEST:
            log.info("%s retest window expired; resetting", pair)
            self.states.reset(
                pair,
                last_alert_key=state.get("last_alert_key"),
                last_alert_at=state.get("last_alert_at"),
            )
            return None

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
        return None

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


def _daily_setup(frame: pd.DataFrame, levels: list[KeyLevel], eps: float) -> dict[str, Any] | None:
    a_levels = recent_levels(levels, "A_SHAPE", "RESISTANCE")
    v_levels = recent_levels(levels, "V_SHAPE", "SUPPORT")
    start = max(1, len(frame) - config.DAILY_SETUP_LOOKBACK_BARS)
    for i in range(len(frame) - 1, start - 1, -1):
        candle = frame.iloc[i]
        prev = frame.iloc[i - 1]
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


def _h4_breakout(
    frame: pd.DataFrame,
    levels: list[KeyLevel],
    direction: str,
    eps: float,
    not_before: str | None = None,
) -> dict[str, Any] | None:
    a_levels = recent_levels(levels, "A_SHAPE", "RESISTANCE")
    v_levels = recent_levels(levels, "V_SHAPE", "SUPPORT")
    start = max(1, len(frame) - config.H4_SETUP_LOOKBACK_BARS)
    for i in range(len(frame) - 1, start - 1, -1):
        candle = frame.iloc[i]
        prev = frame.iloc[i - 1]
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
                    return {
                        "level": level,
                        "bar_time": _iso(candle["datetime"]),
                        "close_status": _close_status(candle, "BEARISH", level.price),
                        "level_flip": "SBR",
                    }
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
