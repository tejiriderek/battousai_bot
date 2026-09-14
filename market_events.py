"""Deterministic market-event helpers derived from provider candles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

import config


@dataclass(frozen=True)
class WeekendGap:
    pair: str
    timeframe: str
    candle_time: str
    friday_close: float
    monday_open: float
    gap_pips: float


def detect_weekend_gap(
    frame: pd.DataFrame,
    pair: str,
    timeframe: str,
) -> WeekendGap | None:
    """Return the latest significant Friday-to-Monday gap, if present.

    Crypto pairs are intentionally excluded because they trade continuously over
    the weekend and do not have the same Friday/Monday session boundary.
    """
    if frame.empty or pair.endswith("USDT"):
        return None

    candles = frame.copy()
    candles["datetime"] = pd.to_datetime(candles["datetime"], utc=True, errors="coerce")
    candles = candles.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    monday_indexes = [
        index
        for index, value in enumerate(candles["datetime"])
        if value.weekday() == 0
    ]
    for monday_index in reversed(monday_indexes):
        monday = candles.iloc[monday_index]
        friday_candidates = candles.iloc[:monday_index]
        friday_candidates = friday_candidates[
            friday_candidates["datetime"].dt.weekday == 4
        ]
        if friday_candidates.empty:
            continue
        friday = friday_candidates.iloc[-1]
        friday_close = float(friday["close"])
        monday_open = float(monday["open"])
        gap_pips = abs(monday_open - friday_close) / config.pip_size(pair)
        if gap_pips <= config.GAP_THRESHOLD_PIPS:
            return None
        return WeekendGap(
            pair=pair,
            timeframe=timeframe,
            candle_time=pd.Timestamp(monday["datetime"]).isoformat(),
            friday_close=friday_close,
            monday_open=monday_open,
            gap_pips=gap_pips,
        )
    return None


def gap_candle_times(frame: pd.DataFrame, pair: str, timeframe: str) -> set[str]:
    """Return significant Monday-open candle timestamps for breakout exclusion."""
    gap = detect_weekend_gap(frame, pair, timeframe)
    return {gap.candle_time} if gap else set()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
