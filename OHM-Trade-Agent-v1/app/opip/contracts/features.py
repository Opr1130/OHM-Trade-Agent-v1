"""FeatureSnapshot and FeatureStateCheckpoint contracts.

The shared architecture owns ``FeatureSnapshot``. ML is a consumer: the
decision-oriented snapshot in ``app.opip.ml.contracts`` keeps its per-feature
availability stamps for training evidence, and
``app.opip.features.ml_bridge`` projects it onto this contract. There is one
logical FeatureSnapshot definition, not two competing ones.

Two design points are deliberate:

* ``snapshot_id`` is the deterministic composite id from the v1.2 fixtures, so
  the same evaluation always produces the same id and the canonical writer can
  dedupe on it. ``content_hash`` separately carries ML's tamper-evidence.
* Availability is stamped once per snapshot rather than once per value. All
  values in one snapshot share the same evaluation cutoff, and a per-value
  stamp would multiply payload size against the writer's 16 KiB bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.contracts.enums import CoverageState, Missingness, RestartState
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import (
    AvailabilityStamp,
    TemporalIntegrityError,
    assert_point_in_time,
    require_utc,
)

FEATURE_SNAPSHOT_RECORD_TYPE = "FeatureSnapshot"
FEATURE_CHECKPOINT_RECORD_TYPE = "FeatureStateCheckpoint"
FEATURE_BUS_SCHEMA_VERSION = 1


def _require_schema_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("schema_version must be an integer")
    if int(value) < 1:
        raise ValueError("schema_version must be >= 1")
    return int(value)


#: N10: IGNITION evaluates on a one-minute grid. Longer candles are feature
#: inputs, never the evaluation cadence.
DEFAULT_EVALUATION_GRID_SECONDS = 60

_SCALAR_TYPES = (str, bool, int, float)


def _scalar_values(values: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    """Accept only JSON scalars, so replay hashing stays stable."""
    cleaned: dict[str, Any] = {}
    for key, value in dict(values).items():
        name = str(key)
        if value is None or isinstance(value, _SCALAR_TYPES):
            cleaned[name] = value
            continue
        raise TypeError(f"{field_name}[{name}] must be a JSON scalar or None")
    return dict(sorted(cleaned.items()))


@dataclass(frozen=True)
class FeatureSnapshot:
    """Every evaluation is persisted, including evaluations that claim nothing.

    A snapshot records what the detector was allowed to see: the values, the
    cutoff, the consumed input watermark, what was missing and why, and the
    warm-up state of the rolling computation.
    """

    instrument_version_id: str
    venue_instrument_id: str
    feature_version: str
    evaluation_cutoff: datetime
    evaluated_at_utc: datetime
    consumed_input_watermark: ConsumedInputWatermark
    values: Mapping[str, Any]
    availability: AvailabilityStamp
    missingness: Mapping[str, Missingness] = field(default_factory=dict)
    coverage: CoverageState = CoverageState.COMPLETE
    restart_state: RestartState = RestartState.WARM
    evaluation_grid_seconds: int = DEFAULT_EVALUATION_GRID_SECONDS
    feature_schema_version: str = "features-schema-v1"
    feature_calc_version: str = "features-calc-v1"
    feature_dag_hash: str = ""
    notes: str | None = None
    schema_version: int = FEATURE_BUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "instrument_version_id",
            "venue_instrument_id",
            "feature_version",
            "feature_schema_version",
            "feature_calc_version",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if not str(self.feature_dag_hash or "").strip():
            raise ValueError("feature_dag_hash is required")
        if int(self.evaluation_grid_seconds) <= 0:
            raise ValueError("evaluation_grid_seconds must be positive")
        cutoff = require_utc(self.evaluation_cutoff, field_name="evaluation_cutoff")
        grid = int(self.evaluation_grid_seconds)
        cutoff_epoch = int(cutoff.timestamp())
        if cutoff_epoch % grid != 0 or cutoff.microsecond:
            raise ValueError("evaluation_cutoff must sit on the evaluation grid")
        evaluated_at = require_utc(self.evaluated_at_utc, field_name="evaluated_at_utc")
        if evaluated_at < cutoff:
            raise ValueError("evaluated_at_utc cannot precede evaluation_cutoff")
        # Two distinct boundaries, deliberately not collapsed into one:
        #   evaluation_cutoff  — no input may have a later source event time.
        #   evaluated_at_utc   — no input may have become visible later.
        # Collapsing them would reject every honest snapshot, because a closed
        # one-minute interval only becomes visible some milliseconds after the
        # grid instant it belongs to.
        assert_point_in_time((self.availability,), decision_at_utc=evaluated_at)

        missingness = {
            str(key): Missingness(value)
            for key, value in dict(self.missingness).items()
        }
        object.__setattr__(self, "evaluation_cutoff", cutoff)
        object.__setattr__(self, "evaluated_at_utc", evaluated_at)
        object.__setattr__(self, "evaluation_grid_seconds", int(self.evaluation_grid_seconds))
        object.__setattr__(
            self,
            "values",
            MappingProxyType(_scalar_values(self.values, field_name="values")),
        )
        object.__setattr__(
            self,
            "missingness",
            MappingProxyType(dict(sorted(missingness.items()))),
        )
        object.__setattr__(
            self, "venue_instrument_id", str(self.venue_instrument_id).strip()
        )
        object.__setattr__(self, "schema_version", _require_schema_version(self.schema_version))

    @property
    def snapshot_id(self) -> str:
        """Deterministic composite id, matching the v1.2 fixture format."""
        return ":".join(
            (
                "FS",
                str(self.schema_version),
                self.venue_instrument_id.lower(),
                iso_z(self.evaluation_cutoff, field_name="evaluation_cutoff"),
                self.feature_version,
            )
        )

    def content_hash(self) -> str:
        """Tamper-evidence over the substantive payload."""
        return stable_hash("FSHASH", self._hash_payload())

    def _hash_payload(self) -> dict[str, Any]:
        availability = self.availability
        return {
            "instrument_version_id": self.instrument_version_id,
            "feature_version": self.feature_version,
            "feature_schema_version": self.feature_schema_version,
            "feature_calc_version": self.feature_calc_version,
            "feature_dag_hash": self.feature_dag_hash,
            "evaluation_cutoff": iso_z(self.evaluation_cutoff),
            "evaluated_at_utc": iso_z(self.evaluated_at_utc),
            "evaluation_grid_seconds": self.evaluation_grid_seconds,
            "consumed_input_watermark": self.consumed_input_watermark.to_dict(),
            "values": dict(self.values),
            "missingness": {
                key: value.value for key, value in self.missingness.items()
            },
            "coverage": self.coverage.value,
            "restart_state": self.restart_state.value,
            "availability": {
                "source_at_utc": (
                    iso_z(availability.source_at_utc)
                    if availability.source_at_utc is not None
                    else None
                ),
                "ingested_at_utc": iso_z(availability.ingested_at_utc),
                "visible_at_utc": iso_z(availability.visible_at_utc),
                "source_version": availability.source_version,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        availability = self.availability
        payload: dict[str, Any] = {
            "record_type": FEATURE_SNAPSHOT_RECORD_TYPE,
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "feature_version": self.feature_version,
            "evaluation_cutoff": iso_z(self.evaluation_cutoff),
            "consumed_input_watermark": self.consumed_input_watermark.to_dict(),
            "evaluation_grid_seconds": self.evaluation_grid_seconds,
            "missingness": {
                key: value.value for key, value in self.missingness.items()
            },
            "values": dict(self.values),
            "notes": self.notes,
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "coverage": self.coverage.value,
            "restart_state": self.restart_state.value,
            "feature_schema_version": self.feature_schema_version,
            "feature_calc_version": self.feature_calc_version,
            "feature_dag_hash": self.feature_dag_hash,
            "evaluated_at_utc": iso_z(self.evaluated_at_utc),
            "availability": {
                "source_at_utc": (
                    iso_z(availability.source_at_utc)
                    if availability.source_at_utc is not None
                    else None
                ),
                "ingested_at_utc": iso_z(availability.ingested_at_utc),
                "visible_at_utc": iso_z(availability.visible_at_utc),
                "source_version": availability.source_version,
            },
            "visible_at_utc": iso_z(availability.visible_at_utc),
            "content_hash": self.content_hash(),
        }
        return payload


@dataclass(frozen=True)
class FeatureStateCheckpoint:
    """Enough rolling state to resume without replaying all of history.

    ``reconstruction_dependencies`` names what a resume additionally needs, so
    a checkpoint can never be silently treated as self-sufficient when it is
    not.
    """

    instrument_version_id: str
    venue_instrument_id: str
    feature_version: str
    consumed_input_watermark: ConsumedInputWatermark
    rolling_state: Mapping[str, Any]
    restart_state: RestartState
    reconstruction_dependencies: tuple[str, ...] = ("fixed_interval_aggregate:60s",)
    created_at_utc: datetime | None = None
    schema_version: int = FEATURE_BUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "instrument_version_id",
            "venue_instrument_id",
            "feature_version",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        dependencies = tuple(str(item) for item in self.reconstruction_dependencies)
        if not dependencies:
            raise ValueError("reconstruction_dependencies must not be empty")
        if self.created_at_utc is not None:
            object.__setattr__(
                self,
                "created_at_utc",
                require_utc(self.created_at_utc, field_name="created_at_utc"),
            )
        object.__setattr__(self, "reconstruction_dependencies", dependencies)
        object.__setattr__(
            self, "venue_instrument_id", str(self.venue_instrument_id).strip()
        )
        object.__setattr__(
            self,
            "rolling_state",
            MappingProxyType(_scalar_or_list_state(self.rolling_state)),
        )
        object.__setattr__(self, "schema_version", _require_schema_version(self.schema_version))

    @property
    def checkpoint_id(self) -> str:
        watermark = self.consumed_input_watermark
        return ":".join(
            (
                "FSC",
                str(self.schema_version),
                self.venue_instrument_id.lower(),
                self.feature_version,
                f"{watermark.history_epoch}-{watermark.local_sequence}",
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": FEATURE_CHECKPOINT_RECORD_TYPE,
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "feature_version": self.feature_version,
            "instrument_version_id": self.instrument_version_id,
            "consumed_input_watermark": self.consumed_input_watermark.to_dict(),
            "reconstruction_dependencies": list(self.reconstruction_dependencies),
            "rolling_state": dict(self.rolling_state),
            "restart_state": self.restart_state.value,
            "venue_instrument_id": self.venue_instrument_id,
            "created_at_utc": (
                iso_z(self.created_at_utc) if self.created_at_utc is not None else None
            ),
        }


def _scalar_or_list_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Rolling state may hold scalars, numeric lists, bool lists, or string lists.

    Nested sequences are frozen as tuples so MappingProxyType exposure cannot be
    mutated through list element assignment after construction.
    """
    cleaned: dict[str, Any] = {}
    for key, value in dict(state).items():
        name = str(key)
        if value is None or isinstance(value, _SCALAR_TYPES):
            cleaned[name] = value
            continue
        if isinstance(value, (list, tuple)):
            items = list(value)
            if not items:
                cleaned[name] = ()
                continue
            if all(isinstance(item, str) for item in items):
                cleaned[name] = tuple(str(item) for item in items)
                continue
            if all(isinstance(item, bool) for item in items):
                cleaned[name] = tuple(bool(item) for item in items)
                continue
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in items):
                raise TypeError(f"rolling_state[{name}] lists must be numeric")
            cleaned[name] = tuple(float(item) for item in items)
            continue
        raise TypeError(f"rolling_state[{name}] must be a scalar or list")
    return dict(sorted(cleaned.items()))


__all__ = [
    "DEFAULT_EVALUATION_GRID_SECONDS",
    "FEATURE_BUS_SCHEMA_VERSION",
    "FEATURE_CHECKPOINT_RECORD_TYPE",
    "FEATURE_SNAPSHOT_RECORD_TYPE",
    "FeatureSnapshot",
    "FeatureStateCheckpoint",
    "TemporalIntegrityError",
]
