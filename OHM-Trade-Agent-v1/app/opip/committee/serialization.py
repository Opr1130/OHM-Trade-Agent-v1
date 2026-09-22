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
    CaseType,
    CommitteeCaseOutcome,
    CostCompleteness,
    DirectionalAssessment,
    EvaluationPhase,
    EvidenceSufficiency,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFailureClass,
    ProviderFamily,
    ReproducibilityClass,
    ResearchAction,
    StructuredOpinion,
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


def _encode_metric(metric) -> dict[str, Any]:
    return {
        "name": metric.name,
        "value": metric.value,
        "applicable": metric.applicable,
        "sample_size": metric.sample_size,
        "reason": metric.not_applicable_reason,
    }


def _decode_metric(row: Mapping[str, Any]):
    from app.opip.committee.metrics import EvaluationMetric

    if not isinstance(row, Mapping):
        raise CommitteeSerializationError("metric must be an object")
    return EvaluationMetric(
        name=row.get("name"),
        value=_parse_optional_str(row.get("value"), field="metric.value"),
        applicable=bool(row.get("applicable")),
        sample_size=row.get("sample_size"),
        not_applicable_reason=_parse_optional_str(
            row.get("reason"), field="metric.reason"
        ),
    )


def _encode_bin(item) -> dict[str, Any]:
    return {
        "lower": item.lower,
        "upper": item.upper,
        "count": item.count,
        "mean_predicted": item.mean_predicted,
        "observed_rate": item.observed_rate,
    }


def _decode_bin(row: Mapping[str, Any]):
    from app.opip.committee.metrics import CalibrationBin

    return CalibrationBin(
        lower=row.get("lower"),
        upper=row.get("upper"),
        count=row.get("count"),
        mean_predicted=row.get("mean_predicted"),
        observed_rate=row.get("observed_rate"),
    )


def _encode_confusion(matrix) -> dict[str, Any] | None:
    if matrix is None:
        return None
    return {
        "true_positive": matrix.true_positive,
        "false_positive": matrix.false_positive,
        "true_negative": matrix.true_negative,
        "false_negative": matrix.false_negative,
    }


def _decode_confusion(row: Mapping[str, Any] | None):
    from app.opip.committee.metrics import ConfusionMatrix

    if row is None:
        return None
    return ConfusionMatrix(
        true_positive=row.get("true_positive"),
        false_positive=row.get("false_positive"),
        true_negative=row.get("true_negative"),
        false_negative=row.get("false_negative"),
    )


def _encode_latency(latency) -> dict[str, Any]:
    return {
        "sample_size": latency.sample_size,
        "p50_micros": latency.p50_micros,
        "p90_micros": latency.p90_micros,
        "maximum_micros": latency.maximum_micros,
    }


def _decode_latency(row: Mapping[str, Any]):
    from app.opip.committee.metrics import LatencyDistribution

    return LatencyDistribution(
        sample_size=row.get("sample_size"),
        p50_micros=_parse_optional_int(row.get("p50_micros"), field="p50_micros"),
        p90_micros=_parse_optional_int(row.get("p90_micros"), field="p90_micros"),
        maximum_micros=_parse_optional_int(
            row.get("maximum_micros"), field="maximum_micros"
        ),
    )


def _encode_cost(cost) -> dict[str, Any]:
    return {
        "sample_size": cost.sample_size,
        "known_cost_microunits": cost.known_cost_microunits,
        "unknown_cost_samples": cost.unknown_cost_samples,
    }


def _decode_cost(row: Mapping[str, Any]):
    from app.opip.committee.metrics import CostAggregate

    return CostAggregate(
        sample_size=row.get("sample_size"),
        known_cost_microunits=_parse_optional_int(
            row.get("known_cost_microunits"), field="known_cost_microunits"
        ),
        unknown_cost_samples=row.get("unknown_cost_samples"),
    )


