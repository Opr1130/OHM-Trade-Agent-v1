from __future__ import annotations

import hashlib
import ast
import inspect
import json
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path

import pytest

from app.opip.canonical.backup import backup_database, build_backup_manifest
from app.opip.canonical.recovery import restore_from_backup
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.models import WriterIntent
from app.opip.decision_intelligence import (
    AdvisoryStance,
    CommitteeAssessmentSummary,
    CommitteeRequest,
    CommitteeRole,
    CommitteeRoleResult,
    ComparisonRecord,
    DecisionContext,
    ModelInvocation,
    Provenance,
    RequestState,
    RequestTransition,
    ResultDisposition,
    DIEventEnvelope,
    timely_evidence_eligible,
    _decision_intelligence_event_types,
)
from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_CONTEXT_RECORDED
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_STREAM,
    canonical_di_idempotency_key,
    context_idempotency_key,
    request_idempotency_key,
    role_result_idempotency_key,
)
from app.opip.decision_intelligence.serialization import canonical_serialize
from app.opip.decision_intelligence.serialization import stable_hash
from app.opip.decision_intelligence.events import (
    context_identity,
    request_identity,
    role_result_identity,
    assessment_identity,
    invocation_identity,
)


def _di_key(event_type: str, payload: dict) -> str:
    return canonical_di_idempotency_key(event_type, payload)


def _provenance(**overrides):
    base = {
        "producing_component": "test_component",
        "artifact_or_build_id": "build-42",
        "process_instance_id": "proc-9",
        "emitted_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        "source_record_refs": ["source:1"],
    }
    base.update(overrides)
    return Provenance(**base)


def _provenance_payload():
    return {
        "producing_component": "test_component",
        "artifact_or_build_id": "build-42",
        "process_instance_id": "proc-9",
        "emitted_at": "2026-01-02T03:04:05Z",
        "source_record_refs": ["source:1"],
        "schema_version": 1,
    }


def _context_payload(context_id: str, *, value: int = 1):
    payload = {
        "context_id": context_id,
        "candidate_id": "candidate-1",
        "episode_id": "episode-1",
        "evaluation_id": "evaluation-1",
        "instrument_version": "instrument-1",
        "snapshot_id": "snapshot-1",
        "snapshot_hash": "snapshot-hash",
        "evaluation_time": "2026-01-02T03:04:00Z",
        "evidence_cutoff": "2026-01-02T03:04:00Z",
        "consumed_input_watermark": {"history_epoch": 1, "local_sequence": 1},
        "feature_version": "features-1",
        "policy_version": "policy-1",
        "detector_version": "detector-1",
        "forecast_version": "forecast-1",
        "candidate_set_ref": "set-1",
        "portfolio_version_ref": None,
        "environment": "paper",
        "eligibility": True,
        "missingness": {"value": value},
        "source_availability_times": {},
        "evidence_eligibility_manifest": {},
        "schema_version": 1,
        "provenance": _provenance_payload(),
    }
    payload["context_id"] = context_identity(payload)
    return payload


def _request_payload(context_payload: dict) -> dict:
    payload = {
        "request_id": "pending",
        "context_id": context_payload["context_id"],
        "experiment_id": "experiment-1",
        "cohort_selection_rule_version": "cohort-1",
        "frozen_snapshot_hash": context_payload["snapshot_hash"],
        "route_version": "route-1",
        "prompt_version": "prompt-1",
        "role_configuration_version": "roles-1",
        "eligibility_at": datetime(
            2026, 1, 2, 3, 4, tzinfo=timezone.utc
        ),
        "deadline_at": datetime(
            2026, 1, 2, 3, 10, tzinfo=timezone.utc
        ),
        "budget_reservation": 1,
        "enqueue_time": datetime(
            2026, 1, 2, 3, 4, tzinfo=timezone.utc
        ),
        "result_selection_rule_version": "selection-1",
        "schema_version": 1,
        "provenance": _provenance_payload(),
    }
    payload["request_id"] = request_identity(payload)
    return payload


def _seed_di_ancestry(writer, *, value: int = 1) -> str:
    context = _context_payload("ctx-ancestry", value=value)
    if value != 1:
        context["candidate_id"] = f"candidate-{value}"
        context["context_id"] = context_identity(context)
    context_ack = writer.submit(
        WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=_di_key(
                "decision_intelligence.context.recorded", context
            ),
            event_type="decision_intelligence.context.recorded",
            payload=context,
        )
    )
    assert context_ack.status in {"OK", "DUPLICATE_OK"}

    request = _request_payload(context)
    request_ack = writer.submit(
        WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=_di_key(
                "decision_intelligence.request.recorded", request
            ),
            event_type="decision_intelligence.request.recorded",
            payload=request,
        )
    )
    assert request_ack.status in {"OK", "DUPLICATE_OK"}
    return request["request_id"]


def _seed_complete_di_ancestry(writer, *, value: int = 1) -> dict:
    context = _context_payload("ctx-ancestry", value=value)
    if value != 1:
        context["candidate_id"] = f"candidate-{value}"
        context["context_id"] = context_identity(context)
    request = _request_payload(context)
    request_id = _seed_di_ancestry(writer, value=value)
    assert request_id == request["request_id"]

    invocation = _invocation_payload(
        "inv-seed",
        request_id=request_id,
    )
    invocation_ack = writer.submit(
        WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=_di_key(
                "decision_intelligence.invocation.recorded", invocation
            ),
            event_type="decision_intelligence.invocation.recorded",
            payload=invocation,
        )
    )
    assert invocation_ack.status in {"OK", "DUPLICATE_OK"}

    role_result = _role_result_payload(
        request_id=request_id,
        invocation_ref=invocation["invocation_id"],
    )
    role_ack = writer.submit(
        WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=_di_key(
                "decision_intelligence.role_result.recorded", role_result
            ),
            event_type="decision_intelligence.role_result.recorded",
            payload=role_result,
        )
    )
    assert role_ack.status in {"OK", "DUPLICATE_OK"}

    assessment = _assessment_payload(
        request_id=request_id,
        referenced_role_result_ids=[role_result["result_id"]],
        invocation_references=[invocation["invocation_id"]],
    )
    assessment_ack = writer.submit(
        WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=_di_key(
                "decision_intelligence.assessment.recorded", assessment
            ),
            event_type="decision_intelligence.assessment.recorded",
            payload=assessment,
        )
    )
    assert assessment_ack.status in {"OK", "DUPLICATE_OK"}
    return {
        "context": context,
        "request": request,
        "invocation": invocation,
        "role_result": role_result,
        "assessment": assessment,
    }


def _invocation_payload(
    invocation_id: str,
    *,
    cost=None,
    supersedes_id=None,
    supersession_reason=None,
    request_id="request-1",
):
    payload = {
        "invocation_id": invocation_id,
        "request_id": request_id,
        "role": "REGIME_ANALYST",
        "attempt": 1,
        "provider": "provider-1",
        "model": "model-1",
        "provider_model_version": "provider-model-1",
        "route_version": "route-1",
        "prompt_version": "prompt-1",
        "reasoning_mode": "standard",
        "input_tokens": 10,
        "output_tokens": 20,
        "cached_tokens": 0,
        "latency_micros": 10,
        "queue_time_micros": 2,
        "provider_time_micros": 8,
        "billed_cost_microunits": cost,
        "estimated_cost_microunits": cost,
        "price_version": "price-1",
        "currency": "USD",
        "reconciliation_status": "UNAVAILABLE" if cost is None else "RECONCILED",
        "cost_completeness": "UNKNOWN" if cost is None else "COMPLETE",
        "started_at": "2026-01-02T03:04:00Z",
        "completed_at": "2026-01-02T03:05:00Z",
        "schema_version": 1,
        "provenance": _provenance_payload(),
        **({"supersedes_id": supersedes_id} if supersedes_id else {}),
        **({"supersession_reason": supersession_reason} if supersession_reason else {}),
    }
    payload["invocation_id"] = invocation_identity(payload)
    return payload


def _assessment_payload(
    *,
    synthesis="synthesis",
    stance="WATCH",
    completeness=7500,
    supersedes_id=None,
    supersession_reason=None,
    request_id="request-1",
    referenced_role_result_ids=None,
    invocation_references=None,
):
    payload = {
        "assessment_id": "assessment-1",
        "request_id": request_id,
        "referenced_role_result_ids": list(referenced_role_result_ids or ()),
        "synthesis": synthesis,
        "advisory_stance": stance,
        "disagreement": False,
        "completeness": completeness,
        "unsupported_claims": [],
        "evidence_refs": [],
        "status": "COMPLETED",
        "result_disposition": "ON_TIME",
        "result_selection_rule_version": "selection-1",
        "completion_time": "2026-01-02T03:04:00Z",
        "commit_time": "2026-01-02T03:05:00Z",
        "invocation_references": list(invocation_references or ()),
        "schema_version": 1,
        "provenance": _provenance_payload(),
        **({"supersedes_id": supersedes_id} if supersedes_id else {}),
        **({"supersession_reason": supersession_reason} if supersession_reason else {}),
    }
    payload["assessment_id"] = assessment_identity(payload)
    return payload


def _transition_payload(
    *,
    reason="selected",
    supersedes_id=None,
    supersession_reason=None,
    request_id="request-1",
    from_state="ELIGIBLE",
    to_state="SELECTED",
    transition_time="2026-01-02T03:00:00Z",
):
    payload = {
        "transition_id": "pending",
        "request_id": request_id,
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "transition_time": transition_time,
        "provenance": _provenance_payload(),
        "schema_version": 1,
        **({"supersedes_id": supersedes_id} if supersedes_id else {}),
        **({"supersession_reason": supersession_reason} if supersession_reason else {}),
    }
    from app.opip.decision_intelligence.events import transition_identity
    payload["transition_id"] = transition_identity(payload)
    return payload


def _role_result_payload(
    *,
    thesis="thesis",
    stance="SUPPORT",
    supersedes_id=None,
    supersession_reason=None,
    request_id="request-1",
    invocation_ref=None,
):
    payload = {
        "result_id": "pending",
        "request_id": request_id,
        "role": "REGIME_ANALYST",
        "role_version": "role-1",
        "attempt": 1,
        "route_version": "route-1",
        "prompt_version": "prompt-1",
        "model_version": "model-1",
        "invocation_ref": invocation_ref,
        "status": "COMPLETED",
        "result_disposition": "ON_TIME",
        "stance": stance,
        "thesis": thesis,
        "risks": [], "evidence_refs": [], "missing_evidence": [],
        "rubric_score": 50, "score_schema_version": 1,
        "self_reported_confidence": 50, "bull_score": 50, "bear_score": 50, "risk_score": 50,
        "provenance": _provenance_payload(), "schema_version": 1,
    }
    if supersedes_id:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["result_id"] = role_result_identity(payload)
    return payload


def test_payload_schema_version_is_independent_from_physical_schema_version():
    assert SCHEMA_VERSION == 1
    ctx = DecisionContext(
        context_id="ctx-1",
        candidate_id="cand-1",
        episode_id="ep-1",
        evaluation_id="eval-1",
        instrument_version="instr-v1",
        snapshot_id="snap-1",
        snapshot_hash="snap-hash",
        evaluation_time=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        evidence_cutoff=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        consumed_input_watermark={"history_epoch": 2, "local_sequence": 7},
        feature_version="fv-1",
        policy_version="pv-1",
        detector_version="dv-1",
        forecast_version="fcast-1",
        candidate_set_ref="set:1",
        portfolio_version_ref=None,
        environment="paper",
        eligibility=True,
        missingness={},
        source_availability_times={},
        evidence_eligibility_manifest={},
        schema_version=1,
        provenance=_provenance(),
    )
    assert ctx.schema_version == 1


def test_provenance_excludes_operational_metadata_from_semantic_identity():
    a = _provenance(emitted_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), artifact_or_build_id="build-a")
    b = _provenance(emitted_at=datetime(2026, 1, 2, 3, 5, 0, tzinfo=timezone.utc), artifact_or_build_id="build-b")
    assert a.semantic_identity() == b.semantic_identity()
    assert a.identity_hash() == b.identity_hash()


