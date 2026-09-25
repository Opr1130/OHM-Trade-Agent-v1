"""Governed seven-role SHADOW runtime: seating, budgets, durability, screening."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    CostCompleteness,
    EvidenceItem,
    EvidenceSnapshot,
    ProviderFamily,
)
from app.opip.committee.fakes import (
    ScriptedAnswer,
    ScriptedCommitteeProvider,
    opinion_json,
)
from app.opip.committee.opinion import OPINION_FIELDS
from app.opip.committee.pricing import COMMITTEE_PRICES_ENV, PriceBook
from app.opip.committee.registry import (
    APPROVED_MAX_CASE_COST_MICROUNITS,
    RegistryError,
)
from app.opip.committee.role_execution import RoleResultStatus
from app.opip.committee.role_runtime import (
    ROLE_PROMPT_HASH,
    SEAT_ORDER,
    SYNTHESIZER_ROLE,
    RoleGovernedRunner,
    build_approved_shadow_registry,
    role_observation_id,
    role_prompt,
)
from app.opip.committee.roles import OPTIONAL_ROLES, CommitteeRole
from app.opip.committee.runtime import CommitteePolicyViolation
from app.opip.committee.serialization import (
    CommitteeSerializationError,
    role_case_outcome_from_dict,
    role_case_outcome_to_dict,
    role_result_to_dict,
)
from app.opip.committee.settings import (
    COMMITTEE_MODE_OFF,
    COMMITTEE_MODE_SHADOW,
    CommitteeShadowSettings,
)
from app.opip.committee.store import (
    CommitteeEvidenceStore,
    DurableRoleResultLedger,
)
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
APPROVED_FROM = datetime(2026, 9, 1, tzinfo=timezone.utc)
REVIEW_BY = datetime(2026, 12, 23, tzinfo=timezone.utc)


def _case(
    *,
    ceiling: int | None = APPROVED_MAX_CASE_COST_MICROUNITS,
    extra_evidence: tuple[EvidenceItem, ...] = (),
) -> CommitteeCase:
    policy = CommitteePolicy(
        policy_version="committee-shadow-case-v1",
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id="committee.case.market_opportunity",
        prompt_version="1",
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=ceiling,
    )
    evidence = (
        EvidenceItem(
            evidence_id="ev-1",
            source_id="EVT:1",
            available_at=NOW,
            payload={
                "observed_at": NOW.isoformat(),
                "instrument_id": "INSTR:1",
                "metric_name": "snapshot.last_price",
                "metric_value": "65000.0",
            },
        ),
    ) + extra_evidence
    snapshot = EvidenceSnapshot(
        case_id="case-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=NOW,
        assembled_at=NOW,
        items=evidence,
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
            producing_component="test.role_runtime",
            artifact_or_build_id="a" * 40,
            process_instance_id="test-1",
            emitted_at=NOW,
            source_record_refs=("EVT:1",),
        ),
        instrument_id="INSTR:1",
        strategy_context_id="CTX:1",
    )


def _registry(*, approved_from: datetime = APPROVED_FROM, review_by: datetime = REVIEW_BY):
    return build_approved_shadow_registry(
        approved_from=approved_from, review_by=review_by
    )


def _providers(registry, *, answer_text: str | None = None):
    text = answer_text if answer_text is not None else opinion_json(
        lists={"supporting_evidence_refs": ("ev-1",)}
    )
    return {
        entry.entry_id: ScriptedCommitteeProvider(
            family=entry.provider_family,
            model=entry.model_id,
            answers=(
                ScriptedAnswer(
                    text=text,
                    reported_provider=entry.provider_family.value,
                    reported_model=entry.model_id,
                    estimated_cost_microunits=1_000,
                    received_at=NOW,
                ),
            ),
            estimated_cost_microunits=10_000,
        )
        for entry in registry.entries
    }


def _runner(
    tmp_path,
    *,
    registry=None,
    providers=None,
    price_book=None,
    settings=None,
    ledger=None,
    answer_text=None,
):
    registry = registry or _registry()
    if providers is None:
        providers = _providers(registry, answer_text=answer_text)
    store = CommitteeEvidenceStore(root=tmp_path)
    return (
        RoleGovernedRunner(
            registry=registry,
            providers=providers,
            ledger=ledger or DurableRoleResultLedger(store=store),
            price_book=price_book or _approved_price_book(),
            now=lambda: NOW,
            settings=settings
            or CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        ),
        store,
        providers,
    )


def _approved_price_book() -> PriceBook:
    from app.opip.committee.registry import APPROVED_PRICE_BOOK_SPEC

    return PriceBook.from_env({COMMITTEE_PRICES_ENV: APPROVED_PRICE_BOOK_SPEC})


# --------------------------------------------------------------------- seating


def test_every_governed_role_is_seated_in_canonical_order(tmp_path):
    runner, store, _ = _runner(tmp_path)
    result = runner.run_case(_case())

    assert tuple(row.role for row in result.role_results) == SEAT_ORDER
    assert set(row.role for row in store.iter_role_results()) == set(SEAT_ORDER)


def test_the_optional_role_reports_unknown_when_its_evidence_does_not_exist(tmp_path):
    runner, _, providers = _runner(tmp_path)
    result = runner.run_case(_case())

    by_role = {row.role: row for row in result.role_results}
    optional = by_role[CommitteeRole.EVENT_SENTIMENT_ANALYST]
    assert optional.status is RoleResultStatus.UNKNOWN
    assert optional.stance is None
    assert "not present in the sealed snapshot" in optional.status_detail
    # No provider was asked for an opinion it could not have had evidence for.
    invoked = {entry_id for entry_id, provider in providers.items() if provider.calls}
    assert all(
        not entry_id.startswith("event_sentiment_analyst") for entry_id in invoked
    )


def test_the_required_roles_all_answer_when_evidence_is_sufficient(tmp_path):
    runner, _, _ = _runner(tmp_path)
    result = runner.run_case(_case())

    answered = {row.role for row in result.role_results if row.is_advisory_opinion}
    assert answered == set(SEAT_ORDER) - {CommitteeRole.EVENT_SENTIMENT_ANALYST}
    assert result.case_outcome.missing_required_roles == ()
    assert result.case_outcome.synthesis is not None
    assert result.case_outcome.synthesis.role is SYNTHESIZER_ROLE


def test_bull_and_bear_are_distinct_roles_with_distinct_prompts():
    bull = role_prompt(CommitteeRole.BULL_ADVOCATE)
    bear = role_prompt(CommitteeRole.BEAR_ADVOCATE)
    assert bull != bear
    # The prompts are distinct contracts with distinct identities.
    assert ROLE_PROMPT_HASH[CommitteeRole.BULL_ADVOCATE] != (
        ROLE_PROMPT_HASH[CommitteeRole.BEAR_ADVOCATE]
    )


def test_the_prompt_asks_for_exactly_the_closed_field_set():
    prompt = role_prompt(CommitteeRole.RISK_CRITIC)
    for field in OPINION_FIELDS:
        assert field in prompt, field


# ------------------------------------------------------------------- budgets


def test_the_case_ceiling_bounds_the_collective_role_spend(tmp_path):
    # A per-case ceiling below one role reservation cannot fund any role, so no
    # provider may be called and every required role reports the skip.
    runner, _, providers = _runner(tmp_path)
    result = runner.run_case(_case(ceiling=1_000))

    skipped = {
        row.role
        for row in result.role_results
        if row.status is RoleResultStatus.SKIPPED_BUDGET
    }
    assert skipped == set(SEAT_ORDER) - {CommitteeRole.EVENT_SENTIMENT_ANALYST}
    assert all(not provider.calls for provider in providers.values())
    assert result.case_outcome.spent_microunits == 0


def test_a_role_whose_cost_cannot_be_bounded_is_skipped_not_sent(tmp_path):
    # An absurd price book makes every worst-case bound exceed the reservation.
    expensive = PriceBook.from_env(
        {
            COMMITTEE_PRICES_ENV: (
                "openai:gpt-5.6-terra=9000000000/9000000000;"
                "anthropic:claude-sonnet-5=9000000000/9000000000"
            )
        }
    )
    runner, _, providers = _runner(tmp_path, price_book=expensive)
    result = runner.run_case(_case())

    by_role = {row.role: row for row in result.role_results}
    assert by_role[CommitteeRole.REGIME_ANALYST].status is (
        RoleResultStatus.SKIPPED_BUDGET
    )
    assert all(not provider.calls for provider in providers.values())


def test_the_case_ceiling_bounds_every_role_by_construction(tmp_path):
    """Seven role seats can never collectively exceed one approved case ceiling.

    The bound is the case ceiling minus spend so far, applied per role, so the
    invariant holds without a fixed per-role share that could starve a role whose
    worst case is legitimately larger than one seventh.
    """
    runner, _, providers = _runner(tmp_path)
    assert runner._case_ceiling(_case()) == APPROVED_MAX_CASE_COST_MICROUNITS
    # A ceiling below any role's worst case funds nothing at all.
    result = runner.run_case(_case(ceiling=1))
    assert all(not provider.calls for provider in providers.values())
    assert result.case_outcome.spent_microunits == 0
    assert result.case_outcome.missing_required_roles == tuple(
        role for role in SEAT_ORDER if role not in OPTIONAL_ROLES
    )
    assert result.case_outcome.complete is False


# --------------------------------------------------------------- governed routes


def test_a_route_outside_its_approval_window_is_refused_not_served(tmp_path):
    # The registry is approved only from a future instant, so no role may route.
    future = datetime(2027, 1, 1, tzinfo=timezone.utc)
    registry = _registry(approved_from=future, review_by=datetime(
        2027, 6, 1, tzinfo=timezone.utc
    ))
    with pytest.raises(RegistryError, match="not usable"):
        registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)

    runner, _, providers = _runner(tmp_path, registry=registry)
    result = runner.run_case(_case())
    by_role = {row.role: row for row in result.role_results}
    assert by_role[CommitteeRole.RISK_CRITIC].status is (
        RoleResultStatus.UNAVAILABLE
    )
    assert all(not provider.calls for provider in providers.values())


def test_a_served_model_off_the_route_is_invalid(tmp_path):
    registry = _registry()
    providers = _providers(registry)
    # Rewrite every adapter to report a model the route was never approved for.
    for entry in registry.entries:
        providers[entry.entry_id] = ScriptedCommitteeProvider(
            family=entry.provider_family,
            model=entry.model_id,
            answers=(
                ScriptedAnswer(
                    text=opinion_json(),
                    reported_provider=entry.provider_family.value,
                    reported_model="some-other-model",
                    received_at=NOW,
                ),
            ),
        )
    runner, _, _ = _runner(tmp_path, registry=registry, providers=providers)
    result = runner.run_case(_case())
    answered = [row for row in result.role_results if row.is_advisory_opinion]
    assert answered == []


# --------------------------------------------------------------- model output


def test_an_action_bearing_response_is_invalid(tmp_path):
    bad = opinion_json(
        overrides={"stop_loss": "1.0", "position_size": "5"}
    )
    runner, _, _ = _runner(tmp_path, answer_text=bad)
    result = runner.run_case(_case())
    by_role = {row.role: row for row in result.role_results}
    assert by_role[CommitteeRole.RISK_CRITIC].status is RoleResultStatus.INVALID
    assert by_role[CommitteeRole.RISK_CRITIC].stance is None


def test_a_hallucinated_evidence_reference_is_invalid(tmp_path):
    bad = opinion_json(
        lists={"supporting_evidence_refs": ("ev-not-in-the-snapshot",)}
    )
    runner, _, _ = _runner(tmp_path, answer_text=bad)
    result = runner.run_case(_case())
    by_role = {row.role: row for row in result.role_results}
    assert by_role[CommitteeRole.BEAR_ADVOCATE].status is RoleResultStatus.INVALID


# ------------------------------------------------------------------ durability


def test_a_redelivered_case_buys_no_second_paid_opinion(tmp_path):
    runner, store, providers = _runner(tmp_path)
    runner.run_case(_case())
    first_calls = {
        entry_id: len(provider.calls)
        for entry_id, provider in providers.items()
        if provider.calls
    }
    assert first_calls

    restarted, _, fresh = _runner(tmp_path)
    restarted.run_case(_case())

    assert all(not provider.calls for provider in fresh.values())
    assert len(tuple(store.iter_role_results())) == len(SEAT_ORDER)


def test_a_divergent_result_for_one_logical_seat_is_refused(tmp_path):
    runner, store, _ = _runner(tmp_path)
    result = runner.run_case(_case())
    seat = next(
        row for row in result.role_results if row.is_advisory_opinion
    )
    divergent = _replace_thesis(seat)
    with pytest.raises(CommitteeSerializationError, match="different content"):
        store.append_role_result(divergent)


def test_the_role_case_outcome_round_trips_through_the_codec(tmp_path):
    runner, _, _ = _runner(tmp_path)
    outcome = runner.run_case(_case()).case_outcome
    decoded = role_case_outcome_from_dict(role_case_outcome_to_dict(outcome))
    assert decoded.role_case_outcome_id == outcome.role_case_outcome_id
    assert decoded.role_results == outcome.role_results


def test_the_role_result_codec_preserves_an_unmeasured_latency(tmp_path):
    runner, _, _ = _runner(tmp_path)
    result = runner.run_case(_case())
    unknown = next(
        row for row in result.role_results if row.status is RoleResultStatus.UNKNOWN
    )
    assert unknown.latency_micros is None
    assert role_result_to_dict(unknown)["latency_micros"] is None


# --------------------------------------------------------------------- gating


def test_off_mode_refuses_the_role_runtime(tmp_path):
    runner, _, _ = _runner(
        tmp_path,
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_OFF),
    )
    case = _case()
    with pytest.raises(CommitteePolicyViolation, match="disabled"):
        runner.run_case(case)


def test_the_logical_role_seat_is_role_scoped_not_model_scoped():
    first = role_observation_id(
        case_id="case-1",
        role=CommitteeRole.RISK_CRITIC,
        prompt_version="1",
        prompt_hash="h",
        committee_policy_version="p",
        evidence_snapshot_hash="s",
    )
    second = role_observation_id(
        case_id="case-1",
        role=CommitteeRole.RISK_CRITIC,
        prompt_version="1",
        prompt_hash="h",
        committee_policy_version="p",
        evidence_snapshot_hash="s",
    )
    other_role = role_observation_id(
        case_id="case-1",
        role=CommitteeRole.BEAR_ADVOCATE,
        prompt_version="1",
        prompt_hash="h",
        committee_policy_version="p",
        evidence_snapshot_hash="s",
    )
    assert first == second
    assert first != other_role


def _replace_thesis(result):
    from dataclasses import replace

    return replace(result, thesis="a materially different thesis")


# ------------------------------------------------- defects found in independent review


def test_redelivering_a_case_with_an_absent_optional_role_does_not_fail(tmp_path):
    """The optional-absent seat must be idempotent, not a divergence.

    The seat is recorded on the first run and re-derived on a redelivery. If the
    re-derived result carried a different recorded_at it would share the logical
    seat identity but not the content identity, and the append would be refused as
    a divergence - turning every redelivered SHADOW case into a permanently FAILED
    one with orphaned evidence.
    """
    from app.opip.committee.shadow_executor import ShadowCaseExecutor

    registry = _registry()
    executor = ShadowCaseExecutor(
        committee_home=tmp_path,
        registry=registry,
        providers=_providers(registry),
        price_book=_approved_price_book(),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=lambda: NOW,
    )
    assert executor(_case()) is True

    store = CommitteeEvidenceStore(root=tmp_path)
    first = next(
        row
        for row in store.iter_role_results()
        if row.role is CommitteeRole.EVENT_SENTIMENT_ANALYST
    )
    assert first.status is RoleResultStatus.UNKNOWN

    # A later clock and a fresh provider map: a redelivery must not re-derive a
    # divergent seat, and must not spend.
    restarted_providers = _providers(registry)
    restarted = ShadowCaseExecutor(
        committee_home=tmp_path,
        registry=registry,
        providers=restarted_providers,
        price_book=_approved_price_book(),
        settings=CommitteeShadowSettings(opip_committee_mode=COMMITTEE_MODE_SHADOW),
        now=lambda: NOW.replace(minute=59),
    )
    assert restarted(_case()) is True

    assert all(not provider.calls for provider in restarted_providers.values())
    rows = tuple(store.iter_role_results())
    assert len(rows) == len(SEAT_ORDER)
    optional_again = next(
        row
        for row in rows
        if row.role is CommitteeRole.EVENT_SENTIMENT_ANALYST
    )
    assert optional_again.role_result_id == first.role_result_id
    assert len(tuple(store.iter_role_case_outcomes())) == 1


def test_the_optional_role_answers_when_its_evidence_namespace_is_present(tmp_path):
    """The optional gate is matched on the producer's metric namespace.

    The producer derives every evidence_id as a content hash, so a literal id could
    never match and the role would be permanently unaskable. A metric in the
    qualified namespace must let it answer.
    """
    runner, _, providers = _runner(tmp_path)
    extra = (
        EvidenceItem(
            evidence_id="ev-event-1",
            source_id="EVT:event",
            available_at=NOW,
            payload={
                "observed_at": NOW.isoformat(),
                "instrument_id": "INSTR:1",
                "metric_name": "event.retained.headline",
                "metric_value": "listed",
            },
        ),
    )
    result = runner.run_case(_case(extra_evidence=extra))
    by_role = {row.role: row for row in result.role_results}
    assert by_role[CommitteeRole.EVENT_SENTIMENT_ANALYST].status is (
        RoleResultStatus.ANSWERED
    )
    assert any(
        entry_id.startswith("event_sentiment_analyst") and provider.calls
        for entry_id, provider in providers.items()
    )


def test_a_role_that_spends_past_its_reservation_unverifies_the_case_ceiling(tmp_path):
    """Measured spend above the pre-flight reservation must stop the ceiling claim.

    The per-role reservation is a pre-flight bound. A provider that reports more
    than it was reserved is a real spend event, so the case must record that its
    ceiling was not verified rather than asserting one.
    """
    registry = _registry()
    over = 400_000
    providers = {
        entry.entry_id: ScriptedCommitteeProvider(
            family=entry.provider_family,
            model=entry.model_id,
            answers=(
                ScriptedAnswer(
                    text=opinion_json(
                        lists={"supporting_evidence_refs": ("ev-1",)}
                    ),
                    reported_provider=entry.provider_family.value,
                    reported_model=entry.model_id,
                    received_at=NOW,
                ),
            ),
            estimated_cost_microunits=10_000,
            cost_override=over,
        )
        for entry in registry.entries
    }
    runner, _, _ = _runner(tmp_path, registry=registry, providers=providers)
    result = runner.run_case(_case())

    assert result.case_outcome.spent_microunits > 0
    # Once the running total can no longer fit the ceiling, every later role is
    # skipped, so the case is bounded even though one role overran its own share.
    assert result.case_outcome.spent_microunits <= (
        APPROVED_MAX_CASE_COST_MICROUNITS + over
    )
    assert result.case_outcome.ceiling_verified is False


def test_an_unmeasured_role_cost_is_recorded_as_incomplete_not_zero(tmp_path):
    """Unknown cost must never be reported as a verified zero."""
    registry = _registry()
    providers = {
        entry.entry_id: ScriptedCommitteeProvider(
            family=entry.provider_family,
            model=entry.model_id,
            answers=(
                ScriptedAnswer(
                    text=opinion_json(
                        lists={"supporting_evidence_refs": ("ev-1",)}
                    ),
                    reported_provider=entry.provider_family.value,
                    reported_model=entry.model_id,
                    estimated_cost_microunits=None,
                    cost_completeness=CostCompleteness.UNKNOWN,
                    input_tokens=None,
                    output_tokens=None,
                    received_at=NOW,
                ),
            ),
        )
        for entry in registry.entries
    }
    runner, _, _ = _runner(tmp_path, registry=registry, providers=providers)
    result = runner.run_case(_case())

    assert result.case_outcome.spent_microunits == 0
    assert result.case_outcome.cost_completeness is CostCompleteness.UNKNOWN
    assert result.case_outcome.ceiling_verified is False
    # The incompleteness survives the codec rather than being flattened.
    decoded = role_case_outcome_from_dict(
        role_case_outcome_to_dict(result.case_outcome)
    )
    assert decoded.cost_completeness is CostCompleteness.UNKNOWN
    assert decoded.ceiling_verified is False


def test_an_incomplete_committee_is_not_reported_as_complete(tmp_path):
    """A case that cannot seat a required role must say so."""
    runner, _, _ = _runner(tmp_path, price_book=_unpriced_price_book())
    result = runner.run_case(_case())
    assert result.case_outcome.complete is False
    assert result.case_outcome.missing_required_roles
    assert result.case_outcome.missing_required_roles == tuple(
        role for role in SEAT_ORDER if role not in OPTIONAL_ROLES
    )


def test_no_roster_role_can_be_omitted_from_the_seat_order():
    """SEAT_ORDER must be exactly the governed role set."""
    assert set(SEAT_ORDER) == set(CommitteeRole)
    assert SEAT_ORDER == tuple(
        role for role in CommitteeRole
    ) or set(SEAT_ORDER) == set(CommitteeRole)


def _unpriced_price_book() -> PriceBook:
    """A price book that prices no model, so every bound is unknown."""
    return PriceBook.from_env({COMMITTEE_PRICES_ENV: "openai:other-model=1/1"})
