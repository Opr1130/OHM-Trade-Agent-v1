"""Prospective shadow experiment with enforced anti-hindsight separation.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The prospective path is a strict three-point protocol:

* **T0** - the evidence cutoff passes, the committee opinions are produced, and
  they are *sealed*: their content hashes are recorded with the cutoff.
* **T1** - a future outcome becomes known and is recorded as a canonical
  observation that references its own source.
* **T2** - the sealed T0 opinion is compared against the T1 outcome.

The rules that make this meaningful are enforced here, not documented and
trusted:

* a sealed opinion can never be altered by anything learned later. Verification
  recomputes the sealed hashes from the recorded T0 evidence and fails closed on
  any mismatch;
* an outcome may only be joined when its whole measurement window lies after the
  evidence cutoff, so no part of the outcome was knowable at T0;
* a retrospective outcome cannot be relabelled prospective. It raises
  :class:`HindsightLeakageError` and the caller is pointed at the retrospective
  bake-off instead;
* provisional outcome evidence is labelled provisional and never presented as
  final evidence;
* retrospective and prospective metrics are never combined: this module records
  ``PROSPECTIVE`` phase evidence only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable, Mapping, Sequence
from app.opip.committee.contracts import (
    CaseType,
    CommitteeCaseOutcome,
    EvaluationPhase,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFamily,
)
from app.opip.committee.evaluation import DirectionalCall, directional_call
from app.opip.committee.metrics import (
    ConfusionMatrix,
    EvaluationMetric,
    classification_report,
)
from app.opip.decision_intelligence.identity import Provenance
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

SEALED_PREDICTION_SCHEMA_VERSION = 1
OUTCOME_OBSERVATION_SCHEMA_VERSION = 1
PROSPECTIVE_EVALUATION_SCHEMA_VERSION = 1

SEALED_PREDICTION_IDENTITY_DOMAIN = "COMMITTEE-SEAL"
OUTCOME_OBSERVATION_IDENTITY_DOMAIN = "COMMITTEE-OUTCOME-OBS"
PROSPECTIVE_EVALUATION_IDENTITY_DOMAIN = "COMMITTEE-PROSPECTIVE-EVAL"

#: Shared validation message so the horizon rule cannot drift.
_HORIZON_MESSAGE = "horizon_seconds must be a positive integer"

_SEALED_PREDICTION_REQUIRED_FIELDS = (
    "case_id",
    "experiment_id",
    "case_outcome_id",
    "evidence_snapshot_hash",
    "committee_policy_version",
)


def _validate_sealed_prediction(prediction: "SealedPrediction") -> None:
    """Validate the sealed T0 record, including its prospective-only phase."""
    if type(prediction.schema_version) is not int or prediction.schema_version != (
        SEALED_PREDICTION_SCHEMA_VERSION
    ):
        raise ValueError("unsupported SealedPrediction schema_version")
    for field_name in _SEALED_PREDICTION_REQUIRED_FIELDS:
        value = getattr(prediction, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ProspectivePolicyError(f"{field_name} is required")
    if not isinstance(prediction.case_type, CaseType):
        raise ProspectivePolicyError("invalid case_type")
    if prediction.phase is not EvaluationPhase.PROSPECTIVE:
        raise ProspectivePolicyError(
            "a sealed prediction is prospective evidence; retrospective "
            "evidence belongs to the bake-off path"
        )
    for field_name in ("evidence_cutoff_at", "sealed_at"):
        object.__setattr__(
            prediction,
            field_name,
            require_utc(getattr(prediction, field_name), field_name=field_name),
        )
    if prediction.sealed_at < prediction.evidence_cutoff_at:
        raise ProspectivePolicyError(
            "a prediction cannot be sealed before its evidence cutoff"
        )
    if (
        type(prediction.sealed_seat_count) is not int
        or prediction.sealed_seat_count < 0
    ):
        raise ProspectivePolicyError(
            "sealed_seat_count must be a non-negative integer"
        )
    if len(prediction.sealed_opinion_hashes) != prediction.sealed_seat_count:
        raise ProspectivePolicyError(
            "sealed_seat_count must match the sealed opinion hashes"
        )


def _validate_outcome_references(observation: "OutcomeObservation") -> None:
    for field_name in ("case_id", "outcome_source"):
        value = getattr(observation, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ProspectivePolicyError(f"{field_name} is required")
    if not observation.source_refs:
        raise ProspectivePolicyError(
            "an outcome observation must reference canonical evidence rather "
            "than being recomputed"
        )
    for ref in observation.source_refs:
        if not isinstance(ref, str) or not ref.strip():
            raise ProspectivePolicyError("source_refs entries are required")


def _validate_outcome_finality(observation: "OutcomeObservation") -> None:
    if not isinstance(observation.finality, OutcomeFinality):
        raise ProspectivePolicyError("invalid outcome finality")
    if (
        observation.finality is OutcomeFinality.PROVISIONAL
        and not observation.incomplete_reason
    ):
        raise ProspectivePolicyError(
            "a provisional outcome must state why it is not final"
        )
    if (
        observation.finality is OutcomeFinality.FINAL
        and observation.incomplete_reason
    ):
        raise ProspectivePolicyError(
            "a final outcome cannot carry an incompleteness reason"
        )


def _validate_outcome_observation(observation: "OutcomeObservation") -> None:
    """Validate a T1 outcome record, including its finality statement."""
    if type(observation.schema_version) is not int or (
        observation.schema_version != OUTCOME_OBSERVATION_SCHEMA_VERSION
    ):
        raise ValueError("unsupported OutcomeObservation schema_version")
    _validate_outcome_references(observation)
    object.__setattr__(
        observation,
        "observed_at",
        require_utc(observation.observed_at, field_name="observed_at"),
    )
    if type(observation.horizon_seconds) is not int or observation.horizon_seconds < 1:
        raise ProspectivePolicyError(_HORIZON_MESSAGE)
    if observation.positive is not None and type(observation.positive) is not bool:
        raise ProspectivePolicyError("positive must be a boolean or null")
    if observation.realised_return_microunits is not None and (
        type(observation.realised_return_microunits) is not int
    ):
        raise ProspectivePolicyError("realised_return_microunits must be an integer")
    _validate_outcome_finality(observation)


_PROSPECTIVE_EVALUATION_REQUIRED_FIELDS = (
    "prediction_id",
    "outcome_observation_id",
    "case_id",
    "experiment_id",
)

_PROSPECTIVE_EVALUATION_TIMESTAMPS = (
    "evidence_cutoff_at",
    "sealed_at",
    "observed_at",
    "evaluated_at",
)

_PROSPECTIVE_EVALUATION_SEAT_COUNTS = (
    "scored_seats",
    "abstained_seats",
    "unavailable_seats",
    "unscored_directional_seats",
)


def _validate_prospective_authority(evaluation: "ProspectiveEvaluation") -> None:
    """A prospective result is measurement evidence and can never act."""
    if evaluation.phase is not EvaluationPhase.PROSPECTIVE:
        raise ProspectivePolicyError(
            "a prospective evaluation cannot carry a retrospective phase"
        )
    if evaluation.automatic_promotion is not False:
        raise ProspectivePolicyError("a prospective result can never promote")
    if evaluation.trade_authority_changed is not False:
        raise ProspectivePolicyError(
            "a prospective result can never change trade authority"
        )
    if evaluation.measurement_only is not True:
        raise ProspectivePolicyError("prospective evidence is measurement only")


def _validate_prospective_identity(evaluation: "ProspectiveEvaluation") -> None:
    for field_name in _PROSPECTIVE_EVALUATION_REQUIRED_FIELDS:
        value = getattr(evaluation, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ProspectivePolicyError(f"{field_name} is required")
    for field_name, expected in (
        ("case_type", CaseType),
        ("finality", OutcomeFinality),
    ):
        if not isinstance(getattr(evaluation, field_name), expected):
            raise ProspectivePolicyError(f"invalid {field_name}")
    for field_name in _PROSPECTIVE_EVALUATION_TIMESTAMPS:
        object.__setattr__(
            evaluation,
            field_name,
            require_utc(getattr(evaluation, field_name), field_name=field_name),
        )


def _validate_prospective_timeline(evaluation: "ProspectiveEvaluation") -> None:
    if type(evaluation.horizon_seconds) is not int or evaluation.horizon_seconds < 1:
        raise ProspectivePolicyError(_HORIZON_MESSAGE)
    if evaluation.observed_at < evaluation.sealed_at:
        raise ProspectivePolicyError(
            "an outcome observed before sealing is not a prospective outcome"
        )
    if evaluation.evaluated_at < evaluation.observed_at:
        raise ProspectivePolicyError("evaluated_at must be >= observed_at")


def _validate_prospective_seat_accounting(
    evaluation: "ProspectiveEvaluation",
) -> None:
    for field_name in _PROSPECTIVE_EVALUATION_SEAT_COUNTS:
        value = getattr(evaluation, field_name)
        if type(value) is not int or value < 0:
            raise ProspectivePolicyError(
                f"{field_name} must be a non-negative integer"
            )
    accounted = sum(
        getattr(evaluation, field_name)
        for field_name in _PROSPECTIVE_EVALUATION_SEAT_COUNTS
    )
    if len(evaluation.seat_scores) != accounted:
        raise ProspectivePolicyError(
            "seat score counts must sum to the recorded seat scores: every "
            "seat is scored, abstaining, unavailable, or unscored because the "
            "outcome carried no direction"
        )
    if evaluation.finality is OutcomeFinality.PROVISIONAL and any(
        score.final for score in evaluation.seat_scores
    ):
        raise ProspectivePolicyError(
            "provisional outcome evidence cannot produce a final seat score"
        )


def _validate_prospective_evaluation(evaluation: "ProspectiveEvaluation") -> None:
    """Validate a T2 evaluation record end to end."""
    if type(evaluation.schema_version) is not int or evaluation.schema_version != (
        PROSPECTIVE_EVALUATION_SCHEMA_VERSION
    ):
        raise ValueError("unsupported ProspectiveEvaluation schema_version")
    _validate_prospective_authority(evaluation)
    _validate_prospective_identity(evaluation)
    _validate_prospective_timeline(evaluation)
    _validate_prospective_seat_accounting(evaluation)


class OutcomeFinality(str, Enum):
    """Whether outcome evidence is complete and final.

    A provisional observation is real evidence, but it is not final evidence and
    must never be scored as though it were.
    """

    FINAL = "FINAL"
    PROVISIONAL = "PROVISIONAL"


class ProspectivePolicyError(ValueError):
    """The prospective protocol was about to be violated."""


class HindsightLeakageError(ProspectivePolicyError):
    """An outcome that was already knowable at the cutoff was offered as future.

    Raised rather than silently accepted, because every metric derived from such
    a join would be optimistic in a way no reader could see.
    """


@dataclass(frozen=True)
class SealedPrediction:
    """T0 evidence: the sealed opinion set for one case, with its cutoff."""

    case_id: str
    case_type: CaseType
    experiment_id: str
    evidence_cutoff_at: datetime
    sealed_at: datetime
    case_outcome_id: str
    evidence_snapshot_hash: str
    committee_policy_version: str
    sealed_opinion_hashes: tuple[str, ...]
    sealed_seat_count: int
    provenance: Provenance
    phase: EvaluationPhase = EvaluationPhase.PROSPECTIVE
    schema_version: int = SEALED_PREDICTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_sealed_prediction(self)

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "case_type": self.case_type,
            "experiment_id": self.experiment_id,
            "evidence_cutoff_at": self.evidence_cutoff_at,
            "sealed_at": self.sealed_at,
            "case_outcome_id": self.case_outcome_id,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "committee_policy_version": self.committee_policy_version,
            "sealed_opinion_hashes": self.sealed_opinion_hashes,
        }

    @property
    def prediction_id(self) -> str:
        return stable_hash(
            SEALED_PREDICTION_IDENTITY_DOMAIN, self.identity_payload()
        )

    def outcome_eligible_at(self, horizon_seconds: int) -> datetime:
        """The earliest instant at which this prediction may be judged."""
        if type(horizon_seconds) is not int or horizon_seconds < 1:
            raise ProspectivePolicyError(_HORIZON_MESSAGE)
        return self.evidence_cutoff_at + timedelta(seconds=horizon_seconds)


@dataclass(frozen=True)
class OutcomeObservation:
    """T1 evidence: a canonical future outcome, by reference."""

    case_id: str
    outcome_source: str
    source_refs: tuple[str, ...]
    observed_at: datetime
    horizon_seconds: int
    finality: OutcomeFinality
    positive: bool | None = None
    realised_return_microunits: int | None = None
    incomplete_reason: str | None = None
    schema_version: int = OUTCOME_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_outcome_observation(self)

    @property
    def is_final(self) -> bool:
        return self.finality is OutcomeFinality.FINAL

    def measurement_window_start(self) -> datetime:
        """The instant the measured window opens, derived from the observation."""
        return self.observed_at - timedelta(seconds=self.horizon_seconds)

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "outcome_source": self.outcome_source,
            "source_refs": self.source_refs,
            "observed_at": self.observed_at,
            "horizon_seconds": self.horizon_seconds,
            "finality": self.finality,
            "positive": self.positive,
            # Every persisted field that changes the meaning of the outcome must
            # be part of its identity, otherwise two materially different
            # observations could share one id and one could be deduped away.
            "realised_return_microunits": self.realised_return_microunits,
            "incomplete_reason": self.incomplete_reason,
        }

    @property
    def observation_id(self) -> str:
        return stable_hash(
            OUTCOME_OBSERVATION_IDENTITY_DOMAIN, self.identity_payload()
        )


@dataclass(frozen=True)
class ProspectiveSeatScore:
    """One sealed seat's directional call against one resolved outcome."""

    provider_family: ProviderFamily
    model: str
    call: DirectionalCall
    correct: bool | None
    final: bool

    @property
    def scored(self) -> bool:
        return self.correct is not None


