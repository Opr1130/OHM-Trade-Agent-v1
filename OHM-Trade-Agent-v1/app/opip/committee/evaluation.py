"""Model bake-off: objective, same-evidence comparison of arms.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The harness compares arms over the *same* committee evidence contract, so every
arm is scored on identical cases. It reports research measurements and nothing
else:

* no arm is ever promoted, preferred, or enabled by this module;
* an arm with too few scored cases is marked ``INSUFFICIENT_SAMPLE`` and must
  not be read as a winner;
* a metric that cannot be computed is reported as not applicable with a reason,
  never as a zero;
* a provider failure and an abstention are counted separately, and neither is
  scored as a directional call.

Calibration rule: a seat's ``confidence`` is an **ordinal** 0-100 self-report. It
is deliberately never converted into a probability, so probabilistic metrics are
computed only from an explicit :class:`ProbabilityForecast` that a case type
genuinely defines. Otherwise Brier score, log loss, and calibration are reported
as not applicable. Deriving a probability from an ordinal score would be exactly
the conflation the repository's statistical protocol forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping, Sequence

from app.opip.committee.contracts import (
    CaseType,
    DirectionalAssessment,
    EvaluationPhase,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFamily,
)
from app.opip.committee.metrics import (
    CalibrationBin,
    ConfusionMatrix,
    CostAggregate,
    EvaluationMetric,
    LatencyDistribution,
    MIN_METRIC_SAMPLE,
    REASON_NOT_DEFINED_FOR_CASE_TYPE,
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
from app.opip.decision_intelligence.identity import Provenance
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

#: Below this many scored cases an arm is not comparable with any confidence.
MIN_EVALUATION_SAMPLES = 30

#: Version stamps for the measurement definitions. Changing a rule changes this.
METRIC_DEFINITIONS_VERSION = "committee-metrics-v1"
COMMITTEE_SIGNAL_RULE_VERSION = "committee-research-signal-v1"

#: Share of answered, non-abstaining seats required for the research signal to
#: read as supportive. This is a *research* aggregation, not a decision rule.
COMMITTEE_SIGNAL_SUPPORTIVE_SHARE_BASIS_POINTS = 6_000

#: A research signal needs at least this many directional seats to be defined.
COMMITTEE_SIGNAL_MIN_ANSWERED_SEATS = 2

ADEQUACY_ADEQUATE = "ADEQUATE"
ADEQUACY_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"

REPORT_IDENTITY_DOMAIN = "COMMITTEE-EVALUATION"

#: Shared validation message so the contract wording cannot drift.
_CASE_ID_MESSAGE = "case_id is required"


class ArmKind(str, Enum):
    """What kind of comparator an arm is.

    ``NULL_BASELINE`` always answers the positive class. That is the honest
    trivial comparator: an arm that cannot beat it has no measured edge.
    """

    DETERMINISTIC_BASELINE = "DETERMINISTIC_BASELINE"
    INDIVIDUAL_MODEL = "INDIVIDUAL_MODEL"
    COMMITTEE_SIGNAL = "COMMITTEE_SIGNAL"
    NULL_BASELINE = "NULL_BASELINE"


class DirectionalCall(str, Enum):
    """A binary research call, before any label comparison."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    ABSTAIN = "ABSTAIN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ResolvedOutcome:
    """A resolved future outcome, joined for evaluation only."""

    case_id: str
    positive: bool
    observed_at: datetime
    source_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError(_CASE_ID_MESSAGE)
        if type(self.positive) is not bool:
            raise ValueError("positive must be a boolean")
        object.__setattr__(
            self, "observed_at", require_utc(self.observed_at, field_name="observed_at")
        )
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ValueError("source_ref is required")


@dataclass(frozen=True)
class BaselineCall:
    """A non-model comparator's call for one case."""

    case_id: str
    positive: bool

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError(_CASE_ID_MESSAGE)
        if type(self.positive) is not bool:
            raise ValueError("positive must be a boolean")


