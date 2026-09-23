"""Role identity, governed model registry, and role budget contracts (IC-006..IC-011).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the separation that makes a role result attributable: a role is
an analytical responsibility, a registry entry is a governed permission to answer
it, and neither may stand in for the other.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.contracts import (
    DirectionalAssessment,
    EvidenceSufficiency,
    ObservationStatus,
    ProviderFamily,
    ResearchAction,
)
from app.opip.committee.registry import (
    ApprovalState,
    ModelRegistry,
    ModelRegistryEntry,
    ReasoningMode,
    RegistryError,
    assert_result_served_by_route,
)
from app.opip.committee.role_execution import (
    MAX_ROLE_CONCURRENCY,
    RoleBudget,
    RoleBudgetViolation,
    RoleResultStatus,
    RoleSeatResult,
    assert_evidence_refs_are_supported,
    assert_no_action_fields,
    role_status_for_observation,
)
from app.opip.committee.roles import (
    OPTIONAL_ROLES,
    REQUIRED_ROLES,
    CommitteeRole,
    CommitteeRoleSpec,
    RoleRequirement,
    RoleSpecSet,
    default_role_spec_set,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=30)
EXPIRES = NOW + timedelta(days=90)


# ------------------------------------------------------------------ IC-006


def test_the_canonical_role_set_seats_every_required_role():
    spec_set = default_role_spec_set()
    spec_set.validate_complete()
    assert spec_set.missing_required_roles() == ()
    assert set(spec_set.roles()) == set(CommitteeRole)


def test_the_seven_governed_roles_are_exactly_as_declared():
    assert {role.value for role in CommitteeRole} == {
        "REGIME_ANALYST",
        "LIQUIDITY_STRUCTURE_ANALYST",
        "EVENT_SENTIMENT_ANALYST",
        "BULL_ADVOCATE",
        "BEAR_ADVOCATE",
        "RISK_CRITIC",
        "DECISION_SYNTHESIZER",
    }
    # The bull case, the bear case, and the risk critique are separate roles.
    # Collapsing them would erase the disagreement the attribution layer measures.
    assert CommitteeRole.BULL_ADVOCATE is not CommitteeRole.BEAR_ADVOCATE
    assert CommitteeRole.BEAR_ADVOCATE is not CommitteeRole.RISK_CRITIC


def test_a_provider_family_is_never_a_role():
    """Provider identity must not substitute for role identity."""
    role_values = {role.value for role in CommitteeRole}
    for family in ProviderFamily:
        assert family.value not in role_values
        assert family.name not in role_values
    # The reserved independent-reviewer seat is a provider concern, not a role.
    assert ProviderFamily.INDEPENDENT_REVIEWER.value not in role_values


def test_event_sentiment_is_optional_and_the_rest_are_required():
    assert CommitteeRole.EVENT_SENTIMENT_ANALYST in OPTIONAL_ROLES
    assert CommitteeRole.EVENT_SENTIMENT_ANALYST not in REQUIRED_ROLES
    for role in (
        CommitteeRole.REGIME_ANALYST,
        CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST,
        CommitteeRole.BULL_ADVOCATE,
        CommitteeRole.BEAR_ADVOCATE,
        CommitteeRole.RISK_CRITIC,
        CommitteeRole.DECISION_SYNTHESIZER,
    ):
        assert role in REQUIRED_ROLES


def test_a_role_set_missing_a_required_role_cannot_validate():
    """A policy cannot omit the bear case and still produce a committee result."""
    spec_set = default_role_spec_set()
    trimmed = RoleSpecSet(
        spec_set_version="trimmed",
        specs=tuple(
            spec for spec in spec_set.specs if spec.role is not CommitteeRole.BEAR_ADVOCATE
        ),
    )
    assert trimmed.missing_required_roles() == (CommitteeRole.BEAR_ADVOCATE,)
    with pytest.raises(ValueError, match="requires every required role"):
        trimmed.validate_complete()


def test_a_required_role_cannot_be_declared_optional():
    with pytest.raises(ValueError, match="cannot be declared optional"):
        RoleSpecSet(
            spec_set_version="bad",
            specs=(
                CommitteeRoleSpec(
                    role=CommitteeRole.RISK_CRITIC,
                    role_version="1",
                    requirement=RoleRequirement.OPTIONAL,
                    prompt_template_id="p",
                    prompt_version="1",
                ),
            ),
        )


def test_the_optional_role_cannot_be_declared_required():
    """Its evidence may genuinely not exist, so demanding it would force a guess."""
    with pytest.raises(ValueError, match="must be declared optional"):
        RoleSpecSet(
            spec_set_version="bad",
            specs=(
                CommitteeRoleSpec(
                    role=CommitteeRole.EVENT_SENTIMENT_ANALYST,
                    role_version="1",
                    requirement=RoleRequirement.REQUIRED,
                    prompt_template_id="p",
                    prompt_version="1",
                ),
            ),
        )


def test_each_role_carries_its_own_prompt_identity():
    spec_set = default_role_spec_set()
    templates = [spec.prompt_template_id for spec in spec_set.specs]
    assert len(set(templates)) == len(templates)
    for spec in spec_set.specs:
        assert spec.prompt_version == "1"
        assert spec.spec_hash.startswith("COMMITTEE-ROLE-SPECS:")


def test_a_role_result_cannot_be_re_attributed_to_another_prompt_version():
    spec = CommitteeRoleSpec(
        role=CommitteeRole.RISK_CRITIC,
        role_version="1",
        requirement=RoleRequirement.REQUIRED,
        prompt_template_id="committee.role.risk_critic",
        prompt_version="1",
    )
    other = CommitteeRoleSpec(
        role=CommitteeRole.RISK_CRITIC,
        role_version="1",
        requirement=RoleRequirement.REQUIRED,
        prompt_template_id="committee.role.risk_critic",
        prompt_version="2",
    )
    assert spec.spec_hash != other.spec_hash


# --------------------------------------------------------- IC-009 / IC-010


def _entry(
    entry_id: str,
    role: CommitteeRole = CommitteeRole.RISK_CRITIC,
    *,
    approval: ApprovalState = ApprovalState.APPROVED,
    provider: ProviderFamily = ProviderFamily.OPENAI,
    model_id: str = "model-a",
    effective_from: datetime = NOW - timedelta(days=1),
    review_by: datetime = EXPIRES,
) -> ModelRegistryEntry:
    return ModelRegistryEntry(
        entry_id=entry_id,
        role=role,
        provider_family=provider,
        model_id=model_id,
        endpoint="https://example.invalid/v1",
        prompt_hash="COMMITTEE-PROMPT:aaaa",
        schema_hash="COMMITTEE-SCHEMA:bbbb",
        owner="owner",
        approval=approval,
        effective_from=effective_from,
        review_by=review_by,
        reasoning_mode=ReasoningMode.LOW,
        max_output_tokens=4_096,
        deadline_seconds=60,
        max_cost_microunits=5_000,
        data_retention_route="no-training",
    )


def _registry(*entries: ModelRegistryEntry, routes=None) -> ModelRegistry:
    if routes is None:
        routes = {}
        for entry in entries:
            existing = routes.get(entry.role)
            if existing is None:
                routes[entry.role] = (entry.entry_id, None)
            else:
                routes[entry.role] = (existing[0], entry.entry_id)
    return ModelRegistry(registry_version="registry-v1", entries=tuple(entries), routes=routes)


def test_a_role_resolves_to_its_primary_and_one_fallback():
    primary = _entry("e-primary", model_id="model-a")
    fallback = _entry(
        "e-fallback",
        model_id="model-b",
        provider=ProviderFamily.ANTHROPIC,
    )
    registry = _registry(primary, fallback)
    route = registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)
    assert route.primary.entry_id == "e-primary"
    assert route.fallback is not None
    assert route.fallback.entry_id == "e-fallback"
    assert len(route.entries) == 2


def test_a_route_never_exceeds_one_fallback():
    """At most one fallback: an unbounded chain is unbounded spend."""
    entries = (
        _entry("e1", model_id="m1"),
        _entry("e2", model_id="m2"),
        _entry("e3", model_id="m3"),
    )
    registry = ModelRegistry(
        registry_version="v",
        entries=entries,
        routes={CommitteeRole.RISK_CRITIC: ("e1", "e2")},
    )
    route = registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)
    assert len(route.entries) == 2
    assert "e3" not in {entry.entry_id for entry in route.entries}


def test_the_fallback_shares_the_primary_budget_and_never_enlarges_it():
    primary = _entry("e-primary", model_id="model-a")
    fallback = _entry("e-fallback", model_id="model-b")
    route = _registry(primary, fallback).route_for(CommitteeRole.RISK_CRITIC, at=NOW)
    assert route.shared_budget == {
        "max_output_tokens": primary.max_output_tokens,
        "deadline_seconds": primary.deadline_seconds,
        "max_cost_microunits": primary.max_cost_microunits,
    }


def test_an_unregistered_model_alias_is_rejected():
    with pytest.raises(RegistryError, match="unregistered"):
        ModelRegistry(
            registry_version="v",
            entries=(_entry("e1"),),
            routes={CommitteeRole.RISK_CRITIC: ("e-missing", None)},
        )


def test_a_role_with_no_registered_route_fails_closed():
    registry = _registry(_entry("e1"))
    with pytest.raises(RegistryError, match="no governed route is registered"):
        registry.route_for(CommitteeRole.BEAR_ADVOCATE, at=NOW)


def test_a_provisional_entry_is_not_routable():
    """Provisional is a bake-off state, not production approval."""
    registry = _registry(_entry("e1", approval=ApprovalState.PROVISIONAL))
    with pytest.raises(RegistryError, match="not usable"):
        registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)


def test_a_suspended_entry_is_not_routable():
    registry = _registry(_entry("e1", approval=ApprovalState.SUSPENDED))
    with pytest.raises(RegistryError, match="not usable"):
        registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)


def test_an_expired_entry_is_refused_as_drift():
    registry = _registry(_entry("e1", review_by=NOW + timedelta(days=1)))
    assert registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW).primary.entry_id == "e1"
    with pytest.raises(RegistryError, match="not usable"):
        registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW + timedelta(days=2))


def test_an_entry_is_not_usable_before_it_is_effective():
    registry = _registry(_entry("e1", effective_from=NOW + timedelta(days=1)))
    with pytest.raises(RegistryError, match="not usable"):
        registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)


def test_an_unusable_fallback_is_dropped_and_not_substituted():
    """The primary stays governed; a third option is never invented."""
    primary = _entry("e-primary", model_id="a")
    fallback = _entry("e-fallback", model_id="b", approval=ApprovalState.SUSPENDED)
    route = _registry(primary, fallback).route_for(CommitteeRole.RISK_CRITIC, at=NOW)
    assert route.fallback is None
    assert route.entries == (route.primary,)


def test_a_fallback_cannot_be_the_primary():
    with pytest.raises(ValueError, match="cannot fall back to its primary"):
        ModelRegistry(
            registry_version="v",
            entries=(_entry("e1"),),
            routes={CommitteeRole.RISK_CRITIC: ("e1", "e1")},
        )


def test_a_served_identity_off_the_route_is_refused():
    """Crediting an unapproved model would misattribute the role's opinion."""
    primary = _entry("e-primary", model_id="model-a")
    route = _registry(primary).route_for(CommitteeRole.RISK_CRITIC, at=NOW)
    assert_result_served_by_route(
        route=route, provider_family=ProviderFamily.OPENAI, model_id="model-a"
    )
    with pytest.raises(RegistryError, match="is not on the governed route"):
        assert_result_served_by_route(
            route=route, provider_family=ProviderFamily.OPENAI, model_id="model-z"
        )
    with pytest.raises(RegistryError, match="is not on the governed route"):
        assert_result_served_by_route(
            route=route, provider_family=ProviderFamily.DEEPSEEK, model_id="model-a"
        )


