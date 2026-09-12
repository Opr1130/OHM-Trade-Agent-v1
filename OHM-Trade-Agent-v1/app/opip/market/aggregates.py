"""Pure one-minute aggregation and coverage semantics (PR3 slice C).

N10 fixes the evaluation grid at one minute. This module owns what that
actually means, and nothing here contains detector logic:

* Exact UTC grid alignment, so a snapshot's inputs are reproducible.
* Forming intervals are excluded. A bar that has not closed is not evidence.
* Missing minutes are detected and reported as contiguous gaps, not averaged
  away or forward-filled.
* Late evidence is visible as late, and a superseding revision replaces an
  earlier one without deleting it.
* Deterministic replay: identical inputs and cutoff always produce identical
  output, independent of dict ordering or arrival order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from app.opip.contracts.enums import CoverageState, PayloadKind
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.observation import Observation
from app.opip.market.observations import (
    IntervalRow,
    NormalizationResult,
    normalize_interval_rows,
)

DEFAULT_INTERVAL_SECONDS = 60

#: An interval arriving more than this long after it closed is flagged late.
#: It is still usable evidence; the flag exists so latency is measurable rather
#: than silently absorbed into feature values.
DEFAULT_LATE_THRESHOLD_SECONDS = 5.0


def grid_floor(
    moment: datetime, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS
) -> datetime:
    """Floor a timestamp onto the UTC interval grid."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    utc = moment.astimezone(timezone.utc).replace(microsecond=0)
    epoch = int(utc.timestamp())
    return datetime.fromtimestamp(
        epoch - (epoch % interval_seconds), tz=timezone.utc
    )


def is_grid_aligned(
    moment: datetime, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS
) -> bool:
    return grid_floor(moment, interval_seconds=interval_seconds) == moment.astimezone(
        timezone.utc
    )


@dataclass(frozen=True)
class CoverageGap:
    """A contiguous run of grid intervals O'Pip expected and does not have."""

    first_missing_utc: datetime
    missing_interval_count: int
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if self.missing_interval_count < 1:
            raise ValueError("missing_interval_count must be >= 1")

    @property
    def last_missing_utc(self) -> datetime:
        return self.first_missing_utc + timedelta(
            seconds=self.interval_seconds * (self.missing_interval_count - 1)
        )

    @property
    def resumes_at_utc(self) -> datetime:
        return self.first_missing_utc + timedelta(
            seconds=self.interval_seconds * self.missing_interval_count
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "first_missing_utc": self.first_missing_utc.isoformat(),
            "last_missing_utc": self.last_missing_utc.isoformat(),
            "missing_interval_count": self.missing_interval_count,
            "interval_seconds": self.interval_seconds,
        }


@dataclass(frozen=True)
class AlignmentResult:
    """Grid-aligned, deduplicated, closed intervals plus coverage evidence."""

    observations: tuple[Observation, ...]
    gaps: tuple[CoverageGap, ...] = ()
    late_arrivals: tuple[Observation, ...] = ()
    superseded: tuple[Observation, ...] = ()
    excluded_forming: int = 0
    excluded_unclosed: int = 0
    excluded_misaligned: int = 0
    expected_intervals: int = 0
    source_incomplete: bool = False

    @property
    def present_intervals(self) -> int:
        return len(self.observations)

    @property
    def missing_intervals(self) -> int:
        return sum(gap.missing_interval_count for gap in self.gaps)

    @property
    def coverage(self) -> CoverageState:
        if self.source_incomplete:
            return CoverageState.INCOMPLETE_COVERAGE
        # A declared expected window with nothing usable is an outage, never COMPLETE.
        if self.expected_intervals > 0 and self.present_intervals == 0:
            return CoverageState.INCOMPLETE_COVERAGE
        if self.gaps or self.excluded_misaligned:
            return CoverageState.INCOMPLETE_COVERAGE
        if any(
            item.coverage is CoverageState.INCOMPLETE_COVERAGE
            for item in self.observations
        ):
            return CoverageState.INCOMPLETE_COVERAGE
        return CoverageState.COMPLETE

    @property
    def coverage_ratio(self) -> float:
        if self.expected_intervals <= 0:
            return 0.0
        return self.present_intervals / self.expected_intervals


