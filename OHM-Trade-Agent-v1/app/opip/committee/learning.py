"""Learning lineage: hypothesis, experiment, conclusion, release, effectiveness.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This is the chain that turns an observation into governed knowledge:

``observation -> validated weakness -> hypothesis -> registered experiment ->
sealed prospective evaluation -> ACCEPTED/REJECTED/INCONCLUSIVE -> separate human
release decision -> post-change effectiveness``

Every link is a durable record with a stable identity, and the properties that make
the chain trustworthy are enforced rather than described:

* **A hypothesis must be falsifiable and grounded.** It names a suspected
  mechanism, a cohort, a falsifiable prediction, and an expected economic effect,
  and it cites the findings or decisions it came from. A hypothesis that cannot be
  disproved cannot be tested, and an ungrounded one is a hunch.

* **A registered experiment is frozen.** Dataset manifest, knowledge cutoff,
  policy and route versions, model version, prompt and schema hashes, endpoint,
  effect definition, experiment family, statistical method, stopping rule, and any
  holdout are committed before results. A change after sealing is refused: it must
  be a new experiment or an explicit compromised status, never a quiet edit.

* **A conclusion is one of three values, and inconclusive is first class.**
  Forcing an unresolved experiment into accepted or rejected would manufacture a
  finding.

* **Promotion stays human.** ``ACCEPTED`` does not change policy. It authorises a
  separate human release decision, and a release must reference the conclusion and
  the exact approved SHA that implemented it. No link in this module can change a
  threshold, a route, or a weight by itself.

* **Post-change effectiveness is measured, not asserted.** It references the
  release and reports the observed before/after evidence, including an explicit
  incomparable-cohort reason when the two cohorts cannot be compared.

**Unknown is never favourable.** An unmeasured effect or effectiveness is ``None``
and stays ``None``; zero is never substituted for "not measured".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Sequence

from app.opip.committee.weakness import WeaknessValidationState
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

HYPOTHESIS_SCHEMA_VERSION = 1
EXPERIMENT_SCHEMA_VERSION = 1
CONCLUSION_SCHEMA_VERSION = 1
RELEASE_SCHEMA_VERSION = 1
EFFECTIVENESS_SCHEMA_VERSION = 1

HYPOTHESIS_IDENTITY_DOMAIN = "COMMITTEE-HYPOTHESIS"
EXPERIMENT_IDENTITY_DOMAIN = "COMMITTEE-EXPERIMENT"
CONCLUSION_IDENTITY_DOMAIN = "COMMITTEE-CONCLUSION"
RELEASE_IDENTITY_DOMAIN = "COMMITTEE-RELEASE"
EFFECTIVENESS_IDENTITY_DOMAIN = "COMMITTEE-EFFECTIVENESS"

#: Recorded when an experiment was changed after sealing. The original remains
#: auditable and the change is visible rather than silent.
COMPROMISED = "COMPROMISED"


class LearningError(ValueError):
    """A learning-lineage contract was violated."""


class ResearchConclusion(str, Enum):
    """The only admissible outcomes of a registered experiment."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EffectDirection(str, Enum):
    """Which way a hypothesis expects the effect to move."""

    IMPROVEMENT = "IMPROVEMENT"
    DETERIORATION = "DETERIORATION"
    NO_CHANGE = "NO_CHANGE"


class CohortComparability(str, Enum):
    """Whether a before/after comparison is legitimate.

    An incomparable cohort is a first-class outcome, because a before/after that
    silently compares unlike populations produces a confident wrong answer.
    """

    COMPARABLE = "COMPARABLE"
    INCOMPARABLE = "INCOMPARABLE"