def test_evidence_available_after_cutoff_is_rejected():
    cutoff = datetime(2026, 1, 2, 3, 5, tzinfo=timezone.utc)
    values = {
        "context_id": "ctx-2",
        "candidate_id": "cand-2",
        "episode_id": "ep-2",
        "evaluation_id": "eval-2",
        "instrument_version": "instr-v2",
        "snapshot_id": "snap-2",
        "snapshot_hash": "snap-hash-2",
        "evaluation_time": cutoff,
        "evidence_cutoff": cutoff,
        "consumed_input_watermark": {"history_epoch": 1, "local_sequence": 1},
        "feature_version": "fv-1",
        "policy_version": "pv-1",
        "detector_version": "dv-1",
        "forecast_version": "fcast-1",
        "candidate_set_ref": "set:1",
        "portfolio_version_ref": None,
        "environment": "paper",
        "eligibility": True,
        "missingness": {},
        "source_availability_times": {
            "e1": cutoff + timedelta(seconds=5)
        },
        "evidence_eligibility_manifest": {
            "e1": {"available_at": cutoff - timedelta(seconds=1)}
        },
        "schema_version": 1,
        "provenance": _provenance(),
    }
    with pytest.raises(ValueError, match="evidence_cutoff"):
        DecisionContext(**values)


def test_writer_rejects_non_low_priority_and_ops_handoff_for_di_event():
    intent = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="NORMAL",
        idempotency_key="di:key:1",
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload={"schema_version": 1, **_context_payload("ctx-1")},
        ops_handoff=None,
    )
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter.__new__(CanonicalWriter)
    with pytest.raises(ValueError, match="LOW priority"):
        CanonicalWriter._validate_intent(writer, intent)

    # Low priority is accepted; no ops_handoff; exact event type is allowed.
    assert DECISION_INTELLIGENCE_CONTEXT_RECORDED in _decision_intelligence_event_types


def test_request_state_has_exactly_expected_values():
    expected = {
        "ELIGIBLE",
        "SELECTED",
        "SKIPPED_BUDGET",
        "SKIPPED_CAPACITY",
        "EXPIRED",
        "FAILED",
        "INVALID",
        "COMPLETED",
    }
    assert {state.value for state in RequestState} == expected
    assert len(RequestState) == 8


def test_committee_request_frozen_snapshot_hash_must_match_context():
    values = {
        "request_id": "req-1",
        "context_id": "ctx-1",
        "experiment_id": "exp-1",
        "cohort_selection_rule_version": "v1",
        "frozen_snapshot_hash": "bad-hash",
        "route_version": "r1",
        "prompt_version": "p1",
        "role_configuration_version": "rc1",
        "eligibility_at": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        "deadline_at": datetime(2026, 1, 2, 4, 4, tzinfo=timezone.utc),
        "budget_reservation": 10,
        "enqueue_time": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        "result_selection_rule_version": "rs1",
        "schema_version": 1,
        "provenance": _provenance(),
        "context_snapshot_hash": "good-hash",
    }
    with pytest.raises(ValueError, match="frozen_snapshot_hash"):
        CommitteeRequest(**values)


def test_timely_evidence_requires_at_least_one_on_time_result():
    assert timely_evidence_eligible(()) is False
    assert timely_evidence_eligible((ResultDisposition.ON_TIME,)) is True
    assert timely_evidence_eligible((ResultDisposition.LATE,)) is False


def test_public_contract_datetimes_are_normalized_to_utc():
    offset = timezone(timedelta(hours=2))
    request = CommitteeRequest(
        request_id="req-time",
        context_id="ctx-time",
        experiment_id="exp-time",
        cohort_selection_rule_version="v1",
        frozen_snapshot_hash="hash",
        route_version="r1",
        prompt_version="p1",
        role_configuration_version="roles1",
        eligibility_at=datetime(2026, 1, 2, 5, 0, tzinfo=offset),
        deadline_at=datetime(2026, 1, 2, 6, 0, tzinfo=offset),
        budget_reservation=1,
        enqueue_time=datetime(2026, 1, 2, 5, 5, tzinfo=offset),
        result_selection_rule_version="select1",
        provenance=_provenance(),
    )
    assert request.eligibility_at.utcoffset() == timedelta(0)
    assert request.eligibility_at.hour == 3

    transition = RequestTransition(
        transition_id="transition-time",
        request_id="req-time",
        from_state=RequestState.ELIGIBLE,
        to_state=RequestState.SELECTED,
        reason="selected",
        transition_time=datetime(2026, 1, 2, 5, 0, tzinfo=offset),
        provenance=_provenance(),
    )
    assert transition.transition_time.utcoffset() == timedelta(0)
    assert transition.transition_time.hour == 3


def test_context_freezes_manifest_and_availability_inputs():
    cutoff = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    manifest = {"e1": {"available_at": cutoff, "label": ["original"]}}
    availability = {"source": cutoff}
    context = DecisionContext(
        context_id="ctx-freeze",
        candidate_id="candidate",
        episode_id="episode",
        evaluation_id="evaluation",
        instrument_version="instrument",
        snapshot_id="snapshot",
        snapshot_hash="hash",
        evaluation_time=cutoff,
        evidence_cutoff=cutoff,
        consumed_input_watermark={"history_epoch": 1, "local_sequence": 1},
        feature_version="features",
        policy_version="policy",
        detector_version="detector",
        forecast_version="forecast",
        candidate_set_ref="set",
        portfolio_version_ref=None,
        environment="paper",
        eligibility=True,
        missingness={},
        source_availability_times=availability,
        evidence_eligibility_manifest=manifest,
        provenance=_provenance(),
    )
    manifest["backfill"] = {"available_at": cutoff}
    manifest["e1"]["label"].append("mutated")
    availability["other"] = cutoff
    with pytest.raises(ValueError, match="not in frozen manifest"):
        context.validate_evidence_refs(("backfill",))
    assert context.evidence_eligibility_manifest["e1"]["label"] == ("original",)
    assert "other" not in context.source_availability_times


def test_malformed_watermarks_fail_closed_at_writer_boundary(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        malformed_context = _context_payload("ctx-bad-watermark")
        malformed_context["consumed_input_watermark"] = {}
        context_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key="di:bad-watermark:context",
                event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                payload=malformed_context,
            )
        )
        malformed_comparison = {
            "schema_version": 1,
            "comparison_id": "placeholder",
            "decision_context_id": "ctx",
            "baseline_decision_id": "baseline",
            "committee_assessment_id": "assessment",
            "committee_request_id": "request",
            "experiment_id": "experiment",
            "variant_version": "v1",
            "environment": "paper",
            "research_account_id": "research",
            "evaluation_window_start": "2026-01-02T03:00:00Z",
            "evaluation_window_end": "2026-01-02T04:00:00Z",
            "common_outcome_horizon": 1,
            "as_of_watermark": "garbage",
            "timeliness_eligibility": False,
            "baseline_policy_version": "policy",
            "simulated_policy_ref": None,
            "execution_model_version": "exec",
            "fee_policy_version": "fee",
            "attribution_method_version": "attr",
            "cost_allocation_version": "cost",
            "currency": "USD",
            "invocation_refs": [],
            "ai_cost_attributed": None,
            "other_incremental_operating_cost": None,
            "cost_reconciliation_status": "UNKNOWN",
            "cost_completeness": "UNKNOWN",
            "baseline_trading_net": 0,
            "variant_trading_net": 0,
            "incremental_trading_net": 0,
            "incremental_operating_net": 0,
            "coverage_grade": "UNKNOWN",
            "uncertainty_method_version": "u1",
            "uncertainty_result": "UNKNOWN",
            "provenance": _provenance_payload(),
            "advisory_disposition": None,
        }
        from app.opip.decision_intelligence.events import comparison_identity
        malformed_comparison["comparison_id"] = comparison_identity(
            malformed_comparison
        )
        comparison_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key="di:bad-watermark:comparison",
                event_type="decision_intelligence.comparison.recorded",
                payload=malformed_comparison,
            )
        )
    finally:
        writer.close()
    assert context_ack.error_code == "INVALID_INTENT"
    assert comparison_ack.error_code == "INVALID_INTENT"


def test_comparison_timely_requires_explicit_on_time_disposition():
    values = {
        "comparison_id": "comparison-direct",
        "decision_context_id": "context",
        "baseline_decision_id": "baseline",
        "committee_assessment_id": "assessment",
        "committee_request_id": "request",
        "experiment_id": "experiment",
        "variant_version": "v1",
        "environment": "paper",
        "research_account_id": "research",
        "evaluation_window_start": datetime(2026, 1, 2, 3, tzinfo=timezone.utc),
        "evaluation_window_end": datetime(2026, 1, 2, 4, tzinfo=timezone.utc),
        "common_outcome_horizon": 1,
        "as_of_watermark": {"history_epoch": 1, "local_sequence": 1},
        "timeliness_eligibility": True,
        "baseline_policy_version": "policy",
        "simulated_policy_ref": None,
        "execution_model_version": "exec",
        "fee_policy_version": "fee",
        "attribution_method_version": "attr",
        "cost_allocation_version": "cost",
        "currency": "USD",
        "invocation_refs": (),
        "ai_cost_attributed": None,
        "other_incremental_operating_cost": None,
        "cost_reconciliation_status": "UNKNOWN",
        "cost_completeness": "UNKNOWN",
        "baseline_trading_net": 0,
        "variant_trading_net": 0,
        "incremental_trading_net": 0,
        "incremental_operating_net": 0,
        "coverage_grade": "UNKNOWN",
        "uncertainty_method_version": "u1",
        "uncertainty_result": "UNKNOWN",
        "provenance": _provenance(),
        "advisory_disposition": None,
    }
    with pytest.raises(ValueError, match="explicit ON_TIME"):
        ComparisonRecord(**values)
    values["advisory_disposition"] = ResultDisposition.ON_TIME
    record = ComparisonRecord(**values)
    assert record.timeliness_eligibility is True

    values["timeliness_eligibility"] = "false"
    with pytest.raises(ValueError, match="must be a boolean"):
        ComparisonRecord(**values)


def test_result_disposition_enforces_late_not_earlier_feasibility():
    assert ResultDisposition.ON_TIME.value == "ON_TIME"
    assert ResultDisposition.LATE.value == "LATE"


def test_request_transition_rejects_undeclared_state_move():
    transition_time = datetime(2026, 1, 2, tzinfo=timezone.utc)
    provenance = _provenance()
    with pytest.raises(ValueError):
        RequestTransition(
            transition_id="transition-1",
            request_id="request-1",
            from_state=RequestState.ELIGIBLE,
            to_state=RequestState.COMPLETED,
            reason="invalid transition",
            transition_time=transition_time,
            schema_version=1,
            provenance=provenance,
        )


