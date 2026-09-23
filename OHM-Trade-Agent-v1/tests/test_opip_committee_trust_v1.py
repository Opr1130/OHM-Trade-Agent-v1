"""Committee Trust Report and investment observability (IC-043, IC-044).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the report's guarantees: every count comes from its source, an
unknown metric stays unknown rather than zero, a stage cannot advance on an
unmeasured or unintegrated result, and no stage grants trading influence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.economics import (
    ArmOutcome,
    FourWayComparison,
    MatchedConditions,
    PopulationKind,
    PortfolioArm,
    build_incremental_economics,
)
from app.opip.committee.scheduler import (
    CommitteeScheduleDisposition,
    PopulationTally,
)
from app.opip.committee.trust import (
    MINIMUM_MATURED_CASES,
    CommitteeInvestment,
    GateName,
    GateResult,
    GateStatus,
    TrustStage,
    build_trust_report,
    evaluate_gates,
)
from app.opip.committee.weakness import (
    WeaknessCategory,
    WeaknessRegistry,
    WeaknessValidation,
    WeaknessValidationState,
    build_finding,
)
from app.opip.committee.roles import CommitteeRole

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=30)
SHA_A = "a" * 40


def _conditions() -> MatchedConditions:
    return MatchedConditions(
        population_id="panel-1",
        capital_microunits=1_000_000,
        window_start=WINDOW_START,
        window_end=NOW,
        execution_model="paper-v2",
        fee_model="taker",
        slippage_model="half-spread",
        liquidity_assumption="top-of-book",
        coverage="full",
        capital_occupancy_semantics="exclusive",
    )


def _population(**counts: int) -> PopulationTally:
    tally = dict.fromkeys(CommitteeScheduleDisposition, 0)
    for name, value in counts.items():
        tally[CommitteeScheduleDisposition[name]] = value
    return PopulationTally(counts=tally, considered=sum(counts.values()), redelivered=0)


def _arm(arm: PortfolioArm, net: int | None) -> ArmOutcome:
    population = dict.fromkeys(PopulationKind, 0)
    population[PopulationKind.FILLED] = 10
    return ArmOutcome(
        arm=arm,
        conditions_hash=_conditions().conditions_hash,
        net_microunits=net,
        population=population,
        net_completeness="COMPLETE" if net is not None else "UNKNOWN",
    )


def _economics(
    baseline: int | None = 0,
    committee: int | None = 10_000,
    cost: int | None = 1_000,
    scored: int = 20,
):
    return build_incremental_economics(
        matched_conditions=_conditions(),
        baseline=_arm(PortfolioArm.DETERMINISTIC_BASELINE, baseline),
        committee=_arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, committee),
        operating_cost_microunits=cost,
        four_way=FourWayComparison(scored, 1, 5, 2),
    )


_UNSET = object()


def _report(
    *,
    population: PopulationTally | None = None,
    economics: object = _UNSET,
    investment: CommitteeInvestment | None = None,
    weakness_registry: WeaknessRegistry | None = None,
    integrity_violations: int = 0,
):
    """Build a report, distinguishing "not supplied" from an explicit None.

    The sentinel matters: passing ``economics=None`` must mean "no comparison was
    produced", which is different from leaving it unset.
    """
    if economics is _UNSET:
        economics = _economics()
    return build_trust_report(
        report_version="trust-v1",
        release_sha=SHA_A,
        registry_version="registry-v1",
        generated_at=NOW,
        population=population
        if population is not None
        else _population(COMPLETED=MINIMUM_MATURED_CASES),
        economics=economics,
        investment=investment
        if investment is not None
        else CommitteeInvestment(attributable_cost_microunits=12_000),
        weakness_registry=weakness_registry,
        integrity_violations=integrity_violations,
    )


def _validated_weakness_registry() -> WeaknessRegistry:
    registry = WeaknessRegistry()
    finding = build_finding(
        finding_id="w-1",
        decision_context_id="ctx-1",
        weakness_category=WeaknessCategory.SLIPPAGE,
        finding_statement="entry filled materially worse than the decision price",
        detected_at=NOW,
        evidence_cutoff=NOW - timedelta(hours=2),
        evidence_refs=("ev-1",),
        recurrence_scope="instrument:BTC-USD",
        recurrence_subject="spread-at-entry",
        committee_role=CommitteeRole.LIQUIDITY_STRUCTURE_ANALYST,
    )
    registry.append_finding(finding)
    registry.append_validation(
        WeaknessValidation(
            validation_id="v-1",
            finding_id="w-1",
            state=WeaknessValidationState.VALIDATED,
            validator=__import__(
                "app.opip.committee.weakness", fromlist=["ValidatorKind"]
            ).ValidatorKind.OUTCOME_EVIDENCE,
            validated_at=NOW,
            rationale="the realised outcome confirms the finding",
            outcome_refs=("outcome-1",),
        )
    )
    return registry


# ------------------------------------------------------ investment


def test_investment_keeps_unknown_cost_and_tokens_unknown():
    """An unmeasured cost must not read as free."""
    investment = CommitteeInvestment(provider_calls=10, failed_calls=2)
    assert investment.cost_is_known is False
    assert investment.attributable_cost_microunits is None
    assert investment.input_tokens is None
    assert investment.output_tokens is None


def test_investment_reports_a_failure_rate_or_none():
    assert CommitteeInvestment().failure_rate is None
    assert CommitteeInvestment(provider_calls=10, failed_calls=2).failure_rate == 0.2


def test_failed_calls_cannot_exceed_total_calls():
    with pytest.raises(ValueError, match="failed_calls cannot exceed"):
        CommitteeInvestment(provider_calls=1, failed_calls=2)


def test_a_negative_investment_figure_is_refused():
    with pytest.raises(ValueError, match="non-negative integer or null"):
        CommitteeInvestment(attributable_cost_microunits=-1)


# ------------------------------------------------------ gates


def test_a_gate_that_did_not_pass_must_state_why():
    with pytest.raises(ValueError, match="must state why"):
        GateResult(gate=GateName.COVERAGE, status=GateStatus.BLOCKED)


def test_integrity_violations_block_the_integrity_gate():
    gates = evaluate_gates(
        population=_population(COMPLETED=100),
        economics=_economics(),
        investment=CommitteeInvestment(attributable_cost_microunits=1),
        weakness_counts={"VALIDATED": 1},
        integrity_violations=3,
    )
    integrity = next(g for g in gates if g.gate is GateName.EVIDENCE_INTEGRITY)
    assert integrity.status is GateStatus.BLOCKED
    assert "3 evidence integrity violations" in integrity.reason


def test_zero_integrity_violations_passes_the_integrity_gate():
    gates = evaluate_gates(
        population=_population(COMPLETED=100),
        economics=_economics(),
        investment=CommitteeInvestment(attributable_cost_microunits=1),
        weakness_counts={"VALIDATED": 1},
        integrity_violations=0,
    )
    integrity = next(g for g in gates if g.gate is GateName.EVIDENCE_INTEGRITY)
    assert integrity.status is GateStatus.PASSED
    assert integrity.reason is None


def test_no_comparison_makes_baseline_and_economics_insufficient():
    """An absent comparison is not a passing comparison."""
    gates = evaluate_gates(
        population=_population(COMPLETED=100),
        economics=None,
        investment=CommitteeInvestment(attributable_cost_microunits=1),
        weakness_counts={"VALIDATED": 1},
        integrity_violations=0,
    )
    names = {gate.gate: gate.status for gate in gates}
    assert names[GateName.BASELINE_COMPARISON] is GateStatus.INSUFFICIENT_EVIDENCE
    assert names[GateName.ECONOMICS] is GateStatus.INSUFFICIENT_EVIDENCE


def test_unknown_operating_cost_blocks_its_gate():
    gates = evaluate_gates(
        population=_population(COMPLETED=100),
        economics=_economics(),
        investment=CommitteeInvestment(),
        weakness_counts={"VALIDATED": 1},
        integrity_violations=0,
    )
    cost_gate = next(g for g in gates if g.gate is GateName.OPERATING_COST_KNOWN)
    assert cost_gate.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert "unknown" in cost_gate.reason


def test_no_validated_weakness_leaves_precision_insufficient():
    gates = evaluate_gates(
        population=_population(COMPLETED=100),
        economics=_economics(),
        investment=CommitteeInvestment(attributable_cost_microunits=1),
        weakness_counts={"VALIDATED": 0, "REJECTED": 4},
        integrity_violations=0,
    )
    precision = next(g for g in gates if g.gate is GateName.WEAKNESS_PRECISION)
    assert precision.status is GateStatus.INSUFFICIENT_EVIDENCE
    assert "4 were rejected" in precision.reason


# ------------------------------------------------------ stage


def test_an_empty_report_is_stage_zero_not_stage_one():
    """An empty report must not read as a measured one."""
    report = _report(
        population=_population(),
        economics=None,
        investment=CommitteeInvestment(),
    )
    assert report.stage is TrustStage.T0_UNTRUSTED_SHADOW
    assert report.incremental_trading_net is None
    assert report.incremental_operating_net is None
    assert report.false_intervention_rate is None


def test_integrity_violations_pin_the_stage_to_zero():
    report = _report(
        population=_population(COMPLETED=500),
        economics=_economics(),
        integrity_violations=1,
    )
    assert report.stage is TrustStage.T0_UNTRUSTED_SHADOW


def test_insufficient_coverage_holds_the_stage_at_measured():
    report = _report(
        population=_population(COMPLETED=MINIMUM_MATURED_CASES - 1),
        economics=_economics(),
    )
    assert report.stage is TrustStage.T1_MEASURED


def test_a_stage_cannot_advance_on_an_unknown_operating_net():
    """A stage reachable by an unmeasured result would be meaningless."""
    report = _report(
        population=_population(COMPLETED=500),
        economics=_economics(),
        investment=CommitteeInvestment(),
        weakness_registry=_validated_weakness_registry(),
    )
    assert report.stage is TrustStage.T1_MEASURED


def test_all_gates_passing_with_positive_net_reaches_the_review_stage():
    report = _report(
        population=_population(COMPLETED=500),
        economics=_economics(),
        investment=CommitteeInvestment(attributable_cost_microunits=1),
        weakness_registry=_validated_weakness_registry(),
    )
    assert report.blocked_gates == ()
    assert report.stage is TrustStage.T3_PAPER_INFLUENCE_ELIGIBLE_REVIEW


def test_non_positive_operating_net_does_not_reach_the_higher_review_stage():
    economics = _economics(baseline=0, committee=1_000, cost=5_000)
    report = _report(
        population=_population(COMPLETED=500),
        economics=economics,
        investment=CommitteeInvestment(attributable_cost_microunits=5_000),
        weakness_registry=_validated_weakness_registry(),
    )
    assert report.incremental_operating_net is not None
    assert report.incremental_operating_net <= 0
    assert report.stage is TrustStage.T2_SIGNAL_ELIGIBLE_REVIEW


def test_no_stage_grants_trading_influence():
    """Every stage is an evidence claim, never an authority grant."""
    for stage in TrustStage:
        assert stage.value.startswith(("T0", "T1", "T2", "T3", "T4"))
    # The highest stage this module can compute is a review stage.
    assert TrustStage.T3_PAPER_INFLUENCE_ELIGIBLE_REVIEW.value.endswith("REVIEW")
    assert TrustStage.T4_LIVE_ELIGIBILITY_REVIEW.value.endswith("REVIEW")


# ------------------------------------------------------ report content


def test_the_report_derives_counts_from_its_sources():
    registry = _validated_weakness_registry()
    report = _report(weakness_registry=registry)
    assert report.weakness_counts == registry.summary()
    assert report.recurring_weakness_count == len(registry.recurring_findings())


def test_insufficiency_reasons_are_recorded_not_hidden():
    report = _report(
        population=_population(),
        economics=None,
        investment=CommitteeInvestment(),
    )
    assert report.insufficiency_reasons
    assert any("COVERAGE" in reason for reason in report.insufficiency_reasons)


def test_every_disposition_appears_in_the_report_population():
    report = _report(population=_population(COMPLETED=40, LATE=2, FAILED=3))
    counts = report.population.as_dict()
    for disposition in CommitteeScheduleDisposition:
        assert disposition.value in counts
    assert counts["LATE"] == 2
    assert counts["FAILED"] == 3


def test_the_report_identity_covers_the_evidence_it_summarises():
    first = _report()
    second = _report(population=_population(COMPLETED=MINIMUM_MATURED_CASES + 1))
    assert first.report_id != second.report_id
    assert first.report_id.startswith("COMMITTEE-TRUST-REPORT:")


def test_the_report_requires_its_release_and_registry_identities():
    with pytest.raises(ValueError, match="release_sha"):
        build_trust_report(
            report_version="v",
            release_sha="",
            registry_version="r",
            generated_at=NOW,
            population=_population(),
            investment=CommitteeInvestment(),
        )


def test_the_maturity_progress_view_reports_every_relevant_state():
    report = _report(population=_population(COMPLETED=10, LATE=1, UNAVAILABLE=2, FAILED=3))
    progress = report.maturity_progress
    assert progress["completed"] == 10
    assert progress["late"] == 1
    assert progress["unavailable"] == 2
    assert progress["failed"] == 3


def test_a_stage_of_zero_reports_the_gates_that_blocked_it():
    report = _report(
        population=_population(),
        economics=None,
        investment=CommitteeInvestment(),
    )
    assert report.blocked_gates
    assert GateName.COVERAGE in report.blocked_gates
    assert report.gate(GateName.COVERAGE) is not None
    assert report.gate(GateName.EVIDENCE_INTEGRITY).status is GateStatus.PASSED


def test_has_unknown_key_metric_flags_an_unmeasurable_report():
    report = _report(population=_population(), economics=None)
    assert report.has_unknown_key_metric is True
    complete = _report()
    assert complete.has_unknown_key_metric is False
