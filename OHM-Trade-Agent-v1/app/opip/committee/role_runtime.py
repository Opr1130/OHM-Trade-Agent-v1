"""The governed seven-role Committee SHADOW runtime.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module is the orchestration path the deployed SHADOW worker uses. It seats
the seven governed roles, resolves each role's governed route from the approved
model registry, executes each route through :class:`RoleRouter` under one shared
per-role budget, records an attributable role result per role, and runs the
designated ``DECISION_SYNTHESIZER`` last over the other roles' recorded results.

A **role** is an analytical responsibility; a provider/model is only the
implementation backing it. Those two identities are deliberately separate, so a
provider swap cannot silently change what the committee claims to have measured
and a missing role can never be filled by a provider family.

What this module enforces, rather than documents and hopes for:

* every required role is seated; a case that cannot seat one fails closed rather
  than quietly producing a smaller "committee";
* an optional role whose evidence does not exist reports ``UNKNOWN`` explicitly
  instead of inventing an opinion;
* one shared budget per role, derived from the governed registry entry, so a
  fallback cannot enlarge a role's deadline, token, or monetary reservation;
* a case-level monetary ceiling is enforced across roles, so seven role budgets
  cannot collectively spend past the approved per-case ceiling;
* a request whose worst-case cost cannot be bounded inside the remaining case
  ceiling is skipped rather than sent;
* the synthesizer is the only role that sees other roles' results, and only a
  screened, purely advisory projection of them;
* every role result is durable and idempotent per logical role seat, so a
  redelivered case does not buy a second paid opinion;
* action-bearing output is refused by the closed opinion schema.

Nothing here constructs a transport, reads a credential, or opens a socket.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from app.opip.committee.contracts import (
    CanonicalDecisionBinding,
    CommitteeCase,
    CostCompleteness,
    EvaluationPhase,
    ProviderFamily,
)
from app.opip.committee.outbound import screen_model_bound_view
from app.opip.committee.opinion import OPINION_FIELDS
from app.opip.committee.providers import (
    CommitteeProvider,
    ProviderWireRequest,
    UnavailableProvider,
)
from app.opip.committee.registry import (
    APPROVED_DEADLINE_SECONDS,
    APPROVED_MAX_CASE_COST_MICROUNITS,
    APPROVED_MAX_OUTPUT_TOKENS,
    APPROVED_SHADOW_MODELS,
    APPROVED_SHADOW_REASONING_MODE,
    APPROVED_SHADOW_ROLE_PRIMARIES,
    ApprovalState,
    ModelIdKind,
    ModelRegistry,
    ModelRegistryEntry,
    RegistryError,
    RoleRoute,
)
from app.opip.committee.role_execution import (
    RoleBudget,
    RoleBudgetViolation,
    RoleResultStatus,
    RoleSeatResult,
)
from app.opip.committee.role_router import RoleRouter
from app.opip.committee.roles import (
    OPTIONAL_ROLES,
    REQUIRED_ROLES,
    CommitteeRole,
)
from app.opip.committee.runtime import CommitteePolicyViolation
from app.opip.committee.settings import committee_shadow_enabled
from app.opip.decision_intelligence.serialization import stable_hash

#: Schema version of the durable role-case-outcome contract.
ROLE_CASE_OUTCOME_SCHEMA_VERSION = 1

ROLE_OBSERVATION_IDENTITY_DOMAIN = "COMMITTEE-ROLE-OBSERVATION"
ROLE_CASE_OUTCOME_IDENTITY_DOMAIN = "COMMITTEE-ROLE-CASE-OUTCOME"
ROLE_OUTPUT_SCHEMA_IDENTITY_DOMAIN = "COMMITTEE-ROLE-OUTPUT-SCHEMA"
COMMITTEE_ROLE_PROMPT_IDENTITY_DOMAIN = "COMMITTEE-ROLE-PROMPT"

#: The canonical seating order. Stable so a case's role population is
#: reproducible and the synthesizer always runs after every other role.
SEAT_ORDER: tuple[CommitteeRole, ...] = (
    CommitteeRole.REGIME_ANALYST,
    CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST,
    CommitteeRole.EVENT_SENTIMENT_ANALYST,
    CommitteeRole.BULL_ADVOCATE,
    CommitteeRole.BEAR_ADVOCATE,
    CommitteeRole.RISK_CRITIC,
    CommitteeRole.DECISION_SYNTHESIZER,
)

#: The one role permitted to read other roles' results, and only because its
#: declared evidence dependency is ``role_opinions``.
SYNTHESIZER_ROLE = CommitteeRole.DECISION_SYNTHESIZER

#: Payload metric-name prefixes that identify evidence the optional
#: event/sentiment role depends on. The producer derives every ``evidence_id`` as a
#: content hash, so an evidence *id* can never be matched by a literal; the metric
#: name the producer records in the payload is the stable, semantic signal.
#:
#: The current canonical producer emits no metric in this namespace, so the
#: optional role legitimately reports ``UNKNOWN`` for every SHADOW case today. The
#: check is expressed against the metric namespace rather than a literal id so it
#: stays correct if the producer later retains qualified event evidence.
QUALIFIED_EVENT_METRIC_PREFIXES = ("event.", "sentiment.")

#: Hard cap on the already-screened logical request handed to a vendor. Mirrors
#: the executor's independent spend bound; it is not a token estimate.
MAX_ROLE_REQUEST_BYTES = 16 * 1024

#: Worst-case input-token bound used for the pre-flight reservation. Deliberately
#: pessimistic: four tokens per UTF-8 byte plus a fixed protocol allowance is far
#: more conservative than a provider's normal tokenisation, so a reservation
#: cannot be optimistic merely because no local tokenizer exists.
INPUT_TOKEN_SAFETY_MULTIPLIER = 4
PROTOCOL_TOKEN_ALLOWANCE = 4_096

#: Longest thesis projection the synthesizer receives per peer. A synthesizer
#: prompt is an aggregation input, not an evidence copy, and the request bound is
#: an independent spend control.
MAX_PEER_THESIS_CHARS = 600

#: The closed response contract, identical to the strict parser's field set. It is
#: derived from the parser rather than restated, so a prompt and its parser cannot
#: drift apart and admit a field the other rejects.
_RESPONSE_CONTRACT = (
    "Reply with a single JSON object and nothing else - no prose, no markdown "
    "fence - with exactly these keys: "
    + ", ".join(sorted(OPINION_FIELDS))
    + ".\n"
    "schema_version must be the integer 1. Cite only evidence_id values that "
    "appear in the snapshot. If the evidence is not sufficient to form a view, "
    "abstain with a reason instead of guessing."
)

#: The authority statement every role prompt carries. A role is advisory
#: research output, so an instruction to act is refused by the parser and is also
#: never requested here.
_NO_AUTHORITY_STATEMENT = (
    "You are one independent reviewer on a research committee. You are given a "
    "frozen evidence snapshot with a cutoff time. Answer only from that "
    "evidence.\n"
    "You have no authority to act: you cannot place, modify, or cancel orders, "
    "size positions, or change risk limits. Never propose execution."
)

ROLE_INSTRUCTIONS: Mapping[CommitteeRole, str] = {
    CommitteeRole.REGIME_ANALYST: (
        "You are the market REGIME ANALYST. State what market regime the "
        "evidence supports (trend, range, or transition), how strong the "
        "evidence for it is, and what would falsify your read."
    ),
    CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST: (
        "You are the LIQUIDITY AND MARKET STRUCTURE ANALYST. State what the "
        "evidence implies about liquidity, depth, and structural levels, and "
        "where that evidence is thin."
    ),
    CommitteeRole.EVENT_SENTIMENT_ANALYST: (
        "You are the EVENT AND SENTIMENT ANALYST. Answer only from qualified "
        "retained event or sentiment evidence that is present in the snapshot. "
        "If no such evidence is present, abstain and say so."
    ),
    CommitteeRole.BULL_ADVOCATE: (
        "You are the BULL ADVOCATE. Make the strongest honest case FOR the "
        "candidate using only the evidence, and state the assumptions the case "
        "depends on."
    ),
    CommitteeRole.BEAR_ADVOCATE: (
        "You are the BEAR ADVOCATE. Make the strongest honest case AGAINST the "
        "candidate using only the evidence, and state the assumptions the case "
        "depends on."
    ),
    CommitteeRole.RISK_CRITIC: (
        "You are the RISK CRITIC. Identify what could go wrong, which risks are "
        "not visible in the evidence, and what evidence is missing before this "
        "could be judged."
    ),
    CommitteeRole.DECISION_SYNTHESIZER: (
        "You are the DECISION SYNTHESIZER. You are given the same frozen "
        "evidence snapshot plus the other roles' advisory results. Weigh them, "
        "preserve the disagreement explicitly, and state what is still missing. "
        "You do not decide anything: produce advisory research output only."
    ),
}


def role_prompt(role: CommitteeRole) -> str:
    """The exact system prompt for one governed role."""
    if not isinstance(role, CommitteeRole):
        raise ValueError("invalid committee role")
    return f"{_NO_AUTHORITY_STATEMENT}\n{ROLE_INSTRUCTIONS[role]}\n\n{_RESPONSE_CONTRACT}"


#: Prompt identity per role, so a result is attributable to the prompt that
#: produced it and a prompt revision is visible as a change of identity.
ROLE_PROMPT_HASH: Mapping[CommitteeRole, str] = {
    role: stable_hash(COMMITTEE_ROLE_PROMPT_IDENTITY_DOMAIN, role_prompt(role))
    for role in SEAT_ORDER
}

#: Output-schema identity for the role layer. Derived from the closed field set
#: so the schema hash cannot disagree with the parser it describes.
ROLE_OUTPUT_SCHEMA_HASH = stable_hash(
    ROLE_OUTPUT_SCHEMA_IDENTITY_DOMAIN, sorted(OPINION_FIELDS)
)
ROLE_OUTPUT_SCHEMA_VERSION = 1


class RoleRuntimeConfigurationError(ValueError):
    """The role-governed runtime cannot be constructed safely."""


class RoleResultLedger(Protocol):
    """Durable, idempotent storage for role results."""

    def role_result_for(self, logical_observation_id: str) -> RoleSeatResult | None:
        """The recorded result for a logical role seat, or ``None``."""

    def record_role_result(self, result: RoleSeatResult) -> None:
        """Append a role result. Never overwrites an existing one."""


@dataclass(frozen=True)
class RoleGovernedCaseOutcome:
    """The durable, auditable aggregation of one role-governed committee case.

    It records the role results themselves plus the registry and policy identity
    that produced them, so the case can be reconstructed and attributed without
    consulting ambient state. It carries the same opaque canonical binding the
    case was run with, so the recommendation-to-decision edge survives the
    in-memory result.

    It grants nothing: it is advisory research evidence.
    """

    case_id: str
    evidence_snapshot_hash: str
    committee_policy_version: str
    policy_hash: str
    registry_version: str
    registry_hash: str
    phase: EvaluationPhase
    started_at: datetime
    completed_at: datetime
    role_results: tuple[RoleSeatResult, ...]
    spent_microunits: int
    provenance: Any
    canonical_binding: CanonicalDecisionBinding | None = None
    #: Whether every role's attempt cost could be measured. ``UNKNOWN`` means at
    #: least one attempt reported no usable cost, so ``spent_microunits`` is a
    #: lower bound rather than a total. An unknown cost is never reported as a
    #: verified one.
    cost_completeness: CostCompleteness = CostCompleteness.COMPLETE
    #: Whether the approved per-case ceiling was provably respected. False when the
    #: cost was incomplete or an attempt's measured spend exceeded its reservation.
    ceiling_verified: bool = True
    schema_version: int = ROLE_CASE_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_role_case_identity(self)
        _require_role_case_results(self)
        _require_role_case_moments(self)
        if not isinstance(self.cost_completeness, CostCompleteness):
            raise ValueError("invalid cost_completeness")
        if type(self.ceiling_verified) is not bool:
            raise ValueError("ceiling_verified must be a bool")


    @property
    def answered_roles(self) -> tuple[RoleSeatResult, ...]:
        return tuple(
            result for result in self.role_results if result.is_advisory_opinion
        )

    @property
    def missing_required_roles(self) -> tuple[CommitteeRole, ...]:
        """Required roles that produced no advisory opinion for this case."""
        answered = {result.role for result in self.answered_roles}
        return tuple(
            role
            for role in SEAT_ORDER
            if role in REQUIRED_ROLES and role not in answered
        )

    @property
    def complete(self) -> bool:
        """Whether this case is a complete committee.

        A case that could not seat every required role is *reported*, not
        silently treated as a smaller committee. Consumers must check this rather
        than counting rows.
        """
        return not self.missing_required_roles

    @property
    def synthesis(self) -> RoleSeatResult | None:
        for result in self.role_results:
            if result.role is SYNTHESIZER_ROLE:
                return result
        return None

    def identity_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "committee_policy_version": self.committee_policy_version,
            "policy_hash": self.policy_hash,
            "registry_version": self.registry_version,
            "registry_hash": self.registry_hash,
            "phase": self.phase,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "role_result_ids": tuple(
                result.role_result_id for result in self.role_results
            ),
            "spent_microunits": self.spent_microunits,
            "cost_completeness": self.cost_completeness,
            "ceiling_verified": self.ceiling_verified,
        }
        if self.canonical_binding is not None:
            payload["canonical_binding"] = (
                self.canonical_binding.decision_id,
                self.canonical_binding.episode_id,
            )
        return payload

    @property
    def role_case_outcome_id(self) -> str:
        return stable_hash(ROLE_CASE_OUTCOME_IDENTITY_DOMAIN, self.identity_payload())

def _require_role_case_identity(outcome: RoleGovernedCaseOutcome) -> None:
    """The opaque identity fields must all be present and non-blank."""
    if outcome.schema_version != ROLE_CASE_OUTCOME_SCHEMA_VERSION or (
        type(outcome.schema_version) is not int
    ):
        raise ValueError("unsupported RoleGovernedCaseOutcome schema_version")
    for field_name in (
        "case_id",
        "evidence_snapshot_hash",
        "committee_policy_version",
        "policy_hash",
        "registry_version",
        "registry_hash",
    ):
        value = getattr(outcome, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required")
    if not isinstance(outcome.phase, EvaluationPhase):
        raise ValueError("invalid evaluation phase")
    if outcome.canonical_binding is not None and not isinstance(
        outcome.canonical_binding, CanonicalDecisionBinding
    ):
        raise ValueError("invalid canonical_binding")


def _require_role_case_results(outcome: RoleGovernedCaseOutcome) -> None:
    """Every seated role must appear exactly once, and belong to this case."""
    if not isinstance(outcome.role_results, tuple) or not outcome.role_results:
        raise ValueError("a role-governed case outcome requires role results")
    seen: set[CommitteeRole] = set()
    for result in outcome.role_results:
        if not isinstance(result, RoleSeatResult):
            raise ValueError("role_results must be RoleSeatResult values")
        if result.case_id != outcome.case_id:
            raise ValueError("every role result must belong to this case")
        if result.role in seen:
            raise ValueError("one role result per role")
        seen.add(result.role)
    if type(outcome.spent_microunits) is not int or outcome.spent_microunits < 0:
        raise ValueError("spent_microunits must be a non-negative integer")


def _require_role_case_moments(outcome: RoleGovernedCaseOutcome) -> None:
    """Both instants must be timezone-aware and correctly ordered."""
    for field_name in ("started_at", "completed_at"):
        value = getattr(outcome, field_name)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{field_name} must be a timezone-aware datetime")
    if outcome.completed_at < outcome.started_at:
        raise ValueError("completed_at must be >= started_at")


@dataclass(frozen=True)
class RoleRunOutcome:
    """What one role's seat contributed to a case, including its accounting.

    ``cost_completeness`` and ``exceeded_reservation`` travel out of the role so
    the case outcome can state whether its ceiling was actually verified rather
    than assuming it was.
    """

    result: RoleSeatResult
    spent_microunits: int = 0
    cost_completeness: CostCompleteness = CostCompleteness.COMPLETE
    #: Whether this role's ceiling was provably respected. False when the cost
    #: could not be measured, or when measured spend exceeded the reservation.
    ceiling_verified: bool = True


@dataclass(frozen=True)
class RoleGovernedRunResult:
    """What one role-governed case run produced."""

    case: CommitteeCase
    role_results: tuple[RoleSeatResult, ...]
    case_outcome: RoleGovernedCaseOutcome
    evidence_view_hash: str
    registry_hash: str
    phase: EvaluationPhase

    @property
    def answered_count(self) -> int:
        return sum(1 for result in self.role_results if result.is_advisory_opinion)


def role_observation_id(
    *,
    case_id: str,
    role: CommitteeRole,
    prompt_version: str,
    prompt_hash: str,
    committee_policy_version: str,
    evidence_snapshot_hash: str,
) -> str:
    """Deterministic logical identity of one role's seat for one case.

    It is role-scoped rather than model-scoped on purpose: the same governed role
    on the same case is one logical seat even if its fallback answers it, so a
    redelivery cannot buy a second opinion by failing over.
    """
    return stable_hash(
        ROLE_OBSERVATION_IDENTITY_DOMAIN,
        {
            "case_id": case_id,
            "role": role,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "committee_policy_version": committee_policy_version,
            "evidence_snapshot_hash": evidence_snapshot_hash,
        },
    )


def build_approved_shadow_registry(
    *,
    approved_from: datetime,
    review_by: datetime,
    owner: str = "owner",
    registry_version: str = "committee-shadow-registry-v1",
) -> ModelRegistry:
    """Build the approved SHADOW registry for the seven governed roles.

    Every entry pins a provider-defined fixed model id, carries the approved
    reasoning mode, deadline, token ceiling and per-case monetary ceiling, and
    records the exact allowlisted endpoint so the registry and the egress
    allowlist cannot disagree.

    The approval window is explicit and mandatory: an approved entry that is not
    yet effective, or past its review date, is refused at routing time rather
    than used, so "approved once" cannot silently mean "approved forever".
    """
    from app.opip.committee.registry import ReasoningMode
    from app.opip.committee.transports import ALLOWED_ENDPOINTS

    if not isinstance(approved_from, datetime) or approved_from.tzinfo is None:
        raise RoleRuntimeConfigurationError("approved_from must be timezone-aware")
    if not isinstance(review_by, datetime) or review_by.tzinfo is None:
        raise RoleRuntimeConfigurationError("review_by must be timezone-aware")
    if review_by <= approved_from:
        raise RoleRuntimeConfigurationError("review_by must be after approved_from")

    entries: list[ModelRegistryEntry] = []
    routes: dict[CommitteeRole, tuple[str, str | None]] = {}
    for role in SEAT_ORDER:
        primary_family = APPROVED_SHADOW_ROLE_PRIMARIES[role]
        fallback_family = next(
            family for family in APPROVED_SHADOW_MODELS if family is not primary_family
        )
        primary_id = f"{role.value.lower()}:{primary_family.value}"
        fallback_id = f"{role.value.lower()}:{fallback_family.value}"
        for entry_id, family in (
            (primary_id, primary_family),
            (fallback_id, fallback_family),
        ):
            entries.append(
                ModelRegistryEntry(
                    entry_id=entry_id,
                    role=role,
                    provider_family=family,
                    model_id=APPROVED_SHADOW_MODELS[family],
                    endpoint=ALLOWED_ENDPOINTS[family],
                    prompt_hash=ROLE_PROMPT_HASH[role],
                    schema_hash=ROLE_OUTPUT_SCHEMA_HASH,
                    owner=owner,
                    approval=ApprovalState.APPROVED,
                    effective_from=approved_from,
                    review_by=review_by,
                    model_id_kind=ModelIdKind.FIXED,
                    reasoning_mode=ReasoningMode(APPROVED_SHADOW_REASONING_MODE.value),
                    max_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
                    max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
                    deadline_seconds=APPROVED_DEADLINE_SECONDS,
                )
            )
        routes[role] = (primary_id, fallback_id)
    return ModelRegistry(
        registry_version=registry_version,
        entries=tuple(entries),
        routes=routes,
    )


def build_role_providers(
    *,
    registry: ModelRegistry,
    transports: Mapping[ProviderFamily, Any],
    available_families: Iterable[ProviderFamily],
) -> dict[str, CommitteeProvider]:
    """Bind every registry entry to an adapter, keyed by registry entry id.

    A registry entry whose family has no configured transport becomes an
    explicitly unavailable seat rather than being dropped or substituted: the
    role still appears in the outcome, with its unreachability visible.
    """
    configured = set(available_families)
    providers: dict[str, CommitteeProvider] = {}
    for entry in registry.entries:
        if entry.provider_family not in configured:
            providers[entry.entry_id] = UnavailableProvider(
                entry.provider_family, model=entry.model_id
            )
            continue
        from app.opip.committee.providers import TransportBackedProvider

        providers[entry.entry_id] = TransportBackedProvider(
            family=entry.provider_family,
            model=entry.model_id,
            transport=transports[entry.provider_family],
        )
    return providers


class RoleGovernedRunner:
    """Runs one case over the seven governed roles, in canonical order."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        providers: Mapping[str, CommitteeProvider],
        ledger: RoleResultLedger,
        price_book: Any,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        settings: object | None = None,
    ) -> None:
        if not isinstance(registry, ModelRegistry):
            raise RoleRuntimeConfigurationError("a ModelRegistry is required")
        self._registry = registry
        self._providers = dict(providers)
        self._ledger = ledger
        self._price_book = price_book
        self._now: Callable[[], datetime] = now or (
            lambda: datetime.now(timezone.utc)
        )
        self._router = RoleRouter(
            providers=self._providers,
            now=self._now,
            monotonic=monotonic,
        )
        self._settings = settings

    # --------------------------------------------------------------- execution

    def run_case(
        self,
        case: CommitteeCase,
        *,
        phase: EvaluationPhase = EvaluationPhase.PROSPECTIVE,
    ) -> RoleGovernedRunResult:
        """Execute one role-governed case. Advisory output only."""
        if not isinstance(case, CommitteeCase):
            raise CommitteePolicyViolation("run_case requires a CommitteeCase")
        if not isinstance(phase, EvaluationPhase):
            raise CommitteePolicyViolation("invalid evaluation phase")
        # The enablement gate is enforced at the execution API, so the advertised
        # off/shadow switch actually governs model egress and spend.
        if not committee_shadow_enabled(self._settings):
            raise CommitteePolicyViolation(
                "the Intelligence Committee is disabled; set OPIP_COMMITTEE_MODE="
                "shadow to permit committee work"
            )

        screened_view = screen_model_bound_view(case.snapshot.model_bound_view())
        evidence_view_hash = stable_hash(
            "COMMITTEE-ROLE-WIRE", screened_view
        )
        allowed_refs = tuple(item.evidence_id for item in case.snapshot.items)

        started_at = self._now()
        case_ceiling = self._case_ceiling(case)
        spent = 0
        cost_complete = True
        ceiling_verified = True
        results: list[RoleSeatResult] = []

        for role in SEAT_ORDER:
            if role in OPTIONAL_ROLES and not self._optional_evidence_present(
                case=case, role=role
            ):
                # Reported, not omitted: an absent opinion must be visible in the
                # case outcome rather than silently missing from it. The recorded
                # result is consulted first so a redelivery of the same logical
                # seat is acknowledged rather than re-recorded.
                unknown = self._optional_absent_result(case=case, role=role)
                results.append(unknown)
                continue

            outcome = self._run_role(
                case=case,
                role=role,
                screened_view=screened_view,
                allowed_refs=allowed_refs,
                peer_results=tuple(results),
                remaining_ceiling=max(0, case_ceiling - spent),
            )
            spent += outcome.spent_microunits
            if outcome.cost_completeness is CostCompleteness.UNKNOWN:
                cost_complete = False
            if not outcome.ceiling_verified:
                ceiling_verified = False
            results.append(outcome.result)

        completed_at = self._now()
        if completed_at < started_at:
            completed_at = started_at

        from app.opip.decision_intelligence.identity import Provenance

        outcome = RoleGovernedCaseOutcome(
            case_id=case.case_id,
            evidence_snapshot_hash=case.snapshot.snapshot_hash,
            committee_policy_version=case.policy.policy_version,
            policy_hash=case.policy.policy_hash,
            registry_version=self._registry.registry_version,
            registry_hash=self._registry.registry_hash,
            phase=phase,
            started_at=started_at,
            completed_at=completed_at,
            role_results=tuple(results),
            spent_microunits=spent,
            cost_completeness=(
                CostCompleteness.COMPLETE if cost_complete else CostCompleteness.UNKNOWN
            ),
            ceiling_verified=ceiling_verified,
            canonical_binding=case.canonical_binding,
            provenance=Provenance(
                producing_component="app.opip.committee.role_runtime",
                artifact_or_build_id=self._registry.registry_version,
                process_instance_id=case.provenance.process_instance_id,
                emitted_at=completed_at,
                source_record_refs=(case.snapshot.snapshot_hash,),
            ),
        )
        return RoleGovernedRunResult(
            case=case,
            role_results=tuple(results),
            case_outcome=outcome,
            evidence_view_hash=evidence_view_hash,
            registry_hash=self._registry.registry_hash,
            phase=phase,
        )

    # ----------------------------------------------------------------- internals

    def _case_ceiling(self, case: CommitteeCase) -> int:
        declared = case.policy.max_estimated_cost_microunits
        if declared is None:
            return APPROVED_MAX_CASE_COST_MICROUNITS
        return min(declared, APPROVED_MAX_CASE_COST_MICROUNITS)

    @staticmethod
    def _optional_evidence_present(
        *, case: CommitteeCase, role: CommitteeRole
    ) -> bool:
        """Whether the optional role's declared evidence exists in this snapshot.

        Matched on the producer's recorded ``metric_name`` namespace rather than a
        literal evidence id: the producer derives evidence ids as content hashes,
        so a literal id could never match and the role would be permanently
        unaskable regardless of the evidence.
        """
        if role not in OPTIONAL_ROLES:
            return True
        for item in case.snapshot.items:
            metric_name = str(item.payload.get("metric_name", "")).strip().lower()
            if metric_name.startswith(QUALIFIED_EVENT_METRIC_PREFIXES):
                return True
        return False

    def _optional_absent_result(
        self, *, case: CommitteeCase, role: CommitteeRole
    ) -> RoleSeatResult:
        """The optional role's explicit UNKNOWN, acknowledged if already recorded.

        The result is deterministic for one logical seat, so recording it is
        idempotent; the ledger is still consulted first so a redelivery returns the
        durable row rather than re-deriving one.
        """
        logical_id = self._logical_id(case=case, role=role)
        recorded = self._ledger.role_result_for(logical_id)
        if recorded is not None:
            return recorded
        unknown = self._unknown_optional_result(
            case=case, role=role, screened_view={}, logical_id=logical_id
        )
        self._ledger.record_role_result(unknown)
        return unknown

    def _logical_id(self, *, case: CommitteeCase, role: CommitteeRole) -> str:
        return role_observation_id(
            case_id=case.case_id,
            role=role,
            prompt_version=case.policy.prompt_version,
            prompt_hash=ROLE_PROMPT_HASH[role],
            committee_policy_version=case.policy.policy_version,
            evidence_snapshot_hash=case.snapshot.snapshot_hash,
        )

    def _run_role(
        self,
        *,
        case: CommitteeCase,
        role: CommitteeRole,
        screened_view: Mapping[str, Any],
        allowed_refs: Sequence[str],
        peer_results: tuple[RoleSeatResult, ...],
        remaining_ceiling: int,
    ) -> RoleRunOutcome:
        prompt_hash = ROLE_PROMPT_HASH[role]
        logical_id = self._logical_id(case=case, role=role)

        recorded = self._ledger.role_result_for(logical_id)
        if recorded is not None:
            # A redelivered case must not buy a second paid opinion: the recorded
            # logical seat is acknowledged as-is and costs nothing new.
            return RoleRunOutcome(result=recorded, spent_microunits=0)

        try:
            route = self._registry.route_for(role, at=self._now())
        except RegistryError as exc:
            return RoleRunOutcome(
                result=self._unavailable_result(
                    case=case,
                    role=role,
                    logical_id=logical_id,
                    prompt_hash=prompt_hash,
                    detail=f"no usable governed route: {exc}",
                )
            )

        payload = self._wire_payload(
            screened_view=screened_view, role=role, peer_results=peer_results
        )
        if payload is None:
            return RoleRunOutcome(
                result=self._skipped_result(
                    case=case,
                    role=role,
                    reason=(
                        "screened request for this role exceeds the bounded "
                        "request size"
                    ),
                    logical_id=logical_id,
                    prompt_hash=prompt_hash,
                    route=route,
                )
            )

        worst_case = self._worst_case_cost(
            route=route, payload_bytes=_payload_bytes(payload, role)
        )
        if worst_case is None:
            return RoleRunOutcome(
                result=self._skipped_result(
                    case=case,
                    role=role,
                    reason=(
                        "the worst-case cost of this role's request cannot be "
                        "bounded, so its ceiling cannot be enforced"
                    ),
                    logical_id=logical_id,
                    prompt_hash=prompt_hash,
                    route=route,
                )
            )
        if worst_case > remaining_ceiling:
            # The approved per-case ceiling is the bound that actually enforces the
            # economics. No role may reserve more than what is left of it, so seven
            # role seats can never collectively exceed one case ceiling.
            return RoleRunOutcome(
                result=self._skipped_result(
                    case=case,
                    role=role,
                    reason=(
                        "committee case cost ceiling would be exceeded by this "
                        "role's worst-case reservation"
                    ),
                    logical_id=logical_id,
                    prompt_hash=prompt_hash,
                    route=route,
                )
            )

        budget = RoleBudget(
            deadline_seconds=APPROVED_DEADLINE_SECONDS,
            max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
            max_cost_microunits=worst_case,
        )

        def build_wire_request(entry: ModelRegistryEntry) -> ProviderWireRequest:
            return ProviderWireRequest(
                case_id=case.case_id,
                logical_observation_id=logical_id,
                model=entry.model_id,
                system_prompt=role_prompt(role),
                user_payload=payload,
                max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
                timeout_seconds=APPROVED_DEADLINE_SECONDS,
            )

        try:
            execution = self._router.execute(
                route=route,
                budget=budget,
                case_id=case.case_id,
                logical_observation_id=logical_id,
                build_wire_request=build_wire_request,
                allowed_evidence_refs=allowed_refs,
                prompt_version=case.policy.prompt_version,
                prompt_hash=prompt_hash,
                schema_version=ROLE_OUTPUT_SCHEMA_VERSION,
            )
        except RoleBudgetViolation as exc:
            # A budget violation is a transient, deterministic refusal: nothing was
            # invoked, so it is reported but deliberately not recorded. Recording
            # it would freeze a transient refusal permanently and stop the role
            # from answering once the daily ceiling resets.
            return RoleRunOutcome(
                result=self._skipped_result(
                    case=case,
                    role=role,
                    reason=str(exc),
                    logical_id=logical_id,
                    prompt_hash=prompt_hash,
                    route=route,
                )
            )

        result = execution.result
        self._ledger.record_role_result(result)
        return RoleRunOutcome(
            result=result,
            spent_microunits=execution.spent_microunits,
            cost_completeness=execution.cost_completeness,
            # The router already refuses to report a ceiling it could not evaluate;
            # a measured spend above the pre-flight reservation is this role
            # breaching its own bound, which is a second way to lose verification.
            ceiling_verified=(
                execution.ceiling_verified
                and execution.spent_microunits <= worst_case
            ),
        )

    def _wire_payload(
        self,
        *,
        screened_view: Mapping[str, Any],
        role: CommitteeRole,
        peer_results: tuple[RoleSeatResult, ...],
    ) -> dict[str, Any] | None:
        """The screened payload for a role, or ``None`` when it cannot be bounded.

        Only the decision synthesizer sees other roles' results, and only as an
        advisory projection: role, status, stance, sufficiency, a bounded thesis,
        the self-reported ordinal, and evidence references. No raw provider
        output, no action field, and no other role's payload is ever included.
        """
        if role is not SYNTHESIZER_ROLE:
            payload: dict[str, Any] = dict(screened_view)
        else:
            payload = dict(screened_view)
            payload["prior_role_opinions"] = [
                _peer_projection(result)
                for result in peer_results
                if result.role is not SYNTHESIZER_ROLE
            ]
        try:
            screened = screen_model_bound_view(payload)
        except Exception:
            # Prohibited outbound material must never travel, and a screening
            # failure is not a reason to send it unscreened.
            return None
        if _payload_bytes(screened, role) > MAX_ROLE_REQUEST_BYTES:
            return None
        return screened

    def _worst_case_cost(self, *, route: RoleRoute, payload_bytes: int) -> int | None:
        """The worst-case cost of this role's request, or ``None`` when unbounded.

        Bounded by the route's own primary/fallback entries so the caller can
        enforce the approved per-case ceiling against it. An unpriced model yields
        ``None`` rather than a zero that would read as free.
        """
        if payload_bytes > MAX_ROLE_REQUEST_BYTES:
            return None
        input_token_bound = (
            payload_bytes * INPUT_TOKEN_SAFETY_MULTIPLIER + PROTOCOL_TOKEN_ALLOWANCE
        )
        worst_case = 0
        for entry in route.entries:
            cost = self._price_book.cost_microunits(
                provider=entry.provider_family.value,
                model=entry.model_id,
                input_tokens=input_token_bound,
                output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
            )
            if cost is None:
                return None
            worst_case = max(worst_case, cost)
        return worst_case if worst_case > 0 else None

    # ----------------------------------------------------------- result builders

    def _role_primary_entry(self, role: CommitteeRole) -> ModelRegistryEntry | None:
        """The registry's declared primary for a role, or ``None`` when unrouted.

        Reported provider family and requested model are read from the governed
        registry rather than assumed, so a disposition is never attributed to a
        vendor the role was not routed to.
        """
        route_ids = self._registry.routes.get(role)
        if not route_ids:
            return None
        return self._registry.entry(route_ids[0])

    def _unknown_optional_result(
        self,
        *,
        case: CommitteeCase,
        role: CommitteeRole,
        screened_view: Mapping[str, Any],
        logical_id: str | None = None,
    ) -> RoleSeatResult:
        """An explicit UNKNOWN for an optional role whose evidence is absent.

        The role is reported, not omitted: an absent opinion must be visible in
        the case outcome rather than silently missing from it. No provider is
        called, so nothing is spent and no opinion is fabricated.
        """
        del screened_view
        entry = self._role_primary_entry(role)
        prompt_hash = ROLE_PROMPT_HASH[role]
        return RoleSeatResult(
            case_id=case.case_id,
            role=role,
            role_version="1",
            status=RoleResultStatus.UNKNOWN,
            prompt_version=case.policy.prompt_version,
            prompt_hash=prompt_hash,
            schema_version=ROLE_OUTPUT_SCHEMA_VERSION,
            provider_family=(
                entry.provider_family if entry is not None else ProviderFamily.OPENAI
            ),
            requested_model=entry.model_id if entry is not None else "unrouted",
            resolved_model=None,
            logical_observation_id=(
                logical_id or self._logical_id(case=case, role=role)
            ),
            attempt=1,
            recorded_at=self._now(),
            status_detail=(
                "the evidence this optional role depends on is not present in the "
                "sealed snapshot; no provider opinion was requested"
            ),
        )

    def _skipped_result(
        self,
        *,
        case: CommitteeCase,
        role: CommitteeRole,
        reason: str,
        logical_id: str | None = None,
        prompt_hash: str | None = None,
        route: RoleRoute | None = None,
    ) -> RoleSeatResult:
        entry = route.primary if route is not None else self._role_primary_entry(role)
        resolved_prompt_hash = prompt_hash or ROLE_PROMPT_HASH[role]
        return RoleSeatResult(
            case_id=case.case_id,
            role=role,
            role_version="1",
            status=RoleResultStatus.SKIPPED_BUDGET,
            prompt_version=case.policy.prompt_version,
            prompt_hash=resolved_prompt_hash,
            schema_version=ROLE_OUTPUT_SCHEMA_VERSION,
            provider_family=(
                entry.provider_family if entry is not None else ProviderFamily.OPENAI
            ),
            requested_model=entry.model_id if entry is not None else "unrouted",
            resolved_model=None,
            logical_observation_id=(
                logical_id or self._logical_id(case=case, role=role)
            ),
            attempt=1,
            recorded_at=self._now(),
            status_detail=reason,
        )

    def _unavailable_result(
        self,
        *,
        case: CommitteeCase,
        role: CommitteeRole,
        logical_id: str,
        prompt_hash: str,
        detail: str,
    ) -> RoleSeatResult:
        entry = self._role_primary_entry(role)
        return RoleSeatResult(
            case_id=case.case_id,
            role=role,
            role_version="1",
            status=RoleResultStatus.UNAVAILABLE,
            prompt_version=case.policy.prompt_version,
            prompt_hash=prompt_hash,
            schema_version=ROLE_OUTPUT_SCHEMA_VERSION,
            provider_family=(
                entry.provider_family if entry is not None else ProviderFamily.OPENAI
            ),
            requested_model=entry.model_id if entry is not None else "unrouted",
            resolved_model=None,
            logical_observation_id=logical_id,
            attempt=1,
            recorded_at=self._now(),
            status_detail=detail,
        )


