"""Durable SHADOW executor: bounded spend, explicit enablement, no implicit network."""

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
    ObservationStatus,
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
    APPROVED_SHADOW_MODELS,
    approved_price_book,
)
from app.opip.committee.settings import (
    COMMITTEE_MODE_OFF,
    COMMITTEE_MODE_SHADOW,
    CommitteeShadowSettings,
)
from app.opip.committee.shadow_executor import (
    MAX_SCREENED_REQUEST_BYTES,
    SEAT_RESERVATION_MICROUNITS,
    ShadowCaseExecutor,
    ShadowExecutorConfigurationError,
    build_credentialled_shadow_executor,
    conservative_cost_reservation,
)
from app.opip.committee.store import CommitteeEvidenceStore
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


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


def _provider(family: ProviderFamily) -> ScriptedCommitteeProvider:
    model = APPROVED_SHADOW_MODELS[family]
    return ScriptedCommitteeProvider(
        family=family,
        model=model,
        answers=(
            ScriptedAnswer(
                text=opinion_json(
                    lists={"supporting_evidence_refs": ("ev-1",)}
                ),
                reported_provider=family.value,
                reported_model=model,
                estimated_cost_microunits=1_000,
                received_at=NOW,
            ),
        ),
        estimated_cost_microunits=10_000,
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


def test_executor_persists_both_calls_and_the_case_outcome(tmp_path):
    providers = {
        family: _provider(family)
        for family in (ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC)
    }
    executor = ShadowCaseExecutor(
        committee_home=tmp_path,
        providers=providers,
        settings=CommitteeShadowSettings(
            opip_committee_mode=COMMITTEE_MODE_SHADOW,
            opip_committee_max_estimated_cost_microunits=(
                APPROVED_MAX_CASE_COST_MICROUNITS
            ),
        ),
        now=lambda: NOW,
    )
    assert executor(_case()) is True

    store = CommitteeEvidenceStore(root=tmp_path)
    calls = tuple(store.iter_call_outcomes())
    cases = tuple(store.iter_case_outcomes())
    assert len(calls) == 2
    assert all(call.status is ObservationStatus.COMPLETED for call in calls)
    assert len(cases) == 1
    assert cases[0].case_id == "case-1"


def test_executor_is_idempotent_across_a_restart(tmp_path):
    providers = {
        family: _provider(family)
        for family in (ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC)
    }
    settings = CommitteeShadowSettings(
        opip_committee_mode=COMMITTEE_MODE_SHADOW,
        opip_committee_max_estimated_cost_microunits=(
            APPROVED_MAX_CASE_COST_MICROUNITS
        ),
    )
    first = ShadowCaseExecutor(
        committee_home=tmp_path,
        providers=providers,
        settings=settings,
        now=lambda: NOW,
    )
    second = ShadowCaseExecutor(
        committee_home=tmp_path,
        providers=providers,
        settings=settings,
        now=lambda: NOW,
    )
    assert first(_case()) is True
    assert second(_case()) is True
    # The durable ledger returns duplicate acknowledgements and makes no second call.
    assert len(providers[ProviderFamily.OPENAI].calls) == 1
    assert len(providers[ProviderFamily.ANTHROPIC].calls) == 1
    store = CommitteeEvidenceStore(root=tmp_path)
    assert len(tuple(store.iter_case_outcomes())) == 1


def test_off_mode_refuses_executor_construction():
    with pytest.raises(ShadowExecutorConfigurationError, match="SHADOW mode"):
        ShadowCaseExecutor(
            committee_home=".",
            providers={},
            settings=CommitteeShadowSettings(
                opip_committee_mode=COMMITTEE_MODE_OFF
            ),
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
