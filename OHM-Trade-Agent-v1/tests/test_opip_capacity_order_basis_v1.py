"""ATDD-OPIP-CAPACITY-ORDER-BASIS-V1: the capacity test judges the order the card recommends."""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.services import actionable_admission as admission
from app.services import trade_action_gate
from app.services.capital_allocation import CapitalAllocation
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.portfolio_risk import PortfolioRiskDecision
from app.services.trade_decision_intelligence import TradeDecisionIntelligence

pytestmark = pytest.mark.acceptance

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _plan() -> EntryExitPlan:
    return EntryExitPlan(
        symbol="SOLUSD", valid_now=True, entry_style="enter_now", entry_low=99.5,
        entry_high=100.5, chase_limit=101.0, stop_price=97.0, target_1=108.0,
        target_2=112.0, reward_to_risk_1=2.6, reward_to_risk_2=3.6, risk_level="MEDIUM",
        reason="trend continuation", direction="LONG",
    )


def _gate(monkeypatch, *, capital: float, ceiling: float, leverage: float | None = None):
    """Run the real action gate, then return the candidate it leaves behind."""
    intelligence = TradeDecisionIntelligence(
        calibration_status="INSUFFICIENT_DATA",
        calibration_multiplier=1.0,
        projected_net_edge_pct=8.0,
        quality_score=90.0,
        allocation=CapitalAllocation(
            recommended_capital=capital, risk_dollars=20.0, position_pct=20.0, reason="base"
        ),
        portfolio_risk=PortfolioRiskDecision(
            allowed=True, reason="ok", open_positions=0, gross_exposure=0.0,
            proposed_exposure=capital, proposed_total_exposure=capital,
        ),
    )
    monkeypatch.setattr(trade_action_gate, "evaluate_trade_decision", lambda **kw: intelligence)
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_portfolio_risk",
        lambda **kw: PortfolioRiskDecision(
            allowed=True, reason="ok", open_positions=0, gross_exposure=0.0,
            proposed_exposure=kw["proposed_capital"],
            proposed_total_exposure=kw["proposed_capital"],
        ),
    )
    leverage = leverage or 1.0
    candidate = {
        "economic_qualified": True,
        "direction": "LONG",
        "margin_leverage": leverage,
        "liquidity_capacity_ceiling_usd": ceiling,
        "economic_validation_capital": capital,
        "economic_validation_net_t2": 160.0,
        # Set by the economic gate before capacity capping (scan_opportunities).
        "economic_position_notional": capital * leverage,
    }
    decision = trade_action_gate.apply_action_gate(
        candidate=candidate, plan=_plan(), account_capital=5_000.0, active_trades=[]
    )
    assert decision.allowed is True
    return candidate


def _capacity_reason(candidate: dict) -> str | None:
    evidence = admission.ActionableEvidence(
        market_liquidity_usd=2_400_000.0,
        position_capacity_usd=candidate["liquidity_capacity_ceiling_usd"],
        order_size_usd=admission.contemplated_order_notional_usd(candidate),
        spread_bps=8.0,
        size_slippage_pct=0.25,
        depth_covered=True,
    )
    return admission._check_execution(  # noqa: SLF001 - the capacity test under audit
        evidence, policy=admission.ActionableNotificationPolicy()
    )


def test_ac_001_a_capacity_capped_order_is_judged_at_its_capped_size(monkeypatch):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-001: a capped order that fits the ceiling is not refused."""
    candidate = _gate(monkeypatch, capital=400.0, ceiling=250.0)
    assert candidate["liquidity_capacity_capped"] is True
    assert candidate["recommended_position_notional"] == 250.0
    assert candidate["economic_position_notional"] == 400.0  # pre-cap, deliberately untouched
    assert admission.contemplated_order_notional_usd(candidate) == 250.0
    assert _capacity_reason(candidate) is None


def test_ac_001_b_limits_are_not_relaxed(monkeypatch):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-001: an order that still exceeds the ceiling is refused."""
    over = {"liquidity_capacity_ceiling_usd": 250.0, "recommended_position_notional": 250.01}
    assert _capacity_reason(over) == admission.REASON_CAPACITY_INSUFFICIENT
    legacy_over = {"liquidity_capacity_ceiling_usd": 250.0, "economic_position_notional": 400.0}
    assert _capacity_reason(legacy_over) == admission.REASON_CAPACITY_INSUFFICIENT
    unusable = {"liquidity_capacity_ceiling_usd": 250.0}
    assert _capacity_reason(unusable) == admission.REASON_ORDER_SIZE_UNUSABLE


@pytest.mark.parametrize(
    "candidate, expected",
    [
        ({"recommended_position_notional": 250.0, "economic_position_notional": 400.0}, 250.0),
        ({"economic_position_notional": 400.0}, 400.0),
        ({"recommended_position_notional": 0, "economic_position_notional": 400.0}, 400.0),
        ({"recommended_position_notional": float("nan"), "economic_position_notional": 400.0}, 400.0),
        ({"recommended_position_notional": True, "economic_position_notional": 400.0}, 400.0),
        ({"recommended_position_notional": "250", "economic_position_notional": 400.0}, 400.0),
        ({}, None),
    ],
)
def test_ac_001_c_basis_selection_fails_closed(candidate, expected):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-001: an unusable recommended size falls back, never invents one."""
    assert admission.contemplated_order_notional_usd(candidate) == expected


@pytest.mark.parametrize("leverage", [1.0, 1.5, 2.0, 3.0])
@pytest.mark.parametrize("ceiling", [250.0, 250.007, 250.004, 100.065, 187.333, 999.999])
def test_ac_002_capped_notional_never_exceeds_the_ceiling(monkeypatch, ceiling, leverage):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-002: cent rounding of a cap can never push the order over it."""
    candidate = _gate(monkeypatch, capital=2_000.0, ceiling=ceiling, leverage=leverage)
    assert candidate["liquidity_capacity_capped"] is True
    assert candidate["recommended_capital"] * leverage <= ceiling + 1e-9
    assert candidate["recommended_position_notional"] <= ceiling + 1e-9
    assert _capacity_reason(candidate) is None


def test_ac_003_scan_call_sites_use_the_contemplated_order_basis():
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-003: both scan call sites read the post-cap order size."""
    source = (APP_ROOT / "app" / "jobs" / "scan_opportunities.py").read_text(encoding="utf-8")
    assert 'order_notional_usd=alert.get("economic_position_notional")' not in source
    assert source.count("order_notional_usd=contemplated_order_notional_usd(alert)") == 2
