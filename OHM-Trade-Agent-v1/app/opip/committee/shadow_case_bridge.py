"""Read-only bridge from sealed producer envelopes to CommitteeCase.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The committee runtime must not invent a second evidence model. A producer on the
read-only learning plane supplies a complete JSONL envelope containing the facts
needed to reconstruct the existing CommitteeCase, CommitteePolicy, EvidenceSnapshot,
and Provenance contracts. This module reconstructs those contracts and verifies
content-derived identities before a case can be handed to any executor.

No credential, network, scheduler, trading, or deployment behavior exists here.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.opip.committee.contracts import (
    CanonicalDecisionBinding,
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    ProviderFamily,
)
from app.opip.committee.evidence import build_evidence_item, build_evidence_snapshot
from app.opip.decision_intelligence.identity import Provenance
from app.opip.decision_intelligence.serialization import require_utc

CASE_ENVELOPE_SCHEMA_VERSION = 1


class ShadowCaseEnvelopeError(ValueError):
    """A producer envelope cannot be admitted as a governed committee case."""


def _mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowCaseEnvelopeError(f"{field_name} must be a JSON object")
    return value


def _sequence(value: object, *, field_name: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ShadowCaseEnvelopeError(f"{field_name} must be a JSON array")
    return value


def _string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowCaseEnvelopeError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name=field_name)


def _datetime(value: object, *, field_name: str) -> datetime:
    text = _string(value, field_name=field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ShadowCaseEnvelopeError(f"{field_name} must be an ISO-8601 timestamp") from exc
    try:
        return require_utc(parsed, field_name=field_name)
    except ValueError as exc:
        raise ShadowCaseEnvelopeError(str(exc)) from exc


def _exact_int(
    value: object,
    *,
    field_name: str,
    minimum: int | None = None,
) -> int:
    if type(value) is not int:
        raise ShadowCaseEnvelopeError(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        raise ShadowCaseEnvelopeError(f"{field_name} must be >= {minimum}")
    return value


def _optional_exact_int(
    value: object,
    *,
    field_name: str,
    minimum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _exact_int(value, field_name=field_name, minimum=minimum)


def _binding(value: object) -> CanonicalDecisionBinding | None:
    if value is None:
        return None
    row = _mapping(value, field_name="canonical_binding")
    try:
        return CanonicalDecisionBinding(
            decision_id=_optional_string(row.get("decision_id"), field_name="decision_id"),
            episode_id=_optional_string(row.get("episode_id"), field_name="episode_id"),
        )
    except ValueError as exc:
        raise ShadowCaseEnvelopeError(str(exc)) from exc


def _provenance(value: object) -> Provenance:
    row = _mapping(value, field_name="provenance")
    refs = _sequence(row.get("source_record_refs"), field_name="provenance.source_record_refs")
    try:
        return Provenance(
            producing_component=_string(
                row.get("producing_component"),
                field_name="provenance.producing_component",
            ),
            artifact_or_build_id=_string(
                row.get("artifact_or_build_id"),
                field_name="provenance.artifact_or_build_id",
            ),
            process_instance_id=_string(
                row.get("process_instance_id"),
                field_name="provenance.process_instance_id",
            ),
            emitted_at=_datetime(row.get("emitted_at"), field_name="provenance.emitted_at"),
            source_record_refs=tuple(
                _string(item, field_name="provenance.source_record_refs[]") for item in refs
            ),
        )
    except ValueError as exc:
        raise ShadowCaseEnvelopeError(str(exc)) from exc


def _policy(value: object) -> CommitteePolicy:
    row = _mapping(value, field_name="policy")
    providers = _sequence(row.get("seated_providers"), field_name="policy.seated_providers")
    try:
        families = tuple(
            ProviderFamily(_string(item, field_name="policy.seated_providers[]"))
            for item in providers
        )
    except ValueError as exc:
        raise ShadowCaseEnvelopeError("policy contains an unsupported provider family") from exc
    try:
        return CommitteePolicy(
            policy_version=_string(row.get("policy_version"), field_name="policy.policy_version"),
            seated_providers=families,
            prompt_template_id=_string(
                row.get("prompt_template_id"),
                field_name="policy.prompt_template_id",
            ),
            prompt_version=_string(row.get("prompt_version"), field_name="policy.prompt_version"),
            max_attempts_per_seat=_exact_int(
                row.get("max_attempts_per_seat"),
                field_name="policy.max_attempts_per_seat",
                minimum=1,
            ),
            max_estimated_cost_microunits=_optional_exact_int(
                row.get("max_estimated_cost_microunits"),
                field_name="policy.max_estimated_cost_microunits",
                minimum=0,
            ),
        )
    except ValueError as exc:
        raise ShadowCaseEnvelopeError(str(exc)) from exc


def case_from_envelope(value: Mapping[str, Any]) -> CommitteeCase:
    """Reconstruct one governed case and verify all supplied identities."""
    row = _mapping(value, field_name="case envelope")
    schema_version = _exact_int(row.get("schema_version"), field_name="schema_version")
    if schema_version != CASE_ENVELOPE_SCHEMA_VERSION:
        raise ShadowCaseEnvelopeError(
            f"unsupported case-envelope schema_version {schema_version}"
        )

    case_id = _string(row.get("case_id"), field_name="case_id")
    try:
        case_type = CaseType(_string(row.get("case_type"), field_name="case_type"))
    except ValueError as exc:
        raise ShadowCaseEnvelopeError("case_type is unsupported") from exc

    policy = _policy(row.get("policy"))
    evidence_cutoff_at = _datetime(
        row.get("evidence_cutoff_at"), field_name="evidence_cutoff_at"
    )
    assembled_at = _datetime(row.get("assembled_at"), field_name="assembled_at")
    instrument_id = _optional_string(row.get("instrument_id"), field_name="instrument_id")
    strategy_context_id = _optional_string(
        row.get("strategy_context_id"), field_name="strategy_context_id"
    )

    evidence_rows = _sequence(row.get("evidence_items"), field_name="evidence_items")
    if not evidence_rows:
        raise ShadowCaseEnvelopeError("evidence_items must not be empty")
    items = []
    for index, raw_item in enumerate(evidence_rows):
        item = _mapping(raw_item, field_name=f"evidence_items[{index}]")
        try:
            items.append(
                build_evidence_item(
                    evidence_id=_string(
                        item.get("evidence_id"),
                        field_name=f"evidence_items[{index}].evidence_id",
                    ),
                    source_id=_string(
                        item.get("source_id"),
                        field_name=f"evidence_items[{index}].source_id",
                    ),
                    available_at=_datetime(
                        item.get("available_at"),
                        field_name=f"evidence_items[{index}].available_at",
                    ),
                    payload=_mapping(
                        item.get("payload"),
                        field_name=f"evidence_items[{index}].payload",
                    ),
                    evidence_cutoff_at=evidence_cutoff_at,
                )
            )
        except ValueError as exc:
            raise ShadowCaseEnvelopeError(str(exc)) from exc

    source_refs_raw = _sequence(row.get("source_refs"), field_name="source_refs")
    source_refs = tuple(
        _string(item, field_name="source_refs[]") for item in source_refs_raw
    )
    try:
        snapshot = build_evidence_snapshot(
            case_id=case_id,
            case_type=case_type,
            evidence_cutoff_at=evidence_cutoff_at,
            assembled_at=assembled_at,
            items=items,
            source_refs=source_refs,
            committee_policy_version=policy.policy_version,
            prompt_template_id=policy.prompt_template_id,
            prompt_version=policy.prompt_version,
            instrument_id=instrument_id,
            strategy_context_id=strategy_context_id,
        )
        case = CommitteeCase(
            case_id=case_id,
            case_type=case_type,
            snapshot=snapshot,
            policy=policy,
            created_at=_datetime(row.get("created_at"), field_name="created_at"),
            provenance=_provenance(row.get("provenance")),
            instrument_id=instrument_id,
            strategy_context_id=strategy_context_id,
            canonical_binding=_binding(row.get("canonical_binding")),
        )
    except ValueError as exc:
        raise ShadowCaseEnvelopeError(str(exc)) from exc

    expected_snapshot_hash = _string(
        row.get("expected_snapshot_hash"), field_name="expected_snapshot_hash"
    )
    expected_policy_hash = _string(
        row.get("expected_policy_hash"), field_name="expected_policy_hash"
    )
    expected_case_hash = _string(
        row.get("expected_case_hash"), field_name="expected_case_hash"
    )
    if snapshot.snapshot_hash != expected_snapshot_hash:
        raise ShadowCaseEnvelopeError("snapshot identity does not match envelope")
    if policy.policy_hash != expected_policy_hash:
        raise ShadowCaseEnvelopeError("policy identity does not match envelope")
    if case.case_hash != expected_case_hash:
        raise ShadowCaseEnvelopeError("case identity does not match envelope")
    return case


def load_case_envelopes(path: Path) -> tuple[CommitteeCase, ...]:
    """Load a complete JSONL input stream, failing closed on any malformed row."""
    source = Path(path)
    if not source.exists():
        return ()
    cases: list[CommitteeCase] = []
    seen_case_ids: set[str] = set()
    seen_case_hashes: set[str] = set()
    for line_number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            decoded = json.loads(raw)
        except ValueError as exc:
            raise ShadowCaseEnvelopeError(
                f"{source.name} line {line_number} is not valid JSON"
            ) from exc
        try:
            case = case_from_envelope(_mapping(decoded, field_name=f"line {line_number}"))
        except ShadowCaseEnvelopeError as exc:
            raise ShadowCaseEnvelopeError(
                f"{source.name} line {line_number}: {exc}"
            ) from exc
        if case.case_id in seen_case_ids:
            raise ShadowCaseEnvelopeError(
                f"{source.name} line {line_number}: duplicate case_id {case.case_id!r}"
            )
        if case.case_hash in seen_case_hashes:
            raise ShadowCaseEnvelopeError(
                f"{source.name} line {line_number}: duplicate case identity"
            )
        seen_case_ids.add(case.case_id)
        seen_case_hashes.add(case.case_hash)
        cases.append(case)
    return tuple(cases)


__all__ = [
    "CASE_ENVELOPE_SCHEMA_VERSION",
    "ShadowCaseEnvelopeError",
    "case_from_envelope",
    "load_case_envelopes",
]
