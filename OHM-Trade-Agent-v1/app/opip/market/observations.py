"""Pure normalization of venue rows into ``Observation`` records (slice B).

Normalization preserves; it does not decide. Everything the venue gave us is
carried forward — including intervals that were still forming and rows that
failed validation — and the aggregation layer then decides what is usable.

Two honesty rules are enforced here:

* Malformed rows are rejected with a reason, never silently repaired. A bar
  whose high is below its close is not evidence, and clamping it would
  manufacture a feature value that never happened.
* Kraken's OHLC endpoint exposes no venue sequence number. The source sequence
  is therefore *derived* from the interval start, and provenance says so, so a
  later reader cannot mistake it for venue-provided ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Iterable, Mapping, Sequence

from app.opip.contracts.enums import CoverageState, PayloadKind
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.observation import Observation
from app.opip.contracts.serialization import stable_hash

DERIVED_SEQUENCE_ORIGIN = "derived_from_interval_start"
VENUE_SEQUENCE_ORIGIN = "venue_provided"


@dataclass(frozen=True)
class IntervalRow:
    """One fixed-interval bar as delivered by a venue, before validation."""

    interval_start_epoch: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    trade_count: int | None = None
    source_sequence: str | None = None


@dataclass(frozen=True)
class RejectedRow:
    interval_start_epoch: int
    reason: str


@dataclass(frozen=True)
class NormalizationResult:
    observations: tuple[Observation, ...]
    rejected: tuple[RejectedRow, ...]
    next_ingestion_order: int

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def aggregate_content_fingerprint(values: Mapping[str, object]) -> str:
    """Deterministic fingerprint of all persisted aggregate fields."""
    payload = {
        key: values[key]
        for key in ("open", "high", "low", "close", "volume", "vwap", "trade_count")
        if key in values and values[key] is not None
    }
    return stable_hash("AGGFP", payload, length=8)


def _validate_row(row: IntervalRow, *, interval_seconds: int) -> str | None:
    if interval_seconds <= 0:
        return "non_positive_interval"
    if row.interval_start_epoch % interval_seconds != 0:
        return "interval_start_not_grid_aligned"
    prices = (row.open, row.high, row.low, row.close, row.volume)
    try:
        floats = tuple(float(value) for value in prices)
    except (TypeError, ValueError):
        return "non_numeric_price"
    # Finite checks before relational OHLC comparisons (NaN otherwise fails open).
    if any(not isfinite(value) for value in floats):
        return "non_finite_price"
    if any(value <= 0 for value in floats[:4]):
        return "non_positive_price"
    if floats[4] < 0:
        return "negative_volume"
    if row.vwap is not None:
        try:
            vwap = float(row.vwap)
        except (TypeError, ValueError):
            return "non_numeric_vwap"
        if not isfinite(vwap):
            return "non_finite_vwap"
    if row.trade_count is not None:
        if isinstance(row.trade_count, bool):
            return "invalid_trade_count"
        try:
            count = int(row.trade_count)
        except (TypeError, ValueError):
            return "invalid_trade_count"
        if count != float(row.trade_count):
            return "invalid_trade_count"
        if count < 0:
            return "negative_trade_count"
    if floats[1] < floats[2]:
        return "high_below_low"
    if floats[1] < max(floats[0], floats[3]):
        return "high_below_body"
    if floats[2] > min(floats[0], floats[3]):
        return "low_above_body"
    return None


def normalize_interval_rows(
    rows: Iterable[IntervalRow],
    *,
    instrument_version: InstrumentVersion,
    interval_seconds: int,
    receipt_time: datetime,
    now: datetime,
    source_label: str,
    source_sequence_prefix: str,
    ingestion_order_start: int = 1,
) -> NormalizationResult:
    """Convert venue rows into observations, in deterministic interval order."""
    if ingestion_order_start < 1:
        raise ValueError("ingestion_order_start must be >= 1")
    ordered = sorted(rows, key=lambda row: int(row.interval_start_epoch))
    observations: list[Observation] = []
    rejected: list[RejectedRow] = []
    ingestion_order = int(ingestion_order_start)
    seen: set[int] = set()

    for row in ordered:
        epoch = int(row.interval_start_epoch)
        if epoch in seen:
            rejected.append(RejectedRow(epoch, "duplicate_interval"))
            continue
        reason = _validate_row(row, interval_seconds=interval_seconds)
        if reason is not None:
            rejected.append(RejectedRow(epoch, reason))
            continue
        seen.add(epoch)

        interval_start = datetime.fromtimestamp(epoch, tz=timezone.utc)
        interval_end = interval_start + timedelta(seconds=interval_seconds)
        forming = interval_end > now

        values: dict[str, float | int] = {
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        if row.vwap is not None:
            values["vwap"] = float(row.vwap)
        if row.trade_count is not None:
            values["trade_count"] = int(row.trade_count)

        if row.source_sequence:
            source_sequence = str(row.source_sequence)
            sequence_origin = VENUE_SEQUENCE_ORIGIN
        else:
            source_sequence = f"{source_sequence_prefix}-{epoch}"
            sequence_origin = DERIVED_SEQUENCE_ORIGIN

        observations.append(
            Observation(
                instrument_version_id=instrument_version.instrument_version_id,
                venue=instrument_version.venue,
                venue_instrument_id=instrument_version.venue_instrument_id,
                source_event_time=interval_start,
                receipt_time=receipt_time,
                ingestion_order=ingestion_order,
                payload_kind=PayloadKind.FIXED_INTERVAL_AGGREGATE,
                aggregate_interval_seconds=int(interval_seconds),
                source_sequence=source_sequence,
                values=values,
                coverage=(
                    CoverageState.INCOMPLETE_COVERAGE
                    if forming
                    else CoverageState.COMPLETE
                ),
                interval_forming=forming,
                provenance={
                    "source": source_label,
                    "interval_seconds": str(int(interval_seconds)),
                    "source_sequence_origin": sequence_origin,
                    "reference_data_version": instrument_version.reference_data_version,
                },
            )
        )
        ingestion_order += 1

    return NormalizationResult(
        observations=tuple(observations),
        rejected=tuple(rejected),
        next_ingestion_order=ingestion_order,
    )


def completed_observations(
    observations: Sequence[Observation],
) -> tuple[Observation, ...]:
    """Drop still-forming intervals. Forming bars are never feature inputs."""
    return tuple(item for item in observations if not item.interval_forming)


__all__ = [
    "DERIVED_SEQUENCE_ORIGIN",
    "VENUE_SEQUENCE_ORIGIN",
    "IntervalRow",
    "NormalizationResult",
    "RejectedRow",
    "aggregate_content_fingerprint",
    "completed_observations",
    "normalize_interval_rows",
]