def test_di_writer_duplicate_conflict_is_semantic_and_isolated(tmp_path):
    from app.opip.canonical.schema import connect, validate_canonical_sqlite
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    try:
        base_payload = {"schema_version": 1, **_context_payload("ctx-1", value=1)}
        base = WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=_di_key(
                DECISION_INTELLIGENCE_CONTEXT_RECORDED, base_payload
            ),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=base_payload,
        )
        first = writer.submit(base)
        duplicate = writer.submit(base)
        conflict = writer.submit(
            WriterIntent(
                schema_version=SCHEMA_VERSION,
                priority="LOW",
                idempotency_key=base.idempotency_key,
                event_type=base.event_type,
                payload={"schema_version": 1, **_context_payload("ctx-1", value=2)},
            )
        )
        assert first.status == "OK"
        assert duplicate.status == "DUPLICATE_OK"
        assert conflict.status == "REJECTED"
        assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    finally:
        writer.close()

    validate_canonical_sqlite(db)
    conn = connect(db, read_only=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM alert_ops_handoffs"
        ).fetchone()[0] == 0
        watermark = conn.execute(
            "SELECT history_epoch, local_sequence FROM watermarks WHERE stream = ?",
            (DECISION_INTELLIGENCE_STREAM,),
        ).fetchone()
        assert watermark is not None
        assert (watermark["history_epoch"], watermark["local_sequence"]) == (1, 1)
        assert conn.execute(
            "SELECT COUNT(*) FROM alert_identity_projection"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_di_writer_ignores_operational_provenance_but_rejects_substantive_conflict(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    try:
        payload = {"schema_version": 1, **_context_payload("ctx-provenance", value=1)}
        first = writer.submit(WriterIntent(
            schema_version=SCHEMA_VERSION, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=payload,
        ))
        changed_provenance = {**payload, "provenance": {**payload["provenance"], "artifact_or_build_id": "build-99", "emitted_at": "2026-01-02T04:00:00Z", "process_instance_id": "proc-99"}}
        duplicate = writer.submit(WriterIntent(
            schema_version=SCHEMA_VERSION, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_CONTEXT_RECORDED, changed_provenance),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=changed_provenance,
        ))
        conflict = writer.submit(WriterIntent(
            schema_version=SCHEMA_VERSION, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_CONTEXT_RECORDED, changed_provenance),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload={**changed_provenance, "missingness": {"value": 2}},
        ))
        assert first.status == "OK"
        assert duplicate.status == "DUPLICATE_OK"
        assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    finally:
        writer.close()


def test_di_envelope_is_low_only_and_requires_schema_and_provenance():
    envelope = DIEventEnvelope(
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload={"schema_version": 1, **_context_payload("ctx-envelope")},
        provenance=_provenance(),
    )
    intent = envelope.to_writer_intent(idempotency_key="di:envelope:1")
    assert intent.priority == "LOW"
    assert intent.ops_handoff is None
    invalid_provenance = _provenance()
    with pytest.raises(ValueError, match="schema_version"):
        DIEventEnvelope(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload={"context_id": "missing-schema"},
            provenance=invalid_provenance,
        )


def test_assessment_completeness_basis_points_support_duplicate_and_conflict(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_ASSESSMENT_RECORDED

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer)
        assessment = _assessment_payload(request_id=request_id)
        changed_assessment = _assessment_payload(
            stance="OPPOSE",
            completeness=8000,
            request_id=request_id,
        )
        assessment_key = _di_key(DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, assessment)
        first = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=assessment_key, event_type=DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, payload=assessment))
        duplicate = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=assessment_key, event_type=DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, payload=assessment))
        conflict = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=assessment_key, event_type=DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, payload=changed_assessment))
    finally:
        writer.close()
    assert first.status == "OK"
    assert duplicate.status == "DUPLICATE_OK"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_assessment_supersession_persists_original_and_new_identity(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_ASSESSMENT_RECORDED

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer)
        original = _assessment_payload(request_id=request_id)
        correction = _assessment_payload(
            stance="OPPOSE",
            completeness=8000,
            supersedes_id=original["assessment_id"],
            supersession_reason="review correction",
            request_id=request_id,
        )
        first = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, original), event_type=DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, payload=original))
        second = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, correction), event_type=DECISION_INTELLIGENCE_ASSESSMENT_RECORDED, payload=correction))
    finally:
        writer.close()
    assert first.status == "OK"
    assert second.status == "OK"
    conn = connect(tmp_path / "canonical.sqlite3", read_only=True)
    try:
        rows = conn.execute("SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence", (DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert json.loads(rows[1]["payload_json"])["supersedes_id"] == json.loads(rows[0]["payload_json"])["assessment_id"]


def test_raw_binary_float_is_rejected_as_ack_not_exception(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ack = writer.submit(WriterIntent(
            schema_version=1, priority="LOW", idempotency_key="di:float:1",
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload={**_context_payload("ctx-float"), "missingness": {"score": 0.5}},
        ))
    finally:
        writer.close()
    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_request_requires_persisted_context_and_matching_snapshot(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        missing = _request_payload(_context_payload("ctx-missing"))
        missing_ack = writer.submit(WriterIntent(
            schema_version=1, priority="LOW", idempotency_key=_di_key("decision_intelligence.request.recorded", missing),
            event_type="decision_intelligence.request.recorded", payload=missing,
        ))
        context = _context_payload("ctx-existing")
        context_ack = writer.submit(WriterIntent(
            schema_version=1, priority="LOW", idempotency_key=_di_key("decision_intelligence.context.recorded", context),
            event_type="decision_intelligence.context.recorded", payload=context,
        ))
        mismatch = _request_payload(context)
        mismatch["frozen_snapshot_hash"] = "wrong-snapshot"
        mismatch_ack = writer.submit(WriterIntent(
            schema_version=1, priority="LOW", idempotency_key=_di_key("decision_intelligence.request.recorded", mismatch),
            event_type="decision_intelligence.request.recorded", payload=mismatch,
        ))
        matching = _request_payload(context)
        matching_ack = writer.submit(WriterIntent(
            schema_version=1, priority="LOW", idempotency_key=_di_key("decision_intelligence.request.recorded", matching),
            event_type="decision_intelligence.request.recorded", payload=matching,
        ))
        duplicate_ack = writer.submit(WriterIntent(
            schema_version=1, priority="LOW", idempotency_key=_di_key("decision_intelligence.request.recorded", matching),
            event_type="decision_intelligence.request.recorded", payload=matching,
        ))
    finally:
        writer.close()
    assert missing_ack.error_code == "INVALID_INTENT"
    assert context_ack.status == "OK"
    assert mismatch_ack.error_code == "INVALID_INTENT"
    assert matching_ack.status == "OK"
    assert duplicate_ack.status == "DUPLICATE_OK"


def test_transition_projection_hydrates_on_restart_and_avoids_json_scan(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_TRANSITION_RECORDED

    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    request_id = _seed_di_ancestry(writer)
    selected = _transition_payload(request_id=request_id, from_state="ELIGIBLE", to_state="SELECTED")
    completed = _transition_payload(request_id=request_id, from_state="SELECTED", to_state="COMPLETED", transition_time="2026-01-02T03:01:00Z")
    for payload in (selected, completed):
        key = _di_key(DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload)
        ack = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=key, event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload=payload))
        assert ack.status == "OK"
    writer.close()
    restarted = CanonicalWriter(db)
    try:
        rejected = restarted.submit(WriterIntent(
            schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_TRANSITION_RECORDED, _transition_payload(request_id=request_id, from_state="SELECTED", to_state="FAILED", transition_time="2026-01-02T03:02:00Z")),
            event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED,
            payload=_transition_payload(request_id=request_id, from_state="SELECTED", to_state="FAILED", transition_time="2026-01-02T03:02:00Z"),
        ))
    finally:
        restarted.close()
    assert rejected.error_code == "INVALID_INTENT"
    assert "json_extract(payload_json, '$.request_id')" not in inspect.getsource(CanonicalWriter)


def test_transition_supersession_does_not_advance_projection(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_TRANSITION_RECORDED

    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    request_id = _seed_di_ancestry(writer)
    original = _transition_payload(request_id=request_id, from_state="ELIGIBLE", to_state="SELECTED")
    assert writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_TRANSITION_RECORDED, original), event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload=original)).status == "OK"
    correction = _transition_payload(request_id=request_id, from_state="ELIGIBLE", to_state="SELECTED", reason="corrected", supersedes_id=original["transition_id"], supersession_reason="review")
    assert writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_TRANSITION_RECORDED, correction), event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload=correction)).status == "OK"
    completed = _transition_payload(request_id=request_id, from_state="SELECTED", to_state="COMPLETED", transition_time="2026-01-02T03:01:00Z")
    result = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_TRANSITION_RECORDED, completed), event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload=completed))
    writer.close()
    assert result.status == "OK"


def test_content_derived_context_id_is_required_at_writer_boundary(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        wrong = {**_context_payload("ctx-wrong"), "context_id": "arbitrary"}
        rejected = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key="di:id:wrong", event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=wrong))
        accepted_payload = _context_payload("ctx-right")
        accepted = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_CONTEXT_RECORDED, accepted_payload), event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=accepted_payload))
    finally:
        writer.close()
    assert rejected.error_code == "INVALID_INTENT"
    assert accepted.status == "OK"


def test_role_attempt_semantics_conflict_under_same_derived_identity(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer)
        first_payload = _role_result_payload(request_id=request_id)
        role_key = _di_key(DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED, first_payload)
        first = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=role_key, event_type=DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED, payload=first_payload))
        changed = _role_result_payload(
            thesis="changed thesis",
            stance="OPPOSE",
            request_id=request_id,
        )
        duplicate_or_conflict = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=role_key, event_type=DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED, payload=changed))
    finally:
        writer.close()
    assert first.status == "OK"
    assert duplicate_or_conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_transition_correction_has_new_identity_and_supersedes_original():
    from app.opip.decision_intelligence.events import transition_identity

    original = {"request_id": "req", "from_state": "ELIGIBLE", "to_state": "SELECTED", "transition_time": "2026-01-02T03:00:00Z", "reason": "selected", "supersedes_id": None, "supersession_reason": None}
    correction = {**original, "reason": "corrected", "supersedes_id": transition_identity(original), "supersession_reason": "operator correction"}
    assert transition_identity(correction) != transition_identity(original)
    assert correction["supersedes_id"] == transition_identity(original)


def test_transition_reason_conflicts_and_supersession_persists_both(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_TRANSITION_RECORDED

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer)
        original = _transition_payload(request_id=request_id)
        changed_reason = _transition_payload(
            reason="different reason",
            request_id=request_id,
        )
        correction = _transition_payload(
            reason="corrected",
            supersedes_id=original["transition_id"],
            supersession_reason="review correction",
            request_id=request_id,
        )
        original_key = _di_key(DECISION_INTELLIGENCE_TRANSITION_RECORDED, original)
        first = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=original_key, event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload=original))
        conflict = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=original_key, event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload=changed_reason))
        second = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_TRANSITION_RECORDED, correction), event_type=DECISION_INTELLIGENCE_TRANSITION_RECORDED, payload=correction))
    finally:
        writer.close()
    assert first.status == "OK"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    assert second.status == "OK"
    conn = connect(tmp_path / "canonical.sqlite3", read_only=True)
    try:
        rows = conn.execute("SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence", (DECISION_INTELLIGENCE_TRANSITION_RECORDED,)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert json.loads(rows[1]["payload_json"])["supersedes_id"] == json.loads(rows[0]["payload_json"])["transition_id"]


def test_equivalent_offset_timestamps_dedupe_after_normalization(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    first_payload = _context_payload("ctx-offset")
    first_payload["evaluation_time"] = "2026-01-02T03:00:00+02:00"
    first_payload["evidence_cutoff"] = "2026-01-02T03:00:00+02:00"
    second_payload = _context_payload("ctx-offset")
    second_payload["evaluation_time"] = "2026-01-02T01:00:00Z"
    second_payload["evidence_cutoff"] = "2026-01-02T01:00:00Z"
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        offset_key = _di_key(DECISION_INTELLIGENCE_CONTEXT_RECORDED, first_payload)
        first = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=offset_key, event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=first_payload))
        duplicate = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=offset_key, event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=second_payload))
    finally:
        writer.close()
    assert first.status == "OK"
    assert duplicate.status == "DUPLICATE_OK"


