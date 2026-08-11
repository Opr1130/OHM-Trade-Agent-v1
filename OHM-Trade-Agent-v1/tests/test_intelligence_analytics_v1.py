from types import SimpleNamespace

from app.services.capital_allocation import recommend_capital
from app.services.entry_timing import evaluate_entry_timing
from app.services.historical_analytics import historical_summary
from app.services.portfolio_risk import evaluate_portfolio_risk
from app.services.self_calibration import calibration_model, calibrated_multiplier


def outcome(net, direction="LONG", regime="NEUTRAL", t2=False):
    return {"entered_trade": True, "terminal_status": "closed", "net_pnl": net, "direction": direction, "market_regime": regime, "target_1_observed": net > 0, "target_2_observed": t2, "mfe_pct": 3.0, "mae_pct": 1.0}


def test_historical_summary_uses_net_financial_outcomes():
    result = historical_summary([outcome(2), outcome(-1), outcome(3, "SHORT")])
    assert result["resolved_entered_trades"] == 3
    assert result["overall"]["net_pnl"] == 4
    assert result["overall"]["win_rate_pct"] == 66.67
    assert result["by_direction"]["SHORT"]["count"] == 1


def test_calibration_is_guarded_until_enough_history():
    assert calibration_model([outcome(1)] * 10)["status"] == "INSUFFICIENT_DATA"
    rows = [outcome(1, "LONG", "RISK_ON", True) for _ in range(20)] + [outcome(-1, "SHORT", "RISK_OFF") for _ in range(10)]
    model = calibration_model(rows)
    assert model["status"] == "CALIBRATED"
    assert 0.75 <= calibrated_multiplier(model, direction="LONG", regime="RISK_ON") <= 1.25


def test_entry_timing_blocks_chasing_and_waits_on_bad_microstructure():
    assert evaluate_entry_timing(direction="LONG", current_price=111, entry_low=99, entry_high=100, chase_limit=105).action == "DO_NOT_CHASE"
    decision = evaluate_entry_timing(direction="LONG", current_price=100, entry_low=99, entry_high=101, chase_limit=105, spread_bps=50, recent_trade_age_seconds=400)
    assert decision.action == "WAIT"


def test_capital_allocation_respects_position_cap_and_net_edge():
    assert recommend_capital(available_capital=10000, stop_distance_pct=2, confidence_score=90, net_edge_pct=-0.1).recommended_capital == 0
    allocation = recommend_capital(available_capital=10000, stop_distance_pct=2, confidence_score=90, net_edge_pct=2)
    assert 0 < allocation.recommended_capital <= 2000
    assert allocation.risk_dollars <= 75


def test_portfolio_risk_rejects_duplicate_capacity_direction_and_exposure():
    t = lambda s, d="LONG", c=1000: SimpleNamespace(symbol=s, direction=d, capital=c, margin_leverage=1, status="active")
    assert not evaluate_portfolio_risk(active_trades=[t("BTCUSD")], proposed_symbol="BTCUSD", proposed_direction="LONG", proposed_capital=500, account_capital=10000).allowed
    assert not evaluate_portfolio_risk(active_trades=[t("BTC"), t("ETH"), t("SOL")], proposed_symbol="XRP", proposed_direction="SHORT", proposed_capital=500, account_capital=10000).allowed
    assert not evaluate_portfolio_risk(active_trades=[t("BTC"), t("ETH")], proposed_symbol="XRP", proposed_direction="LONG", proposed_capital=500, account_capital=10000).allowed
    assert not evaluate_portfolio_risk(active_trades=[t("BTC", c=3000)], proposed_symbol="ETH", proposed_direction="SHORT", proposed_capital=2500, account_capital=10000).allowed
    assert evaluate_portfolio_risk(active_trades=[t("BTC", c=1000)], proposed_symbol="ETH", proposed_direction="SHORT", proposed_capital=500, account_capital=10000).allowed
