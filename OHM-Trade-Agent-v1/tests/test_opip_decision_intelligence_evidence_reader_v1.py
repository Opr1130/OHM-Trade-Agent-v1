"""Read-only Decision Intelligence evidence reader v1 tests.

These tests seed real canonical evidence through ``CanonicalWriter`` (the
existing write authority), then reconstruct it through the read-only reader.
Where a case needs corrupt or forward-incompatible canonical state that the
writer correctly refuses to produce, the test writes that state directly to a
throwaway temporary SQLite file to prove the reader fails closed / surfaces
incompleteness instead of trusting it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.canonical.backup import backup_database
from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import EVENT_SCHEMA_VERSION, SCHEMA_VERSION
from app.opip.canonical.recovery import restore_from_backup
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.decision_intelligence.contracts import (
    AdvisoryStance,
    CommitteeAssessmentSummary,
    CommitteeRequest,
    CommitteeRequestTransition,
    CommitteeRole,
    CommitteeRoleResult,
    ComparisonRecord,
    DecisionContext,
    ModelInvocation,
    RequestState,
    ResultDisposition,
)
from app.opip.decision_intelligence.evidence_reader import (
    ANOMALY_AMBIGUOUS_SUPERSESSION,
    ANOMALY_UNKNOWN_EVENT_TYPE,
    DI_ANOMALY_CODES,
    DIEvidenceIntegrityError,
    DIEvidenceSnapshot,
    DIEvidenceSourceUnavailableError,
    DIIncompatibleSchemaError,
    DIInconsistentBoundaryError,
    DILifecycleReconstructionError,
    read_di_evidence_snapshot,
)
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
    DECISION_INTELLIGENCE_COMPARISON_RECORDED,
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    DECISION_INTELLIGENCE_EVENT_TYPES,
    DECISION_INTELLIGENCE_INVOCATION_RECORDED,
    DECISION_INTELLIGENCE_REQUEST_RECORDED,
    DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
    DECISION_INTELLIGENCE_STREAM,
    DECISION_INTELLIGENCE_TRANSITION_RECORDED,
    assessment_identity,
    canonical_di_idempotency_key,
    comparison_identity,
    context_identity,
    invocation_identity,
    request_identity,
    role_result_identity,
    transition_identity,
)

_CONTEXT = DECISION_INTELLIGENCE_CONTEXT_RECORDED
_REQUEST = DECISION_INTELLIGENCE_REQUEST_RECORDED
_TRANSITION = DECISION_INTELLIGENCE_TRANSITION_RECORDED
_ROLE_RESULT = DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED
_ASSESSMENT = DECISION_INTELLIGENCE_ASSESSMENT_RECORDED
_INVOCATION = DECISION_INTELLIGENCE_INVOCATION_RECORDED
_COMPARISON = DECISION_INTELLIGENCE_COMPARISON_RECORDED

#: Maps the supersession-ownership test cases to their canonical event types.
_RECORD_TYPES_BY_EVENT_TYPE = {
    "request": _REQUEST,
    "invocation": _INVOCATION,
    "role_result": _ROLE_RESULT,
    "assessment": _ASSESSMENT,
    "comparison": _COMPARISON,
}


# --------------------------------------------------------------------------- #
# Canonical payload builders (mirroring the P1A test fixtures)
# --------------------------------------------------------------------------- #


def _provenance_payload():
    return {
        "producing_component": "test_component",
        "artifact_or_build_id": "build-42",
        "process_instance_id": "proc-9",
        "emitted_at": "2026-01-02T03:04:05Z",
        "source_record_refs": ["source:1"],
        "schema_version": 1,
    }


def _context_payload(
    *,
    candidate="candidate-1",
    missingness=None,
    snapshot_hash="snapshot-hash",
    manifest=None,
    evidence_cutoff="2026-01-02T03:04:00Z",
    supersedes_id=None,
    supersession_reason=None,
):
    payload = {
        "context_id": "pending",
        "candidate_id": candidate,
        "episode_id": "episode-1",
        "evaluation_id": "evaluation-1",
        "instrument_version": "instrument-1",
        "snapshot_id": "snapshot-1",
        "snapshot_hash": snapshot_hash,
        "evaluation_time": "2026-01-02T03:04:00Z",
        "evidence_cutoff": evidence_cutoff,
        "consumed_input_watermark": {"history_epoch": 1, "local_sequence": 1},
        "feature_version": "features-1",
        "policy_version": "policy-1",
        "detector_version": "detector-1",
        "forecast_version": "forecast-1",
        "candidate_set_ref": "set-1",
        "portfolio_version_ref": None,
        "environment": "paper",
        "eligibility": True,
        "missingness": {"value": 1} if missingness is None else missingness,
        "source_availability_times": {},
        "evidence_eligibility_manifest": {} if manifest is None else manifest,
        "schema_version": 1,
        "provenance": _provenance_payload(),
    }
    if supersedes_id is not None:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["context_id"] = context_identity(payload)
    return payload


def _request_payload(
    context,
    *,
    experiment="experiment-1",
    supersedes_id=None,
    supersession_reason=None,
):
    payload = {
        "request_id": "pending",
        "context_id": context["context_id"],
        "experiment_id": experiment,
        "cohort_selection_rule_version": "cohort-1",
        "frozen_snapshot_hash": context["snapshot_hash"],
        "route_version": "route-1",
        "prompt_version": "prompt-1",
        "role_configuration_version": "roles-1",
        "eligibility_at": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        "deadline_at": datetime(2026, 1, 2, 3, 10, tzinfo=timezone.utc),
        "budget_reservation": 1,
        "enqueue_time": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        "result_selection_rule_version": "selection-1",
        "schema_version": 1,
        "provenance": _provenance_payload(),
    }
    if supersedes_id is not None:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["request_id"] = request_identity(payload)
    return payload


def _transition_payload(
    *,
    request_id,
    from_state="ELIGIBLE",
    to_state="SELECTED",
    transition_time="2026-01-02T03:00:00Z",
    reason="recorded",
    supersedes_id=None,
    supersession_reason=None,
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
    }
    if supersedes_id is not None:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["transition_id"] = transition_identity(payload)
    return payload


def _invocation_payload(
    *,
    request_id,
    attempt=1,
    cost=None,
    role="REGIME_ANALYST",
    supersedes_id=None,
    supersession_reason=None,
):
    payload = {
        "invocation_id": "pending",
        "request_id": request_id,
        "role": role,
        "attempt": attempt,
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
    }
    if supersedes_id is not None:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["invocation_id"] = invocation_identity(payload)
    return payload


def _role_result_payload(
    *,
    request_id,
    invocation_ref=None,
    role="REGIME_ANALYST",
    attempt=1,
    thesis="thesis",
    stance="SUPPORT",
    evidence_refs=(),
    supersedes_id=None,
    supersession_reason=None,
):
    payload = {
        "result_id": "pending",
        "request_id": request_id,
        "role": role,
        "role_version": "role-1",
        "attempt": attempt,
        "route_version": "route-1",
        "prompt_version": "prompt-1",
        "model_version": "model-1",
        "invocation_ref": invocation_ref,
        "status": "COMPLETED",
        "result_disposition": "ON_TIME",
        "stance": stance,
        "thesis": thesis,
        "risks": [],
        "evidence_refs": list(evidence_refs),
        "missing_evidence": [],
        "rubric_score": 50,
        "score_schema_version": 1,
        "self_reported_confidence": 50,
        "bull_score": 50,
        "bear_score": 50,
        "risk_score": 50,
        "provenance": _provenance_payload(),
        "schema_version": 1,
    }
    if supersedes_id is not None:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["result_id"] = role_result_identity(payload)
    return payload


def _assessment_payload(
    *,
    request_id,
    referenced_role_result_ids=(),
    invocation_references=(),
    selection_rule="selection-1",
    synthesis="synthesis",
    stance="WATCH",
    completeness=7500,
    evidence_refs=(),
    supersedes_id=None,
    supersession_reason=None,
):
    payload = {
        "assessment_id": "pending",
        "request_id": request_id,
        "referenced_role_result_ids": list(referenced_role_result_ids),
        "synthesis": synthesis,
        "advisory_stance": stance,
        "disagreement": False,
        "completeness": completeness,
        "unsupported_claims": [],
        "evidence_refs": list(evidence_refs),
        "status": "COMPLETED",
        "result_disposition": "ON_TIME",
        "result_selection_rule_version": selection_rule,
        "completion_time": "2026-01-02T03:04:00Z",
        "commit_time": "2026-01-02T03:05:00Z",
        "invocation_references": list(invocation_references),
        "schema_version": 1,
        "provenance": _provenance_payload(),
    }
    if supersedes_id is not None:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["assessment_id"] = assessment_identity(payload)
    return payload


def _comparison_payload(
    *,
    context_id,
    request_id,
    assessment_id,
    experiment="experiment-1",
    invocation_refs=(),
    supersedes_id=None,
    supersession_reason=None,
):
    payload = {
        "schema_version": 1,
        "comparison_id": "pending",
        "decision_context_id": context_id,
        "baseline_decision_id": "baseline-1",
        "committee_assessment_id": assessment_id,
        "committee_request_id": request_id,
        "experiment_id": experiment,
        "variant_version": "v1",
        "environment": "paper",
        "research_account_id": "research-1",
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
        "invocation_refs": list(invocation_refs),
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
    if supersedes_id is not None:
        payload["supersedes_id"] = supersedes_id
        payload["supersession_reason"] = supersession_reason
    payload["comparison_id"] = comparison_identity(payload)
    return payload


# --------------------------------------------------------------------------- #
# Seeding and tampering helpers
# --------------------------------------------------------------------------- #


def _write_di(db: Path, entries) -> None:
    """Commit Decision Intelligence evidence through the canonical writer."""
    writer = CanonicalWriter(db)
    try:
        for event_type, payload in entries:
            ack = writer.submit(
                WriterIntent(
                    schema_version=SCHEMA_VERSION,
                    priority="LOW",
                    idempotency_key=canonical_di_idempotency_key(
                        event_type, payload
                    ),
                    event_type=event_type,
                    payload=payload,
                )
            )
            assert ack.status in {"OK", "DUPLICATE_OK"}, (
                ack.status,
                ack.error_code,
                ack.detail,
            )
    finally:
        writer.close()


def _write_non_di_event(db: Path, *, suffix: str = "1") -> None:
    """Commit non-DI feature-bus telemetry (out of the reader's scope)."""
    writer = CanonicalWriter(db)
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=SCHEMA_VERSION,
                priority="LOW",
                idempotency_key=f"feature-bus:reader-scope:{suffix}",
                event_type="market.observation.recorded",
                payload={"observation_id": f"obs-{suffix}"},
            )
        )
        assert ack.status == "OK", (ack.status, ack.error_code)
    finally:
        writer.close()


