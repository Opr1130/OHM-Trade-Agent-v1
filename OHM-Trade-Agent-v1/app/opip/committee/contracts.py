"""Intelligence Committee contracts (shadow-only, non-authoritative).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module defines the immutable, auditable vocabulary the Intelligence
Committee runtime uses to obtain, preserve, and evaluate independent model
opinions. Nothing here grants, widens, or implies trading authority. A
committee observation is research evidence; it is never execution authority.

The contracts deliberately reuse the frozen ``app.opip.decision_intelligence``
plane for canonical persistence vocabulary (``Provenance``, canonical
serialization, content-derived identity) rather than introducing a second,
inconsistent storage system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.decision_intelligence.identity import Provenance
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

COMMITTEE_CASE_SCHEMA_VERSION = 1
COMMITTEE_POLICY_SCHEMA_VERSION = 1
EVIDENCE_SNAPSHOT_SCHEMA_VERSION = 1
STRUCTURED_OPINION_SCHEMA_VERSION = 1
PROVIDER_CALL_OUTCOME_SCHEMA_VERSION = 1
COMMITTEE_CASE_OUTCOME_SCHEMA_VERSION = 1

#: Distinct content-identity hash domains. Distinct domains keep two different
#: record kinds from ever colliding on a 32-character digest.
COMMITTEE_CASE_IDENTITY_DOMAIN = "COMMITTEE-CASE"
COMMITTEE_POLICY_IDENTITY_DOMAIN = "COMMITTEE-POLICY"
EVIDENCE_SNAPSHOT_IDENTITY_DOMAIN = "COMMITTEE-EVIDENCE"
EVIDENCE_SNAPSHOT_ID_DOMAIN = "COMMITTEE-SNAPSHOT"
STRUCTURED_OPINION_IDENTITY_DOMAIN = "COMMITTEE-OPINION"
STRUCTURED_OPINION_ID_DOMAIN = "COMMITTEE-OPINION-ID"
PROVIDER_CALL_OUTCOME_IDENTITY_DOMAIN = "COMMITTEE-CALL"
COMMITTEE_CASE_OUTCOME_IDENTITY_DOMAIN = "COMMITTEE-OUTCOME"
LOGICAL_OBSERVATION_IDENTITY_DOMAIN = "COMMITTEE-LOGICAL"

#: Evidence metadata that the snapshot owns. A payload may not supply these, so
#: the model can never be shown an identity, source, or timestamp that is not the
#: authenticated one the snapshot will validate citations against.
RESERVED_EVIDENCE_FIELDS = frozenset(
    {"evidence_id", "source_id", "available_at"}
)

#: The complete, closed set of fields a model-bound evidence item may carry.
#: Anything outside this set is rejected before an external call is built.
MODEL_BOUND_ITEM_FIELDS = frozenset(
    {
        "evidence_id",
        "source_id",
        "available_at",
        "observed_at",
        "instrument_id",
        "metric_name",
        "metric_value",
        "metric_unit",
        "direction",
        "note",
    }
)


class CaseType(str, Enum):
    """What kind of research question a committee case asks."""

    MARKET_OPPORTUNITY = "MARKET_OPPORTUNITY"
    PRICE_MOVEMENT = "PRICE_MOVEMENT"
    EVENT_INTERPRETATION = "EVENT_INTERPRETATION"
    OUTCOME_HYPOTHESIS = "OUTCOME_HYPOTHESIS"


class ProviderFamily(str, Enum):
    """Logical provider families the committee can seat.

    ``INDEPENDENT_REVIEWER`` is a reserved seat for an independent fifth
    opinion. No supported integration for it exists in this repository, so it
    is defined here and marked unavailable at runtime rather than being
    simulated or silently substituted.
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE_GEMINI = "google_gemini"
    DEEPSEEK = "deepseek"
    INDEPENDENT_REVIEWER = "independent_reviewer"


class ObservationStatus(str, Enum):
    """Terminal status of a single provider call for a single case.

    Only ``COMPLETED`` and ``DUPLICATE_OK`` count as an answered seat. A
    failure is not a vote, an abstention is not a negative vote, and a missing
    output is never agreement.
    """

    COMPLETED = "COMPLETED"
    INVALID = "INVALID"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED_BUDGET = "SKIPPED_BUDGET"
    DUPLICATE_OK = "DUPLICATE_OK"