def _encode_arm(arm) -> dict[str, Any]:
    return {
        "arm_id": arm.arm_id,
        "kind": arm.kind.value,
        "label": arm.label,
        "provider_family": (
            None if arm.provider_family is None else arm.provider_family.value
        ),
        "model": arm.model,
        "cases": arm.cases,
        "answered": arm.answered,
        "abstentions": arm.abstentions,
        "schema_valid": arm.schema_valid,
        "failures": arm.failures,
        "unavailable": arm.unavailable,
        "skipped_budget": arm.skipped_budget,
        "input_tokens": arm.input_tokens,
        "output_tokens": arm.output_tokens,
        "adequacy": arm.adequacy,
        # Keyed by contract field rather than by metric label: a metric's own
        # name (for example "repeatability" for the consistency field) must not
        # decide where it is read back from.
        "metrics": {
            field_name: _encode_metric(getattr(arm, field_name))
            for field_name, _ in _ARM_METRIC_FIELDS
        },
        "calibration_bins": [_encode_bin(item) for item in arm.calibration_bins],
        "confusion": _encode_confusion(arm.confusion),
        "latency": _encode_latency(arm.latency),
        "cost": _encode_cost(arm.cost),
    }


#: Contract fields that carry an EvaluationMetric, in report order.
_ARM_METRIC_FIELDS = (
    ("coverage", "coverage"),
    ("response_validity", "response_validity"),
    ("abstention_rate", "abstention_rate"),
    ("failure_rate", "failure_rate"),
    ("schema_compliance", "schema_compliance"),
    ("consistency", "consistency"),
    ("precision", "precision"),
    ("recall", "recall"),
    ("f1", "f1"),
    ("accuracy", "accuracy"),
    ("brier_score", "brier_score"),
    ("log_loss", "log_loss"),
    ("expected_calibration_error", "expected_calibration_error"),
)


def _decode_arm(row: Mapping[str, Any]):
    from app.opip.committee.evaluation import ArmEvaluation, ArmKind

    raw_metrics = row.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        raise CommitteeSerializationError("arm metrics must be an object")
    metrics_by_field = {
        field_name: _decode_metric(raw_metrics[field_name])
        for field_name, _ in _ARM_METRIC_FIELDS
        if field_name in raw_metrics
    }
    missing = [
        field_name
        for field_name, _ in _ARM_METRIC_FIELDS
        if field_name not in metrics_by_field
    ]
    if missing:
        raise CommitteeSerializationError(f"arm is missing metrics: {missing}")
    return ArmEvaluation(
        arm_id=row.get("arm_id"),
        kind=_parse_enum(row.get("kind"), ArmKind, field="kind"),
        label=row.get("label"),
        provider_family=_parse_optional_enum(
            row.get("provider_family"), ProviderFamily, field="provider_family"
        ),
        model=_parse_optional_str(row.get("model"), field="model"),
        cases=row.get("cases"),
        answered=row.get("answered"),
        abstentions=row.get("abstentions"),
        schema_valid=row.get("schema_valid"),
        failures=row.get("failures"),
        unavailable=row.get("unavailable"),
        skipped_budget=row.get("skipped_budget"),
        input_tokens=_parse_optional_int(row.get("input_tokens"), field="input_tokens"),
        output_tokens=_parse_optional_int(
            row.get("output_tokens"), field="output_tokens"
        ),
        adequacy=row.get("adequacy"),
        coverage=metrics_by_field["coverage"],
        response_validity=metrics_by_field["response_validity"],
        abstention_rate=metrics_by_field["abstention_rate"],
        failure_rate=metrics_by_field["failure_rate"],
        schema_compliance=metrics_by_field["schema_compliance"],
        consistency=metrics_by_field["consistency"],
        precision=metrics_by_field["precision"],
        recall=metrics_by_field["recall"],
        f1=metrics_by_field["f1"],
        accuracy=metrics_by_field["accuracy"],
        brier_score=metrics_by_field["brier_score"],
        log_loss=metrics_by_field["log_loss"],
        expected_calibration_error=metrics_by_field["expected_calibration_error"],
        calibration_bins=tuple(
            _decode_bin(item) for item in row.get("calibration_bins", [])
        ),
        confusion=_decode_confusion(row.get("confusion")),
        latency=_decode_latency(row.get("latency", {})),
        cost=_decode_cost(row.get("cost", {})),
    )


_ARM_FIELDS = frozenset(
    {
        "arm_id",
        "kind",
        "label",
        "provider_family",
        "model",
        "cases",
        "answered",
        "abstentions",
        "schema_valid",
        "failures",
        "unavailable",
        "skipped_budget",
        "input_tokens",
        "output_tokens",
        "adequacy",
        "metrics",
        "calibration_bins",
        "confusion",
        "latency",
        "cost",
    }
)

