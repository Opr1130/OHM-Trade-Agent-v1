"""One shared feature engine for the O'Pip feature bus (PR3 slice D).

Scope discipline: this module computes *features*. It contains no detector
logic, no thresholds that admit or reject a candidate, and it deliberately does
not import or reproduce READY, Signal Quality, technical score or profit rank.
Those are decisions built on features, and mixing them back in is what produced
eight parallel feature implementations in the first place.

Two properties matter more than the feature list itself:

* Every value is either present or explicitly missing with a reason. There are
  no silent zeros standing in for absent evidence.
* Rolling windows are computed on the contiguous tail after the most recent
  feed gap, so a 20-bar average never quietly spans a hole.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from app.opip.contracts.enums import (
    CompressionState,
    CoverageState,
    Missingness,
    RestartState,
    TrendState,
    VolatilityState,
)
from app.opip.contracts.features import (
    DEFAULT_EVALUATION_GRID_SECONDS,
    FeatureSnapshot,
)
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.observation import Observation
from app.opip.contracts.serialization import stable_hash
from app.opip.contracts.temporal import AvailabilityStamp
from app.opip.features.indicators import (
    safe_atr_percentage_series,
    safe_bandwidth,
    safe_bandwidth_series,
    safe_ema,
    safe_percentile_rank,
    safe_volume_ratio,
)
from app.opip.market.aggregates import AlignmentResult, contiguous_tail

FEATURE_VERSION = "features-v1"
FEATURE_SCHEMA_VERSION = "features-schema-v1"
FEATURE_CALC_VERSION = "features-calc-v1"

EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
ATR_PERIOD = 14
BANDWIDTH_PERIOD = 20
VOLUME_PERIOD = 20
VOLUME_EXPANSION_FAST = 5
RANGE_WINDOW_INTERVALS = 15
LOCATION_WINDOW_INTERVALS = 60
PERCENTILE_LOOKBACK_INTERVALS = 120
COMPRESSION_PERCENTILE_THRESHOLD = 20.0
EXPANSION_PERCENTILE_THRESHOLD = 80.0
VOLATILITY_LOW_PERCENTILE = 25.0
VOLATILITY_HIGH_PERCENTILE = 75.0

#: Bound on how much contiguous history any feature may see. It is also the
#: retained window a checkpoint carries, which is what makes "resume from
#: checkpoint equals uninterrupted processing" a testable claim rather than an
#: aspiration: both paths see exactly this many intervals at most.
#:
#: Sized to exactly what the slowest feature needs (the bandwidth percentile
#: lookback plus one bandwidth period) rather than generously, because the
#: retained window is carried in every checkpoint payload and the canonical
#: writer bounds payloads at 16 KiB.
FEATURE_WINDOW_INTERVALS = PERCENTILE_LOOKBACK_INTERVALS + BANDWIDTH_PERIOD

#: Warm only when the retained contiguous window can satisfy every declared
#: supported rolling feature. Derived from the same bound as
#: ``FEATURE_WINDOW_INTERVALS`` so 21–139 bars cannot be labeled fully WARM
#: while percentile/bandwidth features are still missing by construction.
MINIMUM_WARMUP_INTERVALS = FEATURE_WINDOW_INTERVALS

#: N10, restated where it is enforced: longer candles are feature inputs; the
#: evaluation cadence itself is one minute.
N10_NOTE = (
    "15-minute range is a feature input only; "
    "IGNITION evaluation_cutoff is 1-minute."
)

VALUE_PRECISION = 8

#: Inputs the feature bus does not retain on this path (Contract B: raw trades
#: and book depth are ephemeral). Recorded as a deliberate retention decision,
#: not as evidence we expected and lost.
NOT_RETAINED_INPUTS = ("book_depth", "trade_tape")

FEATURE_NAMES: tuple[str, ...] = (
    "return_1m",
    "return_5m",
    "return_15m",
    "return_60m",
    "velocity_5m",
    "velocity_15m",
    "acceleration_5m_vs_15m",
    "relative_volume_20m",
    "volume_expansion_5m_vs_20m",
    "range_15m",
    "atr_pct_14",
    "atr_pct_percentile",
    "volatility_state",
    "bandwidth_20",
    "bandwidth_percentile",
    "compression_state",
    "compression_depth",
    "compression_duration",
    "compression_release_score",
    "distance_from_high_60m",
    "distance_from_low_60m",
    "ema_fast_9",
    "ema_slow_21",
    "ema_spread_pct",
    "trend_state",
    "notional_1m",
    "notional_20m_mean",
    "tick_size_pct",
    "min_order_notional",
    "reference_metadata_complete",
    "coverage_ratio",
    "missing_intervals",
    "late_arrival_count",
    "contiguous_intervals",
    "staleness_seconds",
    "book_depth_imbalance",
    "trade_tape_intensity",
)

#: Features describing *this fetch* rather than the instrument's rolling
#: history. A resumed checkpoint cannot be expected to reproduce them, so
#: replay equivalence is asserted over ROLLING_FEATURE_NAMES only.
FRESHNESS_FEATURE_NAMES: tuple[str, ...] = (
    "coverage_ratio",
    "missing_intervals",
    "late_arrival_count",
    "staleness_seconds",
)

ROLLING_FEATURE_NAMES: tuple[str, ...] = tuple(
    name for name in FEATURE_NAMES if name not in FRESHNESS_FEATURE_NAMES
)

_PARAMS: dict[str, int | float] = {
    "ema_fast_period": EMA_FAST_PERIOD,
    "ema_slow_period": EMA_SLOW_PERIOD,
    "atr_period": ATR_PERIOD,
    "bandwidth_period": BANDWIDTH_PERIOD,
    "volume_period": VOLUME_PERIOD,
    "volume_expansion_fast": VOLUME_EXPANSION_FAST,
    "range_window": RANGE_WINDOW_INTERVALS,
    "location_window": LOCATION_WINDOW_INTERVALS,
    "percentile_lookback": PERCENTILE_LOOKBACK_INTERVALS,
    "feature_window_intervals": FEATURE_WINDOW_INTERVALS,
    "compression_percentile_threshold": COMPRESSION_PERCENTILE_THRESHOLD,
    "expansion_percentile_threshold": EXPANSION_PERCENTILE_THRESHOLD,
    "volatility_low_percentile": VOLATILITY_LOW_PERCENTILE,
    "volatility_high_percentile": VOLATILITY_HIGH_PERCENTILE,
    "value_precision": VALUE_PRECISION,
}


def feature_dag_hash() -> str:
    """Identity of the computation, not just of its version string.

    Adding a feature or changing a period changes this hash, so evidence
    produced under different definitions can never be silently pooled.
    """
    return stable_hash(
        "FDAG",
        {
            "feature_version": FEATURE_VERSION,
            "feature_calc_version": FEATURE_CALC_VERSION,
            "features": list(FEATURE_NAMES),
            "params": dict(sorted(_PARAMS.items())),
        },
    )


@dataclass(frozen=True)
class FeatureComputation:
    """Feature values plus why anything absent is absent."""

    values: Mapping[str, Any]
    missingness: Mapping[str, Missingness]
    contiguous_intervals: int
    warm: bool

    @property
    def present_count(self) -> int:
        return sum(
            1
            for state in self.missingness.values()
            if state is Missingness.PRESENT
        )


class _Accumulator:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.missingness: dict[str, Missingness] = {}

    def number(self, name: str, value: float | int | None) -> None:
        if value is None:
            self.values[name] = None
            self.missingness[name] = Missingness.MISSING
            return
        self.values[name] = round(float(value), VALUE_PRECISION)
        self.missingness[name] = Missingness.PRESENT

    def literal(self, name: str, value: str | bool | int | None) -> None:
        if value is None:
            self.values[name] = None
            self.missingness[name] = Missingness.MISSING
            return
        self.values[name] = value
        self.missingness[name] = Missingness.PRESENT

    def not_retained(self, name: str) -> None:
        self.values[name] = None
        self.missingness[name] = Missingness.NOT_RETAINED


def _series(observations: Sequence[Observation], key: str) -> list[float]:
    return [float(item.values[key]) for item in observations]


def _pct_change(closes: Sequence[float], intervals: int) -> float | None:
    if intervals <= 0 or len(closes) < intervals + 1:
        return None
    previous = closes[-1 - intervals]
    if previous <= 0:
        return None
    return (closes[-1] / previous - 1.0) * 100.0


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _expanding_percentiles(series: Sequence[float]) -> list[float]:
    """Percentile of each point within the history available at that point.

    Expanding rather than full-window, so no point is ranked against values
    that had not happened yet.
    """
    return [
        safe_percentile_rank(series[: index + 1], series[index]) or 0.0
        for index in range(len(series))
    ]


def _trailing_run(percentiles: Sequence[float], threshold: float) -> int:
    run = 0
    for value in reversed(percentiles):
        if value <= threshold:
            run += 1
            continue
        break
    return run


def _compression(accumulator: _Accumulator, closes: Sequence[float]) -> None:
    bandwidth = safe_bandwidth(closes, BANDWIDTH_PERIOD)
    accumulator.number("bandwidth_20", bandwidth)

    window = closes[-(PERCENTILE_LOOKBACK_INTERVALS + BANDWIDTH_PERIOD) :]
    series = safe_bandwidth_series(window, BANDWIDTH_PERIOD)
    if not series:
        accumulator.number("bandwidth_percentile", None)
        accumulator.literal("compression_state", CompressionState.UNKNOWN.value)
        accumulator.number("compression_depth", None)
        accumulator.number("compression_duration", None)
        accumulator.number("compression_release_score", None)
        return

    percentiles = _expanding_percentiles(series)
    current_percentile = percentiles[-1]
    accumulator.number("bandwidth_percentile", current_percentile)

    if current_percentile <= COMPRESSION_PERCENTILE_THRESHOLD:
        state = CompressionState.COILED
    elif current_percentile >= EXPANSION_PERCENTILE_THRESHOLD:
        state = CompressionState.EXPANDED
    else:
        state = CompressionState.NEUTRAL
    accumulator.literal("compression_state", state.value)

    # Depth: 1.0 means the tightest bandwidth observed in the window.
    accumulator.number("compression_depth", 1.0 - current_percentile / 100.0)
    accumulator.number(
        "compression_duration",
        _trailing_run(percentiles, COMPRESSION_PERCENTILE_THRESHOLD),
    )

    recent = series[-BANDWIDTH_PERIOD:]
    floor_value = min(recent) if recent else None
    if floor_value is None or floor_value <= 0 or series[-1] is None:
        accumulator.number("compression_release_score", None)
        return
    # How far bandwidth has expanded off its recent floor. Zero while still
    # compressed; it carries no directional claim.
    accumulator.number(
        "compression_release_score", max(0.0, series[-1] / floor_value - 1.0)
    )


def compute_features(
    alignment: AlignmentResult,
    *,
    instrument_version: InstrumentVersion,
    evaluated_at_utc: datetime,
    freshness_alignment: AlignmentResult | None = None,
) -> FeatureComputation:
    """Compute the IGNITION-oriented feature set from aligned minute bars.

    ``alignment`` supplies the contiguous series for rolling features.
    ``freshness_alignment`` (default: same object) supplies coverage / late /
    expected counts for this evaluation's fetch, which may differ when rolling
    history comes from retained state rather than the current batch alone.
    """
    coverage = freshness_alignment or alignment
    tail = contiguous_tail(alignment)[-FEATURE_WINDOW_INTERVALS:]
    closes = _series(tail, "close")
    highs = _series(tail, "high")
    lows = _series(tail, "low")
    volumes = _series(tail, "volume")
    count = len(tail)

    acc = _Accumulator()

    acc.number("return_1m", _pct_change(closes, 1))
    acc.number("return_5m", _pct_change(closes, 5))
    acc.number("return_15m", _pct_change(closes, 15))
    acc.number("return_60m", _pct_change(closes, 60))

    return_5m = acc.values.get("return_5m")
    return_15m = acc.values.get("return_15m")
    velocity_5m = return_5m / 5.0 if return_5m is not None else None
    velocity_15m = return_15m / 15.0 if return_15m is not None else None
    acc.number("velocity_5m", velocity_5m)
    acc.number("velocity_15m", velocity_15m)
    acc.number(
        "acceleration_5m_vs_15m",
        velocity_5m - velocity_15m
        if velocity_5m is not None and velocity_15m is not None
        else None,
    )

    acc.number("relative_volume_20m", safe_volume_ratio(volumes, VOLUME_PERIOD))
    expansion: float | None = None
    if count >= VOLUME_PERIOD + VOLUME_EXPANSION_FAST:
        fast = _mean(volumes[-VOLUME_EXPANSION_FAST:])
        baseline = _mean(
            volumes[-(VOLUME_PERIOD + VOLUME_EXPANSION_FAST) : -VOLUME_EXPANSION_FAST]
        )
        if fast is not None and baseline is not None and baseline > 0:
            expansion = fast / baseline
    acc.number("volume_expansion_5m_vs_20m", expansion)

    range_15m: float | None = None
    if count >= RANGE_WINDOW_INTERVALS and closes[-1] > 0:
        span = max(highs[-RANGE_WINDOW_INTERVALS:]) - min(lows[-RANGE_WINDOW_INTERVALS:])
        range_15m = span / closes[-1] * 100.0
    acc.number("range_15m", range_15m)

    atr_series = safe_atr_percentage_series(highs, lows, closes, ATR_PERIOD)
    atr_pct = atr_series[-1] if atr_series else None
    acc.number("atr_pct_14", atr_pct)
    atr_percentile: float | None = None
    if atr_series:
        atr_percentile = safe_percentile_rank(
            atr_series[-PERCENTILE_LOOKBACK_INTERVALS:]
        )
    acc.number("atr_pct_percentile", atr_percentile)
    if atr_percentile is None:
        acc.literal("volatility_state", VolatilityState.UNKNOWN.value)
    elif atr_percentile <= VOLATILITY_LOW_PERCENTILE:
        acc.literal("volatility_state", VolatilityState.LOW.value)
    elif atr_percentile >= VOLATILITY_HIGH_PERCENTILE:
        acc.literal("volatility_state", VolatilityState.HIGH.value)
    else:
        acc.literal("volatility_state", VolatilityState.NORMAL.value)

    _compression(acc, closes)

    distance_high: float | None = None
    distance_low: float | None = None
    if count >= LOCATION_WINDOW_INTERVALS:
        window_high = max(highs[-LOCATION_WINDOW_INTERVALS:])
        window_low = min(lows[-LOCATION_WINDOW_INTERVALS:])
        if window_high > 0:
            distance_high = (closes[-1] / window_high - 1.0) * 100.0
        if window_low > 0:
            distance_low = (closes[-1] / window_low - 1.0) * 100.0
    acc.number("distance_from_high_60m", distance_high)
    acc.number("distance_from_low_60m", distance_low)

    ema_fast = safe_ema(closes, EMA_FAST_PERIOD)
    ema_slow = safe_ema(closes, EMA_SLOW_PERIOD)
    acc.number("ema_fast_9", ema_fast)
    acc.number("ema_slow_21", ema_slow)
    spread: float | None = None
    if ema_fast is not None and ema_slow is not None and ema_slow > 0:
        spread = (ema_fast / ema_slow - 1.0) * 100.0
    acc.number("ema_spread_pct", spread)
    if ema_fast is None or ema_slow is None:
        acc.literal("trend_state", TrendState.UNKNOWN.value)
    elif ema_fast > ema_slow and closes[-1] >= ema_fast:
        acc.literal("trend_state", TrendState.UP.value)
    elif ema_fast < ema_slow and closes[-1] <= ema_fast:
        acc.literal("trend_state", TrendState.DOWN.value)
    else:
        acc.literal("trend_state", TrendState.FLAT.value)

    acc.number(
        "notional_1m", closes[-1] * volumes[-1] if count >= 1 else None
    )
    notional_mean: float | None = None
    if count >= VOLUME_PERIOD:
        notional_mean = _mean(
            [
                closes[index] * volumes[index]
                for index in range(count - VOLUME_PERIOD, count)
            ]
        )
    acc.number("notional_20m_mean", notional_mean)

    last_close = closes[-1] if count >= 1 else None
    tick_size = instrument_version.tick_size
    acc.number(
        "tick_size_pct",
        tick_size / last_close * 100.0
        if tick_size is not None and last_close and last_close > 0
        else None,
    )
    min_order_size = instrument_version.min_order_size
    acc.number(
        "min_order_notional",
        min_order_size * last_close
        if min_order_size is not None and last_close is not None
        else None,
    )
    acc.literal(
        "reference_metadata_complete",
        tick_size is not None and min_order_size is not None,
    )

    acc.number("coverage_ratio", coverage.coverage_ratio)
    acc.number("missing_intervals", coverage.missing_intervals)
    acc.number("late_arrival_count", len(coverage.late_arrivals))
    acc.number("contiguous_intervals", count)
    staleness: float | None = None
    if tail:
        end = tail[-1].interval_end or tail[-1].source_event_time
        staleness = (evaluated_at_utc - end).total_seconds()
    acc.number("staleness_seconds", staleness)

    for name in ("book_depth_imbalance", "trade_tape_intensity"):
        acc.not_retained(name)

    produced = set(acc.values)
    expected = set(FEATURE_NAMES)
    if produced != expected:
        raise AssertionError(
            "feature set drifted from FEATURE_NAMES: "
            f"missing={sorted(expected - produced)} extra={sorted(produced - expected)}"
        )

    missingness = dict(acc.missingness)
    for name in NOT_RETAINED_INPUTS:
        missingness[name] = Missingness.NOT_RETAINED

    return FeatureComputation(
        values=dict(acc.values),
        missingness=missingness,
        contiguous_intervals=count,
        warm=count >= MINIMUM_WARMUP_INTERVALS,
    )


def snapshot_availability(
    observations: Sequence[Observation],
    *,
    source_version: str,
) -> AvailabilityStamp | None:
    """Visibility of the snapshot is the latest receipt among its inputs."""
    if not observations:
        return None
    latest_receipt = max(item.receipt_time for item in observations)
    latest_source = max(item.source_event_time for item in observations)
    return AvailabilityStamp(
        source_at_utc=latest_source,
        ingested_at_utc=latest_receipt,
        visible_at_utc=latest_receipt,
        source_version=source_version,
    )


def build_feature_snapshot(
    alignment: AlignmentResult,
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    consumed_input_watermark: ConsumedInputWatermark,
    restart_state: RestartState = RestartState.WARM,
    source_version: str,
    notes: str | None = N10_NOTE,
    evaluation_grid_seconds: int = DEFAULT_EVALUATION_GRID_SECONDS,
    freshness_alignment: AlignmentResult | None = None,
) -> FeatureSnapshot:
    """Compute and seal one snapshot. Every evaluation produces one.

    ``alignment`` is the rolling series (often rebuilt from retained state).
    ``freshness_alignment`` carries this cycle's coverage / receipt evidence
    when that differs from the rolling series.
    """
    coverage_alignment = freshness_alignment or alignment
    computation = compute_features(
        alignment,
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
        freshness_alignment=coverage_alignment,
    )
    # Prefer real receipts from this cycle's present bars when available;
    # fall back to the rolling series (synthetic receipts after resume).
    availability_inputs = contiguous_tail(coverage_alignment)[
        -FEATURE_WINDOW_INTERVALS:
    ] or contiguous_tail(alignment)[-FEATURE_WINDOW_INTERVALS:]
    availability = snapshot_availability(
        availability_inputs,
        source_version=source_version,
    )
    if availability is None:
        # No inputs at all: visibility is the evaluation itself.
        availability = AvailabilityStamp(
            source_at_utc=None,
            ingested_at_utc=evaluated_at_utc,
            visible_at_utc=evaluated_at_utc,
            source_version=source_version,
        )
    effective_restart = restart_state
    if restart_state is RestartState.WARM and not computation.warm:
        effective_restart = RestartState.INSUFFICIENT_HISTORY
    return FeatureSnapshot(
        instrument_version_id=instrument_version.instrument_version_id,
        venue_instrument_id=instrument_version.venue_instrument_id,
        feature_version=FEATURE_VERSION,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        values=computation.values,
        availability=availability,
        missingness=computation.missingness,
        coverage=coverage_alignment.coverage,
        restart_state=effective_restart,
        evaluation_grid_seconds=evaluation_grid_seconds,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_calc_version=FEATURE_CALC_VERSION,
        feature_dag_hash=feature_dag_hash(),
        notes=notes,
    )


__all__ = [
    "ATR_PERIOD",
    "BANDWIDTH_PERIOD",
    "EMA_FAST_PERIOD",
    "EMA_SLOW_PERIOD",
    "FEATURE_CALC_VERSION",
    "FEATURE_NAMES",
    "FEATURE_WINDOW_INTERVALS",
    "FRESHNESS_FEATURE_NAMES",
    "ROLLING_FEATURE_NAMES",
    "FEATURE_SCHEMA_VERSION",
    "FEATURE_VERSION",
    "MINIMUM_WARMUP_INTERVALS",
    "N10_NOTE",
    "NOT_RETAINED_INPUTS",
    "VOLUME_PERIOD",
    "CoverageState",
    "FeatureComputation",
    "build_feature_snapshot",
    "compute_features",
    "feature_dag_hash",
    "snapshot_availability",
]
