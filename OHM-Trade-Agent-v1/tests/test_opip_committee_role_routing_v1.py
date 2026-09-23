"""Role routing: governed route resolution and bounded execution (IC-007..IC-011).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the routing guarantees that make a role result attributable: one
shared budget, at most one fallback, no silent substitution, and an honest cost
report when a ceiling cannot be evaluated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from app.opip.committee.contracts import (
    CostCompleteness,
    ProviderFailureClass,
    ProviderFamily,
)
from app.opip.committee.fakes import ScriptedAnswer, ScriptedCommitteeProvider, opinion_json
from app.opip.committee.providers import ProviderAvailability
from app.opip.committee.registry import (
    ApprovalState,
    ModelRegistry,
    ModelRegistryEntry,
    ReasoningMode,
)
from app.opip.committee.role_execution import RoleBudget, RoleResultStatus
from app.opip.committee.role_router import MAX_ROLE_ATTEMPTS, RoleRouter
from app.opip.committee.roles import CommitteeRole

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
REVIEW_BY = NOW + timedelta(days=90)

CASE_ID = "case-1"
LOGICAL_ID = "COMMITTEE-LOGICAL:aaaa"
PROMPT_HASH = "COMMITTEE-PROMPT:bbbb"
EVIDENCE_REF = "ev-1"


def _entry(
    entry_id: str,
    model_id: str,
    *,
    provider: ProviderFamily = ProviderFamily.OPENAI,
    role: CommitteeRole = CommitteeRole.RISK_CRITIC,
) -> ModelRegistryEntry:
    return ModelRegistryEntry(
        entry_id=entry_id,
        role=role,
        provider_family=provider,
        model_id=model_id,
        endpoint="https://example.invalid/v1",
        prompt_hash=PROMPT_HASH,
        schema_hash="COMMITTEE-SCHEMA:cccc",
        owner="owner",
        approval=ApprovalState.APPROVED,
        effective_from=NOW - timedelta(days=1),
        review_by=REVIEW_BY,
        reasoning_mode=ReasoningMode.LOW,
        max_output_tokens=1_024,
        deadline_seconds=120,
        max_cost_microunits=100_000,
    )


def _route_with_fallback():
    primary = _entry("e-primary", "model-a")
    fallback = _entry("e-fallback", "model-b", provider=ProviderFamily.ANTHROPIC)
    registry = ModelRegistry(
        registry_version="registry-v1",
        entries=(primary, fallback),
        routes={CommitteeRole.RISK_CRITIC: ("e-primary", "e-fallback")},
    )
    return registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)


def _route_primary_only():
    primary = _entry("e-primary", "model-a")
    registry = ModelRegistry(
        registry_version="registry-v1",
        entries=(primary,),
        routes={CommitteeRole.RISK_CRITIC: ("e-primary", None)},
    )
    return registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)


def _budget(**overrides) -> RoleBudget:
    values = {
        "deadline_seconds": 120,
        "max_output_tokens": 1_024,
        "max_cost_microunits": 100_000,
    }
    values.update(overrides)
    return RoleBudget(**values)


def _answer(**overrides) -> ScriptedAnswer:
    values = {
        "text": opinion_json(
            lists={"supporting_evidence_refs": (EVIDENCE_REF,)},
        ),
    }
    values.update(overrides)
    return ScriptedAnswer(**values)


def _build_wire(entry):
    """Stand in for the runtime's single wire-construction point.

    The router is not permitted to build a wire request itself, so tests inject a
    builder exactly as the runtime does.
    """
    from app.opip.committee.providers import ProviderWireRequest

    return ProviderWireRequest(
        case_id=CASE_ID,
        logical_observation_id=LOGICAL_ID,
        model=entry.model_id,
        system_prompt="role prompt",
        user_payload={"evidence": "screened"},
        max_output_tokens=entry.max_output_tokens or 1_024,
        timeout_seconds=entry.deadline_seconds or 120,
    )


def _execute(router: RoleRouter, *, route=None, budget=None):
    return router.execute(
        route=route or _route_primary_only(),
        budget=budget or _budget(),
        case_id=CASE_ID,
        logical_observation_id=LOGICAL_ID,
        build_wire_request=_build_wire,
        allowed_evidence_refs=(EVIDENCE_REF,),
        prompt_version="1",
        prompt_hash=PROMPT_HASH,
        schema_version=1,
    )


def test_a_primary_answer_produces_an_attributable_result():
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI, model="model-a", answers=(_answer(),)
    )
    router = RoleRouter(providers={"e-primary": provider}, now=lambda: NOW)
    execution = _execute(router)
    assert execution.produced_an_opinion
    assert execution.used_fallback is False
    assert len(execution.attempts) == 1
    result = execution.result
    assert result.role is CommitteeRole.RISK_CRITIC
    assert result.status is RoleResultStatus.ANSWERED
    assert result.resolved_model == "model-a"
    assert result.requested_model == "model-a"
    assert result.prompt_hash == PROMPT_HASH
    assert result.evidence_refs == (EVIDENCE_REF,)
    assert execution.ceiling_verified is True
    assert execution.cost_completeness is CostCompleteness.COMPLETE


def test_a_retryable_primary_failure_uses_the_single_fallback():
    primary = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    fallback = ScriptedCommitteeProvider(
        family=ProviderFamily.ANTHROPIC, model="model-b", answers=(_answer(),)
    )
    router = RoleRouter(
        providers={"e-primary": primary, "e-fallback": fallback}, now=lambda: NOW
    )
    execution = _execute(router, route=_route_with_fallback())
    assert execution.used_fallback is True
    assert len(execution.attempts) == MAX_ROLE_ATTEMPTS
    assert execution.produced_an_opinion
    assert execution.result.requested_model == "model-b"
    assert execution.attempts[0].failure_class is ProviderFailureClass.TIMEOUT
    assert execution.attempts[1].status is RoleResultStatus.ANSWERED


def test_a_schema_invalid_answer_is_not_retried():
    """The provider answered; the contract was not met, so asking again is waste."""
    primary = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI, model="model-a", answers=(ScriptedAnswer(text="{"),)
    )
    fallback = ScriptedCommitteeProvider(
        family=ProviderFamily.ANTHROPIC, model="model-b", answers=(_answer(),)
    )
    router = RoleRouter(
        providers={"e-primary": primary, "e-fallback": fallback}, now=lambda: NOW
    )
    execution = _execute(router, route=_route_with_fallback())
    assert len(execution.attempts) == 1
    assert execution.attempts[0].status is RoleResultStatus.INVALID
    assert fallback.calls == []
    assert execution.produced_an_opinion is False


def test_an_identity_mismatch_is_refused_and_not_retried():
    """An unapproved model must not have its opinion credited to the role."""
    primary = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(reported_model="some-other-model"),),
    )
    fallback = ScriptedCommitteeProvider(
        family=ProviderFamily.ANTHROPIC, model="model-b", answers=(_answer(),)
    )
    router = RoleRouter(
        providers={"e-primary": primary, "e-fallback": fallback}, now=lambda: NOW
    )
    execution = _execute(router, route=_route_with_fallback())
    assert len(execution.attempts) == 1
    assert execution.attempts[0].status is RoleResultStatus.INVALID
    assert execution.attempts[0].failure_class is (
        ProviderFailureClass.PROVIDER_IDENTITY_MISMATCH
    )
    assert execution.produced_an_opinion is False


def test_failover_shares_one_budget_and_never_enlarges_the_request():
    """The fallback gets the same request limits, not a fresh reservation."""
    primary = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    fallback = ScriptedCommitteeProvider(
        family=ProviderFamily.ANTHROPIC, model="model-b", answers=(_answer(),)
    )
    router = RoleRouter(
        providers={"e-primary": primary, "e-fallback": fallback}, now=lambda: NOW
    )
    budget = _budget()
    execution = _execute(router, route=_route_with_fallback(), budget=budget)
    assert execution.used_fallback
    # Both attempts carried the route's own limits.
    for provider in (primary, fallback):
        assert provider.calls[0].max_output_tokens == 1_024
        assert provider.calls[0].timeout_seconds == 120
    # The budget is one reservation for the role, not one per attempt.
    assert execution.budget is budget
    assert execution.budget.max_cost_microunits == budget.max_cost_microunits


def test_an_unavailable_seat_yields_no_opinion_and_is_not_substituted():
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(),),
        availability=ProviderAvailability.UNAVAILABLE,
    )
    router = RoleRouter(providers={"e-primary": provider}, now=lambda: NOW)
    execution = _execute(router)
    assert execution.produced_an_opinion is False
    assert execution.result.status is RoleResultStatus.UNAVAILABLE
    assert execution.result.resolved_model is None
    assert provider.calls == []


def test_an_unconfigured_adapter_is_unavailable_rather_than_improvised():
    router = RoleRouter(providers={}, now=lambda: NOW)
    execution = _execute(router)
    assert execution.result.status is RoleResultStatus.UNAVAILABLE
    assert execution.produced_an_opinion is False


def test_an_unknown_cost_leaves_the_ceiling_unverified():
    """An unknown cost is not free, and an unverifiable ceiling is not satisfied."""
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(estimated_cost_microunits=None),),
    )
    router = RoleRouter(providers={"e-primary": provider}, now=lambda: NOW)
    execution = _execute(router)
    assert execution.cost_completeness is CostCompleteness.UNKNOWN
    assert execution.ceiling_verified is False
    # The opinion is still reported; only the ceiling claim is withheld.
    assert execution.produced_an_opinion


def test_exceeding_the_cost_ceiling_is_recorded_not_hidden():
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(estimated_cost_microunits=50_000),),
    )
    router = RoleRouter(providers={"e-primary": provider}, now=lambda: NOW)
    execution = _execute(router, budget=_budget(max_cost_microunits=10_000))
    assert execution.attempts[0].exceeded_ceiling is True
    assert execution.spent_microunits == 50_000
    assert execution.ceiling_verified is False


def test_an_expired_deadline_skips_the_attempt():
    """A clock that advances past the deadline means the attempt is refused."""
    times = iter([NOW, NOW + timedelta(seconds=200)])

    def now() -> datetime:
        return next(times, NOW + timedelta(seconds=200))

    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI, model="model-a", answers=(_answer(),)
    )
    router = RoleRouter(providers={"e-primary": provider}, now=now)
    execution = _execute(router, budget=_budget(deadline_seconds=1))
    assert provider.calls == []
    assert execution.produced_an_opinion is False
    assert execution.result.status is RoleResultStatus.SKIPPED_BUDGET


def test_the_router_holds_no_state_between_executions():
    """Two role executions cannot observe each other."""
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(), _answer()),
    )
    router = RoleRouter(providers={"e-primary": provider}, now=lambda: NOW)
    first = _execute(router)
    second = _execute(router)
    assert first.produced_an_opinion and second.produced_an_opinion
    assert first.result.thesis is not None
    assert second.result.thesis is not None
    # Each execution reports only its own attempt.
    assert len(first.attempts) == 1
    assert len(second.attempts) == 1
    assert first.spent_microunits == second.spent_microunits


def test_an_evidence_citation_outside_the_screened_view_invalidates_the_result():
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(
            ScriptedAnswer(
                text=opinion_json(lists={"supporting_evidence_refs": ("ev-999",)})
            ),
        ),
    )
    router = RoleRouter(providers={"e-primary": provider}, now=lambda: NOW)
    execution = _execute(router)
    assert execution.produced_an_opinion is False
    assert execution.result.status is RoleResultStatus.INVALID


def test_a_failed_role_result_carries_no_stance():
    """A failure is not a vote, so it cannot hold an opinion."""
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.AUTH_FAILURE),),
    )
    router = RoleRouter(providers={"e-primary": provider}, now=lambda: NOW)
    execution = _execute(router)
    assert execution.result.status is RoleResultStatus.FAILED
    assert execution.result.stance is None
    assert execution.result.thesis is None


def test_exhausting_both_attempts_still_reports_the_last_status():
    primary = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    fallback = ScriptedCommitteeProvider(
        family=ProviderFamily.ANTHROPIC,
        model="model-b",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.RATE_LIMIT),),
    )
    router = RoleRouter(
        providers={"e-primary": primary, "e-fallback": fallback}, now=lambda: NOW
    )
    execution = _execute(router, route=_route_with_fallback())
    assert len(execution.attempts) == MAX_ROLE_ATTEMPTS
    assert execution.result.status is RoleResultStatus.FAILED
    assert execution.result.status_detail is not None