_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "report_id",
        "experiment_id",
        "phase",
        "case_type",
        "generated_at",
        "case_count",
        "minimum_samples",
        "arms",
        "provenance",
        "metric_definitions_version",
        "committee_signal_rule_version",
        "measurement_only",
        "automatic_promotion",
        "trade_authority_changed",
    }
)


def evaluation_report_to_dict(report) -> dict[str, Any]:
    provenance = report.provenance
    return {
        "schema_version": report.schema_version,
        "report_id": report.report_id,
        "experiment_id": report.experiment_id,
        "phase": report.phase.value,
        "case_type": report.case_type.value,
        "generated_at": _iso(report.generated_at),
        "case_count": report.case_count,
        "minimum_samples": report.minimum_samples,
        "arms": [_encode_arm(arm) for arm in report.arms],
        "provenance": {
            "schema_version": provenance.schema_version,
            "producing_component": provenance.producing_component,
            "artifact_or_build_id": provenance.artifact_or_build_id,
            "process_instance_id": provenance.process_instance_id,
            "emitted_at": _iso(provenance.emitted_at),
            "source_record_refs": list(provenance.source_record_refs),
        },
        "metric_definitions_version": report.metric_definitions_version,
        "committee_signal_rule_version": report.committee_signal_rule_version,
        "measurement_only": report.measurement_only,
        "automatic_promotion": report.automatic_promotion,
        "trade_authority_changed": report.trade_authority_changed,
    }


def evaluation_report_from_dict(row: Mapping[str, Any]):
    """Rebuild a bake-off report, verifying its content identity on read."""
    from app.opip.committee.evaluation import EvaluationReport

    if not isinstance(row, Mapping):
        raise CommitteeSerializationError("evaluation report must be an object")
    _reject_unknown(row, _REPORT_FIELDS, kind="evaluation report")
    provenance_row = row.get("provenance")
    if not isinstance(provenance_row, Mapping):
        raise CommitteeSerializationError("provenance must be an object")
    _reject_unknown(provenance_row, _PROVENANCE_FIELDS, kind="provenance")
    raw_arms = row.get("arms")
    if not isinstance(raw_arms, list):
        raise CommitteeSerializationError("arms must be a list")
    arms = []
    for arm_row in raw_arms:
        if not isinstance(arm_row, Mapping):
            raise CommitteeSerializationError("arm must be an object")
        _reject_unknown(arm_row, _ARM_FIELDS, kind="arm")
        arms.append(_decode_arm(arm_row))

    from app.opip.committee.contracts import CaseType, EvaluationPhase
    from app.opip.decision_intelligence.identity import Provenance

    report = EvaluationReport(
        schema_version=row.get("schema_version"),
        experiment_id=row.get("experiment_id"),
        phase=_parse_enum(row.get("phase"), EvaluationPhase, field="phase"),
        case_type=_parse_enum(row.get("case_type"), CaseType, field="case_type"),
        generated_at=_parse_dt(row.get("generated_at"), field="generated_at"),
        case_count=row.get("case_count"),
        minimum_samples=row.get("minimum_samples"),
        arms=tuple(arms),
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
        metric_definitions_version=row.get("metric_definitions_version"),
        committee_signal_rule_version=row.get("committee_signal_rule_version"),
        measurement_only=bool(row.get("measurement_only")),
        automatic_promotion=bool(row.get("automatic_promotion")),
        trade_authority_changed=bool(row.get("trade_authority_changed")),
    )
    declared_id = row.get("report_id")
    if declared_id is not None and declared_id != report.report_id:
        raise CommitteeSerializationError(
            "persisted report_id does not match its content identity"
        )
    return report


_SEAL_FIELDS = frozenset(
    {
        "kind",
        "schema_version",
        "prediction_id",
        "case_id",
        "case_type",
        "experiment_id",
        "evidence_cutoff_at",
        "sealed_at",
        "case_outcome_id",
        "evidence_snapshot_hash",
        "committee_policy_version",
        "sealed_opinion_hashes",
        "sealed_seat_count",
        "phase",
        "provenance",
    }
)

_OUTCOME_OBSERVATION_FIELDS = frozenset(
    {
        "kind",
        "schema_version",
        "observation_id",
        "case_id",
        "outcome_source",
        "source_refs",
        "observed_at",
        "horizon_seconds",
        "finality",
        "positive",
        "realised_return_microunits",
        "incomplete_reason",
    }
)

