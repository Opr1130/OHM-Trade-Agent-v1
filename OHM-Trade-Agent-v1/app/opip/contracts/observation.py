"""Market observation contract (Contract B).

An ``Observation`` is one immutable market fact as O'Pip received it. It
separates four different times and orders that are routinely conflated:

* ``source_event_time`` — when the venue says the fact happened.
* ``receipt_time`` — when O'Pip received it.
* ``ingestion_order`` — O'Pip's local arrival order, monotonic per process.
* ``source_sequence`` — the venue's own sequence identity, when it exposes one.

Commit order ``(history_epoch, local_sequence)`` is assigned later by the
canonical writer and is therefore ``None`` until the record is committed.
Corrections never mutate: a superseding revision is appended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.contracts.enums import CoverageState, PayloadKind
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.contracts.serialization import iso_z
from app.opip.contracts.temporal import AvailabilityStamp, require_utc

OBSERVATION_RECORD_TYPE = "Observation"
OBSERVATION_SCHEMA_VERSION = 1

#: Value keys carried by a fixed-interval aggregate. ``vwap`` and
#: ``trade_count`` stay optional because not every venue reports them.
AGGREGATE_REQUIRED_KEYS = ("open", "high", "low", "close", "volume")
AGGREGATE_OPTIONAL_KEYS = ("vwap", "trade_count")


@dataclass(frozen=True)
class SourceWatermark:
    """How far a market source has been consumed for one instrument.

    This is source progress, not canonical commit order. ``through_utc`` is the
    exclusive upper bound of intervals already accepted.
    """

    instrument_version_id: str
    through_utc: datetime | None = None
    last_source_sequence: str | None = None
    last_ingestion_order: int = 0

    def __post_init__(self) -> None:
        if not str(self.instrument_version_id or "").strip():
            raise ValueError("instrument_version_id is required")
        if self.through_utc is not None:
            object.__setattr__(
                self,
                "through_utc",
                require_utc(self.through_utc, field_name="through_utc"),
            )
        if int(self.last_ingestion_order) < 0:
            raise ValueError("last_ingestion_order must be non-negative")
        object.__setattr__(self, "last_ingestion_order", int(self.last_ingestion_order))

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_version_id": self.instrument_version_id,
            "through_utc": (
                iso_z(self.through_utc, field_name="through_utc")
                if self.through_utc is not None
                else None
            ),
            "last_source_sequence": self.last_source_sequence,
            "last_ingestion_order": self.last_ingestion_order,
        }


@dataclass(frozen=True)
class Observation:
    """One immutable market fact, with its provenance and coverage."""

    instrument_version_id: str
    venue: str
    venue_instrument_id: str
    source_event_time: datetime
    receipt_time: datetime
    ingestion_order: int
    payload_kind: PayloadKind
    values: Mapping[str, Any]
    coverage: CoverageState = CoverageState.COMPLETE
    aggregate_interval_seconds: int | None = None
    source_sequence: str | None = None
    revision: int = 1
    supersedes: str | None = None
    interval_forming: bool = False
    provenance: Mapping[str, str] = field(default_factory=dict)
    commit_order: ConsumedInputWatermark | None = None
    schema_version: int = OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("instrument_version_id", "venue", "venue_instrument_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if int(self.ingestion_order) < 1:
            raise ValueError("ingestion_order must be >= 1")
        if int(self.revision) < 1:
            raise ValueError("revision must be >= 1")
        if self.revision > 1 and not str(self.supersedes or "").strip():
            raise ValueError("a superseding revision must name what it supersedes")
        if self.revision == 1 and self.supersedes is not None:
            raise ValueError("revision 1 cannot supersede an earlier record")
        source = require_utc(self.source_event_time, field_name="source_event_time")
        receipt = require_utc(self.receipt_time, field_name="receipt_time")

        is_aggregate = self.payload_kind is PayloadKind.FIXED_INTERVAL_AGGREGATE
        if is_aggregate:
            interval = self.aggregate_interval_seconds
            if interval is None or int(interval) <= 0:
                raise ValueError(
                    "fixed_interval_aggregate requires a positive "
                    "aggregate_interval_seconds"
                )
            if int(interval) % 60 != 0:
                raise ValueError("aggregate intervals must be whole minutes")
            if source.second != 0 or source.microsecond != 0:
                raise ValueError("aggregate source_event_time must be grid aligned")
            missing = [
                key for key in AGGREGATE_REQUIRED_KEYS if self.values.get(key) is None
            ]
            if missing:
                raise ValueError(f"aggregate is missing values: {sorted(missing)}")
            for key in (*AGGREGATE_REQUIRED_KEYS, "vwap"):
                if key not in self.values or self.values.get(key) is None:
                    continue
                try:
                    number = float(self.values[key])  # type: ignore[arg-type]
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"aggregate {key} must be numeric") from exc
                if not isfinite(number):
                    raise ValueError(f"aggregate {key} must be finite")
            if "trade_count" in self.values and self.values.get("trade_count") is not None:
                count = self.values["trade_count"]
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError("aggregate trade_count must be a non-negative int")
            object.__setattr__(self, "aggregate_interval_seconds", int(interval))
        elif self.aggregate_interval_seconds is not None:
            raise ValueError(
                "aggregate_interval_seconds only applies to fixed_interval_aggregate"
            )

        if self.interval_forming and self.coverage is CoverageState.COMPLETE:
            raise ValueError("a forming interval cannot claim COMPLETE coverage")

        object.__setattr__(self, "source_event_time", source)
        object.__setattr__(self, "receipt_time", receipt)
        object.__setattr__(self, "ingestion_order", int(self.ingestion_order))
        object.__setattr__(self, "revision", int(self.revision))
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(
                {str(key): str(value) for key, value in dict(self.provenance).items()}
            ),
        )

    @property
    def observation_id(self) -> str:
        if self.aggregate_interval_seconds is not None:
            grid = int(self.source_event_time.timestamp())
            return (
                f"OBS:{self.instrument_version_id}"
                f":{self.aggregate_interval_seconds}s:{grid}:{self.revision}"
            )
        sequence = self.source_sequence or f"ingest-{self.ingestion_order}"
        return (
            f"OBS:{self.instrument_version_id}"
            f":{self.payload_kind.value}:{sequence}:{self.revision}"
        )

    @property
    def interval_start(self) -> datetime:
        return self.source_event_time

    @property
    def interval_end(self) -> datetime | None:
        if self.aggregate_interval_seconds is None:
            return None
        return self.source_event_time + timedelta(
            seconds=self.aggregate_interval_seconds
        )

    @property
    def arrival_lag_seconds(self) -> float:
        """Receipt delay measured from the end of the interval when known."""
        reference = self.interval_end or self.source_event_time
        return (self.receipt_time - reference).total_seconds()

    def availability_stamp(self, *, source_version: str) -> AvailabilityStamp:
        """Visibility is receipt time. Source time is never used as visibility."""
        return AvailabilityStamp(
            source_at_utc=self.source_event_time,
            ingested_at_utc=self.receipt_time,
            visible_at_utc=self.receipt_time,
            source_version=source_version,
        )

    def to_dict(self) -> dict[str, Any]:
        commit = self.commit_order
        return {
            "record_type": OBSERVATION_RECORD_TYPE,
            "schema_version": self.schema_version,
            "instrument_version_id": self.instrument_version_id,
            "venue": self.venue,
            "venue_instrument_id": self.venue_instrument_id,
            "source_event_time": iso_z(
                self.source_event_time, field_name="source_event_time"
            ),
            "receipt_time": iso_z(self.receipt_time, field_name="receipt_time"),
            "ingestion_order": self.ingestion_order,
            "source_sequence": self.source_sequence,
            "history_epoch": commit.history_epoch if commit is not None else None,
            "local_sequence": commit.local_sequence if commit is not None else None,
            "aggregate_interval_seconds": self.aggregate_interval_seconds,
            "payload_kind": self.payload_kind.value,
            "coverage": self.coverage.value,
            "supersedes": self.supersedes,
            "observation_id": self.observation_id,
            "revision": self.revision,
            "interval_forming": self.interval_forming,
            "values": dict(self.values),
            "provenance": dict(sorted(self.provenance.items())),
        }


__all__ = [
    "AGGREGATE_OPTIONAL_KEYS",
    "AGGREGATE_REQUIRED_KEYS",
    "OBSERVATION_RECORD_TYPE",
    "OBSERVATION_SCHEMA_VERSION",
    "Observation",
    "SourceWatermark",
]
