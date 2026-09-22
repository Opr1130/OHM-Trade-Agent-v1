"""Learning, disagreement and attribution.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module answers research questions about the committee and writes them down
as evidence:

* did the committee add information beyond the existing deterministic baseline?
* which provider contributed information that was genuinely independent of it?
* was an apparent edge merely agreement with the baseline?
* did disagreement concentrate the cases the committee got wrong?
* which case classes does a provider handle poorly?
* does additional model cost buy measurable incremental information?
* are results stable over the observed period?

Three prohibitions are structural, not documentary:

* a provider failure is not a vote, an abstention is not a negative vote, and a
  missing opinion is never agreement;
* dissent is preserved in a disagreement matrix rather than averaged away;
* nothing here can promote a model, alter a threshold, or change trading
  authority. If a result is ever to influence production, that belongs behind a
  separate, future, human-governed promotion gate - never to this module.

Confidence calibration is reported as *not applicable*: a seat's confidence is an
ordinal 0-100 self-report, and rescoring it as a probability here would repeat
exactly the conflation the repository's statistical protocol forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Sequence

from app.opip.committee.contracts import (
    CaseType,
    EvaluationPhase,
    EvidenceSufficiency,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFamily,
)
from app.opip.committee.evaluation import DirectionalCall, directional_call
from app.opip.committee.metrics import (
    EvaluationMetric,
    REASON_NOT_DEFINED_FOR_CASE_TYPE,
    rate_metric,
)
from app.opip.decision_intelligence.identity import Provenance
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

ATTRIBUTION_REPORT_SCHEMA_VERSION = 1
ATTRIBUTION_REPORT_IDENTITY_DOMAIN = "COMMITTEE-ATTRIBUTION"

#: Below this many scored cases an attribution claim is not supportable.
MIN_ATTRIBUTION_SAMPLES = 30

#: Ordinal-point spread across agreeing seats that counts as a confidence
#: disagreement. Documented so the classification is reproducible.
CONFIDENCE_SPREAD_THRESHOLD = 30

#: Version stamp for the attribution method itself.
ATTRIBUTION_METHOD_VERSION = "committee-attribution-v1"


class DisagreementKind(str, Enum):
    """The ways a committee case can be unanimous or contested.

    These are not mutually exclusive: a case can hold a provider failure and a
    single-model dissent at the same time, and both are recorded. The ``primary``
    classification is the most specific structural reading; ``conditions`` keeps
    every condition observed.
    """

    UNANIMOUS_AGREEMENT = "UNANIMOUS_AGREEMENT"
    DIRECTIONAL_AGREEMENT_CONFIDENCE_DISAGREEMENT = (
        "DIRECTIONAL_AGREEMENT_CONFIDENCE_DISAGREEMENT"
    )
    EVIDENCE_DISAGREEMENT = "EVIDENCE_DISAGREEMENT"
    ASSUMPTION_DISAGREEMENT = "ASSUMPTION_DISAGREEMENT"
    SINGLE_MODEL_DISSENT = "SINGLE_MODEL_DISSENT"
    SPLIT_DECISION = "SPLIT_DECISION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    PROVIDER_FAILURE_PRESENT = "PROVIDER_FAILURE_PRESENT"
    SCHEMA_INVALID_PRESENT = "SCHEMA_INVALID_PRESENT"
    SEAT_UNAVAILABLE_PRESENT = "SEAT_UNAVAILABLE_PRESENT"


class BaselineRelation(str, Enum):
    """How one provider's call related to the deterministic baseline."""

    AGREES_WITH_BASELINE = "AGREES_WITH_BASELINE"
    DISAGREES_WITH_BASELINE = "DISAGREES_WITH_BASELINE"
    BASELINE_UNAVAILABLE = "BASELINE_UNAVAILABLE"
    NOT_DIRECTIONAL = "NOT_DIRECTIONAL"


