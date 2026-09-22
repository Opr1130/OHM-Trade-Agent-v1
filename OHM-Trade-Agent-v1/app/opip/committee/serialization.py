"""Auditable row encoding for durable committee evidence.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Encoding is explicit rather than reflective: every persisted field is named in
one place, so a new contract field cannot silently start travelling to disk and
an unknown field cannot silently be accepted from disk.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.opip.committee.contracts import (
    COMMITTEE_CASE_OUTCOME_SCHEMA_VERSION,
    PROVIDER_CALL_OUTCOME_SCHEMA_VERSION,
    ProviderCallOutcome,
    CommitteeCaseOutcome,
    CostCompleteness,
    EvaluationPhase,
    ObservationStatus,
    ProviderFailureClass,
    ProviderFamily,
    StructuredOpinion,
    EvidenceSufficiency,
    DirectionalAssessment,
    ResearchAction,
    ReproducibilityClass,
)
from app.opip.decision_intelligence.serialization import require_utc

_UTC_OFFSET = "+00:00"
_UTC_Z = "Z"

_CALL_OUTCOME_FIELDS = frozenset(
    {
        "schema_version",
        "outcome_id",
        "logical_observation_id",
        "case_id",
        "provider_family",
        "requested_model",
        "status",
        "attempt",
        "reproducibility",
        "request_at",
        "response_at",
        "input_hash",
        "reported_provider",
        "reported_model",
        "failure_class",
        "detail",
        "opinion",
        "raw_response_ref",
        "latency_micros",
        "input_tokens",
        "output_tokens",
        "estimated_cost_microunits",
        "cost_completeness",
        "replay_divergence_detected",
    }
)

_OPINION_FIELDS = frozenset(
    {
        "schema_version",
        "opinion_id",
        "case_id",
        "provider",
        "model",
        "evidence_sufficiency",
        "assessment",
        "hypothesis",
        "confidence",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
        "major_assumptions",
        "risk_factors",
        "missing_evidence",
        "alternative_explanations",
        "recommended_research_action",
        "abstention_reason",
    }
)

_CASE_OUTCOME_FIELDS = frozenset(
    {
        "schema_version",
        "case_outcome_id",
        "case_id",
        "evidence_snapshot_hash",
        "committee_policy_version",
        "phase",
        "started_at",
        "completed_at",
        "outcomes",
        "provenance",
    }
)

_PROVENANCE_FIELDS = frozenset(
    {
        "schema_version",
        "producing_component",
        "artifact_or_build_id",
        "process_instance_id",
        "emitted_at",
        "source_record_refs",
    }
)


class CommitteeSerializationError(ValueError):
    """A persisted committee row did not match its declared schema."""


def _iso(value: datetime) -> str:
    return require_utc(value, field_name="timestamp").isoformat().replace(_UTC_OFFSET, _UTC_Z)


def _parse_dt(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise CommitteeSerializationError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace(_UTC_Z, _UTC_OFFSET))
    except ValueError as exc:
        raise CommitteeSerializationError(f"{field} is not an ISO-8601 timestamp") from exc
    return require_utc(parsed, field_name=field)


def _parse_enum(value: Any, enum_type: type, *, field: str) -> Any:
    if not isinstance(value, str):
        raise CommitteeSerializationError(f"{field} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise CommitteeSerializationError(
            f"{field} is not a declared {enum_type.__name__}"
        ) from exc


def _parse_optional_enum(value: Any, enum_type: type, *, field: str) -> Any:
    if value is None:
        return None
    return _parse_enum(value, enum_type, field=field)


def _parse_optional_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise CommitteeSerializationError(f"{field} must be an integer or null")
    return value


def _parse_optional_str(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CommitteeSerializationError(f"{field} must be a string or null")
    return value


def _parse_str_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CommitteeSerializationError(f"{field} must be a list of strings")
    return tuple(value)


def _reject_unknown(row: Mapping[str, Any], allowed: frozenset[str], *, kind: str) -> None:
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise CommitteeSerializationError(f"undeclared {kind} fields: {unknown}")


def opinion_to_dict(opinion: StructuredOpinion) -> dict[str, Any]:
    return {
        "schema_version": opinion.schema_version,
        "opinion_id": opinion.opinion_id,
        "case_id": opinion.case_id,
        "provider": opinion.provider,
        "model": opinion.model,
        "evidence_sufficiency": opinion.evidence_sufficiency.value,
        "assessment": opinion.assessment.value,
        "hypothesis": opinion.hypothesis,
        "confidence": opinion.confidence,
        "supporting_evidence_refs": list(opinion.supporting_evidence_refs),
        "contradicting_evidence_refs": list(opinion.contradicting_evidence_refs),
        "major_assumptions": list(opinion.major_assumptions),
        "risk_factors": list(opinion.risk_factors),
        "missing_evidence": list(opinion.missing_evidence),
        "alternative_explanations": list(opinion.alternative_explanations),
        "recommended_research_action": opinion.recommended_research_action.value,
        "abstention_reason": opinion.abstention_reason,
    }


def opinion_from_dict(row: Mapping[str, Any]) -> StructuredOpinion:
    if not isinstance(row, Mapping):
        raise CommitteeSerializationError("opinion must be an object")
    _reject_unknown(row, _OPINION_FIELDS, kind="opinion")
    opinion = StructuredOpinion(
        schema_version=row.get("schema_version"),
        case_id=row.get("case_id"),
        provider=row.get("provider"),
        model=row.get("model"),
        evidence_sufficiency=_parse_enum(
            row.get("evidence_sufficiency"), EvidenceSufficiency,
            field="evidence_sufficiency",
        ),
        assessment=_parse_enum(
            row.get("assessment"), DirectionalAssessment, field="assessment"
        ),
        hypothesis=row.get("hypothesis"),
        confidence=_parse_optional_int(row.get("confidence"), field="confidence"),
        supporting_evidence_refs=_parse_str_tuple(
            row.get("supporting_evidence_refs"), field="supporting_evidence_refs"
        ),
        contradicting_evidence_refs=_parse_str_tuple(
            row.get("contradicting_evidence_refs"), field="contradicting_evidence_refs"
        ),
        major_assumptions=_parse_str_tuple(
            row.get("major_assumptions"), field="major_assumptions"
        ),
        risk_factors=_parse_str_tuple(row.get("risk_factors"), field="risk_factors"),
        missing_evidence=_parse_str_tuple(
            row.get("missing_evidence"), field="missing_evidence"
        ),
        alternative_explanations=_parse_str_tuple(
            row.get("alternative_explanations"), field="alternative_explanations"
        ),
        recommended_research_action=_parse_enum(
            row.get("recommended_research_action"), ResearchAction,
            field="recommended_research_action",
        ),
        abstention_reason=_parse_optional_str(
            row.get("abstention_reason"), field="abstention_reason"
        ),
    )
    declared_id = row.get("opinion_id")
    if declared_id is not None and declared_id != opinion.opinion_id:
        raise CommitteeSerializationError(
            "persisted opinion_id does not match its content identity"
        )
    return opinion


def call_outcome_to_dict(outcome: ProviderCallOutcome) -> dict[str, Any]:
    return {
        "schema_version": outcome.schema_version,
        "outcome_id": outcome.outcome_id,
        "logical_observation_id": outcome.logical_observation_id,
        "case_id": outcome.case_id,
        "provider_family": outcome.provider_family.value,
        "requested_model": outcome.requested_model,
        "status": outcome.status.value,
        "attempt": outcome.attempt,
        "reproducibility": outcome.reproducibility.value,
        "request_at": _iso(outcome.request_at),
        "response_at": None if outcome.response_at is None else _iso(outcome.response_at),
        "input_hash": outcome.input_hash,
        "reported_provider": outcome.reported_provider,
        "reported_model": outcome.reported_model,
        "failure_class": (
            None if outcome.failure_class is None else outcome.failure_class.value
        ),
        "detail": outcome.detail,
        "opinion": None if outcome.opinion is None else opinion_to_dict(outcome.opinion),
        "raw_response_ref": outcome.raw_response_ref,
        "latency_micros": outcome.latency_micros,
        "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens,
        "estimated_cost_microunits": outcome.estimated_cost_microunits,
        "cost_completeness": outcome.cost_completeness.value,
        "replay_divergence_detected": outcome.replay_divergence_detected,
    }


def call_outcome_from_dict(
    row: Mapping[str, Any], *, expect_schema_version: int | None = None
) -> ProviderCallOutcome:
    if not isinstance(row, Mapping):
        raise CommitteeSerializationError("call outcome must be an object")
    _reject_unknown(row, _CALL_OUTCOME_FIELDS, kind="call outcome")
    declared = row.get("schema_version")
    if expect_schema_version is not None and declared != expect_schema_version:
        raise CommitteeSerializationError(
            f"unsupported call outcome schema_version: {declared!r}"
        )
    outcome = ProviderCallOutcome(
        schema_version=declared,
        logical_observation_id=row.get("logical_observation_id"),
        case_id=row.get("case_id"),
        provider_family=_parse_enum(
            row.get("provider_family"), ProviderFamily, field="provider_family"
        ),
        requested_model=row.get("requested_model"),
        status=_parse_enum(row.get("status"), ObservationStatus, field="status"),
        attempt=row.get("attempt"),
        reproducibility=_parse_enum(
            row.get("reproducibility"), ReproducibilityClass, field="reproducibility"
        ),
        request_at=_parse_dt(row.get("request_at"), field="request_at"),
        response_at=(
            None
            if row.get("response_at") is None
            else _parse_dt(row.get("response_at"), field="response_at")
        ),
        input_hash=row.get("input_hash"),
        reported_provider=_parse_optional_str(
            row.get("reported_provider"), field="reported_provider"
        ),
        reported_model=_parse_optional_str(row.get("reported_model"), field="reported_model"),
        failure_class=_parse_optional_enum(
            row.get("failure_class"), ProviderFailureClass, field="failure_class"
        ),
        detail=_parse_optional_str(row.get("detail"), field="detail"),
        opinion=(
            None
            if row.get("opinion") is None
            else opinion_from_dict(row.get("opinion"))
        ),
        raw_response_ref=_parse_optional_str(
            row.get("raw_response_ref"), field="raw_response_ref"
        ),
        latency_micros=_parse_optional_int(
            row.get("latency_micros"), field="latency_micros"
        ),
        input_tokens=_parse_optional_int(row.get("input_tokens"), field="input_tokens"),
        output_tokens=_parse_optional_int(row.get("output_tokens"), field="output_tokens"),
        estimated_cost_microunits=_parse_optional_int(
            row.get("estimated_cost_microunits"), field="estimated_cost_microunits"
        ),
        cost_completeness=_parse_enum(
            row.get("cost_completeness"), CostCompleteness, field="cost_completeness"
        ),
        replay_divergence_detected=bool(row.get("replay_divergence_detected", False)),
    )
    declared_id = row.get("outcome_id")
    if declared_id is not None and declared_id != outcome.outcome_id:
        raise CommitteeSerializationError(
            "persisted outcome_id does not match its content identity"
        )
    return outcome


def case_outcome_to_dict(outcome: CommitteeCaseOutcome) -> dict[str, Any]:
    provenance = outcome.provenance
    return {
        "schema_version": outcome.schema_version,
        "case_outcome_id": outcome.case_outcome_id,
        "case_id": outcome.case_id,
        "evidence_snapshot_hash": outcome.evidence_snapshot_hash,
        "committee_policy_version": outcome.committee_policy_version,
        "phase": outcome.phase.value,
        "started_at": _iso(outcome.started_at),
        "completed_at": _iso(outcome.completed_at),
        "outcomes": [call_outcome_to_dict(item) for item in outcome.outcomes],
        "provenance": {
            "schema_version": provenance.schema_version,
            "producing_component": provenance.producing_component,
            "artifact_or_build_id": provenance.artifact_or_build_id,
            "process_instance_id": provenance.process_instance_id,
            "emitted_at": _iso(provenance.emitted_at),
            "source_record_refs": list(provenance.source_record_refs),
        },
    }


def case_outcome_from_dict(row: Mapping[str, Any]) -> CommitteeCaseOutcome:
    from app.opip.decision_intelligence.identity import Provenance

    if not isinstance(row, Mapping):
        raise CommitteeSerializationError("case outcome must be an object")
    _reject_unknown(row, _CASE_OUTCOME_FIELDS, kind="case outcome")
    provenance_row = row.get("provenance")
    if not isinstance(provenance_row, Mapping):
        raise CommitteeSerializationError("provenance must be an object")
    _reject_unknown(provenance_row, _PROVENANCE_FIELDS, kind="provenance")
    raw_outcomes = row.get("outcomes")
    if not isinstance(raw_outcomes, list):
        raise CommitteeSerializationError("outcomes must be a list")
    case_outcome = CommitteeCaseOutcome(
        schema_version=row.get("schema_version"),
        case_id=row.get("case_id"),
        evidence_snapshot_hash=row.get("evidence_snapshot_hash"),
        committee_policy_version=row.get("committee_policy_version"),
        phase=_parse_enum(row.get("phase"), EvaluationPhase, field="phase"),
        started_at=_parse_dt(row.get("started_at"), field="started_at"),
        completed_at=_parse_dt(row.get("completed_at"), field="completed_at"),
        outcomes=tuple(
            call_outcome_from_dict(item, expect_schema_version=None)
            for item in raw_outcomes
        ),
        provenance=Provenance(
            schema_version=provenance_row.get("schema_version"),
            producing_component=provenance_row.get("producing_component"),
            artifact_or_build_id=provenance_row.get("artifact_or_build_id"),
            process_instance_id=provenance_row.get("process_instance_id"),
            emitted_at=_parse_dt(provenance_row.get("emitted_at"), field="emitted_at"),
            source_record_refs=_parse_str_tuple(
                provenance_row.get("source_record_refs"), field="source_record_refs"
            ),
        ),
    )
    declared_id = row.get("case_outcome_id")
    if declared_id is not None and declared_id != case_outcome.case_outcome_id:
        raise CommitteeSerializationError(
            "persisted case_outcome_id does not match its content identity"
        )
    return case_outcome


__all__ = [
    "COMMITTEE_CASE_OUTCOME_SCHEMA_VERSION",
    "PROVIDER_CALL_OUTCOME_SCHEMA_VERSION",
    "CommitteeSerializationError",
    "call_outcome_from_dict",
    "call_outcome_to_dict",
    "case_outcome_from_dict",
    "case_outcome_to_dict",
    "opinion_from_dict",
    "opinion_to_dict",
]