def test_di_schema_version_is_explicit_and_exactly_one(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        missing = dict(_context_payload("ctx-schema-missing"))
        missing.pop("schema_version")
        unsupported = {
            **_context_payload("ctx-schema-unsupported"),
            "schema_version": 2,
        }
        missing_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key="di:schema:missing",
                event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                payload=missing,
            )
        )
        unsupported_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key="di:schema:unsupported",
                event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                payload=unsupported,
            )
        )
        malformed_acks = []
        for index, bad_version in enumerate((True, "1", 1.0)):
            malformed = {
                **_context_payload(f"ctx-schema-malformed-{index}"),
                "schema_version": bad_version,
            }
            malformed_acks.append(
                writer.submit(
                    WriterIntent(
                        schema_version=1,
                        priority="LOW",
                        idempotency_key=f"di:schema:malformed:{index}",
                        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                        payload=malformed,
                    )
                )
            )
    finally:
        writer.close()

    assert missing_ack.error_code == "INVALID_INTENT"
    assert unsupported_ack.error_code == "INVALID_INTENT"
    assert all(ack.status == "REJECTED" for ack in malformed_acks)
    assert all(ack.error_code == "INVALID_INTENT" for ack in malformed_acks)


def test_collision_safe_identities_and_golden_hash_vector():
    from app.opip.decision_intelligence.events import _identity

    assert _identity("DI", "a:b", "c") != _identity("DI", "a", "b:c")
    assert stable_hash("GOLDEN", {"a": 1, "b": "x"}) == "GOLDEN:ecf9e98ec0641e23113ff3ce8bdc78d0"


def test_nested_aware_datetimes_are_canonicalized_and_naive_rejected(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter

    aware = datetime(2026, 1, 2, 3, 4, tzinfo=timezone(timedelta(hours=2)))
    payload = _context_payload("ctx-nested")
    payload["source_availability_times"] = {"source": aware}
    payload["evidence_eligibility_manifest"] = {"e1": {"available_at": aware}}
    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    try:
        ack = writer.submit(WriterIntent(schema_version=1, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload), event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=payload))
    finally:
        writer.close()
    assert ack.status == "OK"
    conn = connect(db, read_only=True)
    try:
        stored = conn.execute("SELECT payload_json FROM events").fetchone()["payload_json"]
    finally:
        conn.close()
    assert "2026-01-02T01:04:00Z" in stored
    payload["source_availability_times"] = {
        "source": datetime(2026, 1, 2, 3, 4)
    }
    provenance = _provenance()
    with pytest.raises(ValueError, match="timezone-aware"):
        DIEventEnvelope(
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
            provenance=provenance,
        )


def test_content_derived_identities_ignore_external_ids_and_provenance():
    context = _context_payload("external-a")
    same_semantics = {**context, "context_id": "external-b", "provenance": {**context["provenance"], "artifact_or_build_id": "other"}}
    assert context_identity(context) == context_identity(same_semantics)
    role = {"request_id": "req", "role": "REGIME_ANALYST", "role_version": "v1", "route_version": "r1", "prompt_version": "p1", "model_version": "m1", "attempt": 1, "stance": "SUPPORT", "thesis": "x", "rubric_score": 50, "self_reported_confidence": 50, "bull_score": 50, "bear_score": 50, "risk_score": 50}
    assert role_result_identity(role) == role_result_identity(dict(role))


def test_raw_writer_rejects_authority_fields_and_invalid_role():
    from app.opip.canonical.writer import CanonicalWriter

    payload = {**_context_payload("ctx-authority"), "trade_authority": True}
    writer = CanonicalWriter.__new__(CanonicalWriter)
    authority_intent = WriterIntent(
        schema_version=1,
        priority="LOW",
        idempotency_key="di:authority",
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload=payload,
    )
    with pytest.raises(ValueError, match="unknown fields"):
        CanonicalWriter._validate_intent(writer, authority_intent)

    invalid_role = _invocation_payload("inv-invalid")
    invalid_role["role"] = "EXECUTION"
    role_intent = WriterIntent(
        schema_version=1,
        priority="LOW",
        idempotency_key="di:role",
        event_type="decision_intelligence.invocation.recorded",
        payload=invalid_role,
    )
    with pytest.raises(ValueError, match="invalid role"):
        CanonicalWriter._validate_intent(writer, role_intent)


def test_di_identity_is_epoch_invariant_and_request_varies_by_model():
    context_key = context_idempotency_key(context_id="ctx-1")
    expected_context_key = stable_hash(
        DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        {"components": ["ctx-1"]},
    )
    assert context_key == expected_context_key
    assert request_idempotency_key(
        request_id="req-1"
    ) != request_idempotency_key(
        request_id="req-2"
    )
    assert role_result_idempotency_key(
        request_id="req-1", role="REGIME_ANALYST", role_version="role-1",
        route_version="r1", prompt_version="p1", attempt=1, model_version="m1",
    ) != role_result_idempotency_key(
        request_id="req-1", role="REGIME_ANALYST", role_version="role-1",
        route_version="r1", prompt_version="p1", attempt=1, model_version="m2",
    )


def test_context_evidence_refs_use_frozen_manifest_and_cutoff_equality():
    cutoff = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    context = DecisionContext(
        context_id="ctx-evidence",
        candidate_id="candidate-1",
        episode_id="episode-1",
        evaluation_id="evaluation-1",
        instrument_version="instrument-1",
        snapshot_id="snapshot-1",
        snapshot_hash="snapshot-hash",
        evaluation_time=cutoff,
        evidence_cutoff=cutoff,
        consumed_input_watermark={"history_epoch": 1, "local_sequence": 1},
        feature_version="features-1",
        policy_version="policy-1",
        detector_version="detector-1",
        forecast_version="forecast-1",
        candidate_set_ref="set-1",
        portfolio_version_ref=None,
        environment="paper",
        eligibility=True,
        missingness={},
        source_availability_times={},
        evidence_eligibility_manifest={
            "e1": {"available_at": cutoff},
        },
        provenance=_provenance(),
    )
    context.validate_evidence_refs(("e1",))
    with pytest.raises(ValueError, match="not in frozen manifest"):
        context.validate_evidence_refs(("missing",))


def test_invalid_ordinal_score_and_stance_are_rejected():
    from app.opip.decision_intelligence import CommitteeRoleResult

    kwargs = dict(
        result_id="result-1", request_id="request-1", role=CommitteeRole.REGIME_ANALYST,
        role_version="role-1", attempt=1, route_version="route-1",
        prompt_version="prompt-1", model_version="model-1", invocation_ref=None,
        status="COMPLETED", result_disposition=ResultDisposition.ON_TIME,
        stance=AdvisoryStance.SUPPORT, thesis="thesis", risks=(), evidence_refs=(),
        missing_evidence=(), rubric_score=1, score_schema_version=1,
        self_reported_confidence=1, bull_score=1, bear_score=1, risk_score=1,
        provenance=_provenance(),
    )
    CommitteeRoleResult(**kwargs)
    with pytest.raises(ValueError, match="ordinal"):
        CommitteeRoleResult(**{**kwargs, "self_reported_confidence": 0.5})
    with pytest.raises(ValueError, match="invalid stance"):
        CommitteeRoleResult(**{**kwargs, "stance": "EXECUTE"})


def _context_for_acceptance(*, snapshot_hash: str = "snapshot-hash", epoch: int = 1) -> DecisionContext:
    cutoff = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    return DecisionContext(
        context_id="ctx-acceptance",
        candidate_id="candidate-1",
        episode_id="episode-1",
        evaluation_id="evaluation-1",
        instrument_version="instrument-1",
        snapshot_id="snapshot-1",
        snapshot_hash=snapshot_hash,
        evaluation_time=cutoff,
        evidence_cutoff=cutoff,
        consumed_input_watermark={"history_epoch": epoch, "local_sequence": 7},
        feature_version="features-1",
        policy_version="policy-1",
        detector_version="detector-1",
        forecast_version="forecast-1",
        candidate_set_ref="set-1",
        portfolio_version_ref=None,
        environment="paper",
        eligibility=True,
        missingness={},
        source_availability_times={},
        evidence_eligibility_manifest={"e1": {"available_at": cutoff}},
        provenance=_provenance(),
    )


def _model_invocation(**overrides):
    values = {
        "invocation_id": "inv-1", "request_id": "req-1", "role": CommitteeRole.REGIME_ANALYST,
        "attempt": 1, "provider": "test", "model": "model-1",
        "provider_model_version": "provider-model-1", "route_version": "route-1",
        "prompt_version": "prompt-1", "reasoning_mode": "standard",
        "input_tokens": 10, "output_tokens": 20, "cached_tokens": 0,
        "latency_micros": 100, "queue_time_micros": 10, "provider_time_micros": 80,
        "billed_cost_microunits": 5, "estimated_cost_microunits": 5,
        "price_version": "price-1", "currency": "USD",
        "reconciliation_status": "RECONCILED", "cost_completeness": "COMPLETE",
        "started_at": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 1, 2, 3, 5, tzinfo=timezone.utc),
        "provenance": _provenance(),
    }
    values.update(overrides)
    return ModelInvocation(**values)


def test_acceptance_lifecycle_table_and_terminal_states():
    assert set(RequestState) == {
        RequestState.ELIGIBLE, RequestState.SELECTED, RequestState.SKIPPED_BUDGET,
        RequestState.SKIPPED_CAPACITY, RequestState.EXPIRED, RequestState.FAILED,
        RequestState.INVALID, RequestState.COMPLETED,
    }
    assert not hasattr(RequestState, "LATE")
    assert ResultDisposition.LATE.value == "LATE"
    transition_time = datetime(2026, 1, 2, tzinfo=timezone.utc)
    provenance = _provenance()
    for terminal in {
        RequestState.SKIPPED_BUDGET, RequestState.SKIPPED_CAPACITY, RequestState.EXPIRED,
        RequestState.FAILED, RequestState.INVALID, RequestState.COMPLETED,
    }:
        with pytest.raises(ValueError, match="terminal"):
            RequestTransition(
                transition_id="t",
                request_id="req",
                from_state=terminal,
                to_state=RequestState.ELIGIBLE,
                reason="invalid",
                transition_time=transition_time,
                provenance=provenance,
            )
    RequestTransition(
        transition_id="t1",
        request_id="req",
        from_state=RequestState.ELIGIBLE,
        to_state=RequestState.SELECTED,
        reason="selected",
        transition_time=transition_time,
        provenance=provenance,
    )
    RequestTransition(
        transition_id="t2",
        request_id="req",
        from_state=RequestState.SELECTED,
        to_state=RequestState.COMPLETED,
        reason="done",
        transition_time=transition_time,
        provenance=provenance,
    )


def test_acceptance_superseding_correction_preserves_original_record():
    original = _model_invocation()
    correction = replace(
        original,
        invocation_id="inv-1-correction",
        billed_cost_microunits=7,
        estimated_cost_microunits=7,
        reconciliation_status="RECONCILED",
        supersedes_id=original.invocation_id,
        supersession_reason="provider invoice correction",
    )
    assert original.billed_cost_microunits == 5
    assert correction.supersedes_id == original.invocation_id
    assert correction.invocation_id != original.invocation_id


def test_acceptance_late_result_is_not_eligible_for_timely_comparison():
    assert ResultDisposition.LATE is not ResultDisposition.ON_TIME
    late_result = CommitteeRoleResult(
        result_id="late-1", request_id="req-1", role=CommitteeRole.REGIME_ANALYST, role_version="v1",
        attempt=1, route_version="r1", prompt_version="p1", model_version="m1",
        invocation_ref="inv-1", status="COMPLETED", result_disposition=ResultDisposition.LATE,
        stance=AdvisoryStance.WATCH, thesis="late", risks=(), evidence_refs=(), missing_evidence=(),
        rubric_score=None, score_schema_version=1, self_reported_confidence=None,
        bull_score=None, bear_score=None, risk_score=None,
        provenance=_provenance(),
    )
    assert late_result.result_disposition is ResultDisposition.LATE
    assert "timeliness_eligibility" in ComparisonRecord.__dataclass_fields__
    assert timely_evidence_eligible((late_result.result_disposition,)) is False
    assert timely_evidence_eligible((ResultDisposition.ON_TIME,)) is True


