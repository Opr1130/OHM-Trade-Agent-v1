"""Immutable, versioned contracts for O'Pip ML Foundation v1.

The contracts are evidence-only. They intentionally contain no exchange,
order, position, Telegram, or risk-gate dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

from app.opip.ml.temporal import AvailabilityStamp, assert_point_in_time, require_utc


FEATURE_SNAPSHOT_SCHEMA_VERSION = 1
LABEL_SCHEMA_VERSION = 1
DATASET_MANIFEST_SCHEMA_VERSION = 1
MODEL_EVIDENCE_SCHEMA_VERSION = 1
MODEL_REGISTRY_SCHEMA_VERSION = 1


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numeric evidence is not allowed")
        return value
    if isinstance(value, datetime):
        return require_utc(value, field_name="timestamp").isoformat()
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_clean(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return str(value)


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _clean(dict(payload)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def stable_hash(prefix: str, payload: Mapping[str, Any], *, length: int = 32) -> str:
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:length]
    return f"{prefix}:{digest}"


@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: Any
    availability: AvailabilityStamp
    missing: bool = False
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("feature name is required")
        _clean(self.value)
        _clean(self.provenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": _clean(self.value),
            "missing": bool(self.missing),
            "source_at_utc": self.availability.source_at_utc.isoformat(),
            "ingested_at_utc": self.availability.ingested_at_utc.isoformat(),
            "visible_at_utc": self.availability.visible_at_utc.isoformat(),
            "source_version": self.availability.source_version,
            "provenance": _clean(self.provenance),
        }


@dataclass(frozen=True)
class FeatureSnapshot:
    snapshot_id: str
    episode_id: str
    candidate_id: str | None
    decision_at_utc: datetime
    canonical_asset_id: str
    venue: str
    pair: str
    direction: str
    lane: str
    regime: str | None
    feature_schema_version: str
    feature_calc_version: str
    feature_dag_hash: str
    serialization_version: int
    features: tuple[FeatureValue, ...]
    source_versions: Mapping[str, str] = field(default_factory=dict)
    audit_deterministic_engine_version: str | None = None
    audit_deterministic_score: float | None = None
    audit_deterministic_classification: str | None = None
    schema_version: int = FEATURE_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        decision = require_utc(self.decision_at_utc, field_name="decision_at_utc")
        if not self.episode_id:
            raise ValueError("episode_id is required")
        if not self.canonical_asset_id:
            raise ValueError("canonical_asset_id is required")
        if not self.pair:
            raise ValueError("pair is required")
        if self.direction not in {"LONG", "SHORT", "NONE"}:
            raise ValueError("direction must be LONG, SHORT, or NONE")
        if not self.feature_schema_version or not self.feature_calc_version:
            raise ValueError("feature schema and calculation versions are required")
        if not self.feature_dag_hash:
            raise ValueError("feature_dag_hash is required")
        if self.serialization_version <= 0:
            raise ValueError("serialization_version must be positive")
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        assert_point_in_time(
            (feature.availability for feature in self.features),
            decision_at_utc=decision,
        )
        if self.audit_deterministic_score is not None and not math.isfinite(
            float(self.audit_deterministic_score)
        ):
            raise ValueError("audit_deterministic_score must be finite")
        object.__setattr__(self, "decision_at_utc", decision)
        expected = stable_hash("MLSNAP", self.hash_payload())
        if self.snapshot_id != expected:
            raise ValueError("snapshot_id does not match canonical snapshot payload")

    def hash_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "candidate_id": self.candidate_id,
            "decision_at_utc": self.decision_at_utc,
            "canonical_asset_id": self.canonical_asset_id,
            "venue": self.venue,
            "pair": self.pair,
            "direction": self.direction,
            "lane": self.lane,
            "regime": self.regime,
            "feature_schema_version": self.feature_schema_version,
            "feature_calc_version": self.feature_calc_version,
            "feature_dag_hash": self.feature_dag_hash,
            "serialization_version": self.serialization_version,
            "features": [feature.to_dict() for feature in self.features],
            "source_versions": dict(self.source_versions),
            "audit_deterministic_engine_version": self.audit_deterministic_engine_version,
            "audit_deterministic_score": self.audit_deterministic_score,
            "audit_deterministic_classification": self.audit_deterministic_classification,
            "schema_version": self.schema_version,
        }

    @classmethod
    def build(cls, **kwargs: Any) -> "FeatureSnapshot":
        payload = dict(kwargs)
        payload.setdefault("schema_version", FEATURE_SNAPSHOT_SCHEMA_VERSION)
        payload["features"] = tuple(payload.get("features") or ())
        provisional = cls.__new__(cls)
        for key, value in payload.items():
            object.__setattr__(provisional, key, value)
        hash_payload = cls.hash_payload(provisional)
        payload["snapshot_id"] = stable_hash("MLSNAP", hash_payload)
        return cls(**payload)

    @property
    def max_visible_at_utc(self) -> datetime | None:
        values = [feature.availability.visible_at_utc for feature in self.features]
        return max(values) if values else None

    def ml_feature_mapping(self) -> dict[str, Any]:
        """Runtime training/scoring inputs; deterministic audit fields excluded."""
        return {feature.name: feature.value for feature in self.features}

    def to_dict(self) -> dict[str, Any]:
        payload = self.hash_payload()
        payload["snapshot_id"] = self.snapshot_id
        payload["max_visible_at_utc"] = (
            self.max_visible_at_utc.isoformat()
            if self.max_visible_at_utc is not None
            else None
        )
        return _clean(payload)


@dataclass(frozen=True)
class SupervisedLabelRecord:
    label_id: str
    snapshot_id: str
    candidate_id: str | None
    direction: str
    label_calc_version: str
    label_available_at_utc: datetime
    horizon_end_utc: datetime
    tp1_before_sl: bool | None
    tp2_before_sl: bool | None
    sl_before_tp1: bool | None
    net_returns_bps: Mapping[str, float | None]
    mfe_bps: float | None
    mae_bps: float | None
    time_to_tp1_seconds: float | None
    time_to_tp2_seconds: float | None
    time_to_sl_seconds: float | None
    censored: bool
    data_gap: bool
    execution_path_ambiguous: bool
    fee_model_version: str
    slippage_model_version: str
    schema_version: int = LABEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        available = require_utc(
            self.label_available_at_utc, field_name="label_available_at_utc"
        )
        horizon = require_utc(self.horizon_end_utc, field_name="horizon_end_utc")
        if available < horizon:
            raise ValueError("label cannot be available before its horizon is resolved")
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("label direction must be LONG or SHORT")
        if not self.fee_model_version or not self.slippage_model_version:
            raise ValueError("fee and slippage model versions are required")
        _clean(self.net_returns_bps)
        object.__setattr__(self, "label_available_at_utc", available)
        object.__setattr__(self, "horizon_end_utc", horizon)


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    created_at_utc: datetime
    cutoff_at_utc: datetime
    feature_schema_version: str
    feature_calc_version: str
    label_schema_version: int
    label_calc_version: str
    cohort_filter: Mapping[str, Any]
    exclusion_policy: Mapping[str, Any]
    embargo_seconds: int
    censoring_policy: str
    overlap_handling_policy: str
    fee_model_version: str
    slippage_model_version: str
    training_code_hash: str
    environment_hash: str
    serialization_version: int
    random_seed: int
    included_snapshot_ids: tuple[str, ...]
    excluded_snapshot_ids: Mapping[str, str]
    schema_version: int = DATASET_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "created_at_utc",
            require_utc(self.created_at_utc, field_name="created_at_utc"),
        )
        object.__setattr__(
            self,
            "cutoff_at_utc",
            require_utc(self.cutoff_at_utc, field_name="cutoff_at_utc"),
        )
        if self.embargo_seconds < 0:
            raise ValueError("embargo_seconds cannot be negative")
        if self.serialization_version <= 0:
            raise ValueError("serialization_version must be positive")


class ModelLifecycle(str, Enum):
    REGISTERED = "REGISTERED"
    VALIDATED = "VALIDATED"
    SHADOW = "SHADOW"
    CHALLENGER = "CHALLENGER"


class ModelHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class ModelRegistryRecord:
    model_id: str
    model_family: str
    model_version: str
    artifact_hash: str
    training_code_hash: str
    environment_hash: str
    hyperparameters: Mapping[str, Any]
    random_seed: int
    feature_schema_version: str
    feature_calc_version: str
    label_schema_version: int
    label_calc_version: str
    training_dataset_id: str
    validation_dataset_id: str
    calibration_dataset_id: str | None
    validation_report_id: str
    lifecycle: ModelLifecycle
    health: ModelHealth
    approval_principal: str | None = None
    approved_at_utc: datetime | None = None
    rollback_model_id: str | None = None
    schema_version: int = MODEL_REGISTRY_SCHEMA_VERSION


@dataclass(frozen=True)
class ModelEvidence:
    evidence_id: str
    model_id: str
    model_version: str
    model_family: str
    snapshot_id: str
    snapshot_hash: str
    feature_schema_version: str
    feature_calc_version: str
    p_tp1_before_sl: float | None
    p_tp2_before_sl: float | None
    p_sl_before_tp1: float | None
    expected_net_return_bps: float | None
    out_of_distribution_flags: tuple[str, ...]
    calibration_version: str | None
    training_dataset_id: str
    scored_at_utc: datetime
    inference_latency_ms: float
    execution_status: str
    trace_id: str
    schema_version: int = MODEL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scored_at_utc",
            require_utc(self.scored_at_utc, field_name="scored_at_utc"),
        )
        for value in (
            self.p_tp1_before_sl,
            self.p_tp2_before_sl,
            self.p_sl_before_tp1,
        ):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError("model probabilities must be in [0, 1]")
        if self.inference_latency_ms < 0:
            raise ValueError("inference_latency_ms cannot be negative")


@dataclass(frozen=True)
class DriftHealthRecord:
    model_id: str
    model_version: str
    evaluated_at_utc: datetime
    rolling_window_start_utc: datetime
    rolling_window_end_utc: datetime
    sample_count: int
    input_distribution_drift: Mapping[str, float]
    missingness_drift: Mapping[str, float]
    out_of_training_support_rate: float | None
    prediction_distribution_drift: float | None
    label_base_rate_drift: float | None
    rolling_brier_score: float | None
    rolling_calibration_error: float | None
    performance_decay: float | None
    model_staleness_seconds: float | None
    schema_incompatibility_count: int
    operational_failure_rate: float | None
    health: ModelHealth

    def __post_init__(self) -> None:
        for field_name in (
            "evaluated_at_utc",
            "rolling_window_start_utc",
            "rolling_window_end_utc",
        ):
            object.__setattr__(
                self,
                field_name,
                require_utc(getattr(self, field_name), field_name=field_name),
            )
        if self.sample_count < 0:
            raise ValueError("sample_count cannot be negative")