def baseline_relation(
    call: DirectionalCall, *, baseline_positive: bool | None
) -> BaselineRelation:
    """Classify one call's relation to the baseline. Not a judgement of merit."""
    if call not in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE):
        return BaselineRelation.NOT_DIRECTIONAL
    if baseline_positive is None:
        return BaselineRelation.BASELINE_UNAVAILABLE
    if (call is DirectionalCall.POSITIVE) == baseline_positive:
        return BaselineRelation.AGREES_WITH_BASELINE
    return BaselineRelation.DISAGREES_WITH_BASELINE


@dataclass(frozen=True)
class AttributionCase:
    """One case: its committee seats, the baseline call, and the outcome."""

    case_id: str
    case_type: CaseType
    decided_at: datetime
    seats: tuple[ProviderCallOutcome, ...]
    baseline_positive: bool | None = None
    observed_positive: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id is required")
        if not isinstance(self.case_type, CaseType):
            raise ValueError("invalid case_type")
        object.__setattr__(
            self,
            "decided_at",
            require_utc(self.decided_at, field_name="decided_at"),
        )
        if not isinstance(self.seats, tuple):
            raise ValueError("seats must be a tuple")
        for seat in self.seats:
            if not isinstance(seat, ProviderCallOutcome):
                raise ValueError("seats must be ProviderCallOutcome")
            if seat.case_id != self.case_id:
                raise ValueError("every seat must belong to its case")
        for field_name in ("baseline_positive", "observed_positive"):
            value = getattr(self, field_name)
            if value is not None and type(value) is not bool:
                raise ValueError(f"{field_name} must be a boolean or null")


@dataclass(frozen=True)
class SeatVerdict:
    """One seat's call, correctness, and stated reasoning surface."""

    provider_family: ProviderFamily
    model: str
    call: DirectionalCall
    correct: bool | None
    confidence: int | None
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]
    major_assumptions: tuple[str, ...]
    evidence_sufficiency: EvidenceSufficiency | None


@dataclass(frozen=True)
class DisagreementMatrix:
    """A normalised reading of one case's dissent, not an average."""

    case_id: str
    verdicts: tuple[SeatVerdict, ...]
    primary: DisagreementKind
    conditions: tuple[DisagreementKind, ...]
    supportive_seats: int
    opposing_seats: int
    abstained_seats: int
    failed_seats: int
    invalid_seats: int
    unavailable_seats: int
    contested_evidence_refs: tuple[str, ...]
    max_confidence_spread: int | None

    @property
    def answered_seats(self) -> int:
        return self.supportive_seats + self.opposing_seats

    @property
    def is_contested(self) -> bool:
        return self.primary in (
            DisagreementKind.SPLIT_DECISION,
            DisagreementKind.SINGLE_MODEL_DISSENT,
        )


@dataclass(frozen=True)
class CaseTypeAccuracy:
    """Accuracy for one provider within one case class."""

    case_type: CaseType
    scored: int
    correct: int
    accuracy: EvaluationMetric


@dataclass(frozen=True)
class ProviderAttribution:
    """One provider's evidence-based contribution, never a verdict on it."""

    provider_family: ProviderFamily
    model: str
    scored_cases: int
    correct_cases: int
    abstained_cases: int
    failed_cases: int
    accuracy: EvaluationMetric
    baseline_agreements: int
    baseline_disagreements: int
    accuracy_when_agreeing_with_baseline: EvaluationMetric
    accuracy_when_disagreeing_with_baseline: EvaluationMetric
    independent_incremental_correct: int
    accuracy_when_contested: EvaluationMetric
    accuracy_by_case_type: tuple[CaseTypeAccuracy, ...]
    known_cost_microunits: int | None
    unknown_cost_samples: int

    @property
    def adequacy_note(self) -> str:
        """An explicit statement when the sample cannot support a claim."""
        return (
            "adequate"
            if self.scored_cases >= MIN_ATTRIBUTION_SAMPLES
            else f"INSUFFICIENT_SAMPLE ({self.scored_cases} of {MIN_ATTRIBUTION_SAMPLES})"
        )


