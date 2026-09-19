from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.decision_intelligence.serialization import (
    canonicalize_nested,
    require_utc,
    stable_hash,
)

_UTC_OFFSET = "+00:00"
_UTC_Z = "Z"
_INVALID_CONSUMED_INPUT_WATERMARK = "invalid consumed_input_watermark"
_WATERMARK_KEYS = {"history_epoch", "local_sequence"}


def _freeze_nested(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_nested(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_nested(item) for item in value)
    return value


def _parse_datetime(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace(_UTC_Z, _UTC_OFFSET))
    return require_utc(value, field_name=field_name)


def _isoformat_z(value: datetime) -> str:
    return value.isoformat().replace(_UTC_OFFSET, _UTC_Z)


def _coerce_consumed_input_watermark(value: Any) -> ConsumedInputWatermark:
    if isinstance(value, ConsumedInputWatermark):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("consumed_input_watermark must be a watermark object")
    if set(value) != _WATERMARK_KEYS:
        raise ValueError(_INVALID_CONSUMED_INPUT_WATERMARK)

    history_epoch = value["history_epoch"]
    local_sequence = value["local_sequence"]
    if type(history_epoch) is not int or type(local_sequence) is not int:
        raise ValueError(_INVALID_CONSUMED_INPUT_WATERMARK)
    if history_epoch < 0 or local_sequence < 0:
        raise ValueError(_INVALID_CONSUMED_INPUT_WATERMARK)
    return ConsumedInputWatermark(
        history_epoch=history_epoch,
        local_sequence=local_sequence,
    )


def _normalize_evidence_manifest(
    manifest: Mapping[str, Any], *, evidence_cutoff: datetime
) -> MappingProxyType:
    normalized: dict[str, Any] = {}
    for evidence_id, entry in dict(manifest).items():
        if not isinstance(entry, Mapping):
            raise ValueError("evidence manifest entries must be objects")
        item = dict(entry)
        available_at = item.get("available_at")
        if available_at is not None:
            available_at = _parse_datetime(
                available_at,
                field_name="evidence.available_at",
            )
            if available_at > evidence_cutoff:
                raise ValueError(
                    "evidence_cutoff rejects evidence available after cutoff"
                )
            item["available_at"] = available_at
        normalized[str(evidence_id)] = _freeze_nested(item)
    return MappingProxyType(normalized)


def _normalize_source_availability(
    values: Mapping[str, Any], *, evidence_cutoff: datetime
) -> MappingProxyType:
    normalized: dict[str, Any] = {}
    for source, availability in dict(values).items():
        if availability is not None:
            availability = _parse_datetime(
                availability,
                field_name="source_availability_time",
            )
            if availability > evidence_cutoff:
                raise ValueError(
                    "evidence_cutoff rejects evidence available after cutoff"
                )
        normalized[str(source)] = availability
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class Provenance:
    producing_component: str
    artifact_or_build_id: str
    process_instance_id: str
    emitted_at: datetime
    source_record_refs: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported Provenance schema_version")
        object.__setattr__(
            self,
            "producing_component",
            str(self.producing_component).strip(),
        )
        object.__setattr__(
            self,
            "artifact_or_build_id",
            str(self.artifact_or_build_id).strip(),
        )
        if not self.producing_component or not self.artifact_or_build_id:
            raise ValueError(
                "provenance component and artifact_or_build_id are required"
            )
        object.__setattr__(
            self,
            "process_instance_id",
            str(self.process_instance_id).strip(),
        )
        object.__setattr__(
            self,
            "emitted_at",
            require_utc(self.emitted_at, field_name="emitted_at"),
        )
        if not self.process_instance_id:
            raise ValueError("provenance process_instance_id is required")
        if not isinstance(self.source_record_refs, (list, tuple)):
            raise ValueError("provenance source_record_refs must be an array")
        refs = tuple(self.source_record_refs)
        if not refs:
            raise ValueError("provenance source_record_refs is required")
        if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise ValueError(
                "provenance source_record_refs entries must be non-empty strings"
            )
        object.__setattr__(self, "source_record_refs", refs)

    def semantic_identity(self) -> dict[str, Any]:
        data = {
            "producing_component": self.producing_component,
            "source_record_refs": list(self.source_record_refs),
            "schema_version": self.schema_version,
        }
        return {
            key: value
            for key, value in data.items()
            if value not in (None, "", (), [])
        }

    def identity_hash(self) -> str:
        return stable_hash("DI-PROV", self.semantic_identity())


@dataclass(frozen=True)
class DecisionContext:
    context_id: str
    candidate_id: str
    episode_id: str
    evaluation_id: str
    instrument_version: str
    snapshot_id: str
    snapshot_hash: str
    evaluation_time: datetime
    evidence_cutoff: datetime
    consumed_input_watermark: ConsumedInputWatermark | Mapping[str, int] | Any
    feature_version: str
    policy_version: str
    detector_version: str
    forecast_version: str
    candidate_set_ref: str
    portfolio_version_ref: str | None
    environment: str
    eligibility: bool
    missingness: Mapping[str, Any]
    source_availability_times: Mapping[str, Any]
    evidence_eligibility_manifest: Mapping[str, Any]
    provenance: Provenance
    schema_version: int = 1
    supersedes_id: str | None = None
    supersession_reason: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "context_id",
            "candidate_id",
            "episode_id",
            "evaluation_id",
            "instrument_version",
            "snapshot_id",
            "snapshot_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                str(getattr(self, field_name)).strip(),
            )

        object.__setattr__(
            self,
            "evaluation_time",
            require_utc(self.evaluation_time, field_name="evaluation_time"),
        )
        object.__setattr__(
            self,
            "evidence_cutoff",
            require_utc(self.evidence_cutoff, field_name="evidence_cutoff"),
        )
        if self.evidence_cutoff > self.evaluation_time:
            raise ValueError("evidence_cutoff cannot be after evaluation_time")

        object.__setattr__(
            self,
            "consumed_input_watermark",
            _coerce_consumed_input_watermark(self.consumed_input_watermark),
        )
        object.__setattr__(
            self,
            "evidence_eligibility_manifest",
            _normalize_evidence_manifest(
                self.evidence_eligibility_manifest,
                evidence_cutoff=self.evidence_cutoff,
            ),
        )
        object.__setattr__(
            self,
            "source_availability_times",
            _normalize_source_availability(
                self.source_availability_times,
                evidence_cutoff=self.evidence_cutoff,
            ),
        )
        object.__setattr__(
            self,
            "missingness",
            _freeze_nested(dict(self.missingness)),
        )
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported Decision Intelligence schema_version")

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "evaluation_id": self.evaluation_id,
            "instrument_version": self.instrument_version,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "evaluation_time": _isoformat_z(self.evaluation_time),
            "evidence_cutoff": _isoformat_z(self.evidence_cutoff),
            "consumed_input_watermark": self.consumed_input_watermark.to_dict(),
            "feature_version": self.feature_version,
            "policy_version": self.policy_version,
            "detector_version": self.detector_version,
            "forecast_version": self.forecast_version,
            "candidate_set_ref": self.candidate_set_ref,
            "portfolio_version_ref": self.portfolio_version_ref,
            "environment": self.environment,
            "eligibility": self.eligibility,
            "missingness": dict(self.missingness),
            "source_availability_times": canonicalize_nested(
                self.source_availability_times
            ),
            "evidence_eligibility_manifest": canonicalize_nested(
                self.evidence_eligibility_manifest
            ),
            "schema_version": self.schema_version,
            "supersedes_id": self.supersedes_id,
            "supersession_reason": self.supersession_reason,
            "provenance": {
                "producing_component": self.provenance.producing_component,
                "artifact_or_build_id": self.provenance.artifact_or_build_id,
                "process_instance_id": self.provenance.process_instance_id,
                "emitted_at": _isoformat_z(self.provenance.emitted_at),
                "source_record_refs": list(self.provenance.source_record_refs),
                "schema_version": self.provenance.schema_version,
            },
        }

    def validate_evidence_refs(
        self, evidence_refs: tuple[str, ...] | list[str]
    ) -> None:
        manifest = dict(self.evidence_eligibility_manifest)
        for evidence_ref in evidence_refs:
            if evidence_ref not in manifest:
                raise ValueError(
                    "evidence reference is not in frozen manifest: "
                    f"{evidence_ref}"
                )
            available_at = manifest[evidence_ref].get("available_at")
            if available_at is None:
                continue
            available_at = _parse_datetime(
                available_at,
                field_name="evidence.available_at",
            )
            if available_at > self.evidence_cutoff:
                raise ValueError(
                    "evidence reference is unavailable at evidence_cutoff"
                )