def _revision_rank(observation: Observation) -> tuple[int, int]:
    """Latest revision wins; ingestion order breaks ties deterministically."""
    return (observation.revision, observation.ingestion_order)


def align_minute_observations(
    observations: Iterable[Observation],
    *,
    cutoff: datetime,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    window_start: datetime | None = None,
    late_threshold_seconds: float = DEFAULT_LATE_THRESHOLD_SECONDS,
) -> AlignmentResult:
    """Align observations onto the grid and report what is missing.

    ``cutoff`` is the evaluation grid instant: only intervals that closed at or
    before it are eligible. ``window_start`` bounds gap detection; without it,
    detection starts at the earliest interval actually present, so a cold start
    is not reported as a giant historical gap.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    cutoff_utc = cutoff.astimezone(timezone.utc)
    if not is_grid_aligned(cutoff_utc, interval_seconds=interval_seconds):
        raise ValueError("cutoff must sit on the interval grid")

    excluded_forming = 0
    excluded_unclosed = 0
    excluded_misaligned = 0
    best: dict[datetime, Observation] = {}
    superseded: list[Observation] = []

    for observation in observations:
        if observation.payload_kind is not PayloadKind.FIXED_INTERVAL_AGGREGATE:
            excluded_misaligned += 1
            continue
        if observation.aggregate_interval_seconds != interval_seconds:
            excluded_misaligned += 1
            continue
        if observation.interval_forming:
            excluded_forming += 1
            continue
        start = observation.source_event_time
        if not is_grid_aligned(start, interval_seconds=interval_seconds):
            excluded_misaligned += 1
            continue
        end = observation.interval_end
        if end is None or end > cutoff_utc:
            excluded_unclosed += 1
            continue
        if window_start is not None and start < window_start.astimezone(timezone.utc):
            continue
        incumbent = best.get(start)
        if incumbent is None:
            best[start] = observation
            continue
        if _revision_rank(observation) > _revision_rank(incumbent):
            best[start] = observation
            superseded.append(incumbent)
        else:
            superseded.append(observation)

    ordered = [best[key] for key in sorted(best)]
    late = tuple(
        item
        for item in ordered
        if item.arrival_lag_seconds > float(late_threshold_seconds)
    )

    gaps: tuple[CoverageGap, ...] = ()
    expected = 0
    first: datetime | None = None
    if window_start is not None:
        window_utc = window_start.astimezone(timezone.utc)
        if not is_grid_aligned(window_utc, interval_seconds=interval_seconds):
            raise ValueError("window_start must sit on the interval grid")
        first = window_utc
    elif ordered:
        first = ordered[0].source_event_time
    if first is not None:
        last_expected = cutoff_utc - timedelta(seconds=interval_seconds)
        if last_expected >= first:
            expected = (
                int((last_expected - first).total_seconds()) // interval_seconds
            ) + 1
            present = {item.source_event_time for item in ordered}
            gaps = _contiguous_gaps(
                first=first,
                last=last_expected,
                present=present,
                interval_seconds=interval_seconds,
            )

    return AlignmentResult(
        observations=tuple(ordered),
        gaps=gaps,
        late_arrivals=late,
        superseded=tuple(superseded),
        excluded_forming=excluded_forming,
        excluded_unclosed=excluded_unclosed,
        excluded_misaligned=excluded_misaligned,
        expected_intervals=expected,
    )


def contiguous_tail(result: AlignmentResult) -> tuple[Observation, ...]:
    """Observations after the most recent *splitting* gap.

    Rolling features computed across a feed gap are contaminated: a 20-bar
    average that silently spans a 40-minute hole is not a 20-minute average.
    Callers therefore compute on the contiguous tail and let warm-up state
    record that history is short, rather than pretending the window is intact.

    Trailing holes after the latest present bar are incomplete coverage of the
    latest minutes, not a mid-window wipe. Including them in the resume cut
    would empty the tail whenever the fetch window ends short of cutoff.
    """
    if not result.gaps or not result.observations:
        return result.observations
    latest_present = max(item.source_event_time for item in result.observations)
    # A gap splits retained history only when evidence resumes at or before
    # the latest present bar. Gaps whose resume is after that bar are trailing.
    split_gaps = tuple(
        gap for gap in result.gaps if gap.resumes_at_utc <= latest_present
    )
    if not split_gaps:
        return result.observations
    resume = max(gap.resumes_at_utc for gap in split_gaps)
    return tuple(
        item for item in result.observations if item.source_event_time >= resume
    )


def _contiguous_gaps(
    *,
    first: datetime,
    last: datetime,
    present: set[datetime],
    interval_seconds: int,
) -> tuple[CoverageGap, ...]:
    gaps: list[CoverageGap] = []
    step = timedelta(seconds=interval_seconds)
    run_start: datetime | None = None
    run_length = 0
    moment = first
    while moment <= last:
        if moment in present:
            if run_start is not None:
                gaps.append(
                    CoverageGap(
                        first_missing_utc=run_start,
                        missing_interval_count=run_length,
                        interval_seconds=interval_seconds,
                    )
                )
                run_start = None
                run_length = 0
        else:
            if run_start is None:
                run_start = moment
            run_length += 1
        moment += step
    if run_start is not None:
        gaps.append(
            CoverageGap(
                first_missing_utc=run_start,
                missing_interval_count=run_length,
                interval_seconds=interval_seconds,
            )
        )
    return tuple(gaps)


def aggregate_trades_to_minutes(
    trades: Sequence[Observation],
    *,
    instrument_version: InstrumentVersion,
    receipt_time: datetime,
    now: datetime,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    source_label: str,
    source_sequence_prefix: str,
    ingestion_order_start: int = 1,
) -> NormalizationResult:
    """Fold trade observations into fixed-interval aggregates, deterministically.

    Trades are ephemeral by contract; the aggregate is what gets persisted.
    Ordering is by ``(source_event_time, ingestion_order)`` so the same trades
    always fold to the same bar regardless of arrival order.
    """
    buckets: dict[int, dict[str, float]] = {}
    ordered = sorted(
        (
            trade
            for trade in trades
            if trade.payload_kind is PayloadKind.TRADE
        ),
        key=lambda trade: (trade.source_event_time, trade.ingestion_order),
    )
    for trade in ordered:
        price = trade.values.get("price")
        quantity = trade.values.get("quantity")
        if price is None or quantity is None:
            raise ValueError("trade observations require price and quantity")
        price = float(price)
        quantity = float(quantity)
        bucket_start = int(
            grid_floor(
                trade.source_event_time, interval_seconds=interval_seconds
            ).timestamp()
        )
        bucket = buckets.get(bucket_start)
        if bucket is None:
            buckets[bucket_start] = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": quantity,
                "notional": price * quantity,
                "trade_count": 1.0,
            }
            continue
        bucket["high"] = max(bucket["high"], price)
        bucket["low"] = min(bucket["low"], price)
        bucket["close"] = price
        bucket["volume"] += quantity
        bucket["notional"] += price * quantity
        bucket["trade_count"] += 1.0

    rows = [
        IntervalRow(
            interval_start_epoch=start,
            open=values["open"],
            high=values["high"],
            low=values["low"],
            close=values["close"],
            volume=values["volume"],
            vwap=(
                values["notional"] / values["volume"] if values["volume"] > 0 else None
            ),
            trade_count=int(values["trade_count"]),
        )
        for start, values in sorted(buckets.items())
    ]
    return normalize_interval_rows(
        rows,
        instrument_version=instrument_version,
        interval_seconds=interval_seconds,
        receipt_time=receipt_time,
        now=now,
        source_label=source_label,
        source_sequence_prefix=source_sequence_prefix,
        ingestion_order_start=ingestion_order_start,
    )


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_LATE_THRESHOLD_SECONDS",
    "AlignmentResult",
    "CoverageGap",
    "aggregate_trades_to_minutes",
    "align_minute_observations",
    "contiguous_tail",
    "grid_floor",
    "is_grid_aligned",
]