def test_a_route_names_the_role_it_belongs_to():
    with pytest.raises(ValueError, match="does not serve this role"):
        from app.opip.committee.registry import RoleRoute

        RoleRoute(
            role=CommitteeRole.BEAR_ADVOCATE,
            primary=_entry("e1", CommitteeRole.RISK_CRITIC),
            fallback=None,
            registry_version="v",
        )


def test_registry_identity_changes_when_a_route_changes():
    one = _registry(_entry("e1", model_id="a"))
    two = _registry(_entry("e1", model_id="b"))
    assert one.registry_hash != two.registry_hash


# ------------------------------------------------------------------ IC-011


def test_role_budget_enforces_the_deadline():
    budget = RoleBudget(
        deadline_seconds=60, max_output_tokens=1_000, max_cost_microunits=1_000
    )
    budget.check_deadline(elapsed_seconds=60)
    with pytest.raises(RoleBudgetViolation, match="deadline exceeded"):
        budget.check_deadline(elapsed_seconds=61)


def test_role_budget_enforces_the_cost_ceiling():
    budget = RoleBudget(
        deadline_seconds=60, max_output_tokens=1_000, max_cost_microunits=1_000
    )
    budget.check_cost(spent_microunits=1_000)
    with pytest.raises(RoleBudgetViolation, match="cost ceiling exceeded"):
        budget.check_cost(spent_microunits=1_001)