#: Decision context schema versions this contract defines. Version 1 is the
#: original committee-oriented context and remains the backward-compatibility
#: authority: its fields, validation, serialization and identity output are
#: frozen and must not change. Version 2 describes the point-in-time context
#: that the real production qualification path can truthfully produce for Paper
#: v2 execution lineage.
DECISION_CONTEXT_SCHEMA_VERSION = 1
DECISION_CONTEXT_SCHEMA_VERSION_V2 = 2

#: Hash domain for the schema-v2 context identity. Deliberately distinct from the
#: v1 ``DI-CONTEXT`` domain so a v1 and a v2 context that happen to share
#: candidate/episode/snapshot facts can never collide.
DECISION_CONTEXT_V2_IDENTITY_DOMAIN = "DI-CONTEXT-V2"


@dataclass(frozen=True)
class DecisionContextV2:
    """Point-in-time context from the real production qualification path.

    This is deliberately **not** a DI committee context. It records only facts the
    synchronous production scan actually produces and that materially protect
    point-in-time correctness, auditability and Paper-v2 execution lineage.

    Fields that describe concepts this runtime does not have - the committee's
    frozen evidence manifest, source-availability map and missingness map, an
    ``evaluation_id``, a feature-generation version, a detector version, a
    forecast/model version, a candidate-set reference and a consumed canonical
    input watermark - are intentionally absent rather than carried as placeholders.
    A later committee path that requires that metadata must fail closed on this
    context instead of having it fabricated here.
    """

    context_id: str
    candidate_id: str
    episode_id: str
    instrument_version: str
    snapshot_id: str
    snapshot_hash: str
    evaluation_time: datetime
    evidence_cutoff: datetime
    policy_version: str
    policy_fingerprint: str
    environment: str
    eligibility: bool
    provenance: Provenance
    schema_version: int = DECISION_CONTEXT_SCHEMA_VERSION_V2
    supersedes_id: str | None = None
    supersession_reason: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "context_id",
            "candidate_id",
            "episode_id",
            "instrument_version",
            "snapshot_id",
            "snapshot_hash",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)

        for field_name in ("policy_version", "policy_fingerprint", "environment"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)

        if not isinstance(self.eligibility, bool):
            raise ValueError("eligibility must be a boolean")

        object.__setattr__(
            self,
            "evaluation_time",
            require_utc(self.evaluation_time, field_name="evaluation_time"),
        )
        object.__setattr__(
            self,
            "evidence_cutoff",
            require_utc(self.evidence_cutoff, field_name="evidence_cutoff"),
        )
        if self.evidence_cutoff > self.evaluation_time:
            raise ValueError("evidence_cutoff cannot be after evaluation_time")

        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance contract")

        if (
            type(self.schema_version) is not int
            or self.schema_version != DECISION_CONTEXT_SCHEMA_VERSION_V2
        ):
            raise ValueError("unsupported Decision Intelligence context schema_version")

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "instrument_version": self.instrument_version,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "evaluation_time": _isoformat_z(self.evaluation_time),
            "evidence_cutoff": _isoformat_z(self.evidence_cutoff),
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "environment": self.environment,
            "eligibility": self.eligibility,
            "schema_version": self.schema_version,
            "supersedes_id": self.supersedes_id,
            "supersession_reason": self.supersession_reason,
            "provenance": {
                "producing_component": self.provenance.producing_component,
                "artifact_or_build_id": self.provenance.artifact_or_build_id,
                "process_instance_id": self.provenance.process_instance_id,
                "emitted_at": _isoformat_z(self.provenance.emitted_at),
                "source_record_refs": list(self.provenance.source_record_refs),
                "schema_version": self.provenance.schema_version,
            },
        }


__all__ = [
    "DECISION_CONTEXT_SCHEMA_VERSION",
    "DECISION_CONTEXT_SCHEMA_VERSION_V2",
    "DECISION_CONTEXT_V2_IDENTITY_DOMAIN",
    "DecisionContext",
    "DecisionContextV2",
    "Provenance",
]
