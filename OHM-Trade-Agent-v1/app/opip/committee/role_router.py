"""Role routing: resolve a governed route and execute it under one budget.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This is where the three contracts meet:

``CommitteeRole`` (what is being asked)
    -> ``RoleRoute`` from the governed registry (who is permitted to answer)
    -> ``RoleBudget`` (what it may spend)
    -> ``RoleSeatResult`` (an attributable, advisory answer)

Three rules are enforced here rather than documented and hoped for:

* **One shared reservation.** The primary and the single fallback draw on the
  same deadline, token ceiling, and monetary ceiling. A fallback cannot be
  granted a fresh budget, so failing over cannot double what a case costs.
* **Bounded failover.** At most two attempts, and only for a retryable failure
  class. A schema-invalid answer is not retried: the model answered, the contract
  was not met, and asking again spends money without changing that.
* **Honest cost.** If any attempt's cost is unknown, the ceiling check is reported
  as incomplete rather than passed. An unknown cost is never treated as free, and
  an unverifiable ceiling is never reported as satisfied.

The router holds no per-run state: every attempt returns what it did, so two
concurrent role executions cannot observe each other.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable, Mapping

from app.opip.committee.contracts import (
    CostCompleteness,
    ProviderFailureClass,
    ProviderFamily,
    StructuredOpinion,
)
from app.opip.committee.opinion import OpinionParseError, parse_structured_opinion
from app.opip.committee.providers import (
    CommitteeProvider,
    ProviderAvailability,
    ProviderInvocationError,
    ProviderRawResponse,
    ProviderWireRequest,
)
from app.opip.committee.registry import ModelRegistryEntry, RoleRoute
from app.opip.committee.role_execution import (
    RoleBudget,
    RoleBudgetViolation,
    RoleResultStatus,
    RoleSeatResult,
)
from app.opip.committee.roles import CommitteeRole

#: Failure classes worth spending a second attempt on. A malformed or
#: schema-invalid response is absent on purpose: the provider answered.
RETRYABLE_ROLE_FAILURE_CLASSES = frozenset(
    {
        ProviderFailureClass.TIMEOUT,
        ProviderFailureClass.RATE_LIMIT,
        ProviderFailureClass.PROVIDER_UNAVAILABLE,
        ProviderFailureClass.INTERNAL_ERROR,
    }
)

#: The largest number of attempts a role route may make: one primary, one
#: fallback, and nothing after that.
MAX_ROLE_ATTEMPTS = 2


class RoleRoutingError(ValueError):
    """A role could not be routed under the governed registry."""


@dataclass(frozen=True)
class RoleAttempt:
    """What one attempt on a route actually did.

    The served identity is recorded separately from the requested one, so a
    provider that answers as a different model than it was asked for is visible
    rather than silently attributed.
    """

    attempt: int
    entry_id: str
    provider_family: ProviderFamily
    requested_model: str
    status: RoleResultStatus
    served_provider: str | None = None
    served_model: str | None = None
    failure_class: ProviderFailureClass | None = None
    cost_microunits: int | None = None
    detail: str | None = None
    exceeded_ceiling: bool = False


@dataclass(frozen=True)
class RoleExecution:
    """The full record of routing one role: the route, the attempts, the result.

    ``cost_completeness`` and ``ceiling_verified`` exist so a reader can tell a
    genuinely satisfied ceiling from one that could not be evaluated. The latter
    is never reported as satisfied.
    """

    role: CommitteeRole
    route: RoleRoute
    budget: RoleBudget
    attempts: tuple[RoleAttempt, ...]
    result: RoleSeatResult
    spent_microunits: int
    cost_completeness: CostCompleteness
    ceiling_verified: bool

    def __post_init__(self) -> None:
        if len(self.attempts) > MAX_ROLE_ATTEMPTS:
            raise ValueError(
                f"a role route may make at most {MAX_ROLE_ATTEMPTS} attempts"
            )
        if self.attempts and self.attempts[0].entry_id != self.route.primary.entry_id:
            raise ValueError("the first attempt must be the route's primary entry")
        if type(self.spent_microunits) is not int or self.spent_microunits < 0:
            raise ValueError("spent_microunits must be a non-negative integer")

    @property
    def produced_an_opinion(self) -> bool:
        return self.result.is_advisory_opinion

    @property
    def used_fallback(self) -> bool:
        return len(self.attempts) > 1


class RoleRouter:
    """Executes a role's governed route under one shared budget.

    Providers are supplied by registry entry id, so the registry decides which
    adapter is reachable rather than a caller naming a vendor directly.
    """

    def __init__(
        self,
        *,
        providers: Mapping[str, CommitteeProvider],
        now: Callable[[], datetime],
    ) -> None:
        self._providers = dict(providers)
        self._now = now

    def execute(
        self,
        *,
        route: RoleRoute,
        budget: RoleBudget,
        case_id: str,
        logical_observation_id: str,
        build_wire_request: Callable[[ModelRegistryEntry], ProviderWireRequest],
        allowed_evidence_refs: Iterable[str],
        prompt_version: str,
        prompt_hash: str,
        schema_version: int,
    ) -> RoleExecution:
        """Run a role's route, returning an attributable advisory result.

        The wire request is supplied by the caller through ``build_wire_request``
        rather than constructed here. That is deliberate: outbound screening must
        happen in exactly one place, so this module is not permitted to build the
        payload that leaves the process. The router decides *which* governed entry
        answers and *what it may spend*; it has no opinion on payload content.

        Provider problems never raise: a failure, an invalid response, an
        unavailable seat, and a budget skip are four distinct recorded statuses,
        because collapsing them would make them indistinguishable in the record.
        """
        started_at = self._now()
        attempts: list[RoleAttempt] = []
        spent = 0
        cost_unknown = False
        answered: tuple[RoleAttempt, StructuredOpinion, ProviderRawResponse] | None = None

        for index, entry in enumerate(route.entries, start=1):
            if index > 1:
                previous = attempts[-1]
                # A second attempt is worth making only when the first failed in a
                # way the other provider could plausibly fix.
                if previous.status is not RoleResultStatus.FAILED:
                    break
                if previous.failure_class not in RETRYABLE_ROLE_FAILURE_CLASSES:
                    break

            elapsed = max(0, int((self._now() - started_at).total_seconds()))
            try:
                budget.check_deadline(elapsed_seconds=elapsed)
            except RoleBudgetViolation as exc:
                attempts.append(
                    RoleAttempt(
                        attempt=index,
                        entry_id=entry.entry_id,
                        provider_family=entry.provider_family,
                        requested_model=entry.model_id,
                        status=RoleResultStatus.SKIPPED_BUDGET,
                        detail=str(exc),
                    )
                )
                break

            attempt, opinion, response = self._attempt(
                entry=entry,
                budget=budget,
                index=index,
                case_id=case_id,
                logical_observation_id=logical_observation_id,
                build_wire_request=build_wire_request,
                allowed_evidence_refs=allowed_evidence_refs,
            )

            if attempt.cost_microunits is None:
                cost_unknown = True
            else:
                spent += attempt.cost_microunits
                if spent > budget.max_cost_microunits and not attempt.exceeded_ceiling:
                    # The spend already happened, so it is recorded rather than
                    # hidden; the ceiling is simply reported as breached.
                    attempt = replace(
                        attempt,
                        exceeded_ceiling=True,
                        detail=(attempt.detail or "")
                        + " role cost ceiling exceeded",
                    )

            attempts.append(attempt)
            if opinion is not None and response is not None:
                answered = (attempt, opinion, response)
                break

        result, status = self._build_result(
            role=route.role,
            case_id=case_id,
            logical_observation_id=logical_observation_id,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            schema_version=schema_version,
            attempts=tuple(attempts),
            answered=answered,
        )
        return RoleExecution(
            role=route.role,
            route=route,
            budget=budget,
            attempts=tuple(attempts),
            result=result,
            spent_microunits=spent,
            cost_completeness=(
                CostCompleteness.UNKNOWN if cost_unknown else CostCompleteness.COMPLETE
            ),
            # An unknown cost means the ceiling could not be evaluated, so it is
            # reported as unverified rather than satisfied.
            ceiling_verified=(
                not cost_unknown and spent <= budget.max_cost_microunits
            ),
        )

    # -------------------------------------------------------------- internals

    def _attempt(
        self,
        *,
        entry: ModelRegistryEntry,
        budget: RoleBudget,
        index: int,
        case_id: str,
        logical_observation_id: str,
        build_wire_request: Callable[[ModelRegistryEntry], ProviderWireRequest],
        allowed_evidence_refs: Iterable[str],
    ) -> tuple[RoleAttempt, StructuredOpinion | None, ProviderRawResponse | None]:
        """One bounded attempt against one registry entry."""
        request = build_wire_request(entry)
        provider = self._providers.get(entry.entry_id)
        if provider is None or provider.availability() is ProviderAvailability.UNAVAILABLE:
            return (
                RoleAttempt(
                    attempt=index,
                    entry_id=entry.entry_id,
                    provider_family=entry.provider_family,
                    requested_model=entry.model_id,
                    status=RoleResultStatus.UNAVAILABLE,
                    detail="no adapter is configured for this registry entry",
                ),
                None,
                None,
            )
        try:
            response = provider.invoke(request)
        except ProviderInvocationError as exc:
            return (
                RoleAttempt(
                    attempt=index,
                    entry_id=entry.entry_id,
                    provider_family=entry.provider_family,
                    requested_model=entry.model_id,
                    status=RoleResultStatus.FAILED,
                    failure_class=exc.failure_class,
                    detail=str(exc),
                ),
                None,
                None,
            )

        # A response served by a model other than the one requested is refused
        # rather than attributed: crediting it would report an opinion from a
        # model this route was never approved to use.
        if response.reported_model != entry.model_id:
            return (
                RoleAttempt(
                    attempt=index,
                    entry_id=entry.entry_id,
                    provider_family=entry.provider_family,
                    requested_model=entry.model_id,
                    status=RoleResultStatus.INVALID,
                    served_provider=response.reported_provider,
                    served_model=response.reported_model,
                    failure_class=ProviderFailureClass.PROVIDER_IDENTITY_MISMATCH,
                    cost_microunits=response.estimated_cost_microunits,
                    detail=(
                        "the served model does not match the requested registry entry"
                    ),
                ),
                None,
                None,
            )

        try:
            parsed = parse_structured_opinion(
                raw_text=response.text,
                case_id=case_id,
                provider=response.reported_provider,
                model=response.reported_model,
                allowed_evidence_refs=allowed_evidence_refs,
            )
        except OpinionParseError as exc:
            return (
                RoleAttempt(
                    attempt=index,
                    entry_id=entry.entry_id,
                    provider_family=entry.provider_family,
                    requested_model=entry.model_id,
                    status=RoleResultStatus.INVALID,
                    served_provider=response.reported_provider,
                    served_model=response.reported_model,
                    failure_class=ProviderFailureClass.SCHEMA_VALIDATION_FAILURE,
                    cost_microunits=response.estimated_cost_microunits,
                    detail=str(exc),
                ),
                None,
                None,
            )

        return (
            RoleAttempt(
                attempt=index,
                entry_id=entry.entry_id,
                provider_family=entry.provider_family,
                requested_model=entry.model_id,
                status=RoleResultStatus.ANSWERED,
                served_provider=response.reported_provider,
                served_model=response.reported_model,
                cost_microunits=response.estimated_cost_microunits,
            ),
            parsed,
            response,
        )

    def _build_result(
        self,
        *,
        role: CommitteeRole,
        case_id: str,
        logical_observation_id: str,
        prompt_version: str,
        prompt_hash: str,
        schema_version: int,
        attempts: tuple[RoleAttempt, ...],
        answered: tuple[RoleAttempt, StructuredOpinion, ProviderRawResponse] | None,
    ) -> tuple[RoleSeatResult, RoleResultStatus]:
        attempt_count = max(1, len(attempts))
        if answered is not None:
            attempt, opinion, response = answered
            return (
                RoleSeatResult(
                    case_id=case_id,
                    role=role,
                    role_version="1",
                    status=RoleResultStatus.ANSWERED,
                    prompt_version=prompt_version,
                    prompt_hash=prompt_hash,
                    schema_version=schema_version,
                    provider_family=attempt.provider_family,
                    requested_model=attempt.requested_model,
                    resolved_model=attempt.served_model,
                    logical_observation_id=logical_observation_id,
                    attempt=attempt.attempt,
                    recorded_at=self._now(),
                    stance=opinion.assessment,
                    evidence_sufficiency=opinion.evidence_sufficiency,
                    thesis=opinion.hypothesis,
                    risks=tuple(opinion.risk_factors),
                    evidence_refs=tuple(opinion.supporting_evidence_refs),
                    missing_evidence=tuple(opinion.missing_evidence),
                    research_action=opinion.recommended_research_action,
                    self_reported_confidence=opinion.confidence,
                ),
                RoleResultStatus.ANSWERED,
            )
        final = attempts[-1] if attempts else None
        status = final.status if final is not None else RoleResultStatus.UNAVAILABLE
        return (
            RoleSeatResult(
                case_id=case_id,
                role=role,
                role_version="1",
                status=status,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                schema_version=schema_version,
                provider_family=(
                    final.provider_family if final is not None else ProviderFamily.OPENAI
                ),
                requested_model=final.requested_model if final is not None else "unrouted",
                resolved_model=None,
                logical_observation_id=logical_observation_id,
                attempt=attempt_count,
                recorded_at=self._now(),
                status_detail=final.detail if final is not None else "no route entry was attempted",
            ),
            status,
        )


__all__ = [
    "MAX_ROLE_ATTEMPTS",
    "RETRYABLE_ROLE_FAILURE_CLASSES",
    "RoleAttempt",
    "RoleExecution",
    "RoleRouter",
    "RoleRoutingError",
]
