from dataclasses import dataclass

from app.services.capital_rotation_intelligence import (
    IncumbentOpportunity,
    build_incumbent_opportunity,
    capital_utilization,
    evaluate_rotation,
)


@dataclass
class Trade:
    symbol: str
    capital: float | None
    entry_price: float = 100.0
    stop_price: float = 95.0
    target_1: float = 108.0
    target_2: float = 112.0
    direction: str = "LONG"


def test_capital_utilization_detects_effectively_full_account():
    usage = capital_utilization(
        account_capital=10_000,
        active_trades=[Trade("AAAUSD", 5_000), Trade("BBBUSD", 4_600)],
    )
    assert usage.utilization_pct == 96.0
    assert usage.fully_utilized is True


def test_rotation_gate_suppresses_non_exceptional_signal_when_full():
    trades = [Trade("AAAUSD", 9_600)]
    decision = evaluate_rotation(
        candidate_symbol="NEWUSD",
        candidate_confidence_score=91.0,
        candidate_remaining_upside_pct=20.0,
        candidate_downside_pct=3.0,
        account_capital=10_000,
        active_trades=trades,
        incumbents=[IncumbentOpportunity("AAAUSD", 9_600, 4.0, 3.0)],
    )
    assert decision.status == "FULL_CAPITAL_EXCEPTIONAL_GATE_NOT_MET"
    assert decision.exceptional_gate_passed is False
    assert decision.production_decision_changed is False
    assert decision.trade_authority_changed is False


def test_rotation_gate_requires_upside_proxy_when_full():
    trades = [Trade("AAAUSD", 9_600)]
    decision = evaluate_rotation(
        candidate_symbol="NEWUSD",
        candidate_confidence_score=94.0,
        candidate_remaining_upside_pct=10.0,
        candidate_downside_pct=2.0,
        account_capital=10_000,
        active_trades=trades,
        incumbents=[IncumbentOpportunity("AAAUSD", 9_600, 2.0, 3.0)],
    )
    assert decision.recommendation == "SUPPRESS_ROTATION_ALERT"


def test_exceptional_candidate_must_materially_beat_weakest_incumbent():
    trades = [Trade("AAAUSD", 4_800), Trade("BBBUSD", 4_800)]
    decision = evaluate_rotation(
        candidate_symbol="NEWUSD",
        candidate_confidence_score=94.0,
        candidate_remaining_upside_pct=14.0,
        candidate_downside_pct=3.0,
        account_capital=10_000,
        active_trades=trades,
        incumbents=[
            IncumbentOpportunity("AAAUSD", 4_800, 8.0, 3.0),
            IncumbentOpportunity("BBBUSD", 4_800, 5.0, 4.0),
        ],
    )
    assert decision.status == "HIGH_CONVICTION_ROTATION_CANDIDATE"
    assert decision.weakest_incumbent_symbol == "BBBUSD"
    assert decision.estimated_rotation_edge_pct == 10.0
    assert decision.recommendation == "REVIEW_ROTATION"
    assert decision.confidence_is_probability is False
    assert decision.shadow_only is True
    assert decision.production_decision_changed is False
    assert decision.trade_authority_changed is False


def test_small_rotation_advantage_prefers_holding():
    trades = [Trade("AAAUSD", 9_600)]
    decision = evaluate_rotation(
        candidate_symbol="NEWUSD",
        candidate_confidence_score=95.0,
        candidate_remaining_upside_pct=12.5,
        candidate_downside_pct=3.0,
        account_capital=10_000,
        active_trades=trades,
        incumbents=[IncumbentOpportunity("AAAUSD", 9_600, 10.0, 3.0)],
    )
    assert decision.status == "ROTATION_ADVANTAGE_TOO_SMALL"
    assert decision.recommendation == "HOLD_CURRENT_CAPITAL"


def test_capital_available_uses_normal_alert_policy():
    decision = evaluate_rotation(
        candidate_symbol="NEWUSD",
        candidate_confidence_score=80.0,
        candidate_remaining_upside_pct=8.0,
        candidate_downside_pct=2.0,
        account_capital=10_000,
        active_trades=[Trade("AAAUSD", 2_000)],
        incumbents=[IncumbentOpportunity("AAAUSD", 2_000, 5.0, 3.0)],
    )
    assert decision.status == "CAPITAL_AVAILABLE"
    assert decision.recommendation == "NORMAL_ALERT_POLICY"


def test_build_incumbent_opportunity_uses_current_price():
    incumbent = build_incumbent_opportunity(Trade("AAAUSD", 2_000), current_price=104.0)
    assert incumbent.remaining_upside_pct > 7.0
    assert incumbent.downside_to_stop_pct > 8.0