_PROSPECTIVE_EVALUATION_FIELDS = frozenset(
    {
        "kind",
        "schema_version",
        "evaluation_id",
        "prediction_id",
        "outcome_observation_id",
        "case_id",
        "case_type",
        "experiment_id",
        "evidence_cutoff_at",
        "sealed_at",
        "observed_at",
        "evaluated_at",
        "horizon_seconds",
        "finality",
        "phase",
        "seat_scores",
        "scored_seats",
        "abstained_seats",
        "unavailable_seats",
        "unscored_directional_seats",
        "metrics",
        "confusion",
        "measurement_only",
        "automatic_promotion",
        "trade_authority_changed",
        "provenance",
    }
)

_SEAT_SCORE_FIELDS = frozenset(
    {"provider_family", "model", "call", "correct", "final"}
)

_PREDICTION_KIND = "SEALED_PREDICTION"
_OUTCOME_KIND = "OUTCOME_OBSERVATION"
_EVALUATION_KIND = "PROSPECTIVE_EVALUATION"


def _encode_provenance(provenance) -> dict[str, Any]:
    return {
        "schema_version": provenance.schema_version,
        "producing_component": provenance.producing_component,
        "artifact_or_build_id": provenance.artifact_or_build_id,
        "process_instance_id": provenance.process_instance_id,
        "emitted_at": _iso(provenance.emitted_at),
        "source_record_refs": list(provenance.source_record_refs),
    }


def _decode_provenance(row: Mapping[str, Any]):
    from app.opip.decision_intelligence.identity import Provenance

    if not isinstance(row, Mapping):
        raise CommitteeSerializationError("provenance must be an object")
    _reject_unknown(row, _PROVENANCE_FIELDS, kind="provenance")
    return Provenance(
        schema_version=row.get("schema_version"),
        producing_component=row.get("producing_component"),
        artifact_or_build_id=row.get("artifact_or_build_id"),
        process_instance_id=row.get("process_instance_id"),
        emitted_at=_parse_dt(row.get("emitted_at"), field="emitted_at"),
        source_record_refs=_parse_str_tuple(
            row.get("source_record_refs"), field="source_record_refs"
        ),
    )


def sealed_prediction_to_dict(prediction) -> dict[str, Any]:
    return {
        "kind": _PREDICTION_KIND,
        "schema_version": prediction.schema_version,
        "prediction_id": prediction.prediction_id,
        "case_id": prediction.case_id,
        "case_type": prediction.case_type.value,
        "experiment_id": prediction.experiment_id,
        "evidence_cutoff_at": _iso(prediction.evidence_cutoff_at),
        "sealed_at": _iso(prediction.sealed_at),
        "case_outcome_id": prediction.case_outcome_id,
        "evidence_snapshot_hash": prediction.evidence_snapshot_hash,
        "committee_policy_version": prediction.committee_policy_version,
        "sealed_opinion_hashes": list(prediction.sealed_opinion_hashes),
        "sealed_seat_count": prediction.sealed_seat_count,
        "phase": prediction.phase.value,
        "provenance": _encode_provenance(prediction.provenance),
    }


def sealed_prediction_from_dict(row: Mapping[str, Any]):
    from app.opip.committee.prospective import SealedPrediction

    _reject_unknown(row, _SEAL_FIELDS, kind="sealed prediction")
    prediction = SealedPrediction(
        schema_version=row.get("schema_version"),
        case_id=row.get("case_id"),
        case_type=_parse_enum(row.get("case_type"), CaseType, field="case_type"),
        experiment_id=row.get("experiment_id"),
        evidence_cutoff_at=_parse_dt(
            row.get("evidence_cutoff_at"), field="evidence_cutoff_at"
        ),
        sealed_at=_parse_dt(row.get("sealed_at"), field="sealed_at"),
        case_outcome_id=row.get("case_outcome_id"),
        evidence_snapshot_hash=row.get("evidence_snapshot_hash"),
        committee_policy_version=row.get("committee_policy_version"),
        sealed_opinion_hashes=_parse_str_tuple(
            row.get("sealed_opinion_hashes"), field="sealed_opinion_hashes"
        ),
        sealed_seat_count=row.get("sealed_seat_count"),
        phase=_parse_enum(row.get("phase"), EvaluationPhase, field="phase"),
        provenance=_decode_provenance(row.get("provenance")),
    )
    declared = row.get("prediction_id")
    if declared is not None and declared != prediction.prediction_id:
        raise CommitteeSerializationError(
            "persisted prediction_id does not match its content identity"
        )
    return prediction


