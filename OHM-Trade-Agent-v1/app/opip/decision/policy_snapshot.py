"""Frozen gate-policy evidence for O'Pip Sequence 5 BUILD 5.1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from app.opip.decision.serialization import canonical_data, canonical_serialize
from app.opip.decision.thresholds import gate_policy_constants
from app.opip.decision.versioning import GATE_POLICY_VERSION, gate_policy_fingerprint


POLICY_SNAPSHOT_SCHEMA_VERSION = 1


def _freeze(value: Any) -> Any:
    value = canonical_data(value)
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        if all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            for item in value
        ):
            return {item[0]: _thaw(item[1]) for item in value}
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class GatePolicySnapshot:
    """Exact deterministic threshold inputs observed at T0."""

    policy_version: str
    policy_fingerprint: str
    thresholds_ordered: tuple[tuple[str, Any], ...]
    requires_streaming_evidence: bool = False
    required_evidence_refs: tuple[str, ...] = ()
    schema_version: int = POLICY_SNAPSHOT_SCHEMA_VERSION

    @classmethod
    def capture_current(
        cls,
        *,
        requires_streaming_evidence: bool = False,
        required_evidence_refs: tuple[str, ...] = (),
    ) -> "GatePolicySnapshot":
        ordered = tuple(
            (key, _freeze(value))
            for key, value in sorted(gate_policy_constants().items())
        )
        required = tuple(
            sorted({str(item) for item in required_evidence_refs if str(item)})
        )
        if (
            requires_streaming_evidence
            and "streaming_snapshot_ref" not in required
        ):
            required = tuple(sorted((*required, "streaming_snapshot_ref")))
        result = cls(
            policy_version=GATE_POLICY_VERSION,
            policy_fingerprint=gate_policy_fingerprint(),
            thresholds_ordered=ordered,
            requires_streaming_evidence=requires_streaming_evidence,
            required_evidence_refs=required,
        )
        result.validate_integrity()
        return result

    def thresholds_dict(self) -> dict[str, Any]:
        return {key: _thaw(value) for key, value in self.thresholds_ordered}

    def calculated_fingerprint(self) -> str:
        payload = json.dumps(
            self.thresholds_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return "GPF:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def validate_integrity(self) -> None:
        if self.schema_version != POLICY_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported policy snapshot schema")
        if self.policy_fingerprint != self.calculated_fingerprint():
            raise ValueError("frozen thresholds do not match policy fingerprint")
        if (
            self.requires_streaming_evidence
            and "streaming_snapshot_ref" not in self.required_evidence_refs
        ):
            raise ValueError("streaming-required policy lacks streaming reference")

    @property
    def snapshot_hash(self) -> str:
        self.validate_integrity()
        return "POL:" + hashlib.sha256(
            canonical_serialize(self.as_dict()).encode("utf-8")
        ).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "thresholds": self.thresholds_dict(),
            "requires_streaming_evidence": self.requires_streaming_evidence,
            "required_evidence_refs": list(self.required_evidence_refs),
        }
