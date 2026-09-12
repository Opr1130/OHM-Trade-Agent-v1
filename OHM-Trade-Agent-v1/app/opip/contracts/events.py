"""Centralized feature-bus canonical event vocabulary.

Event type names and idempotency-key construction live here so they are never
scattered as string literals across producers. Renaming any value below is a
contract change: committed evidence already carries the old name.

Every feature-bus write is LOW priority telemetry. It must never compete with
protection or execution traffic in the canonical writer's priority queue.
"""

from __future__ import annotations

from datetime import datetime

from app.opip.contracts.features import FeatureSnapshot, FeatureStateCheckpoint
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.contracts.observation import Observation
from app.opip.contracts.serialization import iso_z

MARKET_OBSERVATION_RECORDED = "market.observation.recorded"
FEATURE_SNAPSHOT_RECORDED = "feature.snapshot.recorded"
FEATURE_CHECKPOINT_RECORDED = "feature.checkpoint.recorded"
COVERAGE_GAP_RECORDED = "coverage.gap.recorded"
FEATURE_RESTART_RECORDED = "feature.restart.recorded"

FEATURE_BUS_EVENT_TYPES: frozenset[str] = frozenset(
    {
        MARKET_OBSERVATION_RECORDED,
        FEATURE_SNAPSHOT_RECORDED,
        FEATURE_CHECKPOINT_RECORDED,
        COVERAGE_GAP_RECORDED,
        FEATURE_RESTART_RECORDED,
    }
)

#: Canonical watermark stream for feature-bus commit progress. Deliberately
#: separate from the alert-governor stream so neither can rewind the other.
FEATURE_BUS_STREAM = "feature_bus.v1"

#: Feature-bus traffic is telemetry class only (Contract C priority classes).
FEATURE_BUS_PRIORITY = "LOW"


def _watermark_token(watermark: ConsumedInputWatermark) -> str:
    return f"{watermark.history_epoch}-{watermark.local_sequence}"


def observation_idempotency_key(observation: Observation) -> str:
    """Identifies the observed interval and revision, never the wall clock."""
    return f"{MARKET_OBSERVATION_RECORDED}:{observation.observation_id}"


def snapshot_idempotency_key(snapshot: FeatureSnapshot) -> str:
    """Identifies instrument, cutoff, feature version and consumed inputs.

    The consumed watermark is included because re-evaluating the same cutoff
    after late evidence arrives is a genuinely different snapshot, not a
    duplicate of the earlier one.
    """
    token = _watermark_token(snapshot.consumed_input_watermark)
    return f"{FEATURE_SNAPSHOT_RECORDED}:{snapshot.snapshot_id}:{token}"


def checkpoint_idempotency_key(checkpoint: FeatureStateCheckpoint) -> str:
    token = _watermark_token(checkpoint.consumed_input_watermark)
    return (
        f"{FEATURE_CHECKPOINT_RECORDED}:{checkpoint.instrument_version_id}"
        f":{checkpoint.feature_version}:{token}"
    )


def coverage_gap_idempotency_key(
    *,
    instrument_version_id: str,
    aggregate_interval_seconds: int,
    first_missing_utc: datetime,
    missing_interval_count: int,
) -> str:
    """Identifies the gap span itself, so re-detection does not double count."""
    if missing_interval_count < 1:
        raise ValueError("missing_interval_count must be >= 1")
    return (
        f"{COVERAGE_GAP_RECORDED}:{instrument_version_id}"
        f":{int(aggregate_interval_seconds)}s"
        f":{iso_z(first_missing_utc, field_name='first_missing_utc')}"
        f":{int(missing_interval_count)}"
    )


def restart_idempotency_key(
    *,
    instrument_version_id: str,
    feature_version: str,
    restart_state: str,
    watermark: ConsumedInputWatermark,
) -> str:
    return (
        f"{FEATURE_RESTART_RECORDED}:{instrument_version_id}:{feature_version}"
        f":{restart_state}:{_watermark_token(watermark)}"
    )


__all__ = [
    "COVERAGE_GAP_RECORDED",
    "FEATURE_BUS_EVENT_TYPES",
    "FEATURE_BUS_PRIORITY",
    "FEATURE_BUS_STREAM",
    "FEATURE_CHECKPOINT_RECORDED",
    "FEATURE_RESTART_RECORDED",
    "FEATURE_SNAPSHOT_RECORDED",
    "MARKET_OBSERVATION_RECORDED",
    "checkpoint_idempotency_key",
    "coverage_gap_idempotency_key",
    "observation_idempotency_key",
    "restart_idempotency_key",
    "snapshot_idempotency_key",
]
