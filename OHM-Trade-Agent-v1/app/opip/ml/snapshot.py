"""FeatureSnapshot construction helpers.

The builder never recomputes historical values. It seals values supplied by an
already-computed O'Pip decision/observation together with explicit availability
provenance, preserving online/offline parity.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.opip.ml.contracts import FeatureSnapshot, FeatureValue
from app.opip.ml.temporal import AvailabilityStamp, require_utc


AUDIT_ONLY_KEYS = frozenset(
    {
        "deterministic_score",
        "deterministic_classification",
        "opportunity_score",
        "decision_status",
        "suppressed",
    }
)


def seal_feature_snapshot(
    *,
    episode_id: str,
    candidate_id: str | None,
    decision_at_utc: datetime,
    canonical_asset_id: str,
    venue: str,
    pair: str,
    direction: str,
    lane: str,
    regime: str | None,
    feature_values: Mapping[str, Any],
    availability: Mapping[str, AvailabilityStamp],
    feature_schema_version: str,
    feature_calc_version: str,
    feature_dag_hash: str,
    source_versions: Mapping[str, str] | None = None,
    audit_deterministic_engine_version: str | None = None,
    audit_deterministic_score: float | None = None,
    audit_deterministic_classification: str | None = None,
    serialization_version: int = 1,
) -> FeatureSnapshot:
    decision = require_utc(decision_at_utc, field_name="decision_at_utc")
    if set(feature_values) != set(availability):
        missing_stamps = sorted(set(feature_values) - set(availability))
        extra_stamps = sorted(set(availability) - set(feature_values))
        raise ValueError(
            f"feature/availability keys differ: missing={missing_stamps}, "
            f"extra={extra_stamps}"
        )
    forbidden = sorted(set(feature_values) & AUDIT_ONLY_KEYS)
    if forbidden:
        raise ValueError(
            "deterministic/audit outputs are excluded from independent ML features: "
            + ", ".join(forbidden)
        )
    features = tuple(
        FeatureValue(
            name=name,
            value=feature_values[name],
            availability=availability[name],
            missing=feature_values[name] is None,
            provenance={"availability_basis": "EXPLICIT_POINT_IN_TIME"},
        )
        for name in sorted(feature_values)
    )
    return FeatureSnapshot.build(
        episode_id=episode_id,
        candidate_id=candidate_id,
        decision_at_utc=decision,
        canonical_asset_id=canonical_asset_id,
        venue=venue,
        pair=pair,
        direction=direction,
        lane=lane,
        regime=regime,
        feature_schema_version=feature_schema_version,
        feature_calc_version=feature_calc_version,
        feature_dag_hash=feature_dag_hash,
        serialization_version=serialization_version,
        features=features,
        source_versions=dict(source_versions or {}),
        audit_deterministic_engine_version=audit_deterministic_engine_version,
        audit_deterministic_score=audit_deterministic_score,
        audit_deterministic_classification=audit_deterministic_classification,
    )
