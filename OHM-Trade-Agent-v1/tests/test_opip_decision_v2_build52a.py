"""Sequence 5 BUILD 5.2A sustained equivalence tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from app.opip.decision.engine import CandidateEvidence, OPipDecisionEngine
from app.opip.decision.equivalence import (
    DivergenceKind,
    EquivalenceObservation,
    PairingState,
    build_equivalence_observation,
)
from app.opip.decision.equivalence_store import (
    append_equivalence_observations,
    opip_equivalence_ledger_enabled,
    read_equivalence_ledger,
)
from app.opip.decision.identity import opip_candidate_id
from app.opip.decision.models_v2 import DecisionRole, from_v1_decision
from app.opip.decision.policy_snapshot import GatePolicySnapshot, _freeze, _thaw
from app.opip.decision.promotion import (
    PromotionCriteria,
    PromotionEvaluationStatus,
    ScanCoverageExpectation,
    evaluate_shadow_equivalence,
)
from app.opip.decision.replay import replay_decision
from tests.test_opip_decision_v2_build51 import NOW, evidence
from tests.test_opip_decision_engine_v1 import execution, snapshot


def _pair(
    *,
    when=NOW,
    scan_id: str = "SCAN:1",
    candidate_key: str = "build51",
    shadow_mutator=None,
    both_mutator=None,
):
    candidate = snapshot()
    candidate.execution_validation = execution(
        estimated_visible_round_trip_market_drag_pct=0.1
    )
    episode_id = f"EP:{candidate_key}"
    canonical_candidate_id = opip_candidate_id(
        episode_id=episode_id,
        pair="SOLUSD",
        direction="LONG",
        market_type="SPOT",
    )
    row = evidence(
        decision_time_utc=when,
        episode_id=episode_id,
        candidate_id=canonical_candidate_id,
        candidate_snapshot=candidate,
    )
    live = OPipDecisionEngine(
        account_equity=row.account_equity,
        decision_at=when,
    ).evaluate(
        CandidateEvidence(
            snapshot=candidate,
            episode_id=row.episode_id,
            signal_id=row.signal_id,
            asset_display_name=row.asset_display_name,
            pair=row.pair,
        )
    )
    production = from_v1_decision(
        live,
        evidence=row,
        decision_role=DecisionRole.PRODUCTION_REFERENCE,
    )
    shadow = replay_decision(row, decision_role=DecisionRole.SHADOW_ENGINE)
    if shadow_mutator is not None:
        shadow = shadow_mutator(shadow)
    if both_mutator is not None:
        production, shadow = both_mutator(production, shadow)
    return build_equivalence_observation(
        observed_at_utc=when,
        scan_id=scan_id,
        production=production,
        shadow=shadow,
    )


def _coverage(*rows):
    by_scan = {}
    for row in rows:
        by_scan.setdefault(row.scan_id, []).append(row.candidate_id)
    return tuple(
        ScanCoverageExpectation(
            scan_id=scan_id,
            expected_at_utc=next(
                row.observed_at_utc for row in rows if row.scan_id == scan_id
            ),
            expected_candidate_ids=tuple(candidate_ids),
        )
        for scan_id, candidate_ids in sorted(by_scan.items())
    )


def _criteria(n=1, scans=1, days=1):
    return PromotionCriteria(
        min_comparable_observations=n,
        min_distinct_scans=scans,
        min_distinct_days=days,
    )


def test_exact_pair_records_full_equivalence_and_content_identity():
    observation = _pair()
    assert observation.pairing_state is PairingState.COMPLETE
    assert observation.divergence_kind is DivergenceKind.EXACT
    assert observation.exact_match is True
    assert observation.production_decision_hash.startswith("DCH:")
    assert observation.production_gate_history_hash.startswith("GHH:")
    assert observation.observation_id == observation.calculated_observation_id


def test_persisted_row_rejects_mutated_match_evidence():
    observation = _pair()
    payload = observation.as_dict()
    payload["exact_match"] = False
    with pytest.raises(ValueError):
        EquivalenceObservation.from_dict(payload)


def test_missing_side_is_instrumentation_failure_not_match():
    candidate = snapshot()
    candidate.execution_validation = execution()
    row = evidence(candidate_snapshot=candidate)
    shadow = replay_decision(row)
    observation = build_equivalence_observation(
        observed_at_utc=NOW,
        scan_id="SCAN:missing",
        production=None,
        shadow=shadow,
    )
    assert observation.pairing_state is PairingState.INCOMPLETE
    assert observation.exact_match is False
    assert observation.divergence_kind is DivergenceKind.INSTRUMENTATION_INCOMPLETE
    assert "PRODUCTION_REFERENCE_MISSING" in observation.pairing_errors


def test_runtime_identity_mismatch_is_invalid_and_cannot_compare():
    observation = _pair(
        shadow_mutator=lambda value: replace(
            value, engine_code_fingerprint="ACF:" + ("0" * 64)
        )
    )
    assert observation.pairing_state is PairingState.INVALID
    assert observation.exact_match is False
    assert "ENGINE_CODE_FINGERPRINT_MISMATCH" in observation.pairing_errors


def test_gate_history_divergence_is_visible_even_when_verdict_matches():
    def mutate(value):
        last = value.gate_results_ordered[-1]
        changed = replace(last, reason=last.reason + " changed")
        return replace(
            value,
            gate_results_ordered=(*value.gate_results_ordered[:-1], changed),
        )

    observation = _pair(shadow_mutator=mutate)
    assert observation.outcome_match is True
    assert observation.terminal_gate_match is True
    assert observation.gate_history_match is False
    assert observation.divergence_kind is DivergenceKind.GATE_HISTORY


def test_promotion_requires_sustained_exact_homogeneous_evidence():
    rows = tuple(
        _pair(
            when=NOW + timedelta(days=index, minutes=index),
            scan_id=f"SCAN:{index}",
            candidate_key=f"candidate-{index}",
        )
        for index in range(3)
    )
    result = evaluate_shadow_equivalence(
        rows,
        criteria=_criteria(3, 3, 3),
        coverage_expectations=_coverage(*rows),
    )
    assert result.status is PromotionEvaluationStatus.READY_FOR_HUMAN_REVIEW
    assert result.ready_for_human_review is True
    assert result.instrumentation_coverage_pct == 100.0
    assert result.CAN_PROMOTE is False
    assert result.AUTHORITATIVE is False


def test_no_expected_denominator_can_never_be_ready():
    row = _pair()
    result = evaluate_shadow_equivalence([row], criteria=_criteria())
    assert result.status is PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    assert "EXPECTED_COVERAGE_NOT_PROVIDED" in result.blockers


def test_entirely_omitted_candidate_is_detected_by_independent_denominator():
    kept = _pair(scan_id="SCAN:1", candidate_key="kept")
    expectation = ScanCoverageExpectation(
        scan_id="SCAN:1",
        expected_at_utc=NOW,
        expected_candidate_ids=(kept.candidate_id, "OPIPC:omitted"),
    )
    result = evaluate_shadow_equivalence(
        [kept],
        criteria=_criteria(),
        coverage_expectations=(expectation,),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    assert result.instrumentation_coverage_pct == 50.0
    assert result.missing_expected_observations == 1
    assert "EXPECTED_COMPARISON_MISSING" in result.blockers


def test_distinct_days_come_from_independent_scan_expectations():
    first = _pair(
        scan_id="SCAN:1",
        candidate_key="one",
        when=NOW,
    )
    second = _pair(
        scan_id="SCAN:2",
        candidate_key="two",
        when=NOW + timedelta(days=10),
    )
    expectations = (
        ScanCoverageExpectation(
            scan_id="SCAN:1",
            expected_at_utc=NOW,
            expected_candidate_ids=(first.candidate_id,),
        ),
        ScanCoverageExpectation(
            scan_id="SCAN:2",
            expected_at_utc=NOW,
            expected_candidate_ids=(second.candidate_id,),
        ),
    )
    result = evaluate_shadow_equivalence(
        [first, second],
        criteria=_criteria(2, 2, 2),
        coverage_expectations=expectations,
    )
    assert result.status is PromotionEvaluationStatus.INSUFFICIENT_EVIDENCE
    assert result.distinct_scans == 2
    assert result.distinct_days == 1
    assert "INSUFFICIENT_DISTINCT_DAYS" in result.blockers


def test_one_divergence_blocks_engine_equivalence():
    rows = [
        _pair(scan_id="SCAN:1", candidate_key="one"),
        _pair(
            scan_id="SCAN:2",
            candidate_key="two",
            when=NOW + timedelta(days=1),
        ),
    ]
    def mutate_gate(value):
        last = value.gate_results_ordered[-1]
        changed = replace(last, reason=last.reason + " divergent")
        return replace(
            value,
            gate_results_ordered=(*value.gate_results_ordered[:-1], changed),
        )

    divergent = _pair(
        scan_id="SCAN:2",
        candidate_key="two",
        when=NOW + timedelta(days=1),
        shadow_mutator=mutate_gate,
    )
    result = evaluate_shadow_equivalence(
        [rows[0], divergent],
        criteria=_criteria(2, 2, 2),
        coverage_expectations=_coverage(rows[0], divergent),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_DIVERGENCE
    assert "EXACT_EQUIVALENCE_DIVERGENCE_PRESENT" in result.blockers


def test_incomplete_instrumentation_blocks_even_with_exact_pairs():
    exact = _pair(scan_id="SCAN:1", candidate_key="one")
    candidate = snapshot()
    candidate.execution_validation = execution()
    episode_id = "EP:two"
    canonical_candidate_id = opip_candidate_id(
        episode_id=episode_id,
        pair="SOLUSD",
        direction="LONG",
        market_type="SPOT",
    )
    row = evidence(
        episode_id=episode_id,
        candidate_id=canonical_candidate_id,
        candidate_snapshot=candidate,
    )
    missing = build_equivalence_observation(
        observed_at_utc=NOW + timedelta(minutes=1),
        scan_id="SCAN:2",
        production=None,
        shadow=replay_decision(row),
    )
    result = evaluate_shadow_equivalence(
        [exact, missing],
        criteria=_criteria(),
        coverage_expectations=_coverage(exact, missing),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    assert result.instrumentation_coverage_pct == 50.0


def test_incomplete_ledger_coverage_blocks_promotion():
    row = _pair()
    result = evaluate_shadow_equivalence(
        [row],
        criteria=_criteria(),
        coverage_expectations=_coverage(row),
        ledger_complete=False,
        ledger_warnings=("ARCHIVE_SEGMENT_MISSING",),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    assert "LEDGER_COVERAGE_INCOMPLETE" in result.blockers


def test_version_mix_blocks_aggregation():
    first = _pair(scan_id="SCAN:1", candidate_key="one")
    def alternate_code(production, shadow):
        fingerprint = "ACF:" + ("f" * 64)
        return (
            replace(production, engine_code_fingerprint=fingerprint),
            replace(shadow, engine_code_fingerprint=fingerprint),
        )

    second = _pair(
        scan_id="SCAN:2",
        candidate_key="two",
        when=NOW + timedelta(days=1),
        both_mutator=alternate_code,
    )
    result = evaluate_shadow_equivalence(
        [first, second],
        criteria=_criteria(2, 2, 2),
        coverage_expectations=_coverage(first, second),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_VERSION_MIX


def test_duplicate_candidate_observations_block_readiness():
    first = _pair(scan_id="SCAN:1", candidate_key="one")
    def mutate_gate(value):
        last = value.gate_results_ordered[-1]
        changed = replace(last, reason=last.reason + " duplicate-key")
        return replace(
            value,
            gate_results_ordered=(*value.gate_results_ordered[:-1], changed),
        )

    second = _pair(
        scan_id="SCAN:1",
        candidate_key="one",
        shadow_mutator=mutate_gate,
    )
    result = evaluate_shadow_equivalence(
        [first, second],
        criteria=_criteria(),
        coverage_expectations=_coverage(first),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    assert "DUPLICATE_CANDIDATE_OBSERVATION_PRESENT" in result.blockers


def test_criteria_are_explicit_and_validated():
    with pytest.raises(ValueError):
        PromotionCriteria(0, 1, 1)
    with pytest.raises(ValueError):
        PromotionCriteria(1, 1, 1, float("nan"))


def test_ledger_dark_by_default_and_round_trips_when_enabled(tmp_path):
    path = tmp_path / "equivalence.jsonl"
    observation = _pair()
    assert opip_equivalence_ledger_enabled({}) is False
    assert append_equivalence_observations([observation], path=path) == 0
    assert not path.exists()

    assert append_equivalence_observations(
        [observation], path=path, enabled=True
    ) == 1
    result = read_equivalence_ledger(path=path)
    assert result.complete is True
    assert result.warnings == ()
    assert result.observations[0].as_dict() == observation.as_dict()


def test_ledger_deduplicates_idempotent_retry(tmp_path):
    path = tmp_path / "equivalence.jsonl"
    observation = _pair()
    append_equivalence_observations(
        [observation, observation], path=path, enabled=True
    )
    result = read_equivalence_ledger(path=path)
    assert result.complete is True
    assert len(result.observations) == 1


def test_manifest_declared_missing_archive_marks_ledger_incomplete(tmp_path):
    path = tmp_path / "equivalence.jsonl"
    archive_dir = tmp_path / "equivalence_archive"
    archive_dir.mkdir()
    manifest = {
        "schema_version": 1,
        "segments": {
            "a" * 64: {
                "archive": "equivalence-missing.jsonl.gz",
                "tier": "WARM",
                "sha256": "a" * 64,
                "row_count": 1,
                "bytes": 100,
            }
        },
    }
    (archive_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = read_equivalence_ledger(path=path)
    assert result.complete is False
    assert "ARCHIVE_SEGMENT_MISSING" in result.warnings


def test_policy_freeze_thaw_round_trips_ambiguous_lists():
    values = (
        [],
        {},
        [["a", 1], ["b", 2]],
        {"nested": [], "pairs": [["x", 3]], "empty": {}},
    )
    for value in values:
        assert _thaw(_freeze(value)) == value


def test_policy_snapshot_fingerprint_unchanged_after_freeze_hardening():
    policy = GatePolicySnapshot.capture_current()
    assert policy.calculated_fingerprint() == policy.policy_fingerprint