def test_an_unknown_cost_is_not_treated_as_free():
    """A ceiling that cannot be evaluated cannot be enforced."""
    budget = RoleBudget(
        deadline_seconds=60, max_output_tokens=1_000, max_cost_microunits=1_000
    )
    with pytest.raises(RoleBudgetViolation, match="known non-negative integer"):
        budget.check_cost(spent_microunits=None)  # type: ignore[arg-type]


def test_concurrency_is_bounded():
    RoleBudget(
        deadline_seconds=1,
        max_output_tokens=1,
        max_cost_microunits=1,
        max_concurrency=MAX_ROLE_CONCURRENCY,
    )
    with pytest.raises(ValueError, match="max_concurrency"):
        RoleBudget(
            deadline_seconds=1,
            max_output_tokens=1,
            max_cost_microunits=1,
            max_concurrency=MAX_ROLE_CONCURRENCY + 1,
        )


def test_a_budget_requires_positive_limits():
    for field, value in (
        ("deadline_seconds", 0),
        ("max_output_tokens", 0),
        ("max_cost_microunits", 0),
    ):
        kwargs = {
            "deadline_seconds": 60,
            "max_output_tokens": 1_000,
            "max_cost_microunits": 1_000,
        }
        kwargs[field] = value
        with pytest.raises(ValueError, match=field):
            RoleBudget(**kwargs)


