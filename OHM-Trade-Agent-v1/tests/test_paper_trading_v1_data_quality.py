from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.paper_trade_engine import (
    PaperTradeConfig,
    enroll_paper_opportunity,
)
from app.services.paper_trade_monitor import run_paper_trade_monitor
from app.services.paper_trade_registry import account_summary, get_lifecycles


NOW = datetime(2026, 8, 27, 5, 7, tzinfo=timezone.utc)


def config() -> PaperTradeConfig:
    return PaperTradeConfig(
        starting_equity=10_000.0,
        capital_per_trade=1_000.0,
        max_positions=3,
        fee_rate=0.004,
        slippage_bps=10.0,
        tp1_fraction=0.5,
        pending_ttl_hours=24,
        max_hold_hours=24,
        candle_interval_minutes=15,
    )


def plan(*, pending=False) -> EntryExitPlan:
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


def snapshot():
    return SimpleNamespace(
        symbol="SOLUSD",
        underlying_asset="SOL",
        trade_direction="LONG",
        ticker_last=100.0,
        ticker_ask=100.0,
        last_price=99.0,
    )


def candidate():
    return {
        "direction": "LONG",
        "economic_qualified": True,
        "underlying_asset": "SOL",
        "confidence": 90,
        "profit_rank": 1,
        "profit_rank_score": 80.0,
    }


def enroll(tmp_path, *, pending=False):
    return enroll_paper_opportunity(
        candidate=candidate(),
        snapshot=snapshot(),
        plan=plan(pending=pending),
        episode_id="EP:data-quality",
        cohort_id="COHORT:data-quality",
        decision_at=NOW,
        config=config(),
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )


def bar(hour, minute, *, high=101.0, low=99.5, close=100.0):
    return SimpleNamespace(
        timestamp=int(
            datetime(2026, 8, 27, hour, minute, tzinfo=timezone.utc).timestamp()
        ),
        open=100.0,
        high=high,
        low=low,
        close=close,
    )


class RecordingClient:
    def __init__(self, candles):
        self.candles = list(candles)
        self.calls = 0

    def get_ohlc(self, symbol, interval=15, since=None):
        self.calls += 1
        return list(self.candles)

    def get_ticker(self, symbol):
        return {"bid": 100.0, "last": 100.0}


def test_unavailable_control_freezes_pending_entry_without_market_call(tmp_path):
    assert enroll(tmp_path, pending=True).status == "PENDING"
    control = tmp_path / "control.json"
    control.write_text("{broken-json", encoding="utf-8")
    client = RecordingClient([bar(5, 15, low=98.0)])

    summary = run_paper_trade_monitor(
        config(),
        client=client,
        now=datetime(2026, 8, 27, 5, 45, tzinfo=timezone.utc),
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        control_file=control,
    )

    trade = get_lifecycles(state_file=tmp_path / "state.json")[0]
    assert trade.status == "PENDING_ENTRY"
    assert trade.entry_price is None
    assert client.calls == 0
    assert summary.checked == 1
    assert len(summary.failures) == 1
    assert "CONTROL_UNAVAILABLE_PENDING_FROZEN" in summary.failures[0]


def test_unavailable_control_does_not_censor_already_open_position(tmp_path):
    assert enroll(tmp_path, pending=False).status == "OPENED"
    control = tmp_path / "control.json"
    control.write_text("{broken-json", encoding="utf-8")
    client = RecordingClient([bar(5, 15, high=111.0, low=99.0, close=110.0)])

    summary = run_paper_trade_monitor(
        config(),
        client=client,
        now=datetime(2026, 8, 27, 5, 45, tzinfo=timezone.utc),
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        control_file=control,
    )

    trade = get_lifecycles(state_file=tmp_path / "state.json")[0]
    assert summary.closed == 1
    assert trade.status == "CLOSED"
    assert trade.exit_reason == "TARGET_2"
    assert trade.outcome == "WIN"