def test_comparison_structurally_rejects_late_timely_evidence():
    values = {
        "comparison_id": "comparison-1", "decision_context_id": "context-1",
        "baseline_decision_id": "baseline-1", "committee_assessment_id": "assessment-1",
        "committee_request_id": "request-1", "experiment_id": "experiment-1",
        "variant_version": "variant-1", "environment": "paper",
        "research_account_id": "research-1",
        "evaluation_window_start": datetime(2026, 1, 2, 3, tzinfo=timezone.utc),
        "evaluation_window_end": datetime(2026, 1, 2, 4, tzinfo=timezone.utc),
        "common_outcome_horizon": 1,
        "as_of_watermark": {"history_epoch": 1, "local_sequence": 1},
        "timeliness_eligibility": True, "baseline_policy_version": "b1",
        "simulated_policy_ref": None, "execution_model_version": "e1",
        "fee_policy_version": "f1", "attribution_method_version": "a1",
        "cost_allocation_version": "c1", "currency": "USD", "invocation_refs": [],
        "ai_cost_attributed": None, "other_incremental_operating_cost": None,
        "cost_reconciliation_status": "UNKNOWN", "cost_completeness": "UNKNOWN",
        "baseline_trading_net": 0, "variant_trading_net": 0, "incremental_trading_net": 0,
        "incremental_operating_net": 0, "coverage_grade": "UNKNOWN",
        "uncertainty_method_version": "u1", "uncertainty_result": "UNKNOWN",
        "provenance": _provenance(), "schema_version": 1,
        "advisory_disposition": ResultDisposition.LATE,
    }
    with pytest.raises(ValueError, match="LATE"):
        ComparisonRecord(**values)
    values["timeliness_eligibility"] = False
    ComparisonRecord(**values)


def test_acceptance_unknown_cost_and_superseded_billing_reconciliation():
    unknown = _model_invocation(
        billed_cost_microunits=None, estimated_cost_microunits=None,
        cost_completeness="UNKNOWN", reconciliation_status="UNAVAILABLE",
    )
    assert unknown.billed_cost_microunits is None
    assert unknown.estimated_cost_microunits is None
    correction = replace(
        unknown, invocation_id="inv-1-correction", billed_cost_microunits=11,
        estimated_cost_microunits=11, cost_completeness="COMPLETE",
        reconciliation_status="RECONCILED", supersedes_id=unknown.invocation_id,
        supersession_reason="billing record arrived",
    )
    assert correction.supersedes_id == unknown.invocation_id
    assert unknown.cost_completeness == "UNKNOWN"


def test_acceptance_model_invocation_correction_is_append_only_canonical_path(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import DECISION_INTELLIGENCE_INVOCATION_RECORDED

    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    try:
        request_id = _seed_di_ancestry(writer)
        original = _invocation_payload(
            "inv-original",
            request_id=request_id,
        )
        correction = _invocation_payload(
            "inv-correction",
            cost=11,
            supersedes_id=original["invocation_id"],
            supersession_reason="billing reconciliation",
            request_id=request_id,
        )
        first = writer.submit(WriterIntent(schema_version=SCHEMA_VERSION, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_INVOCATION_RECORDED, original), event_type=DECISION_INTELLIGENCE_INVOCATION_RECORDED, payload=original))
        second = writer.submit(WriterIntent(schema_version=SCHEMA_VERSION, priority="LOW", idempotency_key=_di_key(DECISION_INTELLIGENCE_INVOCATION_RECORDED, correction), event_type=DECISION_INTELLIGENCE_INVOCATION_RECORDED, payload=correction))
    finally:
        writer.close()
    assert first.status == "OK"
    assert second.status == "OK"
    conn = connect(db, read_only=True)
    try:
        rows = conn.execute("SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence", (DECISION_INTELLIGENCE_INVOCATION_RECORDED,)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    original_id = json.loads(rows[0]["payload_json"])["invocation_id"]
    assert original_id == original["invocation_id"]
    assert json.loads(rows[1]["payload_json"])["supersedes_id"] == original_id


def test_acceptance_backfill_cannot_revise_context_and_snapshot_link_is_frozen():
    context = _context_for_acceptance()
    context.validate_evidence_refs(("e1",))
    with pytest.raises(ValueError, match="not in frozen manifest"):
        context.validate_evidence_refs(("backfilled-evidence",))
    request = CommitteeRequest(
        request_id="req-1", context_id=context.context_id, experiment_id="exp-1",
        cohort_selection_rule_version="cohort-1", frozen_snapshot_hash=context.snapshot_hash,
        route_version="route-1", prompt_version="prompt-1", role_configuration_version="roles-1",
        eligibility_at=context.evaluation_time, deadline_at=context.evidence_cutoff,
        budget_reservation=1, enqueue_time=context.evaluation_time,
        result_selection_rule_version="select-1", context_snapshot_hash=context.snapshot_hash,
        provenance=_provenance(),
    )
    request.validate_against_context(context)
    changed_context = _context_for_acceptance(snapshot_hash="changed")
    with pytest.raises(ValueError, match="snapshot_hash"):
        request.validate_against_context(changed_context)


def test_acceptance_typed_provenance_and_semantic_exclusion():
    assert isinstance(_provenance(), Provenance)
    emitted_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="required"):
        Provenance(
            producing_component="",
            artifact_or_build_id="build",
            process_instance_id="proc",
            emitted_at=emitted_at,
            source_record_refs=("source",),
        )
    first = _provenance(artifact_or_build_id="build-1", process_instance_id="proc-1")
    second = _provenance(artifact_or_build_id="build-2", process_instance_id="proc-2", emitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert first.identity_hash() == second.identity_hash()


def test_acceptance_backup_restore_preserves_payload_hash_and_increments_epoch(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter

    live = tmp_path / "live.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    payload = {"schema_version": 1, **_context_payload("ctx-restore")}
    restore_key = _di_key(DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload)
    writer = CanonicalWriter(live)
    try:
        ack = writer.submit(WriterIntent(
            schema_version=SCHEMA_VERSION, priority="LOW", idempotency_key=restore_key,
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=payload,
        ))
        assert ack.status == "OK"
        writer.checkpoint_wal()
    finally:
        writer.close()
    before_conn = connect(live, read_only=True)
    try:
        before_payload_json = before_conn.execute(
            "SELECT payload_json FROM events WHERE idempotency_key = ?",
            (restore_key,),
        ).fetchone()["payload_json"]
    finally:
        before_conn.close()
    before_payload_hash = hashlib.sha256(before_payload_json.encode("utf-8")).hexdigest()
    backup_database(live, backup)
    before_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    manifest = build_backup_manifest(backup_path=backup)
    result = restore_from_backup(backup_db=backup, live_db=restored)
    assert manifest["sha256"] == before_hash
    assert result["history_epoch"] == 2
    conn = connect(restored, read_only=True)
    try:
        row = conn.execute(
            "SELECT payload_json FROM events WHERE idempotency_key = ?",
            (restore_key,),
        ).fetchone()
        epoch = conn.execute("SELECT history_epoch FROM meta WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
    stored_payload = json.loads(row["payload_json"])
    restored_payload_json = row["payload_json"]
    original_payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    assert stored_payload["context_id"] == payload["context_id"]
    assert stored_payload["provenance"] == payload["provenance"]
    assert stored_payload["schema_version"] == payload["schema_version"]
    assert restored_payload_json == before_payload_json
    assert hashlib.sha256(restored_payload_json.encode("utf-8")).hexdigest() == before_payload_hash
    assert original_payload_json != ""  # normalized payload includes typed optional fields
    assert epoch == 2
    restored_writer = CanonicalWriter(restored)
    try:
        duplicate = restored_writer.submit(WriterIntent(
            schema_version=SCHEMA_VERSION, priority="LOW", idempotency_key=restore_key,
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED, payload=payload,
        ))
    finally:
        restored_writer.close()
    assert duplicate.status == "DUPLICATE_OK"


def test_acceptance_concurrent_same_key_commits_one_logical_event(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    concurrent_payload = {"schema_version": 1, **_context_payload("ctx-concurrent")}
    intent = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="LOW",
        idempotency_key=_di_key(
            DECISION_INTELLIGENCE_CONTEXT_RECORDED, concurrent_payload
        ),
        event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
        payload=concurrent_payload,
    )
    writer = CanonicalWriter(db)
    acks = []
    lock = threading.Lock()
    def submit():
        ack = writer.submit(intent)
        with lock:
            acks.append(ack)
    threads = [threading.Thread(target=submit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    writer.close()
    assert sum(ack.status == "OK" for ack in acks) == 1
    assert sum(ack.status == "DUPLICATE_OK" for ack in acks) == 7
    conn = connect(db, read_only=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        conn.close()


def test_acceptance_schema_and_authority_boundaries_are_unchanged(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    def schema_map(connection):
        table_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            table: tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
            for table in table_names
        }
    try:
        before = schema_map(writer._conn)
        schema_payload = {"schema_version": 1, **_context_payload("ctx-schema")}
        ack = writer.submit(WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=_di_key(
                DECISION_INTELLIGENCE_CONTEXT_RECORDED, schema_payload
            ),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=schema_payload,
        ))
        assert ack.status == "OK"
    finally:
        writer.close()
    conn = connect(db, read_only=True)
    try:
        after = schema_map(conn)
        assert conn.execute("SELECT schema_version FROM meta WHERE id=1").fetchone()[0] == 1
        assert "decision_intelligence.v1" in {row[0] for row in conn.execute("SELECT stream FROM watermarks")}
        assert conn.execute("SELECT COUNT(*) FROM alert_identity_projection").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM alert_ops_handoffs").fetchone()[0] == 0
        assert after == before
        assert set(after) == {"meta", "events", "idempotency_keys", "alert_identity_projection", "watermarks", "alert_ops_handoffs"}
        assert all(after[table] for table in after)
    finally:
        conn.close()


def test_acceptance_import_and_disabled_authority_boundaries():
    repo = Path(__file__).resolve().parents[1]
    runtime_roots = (
        repo / "app/opip/discovery", repo / "app/opip/decision",
        repo / "app/opip/risk", repo / "app/services", repo / "app/api",
        repo / "app/jobs",
    )
    imported = []
    for root in runtime_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
    assert not any(name == "app.opip.decision_intelligence" or name.startswith("app.opip.decision_intelligence.") for name in imported)


def test_acceptance_links_and_materialized_comparison_contracts():
    from app.opip.decision_intelligence import ContextDecisionLink

    link = ContextDecisionLink(
        link_id="link-1", context_id="ctx-1", decision_id="decision-1",
        decision_record_type="AdmissionDecisionV2", decision_schema_version=2,
        architecture_disposition="LINK_ONLY", provenance=_provenance(),
    )
    assert link.architecture_disposition == "LINK_ONLY"
    assert link.decision_record_type == "AdmissionDecisionV2"
    fields = ComparisonRecord.__dataclass_fields__
    for required in (
        "research_account_id", "attribution_method_version",
        "cost_allocation_version", "uncertainty_method_version", "supersedes_id",
    ):
        assert required in fields


def test_acceptance_golden_serialization_and_timezone_naive_failure():
    assert canonical_serialize({"b": 2, "a": [True, 1]}) == '{"a":[true,1],"b":2}'
    values = {
        "context_id": "ctx-naive",
        "candidate_id": "candidate",
        "episode_id": "episode",
        "evaluation_id": "evaluation",
        "instrument_version": "instrument",
        "snapshot_id": "snapshot",
        "snapshot_hash": "hash",
        "evaluation_time": datetime(2026, 1, 2, 3, 4),
        "evidence_cutoff": datetime(
            2026, 1, 2, 3, 4, tzinfo=timezone.utc
        ),
        "consumed_input_watermark": {
            "history_epoch": 1,
            "local_sequence": 1,
        },
        "feature_version": "features",
        "policy_version": "policy",
        "detector_version": "detector",
        "forecast_version": "forecast",
        "candidate_set_ref": "set",
        "portfolio_version_ref": None,
        "environment": "paper",
        "eligibility": True,
        "missingness": {},
        "source_availability_times": {},
        "evidence_eligibility_manifest": {},
        "provenance": _provenance(),
    }
    with pytest.raises(ValueError, match="timezone-aware"):
        DecisionContext(**values)


def test_acceptance_authority_fields_are_not_part_of_di_contracts():
    for contract in (CommitteeRequest, CommitteeRoleResult, ModelInvocation, ComparisonRecord):
        names = contract.__dataclass_fields__
        assert "trade_authority" not in names
        assert "execution_authority" not in names
        assert "order_authority" not in names


def test_acceptance_unavailable_score_is_not_zero_and_role_attempt_is_explicit():
    result = CommitteeRoleResult(
        result_id="result-unknown", request_id="req-1", role=CommitteeRole.RISK_CRITIC, role_version="v1",
        attempt=2, route_version="r1", prompt_version="p1", model_version="m1",
        invocation_ref=None, status="UNAVAILABLE", result_disposition=ResultDisposition.ON_TIME,
        stance=AdvisoryStance.ABSTAIN, thesis="unavailable", risks=(), evidence_refs=(),
        missing_evidence=("e1",), rubric_score=None, score_schema_version=1,
        self_reported_confidence=None, bull_score=None, bear_score=None, risk_score=None,
        provenance=_provenance(),
    )
    assert result.attempt == 2
    assert result.rubric_score is None
    assert result.rubric_score != 0


def test_acceptance_assessment_summary_is_request_and_synthesis_grain():
    summary = CommitteeAssessmentSummary(
        assessment_id="assessment-1", request_id="req-1",
        referenced_role_result_ids=("result-1",), synthesis="synthesis",
        advisory_stance=AdvisoryStance.WATCH, disagreement=False, completeness=None,
        unsupported_claims=(), evidence_refs=(), status="COMPLETED",
        result_disposition=ResultDisposition.ON_TIME,
        result_selection_rule_version="select-1",
        completion_time=datetime(2026, 1, 2, 3, 5, tzinfo=timezone.utc),
        commit_time=datetime(2026, 1, 2, 3, 6, tzinfo=timezone.utc),
        invocation_references=("inv-1",),
        provenance=_provenance(),
    )
    assert summary.request_id == "req-1"
    assert summary.synthesis == "synthesis"


def _valid_comparison_payload_for_regression(
    *,
    decision_context_id="ctx-regression",
    committee_assessment_id="assessment-regression",
    committee_request_id="request-regression",
    experiment_id="experiment-regression",
    invocation_refs=None,
    supersedes_id=None,
    supersession_reason=None,
):
    from app.opip.decision_intelligence.events import comparison_identity

    payload = {
        "schema_version": 1,
        "comparison_id": "placeholder",
        "decision_context_id": decision_context_id,
        "baseline_decision_id": "baseline-regression",
        "committee_assessment_id": committee_assessment_id,
        "committee_request_id": committee_request_id,
        "experiment_id": experiment_id,
        "variant_version": "v1",
        "environment": "paper",
        "research_account_id": "research",
        "evaluation_window_start": "2026-01-02T03:00:00Z",
        "evaluation_window_end": "2026-01-02T04:00:00Z",
        "common_outcome_horizon": 1,
        "as_of_watermark": {"history_epoch": 1, "local_sequence": 7},
        "timeliness_eligibility": False,
        "baseline_policy_version": "policy",
        "simulated_policy_ref": None,
        "execution_model_version": "exec",
        "fee_policy_version": "fee",
        "attribution_method_version": "attr",
        "cost_allocation_version": "cost",
        "currency": "USD",
        "invocation_refs": list(invocation_refs or ()),
        "ai_cost_attributed": None,
        "other_incremental_operating_cost": None,
        "cost_reconciliation_status": "UNKNOWN",
        "cost_completeness": "UNKNOWN",
        "baseline_trading_net": 0,
        "variant_trading_net": 0,
        "incremental_trading_net": 0,
        "incremental_operating_net": 0,
        "coverage_grade": "UNKNOWN",
        "uncertainty_method_version": "u1",
        "uncertainty_result": "UNKNOWN",
        "provenance": _provenance_payload(),
        "advisory_disposition": None,
        **({"supersedes_id": supersedes_id} if supersedes_id else {}),
        **(
            {"supersession_reason": supersession_reason}
            if supersession_reason
            else {}
        ),
    }
    payload["comparison_id"] = comparison_identity(payload)
    return payload


def test_valid_comparison_persists_after_watermark_normalization(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ancestry = _seed_complete_di_ancestry(writer)
        payload = _valid_comparison_payload_for_regression(
            decision_context_id=ancestry["context"]["context_id"],
            committee_assessment_id=ancestry["assessment"]["assessment_id"],
            committee_request_id=ancestry["request"]["request_id"],
            experiment_id=ancestry["request"]["experiment_id"],
            invocation_refs=[ancestry["invocation"]["invocation_id"]],
        )
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.comparison.recorded", payload
                ),
                event_type="decision_intelligence.comparison.recorded",
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "OK"
    assert ack.error_code is None


@pytest.mark.parametrize(
    "watermark",
    [
        {"history_epoch": True, "local_sequence": 1},
        {"history_epoch": "01", "local_sequence": 1},
        {"history_epoch": 1.0, "local_sequence": 1},
        {"history_epoch": 1, "local_sequence": False},
        {"history_epoch": 1, "local_sequence": "7"},
    ],
)
def test_di_watermark_coordinates_require_exact_non_boolean_integers(
    tmp_path, watermark
):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        context = _context_payload("ctx-strict-watermark")
        context["consumed_input_watermark"] = watermark
        context_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=f"di:strict-watermark:{repr(watermark)}",
                event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                payload=context,
            )
        )

        comparison = _valid_comparison_payload_for_regression()
        comparison["as_of_watermark"] = watermark
        comparison_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=f"di:comparison-watermark:{repr(watermark)}",
                event_type="decision_intelligence.comparison.recorded",
                payload=comparison,
            )
        )
    finally:
        writer.close()

    assert context_ack.error_code == "INVALID_INTENT"
    assert comparison_ack.error_code == "INVALID_INTENT"


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("risks", "risk-one"),
        ("evidence_refs", "EVT:one"),
        ("missing_evidence", {"source": "missing"}),
    ],
)
def test_role_result_repeated_fields_reject_scalar_or_mapping(
    tmp_path, field_name, bad_value
):
    from app.opip.canonical.writer import CanonicalWriter

    payload = _role_result_payload()
    payload[field_name] = bad_value
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=f"di:bad-array:{field_name}",
                event_type="decision_intelligence.role_result.recorded",
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.error_code == "INVALID_INTENT"