def test_observation_statuses_map_onto_role_statuses_without_collapsing():
    assert role_status_for_observation(ObservationStatus.COMPLETED) is (
        RoleResultStatus.ANSWERED
    )
    assert role_status_for_observation(ObservationStatus.FAILED) is RoleResultStatus.FAILED
    assert role_status_for_observation(ObservationStatus.INVALID) is RoleResultStatus.INVALID
    assert role_status_for_observation(ObservationStatus.UNAVAILABLE) is (
        RoleResultStatus.UNAVAILABLE
    )
    assert role_status_for_observation(ObservationStatus.SKIPPED_BUDGET) is (
        RoleResultStatus.SKIPPED_BUDGET
    )
    # A failure, an invalid response, an unavailable seat, and a budget skip are
    # four different facts and must stay four different statuses.
    statuses = {
        role_status_for_observation(status)
        for status in (
            ObservationStatus.FAILED,
            ObservationStatus.INVALID,
            ObservationStatus.UNAVAILABLE,
            ObservationStatus.SKIPPED_BUDGET,
        )
    }
    assert len(statuses) == 4


def _result(**overrides) -> RoleSeatResult:
    values = {
        "case_id": "case-1",
        "role": CommitteeRole.RISK_CRITIC,
        "role_version": "1",
        "status": RoleResultStatus.ANSWERED,
        "prompt_version": "1",
        "prompt_hash": "COMMITTEE-PROMPT:aaaa",
        "schema_version": 1,
        "provider_family": ProviderFamily.OPENAI,
        "requested_model": "model-a",
        "resolved_model": "model-a-2026-01",
        "logical_observation_id": "COMMITTEE-LOGICAL:cccc",
        "attempt": 1,
        "recorded_at": NOW,
        "stance": DirectionalAssessment.SUPPORTIVE,
        "evidence_sufficiency": EvidenceSufficiency.SUFFICIENT,
        "thesis": "evidence supports the hypothesis",
        "self_reported_confidence": 70,
    }
    values.update(overrides)
    return RoleSeatResult(**values)


