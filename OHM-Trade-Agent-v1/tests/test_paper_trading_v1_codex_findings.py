from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.paper_trade_control import set_paper_trade_enabled
from app.services.paper_trade_engine import (
    PaperTradeConfig,
    enroll_paper_opportunity,
)
from app.services.paper_trade_monitor import run_paper_trade_monitor
from app.services.paper_trade_registry import account_summary, get_lifecycles


NOW = datetime(2026, 8, 27, 5, 7, tzinfo=timezone.utc)


def _config(*, interval=15, capital=1000.0, starting=10000.0):
    return PaperTradeConfig(
        starting_equity=starting,
        capital_per_trade=capital,
        max_positions=3,
        fee_rate=0.004,
        slippage_bps=10.0,
        tp1_fraction=0.5,
        pending_ttl_hours=24,
        max_hold_hours=24,
        candle_interval_minutes=interval,
    )


def _plan(*, pending=True):
    return EntryExitPlan(
        symbol="SOLUSD",
        valid_now=not pending,
        entry_style="wait_for_pullback" if pending else "pullback_or_retest",
        entry_low=99.0,
        entry_high=100.0,
        chase_limit=102.0,
        stop_price=95.0,
        target_1=105.0,
        target_2=110.0,
        reward_to_risk_1=1.5,
        reward_to_risk_2=2.5,
        risk_level="low",
        reason="test",
        direction="LONG",
    )


def _snapshot():
    return SimpleNamespace(
        symbol="SOLUSD",
        underlying_asset="SOL",
        trade_direction="LONG",
        ticker_last=100.0,
        ticker_ask=100.0,
        last_price=99.0,
    )


def _candidate():
    return {
        "direction": "LONG",
        "economic_qualified": True,
        "underlying_asset": "SOL",
        "confidence": 90,
        "profit_rank": 1,
        "profit_rank_score": 80.0,
    }


def _enroll(tmp_path, *, config, pending=True):
    return enroll_paper_opportunity(
        candidate=_candidate(),
        snapshot=_snapshot(),
        plan=_plan(pending=pending),
        episode_id="EP:codex",
        cohort_id="COHORT:codex",
        decision_at=NOW,
        config=config,
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )


def test_pending_lifecycle_reserves_projected_entry_fee(tmp_path):
    result = _enroll(tmp_path, config=_config(), pending=True)
    assert result.status == "PENDING"
    summary = account_summary(10_000.0, state_file=tmp_path / "state.json")
    assert summary.reserved_capital == pytest.approx(1004.0)
    assert summary.available_capital == pytest.approx(8996.0)


def test_enrollment_refuses_cash_that_covers_notional_but_not_entry_fee(tmp_path):
    result = _enroll(
        tmp_path,
        config=_config(starting=1000.0, capital=1000.0),
        pending=True,
    )
    assert result.status == "CAPITAL"
    assert get_lifecycles(state_file=tmp_path / "state.json") == []


class RecordingClient:
    def __init__(self):
        self.intervals = []

    def get_ohlc(self, symbol, interval=15, since=None):
        self.intervals.append(interval)
        return []

    def get_ticker(self, symbol):
        return {"bid": 100.0, "last": 100.0}


def test_monitor_uses_enrolled_interval_even_if_runtime_config_changes(tmp_path):
    state = tmp_path / "state.json"
    control = tmp_path / "control.json"
    enrolled = _enroll(tmp_path, config=_config(interval=15), pending=False)
    assert enrolled.status == "OPENED"

    trade = get_lifecycles(state_file=state)[0]
    assert trade.candle_interval_minutes == 15

    set_paper_trade_enabled(True, path=control, now=NOW)
    client = RecordingClient()
    run_paper_trade_monitor(
        _config(interval=60),
        client=client,
        now=datetime(2026, 8, 27, 5, 45, tzinfo=timezone.utc),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        control_file=control,
    )
    assert client.intervals == [15]