def outcome_observation_to_dict(observation) -> dict[str, Any]:
    return {
        "kind": _OUTCOME_KIND,
        "schema_version": observation.schema_version,
        "observation_id": observation.observation_id,
        "case_id": observation.case_id,
        "outcome_source": observation.outcome_source,
        "source_refs": list(observation.source_refs),
        "observed_at": _iso(observation.observed_at),
        "horizon_seconds": observation.horizon_seconds,
        "finality": observation.finality.value,
        "positive": observation.positive,
        "realised_return_microunits": observation.realised_return_microunits,
        "incomplete_reason": observation.incomplete_reason,
    }


def outcome_observation_from_dict(row: Mapping[str, Any]):
    from app.opip.committee.prospective import OutcomeFinality, OutcomeObservation

    _reject_unknown(row, _OUTCOME_OBSERVATION_FIELDS, kind="outcome observation")
    observation = OutcomeObservation(
        schema_version=row.get("schema_version"),
        case_id=row.get("case_id"),
        outcome_source=row.get("outcome_source"),
        source_refs=_parse_str_tuple(row.get("source_refs"), field="source_refs"),
        observed_at=_parse_dt(row.get("observed_at"), field="observed_at"),
        horizon_seconds=row.get("horizon_seconds"),
        finality=_parse_enum(row.get("finality"), OutcomeFinality, field="finality"),
        positive=row.get("positive"),
        realised_return_microunits=_parse_optional_int(
            row.get("realised_return_microunits"), field="realised_return_microunits"
        ),
        incomplete_reason=_parse_optional_str(
            row.get("incomplete_reason"), field="incomplete_reason"
        ),
    )
    declared = row.get("observation_id")
    if declared is not None and declared != observation.observation_id:
        raise CommitteeSerializationError(
            "persisted observation_id does not match its content identity"
        )
    return observation


def prospective_evaluation_to_dict(evaluation) -> dict[str, Any]:
    return {
        "kind": _EVALUATION_KIND,
        "schema_version": evaluation.schema_version,
        "evaluation_id": evaluation.evaluation_id,
        "prediction_id": evaluation.prediction_id,
        "outcome_observation_id": evaluation.outcome_observation_id,
        "case_id": evaluation.case_id,
        "case_type": evaluation.case_type.value,
        "experiment_id": evaluation.experiment_id,
        "evidence_cutoff_at": _iso(evaluation.evidence_cutoff_at),
        "sealed_at": _iso(evaluation.sealed_at),
        "observed_at": _iso(evaluation.observed_at),
        "evaluated_at": _iso(evaluation.evaluated_at),
        "horizon_seconds": evaluation.horizon_seconds,
        "finality": evaluation.finality.value,
        "phase": evaluation.phase.value,
        "seat_scores": [
            {
                "provider_family": score.provider_family.value,
                "model": score.model,
                "call": score.call.value,
                "correct": score.correct,
                "final": score.final,
            }
            for score in evaluation.seat_scores
        ],
        "scored_seats": evaluation.scored_seats,
        "abstained_seats": evaluation.abstained_seats,
        "unavailable_seats": evaluation.unavailable_seats,
        "unscored_directional_seats": evaluation.unscored_directional_seats,
        "metrics": {
            "precision": _encode_metric(evaluation.precision),
            "recall": _encode_metric(evaluation.recall),
            "f1": _encode_metric(evaluation.f1),
            "accuracy": _encode_metric(evaluation.accuracy),
        },
        "confusion": _encode_confusion(evaluation.confusion),
        "measurement_only": evaluation.measurement_only,
        "automatic_promotion": evaluation.automatic_promotion,
        "trade_authority_changed": evaluation.trade_authority_changed,
        "provenance": _encode_provenance(evaluation.provenance),
    }