class ProviderFailureClass(str, Enum):
    """Typed provider failure classification.

    A failure is never a vote, never an abstention, and never agreement.
    """

    TIMEOUT = "TIMEOUT"
    AUTH_FAILURE = "AUTH_FAILURE"
    RATE_LIMIT = "RATE_LIMIT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_IDENTITY_MISMATCH = "PROVIDER_IDENTITY_MISMATCH"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_VALIDATION_FAILURE = "SCHEMA_VALIDATION_FAILURE"
    POLICY_REJECTION = "POLICY_REJECTION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ReproducibilityClass(str, Enum):
    """How far an observation can be reproduced.

    External providers cannot guarantee bit-for-bit determinism, so the class
    is recorded explicitly instead of being asserted away.
    """

    REPRODUCIBLE_INPUT = "REPRODUCIBLE_INPUT"
    REPEATABLE_CONFIGURATION = "REPEATABLE_CONFIGURATION"
    NONDETERMINISTIC_PROVIDER_OUTPUT = "NONDETERMINISTIC_PROVIDER_OUTPUT"


class EvidenceSufficiency(str, Enum):
    """A model's own statement about whether the evidence was enough."""

    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class DirectionalAssessment(str, Enum):
    """A model's directional reading of the evidence."""

    SUPPORTIVE = "SUPPORTIVE"
    OPPOSING = "OPPOSING"
    NEUTRAL = "NEUTRAL"
    UNCERTAIN = "UNCERTAIN"


class ResearchAction(str, Enum):
    """The only actions a committee member may recommend."""

    NO_ACTION = "NO_ACTION"
    GATHER_MORE_EVIDENCE = "GATHER_MORE_EVIDENCE"
    REVISIT_AT_NEXT_WINDOW = "REVISIT_AT_NEXT_WINDOW"
    ESCALATE_FOR_HUMAN_REVIEW = "ESCALATE_FOR_HUMAN_REVIEW"


class EvaluationPhase(str, Enum):
    """Retrospective and prospective evaluation never share metrics."""

    RETROSPECTIVE = "RETROSPECTIVE"
    PROSPECTIVE = "PROSPECTIVE"


class CostCompleteness(str, Enum):
    """Cost is either known or explicitly unknown - never invented."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


def freeze_nested(value: Any) -> Any:
    """Return a deeply immutable copy of nested JSON-like committee data."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): freeze_nested(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_nested(item) for item in value)
    if isinstance(value, Enum):
        return value.value
    return value


def _as_exact_int(value: object, *, field_name: str) -> int:
    """Narrow ``value`` to a real ``int`` or fail.

    ``isinstance(True, int)`` is ``True`` in Python, so only an identity check
    keeps an integer field from silently accepting ``True``.
    """
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    return int(value)


def _require_exact_int(
    value: object,
    *,
    field_name: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Require an exact integer, optionally bounded."""
    number = _as_exact_int(value, field_name=field_name)
    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}")
    return number


def _require_optional_exact_int(
    value: object,
    *,
    field_name: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _require_exact_int(
        value,
        field_name=field_name,
        minimum=minimum,
        maximum=maximum,
    )


def _require_non_empty_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _require_optional_opaque_ref(value: object, *, field_name: str) -> str | None:
    """Accept a non-empty opaque id, or ``None``. Empty/whitespace fails closed."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty when provided")
    return text


def _require_str_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a sequence of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} entries must be non-empty strings")
        items.append(item.strip())
    return tuple(items)


_CALL_OUTCOME_REQUIRED_FIELDS = (
    "logical_observation_id",
    "case_id",
    "requested_model",
    "input_hash",
)

_CALL_OUTCOME_OPTIONAL_INT_FIELDS = (
    "latency_micros",
    "input_tokens",
    "output_tokens",
    "estimated_cost_microunits",
)


def _validate_call_outcome_identity(outcome: "ProviderCallOutcome") -> None:
    """Validate the identity, enum, and numeric surface of a call outcome."""
    if outcome.schema_version != PROVIDER_CALL_OUTCOME_SCHEMA_VERSION or (
        type(outcome.schema_version) is not int
    ):
        raise ValueError("unsupported ProviderCallOutcome schema_version")
    for field_name in _CALL_OUTCOME_REQUIRED_FIELDS:
        object.__setattr__(
            outcome,
            field_name,
            _require_non_empty_str(getattr(outcome, field_name), field_name=field_name),
        )
    for field_name, expected in (
        ("provider_family", ProviderFamily),
        ("status", ObservationStatus),
        ("reproducibility", ReproducibilityClass),
        ("cost_completeness", CostCompleteness),
    ):
        if not isinstance(getattr(outcome, field_name), expected):
            raise ValueError(f"invalid {field_name}")
    _require_exact_int(
        outcome.attempt, field_name="attempt", minimum=1, maximum=5
    )
    for field_name in _CALL_OUTCOME_OPTIONAL_INT_FIELDS:
        _require_optional_exact_int(
            getattr(outcome, field_name), field_name=field_name, minimum=0
        )
    object.__setattr__(
        outcome,
        "request_at",
        require_utc(outcome.request_at, field_name="request_at"),
    )
    if outcome.response_at is None:
        return
    object.__setattr__(
        outcome,
        "response_at",
        require_utc(outcome.response_at, field_name="response_at"),
    )
    if outcome.response_at < outcome.request_at:
        raise ValueError("response_at must be >= request_at")