@dataclass(frozen=True)
class ProspectiveEvaluation:
    """T2 evidence: a sealed T0 opinion joined to a T1 outcome."""

    prediction_id: str
    outcome_observation_id: str
    case_id: str
    case_type: CaseType
    experiment_id: str
    evidence_cutoff_at: datetime
    sealed_at: datetime
    observed_at: datetime
    evaluated_at: datetime
    horizon_seconds: int
    finality: OutcomeFinality
    seat_scores: tuple[ProspectiveSeatScore, ...]
    scored_seats: int
    abstained_seats: int
    unavailable_seats: int
    unscored_directional_seats: int
    precision: EvaluationMetric
    recall: EvaluationMetric
    f1: EvaluationMetric
    accuracy: EvaluationMetric
    confusion: ConfusionMatrix | None
    provenance: Provenance
    phase: EvaluationPhase = EvaluationPhase.PROSPECTIVE
    measurement_only: bool = True
    automatic_promotion: bool = False
    trade_authority_changed: bool = False
    schema_version: int = PROSPECTIVE_EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_prospective_evaluation(self)

    @property
    def counts_as_final_evidence(self) -> bool:
        return self.finality is OutcomeFinality.FINAL and all(
            score.final for score in self.seat_scores
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "prediction_id": self.prediction_id,
            "outcome_observation_id": self.outcome_observation_id,
            "case_id": self.case_id,
            "case_type": self.case_type,
            "experiment_id": self.experiment_id,
            "evidence_cutoff_at": self.evidence_cutoff_at,
            "sealed_at": self.sealed_at,
            "observed_at": self.observed_at,
            "evaluated_at": self.evaluated_at,
            "horizon_seconds": self.horizon_seconds,
            "finality": self.finality,
            "seat_scores": tuple(
                (
                    score.provider_family,
                    score.model,
                    score.call,
                    score.correct,
                )
                for score in self.seat_scores
            ),
        }

    @property
    def evaluation_id(self) -> str:
        return stable_hash(
            PROSPECTIVE_EVALUATION_IDENTITY_DOMAIN, self.identity_payload()
        )


