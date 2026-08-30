"""Sequence 5 BUILD 5.1 Decision V2 evidence/replay contracts."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from app.opip.decision.engine import CandidateEvidence, OPipDecisionEngine
from app.opip.decision.evidence import EvidenceCompleteness, OPipDecisionEvidence
from app.opip.decision.funnel import AI_SUCCEEDED, AIStageEvidence
from app.opip.decision.models import GATE_INDEX
from app.opip.decision.models_v2 import (
    DecisionRole,
    ENGINE_VERSION,
    build_decision_id,
    from_v1_decision,
)
from app.opip.decision.policy_snapshot import GatePolicySnapshot
from app.opip.decision.replay import (
    EngineCodeFingerprintMismatchError,
    EngineVersionMismatchError,
    PolicyVersionMismatchError,
    replay_decision,
)
from app.opip.decision.serialization import canonical_serialize
from app.opip.decision.versioning import app_code_fingerprint, gate_policy_fingerprint
from tests.test_opip_decision_engine_v1 import execution, snapshot


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def evidence(**overrides) -> OPipDecisionEvidence:
    candidate = snapshot()
    candidate.execution_validation = execution()
    values = dict(
        decision_time_utc=NOW,
        episode_id="EP:build51",
        cohort_id="COHORT:build51",
        candidate_id="OPIPC:build51",
        canonical_asset_id="solana",
        asset_display_name="Solana",
        pair="SOLUSD",
        market_type="SPOT",
        direction="LONG",
        asset_identity_provenance=(
            ("source_symbol", "SOL"),
            ("canonical_asset_id", "solana"),
            ("resolved_at_utc", NOW.isoformat()),
        ),
        candidate_snapshot=candidate,
        gate_policy_snapshot=GatePolicySnapshot.capture_current(),
        account_equity=10_000.0,
    )
    values.update(overrides)
    return OPipDecisionEvidence.build(**values)


def test_canonical_serialization_key_order_is_stable():
    assert canonical_serialize({"b": 2, "a": 1}) == canonical_serialize(
        {"a": 1, "b": 2}
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_canonical_serialization_rejects_nonfinite_float(bad):
    with pytest.raises(ValueError):
        canonical_serialize({"bad": bad})


def test_canonical_serialization_rejects_naive_datetime():
    with pytest.raises(ValueError):
        canonical_serialize({"at": datetime(2026, 8, 29, 12, 0)})


def test_canonical_serialization_rejects_set():
    with pytest.raises(TypeError):
        canonical_serialize({"values": {1, 2}})


def test_policy_snapshot_matches_live_fingerprint():
    policy = GatePolicySnapshot.capture_current()
    assert policy.policy_fingerprint == gate_policy_fingerprint()
    assert policy.calculated_fingerprint() == policy.policy_fingerprint
    assert policy.snapshot_hash.startswith("POL:")


def test_policy_snapshot_detects_tampering():
    policy = GatePolicySnapshot.capture_current()
    altered = replace(
        policy,
        thresholds_ordered=tuple(
            (key, 999 if key == "ai_min_confidence" else value)
            for key, value in policy.thresholds_ordered
        ),
    )
    with pytest.raises(ValueError):
        altered.validate_integrity()


def test_streaming_is_optional_for_current_policy():
    assert (
        evidence(streaming_snapshot_ref=None).evidence_completeness
        is EvidenceCompleteness.COMPLETE
    )


def test_streaming_required_only_when_frozen_policy_requires_it():
    policy = GatePolicySnapshot.capture_current(requires_streaming_evidence=True)
    row = evidence(gate_policy_snapshot=policy)
    assert row.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "streaming_snapshot_ref" in row.computed_missing_evidence


def test_required_risk_reference_missing_is_incomplete():
    policy = GatePolicySnapshot.capture_current(
        required_evidence_refs=("risk_snapshot_ref",)
    )
    assert (
        evidence(gate_policy_snapshot=policy).evidence_completeness
        is EvidenceCompleteness.INCOMPLETE
    )


def test_degraded_evidence_is_not_silently_complete():
    assert (
        evidence(degraded_evidence=("provider_late",)).evidence_completeness
        is EvidenceCompleteness.DEGRADED
    )


def test_evidence_hash_is_full_sha256_and_identity():
    row = evidence()
    assert row.evidence_hash.startswith("EVH:")
    assert len(row.evidence_hash) == 68
    assert row.evidence_snapshot_id == row.evidence_hash


def test_same_evidence_rebuild_has_same_hash():
    assert evidence().evidence_hash == evidence().evidence_hash


def test_mutating_source_after_seal_cannot_change_hash():
    candidate = snapshot()
    candidate.execution_validation = execution()
    row = evidence(candidate_snapshot=candidate)
    before = row.evidence_hash
    candidate.last_price = 999.0
    assert row.evidence_hash == before
    assert row.candidate_snapshot["last_price"] != 999.0


def test_changed_evidence_changes_hash():
    candidate = snapshot(last_price=101.0)
    candidate.execution_validation = execution()
    assert evidence().evidence_hash != evidence(
        candidate_snapshot=candidate
    ).evidence_hash


def test_identity_provenance_changes_hash():
    assert evidence().evidence_hash != evidence(
        asset_identity_provenance=(
            ("source_symbol", "SOL"),
            ("canonical_asset_id", "different"),
        )
    ).evidence_hash


def test_ai_item_is_sealed_and_changes_evidence_hash():
    ai_item = {
        "decision": "reject",
        "risk_level": "low",
        "direction": "LONG",
        "confidence": 95,
        "rank": 1,
    }
    row = evidence(ai_item=ai_item)
    before = row.evidence_hash
    ai_item["decision"] = "alert"
    assert row.evidence_hash == before
    assert row.ai_item["decision"] == "reject"
    assert before != evidence(
        ai_item={
            "decision": "alert",
            "risk_level": "low",
            "direction": "LONG",
            "confidence": 95,
            "rank": 1,
        }
    ).evidence_hash


def test_decision_id_is_deterministic_and_role_scoped():
    row = evidence()
    common = dict(
        candidate_id=row.candidate_id,
        engine_version=ENGINE_VERSION,
        gate_policy_fingerprint=row.gate_policy_snapshot.policy_fingerprint,
        evidence_hash=row.evidence_hash,
    )
    first = build_decision_id(
        decision_role=DecisionRole.SHADOW_ENGINE, **common
    )
    assert first == build_decision_id(
        decision_role=DecisionRole.SHADOW_ENGINE, **common
    )
    assert first != build_decision_id(
        decision_role=DecisionRole.CHALLENGER, **common
    )


def test_decision_id_structured_hash_prevents_delimiter_collision():
    left = build_decision_id(
        candidate_id="candidate",
        decision_role=DecisionRole.SHADOW_ENGINE,
        engine_version="E|GPF:1",
        gate_policy_fingerprint="EVH:1",
        evidence_hash="X",
    )
    right = build_decision_id(
        candidate_id="candidate",
        decision_role=DecisionRole.SHADOW_ENGINE,
        engine_version="E",
        gate_policy_fingerprint="GPF:1|EVH:1",
        evidence_hash="X",
    )
    assert left != right


def test_v2_preserves_v1_result_and_gate_order():
    row = evidence()
    candidate = snapshot()
    candidate.execution_validation = execution()
    v1 = OPipDecisionEngine(
        account_equity=10_000.0, decision_at=NOW
    ).evaluate(
        CandidateEvidence(
            snapshot=candidate,
            episode_id=row.episode_id,
            pair=row.pair,
        )
    )
    v2 = from_v1_decision(
        v1,
        evidence=row,
        decision_role=DecisionRole.SHADOW_ENGINE,
    )
    indexes = [GATE_INDEX[result.gate] for result in v2.gate_results_ordered]
    assert indexes == sorted(indexes)
    assert v2.decision == v1.decision
    assert v2.first_terminal_gate == v1.first_terminal_gate


def test_live_and_replay_match_when_candidate_reaches_ai_stage():
    candidate = snapshot()
    candidate.execution_validation = execution(
        estimated_visible_round_trip_market_drag_pct=0.1
    )
    ai_stage = AIStageEvidence(
        invocation_status=AI_SUCCEEDED,
        eligible_candidates_before_ai=1,
        candidates_returned_by_ai=1,
        confidences=[95],
    )
    ai_item = {
        "decision": "reject",
        "risk_level": "low",
        "direction": "LONG",
        "confidence": 95,
        "rank": 1,
    }
    row = evidence(
        candidate_snapshot=candidate,
        ai_evidence=ai_stage,
        ai_item=ai_item,
    )
    live = OPipDecisionEngine(
        account_equity=row.account_equity,
        decision_at=NOW,
        ai_stage=ai_stage,
    ).evaluate(
        CandidateEvidence(
            snapshot=candidate,
            episode_id=row.episode_id,
            signal_id=row.signal_id,
            asset_display_name=row.asset_display_name,
            pair=row.pair,
            ai_item=ai_item,
            market_intelligence=row.market_intelligence,
        )
    )
    replayed = replay_decision(row)

    assert replayed.decision == live.decision
    assert replayed.first_terminal_gate == live.first_terminal_gate
    assert replayed.terminal_reason_code == live.terminal_reason_code.value
    assert [item.as_dict() for item in replayed.gate_results_ordered] == [
        item.as_dict() for item in live.gate_results
    ]


def test_replay_is_byte_stable():
    first = replay_decision(evidence())
    second = replay_decision(evidence())
    assert first.canonical_json == second.canonical_json
    assert first.decision_id == second.decision_id


def test_replay_refuses_wrong_engine_version():
    with pytest.raises(EngineVersionMismatchError):
        replay_decision(evidence(), engine_version="OLD")


def test_evidence_captures_current_application_code_fingerprint():
    row = evidence()
    assert row.engine_code_fingerprint == app_code_fingerprint()
    assert row.engine_code_fingerprint.startswith("ACF:")
    assert len(row.engine_code_fingerprint) == 68


def test_replay_refuses_code_fingerprint_mismatch(monkeypatch):
    monkeypatch.setattr(
        "app.opip.decision.replay.app_code_fingerprint",
        lambda: "ACF:" + ("0" * 64),
    )
    with pytest.raises(EngineCodeFingerprintMismatchError):
        replay_decision(evidence())


def test_replay_refuses_policy_mismatch(monkeypatch):
    monkeypatch.setattr(
        "app.opip.decision.replay.gate_policy_fingerprint",
        lambda: "GPF:different",
    )
    with pytest.raises(PolicyVersionMismatchError):
        replay_decision(evidence())


def test_replay_does_not_resolve_current_asset_identity(monkeypatch):
    import app.opip.events.identity as identity

    monkeypatch.setattr(
        identity,
        "resolve_structured_identity",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("current alias registry must not be read")
        ),
    )
    assert replay_decision(evidence()).canonical_asset_id == "solana"


def test_replay_does_not_read_event_store(monkeypatch):
    import app.opip.events.storage as storage

    monkeypatch.setattr(
        storage.EventStore,
        "read_visible_window",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("current EventStore must not be read")
        ),
    )
    replay_decision(evidence())


def test_v2_rejects_duplicate_or_nonprefix_gate_sequences():
    row = evidence()
    decision = replay_decision(row)
    first = decision.gate_results_ordered[0]
    with pytest.raises(ValueError):
        replace(decision, gate_results_ordered=(first, first))
    if len(decision.gate_results_ordered) >= 3:
        with pytest.raises(ValueError):
            replace(
                decision,
                gate_results_ordered=(
                    decision.gate_results_ordered[0],
                    decision.gate_results_ordered[2],
                ),
            )


def test_replay_keeps_engine_non_authoritative():
    replay_decision(evidence())
    assert OPipDecisionEngine.AUTHORITATIVE is False
    assert OPipDecisionEngine.CAN_PLACE_ORDERS is False


def test_v2_keeps_ml_versions_unset():
    decision = replay_decision(evidence())
    assert decision.feature_schema_version is None
    assert decision.model_version is None


def test_v2_schema_and_role_are_explicit():
    payload = json.loads(replay_decision(evidence()).canonical_json)
    assert payload["schema_version"] == 2
    assert payload["decision_role"] == "SHADOW_ENGINE"
    assert payload["decision_id"].startswith("DEC:")
    assert payload["evidence_hash"].startswith("EVH:")
