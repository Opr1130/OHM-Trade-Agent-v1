from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from statistics import median

from app.exchanges.kraken import Candle


PASS = "PASS"
WARN = "WARN"
REJECT = "REJECT"

# Indicators and target structure use the latest 72 hourly observations.
RECENT_CONTINUITY_HOURS = 72
# One absent hourly observation is diagnosable; two or more consecutive
# missing observations inside the recent calculation window make it unsafe.
RECENT_GAP_REJECT_MULTIPLE = 2.0
# Kraken may include the currently forming candle. Two intervals allows either
# that representation or the most recently closed candle without false rejects.
FRESHNESS_WARN_INTERVALS = 1.5
FRESHNESS_REJECT_INTERVALS = 2.0
# Ticker and OHLC are sampled at different instants. Thresholds expand with
# robust observed hourly movement and retain broad crypto-safe floors.
TICKER_WARN_FLOOR_PCT = 2.0
TICKER_REJECT_FLOOR_PCT = 10.0
TICKER_WARN_RANGE_MULTIPLE = 4.0
TICKER_REJECT_RANGE_MULTIPLE = 10.0
# Spike detection is warning-only in v1 and requires both a broad 5% floor and
# an eight-fold departure from robust local range/return behavior.
SPIKE_FLOOR_PCT = 5.0
SPIKE_LOCAL_MULTIPLE = 8.0


@dataclass(frozen=True)
class MarketDataValidation:
    status: str
    qualified: bool
    warnings: list[str]
    rejection_reasons: list[str]
    candle_count: int
    latest_candle_timestamp: int | None
    latest_candle_age_seconds: float | None
    duplicate_timestamp_count: int
    gap_count: int
    largest_gap_seconds: float
    invalid_ohlc_count: int
    non_finite_value_count: int
    ticker_last: float | None
    latest_ohlc_close: float | None
    ticker_vs_ohlc_difference_pct: float | None
    suspicious_spike_detected: bool


def _pct_difference(left: float, right: float) -> float:
    return abs(left - right) / right * 100 if right > 0 else math.inf


