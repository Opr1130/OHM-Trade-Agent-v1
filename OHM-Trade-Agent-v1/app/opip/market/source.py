"""Source-independent market observation interface (PR3 slice B).

Ruling D4: PR3 defines the interface and supports a *pilot* implementation for
measurement. It does not authorize a scheduled production one-minute source.
Nothing in this module registers itself with the scheduler, and
``run_pilot_cycle`` is only reachable from the manual pilot job.

The interface is deliberately watermark-oriented — "give me observations
through this watermark" — so a REST poller, a websocket feed, or a replay
fixture are interchangeable to every downstream consumer.

No exchange transport is imported here. ``app/opip`` is barred from importing
``app.exchanges`` at all, which is why the venue wiring lives in
``app.services.opip_feature_bus_market_source`` and reaches this module through
an injected fetcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import time
from typing import Callable, Mapping, Protocol, Sequence

from app.opip.contracts.enums import CoverageState
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.observation import Observation, SourceWatermark
from app.opip.market.aggregates import grid_floor
from app.opip.market.observations import (
    IntervalRow,
    NormalizationResult,
    completed_observations,
    normalize_interval_rows,
)


@dataclass(frozen=True)
class SourceMetrics:
    """Measurement evidence for the D4 feasibility question."""

    requests: int = 0
    failures: int = 0
    rate_limited: int = 0
    observations: int = 0
    forming_excluded: int = 0
    rejected_rows: int = 0
    elapsed_seconds: float = 0.0

    def merged(self, other: "SourceMetrics") -> "SourceMetrics":
        return SourceMetrics(
            requests=self.requests + other.requests,
            failures=self.failures + other.failures,
            rate_limited=self.rate_limited + other.rate_limited,
            observations=self.observations + other.observations,
            forming_excluded=self.forming_excluded + other.forming_excluded,
            rejected_rows=self.rejected_rows + other.rejected_rows,
            elapsed_seconds=self.elapsed_seconds + other.elapsed_seconds,
        )

    def to_dict(self) -> dict[str, float | int]:
        return {
            "requests": self.requests,
            "failures": self.failures,
            "rate_limited": self.rate_limited,
            "observations": self.observations,
            "forming_excluded": self.forming_excluded,
            "rejected_rows": self.rejected_rows,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
        }


@dataclass(frozen=True)
class SourceBatch:
    """Completed observations for one instrument plus advanced watermark."""

    instrument_version: InstrumentVersion
    observations: tuple[Observation, ...]
    watermark: SourceWatermark
    coverage: CoverageState
    metrics: SourceMetrics
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class MarketObservationSource(Protocol):
    """Any source of market observations, addressable by watermark."""

    venue: str
    source_label: str
    interval_seconds: int

    def fetch_through(
        self,
        instrument_version: InstrumentVersion,
        *,
        watermark: SourceWatermark | None,
        now: datetime,
    ) -> SourceBatch: ...


class MinuteBarFetcher(Protocol):
    """Venue adapter: fixed-interval rows at or after ``since_epoch``.

    Implementations live outside ``app/opip`` and own all transport concerns.
    They return already-typed rows, so nothing venue-specific crosses into the
    feature bus.
    """

    def __call__(
        self,
        venue_instrument_id: str,
        *,
        interval_minutes: int,
        since_epoch: int | None,
    ) -> Sequence[IntervalRow]: ...


def _advance_watermark(
    previous: SourceWatermark,
    result: NormalizationResult,
    completed: Sequence[Observation],
) -> SourceWatermark:
    if not completed:
        return replace(previous, last_ingestion_order=result.next_ingestion_order - 1)
    last = completed[-1]
    through = last.interval_end or last.source_event_time
    if previous.through_utc is not None and through <= previous.through_utc:
        through = previous.through_utc
    return SourceWatermark(
        instrument_version_id=previous.instrument_version_id,
        through_utc=through,
        last_source_sequence=last.source_sequence,
        last_ingestion_order=result.next_ingestion_order - 1,
    )


class PolledMinuteBarSource:
    """Pilot polling source over an injected fixed-interval fetcher.

    Pilot status is structural, not a comment: exactly one fetch per call, and
    no retry, backoff, universe fan-out, or scheduling behaviour of its own.
    Those decisions wait for the measured evidence this source exists to
    produce.

    ``transport_errors`` is empty by default, so an unexpected exception
    propagates instead of being silently recorded as a coverage gap. A venue
    adapter names its own transport failure type explicitly.
    """

    def __init__(
        self,
        fetcher: MinuteBarFetcher,
        *,
        venue: str,
        source_label: str,
        sequence_prefix: str,
        interval_seconds: int = 60,
        transport_errors: tuple[type[BaseException], ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if interval_seconds <= 0 or interval_seconds % 60 != 0:
            raise ValueError("interval_seconds must be a positive whole minute")
        self._fetcher = fetcher
        self.venue = str(venue)
        self.source_label = str(source_label)
        self.interval_seconds = int(interval_seconds)
        self._sequence_prefix = str(sequence_prefix)
        self._transport_errors = tuple(transport_errors)
        self._interval_minutes = int(interval_seconds // 60)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def sequence_prefix(self) -> str:
        return f"{self._sequence_prefix}-{self._interval_minutes}m"

    def fetch_through(
        self,
        instrument_version: InstrumentVersion,
        *,
        watermark: SourceWatermark | None,
        now: datetime,
    ) -> SourceBatch:
        previous = watermark or SourceWatermark(
            instrument_version_id=instrument_version.instrument_version_id
        )
        if previous.instrument_version_id != instrument_version.instrument_version_id:
            raise ValueError("watermark belongs to a different instrument version")

        since: int | None = None
        if previous.through_utc is not None:
            # Venues commonly return intervals strictly after ``since``; step
            # back one second so the interval starting exactly at the watermark
            # is kept.
            since = int(previous.through_utc.timestamp()) - 1

        started = time.monotonic()
        try:
            rows = self._fetcher(
                instrument_version.venue_instrument_id,
                interval_minutes=self._interval_minutes,
                since_epoch=since,
            )
        except self._transport_errors as exc:  # type: ignore[misc]
            elapsed = time.monotonic() - started
            message = str(exc)
            return SourceBatch(
                instrument_version=instrument_version,
                observations=(),
                watermark=previous,
                coverage=CoverageState.INCOMPLETE_COVERAGE,
                metrics=SourceMetrics(
                    requests=1,
                    failures=1,
                    rate_limited=1 if "rate limit" in message.lower() else 0,
                    elapsed_seconds=elapsed,
                ),
                error=message,
            )
        # Receipt time is when the payload arrived, not when the request started.
        receipt_time = self._clock()
        elapsed = time.monotonic() - started

        result = normalize_interval_rows(
            rows,
            instrument_version=instrument_version,
            interval_seconds=self.interval_seconds,
            receipt_time=receipt_time,
            now=now,
            source_label=self.source_label,
            source_sequence_prefix=self.sequence_prefix,
            ingestion_order_start=previous.last_ingestion_order + 1,
        )
        closed = completed_observations(result.observations)
        completed = closed
        if previous.through_utc is not None:
            completed = tuple(
                item
                for item in closed
                if item.source_event_time >= previous.through_utc
            )
        coverage = (
            CoverageState.COMPLETE
            if completed and not result.rejected
            else CoverageState.INCOMPLETE_COVERAGE
        )
        return SourceBatch(
            instrument_version=instrument_version,
            observations=completed,
            watermark=_advance_watermark(previous, result, completed),
            coverage=coverage,
            metrics=SourceMetrics(
                requests=1,
                observations=len(completed),
                forming_excluded=len(result.observations) - len(closed),
                rejected_rows=result.rejected_count,
                elapsed_seconds=elapsed,
            ),
        )


@dataclass(frozen=True)
class PilotCycleReport:
    """The D4 measurement: what a one-minute source would actually cost.

    This report is the deliverable that a future activation decision needs. It
    is not itself an activation.
    """

    started_at_utc: datetime
    finished_at_utc: datetime
    eligible_instruments: int
    requested_instruments: int
    metrics: SourceMetrics
    coverage_complete: int = 0
    coverage_incomplete: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def wall_seconds(self) -> float:
        return (self.finished_at_utc - self.started_at_utc).total_seconds()

    @property
    def requests_per_minute(self) -> float:
        seconds = self.wall_seconds
        if seconds <= 0:
            return float(self.metrics.requests)
        return self.metrics.requests * 60.0 / seconds

    @property
    def coverage_pct(self) -> float:
        total = self.coverage_complete + self.coverage_incomplete
        if total == 0:
            return 0.0
        return self.coverage_complete * 100.0 / total

    @property
    def projected_requests_per_minute_full_universe(self) -> float:
        """What the full eligible universe would cost at one request each."""
        return float(self.eligible_instruments)

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at_utc": self.started_at_utc.isoformat(),
            "finished_at_utc": self.finished_at_utc.isoformat(),
            "eligible_instruments": self.eligible_instruments,
            "requested_instruments": self.requested_instruments,
            "wall_seconds": round(self.wall_seconds, 6),
            "requests_per_minute": round(self.requests_per_minute, 3),
            "projected_requests_per_minute_full_universe": (
                self.projected_requests_per_minute_full_universe
            ),
            "coverage_complete": self.coverage_complete,
            "coverage_incomplete": self.coverage_incomplete,
            "coverage_pct": round(self.coverage_pct, 3),
            "errors": list(self.errors),
            **{f"metric_{key}": value for key, value in self.metrics.to_dict().items()},
        }


def run_pilot_cycle(
    source: MarketObservationSource,
    instrument_versions: Sequence[InstrumentVersion],
    *,
    watermarks: Mapping[str, SourceWatermark] | None = None,
    now: datetime,
    eligible_instruments: int | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[list[SourceBatch], PilotCycleReport, dict[str, SourceWatermark]]:
    """Fetch one cycle for the supplied instruments and measure the cost.

    Manual measurement only. No caller in this package schedules it.
    """
    tick = clock or (lambda: datetime.now(timezone.utc))
    started = tick()
    current = dict(watermarks or {})
    batches: list[SourceBatch] = []
    metrics = SourceMetrics()
    complete = 0
    incomplete = 0
    errors: list[str] = []

    for instrument_version in instrument_versions:
        key = instrument_version.instrument_version_id
        batch = source.fetch_through(
            instrument_version, watermark=current.get(key), now=now
        )
        batches.append(batch)
        current[key] = batch.watermark
        metrics = metrics.merged(batch.metrics)
        if batch.coverage is CoverageState.COMPLETE:
            complete += 1
        else:
            incomplete += 1
        if batch.error:
            errors.append(f"{key}: {batch.error}")

    finished = tick()
    report = PilotCycleReport(
        started_at_utc=started,
        finished_at_utc=finished,
        eligible_instruments=(
            int(eligible_instruments)
            if eligible_instruments is not None
            else len(instrument_versions)
        ),
        requested_instruments=len(instrument_versions),
        metrics=metrics,
        coverage_complete=complete,
        coverage_incomplete=incomplete,
        errors=tuple(errors),
    )
    return batches, report, current


def latest_closed_cutoff(now: datetime, *, interval_seconds: int = 60) -> datetime:
    """Latest grid instant at or before ``now``, i.e. the usable cutoff."""
    return grid_floor(now, interval_seconds=interval_seconds)


__all__ = [
    "MarketObservationSource",
    "MinuteBarFetcher",
    "PilotCycleReport",
    "PolledMinuteBarSource",
    "SourceBatch",
    "SourceMetrics",
    "latest_closed_cutoff",
    "run_pilot_cycle",
]
