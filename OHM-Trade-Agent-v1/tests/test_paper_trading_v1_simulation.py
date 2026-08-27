from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.paper_trade_models import PaperTradeLifecycle
from app.services.paper_trade_simulation import (
    first_full_bar_start,
    process_closed_candle,
)


INTERVAL = 15


def lifecycle(*, status="OPEN", entry_price=100.0) -> PaperTradeLifecycle:
    quantity = 10.0 if status == "OPEN" else 0.0
    return PaperTradeLifecycle(
        paper_trade_id="PAPER:test",
        episode_id="EP:test",
        cohort_id="COHORT:test",
        symbol="SOLUSD",
        base_asset="SOL",
        direction="LONG",
        status=status,
        entry_action="MARKET_DECISION_TIME" if status == "OPEN" else "LIMIT_PULLBACK",
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
        profit_rank_score=88.0,
        capital=1000.0,
        fee_rate=0.004,
        slippage_bps=10.0,
        tp1_fraction=0.5,
        pending_ttl_hours=24,
        max_hold_hours=24,
        reference_price=100.0,
        reference_ask=100.0,
        entry_price=entry_price if status == "OPEN" else None,
        entry_fee=4.0 if status == "OPEN" else 0.0,
        quantity_initial=quantity,
        quantity_remaining=quantity,
        opened_at="2026-08-27T05:07:00+00:00" if status == "OPEN" else None,
        fees_paid=4.0 if status == "OPEN" else 0.0,
    )


def candle(ts, *, open=100.0, high=104.0, low=98.0, close=101.0):
    return SimpleNamespace(
        timestamp=ts,
        open=open,
        high=high,
        low=low,
        close=close,
    )


def test_first_full_bar_excludes_signal_candle():
    assert first_full_bar_start(
        "2026-08-27T05:07:00+00:00",
        15,
    ) == int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp())


def test_stop_wins_when_stop_and_targets_touch_same_candle():
    trade = lifecycle()
    result = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()),
            open=100.0,
            high=112.0,
            low=94.0,
            close=108.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.status == "CLOSED"
    assert trade.exit_reason == "STOP"
    assert trade.tp1_hit is False
    assert trade.exit_price == pytest.approx(95.0 * 0.999)
    assert trade.net_pnl < 0
    assert trade.outcome == "LOSS"


def test_target2_candle_books_tp1_then_remaining_target2_when_stop_not_hit():
    trade = lifecycle()
    result = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()),
            open=100.0,
            high=111.0,
            low=99.0,
            close=109.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.tp1_hit is True
    assert trade.tp1_quantity == pytest.approx(5.0)
    assert trade.exit_reason == "TARGET_2"
    assert trade.quantity_remaining == pytest.approx(0.0)
    assert trade.net_pnl > 0
    assert trade.outcome == "WIN"


def test_tp1_partial_then_stop_on_later_candle_only_stops_remaining_quantity():
    trade = lifecycle()
    first = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()),
            high=106.0,
            low=99.0,
            close=104.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert first == "TP1"
    assert trade.status == "OPEN"
    assert trade.tp1_hit is True
    assert trade.quantity_remaining == pytest.approx(5.0)

    second = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 5, 30, tzinfo=timezone.utc).timestamp()),
            open=104.0,
            high=111.0,
            low=94.0,
            close=96.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert second == "CLOSED"
    assert trade.exit_reason == "STOP"
    assert trade.quantity_remaining == pytest.approx(0.0)
    # Stop wins over T2 for the remaining half because both touched.
    expected_stop_fill = 95.0 * 0.999
    expected_gross = 5.0 * (105.0 - 100.0) + 5.0 * (expected_stop_fill - 100.0)
    assert trade.gross_pnl == pytest.approx(expected_gross)


def test_pending_limit_fill_does_not_credit_same_candle_target():
    trade = lifecycle(status="PENDING_ENTRY")
    result = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()),
            open=100.0,
            high=111.0,
            low=98.0,
            close=108.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert result == "OPENED"
    assert trade.status == "OPEN"
    assert trade.entry_price == pytest.approx(99.0)
    assert trade.tp1_hit is False
    assert trade.quantity_remaining == pytest.approx(1000.0 / 99.0)


def test_pending_entry_and_stop_same_candle_records_loss():
    trade = lifecycle(status="PENDING_ENTRY")
    result = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()),
            open=100.0,
            high=101.0,
            low=94.0,
            close=96.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.status == "CLOSED"
    assert trade.exit_reason == "ENTRY_CANDLE_STOP"
    assert trade.outcome == "LOSS"


def test_pending_setup_cancels_only_when_closed_price_is_beyond_chase_without_fill():
    trade = lifecycle(status="PENDING_ENTRY")
    result = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 5, 15, tzinfo=timezone.utc).timestamp()),
            open=101.0,
            high=104.0,
            low=100.0,
            close=103.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert result == "CANCELLED"
    assert trade.status == "CANCELLED"
    assert trade.exit_reason == "DO_NOT_CHASE"
    assert trade.outcome == "NO_TRADE"


def test_time_exit_uses_market_slippage_and_fees():
    trade = lifecycle()
    trade.max_hold_hours = 1
    # Opened 05:07; bar ending 06:15 is beyond one hour.
    result = process_closed_candle(
        trade,
        candle(
            int(datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc).timestamp()),
            open=102.0,
            high=104.0,
            low=98.0,
            close=103.0,
        ),
        interval_minutes=INTERVAL,
    )
    assert result == "CLOSED"
    assert trade.exit_reason == "TIME_EXIT"
    assert trade.exit_price == pytest.approx(102.0 * 0.999)
    assert trade.fees_paid > 4.0