def _sealed_opinion_hashes(case_outcome: CommitteeCaseOutcome) -> tuple[str, ...]:
    hashes: list[str] = []
    for outcome in case_outcome.outcomes:
        if outcome.status in (
            ObservationStatus.COMPLETED,
            ObservationStatus.DUPLICATE_OK,
        ) and outcome.opinion is not None:
            hashes.append(outcome.opinion.opinion_hash)
    return tuple(hashes)


def seal_prediction(
    *,
    case_outcome: CommitteeCaseOutcome,
    evidence_cutoff_at: datetime,
    sealed_at: datetime,
    experiment_id: str,
    provenance: Provenance,
    case_type: CaseType,
) -> SealedPrediction:
    """Seal the T0 opinions for a case so no later evidence can alter them."""
    cutoff = require_utc(evidence_cutoff_at, field_name="evidence_cutoff_at")
    sealed = require_utc(sealed_at, field_name="sealed_at")
    hashes = _sealed_opinion_hashes(case_outcome)
    if case_outcome.phase is not EvaluationPhase.PROSPECTIVE:
        raise ProspectivePolicyError(
            "a retrospective committee run cannot be sealed as a prospective "
            "prediction; retrospective evidence belongs to the bake-off path"
        )
    if sealed < case_outcome.completed_at:
        raise ProspectivePolicyError(
            "a prediction cannot be sealed before its committee run completed"
        )
    return SealedPrediction(
        case_id=case_outcome.case_id,
        case_type=case_type,
        experiment_id=experiment_id,
        evidence_cutoff_at=cutoff,
        sealed_at=sealed,
        case_outcome_id=case_outcome.case_outcome_id,
        evidence_snapshot_hash=case_outcome.evidence_snapshot_hash,
        committee_policy_version=case_outcome.committee_policy_version,
        sealed_opinion_hashes=hashes,
        sealed_seat_count=len(hashes),
        provenance=provenance,
    )


