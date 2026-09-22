"""Model bake-off and evaluation (increment 2B).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests prove the harness compares arms on identical cases, reports metrics
only where they are mathematically valid, never invents a metric or a cost,
never treats an ordinal confidence as a probability, and never promotes a model.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

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
from app.opip.committee.evaluation import (
    ADEQUACY_INSUFFICIENT_SAMPLE,
    ArmKind,
    BaselineCall,
    CaseObservation,
    DirectionalCall,
    ProbabilityForecast,
    ReplayComparison,
    ResolvedOutcome,
    committee_research_signal,
    directional_call,
    evaluate_model_bake_off,
    ordinal_confidence_is_not_a_probability,
)
from app.opip.committee.metrics import (
    MIN_METRIC_SAMPLE,
    REASON_INSUFFICIENT_SAMPLE,
    REASON_NO_LABELED_SAMPLES,
    REASON_NO_PROBABILISTIC_FORECAST,
    ProbabilitySample,
    brier_score,
    calibration_report,
    classification_report,
    cost_aggregate,
    latency_distribution,
    log_loss,
    rate_metric,
    repeatability_metric,
)
from app.opip.committee.pricing import (
    COMMITTEE_PRICES_ENV,
    MICROUNITS_PER_UNIT,
    TOKENS_PER_PRICING_UNIT,
    PriceBook,
    PriceBookConfigError,
    TokenPrice,
    scale_tokens,
)
from app.opip.committee.store import (
    REASON_DUPLICATE,
    REASON_STORED,
    CommitteeEvidenceStore,
)
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=4)


def _provenance() -> Provenance:
    return Provenance(
        producing_component="tests.committee.bake_off",
        artifact_or_build_id="test-build",
        process_instance_id="test-process",
        emitted_at=NOW,
        source_record_refs=("src-1",),
    )


def _opinion(
    case_id: str,
    *,
    provider: str = "openai",
    model: str = "model-a",
    assessment: str = "SUPPORTIVE",
    sufficiency: str = "SUFFICIENT",
    confidence: int | None = 60,
    abstention: str | None = None,
) -> StructuredOpinion:
    return StructuredOpinion(
        case_id=case_id,
        provider=provider,
        model=model,
        evidence_sufficiency=EvidenceSufficiency(sufficiency),
        assessment=DirectionalAssessment(assessment),
        hypothesis="scripted research hypothesis",
        confidence=confidence,
        recommended_research_action=ResearchAction.NO_ACTION,
        abstention_reason=abstention,
    )


def _seat(
    case_id: str,
    *,
    family: ProviderFamily = ProviderFamily.OPENAI,
    model: str = "model-a",
    assessment: str = "SUPPORTIVE",
    sufficiency: str = "SUFFICIENT",
    confidence: int | None = 60,
    abstention: str | None = None,
    status: ObservationStatus = ObservationStatus.COMPLETED,
    failure_class: ProviderFailureClass | None = None,
    input_tokens: int | None = 100,
    output_tokens: int | None = 40,
    cost: int | None = 1_000,
    latency: int | None = 500_000,
) -> ProviderCallOutcome:
    opinion = None
    if status in (ObservationStatus.COMPLETED, ObservationStatus.DUPLICATE_OK):
        opinion = _opinion(
            case_id,
            provider=family.value,
            model=model,
            assessment=assessment,
            sufficiency=sufficiency,
            confidence=confidence,
            abstention=abstention,
        )
    # Only a validated opinion can carry usage or cost. A failed or unavailable
    # seat has no served tokens at all, so completeness must follow the values
    # that are actually recorded rather than the requested ones.
    effective_input = input_tokens if opinion is not None else None
    effective_output = output_tokens if opinion is not None else None
    effective_cost = cost if opinion is not None else None
    return ProviderCallOutcome(
        logical_observation_id=f"COMMITTEE-LOGICAL:{case_id}:{family.value}:{model}",
        case_id=case_id,
        provider_family=family,
        requested_model=model,
        status=status,
        attempt=1,
        reproducibility=ReproducibilityClass.NONDETERMINISTIC_PROVIDER_OUTPUT,
        request_at=NOW,
        input_hash=f"COMMITTEE-WIRE:{case_id}:{family.value}",
        reported_provider=family.value if opinion is not None else None,
        reported_model=model if opinion is not None else None,
        failure_class=failure_class,
        opinion=opinion,
        response_at=(NOW if opinion is not None else None),
        latency_micros=latency if opinion is not None else None,
        input_tokens=effective_input,
        output_tokens=effective_output,
        estimated_cost_microunits=effective_cost,
        cost_completeness=(
            CostCompleteness.COMPLETE
            if (
                effective_input is not None
                and effective_output is not None
                and effective_cost is not None
            )
            else CostCompleteness.UNKNOWN
        ),
    )


def _cases(count: int, *, assessments: tuple[str, ...] = ("SUPPORTIVE",)) -> tuple:
    observations = []
    for index in range(count):
        case_id = f"case-{index:03d}"
        assessment = assessments[index % len(assessments)]
        observations.append(
            CaseObservation(
                case_id=case_id,
                case_type=CaseType.MARKET_OPPORTUNITY,
                seats=(
                    _seat(case_id, assessment=assessment),
                    _seat(
                        case_id,
                        family=ProviderFamily.ANTHROPIC,
                        model="model-b",
                        assessment=assessment,
                    ),
                ),
            )
        )
    return tuple(observations)


# ------------------------------------------------------------------ metrics


def test_brier_score_matches_hand_computed_value():
    samples = [
        ProbabilitySample(Decimal("0.8"), True),
        ProbabilitySample(Decimal("0.2"), False),
        ProbabilitySample(Decimal("0.6"), True),
        ProbabilitySample(Decimal("0.4"), False),
        ProbabilitySample(Decimal("0.5"), True),
    ]
    metric = brier_score(samples)
    assert metric.applicable is True
    # (0.04 + 0.04 + 0.16 + 0.16 + 0.25) / 5
    assert Decimal(metric.value) == Decimal("0.13")


def test_log_loss_matches_hand_computed_value():
    samples = [
        ProbabilitySample(Decimal("0.8"), True),
        ProbabilitySample(Decimal("0.2"), False),
    ]
    metric = log_loss(samples)
    assert metric.applicable is False
    assert metric.not_applicable_reason == REASON_INSUFFICIENT_SAMPLE

    bigger = [
        ProbabilitySample(Decimal("0.5"), True),
        ProbabilitySample(Decimal("0.5"), False),
        ProbabilitySample(Decimal("0.5"), True),
        ProbabilitySample(Decimal("0.5"), False),
        ProbabilitySample(Decimal("0.5"), True),
    ]
    assert Decimal(log_loss(bigger).value).quantize(Decimal("0.000001")) == Decimal(
        "0.693147"
    ).quantize(Decimal("0.000001"))


def test_log_loss_is_bounded_by_documented_clipping():
    samples = [
        ProbabilitySample(Decimal("0"), True),
        ProbabilitySample(Decimal("1"), False),
        ProbabilitySample(Decimal("0.5"), True),
        ProbabilitySample(Decimal("0.5"), False),
        ProbabilitySample(Decimal("0.5"), True),
    ]
    metric = log_loss(samples)
    assert metric.applicable is True
    assert Decimal(metric.value) < Decimal("20")


def test_probability_outside_zero_to_one_is_rejected():
    for value in (Decimal("-0.1"), Decimal("1.1")):
        with pytest.raises(ValueError):
            ProbabilitySample(value, True)


def test_calibration_reports_bins_and_error():
    samples = [
        ProbabilitySample(Decimal("0.9"), True),
        ProbabilitySample(Decimal("0.9"), True),
        ProbabilitySample(Decimal("0.1"), False),
        ProbabilitySample(Decimal("0.1"), False),
        ProbabilitySample(Decimal("0.5"), True),
    ]
    report = calibration_report(samples, bin_count=10)
    assert report.expected_calibration_error.applicable is True
    assert sum(item.count for item in report.bins) == 5
    high = [item for item in report.bins if Decimal(item.lower) == Decimal("0.9")]
    assert high
    assert high[0].observed_rate == "1.000000"


def test_calibration_without_samples_is_not_applicable_not_zero():
    report = calibration_report([])
    assert report.bins == ()
    assert report.expected_calibration_error.applicable is False
    assert (
        report.expected_calibration_error.value is None
    )


def test_classification_metrics_match_hand_computed_values():
    pairs = [(True, True), (True, False), (False, True), (False, False)]
    report = classification_report(pairs)
    assert report.matrix.true_positive == 1
    assert report.matrix.false_positive == 1
    assert report.matrix.true_negative == 1
    assert report.matrix.false_negative == 1
    assert report.precision.value == "0.500000"
    assert report.recall.value == "0.500000"
    assert report.f1.value == "0.500000"
    assert report.accuracy.value == "0.500000"


def test_classification_without_labels_is_not_applicable():
    report = classification_report([])
    assert report.matrix.total == 0
    assert report.precision.applicable is False
    assert report.precision.value is None
    assert report.accuracy.applicable is False


def test_rate_metric_with_empty_denominator_is_not_applicable():
    metric = rate_metric("coverage", numerator=0, denominator=0)
    assert metric.applicable is False
    assert metric.not_applicable_reason == REASON_NO_LABELED_SAMPLES
    assert metric.value is None


def test_repeatability_without_replays_is_not_applicable():
    metric = repeatability_metric(agreed=0, compared=0)
    assert metric.applicable is False
    assert metric.value is None


def test_latency_distribution_is_nearest_rank():
    distribution = latency_distribution([100, 200, 300, 400, 500])
    assert distribution.sample_size == 5
    assert distribution.p50_micros == 300
    assert distribution.p90_micros == 500
    assert distribution.maximum_micros == 500
    assert latency_distribution([]).applicable is False


def test_cost_aggregate_keeps_unknown_separate_from_zero():
    aggregate = cost_aggregate([100, None, 200])
    assert aggregate.known_cost_microunits == 300
    assert aggregate.unknown_cost_samples == 1
    assert aggregate.cost_is_known is False
    assert cost_aggregate([None, None]).known_cost_microunits is None


# ------------------------------------------------------------------ pricing


def test_price_book_computes_cost_from_configuration():
    book = PriceBook(
        [
            TokenPrice(
                provider="openai",
                model="model-a",
                microunits_per_million_input_tokens=1_000_000,
                microunits_per_million_output_tokens=3_000_000,
            )
        ]
    )
    # 100 input tokens at 1 microunit/token, 40 output at 3.
    assert (
        book.cost_microunits(
            provider="openai", model="model-a", input_tokens=100, output_tokens=40
        )
        == 100 + 120
    )


def test_unknown_price_is_unknown_cost_never_zero():
    book = PriceBook(())
    assert book.is_empty
    assert (
        book.cost_microunits(
            provider="openai", model="model-a", input_tokens=100, output_tokens=40
        )
        is None
    )


def test_missing_token_counts_make_cost_unknown():
    book = PriceBook(
        [
            TokenPrice(
                provider="openai",
                model="model-a",
                microunits_per_million_input_tokens=1_000_000,
                microunits_per_million_output_tokens=3_000_000,
            )
        ]
    )
    assert (
        book.cost_microunits(
            provider="openai", model="model-a", input_tokens=None, output_tokens=40
        )
        is None
    )


def test_price_book_parses_configuration():
    book = PriceBook.from_env(
        {
            COMMITTEE_PRICES_ENV: (
                "openai:model-a=2000000/8000000; anthropic:model-b=3000000/15000000"
            )
        }
    )
    assert len(book) == 2
    priced = book.price_for("ANTHROPIC", "model-b")
    assert priced.microunits_per_million_output_tokens == 15_000_000
    assert priced.microunits_per_million_input_tokens == 3_000_000
    # Lookup is case-insensitive on the provider family name.
    assert book.price_for("anthropic", "model-b") is priced
    assert PriceBook.from_env({}).is_empty


def test_malformed_price_specification_is_rejected_not_ignored():
    for spec in ("openai:model-a", "openai:model-a=x/y", "openai:model-a=1", "nonsense"):
        with pytest.raises(PriceBookConfigError):
            PriceBook.from_env({COMMITTEE_PRICES_ENV: spec})


def test_duplicate_price_entries_are_rejected():
    price = TokenPrice(
        provider="openai",
        model="model-a",
        microunits_per_million_input_tokens=1,
        microunits_per_million_output_tokens=1,
    )
    with pytest.raises(PriceBookConfigError):
        PriceBook([price, price])


def test_pricing_units_are_explicit_and_integer_exact():
    assert TOKENS_PER_PRICING_UNIT == 1_000_000
    assert MICROUNITS_PER_UNIT == 1_000_000
    # 1,000,000 tokens at 2,500,000 microunits per million tokens = 2,500,000.
    assert scale_tokens(1_000_000, 2_500_000) == 2_500_000
    # Rounds half up rather than truncating to zero.
    assert scale_tokens(1, 500_000) == 1
    assert scale_tokens(0, 2_500_000) == 0


# ------------------------------------------------------------- directional


def test_failure_and_abstention_are_not_directional_calls():
    failing = _seat(
        "case-0",
        status=ObservationStatus.FAILED,
        failure_class=ProviderFailureClass.TIMEOUT,
    )
    abstaining = _seat(
        "case-0",
        assessment="NEUTRAL",
        abstention="insufficient evidence",
        sufficiency="INSUFFICIENT",
        confidence=None,
    )
    supportive = _seat("case-0", assessment="SUPPORTIVE")
    opposing = _seat("case-0", assessment="OPPOSING")
    neutral = _seat("case-0", assessment="UNCERTAIN", confidence=10)

    assert directional_call(failing) is DirectionalCall.UNAVAILABLE
    assert directional_call(abstaining) is DirectionalCall.ABSTAIN
    assert directional_call(neutral) is DirectionalCall.ABSTAIN
    assert directional_call(supportive) is DirectionalCall.POSITIVE
    assert directional_call(opposing) is DirectionalCall.NEGATIVE


def test_partial_committee_does_not_produce_a_directional_signal():
    seats = (
        _seat("case-0", assessment="SUPPORTIVE"),
        _seat(
            "case-0",
            family=ProviderFamily.ANTHROPIC,
            model="model-b",
            status=ObservationStatus.FAILED,
            failure_class=ProviderFailureClass.RATE_LIMIT,
        ),
    )
    signal = committee_research_signal(seats)
    assert signal is DirectionalCall.ABSTAIN


def test_committee_signal_preserves_dissent_as_abstention():
    seats = (
        _seat("case-0", assessment="SUPPORTIVE"),
        _seat(
            "case-0",
            family=ProviderFamily.ANTHROPIC,
            model="model-b",
            assessment="OPPOSING",
        ),
    )
    assert committee_research_signal(seats) is DirectionalCall.ABSTAIN


def test_committee_signal_reads_supportive_when_seats_agree():
    seats = (
        _seat("case-0", assessment="SUPPORTIVE"),
        _seat(
            "case-0",
            family=ProviderFamily.ANTHROPIC,
            model="model-b",
            assessment="SUPPORTIVE",
        ),
    )
    assert committee_research_signal(seats) is DirectionalCall.POSITIVE


# ----------------------------------------------------------------- bake-off


def test_bake_off_compares_all_required_baselines():
    cases = _cases(4)
    resolved = [
        ResolvedOutcome(
            case_id=case.case_id, positive=True, observed_at=LATER, source_ref="outcome-1"
        )
        for case in cases
    ]
    report = evaluate_model_bake_off(
        cases,
        experiment_id="exp-1",
        provenance=_provenance(),
        generated_at=LATER,
        resolved_outcomes=resolved,
        deterministic_baseline=[
            BaselineCall(case_id=case.case_id, positive=True) for case in cases
        ],
        minimum_samples=2,
    )
    kinds = {arm.kind for arm in report.arms}
    assert ArmKind.INDIVIDUAL_MODEL in kinds
    assert ArmKind.COMMITTEE_SIGNAL in kinds
    assert ArmKind.DETERMINISTIC_BASELINE in kinds
    assert ArmKind.NULL_BASELINE in kinds


def test_bake_off_labels_identity_but_never_promotes():
    report = evaluate_model_bake_off(
        _cases(3),
        experiment_id="exp-2",
        provenance=_provenance(),
        generated_at=LATER,
        minimum_samples=2,
    )
    assert report.measurement_only is True
    assert report.automatic_promotion is False
    assert report.trade_authority_changed is False
    assert report.phase is EvaluationPhase.RETROSPECTIVE
    assert report.report_id.startswith("COMMITTEE-EVALUATION:")
    assert evaluate_model_bake_off(
        _cases(3),
        experiment_id="exp-2",
        provenance=_provenance(),
        generated_at=LATER,
        minimum_samples=2,
    ).report_id == report.report_id


def test_bake_off_marks_arms_insufficient_below_the_minimum():
    report = evaluate_model_bake_off(
        _cases(2),
        experiment_id="exp-3",
        provenance=_provenance(),
        generated_at=LATER,
        minimum_samples=30,
    )
    assert all(not arm.is_adequate for arm in report.arms)
    assert all(arm.adequacy == ADEQUACY_INSUFFICIENT_SAMPLE for arm in report.arms)
    assert report.adequate_arms == ()


def test_bake_off_rejects_mixed_case_types():
    cases = list(_cases(2))
    cases.append(
        CaseObservation(
            case_id="case-other",
            case_type=CaseType.EVENT_INTERPRETATION,
            seats=(_seat("case-other"),),
        )
    )
    mixed = tuple(cases)
    provenance = _provenance()
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            mixed,
            experiment_id="exp-4",
            provenance=provenance,
            generated_at=LATER,
        )


def test_bake_off_rejects_a_label_for_an_unknown_case():
    observations = _cases(2)
    provenance = _provenance()
    unknown_label = [
        ResolvedOutcome(
            case_id="not-a-case",
            positive=True,
            observed_at=LATER,
            source_ref="outcome",
        )
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            observations,
            experiment_id="exp-5",
            provenance=provenance,
            generated_at=LATER,
            resolved_outcomes=unknown_label,
        )


def test_provider_failures_are_counted_but_never_scored_as_calls():
    case_id = "case-000"
    case = CaseObservation(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(
            _seat(case_id, assessment="SUPPORTIVE"),
            _seat(
                case_id,
                family=ProviderFamily.ANTHROPIC,
                model="model-b",
                status=ObservationStatus.FAILED,
                failure_class=ProviderFailureClass.TIMEOUT,
            ),
        ),
    )
    report = evaluate_model_bake_off(
        (case,),
        experiment_id="exp-6",
        provenance=_provenance(),
        generated_at=LATER,
        resolved_outcomes=[
            ResolvedOutcome(
                case_id=case_id, positive=True, observed_at=LATER, source_ref="outcome"
            )
        ],
        minimum_samples=1,
    )
    failing = report.arm("model:anthropic:model-b")
    assert failing.failures == 1
    assert failing.answered == 0
    # A seat that never answered has zero directional coverage over the case.
    assert failing.coverage.applicable is True
    assert failing.coverage.value == "0.000000"
    # The failed seat is excluded from scoring rather than counted as a miss.
    assert failing.confusion is None
    healthy = report.arm("model:openai:model-a")
    assert healthy.answered == 1
    assert healthy.confusion is not None
    assert healthy.confusion.true_positive == 1


def test_cost_is_tracked_and_unknown_cost_is_never_zero_in_the_arm():
    case_id = "case-000"
    case = CaseObservation(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(
            _seat(case_id, cost=1_000),
            _seat(
                case_id,
                family=ProviderFamily.ANTHROPIC,
                model="model-b",
                cost=None,
            ),
        ),
    )
    report = evaluate_model_bake_off(
        (case,), experiment_id="exp-7", provenance=_provenance(), generated_at=LATER
    )
    unknown = report.arm("model:anthropic:model-b").cost
    assert unknown.known_cost_microunits is None
    assert unknown.unknown_cost_samples == 1
    assert unknown.cost_is_known is False

    signal = report.arm("committee:research-signal").cost
    # One unknown seat makes the committee aggregate unknown, never partial.
    assert signal.known_cost_microunits is None


def test_ordinal_confidence_is_never_scored_as_a_probability():
    cases = _cases(
        6, assessments=("SUPPORTIVE", "OPPOSING")
    )
    resolved = [
        ResolvedOutcome(
            case_id=case.case_id,
            positive=index % 2 == 0,
            observed_at=LATER,
            source_ref="outcome",
        )
        for index, case in enumerate(cases)
    ]
    report = evaluate_model_bake_off(
        cases,
        experiment_id="exp-8",
        provenance=_provenance(),
        generated_at=LATER,
        resolved_outcomes=resolved,
        minimum_samples=2,
    )
    for arm in report.arms:
        assert arm.brier_score.applicable is False
        assert arm.brier_score.value is None
        # The reason is explicit: an ordinal score is not a probability, and no
        # forecast was defined, so no value is reported at all.
        assert arm.brier_score.not_applicable_reason == REASON_NO_PROBABILISTIC_FORECAST
        assert arm.log_loss.value is None
        assert arm.expected_calibration_error.value is None
        assert arm.calibration_bins == ()
    declaration = ordinal_confidence_is_not_a_probability()
    assert declaration["treated_as_probability"] == "false"


def test_probabilistic_metrics_apply_only_when_a_forecast_is_defined():
    cases = _cases(6, assessments=("SUPPORTIVE", "OPPOSING"))
    resolved = [
        ResolvedOutcome(
            case_id=case.case_id,
            positive=index % 2 == 0,
            observed_at=LATER,
            source_ref="outcome",
        )
        for index, case in enumerate(cases)
    ]
    forecasts = [
        ProbabilityForecast(
            case_id=case.case_id,
            arm_id="model:openai:model-a",
            probability=Decimal("0.90") if index % 2 == 0 else Decimal("0.10"),
        )
        for index, case in enumerate(cases)
    ]
    report = evaluate_model_bake_off(
        cases,
        experiment_id="exp-9",
        provenance=_provenance(),
        generated_at=LATER,
        resolved_outcomes=resolved,
        probability_forecasts=forecasts,
        minimum_samples=2,
    )
    openai = report.arm("model:openai:model-a")
    assert openai.brier_score.applicable is True
    assert Decimal(openai.brier_score.value) < Decimal("0.05")
    assert openai.expected_calibration_error.applicable is True
    # An arm with no declared forecast stays not applicable.
    anthropic = report.arm("model:anthropic:model-b")
    assert anthropic.brier_score.applicable is False


def test_replay_comparisons_drive_consistency_only_for_that_arm():
    cases = _cases(2)
    report = evaluate_model_bake_off(
        cases,
        experiment_id="exp-10",
        provenance=_provenance(),
        generated_at=LATER,
        replays=[
            ReplayComparison(
                provider_family=ProviderFamily.OPENAI,
                model="model-a",
                case_id="case-000",
                reproduced=True,
            ),
            ReplayComparison(
                provider_family=ProviderFamily.OPENAI,
                model="model-a",
                case_id="case-001",
                reproduced=True,
            ),
        ],
        minimum_samples=1,
    )
    assert report.arm("model:openai:model-a").consistency.value == "1.000000"
    assert report.arm("model:anthropic:model-b").consistency.applicable is False


def test_null_baseline_scores_only_when_labels_exist():
    without_labels = evaluate_model_bake_off(
        _cases(2), experiment_id="exp-11", provenance=_provenance(), generated_at=LATER
    )
    assert all(arm.kind is not ArmKind.NULL_BASELINE for arm in without_labels.arms)

    with_labels = evaluate_model_bake_off(
        _cases(2),
        experiment_id="exp-11",
        provenance=_provenance(),
        generated_at=LATER,
        resolved_outcomes=[
            ResolvedOutcome(
                case_id="case-000", positive=True, observed_at=LATER, source_ref="o"
            )
        ],
        minimum_samples=1,
    )
    assert with_labels.arm("baseline:null").kind is ArmKind.NULL_BASELINE


def test_calibration_declaration_reports_reason_codes():
    declaration = ordinal_confidence_is_not_a_probability()
    assert declaration["confidence_semantics"] == "ORDINAL_0_100_SELF_REPORT"
    assert declaration["minimum_metric_sample"] == str(MIN_METRIC_SAMPLE)


# ------------------------------------------------------------- persistence


def test_evaluation_report_is_stored_and_reloaded(tmp_path):
    store = CommitteeEvidenceStore(root=tmp_path)
    report = evaluate_model_bake_off(
        _cases(3),
        experiment_id="exp-store",
        provenance=_provenance(),
        generated_at=LATER,
        minimum_samples=2,
    )
    assert store.append_evaluation_report(report).reason == REASON_STORED
    assert store.append_evaluation_report(report).reason == REASON_DUPLICATE

    reloaded = list(store.iter_evaluation_reports())
    assert len(reloaded) == 1
    assert reloaded[0].report_id == report.report_id
    assert len(reloaded[0].arms) == len(report.arms)
    assert (
        reloaded[0].arm("model:openai:model-a").precision.value
        == report.arm("model:openai:model-a").precision.value
    )
    assert reloaded[0].automatic_promotion is False


# ==================== review findings: bake-off input and metric integrity


def test_duplicate_case_observations_are_rejected():
    """A repeated case id cannot inflate samples or overwrite a seat result."""
    cases = _cases(3)
    duplicated = cases + cases[:1]
    provenance = _provenance()
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            duplicated,
            experiment_id="dup-1",
            provenance=provenance,
            generated_at=LATER,
        )


def test_duplicate_resolved_outcomes_are_rejected():
    cases = _cases(2)
    provenance = _provenance()
    labels = [
        ResolvedOutcome(
            case_id="case-000", positive=True, observed_at=LATER, source_ref="o1"
        ),
        ResolvedOutcome(
            case_id="case-000", positive=False, observed_at=LATER, source_ref="o2"
        ),
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            cases,
            experiment_id="dup-2",
            provenance=provenance,
            generated_at=LATER,
            resolved_outcomes=labels,
        )


def test_duplicate_baseline_calls_are_rejected():
    cases = _cases(2)
    provenance = _provenance()
    baseline = [
        BaselineCall(case_id="case-000", positive=True),
        BaselineCall(case_id="case-000", positive=False),
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            cases,
            experiment_id="dup-3",
            provenance=provenance,
            generated_at=LATER,
            deterministic_baseline=baseline,
        )


def test_duplicate_forecasts_for_one_case_and_arm_are_rejected():
    cases = _cases(2)
    provenance = _provenance()
    forecasts = [
        ProbabilityForecast(
            case_id="case-000", arm_id="model:openai:model-a", probability=Decimal("0.6")
        ),
        ProbabilityForecast(
            case_id="case-000", arm_id="model:openai:model-a", probability=Decimal("0.7")
        ),
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            cases,
            experiment_id="dup-4",
            provenance=provenance,
            generated_at=LATER,
            probability_forecasts=forecasts,
        )


def test_forecasts_are_rejected_for_a_non_probabilistic_case_type():
    """Only a case type that defines a probability may carry one."""
    case = CaseObservation(
        case_id="evt-1",
        case_type=CaseType.EVENT_INTERPRETATION,
        seats=(_seat("evt-1"),),
    )
    provenance = _provenance()
    forecasts = [
        ProbabilityForecast(
            case_id="evt-1", arm_id="model:openai:model-a", probability=Decimal("0.6")
        )
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            (case,),
            experiment_id="fc-1",
            provenance=provenance,
            generated_at=LATER,
            probability_forecasts=forecasts,
        )


def test_zero_token_usage_is_a_known_value_not_missing():
    """A recorded zero must not be treated as unknown usage."""
    from app.opip.committee.evaluation import _sum_tokens

    all_known = [_seat("case-000", input_tokens=0), _seat("case-001", input_tokens=0)]
    assert _sum_tokens(all_known, "input_tokens") == 0

    one_unknown = [_seat("case-000", input_tokens=0), _seat("case-001", input_tokens=None)]
    assert _sum_tokens(one_unknown, "input_tokens") is None

    every_unknown = [
        _seat("case-000", input_tokens=None),
        _seat("case-001", input_tokens=None),
    ]
    assert _sum_tokens(every_unknown, "input_tokens") is None

    known_sum = [_seat("case-000", input_tokens=10), _seat("case-001", input_tokens=32)]
    assert _sum_tokens(known_sum, "input_tokens") == 42


def test_committee_signal_token_aggregate_is_unknown_if_any_seat_is_unknown():
    """A partial token total must not be presented as a complete one."""
    case_id = "case-000"
    case = CaseObservation(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(
            _seat(case_id, input_tokens=10, output_tokens=5),
            _seat(
                case_id,
                family=ProviderFamily.ANTHROPIC,
                model="model-b",
                input_tokens=None,
                output_tokens=None,
            ),
        ),
    )
    report = evaluate_model_bake_off(
        (case,), experiment_id="tok-1", provenance=_provenance(), generated_at=LATER
    )
    signal = report.arm("committee:research-signal")
    assert signal.input_tokens is None
    assert signal.output_tokens is None


def test_every_metric_keeps_its_own_name_when_nothing_is_applicable():
    """Persisted names stay correct and public lookup works on an empty arm."""
    from app.opip.committee.metrics import classification_report

    report = classification_report([])
    assert report.precision.name == "precision"
    assert report.recall.name == "recall"
    assert report.f1.name == "f1"
    assert report.accuracy.name == "accuracy"
    for metric in (report.precision, report.recall, report.f1, report.accuracy):
        assert metric.applicable is False
        assert metric.value is None
        assert metric.sample_size == 0


def test_an_unevaluated_arm_still_resolves_every_metric_by_name():
    """`metric("accuracy")` must not raise precisely for an unevaluated arm."""
    case_id = "case-000"
    case = CaseObservation(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(_seat(case_id),),
    )
    report = evaluate_model_bake_off(
        (case,), experiment_id="names-1", provenance=_provenance(), generated_at=LATER
    )
    arm = report.arm("model:openai:model-a")
    assert arm.confusion is None
    for name in ("precision", "recall", "f1", "accuracy"):
        metric = arm.metric(name)
        assert metric.name == name
        assert metric.applicable is False


def test_report_identity_covers_failures_skips_cost_and_latency():
    """A report differing only in persisted arm evidence gets a new id."""
    case_id = "case-000"
    base_case = CaseObservation(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(_seat(case_id, cost=100, latency=1_000),),
    )
    other_case = CaseObservation(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(_seat(case_id, cost=999, latency=1_000),),
    )
    first = evaluate_model_bake_off(
        (base_case,), experiment_id="ident-1", provenance=_provenance(), generated_at=LATER
    )
    second = evaluate_model_bake_off(
        (other_case,), experiment_id="ident-1", provenance=_provenance(), generated_at=LATER
    )
    assert first.report_id != second.report_id

    failed_seat = _seat(
        case_id,
        status=ObservationStatus.FAILED,
        failure_class=ProviderFailureClass.TIMEOUT,
    )
    failed_case = CaseObservation(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(failed_seat,),
    )
    third = evaluate_model_bake_off(
        (failed_case,), experiment_id="ident-1", provenance=_provenance(), generated_at=LATER
    )
    assert third.report_id != first.report_id


def test_an_individual_arm_propagates_unknown_token_usage():
    """An arm with one unknown call must not report a partial total."""
    case_known = CaseObservation(
        case_id="case-000",
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(_seat("case-000", input_tokens=10, output_tokens=4),),
    )
    case_unknown = CaseObservation(
        case_id="case-001",
        case_type=CaseType.MARKET_OPPORTUNITY,
        seats=(_seat("case-001", input_tokens=None, output_tokens=None),),
    )
    report = evaluate_model_bake_off(
        (case_known, case_unknown),
        experiment_id="tok-arm-1",
        provenance=_provenance(),
        generated_at=LATER,
    )
    arm = report.arm("model:openai:model-a")
    assert arm.input_tokens is None
    assert arm.output_tokens is None


def test_an_individual_arm_reports_totals_when_every_call_is_known():
    cases = (
        CaseObservation(
            case_id="case-000",
            case_type=CaseType.MARKET_OPPORTUNITY,
            seats=(_seat("case-000", input_tokens=10, output_tokens=4),),
        ),
        CaseObservation(
            case_id="case-001",
            case_type=CaseType.MARKET_OPPORTUNITY,
            seats=(_seat("case-001", input_tokens=32, output_tokens=8),),
        ),
    )
    report = evaluate_model_bake_off(
        cases, experiment_id="tok-arm-2", provenance=_provenance(), generated_at=LATER
    )
    arm = report.arm("model:openai:model-a")
    assert arm.input_tokens == 42
    assert arm.output_tokens == 12


def test_a_forecast_for_a_nonexistent_arm_is_rejected():
    """A mistyped arm id must not be silently discarded."""
    cases = _cases(2)
    provenance = _provenance()
    bogus = [
        ProbabilityForecast(
            case_id="case-000",
            arm_id="model:openai:model-typo",
            probability=Decimal("0.6"),
        )
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            cases,
            experiment_id="arm-1",
            provenance=provenance,
            generated_at=LATER,
            probability_forecasts=bogus,
        )


def test_replays_outside_the_report_case_set_are_rejected():
    cases = _cases(2)
    provenance = _provenance()
    foreign = [
        ReplayComparison(
            provider_family=ProviderFamily.OPENAI,
            model="model-a",
            case_id="not-a-case",
            reproduced=True,
        )
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            cases,
            experiment_id="replay-1",
            provenance=provenance,
            generated_at=LATER,
            replays=foreign,
        )


def test_duplicate_replay_comparisons_are_rejected():
    """A repeated comparison must not inflate the repeatability denominator."""
    cases = _cases(2)
    provenance = _provenance()
    repeated = [
        ReplayComparison(
            provider_family=ProviderFamily.OPENAI,
            model="model-a",
            case_id="case-000",
            reproduced=True,
        ),
        ReplayComparison(
            provider_family=ProviderFamily.OPENAI,
            model="model-a",
            case_id="case-000",
            reproduced=True,
        ),
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            cases,
            experiment_id="replay-2",
            provenance=provenance,
            generated_at=LATER,
            replays=repeated,
        )


def test_a_replay_for_an_unobserved_seat_is_rejected():
    """A replay must name a seat actually observed for its case."""
    cases = _cases(2)
    provenance = _provenance()
    unobserved = [
        ReplayComparison(
            provider_family=ProviderFamily.DEEPSEEK,
            model="model-not-seated",
            case_id="case-000",
            reproduced=True,
        )
    ]
    with pytest.raises(ValueError):
        evaluate_model_bake_off(
            cases,
            experiment_id="replay-3",
            provenance=provenance,
            generated_at=LATER,
            replays=unobserved,
        )


def test_a_valid_replay_still_scores_consistency():
    cases = _cases(2)
    report = evaluate_model_bake_off(
        cases,
        experiment_id="replay-4",
        provenance=_provenance(),
        generated_at=LATER,
        replays=[
            ReplayComparison(
                provider_family=ProviderFamily.OPENAI,
                model="model-a",
                case_id="case-000",
                reproduced=True,
            ),
            ReplayComparison(
                provider_family=ProviderFamily.OPENAI,
                model="model-a",
                case_id="case-001",
                reproduced=True,
            ),
        ],
        minimum_samples=1,
    )
    assert report.arm("model:openai:model-a").consistency.value == "1.000000"
    assert report.arm("model:openai:model-a").consistency.sample_size == 2
