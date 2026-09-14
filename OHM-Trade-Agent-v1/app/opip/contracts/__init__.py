"""Shared O'Pip architecture contracts (PR3 feature bus, slice A).

This package is the single owner of the vocabulary that market ingestion,
feature computation, detectors, forecasting and learning all share. It is pure:
no exchange client, canonical writer, storage, scheduler, notification, AI or
risk-gate import is permitted here, and nothing in this package may reach into
``app.services``.
"""

from __future__ import annotations

from app.opip.contracts.enums import (
    CompressionState,
    CoverageState,
    Missingness,
    PayloadKind,
    RestartState,
    TrendState,
    VolatilityState,
)
from app.opip.contracts.events import (
    COVERAGE_GAP_RECORDED,
    FEATURE_BUS_EVENT_TYPES,
    FEATURE_BUS_PRIORITY,
    FEATURE_BUS_STREAM,
    FEATURE_CHECKPOINT_RECORDED,
    FEATURE_RESTART_RECORDED,
    FEATURE_SNAPSHOT_RECORDED,
    MARKET_INSTRUMENT_VERSION_RECORDED,
    MARKET_OBSERVATION_RECORDED,
    checkpoint_idempotency_key,
    coverage_gap_idempotency_key,
    instrument_version_idempotency_key,
    observation_idempotency_key,
    restart_idempotency_key,
    snapshot_idempotency_key,
)
from app.opip.contracts.features import (
    DEFAULT_EVALUATION_GRID_SECONDS,
    FEATURE_BUS_SCHEMA_VERSION,
    FeatureSnapshot,
    FeatureStateCheckpoint,
)
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.observation import Observation, SourceWatermark
from app.opip.contracts.serialization import canonical_json_bytes, iso_z, stable_hash
from app.opip.contracts.temporal import (
    AvailabilityStamp,
    TemporalIntegrityError,
    assert_point_in_time,
    require_utc,
)

__all__ = [
    "COVERAGE_GAP_RECORDED",
    "DEFAULT_EVALUATION_GRID_SECONDS",
    "FEATURE_BUS_EVENT_TYPES",
    "FEATURE_BUS_PRIORITY",
    "FEATURE_BUS_SCHEMA_VERSION",
    "FEATURE_BUS_STREAM",
    "FEATURE_CHECKPOINT_RECORDED",
    "FEATURE_RESTART_RECORDED",
    "FEATURE_SNAPSHOT_RECORDED",
    "MARKET_INSTRUMENT_VERSION_RECORDED",
    "MARKET_OBSERVATION_RECORDED",
    "AvailabilityStamp",
    "CompressionState",
    "ConsumedInputWatermark",
    "CoverageState",
    "FeatureSnapshot",
    "FeatureStateCheckpoint",
    "InstrumentVersion",
    "Missingness",
    "Observation",
    "PayloadKind",
    "RestartState",
    "SourceWatermark",
    "TemporalIntegrityError",
    "TrendState",
    "VolatilityState",
    "assert_point_in_time",
    "canonical_json_bytes",
    "checkpoint_idempotency_key",
    "coverage_gap_idempotency_key",
    "instrument_version_idempotency_key",
    "iso_z",
    "observation_idempotency_key",
    "require_utc",
    "restart_idempotency_key",
    "snapshot_idempotency_key",
    "stable_hash",
]
