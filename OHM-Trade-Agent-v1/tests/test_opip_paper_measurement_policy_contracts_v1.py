from __future__ import annotations

from dataclasses import replace
import re

import pytest

from app.opip.contracts.paper_execution_events import (
    PAPER_FILL_RECORDED,
    event_contract,
)
from app.opip.contracts.paper_metrics import (
    PAPER_METRIC_REGISTRY_VERSION,
    PAPER_METRICS,
    MetricUnit,
    UncertaintyRequirement,
    metric_definition,
)
from app.opip.contracts.paper_simulator_policy import (
    OPIP_PAPER_V2_TARGET_POLICY,
    LatencyModel,
    ModelingDisposition,
    PassiveLimitFillModel,
    SimulationFidelity,
)


def test_registry_ids_are_unique_and_definition_version_is_frozen():
    assert PAPER_METRICS
    assert len(PAPER_METRICS) == len(set(PAPER_METRICS))
    assert all(
        key == metric.metric_id
        for key, metric in PAPER_METRICS.items()
    )
    assert all(
        metric.definition_version == PAPER_METRIC_REGISTRY_VERSION
        for metric in PAPER_METRICS.values()
    )


def test_realized_net_pnl_excludes_counterfactual_and_hindsight():
    metric = metric_definition("paper.realized_net_pnl")
    assert metric.unit is MetricUnit.QUOTE_CURRENCY
    assert "COUNTERFACTUAL_POLICY" in metric.exclusions
    assert "HINDSIGHT_DIAGNOSTIC" in metric.exclusions
    assert "FINAL_VERIFIED" in metric.eligible_population


def test_capture_efficiency_is_not_mfe_based():
    metric = metric_definition("paper.capture_efficiency")
    assert "counterfactual" in metric.formula.lower()
    assert "MFE" not in metric.formula
    assert "HINDSIGHT_DIAGNOSTIC" in metric.exclusions


def test_drawdown_is_quote_currency_scoped():
    metric = metric_definition("paper.realized_equity_drawdown_pct")
    assert metric.unit is MetricUnit.PERCENT
    assert "one quote_currency portfolio" in metric.eligible_population
    assert "mixed quote_currency" in metric.exclusions


def test_expectancy_requires_uncertainty_interval():
    metric = metric_definition("paper.net_expectancy")
    assert metric.uncertainty_requirement is UncertaintyRequirement.SHOW_INTERVAL


def test_unknown_metric_fails_closed():
    with pytest.raises(KeyError, match="unknown paper metric"):
        metric_definition("paper.magic_profit_score")


def test_level1_policy_requires_quote_spread_fee_and_slippage_semantics():
    policy = OPIP_PAPER_V2_TARGET_POLICY
    assert policy.fidelity is SimulationFidelity.LEVEL_1_QUOTE_BACKED
    assert policy.quote_freshness is ModelingDisposition.REQUIRED
    assert policy.bid_ask_spread is ModelingDisposition.REQUIRED
    assert policy.fees is ModelingDisposition.REQUIRED
    assert policy.slippage is ModelingDisposition.REQUIRED


def test_v2_policy_does_not_invent_unfounded_execution_precision():
    policy = OPIP_PAPER_V2_TARGET_POLICY
    assert policy.latency_model is LatencyModel.NOT_MODELED
    assert policy.fixed_latency_ms is None
    assert policy.market_impact is ModelingDisposition.NOT_MODELED
    assert policy.queue_position is ModelingDisposition.NOT_MODELED
    assert policy.partial_fills is ModelingDisposition.OPTIONAL_WHEN_EVIDENCE_EXISTS


def test_v2_policy_uses_quote_cross_confirmation_for_passive_limits():
    assert (
        OPIP_PAPER_V2_TARGET_POLICY.passive_limit_fill_model
        is PassiveLimitFillModel.QUOTE_CROSS_CONFIRMATION
    )



def test_market_impact_cannot_be_enabled_without_new_contract():
    with pytest.raises(ValueError, match="market impact"):
        replace(
            OPIP_PAPER_V2_TARGET_POLICY,
            market_impact=ModelingDisposition.REQUIRED,
        )


def test_queue_position_cannot_be_enabled_without_new_contract():
    with pytest.raises(ValueError, match="queue position"):
        replace(
            OPIP_PAPER_V2_TARGET_POLICY,
            queue_position=ModelingDisposition.REQUIRED,
        )


