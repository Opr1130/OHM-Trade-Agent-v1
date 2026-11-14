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
        ({"recommended_position_notional": None, "economic_position_notional": 400.0}, 400.0),
        ({"recommended_position_notional": 0, "economic_position_notional": 400.0}, None),
        ({"recommended_position_notional": -5.0, "economic_position_notional": 400.0}, None),
        ({"recommended_position_notional": float("nan"), "economic_position_notional": 400.0}, None),
        ({"recommended_position_notional": float("inf"), "economic_position_notional": 400.0}, None),
        ({"recommended_position_notional": True, "economic_position_notional": 400.0}, None),
        ({"recommended_position_notional": "250", "economic_position_notional": 400.0}, None),
        ({"recommended_position_notional": 250.0, "economic_position_notional": 0}, 250.0),
        ({"economic_position_notional": 0}, None),
        ({}, None),
    ],
)
def test_ac_001_c_basis_selection_fails_closed(candidate, expected):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-001: a present recommended size is the only basis; unusable means refuse, never a larger fallback."""
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


def _decide(candidate: dict, *, order: float | None, **over) -> str | None:
    """Admission execution check for an explicit order size, other evidence healthy unless overridden."""
    fields = dict(
        market_liquidity_usd=2_400_000.0,
        position_capacity_usd=candidate.get("liquidity_capacity_ceiling_usd"),
        order_size_usd=order,
        spread_bps=8.0,
        size_slippage_pct=0.25,
        depth_covered=True,
    )
    fields.update(over)
    return admission._check_execution(  # noqa: SLF001
        admission.ActionableEvidence(**fields), policy=admission.ActionableNotificationPolicy()
    )


def _swept_candidates(monkeypatch):
    for capital in (50.0, 100.0, 250.0, 400.0, 1_000.0, 2_500.0):
        for ceiling in (40.0, 99.99, 100.0, 250.0, 250.004, 400.0, 1_000.0, 5_000.0):
            for leverage in (1.0, 2.0, 3.0):
                try:
                    yield capital, ceiling, leverage, _gate(
                        monkeypatch, capital=capital, ceiling=ceiling, leverage=leverage
                    )
                except AssertionError:
                    continue  # the action gate refused this combination; no card, nothing to admit


def test_ac_004_a_uncapped_orders_are_judged_exactly_as_before(monkeypatch):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-004: when nothing was capped the new basis changes no decision."""
    seen = 0
    for _capital, _ceiling, _leverage, candidate in _swept_candidates(monkeypatch):
        if candidate["liquidity_capacity_capped"]:
            continue
        seen += 1
        old_order = candidate["economic_position_notional"]
        assert admission.contemplated_order_notional_usd(candidate) == pytest.approx(old_order, abs=0.01)
        assert _decide(candidate, order=admission.contemplated_order_notional_usd(candidate)) == _decide(
            candidate, order=old_order
        )
    assert seen >= 20, "the sweep must exercise uncapped candidates"


def test_ac_004_b_no_false_admission_across_the_capacity_grid(monkeypatch):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-004: admit exactly when the order fits the ceiling; never admit more than before's ceiling allows."""
    utilization = admission.ActionableNotificationPolicy().max_capacity_utilization_pct
    newly_admitted = capped = 0
    for capital, ceiling, leverage, candidate in _swept_candidates(monkeypatch):
        new_order = admission.contemplated_order_notional_usd(candidate)
        old_order = candidate["economic_position_notional"]
        new_reason, old_reason = _decide(candidate, order=new_order), _decide(candidate, order=old_order)
        allowed = ceiling * utilization / 100.0
        # An independent oracle: the order fits, or it is refused for capacity.
        assert (new_reason is None) == (new_order <= allowed), (capital, ceiling, leverage)
        if new_reason is None:
            assert new_order <= allowed + 1e-9, "never admits an order above the ceiling"
        if old_reason is None:
            assert new_reason is None, "no previously admitted candidate may become refused"
        if candidate["liquidity_capacity_capped"]:
            capped += 1
            if old_reason == admission.REASON_CAPACITY_INSUFFICIENT and new_reason is None:
                newly_admitted += 1
                assert new_order < old_order and new_order <= allowed
    assert capped >= 10 and newly_admitted >= 1, "the fix must change some decisions, and only those"