@dataclass(frozen=True)
class CommitteeIncrement:
    """Did the committee add information beyond the deterministic baseline?"""

    baseline_scored: int
    baseline_correct: int
    committee_scored: int
    committee_correct: int
    both_correct: int
    both_wrong: int
    only_committee_correct: int
    only_baseline_correct: int
    committee_accuracy: EvaluationMetric
    baseline_accuracy: EvaluationMetric
    incremental_accuracy: EvaluationMetric

    @property
    def added_information(self) -> bool | None:
        """Whether the committee beat the baseline, or ``None`` if unsupported."""
        if self.committee_scored < MIN_ATTRIBUTION_SAMPLES:
            return None
        if not self.incremental_accuracy.applicable:
            return None
        return (self.incremental_accuracy.decimal_value or 0) > 0


@dataclass(frozen=True)
class DisagreementSummary:
    """How often a disagreement condition was observed across the case set."""

    kind: DisagreementKind
    cases: int
    share: EvaluationMetric


@dataclass(frozen=True)
class WindowAccuracy:
    """Accuracy over one chronological window of the observed period."""

    label: str
    cases: int
    scored: int
    correct: int
    accuracy: EvaluationMetric


@dataclass(frozen=True)
class AttributionReport:
    """Durable research evidence about the committee. It promotes nothing."""

    experiment_id: str
    phase: EvaluationPhase
    generated_at: datetime
    case_count: int
    minimum_samples: int
    providers: tuple[ProviderAttribution, ...]
    disagreements: tuple[DisagreementSummary, ...]
    committee_increment: CommitteeIncrement
    contested_case_accuracy: EvaluationMetric
    unanimous_case_accuracy: EvaluationMetric
    chronological_stability: tuple[WindowAccuracy, ...]
    incremental_cost_microunits: int | None
    calibration: EvaluationMetric
    provenance: Provenance
    attribution_method_version: str = ATTRIBUTION_METHOD_VERSION
    advisory_only: bool = True
    measurement_only: bool = True
    automatic_promotion: bool = False
    trade_authority_changed: bool = False
    schema_version: int = ATTRIBUTION_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != (
            ATTRIBUTION_REPORT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported AttributionReport schema_version")
        if self.automatic_promotion is not False:
            raise ValueError("attribution can never promote automatically")
        if self.trade_authority_changed is not False:
            raise ValueError("attribution can never change trade authority")
        if self.measurement_only is not True or self.advisory_only is not True:
            raise ValueError("attribution is advisory measurement evidence only")
        if not isinstance(self.phase, EvaluationPhase):
            raise ValueError("invalid evaluation phase")
        object.__setattr__(
            self,
            "generated_at",
            require_utc(self.generated_at, field_name="generated_at"),
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "phase": self.phase,
            "generated_at": self.generated_at,
            "case_count": self.case_count,
            "minimum_samples": self.minimum_samples,
            "attribution_method_version": self.attribution_method_version,
            "providers": tuple(
                (
                    provider.provider_family,
                    provider.model,
                    provider.scored_cases,
                    provider.correct_cases,
                    provider.baseline_agreements,
                    provider.baseline_disagreements,
                    provider.independent_incremental_correct,
                )
                for provider in self.providers
            ),
            "disagreements": tuple(
                (item.kind, item.cases) for item in self.disagreements
            ),
            "committee_increment": (
                self.committee_increment.only_committee_correct,
                self.committee_increment.only_baseline_correct,
                self.committee_increment.committee_scored,
            ),
        }

    @property
    def attribution_id(self) -> str:
        return stable_hash(
            ATTRIBUTION_REPORT_IDENTITY_DOMAIN, self.identity_payload()
        )


def _seat_verdict(
    seat: ProviderCallOutcome, *, observed_positive: bool | None
) -> SeatVerdict:
    call = directional_call(seat)
    opinion = seat.opinion
    correct: bool | None = None
    if (
        observed_positive is not None
        and call in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE)
    ):
        correct = (call is DirectionalCall.POSITIVE) == observed_positive
    return SeatVerdict(
        provider_family=seat.provider_family,
        model=seat.requested_model,
        call=call,
        correct=correct,
        confidence=None if opinion is None else opinion.confidence,
        supporting_evidence_refs=(
            () if opinion is None else opinion.supporting_evidence_refs
        ),
        contradicting_evidence_refs=(
            () if opinion is None else opinion.contradicting_evidence_refs
        ),
        major_assumptions=() if opinion is None else opinion.major_assumptions,
        evidence_sufficiency=None if opinion is None else opinion.evidence_sufficiency,
    )