def test_role_result_idempotency_distinguishes_version_and_supersession(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    common = {
        "request_id": "request-1",
        "role": "REGIME_ANALYST",
        "route_version": "route-1",
        "prompt_version": "prompt-1",
        "attempt": 1,
        "model_version": "model-1",
    }
    key_v1 = role_result_idempotency_key(
        **common,
        role_version="role-1",
    )
    key_v2 = role_result_idempotency_key(
        **common,
        role_version="role-2",
    )
    assert key_v1 != key_v2

    first = _role_result_payload()
    correction = _role_result_payload(
        thesis="corrected thesis",
        supersedes_id=first["result_id"],
        supersession_reason="correction",
    )
    correction_key = role_result_idempotency_key(
        **common,
        role_version="role-1",
        supersedes_id=first["result_id"],
        supersession_reason="correction",
    )
    assert correction_key != key_v1

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer)
        first = _role_result_payload(request_id=request_id)
        correction = _role_result_payload(
            thesis="corrected thesis",
            supersedes_id=first["result_id"],
            supersession_reason="correction",
            request_id=request_id,
        )
        first_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.role_result.recorded", first
                ),
                event_type="decision_intelligence.role_result.recorded",
                payload=first,
            )
        )
        correction_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.role_result.recorded", correction
                ),
                event_type="decision_intelligence.role_result.recorded",
                payload=correction,
            )
        )
    finally:
        writer.close()

    assert first_ack.status == "OK"
    assert correction_ack.status == "OK"
    assert correction_ack.event_id != first_ack.event_id


def test_writer_rejects_non_boolean_timeliness_eligibility(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import comparison_identity

    payload = _valid_comparison_payload_for_regression()
    payload["timeliness_eligibility"] = "false"
    payload["advisory_disposition"] = "ON_TIME"
    payload["comparison_id"] = comparison_identity(payload)

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key="di:comparison:bad-timeliness-bool",
                event_type="decision_intelligence.comparison.recorded",
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.error_code == "INVALID_INTENT"


def test_only_optional_advisory_disposition_accepts_explicit_null(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    invalid_role = _role_result_payload()
    invalid_role["role"] = None

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ancestry = _seed_complete_di_ancestry(writer)
        comparison = _valid_comparison_payload_for_regression(
            decision_context_id=ancestry["context"]["context_id"],
            committee_assessment_id=ancestry["assessment"]["assessment_id"],
            committee_request_id=ancestry["request"]["request_id"],
            experiment_id=ancestry["request"]["experiment_id"],
            invocation_refs=[ancestry["invocation"]["invocation_id"]],
        )
        assert comparison["advisory_disposition"] is None
        valid_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.comparison.recorded", comparison
                ),
                event_type="decision_intelligence.comparison.recorded",
                payload=comparison,
            )
        )
        invalid_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key="di:role:required-null",
                event_type="decision_intelligence.role_result.recorded",
                payload=invalid_role,
            )
        )
    finally:
        writer.close()

    assert valid_ack.status == "OK"
    assert invalid_ack.error_code == "INVALID_INTENT"


def test_empty_evidence_role_result_still_requires_persisted_ancestry(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    payload = _role_result_payload(request_id="missing-request")
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.role_result.recorded",
                    payload,
                ),
                event_type="decision_intelligence.role_result.recorded",
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_transition_must_match_persisted_request_state(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer)
        selected = _transition_payload(
            request_id=request_id,
            from_state="ELIGIBLE",
            to_state="SELECTED",
            transition_time="2026-01-02T03:05:00Z",
        )
        completed = _transition_payload(
            request_id=request_id,
            from_state="SELECTED",
            to_state="COMPLETED",
            reason="completed",
            transition_time="2026-01-02T03:06:00Z",
        )
        contradictory = _transition_payload(
            request_id=request_id,
            from_state="SELECTED",
            to_state="FAILED",
            reason="late failure",
            transition_time="2026-01-02T03:07:00Z",
        )

        selected_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.transition.recorded",
                    selected,
                ),
                event_type="decision_intelligence.transition.recorded",
                payload=selected,
            )
        )
        completed_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.transition.recorded",
                    completed,
                ),
                event_type="decision_intelligence.transition.recorded",
                payload=completed,
            )
        )
        contradictory_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.transition.recorded",
                    contradictory,
                ),
                event_type="decision_intelligence.transition.recorded",
                payload=contradictory,
            )
        )
    finally:
        writer.close()

    assert selected_ack.status == "OK"
    assert completed_ack.status == "OK"
    assert contradictory_ack.status == "REJECTED"
    assert contradictory_ack.error_code == "INVALID_INTENT"


def test_request_and_transition_identity_normalize_equivalent_offsets():
    from app.opip.decision_intelligence.events import (
        request_identity,
        transition_identity,
    )

    request_z = {
        "context_id": "ctx",
        "experiment_id": "exp",
        "cohort_selection_rule_version": "cohort-1",
        "frozen_snapshot_hash": "snapshot",
        "route_version": "route-1",
        "prompt_version": "prompt-1",
        "role_configuration_version": "roles-1",
        "eligibility_at": "2026-01-02T01:00:00Z",
        "deadline_at": "2026-01-02T02:00:00Z",
        "budget_reservation": 1,
        "result_selection_rule_version": "select-1",
    }
    request_offset = {
        **request_z,
        "eligibility_at": "2026-01-02T03:00:00+02:00",
        "deadline_at": "2026-01-02T04:00:00+02:00",
    }
    assert request_identity(request_z) == request_identity(request_offset)

    transition_z = {
        "request_id": "req",
        "from_state": "ELIGIBLE",
        "to_state": "SELECTED",
        "transition_time": "2026-01-02T01:00:00Z",
        "supersedes_id": None,
        "supersession_reason": None,
    }
    transition_offset = {
        **transition_z,
        "transition_time": "2026-01-02T03:00:00+02:00",
    }
    assert transition_identity(transition_z) == transition_identity(
        transition_offset
    )


