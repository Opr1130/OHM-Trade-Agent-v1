from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.decision_intelligence.serialization import canonicalize_nested, require_utc, stable_hash


def _freeze_nested(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_nested(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_nested(item) for item in value)
    return value


@dataclass(frozen=True)
class Provenance:
    producing_component: str
    artifact_or_build_id: str
    process_instance_id: str
    emitted_at: datetime
    source_record_refs: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "producing_component", str(self.producing_component).strip())
        object.__setattr__(self, "artifact_or_build_id", str(self.artifact_or_build_id).strip())
        if not self.producing_component or not self.artifact_or_build_id:
            raise ValueError("provenance component and artifact_or_build_id are required")
        object.__setattr__(self, "process_instance_id", str(self.process_instance_id).strip())
        object.__setattr__(self, "emitted_at", require_utc(self.emitted_at, field_name="emitted_at"))
        if not self.process_instance_id:
            raise ValueError("provenance process_instance_id is required")
        refs = tuple(str(ref) for ref in self.source_record_refs)
        if not refs:
            raise ValueError("provenance source_record_refs is required")
        object.__setattr__(self, "source_record_refs", refs)

    def semantic_identity(self) -> dict[str, Any]:
        data = {
            "producing_component": self.producing_component,
            "source_record_refs": list(self.source_record_refs),
            "schema_version": self.schema_version,
        }
        return {key: value for key, value in data.items() if value not in (None, "", (), [])}

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
        object.__setattr__(self, "context_id", str(self.context_id).strip())
        object.__setattr__(self, "candidate_id", str(self.candidate_id).strip())
        object.__setattr__(self, "episode_id", str(self.episode_id).strip())
        object.__setattr__(self, "evaluation_id", str(self.evaluation_id).strip())
        object.__setattr__(self, "instrument_version", str(self.instrument_version).strip())
        object.__setattr__(self, "snapshot_id", str(self.snapshot_id).strip())
        object.__setattr__(self, "snapshot_hash", str(self.snapshot_hash).strip())
        object.__setattr__(self, "evaluation_time", require_utc(self.evaluation_time, field_name="evaluation_time"))
        object.__setattr__(self, "evidence_cutoff", require_utc(self.evidence_cutoff, field_name="evidence_cutoff"))
        if self.evidence_cutoff > self.evaluation_time:
            raise ValueError("evidence_cutoff cannot be after evaluation_time")
        if isinstance(self.consumed_input_watermark, ConsumedInputWatermark):
            watermark = self.consumed_input_watermark
        else:
            if not isinstance(self.consumed_input_watermark, Mapping):
                raise ValueError("consumed_input_watermark must be a watermark object")
            try:
                watermark = ConsumedInputWatermark.from_dict(
                    self.consumed_input_watermark
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid consumed_input_watermark") from exc
        object.__setattr__(self, "consumed_input_watermark", watermark)

        normalized_manifest: dict[str, Any] = {}
        for evidence_id, entry in dict(self.evidence_eligibility_manifest).items():
            if not isinstance(entry, Mapping):
                raise ValueError("evidence manifest entries must be objects")
            item = dict(entry)
            available_at = item.get("available_at")
            if available_at is not None:
                if not isinstance(available_at, datetime):
                    available_at = datetime.fromisoformat(
                        str(available_at).replace("Z", "+00:00")
                    )
                available_at = require_utc(
                    available_at, field_name="evidence.available_at"
                )
                if available_at > self.evidence_cutoff:
                    raise ValueError(
                        "evidence_cutoff rejects evidence available after cutoff"
                    )
                item["available_at"] = available_at
            normalized_manifest[str(evidence_id)] = _freeze_nested(item)
        object.__setattr__(
            self,
            "evidence_eligibility_manifest",
            MappingProxyType(normalized_manifest),
        )

        normalized_availability: dict[str, Any] = {}
        for source, availability in dict(self.source_availability_times).items():
            if availability is not None:
                if not isinstance(availability, datetime):
                    availability = datetime.fromisoformat(
                        str(availability).replace("Z", "+00:00")
                    )
                availability = require_utc(
                    availability, field_name="source_availability_time"
                )
                if availability > self.evidence_cutoff:
                    raise ValueError(
                        "evidence_cutoff rejects evidence available after cutoff"
                    )
            normalized_availability[str(source)] = availability
        object.__setattr__(
            self,
            "source_availability_times",
            MappingProxyType(normalized_availability),
        )
        object.__setattr__(
            self,
            "missingness",
            _freeze_nested(dict(self.missingness)),
        )
        if self.schema_version != 1:
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
            "evaluation_time": self.evaluation_time.isoformat().replace("+00:00", "Z"),
            "evidence_cutoff": self.evidence_cutoff.isoformat().replace("+00:00", "Z"),
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
            "source_availability_times": canonicalize_nested(self.source_availability_times),
            "evidence_eligibility_manifest": canonicalize_nested(self.evidence_eligibility_manifest),
            "schema_version": self.schema_version,
            "supersedes_id": self.supersedes_id,
            "supersession_reason": self.supersession_reason,
            "provenance": {
                "producing_component": self.provenance.producing_component,
                "artifact_or_build_id": self.provenance.artifact_or_build_id,
                "process_instance_id": self.provenance.process_instance_id,
                "emitted_at": self.provenance.emitted_at.isoformat().replace("+00:00", "Z"),
                "source_record_refs": list(self.provenance.source_record_refs),
                "schema_version": self.provenance.schema_version,
            },
        }

    def validate_evidence_refs(self, evidence_refs: tuple[str, ...] | list[str]) -> None:
        manifest = dict(self.evidence_eligibility_manifest)
        for evidence_ref in evidence_refs:
            if evidence_ref not in manifest:
                raise ValueError(f"evidence reference is not in frozen manifest: {evidence_ref}")
            available_at = manifest[evidence_ref].get("available_at")
            if available_at is None:
                continue
            if not isinstance(available_at, datetime):
                available_at = datetime.fromisoformat(str(available_at).replace("Z", "+00:00"))
            available_at = require_utc(available_at, field_name="evidence.available_at")
            if available_at > self.evidence_cutoff:
                raise ValueError("evidence reference is unavailable at evidence_cutoff")


__all__ = ["DecisionContext", "Provenance"]