def _call_counts(verdicts: tuple[SeatVerdict, ...]) -> tuple[int, int, int, int]:
    def _count(call: DirectionalCall) -> int:
        return sum(1 for item in verdicts if item.call is call)

    return (
        _count(DirectionalCall.POSITIVE),
        _count(DirectionalCall.NEGATIVE),
        _count(DirectionalCall.ABSTAIN),
        _count(DirectionalCall.UNAVAILABLE),
    )


def _directional(verdicts: tuple[SeatVerdict, ...]) -> list[SeatVerdict]:
    return [
        item
        for item in verdicts
        if item.call in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE)
    ]


def _contested_refs(directional: list[SeatVerdict]) -> tuple[str, ...]:
    supported: set[str] = set()
    contradicted: set[str] = set()
    for item in directional:
        supported |= set(item.supporting_evidence_refs)
        contradicted |= set(item.contradicting_evidence_refs)
    return tuple(sorted(supported & contradicted))


def _assumption_sets(directional: list[SeatVerdict]) -> set[frozenset[str]]:
    return {
        frozenset(item.major_assumptions)
        for item in directional
        if item.major_assumptions
    }


def _confidence_spread(directional: list[SeatVerdict]) -> int | None:
    confidences = [
        item.confidence for item in directional if item.confidence is not None
    ]
    if len(confidences) < 2:
        return None
    return max(confidences) - min(confidences)


def _primary_disagreement(
    *,
    directional: list[SeatVerdict],
    supportive: int,
    opposing: int,
    spread: int | None,
    conditions: set[DisagreementKind],
) -> DisagreementKind:
    """The most specific structural reading of the case."""
    if not directional:
        conditions.add(DisagreementKind.INSUFFICIENT_EVIDENCE)
        return DisagreementKind.INSUFFICIENT_EVIDENCE
    if supportive and opposing:
        # A single dissent needs a majority to dissent from. A one-against-one
        # reading is a split, not one model standing alone against the rest.
        if min(supportive, opposing) == 1 and max(supportive, opposing) >= 2:
            primary = DisagreementKind.SINGLE_MODEL_DISSENT
        else:
            primary = DisagreementKind.SPLIT_DECISION
        conditions.add(primary)
        return primary
    conditions.add(DisagreementKind.UNANIMOUS_AGREEMENT)
    if spread is not None and spread > CONFIDENCE_SPREAD_THRESHOLD:
        conditions.add(DisagreementKind.DIRECTIONAL_AGREEMENT_CONFIDENCE_DISAGREEMENT)
        return DisagreementKind.DIRECTIONAL_AGREEMENT_CONFIDENCE_DISAGREEMENT
    for candidate in (
        DisagreementKind.EVIDENCE_DISAGREEMENT,
        DisagreementKind.ASSUMPTION_DISAGREEMENT,
    ):
        if candidate in conditions:
            return candidate
    return DisagreementKind.UNANIMOUS_AGREEMENT