def _read_stream_watermark(db: Path, stream: str = DECISION_INTELLIGENCE_STREAM):
    connection = sqlite3.connect(str(db))
    try:
        row = connection.execute(
            "SELECT history_epoch, local_sequence FROM watermarks WHERE stream = ?",
            (stream,),
        ).fetchone()
    finally:
        connection.close()
    return row


def _set_di_watermark(db: Path, *, history_epoch: int, local_sequence: int) -> None:
    """Force a specific frozen DI watermark to exercise boundary validation."""
    connection = sqlite3.connect(str(db))
    try:
        connection.execute(
            """
            INSERT INTO watermarks (stream, history_epoch, local_sequence, updated_at)
            VALUES (?, ?, ?, '2026-01-02T03:04:05Z')
            ON CONFLICT(stream) DO UPDATE SET
                history_epoch = excluded.history_epoch,
                local_sequence = excluded.local_sequence
            """,
            (DECISION_INTELLIGENCE_STREAM, history_epoch, local_sequence),
        )
        connection.commit()
    finally:
        connection.close()


def _delete_di_watermark(db: Path) -> None:
    connection = sqlite3.connect(str(db))
    try:
        connection.execute(
            "DELETE FROM watermarks WHERE stream = ?",
            (DECISION_INTELLIGENCE_STREAM,),
        )
        connection.commit()
    finally:
        connection.close()


def _di_evidence_tip(db: Path):
    """Highest recorded DI-stream coordinate, or None when there is none."""
    connection = sqlite3.connect(str(db))
    try:
        return connection.execute(
            """
            SELECT history_epoch, local_sequence
            FROM events
            WHERE event_type GLOB 'decision_intelligence.*'
            ORDER BY history_epoch DESC, local_sequence DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()


def _global_event_tip(db: Path):
    connection = sqlite3.connect(str(db))
    try:
        return connection.execute(
            "SELECT MAX(local_sequence) FROM events"
        ).fetchone()[0]
    finally:
        connection.close()


def _seed_context_and_request(db: Path, *, candidate="candidate-1", experiment="experiment-1"):
    context = _context_payload(candidate=candidate)
    request = _request_payload(context, experiment=experiment)
    _write_di(db, [(_CONTEXT, context), (_REQUEST, request)])
    return context, request


def _seed_full_ancestry(db: Path, *, candidate="candidate-1", experiment="experiment-1"):
    context = _context_payload(candidate=candidate)
    request = _request_payload(context, experiment=experiment)
    invocation = _invocation_payload(request_id=request["request_id"], cost=25)
    role_result = _role_result_payload(
        request_id=request["request_id"],
        invocation_ref=invocation["invocation_id"],
    )
    assessment = _assessment_payload(
        request_id=request["request_id"],
        referenced_role_result_ids=[role_result["result_id"]],
        invocation_references=[invocation["invocation_id"]],
    )
    _write_di(
        db,
        [
            (_CONTEXT, context),
            (_REQUEST, request),
            (_INVOCATION, invocation),
            (_ROLE_RESULT, role_result),
            (_ASSESSMENT, assessment),
        ],
    )
    return {
        "context": context,
        "request": request,
        "invocation": invocation,
        "role_result": role_result,
        "assessment": assessment,
    }


def _mutate_canonical(db: Path, statements) -> None:
    """Simulate corrupt / forward-incompatible canonical state for fail-closed tests."""
    connection = sqlite3.connect(str(db))
    try:
        for sql, params in statements:
            connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


def _rewrite_payload(db: Path, event_id: str, transform) -> None:
    connection = sqlite3.connect(str(db))
    try:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_id = ?",
            (transform(row[0]), event_id),
        )
        connection.commit()
    finally:
        connection.close()


def _insert_event(
    db: Path,
    *,
    event_id: str,
    event_type: str,
    history_epoch: int,
    local_sequence: int,
    payload_json: str,
    schema_version: int = EVENT_SCHEMA_VERSION,
) -> None:
    connection = sqlite3.connect(str(db))
    try:
        connection.execute(
            """
            INSERT INTO events (
                event_id, schema_version, event_type, history_epoch,
                local_sequence, recorded_at, event_time, causation_id,
                correlation_id, idempotency_key, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (
                event_id,
                schema_version,
                event_type,
                history_epoch,
                local_sequence,
                "2026-01-02T03:04:05Z",
                f"di-reader-test:{event_id}",
                payload_json,
            ),
        )
        connection.execute(
            """
            INSERT INTO watermarks (stream, history_epoch, local_sequence, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stream) DO UPDATE SET
                history_epoch = excluded.history_epoch,
                local_sequence = excluded.local_sequence,
                updated_at = excluded.updated_at
            """,
            (
                DECISION_INTELLIGENCE_STREAM,
                history_epoch,
                local_sequence,
                "2026-01-02T03:04:05Z",
            ),
        )
        connection.execute(
            "UPDATE meta SET next_local_sequence = ? WHERE id = 1",
            (local_sequence + 1,),
        )
        connection.commit()
    finally:
        connection.close()


def _payload_json(payload) -> str:
    """Canonical-ish JSON for direct event insertion (datetimes -> ISO-8601)."""

    def _default(value):
        return value.isoformat() if isinstance(value, datetime) else str(value)

    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=_default
    )


def _set_event_envelope_version(
    db: Path, event_id: str, *, schema_version: int
) -> None:
    """Force a canonical event-envelope version to exercise Finding 2."""
    connection = sqlite3.connect(str(db))
    try:
        connection.execute(
            "UPDATE events SET schema_version = ? WHERE event_id = ?",
            (schema_version, event_id),
        )
        connection.commit()
    finally:
        connection.close()


def _canonical_state(db: Path) -> dict:
    connection = sqlite3.connect(str(db))
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: sorted(
                repr(tuple(row))
                for row in connection.execute(f'SELECT * FROM "{table}"')
            )
            for table in tables
        }
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# 1. Empty Decision Intelligence stream
# --------------------------------------------------------------------------- #