@dataclass(frozen=True)
class ReplayComparison:
    """One explicit repeatability observation for an individual-model arm."""

    provider_family: ProviderFamily
    model: str
    case_id: str
    reproduced: bool

    def __post_init__(self) -> None:
        if not isinstance(self.provider_family, ProviderFamily):
            raise ValueError("invalid provider_family")
        for field_name in ("model", "case_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if type(self.reproduced) is not bool:
            raise ValueError("reproduced must be a boolean")


@dataclass(frozen=True)
class ProbabilityForecast:
    """A genuine probabilistic forecast for one arm on one case.

    Only a case type that actually defines a probability may supply one. An
    ordinal confidence score must never be passed here.
    """

    case_id: str
    arm_id: str
    probability: Decimal

    def __post_init__(self) -> None:
        for field_name in ("case_id", "arm_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.probability, Decimal):
            raise ValueError("probability must be a Decimal")
        if not (Decimal(0) <= self.probability <= Decimal(1)):
            raise ValueError("probability must be within 0..1")


@dataclass(frozen=True)
class CaseObservation:
    """Everything the harness knows about one case, from committee evidence."""

    case_id: str
    case_type: CaseType
    seats: tuple[ProviderCallOutcome, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError(_CASE_ID_MESSAGE)
        if not isinstance(self.case_type, CaseType):
            raise ValueError("invalid case_type")
        if not isinstance(self.seats, tuple):
            raise ValueError("seats must be a tuple")
        for seat in self.seats:
            if not isinstance(seat, ProviderCallOutcome):
                raise ValueError("seats must be ProviderCallOutcome")
            if seat.case_id != self.case_id:
                raise ValueError("every seat must belong to its case")


@dataclass(frozen=True)
class ArmEvaluation:
    """One arm's measured behaviour over the shared case set."""

    arm_id: str
    kind: ArmKind
    label: str
    provider_family: ProviderFamily | None
    model: str | None
    cases: int
    answered: int
    abstentions: int
    schema_valid: int
    failures: int
    unavailable: int
    skipped_budget: int
    input_tokens: int | None
    output_tokens: int | None
    adequacy: str
    coverage: EvaluationMetric
    response_validity: EvaluationMetric
    abstention_rate: EvaluationMetric
    failure_rate: EvaluationMetric
    schema_compliance: EvaluationMetric
    consistency: EvaluationMetric
    precision: EvaluationMetric
    recall: EvaluationMetric
    f1: EvaluationMetric
    accuracy: EvaluationMetric
    brier_score: EvaluationMetric
    log_loss: EvaluationMetric
    expected_calibration_error: EvaluationMetric
    calibration_bins: tuple[CalibrationBin, ...]
    confusion: ConfusionMatrix | None
    latency: LatencyDistribution
    cost: CostAggregate

    @property
    def is_adequate(self) -> bool:
        return self.adequacy == ADEQUACY_ADEQUATE

    def metrics(self) -> tuple[EvaluationMetric, ...]:
        return (
            self.coverage,
            self.response_validity,
            self.abstention_rate,
            self.failure_rate,
            self.schema_compliance,
            self.consistency,
            self.precision,
            self.recall,
            self.f1,
            self.accuracy,
            self.brier_score,
            self.log_loss,
            self.expected_calibration_error,
        )

    def metric(self, name: str) -> EvaluationMetric:
        for item in self.metrics():
            if item.name == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True)
class EvaluationReport:
    """A durable, auditable bake-off report. It never promotes anything."""

    experiment_id: str
    phase: EvaluationPhase
    case_type: CaseType
    generated_at: datetime
    case_count: int
    minimum_samples: int
    arms: tuple[ArmEvaluation, ...]
    provenance: Provenance
    metric_definitions_version: str = METRIC_DEFINITIONS_VERSION
    committee_signal_rule_version: str = COMMITTEE_SIGNAL_RULE_VERSION
    measurement_only: bool = True
    automatic_promotion: bool = False
    trade_authority_changed: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported EvaluationReport schema_version")
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if not isinstance(self.phase, EvaluationPhase):
            raise ValueError("invalid evaluation phase")
        if not isinstance(self.case_type, CaseType):
            raise ValueError("invalid case type")
        object.__setattr__(
            self,
            "generated_at",
            require_utc(self.generated_at, field_name="generated_at"),
        )
        if type(self.case_count) is not int or self.case_count < 0:
            raise ValueError("case_count must be a non-negative integer")
        if type(self.minimum_samples) is not int or self.minimum_samples < 1:
            raise ValueError("minimum_samples must be a positive integer")
        if not self.arms:
            raise ValueError("an evaluation report requires at least one arm")
        if self.automatic_promotion is not False:
            raise ValueError("bake-off results can never promote automatically")
        if self.trade_authority_changed is not False:
            raise ValueError("bake-off results can never change trade authority")
        if self.measurement_only is not True:
            raise ValueError("bake-off evidence is measurement only")

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "phase": self.phase,
            "case_type": self.case_type,
            "generated_at": self.generated_at,
            "case_count": self.case_count,
            "minimum_samples": self.minimum_samples,
            "metric_definitions_version": self.metric_definitions_version,
            "committee_signal_rule_version": self.committee_signal_rule_version,
            "arms": tuple(
                {
                    "arm_id": arm.arm_id,
                    "kind": arm.kind,
                    "cases": arm.cases,
                    "answered": arm.answered,
                    "adequacy": arm.adequacy,
                    "metrics": tuple(
                        (metric.name, metric.value) for metric in arm.metrics()
                    ),
                }
                for arm in self.arms
            ),
        }

    @property
    def report_id(self) -> str:
        return stable_hash(REPORT_IDENTITY_DOMAIN, self.identity_payload())

    @property
    def adequate_arms(self) -> tuple[ArmEvaluation, ...]:
        return tuple(arm for arm in self.arms if arm.is_adequate)

    def arm(self, arm_id: str) -> ArmEvaluation:
        for arm in self.arms:
            if arm.arm_id == arm_id:
                return arm
        raise KeyError(arm_id)


def directional_call(outcome: ProviderCallOutcome) -> DirectionalCall:
    """Map one seat outcome to a research call.

    A failure, an unavailable seat, a budget skip, and an abstention are each
    their own kind of nothing: none is a positive or a negative call.
    """
    if outcome.status is not ObservationStatus.COMPLETED and (
        outcome.status is not ObservationStatus.DUPLICATE_OK
    ):
        return DirectionalCall.UNAVAILABLE
    opinion = outcome.opinion
    if opinion is None:
        return DirectionalCall.UNAVAILABLE
    if opinion.abstained:
        return DirectionalCall.ABSTAIN
    if opinion.assessment is DirectionalAssessment.SUPPORTIVE:
        return DirectionalCall.POSITIVE
    if opinion.assessment is DirectionalAssessment.OPPOSING:
        return DirectionalCall.NEGATIVE
    # NEUTRAL and UNCERTAIN are neither supportive nor opposing.
    return DirectionalCall.ABSTAIN


def committee_research_signal(seats: Sequence[ProviderCallOutcome]) -> DirectionalCall:
    """A documented research aggregate over the answered seats of one case.

    This is *not* a decision rule and confers no authority. It exists so the
    bake-off can compare a committee-level research signal against individual
    models and the existing deterministic baseline. Abstentions and failures are
    excluded rather than counted as dissent, so a partial committee cannot look
    like a directional answer.
    """
    answers = [
        directional_call(seat)
        for seat in seats
        if directional_call(seat)
        in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE)
    ]
    if len(answers) < COMMITTEE_SIGNAL_MIN_ANSWERED_SEATS:
        return DirectionalCall.ABSTAIN
    supportive = sum(1 for call in answers if call is DirectionalCall.POSITIVE)
    share = (supportive * 10_000) // len(answers)
    if share >= COMMITTEE_SIGNAL_SUPPORTIVE_SHARE_BASIS_POINTS:
        return DirectionalCall.POSITIVE
    if share <= 10_000 - COMMITTEE_SIGNAL_SUPPORTIVE_SHARE_BASIS_POINTS:
        return DirectionalCall.NEGATIVE
    return DirectionalCall.ABSTAIN


