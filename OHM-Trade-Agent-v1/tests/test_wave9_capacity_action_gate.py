from app.services import trade_action_gate
from app.services.capital_allocation import CapitalAllocation
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.portfolio_risk import PortfolioRiskDecision
from app.services.trade_decision_intelligence import TradeDecisionIntelligence


def _plan():
    return EntryExitPlan(
        symbol="SOLUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=95.0,
        target_1=110.0,
        target_2=115.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="qualified",
        direction="LONG",
    )


def _intelligence(capital=400.0):
    return TradeDecisionIntelligence(
        calibration_status="INSUFFICIENT_DATA",
        calibration_multiplier=1.0,
        projected_net_edge_pct=8.0,
        quality_score=90.0,
        allocation=CapitalAllocation(
            recommended_capital=capital,
            risk_dollars=20.0,
            position_pct=20.0,
            reason="base",
        ),
        portfolio_risk=PortfolioRiskDecision(
            allowed=True,
            reason="portfolio risk limits satisfied",
            open_positions=0,
            gross_exposure=0.0,
            proposed_exposure=capital,
            proposed_total_exposure=capital,
        ),
    )


def test_capacity_ceiling_caps_recommended_capital(monkeypatch):
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_trade_decision",
        lambda **kwargs: _intelligence(400.0),
    )
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_portfolio_risk",
        lambda **kwargs: PortfolioRiskDecision(
            allowed=True,
            reason="portfolio risk limits satisfied",
            open_positions=0,
            gross_exposure=0.0,
            proposed_exposure=kwargs["proposed_capital"],
            proposed_total_exposure=kwargs["proposed_capital"],
        ),
    )
    candidate = {
        "economic_qualified": True,
        "direction": "LONG",
        "liquidity_capacity_ceiling_usd": 250.0,
        "economic_validation_capital": 400.0,
        "economic_validation_net_t2": 160.0,
    }

    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=_plan(),
        account_capital=2_000.0,
        active_trades=[],
    )

    assert decision.allowed is True
    assert decision.intelligence is not None
    assert decision.intelligence.allocation.recommended_capital == 250.0
    assert candidate["liquidity_capacity_capped"] is True
    assert candidate["capacity_adjusted_validation_net_t2"] == 100.0


def test_capacity_cap_rejects_when_economic_minimum_no_longer_holds(monkeypatch):
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_trade_decision",
        lambda **kwargs: _intelligence(400.0),
    )
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_portfolio_risk",
        lambda **kwargs: PortfolioRiskDecision(
            allowed=True,
            reason="portfolio risk limits satisfied",
            open_positions=0,
            gross_exposure=0.0,
            proposed_exposure=kwargs["proposed_capital"],
            proposed_total_exposure=kwargs["proposed_capital"],
        ),
    )
    candidate = {
        "economic_qualified": True,
        "direction": "LONG",
        "liquidity_capacity_ceiling_usd": 120.0,
        "economic_validation_capital": 400.0,
        "economic_validation_net_t2": 160.0,
    }

    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=_plan(),
        account_capital=2_000.0,
        active_trades=[],
    )

    assert decision.allowed is False
    assert candidate["recommended_capital"] == 120.0
    assert candidate["capacity_adjusted_validation_net_t2"] == 48.0
    assert "below economic minimum" in decision.reason


def test_capacity_above_allocation_does_not_change_recommendation(monkeypatch):
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_trade_decision",
        lambda **kwargs: _intelligence(300.0),
    )
    candidate = {
        "economic_qualified": True,
        "direction": "LONG",
        "liquidity_capacity_ceiling_usd": 1_000.0,
        "economic_validation_capital": 400.0,
        "economic_validation_net_t2": 160.0,
    }

    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=_plan(),
        account_capital=2_000.0,
        active_trades=[],
    )

    assert decision.allowed is True
    assert candidate["recommended_capital"] == 300.0
    assert candidate["liquidity_capacity_capped"] is False

def test_action_gate_rejects_missing_economic_qualification():
    candidate = {"direction": "LONG"}

    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=_plan(),
        account_capital=2_000.0,
        active_trades=[],
    )

    assert decision.allowed is False
    assert candidate["action_gate_allowed"] is False
    assert "economic qualification is required" in decision.reason


def test_post_cap_minimum_notional_rejects_without_validation_inputs(monkeypatch):
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_trade_decision",
        lambda **kwargs: _intelligence(400.0),
    )
    monkeypatch.setattr(
        trade_action_gate,
        "evaluate_portfolio_risk",
        lambda **kwargs: PortfolioRiskDecision(
            allowed=True,
            reason="portfolio risk limits satisfied",
            open_positions=0,
            gross_exposure=0.0,
            proposed_exposure=kwargs["proposed_capital"],
            proposed_total_exposure=kwargs["proposed_capital"],
        ),
    )
    candidate = {
        "economic_qualified": True,
        "direction": "LONG",
        "liquidity_capacity_ceiling_usd": 50.0,
    }

    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=_plan(),
        account_capital=2_000.0,
        active_trades=[],
    )

    assert decision.allowed is False
    assert candidate["recommended_capital"] == 50.0
    assert candidate["recommended_position_notional"] == 50.0
    assert "minimum executable notional" in decision.reason

def test_action_gate_rejects_invalid_account_capital():
    candidate = {
        "economic_qualified": True,
        "direction": "LONG",
    }

    decision = trade_action_gate.apply_action_gate(
        candidate=candidate,
        plan=_plan(),
        account_capital=0.0,
        active_trades=[],
    )

    assert decision.allowed is False
    assert candidate["action_gate_allowed"] is False
    assert "account capital" in decision.reason
