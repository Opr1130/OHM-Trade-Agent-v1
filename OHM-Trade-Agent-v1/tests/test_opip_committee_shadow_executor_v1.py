"""Durable role-governed SHADOW executor: bounded spend, explicit enablement."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.opip.committee import shadow_executor
from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    EvidenceItem,
    EvidenceSnapshot,
    ProviderFamily,
)
from app.opip.committee.fakes import (
    ScriptedAnswer,
    ScriptedCommitteeProvider,
    opinion_json,
)
from app.opip.committee.providers import ProviderWireRequest
from app.opip.committee.registry import (
    APPROVED_MAX_CASE_COST_MICROUNITS,
    approved_price_book,
)
from app.opip.committee.role_execution import RoleResultStatus
from app.opip.committee.role_runtime import (
    SEAT_ORDER,
    SYNTHESIZER_ROLE,
    build_approved_shadow_registry,
)
from app.opip.committee.roles import CommitteeRole
from app.opip.committee.settings import (
    COMMITTEE_MODE_OFF,
    COMMITTEE_MODE_SHADOW,
    CommitteeShadowSettings,
)
from app.opip.committee.shadow_executor import (
    MAX_SCREENED_REQUEST_BYTES,
    REGISTRY_REVIEW_BY_ENV,
    SEAT_RESERVATION_MICROUNITS,
    SHADOW_APPROVED_FROM_ENV,
    ShadowCaseExecutor,
    ShadowExecutorConfigurationError,
    build_credentialled_shadow_executor,
    conservative_cost_reservation,
)
from app.opip.committee.store import CommitteeEvidenceStore
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
REVIEW_BY = datetime(2026, 12, 23, 12, 0, tzinfo=timezone.utc)


def _case() -> CommitteeCase:
    policy = CommitteePolicy(
        policy_version="committee-shadow-case-v1",
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id="committee.case.market_opportunity",
        prompt_version="1",
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
    )
    evidence = EvidenceItem(
        evidence_id="ev-1",
        source_id="EVT:1",
        available_at=NOW,
        payload={
            "observed_at": NOW.isoformat(),
            "instrument_id": "INSTR:1",
            "metric_name": "snapshot.last_price",
            "metric_value": "65000.0",
        },
    )
    snapshot = EvidenceSnapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=NOW,
        assembled_at=NOW,
        items=(evidence,),
        source_refs=("EVT:1",),
        committee_policy_version=policy.policy_version,
        prompt_template_id=policy.prompt_template_id,
        prompt_version=policy.prompt_version,
        instrument_id="INSTR:1",
        strategy_context_id="CTX:1",
    )
    return CommitteeCase(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=NOW,
        provenance=Provenance(
            producing_component="test.shadow_executor",
            artifact_or_build_id="a" * 40,
            process_instance_id="test-1",
            emitted_at=NOW,
            source_record_refs=("EVT:1",),
        ),
        instrument_id="INSTR:1",
        strategy_context_id="CTX:1",
    )


def _registry():
    return build_approved_shadow_registry(
        approved_from=datetime(2026, 9, 1, tzinfo=timezone.utc),
        review_by=REVIEW_BY,
    )


def _role_providers(registry) -> dict[str, ScriptedCommitteeProvider]:
    """One scripted adapter per governed registry entry, keyed by entry id."""
    providers: dict[str, ScriptedCommitteeProvider] = {}
    for entry in registry.entries:
        # Each role names exactly one primary per family in SEAT_ORDER, so the
        # scripted answer is identical across the seven roles.
        providers[entry.entry_id] = ScriptedCommitteeProvider(
            family=entry.provider_family,
            model=entry.model_id,
            answers=(
                ScriptedAnswer(
                    text=opinion_json(
                        lists={"supporting_evidence_refs": ("ev-1",)}
                    ),
                    reported_provider=entry.provider_family.value,
                    reported_model=entry.model_id,
                    estimated_cost_microunits=1_000,
                    received_at=NOW,
                ),
            ),
            estimated_cost_microunits=10_000,
        )
    return providers


def _settings(mode: str = COMMITTEE_MODE_SHADOW) -> CommitteeShadowSettings:
    return CommitteeShadowSettings(
        opip_committee_mode=mode,
        opip_committee_max_estimated_cost_microunits=(
            APPROVED_MAX_CASE_COST_MICROUNITS
        ),
    )


def _wire(*, payload: str = "x") -> ProviderWireRequest:
    return ProviderWireRequest(
        case_id="case-1",
        logical_observation_id="COMMITTEE-LOGICAL:test",
        model="gpt-5.6-terra",
        system_prompt="bounded prompt",
        user_payload={"evidence": payload},
        max_output_tokens=1_200,
        timeout_seconds=45,
    )


def test_preflight_reservation_is_bounded_and_known():
    reservation = conservative_cost_reservation(
        request=_wire(),
        family=ProviderFamily.OPENAI,
        price_book=approved_price_book(),
    )
    assert reservation == SEAT_RESERVATION_MICROUNITS
    assert reservation * 2 == APPROVED_MAX_CASE_COST_MICROUNITS


def test_oversize_request_has_no_cost_bound_and_is_refused():
    assert (
        conservative_cost_reservation(
            request=_wire(payload="x" * (MAX_SCREENED_REQUEST_BYTES + 1)),
            family=ProviderFamily.OPENAI,
            price_book=approved_price_book(),
        )
        is None
    )


def test_executor_runs_the_governed_roles_and_persists_role_evidence(tmp_path):
    registry = _registry()
    providers = _role_providers(registry)
    executor = ShadowCaseExecutor(
        committee_home=tmp_path,
        registry=registry,
        providers=providers,
        price_book=approved_price_book(),
        settings=_settings(),
        now=lambda: NOW,
    )
    assert executor(_case()) is True

    store = CommitteeEvidenceStore(root=tmp_path)
    results = {row.role: row for row in store.iter_role_results()}
    # Every governed role is seated, in the canonical population.
    assert set(results) == set(SEAT_ORDER)
    # The event/sentiment role depends on qualified retained evidence that this
    # snapshot does not contain, so it reports UNKNOWN explicitly, with no call.
    assert results[CommitteeRole.EVENT_SENTIMENT_ANALYST].status is (
        RoleResultStatus.UNKNOWN
    )
    answered = {
        role for role, row in results.items() if row.status is RoleResultStatus.ANSWERED
    }
    assert answered == set(SEAT_ORDER) - {CommitteeRole.EVENT_SENTIMENT_ANALYST}

    outcomes = tuple(store.iter_role_case_outcomes())
    assert len(outcomes) == 1
    assert outcomes[0].case_id == "case-1"
    assert outcomes[0].synthesis is not None

    # The role path writes role evidence, never the provider-seat case stream.
    assert tuple(store.iter_case_outcomes()) == ()


def test_the_synthesizer_sees_the_other_roles_and_only_them(tmp_path):
    registry = _registry()
    providers = _role_providers(registry)
    executor = ShadowCaseExecutor(
        committee_home=tmp_path,
        registry=registry,
        providers=providers,
        price_book=approved_price_book(),
        settings=_settings(),
        now=lambda: NOW,
    )
    assert executor(_case()) is True

    synthesizer_entry = [
        entry
        for entry in registry.entries
        if entry.role is SYNTHESIZER_ROLE
    ]
    seen: list[dict] = []
    for entry in synthesizer_entry:
        provider = providers[entry.entry_id]
        for request in provider.calls:
            seen.append(dict(request.user_payload))
    assert seen, "the synthesizer must have been invoked"
    for payload in seen:
        peers = payload["prior_role_opinions"]
        assert all(item["role"] != SYNTHESIZER_ROLE.value for item in peers)
        assert {item["role"] for item in peers} == {
            role.value for role in SEAT_ORDER if role is not SYNTHESIZER_ROLE
        }
        # Only a screened advisory projection travels, never a raw provider body.
        assert all("assessment" not in item for item in peers)


def test_executor_is_idempotent_across_a_restart(tmp_path):
    registry = _registry()
    providers = _role_providers(registry)
    first = ShadowCaseExecutor(
        committee_home=tmp_path,
        registry=registry,
        providers=providers,
        price_book=approved_price_book(),
        settings=_settings(),
        now=lambda: NOW,
    )
    assert first(_case()) is True
    calls_after_first = {
        entry_id: len(provider.calls) for entry_id, provider in providers.items()
    }

    restarted = _role_providers(registry)
    second = ShadowCaseExecutor(
        committee_home=tmp_path,
        registry=registry,
        providers=restarted,
        price_book=approved_price_book(),
        settings=_settings(),
        now=lambda: NOW,
    )
    assert second(_case()) is True
    # A redelivered case buys no second paid opinion for any governed seat.
    assert all(len(provider.calls) == 0 for provider in restarted.values())
    assert all(count == 1 for count in calls_after_first.values() if count)

    store = CommitteeEvidenceStore(root=tmp_path)
    assert len(tuple(store.iter_role_results())) == len(SEAT_ORDER)
    assert len(tuple(store.iter_role_case_outcomes())) == 1


def test_off_mode_refuses_executor_construction():
    registry = _registry()
    price_book = approved_price_book()
    settings = _settings(COMMITTEE_MODE_OFF)
    with pytest.raises(ShadowExecutorConfigurationError, match="SHADOW mode"):
        ShadowCaseExecutor(
            committee_home=".",
            registry=registry,
            providers={},
            price_book=price_book,
            settings=settings,
        )


def test_production_builder_refuses_off_before_touching_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shadow_executor, "resolve_committee_mode", lambda: "off")
    touched = []

    def forbidden_credentials(*args, **kwargs):
        touched.append(True)
        raise AssertionError("credentials must not be read while OFF")

    monkeypatch.setattr(
        shadow_executor, "EnvironmentCredentialSource", forbidden_credentials
    )
    with pytest.raises(ShadowExecutorConfigurationError, match="while mode is OFF"):
        build_credentialled_shadow_executor(committee_home=tmp_path)
    assert touched == []


def test_production_builder_requires_both_provider_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shadow_executor, "resolve_committee_mode", lambda: "shadow")
    monkeypatch.setattr(
        shadow_executor, "resolve_committee_cost_ceiling", lambda: None
    )
    with pytest.raises(
        ShadowExecutorConfigurationError,
        match="both approved provider credentials",
    ):
        build_credentialled_shadow_executor(
            committee_home=tmp_path,
            environ={"OPIP_COMMITTEE_OPENAI_API_KEY": "test-openai-only"},
            poster=lambda request: None,
        )


def test_production_builder_requires_an_explicit_registry_approval_window(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(shadow_executor, "resolve_committee_mode", lambda: "shadow")
    monkeypatch.setattr(
        shadow_executor, "resolve_committee_cost_ceiling", lambda: None
    )
    environ = {
        "OPIP_COMMITTEE_OPENAI_API_KEY": "test-openai",
        "OPIP_COMMITTEE_ANTHROPIC_API_KEY": "test-anthropic",
    }
    # Both credentials present but no approval window: the registry cannot be
    # approved by default, so construction fails closed.
    with pytest.raises(
        ShadowExecutorConfigurationError, match=SHADOW_APPROVED_FROM_ENV
    ):
        build_credentialled_shadow_executor(
            committee_home=tmp_path,
            environ=environ,
            poster=lambda request: None,
        )

    environ[SHADOW_APPROVED_FROM_ENV] = NOW.isoformat()
    with pytest.raises(
        ShadowExecutorConfigurationError, match=REGISTRY_REVIEW_BY_ENV
    ):
        build_credentialled_shadow_executor(
            committee_home=tmp_path,
            environ=environ,
            poster=lambda request: None,
        )

    environ[REGISTRY_REVIEW_BY_ENV] = REVIEW_BY.isoformat()
    executor = build_credentialled_shadow_executor(
        committee_home=tmp_path,
        environ=environ,
        poster=lambda request: None,
    )
    assert isinstance(executor, ShadowCaseExecutor)
