"""Phase 2A focused contract tests: economic floor removal, AI-stage
distinction, and risk-sizing forwarding.

These are behavioral tests for the Phase 2A corrections only. They do not
exercise network, funded execution, or learning paths.
"""
from types import SimpleNamespace

import pytest

from app.services import chief_analyst, trade_decision_intelligence
from app.services.economic_quality_gate import (
    MIN_NET_PROFIT,
    PRODUCTION_MAX_CAPITAL_FRACTION,
    evaluate_economic_quality,
)
from app.services.entry_exit_advisor import EntryExitPlan


def _plan(**overrides):
    base = dict(
        symbol="TESTUSD",
        valid_now=True,
        entry_style="ready",
        entry_low=100.0,
        entry_high=100.0,
        chase_limit=101.0,
        stop_price=98.0,
        target_1=104.0,
        target_2=108.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=4.0,
        risk_level="low",
        reason="test",
        direction="LONG",
    )
    base.update(overrides)
    return EntryExitPlan(**base)


def test_small_positive_net_is_not_rejected_below_seventy_five():
    """A $20-$50 net opportunity passes the shared economic default."""
    # 20% of $2,000 = $400 notional; 8% move = $32 gross, $2.40 costs -> ~$29.60 net.
    result = evaluate_economic_quality(
        _plan(), available_capital=2_000.0,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
    )
    assert MIN_NET_PROFIT == 0.0
    assert result.qualified is True
    assert 0 < result.target_2_net_profit < 75


def test_negative_net_after_costs_is_still_rejected():
    """Positive projected net after costs remains the one default requirement."""
    # A 0.5% round-trip cost on a 0.4% move nets negative.
    result = evaluate_economic_quality(
        _plan(target_1=100.2, target_2=100.4),
        available_capital=2_000.0,
        max_capital_fraction=PRODUCTION_MAX_CAPITAL_FRACTION,
        estimated_round_trip_cost_pct=0.6,
    )
    assert result.qualified is False
    assert result.target_2_net_profit < 0


def test_chief_not_configured_is_recorded_distinctly(monkeypatch):
    """No provider configured is a config state, not a provider outage."""
    from app.scanner.models import MarketSnapshot

    monkeypatch.setattr(
        chief_analyst, "_quality_by_risk_level",
        lambda candidate, equity, policy=None: ({"low": {"economic_qualified": True, "target_quality_qualified": True}}, True),
    )
    monkeypatch.setattr(
        chief_analyst, "plan_chief_route",
        lambda payload, default_model, openai_api_key: SimpleNamespace(
            route_tier="standard", route_reason="test", targets=(),
        ),
    )
    monkeypatch.setattr(chief_analyst, "trace_candidate_event", lambda **kwargs: {})
    snapshot = MarketSnapshot(
        symbol="TESTUSD",
        last_price=100,
        ema20=99,
        ema50=98,
        ema200=97,
        rsi=55,
        macd_line=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2,
        atr_pct=2,
        volume_ratio=1.2,
        technical_score=85,
        trend="bullish",
        trade_direction="LONG",
        underlying_asset="TEST",
        primary_pair="TESTUSD",
        primary_quote_currency="USD",
        ticker_last=100,
    )
    review = chief_analyst.review_candidates([snapshot], "model", None, 10_000)
    assert review["recommended_action"] == "no_trade"
    assert review["chief_failure_code"] == "CHIEF_NOT_CONFIGURED"
    assert review["opip_stage_evidence"]["invocation_status"] == "NOT_CONFIGURED"
    assert review["opip_stage_evidence"]["failure_type"] == "NO_AI_PROVIDER_CONFIGURED"


def test_ai_status_map_distinguishes_not_configured():
    """The funnel map keeps NOT_CONFIGURED separate from FAILED."""
    from app.opip.decision.funnel import AI_FAILED, AI_NOT_CONFIGURED
    from app.opip.decision.observer import _AI_STATUS_MAP

    assert _AI_STATUS_MAP["NOT_CONFIGURED"] == AI_NOT_CONFIGURED
    assert _AI_STATUS_MAP["FAILED"] == AI_FAILED
    assert AI_NOT_CONFIGURED != AI_FAILED


def test_trade_decision_forwards_configured_risk_sizing(monkeypatch):
    """Configured risk-per-trade and max-positions reach the allocator."""
    captured = {}

    def fake_recommend(**kwargs):
        captured["recommend"] = kwargs
        from app.services.capital_allocation import CapitalAllocation
        return CapitalAllocation(100.0, 2.0, 1.0, "ok")

    def fake_portfolio(**kwargs):
        captured["portfolio"] = kwargs
        from app.services.portfolio_risk import PortfolioRiskDecision
        return PortfolioRiskDecision(True, "ok", 0, 0.0, 0.0, 0.0)

    monkeypatch.setattr(
        trade_decision_intelligence, "recommend_capital", fake_recommend
    )
    monkeypatch.setattr(
        trade_decision_intelligence, "evaluate_portfolio_risk", fake_portfolio
    )
    candidate = {"direction": "LONG", "confidence": 80, "technical_score": 90}
    trade_decision_intelligence.evaluate_trade_decision(
        candidate=candidate,
        plan=_plan(),
        account_capital=10_000.0,
        active_trades=[],
        risk_per_trade_pct=0.35,
        max_positions=2,
    )
    assert captured["recommend"]["risk_per_trade_pct"] == 0.35
    assert captured["portfolio"]["max_positions"] == 2