def validate_market_data(
    candles: list[Candle],
    ticker_last: float | None,
    interval_minutes: int,
    now: datetime | None = None,
) -> MarketDataValidation:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    interval_seconds = interval_minutes * 60
    warnings: list[str] = []
    rejections: list[str] = []
    non_finite = 0
    invalid_ohlc = 0

    timestamps: list[float] = []
    for candle in candles:
        values = (
            candle.timestamp, candle.open, candle.high, candle.low,
            candle.close, candle.vwap, candle.volume,
        )
        if not all(isinstance(value, (int, float)) for value in values):
            non_finite += 1
            continue
        if not all(math.isfinite(float(value)) for value in values):
            non_finite += 1
            continue
        timestamps.append(float(candle.timestamp))
        ohlc_prices = (candle.open, candle.high, candle.low, candle.close)
        zero_activity_vwap = (
            candle.vwap == 0
            and candle.volume == 0
            and candle.trade_count == 0
        )
        if (
            any(price <= 0 for price in ohlc_prices)
            or candle.volume < 0
            or candle.vwap < 0
            or (candle.vwap == 0 and not zero_activity_vwap)
        ):
            invalid_ohlc += 1
            continue
        if (
            candle.high < candle.low
            or candle.high < candle.open
            or candle.high < candle.close
            or candle.low > candle.open
            or candle.low > candle.close
        ):
            invalid_ohlc += 1

    if non_finite:
        rejections.append(f"{non_finite} candle(s) contain non-finite core values")
    if invalid_ohlc:
        rejections.append(f"{invalid_ohlc} candle(s) have invalid OHLC/volume geometry")

    duplicate_count = len(timestamps) - len(set(timestamps))
    if duplicate_count:
        rejections.append(f"{duplicate_count} duplicate candle timestamp(s)")
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        rejections.append("candle timestamps are not strictly increasing")

    gaps = [
        current - previous
        for previous, current in zip(timestamps, timestamps[1:])
        if current - previous > interval_seconds
    ]
    largest_gap = max(gaps, default=0.0)
    recent_start = max(0, len(timestamps) - RECENT_CONTINUITY_HOURS - 1)
    recent_gaps = [
        current - previous
        for previous, current in zip(
            timestamps[recent_start:], timestamps[recent_start + 1:]
        )
        if current - previous > interval_seconds
    ]
    if any(gap > interval_seconds * RECENT_GAP_REJECT_MULTIPLE for gap in recent_gaps):
        rejections.append("recent candle continuity has a severe gap")
    elif recent_gaps:
        warnings.append("recent candle continuity contains a minor gap")
    if gaps and not recent_gaps:
        warnings.append("historical candle gap exists outside the recent 72h window")

    latest_timestamp = int(timestamps[-1]) if timestamps else None
    age = now.timestamp() - latest_timestamp if latest_timestamp is not None else None
    if age is None:
        rejections.append("no valid candle timestamp available")
    elif age < -interval_seconds:
        rejections.append("latest candle timestamp is implausibly in the future")
    elif age > interval_seconds * FRESHNESS_REJECT_INTERVALS:
        rejections.append("latest hourly candle is stale")
    elif age > interval_seconds * FRESHNESS_WARN_INTERVALS:
        warnings.append("latest hourly candle is approaching stale")

    valid_candles = [
        candle for candle in candles
        if all(math.isfinite(float(value)) for value in (
            candle.open, candle.high, candle.low, candle.close, candle.volume
        )) and candle.close > 0
    ]
    latest_close = valid_candles[-1].close if valid_candles else None
    hourly_ranges = [
        (candle.high - candle.low) / candle.close * 100
        for candle in valid_candles[-73:-1]
        if candle.close > 0 and candle.high >= candle.low
    ]
    local_range = median(hourly_ranges) if hourly_ranges else 0.0
    ticker_difference = None
    if ticker_last is not None:
        if not math.isfinite(ticker_last) or ticker_last <= 0:
            rejections.append("ticker last price is non-finite or non-positive")
        elif latest_close is not None:
            ticker_difference = _pct_difference(ticker_last, latest_close)
            reject_at = max(
                TICKER_REJECT_FLOOR_PCT,
                local_range * TICKER_REJECT_RANGE_MULTIPLE,
            )
            warn_at = max(
                TICKER_WARN_FLOOR_PCT,
                local_range * TICKER_WARN_RANGE_MULTIPLE,
            )
            if ticker_difference > reject_at:
                rejections.append("ticker and latest OHLC close are extremely inconsistent")
            elif ticker_difference > warn_at:
                warnings.append("ticker and latest OHLC close differ materially")

    suspicious_spike = False
    if len(valid_candles) >= 3:
        context = valid_candles[-73:-1]
        context_ranges = [
            (c.high - c.low) / c.close * 100 for c in context if c.close > 0
        ]
        returns = [
            abs((current.close - previous.close) / previous.close * 100)
            for previous, current in zip(context, context[1:])
            if previous.close > 0
        ]
        latest = valid_candles[-1]
        previous = valid_candles[-2]
        latest_range = (latest.high - latest.low) / latest.close * 100
        latest_return = abs((latest.close - previous.close) / previous.close * 100)
        range_limit = max(SPIKE_FLOOR_PCT, median(context_ranges) * SPIKE_LOCAL_MULTIPLE)
        return_limit = max(SPIKE_FLOOR_PCT, median(returns) * SPIKE_LOCAL_MULTIPLE)
        suspicious_spike = latest_range > range_limit or latest_return > return_limit
        if suspicious_spike:
            warnings.append("latest candle is an unusual spike versus robust local behavior")

    status = REJECT if rejections else WARN if warnings else PASS
    return MarketDataValidation(
        status=status, qualified=not rejections, warnings=warnings,
        rejection_reasons=rejections, candle_count=len(candles),
        latest_candle_timestamp=latest_timestamp,
        latest_candle_age_seconds=age,
        duplicate_timestamp_count=duplicate_count, gap_count=len(gaps),
        largest_gap_seconds=largest_gap, invalid_ohlc_count=invalid_ohlc,
        non_finite_value_count=non_finite, ticker_last=ticker_last,
        latest_ohlc_close=latest_close,
        ticker_vs_ohlc_difference_pct=ticker_difference,
        suspicious_spike_detected=suspicious_spike,
    )
