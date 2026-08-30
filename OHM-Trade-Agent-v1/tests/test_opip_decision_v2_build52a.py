"""Sequence 5 BUILD 5.2A sustained equivalence tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.decision.engine import CandidateEvidence, OPipDecisionEngine
from app.opip.decision.equivalence import (
    DivergenceKind,
    PairingState,
    build_equivalence_observation,
)
from app.opip.decision.equivalence_store import (
    append_equivalence_observations,
    opip_equivalence_ledger_enabled,
    read_equivalence_ledger,
)
from app.opip.decision.models_v2 import DecisionRole, from_v1_decision
from app.opip.decision.policy_snapshot import GatePolicySnapshot, _freeze, _thaw
from app.opip.decision.promotion import (
    PromotionCriteria,
    PromotionEvaluationStatus,
    evaluate_shadow_equivalence,
)
from app.opip.decision.replay import replay_decision
from tests.test_opip_decision_v2_build51 import NOW, evidence
from tests.test_opip_decision_engine_v1 import execution, snapshot


def _pair(
    *,
    when: datetime = NOW,
    scan_id: str = "SCAN:1",
    shadow_mutator=None,
):
    candidate = snapshot()
    candidate.execution_validation = execution(
        estimated_visible_round_trip_market_drag_pct=0.1
    )
    row = evidence(
        decision_time_utc=when,
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
    return build_equivalence_observation(
        observed_at_utc=when,
        scan_id=scan_id,
        production=production,
        shadow=shadow,
    )


def test_exact_pair_records_full_equivalence():
    observation = _pair()
    assert observation.pairing_state is PairingState.COMPLETE
    assert observation.divergence_kind is DivergenceKind.EXACT
    assert observation.exact_match is True
    assert observation.outcome_match is True
    assert observation.terminal_gate_match is True
    assert observation.reason_match is True
    assert observation.gate_history_match is True
    assert observation.observation_id.startswith("EQO:")


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
    assert observation.divergence_kind is DivergenceKind.PAIRING_INVALID
    assert "ENGINE_CODE_FINGERPRINT_MISMATCH" in observation.pairing_errors


def test_gate_history_divergence_is_visible_even_when_verdict_matches():
    baseline = _pair()
    assert baseline.exact_match is True

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
        )
        for index in range(3)
    )
    result = evaluate_shadow_equivalence(
        rows,
        criteria=PromotionCriteria(
            min_comparable_observations=3,
            min_distinct_scans=3,
            min_distinct_days=3,
        ),
    )
    assert result.status is PromotionEvaluationStatus.READY_FOR_HUMAN_REVIEW
    assert result.ready_for_human_review is True
    assert result.divergences == 0
    assert result.instrumentation_coverage_pct == 100.0
    assert result.CAN_PROMOTE is False
    assert result.AUTHORITATIVE is False


def test_one_divergence_blocks_engine_equivalence():
    rows = [_pair(scan_id="SCAN:1"), _pair(scan_id="SCAN:2", when=NOW + timedelta(days=1))]
    divergent = replace(
        rows[1],
        exact_match=False,
        gate_history_match=False,
        divergence_kind=DivergenceKind.GATE_HISTORY,
    )
    result = evaluate_shadow_equivalence(
        [rows[0], divergent],
        criteria=PromotionCriteria(
            min_comparable_observations=2,
            min_distinct_scans=2,
            min_distinct_days=2,
        ),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_DIVERGENCE
    assert "EXACT_EQUIVALENCE_DIVERGENCE_PRESENT" in result.blockers


def test_incomplete_instrumentation_blocks_even_with_exact_pairs():
    exact = _pair()
    candidate = snapshot()
    candidate.execution_validation = execution()
    row = evidence(candidate_snapshot=candidate)
    missing = build_equivalence_observation(
        observed_at_utc=NOW + timedelta(minutes=1),
        scan_id="SCAN:missing",
        production=None,
        shadow=replay_decision(row),
    )
    result = evaluate_shadow_equivalence(
        [exact, missing],
        criteria=PromotionCriteria(
            min_comparable_observations=1,
            min_distinct_scans=1,
            min_distinct_days=1,
            min_instrumentation_coverage_pct=100.0,
        ),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    assert result.instrumentation_coverage_pct == 50.0


def test_incomplete_ledger_coverage_blocks_promotion():
    result = evaluate_shadow_equivalence(
        [_pair()],
        criteria=PromotionCriteria(
            min_comparable_observations=1,
            min_distinct_scans=1,
            min_distinct_days=1,
        ),
        ledger_complete=False,
        ledger_warnings=("ARCHIVE_READ_FAILED:RuntimeError",),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_INSTRUMENTATION
    assert "LEDGER_COVERAGE_INCOMPLETE" in result.blockers


def test_version_mix_blocks_aggregation():
    first = _pair(scan_id="SCAN:1")
    second = replace(
        _pair(scan_id="SCAN:2", when=NOW + timedelta(days=1)),
        engine_code_fingerprint="ACF:" + ("f" * 64),
    )
    result = evaluate_shadow_equivalence(
        [first, second],
        criteria=PromotionCriteria(
            min_comparable_observations=2,
            min_distinct_scans=2,
            min_distinct_days=2,
        ),
    )
    assert result.status is PromotionEvaluationStatus.BLOCKED_VERSION_MIX


def test_criteria_are_explicit_and_validated():
    with pytest.raises(ValueError):
        PromotionCriteria(
            min_comparable_observations=0,
            min_distinct_scans=1,
            min_distinct_days=1,
        )
    with pytest.raises(ValueError):
        PromotionCriteria(
            min_comparable_observations=1,
            min_distinct_scans=1,
            min_distinct_days=1,
            min_instrumentation_coverage_pct=float("nan"),
        )


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
    assert len(result.observations) == 1
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


def test_policy_freeze_thaw_round_trips_ambiguous_lists():
    values = (
        [],
        [["a", 1], ["b", 2]],
        {"nested": [], "pairs": [["x", 3]]},
    )
    for value in values:
        assert _thaw(_freeze(value)) == value


def test_policy_snapshot_fingerprint_unchanged_after_freeze_hardening():
    policy = GatePolicySnapshot.capture_current()
    assert policy.calculated_fingerprint() == policy.policy_fingerprint
