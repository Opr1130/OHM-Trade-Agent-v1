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
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.ml.temporal import AvailabilityStamp, assert_point_in_time, require_utc


FEATURE_SNAPSHOT_SCHEMA_VERSION = 1
LABEL_SCHEMA_VERSION = 1
DATASET_MANIFEST_SCHEMA_VERSION = 1
MODEL_EVIDENCE_SCHEMA_VERSION = 1
MODEL_REGISTRY_SCHEMA_VERSION = 1
DRIFT_HEALTH_SCHEMA_VERSION = 1


def _freeze_jsonish(value: Any) -> Any:
    """Deep-freeze deterministic JSON-like evidence.

    Unsupported objects are rejected rather than converted to strings because
    object representations can contain process-specific state and break replay
    hashing.
    """
    if isinstance(value, Enum):
        return _freeze_jsonish(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numeric evidence is not allowed")
        return value
    if isinstance(value, datetime):
        return require_utc(value, field_name="timestamp").isoformat()
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("evidence mapping keys must be strings")
            frozen[key] = _freeze_jsonish(item)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_jsonish(item) for item in value)
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


def _clean(value: Any) -> Any:
    """Convert frozen JSON-like evidence to canonical serialization values."""
    if isinstance(value, Enum):
        return _clean(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numeric evidence is not allowed")
        return value
    if isinstance(value, datetime):
        return require_utc(value, field_name="timestamp").isoformat()
    if isinstance(value, Mapping):
        items = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("evidence mapping keys must be strings")
            items.append((key, _clean(item)))
        return {key: item for key, item in sorted(items)}
    if isinstance(value, (tuple, list)):
        return [_clean(item) for item in value]
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


def _optional_finite(value: float | None, *, field_name: str) -> None:
    if value is not None and not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite when present")


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
        object.__setattr__(self, "value", _freeze_jsonish(self.value))
        object.__setattr__(self, "provenance", _freeze_jsonish(self.provenance))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": _clean(self.value),
            "missing": bool(self.missing),
            "source_at_utc": (
                self.availability.source_at_utc.isoformat()
                if self.availability.source_at_utc is not None
                else None
            ),
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
        features = tuple(self.features)
        names = [feature.name for feature in features]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        assert_point_in_time(
            (feature.availability for feature in features),
            decision_at_utc=decision,
        )
        _optional_finite(
            self.audit_deterministic_score,
            field_name="audit_deterministic_score",
        )
        frozen_versions = _freeze_jsonish(dict(self.source_versions))
        for key, value in frozen_versions.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"source version for {key} must be a non-empty string")
        object.__setattr__(self, "decision_at_utc", decision)
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "source_versions", frozen_versions)
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
        payload.setdefault("source_versions", {})
        payload.setdefault("audit_deterministic_engine_version", None)
        payload.setdefault("audit_deterministic_score", None)
        payload.setdefault("audit_deterministic_classification", None)
        payload["features"] = tuple(payload.get("features") or ())
        required = (
            "episode_id",
            "candidate_id",
            "decision_at_utc",
            "canonical_asset_id",
            "venue",
            "pair",
            "direction",
            "lane",
            "regime",
            "feature_schema_version",
            "feature_calc_version",
            "feature_dag_hash",
            "serialization_version",
            "features",
        )
        missing = [name for name in required if name not in payload]
        if missing:
            raise TypeError("missing FeatureSnapshot fields: " + ", ".join(missing))
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
        return {feature.name: _clean(feature.value) for feature in self.features}

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
    label_computed_at_utc: datetime
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
        computed = require_utc(
            self.label_computed_at_utc, field_name="label_computed_at_utc"
        )
        horizon = require_utc(self.horizon_end_utc, field_name="horizon_end_utc")
        if available < horizon:
            raise ValueError("label cannot be available before its horizon is resolved")
        if computed < available:
            raise ValueError("label cannot be computed before its inputs are available")
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("label direction must be LONG or SHORT")
        if not self.label_calc_version:
            raise ValueError("label_calc_version is required")
        if not self.fee_model_version or not self.slippage_model_version:
            raise ValueError("fee and slippage model versions are required")
        for name in (
            "mfe_bps",
            "mae_bps",
            "time_to_tp1_seconds",
            "time_to_tp2_seconds",
            "time_to_sl_seconds",
        ):
            _optional_finite(getattr(self, name), field_name=name)
        frozen_returns = _freeze_jsonish(dict(self.net_returns_bps))
        object.__setattr__(self, "label_available_at_utc", available)
        object.__setattr__(self, "label_computed_at_utc", computed)
        object.__setattr__(self, "horizon_end_utc", horizon)
        object.__setattr__(self, "net_returns_bps", frozen_returns)
        expected = stable_hash("MLLBL", self.hash_payload())
        if self.label_id != expected:
            raise ValueError("label_id does not match canonical label payload")

    def hash_payload(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "candidate_id": self.candidate_id,
            "direction": self.direction,
            "label_calc_version": self.label_calc_version,
            "label_available_at_utc": self.label_available_at_utc,
            "label_computed_at_utc": self.label_computed_at_utc,
            "horizon_end_utc": self.horizon_end_utc,
            "tp1_before_sl": self.tp1_before_sl,
            "tp2_before_sl": self.tp2_before_sl,
            "sl_before_tp1": self.sl_before_tp1,
            "net_returns_bps": self.net_returns_bps,
            "mfe_bps": self.mfe_bps,
            "mae_bps": self.mae_bps,
            "time_to_tp1_seconds": self.time_to_tp1_seconds,
            "time_to_tp2_seconds": self.time_to_tp2_seconds,
            "time_to_sl_seconds": self.time_to_sl_seconds,
            "censored": self.censored,
            "data_gap": self.data_gap,
            "execution_path_ambiguous": self.execution_path_ambiguous,
            "fee_model_version": self.fee_model_version,
            "slippage_model_version": self.slippage_model_version,
            "schema_version": self.schema_version,
        }


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
    included_label_ids: tuple[str, ...]
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
        object.__setattr__(self, "cohort_filter", _freeze_jsonish(self.cohort_filter))
        object.__setattr__(
            self, "exclusion_policy", _freeze_jsonish(self.exclusion_policy)
        )
        object.__setattr__(
            self, "included_snapshot_ids", tuple(self.included_snapshot_ids)
        )
        object.__setattr__(
            self, "included_label_ids", tuple(self.included_label_ids)
        )
        if len(self.included_snapshot_ids) != len(self.included_label_ids):
            raise ValueError("included snapshot and label identities must align")
        object.__setattr__(
            self, "excluded_snapshot_ids", _freeze_jsonish(self.excluded_snapshot_ids)
        )


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

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hyperparameters", _freeze_jsonish(dict(self.hyperparameters))
        )
        approved = self.approved_at_utc
        if approved is not None:
            approved = require_utc(approved, field_name="approved_at_utc")
            object.__setattr__(self, "approved_at_utc", approved)
        if (self.approval_principal is None) != (approved is None):
            raise ValueError("approval principal and timestamp must be supplied together")
        if self.lifecycle == ModelLifecycle.CHALLENGER and (
            not str(self.approval_principal or "").strip() or approved is None
        ):
            raise ValueError("CHALLENGER requires explicit approval metadata")


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
        object.__setattr__(
            self, "out_of_distribution_flags", tuple(self.out_of_distribution_flags)
        )
        for value in (
            self.p_tp1_before_sl,
            self.p_tp2_before_sl,
            self.p_sl_before_tp1,
        ):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError("model probabilities must be in [0, 1]")
        _optional_finite(
            self.expected_net_return_bps, field_name="expected_net_return_bps"
        )
        if not math.isfinite(float(self.inference_latency_ms)):
            raise ValueError("inference_latency_ms must be finite")
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
    schema_version: int = DRIFT_HEALTH_SCHEMA_VERSION

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
        if self.rolling_window_end_utc < self.rolling_window_start_utc:
            raise ValueError("rolling window end cannot precede its start")
        if self.sample_count < 0:
            raise ValueError("sample_count cannot be negative")
        if self.schema_incompatibility_count < 0:
            raise ValueError("schema_incompatibility_count cannot be negative")
        object.__setattr__(
            self,
            "input_distribution_drift",
            _freeze_jsonish(dict(self.input_distribution_drift)),
        )
        object.__setattr__(
            self,
            "missingness_drift",
            _freeze_jsonish(dict(self.missingness_drift)),
        )
        for name in (
            "out_of_training_support_rate",
            "prediction_distribution_drift",
            "label_base_rate_drift",
            "rolling_brier_score",
            "rolling_calibration_error",
            "performance_decay",
            "model_staleness_seconds",
            "operational_failure_rate",
        ):
            _optional_finite(getattr(self, name), field_name=name)
