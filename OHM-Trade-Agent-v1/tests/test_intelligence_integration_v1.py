from types import SimpleNamespace

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.pending_setup_monitor import monitor_pending_setup
from app.services.pending_setup_registry import PendingSetup
from app.services.portfolio_risk import evaluate_portfolio_risk
from app.services.trade_decision_intelligence import evaluate_trade_decision


def _plan(direction="LONG"):
    if direction == "SHORT":
        return EntryExitPlan(
            symbol="BTCUSD",
            valid_now=True,
            entry_style="rebound_or_retest",
            entry_low=99.0,
            entry_high=101.0,
            chase_limit=97.0,
            stop_price=103.0,
            target_1=95.0,
            target_2=92.0,
            reward_to_risk_1=2.0,
            reward_to_risk_2=3.0,
            risk_level="low",
            reason="test",
            direction="SHORT",
        )
    return EntryExitPlan(
        symbol="ETHUSD",
        valid_now=True,
        entry_style="pullback_or_retest",
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=103.0,
        stop_price=97.0,
        target_1=105.0,
        target_2=108.0,
        reward_to_risk_1=2.0,
        reward_to_risk_2=3.0,
        risk_level="low",
        reason="test",
        direction="LONG",
    )


def test_trade_decision_uses_profit_rank_and_guarded_calibration():
    candidate = {
        "direction": "LONG",
        "profit_rank_score": 85.0,
        "technical_score": 70.0,
        "confidence": 99,
        "economic_validation_net_t2": 200.0,
        "economic_target_2_move_pct": 5.0,
        "market_regime": "RISK_ON",
    }
    decision = evaluate_trade_decision(
        candidate=candidate,
        plan=_plan(),
        account_capital=10000.0,
        active_trades=[],
        outcomes=[],
    )
    assert decision.quality_score == 85.0
    assert decision.calibration_status == "INSUFFICIENT_DATA"
    assert decision.calibration_multiplier == 1.0
    assert decision.allowed
    assert 0 < decision.allocation.recommended_capital <= 2000


def test_portfolio_exposure_counts_proposed_leverage():
    decision = evaluate_portfolio_risk(
        active_trades=[],
        proposed_symbol="BTCUSD",
        proposed_direction="SHORT",
        proposed_capital=2000.0,
        proposed_leverage=3.0,
        account_capital=10000.0,
    )
    assert not decision.allowed
    assert decision.proposed_exposure == 6000.0
    assert decision.reason == "gross exposure limit exceeded"


def test_short_pending_setup_uses_directional_chase_logic(monkeypatch):
    setup = PendingSetup(
        symbol="BTCUSD",
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=97.0,
        stop_price=103.0,
        target_1=95.0,
        target_2=92.0,
        risk_level="low",
        confidence=80,
        direction="SHORT",
    )

    class Client:
        def get_ticker(self, symbol):
            return {"last": 98.0}

    monkeypatch.setattr(
        "app.services.pending_setup_monitor.KrakenClient",
        lambda: Client(),
    )
    result = monitor_pending_setup(setup)
    assert result.status == "NEAR_ENTRY"
    assert result.timing_score is not None


def test_short_pending_setup_invalidates_above_stop(monkeypatch):
    setup = PendingSetup(
        symbol="BTCUSD",
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=97.0,
        stop_price=103.0,
        target_1=95.0,
        target_2=92.0,
        risk_level="low",
        confidence=80,
        direction="SHORT",
    )

    class Client:
        def get_ticker(self, symbol):
            return {"last": 104.0}

    monkeypatch.setattr(
        "app.services.pending_setup_monitor.KrakenClient",
        lambda: Client(),
    )
    result = monitor_pending_setup(setup)
    assert result.status == "INVALIDATED"