def build_disagreement_matrix(case: AttributionCase) -> DisagreementMatrix:
    """Read one case's dissent without averaging it away."""
    verdicts = tuple(
        _seat_verdict(seat, observed_positive=case.observed_positive)
        for seat in case.seats
    )
    supportive, opposing, abstained, unavailable = _call_counts(verdicts)
    failed = sum(
        1
        for seat in case.seats
        if seat.status in (ObservationStatus.FAILED, ObservationStatus.INVALID)
    )
    invalid = sum(
        1 for seat in case.seats if seat.status is ObservationStatus.INVALID
    )
    conditions: set[DisagreementKind] = set()
    for count, kind in (
        (failed, DisagreementKind.PROVIDER_FAILURE_PRESENT),
        (invalid, DisagreementKind.SCHEMA_INVALID_PRESENT),
        (unavailable, DisagreementKind.SEAT_UNAVAILABLE_PRESENT),
    ):
        if count:
            conditions.add(kind)

    directional = _directional(verdicts)
    contested = _contested_refs(directional)
    if contested:
        conditions.add(DisagreementKind.EVIDENCE_DISAGREEMENT)
    if len(_assumption_sets(directional)) > 1:
        conditions.add(DisagreementKind.ASSUMPTION_DISAGREEMENT)

    spread = _confidence_spread(directional)
    primary = _primary_disagreement(
        directional=directional,
        supportive=supportive,
        opposing=opposing,
        spread=spread,
        conditions=conditions,
    )

    return DisagreementMatrix(
        case_id=case.case_id,
        verdicts=verdicts,
        primary=primary,
        conditions=tuple(sorted(conditions, key=lambda item: item.value)),
        supportive_seats=supportive,
        opposing_seats=opposing,
        abstained_seats=abstained,
        failed_seats=failed,
        invalid_seats=invalid,
        unavailable_seats=unavailable,
        contested_evidence_refs=contested,
        max_confidence_spread=spread,
    )


def committee_research_call(case: AttributionCase) -> DirectionalCall:
    """The committee's own directional reading, via the 2B research rule."""
    from app.opip.committee.evaluation import committee_research_signal

    return committee_research_signal(case.seats)


def _accuracy(scored: int, correct: int, *, name: str) -> EvaluationMetric:
    return rate_metric(name, numerator=correct, denominator=scored)


def build_attribution_report(
    cases: Sequence[AttributionCase],
    *,
    experiment_id: str,
    provenance: Provenance,
    generated_at: datetime,
    phase: EvaluationPhase = EvaluationPhase.PROSPECTIVE,
    minimum_samples: int = MIN_ATTRIBUTION_SAMPLES,
) -> AttributionReport:
    """Build evidence-based attribution over a shared case set."""
    if not cases:
        raise ValueError("attribution requires at least one case")
    case_types = {case.case_type for case in cases}
    if len(case_types) != 1:
        raise ValueError(
            "attribution compares a single case type; evaluate mixed types separately"
        )
    ordered = sorted(cases, key=lambda item: (item.decided_at, item.case_id))
    matrices = {case.case_id: build_disagreement_matrix(case) for case in ordered}

    disagreement_counts: dict[DisagreementKind, int] = {}
    for matrix in matrices.values():
        for condition in matrix.conditions:
            disagreement_counts[condition] = disagreement_counts.get(condition, 0) + 1
    disagreements = tuple(
        DisagreementSummary(
            kind=kind,
            cases=count,
            share=rate_metric(
                kind.value, numerator=count, denominator=len(ordered)
            ),
        )
        for kind, count in sorted(disagreement_counts.items(), key=lambda item: item[0].value)
    )

    providers: list[ProviderAttribution] = []
    provider_keys = sorted(
        {
            (seat.provider_family, seat.requested_model)
            for case in ordered
            for seat in case.seats
        },
        key=lambda item: (item[0].value, item[1]),
    )
    for family, model in provider_keys:
        providers.append(
            _provider_attribution(
                ordered,
                matrices,
                family=family,
                model=model,
            )
        )

    increment = _committee_increment(ordered)
    contested = [
        case
        for case in ordered
        if matrices[case.case_id].is_contested
    ]
    unanimous = [
        case
        for case in ordered
        if matrices[case.case_id].primary
        in (
            DisagreementKind.UNANIMOUS_AGREEMENT,
            DisagreementKind.DIRECTIONAL_AGREEMENT_CONFIDENCE_DISAGREEMENT,
        )
    ]
    incremental_cost = _incremental_cost(
        providers, increment.only_committee_correct, minimum_samples=minimum_samples
    )
    return AttributionReport(
        experiment_id=experiment_id,
        phase=phase,
        generated_at=generated_at,
        case_count=len(ordered),
        minimum_samples=minimum_samples,
        providers=tuple(providers),
        disagreements=disagreements,
        committee_increment=increment,
        contested_case_accuracy=_committee_accuracy(contested),
        unanimous_case_accuracy=_committee_accuracy(unanimous),
        chronological_stability=_chronological_windows(ordered),
        incremental_cost_microunits=incremental_cost,
        calibration=_calibration_metric(),
        provenance=provenance,
    )


