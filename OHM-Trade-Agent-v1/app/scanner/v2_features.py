"""Point-in-time V2 features from the scanner's existing completed 1h candles.

No external API requests or side effects. Calendar-aligned complete 4h bars;
if sufficient continuous evidence is missing, directional V2 cannot score.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from app.exchanges.kraken import Candle
from app.indicators.technical import atr_percentage_series, ema, percentile_rank
from app.scanner.technical_score_v2 import V2Features


def _macd_histograms_last_two(closes: Sequence[float]) -> tuple[float, float] | None:
    """O(N) MACD 12/26/9, seed-compatible with app.indicators.technical.macd."""
    if len(closes) < 36:
        return None
    fast = sum(closes[:12]) / 12
    for price in closes[12:26]:
        fast = (price - fast) * (2 / 13) + fast
    slow = sum(closes[:26]) / 26
    macd_values = [fast - slow]
    for price in closes[26:]:
        fast = (price - fast) * (2 / 13) + fast
        slow = (price - slow) * (2 / 27) + slow
        macd_values.append(fast - slow)
    signal = sum(macd_values[:9]) / 9
    histograms = [macd_values[8] - signal]
    for value in macd_values[9:]:
        signal = (value - signal) * (2 / 10) + signal
        histograms.append(value - signal)
    return histograms[-2], histograms[-1]


def _adx14(candles: Sequence[Candle], period: int = 14) -> float | None:
    """Wilder ADX; requires at least two full 14-period smoothing windows."""
    if len(candles) < 2 * period + 1:
        return None
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        up = float(current.high) - float(previous.high)
        down = float(previous.low) - float(current.low)
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(max(
            float(current.high) - float(current.low),
            abs(float(current.high) - float(previous.close)),
            abs(float(current.low) - float(previous.close)),
        ))
    tr = sum(true_ranges[:period])
    pos = sum(plus_dm[:period])
    neg = sum(minus_dm[:period])
    dx: list[float] = []
    for i in range(period - 1, len(true_ranges)):
        if i >= period:
            tr = tr - tr / period + true_ranges[i]
            pos = pos - pos / period + plus_dm[i]
            neg = neg - neg / period + minus_dm[i]
        if tr <= 0:
            dx.append(0.0)
            continue
        positive = pos / tr * 100
        negative = neg / tr * 100
        denom = positive + negative
        dx.append(abs(positive - negative) / denom * 100 if denom > 0 else 0.0)
    if len(dx) < period:
        return None
    adx = sum(dx[:period]) / period
    for value in dx[period:]:
        adx = (adx * (period - 1) + value) / period
    return adx


def _complete_4h_closes(candles: Sequence[Candle]) -> list[tuple[int, float]]:
    groups: dict[int, list[Candle]] = defaultdict(list)
    for c in candles:
        ts = int(c.timestamp)
        if ts % 3600:
            continue
        groups[ts // 14400].append(c)
    completed: list[tuple[int, float]] = []
    for group in sorted(groups):
        rows = sorted(groups[group], key=lambda c: c.timestamp)
        if len(rows) != 4:
            continue
        if [int(c.timestamp) for c in rows] != [group * 14400 + i * 3600 for i in range(4)]:
            continue
        completed.append((group, float(rows[-1].close)))
    return completed


def extract_v2_features(completed_hourly: Sequence[Candle]) -> V2Features:
    """Use *only closed* 1-hour bars as supplied by market_scanner.

    Never silently downgrade to the 15m movement radar's ATR percentile.
    """
    aligned = list(completed_hourly)
    valid_hourly = (
        len(aligned) >= 115
        and all(
            int(current.timestamp) - int(previous.timestamp) == 3600
            for previous, current in zip(aligned[-115:-1], aligned[-114:])
        )
    )
    if not valid_hourly:
        return V2Features(None, None, None, None, None)

    grouped = _complete_4h_closes(aligned)
    recent = grouped[-51:]
    enough_4h = (
        len(recent) == 51
        and all(right[0] - left[0] == 1 for left, right in zip(recent, recent[1:]))
    )
    ema_rising: bool | None = None
    ema_falling: bool | None = None
    if enough_4h:
        closes_4h = [value for _, value in recent]
        earlier = ema(closes_4h[:-1], 50)
        now = ema(closes_4h, 50)
        ema_rising = now > earlier
        ema_falling = now < earlier

    last = aligned[-1]
    bar_range = float(last.high) - float(last.low)
    close_location = (
        (float(last.close) - float(last.low)) / bar_range if bar_range > 0 else None
    )
    history = aligned[-115:]
    vols = atr_percentage_series(
        [float(c.high) for c in history],
        [float(c.low) for c in history],
        [float(c.close) for c in history],
    )
    atr_rank = percentile_rank(vols[-100:])
    hist = _macd_histograms_last_two([float(c.close) for c in aligned])
    histogram_rising = hist[1] > hist[0] if hist else None
    histogram_falling = hist[1] < hist[0] if hist else None

    return V2Features(
        higher_timeframe_ema50_rising=ema_rising,
        adx=_adx14(aligned[-50:]),
        candle_close_location=close_location,
        macd_histogram_rising=histogram_rising,
        atr_percentile=atr_rank,
        higher_timeframe_ema50_falling=ema_falling,
        macd_histogram_falling=histogram_falling,
    )