def test_ac_004_c_thin_books_and_missing_evidence_still_refuse_a_capped_order(monkeypatch):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-004: capacity is one gate of several and the others are untouched."""
    candidate = _gate(monkeypatch, capital=400.0, ceiling=250.0)
    order = admission.contemplated_order_notional_usd(candidate)
    assert _decide(candidate, order=order) is None
    assert _decide(candidate, order=order, market_liquidity_usd=1.0) == admission.REASON_LIQUIDITY_INSUFFICIENT
    assert _decide(candidate, order=order, market_liquidity_usd=None) == admission.REASON_EXECUTION_EVIDENCE_MISSING
    assert _decide(candidate, order=order, spread_bps=10_000.0) == admission.REASON_SPREAD_TOO_WIDE
    assert _decide(candidate, order=order, spread_bps=None) == admission.REASON_EXECUTION_EVIDENCE_MISSING
    assert _decide(candidate, order=order, size_slippage_pct=50.0) == admission.REASON_SIZE_SLIPPAGE_EXCESSIVE
    assert _decide(candidate, order=order, depth_covered=False) == admission.REASON_DEPTH_INSUFFICIENT
    assert _decide(candidate, order=order, depth_covered=None) == admission.REASON_DEPTH_INSUFFICIENT
    for ceiling in (None, float("nan"), float("inf"), "not-a-number"):
        assert _decide(candidate, order=order, position_capacity_usd=ceiling) == admission.REASON_EXECUTION_EVIDENCE_MISSING, ceiling
    # Pre-existing, unchanged: the shared number parser reads a bool as 1.0 and a numeric string as its
    # value. The producer only ever supplies floats; a bool still refuses a real order.
    assert _decide(candidate, order=order, position_capacity_usd=True) is not None
    for ceiling in (0.0, -10.0):
        assert _decide(candidate, order=order, position_capacity_usd=ceiling) == admission.REASON_CAPACITY_INSUFFICIENT
    for bad_order in (None, 0.0, -1.0, float("nan"), float("inf")):  # a bool never reaches here: the basis helper drops it
        assert _decide(candidate, order=bad_order) == admission.REASON_ORDER_SIZE_UNUSABLE, bad_order
    thin = _decide(candidate, order=order, position_capacity_usd=order - 0.01)
    assert thin == admission.REASON_CAPACITY_INSUFFICIENT, "a thinner book than the order refuses it"


@pytest.mark.parametrize("bad", [0, -5.0, float("nan"), float("inf"), True, "250"])
def test_ac_005_inconsistent_recommended_size_cannot_reach_telegram(monkeypatch, bad):
    """ATDD-OPIP-CAPACITY-ORDER-BASIS-V1/AC-005: scan basis -> send_trade_plan sends nothing for an inconsistent payload.

    The candidate has a healthy pre-cap economic size and ample capacity, but its recommended
    size is present and unusable. The real producer must suppress it with ORDER_SIZE_UNUSABLE and
    never call the transport; the same candidate with a valid recommended size is delivered.
    """
    from tests.test_opip_alert_actionable_only_regressions_v1 import _run_send_trade_plan

    def run(recommended):
        candidate = {"recommended_position_notional": recommended, "economic_position_notional": 750.0}
        evidence = {
            "venue": "KRAKEN", "venue_identity_valid": True, "market_liquidity_usd": 2_400_000.0,
            "position_capacity_usd": 5_000.0, "spread_bps": 8.0, "size_slippage_pct": 0.25,
            "depth_covered": True, "current_price": 100.0,
            # the exact expression scan_opportunities passes to execution_evidence_from_snapshot
            "order_size_usd": admission.contemplated_order_notional_usd(candidate),
        }
        return _run_send_trade_plan(monkeypatch, candidate={**candidate, "execution_evidence": evidence})

    refused = run(bad)
    assert refused["delivered"] is False
    assert "message" not in refused
    assert refused["suppression"]["reason"] == "ACTIONABLE_GATE:ORDER_SIZE_UNUSABLE"
    admitted = run(250.0)
    assert admitted["delivered"] is True and "message" in admitted