@dataclass
class _ArmAccumulator:
    arm_id: str
    kind: ArmKind
    label: str
    provider_family: ProviderFamily | None = None
    model: str | None = None
    calls: dict[str, DirectionalCall] = field(default_factory=dict)
    attempts: int = 0
    schema_valid: int = 0
    abstentions: int = 0
    failures: int = 0
    unavailable: int = 0
    skipped_budget: int = 0
    input_tokens: int = 0
    input_tokens_seen: bool = False
    output_tokens: int = 0
    output_tokens_seen: bool = False
    latencies: list[int] = field(default_factory=list)
    costs: list[int | None] = field(default_factory=list)


def _record(
    accumulator: _ArmAccumulator,
    *,
    case_id: str,
    call: DirectionalCall,
    status: ObservationStatus | None = None,
    schema_valid: bool = False,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_micros: int | None = None,
    cost_microunits: int | None = None,
) -> None:
    accumulator.attempts += 1
    accumulator.calls[case_id] = call
    if schema_valid:
        accumulator.schema_valid += 1
    if call is DirectionalCall.ABSTAIN:
        accumulator.abstentions += 1
    if call is DirectionalCall.UNAVAILABLE:
        accumulator.unavailable += 1
    if status in (ObservationStatus.FAILED, ObservationStatus.INVALID):
        accumulator.failures += 1
    if status is ObservationStatus.SKIPPED_BUDGET:
        accumulator.skipped_budget += 1
    if input_tokens is not None:
        accumulator.input_tokens += input_tokens
        accumulator.input_tokens_seen = True
    if output_tokens is not None:
        accumulator.output_tokens += output_tokens
        accumulator.output_tokens_seen = True
    if latency_micros is not None:
        accumulator.latencies.append(latency_micros)
    accumulator.costs.append(cost_microunits)