def _committee_accuracy(cases: Sequence[AttributionCase]) -> EvaluationMetric:
    scored = 0
    correct = 0
    for case in cases:
        if case.observed_positive is None:
            continue
        call = committee_research_call(case)
        if call in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE):
            scored += 1
            if (call is DirectionalCall.POSITIVE) == case.observed_positive:
                correct += 1
    return _accuracy(scored, correct, name="committee_accuracy")


@dataclass
class _ProviderAccumulator:
    """Mutable tally for one provider while its cases are walked."""

    scored: int = 0
    correct: int = 0
    abstained: int = 0
    failed: int = 0
    agree_cases: int = 0
    agree_correct: int = 0
    disagree_cases: int = 0
    disagree_correct: int = 0
    incremental: int = 0
    contested_scored: int = 0
    contested_correct: int = 0
    known_cost_microunits: int | None = 0
    unknown_cost_samples: int = 0
    by_type: dict[CaseType, list[int]] = field(default_factory=dict)

    def add_cost(self, cost: int | None) -> None:
        """Track spend, staying unknown if any single sample is unknown."""
        if cost is None:
            self.unknown_cost_samples += 1
            self.known_cost_microunits = None
        elif self.known_cost_microunits is not None:
            self.known_cost_microunits += cost

    def add_call(self, verdict: SeatVerdict, seat: ProviderCallOutcome) -> None:
        if verdict.call is DirectionalCall.ABSTAIN:
            self.abstained += 1
        if seat.status in (ObservationStatus.FAILED, ObservationStatus.INVALID):
            self.failed += 1

    def add_scored(self, case_type: CaseType, *, correct: bool) -> None:
        self.scored += 1
        counts = self.by_type.setdefault(case_type, [0, 0])
        counts[0] += 1
        if correct:
            self.correct += 1
            counts[1] += 1

    def add_contested(self, *, correct: bool) -> None:
        self.contested_scored += 1
        if correct:
            self.contested_correct += 1

    def add_baseline_relation(self, case: AttributionCase, verdict: SeatVerdict) -> None:
        """Compare against the deterministic baseline, or record unavailability."""
        if case.baseline_positive is None:
            return
        agrees = (verdict.call is DirectionalCall.POSITIVE) == case.baseline_positive
        if agrees:
            self.agree_cases += 1
            if verdict.correct:
                self.agree_correct += 1
            return
        self.disagree_cases += 1
        if verdict.correct:
            self.disagree_correct += 1
            # Independent and right where the baseline was wrong: the only
            # configuration that demonstrates added information.
            if case.baseline_positive != case.observed_positive:
                self.incremental += 1


def _seat_for(
    case: AttributionCase, *, family: ProviderFamily, model: str
) -> ProviderCallOutcome | None:
    return next(
        (
            item
            for item in case.seats
            if item.provider_family is family and item.requested_model == model
        ),
        None,
    )


