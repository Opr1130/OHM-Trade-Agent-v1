"""ML as a consumer of the shared FeatureSnapshot contract (PR3 slice A/G).

``app.opip.ml.contracts.FeatureSnapshot`` remains the decision-oriented
training record, with one availability stamp per feature value. The shared
architecture owns the feature-bus snapshot. This module is the only place the
two meet: it projects an ML snapshot onto the shared contract and proves the
versioning identity survives, so there is one logical FeatureSnapshot definition
rather than two competing ones.

No ML module is modified. Physical relocation of the ML snapshot is deferred to
a later retirement PR, once every consumer reads through the shared contract.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.opip.contracts.enums import CoverageState, Missingness, RestartState
from app.opip.contracts.features import (
    DEFAULT_EVALUATION_GRID_SECONDS,
    FeatureSnapshot,
)
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.temporal import AvailabilityStamp
from app.opip.market.aggregates import grid_floor
from app.opip.ml.contracts import FeatureSnapshot as MlFeatureSnapshot

_SCALARS = (str, bool, int, float)


def versioning_triple(snapshot: MlFeatureSnapshot | FeatureSnapshot) -> dict[str, str]:
    """The identity that makes two feature sets poolable, or not."""
    return {
        "feature_schema_version": str(snapshot.feature_schema_version),
        "feature_calc_version": str(snapshot.feature_calc_version),
        "feature_dag_hash": str(snapshot.feature_dag_hash),
    }


def to_shared_snapshot(
    ml_snapshot: MlFeatureSnapshot,
    *,
    instrument_version: InstrumentVersion,
    consumed_input_watermark: ConsumedInputWatermark,
    feature_version: str | None = None,
    coverage: CoverageState = CoverageState.COMPLETE,
    restart_state: RestartState = RestartState.WARM,
    evaluation_grid_seconds: int = DEFAULT_EVALUATION_GRID_SECONDS,
    notes: str | None = None,
) -> FeatureSnapshot:
    """Project an ML training snapshot onto the shared feature-bus contract.

    Structured feature values are rejected rather than flattened: silently
    stringifying a nested value would produce a snapshot whose content hash
    looks stable while its meaning has changed.
    """
    values: dict[str, Any] = {}
    missingness: dict[str, Missingness] = {}
    for feature in ml_snapshot.features:
        if feature.value is not None and not isinstance(feature.value, _SCALARS):
            raise ValueError(
                f"ML feature {feature.name!r} is not a scalar and cannot be "
                "projected onto the shared snapshot contract"
            )
        values[feature.name] = feature.value
        missingness[feature.name] = (
            Missingness.MISSING if feature.missing else Missingness.PRESENT
        )

    decision_at = ml_snapshot.decision_at_utc
    visible_at = max(
        (feature.availability.visible_at_utc for feature in ml_snapshot.features),
        default=decision_at,
    )
    ingested_at = max(
        (feature.availability.ingested_at_utc for feature in ml_snapshot.features),
        default=decision_at,
    )
    source_times = [
        feature.availability.source_at_utc
        for feature in ml_snapshot.features
        if feature.availability.source_at_utc is not None
    ]
    availability = AvailabilityStamp(
        source_at_utc=max(source_times) if source_times else None,
        ingested_at_utc=ingested_at,
        visible_at_utc=visible_at,
        source_version=ml_snapshot.feature_calc_version,
    )

    return FeatureSnapshot(
        instrument_version_id=instrument_version.instrument_version_id,
        venue_instrument_id=instrument_version.venue_instrument_id,
        feature_version=feature_version or ml_snapshot.feature_calc_version,
        evaluation_cutoff=grid_floor(
            decision_at, interval_seconds=evaluation_grid_seconds
        ),
        evaluated_at_utc=decision_at,
        consumed_input_watermark=consumed_input_watermark,
        values=values,
        availability=availability,
        missingness=missingness,
        coverage=coverage,
        restart_state=restart_state,
        evaluation_grid_seconds=evaluation_grid_seconds,
        feature_schema_version=ml_snapshot.feature_schema_version,
        feature_calc_version=ml_snapshot.feature_calc_version,
        feature_dag_hash=ml_snapshot.feature_dag_hash,
        notes=notes,
    )


def versioning_is_lossless(
    ml_snapshot: MlFeatureSnapshot, shared: FeatureSnapshot
) -> bool:
    return versioning_triple(ml_snapshot) == versioning_triple(shared)


def projected_value_map(shared: FeatureSnapshot) -> Mapping[str, Any]:
    return dict(shared.values)


__all__ = [
    "projected_value_map",
    "to_shared_snapshot",
    "versioning_is_lossless",
    "versioning_triple",
]