def _accumulate_seat(accumulator: _ArmAccumulator, seat: ProviderCallOutcome) -> None:
    _record(
        accumulator,
        case_id=seat.case_id,
        call=directional_call(seat),
        status=seat.status,
        schema_valid=seat.status
        in (ObservationStatus.COMPLETED, ObservationStatus.DUPLICATE_OK),
        input_tokens=seat.input_tokens,
        output_tokens=seat.output_tokens,
        latency_micros=seat.latency_micros,
        cost_microunits=seat.estimated_cost_microunits,
    )


def _classification_pairs(
    accumulator: _ArmAccumulator, resolved: Mapping[str, ResolvedOutcome]
) -> list[tuple[bool, bool]]:
    pairs: list[tuple[bool, bool]] = []
    for case_id, outcome in resolved.items():
        call = accumulator.calls.get(case_id)
        if call in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE):
            pairs.append((call is DirectionalCall.POSITIVE, outcome.positive))
    return pairs


def _probability_samples(
    probabilities: Mapping[tuple[str, str], Decimal],
    *,
    arm_id: str,
    resolved: Mapping[str, ResolvedOutcome],
) -> list[ProbabilitySample]:
    """Explicit probabilistic forecasts only. Ordinal confidence never qualifies."""
    samples: list[ProbabilitySample] = []
    for (case_id, probability_arm), probability in probabilities.items():
        if probability_arm != arm_id:
            continue
        outcome = resolved.get(case_id)
        if outcome is None:
            continue
        samples.append(
            ProbabilitySample(
                predicted_probability=probability, observed=outcome.positive
            )
        )
    return samples


def _finalize_arm(
    accumulator: _ArmAccumulator,
    *,
    resolved: Mapping[str, ResolvedOutcome],
    probabilities: Mapping[tuple[str, str], Decimal],
    replays: Sequence[ReplayComparison],
    case_count: int,
    minimum_samples: int,
) -> ArmEvaluation:
    pairs = _classification_pairs(accumulator, resolved)
    classification = classification_report(pairs)
    probability_samples = _probability_samples(
        probabilities, arm_id=accumulator.arm_id, resolved=resolved
    )
    calibration = calibration_report(probability_samples)

    relevant_replays = [
        replay
        for replay in replays
        if replay.provider_family is accumulator.provider_family
        and replay.model == accumulator.model
    ]
    consistency = repeatability_metric(
        agreed=sum(1 for replay in relevant_replays if replay.reproduced),
        compared=len(relevant_replays),
    )

    directional = sum(
        1
        for call in accumulator.calls.values()
        if call in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE)
    )
    responsive = directional + accumulator.abstentions
    adequate = len(pairs) >= minimum_samples
    return ArmEvaluation(
        arm_id=accumulator.arm_id,
        kind=accumulator.kind,
        label=accumulator.label,
        provider_family=accumulator.provider_family,
        model=accumulator.model,
        cases=case_count,
        answered=directional,
        abstentions=accumulator.abstentions,
        schema_valid=accumulator.schema_valid,
        failures=accumulator.failures,
        unavailable=accumulator.unavailable,
        skipped_budget=accumulator.skipped_budget,
        input_tokens=(
            accumulator.input_tokens if accumulator.input_tokens_seen else None
        ),
        output_tokens=(
            accumulator.output_tokens if accumulator.output_tokens_seen else None
        ),
        adequacy=(
            ADEQUACY_ADEQUATE if adequate else ADEQUACY_INSUFFICIENT_SAMPLE
        ),
        coverage=rate_metric(
            "coverage", numerator=directional, denominator=case_count
        ),
        response_validity=rate_metric(
            "response_validity",
            numerator=accumulator.schema_valid,
            denominator=accumulator.attempts,
        ),
        abstention_rate=rate_metric(
            "abstention_rate",
            numerator=accumulator.abstentions,
            denominator=responsive,
        ),
        failure_rate=rate_metric(
            "failure_rate",
            numerator=accumulator.failures,
            denominator=accumulator.attempts,
        ),
        schema_compliance=rate_metric(
            "schema_compliance",
            numerator=accumulator.schema_valid,
            denominator=accumulator.attempts,
        ),
        consistency=consistency,
        precision=classification.precision,
        recall=classification.recall,
        f1=classification.f1,
        accuracy=classification.accuracy,
        brier_score=brier_score(probability_samples),
        log_loss=log_loss(probability_samples),
        expected_calibration_error=calibration.expected_calibration_error,
        calibration_bins=calibration.bins,
        confusion=(classification.matrix if classification.matrix.total else None),
        latency=latency_distribution(accumulator.latencies),
        cost=cost_aggregate(accumulator.costs),
    )