def _validate_completed_outcome(outcome: "ProviderCallOutcome") -> None:
    if outcome.opinion is None:
        raise ValueError("a COMPLETED outcome must carry a validated opinion")
    if outcome.failure_class is not None:
        raise ValueError("a COMPLETED outcome cannot carry a failure class")
    if outcome.response_at is None:
        raise ValueError("a COMPLETED outcome requires response_at")
    if outcome.opinion.case_id != outcome.case_id:
        raise ValueError("opinion case_id must match the call outcome")
    if outcome.replay_divergence_detected:
        raise ValueError("a first committed opinion cannot be a replay divergence")
    if not outcome.reported_provider or not outcome.reported_model:
        raise ValueError("a committed outcome must record the served identity")


def _validate_duplicate_outcome(outcome: "ProviderCallOutcome") -> None:
    if outcome.opinion is None:
        raise ValueError("a DUPLICATE_OK outcome must carry the committed opinion")
    if outcome.failure_class is not None:
        raise ValueError("a DUPLICATE_OK outcome cannot carry a failure class")
    if outcome.opinion.case_id != outcome.case_id:
        raise ValueError("opinion case_id must match the call outcome")
    if outcome.replay_divergence_detected:
        raise ValueError(
            "a divergent replay is rejected, not recorded as a duplicate"
        )
    if not outcome.reported_provider or not outcome.reported_model:
        raise ValueError("a duplicate outcome must record the served identity")


def _validate_failed_outcome(outcome: "ProviderCallOutcome") -> None:
    if outcome.failure_class is None:
        raise ValueError(f"a {outcome.status.value} outcome requires a failure class")
    if outcome.opinion is not None:
        raise ValueError("a failed outcome cannot carry an opinion")


def _validate_unavailable_outcome(outcome: "ProviderCallOutcome") -> None:
    if outcome.failure_class is not ProviderFailureClass.PROVIDER_UNAVAILABLE:
        raise ValueError(
            "an UNAVAILABLE outcome must be classified PROVIDER_UNAVAILABLE"
        )
    if outcome.opinion is not None:
        raise ValueError("an unavailable seat cannot carry an opinion")


def _validate_budget_skipped_outcome(outcome: "ProviderCallOutcome") -> None:
    if outcome.failure_class is not None:
        raise ValueError("a budget skip is a policy outcome, not a failure")
    if outcome.opinion is not None:
        raise ValueError("a budget-skipped seat cannot carry an opinion")


_CALL_OUTCOME_STATUS_VALIDATORS = {
    ObservationStatus.COMPLETED: _validate_completed_outcome,
    ObservationStatus.DUPLICATE_OK: _validate_duplicate_outcome,
    ObservationStatus.FAILED: _validate_failed_outcome,
    ObservationStatus.INVALID: _validate_failed_outcome,
    ObservationStatus.UNAVAILABLE: _validate_unavailable_outcome,
    ObservationStatus.SKIPPED_BUDGET: _validate_budget_skipped_outcome,
}


def _validate_call_outcome_status(outcome: "ProviderCallOutcome") -> None:
    """Validate the status-specific form of a call outcome."""
    validator = _CALL_OUTCOME_STATUS_VALIDATORS.get(outcome.status)
    if validator is None:
        raise ValueError(f"undeclared observation status: {outcome.status}")
    validator(outcome)
    cost_fields = (
        outcome.input_tokens,
        outcome.output_tokens,
        outcome.estimated_cost_microunits,
    )
    if any(value is None for value in cost_fields) and (
        outcome.cost_completeness is CostCompleteness.COMPLETE
    ):
        raise ValueError("COMPLETE cost requires tokens and cost to be present")


