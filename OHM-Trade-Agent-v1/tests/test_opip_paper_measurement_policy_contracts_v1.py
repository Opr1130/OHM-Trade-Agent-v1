from __future__ import annotations

from dataclasses import replace

import pytest

from app.opip.contracts.paper_metrics import (
    PAPER_METRIC_REGISTRY_VERSION,
    PAPER_METRICS,
    MetricUnit,
    UncertaintyRequirement,
    metric_definition,
)
from app.opip.contracts.paper_simulator_policy import (
    LEGACY_OHLC_DIAGNOSTIC_POLICY,
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


def test_legacy_ohlc_policy_is_bounded_diagnostic_only():
    policy = LEGACY_OHLC_DIAGNOSTIC_POLICY
    assert policy.fidelity is SimulationFidelity.LEVEL_0_OHLC_BOUNDED
    assert policy.passive_limit_fill_model is PassiveLimitFillModel.OHLC_TOUCH_BOUNDED
    assert policy.partial_fills is ModelingDisposition.NOT_MODELED


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
    with pytest.raises(ValueError, match="fixed_latency_ms"):
        replace(
            OPIP_PAPER_V2_TARGET_POLICY,
            latency_model=LatencyModel.FIXED_DETERMINISTIC,
            fixed_latency_ms=None,
        )

    policy = replace(
        OPIP_PAPER_V2_TARGET_POLICY,
        latency_model=LatencyModel.FIXED_DETERMINISTIC,
        fixed_latency_ms=250,
    )
    assert policy.fixed_latency_ms == 250


def test_trigger_can_never_be_redefined_as_exit():
    with pytest.raises(ValueError, match="trigger"):
        replace(OPIP_PAPER_V2_TARGET_POLICY, trigger_is_exit=True)


def test_cross_currency_equity_aggregation_is_not_authorized():
    with pytest.raises(ValueError, match="cross-currency"):
        replace(
            OPIP_PAPER_V2_TARGET_POLICY,
            allow_cross_currency_equity_aggregation=True,
        )