@pytest.mark.parametrize("schema_version", [2, True, "1"])
def test_provenance_schema_version_requires_exact_integer_one(schema_version):
    emitted_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="unsupported Provenance schema_version"):
        Provenance(
            producing_component="component",
            artifact_or_build_id="build",
            process_instance_id="process",
            emitted_at=emitted_at,
            source_record_refs=("source:1",),
            schema_version=schema_version,
        )


def test_canonical_mapping_keys_are_nfc_normalized_and_collisions_rejected():
    nfc_key = "\u00e9"
    nfd_key = "e\u0301"
    assert canonical_serialize({nfc_key: "value"}) == canonical_serialize(
        {nfd_key: "value"}
    )
    with pytest.raises(ValueError, match="collide after NFC normalization"):
        canonical_serialize({nfc_key: 1, nfd_key: 2})


def test_di_record_identity_is_unique_across_different_idempotency_keys(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer)
        first_payload = _role_result_payload(
            thesis="first thesis",
            request_id=request_id,
        )
        same_payload = dict(first_payload)
        conflict_payload = {
            **first_payload,
            "thesis": "conflicting thesis",
        }
        canonical_key = _di_key(
            "decision_intelligence.role_result.recorded", first_payload
        )
        first = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=canonical_key,
                event_type="decision_intelligence.role_result.recorded",
                payload=first_payload,
            )
        )
        duplicate = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=canonical_key,
                event_type="decision_intelligence.role_result.recorded",
                payload=same_payload,
            )
        )
        arbitrary = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key="di:role:arbitrary-b",
                event_type="decision_intelligence.role_result.recorded",
                payload=same_payload,
            )
        )
        conflict = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=canonical_key,
                event_type="decision_intelligence.role_result.recorded",
                payload=conflict_payload,
            )
        )
    finally:
        writer.close()

    assert first.status == "OK"
    assert duplicate.status == "DUPLICATE_OK"
    assert duplicate.event_id == first.event_id
    assert arbitrary.error_code == "INVALID_INTENT"
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"

    conn = connect(tmp_path / "canonical.sqlite3", read_only=True)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ?",
            ("decision_intelligence.role_result.recorded",),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_invocation_requires_persisted_request_ancestry(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    payload = _invocation_payload("inv-missing", request_id="missing-request")
    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.invocation.recorded", payload
                ),
                event_type="decision_intelligence.invocation.recorded",
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_role_result_invocation_must_belong_to_same_request(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_a = _seed_di_ancestry(writer)

        context_b = _context_payload("ctx-other-request", value=2)
        context_b["candidate_id"] = "candidate-other-request"
        context_b["context_id"] = context_identity(context_b)
        context_b_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.context.recorded", context_b
                ),
                event_type="decision_intelligence.context.recorded",
                payload=context_b,
            )
        )
        assert context_b_ack.status == "OK"
        request_b_payload = _request_payload(context_b)
        request_b_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.request.recorded", request_b_payload
                ),
                event_type="decision_intelligence.request.recorded",
                payload=request_b_payload,
            )
        )
        assert request_b_ack.status == "OK"

        invocation_b = _invocation_payload(
            "inv-other",
            request_id=request_b_payload["request_id"],
        )
        invocation_b_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.invocation.recorded", invocation_b
                ),
                event_type="decision_intelligence.invocation.recorded",
                payload=invocation_b,
            )
        )
        assert invocation_b_ack.status == "OK"

        role_result = _role_result_payload(
            request_id=request_a,
            invocation_ref=invocation_b["invocation_id"],
        )
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.role_result.recorded", role_result
                ),
                event_type="decision_intelligence.role_result.recorded",
                payload=role_result,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_assessment_references_must_exist_and_match_request(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ancestry = _seed_complete_di_ancestry(writer)
        request_id = ancestry["request"]["request_id"]

        missing = _assessment_payload(
            request_id=request_id,
            referenced_role_result_ids=["missing-role-result"],
            invocation_references=[],
        )
        missing_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.assessment.recorded", missing
                ),
                event_type="decision_intelligence.assessment.recorded",
                payload=missing,
            )
        )

        context_b = _context_payload("ctx-assessment-other", value=3)
        context_b["candidate_id"] = "candidate-assessment-other"
        context_b["context_id"] = context_identity(context_b)
        context_b_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.context.recorded", context_b
                ),
                event_type="decision_intelligence.context.recorded",
                payload=context_b,
            )
        )
        assert context_b_ack.status == "OK"
        request_b = _request_payload(context_b)
        request_b_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.request.recorded", request_b
                ),
                event_type="decision_intelligence.request.recorded",
                payload=request_b,
            )
        )
        assert request_b_ack.status == "OK"
        invocation_b = _invocation_payload(
            "inv-assessment-other",
            request_id=request_b["request_id"],
        )
        invocation_b_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.invocation.recorded", invocation_b
                ),
                event_type="decision_intelligence.invocation.recorded",
                payload=invocation_b,
            )
        )
        assert invocation_b_ack.status == "OK"

        mismatched = _assessment_payload(
            request_id=request_id,
            referenced_role_result_ids=[],
            invocation_references=[invocation_b["invocation_id"]],
        )
        mismatch_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.assessment.recorded", mismatched
                ),
                event_type="decision_intelligence.assessment.recorded",
                payload=mismatched,
            )
        )
    finally:
        writer.close()

    assert missing_ack.status == "REJECTED"
    assert missing_ack.error_code == "INVALID_INTENT"
    assert mismatch_ack.status == "REJECTED"
    assert mismatch_ack.error_code == "INVALID_INTENT"


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("decision_context_id", "missing-context"),
        ("committee_request_id", "missing-request"),
        ("committee_assessment_id", "missing-assessment"),
        ("invocation_refs", ["missing-invocation"]),
    ],
)
def test_comparison_requires_complete_canonical_ancestry(
    tmp_path, field_name, bad_value
):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import comparison_identity

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ancestry = _seed_complete_di_ancestry(writer)
        payload = _valid_comparison_payload_for_regression(
            decision_context_id=ancestry["context"]["context_id"],
            committee_assessment_id=ancestry["assessment"]["assessment_id"],
            committee_request_id=ancestry["request"]["request_id"],
            experiment_id=ancestry["request"]["experiment_id"],
            invocation_refs=[ancestry["invocation"]["invocation_id"]],
        )
        payload[field_name] = bad_value
        payload["comparison_id"] = comparison_identity(payload)
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.comparison.recorded", payload
                ),
                event_type="decision_intelligence.comparison.recorded",
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"



@pytest.mark.parametrize(
    "record_kind",
    ["invocation", "role_result", "assessment", "comparison"],
)
def test_non_transition_supersession_requires_existing_target(
    tmp_path, record_kind
):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ancestry = _seed_complete_di_ancestry(writer)
        request_id = ancestry["request"]["request_id"]
        reason = "correction"
        if record_kind == "invocation":
            event_type = "decision_intelligence.invocation.recorded"
            payload = _invocation_payload(
                "inv-missing-supersession",
                request_id=request_id,
                supersedes_id="missing-invocation",
                supersession_reason=reason,
            )
        elif record_kind == "role_result":
            event_type = "decision_intelligence.role_result.recorded"
            payload = _role_result_payload(
                request_id=request_id,
                supersedes_id="missing-role-result",
                supersession_reason=reason,
            )
        elif record_kind == "assessment":
            event_type = "decision_intelligence.assessment.recorded"
            payload = _assessment_payload(
                request_id=request_id,
                supersedes_id="missing-assessment",
                supersession_reason=reason,
            )
        else:
            event_type = "decision_intelligence.comparison.recorded"
            payload = _valid_comparison_payload_for_regression(
                decision_context_id=ancestry["context"]["context_id"],
                committee_assessment_id=ancestry["assessment"]["assessment_id"],
                committee_request_id=request_id,
                experiment_id=ancestry["request"]["experiment_id"],
                invocation_refs=[ancestry["invocation"]["invocation_id"]],
                supersedes_id="missing-comparison",
                supersession_reason=reason,
            )

        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(event_type, payload),
                event_type=event_type,
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


@pytest.mark.parametrize(
    "record_kind",
    ["invocation", "role_result", "assessment", "comparison"],
)
def test_non_transition_supersession_rejects_cross_request_target(
    tmp_path, record_kind
):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        current = _seed_complete_di_ancestry(writer, value=1)
        other = _seed_complete_di_ancestry(writer, value=2)

        other_comparison = _valid_comparison_payload_for_regression(
            decision_context_id=other["context"]["context_id"],
            committee_assessment_id=other["assessment"]["assessment_id"],
            committee_request_id=other["request"]["request_id"],
            experiment_id=other["request"]["experiment_id"],
            invocation_refs=[other["invocation"]["invocation_id"]],
        )
        other_comparison_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.comparison.recorded",
                    other_comparison,
                ),
                event_type="decision_intelligence.comparison.recorded",
                payload=other_comparison,
            )
        )
        assert other_comparison_ack.status == "OK"

        request_id = current["request"]["request_id"]
        reason = "cross-lineage correction"
        if record_kind == "invocation":
            event_type = "decision_intelligence.invocation.recorded"
            payload = _invocation_payload(
                "inv-cross-request",
                request_id=request_id,
                supersedes_id=other["invocation"]["invocation_id"],
                supersession_reason=reason,
            )
        elif record_kind == "role_result":
            event_type = "decision_intelligence.role_result.recorded"
            payload = _role_result_payload(
                request_id=request_id,
                supersedes_id=other["role_result"]["result_id"],
                supersession_reason=reason,
            )
        elif record_kind == "assessment":
            event_type = "decision_intelligence.assessment.recorded"
            payload = _assessment_payload(
                request_id=request_id,
                supersedes_id=other["assessment"]["assessment_id"],
                supersession_reason=reason,
            )
        else:
            event_type = "decision_intelligence.comparison.recorded"
            payload = _valid_comparison_payload_for_regression(
                decision_context_id=current["context"]["context_id"],
                committee_assessment_id=current["assessment"]["assessment_id"],
                committee_request_id=request_id,
                experiment_id=current["request"]["experiment_id"],
                invocation_refs=[current["invocation"]["invocation_id"]],
                supersedes_id=other_comparison["comparison_id"],
                supersession_reason=reason,
            )

        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(event_type, payload),
                event_type=event_type,
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"



@pytest.mark.parametrize("record_kind", ["context", "request"])
def test_root_record_supersession_requires_existing_same_type_target(
    tmp_path, record_kind
):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ancestry = _seed_complete_di_ancestry(writer, value=1)
        if record_kind == "context":
            payload = _context_payload("ctx-root-correction", value=3)
            payload["supersedes_id"] = ancestry["request"]["request_id"]
            payload["supersession_reason"] = "invalid wrong-type correction"
            payload["context_id"] = context_identity(payload)
            event_type = "decision_intelligence.context.recorded"
        else:
            payload = _request_payload(ancestry["context"])
            payload["supersedes_id"] = "missing-request"
            payload["supersession_reason"] = "invalid missing correction"
            payload["request_id"] = request_identity(payload)
            event_type = "decision_intelligence.request.recorded"

        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(event_type, payload),
                event_type=event_type,
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_request_supersession_rejects_cross_context_target(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        current = _seed_complete_di_ancestry(writer, value=1)
        other = _seed_complete_di_ancestry(writer, value=2)

        payload = _request_payload(current["context"])
        payload["supersedes_id"] = other["request"]["request_id"]
        payload["supersession_reason"] = "cross-context correction"
        payload["request_id"] = request_identity(payload)

        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.request.recorded", payload
                ),
                event_type="decision_intelligence.request.recorded",
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_context_supersession_accepts_existing_context_target(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        original = _context_payload("ctx-root-original", value=1)
        original_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.context.recorded", original
                ),
                event_type="decision_intelligence.context.recorded",
                payload=original,
            )
        )
        assert original_ack.status == "OK"

        correction = _context_payload("ctx-root-correction", value=4)
        correction["supersedes_id"] = original["context_id"]
        correction["supersession_reason"] = "context metadata correction"
        correction["context_id"] = context_identity(correction)
        correction_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    "decision_intelligence.context.recorded", correction
                ),
                event_type="decision_intelligence.context.recorded",
                payload=correction,
            )
        )
    finally:
        writer.close()

    assert correction_ack.status == "OK"