def verify_seal(
    prediction: SealedPrediction, case_outcome: CommitteeCaseOutcome
) -> None:
    """Recompute the sealed T0 hashes and fail closed on any mismatch.

    This is the anti-hindsight proof for stored evidence: if anything rewrote the
    opinion that was sealed at T0, verification fails here rather than silently
    producing a flattering evaluation.
    """
    if prediction.case_outcome_id != case_outcome.case_outcome_id:
        raise ProspectivePolicyError(
            "the sealed prediction does not reference this committee case outcome"
        )
    if prediction.case_id != case_outcome.case_id:
        raise ProspectivePolicyError("the sealed prediction is for a different case")
    if prediction.evidence_snapshot_hash != case_outcome.evidence_snapshot_hash:
        raise ProspectivePolicyError(
            "the sealed prediction references a different evidence snapshot"
        )
    current = _sealed_opinion_hashes(case_outcome)
    if current != prediction.sealed_opinion_hashes:
        raise ProspectivePolicyError(
            "the sealed T0 opinions no longer match the recorded prediction; "
            "sealed evidence must never be altered after sealing"
        )


def assert_outcome_is_prospective(
    prediction: SealedPrediction, observation: OutcomeObservation
) -> None:
    """Fail closed unless the outcome is genuinely unknowable at the cutoff."""
    if observation.case_id != prediction.case_id:
        raise ProspectivePolicyError("the outcome observation is for a different case")
    window_start = observation.measurement_window_start()
    if window_start < prediction.evidence_cutoff_at:
        raise HindsightLeakageError(
            "the outcome measurement window opens before the evidence cutoff, so "
            "part of this outcome was knowable at T0; this is retrospective "
            "evidence and belongs in the retrospective bake-off"
        )
    if observation.observed_at < prediction.sealed_at:
        raise HindsightLeakageError(
            "the outcome was observed before the prediction was sealed"
        )
    if observation.horizon_seconds < 1:
        raise ProspectivePolicyError(_HORIZON_MESSAGE)