def prospective_evaluation_from_dict(row: Mapping[str, Any]):
    from app.opip.committee.evaluation import DirectionalCall
    from app.opip.committee.prospective import (
        OutcomeFinality,
        ProspectiveEvaluation,
        ProspectiveSeatScore,
    )

    _reject_unknown(row, _PROSPECTIVE_EVALUATION_FIELDS, kind="prospective evaluation")
    raw_metrics = row.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        raise CommitteeSerializationError("prospective metrics must be an object")
    missing = [
        name
        for name in ("precision", "recall", "f1", "accuracy")
        if name not in raw_metrics
    ]
    if missing:
        raise CommitteeSerializationError(f"missing metrics: {missing}")
    raw_scores = row.get("seat_scores")
    if not isinstance(raw_scores, list):
        raise CommitteeSerializationError("seat_scores must be a list")
    seat_scores = []
    for score_row in raw_scores:
        if not isinstance(score_row, Mapping):
            raise CommitteeSerializationError("seat score must be an object")
        _reject_unknown(score_row, _SEAT_SCORE_FIELDS, kind="seat score")
        seat_scores.append(
            ProspectiveSeatScore(
                provider_family=_parse_enum(
                    score_row.get("provider_family"),
                    ProviderFamily,
                    field="provider_family",
                ),
                model=score_row.get("model"),
                call=_parse_enum(
                    score_row.get("call"), DirectionalCall, field="call"
                ),
                correct=score_row.get("correct"),
                final=bool(score_row.get("final")),
            )
        )
    evaluation = ProspectiveEvaluation(
        schema_version=row.get("schema_version"),
        prediction_id=row.get("prediction_id"),
        outcome_observation_id=row.get("outcome_observation_id"),
        case_id=row.get("case_id"),
        case_type=_parse_enum(row.get("case_type"), CaseType, field="case_type"),
        experiment_id=row.get("experiment_id"),
        evidence_cutoff_at=_parse_dt(
            row.get("evidence_cutoff_at"), field="evidence_cutoff_at"
        ),
        sealed_at=_parse_dt(row.get("sealed_at"), field="sealed_at"),
        observed_at=_parse_dt(row.get("observed_at"), field="observed_at"),
        evaluated_at=_parse_dt(row.get("evaluated_at"), field="evaluated_at"),
        horizon_seconds=row.get("horizon_seconds"),
        finality=_parse_enum(row.get("finality"), OutcomeFinality, field="finality"),
        phase=_parse_enum(row.get("phase"), EvaluationPhase, field="phase"),
        seat_scores=tuple(seat_scores),
        scored_seats=row.get("scored_seats"),
        abstained_seats=row.get("abstained_seats"),
        unavailable_seats=row.get("unavailable_seats"),
        unscored_directional_seats=row.get("unscored_directional_seats"),
        precision=_decode_metric(raw_metrics["precision"]),
        recall=_decode_metric(raw_metrics["recall"]),
        f1=_decode_metric(raw_metrics["f1"]),
        accuracy=_decode_metric(raw_metrics["accuracy"]),
        confusion=_decode_confusion(row.get("confusion")),
        measurement_only=bool(row.get("measurement_only")),
        automatic_promotion=bool(row.get("automatic_promotion")),
        trade_authority_changed=bool(row.get("trade_authority_changed")),
        provenance=_decode_provenance(row.get("provenance")),
    )
    declared = row.get("evaluation_id")
    if declared is not None and declared != evaluation.evaluation_id:
        raise CommitteeSerializationError(
            "persisted evaluation_id does not match its content identity"
        )
    return evaluation


def prospective_record_from_dict(row: Mapping[str, Any]):
    """Dispatch a persisted prospective row to its contract by record kind."""
    if not isinstance(row, Mapping):
        raise CommitteeSerializationError("prospective record must be an object")
    kind = row.get("kind")
    if kind == _PREDICTION_KIND:
        return sealed_prediction_from_dict(row)
    if kind == _OUTCOME_KIND:
        return outcome_observation_from_dict(row)
    if kind == _EVALUATION_KIND:
        return prospective_evaluation_from_dict(row)
    raise CommitteeSerializationError(f"undeclared prospective record kind: {kind!r}")


__all__ = [
    "COMMITTEE_CASE_OUTCOME_SCHEMA_VERSION",
    "PROVIDER_CALL_OUTCOME_SCHEMA_VERSION",
    "CommitteeSerializationError",
    "call_outcome_from_dict",
    "call_outcome_to_dict",
    "case_outcome_from_dict",
    "case_outcome_to_dict",
    "evaluation_report_from_dict",
    "evaluation_report_to_dict",
    "opinion_from_dict",
    "opinion_to_dict",
    "outcome_observation_from_dict",
    "outcome_observation_to_dict",
    "prospective_evaluation_from_dict",
    "prospective_evaluation_to_dict",
    "prospective_record_from_dict",
    "sealed_prediction_from_dict",
    "sealed_prediction_to_dict",
]
