"""Role budgets and attributable role results.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

A role result is only trustworthy if it says exactly what produced it. This
module binds four things that must never be inferred later:

* **budget** - deadline, token, cost, and concurrency limits belong to the
  *role's route*, not to an individual attempt, so a fallback cannot enlarge
  them (see :class:`RoleBudget`);
* **identity** - role, role version, prompt version/hash, schema version/hash,
  and the serving provider/model travel with the result, so no result can be
  re-attributed to a prompt or model other than the one that produced it;
* **linkage** - the provider call and attempt identity are referenced, never
  re-derived, so cost and timing remain the ones actually recorded;
* **honesty** - a missing value stays ``None``. An ordinal advisory score is
  never rescaled into a probability, and an absent rubric score is never written
  as zero.

Action fields are forbidden. A role result is advisory research output: it may
state a stance and a thesis, and it may not instruct anyone to enter, size,
protect, or exit anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping

from app.opip.committee.contracts import (
    DirectionalAssessment,
    EvidenceSufficiency,
    ObservationStatus,
    ProviderCallOutcome,
    ProviderFamily,
    ResearchAction,
)
from app.opip.committee.roles import CommitteeRole
from app.opip.decision_intelligence.serialization import require_utc, stable_hash

ROLE_BUDGET_SCHEMA_VERSION = 1
ROLE_RESULT_SCHEMA_VERSION = 1

ROLE_BUDGET_IDENTITY_DOMAIN = "COMMITTEE-ROLE-BUDGET"
ROLE_RESULT_IDENTITY_DOMAIN = "COMMITTEE-ROLE-RESULT"

#: Canonical upper bound on a role's concurrency. A role that could fan out
#: without limit would spend without limit, so this is a contract, not a hint.
MAX_ROLE_CONCURRENCY = 4

#: Field names that would turn an advisory research result into an instruction.
#: Presence of any of these is refused rather than ignored.
FORBIDDEN_ACTION_FIELDS = frozenset(
    {
        "action",
        "order",
        "order_intent",
        "side",
        "size",
        "position_size",
        "quantity",
        "notional",
        "entry",
        "entry_price",
        "stop",
        "stop_loss",
        "take_profit",
        "target",
        "leverage",
        "risk_fraction",
        "capital_fraction",
        "execute",
        "execution",
        "trade",
        "buy",
        "sell",
    }
)


class RoleBudgetViolation(ValueError):
    """A role attempted to exceed its governed budget."""


class RoleResultStatus(str, Enum):
    """Terminal status of a role's attempt to answer for one case.

    Only ``ANSWERED`` carries an advisory opinion. The remaining statuses exist
    so a missing opinion is visible as its own reason instead of collapsing into
    "no opinion", which would make a budget skip and a provider failure look
    identical.
    """

    ANSWERED = "ANSWERED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED_BUDGET = "SKIPPED_BUDGET"


#: Statuses that represent an advisory opinion a reader may reason about.
ANSWERED_ROLE_STATUSES = frozenset({RoleResultStatus.ANSWERED})

#: Observation statuses mapped onto the role-level status vocabulary, so the two
#: layers cannot drift apart.
_OBSERVATION_TO_ROLE_STATUS: Mapping[ObservationStatus, RoleResultStatus] = {
    ObservationStatus.COMPLETED: RoleResultStatus.ANSWERED,
    ObservationStatus.DUPLICATE_OK: RoleResultStatus.ANSWERED,
    ObservationStatus.FAILED: RoleResultStatus.FAILED,
    ObservationStatus.INVALID: RoleResultStatus.INVALID,
    ObservationStatus.UNAVAILABLE: RoleResultStatus.UNAVAILABLE,
    ObservationStatus.SKIPPED_BUDGET: RoleResultStatus.SKIPPED_BUDGET,
}


def role_status_for_observation(status: ObservationStatus) -> RoleResultStatus:
    """Translate a provider observation status into a role result status."""
    try:
        return _OBSERVATION_TO_ROLE_STATUS[status]
    except KeyError as exc:  # pragma: no cover - guards a future enum addition
        raise ValueError(f"unmapped observation status {status!r}") from exc


@dataclass(frozen=True)
class RoleBudget:
    """The governed budget a role's route may consume for one case.

    A fallback shares this budget with the primary. It is one reservation for the
    role, not one per attempt, so a route cannot double its ceiling by failing
    over.
    """

    deadline_seconds: int
    max_output_tokens: int
    max_cost_microunits: int
    max_concurrency: int = 1
    schema_version: int = ROLE_BUDGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROLE_BUDGET_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ValueError("unsupported RoleBudget schema_version")
        for field_name in ("deadline_seconds", "max_output_tokens", "max_cost_microunits"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if type(self.max_concurrency) is not int or not (
            1 <= self.max_concurrency <= MAX_ROLE_CONCURRENCY
        ):
            raise ValueError(
                f"max_concurrency must be an integer in 1..{MAX_ROLE_CONCURRENCY}"
            )

    def check_deadline(self, *, elapsed_seconds: int) -> None:
        """Fail closed when a role exceeds its deadline."""
        if type(elapsed_seconds) is not int or elapsed_seconds < 0:
            raise RoleBudgetViolation("elapsed_seconds must be a non-negative integer")
        if elapsed_seconds > self.deadline_seconds:
            raise RoleBudgetViolation(
                f"role deadline exceeded ({elapsed_seconds}s > "
                f"{self.deadline_seconds}s)"
            )

    def check_cost(self, *, spent_microunits: int) -> None:
        """Fail closed when a role exceeds its monetary reservation.

        An unknown cost is not treated as free: the caller must pass a known
        figure, because a ceiling that cannot be evaluated cannot be enforced.
        """
        if type(spent_microunits) is not int or spent_microunits < 0:
            raise RoleBudgetViolation(
                "spent_microunits must be a known non-negative integer"
            )
        if spent_microunits > self.max_cost_microunits:
            raise RoleBudgetViolation(
                f"role cost ceiling exceeded ({spent_microunits} > "
                f"{self.max_cost_microunits} microunits)"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "deadline_seconds": self.deadline_seconds,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_microunits": self.max_cost_microunits,
            "max_concurrency": self.max_concurrency,
        }

    @property
    def budget_hash(self) -> str:
        return stable_hash(ROLE_BUDGET_IDENTITY_DOMAIN, self.identity_payload())


@dataclass(frozen=True)
class RoleSeatResult:
    """One role's attributable, advisory result for one case.

    Everything needed to audit the result is present: what was asked (role,
    versions, prompt and schema hashes), who answered (provider, resolved model),
    what it said (stance, thesis, risks, missing evidence), how confident it
    claimed to be, and which recorded call it came from.
    """

    case_id: str
    role: CommitteeRole
    role_version: str
    status: RoleResultStatus
    prompt_version: str
    prompt_hash: str
    schema_version: int
    provider_family: ProviderFamily
    requested_model: str
    resolved_model: str | None
    logical_observation_id: str
    attempt: int
    recorded_at: datetime
    #: The recorded call this result is derived from. Referenced, never
    #: recomputed, so cost and timing stay the ones actually observed.
    call_outcome_id: str | None = None
    call_outcome: ProviderCallOutcome | None = None
    stance: DirectionalAssessment | None = None
    evidence_sufficiency: EvidenceSufficiency | None = None
    thesis: str | None = None
    risks: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    research_action: ResearchAction | None = None
    #: Ordinal advisory self-report, 0-100, or ``None``. Never a probability and
    #: never silently defaulted to zero.
    self_reported_confidence: int | None = None
    rubric_score: int | None = None
    #: Measured attempt duration in microseconds, or ``None`` when nothing was
    #: invoked and therefore nothing was measured. Never inferred from timestamps
    #: and never replaced with zero.
    latency_micros: int | None = None
    status_detail: str | None = None
    #: Schema version of *this contract*. Distinct from ``schema_version`` above,
    #: which is the version of the role's output schema. Conflating the two would
    #: let a contract upgrade masquerade as an output-schema change.
    contract_schema_version: int = ROLE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.contract_schema_version != ROLE_RESULT_SCHEMA_VERSION or (
            type(self.contract_schema_version) is not int
        ):
            raise ValueError("unsupported RoleSeatResult schema_version")
        if not isinstance(self.role, CommitteeRole):
            raise ValueError("invalid role")
        if not isinstance(self.status, RoleResultStatus):
            raise ValueError("invalid role status")
        if not isinstance(self.provider_family, ProviderFamily):
            raise ValueError("invalid provider_family")
        for field_name in (
            "case_id",
            "role_version",
            "prompt_version",
            "prompt_hash",
            "requested_model",
            "logical_observation_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        for field_name in ("self_reported_confidence", "rubric_score"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or not 0 <= value <= 100):
                raise ValueError(f"{field_name} must be an integer in 0..100 or null")
        if self.latency_micros is not None and (
            type(self.latency_micros) is not int or self.latency_micros < 0
        ):
            raise ValueError(
                "latency_micros must be a non-negative integer or null; a missing "
                "measurement must stay null rather than becoming zero"
            )
        object.__setattr__(
            self,
            "recorded_at",
            require_utc(self.recorded_at, field_name="recorded_at"),
        )
        if self.status is RoleResultStatus.ANSWERED:
            # An answered role must actually carry an opinion. A status that says
            # "answered" with nothing behind it would read as agreement.
            if self.stance is None or self.thesis is None:
                raise ValueError(
                    "an ANSWERED role result must carry a stance and a thesis"
                )
            if self.resolved_model is None:
                raise ValueError(
                    "an ANSWERED role result must record the resolved model"
                )
        elif self.stance is not None:
            raise ValueError(
                "only an ANSWERED role result may carry a stance; "
                f"{self.status.value} is not an opinion"
            )

    @property
    def is_advisory_opinion(self) -> bool:
        """Whether this result may be read as an opinion at all."""
        return self.status in ANSWERED_ROLE_STATUSES

    def identity_payload(self) -> dict[str, Any]:
        return {
            # Distinct key names on purpose: the contract version and the role's
            # output-schema version are different facts, and a shared key would
            # let one silently overwrite the other in this mapping.
            "contract_schema_version": self.contract_schema_version,
            "role_output_schema_version": self.schema_version,
            "case_id": self.case_id,
            "role": self.role,
            "role_version": self.role_version,
            "status": self.status,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "provider_family": self.provider_family,
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "logical_observation_id": self.logical_observation_id,
            "attempt": self.attempt,
            "recorded_at": self.recorded_at,
            "call_outcome_id": self.call_outcome_id,
            "stance": self.stance,
            "evidence_sufficiency": self.evidence_sufficiency,
            "thesis": self.thesis,
            "risks": self.risks,
            "evidence_refs": self.evidence_refs,
            "missing_evidence": self.missing_evidence,
            "research_action": self.research_action,
            "self_reported_confidence": self.self_reported_confidence,
            "rubric_score": self.rubric_score,
            "latency_micros": self.latency_micros,
            "status_detail": self.status_detail,
        }

    @property
    def role_result_id(self) -> str:
        return stable_hash(ROLE_RESULT_IDENTITY_DOMAIN, self.identity_payload())


def assert_no_action_fields(payload: Mapping[str, Any]) -> None:
    """Refuse a model payload that tries to instruct rather than advise.

    Checked against declared keys, case-insensitively, so an action-bearing field
    cannot be smuggled in under different capitalisation.
    """
    offending = sorted(
        key for key in payload if str(key).strip().lower() in FORBIDDEN_ACTION_FIELDS
    )
    if offending:
        raise ValueError(
            "a role result may not carry action fields; found "
            f"{offending}"
        )


def assert_evidence_refs_are_supported(
    *,
    refs: Iterable[str],
    supported_refs: Iterable[str],
) -> None:
    """Refuse an evidence citation the screened view does not contain.

    A citation outside the manifest is a hallucinated reference, so it
    invalidates the result instead of being recorded as if it were evidence.
    """
    supported = set(supported_refs)
    unsupported = sorted(ref for ref in refs if ref not in supported)
    if unsupported:
        raise ValueError(
            "evidence references outside the screened evidence view: "
            f"{unsupported}"
        )


__all__ = [
    "ANSWERED_ROLE_STATUSES",
    "FORBIDDEN_ACTION_FIELDS",
    "MAX_ROLE_CONCURRENCY",
    "ROLE_BUDGET_SCHEMA_VERSION",
    "ROLE_RESULT_SCHEMA_VERSION",
    "ROLE_BUDGET_IDENTITY_DOMAIN",
    "ROLE_RESULT_IDENTITY_DOMAIN",
    "RoleBudget",
    "RoleBudgetViolation",
    "RoleResultStatus",
    "RoleSeatResult",
    "assert_evidence_refs_are_supported",
    "assert_no_action_fields",
    "role_status_for_observation",
]