def _score_seats(
    seats: Iterable[ProviderCallOutcome],
    *,
    positive: bool | None,
    final: bool,
) -> list[ProspectiveSeatScore]:
    """Score each seat's directional call. ``positive=None`` cannot score."""
    scores: list[ProspectiveSeatScore] = []
    for seat in seats:
        call = directional_call(seat)
        correct: bool | None = None
        if positive is not None and call in (
            DirectionalCall.POSITIVE,
            DirectionalCall.NEGATIVE,
        ):
            correct = (call is DirectionalCall.POSITIVE) == positive
        scores.append(
            ProspectiveSeatScore(
                provider_family=seat.provider_family,
                model=seat.requested_model,
                call=call,
                correct=correct,
                final=final and correct is not None,
            )
        )
    return scores


def evaluate_prospective(
    *,
    prediction: SealedPrediction,
    case_outcome: CommitteeCaseOutcome,
    observation: OutcomeObservation,
    evaluated_at: datetime,
    provenance: Provenance,
) -> ProspectiveEvaluation:
    """T2: compare the sealed T0 opinion against the T1 outcome."""
    verify_seal(prediction, case_outcome)
    assert_outcome_is_prospective(prediction, observation)
    evaluated = require_utc(evaluated_at, field_name="evaluated_at")
    if evaluated < observation.observed_at:
        raise ProspectivePolicyError("evaluated_at must be >= observed_at")

    final = observation.is_final
    scores = _score_seats(
        case_outcome.outcomes, positive=observation.positive, final=final
    )
    directional_seats = sum(
        1 for score in scores if score.call in (
            DirectionalCall.POSITIVE, DirectionalCall.NEGATIVE
        )
    )
    scored_seats = sum(1 for score in scores if score.scored)
    if observation.positive is None:
        # A non-directional outcome cannot score a directional call. The seats
        # are still reported, but classification is not applicable - never zero.
        classification = classification_report([])
    else:
        pairs = [
            (score.call is DirectionalCall.POSITIVE, observation.positive)
            for score in scores
            if score.scored
        ]
        classification = classification_report(pairs)

    return ProspectiveEvaluation(
        prediction_id=prediction.prediction_id,
        outcome_observation_id=observation.observation_id,
        case_id=prediction.case_id,
        case_type=prediction.case_type,
        experiment_id=prediction.experiment_id,
        evidence_cutoff_at=prediction.evidence_cutoff_at,
        sealed_at=prediction.sealed_at,
        observed_at=observation.observed_at,
        evaluated_at=evaluated,
        horizon_seconds=observation.horizon_seconds,
        finality=observation.finality,
        seat_scores=tuple(scores),
        scored_seats=scored_seats,
        abstained_seats=sum(
            1 for score in scores if score.call is DirectionalCall.ABSTAIN
        ),
        unavailable_seats=sum(
            1 for score in scores if score.call is DirectionalCall.UNAVAILABLE
        ),
        unscored_directional_seats=directional_seats - scored_seats,
        precision=classification.precision,
        recall=classification.recall,
        f1=classification.f1,
        accuracy=classification.accuracy,
        confusion=(
            classification.matrix if classification.matrix.total else None
        ),
        provenance=provenance,
    )