@dataclass(frozen=True)
class EvidenceItem:
    """One point-in-time evidence element available to every committee seat.

    ``available_at`` is the moment the evidence became available. It must be at
    or before the case evidence cutoff; the cutoff gate is enforced in
    :mod:`app.opip.committee.evidence`, so a look-ahead row cannot be sealed
    into a prospective snapshot.
    """

    evidence_id: str
    source_id: str
    available_at: datetime
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            _require_non_empty_str(self.evidence_id, field_name="evidence_id"),
        )
        object.__setattr__(
            self,
            "source_id",
            _require_non_empty_str(self.source_id, field_name="source_id"),
        )
        object.__setattr__(
            self,
            "available_at",
            require_utc(self.available_at, field_name="available_at"),
        )
        object.__setattr__(self, "payload", freeze_nested(dict(self.payload)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "available_at": self.available_at,
            "payload": {key: self.payload[key] for key in sorted(self.payload)},
        }


@dataclass(frozen=True)
class EvidenceSnapshot:
    """The sealed, logically equivalent input every committee seat receives.

    Identity is content-derived rather than caller-supplied, so a snapshot hash
    can never disagree with the evidence it claims to describe.
    """

    case_id: str
    case_type: CaseType
    evidence_cutoff_at: datetime
    assembled_at: datetime
    items: tuple[EvidenceItem, ...]
    source_refs: tuple[str, ...]
    committee_policy_version: str
    prompt_template_id: str
    prompt_version: str
    instrument_id: str | None = None
    strategy_context_id: str | None = None
    schema_version: int = EVIDENCE_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_SNAPSHOT_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported EvidenceSnapshot schema_version")
        for field_name in (
            "case_id",
            "committee_policy_version",
            "prompt_template_id",
            "prompt_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_non_empty_str(getattr(self, field_name), field_name=field_name),
            )
        if not isinstance(self.case_type, CaseType):
            raise ValueError("invalid case_type")
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("EvidenceSnapshot requires at least one evidence item")
        for item in self.items:
            if not isinstance(item, EvidenceItem):
                raise ValueError("EvidenceSnapshot items must be EvidenceItem")
        object.__setattr__(
            self,
            "evidence_cutoff_at",
            require_utc(self.evidence_cutoff_at, field_name="evidence_cutoff_at"),
        )
        object.__setattr__(
            self,
            "assembled_at",
            require_utc(self.assembled_at, field_name="assembled_at"),
        )
        object.__setattr__(
            self,
            "source_refs",
            _require_str_tuple(self.source_refs, field_name="source_refs"),
        )
        if self.assembled_at < self.evidence_cutoff_at:
            raise ValueError("assembled_at must be >= evidence_cutoff_at")
        seen: set[str] = set()
        for item in self.items:
            if item.evidence_id in seen:
                raise ValueError("duplicate evidence_id in snapshot")
            seen.add(item.evidence_id)
            if item.available_at > self.evidence_cutoff_at:
                raise ValueError(
                    "evidence available after the cutoff cannot enter the snapshot"
                )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "case_type": self.case_type,
            "evidence_cutoff_at": self.evidence_cutoff_at,
            "instrument_id": self.instrument_id,
            "strategy_context_id": self.strategy_context_id,
            "committee_policy_version": self.committee_policy_version,
            "prompt_template_id": self.prompt_template_id,
            "prompt_version": self.prompt_version,
            "source_refs": self.source_refs,
            "items": tuple(item.as_dict() for item in self.items),
        }

    @property
    def snapshot_hash(self) -> str:
        return stable_hash(
            EVIDENCE_SNAPSHOT_IDENTITY_DOMAIN, self.identity_payload()
        )

    @property
    def snapshot_id(self) -> str:
        """Content-derived snapshot identity in its own hash domain."""
        return stable_hash(EVIDENCE_SNAPSHOT_ID_DOMAIN, self.identity_payload())

    def model_bound_view(self) -> dict[str, Any]:
        """The only representation permitted to leave the process boundary.

        Authenticated metadata is written last and cannot be overridden by an
        evidence payload. Otherwise a payload carrying an allowlisted key such as
        ``evidence_id`` would show the model a value that is absent from the
        snapshot's citable reference set - so a correct citation of the visible id
        would be rejected - and could spoof source or timing metadata.
        """
        return {
            "case_id": self.case_id,
            "case_type": self.case_type.value,
            "evidence_cutoff_at": self.evidence_cutoff_at.isoformat(),
            "instrument_id": self.instrument_id,
            "strategy_context_id": self.strategy_context_id,
            "prompt_version": self.prompt_version,
            "evidence": tuple(
                {
                    **{
                        key: item.payload[key]
                        for key in sorted(item.payload)
                        if key in MODEL_BOUND_ITEM_FIELDS
                        and key not in RESERVED_EVIDENCE_FIELDS
                    },
                    "evidence_id": item.evidence_id,
                    "source_id": item.source_id,
                    "available_at": item.available_at.isoformat(),
                }
                for item in self.items
            ),
        }


