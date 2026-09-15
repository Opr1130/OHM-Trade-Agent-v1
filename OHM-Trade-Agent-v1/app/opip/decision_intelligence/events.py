from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any, Mapping, Type

from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.decision_intelligence.identity import DecisionContext, Provenance
from app.opip.decision_intelligence.contracts import (
    AdvisoryStance,
    CommitteeAssessmentSummary,
    CommitteeRequest,
    CommitteeRequestTransition,
    CommitteeRole,
    CommitteeRoleResult,
    ComparisonRecord,
    ModelInvocation,
    RequestState,
    ResultDisposition,
)
from app.opip.decision_intelligence.serialization import stable_hash

DECISION_INTELLIGENCE_STREAM = "decision_intelligence.v1"
DECISION_INTELLIGENCE_CONTEXT_RECORDED = "decision_intelligence.context.recorded"
DECISION_INTELLIGENCE_REQUEST_RECORDED = "decision_intelligence.request.recorded"
DECISION_INTELLIGENCE_TRANSITION_RECORDED = "decision_intelligence.transition.recorded"
DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED = "decision_intelligence.role_result.recorded"
DECISION_INTELLIGENCE_ASSESSMENT_RECORDED = "decision_intelligence.assessment.recorded"
DECISION_INTELLIGENCE_INVOCATION_RECORDED = "decision_intelligence.invocation.recorded"
DECISION_INTELLIGENCE_COMPARISON_RECORDED = "decision_intelligence.comparison.recorded"

DECISION_INTELLIGENCE_EVENT_TYPES = frozenset(
    {
        DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        DECISION_INTELLIGENCE_REQUEST_RECORDED,
        DECISION_INTELLIGENCE_TRANSITION_RECORDED,
        DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
        DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
        DECISION_INTELLIGENCE_INVOCATION_RECORDED,
        DECISION_INTELLIGENCE_COMPARISON_RECORDED,
    }
)

_decision_intelligence_event_types = DECISION_INTELLIGENCE_EVENT_TYPES

def _identity(event_type: str, *parts: object) -> str:
    return stable_hash(event_type, {"components": list(parts)})


def context_idempotency_key(*, context_id: str, watermark: ConsumedInputWatermark | dict[str, int]) -> str:
    return _identity(DECISION_INTELLIGENCE_CONTEXT_RECORDED, context_id)


def request_idempotency_key(*, request_id: str) -> str:
    return _identity(DECISION_INTELLIGENCE_REQUEST_RECORDED, request_id)


def context_decision_link_idempotency_key(*, link_id: str) -> str:
    return _identity("decision_intelligence.context_decision_link", link_id)


def transition_idempotency_key(*, transition_id: str) -> str:
    return _identity(DECISION_INTELLIGENCE_TRANSITION_RECORDED, transition_id)


def transition_identity(transition: Mapping[str, Any]) -> str:
    identity = {
        key: transition.get(key)
        for key in (
            "request_id",
            "from_state",
            "to_state",
            "transition_time",
            "supersedes_id",
            "supersession_reason",
        )
    }
    identity["transition_time"] = _timestamp(
        identity["transition_time"], "transition_time"
    )
    return stable_hash("DI-TRANSITION", identity)


def role_result_idempotency_key(
    *,
    request_id: str,
    role: str,
    role_version: str,
    route_version: str,
    prompt_version: str,
    attempt: int,
    model_version: str,
    supersedes_id: str | None = None,
    supersession_reason: str | None = None,
) -> str:
    return _identity(
        DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
        request_id,
        role,
        role_version,
        route_version,
        prompt_version,
        attempt,
        model_version,
        supersedes_id,
        supersession_reason,
    )


def assessment_idempotency_key(*, assessment_id: str) -> str:
    return _identity(DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, assessment_id)


def invocation_idempotency_key(*, invocation_id: str) -> str:
    return _identity(DECISION_INTELLIGENCE_INVOCATION_RECORDED, invocation_id)


def comparison_idempotency_key(*, comparison_id: str) -> str:
    return _identity(DECISION_INTELLIGENCE_COMPARISON_RECORDED, comparison_id)


def context_identity(context: Mapping[str, Any]) -> str:
    return stable_hash("DI-CONTEXT", {key: context[key] for key in ("candidate_id", "episode_id", "evaluation_id", "snapshot_hash", "feature_version", "policy_version")})


def request_identity(request: Mapping[str, Any]) -> str:
    identity = {
        key: request[key]
        for key in (
            "context_id",
            "experiment_id",
            "cohort_selection_rule_version",
            "frozen_snapshot_hash",
            "route_version",
            "prompt_version",
            "role_configuration_version",
            "eligibility_at",
            "deadline_at",
            "budget_reservation",
            "result_selection_rule_version",
        )
    }
    for field_name in ("eligibility_at", "deadline_at"):
        identity[field_name] = _timestamp(identity[field_name], field_name)
    return stable_hash("DI-REQUEST", identity)