def _validate_bake_off_inputs(
    observations: Sequence[CaseObservation],
    *,
    resolved_outcomes: Sequence[ResolvedOutcome],
    deterministic_baseline: Sequence[BaselineCall],
    probability_forecasts: Sequence[ProbabilityForecast],
) -> CaseType:
    """Validate the shared case set and return its single case type."""
    if not observations:
        raise ValueError("a bake-off requires at least one case observation")
    case_types = {observation.case_type for observation in observations}
    if len(case_types) != 1:
        raise ValueError(
            "a bake-off compares arms over a single case type; "
            "mixed case types must be evaluated separately"
        )
    known_cases = {observation.case_id for observation in observations}
    for label, case_id in (
        *(
            ("resolved outcome", item.case_id)
            for item in resolved_outcomes
        ),
        *(
            ("baseline call", item.case_id)
            for item in deterministic_baseline
        ),
        *(
            ("forecast", item.case_id)
            for item in probability_forecasts
        ),
    ):
        if case_id not in known_cases:
            raise ValueError(f"{label} for unknown case: {case_id}")
    return next(iter(case_types))


def _accumulate_independent_seats(
    arm_for, observation: CaseObservation
) -> None:
    """Accumulate one case's individual-model arms and the committee signal."""
    for seat in observation.seats:
        key = f"model:{seat.provider_family.value}:{seat.requested_model}"
        _accumulate_seat(
            arm_for(
                key,
                kind=ArmKind.INDIVIDUAL_MODEL,
                label=f"{seat.provider_family.value}/{seat.requested_model}",
                provider_family=seat.provider_family,
                model=seat.requested_model,
            ),
            seat,
        )

    signal_call = committee_research_signal(observation.seats)
    signal = arm_for(
        "committee:research-signal",
        kind=ArmKind.COMMITTEE_SIGNAL,
        label="committee research signal",
    )
    # The signal arm's cost is the full cost of producing the committee signal,
    # so the incremental-information question can be asked directly.
    _record(
        signal,
        case_id=observation.case_id,
        call=signal_call,
        status=None,
        schema_valid=signal_call
        in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE),
        input_tokens=_sum_tokens(observation.seats, "input_tokens"),
        output_tokens=_sum_tokens(observation.seats, "output_tokens"),
        cost_microunits=_sum_costs(observation.seats),
    )


def _accumulate_baseline_arm(arm_for, deterministic_baseline) -> None:
    if not deterministic_baseline:
        return
    baseline = arm_for(
        "baseline:deterministic",
        kind=ArmKind.DETERMINISTIC_BASELINE,
        label="deterministic O'Pip baseline",
    )
    for call in deterministic_baseline:
        _record(
            baseline,
            case_id=call.case_id,
            call=(
                DirectionalCall.POSITIVE
                if call.positive
                else DirectionalCall.NEGATIVE
            ),
            status=None,
            schema_valid=True,
            cost_microunits=None,
        )