@dataclass(frozen=True)
class CanonicalDecisionBinding:
    """Opaque by-reference link from a committee case to a canonical decision.

    MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

    This closes the learning-loop edge *recommendation → canonical decision*
    without writing to Decision Intelligence streams and without inventing a
    second decision authority. Both fields are opaque string references only;
    at least one must be set. Empty or whitespace-only refs fail closed.

    Presence of a binding never grants admission, ranking, sizing, execution,
    or promotion authority. A governed DI/canonical writer bridge remains a
    separate, future, human-approved change.
    """

    decision_id: str | None = None
    episode_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_id",
            _require_optional_opaque_ref(self.decision_id, field_name="decision_id"),
        )
        object.__setattr__(
            self,
            "episode_id",
            _require_optional_opaque_ref(self.episode_id, field_name="episode_id"),
        )
        if self.decision_id is None and self.episode_id is None:
            raise ValueError(
                "canonical binding requires decision_id and/or episode_id"
            )

    def identity_payload(self) -> dict[str, str | None]:
        return {
            "decision_id": self.decision_id,
            "episode_id": self.episode_id,
        }


@dataclass(frozen=True)
class CommitteePolicy:
    """Versioned committee configuration. No trading meaning is attached."""

    policy_version: str
    seated_providers: tuple[ProviderFamily, ...]
    prompt_template_id: str
    prompt_version: str
    max_attempts_per_seat: int = 1
    max_estimated_cost_microunits: int | None = None
    schema_version: int = COMMITTEE_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != COMMITTEE_POLICY_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported CommitteePolicy schema_version")
        for field_name in ("policy_version", "prompt_template_id", "prompt_version"):
            object.__setattr__(
                self,
                field_name,
                _require_non_empty_str(getattr(self, field_name), field_name=field_name),
            )
        if not isinstance(self.seated_providers, tuple) or not self.seated_providers:
            raise ValueError("CommitteePolicy requires at least one seated provider")
        seen: set[ProviderFamily] = set()
        for provider in self.seated_providers:
            if not isinstance(provider, ProviderFamily):
                raise ValueError("invalid seated provider family")
            if provider in seen:
                raise ValueError("duplicate seated provider family")
            seen.add(provider)
        _require_exact_int(
            self.max_attempts_per_seat,
            field_name="max_attempts_per_seat",
            minimum=1,
            maximum=5,
        )
        _require_optional_exact_int(
            self.max_estimated_cost_microunits,
            field_name="max_estimated_cost_microunits",
            minimum=0,
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "seated_providers": self.seated_providers,
            "prompt_template_id": self.prompt_template_id,
            "prompt_version": self.prompt_version,
            "max_attempts_per_seat": self.max_attempts_per_seat,
            "max_estimated_cost_microunits": self.max_estimated_cost_microunits,
        }

    @property
    def policy_hash(self) -> str:
        return stable_hash(COMMITTEE_POLICY_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class CommitteeCase:
    """A single governed research question put to the committee.

    The case binds a sealed evidence snapshot to a committee policy. It carries
    no admission, ranking, sizing, or execution meaning.

    An optional :class:`CanonicalDecisionBinding` may reference an existing
    opaque canonical decision and/or episode identity by string only. The
    binding is advisory linkage for learning attribution; it is not a second
    decision authority and never writes to Decision Intelligence streams.
    """

    case_id: str
    case_type: CaseType
    snapshot: EvidenceSnapshot
    policy: CommitteePolicy
    created_at: datetime
    provenance: Provenance
    instrument_id: str | None = None
    strategy_context_id: str | None = None
    canonical_binding: CanonicalDecisionBinding | None = None
    schema_version: int = COMMITTEE_CASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != COMMITTEE_CASE_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported CommitteeCase schema_version")
        object.__setattr__(
            self,
            "case_id",
            _require_non_empty_str(self.case_id, field_name="case_id"),
        )
        if not isinstance(self.case_type, CaseType):
            raise ValueError("invalid case_type")
        if not isinstance(self.snapshot, EvidenceSnapshot):
            raise ValueError("CommitteeCase requires an EvidenceSnapshot")
        if not isinstance(self.policy, CommitteePolicy):
            raise ValueError("CommitteeCase requires a CommitteePolicy")
        if self.snapshot.case_id != self.case_id:
            raise ValueError("snapshot case_id must match the committee case")
        if self.snapshot.case_type is not self.case_type:
            raise ValueError("snapshot case_type must match the committee case")
        if self.snapshot.committee_policy_version != self.policy.policy_version:
            raise ValueError("snapshot policy version must match the committee policy")
        if self.snapshot.prompt_version != self.policy.prompt_version:
            raise ValueError("snapshot prompt version must match the committee policy")
        if self.snapshot.instrument_id != self.instrument_id:
            raise ValueError("snapshot instrument_id must match the committee case")
        if self.snapshot.strategy_context_id != self.strategy_context_id:
            raise ValueError("snapshot strategy_context_id must match the committee case")
        if self.canonical_binding is not None and not isinstance(
            self.canonical_binding, CanonicalDecisionBinding
        ):
            raise ValueError(
                "canonical_binding must be a CanonicalDecisionBinding or null"
            )
        object.__setattr__(
            self,
            "created_at",
            require_utc(self.created_at, field_name="created_at"),
        )
        if self.created_at < self.snapshot.assembled_at:
            raise ValueError("case created_at must be >= snapshot assembled_at")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "case_type": self.case_type,
            "snapshot_hash": self.snapshot.snapshot_hash,
            "policy_hash": self.policy.policy_hash,
            "created_at": self.created_at,
            "instrument_id": self.instrument_id,
            "strategy_context_id": self.strategy_context_id,
            "canonical_binding": (
                None
                if self.canonical_binding is None
                else self.canonical_binding.identity_payload()
            ),
        }

    @property
    def case_hash(self) -> str:
        """Content-derived identity of this committee case."""
        return stable_hash(COMMITTEE_CASE_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class StructuredOpinion:
    """The validated structured opinion a single seat produced.

    Free-form prose alone is not accepted as an observation. Every enum, range,
    and evidence reference is validated before an opinion is admitted, and
    identity is content-derived so a caller cannot label an opinion.
    """

    case_id: str
    provider: str
    model: str
    evidence_sufficiency: EvidenceSufficiency
    assessment: DirectionalAssessment
    hypothesis: str
    recommended_research_action: ResearchAction
    confidence: int | None = None
    supporting_evidence_refs: tuple[str, ...] = ()
    contradicting_evidence_refs: tuple[str, ...] = ()
    major_assumptions: tuple[str, ...] = ()
    risk_factors: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    alternative_explanations: tuple[str, ...] = ()
    abstention_reason: str | None = None
    schema_version: int = STRUCTURED_OPINION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != STRUCTURED_OPINION_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported StructuredOpinion schema_version")
        for field_name in ("case_id", "provider", "model", "hypothesis"):
            object.__setattr__(
                self,
                field_name,
                _require_non_empty_str(getattr(self, field_name), field_name=field_name),
            )
        if not isinstance(self.evidence_sufficiency, EvidenceSufficiency):
            raise ValueError("invalid evidence_sufficiency")
        if not isinstance(self.assessment, DirectionalAssessment):
            raise ValueError("invalid assessment")
        if not isinstance(self.recommended_research_action, ResearchAction):
            raise ValueError("invalid recommended_research_action")
        _require_optional_exact_int(
            self.confidence,
            field_name="confidence",
            minimum=0,
            maximum=100,
        )
        for field_name in (
            "supporting_evidence_refs",
            "contradicting_evidence_refs",
            "major_assumptions",
            "risk_factors",
            "missing_evidence",
            "alternative_explanations",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_str_tuple(getattr(self, field_name), field_name=field_name),
            )
        if self.abstention_reason is not None:
            object.__setattr__(
                self,
                "abstention_reason",
                _require_non_empty_str(
                    self.abstention_reason, field_name="abstention_reason"
                ),
            )
            if self.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT:
                raise ValueError(
                    "a seat cannot abstain while declaring sufficient evidence"
                )
            if self.confidence is not None:
                raise ValueError("an abstaining seat must not report a confidence")
        overlap = set(self.supporting_evidence_refs) & set(
            self.contradicting_evidence_refs
        )
        if overlap:
            raise ValueError(
                f"evidence cannot both support and contradict: {sorted(overlap)}"
            )

    @property
    def abstained(self) -> bool:
        return self.abstention_reason is not None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "provider": self.provider,
            "model": self.model,
            "evidence_sufficiency": self.evidence_sufficiency,
            "assessment": self.assessment,
            "hypothesis": self.hypothesis,
            "confidence": self.confidence,
            "supporting_evidence_refs": self.supporting_evidence_refs,
            "contradicting_evidence_refs": self.contradicting_evidence_refs,
            "major_assumptions": self.major_assumptions,
            "risk_factors": self.risk_factors,
            "missing_evidence": self.missing_evidence,
            "alternative_explanations": self.alternative_explanations,
            "recommended_research_action": self.recommended_research_action,
            "abstention_reason": self.abstention_reason,
        }

    @property
    def opinion_id(self) -> str:
        """Content-derived opinion identity in its own hash domain."""
        return stable_hash(STRUCTURED_OPINION_ID_DOMAIN, self.identity_payload())

    @property
    def opinion_hash(self) -> str:
        return stable_hash(STRUCTURED_OPINION_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class ProviderCallOutcome:
    """The immutable result of exactly one provider call for one case.

    ``outcome_id`` is content-derived (the deterministic logical identity of the
    call, the served identity, the status, and the produced opinion), so a
    retry that re-delivers the same logical observation is recorded as
    ``DUPLICATE_OK`` and never becomes a second, independent opinion.
    """

    logical_observation_id: str
    case_id: str
    provider_family: ProviderFamily
    requested_model: str
    status: ObservationStatus
    attempt: int
    reproducibility: ReproducibilityClass
    request_at: datetime
    input_hash: str
    reported_provider: str | None = None
    reported_model: str | None = None
    failure_class: ProviderFailureClass | None = None
    detail: str | None = None
    opinion: StructuredOpinion | None = None
    raw_response_ref: str | None = None
    response_at: datetime | None = None
    latency_micros: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_microunits: int | None = None
    cost_completeness: CostCompleteness = CostCompleteness.UNKNOWN
    replay_divergence_detected: bool = False
    schema_version: int = PROVIDER_CALL_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_call_outcome_identity(self)
        _validate_call_outcome_status(self)

    @property
    def is_independent_opinion(self) -> bool:
        """Only a first, committed, validated opinion is independent evidence."""
        return (
            self.status is ObservationStatus.COMPLETED
            and self.opinion is not None
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "logical_observation_id": self.logical_observation_id,
            "case_id": self.case_id,
            "provider_family": self.provider_family,
            "requested_model": self.requested_model,
            "status": self.status,
            "attempt": self.attempt,
            "failure_class": self.failure_class,
            "input_hash": self.input_hash,
            "opinion_hash": (
                self.opinion.opinion_hash if self.opinion is not None else None
            ),
            "request_at": self.request_at,
            "replay_divergence_detected": self.replay_divergence_detected,
            # The served identity and the recorded call telemetry are persisted
            # evidence, so they participate in the identity too. Otherwise a row
            # differing only in latency, tokens, cost, or completeness would keep
            # its outcome_id, and the bake-off's cost and latency evidence could
            # change without the durable artifact identity detecting it.
            "reported_provider": self.reported_provider,
            "reported_model": self.reported_model,
            "response_at": self.response_at,
            "raw_response_ref": self.raw_response_ref,
            "latency_micros": self.latency_micros,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_microunits": self.estimated_cost_microunits,
            "cost_completeness": self.cost_completeness,
            "detail": self.detail,
        }

    @property
    def outcome_id(self) -> str:
        """Content-derived identity of this exact call outcome."""
        return stable_hash(
            PROVIDER_CALL_OUTCOME_IDENTITY_DOMAIN, self.identity_payload()
        )


@dataclass(frozen=True)
class CommitteeCaseOutcome:
    """The aggregated, auditable result of running one committee case.

    Dissent is preserved: every seat keeps its own record. ``completeness_basis_points``
    counts *answered* seats (completed or duplicate-acknowledged), never failures.
    """

    case_id: str
    evidence_snapshot_hash: str
    committee_policy_version: str
    phase: EvaluationPhase
    started_at: datetime
    completed_at: datetime
    outcomes: tuple[ProviderCallOutcome, ...]
    provenance: Provenance
    schema_version: int = COMMITTEE_CASE_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != COMMITTEE_CASE_OUTCOME_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported CommitteeCaseOutcome schema_version")
        for field_name in (
            "case_id",
            "evidence_snapshot_hash",
            "committee_policy_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_non_empty_str(getattr(self, field_name), field_name=field_name),
            )
        if not isinstance(self.phase, EvaluationPhase):
            raise ValueError("invalid evaluation phase")
        if not isinstance(self.outcomes, tuple) or not self.outcomes:
            raise ValueError("a committee case outcome requires at least one call")
        seen: set[ProviderFamily] = set()
        for outcome in self.outcomes:
            if not isinstance(outcome, ProviderCallOutcome):
                raise ValueError("outcomes must be ProviderCallOutcome")
            if outcome.case_id != self.case_id:
                raise ValueError("every call outcome must belong to this case")
            if outcome.provider_family in seen:
                raise ValueError("one sealed opinion per provider family")
            seen.add(outcome.provider_family)
        object.__setattr__(
            self,
            "started_at",
            require_utc(self.started_at, field_name="started_at"),
        )
        object.__setattr__(
            self,
            "completed_at",
            require_utc(self.completed_at, field_name="completed_at"),
        )
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must be >= started_at")

    @property
    def sealed_opinions(self) -> tuple[StructuredOpinion, ...]:
        return tuple(
            outcome.opinion
            for outcome in self.outcomes
            if outcome.is_independent_opinion and outcome.opinion is not None
        )

    @property
    def failed_seats(self) -> tuple[ProviderCallOutcome, ...]:
        return tuple(
            outcome
            for outcome in self.outcomes
            if outcome.status
            in (ObservationStatus.FAILED, ObservationStatus.INVALID)
        )

    @property
    def unavailable_seats(self) -> tuple[ProviderCallOutcome, ...]:
        return tuple(
            outcome
            for outcome in self.outcomes
            if outcome.status is ObservationStatus.UNAVAILABLE
        )

    def completeness_basis_points(self, *, seated: int) -> int:
        """Answered fraction of seated providers, in basis points."""
        _require_exact_int(seated, field_name="seated", minimum=1)
        answered = len(self.answered_seats)
        if answered > seated:
            raise ValueError("answered seats cannot exceed seated providers")
        return (answered * 10_000) // seated

    @property
    def answered_seats(self) -> tuple[ProviderCallOutcome, ...]:
        return tuple(
            outcome
            for outcome in self.outcomes
            if outcome.status in (ObservationStatus.COMPLETED, ObservationStatus.DUPLICATE_OK)
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "committee_policy_version": self.committee_policy_version,
            "phase": self.phase,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "outcomes": tuple(outcome.outcome_id for outcome in self.outcomes),
        }

    @property
    def case_outcome_id(self) -> str:
        """Content-derived identity of this aggregated case outcome."""
        return stable_hash(
            COMMITTEE_CASE_OUTCOME_IDENTITY_DOMAIN, self.identity_payload()
        )


def logical_observation_id(
    *,
    case_id: str,
    provider_family: ProviderFamily,
    requested_model: str,
    prompt_version: str,
    committee_policy_version: str,
    evidence_snapshot_hash: str,
) -> str:
    """Deterministic logical identity of one committee seat's observation.

    The same case, provider, model, prompt version, policy version, and evidence
    snapshot always map to the same logical identity, so an internally retried
    or duplicated call cannot create a second independent opinion.
    """
    for field_name, value in (
        ("case_id", case_id),
        ("requested_model", requested_model),
        ("prompt_version", prompt_version),
        ("committee_policy_version", committee_policy_version),
        ("evidence_snapshot_hash", evidence_snapshot_hash),
    ):
        _require_non_empty_str(value, field_name=field_name)
    if not isinstance(provider_family, ProviderFamily):
        raise ValueError("invalid provider_family")
    return stable_hash(
        LOGICAL_OBSERVATION_IDENTITY_DOMAIN,
        {
            "case_id": case_id,
            "provider_family": provider_family,
            "requested_model": requested_model,
            "prompt_version": prompt_version,
            "committee_policy_version": committee_policy_version,
            "evidence_snapshot_hash": evidence_snapshot_hash,
        },
    )


__all__ = [
    "COMMITTEE_CASE_SCHEMA_VERSION",
    "COMMITTEE_POLICY_SCHEMA_VERSION",
    "EVIDENCE_SNAPSHOT_SCHEMA_VERSION",
    "STRUCTURED_OPINION_SCHEMA_VERSION",
    "PROVIDER_CALL_OUTCOME_SCHEMA_VERSION",
    "COMMITTEE_CASE_OUTCOME_SCHEMA_VERSION",
    "COMMITTEE_CASE_IDENTITY_DOMAIN",
    "COMMITTEE_POLICY_IDENTITY_DOMAIN",
    "EVIDENCE_SNAPSHOT_IDENTITY_DOMAIN",
    "EVIDENCE_SNAPSHOT_ID_DOMAIN",
    "STRUCTURED_OPINION_IDENTITY_DOMAIN",
    "STRUCTURED_OPINION_ID_DOMAIN",
    "PROVIDER_CALL_OUTCOME_IDENTITY_DOMAIN",
    "COMMITTEE_CASE_OUTCOME_IDENTITY_DOMAIN",
    "LOGICAL_OBSERVATION_IDENTITY_DOMAIN",
    "MODEL_BOUND_ITEM_FIELDS",
    "CaseType",
    "CanonicalDecisionBinding",
    "CommitteeCase",
    "CommitteeCaseOutcome",
    "CommitteePolicy",
    "CostCompleteness",
    "DirectionalAssessment",
    "EvaluationPhase",
    "EvidenceItem",
    "EvidenceSnapshot",
    "EvidenceSufficiency",
    "ObservationStatus",
    "ProviderCallOutcome",
    "ProviderFailureClass",
    "ProviderFamily",
    "ReproducibilityClass",
    "ResearchAction",
    "StructuredOpinion",
    "freeze_nested",
    "logical_observation_id",
]
