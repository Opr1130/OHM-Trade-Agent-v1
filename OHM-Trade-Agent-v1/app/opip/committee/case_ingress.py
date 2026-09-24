"""Durable, fail-closed ingress for Committee SHADOW cases.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The scheduler only needs compact metadata, while :class:`CommitteeRunner` needs the
full sealed :class:`CommitteeCase`.  This module is the bridge between those two
contracts.  It reconstructs a case from durable JSON evidence and independently
recomputes the content-derived snapshot identity before exposing scheduler metadata.

Nothing here calls a provider, reads a credential, schedules work, or grants trading
authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.opip.committee.contracts import (
    CanonicalDecisionBinding,
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    EvidenceItem,
    EvidenceSnapshot,
    ProviderFamily,
)
from app.opip.committee.scheduler import CommittedEvidenceItem
from app.opip.decision_intelligence.identity import Provenance
from app.opip.decision_intelligence.serialization import require_utc

CASE_INGRESS_SCHEMA_VERSION = 1


class CaseIngressError(ValueError):
    """Durable case evidence cannot be reconstructed without ambiguity."""


def _required_str(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseIngressError(f"{field} is required")
    return value.strip()


def _optional_str(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _required_str(value, field=field)


def _datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CaseIngressError(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaseIngressError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    try:
        return require_utc(parsed, field_name=field)
    except ValueError as exc:
        raise CaseIngressError(str(exc)) from exc


def _optional_datetime(value: object, *, field: str) -> datetime | None:
    return None if value is None else _datetime(value, field=field)


def _optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise CaseIngressError(f"{field} must be a non-negative integer or null")
    return value


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CaseIngressError(f"{field} must be an object")
    return value


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise CaseIngressError(f"{field} must be an array")
    result = tuple(_required_str(item, field=f"{field}[]") for item in value)
    if not result:
        raise CaseIngressError(f"{field} must not be empty")
    return result


def _provider_families(policy_raw: Mapping[str, Any]) -> tuple[ProviderFamily, ...]:
    try:
        return tuple(
            ProviderFamily(item)
            for item in _string_tuple(
                policy_raw.get("seated_providers"),
                field="case.policy.seated_providers",
            )
        )
    except ValueError as exc:
        raise CaseIngressError(str(exc)) from exc


def _policy_from_mapping(policy_raw: Mapping[str, Any]) -> CommitteePolicy:
    return CommitteePolicy(
        policy_version=_required_str(
            policy_raw.get("policy_version"), field="case.policy.policy_version"
        ),
        seated_providers=_provider_families(policy_raw),
        prompt_template_id=_required_str(
            policy_raw.get("prompt_template_id"),
            field="case.policy.prompt_template_id",
        ),
        prompt_version=_required_str(
            policy_raw.get("prompt_version"), field="case.policy.prompt_version"
        ),
        max_attempts_per_seat=policy_raw.get("max_attempts_per_seat", 1),
        max_estimated_cost_microunits=_optional_int(
            policy_raw.get("max_estimated_cost_microunits"),
            field="case.policy.max_estimated_cost_microunits",
        ),
    )


def _items_from_snapshot(snapshot_raw: Mapping[str, Any]) -> tuple[EvidenceItem, ...]:
    raw_items = snapshot_raw.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise CaseIngressError("case.snapshot.items must be a non-empty array")
    items: list[EvidenceItem] = []
    for index, item_raw in enumerate(raw_items):
        item = _mapping(item_raw, field=f"case.snapshot.items[{index}]")
        payload = _mapping(
            item.get("payload"), field=f"case.snapshot.items[{index}].payload"
        )
        items.append(
            EvidenceItem(
                evidence_id=_required_str(
                    item.get("evidence_id"),
                    field=f"case.snapshot.items[{index}].evidence_id",
                ),
                source_id=_required_str(
                    item.get("source_id"),
                    field=f"case.snapshot.items[{index}].source_id",
                ),
                available_at=_datetime(
                    item.get("available_at"),
                    field=f"case.snapshot.items[{index}].available_at",
                ),
                payload=dict(payload),
            )
        )
    return tuple(items)


def _snapshot_from_mapping(
    snapshot_raw: Mapping[str, Any], *, case_type: CaseType
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        case_id=_required_str(snapshot_raw.get("case_id"), field="case.snapshot.case_id"),
        case_type=case_type,
        evidence_cutoff_at=_datetime(
            snapshot_raw.get("evidence_cutoff_at"),
            field="case.snapshot.evidence_cutoff_at",
        ),
        assembled_at=_datetime(
            snapshot_raw.get("assembled_at"), field="case.snapshot.assembled_at"
        ),
        items=_items_from_snapshot(snapshot_raw),
        source_refs=_string_tuple(
            snapshot_raw.get("source_refs"), field="case.snapshot.source_refs"
        ),
        committee_policy_version=_required_str(
            snapshot_raw.get("committee_policy_version"),
            field="case.snapshot.committee_policy_version",
        ),
        prompt_template_id=_required_str(
            snapshot_raw.get("prompt_template_id"),
            field="case.snapshot.prompt_template_id",
        ),
        prompt_version=_required_str(
            snapshot_raw.get("prompt_version"),
            field="case.snapshot.prompt_version",
        ),
        instrument_id=_optional_str(
            snapshot_raw.get("instrument_id"), field="case.snapshot.instrument_id"
        ),
        strategy_context_id=_optional_str(
            snapshot_raw.get("strategy_context_id"),
            field="case.snapshot.strategy_context_id",
        ),
    )


def _binding_from_mapping(raw: object) -> CanonicalDecisionBinding | None:
    if raw is None:
        return None
    binding = _mapping(raw, field="case.canonical_binding")
    return CanonicalDecisionBinding(
        decision_id=_optional_str(
            binding.get("decision_id"), field="case.canonical_binding.decision_id"
        ),
        episode_id=_optional_str(
            binding.get("episode_id"), field="case.canonical_binding.episode_id"
        ),
    )


def _provenance_from_mapping(raw: Mapping[str, Any]) -> Provenance:
    return Provenance(
        producing_component=_required_str(
            raw.get("producing_component"),
            field="case.provenance.producing_component",
        ),
        artifact_or_build_id=_required_str(
            raw.get("artifact_or_build_id"),
            field="case.provenance.artifact_or_build_id",
        ),
        process_instance_id=_required_str(
            raw.get("process_instance_id"),
            field="case.provenance.process_instance_id",
        ),
        emitted_at=_datetime(raw.get("emitted_at"), field="case.provenance.emitted_at"),
        source_record_refs=_string_tuple(
            raw.get("source_record_refs"), field="case.provenance.source_record_refs"
        ),
    )


def _case_from_mapping(raw: Mapping[str, Any]) -> CommitteeCase:
    snapshot_raw = _mapping(raw.get("snapshot"), field="case.snapshot")
    policy_raw = _mapping(raw.get("policy"), field="case.policy")
    provenance_raw = _mapping(raw.get("provenance"), field="case.provenance")
    try:
        case_type = CaseType(
            _required_str(raw.get("case_type"), field="case.case_type")
        )
        policy = _policy_from_mapping(policy_raw)
        snapshot = _snapshot_from_mapping(snapshot_raw, case_type=case_type)
        return CommitteeCase(
            case_id=_required_str(raw.get("case_id"), field="case.case_id"),
            case_type=case_type,
            snapshot=snapshot,
            policy=policy,
            created_at=_datetime(raw.get("created_at"), field="case.created_at"),
            provenance=_provenance_from_mapping(provenance_raw),
            instrument_id=_optional_str(
                raw.get("instrument_id"), field="case.instrument_id"
            ),
            strategy_context_id=_optional_str(
                raw.get("strategy_context_id"), field="case.strategy_context_id"
            ),
            canonical_binding=_binding_from_mapping(raw.get("canonical_binding")),
        )
    except ValueError as exc:
        raise CaseIngressError(str(exc)) from exc


@dataclass(frozen=True)
class CommittedCaseEnvelope:
    """One sealed case plus the compact scheduler metadata derived from it."""

    evidence_id: str
    case: CommitteeCase
    committed: bool
    sealed: bool
    available_at: datetime
    expires_at: datetime | None
    estimated_cost_microunits: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.committed, bool) or not isinstance(self.sealed, bool):
            raise CaseIngressError("committed and sealed must be booleans")
        object.__setattr__(
            self, "evidence_id", _required_str(self.evidence_id, field="evidence_id")
        )
        object.__setattr__(
            self,
            "available_at",
            require_utc(self.available_at, field_name="available_at"),
        )
        if self.expires_at is not None:
            object.__setattr__(
                self,
                "expires_at",
                require_utc(self.expires_at, field_name="expires_at"),
            )
        if self.estimated_cost_microunits is not None and (
            type(self.estimated_cost_microunits) is not int
            or self.estimated_cost_microunits < 0
        ):
            raise CaseIngressError(
                "estimated_cost_microunits must be a non-negative integer or null"
            )
        if self.available_at > self.case.snapshot.evidence_cutoff_at:
            raise CaseIngressError(
                "available_at cannot post-date the sealed evidence cutoff"
            )

    @property
    def scheduler_item(self) -> CommittedEvidenceItem:
        """Compact scheduling view, derived rather than trusted from JSON."""
        return CommittedEvidenceItem(
            evidence_id=self.evidence_id,
            case_id=self.case.case_id,
            evidence_snapshot_hash=self.case.snapshot.snapshot_hash,
            committee_policy_version=self.case.policy.policy_version,
            committed=self.committed,
            sealed=self.sealed,
            available_at=self.available_at,
            evidence_cutoff_at=self.case.snapshot.evidence_cutoff_at,
            expires_at=self.expires_at,
            estimated_cost_microunits=self.estimated_cost_microunits,
        )


def envelope_from_dict(row: Mapping[str, Any]) -> CommittedCaseEnvelope:
    """Reconstruct one envelope and reject identity metadata that does not match."""
    if (
        type(row.get("schema_version")) is not int
        or row.get("schema_version") != CASE_INGRESS_SCHEMA_VERSION
    ):
        raise CaseIngressError("unsupported case-ingress schema_version")
    case = _case_from_mapping(_mapping(row.get("case"), field="case"))

    expected_snapshot_hash = _required_str(
        row.get("evidence_snapshot_hash"), field="evidence_snapshot_hash"
    )
    if expected_snapshot_hash != case.snapshot.snapshot_hash:
        raise CaseIngressError(
            "evidence_snapshot_hash does not match the reconstructed sealed snapshot"
        )

    expected_policy = _required_str(
        row.get("committee_policy_version"), field="committee_policy_version"
    )
    if expected_policy != case.policy.policy_version:
        raise CaseIngressError(
            "committee_policy_version does not match the reconstructed case policy"
        )

    return CommittedCaseEnvelope(
        evidence_id=_required_str(row.get("evidence_id"), field="evidence_id"),
        case=case,
        committed=row.get("committed"),
        sealed=row.get("sealed"),
        available_at=_datetime(row.get("available_at"), field="available_at"),
        expires_at=_optional_datetime(row.get("expires_at"), field="expires_at"),
        estimated_cost_microunits=_optional_int(
            row.get("estimated_cost_microunits"),
            field="estimated_cost_microunits",
        ),
    )


@dataclass(frozen=True)
class CaseIngressPopulation:
    """Validated envelopes plus an exact scheduler-item-to-case binding."""

    envelopes: tuple[CommittedCaseEnvelope, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for envelope in self.envelopes:
            if not isinstance(envelope, CommittedCaseEnvelope):
                raise CaseIngressError(
                    "case ingress population requires CommittedCaseEnvelope values"
                )
            if envelope.evidence_id in seen:
                raise CaseIngressError(
                    f"duplicate case-ingress evidence_id {envelope.evidence_id!r}"
                )
            seen.add(envelope.evidence_id)

    @property
    def scheduler_items(self) -> tuple[CommittedEvidenceItem, ...]:
        """The compact scheduling population derived from validated cases."""
        return tuple(envelope.scheduler_item for envelope in self.envelopes)

    def case_for(self, item: CommittedEvidenceItem) -> CommitteeCase:
        """Return the exact reconstructed case for a scheduler item or fail closed."""
        if not isinstance(item, CommittedEvidenceItem):
            raise CaseIngressError("case lookup requires a CommittedEvidenceItem")
        for envelope in self.envelopes:
            if envelope.evidence_id != item.evidence_id:
                continue
            expected = envelope.scheduler_item
            if item != expected:
                raise CaseIngressError(
                    "scheduler item does not match its validated case-ingress envelope"
                )
            return envelope.case
        raise CaseIngressError(
            f"no validated CommitteeCase exists for evidence_id {item.evidence_id!r}"
        )


def load_case_envelopes(path: Path) -> tuple[CommittedCaseEnvelope, ...]:
    """Load a JSONL ingress file; malformed rows fail the entire population closed."""
    if not path.exists():
        return ()
    envelopes: list[CommittedCaseEnvelope] = []
    seen_evidence: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except ValueError as exc:
            raise CaseIngressError(f"{path.name} line {number} is not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise CaseIngressError(f"{path.name} line {number} is not a JSON object")
        try:
            envelope = envelope_from_dict(decoded)
        except (ValueError, TypeError, KeyError) as exc:
            raise CaseIngressError(
                f"{path.name} line {number} is not a valid committed case: {exc}"
            ) from exc
        if envelope.evidence_id in seen_evidence:
            raise CaseIngressError(
                f"{path.name} line {number} duplicates evidence_id {envelope.evidence_id!r}"
            )
        seen_evidence.add(envelope.evidence_id)
        envelopes.append(envelope)
    return tuple(envelopes)


__all__ = [
    "CASE_INGRESS_SCHEMA_VERSION",
    "CaseIngressError",
    "CaseIngressPopulation",
    "CommittedCaseEnvelope",
    "envelope_from_dict",
    "load_case_envelopes",
]
