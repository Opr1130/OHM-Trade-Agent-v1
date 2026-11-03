"""Advisory-only economic and AI move-scenario contract tests."""
from types import SimpleNamespace

import pytest

from app.jobs.scan_opportunities import _economic_quality
from app.services.chief_analyst import _normalize_chief_potential
from app.services.economic_quality_gate import (
    MIN_NET_PROFIT, PRODUCTION_MAX_CAPITAL_FRACTION, evaluate_economic_quality,
)
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
    """ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1/AC-001: $75 cannot veto scanner advice."""
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
