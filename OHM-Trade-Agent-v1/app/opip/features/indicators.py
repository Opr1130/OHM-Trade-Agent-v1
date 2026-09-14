"""Adapter over the proven production indicator math (PR3 slice D).

``app.indicators.technical`` stays the single implementation of EMA, ATR,
Bollinger bandwidth, volume ratio and percentile rank. This module only adapts
its calling convention: production callers want an exception when history is
too short, while the feature bus wants ``None`` plus a recorded missingness
reason, because a snapshot with a known-absent feature is still valid evidence.

No math is reimplemented here. If a number differs from what the scanner
computes on the same inputs, that is a parity defect to surface, not a second
opinion to keep.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.indicators.technical import (
    atr_percentage_series,
    bollinger_bandwidth,
    bollinger_bandwidth_series,
    ema,
    percentile_rank,
    volume_ratio,
)


def safe_ema(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return ema(values, period)


def safe_volume_ratio(volumes: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(volumes) < period + 1:
        return None
    return volume_ratio(volumes, period)


def safe_bandwidth(closes: Sequence[float], period: int) -> float | None:
    if period <= 1 or len(closes) < period:
        return None
    window = closes[-period:]
    if sum(window) / period <= 0:
        return None
    return bollinger_bandwidth(closes, period)


def safe_bandwidth_series(
    closes: Sequence[float], period: int
) -> list[float] | None:
    if period <= 1 or len(closes) < period:
        return None
    if any(float(value) <= 0 for value in closes):
        return None
    return bollinger_bandwidth_series(closes, period)


def safe_atr_percentage_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> list[float] | None:
    if period <= 0:
        return None
    if not (len(highs) == len(lows) == len(closes)):
        return None
    if len(closes) < period + 1:
        return None
    return atr_percentage_series(highs, lows, closes, period)


def safe_percentile_rank(
    values: Sequence[float], value: float | None = None
) -> float | None:
    if not values:
        return None
    return percentile_rank(values, value)


__all__ = [
    "safe_atr_percentage_series",
    "safe_bandwidth",
    "safe_bandwidth_series",
    "safe_ema",
    "safe_percentile_rank",
    "safe_volume_ratio",
]
