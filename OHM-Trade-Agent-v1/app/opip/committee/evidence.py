"""Evidence snapshot assembly with an enforced point-in-time cutoff.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Every seated member must receive the same logically equivalent evidence, and no
fact that became available after the cutoff may enter a prospective opinion.
The cutoff is enforced here, at assembly, so a look-ahead row cannot be sealed
into a snapshot and later discovered during evaluation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.opip.committee.contracts import (
    CaseType,
    EvidenceItem,
    EvidenceSnapshot,
    freeze_nested,
)
from app.opip.decision_intelligence.serialization import require_utc

#: Maximum payload keys per evidence item, to bound what reaches a model.
MAX_PAYLOAD_FIELDS = 64


class EvidencePolicyError(ValueError):
    """A snapshot could not be assembled without violating evidence policy."""


def validate_evidence_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and freeze one evidence item's payload.

    Binary floats are rejected: canonical committee identity is built from
    normalized data that forbids them, and a float would make a hash depend on
    platform formatting. Metrics travel as integers or decimal strings, which
    matches the repository's existing numeric convention.
    """
    if not isinstance(payload, Mapping):
        raise EvidencePolicyError("evidence payload must be a mapping")
    if len(payload) > MAX_PAYLOAD_FIELDS:
        raise EvidencePolicyError(
            f"evidence payload exceeds {MAX_PAYLOAD_FIELDS} fields"
        )
    validated: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise EvidencePolicyError("evidence payload keys must be non-empty strings")
        validated[key] = _validate_scalar(value, path=key)
    return freeze_nested(validated)


def _validate_scalar(value: Any, *, path: str) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        raise EvidencePolicyError(
            f"binary float at {path} is not allowed; use an integer or decimal string"
        )
    if isinstance(value, Mapping):
        return {str(key): _validate_scalar(item, path=f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [
            _validate_scalar(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise EvidencePolicyError(f"unsupported evidence type at {path}: {type(value).__name__}")


def build_evidence_item(
    *,
    evidence_id: str,
    source_id: str,
    available_at: datetime,
    payload: Mapping[str, Any],
    evidence_cutoff_at: datetime,
) -> EvidenceItem:
    """Build one item, rejecting anything that post-dates the cutoff."""
    cutoff = require_utc(evidence_cutoff_at, field_name="evidence_cutoff_at")
    available = require_utc(available_at, field_name="available_at")
    if available > cutoff:
        raise EvidencePolicyError(
            f"evidence {evidence_id!r} became available after the cutoff; "
            "a prospective snapshot cannot contain it"
        )
    return EvidenceItem(
        evidence_id=evidence_id,
        source_id=source_id,
        available_at=available,
        payload=validate_evidence_payload(payload),
    )


def build_evidence_snapshot(
    *,
    case_id: str,
    case_type: CaseType,
    evidence_cutoff_at: datetime,
    assembled_at: datetime,
    items: Iterable[EvidenceItem],
    source_refs: Iterable[str],
    committee_policy_version: str,
    prompt_template_id: str,
    prompt_version: str,
    instrument_id: str | None = None,
    strategy_context_id: str | None = None,
) -> EvidenceSnapshot:
    """Assemble the sealed snapshot every seat will receive."""
    cutoff = require_utc(evidence_cutoff_at, field_name="evidence_cutoff_at")
    ordered = tuple(sorted(items, key=lambda item: item.evidence_id))
    if not ordered:
        raise EvidencePolicyError("a committee case requires at least one evidence item")
    seen: set[str] = set()
    for item in ordered:
        if item.evidence_id in seen:
            raise EvidencePolicyError(f"duplicate evidence_id: {item.evidence_id}")
        seen.add(item.evidence_id)
        if item.available_at > cutoff:
            raise EvidencePolicyError(
                f"evidence {item.evidence_id!r} became available after the cutoff"
            )
    return EvidenceSnapshot(
        case_id=case_id,
        case_type=case_type,
        evidence_cutoff_at=cutoff,
        assembled_at=require_utc(assembled_at, field_name="assembled_at"),
        items=ordered,
        source_refs=tuple(source_refs),
        committee_policy_version=committee_policy_version,
        prompt_template_id=prompt_template_id,
        prompt_version=prompt_version,
        instrument_id=instrument_id,
        strategy_context_id=strategy_context_id,
    )


__all__ = [
    "MAX_PAYLOAD_FIELDS",
    "EvidencePolicyError",
    "build_evidence_item",
    "build_evidence_snapshot",
    "validate_evidence_payload",
]