def test_fixed_latency_requires_explicit_versioned_numeric_assumption():
    """Numeric latency cannot be enabled while the policy version stays frozen.

    NOTE: this fixture previously asserted the opposite - that a caller could set
    FIXED_DETERMINISTIC with `fixed_latency_ms=250` while `policy_version` stayed
    `opip-paper-sim-policy-v1`. That is an unversioned model change, which is the
    defect the freeze removes, so the assertion is inverted here. The fixture's
    original invariant (a numeric latency assumption requires an explicit,
    approved version) is preserved and now enforced rather than assumed.
    """
    with pytest.raises(ValueError, match="latency"):
        replace(
            OPIP_PAPER_V2_TARGET_POLICY,
            latency_model=LatencyModel.FIXED_DETERMINISTIC,
            fixed_latency_ms=None,
        )

    with pytest.raises(ValueError, match="latency"):
        replace(
            OPIP_PAPER_V2_TARGET_POLICY,
            latency_model=LatencyModel.FIXED_DETERMINISTIC,
            fixed_latency_ms=250,
        )

    # A numeric value cannot ride along with the frozen NOT_MODELED model either.
    with pytest.raises(ValueError, match="fixed_latency_ms"):
        replace(OPIP_PAPER_V2_TARGET_POLICY, fixed_latency_ms=250)

    # The frozen target policy itself stays uncalibrated.
    assert OPIP_PAPER_V2_TARGET_POLICY.latency_model is LatencyModel.NOT_MODELED
    assert OPIP_PAPER_V2_TARGET_POLICY.fixed_latency_ms is None


def test_latency_model_enum_member_survives_for_a_future_approved_version():
    """Fail-closed is version-scoped, not a removal of the capability."""
    assert LatencyModel.FIXED_DETERMINISTIC.value == "FIXED_DETERMINISTIC"
    assert LatencyModel.NOT_MODELED.value == "NOT_MODELED"


# ---------------------------------------------------------------------------
# Greptile P1-4: one canonical execution-cost field name
# ---------------------------------------------------------------------------


def test_execution_cost_formula_names_only_canonical_fill_cost_fields():
    """The metric formula and the canonical fill contract must agree exactly.

    Freezes the relationship rather than the spelling alone: every cost term named
    by `paper.execution_cost` must be a real fill-contract field. This is what
    prevents a near-miss name such as `other_supported_execution_cost` (which no
    canonical fill can carry) from being reintroduced.
    """
    metric = metric_definition("paper.execution_cost")
    fill_fields = event_contract(PAPER_FILL_RECORDED).required_fields
    canonical_cost_fields = {
        "fee_cost",
        "spread_cost",
        "slippage_cost",
        "other_supported_cost",
    }

    formula_terms = set(re.findall(r"[a-z_]+_cost", metric.formula))
    assert formula_terms == canonical_cost_fields
    # Every term the formula sums is actually recordable on a canonical fill.
    assert formula_terms <= fill_fields
    # The specific regression: the stale name must never come back.
    assert "other_supported_execution_cost" not in metric.formula


# ---------------------------------------------------------------------------
# Greptile P1-7: bounded temporal evidence must not lose its uncertainty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metric_id",
    ["paper.entry_latency", "paper.exit_latency"],
)
def test_bounded_latency_metrics_require_interval_presentation(metric_id):
    """Latency may derive from bounded evidence, so it must show an interval.

    Under `NONE` a consumer could present a bounded temporal window as an invented
    point measurement, which the temporal contract explicitly forbids.
    """
    metric = metric_definition(metric_id)
    assert metric.unit is MetricUnit.SECONDS
    assert metric.uncertainty_requirement is UncertaintyRequirement.SHOW_INTERVAL
    assert metric.uncertainty_requirement is not UncertaintyRequirement.NONE


def test_no_latency_metric_may_degrade_to_point_uncertainty():
    """Registry-wide guard so a future latency metric cannot silently opt out."""
    latency_metrics = {
        metric_id: metric
        for metric_id, metric in PAPER_METRICS.items()
        if "latency" in metric_id
    }
    assert latency_metrics
    assert all(
        metric.uncertainty_requirement is UncertaintyRequirement.SHOW_INTERVAL
        for metric in latency_metrics.values()
    )


def test_trigger_can_never_be_redefined_as_exit():
    with pytest.raises(ValueError, match="trigger"):
        replace(OPIP_PAPER_V2_TARGET_POLICY, trigger_is_exit=True)


def test_cross_currency_equity_aggregation_is_not_authorized():
    with pytest.raises(ValueError, match="cross-currency"):
        replace(
            OPIP_PAPER_V2_TARGET_POLICY,
            allow_cross_currency_equity_aggregation=True,
        )