@dataclass(frozen=True)
class Hypothesis:
    """A falsifiable statement grounded in observed findings.

    It carries the suspected mechanism, the cohort it applies to, the prediction
    that would disprove it, and the economic effect it expects, so an experiment
    can be designed against it rather than around it.
    """

    hypothesis_id: str
    suspected_mechanism: str
    eligible_cohort: str
    falsifiable_prediction: str
    expected_effect_direction: EffectDirection
    expected_effect_microunits: int | None
    source_finding_ids: tuple[str, ...]
    raised_at: datetime
    schema_version: int = HYPOTHESIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != HYPOTHESIS_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported Hypothesis schema_version")
        for field_name in (
            "hypothesis_id",
            "suspected_mechanism",
            "eligible_cohort",
            "falsifiable_prediction",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise LearningError(f"{field_name} is required")
        if not isinstance(self.expected_effect_direction, EffectDirection):
            raise LearningError("invalid expected_effect_direction")
        if not isinstance(self.source_finding_ids, tuple) or not self.source_finding_ids:
            raise LearningError(
                "a hypothesis must cite the findings or decisions it came from"
            )
        if self.expected_effect_microunits is not None and (
            type(self.expected_effect_microunits) is not int
        ):
            raise LearningError(
                "expected_effect_microunits must be an integer or null"
            )
        if self.expected_effect_direction is EffectDirection.NO_CHANGE and (
            self.expected_effect_microunits not in (None, 0)
        ):
            raise LearningError(
                "a NO_CHANGE hypothesis cannot expect a non-zero economic effect"
            )
        object.__setattr__(
            self,
            "raised_at",
            require_utc(self.raised_at, field_name="raised_at"),
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis_id": self.hypothesis_id,
            "suspected_mechanism": self.suspected_mechanism,
            "eligible_cohort": self.eligible_cohort,
            "falsifiable_prediction": self.falsifiable_prediction,
            "expected_effect_direction": self.expected_effect_direction,
            "expected_effect_microunits": self.expected_effect_microunits,
            "source_finding_ids": self.source_finding_ids,
            "raised_at": self.raised_at,
        }

    @property
    def hypothesis_hash(self) -> str:
        return stable_hash(HYPOTHESIS_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class RegisteredExperiment:
    """A frozen experiment design.

    Everything that could otherwise be chosen after seeing results is committed
    here. ``verify_unchanged`` refuses an edit, so a post-hoc change becomes a new
    experiment or an explicit compromise.
    """

    experiment_id: str
    hypothesis_id: str
    dataset_manifest_ref: str
    knowledge_cutoff_at: datetime
    policy_version: str
    route_version: str
    model_version: str
    prompt_hash: str
    schema_hash: str
    endpoint: str
    effect_definition: str
    experiment_family: str
    statistical_method: str
    stopping_rule: str
    registered_at: datetime
    holdout_ref: str | None = None
    compromised_reason: str | None = None
    schema_version: int = EXPERIMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXPERIMENT_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported RegisteredExperiment schema_version")
        for field_name in (
            "experiment_id",
            "hypothesis_id",
            "dataset_manifest_ref",
            "policy_version",
            "route_version",
            "model_version",
            "prompt_hash",
            "schema_hash",
            "endpoint",
            "effect_definition",
            "experiment_family",
            "statistical_method",
            "stopping_rule",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise LearningError(f"{field_name} is required")
        object.__setattr__(
            self,
            "knowledge_cutoff_at",
            require_utc(self.knowledge_cutoff_at, field_name="knowledge_cutoff_at"),
        )
        object.__setattr__(
            self,
            "registered_at",
            require_utc(self.registered_at, field_name="registered_at"),
        )

    @property
    def is_compromised(self) -> bool:
        return self.compromised_reason is not None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "dataset_manifest_ref": self.dataset_manifest_ref,
            "knowledge_cutoff_at": self.knowledge_cutoff_at,
            "policy_version": self.policy_version,
            "route_version": self.route_version,
            "model_version": self.model_version,
            "prompt_hash": self.prompt_hash,
            "schema_hash": self.schema_hash,
            "endpoint": self.endpoint,
            "effect_definition": self.effect_definition,
            "experiment_family": self.experiment_family,
            "statistical_method": self.statistical_method,
            "stopping_rule": self.stopping_rule,
            "registered_at": self.registered_at,
            "holdout_ref": self.holdout_ref,
            # The compromise is semantic state, so it participates in the identity.
            # Without it a compromised experiment would share a hash with the sealed
            # design and would pass verify_unchanged, hiding the very deviation the
            # compromise exists to surface.
            "compromised_reason": self.compromised_reason,
        }

    @property
    def experiment_hash(self) -> str:
        return stable_hash(EXPERIMENT_IDENTITY_DOMAIN, self.identity_payload())

    def verify_unchanged(self, other: "RegisteredExperiment") -> None:
        """Fail closed unless this exactly reproduces the sealed design."""
        if other.experiment_hash != self.experiment_hash:
            raise LearningError(
                "the experiment changed after registration; a sealed experiment "
                "cannot be edited in place. Register a new experiment or record an "
                f"explicit {COMPROMISED} reason."
            )

    def mark_compromised(self, reason: str) -> "RegisteredExperiment":
        """Return a copy carrying an explicit compromise reason."""
        if not isinstance(reason, str) or not reason.strip():
            raise LearningError("a compromise requires a reason")
        return RegisteredExperiment(
            **{
                **{
                    name: getattr(self, name)
                    for name in (
                        "experiment_id",
                        "hypothesis_id",
                        "dataset_manifest_ref",
                        "knowledge_cutoff_at",
                        "policy_version",
                        "route_version",
                        "model_version",
                        "prompt_hash",
                        "schema_hash",
                        "endpoint",
                        "effect_definition",
                        "experiment_family",
                        "statistical_method",
                        "stopping_rule",
                        "registered_at",
                        "holdout_ref",
                    )
                },
                "compromised_reason": reason,
            }
        )


@dataclass(frozen=True)
class ResearchConclusionRecord:
    """The conclusion of one registered experiment.

    ``INCONCLUSIVE`` is first class: forcing an unresolved experiment into accepted
    or rejected would manufacture a finding.
    """

    conclusion_id: str
    experiment_id: str
    conclusion: ResearchConclusion
    rationale: str
    concluded_at: datetime
    evaluation_refs: tuple[str, ...] = ()
    measured_effect_microunits: int | None = None
    schema_version: int = CONCLUSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONCLUSION_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported ResearchConclusionRecord schema_version")
        for field_name in ("conclusion_id", "experiment_id", "rationale"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise LearningError(f"{field_name} is required")
        if not isinstance(self.conclusion, ResearchConclusion):
            raise LearningError("invalid conclusion")
        if self.conclusion is not ResearchConclusion.INCONCLUSIVE and (
            not self.evaluation_refs
        ):
            raise LearningError(
                f"a {self.conclusion.value} conclusion must reference the sealed "
                "evaluation it was drawn from"
            )
        if self.measured_effect_microunits is not None and (
            type(self.measured_effect_microunits) is not int
        ):
            raise LearningError(
                "measured_effect_microunits must be an integer or null"
            )
        object.__setattr__(
            self,
            "concluded_at",
            require_utc(self.concluded_at, field_name="concluded_at"),
        )

    @property
    def authorises_policy_change(self) -> bool:
        """Always false: a conclusion is evidence, never an action.

        Promotion requires a separate human-governed release decision, which is a
        different record with a different owner.
        """
        return False

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "conclusion_id": self.conclusion_id,
            "experiment_id": self.experiment_id,
            "conclusion": self.conclusion,
            "rationale": self.rationale,
            "concluded_at": self.concluded_at,
            "evaluation_refs": self.evaluation_refs,
            "measured_effect_microunits": self.measured_effect_microunits,
        }

    @property
    def conclusion_hash(self) -> str:
        return stable_hash(CONCLUSION_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class ReleaseRecord:
    """A separate, human-governed release decision.

    This is the only record in the chain that links a conclusion to shipped code,
    and it does so by exact approved SHA. It records who decided, which is what
    keeps promotion a human act rather than an inferred one.
    """

    release_id: str
    conclusion_id: str
    approved_sha: str
    released_at: datetime
    approved_by: str
    artifact_ref: str | None = None
    schema_version: int = RELEASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RELEASE_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported ReleaseRecord schema_version")
        for field_name in ("release_id", "conclusion_id", "approved_by"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise LearningError(f"{field_name} is required")
        if not isinstance(self.approved_sha, str) or len(self.approved_sha) != 40:
            raise LearningError(
                "approved_sha must be a full 40-character commit SHA; a short or "
                "absent SHA cannot identify the released artifact"
            )
        if any(character not in "0123456789abcdef" for character in self.approved_sha):
            raise LearningError("approved_sha must be a lowercase hexadecimal SHA")
        object.__setattr__(
            self,
            "released_at",
            require_utc(self.released_at, field_name="released_at"),
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "conclusion_id": self.conclusion_id,
            "approved_sha": self.approved_sha,
            "released_at": self.released_at,
            "approved_by": self.approved_by,
            "artifact_ref": self.artifact_ref,
        }

    @property
    def release_hash(self) -> str:
        return stable_hash(RELEASE_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class PostChangeEffectiveness:
    """Measured effectiveness of a released change.

    Reports the observed before/after evidence and states explicitly whether the
    two cohorts are comparable. An effectiveness claim over incomparable cohorts is
    refused rather than reported with a caveat.
    """

    effectiveness_id: str
    release_id: str
    comparability: CohortComparability
    measured_at: datetime
    before_window_ref: str
    after_window_ref: str
    before_metric_microunits: int | None = None
    after_metric_microunits: int | None = None
    recurrence_before: int | None = None
    recurrence_after: int | None = None
    incomparable_reason: str | None = None
    schema_version: int = EFFECTIVENESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EFFECTIVENESS_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported PostChangeEffectiveness schema_version")
        for field_name in (
            "effectiveness_id",
            "release_id",
            "before_window_ref",
            "after_window_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise LearningError(f"{field_name} is required")
        if not isinstance(self.comparability, CohortComparability):
            raise LearningError("invalid comparability")
        for field_name in (
            "before_metric_microunits",
            "after_metric_microunits",
            "recurrence_before",
            "recurrence_after",
        ):
            value = getattr(self, field_name)
            if value is not None and type(value) is not int:
                raise LearningError(f"{field_name} must be an integer or null")
        if self.comparability is CohortComparability.INCOMPARABLE:
            if not (self.incomparable_reason or "").strip():
                raise LearningError(
                    "an incomparable cohort must state why the two cohorts cannot "
                    "be compared"
                )
            if (
                self.before_metric_microunits is not None
                or self.after_metric_microunits is not None
            ):
                raise LearningError(
                    "an incomparable cohort must not report a before/after metric; "
                    "reporting one would state a conclusion the cohorts cannot support"
                )
        object.__setattr__(
            self,
            "measured_at",
            require_utc(self.measured_at, field_name="measured_at"),
        )

    @property
    def measured_delta_microunits(self) -> int | None:
        """The observed change, or ``None`` when it was not measurable."""
        if self.comparability is CohortComparability.INCOMPARABLE:
            return None
        if (
            self.before_metric_microunits is None
            or self.after_metric_microunits is None
        ):
            return None
        return self.after_metric_microunits - self.before_metric_microunits

    @property
    def recurrence_delta(self) -> int | None:
        """Change in recurrence count, or ``None`` when not measurable."""
        if self.comparability is CohortComparability.INCOMPARABLE:
            return None
        if self.recurrence_before is None or self.recurrence_after is None:
            return None
        return self.recurrence_after - self.recurrence_before

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "effectiveness_id": self.effectiveness_id,
            "release_id": self.release_id,
            "comparability": self.comparability,
            "measured_at": self.measured_at,
            "before_window_ref": self.before_window_ref,
            "after_window_ref": self.after_window_ref,
            "before_metric_microunits": self.before_metric_microunits,
            "after_metric_microunits": self.after_metric_microunits,
            "recurrence_before": self.recurrence_before,
            "recurrence_after": self.recurrence_after,
            "incomparable_reason": self.incomparable_reason,
        }

    @property
    def effectiveness_hash(self) -> str:
        return stable_hash(EFFECTIVENESS_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class LearningChain:
    """The assembled lineage for one line of enquiry.

    Assembled by lookup rather than stored, so the chain cannot drift from the
    records it describes, and every link is checked to reference the previous one.
    """

    hypothesis: Hypothesis
    experiment: RegisteredExperiment
    conclusion: ResearchConclusionRecord | None = None
    release: ReleaseRecord | None = None
    effectiveness: PostChangeEffectiveness | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.hypothesis, Hypothesis):
            raise LearningError("a chain starts with a hypothesis")
        if not isinstance(self.experiment, RegisteredExperiment):
            raise LearningError("a chain requires a registered experiment")
        if self.experiment.hypothesis_id != self.hypothesis.hypothesis_id:
            raise LearningError("the experiment does not test the stated hypothesis")
        if self.conclusion is not None and (
            self.conclusion.experiment_id != self.experiment.experiment_id
        ):
            raise LearningError("the conclusion does not belong to the experiment")
        if self.release is not None:
            if self.conclusion is None:
                raise LearningError(
                    "a release cannot exist without a conclusion to release"
                )
            if self.release.conclusion_id != self.conclusion.conclusion_id:
                raise LearningError("the release does not reference the conclusion")
        if self.effectiveness is not None:
            if self.release is None:
                raise LearningError(
                    "post-change effectiveness requires a release to measure"
                )
            if self.effectiveness.release_id != self.release.release_id:
                raise LearningError(
                    "the effectiveness record does not reference the release"
                )

    @property
    def stage(self) -> str:
        """The furthest completed stage, so an unfinished chain is visible."""
        if self.effectiveness is not None:
            return "EFFECTIVENESS_MEASURED"
        if self.release is not None:
            return "RELEASED"
        if self.conclusion is not None:
            return "CONCLUDED"
        return "REGISTERED"

    @property
    def reached_a_conclusion(self) -> bool:
        return self.conclusion is not None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis.hypothesis_hash,
            "experiment": self.experiment.experiment_hash,
            "conclusion": (
                None if self.conclusion is None else self.conclusion.conclusion_hash
            ),
            "release": None if self.release is None else self.release.release_hash,
            "effectiveness": (
                None
                if self.effectiveness is None
                else self.effectiveness.effectiveness_hash
            ),
        }

    @property
    def chain_hash(self) -> str:
        return stable_hash(EXPERIMENT_IDENTITY_DOMAIN, self.identity_payload())


def build_learning_chain(
    *,
    hypothesis: Hypothesis,
    experiment: RegisteredExperiment,
    conclusion: ResearchConclusionRecord | None = None,
    release: ReleaseRecord | None = None,
    effectiveness: PostChangeEffectiveness | None = None,
) -> LearningChain:
    """Assemble a lineage, refusing any link that contradicts its predecessor."""
    return LearningChain(
        hypothesis=hypothesis,
        experiment=experiment,
        conclusion=conclusion,
        release=release,
        effectiveness=effectiveness,
    )


def require_validated_weakness(states: Iterable[WeaknessValidationState]) -> None:
    """Fail closed unless a hypothesis is grounded in a validated weakness.

    A hypothesis raised from a pending, rejected, or inconclusive finding is not
    grounded: the finding itself has not been established, so an experiment on it
    would test a guess.
    """
    resolved = tuple(states)
    if not resolved:
        raise LearningError("a hypothesis must be grounded in at least one finding")
    if not any(state is WeaknessValidationState.VALIDATED for state in resolved):
        raise LearningError(
            "a hypothesis requires a VALIDATED finding to be grounded in; a "
            "pending, rejected, or inconclusive finding does not establish one"
        )


def chain_for(
    chains: Sequence[LearningChain], *, hypothesis_id: str
) -> LearningChain | None:
    """Find the chain testing a hypothesis, if one exists."""
    for chain in chains:
        if chain.hypothesis.hypothesis_id == hypothesis_id:
            return chain
    return None


__all__ = [
    "COMPROMISED",
    "CONCLUSION_IDENTITY_DOMAIN",
    "EFFECTIVENESS_IDENTITY_DOMAIN",
    "EXPERIMENT_IDENTITY_DOMAIN",
    "HYPOTHESIS_IDENTITY_DOMAIN",
    "RELEASE_IDENTITY_DOMAIN",
    "CohortComparability",
    "EffectDirection",
    "Hypothesis",
    "LearningChain",
    "LearningError",
    "PostChangeEffectiveness",
    "RegisteredExperiment",
    "ReleaseRecord",
    "ResearchConclusion",
    "ResearchConclusionRecord",
    "build_learning_chain",
    "chain_for",
    "require_validated_weakness",
]
