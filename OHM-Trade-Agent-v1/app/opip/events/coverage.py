"""F1 observation coverage and availability evidence for the R5-A slice.

Adopted authority: `docs/architecture/v1.5.0/ARCHITECTURE.md` section 3
(`ME-01`, `ME-06`, `ME-07`), section 10 (`DC-01`) and the R5-0 structural
contract `SC-01`. The clauses require that *what was observed, when it arrived
and what could not be observed are all persisted*, and that **gaps reconcile
against the same source watermark rather than a wall clock**.

Three honesty rules are enforced here:

* A forming interval, an unaligned interval, an invalid payload, a late arrival,
  a clock-skewed arrival or an unsupported instrument is evidence about what
  could *not* be observed. It is recorded as a gap and it blocks a favourable
  downstream decision; it is never silently repaired, clamped or ignored.
* The expected interval set is derived from the consumed source watermark, not
  from the current time, so a call cannot invent expectations or leak future
  information into a point-in-time computation.
* Duplicate and out-of-order arrivals are recorded as non-blocking dispositions,
  because the interval evidence itself is present and valid.

Nothing here reads a clock, the environment, the filesystem, the network or a
database, and nothing here carries trading authority: coverage evidence can block
an evaluation and can never authorize one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.enums import CoverageState
from app.opip.contracts.observation import SourceWatermark
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import AvailabilityStamp, require_utc

#: Schema version of the coverage evidence record.
COVERAGE_SCHEMA_VERSION = "observation-coverage-v1"

COVERAGE_ID_PREFIX = "OCOV"
GAP_ID_PREFIX = "OGAP"


class CoverageContractError(ValueError):
    """A coverage contract violation. Always fails closed."""


class GapReason(str, Enum):
    """Why part of the expected observation window is not usable evidence."""

    MISSING_INTERVAL = "MISSING_INTERVAL"
    FORMING_INTERVAL = "FORMING_INTERVAL"
    INVALID_ROW = "INVALID_ROW"
    DUPLICATE_INTERVAL = "DUPLICATE_INTERVAL"
    LATE_ARRIVAL = "LATE_ARRIVAL"
    OUT_OF_ORDER_ARRIVAL = "OUT_OF_ORDER_ARRIVAL"
    CLOCK_SKEW = "CLOCK_SKEW"
    UNALIGNED_INTERVAL = "UNALIGNED_INTERVAL"
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    WINDOW_NOT_CONSUMED = "WINDOW_NOT_CONSUMED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    METADATA_CHANGE = "METADATA_CHANGE"


#: Reasons that describe evidence which is present and usable. Every other
#: reason blocks a favourable downstream decision.
NON_BLOCKING_GAP_REASONS: frozenset[GapReason] = frozenset(
    {GapReason.DUPLICATE_INTERVAL, GapReason.OUT_OF_ORDER_ARRIVAL}
)


class ArrivalDisposition(str, Enum):
    """How one expected interval arrived, relative to the watermark."""

    ON_TIME = "ON_TIME"
    LATE = "LATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DUPLICATE = "DUPLICATE"
    UNUSABLE = "UNUSABLE"


@dataclass(frozen=True)
class IntervalEvidence:
    """One delivered interval as the ingestion path observed it.

    `payload_valid` is the normalization verdict (`app.opip.market.observations`
    owns that decision); this record does not re-validate prices.
    """

    interval_start_epoch: int
    receipt_time: datetime
    source_sequence: str | None = None
    payload_valid: bool = True
    invalid_reason: str | None = None
    interval_forming: bool = False

    def __post_init__(self) -> None:
        epoch = self.interval_start_epoch
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise CoverageContractError("interval_start_epoch must be an integer")
        if epoch < 0:
            raise CoverageContractError("interval_start_epoch must be non-negative")
        object.__setattr__(self, "interval_start_epoch", int(epoch))
        object.__setattr__(
            self,
            "receipt_time",
            require_utc(self.receipt_time, field_name="receipt_time"),
        )
        if self.source_sequence is not None:
            sequence = str(self.source_sequence).strip()
            if not sequence:
                raise CoverageContractError("source_sequence must be non-empty or None")
            object.__setattr__(self, "source_sequence", sequence)
        for name in ("payload_valid", "interval_forming"):
            if not isinstance(getattr(self, name), bool):
                raise CoverageContractError(f"{name} must be a bool")
        if self.invalid_reason is not None and not str(self.invalid_reason).strip():
            raise CoverageContractError("invalid_reason must be non-empty or None")


@dataclass(frozen=True)
class CoverageGap:
    """One recorded reason a span of the expected window is not usable."""

    gap_id: str
    instrument_version_id: str
    reason: GapReason
    from_utc: datetime
    to_utc: datetime
    detail: str = "unrecorded"
    blocking: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.reason, GapReason):
            raise CoverageContractError("reason must be a GapReason")
        start = require_utc(self.from_utc, field_name="from_utc")
        end = require_utc(self.to_utc, field_name="to_utc")
        if end < start:
            raise CoverageContractError("a gap cannot end before it starts")
        object.__setattr__(self, "from_utc", start)
        object.__setattr__(self, "to_utc", end)
        if not isinstance(self.blocking, bool):
            raise CoverageContractError("blocking must be a bool")
        expected = gap_identity(
            instrument_version_id=self.instrument_version_id,
            reason=self.reason,
            start=start,
            end=end,
            detail=self.detail,
        )
        if self.gap_id != expected:
            raise CoverageContractError(
                "gap_id does not match its own span; build gaps with "
                "reconcile_coverage()"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "instrument_version_id": self.instrument_version_id,
            "reason": self.reason.value,
            "from_utc": iso_z(self.from_utc, field_name="from_utc"),
            "to_utc": iso_z(self.to_utc, field_name="to_utc"),
            "detail": self.detail,
            "blocking": self.blocking,
        }


def gap_identity(
    *,
    instrument_version_id: str,
    reason: GapReason,
    start: datetime,
    end: datetime,
    detail: str,
) -> str:
    """Deterministic ``OGAP:`` identity of one coverage gap."""

    return stable_hash(
        GAP_ID_PREFIX,
        {
            "instrument_version_id": str(instrument_version_id),
            "reason": reason.value,
            "from_utc": iso_z(start, field_name="from_utc"),
            "to_utc": iso_z(end, field_name="to_utc"),
            "detail": str(detail),
        },
    )


@dataclass(frozen=True)
class ObservationCoverage:
    """Retained availability evidence for one instrument window.

    The record answers the adopted question directly: what was observed, when it
    arrived, and what could not be observed. `coverage_state` is `COMPLETE` only
    when every expected interval is present, usable and non-blocking.
    """

    coverage_id: str
    schema_version: str
    instrument_version_id: str
    venue_instrument_id: str
    reference_data_version: str
    source_label: str
    interval_seconds: int
    window_start_utc: datetime
    watermark_through_utc: datetime | None
    evaluated_at_utc: datetime
    expected_intervals: int
    observed_intervals: int
    usable_intervals: int
    missing_intervals: int
    coverage_state: CoverageState
    gaps: tuple[CoverageGap, ...] = ()
    dispositions: Mapping[int, ArrivalDisposition] = MappingProxyType({})
    latest_source_event_time: datetime | None = None
    latest_receipt_time: datetime | None = None
    availability: AvailabilityStamp | None = None

    def __post_init__(self) -> None:
        if self.schema_version != COVERAGE_SCHEMA_VERSION:
            raise CoverageContractError(
                f"schema_version is not {COVERAGE_SCHEMA_VERSION}"
            )
        for name in (
            "instrument_version_id",
            "venue_instrument_id",
            "reference_data_version",
            "source_label",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CoverageContractError(f"{name} must be a non-empty string")
        seconds = self.interval_seconds
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            raise CoverageContractError("interval_seconds must be a positive integer")
        object.__setattr__(
            self,
            "window_start_utc",
            require_utc(self.window_start_utc, field_name="window_start_utc"),
        )
        object.__setattr__(
            self,
            "evaluated_at_utc",
            require_utc(self.evaluated_at_utc, field_name="evaluated_at_utc"),
        )
        if self.watermark_through_utc is not None:
            object.__setattr__(
                self,
                "watermark_through_utc",
                require_utc(
                    self.watermark_through_utc, field_name="watermark_through_utc"
                ),
            )
        for name in (
            "expected_intervals",
            "observed_intervals",
            "usable_intervals",
            "missing_intervals",
        ):
            number = getattr(self, name)
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise CoverageContractError(f"{name} must be a non-negative integer")
            object.__setattr__(self, name, int(number))
        if self.expected_intervals != (
            self.usable_intervals + self.missing_intervals
        ):
            raise CoverageContractError(
                "expected intervals must reconcile as usable plus missing"
            )
        object.__setattr__(self, "gaps", tuple(self.gaps))
        if not isinstance(self.dispositions, Mapping):
            raise CoverageContractError("dispositions must be a mapping")
        object.__setattr__(
            self,
            "dispositions",
            MappingProxyType(
                {
                    int(epoch): (
                        value
                        if isinstance(value, ArrivalDisposition)
                        else ArrivalDisposition(value)
                    )
                    for epoch, value in self.dispositions.items()
                }
            ),
        )
        coverage = self.coverage_state
        if not isinstance(coverage, CoverageState):
            coverage = CoverageState(str(coverage))
        blocking = tuple(gap for gap in self.gaps if gap.blocking)
        if coverage is CoverageState.COMPLETE and (
            blocking or self.missing_intervals or self.usable_intervals <= 0
        ):
            raise CoverageContractError(
                "COMPLETE coverage requires usable evidence and no blocking gap"
            )
        if coverage is CoverageState.INCOMPLETE_COVERAGE and not (
            blocking or self.missing_intervals or self.usable_intervals == 0
        ):
            raise CoverageContractError(
                "INCOMPLETE coverage requires a recorded blocking gap or a shortfall"
            )
        object.__setattr__(self, "coverage_state", coverage)
        if self.availability is not None and not isinstance(
            self.availability, AvailabilityStamp
        ):
            raise CoverageContractError("availability must be an AvailabilityStamp")


    @property
    def blocking_gaps(self) -> tuple[CoverageGap, ...]:
        return tuple(gap for gap in self.gaps if gap.blocking)

    @property
    def gap_reasons(self) -> tuple[str, ...]:
        return tuple(sorted({gap.reason.value for gap in self.gaps}))

    def usable_at(self, *, cutoff: datetime) -> bool:
        """Whether this evidence may back an evaluation at `cutoff`.

        Visibility is receipt time, never source time, and an empty or
        incomplete window is never usable.
        """

        instant = require_utc(cutoff, field_name="cutoff")
        if self.coverage_state is not CoverageState.COMPLETE:
            return False
        if self.blocking_gaps or self.missing_intervals:
            return False
        if self.availability is None or self.availability.visible_at_utc is None:
            return False
        return self.availability.visible_at_utc <= instant

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "ObservationCoverage",
            "schema_version": self.schema_version,
            "coverage_id": self.coverage_id,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "reference_data_version": self.reference_data_version,
            "source_label": self.source_label,
            "interval_seconds": self.interval_seconds,
            "window_start_utc": iso_z(
                self.window_start_utc, field_name="window_start_utc"
            ),
            "watermark_through_utc": (
                iso_z(self.watermark_through_utc, field_name="watermark_through_utc")
                if self.watermark_through_utc is not None
                else None
            ),
            "evaluated_at_utc": iso_z(
                self.evaluated_at_utc, field_name="evaluated_at_utc"
            ),
            "expected_intervals": self.expected_intervals,
            "observed_intervals": self.observed_intervals,
            "usable_intervals": self.usable_intervals,
            "missing_intervals": self.missing_intervals,
            "coverage_state": self.coverage_state.value,
            "gaps": [gap.to_dict() for gap in self.gaps],
            "dispositions": {
                str(epoch): value.value
                for epoch, value in sorted(self.dispositions.items())
            },
        }


def _gap(
    *,
    instrument_version_id: str,
    reason: GapReason,
    start: datetime,
    end: datetime,
    detail: str,
) -> CoverageGap:
    return CoverageGap(
        gap_id=gap_identity(
            instrument_version_id=instrument_version_id,
            reason=reason,
            start=start,
            end=end,
            detail=detail,
        ),
        instrument_version_id=instrument_version_id,
        reason=reason,
        from_utc=start,
        to_utc=end,
        detail=detail,
        blocking=reason not in NON_BLOCKING_GAP_REASONS,
    )


def _expected_epochs(
    *, window_start_epoch: int, interval_seconds: int, through_utc: datetime | None
) -> tuple[int, ...]:
    """Grid epochs already consumed by the source watermark.

    Expectations come from the watermark, never from a wall clock, so a call
    cannot invent expectations for time the source has not delivered.
    """

    if through_utc is None:
        return ()
    consumed = int(through_utc.timestamp())
    epochs: list[int] = []
    epoch = int(window_start_epoch)
    while epoch + interval_seconds <= consumed:
        epochs.append(epoch)
        epoch += interval_seconds
    return tuple(epochs)



def reconcile_coverage(
    *,
    instrument_version_id: str,
    venue_instrument_id: str,
    source_label: str,
    reference_data_version: str,
    window_start_utc: datetime,
    interval_seconds: int,
    evidence: Sequence[IntervalEvidence],
    watermark: SourceWatermark,
    evaluated_at_utc: datetime,
    source_available: bool = True,
    instrument_supported: bool = True,
    metadata_changed: bool = False,
    availability: AvailabilityStamp | None = None,
) -> ObservationCoverage:
    """Reconcile delivered interval evidence against the consumed watermark.

    Deterministic: the same evidence order and the same watermark produce the
    same coverage record, and the first arrival of a duplicated interval is the
    canonical one.
    """

    if not isinstance(watermark, SourceWatermark):
        raise CoverageContractError("watermark must be a SourceWatermark")
    if watermark.instrument_version_id != instrument_version_id:
        raise CoverageContractError(
            "watermark belongs to a different instrument version"
        )
    for name, value in (
        ("instrument_version_id", instrument_version_id),
        ("venue_instrument_id", venue_instrument_id),
        ("source_label", source_label),
        ("reference_data_version", reference_data_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise CoverageContractError(f"{name} must be a non-empty string")
    for name, value in (
        ("source_available", source_available),
        ("instrument_supported", instrument_supported),
        ("metadata_changed", metadata_changed),
    ):
        if not isinstance(value, bool):
            raise CoverageContractError(f"{name} must be a bool")
    start = require_utc(window_start_utc, field_name="window_start_utc")
    instant = require_utc(evaluated_at_utc, field_name="evaluated_at_utc")
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int):
        raise CoverageContractError("interval_seconds must be an integer")
    if interval_seconds <= 0:
        raise CoverageContractError("interval_seconds must be positive")
    start_epoch = int(start.timestamp())
    if start_epoch % interval_seconds != 0:
        raise CoverageContractError("window_start_utc must sit on the interval grid")

    ordered = sorted(
        evidence, key=lambda item: (item.interval_start_epoch, item.receipt_time)
    )
    expected = _expected_epochs(
        window_start_epoch=start_epoch,
        interval_seconds=interval_seconds,
        through_utc=watermark.through_utc,
    )
    expected_set = set(expected)
    window_end_epoch = (
        expected[-1] + interval_seconds if expected else start_epoch + interval_seconds
    )

    def at(epoch: int) -> datetime:
        return datetime.fromtimestamp(epoch, tz=start.tzinfo)

    gaps: list[CoverageGap] = []
    dispositions: dict[int, ArrivalDisposition] = {}
    usable: set[int] = set()
    observed = 0

    whole_window_reason: GapReason | None = None
    whole_window_detail = ""
    if not instrument_supported:
        whole_window_reason = GapReason.UNSUPPORTED_INSTRUMENT
        whole_window_detail = "the instrument is not supported by this source"
    elif metadata_changed:
        whole_window_reason = GapReason.METADATA_CHANGE
        whole_window_detail = "instrument metadata changed inside the window"
    elif not source_available:
        whole_window_reason = GapReason.SOURCE_UNAVAILABLE
        whole_window_detail = "the source was unavailable for the window"
    elif not expected:
        whole_window_reason = GapReason.WINDOW_NOT_CONSUMED
        whole_window_detail = "the source watermark has not consumed this window"

    if whole_window_reason is not None:
        gaps.append(
            _gap(
                instrument_version_id=instrument_version_id,
                reason=whole_window_reason,
                start=at(start_epoch),
                end=at(window_end_epoch),
                detail=whole_window_detail,
            )
        )
    else:
        first_by_epoch: dict[int, IntervalEvidence] = {}
        for item in ordered:
            epoch = item.interval_start_epoch
            observed += 1
            if epoch in first_by_epoch:
                dispositions[epoch] = ArrivalDisposition.DUPLICATE
                gaps.append(
                    _gap(
                        instrument_version_id=instrument_version_id,
                        reason=GapReason.DUPLICATE_INTERVAL,
                        start=at(epoch),
                        end=at(epoch + interval_seconds),
                        detail="a duplicate delivery converged on one interval",
                    )
                )
                continue
            first_by_epoch[epoch] = item
            if epoch not in expected_set:
                aligned = (epoch - start_epoch) % interval_seconds == 0
                dispositions[epoch] = ArrivalDisposition.UNUSABLE
                gaps.append(
                    _gap(
                        instrument_version_id=instrument_version_id,
                        reason=(
                            GapReason.OUTSIDE_WINDOW
                            if aligned
                            else GapReason.UNALIGNED_INTERVAL
                        ),
                        start=at(epoch),
                        end=at(epoch + interval_seconds),
                        detail=(
                            "evidence outside the consumed window is not usable here"
                        ),
                    )
                )
                continue

            if item.interval_forming:
                dispositions[epoch] = ArrivalDisposition.UNUSABLE
                gaps.append(
                    _gap(
                        instrument_version_id=instrument_version_id,
                        reason=GapReason.FORMING_INTERVAL,
                        start=at(epoch),
                        end=at(epoch + interval_seconds),
                        detail="a forming interval is never feature input",
                    )
                )
                continue
            if not item.payload_valid:
                dispositions[epoch] = ArrivalDisposition.UNUSABLE
                gaps.append(
                    _gap(
                        instrument_version_id=instrument_version_id,
                        reason=GapReason.INVALID_ROW,
                        start=at(epoch),
                        end=at(epoch + interval_seconds),
                        detail=str(item.invalid_reason or "row failed validation"),
                    )
                )
                continue
            if item.receipt_time < at(epoch):
                dispositions[epoch] = ArrivalDisposition.UNUSABLE
                gaps.append(
                    _gap(
                        instrument_version_id=instrument_version_id,
                        reason=GapReason.CLOCK_SKEW,
                        start=at(epoch),
                        end=at(epoch + interval_seconds),
                        detail="receipt precedes the interval it carries",
                    )
                )
                continue
            if item.receipt_time > instant:
                dispositions[epoch] = ArrivalDisposition.LATE
                gaps.append(
                    _gap(
                        instrument_version_id=instrument_version_id,
                        reason=GapReason.LATE_ARRIVAL,
                        start=at(epoch),
                        end=at(epoch + interval_seconds),
                        detail="arrival follows the evaluation instant",
                    )
                )
                continue
            dispositions[epoch] = ArrivalDisposition.ON_TIME
            usable.add(epoch)

        sequenced = [
            item
            for item in first_by_epoch.values()
            if item.source_sequence is not None and item.interval_start_epoch in expected_set
        ]
        if sequenced:
            order = {item.interval_start_epoch: item.source_sequence for item in sequenced}
            previous: str | None = None
            for epoch in sorted(order):
                if previous is not None and order[epoch] < previous:
                    dispositions[epoch] = ArrivalDisposition.OUT_OF_ORDER
                    gaps.append(
                        _gap(
                            instrument_version_id=instrument_version_id,
                            reason=GapReason.OUT_OF_ORDER_ARRIVAL,
                            start=at(epoch),
                            end=at(epoch + interval_seconds),
                            detail="source sequence regressed against an earlier interval",
                        )
                    )
                previous = order[epoch]

        for epoch in expected:
            if epoch in usable:
                continue
            if epoch in first_by_epoch:
                continue
            gaps.append(
                _gap(
                    instrument_version_id=instrument_version_id,
                    reason=GapReason.MISSING_INTERVAL,
                    start=at(epoch),
                    end=at(epoch + interval_seconds),
                    detail="the source watermark covers this interval and it is absent",
                )
            )


    usable_intervals = len(usable)
    missing_intervals = len(expected) - usable_intervals
    blocking = any(gap.blocking for gap in gaps)
    coverage_state = (
        CoverageState.COMPLETE
        if expected and not blocking and missing_intervals == 0
        else CoverageState.INCOMPLETE_COVERAGE
    )
    latest_source_epoch = max(
        (item.interval_start_epoch for item in ordered), default=None
    )
    latest_source = (
        at(latest_source_epoch) if latest_source_epoch is not None else None
    )
    latest_receipt = max((item.receipt_time for item in ordered), default=None)
    if availability is None and latest_receipt is not None:
        availability = AvailabilityStamp(
            source_at_utc=latest_source,
            ingested_at_utc=latest_receipt,
            visible_at_utc=latest_receipt,
            source_version=reference_data_version,
        )
    coverage_id = stable_hash(
        COVERAGE_ID_PREFIX,
        {
            "schema_version": COVERAGE_SCHEMA_VERSION,
            "instrument_version_id": instrument_version_id,
            "source_label": source_label,
            "interval_seconds": int(interval_seconds),
            "window_start_utc": iso_z(start, field_name="window_start_utc"),
            "watermark_through_utc": (
                iso_z(watermark.through_utc, field_name="watermark_through_utc")
                if watermark.through_utc is not None
                else None
            ),
            "evaluated_at_utc": iso_z(instant, field_name="evaluated_at_utc"),
            "gaps": sorted(gap.gap_id for gap in gaps),
        },
    )
    return ObservationCoverage(
        coverage_id=coverage_id,
        schema_version=COVERAGE_SCHEMA_VERSION,
        instrument_version_id=instrument_version_id,
        venue_instrument_id=venue_instrument_id,
        reference_data_version=reference_data_version,
        source_label=source_label,
        interval_seconds=int(interval_seconds),
        window_start_utc=start,
        watermark_through_utc=watermark.through_utc,
        evaluated_at_utc=instant,
        expected_intervals=len(expected),
        observed_intervals=observed,
        usable_intervals=usable_intervals,
        missing_intervals=missing_intervals,
        coverage_state=coverage_state,
        gaps=tuple(gaps),
        dispositions=dispositions,
        latest_source_event_time=latest_source,
        latest_receipt_time=latest_receipt,
        availability=availability,
    )


def require_usable_coverage(
    coverage: ObservationCoverage, *, cutoff: datetime
) -> ObservationCoverage:
    """Fail closed unless the coverage evidence may back an evaluation."""

    if not isinstance(coverage, ObservationCoverage):
        raise CoverageContractError("coverage must be an ObservationCoverage")
    if not coverage.usable_at(cutoff=cutoff):
        reasons = coverage.gap_reasons or ("NO_USABLE_WINDOW",)
        raise CoverageContractError(
            "coverage does not authorize an evaluation at the cutoff: "
            + ",".join(reasons)
        )
    return coverage


__all__ = [
    "ArrivalDisposition",
    "COVERAGE_ID_PREFIX",
    "COVERAGE_SCHEMA_VERSION",
    "CoverageContractError",
    "CoverageGap",
    "GAP_ID_PREFIX",
    "GapReason",
    "IntervalEvidence",
    "NON_BLOCKING_GAP_REASONS",
    "ObservationCoverage",
    "gap_identity",
    "reconcile_coverage",
    "require_usable_coverage",
]

