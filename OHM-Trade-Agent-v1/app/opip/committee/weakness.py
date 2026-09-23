"""Immutable Weakness Finding Registry (IC-029 to IC-034).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

A weakness is not prose. It is a durable, structured record that can be validated
against a realised outcome, attributed to the role and model that discovered it,
and later checked for recurrence. Five properties are enforced here:

* **The original finding is immutable.** A finding is never edited. Validation,
  remediation, and post-change evidence are *appended*, so history reads as a
  sequence rather than a state that silently changes meaning.

* **The committee cannot certify itself.** A validation carries a validator kind,
  and the committee plane is refused as a validator: a model asserting its own
  finding is correct is not evidence. Validation requires a governed or human
  disposition grounded in an observed outcome.

* **Taxonomy evolves additively.** A finding records the taxonomy version in force
  when it was raised, so a later taxonomy change cannot retroactively re-label
  historical findings.

* **Recurrence is scope-bound.** A recurrence key includes an explicit scope
  discriminator, so two incidents that merely share a category are never merged
  into one "recurring" story.

* **Unknown stays unknown.** An economic effect that was not legitimately
  measurable is ``None``, never zero. Reporting an unmeasured weakness as costing
  nothing would make the weakest evidence look like the strongest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from app.opip.committee.roles import CommitteeRole
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

WEAKNESS_FINDING_SCHEMA_VERSION = 1
WEAKNESS_VALIDATION_SCHEMA_VERSION = 1
RECURRENCE_KEY_SCHEMA_VERSION = 1

WEAKNESS_FINDING_IDENTITY_DOMAIN = "COMMITTEE-WEAKNESS"
WEAKNESS_VALIDATION_IDENTITY_DOMAIN = "COMMITTEE-WEAKNESS-VALIDATION"
RECURRENCE_KEY_IDENTITY_DOMAIN = "COMMITTEE-RECURRENCE"

#: The taxonomy version this module implements. Recorded on every finding so a
#: later taxonomy change cannot retroactively re-label history.
WEAKNESS_TAXONOMY_VERSION = "weakness-taxonomy-v1"


class WeaknessCategory(str, Enum):
    """The controlled weakness taxonomy.

    ``OTHER`` exists because a closed taxonomy that cannot express a genuinely new
    weakness would force miscategorisation. It is not a loophole: ``OTHER``
    requires a specific structured statement and at least one evidence reference.
    """

    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    OBSERVABILITY_GAP = "OBSERVABILITY_GAP"
    DECISION_OR_QUALIFICATION_WEAKNESS = "DECISION_OR_QUALIFICATION_WEAKNESS"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    MISSED_OPPORTUNITY = "MISSED_OPPORTUNITY"
    REGIME_MISCLASSIFICATION = "REGIME_MISCLASSIFICATION"
    MODEL_ASSUMPTION_CONFLICT = "MODEL_ASSUMPTION_CONFLICT"
    ENTRY_TIMING = "ENTRY_TIMING"
    LIQUIDITY_EXECUTION = "LIQUIDITY_EXECUTION"
    SLIPPAGE = "SLIPPAGE"
    RISK_POLICY = "RISK_POLICY"
    PROTECTION_WEAKNESS = "PROTECTION_WEAKNESS"
    EXIT_POLICY = "EXIT_POLICY"
    POLICY_VERSION_WEAKNESS = "POLICY_VERSION_WEAKNESS"
    RECURRING_SYSTEM_WEAKNESS = "RECURRING_SYSTEM_WEAKNESS"
    OTHER = "OTHER"


class WeaknessValidationState(str, Enum):
    """The validation lifecycle of a finding.

    ``INCONCLUSIVE`` is a first-class outcome: a weakness that could not be
    resolved against the available outcome evidence must say so rather than being
    forced into validated or rejected.
    """

    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class ValidatorKind(str, Enum):
    """Who is permitted to validate a finding.

    ``COMMITTEE_MODEL`` is listed so it can be explicitly refused rather than
    silently absent: the plane that raised a finding may not certify it.
    """

    GOVERNED_REVIEW = "GOVERNED_REVIEW"
    HUMAN_OPERATOR = "HUMAN_OPERATOR"
    OUTCOME_EVIDENCE = "OUTCOME_EVIDENCE"
    COMMITTEE_MODEL = "COMMITTEE_MODEL"


#: Validators that may legitimately move a finding out of PENDING.
PERMITTED_VALIDATORS = frozenset(
    {
        ValidatorKind.GOVERNED_REVIEW,
        ValidatorKind.HUMAN_OPERATOR,
        ValidatorKind.OUTCOME_EVIDENCE,
    }
)


class WeaknessRegistryError(ValueError):
    """A weakness registry contract was violated."""


@dataclass(frozen=True)
class RecurrenceKey:
    """A scope-bound recurrence identity.

    The scope discriminator is required, so incidents that share only a category
    are not merged. Two slippage incidents on different instruments or strategies
    are two incidents, not one recurring pattern.
    """

    category: WeaknessCategory
    scope: str
    subject: str
    taxonomy_version: str = WEAKNESS_TAXONOMY_VERSION
    schema_version: int = RECURRENCE_KEY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RECURRENCE_KEY_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported RecurrenceKey schema_version")
        if not isinstance(self.category, WeaknessCategory):
            raise ValueError("invalid weakness category")
        for field_name in ("scope", "subject", "taxonomy_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise WeaknessRegistryError(f"{field_name} is required")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "category": self.category,
            "scope": self.scope,
            "subject": self.subject,
            "taxonomy_version": self.taxonomy_version,
        }

    @property
    def recurrence_key_id(self) -> str:
        return stable_hash(RECURRENCE_KEY_IDENTITY_DOMAIN, self.identity_payload())


def _validate_finding_schema_version(schema_version: object) -> None:
    if schema_version != WEAKNESS_FINDING_SCHEMA_VERSION or (
        type(schema_version) is not int
    ):
        raise ValueError("unsupported WeaknessFinding schema_version")


def _require_finding_text_fields(finding: "WeaknessFinding") -> None:
    for field_name in (
        "finding_id",
        "decision_context_id",
        "finding_statement",
        "taxonomy_version",
    ):
        value = getattr(finding, field_name)
        if not isinstance(value, str) or not value.strip():
            raise WeaknessRegistryError(f"{field_name} is required")


def _require_finding_category_and_key(finding: "WeaknessFinding") -> None:
    if not isinstance(finding.weakness_category, WeaknessCategory):
        raise WeaknessRegistryError("invalid weakness_category")
    if not isinstance(finding.recurrence_key, RecurrenceKey):
        raise WeaknessRegistryError("recurrence_key is required")
    if finding.recurrence_key.category is not finding.weakness_category:
        raise WeaknessRegistryError(
            "the recurrence key category must match the finding category"
        )


def _require_finding_evidence(evidence_refs: object) -> None:
    if not isinstance(evidence_refs, tuple) or not evidence_refs:
        raise WeaknessRegistryError(
            "a finding must cite at least one piece of evidence"
        )


def _require_finding_role(role: object) -> None:
    if role is not None and not isinstance(role, CommitteeRole):
        raise WeaknessRegistryError("invalid committee_role")


def _require_optional_effect(value: object) -> None:
    if value is not None and type(value) is not int:
        raise WeaknessRegistryError(
            "economic_effect_microunits must be an integer or null"
        )


def _normalise_finding_timestamps(finding: "WeaknessFinding") -> None:
    for field_name in ("detected_at", "evidence_cutoff"):
        object.__setattr__(
            finding,
            field_name,
            require_utc(getattr(finding, field_name), field_name=field_name),
        )
    if finding.evidence_cutoff > finding.detected_at:
        raise WeaknessRegistryError(
            "evidence_cutoff cannot be after detected_at; a finding may not "
            "cite evidence from its own future"
        )


def _require_finding_statement_quality(finding: "WeaknessFinding") -> None:
    """``OTHER`` must be specific, or the escape hatch replaces categorising.

    Evidence is required of every finding, so that part needs no separate rule.
    """
    if finding.weakness_category is not WeaknessCategory.OTHER:
        return
    if len(finding.finding_statement.strip()) < 20:
        raise WeaknessRegistryError(
            "a weakness categorised OTHER requires a specific structured "
            "statement, not a placeholder"
        )


@dataclass(frozen=True)
class WeaknessFinding:
    """One immutable weakness finding.

    Every field the architecture requires is present. Fields that may legitimately
    not exist — a paper trade, an economic effect, a hypothesis — are ``None`` and
    stay ``None``; nothing is defaulted to zero or to an empty success.
    """

    finding_id: str
    decision_context_id: str
    weakness_category: WeaknessCategory
    finding_statement: str
    detected_at: datetime
    evidence_cutoff: datetime
    evidence_refs: tuple[str, ...]
    recurrence_key: RecurrenceKey
    taxonomy_version: str = WEAKNESS_TAXONOMY_VERSION
    paper_trade_id: str | None = None
    committee_case_id: str | None = None
    committee_assessment_id: str | None = None
    baseline_decision_ref: str | None = None
    committee_role: CommitteeRole | None = None
    provider: str | None = None
    model: str | None = None
    prompt_policy_version: str | None = None
    #: Measured economic effect, or ``None`` when it was not legitimately
    #: measurable. Never zero-by-default.
    economic_effect_microunits: int | None = None
    schema_version: int = WEAKNESS_FINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_finding_schema_version(self.schema_version)
        _require_finding_text_fields(self)
        _require_finding_category_and_key(self)
        _require_finding_evidence(self.evidence_refs)
        _require_finding_role(self.committee_role)
        _require_optional_effect(self.economic_effect_microunits)
        _normalise_finding_timestamps(self)
        _require_finding_statement_quality(self)

    @property
    def is_economically_measured(self) -> bool:
        return self.economic_effect_microunits is not None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "finding_id": self.finding_id,
            "decision_context_id": self.decision_context_id,
            "weakness_category": self.weakness_category,
            "finding_statement": self.finding_statement,
            "detected_at": self.detected_at,
            "evidence_cutoff": self.evidence_cutoff,
            "evidence_refs": self.evidence_refs,
            "recurrence_key": self.recurrence_key.recurrence_key_id,
            "taxonomy_version": self.taxonomy_version,
            "paper_trade_id": self.paper_trade_id,
            "committee_case_id": self.committee_case_id,
            "committee_assessment_id": self.committee_assessment_id,
            "baseline_decision_ref": self.baseline_decision_ref,
            "committee_role": self.committee_role,
            "provider": self.provider,
            "model": self.model,
            "prompt_policy_version": self.prompt_policy_version,
            "economic_effect_microunits": self.economic_effect_microunits,
        }

    @property
    def finding_hash(self) -> str:
        return stable_hash(WEAKNESS_FINDING_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class WeaknessValidation:
    """An appended validation of a finding.

    Carries the validator kind, so a self-certifying committee is refused rather
    than merely discouraged.
    """

    validation_id: str
    finding_id: str
    state: WeaknessValidationState
    validator: ValidatorKind
    validated_at: datetime
    rationale: str
    outcome_refs: tuple[str, ...] = ()
    economic_effect_microunits: int | None = None
    schema_version: int = WEAKNESS_VALIDATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WEAKNESS_VALIDATION_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported WeaknessValidation schema_version")
        for field_name in ("validation_id", "finding_id", "rationale"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise WeaknessRegistryError(f"{field_name} is required")
        if not isinstance(self.state, WeaknessValidationState):
            raise WeaknessRegistryError("invalid validation state")
        if not isinstance(self.validator, ValidatorKind):
            raise WeaknessRegistryError("invalid validator")
        if self.validator not in PERMITTED_VALIDATORS:
            raise WeaknessRegistryError(
                "the committee plane may not validate its own finding; validation "
                f"requires one of {sorted(kind.value for kind in PERMITTED_VALIDATORS)}"
            )
        if self.state is WeaknessValidationState.PENDING:
            raise WeaknessRegistryError(
                "a validation record must resolve a finding; PENDING is the "
                "absence of a validation, not one"
            )
        if self.state in (
            WeaknessValidationState.VALIDATED,
            WeaknessValidationState.REJECTED,
        ) and not self.outcome_refs:
            raise WeaknessRegistryError(
                f"a {self.state.value} validation must reference the realised "
                "outcome it was judged against"
            )
        if self.economic_effect_microunits is not None and (
            type(self.economic_effect_microunits) is not int
        ):
            raise WeaknessRegistryError(
                "economic_effect_microunits must be an integer or null"
            )
        object.__setattr__(
            self,
            "validated_at",
            require_utc(self.validated_at, field_name="validated_at"),
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "validation_id": self.validation_id,
            "finding_id": self.finding_id,
            "state": self.state,
            "validator": self.validator,
            "validated_at": self.validated_at,
            "rationale": self.rationale,
            "outcome_refs": self.outcome_refs,
            "economic_effect_microunits": self.economic_effect_microunits,
        }

    @property
    def validation_hash(self) -> str:
        return stable_hash(WEAKNESS_VALIDATION_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class WeaknessFollowUp:
    """An appended remediation or post-change record.

    Appended rather than folded into the finding, so the original statement stays
    readable exactly as it was raised.
    """

    follow_up_id: str
    finding_id: str
    recorded_at: datetime
    remediation_ref: str | None = None
    resolution_release_sha: str | None = None
    post_change_result: str | None = None
    hypothesis_id: str | None = None
    experiment_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("follow_up_id", "finding_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise WeaknessRegistryError(f"{field_name} is required")
        object.__setattr__(
            self,
            "recorded_at",
            require_utc(self.recorded_at, field_name="recorded_at"),
        )
        if not any(
            (
                self.remediation_ref,
                self.resolution_release_sha,
                self.post_change_result,
                self.hypothesis_id,
                self.experiment_id,
            )
        ):
            raise WeaknessRegistryError(
                "a follow-up record must carry remediation, a resolution release, a "
                "post-change result, a hypothesis, or an experiment"
            )


@dataclass
class WeaknessRegistry:
    """An append-only registry of findings, validations, and follow-ups.

    Findings are never replaced. Validity is computed from the appended records, so
    the current state of a finding is derivable from history rather than stored
    beside it.
    """

    _findings: dict[str, WeaknessFinding] = field(default_factory=dict)
    _validations: list[WeaknessValidation] = field(default_factory=list)
    _follow_ups: list[WeaknessFollowUp] = field(default_factory=list)
    _rejections: list[str] = field(default_factory=list)

    def append_finding(self, finding: WeaknessFinding) -> WeaknessFinding:
        """Register a finding. A duplicate id is refused, not overwritten."""
        if not isinstance(finding, WeaknessFinding):
            raise WeaknessRegistryError("append_finding requires a WeaknessFinding")
        if finding.finding_id in self._findings:
            raise WeaknessRegistryError(
                f"finding {finding.finding_id!r} is already registered; findings are "
                "immutable and cannot be replaced"
            )
        self._findings[finding.finding_id] = finding
        return finding

    def append_validation(self, validation: WeaknessValidation) -> WeaknessValidation:
        if not isinstance(validation, WeaknessValidation):
            raise WeaknessRegistryError(
                "append_validation requires a WeaknessValidation"
            )
        if validation.finding_id not in self._findings:
            raise WeaknessRegistryError(
                f"validation references unknown finding {validation.finding_id!r}"
            )
        self._validations.append(validation)
        return validation

    def append_follow_up(self, follow_up: WeaknessFollowUp) -> WeaknessFollowUp:
        if not isinstance(follow_up, WeaknessFollowUp):
            raise WeaknessRegistryError("append_follow_up requires a WeaknessFollowUp")
        if follow_up.finding_id not in self._findings:
            raise WeaknessRegistryError(
                f"follow-up references unknown finding {follow_up.finding_id!r}"
            )
        self._follow_ups.append(follow_up)
        return follow_up

    def note_rejection(self, reason: str) -> None:
        """Record a refused write, so a rejected append is not invisible."""
        self._rejections.append(reason)

    @property
    def rejections(self) -> tuple[str, ...]:
        return tuple(self._rejections)

    def findings(self) -> tuple[WeaknessFinding, ...]:
        return tuple(self._findings.values())

    def validations_for(self, finding_id: str) -> tuple[WeaknessValidation, ...]:
        return tuple(
            validation
            for validation in self._validations
            if validation.finding_id == finding_id
        )

    def follow_ups_for(self, finding_id: str) -> tuple[WeaknessFollowUp, ...]:
        return tuple(
            follow_up
            for follow_up in self._follow_ups
            if follow_up.finding_id == finding_id
        )

    def state_of(self, finding_id: str) -> WeaknessValidationState:
        """The current validation state, derived from appended history.

        The latest validation wins; with none, the finding is ``PENDING``. A
        finding never becomes valid because nobody looked at it.
        """
        if finding_id not in self._findings:
            raise WeaknessRegistryError(f"unknown finding {finding_id!r}")
        validations = self.validations_for(finding_id)
        if not validations:
            return WeaknessValidationState.PENDING
        return max(validations, key=lambda item: item.validated_at).state

    def recurrence_groups(self) -> Mapping[str, tuple[WeaknessFinding, ...]]:
        """Group findings by scope-bound recurrence key.

        Only genuinely same-scope, same-subject, same-category findings group
        together, so unrelated incidents are never merged into a false pattern.
        """
        groups: dict[str, list[WeaknessFinding]] = {}
        for finding in self._findings.values():
            groups.setdefault(finding.recurrence_key.recurrence_key_id, []).append(finding)
        return {key: tuple(items) for key, items in groups.items()}

    def recurring_findings(self, *, minimum_occurrences: int = 2) -> tuple[str, ...]:
        """Recurrence keys seen at least ``minimum_occurrences`` times, in order."""
        if type(minimum_occurrences) is not int or minimum_occurrences < 2:
            raise WeaknessRegistryError("minimum_occurrences must be an integer >= 2")
        groups = self.recurrence_groups()
        return tuple(
            sorted(
                key
                for key, findings in groups.items()
                if len(findings) >= minimum_occurrences
            )
        )

    def validated_economic_effect(self, finding_id: str) -> int | None:
        """The measured economic effect of a validated finding, or ``None``.

        Returns ``None`` when the finding is not validated or the effect was never
        legitimately measured. It never returns zero to mean "not measured".
        """
        state = self.state_of(finding_id)
        if state is not WeaknessValidationState.VALIDATED:
            return None
        validations = [
            validation
            for validation in self.validations_for(finding_id)
            if validation.state is WeaknessValidationState.VALIDATED
        ]
        latest = max(validations, key=lambda item: item.validated_at)
        if latest.economic_effect_microunits is not None:
            return latest.economic_effect_microunits
        return self._findings[finding_id].economic_effect_microunits

    def summary(self) -> Mapping[str, int]:
        """Counts by validation state, with every state present.

        A state that no finding occupies is reported as zero rather than omitted,
        so a reader cannot mistake an absent key for an unimplemented state.
        """
        counts = dict.fromkeys(WeaknessValidationState, 0)
        for finding in self._findings.values():
            counts[self.state_of(finding.finding_id)] += 1
        return {
            state.value: counts[state]
            for state in WeaknessValidationState
        }


def build_finding(
    *,
    finding_id: str,
    decision_context_id: str,
    weakness_category: WeaknessCategory,
    finding_statement: str,
    detected_at: datetime,
    evidence_cutoff: datetime,
    evidence_refs: Sequence[str],
    recurrence_scope: str,
    recurrence_subject: str,
    **overrides: Any,
) -> WeaknessFinding:
    """Build a finding together with its scope-bound recurrence key.

    The key is derived here rather than accepted from a caller, so a finding cannot
    arrive with a recurrence identity unrelated to its own category.
    """
    key = RecurrenceKey(
        category=weakness_category,
        scope=recurrence_scope,
        subject=recurrence_subject,
    )
    return WeaknessFinding(
        finding_id=finding_id,
        decision_context_id=decision_context_id,
        weakness_category=weakness_category,
        finding_statement=finding_statement,
        detected_at=detected_at,
        evidence_cutoff=evidence_cutoff,
        evidence_refs=tuple(evidence_refs),
        recurrence_key=key,
        **overrides,
    )


__all__ = [
    "PERMITTED_VALIDATORS",
    "RECURRENCE_KEY_IDENTITY_DOMAIN",
    "RECURRENCE_KEY_SCHEMA_VERSION",
    "WEAKNESS_FINDING_IDENTITY_DOMAIN",
    "WEAKNESS_FINDING_SCHEMA_VERSION",
    "WEAKNESS_TAXONOMY_VERSION",
    "WEAKNESS_VALIDATION_IDENTITY_DOMAIN",
    "WEAKNESS_VALIDATION_SCHEMA_VERSION",
    "RecurrenceKey",
    "ValidatorKind",
    "WeaknessCategory",
    "WeaknessFinding",
    "WeaknessFollowUp",
    "WeaknessRegistry",
    "WeaknessRegistryError",
    "WeaknessValidation",
    "WeaknessValidationState",
    "build_finding",
]