@pytest.mark.parametrize(
    "record_kind",
    [
        "role_attempt",
        "invocation_attempt",
        "request_budget",
        "role_score_schema",
        "comparison_horizon",
    ],
)
def test_writer_rejects_boolean_values_for_di_integer_semantics(
    tmp_path, record_kind
):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import comparison_identity

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        if record_kind == "request_budget":
            context = _context_payload("ctx-int-budget")
            context_ack = writer.submit(
                WriterIntent(
                    schema_version=1,
                    priority="LOW",
                    idempotency_key=_di_key(
                        DECISION_INTELLIGENCE_CONTEXT_RECORDED, context
                    ),
                    event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                    payload=context,
                )
            )
            assert context_ack.status == "OK"
            payload = _request_payload(context)
            payload["budget_reservation"] = True
            payload["request_id"] = request_identity(payload)
            event_type = "decision_intelligence.request.recorded"

        elif record_kind in {"role_attempt", "role_score_schema"}:
            request_id = _seed_di_ancestry(writer, value=11)
            payload = _role_result_payload(request_id=request_id)
            field_name = (
                "attempt"
                if record_kind == "role_attempt"
                else "score_schema_version"
            )
            payload[field_name] = True
            payload["result_id"] = role_result_identity(payload)
            event_type = "decision_intelligence.role_result.recorded"

        elif record_kind == "invocation_attempt":
            request_id = _seed_di_ancestry(writer, value=12)
            payload = _invocation_payload(
                "inv-bool-attempt",
                request_id=request_id,
            )
            payload["attempt"] = True
            payload["invocation_id"] = invocation_identity(payload)
            event_type = "decision_intelligence.invocation.recorded"

        else:
            ancestry = _seed_complete_di_ancestry(writer, value=13)
            payload = _valid_comparison_payload_for_regression(
                decision_context_id=ancestry["context"]["context_id"],
                committee_assessment_id=ancestry["assessment"]["assessment_id"],
                committee_request_id=ancestry["request"]["request_id"],
                experiment_id=ancestry["request"]["experiment_id"],
                invocation_refs=[ancestry["invocation"]["invocation_id"]],
            )
            payload["common_outcome_horizon"] = True
            payload["comparison_id"] = comparison_identity(payload)
            event_type = "decision_intelligence.comparison.recorded"

        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(event_type, payload),
                event_type=event_type,
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


@pytest.mark.parametrize(
    ("record_kind", "field_name"),
    [
        ("invocation", "input_tokens"),
        ("invocation", "latency_micros"),
        ("invocation", "billed_cost_microunits"),
        ("comparison", "ai_cost_attributed"),
        ("comparison", "baseline_trading_net"),
    ],
)
def test_writer_rejects_non_integer_values_across_remaining_di_integer_fields(
    tmp_path, record_kind, field_name
):
    from app.opip.canonical.writer import CanonicalWriter
    from app.opip.decision_intelligence.events import comparison_identity

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        ancestry = _seed_complete_di_ancestry(writer, value=14)
        request_id = ancestry["request"]["request_id"]

        if record_kind == "invocation":
            payload = _invocation_payload(
                f"inv-bad-{field_name}",
                request_id=request_id,
            )
            payload[field_name] = 1.5
            payload["invocation_id"] = invocation_identity(payload)
            event_type = "decision_intelligence.invocation.recorded"
        else:
            payload = _valid_comparison_payload_for_regression(
                decision_context_id=ancestry["context"]["context_id"],
                committee_assessment_id=ancestry["assessment"]["assessment_id"],
                committee_request_id=request_id,
                experiment_id=ancestry["request"]["experiment_id"],
                invocation_refs=[ancestry["invocation"]["invocation_id"]],
            )
            payload[field_name] = 1.5
            payload["comparison_id"] = comparison_identity(payload)
            event_type = "decision_intelligence.comparison.recorded"

        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(event_type, payload),
                event_type=event_type,
                payload=payload,
            )
        )
    finally:
        writer.close()

    assert ack.status == "REJECTED"
    assert ack.error_code == "INVALID_INTENT"


def test_boolean_role_attempt_cannot_block_valid_integer_attempt(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    writer = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        request_id = _seed_di_ancestry(writer, value=15)

        valid = _role_result_payload(request_id=request_id)
        valid_key = _di_key(
            "decision_intelligence.role_result.recorded",
            valid,
        )

        malformed = dict(valid)
        malformed["attempt"] = True
        malformed["result_id"] = role_result_identity(malformed)

        malformed_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=valid_key,
                event_type="decision_intelligence.role_result.recorded",
                payload=malformed,
            )
        )
        valid_ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=valid_key,
                event_type="decision_intelligence.role_result.recorded",
                payload=valid,
            )
        )
    finally:
        writer.close()

    assert malformed_ack.status == "REJECTED"
    assert malformed_ack.error_code == "INVALID_INTENT"
    assert valid_ack.status == "OK"


def test_context_decision_link_rejects_boolean_decision_schema_version():
    from app.opip.decision_intelligence import ContextDecisionLink

    with pytest.raises(ValueError, match="decision_schema_version"):
        ContextDecisionLink(
            link_id="link-bool-schema",
            context_id="ctx",
            decision_id="decision",
            decision_record_type="AdmissionDecisionV2",
            decision_schema_version=True,
            architecture_disposition="LINK_ONLY",
            provenance=_provenance(),
        )



def test_two_writers_cannot_fork_request_lifecycle(tmp_path):
    from app.opip.canonical.schema import connect
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    writer_a = CanonicalWriter(db)
    try:
        request_id = _seed_di_ancestry(writer_a, value=31)
        writer_b = CanonicalWriter(db)
        try:
            selected = _transition_payload(
                request_id=request_id,
                from_state="ELIGIBLE",
                to_state="SELECTED",
                transition_time="2026-01-02T03:06:00Z",
            )
            skipped = _transition_payload(
                request_id=request_id,
                from_state="ELIGIBLE",
                to_state="SKIPPED_BUDGET",
                transition_time="2026-01-02T03:07:00Z",
            )

            selected_ack = writer_a.submit(
                WriterIntent(
                    schema_version=1,
                    priority="LOW",
                    idempotency_key=_di_key(
                        "decision_intelligence.transition.recorded",
                        selected,
                    ),
                    event_type="decision_intelligence.transition.recorded",
                    payload=selected,
                )
            )
            skipped_ack = writer_b.submit(
                WriterIntent(
                    schema_version=1,
                    priority="LOW",
                    idempotency_key=_di_key(
                        "decision_intelligence.transition.recorded",
                        skipped,
                    ),
                    event_type="decision_intelligence.transition.recorded",
                    payload=skipped,
                )
            )
        finally:
            writer_b.close()
    finally:
        writer_a.close()

    assert selected_ack.status == "OK"
    assert skipped_ack.status == "REJECTED"
    assert skipped_ack.error_code == "INVALID_INTENT"

    conn = connect(db, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            ("decision_intelligence.transition.recorded",),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    persisted = json.loads(rows[0]["payload_json"])
    assert persisted["to_state"] == "SELECTED"

    restarted = CanonicalWriter(db)
    try:
        assert restarted._request_lifecycle_projection[request_id] == "SELECTED"
    finally:
        restarted.close()



def test_losing_idempotency_race_rolls_back_for_next_submit(
    tmp_path, monkeypatch
):
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    writer_a = CanonicalWriter(db)
    writer_b = CanonicalWriter(db)
    try:
        payload = _context_payload("ctx-idempotency-race")
        intent = WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=_di_key(
                DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                payload,
            ),
            event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
            payload=payload,
        )

        winning_ack = writer_a.submit(intent)
        assert winning_ack.status == "OK"

        original_lookup = writer_b._lookup_idempotency
        lookup_calls = 0

        def stale_once(key, *, payload_json=None, event_type=None):
            nonlocal lookup_calls
            lookup_calls += 1
            if lookup_calls == 1:
                return None
            return original_lookup(
                key,
                payload_json=payload_json,
                event_type=event_type,
            )

        monkeypatch.setattr(
            writer_b,
            "_lookup_idempotency",
            stale_once,
        )

        losing_ack = writer_b.submit(intent)
        assert losing_ack.status == "DUPLICATE_OK"

        next_payload = _context_payload("ctx-after-idempotency-race")
        next_payload["candidate_id"] = "candidate-after-idempotency-race"
        next_payload["context_id"] = context_identity(next_payload)
        next_ack = writer_b.submit(
            WriterIntent(
                schema_version=1,
                priority="LOW",
                idempotency_key=_di_key(
                    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                    next_payload,
                ),
                event_type=DECISION_INTELLIGENCE_CONTEXT_RECORDED,
                payload=next_payload,
            )
        )
    finally:
        writer_b.close()
        writer_a.close()

    assert next_ack.status == "OK"



def test_second_writer_refreshes_role_result_for_assessment(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    writer_a = CanonicalWriter(db)
    try:
        request_id = _seed_di_ancestry(writer_a, value=41)
        writer_b = CanonicalWriter(db)
        try:
            role_result = _role_result_payload(request_id=request_id)
            role_ack = writer_a.submit(
                WriterIntent(
                    schema_version=1,
                    priority="LOW",
                    idempotency_key=_di_key(
                        "decision_intelligence.role_result.recorded",
                        role_result,
                    ),
                    event_type="decision_intelligence.role_result.recorded",
                    payload=role_result,
                )
            )
            assert role_ack.status == "OK"

            assessment = _assessment_payload(
                request_id=request_id,
                referenced_role_result_ids=[role_result["result_id"]],
                invocation_references=[],
            )
            assessment_ack = writer_b.submit(
                WriterIntent(
                    schema_version=1,
                    priority="LOW",
                    idempotency_key=_di_key(
                        "decision_intelligence.assessment.recorded",
                        assessment,
                    ),
                    event_type="decision_intelligence.assessment.recorded",
                    payload=assessment,
                )
            )
        finally:
            writer_b.close()
    finally:
        writer_a.close()

    assert assessment_ack.status == "OK"


def test_second_writer_refreshes_role_result_for_correction(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "canonical.sqlite3"
    writer_a = CanonicalWriter(db)
    try:
        request_id = _seed_di_ancestry(writer_a, value=42)
        writer_b = CanonicalWriter(db)
        try:
            original = _role_result_payload(request_id=request_id)
            original_ack = writer_a.submit(
                WriterIntent(
                    schema_version=1,
                    priority="LOW",
                    idempotency_key=_di_key(
                        "decision_intelligence.role_result.recorded",
                        original,
                    ),
                    event_type="decision_intelligence.role_result.recorded",
                    payload=original,
                )
            )
            assert original_ack.status == "OK"

            correction = _role_result_payload(
                request_id=request_id,
                supersedes_id=original["result_id"],
                supersession_reason="role result correction",
            )
            correction_ack = writer_b.submit(
                WriterIntent(
                    schema_version=1,
                    priority="LOW",
                    idempotency_key=_di_key(
                        "decision_intelligence.role_result.recorded",
                        correction,
                    ),
                    event_type="decision_intelligence.role_result.recorded",
                    payload=correction,
                )
            )
        finally:
            writer_b.close()
    finally:
        writer_a.close()

    assert correction_ack.status == "OK"