def _provider_attribution(
    cases: Sequence[AttributionCase],
    matrices: Mapping[str, DisagreementMatrix],
    *,
    family: ProviderFamily,
    model: str,
) -> ProviderAttribution:
    tally = _ProviderAccumulator()
    for case in cases:
        seat = _seat_for(case, family=family, model=model)
        if seat is None:
            continue
        tally.add_cost(seat.estimated_cost_microunits)
        verdict = _seat_verdict(seat, observed_positive=case.observed_positive)
        tally.add_call(verdict, seat)
        if verdict.correct is None:
            continue
        tally.add_scored(case.case_type, correct=verdict.correct)
        if matrices[case.case_id].is_contested:
            tally.add_contested(correct=verdict.correct)
        tally.add_baseline_relation(case, verdict)

    return ProviderAttribution(
        provider_family=family,
        model=model,
        scored_cases=tally.scored,
        correct_cases=tally.correct,
        abstained_cases=tally.abstained,
        failed_cases=tally.failed,
        accuracy=_accuracy(tally.scored, tally.correct, name="accuracy"),
        baseline_agreements=tally.agree_cases,
        baseline_disagreements=tally.disagree_cases,
        accuracy_when_agreeing_with_baseline=_accuracy(
            tally.agree_cases,
            tally.agree_correct,
            name="accuracy_when_agreeing_with_baseline",
        ),
        accuracy_when_disagreeing_with_baseline=_accuracy(
            tally.disagree_cases,
            tally.disagree_correct,
            name="accuracy_when_disagreeing_with_baseline",
        ),
        independent_incremental_correct=tally.incremental,
        accuracy_when_contested=_accuracy(
            tally.contested_scored,
            tally.contested_correct,
            name="accuracy_when_contested",
        ),
        accuracy_by_case_type=tuple(
            CaseTypeAccuracy(
                case_type=case_type,
                scored=counts[0],
                correct=counts[1],
                accuracy=_accuracy(
                    counts[0], counts[1], name=f"accuracy_{case_type.value}"
                ),
            )
            for case_type, counts in sorted(
                tally.by_type.items(), key=lambda item: item[0].value
            )
        ),
        known_cost_microunits=tally.known_cost_microunits,
        unknown_cost_samples=tally.unknown_cost_samples,
    )


@dataclass
class _IncrementTally:
    """Paired baseline/committee tallies over the shared case set."""

    baseline_scored: int = 0
    baseline_correct: int = 0
    committee_scored: int = 0
    committee_correct: int = 0
    both_correct: int = 0
    both_wrong: int = 0
    only_committee_correct: int = 0
    only_baseline_correct: int = 0

    def add(self, *, committee_right: bool, baseline_right: bool) -> None:
        if committee_right and baseline_right:
            self.both_correct += 1
        elif committee_right:
            self.only_committee_correct += 1
        elif baseline_right:
            self.only_baseline_correct += 1
        else:
            self.both_wrong += 1


def _increment_case(case: AttributionCase, tally: _IncrementTally) -> None:
    """Record one case's paired outcome, skipping anything unscoreable."""
    if case.observed_positive is None:
        return
    call = committee_research_call(case)
    if call not in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE):
        return
    tally.committee_scored += 1
    committee_right = (call is DirectionalCall.POSITIVE) == case.observed_positive
    if committee_right:
        tally.committee_correct += 1
    if case.baseline_positive is None:
        return
    tally.baseline_scored += 1
    baseline_right = case.baseline_positive == case.observed_positive
    if baseline_right:
        tally.baseline_correct += 1
    tally.add(committee_right=committee_right, baseline_right=baseline_right)