def _peer_projection(result: RoleSeatResult) -> dict[str, Any]:
    """A bounded, purely advisory projection of one peer role's result."""
    thesis = result.thesis or ""
    return {
        "role": result.role.value,
        "status": result.status.value,
        "stance": None if result.stance is None else result.stance.value,
        "evidence_sufficiency": (
            None
            if result.evidence_sufficiency is None
            else result.evidence_sufficiency.value
        ),
        "thesis": thesis[:MAX_PEER_THESIS_CHARS],
        "self_reported_confidence": result.self_reported_confidence,
        "evidence_refs": list(result.evidence_refs),
        "missing_evidence": list(result.missing_evidence),
    }


def _payload_bytes(payload: Mapping[str, Any], role: CommitteeRole) -> int:
    encoded = json.dumps(
        {"role": role.value, "user_payload": dict(payload)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return len(encoded)


__all__ = [
    "COMMITTEE_ROLE_PROMPT_IDENTITY_DOMAIN",
    "INPUT_TOKEN_SAFETY_MULTIPLIER",
    "MAX_PEER_THESIS_CHARS",
    "MAX_ROLE_REQUEST_BYTES",
    "PROTOCOL_TOKEN_ALLOWANCE",
    "QUALIFIED_EVENT_METRIC_PREFIXES",
    "ROLE_CASE_OUTCOME_IDENTITY_DOMAIN",
    "ROLE_CASE_OUTCOME_SCHEMA_VERSION",
    "ROLE_INSTRUCTIONS",
    "ROLE_OBSERVATION_IDENTITY_DOMAIN",
    "ROLE_OUTPUT_SCHEMA_HASH",
    "ROLE_OUTPUT_SCHEMA_VERSION",
    "ROLE_PROMPT_HASH",
    "SEAT_ORDER",
    "SYNTHESIZER_ROLE",
    "RoleGovernedCaseOutcome",
    "RoleGovernedRunResult",
    "RoleGovernedRunner",
    "RoleResultLedger",
    "RoleRunOutcome",
    "RoleRuntimeConfigurationError",
    "build_approved_shadow_registry",
    "build_role_providers",
    "role_observation_id",
    "role_prompt",
]
