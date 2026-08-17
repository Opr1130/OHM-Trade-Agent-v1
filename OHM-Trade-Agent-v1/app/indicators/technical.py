from __future__ import annotations

from collections.abc import Sequence
from statistics import pstdev


def ema(values: Sequence[float], period: int) -> float:
    if period <= 0:
        raise ValueError("period must be greater than zero")

    if len(values) < period:
        raise ValueError(
            f"EMA requires at least {period} values; received {len(values)}"
        )

    multiplier = 2 / (period + 1)
    current_ema = sum(values[:period]) / period

    for value in values[period:]:
        current_ema = ((value - current_ema) * multiplier) + current_ema

    return current_ema


def rsi(values: Sequence[float], period: int = 14) -> float:
    if period <= 0:
        raise ValueError("period must be greater than zero")

    if len(values) < period + 1:
        raise ValueError(
            f"RSI requires at least {period + 1} values; received {len(values)}"
        )

    gains: list[float] = []
    losses: list[float] = []

    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def macd(
    values: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float]:
    if len(values) < slow_period + signal_period:
        raise ValueError(
            f"MACD requires at least {slow_period + signal_period} values"
        )

    fast_values: list[float] = []
    slow_values: list[float] = []

    for index in range(slow_period, len(values) + 1):
        window = values[:index]
        fast_values.append(ema(window, fast_period))
        slow_values.append(ema(window, slow_period))

    macd_values = [
        fast - slow
        for fast, slow in zip(fast_values, slow_values)
    ]

    macd_line = macd_values[-1]
    signal_line = ema(macd_values, signal_period)
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must have equal lengths")

    if len(closes) < period + 1:
        raise ValueError(
            f"ATR requires at least {period + 1} candles"
        )

    true_ranges: list[float] = []

    for index in range(1, len(closes)):
        high = highs[index]
        low = lows[index]
        previous_close = closes[index - 1]

        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    current_atr = sum(true_ranges[:period]) / period

    for true_range in true_ranges[period:]:
        current_atr = (
            (current_atr * (period - 1)) + true_range
        ) / period

    return current_atr


def volume_ratio(
    volumes: Sequence[float],
    period: int = 20,
) -> float:
    if len(volumes) < period + 1:
        raise ValueError(
            f"Volume ratio requires at least {period + 1} values"
        )

    current_volume = volumes[-2]
    average_volume = sum(volumes[-(period + 2):-2]) / period

    if average_volume == 0:
        return 0.0

    return current_volume / average_volume


def bollinger_bandwidth(
    values: Sequence[float],
    period: int = 20,
    standard_deviations: float = 2.0,
) -> float:
    """Return normalized Bollinger Band width as a percentage.

    The result measures compression only. It deliberately carries no
    directional or probability semantics.
    """
    if period <= 1:
        raise ValueError("period must be greater than one")
    if standard_deviations <= 0:
        raise ValueError("standard_deviations must be positive")
    if len(values) < period:
        raise ValueError(
            f"Bollinger bandwidth requires at least {period} values"
        )

    window = [float(value) for value in values[-period:]]
    middle = sum(window) / period
    if middle <= 0:
        raise ValueError("Bollinger bandwidth requires a positive middle band")
    deviation = pstdev(window)
    return (2.0 * standard_deviations * deviation / middle) * 100.0


def bollinger_bandwidth_series(
    values: Sequence[float],
    period: int = 20,
    standard_deviations: float = 2.0,
) -> list[float]:
    if len(values) < period:
        raise ValueError(
            f"Bollinger bandwidth requires at least {period} values"
        )
    return [
        bollinger_bandwidth(
            values[:end],
            period=period,
            standard_deviations=standard_deviations,
        )
        for end in range(period, len(values) + 1)
    ]


def atr_percentage_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float]:
    """Return Wilder ATR as a percentage for every fully formed window."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must have equal lengths")
    if len(closes) < period + 1:
        raise ValueError(
            f"ATR requires at least {period + 1} candles"
        )

    true_ranges = [
        max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        for index in range(1, len(closes))
    ]
    current = sum(true_ranges[:period]) / period
    values: list[float] = []
    first_close = float(closes[period])
    values.append((current / first_close) * 100.0 if first_close > 0 else 0.0)
    for true_range_index in range(period, len(true_ranges)):
        current = (
            (current * (period - 1)) + true_ranges[true_range_index]
        ) / period
        close = float(closes[true_range_index + 1])
        values.append((current / close) * 100.0 if close > 0 else 0.0)
    return values


def percentile_rank(values: Sequence[float], value: float | None = None) -> float:
    """Return the percentage of observations at or below ``value``.

    Including equal observations means a flat series ranks at 100 rather than
    being misclassified as an unusually rare compression.
    """
    if not values:
        raise ValueError("percentile rank requires at least one value")
    target = float(values[-1] if value is None else value)
    return sum(float(item) <= target for item in values) / len(values) * 100.0
