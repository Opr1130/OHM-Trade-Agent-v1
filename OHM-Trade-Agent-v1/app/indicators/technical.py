from __future__ import annotations

from collections.abc import Sequence


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
