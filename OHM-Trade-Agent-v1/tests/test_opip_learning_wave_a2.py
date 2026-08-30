"""Sequence 5 Wave A2 governance/evaluation/diagnostic tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.learning.diagnostics import build_zero_trade_diagnostic
from app.opip.learning.evaluation import (
    EvaluationSupport,
    PairedEvaluationSample,
    evaluate_champion_challenger,
)
from app.opip.learning.governance import (
    LearningStage,
    create_learning_observation,
    transition_learning,
)


NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def test_learning_lifecycle_requires_explicit_human_acceptance():
    """Verify learning lifecycle requires explicit human acceptance."""
    row = create_learning_observation(
        hypothesis_key="reduce-false-positive",
        created_at_utc=NOW,
        evidence_ids=("EV:1",),
        metrics={"baseline_fp": 0.4},
    )
    assert row.stage is LearningStage.OBSERVATION
    row = transition_learning(
        row,
        target=LearningStage.HYPOTHESIS,
        updated_at_utc=NOW + timedelta(minutes=1),
    )
    row = transition_learning(
        row,
        target=LearningStage.SHADOW_TEST,
        updated_at_utc=NOW + timedelta(minutes=2),
        evidence_ids=("EV:2",),
        metrics={"challenger_fp": 0.2},
    )
    with pytest.raises(ValueError, match="explicit human approval"):
        transition_learning(
            row,
            target=LearningStage.ACCEPTED,
            updated_at_utc=NOW + timedelta(minutes=3),
        )

    accepted = transition_learning(
        row,
        target=LearningStage.ACCEPTED,
        updated_at_utc=NOW + timedelta(minutes=3),
        approving_principal="authorized-human",
        approved_at_utc=NOW + timedelta(minutes=3),
        effective_ref="POLICY:future-review",
        rollback_ref="POLICY:current",
    )
    assert accepted.stage is LearningStage.ACCEPTED
    assert accepted.automatic_activation is False
    assert accepted.automatic_promotion is False
    assert accepted.trade_authority_changed is False
    assert accepted.evidence_ids == ("EV:1", "EV:2")


def test_learning_lifecycle_rejects_backward_time():
    """Verify learning lifecycle rejects backward time."""
    row = create_learning_observation(
        hypothesis_key="time-order",
        created_at_utc=NOW,
        evidence_ids=("EV:1",),
    )
    row = transition_learning(
        row,
        target=LearningStage.HYPOTHESIS,
        updated_at_utc=NOW + timedelta(minutes=2),
    )
    with pytest.raises(ValueError, match="time cannot move backward"):
        transition_learning(
            row,
            target=LearningStage.SHADOW_TEST,
            updated_at_utc=NOW + timedelta(minutes=1),
        )


def test_learning_lifecycle_rejects_invalid_transition():
    """Verify learning lifecycle rejects invalid transition."""
    row = create_learning_observation(
        hypothesis_key="x",
        created_at_utc=NOW,
        evidence_ids=("EV:1",),
    )
    with pytest.raises(ValueError, match="invalid learning transition"):
        transition_learning(
            row,
            target=LearningStage.ACCEPTED,
            updated_at_utc=NOW + timedelta(minutes=1),
            approving_principal="human",
            approved_at_utc=NOW + timedelta(minutes=1),
        )


def _samples(n=40):
    """Return samples."""
    rows = []
    for index in range(n):
        positive = index % 2 == 0
        rows.append(
            PairedEvaluationSample(
                sample_id=f"S:{index}",
                cohort="QUALIFIED_PAPER",
                champion_admitted=index % 3 != 0,
                challenger_admitted=(positive or index % 5 == 0),
                realized_net_return=2.0 if positive else -1.0,
                mfe=3.0 if positive else 0.5,
                mae=-0.5 if positive else -2.0,
            )
        )
    return rows


def test_paired_evaluation_is_measurement_only_and_support_gated():
    """Verify paired evaluation is measurement only and support gated."""
    result = evaluate_champion_challenger(_samples(), minimum_support=20)
    assert result.support is EvaluationSupport.SUFFICIENT
    assert result.cohort == "QUALIFIED_PAPER"
    assert result.paired_samples == 40
    assert result.can_promote is False
    assert result.automatic_promotion is False
    assert result.trade_authority_changed is False
    assert result.challenger.precision.mean is not None
    assert result.champion.net_expectancy.mean is not None


def test_paired_evaluation_marks_small_sample_insufficient():
    """Verify paired evaluation marks small sample insufficient."""
    result = evaluate_champion_challenger(_samples(5), minimum_support=20)
    assert result.support is EvaluationSupport.INSUFFICIENT
    assert result.champion.net_expectancy.support is EvaluationSupport.INSUFFICIENT
    assert result.champion.net_expectancy.ci_low is None


def test_paired_evaluation_rejects_mixed_cohorts():
    """Verify paired evaluation rejects mixed cohorts."""
    rows = _samples(4)
    rows[1] = PairedEvaluationSample(
        sample_id=rows[1].sample_id,
        cohort="COUNTERFACTUAL_REJECTED",
        champion_admitted=rows[1].champion_admitted,
        challenger_admitted=rows[1].challenger_admitted,
        realized_net_return=rows[1].realized_net_return,
    )
    with pytest.raises(ValueError, match="mixed cohorts"):
        evaluate_champion_challenger(rows, minimum_support=2)


def test_paired_evaluation_rejects_duplicate_t0_sample_identity():
    """Verify paired evaluation rejects duplicate t0 sample identity."""
    rows = _samples(2)
    rows[1] = PairedEvaluationSample(
        sample_id=rows[0].sample_id,
        cohort=rows[1].cohort,
        champion_admitted=rows[1].champion_admitted,
        challenger_admitted=rows[1].challenger_admitted,
        realized_net_return=rows[1].realized_net_return,
    )
    with pytest.raises(ValueError, match="sample ids must be unique"):
        evaluate_champion_challenger(rows, minimum_support=2)


def test_zero_trade_diagnostic_uses_structured_gate_and_health_evidence():
    """Verify zero trade diagnostic uses structured gate and health evidence."""
    diagnostic = build_zero_trade_diagnostic(
        [
            {
                "candidate_id": "C:1",
                "pair": "BTCUSD",
                "candidate_rank": 1,
                "decision_status": "REJECTED",
                "first_terminal_gate": "EVENT_RISK",
                "terminal_reason_code": "EVENT_HIGH_RISK",
                "gate_results_ordered": [
                    {
                        "gate": "EVENT_RISK",
                        "status": "FAILED",
                        "threshold_distance": -0.05,
                    }
                ],
            },
            {
                "candidate_id": "C:2",
                "pair": "ETHUSD",
                "candidate_rank": 2,
                "decision_status": "REJECTED",
                "gate_results_ordered": [
                    {
                        "gate": "ECONOMIC",
                        "status": "FAILED",
                        "threshold_distance": -0.01,
                    }
                ],
            },
        ],
        provider_health={
            "binance": {"status": "HEALTHY"},
            "news": {"status": "STALE"},
        },
        linkage_health={"readiness_state": "COLLECT_MORE_DATA"},
    )
    assert diagnostic.candidate_count == 2
    assert diagnostic.qualified_count == 0
    assert diagnostic.rejected_count == 2
    assert diagnostic.unscored_count == 0
    assert diagnostic.binding_gate == "EVENT_RISK"
    assert diagnostic.event_or_risk_restriction == "EVENT_RESTRICTION"
    assert diagnostic.nearest_miss_candidate_id == "C:2"
    assert diagnostic.nearest_miss_gate == "ECONOMIC"
    assert diagnostic.degraded_providers == ("news:STALE",)
    assert "LINKAGE_READINESS_COLLECT_MORE_DATA" in diagnostic.operational_issues