def _committee_increment(cases: Sequence[AttributionCase]) -> CommitteeIncrement:
    tally = _IncrementTally()
    for case in cases:
        _increment_case(case, tally)
    return CommitteeIncrement(
        baseline_scored=tally.baseline_scored,
        baseline_correct=tally.baseline_correct,
        committee_scored=tally.committee_scored,
        committee_correct=tally.committee_correct,
        both_correct=tally.both_correct,
        both_wrong=tally.both_wrong,
        only_committee_correct=tally.only_committee_correct,
        only_baseline_correct=tally.only_baseline_correct,
        committee_accuracy=_accuracy(
            tally.committee_scored, tally.committee_correct, name="committee_accuracy"
        ),
        baseline_accuracy=_accuracy(
            tally.baseline_scored, tally.baseline_correct, name="baseline_accuracy"
        ),
        incremental_accuracy=rate_metric(
            "incremental_accuracy",
            numerator=(
                tally.only_committee_correct - tally.only_baseline_correct
            ),
            denominator=max(tally.baseline_scored, tally.committee_scored),
        ),
    )


def _chronological_windows(
    cases: Sequence[AttributionCase],
) -> tuple[WindowAccuracy, ...]:
    """Split the observed period in half so drift is visible, not asserted."""
    windows = (
        ("EARLIER_HALF", cases[: len(cases) // 2]),
        ("LATER_HALF", cases[len(cases) // 2 :]),
    )
    results: list[WindowAccuracy] = []
    for label, members in windows:
        scored = correct = 0
        for case in members:
            if case.observed_positive is None:
                continue
            call = committee_research_call(case)
            if call in (DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE):
                scored += 1
                if (call is DirectionalCall.POSITIVE) == case.observed_positive:
                    correct += 1
        results.append(
            WindowAccuracy(
                label=label,
                cases=len(members),
                scored=scored,
                correct=correct,
                accuracy=_accuracy(scored, correct, name=label.lower()),
            )
        )
    return tuple(results)


def _incremental_cost(
    providers: Sequence[ProviderAttribution],
    only_committee_correct: int,
    *,
    minimum_samples: int,
) -> int | None:
    """Additional model cost per incrementally correct case, when supportable."""
    if len(providers) == 0:
        return None
    total = 0
    for provider in providers:
        if provider.known_cost_microunits is None:
            # One unknown provider cost makes the aggregate unknown, not partial.
            return None
        total += provider.known_cost_microunits
    if only_committee_correct < 1:
        # There is nothing incremental to attribute the spend to; reporting a
        # per-increment figure here would divide by zero or invent a target.
        return None
    if only_committee_correct < minimum_samples:
        return None
    return total // only_committee_correct


def _calibration_metric() -> EvaluationMetric:
    """Confidence is ordinal, so calibration is explicitly not applicable."""
    return EvaluationMetric(
        name="calibration",
        value=None,
        applicable=False,
        sample_size=0,
        not_applicable_reason=REASON_NOT_DEFINED_FOR_CASE_TYPE,
    )


def advisory_disposition(report: AttributionReport) -> Mapping[str, object]:
    """The explicit non-promotion disposition every attribution result carries.

    Nothing in this module changes production behaviour. If a finding is ever to
    influence production it must pass a separate, future, human-governed
    promotion gate; this disposition exists so that requirement is on the record.
    """
    return {
        "attribution_id": report.attribution_id,
        "advisory_only": True,
        "measurement_only": True,
        "automatic_promotion": False,
        "trade_authority_changed": False,
        "threshold_change_authorized": False,
        "consumption": "ADVISORY",
        "promotion_path": "SEPARATE_HUMAN_GOVERNED_PROMOTION_GATE_REQUIRED",
    }


__all__ = [
    "ATTRIBUTION_METHOD_VERSION",
    "ATTRIBUTION_REPORT_SCHEMA_VERSION",
    "AttributionCase",
    "AttributionReport",
    "BaselineRelation",
    "CONFIDENCE_SPREAD_THRESHOLD",
    "CaseTypeAccuracy",
    "CommitteeIncrement",
    "DisagreementKind",
    "DisagreementMatrix",
    "DisagreementSummary",
    "MIN_ATTRIBUTION_SAMPLES",
    "ProviderAttribution",
    "SeatVerdict",
    "WindowAccuracy",
    "advisory_disposition",
    "baseline_relation",
    "build_attribution_report",
    "build_disagreement_matrix",
    "committee_research_call",
]
