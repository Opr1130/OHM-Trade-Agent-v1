"""Advisory-only economic and AI move-scenario contract tests."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from app.jobs.scan_opportunities import _economic_quality
from app.services.chief_analyst import _normalize_chief_potential
from app.services.economic_quality_gate import (
    MIN_NET_PROFIT, PRODUCTION_MAX_CAPITAL_FRACTION, evaluate_economic_quality,
)
from app.core.strategy_policy import load_strategy_policy, parse_strategy_policy
from app.services.chief_alert_notifier import format_trade_plan
from app.services.entry_exit_advisor import EntryExitPlan


def plan():
    return EntryExitPlan(
        symbol="TESTUSD",valid_now=True,entry_style="ready",entry_low=100,
        entry_high=100,chase_limit=101,stop_price=98,target_1=103,target_2=106,
        reward_to_risk_1=1.5,reward_to_risk_2=3,risk_level="low",
        reason="test",direction="LONG",
    )


@pytest.mark.acceptance
def test_fixed_profit_floor_remains_in_execution_default_but_not_advisory_override():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-007: advisor is not execution policy."""
    execution_default = evaluate_economic_quality(
        plan(), available_capital=500,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
    )
    advisory_default = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
    )
    advisory_configured = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={
            "min_net_profit": 0,
            "min_target_2_move_pct": 4,
            "min_reward_to_risk": 2.5,
            "estimated_round_trip_cost_pct": 0.6,
        },
    )
    assert MIN_NET_PROFIT == 75.0
    assert execution_default.qualified is False
    assert advisory_default.qualified is True
    assert advisory_configured.qualified is True
    assert 0 < advisory_default.target_2_net_profit < 75


@pytest.mark.acceptance
def test_advisory_has_no_dollar_cutoff_while_execution_defaults_unchanged():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-001: advisory dollar veto absent."""
    advice = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
    )
    assert advice.qualified is True
    assert 0 < advice.target_2_net_profit < 75
    assert MIN_NET_PROFIT == 75.0


@pytest.mark.acceptance
def test_negative_after_costs_is_not_qualified_and_explicit_stricter_policy_is_preserved():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-002: fees and opt-in policy still matter."""
    shortfall = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={"estimated_round_trip_cost_pct": 7.0},
    )
    stricter = _economic_quality(
        plan(), SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={"min_net_profit": 75},
    )
    assert shortfall.qualified is False
    assert shortfall.target_2_net_profit < 0
    assert stricter.qualified is False
    assert "below minimum $75.00" in (stricter.rejection_reason or "")



@pytest.mark.acceptance
def test_optional_move_and_reward_risk_filters_default_off_but_can_be_enabled():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-003: observation is not a fixed-threshold veto."""
    smaller_move = replace(
        plan(), target_1=101, target_2=102,
        reward_to_risk_1=0.5, reward_to_risk_2=1.0,
    )
    default = _economic_quality(
        smaller_move, SimpleNamespace(trade_direction="LONG"), 500,
    )
    stricter = _economic_quality(
        smaller_move, SimpleNamespace(trade_direction="LONG"), 500,
        advisory_policy={"min_target_2_move_pct": 4, "min_reward_to_risk": 2.5},
    )
    assert default.qualified is True
    assert 0 < default.target_2_net_profit < 75
    assert stricter.qualified is False


@pytest.mark.acceptance
def test_versioned_reference_policy_disables_only_advisory_filters():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-004: optional filters stay owner-configurable."""
    policy = load_strategy_policy()
    assert policy.advisory_economics["min_net_profit_usd"] == 0
    assert policy.advisory_economics["min_target_2_move_pct"] == 0
    assert policy.advisory_economics["min_reward_to_risk"] == 0
    assert policy.advisory_economics["estimated_round_trip_cost_pct"] > 0
    assert policy.scoring["v2_threshold"] == 75
    assert policy.scoring["production"] == "v1"
    # Preserve the ability to explicitly select a bounded advisory filter.
    import json
    from pathlib import Path
    source = json.loads((Path(__file__).parents[1] / "app/core/strategy_policy.json").read_text())
    source["advisory_economics"]["min_reward_to_risk"] = 2.5
    source["advisory_economics"]["min_target_2_move_pct"] = 4
    strict = parse_strategy_policy(json.dumps(source))
    assert strict.advisory_economics["min_reward_to_risk"] == 2.5
    assert strict.advisory_economics["min_target_2_move_pct"] == 4


@pytest.mark.acceptance
def test_private_editor_supports_disabled_advisory_reward_risk_filter():
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-005: zero is editable, protected fields stay protected."""
    from pathlib import Path
    editor = (Path(__file__).parents[1] / "app/api/strategy_admin.html").read_text()
    assert 'id="rr" type="number" min="0"' in editor
    assert "Protected: model authority, position limits" in editor
    assert "strategy_admin" not in editor.lower() or "funded execution" in editor.lower()


@pytest.mark.acceptance
def test_invalid_ai_return_potential_cannot_become_trade_claim():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-007: AI potential is bounded evidence."""
    review = {"top_candidates": [
        {"potential_move_pct": 150, "potential_timeframe_hours": 8, "potential_rationale": "unsupported"},
        {"potential_move_pct": float("nan"), "potential_timeframe_hours": 24},
        {"potential_move_pct": 6.2, "potential_timeframe_hours": 48,
         "potential_rationale": "conditional momentum"},
    ]}
    _normalize_chief_potential(review)
    entries = review["top_candidates"]
    assert entries[0]["potential_move_pct"] is None
    assert entries[1]["potential_move_pct"] is None
    assert entries[2]["potential_move_pct"] == pytest.approx(6.2)
    assert entries[2]["potential_timeframe_hours"] == 48


@pytest.mark.acceptance
def test_conditional_ai_move_is_visible_but_not_claimed_as_guaranteed_return():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-007: advisory communicates uncertainty."""
    candidate = {
        "direction": "LONG", "symbol": "TESTUSD", "decision": "alert",
        "confidence": 90, "potential_move_pct": 5.5,
        "potential_timeframe_hours": 48,
        "potential_rationale": "4h trend, support retest and volatility",
    }
    text = format_trade_plan(candidate, plan(), "Regime neutral")
    assert "Conditional AI upside: 5.50% over ~48h" in text
    assert "not a trade target or guarantee" in text
    assert "AI scenario basis: 4h trend" in text
