"""A-Shape (resistance), V-Shape (support), and Open-Close (OCL) key levels."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import config


@dataclass(frozen=True)
class KeyLevel:
    kind: str  # A_SHAPE | V_SHAPE | OCL
    role: str  # RESISTANCE | SUPPORT
    price: float
    zone_low: float
    zone_high: float
    index: int
    timestamp: str

    @property
    def label(self) -> str:
        return self.kind.replace("_", "-")


def detect_levels(frame: pd.DataFrame) -> list[KeyLevel]:
    if frame is None or len(frame) < config.MIN_CANDLES:
        return []
    atr = _atr(frame, config.ATR_PERIOD)
    levels: list[KeyLevel] = []
    levels.extend(_a_shape_levels(frame, atr))
    levels.extend(_v_shape_levels(frame, atr))
    levels.extend(_ocl_levels(frame, atr))
    levels.sort(key=lambda item: item.index)
    return levels


def recent_levels(levels: list[KeyLevel], kind: str, role: str | None = None) -> list[KeyLevel]:
    filtered = [item for item in levels if item.kind == kind]
    if role:
        filtered = [item for item in filtered if item.role == role]
    return filtered[-config.RECENT_LEVELS :]


def nearest_level(levels: list[KeyLevel], price: float) -> KeyLevel | None:
    if not levels:
        return None
    return min(levels, key=lambda item: abs(item.price - price))


def _a_shape_levels(frame: pd.DataFrame, atr: pd.Series) -> list[KeyLevel]:
    highs = frame["high"].to_numpy()
    lows = frame["low"].to_numpy()
    left, right = config.PIVOT_LEFT, config.PIVOT_RIGHT
    out: list[KeyLevel] = []
    end = len(frame) - right
    for i in range(left, end):
        window = highs[i - left : i + right + 1]
        if highs[i] < np.max(window) - 1e-12:
            continue
        if np.sum(np.isclose(window, highs[i])) > 2:
            continue
        left_run = highs[i] - np.min(lows[i - left : i + 1])
        right_run = highs[i] - np.min(lows[i : i + right + 1])
        threshold = float(atr.iloc[i]) * config.SHARP_ATR_MULT
        if threshold <= 0 or left_run < threshold or right_run < threshold:
            continue
        price = float(highs[i])
        out.append(
            KeyLevel(
                kind="A_SHAPE",
                role="RESISTANCE",
                price=price,
                zone_low=price,
                zone_high=price,
                index=i,
                timestamp=_ts(frame, i),
            )
        )
    return out


def _v_shape_levels(frame: pd.DataFrame, atr: pd.Series) -> list[KeyLevel]:
    highs = frame["high"].to_numpy()
    lows = frame["low"].to_numpy()
    left, right = config.PIVOT_LEFT, config.PIVOT_RIGHT
    out: list[KeyLevel] = []
    end = len(frame) - right
    for i in range(left, end):
        window = lows[i - left : i + right + 1]
        if lows[i] > np.min(window) + 1e-12:
            continue
        if np.sum(np.isclose(window, lows[i])) > 2:
            continue
        left_run = np.max(highs[i - left : i + 1]) - lows[i]
        right_run = np.max(highs[i : i + right + 1]) - lows[i]
        threshold = float(atr.iloc[i]) * config.SHARP_ATR_MULT
        if threshold <= 0 or left_run < threshold or right_run < threshold:
            continue
        price = float(lows[i])
        out.append(
            KeyLevel(
                kind="V_SHAPE",
                role="SUPPORT",
                price=price,
                zone_low=price,
                zone_high=price,
                index=i,
                timestamp=_ts(frame, i),
            )
        )
    return out


def _ocl_levels(frame: pd.DataFrame, atr: pd.Series) -> list[KeyLevel]:
    """Option C: OCL is the open-close body of a reversal candle."""
    out: list[KeyLevel] = []
    for i in range(1, len(frame) - 1):
        candle = frame.iloc[i]
        prev = frame.iloc[i - 1]
        body_low = float(min(candle["open"], candle["close"]))
        body_high = float(max(candle["open"], candle["close"]))
        body = body_high - body_low
        upper = float(candle["high"] - body_high)
        lower = float(body_low - candle["low"])
        rng = float(candle["high"] - candle["low"])
        if rng <= 0:
            continue
        threshold = float(atr.iloc[i]) * 0.35
        bullish_reversal = False
        bearish_reversal = False

        engulf_bull = (
            candle["close"] > candle["open"]
            and prev["close"] < prev["open"]
            and candle["close"] >= prev["open"]
            and candle["open"] <= prev["close"]
        )
        engulf_bear = (
            candle["close"] < candle["open"]
            and prev["close"] > prev["open"]
            and candle["close"] <= prev["open"]
            and candle["open"] >= prev["close"]
        )
        pin_bull = lower >= 2 * max(body, 1e-12) and lower >= 0.55 * rng and candle["close"] > body_low
        pin_bear = upper >= 2 * max(body, 1e-12) and upper >= 0.55 * rng and candle["close"] < body_high

        if (engulf_bull or pin_bull) and rng >= threshold:
            bullish_reversal = True
        if (engulf_bear or pin_bear) and rng >= threshold:
            bearish_reversal = True
        if not bullish_reversal and not bearish_reversal:
            continue

        role = "SUPPORT" if bullish_reversal and not bearish_reversal else "RESISTANCE"
        if bullish_reversal and bearish_reversal:
            role = "SUPPORT" if candle["close"] >= candle["open"] else "RESISTANCE"
        out.append(
            KeyLevel(
                kind="OCL",
                role=role,
                price=(body_low + body_high) / 2.0,
                zone_low=body_low,
                zone_high=body_high,
                index=i,
                timestamp=_ts(frame, i),
            )
        )
    return out


def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    prev_close = frame["close"].shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    return atr.bfill().fillna(tr.median())


def _ts(frame: pd.DataFrame, index: int) -> str:
    value = frame.iloc[index]["datetime"]
    return pd.Timestamp(value).isoformat()