def role_result_identity(result: Mapping[str, Any]) -> str:
    return stable_hash("DI-ROLE-RESULT", {key: result.get(key) for key in ("request_id", "role", "role_version", "route_version", "prompt_version", "model_version", "attempt", "supersedes_id", "supersession_reason")})


def assessment_identity(assessment: Mapping[str, Any]) -> str:
    return stable_hash("DI-ASSESSMENT", {key: assessment.get(key) for key in ("request_id", "referenced_role_result_ids", "result_selection_rule_version", "supersedes_id", "supersession_reason")})


def invocation_identity(invocation: Mapping[str, Any]) -> str:
    return stable_hash("DI-INVOCATION", {key: invocation.get(key) for key in ("request_id", "role", "attempt", "provider", "model", "provider_model_version", "route_version", "prompt_version", "supersedes_id", "supersession_reason")})


def comparison_identity(comparison: Mapping[str, Any]) -> str:
    identity = {
        key: comparison.get(key)
        for key in (
            "decision_context_id",
            "baseline_decision_id",
            "committee_assessment_id",
            "committee_request_id",
            "experiment_id",
            "variant_version",
            "as_of_watermark",
            "attribution_method_version",
            "cost_allocation_version",
            "uncertainty_method_version",
            "supersedes_id",
            "supersession_reason",
        )
    }
    watermark = identity["as_of_watermark"]
    if isinstance(watermark, ConsumedInputWatermark):
        identity["as_of_watermark"] = watermark.to_dict()
    return stable_hash("DI-COMPARISON", identity)


def _timestamp(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} must be an aware UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be an aware UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _provenance(value: Any) -> Provenance:
    if not isinstance(value, Mapping):
        raise ValueError("provenance is required")
    allowed = {field.name for field in fields(Provenance)}
    if set(value) != allowed:
        raise ValueError("provenance fields are incomplete or unknown")
    data = dict(value)
    data["emitted_at"] = _timestamp(data["emitted_at"], "provenance.emitted_at")
    data["source_record_refs"] = _string_tuple(
        data["source_record_refs"], "provenance.source_record_refs"
    )
    return Provenance(**data)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    items = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    return items


def _watermark(value: Any, field_name: str) -> ConsumedInputWatermark:
    if isinstance(value, ConsumedInputWatermark):
        return value
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a watermark object")
    required = {"history_epoch", "local_sequence"}
    if set(value) != required:
        raise ValueError(f"invalid {field_name}")
    history_epoch = value["history_epoch"]
    local_sequence = value["local_sequence"]
    if type(history_epoch) is not int or type(local_sequence) is not int:
        raise ValueError(f"invalid {field_name}")
    if history_epoch < 0 or local_sequence < 0:
        raise ValueError(f"invalid {field_name}")
    return ConsumedInputWatermark(
        history_epoch=history_epoch,
        local_sequence=local_sequence,
    )


def _strict_payload(payload: Mapping[str, Any], record_type: Type[Any]) -> dict[str, Any]:
    allowed = {field.name for field in fields(record_type)}
    if set(payload) - allowed:
        raise ValueError(f"unknown fields for {record_type.__name__}")
    required = {
        field.name
        for field in fields(record_type)
        if field.default is MISSING and field.default_factory is MISSING
    }
    missing = {name for name in required if name not in payload}
    if missing:
        raise ValueError(f"missing fields for {record_type.__name__}: {sorted(missing)}")
    return dict(payload)