def test_missing_first_required_historical_bar_marks_lifecycle_unresolved(tmp_path):
    assert enroll(tmp_path, pending=False).status == "OPENED"
    control = tmp_path / "control.json"
    control.write_text(
        '{"enabled":true,"updated_by":"test","updated_at":"2026-08-27T05:07:00+00:00"}',
        encoding="utf-8",
    )
    # First full post-signal bar must be 05:15. Returning 05:30 first proves
    # historical price action was skipped and cannot be reconstructed safely.
    client = RecordingClient([bar(5, 30), bar(5, 45)])

    summary = run_paper_trade_monitor(
        config(),
        client=client,
        now=datetime(2026, 8, 27, 6, 15, tzinfo=timezone.utc),
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        control_file=control,
    )

    trade = get_lifecycles(state_file=tmp_path / "state.json")[0]
    assert summary.unresolved == 1
    assert trade.status == "UNRESOLVED"
    assert trade.exit_reason.startswith("OHLC_GAP:")
    assert trade.outcome == "UNRESOLVED"
    assert trade.net_pnl is None

    account = account_summary(10_000.0, state_file=tmp_path / "state.json")
    assert account.unresolved_trades == 1
    assert account.closed_trades == 0
    assert account.realized_net_pnl == 0.0
    assert account.closed_equity == 10_000.0


def test_internal_historical_gap_marks_lifecycle_unresolved(tmp_path):
    assert enroll(tmp_path, pending=False).status == "OPENED"
    state = tmp_path / "state.json"
    trade = get_lifecycles(state_file=state)[0]

    # Simulate a prior successful pass through 05:15.
    import json
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["lifecycles"][trade.paper_trade_id]["last_processed_candle_ts"] = int(
        datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()
    )
    state.write_text(json.dumps(payload), encoding="utf-8")

    control = tmp_path / "control.json"
    control.write_text(
        '{"enabled":true,"updated_by":"test","updated_at":"2026-08-27T05:07:00+00:00"}',
        encoding="utf-8",
    )
    client = RecordingClient([bar(5, 30), bar(6, 0)])

    summary = run_paper_trade_monitor(
        config(),
        client=client,
        now=datetime(2026, 8, 27, 6, 30, tzinfo=timezone.utc),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        control_file=control,
    )

    trade = get_lifecycles(state_file=state)[0]
    assert summary.unresolved == 1
    assert trade.status == "UNRESOLVED"
    assert "OHLC_GAP" in str(trade.exit_reason)


def test_no_new_closed_bar_is_a_freeze_not_an_unresolved_outcome(tmp_path):
    assert enroll(tmp_path, pending=False).status == "OPENED"
    control = tmp_path / "control.json"
    control.write_text(
        '{"enabled":true,"updated_by":"test","updated_at":"2026-08-27T05:07:00+00:00"}',
        encoding="utf-8",
    )
    client = RecordingClient([])

    summary = run_paper_trade_monitor(
        config(),
        client=client,
        now=datetime(2026, 8, 27, 5, 20, tzinfo=timezone.utc),
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        control_file=control,
    )

    trade = get_lifecycles(state_file=tmp_path / "state.json")[0]
    assert summary.unresolved == 0
    assert trade.status == "OPEN"
    assert trade.last_processed_candle_ts is None



def test_missing_control_file_freezes_existing_pending_instead_of_assuming_operator_off(tmp_path):
    assert enroll(tmp_path, pending=True).status == "PENDING"
    control = tmp_path / "control.json"
    client = RecordingClient([bar(5, 15, low=98.0)])

    summary = run_paper_trade_monitor(
        config(),
        client=client,
        now=datetime(2026, 8, 27, 5, 45, tzinfo=timezone.utc),
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        control_file=control,
    )

    trade = get_lifecycles(state_file=tmp_path / "state.json")[0]
    assert trade.status == "PENDING_ENTRY"
    assert trade.entry_price is None
    assert client.calls == 0
    assert summary.control_enabled is False
    assert len(summary.failures) == 1
    assert "CONTROL_UNAVAILABLE_PENDING_FROZEN" in summary.failures[0]