def awaiting_outcome(
    *,
    predictions: Sequence[SealedPrediction],
    observations: Sequence[OutcomeObservation],
    evaluations: Sequence[ProspectiveEvaluation] = (),
) -> tuple[SealedPrediction, ...]:
    """Sealed predictions that have no evaluation yet."""
    evaluated = {item.prediction_id for item in evaluations}
    observed = {item.case_id for item in observations}
    pending: list[SealedPrediction] = []
    for prediction in predictions:
        if prediction.prediction_id in evaluated:
            continue
        if prediction.case_id in observed:
            # An outcome exists but the join has not been recorded yet.
            continue
        pending.append(prediction)
    return tuple(pending)


def prospective_observability_counts(
    *,
    predictions: Sequence[SealedPrediction],
    observations: Sequence[OutcomeObservation],
    evaluations: Sequence[ProspectiveEvaluation],
) -> Mapping[str, int]:
    """Counters for the prospective experiment, for operational visibility."""
    return {
        "sealed_predictions": len(predictions),
        "outcome_observations": len(observations),
        "provisional_outcomes": sum(
            1 for item in observations if not item.is_final
        ),
        "evaluated_predictions": len(evaluations),
        "final_evaluations": sum(
            1 for item in evaluations if item.counts_as_final_evidence
        ),
        "provisional_evaluations": sum(
            1 for item in evaluations if not item.counts_as_final_evidence
        ),
        "awaiting_outcome": len(
            awaiting_outcome(
                predictions=predictions,
                observations=observations,
                evaluations=evaluations,
            )
        ),
    }


__all__ = [
    "HindsightLeakageError",
    "OUTCOME_OBSERVATION_SCHEMA_VERSION",
    "PROSPECTIVE_EVALUATION_SCHEMA_VERSION",
    "ProspectiveEvaluation",
    "ProspectivePolicyError",
    "ProspectiveSeatScore",
    "SEALED_PREDICTION_SCHEMA_VERSION",
    "OutcomeFinality",
    "OutcomeObservation",
    "SealedPrediction",
    "assert_outcome_is_prospective",
    "awaiting_outcome",
    "evaluate_prospective",
    "prospective_observability_counts",
    "seal_prediction",
    "verify_seal",
]