def test_empty_di_stream_with_non_di_evidence_reads_as_empty_snapshot(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _write_non_di_event(db)

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.boundary == ConsumedInputWatermark.zero()
    assert snapshot.events == ()
    assert snapshot.unknown_events == ()
    assert snapshot.contexts == {}
    assert snapshot.requests == {}
    assert snapshot.transitions == {}
    assert snapshot.transition_history == {}
    assert snapshot.lifecycles == {}
    assert snapshot.request_states == {}
    assert snapshot.role_results == {}
    assert snapshot.assessments == {}
    assert snapshot.invocations == {}
    assert snapshot.comparisons == {}
    assert snapshot.provenance == {}
    assert snapshot.supersession_edges == ()
    assert snapshot.anomalies == ()
    assert snapshot.is_complete is True
    assert snapshot.cost_evidence_complete is True


def test_existing_canonical_database_with_zero_di_events_is_empty_and_complete(
    tmp_path,
):
    """An existing source of truth with no DI evidence is a valid empty stream."""
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.boundary == ConsumedInputWatermark.zero()
    assert snapshot.events == ()
    assert snapshot.unknown_events == ()
    assert snapshot.anomalies == ()
    assert snapshot.is_complete is True
    assert snapshot.cost_evidence_complete is True
    assert _read_stream_watermark(db) is None


def test_missing_canonical_database_fails_closed_without_touching_filesystem(
    tmp_path, monkeypatch
):
    """FINDING 1: an absent source of truth must never read as empty-complete."""
    absent = tmp_path / "absent"
    db = absent / "canonical.sqlite3"
    assert not absent.exists()

    # The canonical connect helper creates parent directories, so the reader
    # must never reach it for a missing database.
    from app.opip.canonical import schema

    def _connect_must_not_be_called(target, *, read_only=False):
        raise AssertionError(
            "reader attempted a canonical connection for a missing database"
        )

    monkeypatch.setattr(schema, "connect", _connect_must_not_be_called)

    with pytest.raises(DIEvidenceSourceUnavailableError):
        read_di_evidence_snapshot(db)

    # Nothing created: no database, no parent directory, no sidecars.
    assert not db.exists()
    assert not absent.exists()
    assert not (tmp_path / "canonical.sqlite3").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_missing_canonical_database_error_is_a_reader_error(tmp_path):
    """The failure is a DIEvidenceReaderError, and is not an integrity claim."""
    from app.opip.decision_intelligence.evidence_reader import (
        DIEvidenceReaderError,
    )

    with pytest.raises(DIEvidenceReaderError):
        read_di_evidence_snapshot(tmp_path / "definitely-absent.sqlite3")
    assert issubclass(DIEvidenceSourceUnavailableError, DIEvidenceReaderError)
    # Absent source and corrupt evidence are deliberately distinct axes.
    assert not issubclass(DIEvidenceSourceUnavailableError, DIEvidenceIntegrityError)


def test_existing_directory_path_fails_closed_as_unavailable(tmp_path):
    directory = tmp_path / "not-a-database"
    directory.mkdir()

    with pytest.raises(DIEvidenceSourceUnavailableError):
        read_di_evidence_snapshot(directory)
    assert directory.is_dir()


# --------------------------------------------------------------------------- #
# 2. Context / request reconstruction
# --------------------------------------------------------------------------- #


def test_context_and_request_are_reconstructed_as_typed_contracts(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    context, request = _seed_context_and_request(db)

    snapshot = read_di_evidence_snapshot(db)

    reconstructed_context = snapshot.contexts[context["context_id"]]
    reconstructed_request = snapshot.requests[request["request_id"]]
    assert isinstance(reconstructed_context, DecisionContext)
    assert isinstance(reconstructed_request, CommitteeRequest)
    assert reconstructed_context.candidate_id == "candidate-1"
    assert reconstructed_context.snapshot_hash == context["snapshot_hash"]
    assert reconstructed_context.consumed_input_watermark == ConsumedInputWatermark(
        history_epoch=1, local_sequence=1
    )
    assert reconstructed_request.context_id == context["context_id"]
    assert reconstructed_request.experiment_id == "experiment-1"
    assert reconstructed_request.frozen_snapshot_hash == context["snapshot_hash"]
    assert snapshot.requests_for_context(context["context_id"]) == (
        request["request_id"],
    )
    assert snapshot.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=2)
    assert len(snapshot.events) == 2
    assert snapshot.is_complete is True


# --------------------------------------------------------------------------- #
# 3. Lifecycle reconstruction
# --------------------------------------------------------------------------- #


def test_lifecycle_reconstruction_uses_recorded_transition_order(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    request_id = request["request_id"]
    selected = _transition_payload(
        request_id=request_id,
        from_state="ELIGIBLE",
        to_state="SELECTED",
        transition_time="2026-01-02T03:30:00Z",
    )
    completed = _transition_payload(
        request_id=request_id,
        from_state="SELECTED",
        to_state="COMPLETED",
        transition_time="2026-01-02T03:00:00Z",
    )
    _write_di(db, [(_TRANSITION, selected), (_TRANSITION, completed)])

    snapshot = read_di_evidence_snapshot(db)

    lifecycle = snapshot.lifecycles[request_id]
    assert lifecycle.initial_state is RequestState.ELIGIBLE
    assert lifecycle.state is RequestState.COMPLETED
    assert snapshot.state_of(request_id) is RequestState.COMPLETED
    assert [t.transition_id for t in lifecycle.applied_transitions] == [
        selected["transition_id"],
        completed["transition_id"],
    ]
    assert lifecycle.corrected_transitions == ()
    assert snapshot.transition_history[request_id] == lifecycle.applied_transitions
    assert isinstance(
        lifecycle.applied_transitions[0], CommitteeRequestTransition
    )
    assert snapshot.is_complete is True


def test_request_without_transitions_reconstructs_as_eligible(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.state_of(request["request_id"]) is RequestState.ELIGIBLE
    assert snapshot.transitions_for(request["request_id"]) == ()


# --------------------------------------------------------------------------- #
# 4. Deterministic replay
# --------------------------------------------------------------------------- #


def test_replay_is_driven_by_commit_order_not_recorded_wall_clock(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    request_id = request["request_id"]
    # Commit order and transition_time order deliberately disagree: the reader
    # must replay recorded transition order through the frozen watermark.
    first_committed = _transition_payload(
        request_id=request_id,
        from_state="ELIGIBLE",
        to_state="SELECTED",
        transition_time="2026-01-02T09:00:00Z",
    )
    second_committed = _transition_payload(
        request_id=request_id,
        from_state="SELECTED",
        to_state="COMPLETED",
        transition_time="2026-01-02T01:00:00Z",
    )
    _write_di(db, [(_TRANSITION, first_committed), (_TRANSITION, second_committed)])

    first_read = read_di_evidence_snapshot(db)
    second_read = read_di_evidence_snapshot(db)

    assert first_read.lifecycles[request_id].state is RequestState.COMPLETED
    assert [
        transition.transition_id
        for transition in first_read.transitions_for(request_id)
    ] == [first_committed["transition_id"], second_committed["transition_id"]]
    assert [
        event.record_id for event in first_read.events
    ] == [event.record_id for event in second_read.events]


# --------------------------------------------------------------------------- #
# 5. Repeated read produces an identical result
# --------------------------------------------------------------------------- #


def test_repeated_read_produces_an_identical_snapshot(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    _write_di(
        db,
        [
            (
                _TRANSITION,
                _transition_payload(
                    request_id=ancestry["request"]["request_id"],
                    from_state="ELIGIBLE",
                    to_state="SELECTED",
                ),
            )
        ],
    )

    first = read_di_evidence_snapshot(db)
    second = read_di_evidence_snapshot(db)

    assert first == second
    assert first.boundary == second.boundary
    assert hash(tuple(first.events)) == hash(tuple(second.events))


# --------------------------------------------------------------------------- #
# 6. Frozen watermark excludes later events
# --------------------------------------------------------------------------- #


def test_frozen_boundary_excludes_events_committed_later(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    first_context, first_request = _seed_context_and_request(db)

    frozen = read_di_evidence_snapshot(db)
    assert frozen.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=2)

    second_context, second_request = _seed_context_and_request(
        db, candidate="candidate-2", experiment="experiment-2"
    )

    assert frozen.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=2)
    assert set(frozen.requests) == {first_request["request_id"]}
    assert set(frozen.contexts) == {first_context["context_id"]}

    refrozen = read_di_evidence_snapshot(db)
    assert refrozen.boundary == ConsumedInputWatermark(
        history_epoch=1, local_sequence=4
    )
    assert set(refrozen.requests) == {
        first_request["request_id"],
        second_request["request_id"],
    }
    assert set(refrozen.contexts) == {
        first_context["context_id"],
        second_context["context_id"],
    }


# --------------------------------------------------------------------------- #
# 7. Multiple requests / contexts
# --------------------------------------------------------------------------- #


def test_multiple_contexts_and_requests_stay_isolated(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    context_a = _context_payload(candidate="candidate-a")
    context_b = _context_payload(candidate="candidate-b")
    request_a = _request_payload(context_a, experiment="experiment-a")
    request_b = _request_payload(context_b, experiment="experiment-b")
    transition_a = _transition_payload(
        request_id=request_a["request_id"],
        from_state="ELIGIBLE",
        to_state="SELECTED",
    )
    transition_b = _transition_payload(
        request_id=request_b["request_id"],
        from_state="ELIGIBLE",
        to_state="EXPIRED",
    )
    _write_di(
        db,
        [
            (_CONTEXT, context_a),
            (_CONTEXT, context_b),
            (_REQUEST, request_a),
            (_REQUEST, request_b),
            (_TRANSITION, transition_a),
            (_TRANSITION, transition_b),
        ],
    )

    snapshot = read_di_evidence_snapshot(db)

    assert set(snapshot.contexts) == {
        context_a["context_id"],
        context_b["context_id"],
    }
    assert snapshot.requests_for_context(context_a["context_id"]) == (
        request_a["request_id"],
    )
    assert snapshot.requests_for_context(context_b["context_id"]) == (
        request_b["request_id"],
    )
    assert snapshot.state_of(request_a["request_id"]) is RequestState.SELECTED
    assert snapshot.state_of(request_b["request_id"]) is RequestState.EXPIRED
    assert [
        transition.transition_id
        for transition in snapshot.transitions_for(request_a["request_id"])
    ] == [transition_a["transition_id"]]
    assert [
        transition.transition_id
        for transition in snapshot.transitions_for(request_b["request_id"])
    ] == [transition_b["transition_id"]]
    assert snapshot.is_complete is True


# --------------------------------------------------------------------------- #
# 8. Role result reconstruction
# --------------------------------------------------------------------------- #


def test_role_results_and_invocations_are_reconstructed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    request_id = ancestry["request"]["request_id"]

    snapshot = read_di_evidence_snapshot(db)

    result = snapshot.role_results[ancestry["role_result"]["result_id"]]
    invocation = snapshot.invocations[ancestry["invocation"]["invocation_id"]]
    assert isinstance(result, CommitteeRoleResult)
    assert isinstance(invocation, ModelInvocation)
    assert result.request_id == request_id
    assert result.role is CommitteeRole.REGIME_ANALYST
    assert result.stance is AdvisoryStance.SUPPORT
    assert result.result_disposition is ResultDisposition.ON_TIME
    assert result.rubric_score == 50
    assert result.invocation_ref == invocation.invocation_id
    assert snapshot.role_results_for(request_id) == (result,)
    assert snapshot.invocations_for(request_id) == (invocation,)
    assert snapshot.anomalies == ()
    assert snapshot.is_complete is True


# --------------------------------------------------------------------------- #
# 9. Assessment reconstruction
# --------------------------------------------------------------------------- #


def test_assessment_reconstruction_preserves_references_and_stance(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    request_id = ancestry["request"]["request_id"]

    snapshot = read_di_evidence_snapshot(db)

    assessment = snapshot.assessments[ancestry["assessment"]["assessment_id"]]
    assert isinstance(assessment, CommitteeAssessmentSummary)
    assert assessment.request_id == request_id
    assert assessment.advisory_stance is AdvisoryStance.WATCH
    assert assessment.result_disposition is ResultDisposition.ON_TIME
    assert assessment.completeness == 7500
    assert assessment.referenced_role_result_ids == (
        ancestry["role_result"]["result_id"],
    )
    assert assessment.invocation_references == (
        ancestry["invocation"]["invocation_id"],
    )
    assert assessment.synthesis == "synthesis"
    assert snapshot.assessments_for(request_id) == (assessment,)
    assert snapshot.is_complete is True


# --------------------------------------------------------------------------- #
# 10. Invocation / raw cost evidence
# --------------------------------------------------------------------------- #


def test_invocation_cost_evidence_is_raw_and_never_totalled(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    reconciled = ancestry["invocation"]
    unpriced = _invocation_payload(
        request_id=ancestry["request"]["request_id"], attempt=2, cost=None
    )
    _write_di(db, [(_INVOCATION, unpriced)])

    snapshot = read_di_evidence_snapshot(db)
    evidence = snapshot.invocation_cost_evidence()

    assert [item.invocation_id for item in evidence] == [
        reconciled["invocation_id"],
        unpriced["invocation_id"],
    ]
    first, second = evidence
    assert first.billed_cost_microunits == 25
    assert first.estimated_cost_microunits == 25
    assert first.reconciliation_status == "RECONCILED"
    assert first.cost_completeness == "COMPLETE"
    assert first.complete is True
    assert first.currency == "USD"
    assert first.price_version == "price-1"
    assert first.role is CommitteeRole.REGIME_ANALYST
    assert second.billed_cost_microunits is None
    assert second.estimated_cost_microunits is None
    assert second.cost_completeness == "UNKNOWN"
    assert second.complete is False
    assert snapshot.cost_evidence_complete is False
    for absent in (
        "total_cost",
        "ai_cost_total",
        "reconciled_cost",
        "cost_total",
        "reconciled_ai_cost",
    ):
        assert not hasattr(snapshot, absent)
        assert not hasattr(DIEvidenceSnapshot, absent)


# --------------------------------------------------------------------------- #
# 11. Comparison reconstruction
# --------------------------------------------------------------------------- #


def test_comparison_reconstruction_preserves_recorded_cost_boundary(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    comparison = _comparison_payload(
        context_id=ancestry["context"]["context_id"],
        request_id=ancestry["request"]["request_id"],
        assessment_id=ancestry["assessment"]["assessment_id"],
        invocation_refs=(ancestry["invocation"]["invocation_id"],),
    )
    _write_di(db, [(_COMPARISON, comparison)])

    snapshot = read_di_evidence_snapshot(db)

    record = snapshot.comparisons[comparison["comparison_id"]]
    assert isinstance(record, ComparisonRecord)
    assert record.committee_request_id == ancestry["request"]["request_id"]
    assert record.decision_context_id == ancestry["context"]["context_id"]
    assert record.committee_assessment_id == ancestry["assessment"]["assessment_id"]
    assert record.as_of_watermark == ConsumedInputWatermark(
        history_epoch=1, local_sequence=7
    )
    assert record.invocation_refs == (ancestry["invocation"]["invocation_id"],)
    assert record.ai_cost_attributed is None
    assert record.cost_reconciliation_status == "UNKNOWN"
    assert record.timeliness_eligibility is False
    assert snapshot.comparisons_for(ancestry["request"]["request_id"]) == (record,)
    assert snapshot.anomalies == ()
    assert snapshot.is_complete is True


# --------------------------------------------------------------------------- #
# 12. Provenance preservation
# --------------------------------------------------------------------------- #


def test_provenance_is_preserved_for_every_reconstructed_record(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    comparison = _comparison_payload(
        context_id=ancestry["context"]["context_id"],
        request_id=ancestry["request"]["request_id"],
        assessment_id=ancestry["assessment"]["assessment_id"],
    )
    correction = _assessment_payload(
        request_id=ancestry["request"]["request_id"],
        referenced_role_result_ids=[ancestry["role_result"]["result_id"]],
        selection_rule="selection-2",
        synthesis="corrected synthesis",
        supersedes_id=ancestry["assessment"]["assessment_id"],
        supersession_reason="correction",
    )
    _write_di(db, [(_COMPARISON, comparison), (_ASSESSMENT, correction)])

    snapshot = read_di_evidence_snapshot(db)

    for record_id in (
        ancestry["context"]["context_id"],
        ancestry["request"]["request_id"],
        ancestry["invocation"]["invocation_id"],
        ancestry["role_result"]["result_id"],
        ancestry["assessment"]["assessment_id"],
        comparison["comparison_id"],
        correction["assessment_id"],
    ):
        provenance = snapshot.provenance[record_id]
        assert provenance.producing_component == "test_component"
        assert provenance.artifact_or_build_id == "build-42"
        assert provenance.process_instance_id == "proc-9"
        assert provenance.source_record_refs == ("source:1",)
        assert provenance.emitted_at == datetime(
            2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc
        )
    # The superseded original keeps its own recorded provenance.
    assert (
        snapshot.provenance[ancestry["assessment"]["assessment_id"]]
        == snapshot.assessments[
            ancestry["assessment"]["assessment_id"]
        ].provenance
    )


# --------------------------------------------------------------------------- #
# 13. Supersession edge preservation
# --------------------------------------------------------------------------- #


def test_supersession_edge_is_preserved_without_selecting_a_winner(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    original = ancestry["assessment"]
    correction = _assessment_payload(
        request_id=ancestry["request"]["request_id"],
        referenced_role_result_ids=[ancestry["role_result"]["result_id"]],
        selection_rule="selection-2",
        synthesis="corrected synthesis",
        supersedes_id=original["assessment_id"],
        supersession_reason="corrected completeness",
    )
    _write_di(db, [(_ASSESSMENT, correction)])

    snapshot = read_di_evidence_snapshot(db)

    assert set(snapshot.assessments) == {
        original["assessment_id"],
        correction["assessment_id"],
    }
    edges = snapshot.supersessions_of(correction["assessment_id"])
    assert len(edges) == 1
    edge = edges[0]
    assert edge.supersedes_id == original["assessment_id"]
    assert edge.supersession_reason == "corrected completeness"
    assert edge.resolved is True
    assert edge.superseded_kind == _ASSESSMENT
    assert edge.record_kind == _ASSESSMENT
    assert edge.coordinate == ConsumedInputWatermark(
        history_epoch=1, local_sequence=6
    )
    assert snapshot.superseded_by(original["assessment_id"]) == (edge,)
    assert snapshot.superseded_by(correction["assessment_id"]) == ()
    assert snapshot.is_complete is True


# --------------------------------------------------------------------------- #
# 14. Correction branches stay explicit / ambiguous
# --------------------------------------------------------------------------- #


def test_forked_correction_branch_is_explicit_and_unresolved(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    original = ancestry["assessment"]
    branch_a = _assessment_payload(
        request_id=ancestry["request"]["request_id"],
        referenced_role_result_ids=[ancestry["role_result"]["result_id"]],
        selection_rule="selection-2",
        synthesis="branch a",
        supersedes_id=original["assessment_id"],
        supersession_reason="branch a",
    )
    branch_b = _assessment_payload(
        request_id=ancestry["request"]["request_id"],
        referenced_role_result_ids=[ancestry["role_result"]["result_id"]],
        selection_rule="selection-3",
        synthesis="branch b",
        supersedes_id=original["assessment_id"],
        supersession_reason="branch b",
    )
    _write_di(db, [(_ASSESSMENT, branch_a), (_ASSESSMENT, branch_b)])

    snapshot = read_di_evidence_snapshot(db)

    assert set(snapshot.assessments) == {
        original["assessment_id"],
        branch_a["assessment_id"],
        branch_b["assessment_id"],
    }
    assert len(snapshot.superseded_by(original["assessment_id"])) == 2
    assert ANOMALY_AMBIGUOUS_SUPERSESSION in snapshot.anomaly_codes
    assert snapshot.is_complete is False
    ambiguous = [
        anomaly
        for anomaly in snapshot.anomalies
        if anomaly.code == ANOMALY_AMBIGUOUS_SUPERSESSION
    ]
    assert [anomaly.record_id for anomaly in ambiguous] == [
        original["assessment_id"]
    ]
    # No winner is invented anywhere in the public surface.
    for absent in ("latest_assessment", "winning_assessment", "winner"):
        assert not hasattr(snapshot, absent)
        assert not hasattr(DIEvidenceSnapshot, absent)


# --------------------------------------------------------------------------- #
# 15. Malformed known evidence fails closed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tampered",
    ['"{not json"', "[1, 2, 3]", '"a string"', "null"],
)
def test_malformed_known_payload_fails_closed(tmp_path, tampered):
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    event_id = read_di_evidence_snapshot(db).events[0].event_id
    _rewrite_payload(db, event_id, lambda _current: tampered)
    tampered_state = _canonical_state(db)

    with pytest.raises(DIEvidenceIntegrityError):
        read_di_evidence_snapshot(db)
    # The corrupt evidence is neither repaired nor removed.
    assert _canonical_state(db) == tampered_state


def test_known_payload_failing_p1a_identity_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    event_id = read_di_evidence_snapshot(db).events[0].event_id

    def _break_identity(current: str) -> str:
        payload = json.loads(current)
        payload["candidate_id"] = "tampered-candidate"
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    _rewrite_payload(db, event_id, _break_identity)

    with pytest.raises(DIEvidenceIntegrityError, match="P1A validation"):
        read_di_evidence_snapshot(db)


# --------------------------------------------------------------------------- #
# 16. Incompatible schema and unknown future event behavior
# --------------------------------------------------------------------------- #


def test_known_payload_with_future_schema_version_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    event_id = read_di_evidence_snapshot(db).events[0].event_id

    def _bump_version(current: str) -> str:
        payload = json.loads(current)
        payload["schema_version"] = 2
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    _rewrite_payload(db, event_id, _bump_version)

    with pytest.raises(DIIncompatibleSchemaError):
        read_di_evidence_snapshot(db)


def test_incompatible_canonical_schema_version_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    _mutate_canonical(
        db, [("UPDATE meta SET schema_version = ? WHERE id = 1", (2,))]
    )

    with pytest.raises(DIIncompatibleSchemaError):
        read_di_evidence_snapshot(db)


def test_supported_event_envelope_version_is_accepted(tmp_path):
    """FINDING 2 baseline: the writer's EVENT_SCHEMA_VERSION is interpretable."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)

    connection = sqlite3.connect(str(db))
    try:
        versions = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT schema_version FROM events "
                "WHERE event_type GLOB 'decision_intelligence.*'"
            )
        }
    finally:
        connection.close()

    assert versions == {EVENT_SCHEMA_VERSION}
    snapshot = read_di_evidence_snapshot(db)
    assert snapshot.is_complete is True
    assert len(snapshot.events) == 2


def test_known_di_event_with_future_envelope_version_fails_closed(tmp_path):
    """A future canonical envelope cannot be trusted even for a known type."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    snapshot = read_di_evidence_snapshot(db)
    known_event = snapshot.events[0]

    _set_event_envelope_version(
        db, known_event.event_id, schema_version=EVENT_SCHEMA_VERSION + 1
    )

    with pytest.raises(DIIncompatibleSchemaError, match="event envelope"):
        read_di_evidence_snapshot(db)


def test_unknown_di_event_with_supported_envelope_stays_opaque(tmp_path):
    """An unknown TYPE is incomplete; a supported ENVELOPE is still readable."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    _insert_event(
        db,
        event_id="EVT:future-di-envelope-ok",
        event_type="decision_intelligence.future.recorded",
        history_epoch=1,
        local_sequence=3,
        payload_json=json.dumps({"schema_version": 1, "opaque": True}),
    )

    snapshot = read_di_evidence_snapshot(db)

    assert ANOMALY_UNKNOWN_EVENT_TYPE in snapshot.anomaly_codes
    assert snapshot.is_complete is False
    assert [event.event_type for event in snapshot.unknown_events] == [
        "decision_intelligence.future.recorded"
    ]


def test_unknown_di_event_with_unsupported_envelope_fails_closed(tmp_path):
    """The envelope must be interpretable even when the payload stays opaque."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    _insert_event(
        db,
        event_id="EVT:future-di-envelope-bad",
        event_type="decision_intelligence.future.recorded",
        history_epoch=1,
        local_sequence=3,
        payload_json=json.dumps({"schema_version": 1, "opaque": True}),
        schema_version=EVENT_SCHEMA_VERSION + 1,
    )

    with pytest.raises(DIIncompatibleSchemaError, match="event envelope"):
        read_di_evidence_snapshot(db)


@pytest.mark.parametrize("bad_version", [0, -1, 2, 99])
def test_malformed_event_envelope_version_fails_closed(tmp_path, bad_version):
    """``events.schema_version`` is INTEGER NOT NULL, so only integers can be
    recorded; every non-supported integer must fail closed."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    snapshot = read_di_evidence_snapshot(db)
    _set_event_envelope_version(
        db, snapshot.events[0].event_id, schema_version=bad_version
    )

    with pytest.raises(DIIncompatibleSchemaError, match="event envelope"):
        read_di_evidence_snapshot(db)


def test_unknown_future_event_type_surfaces_explicit_incompleteness(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    context, request = _seed_context_and_request(db)
    _insert_event(
        db,
        event_id="EVT:future-di-1",
        event_type="decision_intelligence.future.recorded",
        history_epoch=1,
        local_sequence=3,
        payload_json=json.dumps({"schema_version": 1, "future_field": "opaque"}),
    )

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=3)
    assert [event.event_type for event in snapshot.unknown_events] == [
        "decision_intelligence.future.recorded"
    ]
    assert snapshot.unknown_events[0].event_id == "EVT:future-di-1"
    assert snapshot.unknown_events[0].history_epoch == 1
    assert snapshot.unknown_events[0].local_sequence == 3
    assert ANOMALY_UNKNOWN_EVENT_TYPE in snapshot.anomaly_codes
    assert snapshot.is_complete is False
    # Known evidence is still reconstructed; the unknown event's payload is not
    # interpreted and the snapshot does not claim completeness.
    assert set(snapshot.requests) == {request["request_id"]}
    assert set(snapshot.contexts) == {context["context_id"]}
    assert len(snapshot.events) == 2


def test_evidence_beyond_frozen_di_watermark_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    # Append DI evidence without advancing the canonical DI stream watermark.
    connection = sqlite3.connect(str(db))
    try:
        connection.execute(
            """
            INSERT INTO events (
                event_id, schema_version, event_type, history_epoch,
                local_sequence, recorded_at, event_time, causation_id,
                correlation_id, idempotency_key, payload_json
            ) VALUES (?, ?, ?, 1, 3, ?, NULL, NULL, NULL, ?, '{}')
            """,
            (
                "EVT:beyond-boundary",
                SCHEMA_VERSION,
                _CONTEXT,
                "2026-01-02T03:04:05Z",
                "di-reader-test:beyond-boundary",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DIInconsistentBoundaryError):
        read_di_evidence_snapshot(db)


def test_normal_exact_di_watermark_matches_recorded_tip(tmp_path):
    """FINDING 2 baseline: the writer keeps the DI watermark at the DI tip."""
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    _write_di(
        db,
        [
            (
                _TRANSITION,
                _transition_payload(
                    request_id=ancestry["request"]["request_id"],
                    from_state="ELIGIBLE",
                    to_state="SELECTED",
                ),
            )
        ],
    )

    watermark = _read_stream_watermark(db)
    tip = _di_evidence_tip(db)

    assert watermark == tip
    assert (watermark[0], watermark[1]) == (1, 6)
    snapshot = read_di_evidence_snapshot(db)
    assert snapshot.boundary == ConsumedInputWatermark(
        history_epoch=watermark[0], local_sequence=watermark[1]
    )
    assert snapshot.is_complete is True


def test_watermark_ahead_of_di_tip_in_same_epoch_fails_closed(tmp_path):
    """FINDING 2A: a watermark claiming progress with no boundary event."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    assert _di_evidence_tip(db)[1] == 2
    _set_di_watermark(db, history_epoch=1, local_sequence=99)

    with pytest.raises(DIInconsistentBoundaryError, match="claims progress"):
        read_di_evidence_snapshot(db)


def test_watermark_ahead_of_di_tip_across_epochs_fails_closed(tmp_path):
    """A full-history epoch always dominates local_sequence in commit order."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    _set_di_watermark(db, history_epoch=5, local_sequence=1)

    with pytest.raises(DIInconsistentBoundaryError, match="claims progress"):
        read_di_evidence_snapshot(db)


def test_watermark_row_without_any_di_evidence_fails_closed(tmp_path):
    """FINDING 2B: the writer creates the row only when DI commits."""
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()
    assert _di_evidence_tip(db) is None
    _set_di_watermark(db, history_epoch=1, local_sequence=1)

    with pytest.raises(DIInconsistentBoundaryError, match="no decision intelligence evidence"):
        read_di_evidence_snapshot(db)


def test_di_evidence_without_watermark_row_fails_closed(tmp_path):
    """DI evidence with no frozen boundary cannot be safely scoped."""
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    _delete_di_watermark(db)

    with pytest.raises(DIInconsistentBoundaryError, match="watermark is missing"):
        read_di_evidence_snapshot(db)


def test_non_di_events_beyond_di_tip_do_not_affect_di_comparison(tmp_path):
    """Non-DI streams share the global sequence space and must not be compared."""
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    # DI tip and DI watermark are (1, 2); secondary traffic pushes the global
    # sequence tip well past that without touching the DI stream.
    _write_non_di_event(db, suffix="a")
    _write_non_di_event(db, suffix="b")

    assert _di_evidence_tip(db) == (1, 2)
    assert _read_stream_watermark(db) == (1, 2)
    assert _global_event_tip(db) == 4

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=2)
    assert set(snapshot.requests) == {request["request_id"]}
    assert len(snapshot.events) == 2
    assert snapshot.anomalies == ()
    assert snapshot.is_complete is True


def test_watermark_tracking_survives_interleaved_non_di_events(tmp_path):
    """A sparse DI sequence still validates exactly against DI evidence."""
    db = tmp_path / "canonical.sqlite3"
    context = _context_payload()
    request = _request_payload(context)
    _write_di(db, [(_CONTEXT, context)])
    _write_non_di_event(db, suffix="mid")
    _write_di(db, [(_REQUEST, request)])

    assert _read_stream_watermark(db) == (1, 3)
    assert _di_evidence_tip(db) == (1, 3)
    assert _global_event_tip(db) == 3

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=3)
    assert set(snapshot.requests) == {request["request_id"]}
    assert snapshot.is_complete is True


def test_boundary_validator_is_exact_match_both_directions(tmp_path):
    """Direct check of the DI boundary invariant in all six states."""
    from app.opip.decision_intelligence.evidence_reader import _validate_boundary

    zero = ConsumedInputWatermark.zero()
    tip = ConsumedInputWatermark(history_epoch=1, local_sequence=4)

    # No DI evidence and no row: the valid empty state.
    assert (
        _validate_boundary(watermark=None, di_event_count=0, di_tip=None) == zero
    )
    # Exact match: accepted, and the frozen coordinate is returned.
    assert (
        _validate_boundary(watermark=tip, di_event_count=3, di_tip=tip) == tip
    )
    # Row without evidence: rejected.
    with pytest.raises(DIInconsistentBoundaryError):
        _validate_boundary(watermark=tip, di_event_count=0, di_tip=None)
    # Evidence without row: rejected.
    with pytest.raises(DIInconsistentBoundaryError):
        _validate_boundary(watermark=None, di_event_count=3, di_tip=tip)
    # Watermark ahead of the DI tip: rejected.
    ahead_of_tip = ConsumedInputWatermark(1, 5)
    with pytest.raises(DIInconsistentBoundaryError, match="claims progress"):
        _validate_boundary(
            watermark=ahead_of_tip, di_event_count=3, di_tip=tip
        )
    # Watermark behind the DI tip (evidence beyond): rejected.
    behind_tip = ConsumedInputWatermark(1, 3)
    with pytest.raises(DIInconsistentBoundaryError, match="beyond the frozen"):
        _validate_boundary(
            watermark=behind_tip, di_event_count=3, di_tip=tip
        )


def test_restore_and_epoch_advance_keep_di_boundary_consistent(tmp_path):
    """Restore semantics: epoch advance must not desynchronise the DI boundary."""
    live = tmp_path / "live" / "opip_canonical_v1.sqlite3"
    ancestry = _seed_full_ancestry(live)
    request_id = ancestry["request"]["request_id"]

    backup = tmp_path / "backup" / "opip_canonical_v1.backup.sqlite3"
    backup_database(live, backup)

    restored = tmp_path / "restored" / "opip_canonical_v1.sqlite3"
    restore_from_backup(backup_db=backup, live_db=restored, advance_epoch=True)

    # advance_history_epoch_for_restore only moves meta; the frozen DI row
    # still matches the restored file's DI evidence tip.
    assert _read_stream_watermark(restored) == _di_evidence_tip(restored)
    snapshot = read_di_evidence_snapshot(restored)
    assert snapshot.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=5)
    assert snapshot.is_complete is True

    # A post-restore DI commit lands in the new epoch and stays consistent.
    _write_di(
        restored,
        [
            (
                _TRANSITION,
                _transition_payload(
                    request_id=request_id,
                    from_state="ELIGIBLE",
                    to_state="SELECTED",
                ),
            )
        ],
    )
    assert _read_stream_watermark(restored) == (2, 1)
    assert _di_evidence_tip(restored) == (2, 1)

    after = read_di_evidence_snapshot(restored)
    assert after.boundary == ConsumedInputWatermark(history_epoch=2, local_sequence=1)
    assert after.state_of(request_id) is RequestState.SELECTED
    assert after.is_complete is True


def test_impossible_lifecycle_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed_context_and_request(db)
    request_only = read_di_evidence_snapshot(db)
    request_id = next(iter(request_only.requests))
    transition = _transition_payload(
        request_id=request_id,
        from_state="SELECTED",
        to_state="COMPLETED",
        transition_time="2026-01-02T03:05:00Z",
    )
    # Bypass writer lifecycle validation to record an impossible sequence.
    _insert_event(
        db,
        event_id="EVT:impossible-transition",
        event_type=_TRANSITION,
        history_epoch=1,
        local_sequence=3,
        payload_json=json.dumps(
            transition,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )

    with pytest.raises(DILifecycleReconstructionError):
        read_di_evidence_snapshot(db)


def test_transition_without_recorded_request_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    context = _context_payload()
    _write_di(db, [(_CONTEXT, context)])
    orphan = _transition_payload(
        request_id="DI-REQUEST:unrecorded",
        from_state="ELIGIBLE",
        to_state="SELECTED",
    )
    _insert_event(
        db,
        event_id="EVT:orphan-transition",
        event_type=_TRANSITION,
        history_epoch=1,
        local_sequence=2,
        payload_json=json.dumps(
            orphan,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )

    with pytest.raises(DILifecycleReconstructionError):
        read_di_evidence_snapshot(db)


def test_unrecorded_reference_fails_closed_for_known_records(tmp_path):
    """FINDING 1: an impossible known-record relationship is not incompleteness."""
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    orphan = _invocation_payload(request_id="DI-REQUEST:unrecorded")
    _insert_event(
        db,
        event_id="EVT:orphan-invocation",
        event_type=_INVOCATION,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(orphan),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="ModelInvocation.request_id"
    ):
        read_di_evidence_snapshot(db)


def test_request_with_unrecorded_context_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()
    forged_context = {**_context_payload(), "context_id": "DI-CONTEXT:absent"}
    request = _request_payload(forged_context)
    _insert_event(
        db,
        event_id="EVT:request-absent-context",
        event_type=_REQUEST,
        history_epoch=1,
        local_sequence=1,
        payload_json=_payload_json(request),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="CommitteeRequest.context_id"
    ):
        read_di_evidence_snapshot(db)


def test_request_context_snapshot_mismatch_fails_closed(tmp_path):
    """Mirrors the writer's 'request/context snapshot mismatch' guard."""
    db = tmp_path / "canonical.sqlite3"
    recorded = _context_payload()
    _write_di(db, [(_CONTEXT, recorded)])
    # Same recorded context id, but the request freezes a different snapshot.
    forged = {**recorded, "snapshot_hash": "other-snapshot-hash"}
    request = _request_payload(forged)
    _insert_event(
        db,
        event_id="EVT:request-snapshot-mismatch",
        event_type=_REQUEST,
        history_epoch=1,
        local_sequence=2,
        payload_json=_payload_json(request),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="frozen_snapshot_hash|not valid against"
    ):
        read_di_evidence_snapshot(db)


def test_role_result_with_unrecorded_request_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()
    result = _role_result_payload(request_id="DI-REQUEST:absent")
    _insert_event(
        db,
        event_id="EVT:result-absent-request",
        event_type=_ROLE_RESULT,
        history_epoch=1,
        local_sequence=1,
        payload_json=_payload_json(result),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="CommitteeRoleResult.request_id"
    ):
        read_di_evidence_snapshot(db)


def test_role_result_with_unrecorded_invocation_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    result = _role_result_payload(
        request_id=request["request_id"],
        invocation_ref="DI-INVOCATION:absent",
    )
    _insert_event(
        db,
        event_id="EVT:result-absent-invocation",
        event_type=_ROLE_RESULT,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(result),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="CommitteeRoleResult.invocation_ref"
    ):
        read_di_evidence_snapshot(db)


def test_role_result_invocation_from_other_request_fails_closed(tmp_path):
    """Mirrors the writer's 'role result invocation request mismatch' guard."""
    db = tmp_path / "canonical.sqlite3"
    context_a = _context_payload(candidate="candidate-a")
    context_b = _context_payload(candidate="candidate-b")
    request_a = _request_payload(context_a, experiment="experiment-a")
    request_b = _request_payload(context_b, experiment="experiment-b")
    invocation_b = _invocation_payload(request_id=request_b["request_id"])
    _write_di(
        db,
        [
            (_CONTEXT, context_a),
            (_CONTEXT, context_b),
            (_REQUEST, request_a),
            (_REQUEST, request_b),
            (_INVOCATION, invocation_b),
        ],
    )
    result = _role_result_payload(
        request_id=request_a["request_id"],
        invocation_ref=invocation_b["invocation_id"],
    )
    _insert_event(
        db,
        event_id="EVT:result-cross-request-invocation",
        event_type=_ROLE_RESULT,
        history_epoch=1,
        local_sequence=6,
        payload_json=_payload_json(result),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="belongs to request"):
        read_di_evidence_snapshot(db)


def test_assessment_with_unrecorded_request_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()
    assessment = _assessment_payload(request_id="DI-REQUEST:absent")
    _insert_event(
        db,
        event_id="EVT:assessment-absent-request",
        event_type=_ASSESSMENT,
        history_epoch=1,
        local_sequence=1,
        payload_json=_payload_json(assessment),
    )

    with pytest.raises(
        DIEvidenceIntegrityError,
        match="CommitteeAssessmentSummary.request_id",
    ):
        read_di_evidence_snapshot(db)


@pytest.mark.parametrize("field_name", ["role_result", "invocation"])
def test_assessment_reference_missing_or_cross_request_fails_closed(
    tmp_path, field_name
):
    db = tmp_path / "canonical.sqlite3"
    context_a = _context_payload(candidate="candidate-a")
    context_b = _context_payload(candidate="candidate-b")
    request_a = _request_payload(context_a, experiment="experiment-a")
    request_b = _request_payload(context_b, experiment="experiment-b")
    invocation_b = _invocation_payload(request_id=request_b["request_id"])
    result_b = _role_result_payload(
        request_id=request_b["request_id"],
        invocation_ref=invocation_b["invocation_id"],
    )
    _write_di(
        db,
        [
            (_CONTEXT, context_a),
            (_CONTEXT, context_b),
            (_REQUEST, request_a),
            (_REQUEST, request_b),
            (_INVOCATION, invocation_b),
            (_ROLE_RESULT, result_b),
        ],
    )

    if field_name == "role_result":
        assessment = _assessment_payload(
            request_id=request_a["request_id"],
            referenced_role_result_ids=[result_b["result_id"]],
        )
        expected = "CommitteeAssessmentSummary.referenced_role_result_ids"
    else:
        assessment = _assessment_payload(
            request_id=request_a["request_id"],
            invocation_references=[invocation_b["invocation_id"]],
        )
        expected = "CommitteeAssessmentSummary.invocation_references"

    _insert_event(
        db,
        event_id=f"EVT:assessment-cross-request-{field_name}",
        event_type=_ASSESSMENT,
        history_epoch=1,
        local_sequence=7,
        payload_json=_payload_json(assessment),
    )

    with pytest.raises(DIEvidenceIntegrityError, match=expected):
        read_di_evidence_snapshot(db)


def test_assessment_unrecorded_role_result_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    assessment = _assessment_payload(
        request_id=request["request_id"],
        referenced_role_result_ids=["DI-ROLE-RESULT:absent"],
    )
    _insert_event(
        db,
        event_id="EVT:assessment-absent-result",
        event_type=_ASSESSMENT,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(assessment),
    )

    with pytest.raises(
        DIEvidenceIntegrityError,
        match="CommitteeAssessmentSummary.referenced_role_result_ids",
    ):
        read_di_evidence_snapshot(db)


def test_comparison_with_unrecorded_request_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    context = _context_payload()
    _write_di(db, [(_CONTEXT, context)])
    comparison = _comparison_payload(
        context_id=context["context_id"],
        request_id="DI-REQUEST:absent",
        assessment_id="DI-ASSESSMENT:absent",
    )
    _insert_event(
        db,
        event_id="EVT:comparison-absent-request",
        event_type=_COMPARISON,
        history_epoch=1,
        local_sequence=2,
        payload_json=_payload_json(comparison),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="ComparisonRecord.committee_request_id"
    ):
        read_di_evidence_snapshot(db)


def test_comparison_request_context_link_mismatch_fails_closed(tmp_path):
    """Mirrors the writer's 'comparison request/context mismatch' guard."""
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    other_context = _context_payload(candidate="candidate-other")
    _write_di(db, [(_CONTEXT, other_context)])
    comparison = _comparison_payload(
        context_id=other_context["context_id"],
        request_id=ancestry["request"]["request_id"],
        assessment_id=ancestry["assessment"]["assessment_id"],
    )
    _insert_event(
        db,
        event_id="EVT:comparison-context-mismatch",
        event_type=_COMPARISON,
        history_epoch=1,
        local_sequence=7,
        payload_json=_payload_json(comparison),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="claims context"):
        read_di_evidence_snapshot(db)


def test_comparison_experiment_mismatch_fails_closed(tmp_path):
    """Mirrors the writer's 'comparison experiment/request mismatch' guard."""
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    comparison = _comparison_payload(
        context_id=ancestry["context"]["context_id"],
        request_id=ancestry["request"]["request_id"],
        assessment_id=ancestry["assessment"]["assessment_id"],
        experiment="experiment-other",
    )
    _insert_event(
        db,
        event_id="EVT:comparison-experiment-mismatch",
        event_type=_COMPARISON,
        history_epoch=1,
        local_sequence=6,
        payload_json=_payload_json(comparison),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="does not agree with request"):
        read_di_evidence_snapshot(db)


def test_comparison_assessment_from_other_request_fails_closed(tmp_path):
    """Mirrors the writer's 'comparison assessment request mismatch' guard."""
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    other_context = _context_payload(candidate="candidate-other")
    other_request = _request_payload(other_context, experiment="experiment-1")
    other_assessment = _assessment_payload(request_id=other_request["request_id"])
    _write_di(
        db,
        [
            (_CONTEXT, other_context),
            (_REQUEST, other_request),
            (_ASSESSMENT, other_assessment),
        ],
    )
    comparison = _comparison_payload(
        context_id=ancestry["context"]["context_id"],
        request_id=ancestry["request"]["request_id"],
        assessment_id=other_assessment["assessment_id"],
    )
    _insert_event(
        db,
        event_id="EVT:comparison-cross-request-assessment",
        event_type=_COMPARISON,
        history_epoch=1,
        local_sequence=9,
        payload_json=_payload_json(comparison),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="belongs to request"):
        read_di_evidence_snapshot(db)


def test_comparison_invocation_ref_from_other_request_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    other_context = _context_payload(candidate="candidate-other")
    other_request = _request_payload(other_context, experiment="experiment-1")
    other_invocation = _invocation_payload(request_id=other_request["request_id"])
    _write_di(
        db,
        [
            (_CONTEXT, other_context),
            (_REQUEST, other_request),
            (_INVOCATION, other_invocation),
        ],
    )
    comparison = _comparison_payload(
        context_id=ancestry["context"]["context_id"],
        request_id=ancestry["request"]["request_id"],
        assessment_id=ancestry["assessment"]["assessment_id"],
        invocation_refs=(other_invocation["invocation_id"],),
    )
    _insert_event(
        db,
        event_id="EVT:comparison-cross-request-invocation",
        event_type=_COMPARISON,
        history_epoch=1,
        local_sequence=9,
        payload_json=_payload_json(comparison),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="belongs to request"):
        read_di_evidence_snapshot(db)


def test_unresolved_supersession_target_fails_closed(tmp_path):
    """FINDING 1: an unrecorded supersession target is integrity failure."""
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    orphan_correction = _assessment_payload(
        request_id=ancestry["request"]["request_id"],
        referenced_role_result_ids=[ancestry["role_result"]["result_id"]],
        selection_rule="selection-2",
        synthesis="orphan correction",
        supersedes_id="DI-ASSESSMENT:absent",
        supersession_reason="orphan",
    )
    _insert_event(
        db,
        event_id="EVT:orphan-supersession",
        event_type=_ASSESSMENT,
        history_epoch=1,
        local_sequence=6,
        payload_json=_payload_json(orphan_correction),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="not a recorded record of the same kind"
    ):
        read_di_evidence_snapshot(db)


def _two_request_world(db: Path) -> dict:
    """Two isolated context/request worlds, each with full downstream evidence."""
    context_a = _context_payload(candidate="candidate-a")
    context_b = _context_payload(candidate="candidate-b")
    request_a = _request_payload(context_a, experiment="experiment-a")
    request_b = _request_payload(context_b, experiment="experiment-b")
    invocation_a = _invocation_payload(request_id=request_a["request_id"])
    invocation_b = _invocation_payload(request_id=request_b["request_id"])
    result_a = _role_result_payload(
        request_id=request_a["request_id"],
        invocation_ref=invocation_a["invocation_id"],
    )
    result_b = _role_result_payload(
        request_id=request_b["request_id"],
        invocation_ref=invocation_b["invocation_id"],
    )
    assessment_a = _assessment_payload(
        request_id=request_a["request_id"],
        referenced_role_result_ids=[result_a["result_id"]],
    )
    assessment_b = _assessment_payload(
        request_id=request_b["request_id"],
        referenced_role_result_ids=[result_b["result_id"]],
    )
    comparison_a = _comparison_payload(
        context_id=context_a["context_id"],
        request_id=request_a["request_id"],
        assessment_id=assessment_a["assessment_id"],
        experiment="experiment-a",
    )
    _write_di(
        db,
        [
            (_CONTEXT, context_a),
            (_CONTEXT, context_b),
            (_REQUEST, request_a),
            (_REQUEST, request_b),
            (_INVOCATION, invocation_a),
            (_INVOCATION, invocation_b),
            (_ROLE_RESULT, result_a),
            (_ROLE_RESULT, result_b),
            (_ASSESSMENT, assessment_a),
            (_ASSESSMENT, assessment_b),
            (_COMPARISON, comparison_a),
        ],
    )
    return {
        "context_a": context_a,
        "context_b": context_b,
        "request_a": request_a,
        "request_b": request_b,
        "invocation_a": invocation_a,
        "invocation_b": invocation_b,
        "result_a": result_a,
        "result_b": result_b,
        "assessment_a": assessment_a,
        "assessment_b": assessment_b,
        "comparison_a": comparison_a,
    }


@pytest.mark.parametrize(
    "case", ["request", "invocation", "role_result", "assessment", "comparison"]
)
def test_supersession_ownership_violation_fails_closed(tmp_path, case):
    """Each kind reproduces the writer's cross-owner supersession guard.

    ``DecisionContext`` is intentionally absent: the writer imposes no ownership
    constraint on a context superseding a context, so there is no violation to
    reproduce for that kind.
    """
    db = tmp_path / "canonical.sqlite3"
    world = _two_request_world(db)

    if case == "request":
        offending = _request_payload(
            world["context_a"],
            experiment="experiment-a",
            supersedes_id=world["request_b"]["request_id"],
            supersession_reason="cross-context",
        )
    elif case == "invocation":
        offending = _invocation_payload(
            request_id=world["request_a"]["request_id"],
            supersedes_id=world["invocation_b"]["invocation_id"],
            supersession_reason="cross-request",
        )
    elif case == "role_result":
        offending = _role_result_payload(
            request_id=world["request_b"]["request_id"],
            invocation_ref=world["invocation_b"]["invocation_id"],
            supersedes_id=world["result_a"]["result_id"],
            supersession_reason="cross-request",
        )
    elif case == "assessment":
        offending = _assessment_payload(
            request_id=world["request_b"]["request_id"],
            referenced_role_result_ids=[world["result_b"]["result_id"]],
            supersedes_id=world["assessment_a"]["assessment_id"],
            supersession_reason="cross-request",
        )
    else:
        comparison_b = _comparison_payload(
            context_id=world["context_b"]["context_id"],
            request_id=world["request_b"]["request_id"],
            assessment_id=world["assessment_b"]["assessment_id"],
            experiment="experiment-b",
        )
        _write_di(db, [(_COMPARISON, comparison_b)])
        offending = _comparison_payload(
            context_id=world["context_b"]["context_id"],
            request_id=world["request_b"]["request_id"],
            assessment_id=world["assessment_b"]["assessment_id"],
            experiment="experiment-b",
            supersedes_id=world["comparison_a"]["comparison_id"],
            supersession_reason="cross-request",
        )

    _insert_event(
        db,
        event_id=f"EVT:supersession-ownership-{case}",
        event_type=_RECORD_TYPES_BY_EVENT_TYPE[case],
        history_epoch=1,
        local_sequence=20,
        payload_json=_payload_json(offending),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="with a different"):
        read_di_evidence_snapshot(db)


def test_transition_correction_must_preserve_lifecycle_coordinate(tmp_path):
    """FINDING 1: a malformed correction is never accepted silently."""
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    request_id = request["request_id"]
    original = _transition_payload(
        request_id=request_id,
        from_state="ELIGIBLE",
        to_state="SELECTED",
        transition_time="2026-01-02T03:05:00Z",
    )
    _write_di(db, [(_TRANSITION, original)])
    # A correction that changes the recorded target state.
    malformed = _transition_payload(
        request_id=request_id,
        from_state="ELIGIBLE",
        to_state="SKIPPED_BUDGET",
        transition_time="2026-01-02T03:05:00Z",
        supersedes_id=original["transition_id"],
        supersession_reason="malformed correction",
    )
    _insert_event(
        db,
        event_id="EVT:malformed-correction",
        event_type=_TRANSITION,
        history_epoch=1,
        local_sequence=4,
        payload_json=_payload_json(malformed),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="must preserve lifecycle coordinate"
    ):
        read_di_evidence_snapshot(db)


def test_transition_correction_with_unrecorded_target_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    malformed = _transition_payload(
        request_id=request["request_id"],
        from_state="ELIGIBLE",
        to_state="SELECTED",
        supersedes_id="DI-TRANSITION:absent",
        supersession_reason="orphan correction",
    )
    _insert_event(
        db,
        event_id="EVT:orphan-transition-correction",
        event_type=_TRANSITION,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(malformed),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="not a recorded record of the same kind"
    ):
        read_di_evidence_snapshot(db)


def _seed_evidence_context(db: Path, *, manifest, candidate="candidate-1"):
    """A request whose frozen context carries the supplied evidence manifest."""
    context = _context_payload(candidate=candidate, manifest=manifest)
    request = _request_payload(context)
    _write_di(db, [(_CONTEXT, context), (_REQUEST, request)])
    return context, request


def test_role_result_evidence_ref_absent_from_manifest_fails_closed(tmp_path):
    """FINAL GAP: a ref outside the frozen manifest is corruption, not ambiguity."""
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_evidence_context(
        db, manifest={"evidence:known": {"available_at": "2026-01-02T03:00:00Z"}}
    )
    result = _role_result_payload(
        request_id=request["request_id"],
        evidence_refs=("evidence:absent",),
    )
    _insert_event(
        db,
        event_id="EVT:role-result-ref-absent",
        event_type=_ROLE_RESULT,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(result),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="not eligible against the frozen"
    ):
        read_di_evidence_snapshot(db)


def test_role_result_evidence_available_after_cutoff_fails_closed(tmp_path):
    """FINAL GAP: evidence available after the frozen cutoff must never be used.

    The frozen ``DecisionContext`` contract rejects a manifest entry beyond its
    own ``evidence_cutoff`` at construction, so this corruption is caught while
    hydrating the context — the same invariant, enforced one layer earlier. The
    reader must still fail closed rather than expose one of the two records.
    """
    db = tmp_path / "canonical.sqlite3"
    context = _context_payload(
        manifest={"evidence:late": {"available_at": "2026-01-02T09:00:00Z"}}
    )
    request = _request_payload(context)
    result = _role_result_payload(
        request_id=request["request_id"],
        evidence_refs=("evidence:late",),
    )
    # Injected directly: the writer rejects a manifest entry beyond cutoff.
    CanonicalWriter(db).close()
    _insert_event(
        db,
        event_id="EVT:late-context",
        event_type=_CONTEXT,
        history_epoch=1,
        local_sequence=1,
        payload_json=_payload_json(context),
    )
    _insert_event(
        db,
        event_id="EVT:late-request",
        event_type=_REQUEST,
        history_epoch=1,
        local_sequence=2,
        payload_json=_payload_json(request),
    )
    _insert_event(
        db,
        event_id="EVT:role-result-ref-late",
        event_type=_ROLE_RESULT,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(result),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="available after cutoff"):
        read_di_evidence_snapshot(db)


def test_assessment_evidence_ref_absent_from_manifest_fails_closed(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_evidence_context(
        db, manifest={"evidence:known": {"available_at": "2026-01-02T03:00:00Z"}}
    )
    assessment = _assessment_payload(
        request_id=request["request_id"],
        evidence_refs=("evidence:absent",),
    )
    _insert_event(
        db,
        event_id="EVT:assessment-ref-absent",
        event_type=_ASSESSMENT,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(assessment),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="not eligible against the frozen"
    ):
        read_di_evidence_snapshot(db)


def test_assessment_evidence_available_after_cutoff_fails_closed(tmp_path):
    """The same cutoff invariant applies to assessment evidence_refs."""
    db = tmp_path / "canonical.sqlite3"
    context = _context_payload(
        manifest={"evidence:late": {"available_at": "2026-01-02T09:00:00Z"}}
    )
    request = _request_payload(context)
    assessment = _assessment_payload(
        request_id=request["request_id"],
        evidence_refs=("evidence:late",),
    )
    CanonicalWriter(db).close()
    _insert_event(
        db,
        event_id="EVT:late-context-b",
        event_type=_CONTEXT,
        history_epoch=1,
        local_sequence=1,
        payload_json=_payload_json(context),
    )
    _insert_event(
        db,
        event_id="EVT:late-request-b",
        event_type=_REQUEST,
        history_epoch=1,
        local_sequence=2,
        payload_json=_payload_json(request),
    )
    _insert_event(
        db,
        event_id="EVT:assessment-ref-late",
        event_type=_ASSESSMENT,
        history_epoch=1,
        local_sequence=3,
        payload_json=_payload_json(assessment),
    )

    with pytest.raises(DIEvidenceIntegrityError, match="available after cutoff"):
        read_di_evidence_snapshot(db)


def test_eligible_evidence_refs_reconstruct_and_pass(tmp_path):
    """Positive control: in-manifest, in-cutoff refs are valid recorded evidence."""
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_evidence_context(
        db,
        manifest={
            "evidence:known": {"available_at": "2026-01-02T03:00:00Z"},
            "evidence:no-clock": {},
        },
    )
    request_id = request["request_id"]
    result = _role_result_payload(
        request_id=request_id,
        evidence_refs=("evidence:known", "evidence:no-clock"),
    )
    assessment = _assessment_payload(
        request_id=request_id,
        referenced_role_result_ids=[result["result_id"]],
        evidence_refs=("evidence:known",),
    )
    _write_di(db, [(_ROLE_RESULT, result), (_ASSESSMENT, assessment)])

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.role_results[result["result_id"]].evidence_refs == (
        "evidence:known",
        "evidence:no-clock",
    )
    assert snapshot.assessments[assessment["assessment_id"]].evidence_refs == (
        "evidence:known",
    )
    # A ref exactly at the cutoff is available (boundary is inclusive).
    assert snapshot.anomalies == ()
    assert snapshot.is_complete is True


def test_evidence_ref_at_cutoff_is_eligible(tmp_path):
    """The availability boundary is inclusive, matching the contract."""
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_evidence_context(
        db, manifest={"evidence:boundary": {"available_at": "2026-01-02T03:04:00Z"}}
    )
    result = _role_result_payload(
        request_id=request["request_id"],
        evidence_refs=("evidence:boundary",),
    )
    _write_di(db, [(_ROLE_RESULT, result)])

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.role_results[result["result_id"]].evidence_refs == (
        "evidence:boundary",
    )
    assert snapshot.is_complete is True


def test_empty_evidence_refs_remain_valid(tmp_path):
    """FINAL GAP: empty evidence_refs is optional and stays valid everywhere."""
    db = tmp_path / "canonical.sqlite3"
    # Deliberately empty manifest: no refs means no eligibility obligation.
    ancestry = _seed_full_ancestry(db)

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.role_results[
        ancestry["role_result"]["result_id"]
    ].evidence_refs == ()
    assert snapshot.assessments[
        ancestry["assessment"]["assessment_id"]
    ].evidence_refs == ()
    assert snapshot.anomalies == ()
    assert snapshot.is_complete is True


def test_evidence_ref_eligibility_is_scoped_per_request_context(tmp_path):
    """A ref eligible in one frozen context must not satisfy another."""
    db = tmp_path / "canonical.sqlite3"
    manifest_a = {"evidence:a": {"available_at": "2026-01-02T03:00:00Z"}}
    manifest_b = {"evidence:b": {"available_at": "2026-01-02T03:00:00Z"}}
    context_a = _context_payload(candidate="candidate-a", manifest=manifest_a)
    context_b = _context_payload(candidate="candidate-b", manifest=manifest_b)
    request_a = _request_payload(context_a, experiment="experiment-a")
    request_b = _request_payload(context_b, experiment="experiment-b")
    _write_di(
        db,
        [
            (_CONTEXT, context_a),
            (_CONTEXT, context_b),
            (_REQUEST, request_a),
            (_REQUEST, request_b),
        ],
    )
    # request_b's frozen context never had evidence:a, so citing it is corruption.
    offending = _role_result_payload(
        request_id=request_b["request_id"],
        evidence_refs=("evidence:a",),
    )
    _insert_event(
        db,
        event_id="EVT:cross-context-evidence-ref",
        event_type=_ROLE_RESULT,
        history_epoch=1,
        local_sequence=5,
        payload_json=_payload_json(offending),
    )

    with pytest.raises(
        DIEvidenceIntegrityError, match="evidence reference is not in frozen manifest"
    ):
        read_di_evidence_snapshot(db)


def test_valid_transition_correction_is_preserved_not_applied(tmp_path):
    """A well-formed correction is admitted and does not advance state."""
    db = tmp_path / "canonical.sqlite3"
    _, request = _seed_context_and_request(db)
    request_id = request["request_id"]
    original = _transition_payload(
        request_id=request_id,
        from_state="ELIGIBLE",
        to_state="SELECTED",
        transition_time="2026-01-02T03:05:00Z",
    )
    correction = _transition_payload(
        request_id=request_id,
        from_state="ELIGIBLE",
        to_state="SELECTED",
        transition_time="2026-01-02T03:05:00Z",
        reason="restated reason",
        supersedes_id=original["transition_id"],
        supersession_reason="restatement",
    )
    _write_di(db, [(_TRANSITION, original), (_TRANSITION, correction)])

    snapshot = read_di_evidence_snapshot(db)

    lifecycle = snapshot.lifecycles[request_id]
    assert [t.transition_id for t in lifecycle.applied_transitions] == [
        original["transition_id"]
    ]
    assert [t.transition_id for t in lifecycle.corrected_transitions] == [
        correction["transition_id"]
    ]
    assert lifecycle.state is RequestState.SELECTED
    edge = snapshot.supersessions_of(correction["transition_id"])[0]
    assert edge.supersedes_id == original["transition_id"]
    assert edge.resolved is True
    assert snapshot.is_complete is True


# --------------------------------------------------------------------------- #
# 17. Reader never opens the writer path
# --------------------------------------------------------------------------- #


def test_reader_module_has_no_writer_or_write_schema_coupling():
    from app.opip.decision_intelligence import evidence_reader

    source = Path(evidence_reader.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(evidence_reader.__file__))

    imported: list[str] = []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    assert not any(
        module == "app.opip.canonical.writer"
        or module.startswith("app.opip.canonical.writer.")
        for module in imported
    )
    assert "CanonicalWriter" not in names
    assert "initialize_schema" not in names
    # The canonical read-only connection mechanism is the only SQLite entry.
    assert "sqlite3" in imported
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr == "connect"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sqlite3"
        for node in ast.walk(tree)
    )


def test_reader_opens_canonical_database_read_only_and_refuses_writes(
    tmp_path, monkeypatch
):
    db = tmp_path / "canonical.sqlite3"
    _seed_full_ancestry(db)

    from app.opip.canonical import schema

    calls: list[bool] = []
    real_connect = schema.connect

    def _spy(target, *, read_only=False):
        calls.append(read_only)
        return real_connect(target, read_only=read_only)

    monkeypatch.setattr(schema, "connect", _spy)
    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.is_complete is True
    assert calls == [True]

    read_only_connection = real_connect(db, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            read_only_connection.execute("DELETE FROM events")
    finally:
        read_only_connection.close()


# --------------------------------------------------------------------------- #
# 18. No database mutation
# --------------------------------------------------------------------------- #


def test_reading_does_not_mutate_canonical_storage(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    _write_di(
        db,
        [
            (
                _TRANSITION,
                _transition_payload(
                    request_id=ancestry["request"]["request_id"],
                    from_state="ELIGIBLE",
                    to_state="SELECTED",
                ),
            )
        ],
    )

    before_state = _canonical_state(db)
    before_bytes = hashlib.sha256(db.read_bytes()).hexdigest()
    before_files = sorted(path.name for path in db.parent.iterdir())

    for _ in range(2):
        assert read_di_evidence_snapshot(db).is_complete is True

    assert _canonical_state(db) == before_state
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before_bytes
    assert sorted(path.name for path in db.parent.iterdir()) == before_files


# --------------------------------------------------------------------------- #
# 19. Backup / restore produces equivalent reader output
# --------------------------------------------------------------------------- #


def test_backup_and_restore_produce_equivalent_snapshot(tmp_path):
    live = tmp_path / "live" / "opip_canonical_v1.sqlite3"
    ancestry = _seed_full_ancestry(live)
    request_id = ancestry["request"]["request_id"]
    _write_di(
        live,
        [
            (
                _TRANSITION,
                _transition_payload(
                    request_id=request_id,
                    from_state="ELIGIBLE",
                    to_state="SELECTED",
                    transition_time="2026-01-02T03:06:00Z",
                ),
            ),
            (
                _TRANSITION,
                _transition_payload(
                    request_id=request_id,
                    from_state="SELECTED",
                    to_state="COMPLETED",
                    transition_time="2026-01-02T03:07:00Z",
                ),
            ),
        ],
    )

    live_snapshot = read_di_evidence_snapshot(live)

    backup = tmp_path / "backup" / "opip_canonical_v1.backup.sqlite3"
    backup_database(live, backup)
    backup_snapshot = read_di_evidence_snapshot(backup)
    assert backup_snapshot == live_snapshot

    restored = tmp_path / "restored" / "opip_canonical_v1.sqlite3"
    restore_from_backup(backup_db=backup, live_db=restored, advance_epoch=True)
    restored_snapshot = read_di_evidence_snapshot(restored)

    assert restored_snapshot == live_snapshot
    assert restored_snapshot.boundary == live_snapshot.boundary
    assert restored_snapshot.is_complete is True
    assert restored_snapshot.state_of(
        ancestry["request"]["request_id"]
    ) is RequestState.COMPLETED


# --------------------------------------------------------------------------- #
# 20. Existing P1A authority / import boundaries remain valid
# --------------------------------------------------------------------------- #


def test_reader_snapshot_is_frozen_against_concurrent_canonical_commit(
    tmp_path, monkeypatch
):
    """A commit during an in-flight read must not enter that reader's snapshot.

    Deterministic: the in-flight reader's SQLite read snapshot is established
    first (the boundary read runs before the evidence read), then a separate
    canonical writer commits new Decision Intelligence evidence while the read
    transaction is still open. No sleeps, no production changes.
    """
    db = tmp_path / "canonical.sqlite3"
    context, request = _seed_context_and_request(db)
    request_id = request["request_id"]

    from app.opip.decision_intelligence import evidence_reader

    real_read = evidence_reader._read_committed_di_events
    observed: dict = {}

    def _commit_then_read(connection, boundary):
        transition = _transition_payload(
            request_id=request_id,
            from_state="ELIGIBLE",
            to_state="SELECTED",
            transition_time="2026-01-02T03:06:00Z",
        )
        _write_di(db, [(_TRANSITION, transition)])
        observed["transition_id"] = transition["transition_id"]
        observed["frozen_boundary"] = boundary
        return real_read(connection, boundary)

    monkeypatch.setattr(
        evidence_reader, "_read_committed_di_events", _commit_then_read
    )

    frozen = read_di_evidence_snapshot(db)

    # The in-flight reader keeps its original frozen boundary and evidence.
    assert observed["frozen_boundary"] == ConsumedInputWatermark(
        history_epoch=1, local_sequence=2
    )
    assert frozen.boundary == ConsumedInputWatermark(
        history_epoch=1, local_sequence=2
    )
    assert [event.record_id for event in frozen.events] == [
        context["context_id"],
        request_id,
    ]
    assert frozen.transitions_for(request_id) == ()
    assert frozen.state_of(request_id) is RequestState.ELIGIBLE

    # A subsequent read sees the newly committed evidence.
    monkeypatch.undo()
    after = read_di_evidence_snapshot(db)

    assert after.boundary == ConsumedInputWatermark(history_epoch=1, local_sequence=3)
    assert [
        transition.transition_id for transition in after.transitions_for(request_id)
    ] == [observed["transition_id"]]
    assert after.state_of(request_id) is RequestState.SELECTED
    assert after.is_complete is True


def test_p1a_authority_and_import_boundaries_remain_intact():
    repo = Path(__file__).resolve().parents[1]
    runtime_roots = (
        repo / "app/opip/discovery",
        repo / "app/opip/decision",
        repo / "app/opip/risk",
        repo / "app/services",
        repo / "app/api",
        repo / "app/jobs",
    )
    imported: list[str] = []
    for root in runtime_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)

    assert not any(
        name == "app.opip.decision_intelligence"
        or name.startswith("app.opip.decision_intelligence.")
        for name in imported
    )

    for contract in (
        CommitteeRequest,
        CommitteeRoleResult,
        ModelInvocation,
        ComparisonRecord,
    ):
        assert "trade_authority" not in contract.__dataclass_fields__
        assert "execution_authority" not in contract.__dataclass_fields__
        assert "order_authority" not in contract.__dataclass_fields__

    for absent in (
        "trade_authority",
        "execution_authority",
        "order_authority",
        "place_order",
        "execute",
    ):
        assert not hasattr(DIEvidenceSnapshot, absent)


def test_full_vocabulary_snapshot_is_complete_without_anomalies(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    ancestry = _seed_full_ancestry(db)
    request_id = ancestry["request"]["request_id"]
    _write_di(
        db,
        [
            (
                _TRANSITION,
                _transition_payload(
                    request_id=request_id,
                    from_state="ELIGIBLE",
                    to_state="SELECTED",
                ),
            ),
            (
                _COMPARISON,
                _comparison_payload(
                    context_id=ancestry["context"]["context_id"],
                    request_id=request_id,
                    assessment_id=ancestry["assessment"]["assessment_id"],
                    invocation_refs=(ancestry["invocation"]["invocation_id"],),
                ),
            ),
        ],
    )

    snapshot = read_di_evidence_snapshot(db)

    assert snapshot.unknown_events == ()
    assert snapshot.anomalies == ()
    assert snapshot.anomaly_codes == frozenset()
    assert snapshot.is_complete is True
    assert snapshot.cost_evidence_complete is True
    assert len(snapshot.events) == len(DECISION_INTELLIGENCE_EVENT_TYPES)
    assert {event.event_type for event in snapshot.events} == set(
        DECISION_INTELLIGENCE_EVENT_TYPES
    )
    assert DI_ANOMALY_CODES == {
        ANOMALY_AMBIGUOUS_SUPERSESSION,
        ANOMALY_UNKNOWN_EVENT_TYPE,
    }
