"""IC-012: measured per-attempt latency and its durability.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the rules that make latency evidence trustworthy: it is measured
with a monotonic clock rather than inferred from timestamps, it is recorded for
every attempt outcome including failures and unavailable seats, a missing
measurement stays null rather than becoming zero, aggregation uses measured
observations only, and the value survives serialization and reload.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.contracts import ProviderFailureClass, ProviderFamily
from app.opip.committee.fakes import ScriptedAnswer, ScriptedCommitteeProvider, opinion_json
from app.opip.committee.providers import ProviderAvailability
from app.opip.committee.registry import (
    ApprovalState,
    ModelRegistry,
    ModelRegistryEntry,
    ReasoningMode,
)
from app.opip.committee.role_execution import RoleBudget, RoleResultStatus, RoleSeatResult
from app.opip.committee.role_router import RoleAttempt, RoleRouter
from app.opip.committee.roles import CommitteeRole
from app.opip.committee.serialization import (
    CommitteeSerializationError,
    role_result_from_dict,
    role_result_to_dict,
)
from app.opip.committee.trust import CommitteeInvestment

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
REVIEW_BY = NOW + timedelta(days=90)
CASE_ID = "case-1"
LOGICAL_ID = "COMMITTEE-LOGICAL:aaaa"
PROMPT_HASH = "COMMITTEE-PROMPT:bbbb"
REF = "ev-1"

#: Deterministic monotonic values so a duration can be asserted exactly.
_CLOCK = [0.0]


def _clock() -> float:
    return _CLOCK[0]


def _advance(seconds: float) -> None:
    _CLOCK[0] += seconds


@pytest.fixture(autouse=True)
def _reset_clock():
    _CLOCK[0] = 0.0
    yield
    _CLOCK[0] = 0.0


def _entry(entry_id: str, model_id: str, provider: ProviderFamily = ProviderFamily.OPENAI):
    return ModelRegistryEntry(
        entry_id=entry_id,
        role=CommitteeRole.RISK_CRITIC,
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


def _route(with_fallback: bool = False):
    primary = _entry("e-primary", "model-a")
    if not with_fallback:
        registry = ModelRegistry(
            registry_version="v", entries=(primary,), routes={CommitteeRole.RISK_CRITIC: ("e-primary", None)}
        )
        return registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)
    fallback = _entry("e-fallback", "model-b", ProviderFamily.ANTHROPIC)
    registry = ModelRegistry(
        registry_version="v",
        entries=(primary, fallback),
        routes={CommitteeRole.RISK_CRITIC: ("e-primary", "e-fallback")},
    )
    return registry.route_for(CommitteeRole.RISK_CRITIC, at=NOW)


def _budget() -> RoleBudget:
    return RoleBudget(
        deadline_seconds=120, max_output_tokens=1_024, max_cost_microunits=100_000
    )


def _build_wire(entry):
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


def _answer(**overrides) -> ScriptedAnswer:
    values = {"text": opinion_json(lists={"supporting_evidence_refs": (REF,)})}
    values.update(overrides)
    return ScriptedAnswer(**values)


def _run(router: RoleRouter, *, route=None):
    return router.execute(
        route=route or _route(),
        budget=_budget(),
        case_id=CASE_ID,
        logical_observation_id=LOGICAL_ID,
        build_wire_request=_build_wire,
        allowed_evidence_refs=(REF,),
        prompt_version="1",
        prompt_hash=PROMPT_HASH,
        schema_version=1,
    )


class _AdvancingProvider(ScriptedCommitteeProvider):
    """A provider that advances the monotonic clock as it is invoked.

    This is how a real call consumes time, so the test measures a duration rather
    than fabricating one.
    """

    def __init__(self, *, delay_seconds: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self._delay = delay_seconds

    def invoke(self, request):
        _advance(self._delay)
        return super().invoke(request)


# ------------------------------------------------- measurement


def test_a_successful_attempt_records_its_measured_duration():
    provider = _AdvancingProvider(
        delay_seconds=0.25,
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(),),
    )
    router = RoleRouter(
        providers={"e-primary": provider}, now=lambda: NOW, monotonic=_clock
    )
    execution = _run(router)
    assert execution.attempts[0].latency_micros == 250_000
    assert execution.result.latency_micros == 250_000


def test_latency_is_measured_not_inferred_from_timestamps():
    """A clock that does not advance yields zero elapsed, not a timestamp delta."""
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI, model="model-a", answers=(_answer(),)
    )
    router = RoleRouter(
        providers={"e-primary": provider}, now=lambda: NOW, monotonic=_clock
    )
    execution = _run(router)
    # The wall clock never changes, so no duration can be inferred from it; the
    # monotonic measurement is what produced this value.
    assert execution.attempts[0].latency_micros == 0
    assert execution.result.latency_micros == 0


def test_a_failed_attempt_still_records_its_duration():
    """A timeout is exactly the latency an operator needs to see."""
    provider = _AdvancingProvider(
        delay_seconds=5.0,
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    router = RoleRouter(
        providers={"e-primary": provider}, now=lambda: NOW, monotonic=_clock
    )
    execution = _run(router)
    assert execution.attempts[0].status is RoleResultStatus.FAILED
    assert execution.attempts[0].latency_micros == 5_000_000


def test_an_invalid_response_records_its_duration():
    provider = _AdvancingProvider(
        delay_seconds=0.1,
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(ScriptedAnswer(text="{"),),
    )
    router = RoleRouter(
        providers={"e-primary": provider}, now=lambda: NOW, monotonic=_clock
    )
    execution = _run(router)
    assert execution.attempts[0].status is RoleResultStatus.INVALID
    assert execution.attempts[0].latency_micros == 100_000


def test_an_identity_mismatch_records_its_duration():
    provider = _AdvancingProvider(
        delay_seconds=0.3,
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(reported_model="some-other-model"),),
    )
    router = RoleRouter(
        providers={"e-primary": provider}, now=lambda: NOW, monotonic=_clock
    )
    execution = _run(router)
    assert execution.attempts[0].failure_class is (
        ProviderFailureClass.PROVIDER_IDENTITY_MISMATCH
    )
    assert execution.attempts[0].latency_micros == 300_000


def test_an_unavailable_seat_records_no_duration_because_nothing_ran():
    provider = ScriptedCommitteeProvider(
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(_answer(),),
        availability=ProviderAvailability.UNAVAILABLE,
    )
    router = RoleRouter(
        providers={"e-primary": provider}, now=lambda: NOW, monotonic=_clock
    )
    execution = _run(router)
    assert execution.attempts[0].status is RoleResultStatus.UNAVAILABLE
    # Null, not zero: nothing was invoked, so there is no duration to report.
    assert execution.attempts[0].latency_micros is None


def test_a_missing_adapter_records_no_duration():
    router = RoleRouter(providers={}, now=lambda: NOW, monotonic=_clock)
    execution = _run(router)
    assert execution.result.status is RoleResultStatus.UNAVAILABLE
    assert execution.result.latency_micros is None


def test_failover_records_a_duration_for_each_attempt():
    primary = _AdvancingProvider(
        delay_seconds=2.0,
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(ScriptedAnswer(failure_class=ProviderFailureClass.TIMEOUT),),
    )
    fallback = _AdvancingProvider(
        delay_seconds=0.5,
        family=ProviderFamily.ANTHROPIC,
        model="model-b",
        answers=(_answer(),),
    )
    router = RoleRouter(
        providers={"e-primary": primary, "e-fallback": fallback},
        now=lambda: NOW,
        monotonic=_clock,
    )
    execution = _run(router, route=_route(with_fallback=True))
    assert execution.attempts[0].latency_micros == 2_000_000
    assert execution.attempts[1].latency_micros == 500_000
    assert execution.result.latency_micros == 500_000  # the answering attempt


def test_a_payload_needing_repair_records_its_duration():
    """A payload whose form needs repair still took the whole call to arrive.

    Repair is applied by the conformance harness, not by the router: the router
    admits only a payload that already satisfies the contract, so a fenced body is a
    recorded INVALID attempt. The duration of that call is still measured, which is
    what makes a repair round visible in latency rather than invisible.
    """
    provider = _AdvancingProvider(
        delay_seconds=0.75,
        family=ProviderFamily.OPENAI,
        model="model-a",
        answers=(
            ScriptedAnswer(
                text="```json\n"
                + opinion_json(lists={"supporting_evidence_refs": (REF,)})
                + "\n```"
            ),
        ),
    )
    router = RoleRouter(
        providers={"e-primary": provider}, now=lambda: NOW, monotonic=_clock
    )
    execution = _run(router)
    assert execution.produced_an_opinion is False
    assert execution.attempts[0].status is RoleResultStatus.INVALID
    assert execution.attempts[0].latency_micros == 750_000


# ------------------------------------------------- aggregation


def test_aggregation_uses_only_measured_observations():
    measured = RoleAttempt(
        attempt=1,
        entry_id="e-primary",
        provider_family=ProviderFamily.OPENAI,
        requested_model="model-a",
        status=RoleResultStatus.FAILED,
        latency_micros=1_000,
    )
    unmeasured = RoleAttempt(
        attempt=2,
        entry_id="e-fallback",
        provider_family=ProviderFamily.ANTHROPIC,
        requested_model="model-b",
        status=RoleResultStatus.UNAVAILABLE,
    )
    from app.opip.committee.role_router import RoleExecution

    execution = RoleExecution(
        role=CommitteeRole.RISK_CRITIC,
        route=_route(with_fallback=True),
        budget=_budget(),
        attempts=(measured, unmeasured),
        result=RoleSeatResult(
            case_id=CASE_ID,
            role=CommitteeRole.RISK_CRITIC,
            role_version="1",
            status=RoleResultStatus.UNAVAILABLE,
            prompt_version="1",
            prompt_hash=PROMPT_HASH,
            schema_version=1,
            provider_family=ProviderFamily.OPENAI,
            requested_model="model-a",
            resolved_model=None,
            logical_observation_id=LOGICAL_ID,
            attempt=2,
            recorded_at=NOW,
        ),
        spent_microunits=0,
        cost_completeness=__import__(
            "app.opip.committee.contracts", fromlist=["CostCompleteness"]
        ).CostCompleteness.COMPLETE,
        ceiling_verified=True,
    )
    assert execution.measured_latencies_micros == (1_000,)
    assert execution.total_latency_micros == 1_000
    assert execution.attempts_measured == 1
    assert execution.attempts_unmeasured == 1


def test_a_role_with_no_measured_attempt_reports_unknown_latency():
    """Unknown latency must not read as an instant call."""
    report = CommitteeInvestment(unmeasured_attempts=3)
    assert report.total_latency_micros is None
    assert report.mean_latency_micros is None
    assert report.latency_sample_complete is False


def test_investment_latency_aggregates_only_measured_values():
    report = CommitteeInvestment(measured_latencies_micros=(1_000, 3_000, 2_000))
    assert report.total_latency_micros == 6_000
    assert report.mean_latency_micros == 2_000
    assert report.latency_sample_complete is True


def test_a_negative_latency_is_refused():
    with pytest.raises(ValueError, match="non-negative integers"):
        CommitteeInvestment(measured_latencies_micros=(-1,))
    with pytest.raises(ValueError, match="latency_micros"):
        RoleAttempt(
            attempt=1,
            entry_id="e",
            provider_family=ProviderFamily.OPENAI,
            requested_model="m",
            status=RoleResultStatus.FAILED,
            latency_micros=-5,
        )


# ------------------------------------------------- durability


def _result(**overrides) -> RoleSeatResult:
    from app.opip.committee.contracts import DirectionalAssessment, EvidenceSufficiency

    values = {
        "case_id": CASE_ID,
        "role": CommitteeRole.RISK_CRITIC,
        "role_version": "1",
        "status": RoleResultStatus.ANSWERED,
        "prompt_version": "1",
        "prompt_hash": PROMPT_HASH,
        "schema_version": 1,
        "provider_family": ProviderFamily.OPENAI,
        "requested_model": "model-a",
        "resolved_model": "model-a-2026-01",
        "logical_observation_id": LOGICAL_ID,
        "attempt": 1,
        "recorded_at": NOW,
        "stance": DirectionalAssessment.SUPPORTIVE,
        "evidence_sufficiency": EvidenceSufficiency.SUFFICIENT,
        "thesis": "t",
        "latency_micros": 250_000,
    }
    values.update(overrides)
    return RoleSeatResult(**values)


def test_a_role_result_round_trips_with_its_latency():
    result = _result()
    row = role_result_to_dict(result)
    assert row["latency_micros"] == 250_000
    restored = role_result_from_dict(row)
    assert restored.latency_micros == 250_000
    assert restored.role_result_id == result.role_result_id


def test_latency_survives_a_reload_by_re_encoding_identically():
    result = _result()
    first = role_result_to_dict(result)
    second = role_result_to_dict(role_result_from_dict(first))
    assert first == second


def test_a_legacy_row_without_latency_decodes_to_null_not_zero():
    """A row written before latency was measured must not look instant."""
    row = role_result_to_dict(_result())
    row.pop("latency_micros")
    restored = role_result_from_dict(row)
    assert restored.latency_micros is None


def test_a_legacy_row_keeps_its_original_identity():
    result = _result(latency_micros=None)
    row = role_result_to_dict(result)
    row.pop("latency_micros")
    assert role_result_from_dict(row).role_result_id == result.role_result_id


def test_a_corrupted_latency_value_is_refused_on_read():
    row = role_result_to_dict(_result())
    row["latency_micros"] = "250000"
    with pytest.raises(CommitteeSerializationError, match="latency_micros"):
        role_result_from_dict(row)
    row["latency_micros"] = -1
    with pytest.raises(CommitteeSerializationError, match="latency_micros"):
        role_result_from_dict(row)


def test_latency_participates_in_the_role_result_identity():
    """A changed duration is a different observation, so its identity must differ."""
    fast = _result(latency_micros=1_000)
    slow = _result(latency_micros=9_000)
    assert fast.role_result_id != slow.role_result_id


def test_a_role_result_row_of_another_kind_is_refused():
    row = role_result_to_dict(_result())
    row["kind"] = "SOMETHING_ELSE"
    with pytest.raises(CommitteeSerializationError, match="expected a"):
        role_result_from_dict(row)


def test_the_trust_report_exposes_latency_observability():
    from app.opip.committee.scheduler import (
        CommitteeScheduleDisposition,
        PopulationTally,
    )
    from app.opip.committee.trust import build_trust_report

    tally = dict.fromkeys(CommitteeScheduleDisposition, 0)
    tally[CommitteeScheduleDisposition.COMPLETED] = 5
    report = build_trust_report(
        report_version="v",
        release_sha="a" * 40,
        registry_version="r",
        generated_at=NOW,
        population=PopulationTally(counts=tally, considered=5, redelivered=0),
        investment=CommitteeInvestment(
            measured_latencies_micros=(1_000, 3_000), unmeasured_attempts=1
        ),
    )
    assert report.mean_latency_micros == 2_000
    assert report.maturity_progress["latency_measured"] == 2
    assert report.maturity_progress["latency_unmeasured"] == 1


def test_the_trust_report_reports_unknown_latency_as_null():
    from app.opip.committee.scheduler import (
        CommitteeScheduleDisposition,
        PopulationTally,
    )
    from app.opip.committee.trust import build_trust_report

    tally = dict.fromkeys(CommitteeScheduleDisposition, 0)
    report = build_trust_report(
        report_version="v",
        release_sha="a" * 40,
        registry_version="r",
        generated_at=NOW,
        population=PopulationTally(counts=tally, considered=0, redelivered=0),
        investment=CommitteeInvestment(),
    )
    assert report.mean_latency_micros is None
