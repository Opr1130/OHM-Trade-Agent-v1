"""Matched portfolio economics and incremental value (IC-021 to IC-028).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the disciplines that make an incremental claim honest: arms must
be matched before they are subtracted, no population may be silently excluded, an
unknown cost is never free, and a counterfactual is simulated rather than realised.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.committee.economics import (
    SIMULATED_COUNTERFACTUAL,
    ArmOutcome,
    CounterfactualClaim,
    EconomicsError,
    FourWayComparison,
    MatchedConditions,
    PopulationKind,
    PortfolioArm,
    assert_no_survivorship,
    build_incremental_economics,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=30)
POPULATIONS = {kind: 0 for kind in PopulationKind}


def _conditions(**overrides) -> MatchedConditions:
    values = {
        "population_id": "panel-1",
        "capital_microunits": 1_000_000,
        "window_start": WINDOW_START,
        "window_end": NOW,
        "execution_model": "paper-v2",
        "fee_model": "taker-0.26pct",
        "slippage_model": "spread-half",
        "liquidity_assumption": "top-of-book",
        "coverage": "full",
        "capital_occupancy_semantics": "exclusive",
    }
    values.update(overrides)
    return MatchedConditions(**values)


def _population(**overrides) -> dict:
    values = {kind: 0 for kind in PopulationKind}
    values[PopulationKind.FILLED] = 10
    values.update(overrides)
    return values


def _arm(
    arm: PortfolioArm,
    net: int | None,
    *,
    conditions: MatchedConditions | None = None,
    **population_overrides,
) -> ArmOutcome:
    return ArmOutcome(
        arm=arm,
        conditions_hash=(conditions or _conditions()).conditions_hash,
        net_microunits=net,
        population=_population(**population_overrides),
        net_completeness="COMPLETE" if net is not None else "UNKNOWN",
    )


# ------------------------------------------------- matching preconditions


def test_arms_may_only_be_compared_under_identical_conditions():
    first = _conditions()
    second = _conditions(slippage_model="zero")
    with pytest.raises(EconomicsError, match="identical conditions"):
        first.require_match(second)


def test_the_mismatch_names_the_differing_fields():
    first = _conditions()
    second = _conditions(fee_model="maker-0.16pct", coverage="partial")
    with pytest.raises(EconomicsError) as error:
        first.require_match(second)
    assert "fee_model" in str(error.value)
    assert "coverage" in str(error.value)


def test_identical_conditions_match():
    _conditions().require_match(_conditions())


def test_an_arm_that_did_not_run_under_the_stated_conditions_is_refused():
    conditions = _conditions()
    other = _conditions(population_id="panel-2")
    baseline = _arm(PortfolioArm.DETERMINISTIC_BASELINE, -1_000)
    committee = _arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, -500, conditions=other)
    with pytest.raises(EconomicsError, match="did not run under the stated conditions"):
        build_incremental_economics(
            matched_conditions=conditions,
            baseline=baseline,
            committee=committee,
            operating_cost_microunits=100,
            four_way=FourWayComparison(1, 1, 1, 1),
        )


def test_a_zero_length_window_is_refused():
    with pytest.raises(EconomicsError, match="window_end must be after"):
        _conditions(window_end=WINDOW_START)


# ------------------------------------------------- population accounting


def test_an_arm_must_account_for_every_population():
    """An omitted population is an unreported exclusion."""
    partial = {PopulationKind.FILLED: 3}
    conditions_hash = _conditions().conditions_hash
    with pytest.raises(EconomicsError, match="does not account for every population"):
        ArmOutcome(
            arm=PortfolioArm.DETERMINISTIC_BASELINE,
            conditions_hash=conditions_hash,
            net_microunits=0,
            population=partial,
            net_completeness="COMPLETE",
        )


def test_the_seven_populations_are_exactly_as_declared():
    assert {kind.value for kind in PopulationKind} == {
        "FILLED",
        "NO_FILL",
        "BASELINE_REJECT",
        "FAILED",
        "LATE",
        "MISSING_COMMITTEE_CALL",
        "RETRY",
    }


def test_a_negative_population_count_is_refused():
    with pytest.raises(EconomicsError, match="non-negative integer"):
        _arm(
            PortfolioArm.DETERMINISTIC_BASELINE,
            0,
            **{PopulationKind.NO_FILL: -1},
        )


def test_a_declared_exclusion_is_permitted_but_an_undeclared_one_is_not():
    arm = _arm(PortfolioArm.DETERMINISTIC_BASELINE, 0, **{PopulationKind.NO_FILL: 4})
    # Declared exclusions are fine.
    assert_no_survivorship(arm=arm, excluded_kinds=[PopulationKind.NO_FILL])
    # An arm that omits a population cannot exist, so the guard's job is to make
    # the requirement explicit at the call site.
    assert_no_survivorship(arm=arm, excluded_kinds=[])


def test_dropping_failures_cannot_hide_behind_a_successful_subset():
    """The population counts keep a successful-calls-only arm from existing."""
    honest = _arm(
        PortfolioArm.COMMITTEE_RESEARCH_POLICY,
        5_000,
        **{PopulationKind.FAILED: 7, PopulationKind.NO_FILL: 3},
    )
    assert honest.count(PopulationKind.FAILED) == 7
    # The total is the whole population, not just the fills.
    assert honest.total_population == 10 + 7 + 3
    assert honest.filled == 10


# ------------------------------------------------- incremental metrics


def test_incremental_trading_net_is_the_difference_from_the_baseline():
    economics = build_incremental_economics(
        matched_conditions=_conditions(),
        baseline=_arm(PortfolioArm.DETERMINISTIC_BASELINE, -40_000),
        committee=_arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, -25_000),
        operating_cost_microunits=5_000,
        four_way=FourWayComparison(4, 1, 5, 2),
    )
    assert economics.incremental_trading_net == 15_000


def test_incremental_operating_net_subtracts_attributable_cost():
    economics = build_incremental_economics(
        matched_conditions=_conditions(),
        baseline=_arm(PortfolioArm.DETERMINISTIC_BASELINE, -40_000),
        committee=_arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, -25_000),
        operating_cost_microunits=20_000,
        four_way=FourWayComparison(4, 1, 5, 2),
    )
    assert economics.incremental_trading_net == 15_000
    assert economics.incremental_operating_net == -5_000


def test_an_unknown_operating_cost_makes_the_operating_net_unknown():
    """An unknown cost is not free, and a partial total is not presented as complete."""
    economics = build_incremental_economics(
        matched_conditions=_conditions(),
        baseline=_arm(PortfolioArm.DETERMINISTIC_BASELINE, -40_000),
        committee=_arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, -25_000),
        operating_cost_microunits=None,
        four_way=FourWayComparison(4, 1, 5, 2),
    )
    assert economics.incremental_trading_net == 15_000
    assert economics.incremental_operating_net is None
    assert economics.has_negative_operating_value is None


def test_an_unknown_baseline_makes_the_incremental_net_unknown():
    """Comparing against an unknown baseline would fabricate the comparison."""
    economics = build_incremental_economics(
        matched_conditions=_conditions(),
        baseline=_arm(PortfolioArm.DETERMINISTIC_BASELINE, None),
        committee=_arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, -25_000),
        operating_cost_microunits=1_000,
        four_way=FourWayComparison(4, 1, 5, 2),
    )
    assert economics.incremental_trading_net is None
    assert economics.incremental_operating_net is None


def test_an_unmeasured_arm_net_is_null_and_never_zero():
    arm = _arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, None)
    assert arm.net_microunits is None
    assert arm.is_complete is False
    assert arm.net_completeness == "UNKNOWN"


def test_positive_gross_value_with_negative_operating_value_is_visible():
    """High accuracy with harmful economics must not read as a win."""
    economics = build_incremental_economics(
        matched_conditions=_conditions(),
        baseline=_arm(PortfolioArm.DETERMINISTIC_BASELINE, 0),
        committee=_arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, 10_000),
        operating_cost_microunits=25_000,
        four_way=FourWayComparison(6, 0, 4, 0),
    )
    assert economics.incremental_trading_net == 10_000
    assert economics.incremental_operating_net == -15_000
    assert economics.has_negative_operating_value is True


def test_the_cash_comparator_is_carried_when_supplied():
    economics = build_incremental_economics(
        matched_conditions=_conditions(),
        baseline=_arm(PortfolioArm.DETERMINISTIC_BASELINE, -40_000),
        committee=_arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, -25_000),
        operating_cost_microunits=1_000,
        four_way=FourWayComparison(4, 1, 5, 2),
        cash_no_trade=_arm(PortfolioArm.CASH_NO_TRADE, 0),
    )
    assert economics.cash_no_trade_net_microunits == 0


def test_a_mislabelled_arm_is_refused():
    conditions = _conditions()
    baseline = _arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, 0)
    committee = _arm(PortfolioArm.COMMITTEE_RESEARCH_POLICY, 0)
    with pytest.raises(EconomicsError, match="must be DETERMINISTIC_BASELINE"):
        build_incremental_economics(
            matched_conditions=conditions,
            baseline=baseline,
            committee=committee,
            operating_cost_microunits=0,
            four_way=FourWayComparison(0, 0, 0, 0),
        )


# ------------------------------------------------- four-way comparison


def test_the_four_way_comparison_counts_every_outcome():
    four_way = FourWayComparison(
        committee_right_baseline_wrong=5,
        baseline_right_committee_wrong=2,
        both_right=10,
        both_wrong=3,
    )
    assert four_way.total_scored == 20
    assert four_way.false_intervention == 2
    assert four_way.false_intervention_rate == pytest.approx(0.1)


def test_an_undefined_false_intervention_rate_is_none_not_zero():
    four_way = FourWayComparison(0, 0, 0, 0)
    assert four_way.total_scored == 0
    assert four_way.false_intervention_rate is None


def test_false_intervention_is_kept_separate_from_the_net():
    """A net-positive total can still hide a harmful intervention pattern."""
    four_way = FourWayComparison(9, 4, 1, 0)
    assert four_way.false_intervention == 4
    assert four_way.false_intervention_rate == pytest.approx(4 / 14)


# ------------------------------------------------- counterfactuals


def test_a_counterfactual_is_labelled_simulated_not_realised_cash():
    claim = CounterfactualClaim(
        claim_kind="AVOIDED_LOSS",
        policy_ref="policy-v1",
        amount_microunits=12_000,
        feasible_timing=True,
    )
    assert claim.disposition == SIMULATED_COUNTERFACTUAL
    assert claim.disposition == "SIMULATED_NOT_REALISED_CASH"


def test_a_counterfactual_requires_feasible_timing():
    """An opinion that arrived after the opportunity expired cannot avoid it."""
    with pytest.raises(EconomicsError, match="feasible timing"):
        CounterfactualClaim(
            claim_kind="AVOIDED_LOSS",
            policy_ref="policy-v1",
            amount_microunits=12_000,
            feasible_timing=False,
        )


def test_a_counterfactual_cannot_be_relabelled_as_realised():
    with pytest.raises(EconomicsError, match="must be labelled simulated"):
        CounterfactualClaim(
            claim_kind="AVOIDED_LOSS",
            policy_ref="policy-v1",
            amount_microunits=1,
            feasible_timing=True,
            disposition="REALISED_CASH",
        )


def test_a_counterfactual_requires_a_preregistered_policy():
    with pytest.raises(EconomicsError, match="policy_ref"):
        CounterfactualClaim(
            claim_kind="AVOIDED_LOSS",
            policy_ref="  ",
            amount_microunits=1,
            feasible_timing=True,
        )
