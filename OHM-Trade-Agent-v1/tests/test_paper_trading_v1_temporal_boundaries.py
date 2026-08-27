from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.paper_trade_models import PaperTradeLifecycle
from app.services.paper_trade_simulation import process_closed_candle


INTERVAL = 15


def _trade(*, pending=False) -> PaperTradeLifecycle:
    return PaperTradeLifecycle(
        paper_trade_id="PAPER:boundary",
        episode_id="EP:boundary",
        cohort_id="COHORT:boundary",
        symbol="SOLUSD",
        base_asset="SOL",
        direction="LONG",
        status="PENDING_ENTRY" if pending else "OPEN",
        entry_action="LIMIT_PULLBACK" if pending else "MARKET_DECISION_TIME",
        signal_at="2026-08-27T05:07:00+00:00",
        created_at="2026-08-27T05:07:00+00:00",
        updated_at="2026-08-27T05:07:00+00:00",
        entry_low=99.0,
        entry_high=100.0,
        entry_limit=99.0,
        chase_limit=102.0,
        stop_price=95.0,
        target_1=105.0,
        target_2=110.0,
        risk_level="low",
        confidence=90,
        profit_rank=1,
        profit_rank_score=80.0,
        capital=1000.0,
        fee_rate=0.004,
        slippage_bps=10.0,
        tp1_fraction=0.5,
        pending_ttl_hours=1,
        max_hold_hours=1,
        reference_price=100.0,
        entry_price=None if pending else 100.0,
        entry_fee=0.0 if pending else 4.0,
        quantity_initial=0.0 if pending else 10.0,
        quantity_remaining=0.0 if pending else 10.0,
        opened_at=None if pending else "2026-08-27T05:07:00+00:00",
        fees_paid=0.0 if pending else 4.0,
    )


def _bar(start_hour, start_minute, *, open_price, high, low, close):
    return SimpleNamespace(
        timestamp=int(
            datetime(
                2026, 8, 27, start_hour, start_minute, tzinfo=timezone.utc
            ).timestamp()
        ),
        open=open_price,
        high=high,
        low=low,
        close=close,
    )


def test_pending_ttl_straddling_bar_cannot_claim_entry():
    trade = _trade(pending=True)
    result = process_closed_candle(
        trade,
        _bar(6, 0, open_price=100.0, high=101.0, low=98.0, close=100.0),
        interval_minutes=INTERVAL,
    )
    assert result == "CANCELLED"
    assert trade.exit_reason == "PENDING_TTL_EXPIRED"
    assert trade.entry_price is None


def test_max_hold_straddling_bar_cannot_credit_target_after_deadline():
    trade = _trade()
    result = process_closed_candle(
        trade,
        _bar(6, 0, open_price=102.0, high=112.0, low=98.0, close=111.0),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.exit_reason == "TIME_EXIT"
    assert trade.tp1_hit is False
    assert trade.exit_price == pytest.approx(102.0 * 0.999)


def test_stop_wins_over_time_boundary_when_same_bar_is_ambiguous():
    trade = _trade()
    result = process_closed_candle(
        trade,
        _bar(6, 0, open_price=102.0, high=112.0, low=94.0, close=110.0),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.exit_reason == "STOP"
    assert trade.outcome == "LOSS"


def test_bar_ending_exactly_at_deadline_can_reach_target():
    trade = _trade()
    trade.opened_at = "2026-08-27T05:15:00+00:00"
    result = process_closed_candle(
        trade,
        _bar(6, 0, open_price=103.0, high=111.0, low=100.0, close=109.0),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.exit_reason == "TARGET_2"


def test_bar_ending_exactly_at_deadline_time_exits_at_close_when_no_target():
    trade = _trade()
    trade.opened_at = "2026-08-27T05:15:00+00:00"
    result = process_closed_candle(
        trade,
        _bar(6, 0, open_price=102.0, high=104.0, low=98.0, close=103.0),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.exit_reason == "TIME_EXIT"
    assert trade.exit_price == pytest.approx(103.0 * 0.999)
