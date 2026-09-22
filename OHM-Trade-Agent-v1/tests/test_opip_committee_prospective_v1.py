"""Prospective shadow experiment and anti-hindsight guarantees (increment 2C).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests are adversarial by design. Each one attempts a specific way that
future knowledge could contaminate a sealed historical opinion, and asserts the
protocol refuses it. A future outcome must never be able to alter, reinterpret,
or retroactively flatter what the committee said at T0.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCaseOutcome,
    EvaluationPhase,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFailureClass,
    ProviderFamily,
    ReproducibilityClass,
    StructuredOpinion,
    DirectionalAssessment,
    EvidenceSufficiency,
    ResearchAction,
    CostCompleteness,
)
from app.opip.committee.evaluation import DirectionalCall
from app.opip.committee.evidence import build_evidence_item, build_evidence_snapshot
from app.opip.committee.prospective import (
    HindsightLeakageError,
    OutcomeFinality,
    OutcomeObservation,
    ProspectivePolicyError,
    SealedPrediction,
    assert_outcome_is_prospective,
    awaiting_evaluation,
    awaiting_outcome,
    evaluate_prospective,
    prospective_observability_counts,
    seal_prediction,
    verify_seal,
)
from app.opip.committee.serialization import (
    CommitteeSerializationError,
    outcome_observation_from_dict,
    outcome_observation_to_dict,
    prospective_evaluation_from_dict,
    prospective_evaluation_to_dict,
    prospective_record_from_dict,
    sealed_prediction_from_dict,
    sealed_prediction_to_dict,
)
from app.opip.committee.store import (
    REASON_DUPLICATE,
    REASON_STORED,
    CommitteeEvidenceStore,
)
from app.opip.decision_intelligence.identity import Provenance

CUTOFF = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
SEALED_AT = CUTOFF + timedelta(seconds=30)
HORIZON = 4 * 3600
OBSERVED_AT = CUTOFF + timedelta(seconds=HORIZON)
EVALUATED_AT = OBSERVED_AT + timedelta(minutes=5)
CASE_ID = "case-1"
EXPERIMENT_ID = "prospective-exp-1"


def _snapshot(*, cutoff=CUTOFF):
    """The authenticated T0 evidence snapshot the committee actually ran on."""
    return build_evidence_snapshot(
        case_id=CASE_ID,
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=cutoff,
        assembled_at=cutoff + timedelta(seconds=10),
        items=(
            build_evidence_item(
                evidence_id="E1",
                source_id="market-observation",
                available_at=cutoff - timedelta(minutes=10),
                payload={"metric_name": "close", "metric_value": "100"},
                evidence_cutoff_at=cutoff,
            ),
        ),
        source_refs=("snapshot-ref-1",),
        committee_policy_version="committee-policy-v1",
        prompt_template_id="committee.opinion.v1",
        prompt_version="3",
    )


def _provenance() -> Provenance:
    return Provenance(
        producing_component="tests.committee.prospective",
        artifact_or_build_id="test-build",
        process_instance_id="test-process",
        emitted_at=CUTOFF,
        source_record_refs=("src-1",),
    )


def _opinion(
    case_id: str,
    *,
    assessment: str,
    provider: str = "openai",
    model: str = "model-a",
    hypothesis: str = "sealed research hypothesis",
) -> StructuredOpinion:
    return StructuredOpinion(
        case_id=case_id,
        provider=provider,
        model=model,
        evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
        assessment=DirectionalAssessment(assessment),
        hypothesis=hypothesis,
        confidence=60,
        recommended_research_action=ResearchAction.NO_ACTION,
    )


def _seat(
    *,
    assessment: str = "SUPPORTIVE",
    family: ProviderFamily = ProviderFamily.OPENAI,
    model: str = "model-a",
    status: ObservationStatus = ObservationStatus.COMPLETED,
    hypothesis: str = "sealed research hypothesis",
) -> ProviderCallOutcome:
    opinion = (
        _opinion(
            CASE_ID,
            assessment=assessment,
            provider=family.value,
            model=model,
            hypothesis=hypothesis,
        )
        if status is ObservationStatus.COMPLETED
        else None
    )
    return ProviderCallOutcome(
        logical_observation_id=f"COMMITTEE-LOGICAL:{CASE_ID}:{family.value}:{model}",
        case_id=CASE_ID,
        provider_family=family,
        requested_model=model,
        status=status,
        attempt=1,
        reproducibility=ReproducibilityClass.NONDETERMINISTIC_PROVIDER_OUTPUT,
        request_at=CUTOFF - timedelta(minutes=5),
        input_hash=f"COMMITTEE-WIRE:{family.value}",
        reported_provider=family.value if opinion is not None else None,
        reported_model=model if opinion is not None else None,
        failure_class=(
            ProviderFailureClass.TIMEOUT
            if status is ObservationStatus.FAILED
            else None
        ),
        opinion=opinion,
        response_at=CUTOFF + timedelta(seconds=5) if opinion is not None else None,
        latency_micros=500_000 if opinion is not None else None,
        input_tokens=100 if opinion is not None else None,
        output_tokens=40 if opinion is not None else None,
        estimated_cost_microunits=1_000 if opinion is not None else None,
        cost_completeness=(
            CostCompleteness.COMPLETE if opinion is not None else CostCompleteness.UNKNOWN
        ),
    )


def _case_outcome(*seats: ProviderCallOutcome, completed_at=None) -> CommitteeCaseOutcome:
    return CommitteeCaseOutcome(
        case_id=CASE_ID,
        evidence_snapshot_hash=_snapshot().snapshot_hash,
        committee_policy_version="committee-policy-v1",
        phase=EvaluationPhase.PROSPECTIVE,
        started_at=CUTOFF - timedelta(minutes=5),
        completed_at=completed_at or SEALED_AT,
        outcomes=seats or (_seat(),),
        provenance=_provenance(),
    )


def _prediction(case_outcome=None, **overrides) -> SealedPrediction:
    case_outcome = case_outcome or _case_outcome()
    values = {
        "case_outcome": case_outcome,
        "evidence_snapshot": _snapshot(),
        "sealed_at": SEALED_AT,
        "horizon_seconds": HORIZON,
        "experiment_id": EXPERIMENT_ID,
        "provenance": _provenance(),
        "case_type": CaseType.MARKET_OPPORTUNITY,
    }
    values.update(overrides)
    return seal_prediction(**values)


def _observation(**overrides) -> OutcomeObservation:
    values = {
        "case_id": CASE_ID,
        "outcome_source": "kraken_public_ohlc",
        "source_refs": ("phase3c_forward_outcomes:row-1",),
        "observed_at": OBSERVED_AT,
        "horizon_seconds": HORIZON,
        "finality": OutcomeFinality.FINAL,
        "positive": True,
        "realised_return_microunits": 12_500,
    }
    values.update(overrides)
    return OutcomeObservation(**values)


# ------------------------------------------------------- sealing and drift


def test_sealing_records_the_t0_opinion_hashes_and_cutoff():
    case_outcome = _case_outcome()
    prediction = _prediction(case_outcome)
    assert prediction.sealed_seat_count == 1
    assert prediction.sealed_opinion_hashes == (
        case_outcome.outcomes[0].opinion.opinion_hash,
    )
    assert prediction.evidence_cutoff_at == CUTOFF
    assert prediction.sealed_at == SEALED_AT
    assert prediction.phase is EvaluationPhase.PROSPECTIVE
    assert prediction.prediction_id.startswith("COMMITTEE-SEAL:")


def test_seal_verification_detects_a_rewritten_t0_opinion():
    case_outcome = _case_outcome()
    prediction = _prediction(case_outcome)
    verify_seal(prediction, case_outcome)

    # Simulate someone rewriting the sealed opinion after the fact.
    rewritten_opinion = _opinion(CASE_ID, assessment="OPPOSING", hypothesis="rewritten")
    rewritten_seat = _seat(assessment="OPPOSING", hypothesis="rewritten")
    assert rewritten_seat.opinion.opinion_hash != prediction.sealed_opinion_hashes[0]
    del rewritten_opinion

    rewritten = _case_outcome(rewritten_seat)
    with pytest.raises(ProspectivePolicyError):
        verify_seal(prediction, rewritten)


def test_seal_verification_detects_a_different_evidence_snapshot():
    prediction = _prediction()
    other = CommitteeCaseOutcome(
        case_id=CASE_ID,
        evidence_snapshot_hash="COMMITTEE-EVIDENCE:different",
        committee_policy_version="committee-policy-v1",
        phase=EvaluationPhase.PROSPECTIVE,
        started_at=CUTOFF - timedelta(minutes=5),
        completed_at=SEALED_AT,
        outcomes=(_seat(),),
        provenance=_provenance(),
    )
    with pytest.raises(ProspectivePolicyError):
        verify_seal(prediction, other)


def test_a_prediction_cannot_be_sealed_before_its_run_completed():
    case_outcome = _case_outcome(completed_at=SEALED_AT)
    with pytest.raises(ProspectivePolicyError):
        _prediction(case_outcome, sealed_at=CUTOFF)


def test_a_retrospective_prediction_cannot_be_labelled_prospective():
    provenance = _provenance()
    with pytest.raises(ProspectivePolicyError):
        SealedPrediction(
            case_id=CASE_ID,
            case_type=CaseType.MARKET_OPPORTUNITY,
            experiment_id=EXPERIMENT_ID,
            evidence_cutoff_at=CUTOFF,
            sealed_at=SEALED_AT,
            horizon_seconds=HORIZON,
            case_outcome_id="COMMITTEE-OUTCOME:x",
            evidence_snapshot_hash="COMMITTEE-EVIDENCE:abc",
            committee_policy_version="committee-policy-v1",
            sealed_opinion_hashes=("COMMITTEE-OPINION:x",),
            sealed_seat_count=1,
            provenance=provenance,
            phase=EvaluationPhase.RETROSPECTIVE,
        )


# --------------------------------------------------------- leakage defence


def test_outcome_observed_before_the_cutoff_is_refused_as_hindsight():
    prediction = _prediction()
    leaked = _observation(observed_at=CUTOFF - timedelta(hours=1))
    with pytest.raises(HindsightLeakageError):
        assert_outcome_is_prospective(prediction, leaked)


def test_outcome_window_overlapping_the_cutoff_is_refused():
    """An outcome whose measured window opened before the cutoff is leakage."""
    prediction = _prediction()
    # Observed at the horizon boundary but with a longer horizon, so the window
    # reaches back past the cutoff.
    overlapping = _observation(
        observed_at=CUTOFF + timedelta(hours=6), horizon_seconds=12 * 3600
    )
    with pytest.raises(HindsightLeakageError):
        assert_outcome_is_prospective(prediction, overlapping)


def test_outcome_for_another_case_is_refused():
    prediction = _prediction()
    foreign = _observation(case_id="some-other-case")
    with pytest.raises(ProspectivePolicyError):
        assert_outcome_is_prospective(prediction, foreign)


def test_outcome_observed_before_sealing_is_refused():
    prediction = _prediction()
    early = _observation(
        observed_at=SEALED_AT - timedelta(minutes=1), horizon_seconds=1
    )
    with pytest.raises(HindsightLeakageError):
        assert_outcome_is_prospective(prediction, early)


def test_evaluation_refuses_a_leaked_outcome_end_to_end():
    prediction = _prediction()
    case_outcome = _case_outcome()
    leaked = _observation(observed_at=CUTOFF - timedelta(minutes=30))
    provenance = _provenance()
    with pytest.raises(HindsightLeakageError):
        evaluate_prospective(
            prediction=prediction,
            case_outcome=case_outcome,
            observation=leaked,
            evaluated_at=EVALUATED_AT,
            provenance=provenance,
        )


def test_a_future_outcome_cannot_alter_the_sealed_t0_opinion():
    """The central guarantee: T1 evidence never changes T0 evidence."""
    case_outcome = _case_outcome()
    prediction = _prediction(case_outcome)
    before = prediction.prediction_id
    before_sealed = prediction.sealed_opinion_hashes

    # A later, adversarial outcome that contradicts the sealed opinion.
    evaluate_prospective(
        prediction=prediction,
        case_outcome=case_outcome,
        observation=_observation(positive=False),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )

    assert prediction.prediction_id == before
    assert prediction.sealed_opinion_hashes == before_sealed
    assert case_outcome.outcomes[0].opinion.assessment is (
        DirectionalAssessment.SUPPORTIVE
    )
    verify_seal(prediction, case_outcome)


def test_outcome_evidence_cannot_be_recomputed_only_referenced():
    with pytest.raises(ProspectivePolicyError):
        OutcomeObservation(
            case_id=CASE_ID,
            outcome_source="kraken_public_ohlc",
            source_refs=(),
            observed_at=OBSERVED_AT,
            horizon_seconds=HORIZON,
            finality=OutcomeFinality.FINAL,
            positive=True,
        )


# ------------------------------------------------- finality and labelling


def test_provisional_outcome_is_never_presented_as_final():
    evaluation = evaluate_prospective(
        prediction=_prediction(),
        case_outcome=_case_outcome(),
        observation=_observation(
            finality=OutcomeFinality.PROVISIONAL,
            incomplete_reason="outcome window not yet complete",
        ),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    assert evaluation.finality is OutcomeFinality.PROVISIONAL
    assert evaluation.counts_as_final_evidence is False
    assert all(score.final is False for score in evaluation.seat_scores)


def test_provisional_outcome_must_state_why_it_is_not_final():
    with pytest.raises(ProspectivePolicyError):
        _observation(
            finality=OutcomeFinality.PROVISIONAL, incomplete_reason=None
        )


def test_final_outcome_cannot_carry_an_incompleteness_reason():
    with pytest.raises(ProspectivePolicyError):
        _observation(
            finality=OutcomeFinality.FINAL,
            incomplete_reason="not finished",
        )


def test_final_evaluation_is_labelled_final_evidence():
    evaluation = evaluate_prospective(
        prediction=_prediction(),
        case_outcome=_case_outcome(),
        observation=_observation(),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    assert evaluation.finality is OutcomeFinality.FINAL
    assert evaluation.counts_as_final_evidence is True
    assert evaluation.phase is EvaluationPhase.PROSPECTIVE
    assert evaluation.measurement_only is True
    assert evaluation.automatic_promotion is False
    assert evaluation.trade_authority_changed is False


def test_prospective_evaluation_cannot_carry_a_retrospective_phase():
    evaluation = evaluate_prospective(
        prediction=_prediction(),
        case_outcome=_case_outcome(),
        observation=_observation(),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    retrospective_fields = {
        field: getattr(evaluation, field)
        for field in evaluation.__dataclass_fields__
    }
    retrospective_fields["phase"] = EvaluationPhase.RETROSPECTIVE
    evaluation_type = type(evaluation)
    with pytest.raises(ProspectivePolicyError):
        evaluation_type(**retrospective_fields)


def test_evaluation_time_cannot_precede_the_outcome():
    prediction = _prediction()
    case_outcome = _case_outcome()
    observation = _observation()
    too_early = OBSERVED_AT - timedelta(minutes=1)
    provenance = _provenance()
    with pytest.raises(ProspectivePolicyError):
        evaluate_prospective(
            prediction=prediction,
            case_outcome=case_outcome,
            observation=observation,
            evaluated_at=too_early,
            provenance=provenance,
        )


# ------------------------------------------------------------ seat scoring


def test_seats_are_scored_against_the_outcome():
    case_outcome = _case_outcome(
        _seat(assessment="SUPPORTIVE"),
        _seat(
            assessment="OPPOSING",
            family=ProviderFamily.ANTHROPIC,
            model="model-b",
        ),
    )
    evaluation = evaluate_prospective(
        prediction=_prediction(case_outcome),
        case_outcome=case_outcome,
        observation=_observation(positive=True),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    correct = {
        score.model: score.correct for score in evaluation.seat_scores
    }
    assert correct == {"model-a": True, "model-b": False}
    assert evaluation.scored_seats == 2
    assert evaluation.confusion is not None
    assert evaluation.confusion.true_positive == 1
    assert evaluation.confusion.false_negative == 1


def test_failed_and_abstaining_seats_are_not_scored():
    case_outcome = _case_outcome(
        _seat(assessment="SUPPORTIVE"),
        _seat(
            family=ProviderFamily.ANTHROPIC,
            model="model-b",
            status=ObservationStatus.FAILED,
        ),
    )
    evaluation = evaluate_prospective(
        prediction=_prediction(case_outcome),
        case_outcome=case_outcome,
        observation=_observation(positive=True),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    assert evaluation.scored_seats == 1
    assert evaluation.unavailable_seats == 1
    assert evaluation.abstained_seats == 0
    unavailable = [
        score
        for score in evaluation.seat_scores
        if score.call is DirectionalCall.UNAVAILABLE
    ]
    assert len(unavailable) == 1
    assert unavailable[0].correct is None


def test_non_directional_outcome_yields_not_applicable_metrics_not_zero():
    evaluation = evaluate_prospective(
        prediction=_prediction(),
        case_outcome=_case_outcome(),
        observation=_observation(positive=None),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    assert evaluation.scored_seats == 0
    assert evaluation.confusion is None
    assert evaluation.accuracy.applicable is False
    assert evaluation.accuracy.value is None


# -------------------------------------------------------------- lifecycle


def test_awaiting_outcome_tracks_unjoined_seals():
    prediction = _prediction()
    # A seal with no recorded evaluation is unresolved.
    assert awaiting_outcome(predictions=(prediction,)) == (prediction,)

    evaluation = evaluate_prospective(
        prediction=prediction,
        case_outcome=_case_outcome(),
        observation=_observation(),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    assert awaiting_outcome(
        predictions=(prediction,),
        evaluations=(evaluation,),
    ) == ()


def test_an_observed_but_unevaluated_prediction_stays_visible():
    """A failed T2 must not silently drop the prediction from the pending set."""
    prediction = _prediction()
    observation = _observation()

    # The outcome was observed, but evaluation never completed or was not
    # persisted. The prediction must remain visible as pending evaluation rather
    # than disappearing from operational counters.
    assert awaiting_outcome(predictions=(prediction,)) == (prediction,)

    counts = prospective_observability_counts(
        predictions=(prediction,),
        observations=(observation,),
        evaluations=(),
    )
    assert counts["awaiting_outcome"] == 1
    assert counts["awaiting_evaluation"] == 1
    assert counts["unresolved_predictions"] == 1


def test_observability_counts_distinguish_final_from_provisional():
    prediction = _prediction()
    provisional_evaluation = evaluate_prospective(
        prediction=prediction,
        case_outcome=_case_outcome(),
        observation=_observation(
            finality=OutcomeFinality.PROVISIONAL,
            incomplete_reason="window open",
        ),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    counts = prospective_observability_counts(
        predictions=(prediction,),
        observations=(
            _observation(
                finality=OutcomeFinality.PROVISIONAL, incomplete_reason="window open"
            ),
        ),
        evaluations=(provisional_evaluation,),
    )
    assert counts["sealed_predictions"] == 1
    assert counts["provisional_outcomes"] == 1
    assert counts["provisional_evaluations"] == 1
    assert counts["final_evaluations"] == 0
    # This prediction has already been evaluated, so nothing is outstanding.
    assert counts["awaiting_outcome"] == 0


def test_awaiting_outcome_ignores_an_already_evaluated_prediction():
    prediction = _prediction()
    observation = _observation()
    evaluation = evaluate_prospective(
        prediction=prediction,
        case_outcome=_case_outcome(),
        observation=observation,
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    assert awaiting_outcome(
        predictions=(prediction,),
        evaluations=(evaluation,),
    ) == ()
    assert awaiting_evaluation(
        predictions=(prediction,),
        observations=(observation,),
        evaluations=(evaluation,),
    ) == ()


def test_non_directional_outcome_seats_are_counted_explicitly():
    evaluation = evaluate_prospective(
        prediction=_prediction(),
        case_outcome=_case_outcome(),
        observation=_observation(positive=None),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    # A directional call with no directional truth is neither scored nor an
    # abstention; it is counted separately so no seat disappears.
    assert evaluation.scored_seats == 0
    assert evaluation.unscored_directional_seats == 1
    assert evaluation.abstained_seats == 0
    assert evaluation.unavailable_seats == 0


# ------------------------------------------------------------- persistence


def test_prospective_records_round_trip_and_are_immutable(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    case_outcome = _case_outcome()
    prediction = _prediction(case_outcome)
    observation = _observation()
    evaluation = evaluate_prospective(
        prediction=prediction,
        case_outcome=case_outcome,
        observation=observation,
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )

    assert store.append_sealed_prediction(prediction).reason == REASON_STORED
    assert store.append_outcome_observation(observation).reason == REASON_STORED
    assert store.append_prospective_evaluation(evaluation).reason == REASON_STORED
    # Re-appending the same logical records is acknowledged, never duplicated.
    assert store.append_sealed_prediction(prediction).reason == REASON_DUPLICATE
    assert store.append_outcome_observation(observation).reason == REASON_DUPLICATE
    assert (
        store.append_prospective_evaluation(evaluation).reason == REASON_DUPLICATE
    )

    seals = list(store.iter_sealed_predictions())
    observations = list(store.iter_outcome_observations())
    evaluations = list(store.iter_prospective_evaluations())
    assert len(seals) == 1
    assert len(observations) == 1
    assert len(evaluations) == 1
    assert seals[0].prediction_id == prediction.prediction_id
    assert seals[0].sealed_opinion_hashes == prediction.sealed_opinion_hashes
    assert observations[0].source_refs == observation.source_refs
    assert evaluations[0].evaluation_id == evaluation.evaluation_id
    assert evaluations[0].counts_as_final_evidence is True


def test_a_forged_prospective_identity_is_rejected_on_read():
    prediction = _prediction()
    row = sealed_prediction_to_dict(prediction)
    row["prediction_id"] = "COMMITTEE-SEAL:forged"
    with pytest.raises(CommitteeSerializationError):
        sealed_prediction_from_dict(row)

    observation = _observation()
    row = outcome_observation_to_dict(observation)
    row["observation_id"] = "COMMITTEE-OUTCOME-OBS:forged"
    with pytest.raises(CommitteeSerializationError):
        outcome_observation_from_dict(row)

    evaluation = evaluate_prospective(
        prediction=prediction,
        case_outcome=_case_outcome(),
        observation=observation,
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    row = prospective_evaluation_to_dict(evaluation)
    row["evaluation_id"] = "COMMITTEE-PROSPECTIVE-EVAL:forged"
    with pytest.raises(CommitteeSerializationError):
        prospective_evaluation_from_dict(row)


def test_undeclared_prospective_record_kind_is_rejected():
    with pytest.raises(CommitteeSerializationError):
        prospective_record_from_dict({"kind": "SOMETHING_ELSE"})


def test_outcome_eligibility_is_derived_from_the_cutoff_and_horizon():
    prediction = _prediction()
    # The horizon is the one committed at sealing, not a caller-supplied value.
    assert prediction.horizon_seconds == HORIZON
    assert prediction.outcome_eligible_at() == CUTOFF + timedelta(seconds=HORIZON)


# ------------------------------------------- review findings (fail-closed)


def test_a_retrospective_run_cannot_be_sealed_as_a_prospective_prediction():
    """Sealing must refuse a retrospective committee run outright."""
    retrospective = CommitteeCaseOutcome(
        case_id=CASE_ID,
        evidence_snapshot_hash="COMMITTEE-EVIDENCE:abc123",
        committee_policy_version="committee-policy-v1",
        phase=EvaluationPhase.RETROSPECTIVE,
        started_at=CUTOFF - timedelta(minutes=5),
        completed_at=SEALED_AT,
        outcomes=(_seat(),),
        provenance=_provenance(),
    )
    retrospective_seal = _provenance()
    snapshot = _snapshot()
    with pytest.raises(ProspectivePolicyError):
        seal_prediction(
            case_outcome=retrospective,
            evidence_snapshot=snapshot,
            sealed_at=SEALED_AT,
            horizon_seconds=HORIZON,
            experiment_id=EXPERIMENT_ID,
            provenance=retrospective_seal,
            case_type=CaseType.MARKET_OPPORTUNITY,
        )


def test_outcome_identity_covers_every_field_that_changes_its_meaning():
    """Two materially different outcomes must not share one identity."""
    base = _observation()
    differing_return = _observation(realised_return_microunits=99_999)
    provisional = _observation(
        finality=OutcomeFinality.PROVISIONAL,
        incomplete_reason="window still open",
    )
    assert differing_return.observation_id != base.observation_id
    assert provisional.observation_id != base.observation_id
    # Identical content still collapses to one identity.
    assert _observation().observation_id == base.observation_id


# ------------------------------- review findings (evidence integrity)


def test_sealing_derives_the_cutoff_from_the_authenticated_snapshot():
    """A caller cannot substitute an earlier cutoff than the snapshot used."""
    # A snapshot built on an *earlier* cutoff has a different hash, so it cannot
    # stand in for the one the committee actually ran on.
    earlier = _snapshot(cutoff=CUTOFF - timedelta(hours=6))
    case_outcome = _case_outcome()
    provenance = _provenance()
    with pytest.raises(ProspectivePolicyError):
        seal_prediction(
            case_outcome=case_outcome,
            evidence_snapshot=earlier,
            sealed_at=SEALED_AT,
            horizon_seconds=HORIZON,
            experiment_id=EXPERIMENT_ID,
            provenance=provenance,
            case_type=CaseType.MARKET_OPPORTUNITY,
        )


def test_sealing_rejects_a_snapshot_for_another_case():
    other = build_evidence_snapshot(
        case_id="some-other-case",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CUTOFF,
        assembled_at=CUTOFF + timedelta(seconds=10),
        items=(
            build_evidence_item(
                evidence_id="E1",
                source_id="market-observation",
                available_at=CUTOFF - timedelta(minutes=10),
                payload={"metric_name": "close", "metric_value": "100"},
                evidence_cutoff_at=CUTOFF,
            ),
        ),
        source_refs=("ref",),
        committee_policy_version="committee-policy-v1",
        prompt_template_id="committee.opinion.v1",
        prompt_version="3",
    )
    provenance = _provenance()
    case_outcome = _case_outcome()
    with pytest.raises(ProspectivePolicyError):
        seal_prediction(
            case_outcome=case_outcome,
            evidence_snapshot=other,
            sealed_at=SEALED_AT,
            horizon_seconds=HORIZON,
            experiment_id=EXPERIMENT_ID,
            provenance=provenance,
            case_type=CaseType.MARKET_OPPORTUNITY,
        )


def test_the_sealed_cutoff_equals_the_snapshot_cutoff():
    prediction = _prediction()
    assert prediction.evidence_cutoff_at == _snapshot().evidence_cutoff_at
    assert prediction.evidence_cutoff_at == CUTOFF


def test_evaluation_identity_covers_finality_metrics_and_counts():
    """A changed result must change the identity, not reuse the original id."""
    evaluation = evaluate_prospective(
        prediction=_prediction(),
        case_outcome=_case_outcome(),
        observation=_observation(),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    payload = evaluation.identity_payload()
    assert payload["seat_counts"] == (
        evaluation.scored_seats,
        evaluation.abstained_seats,
        evaluation.unavailable_seats,
        evaluation.unscored_directional_seats,
    )
    assert tuple(metric[0] for metric in payload["metrics"]) == (
        "precision",
        "recall",
        "f1",
        "accuracy",
    )
    # The per-seat final flag is part of the identity, not just the verdict.
    assert all(score[4] is True for score in payload["seat_scores"])

    # Rebinding finality must yield a different identity.
    provisional = replace(
        evaluation,
        finality=OutcomeFinality.PROVISIONAL,
        seat_scores=tuple(
            replace(score, final=False) for score in evaluation.seat_scores
        ),
    )
    assert provisional.evaluation_id != evaluation.evaluation_id


def test_a_lost_prospective_index_is_rebuilt_with_evaluation_ids(tmp_path):
    """Rebuilding must index evaluation ids, or a redelivery duplicates a row."""
    store = CommitteeEvidenceStore(root=tmp_path)
    prediction = _prediction()
    observation = _observation()
    evaluation = evaluate_prospective(
        prediction=prediction,
        case_outcome=_case_outcome(),
        observation=observation,
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    store.append_sealed_prediction(prediction)
    store.append_outcome_observation(observation)
    assert store.append_prospective_evaluation(evaluation).reason == REASON_STORED

    store.prospective_index_file.unlink()

    result = store.append_prospective_evaluation(evaluation)
    assert result.stored is False
    assert result.reason == REASON_DUPLICATE
    assert result.record_id == evaluation.evaluation_id
    assert len(list(store.iter_prospective_evaluations())) == 1

# ============ review findings: horizon binding and reservation integrity


def test_the_outcome_horizon_must_be_the_one_committed_at_sealing():
    """A later horizon cannot be selected once results are known."""
    prediction = _prediction()
    wrong_horizon = _observation(horizon_seconds=12 * 3600, observed_at=SEALED_AT + timedelta(hours=13))
    with pytest.raises(ProspectivePolicyError):
        assert_outcome_is_prospective(prediction, wrong_horizon)


def test_the_evaluation_records_the_sealed_horizon_not_the_observation_horizon():
    prediction = _prediction()
    evaluation = evaluate_prospective(
        prediction=prediction,
        case_outcome=_case_outcome(),
        observation=_observation(),
        evaluated_at=EVALUATED_AT,
        provenance=_provenance(),
    )
    assert evaluation.horizon_seconds == prediction.horizon_seconds == HORIZON


def test_the_sealed_horizon_participates_in_the_prediction_identity():
    from dataclasses import replace

    prediction = _prediction()
    other = replace(prediction, horizon_seconds=HORIZON * 2)
    assert other.prediction_id != prediction.prediction_id
    assert prediction.identity_payload()["horizon_seconds"] == HORIZON


def test_the_sealed_horizon_survives_a_storage_round_trip(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    prediction = _prediction()
    assert store.append_sealed_prediction(prediction).reason == REASON_STORED
    reloaded = list(store.iter_sealed_predictions())[0]
    assert reloaded.horizon_seconds == HORIZON
    assert reloaded.prediction_id == prediction.prediction_id