def _accumulate_null_arm(arm_for, resolved) -> None:
    if not resolved:
        return
    null_arm = arm_for(
        "baseline:null",
        kind=ArmKind.NULL_BASELINE,
        label="always-positive null baseline",
    )
    for case_id in resolved:
        _record(
            null_arm,
            case_id=case_id,
            call=DirectionalCall.POSITIVE,
            status=None,
            schema_valid=True,
            cost_microunits=None,
        )


def evaluate_model_bake_off(
    observations: Sequence[CaseObservation],
    *,
    experiment_id: str,
    provenance: Provenance,
    generated_at: datetime,
    phase: EvaluationPhase = EvaluationPhase.RETROSPECTIVE,
    resolved_outcomes: Sequence[ResolvedOutcome] = (),
    deterministic_baseline: Sequence[BaselineCall] = (),
    replays: Sequence[ReplayComparison] = (),
    probability_forecasts: Sequence[ProbabilityForecast] = (),
    minimum_samples: int = MIN_EVALUATION_SAMPLES,
) -> EvaluationReport:
    """Compare arms over a shared case set and return a research report.

    Retrospective and prospective evidence must be evaluated separately: the
    caller selects one ``phase`` and one case type per report.
    """
    case_type = _validate_bake_off_inputs(
        observations,
        resolved_outcomes=resolved_outcomes,
        deterministic_baseline=deterministic_baseline,
        probability_forecasts=probability_forecasts,
    )
    resolved = {outcome.case_id: outcome for outcome in resolved_outcomes}
    probabilities = {
        (forecast.case_id, forecast.arm_id): forecast.probability
        for forecast in probability_forecasts
    }

    accumulators: dict[str, _ArmAccumulator] = {}

    def arm_for(key: str, **kwargs) -> _ArmAccumulator:
        if key not in accumulators:
            accumulators[key] = _ArmAccumulator(arm_id=key, **kwargs)
        return accumulators[key]

    for observation in observations:
        _accumulate_independent_seats(arm_for, observation)
    _accumulate_baseline_arm(arm_for, deterministic_baseline)
    _accumulate_null_arm(arm_for, resolved)

    arms = tuple(
        _finalize_arm(
            accumulators[key],
            resolved=resolved,
            probabilities=probabilities,
            replays=replays,
            case_count=len(observations),
            minimum_samples=minimum_samples,
        )
        for key in sorted(accumulators)
    )
    return EvaluationReport(
        experiment_id=experiment_id,
        phase=phase,
        case_type=case_type,
        generated_at=generated_at,
        case_count=len(observations),
        minimum_samples=minimum_samples,
        arms=arms,
        provenance=provenance,
    )


def _sum_tokens(seats: Sequence[ProviderCallOutcome], attribute: str) -> int | None:
    values = [getattr(seat, attribute) for seat in seats]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _sum_costs(seats: Sequence[ProviderCallOutcome]) -> int | None:
    values = [seat.estimated_cost_microunits for seat in seats]
    if any(value is None for value in values):
        # One unknown seat cost makes the aggregate unknown, never zero.
        return None
    return sum(value for value in values if value is not None)


def ordinal_confidence_is_not_a_probability() -> dict[str, str]:
    """Document the calibration prohibition for callers and tests."""
    return {
        "confidence_semantics": "ORDINAL_0_100_SELF_REPORT",
        "treated_as_probability": "false",
        "reason": REASON_NOT_DEFINED_FOR_CASE_TYPE,
        "minimum_metric_sample": str(MIN_METRIC_SAMPLE),
    }


__all__ = [
    "ADEQUACY_ADEQUATE",
    "ADEQUACY_INSUFFICIENT_SAMPLE",
    "ArmEvaluation",
    "ArmKind",
    "BaselineCall",
    "COMMITTEE_SIGNAL_MIN_ANSWERED_SEATS",
    "COMMITTEE_SIGNAL_RULE_VERSION",
    "CaseObservation",
    "DirectionalCall",
    "EvaluationReport",
    "METRIC_DEFINITIONS_VERSION",
    "MIN_EVALUATION_SAMPLES",
    "ProbabilityForecast",
    "ReplayComparison",
    "ResolvedOutcome",
    "committee_research_signal",
    "directional_call",
    "evaluate_model_bake_off",
    "ordinal_confidence_is_not_a_probability",
]
