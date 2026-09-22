"""Learning, disagreement and attribution (increment 2D).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests prove the disagreement matrix preserves dissent instead of averaging
it away, that attribution separates genuine incremental information from mere
agreement with the baseline, and that nothing in the learning plane can promote a
model or change trading behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.attribution import (
    CONFIDENCE_SPREAD_THRESHOLD,
    MIN_ATTRIBUTION_SAMPLES,
    AttributionCase,
    BaselineRelation,
    DisagreementKind,
    advisory_disposition,
    baseline_relation,
    build_attribution_report,
    build_disagreement_matrix,
)
from app.opip.committee.contracts import (
    CaseType,
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
from app.opip.committee.evaluation import DirectionalCall
from app.opip.committee.serialization import (
    attribution_report_from_dict,
    attribution_report_to_dict,
)
from app.opip.committee.store import (
    REASON_DUPLICATE,
    REASON_STORED,
    CommitteeEvidenceStore,
)
from app.opip.decision_intelligence.identity import Provenance

START = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
PROVENANCE = Provenance(
    producing_component="tests.committee.attribution",
    artifact_or_build_id="test-build",
    process_instance_id="test-process",
    emitted_at=START,
    source_record_refs=("src-1",),
)


def _seat(
    case_id: str,
    *,
    family: ProviderFamily = ProviderFamily.OPENAI,
    model: str = "model-a",
    assessment: str = "SUPPORTIVE",
    confidence: int | None = 60,
    abstention: str | None = None,
    sufficiency: str = "SUFFICIENT",
    supporting: tuple[str, ...] = (),
    contradicting: tuple[str, ...] = (),
    assumptions: tuple[str, ...] = (),
    status: ObservationStatus = ObservationStatus.COMPLETED,
    failure_class: ProviderFailureClass | None = None,
    cost: int | None = 1_000,
) -> ProviderCallOutcome:
    opinion = None
    if status in (ObservationStatus.COMPLETED, ObservationStatus.DUPLICATE_OK):
        opinion = StructuredOpinion(
            case_id=case_id,
            provider=family.value,
            model=model,
            evidence_sufficiency=EvidenceSufficiency(sufficiency),
            assessment=DirectionalAssessment(assessment),
            hypothesis="scripted hypothesis",
            confidence=confidence,
            recommended_research_action=ResearchAction.NO_ACTION,
            abstention_reason=abstention,
            supporting_evidence_refs=supporting,
            contradicting_evidence_refs=contradicting,
            major_assumptions=assumptions,
        )
    return ProviderCallOutcome(
        logical_observation_id=f"COMMITTEE-LOGICAL:{case_id}:{family.value}:{model}",
        case_id=case_id,
        provider_family=family,
        requested_model=model,
        status=status,
        attempt=1,
        reproducibility=ReproducibilityClass.NONDETERMINISTIC_PROVIDER_OUTPUT,
        request_at=START,
        input_hash=f"COMMITTEE-WIRE:{case_id}:{family.value}",
        reported_provider=family.value if opinion is not None else None,
        reported_model=model if opinion is not None else None,
        failure_class=failure_class,
        opinion=opinion,
        response_at=START if opinion is not None else None,
        latency_micros=400_000 if opinion is not None else None,
        input_tokens=100 if opinion is not None else None,
        output_tokens=40 if opinion is not None else None,
        estimated_cost_microunits=cost if opinion is not None else None,
        cost_completeness=(
            CostCompleteness.COMPLETE
            if (opinion is not None and cost is not None)
            else CostCompleteness.UNKNOWN
        ),
    )


def _case(
    case_id: str = "case-1",
    *,
    seats: tuple[ProviderCallOutcome, ...],
    baseline_positive: bool | None = None,
    observed_positive: bool | None = None,
    decided_at: datetime = START,
    case_type: CaseType = CaseType.MARKET_OPPORTUNITY,
) -> AttributionCase:
    return AttributionCase(
        case_id=case_id,
        case_type=case_type,
        decided_at=decided_at,
        seats=seats,
        baseline_positive=baseline_positive,
        observed_positive=observed_positive,
    )


def _report(cases, **overrides):
    values = {
        "experiment_id": "attribution-exp-1",
        "provenance": PROVENANCE,
        "generated_at": START + timedelta(days=7),
        "phase": EvaluationPhase.PROSPECTIVE,
    }
    values.update(overrides)
    return build_attribution_report(cases, **values)


# ------------------------------------------------------- disagreement matrix


def test_unanimous_agreement_is_recognised():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE"),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    assessment="SUPPORTIVE",
                ),
            )
        )
    )
    assert matrix.primary is DisagreementKind.UNANIMOUS_AGREEMENT
    assert matrix.supportive_seats == 2
    assert matrix.opposing_seats == 0
    assert matrix.is_contested is False


def test_single_model_dissent_is_preserved_not_averaged():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE"),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    assessment="SUPPORTIVE",
                ),
                _seat(
                    "case-1",
                    family=ProviderFamily.GOOGLE_GEMINI,
                    model="model-c",
                    assessment="OPPOSING",
                ),
            )
        )
    )
    assert matrix.primary is DisagreementKind.SINGLE_MODEL_DISSENT
    assert matrix.supportive_seats == 2
    assert matrix.opposing_seats == 1
    assert matrix.is_contested is True


def test_split_decision_is_distinguished_from_dissent():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE"),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    assessment="OPPOSING",
                ),
            )
        )
    )
    assert matrix.primary is DisagreementKind.SPLIT_DECISION
    assert DisagreementKind.SINGLE_MODEL_DISSENT not in matrix.conditions


def test_directional_agreement_with_confidence_disagreement_is_distinguished():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE", confidence=90),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    assessment="SUPPORTIVE",
                    confidence=10,
                ),
            )
        )
    )
    assert matrix.primary is (
        DisagreementKind.DIRECTIONAL_AGREEMENT_CONFIDENCE_DISAGREEMENT
    )
    assert matrix.max_confidence_spread == 80
    assert matrix.max_confidence_spread > CONFIDENCE_SPREAD_THRESHOLD


def test_evidence_disagreement_is_detected_from_contested_references():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE", supporting=("E1", "E2")),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    assessment="SUPPORTIVE",
                    contradicting=("E1",),
                ),
            )
        )
    )
    assert DisagreementKind.EVIDENCE_DISAGREEMENT in matrix.conditions
    assert matrix.contested_evidence_refs == ("E1",)


def test_assumption_disagreement_is_detected_from_different_premises():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE", assumptions=("A",)),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    assessment="SUPPORTIVE",
                    assumptions=("B", "C"),
                ),
            )
        )
    )
    assert DisagreementKind.ASSUMPTION_DISAGREEMENT in matrix.conditions


def test_provider_failure_is_recorded_but_is_not_a_vote():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE"),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    status=ObservationStatus.FAILED,
                    failure_class=ProviderFailureClass.TIMEOUT,
                ),
            )
        )
    )
    assert DisagreementKind.PROVIDER_FAILURE_PRESENT in matrix.conditions
    assert matrix.failed_seats == 1
    # The failure contributes no directional vote, so the case is unanimous.
    assert matrix.supportive_seats == 1
    assert matrix.opposing_seats == 0
    assert matrix.primary is DisagreementKind.UNANIMOUS_AGREEMENT


def test_abstention_is_not_a_negative_vote():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE"),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    assessment="NEUTRAL",
                    abstention="insufficient evidence",
                    sufficiency="INSUFFICIENT",
                    confidence=None,
                ),
            )
        )
    )
    assert matrix.abstained_seats == 1
    assert matrix.opposing_seats == 0
    assert matrix.primary is DisagreementKind.UNANIMOUS_AGREEMENT


def test_no_directional_seat_is_insufficient_evidence_not_a_majority():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat(
                    "case-1",
                    assessment="NEUTRAL",
                    abstention="evidence too thin",
                    sufficiency="INSUFFICIENT",
                    confidence=None,
                ),
            )
        )
    )
    assert matrix.primary is DisagreementKind.INSUFFICIENT_EVIDENCE
    assert matrix.answered_seats == 0


def test_invalid_response_is_recorded_as_its_own_condition():
    matrix = build_disagreement_matrix(
        _case(
            seats=(
                _seat("case-1", assessment="SUPPORTIVE"),
                _seat(
                    "case-1",
                    family=ProviderFamily.ANTHROPIC,
                    model="model-b",
                    status=ObservationStatus.INVALID,
                    failure_class=ProviderFailureClass.SCHEMA_VALIDATION_FAILURE,
                ),
            )
        )
    )
    assert DisagreementKind.SCHEMA_INVALID_PRESENT in matrix.conditions
    assert matrix.invalid_seats == 1


# ------------------------------------------------------------ baseline links


def test_baseline_relation_classifies_without_judgement():
    assert (
        baseline_relation(DirectionalCall.POSITIVE, baseline_positive=True)
        is BaselineRelation.AGREES_WITH_BASELINE
    )
    assert (
        baseline_relation(DirectionalCall.POSITIVE, baseline_positive=False)
        is BaselineRelation.DISAGREES_WITH_BASELINE
    )
    assert (
        baseline_relation(DirectionalCall.POSITIVE, baseline_positive=None)
        is BaselineRelation.BASELINE_UNAVAILABLE
    )
    assert (
        baseline_relation(DirectionalCall.ABSTAIN, baseline_positive=True)
        is BaselineRelation.NOT_DIRECTIONAL
    )


# --------------------------------------------------------------- attribution


def _uniform_cases(count: int, *, committee_right: bool, baseline_right: bool):
    """Cases where both seats and the baseline take a fixed, known relation."""
    committee_assessment = "SUPPORTIVE" if committee_right else "OPPOSING"
    baseline_positive = True if baseline_right else False
    cases = []
    for index in range(count):
        case_id = f"case-{index:03d}"
        cases.append(
            _case(
                case_id,
                seats=(
                    _seat(case_id, assessment=committee_assessment),
                    _seat(
                        case_id,
                        family=ProviderFamily.ANTHROPIC,
                        model="model-b",
                        assessment=committee_assessment,
                    ),
                ),
                baseline_positive=baseline_positive,
                observed_positive=True,
                decided_at=START + timedelta(days=index),
            )
        )
    return tuple(cases)


def test_committee_increment_counts_only_committee_correct_cases():
    cases = _uniform_cases(
        MIN_ATTRIBUTION_SAMPLES, committee_right=True, baseline_right=False
    )
    report = _report(cases)
    increment = report.committee_increment
    assert increment.committee_scored == MIN_ATTRIBUTION_SAMPLES
    assert increment.committee_correct == MIN_ATTRIBUTION_SAMPLES
    assert increment.baseline_correct == 0
    assert increment.only_committee_correct == MIN_ATTRIBUTION_SAMPLES
    assert increment.only_baseline_correct == 0
    assert increment.added_information is True


def test_committee_increment_reports_no_added_information_when_it_loses():
    cases = _uniform_cases(
        MIN_ATTRIBUTION_SAMPLES, committee_right=False, baseline_right=True
    )
    increment = _report(cases).committee_increment
    assert increment.only_baseline_correct == MIN_ATTRIBUTION_SAMPLES
    assert increment.only_committee_correct == 0
    assert increment.added_information is False


def test_added_information_is_unsupported_on_a_small_sample():
    cases = _uniform_cases(3, committee_right=True, baseline_right=False)
    increment = _report(cases).committee_increment
    # Below the minimum sample the report refuses to assert an edge.
    assert increment.added_information is None


def test_provider_attribution_separates_incremental_from_agreement():
    cases = _uniform_cases(
        MIN_ATTRIBUTION_SAMPLES, committee_right=True, baseline_right=False
    )
    provider = _report(cases).providers[0]
    assert provider.scored_cases == MIN_ATTRIBUTION_SAMPLES
    assert provider.correct_cases == MIN_ATTRIBUTION_SAMPLES
    # The baseline was wrong on every case, so all correct calls were incremental.
    assert provider.independent_incremental_correct == MIN_ATTRIBUTION_SAMPLES
    assert provider.baseline_agreements == 0
    assert provider.baseline_disagreements == MIN_ATTRIBUTION_SAMPLES
    assert provider.accuracy_when_disagreeing_with_baseline.value == "1.000000"
    assert provider.accuracy_when_agreeing_with_baseline.applicable is False


def test_agreement_with_baseline_does_not_count_as_incremental():
    cases = _uniform_cases(
        MIN_ATTRIBUTION_SAMPLES, committee_right=True, baseline_right=True
    )
    provider = _report(cases).providers[0]
    assert provider.baseline_agreements == MIN_ATTRIBUTION_SAMPLES
    assert provider.baseline_disagreements == 0
    assert provider.independent_incremental_correct == 0
    assert provider.accuracy_when_agreeing_with_baseline.value == "1.000000"


def test_accuracy_is_reported_per_case_class():
    cases = _uniform_cases(2, committee_right=True, baseline_right=False)
    report = _report(cases)
    provider = report.providers[0]
    assert len(provider.accuracy_by_case_type) == 1
    assert provider.accuracy_by_case_type[0].case_type is CaseType.MARKET_OPPORTUNITY
    assert provider.accuracy_by_case_type[0].correct == 2


def test_attribution_rejects_mixed_case_types():
    cases = list(_uniform_cases(2, committee_right=True, baseline_right=False))
    cases.append(
        _case(
            "case-other",
            seats=(_seat("case-other"),),
            case_type=CaseType.EVENT_INTERPRETATION,
        )
    )
    with pytest.raises(ValueError):
        _report(tuple(cases))


def test_contested_and_unanimous_accuracy_are_reported_separately():
    contested = []
    for index in range(MIN_ATTRIBUTION_SAMPLES):
        case_id = f"contested-{index:03d}"
        contested.append(
            _case(
                case_id,
                seats=(
                    _seat(case_id, assessment="SUPPORTIVE"),
                    _seat(
                        case_id,
                        family=ProviderFamily.ANTHROPIC,
                        model="model-b",
                        assessment="OPPOSING",
                    ),
                ),
                baseline_positive=True,
                observed_positive=True,
                decided_at=START + timedelta(days=index),
            )
        )
    report = _report(tuple(contested))
    # The committee signal abstains on a split, so it has nothing to score.
    assert report.contested_case_accuracy.applicable is False
    assert report.unanimous_case_accuracy.applicable is False


def test_chronological_stability_reports_both_halves():
    cases = _uniform_cases(6, committee_right=True, baseline_right=False)
    report = _report(cases)
    labels = [window.label for window in report.chronological_stability]
    assert labels == ["EARLIER_HALF", "LATER_HALF"]
    assert all(window.scored == 3 for window in report.chronological_stability)


def test_calibration_is_not_applicable_for_ordinal_confidence():
    report = _report(
        _uniform_cases(MIN_ATTRIBUTION_SAMPLES, committee_right=True, baseline_right=False)
    )
    assert report.calibration.applicable is False
    assert report.calibration.value is None


def test_incremental_cost_is_unknown_when_a_provider_cost_is_unknown():
    cases = []
    for index in range(MIN_ATTRIBUTION_SAMPLES):
        case_id = f"case-{index:03d}"
        cases.append(
            _case(
                case_id,
                seats=(
                    _seat(case_id, assessment="SUPPORTIVE", cost=None),
                    _seat(
                        case_id,
                        family=ProviderFamily.ANTHROPIC,
                        model="model-b",
                        assessment="SUPPORTIVE",
                        cost=500,
                    ),
                ),
                baseline_positive=False,
                observed_positive=True,
                decided_at=START + timedelta(days=index),
            )
        )
    report = _report(tuple(cases))
    # A single unknown provider cost makes the aggregate unknown, never partial.
    assert report.incremental_cost_microunits is None
    openai = next(
        provider
        for provider in report.providers
        if provider.provider_family is ProviderFamily.OPENAI
    )
    assert openai.known_cost_microunits is None
    assert openai.unknown_cost_samples == MIN_ATTRIBUTION_SAMPLES


def test_incremental_cost_is_computed_when_fully_known_and_supported():
    cases = []
    for index in range(MIN_ATTRIBUTION_SAMPLES):
        case_id = f"case-{index:03d}"
        cases.append(
            _case(
                case_id,
                seats=(
                    _seat(case_id, assessment="SUPPORTIVE", cost=100),
                    _seat(
                        case_id,
                        family=ProviderFamily.ANTHROPIC,
                        model="model-b",
                        assessment="SUPPORTIVE",
                        cost=100,
                    ),
                ),
                baseline_positive=False,
                observed_positive=True,
                decided_at=START + timedelta(days=index),
            )
        )
    report = _report(tuple(cases))
    assert report.incremental_cost_microunits == (
        2 * 100 * MIN_ATTRIBUTION_SAMPLES
    ) // MIN_ATTRIBUTION_SAMPLES


def test_attribution_report_identity_is_content_derived():
    cases = _uniform_cases(4, committee_right=True, baseline_right=False)
    first = _report(cases)
    second = _report(cases)
    assert first.attribution_id == second.attribution_id
    assert first.attribution_id.startswith("COMMITTEE-ATTRIBUTION:")


def test_attribution_report_can_never_promote():
    report = _report(
        _uniform_cases(4, committee_right=True, baseline_right=False)
    )
    assert report.advisory_only is True
    assert report.measurement_only is True
    assert report.automatic_promotion is False
    assert report.trade_authority_changed is False

    disposition = advisory_disposition(report)
    assert disposition["consumption"] == "ADVISORY"
    assert disposition["automatic_promotion"] is False
    assert disposition["threshold_change_authorized"] is False
    assert disposition["promotion_path"] == (
        "SEPARATE_HUMAN_GOVERNED_PROMOTION_GATE_REQUIRED"
    )


def test_attribution_report_refuses_to_declare_promotion():
    from app.opip.committee.attribution import AttributionReport

    report = _report(
        _uniform_cases(2, committee_right=True, baseline_right=False)
    )
    values = {
        field: getattr(report, field) for field in report.__dataclass_fields__
    }
    values["automatic_promotion"] = True
    with pytest.raises(ValueError):
        AttributionReport(**values)


def test_provider_adequacy_note_states_insufficient_sample():
    report = _report(
        _uniform_cases(3, committee_right=True, baseline_right=False)
    )
    assert "INSUFFICIENT_SAMPLE" in report.providers[0].adequacy_note


# ------------------------------------------------------------- persistence


def test_attribution_report_round_trips_and_is_immutable(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    report = _report(
        _uniform_cases(MIN_ATTRIBUTION_SAMPLES, committee_right=True, baseline_right=False)
    )
    assert store.append_attribution_report(report).reason == REASON_STORED
    assert store.append_attribution_report(report).reason == REASON_DUPLICATE

    reloaded = list(store.iter_attribution_reports())
    assert len(reloaded) == 1
    assert reloaded[0].attribution_id == report.attribution_id
    assert reloaded[0].automatic_promotion is False
    assert reloaded[0].committee_increment.only_committee_correct == (
        report.committee_increment.only_committee_correct
    )
    assert len(reloaded[0].providers) == len(report.providers)


def test_forged_attribution_identity_is_rejected_on_read():
    report = _report(
        _uniform_cases(3, committee_right=True, baseline_right=False)
    )
    row = attribution_report_to_dict(report)
    row["attribution_id"] = "COMMITTEE-ATTRIBUTION:forged"
    with pytest.raises(Exception):
        attribution_report_from_dict(row)


def test_serialization_round_trip_preserves_metrics():
    report = _report(
        _uniform_cases(MIN_ATTRIBUTION_SAMPLES, committee_right=True, baseline_right=False)
    )
    restored = attribution_report_from_dict(attribution_report_to_dict(report))
    assert restored.attribution_id == report.attribution_id
    assert restored.committee_increment.committee_accuracy.value == (
        report.committee_increment.committee_accuracy.value
    )
    assert restored.calibration.applicable is False