def validate_di_payload(event_type: str, payload: Mapping[str, Any]) -> Any:
    if not isinstance(payload, Mapping):
        raise ValueError("DI payload must be an object")
    record_types = {
        DECISION_INTELLIGENCE_CONTEXT_RECORDED: DecisionContext,
        DECISION_INTELLIGENCE_REQUEST_RECORDED: CommitteeRequest,
        DECISION_INTELLIGENCE_TRANSITION_RECORDED: CommitteeRequestTransition,
        DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED: CommitteeRoleResult,
        DECISION_INTELLIGENCE_ASSESSMENT_RECORDED: CommitteeAssessmentSummary,
        DECISION_INTELLIGENCE_INVOCATION_RECORDED: ModelInvocation,
        DECISION_INTELLIGENCE_COMPARISON_RECORDED: ComparisonRecord,
    }
    record_type = record_types.get(event_type)
    if record_type is None:
        raise ValueError("unsupported decision intelligence event type")
    if "schema_version" not in payload:
        raise ValueError("DI payload schema_version is required")
    if payload["schema_version"] != 1:
        raise ValueError("unsupported DI payload schema_version")
    data = _strict_payload(payload, record_type)
    data["provenance"] = _provenance(data["provenance"])
    enum_fields = {"from_state": RequestState, "to_state": RequestState, "result_disposition": ResultDisposition, "stance": AdvisoryStance, "advisory_stance": AdvisoryStance, "advisory_disposition": ResultDisposition, "role": CommitteeRole}
    for name, enum_type in enum_fields.items():
        if name not in data:
            continue
        if name == "advisory_disposition" and data[name] is None:
            continue
        if not isinstance(data[name], enum_type):
            try:
                data[name] = enum_type(data[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid {name}") from exc
    timestamp_names = {
        field.name for field in fields(record_type)
        if field.name.endswith("_at") or field.name in {
            "evaluation_time",
            "evidence_cutoff",
            "transition_time",
            "started_at",
            "completed_at",
            "completion_time",
            "commit_time",
            "evaluation_window_start",
            "evaluation_window_end",
        }
    }
    for name in timestamp_names:
        if name in data:
            data[name] = _timestamp(data[name], name)
    for name in (
        "risks",
        "evidence_refs",
        "missing_evidence",
        "referenced_role_result_ids",
        "unsupported_claims",
        "invocation_references",
        "invocation_refs",
    ):
        if name in data:
            data[name] = _string_tuple(data[name], name)
    if record_type is ComparisonRecord:
        data["as_of_watermark"] = _watermark(
            data.get("as_of_watermark"), "as_of_watermark"
        )
    if record_type is DecisionContext:
        data["consumed_input_watermark"] = _watermark(
            data.get("consumed_input_watermark"),
            "consumed_input_watermark",
        )
    identity_builders = {
        DecisionContext: context_identity,
        CommitteeRequest: request_identity,
        CommitteeRequestTransition: transition_identity,
        CommitteeRoleResult: role_result_identity,
        CommitteeAssessmentSummary: assessment_identity,
        ModelInvocation: invocation_identity,
        ComparisonRecord: comparison_identity,
    }
    if record_type in identity_builders:
        identity_field = {
            DecisionContext: "context_id", CommitteeRequest: "request_id",
            CommitteeRequestTransition: "transition_id", CommitteeRoleResult: "result_id",
            CommitteeAssessmentSummary: "assessment_id", ModelInvocation: "invocation_id",
            ComparisonRecord: "comparison_id",
        }[record_type]
        expected_id = identity_builders[record_type](data)
        if data[identity_field] != expected_id:
            raise ValueError(f"{identity_field} does not match content-derived identity")
    record = record_type(**data)
    if isinstance(record, DecisionContext):
        normalized = record.as_dict()
    else:
        normalized = asdict(record)
    return _canonical_payload(normalized)


def _canonical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    from app.opip.decision_intelligence.serialization import canonicalize_nested

    return canonicalize_nested(payload)


@dataclass(frozen=True)
class DIEventEnvelope:
    event_type: str
    payload: dict[str, Any]
    provenance: Provenance
    payload_schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, Provenance):
            raise ValueError("DI event provenance is required")
        if self.event_type not in DECISION_INTELLIGENCE_EVENT_TYPES:
            raise ValueError("unsupported decision intelligence event type")
        if int(self.payload.get("schema_version", -1)) != self.payload_schema_version:
            raise ValueError("DI payload schema_version is required")
        validate_di_payload(self.event_type, self.payload)

    @property
    def stream(self) -> str:
        return DECISION_INTELLIGENCE_STREAM

    def to_writer_intent(self, *, idempotency_key: str):
        from app.opip.canonical.models import WriterIntent
        return WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=idempotency_key,
            event_type=self.event_type,
            payload={
                **self.payload,
                "provenance": {
                    "producing_component": self.provenance.producing_component,
                    "artifact_or_build_id": self.provenance.artifact_or_build_id,
                    "process_instance_id": self.provenance.process_instance_id,
                    "emitted_at": self.provenance.emitted_at.isoformat().replace("+00:00", "Z"),
                    "source_record_refs": list(self.provenance.source_record_refs),
                    "schema_version": self.provenance.schema_version,
                },
            },
            ops_handoff=None,
        )


__all__ = [
    "DECISION_INTELLIGENCE_ASSESSMENT_RECORDED",
    "DECISION_INTELLIGENCE_COMPARISON_RECORDED",
    "DECISION_INTELLIGENCE_CONTEXT_RECORDED",
    "DECISION_INTELLIGENCE_EVENT_TYPES",
    "DECISION_INTELLIGENCE_REQUEST_RECORDED",
    "DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED",
    "DECISION_INTELLIGENCE_STREAM",
    "DECISION_INTELLIGENCE_TRANSITION_RECORDED",
    "DECISION_INTELLIGENCE_INVOCATION_RECORDED",
    "DIEventEnvelope",
    "_decision_intelligence_event_types",
    "context_idempotency_key",
    "context_decision_link_idempotency_key",
    "request_idempotency_key",
    "transition_idempotency_key",
    "transition_identity",
    "role_result_idempotency_key",
    "assessment_idempotency_key",
    "invocation_idempotency_key",
    "comparison_idempotency_key",
    "validate_di_payload",
    "context_identity",
    "request_identity",
    "role_result_identity",
    "assessment_identity",
    "invocation_identity",
    "comparison_identity",
]
