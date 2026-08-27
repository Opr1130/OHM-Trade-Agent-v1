from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.entry_exit_advisor import EntryExitPlan
from app.services.paper_trade_control import (
    get_paper_trade_control,
    set_paper_trade_enabled,
)
from app.services.paper_trade_engine import (
    PaperTradeConfig,
    enroll_paper_opportunity,
)
from app.services.paper_trade_registry import get_lifecycles


NOW = datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc)


def config(**overrides) -> PaperTradeConfig:
    values = dict(
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
    values.update(overrides)
    return PaperTradeConfig(**values)


def plan(
    symbol="SOLUSD",
    *,
    valid_now=True,
    entry_style="pullback_or_retest",
) -> EntryExitPlan:
    return EntryExitPlan(
        symbol=symbol,
        valid_now=valid_now,
        entry_style=entry_style,
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


def snapshot(symbol="SOLUSD", direction="LONG", *, ask=100.1):
    return SimpleNamespace(
        symbol=symbol,
        underlying_asset=symbol.removesuffix("USD"),
        trade_direction=direction,
        ticker_last=100.0,
        ticker_ask=ask,
        last_price=99.5,
    )


def candidate(direction="LONG"):
    return {
        "direction": direction,
        "economic_qualified": True,
        "underlying_asset": "SOL",
        "confidence": 91,
        "profit_rank": 1,
        "profit_rank_score": 88.4,
    }


def test_paper_control_defaults_off_and_toggles_atomically(tmp_path):
    path = tmp_path / "control.json"
    assert get_paper_trade_control(path).enabled is False

    enabled = set_paper_trade_enabled(True, now=NOW, path=path)
    assert enabled.enabled is True
    assert get_paper_trade_control(path).enabled is True

    disabled = set_paper_trade_enabled(False, now=NOW, path=path)
    assert disabled.enabled is False
    assert get_paper_trade_control(path).enabled is False


def test_corrupt_control_fails_safe_to_off(tmp_path):
    path = tmp_path / "control.json"
    path.write_text("{bad-json", encoding="utf-8")
    state = get_paper_trade_control(path)
    assert state.enabled is False
    assert state.status == "UNAVAILABLE"
    assert list(tmp_path.glob("control.json.corrupt-*"))


def test_disabled_enrollment_writes_nothing(tmp_path):
    state = tmp_path / "state.json"
    result = enroll_paper_opportunity(
        candidate=candidate(),
        snapshot=snapshot(),
        plan=plan(),
        episode_id="EP:one",
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        enabled=False,
    )
    assert result.status == "DISABLED"
    assert get_lifecycles(state_file=state) == []


def test_enter_now_uses_decision_ask_plus_configured_slippage(tmp_path):
    state = tmp_path / "state.json"
    result = enroll_paper_opportunity(
        candidate=candidate(),
        snapshot=snapshot(ask=100.1),
        plan=plan(),
        episode_id="EP:market",
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )
    assert result.status == "OPENED"
    trade = get_lifecycles(state_file=state)[0]
    assert trade.status == "OPEN"
    assert trade.entry_price == pytest.approx(100.1 * 1.001)
    assert trade.entry_fee == pytest.approx(4.0)
    assert trade.fees_paid == pytest.approx(4.0)
    assert trade.quantity_initial == pytest.approx(1000.0 / trade.entry_price)
    assert trade.episode_id == "EP:market"
    assert trade.paper_only is True
    assert trade.exchange_write_authority is False


def test_pullback_plan_creates_pending_limit_without_fake_fill(tmp_path):
    state = tmp_path / "state.json"
    result = enroll_paper_opportunity(
        candidate=candidate(),
        snapshot=snapshot(),
        plan=plan(valid_now=False, entry_style="wait_for_pullback"),
        episode_id="EP:limit",
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )
    assert result.status == "PENDING"
    trade = get_lifecycles(state_file=state)[0]
    assert trade.status == "PENDING_ENTRY"
    assert trade.entry_limit == 99.0
    assert trade.entry_price is None
    assert trade.quantity_initial == 0.0
    assert trade.fees_paid == 0.0


def test_short_signal_is_rejected_from_spot_long_paper_v1(tmp_path):
    state = tmp_path / "state.json"
    short_plan = plan()
    short_plan.direction = "SHORT"
    result = enroll_paper_opportunity(
        candidate=candidate("SHORT"),
        snapshot=snapshot(direction="SHORT"),
        plan=short_plan,
        episode_id="EP:short",
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )
    assert result.status == "UNSUPPORTED_DIRECTION"
    assert get_lifecycles(state_file=state) == []


def test_same_symbol_cannot_create_parallel_paper_exposure(tmp_path):
    state = tmp_path / "state.json"
    kwargs = dict(
        candidate=candidate(),
        snapshot=snapshot(),
        plan=plan(),
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )
    first = enroll_paper_opportunity(episode_id="EP:first", **kwargs)
    second = enroll_paper_opportunity(episode_id="EP:second", **kwargs)
    assert first.status == "OPENED"
    assert second.status == "ALREADY_TRACKED"
    assert len(get_lifecycles(state_file=state)) == 1


def test_capacity_guard_reserves_pending_and_open_capital(tmp_path):
    state = tmp_path / "state.json"
    one = enroll_paper_opportunity(
        candidate=candidate(),
        snapshot=snapshot("SOLUSD"),
        plan=plan("SOLUSD"),
        episode_id="EP:sol",
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(max_positions=1),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )
    two = enroll_paper_opportunity(
        candidate={**candidate(), "underlying_asset": "ETH"},
        snapshot=snapshot("ETHUSD"),
        plan=plan("ETHUSD"),
        episode_id="EP:eth",
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(max_positions=1),
        state_file=state,
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )
    assert one.status == "OPENED"
    assert two.status == "CAPACITY"


def test_enter_now_refuses_to_chase_stale_decision_price(tmp_path):
    result = enroll_paper_opportunity(
        candidate=candidate(),
        snapshot=snapshot(ask=103.0),
        plan=plan(),
        episode_id="EP:chase",
        cohort_id="COHORT:one",
        decision_at=NOW,
        config=config(),
        state_file=tmp_path / "state.json",
        event_file=tmp_path / "events.jsonl",
        enabled=True,
    )
    assert result.status == "DO_NOT_CHASE"
