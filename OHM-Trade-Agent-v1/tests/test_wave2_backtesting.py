from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.engine import ReplaySetup, replay_setup
from app.backtesting.models import Candle, ReplayTrade
from app.backtesting.walk_forward import WalkForwardWindow, evaluate_walk_forward
from app.services.drawdown_guard import DrawdownPolicy, evaluate_drawdown_guard


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(hour, low, high, close=100.0):
    return Candle(BASE + timedelta(hours=hour), close, high, low, close)


def _trade(day, pnl):
    at = BASE + timedelta(days=day)
    return ReplayTrade("BTCUSD", "LONG", at, at, 100, 101, 1000, 1, pnl, 0, pnl,
                       "WIN" if pnl > 0 else "LOSS", 1, "TIME")


def test_replay_uses_stop_when_stop_and_target_touch_same_bar():
    setup = ReplaySetup("BTCUSD", "LONG", BASE, 100, 95, 110, 1000, round_trip_cost_pct=0)
    trade = replay_setup(setup, [_candle(0, 94, 111)])
    assert trade is not None
    assert trade.exit_reason == "STOP"
    assert trade.net_pnl == -50.0


def test_replay_deducts_round_trip_costs():
    setup = ReplaySetup("BTCUSD", "LONG", BASE, 100, 95, 110, 1000, round_trip_cost_pct=1)
    trade = replay_setup(setup, [_candle(0, 100, 100), _candle(1, 100, 110, 110)])
    assert trade is not None
    assert trade.gross_pnl == 100.0
    assert trade.costs == 10.0
    assert trade.net_pnl == 90.0


def test_walk_forward_reports_only_out_of_sample_test_performance():
    trades = [_trade(1, 20), _trade(2, -10), _trade(5, 30), _trade(6, -5)]
    windows = [WalkForwardWindow(BASE, BASE + timedelta(days=4), BASE + timedelta(days=4), BASE + timedelta(days=8))]
    result = evaluate_walk_forward(trades, windows, 1000)
    assert result["window_count"] == 1
    assert result["profitable_test_windows"] == 1
    assert result["mean_test_expectancy"] == 12.5


def test_walk_forward_rejects_overlapping_test_windows():
    trades = [_trade(5, 10)]
    windows = [
        WalkForwardWindow(BASE, BASE + timedelta(days=2), BASE + timedelta(days=4), BASE + timedelta(days=7)),
        WalkForwardWindow(BASE, BASE + timedelta(days=3), BASE + timedelta(days=5), BASE + timedelta(days=8)),
    ]
    with pytest.raises(ValueError, match="overlap"):
        evaluate_walk_forward(trades, windows, 1000)


def test_drawdown_guard_reduces_then_halts_risk():
    policy = DrawdownPolicy(soft_limit_pct=5, hard_limit_pct=8, soft_size_multiplier=0.4)
    reduced = evaluate_drawdown_guard(current_equity=940, peak_equity=1000, policy=policy)
    halted = evaluate_drawdown_guard(current_equity=910, peak_equity=1000, policy=policy)
    assert reduced.state == "REDUCED_RISK"
    assert reduced.size_multiplier == 0.4
    assert reduced.allow_new_entries is True
    assert halted.state == "HALT"
    assert halted.size_multiplier == 0.0
    assert halted.allow_new_entries is False