def test_an_answered_role_result_carries_its_full_provenance():
    result = _result()
    assert result.is_advisory_opinion
    payload = result.identity_payload()
    for key in (
        "role",
        "role_version",
        "prompt_version",
        "prompt_hash",
        "role_output_schema_version",
        "provider_family",
        "requested_model",
        "resolved_model",
        "logical_observation_id",
        "attempt",
        "recorded_at",
    ):
        assert key in payload
    assert result.role_result_id.startswith("COMMITTEE-ROLE-RESULT:")


def test_an_unanswered_role_result_may_not_carry_a_stance():
    """A failure is not a vote, so it cannot hold an opinion."""
    with pytest.raises(ValueError, match="only an ANSWERED role result may carry"):
        _result(status=RoleResultStatus.FAILED)
    with pytest.raises(ValueError, match="only an ANSWERED role result may carry"):
        _result(status=RoleResultStatus.SKIPPED_BUDGET)


def test_an_answered_role_result_must_actually_carry_an_opinion():
    with pytest.raises(ValueError, match="must carry a stance and a thesis"):
        _result(stance=None)
    with pytest.raises(ValueError, match="must carry a stance and a thesis"):
        _result(thesis=None)


def test_an_answered_role_result_must_record_the_resolved_model():
    """Without it, the opinion cannot be attributed to what actually served it."""
    with pytest.raises(ValueError, match="must record the resolved model"):
        _result(resolved_model=None)


def test_a_missing_confidence_is_null_and_never_zero():
    result = _result(self_reported_confidence=None, rubric_score=None)
    payload = result.identity_payload()
    assert payload["self_reported_confidence"] is None
    assert payload["rubric_score"] is None


def test_confidence_must_stay_ordinal_in_range():
    for value in (-1, 101):
        with pytest.raises(ValueError, match="self_reported_confidence"):
            _result(self_reported_confidence=value)


def test_action_fields_are_refused_outright():
    with pytest.raises(ValueError, match="may not carry action fields"):
        assert_no_action_fields({"stance": "SUPPORTIVE", "size": 100})
    with pytest.raises(ValueError, match="may not carry action fields"):
        assert_no_action_fields({"StOp_LoSS": "1.0"})
    # A research-only shape is accepted.
    assert_no_action_fields(
        {"stance": "SUPPORTIVE", "thesis": "t", "research_action": "NO_ACTION"}
    )


def test_an_unsupported_evidence_citation_is_refused():
    """A hallucinated reference invalidates the result rather than being stored."""
    assert_evidence_refs_are_supported(
        refs=["ev-1"], supported_refs=["ev-1", "ev-2"]
    )
    with pytest.raises(ValueError, match="outside the screened evidence view"):
        assert_evidence_refs_are_supported(refs=["ev-9"], supported_refs=["ev-1"])


def test_a_research_action_is_not_an_execution_instruction():
    """The research vocabulary contains no order-shaped action."""
    assert {action.value for action in ResearchAction} == {
        "NO_ACTION",
        "GATHER_MORE_EVIDENCE",
        "REVISIT_AT_NEXT_WINDOW",
        "ESCALATE_FOR_HUMAN_REVIEW",
    }
    result = _result(research_action=ResearchAction.ESCALATE_FOR_HUMAN_REVIEW)
    assert result.is_advisory_opinion
