from datetime import datetime, timedelta, timezone

from app.services.technical_structure import (
    BIAS_INSUFFICIENT,
    RETEST_HELD,
    StructureBar,
    analyze_technical_structure,
    confirmed_swings,
    latest_fvg_zone,
    latest_liquidity_sweep,
)

BASE = datetime(2026, 8, 24, tzinfo=timezone.utc)


def b(i, o, h, l, c):
    return StructureBar(BASE + timedelta(minutes=10 * i), o, h, l, c)


def test_future_bar_cannot_change_decision_time_structure():
    bars = [
        b(0, 100, 102, 99, 101), b(1, 101, 105, 100, 104),
        b(2, 104, 110, 103, 108), b(3, 108, 109, 103, 104),
        b(4, 104, 106, 101, 102), b(5, 102, 107, 101, 106),
        b(6, 106, 112, 105, 111), b(7, 111, 113, 109, 112),
    ]
    at = bars[6].observed_at
    first = analyze_technical_structure("TESTUSD", bars, decision_at=at)
    future_changed = bars + [b(8, 112, 150, 90, 95)]
    second = analyze_technical_structure("TESTUSD", future_changed, decision_at=at)
    assert first == second


def test_swing_requires_right_side_confirmation():
    bars = [b(0, 100, 101, 99, 100), b(1, 100, 110, 99, 105), b(2, 105, 106, 100, 102)]
    highs, _ = confirmed_swings(bars, left=1, right=1)
    assert [s.price for s in highs] == [110]
    highs_unconfirmed, _ = confirmed_swings(bars[:2], left=1, right=1)
    assert highs_unconfirmed == ()


def test_fvg_is_mechanical_three_bar_non_overlap():
    bars = [b(0, 100, 102, 99, 101), b(1, 101, 105, 101, 104), b(2, 106, 110, 106, 109)]
    low, high = latest_fvg_zone(bars)
    assert (low, high) == (102, 106)


def test_no_fvg_when_ranges_overlap():
    bars = [b(0, 100, 103, 99, 102), b(1, 102, 105, 101, 104), b(2, 104, 106, 102, 105)]
    assert latest_fvg_zone(bars) == (None, None)


def test_liquidity_high_sweep_requires_wick_and_reclaim():
    bars = [
        b(0, 100, 102, 99, 101), b(1, 101, 110, 100, 108), b(2, 108, 109, 104, 105),
        b(3, 105, 106, 102, 103), b(4, 103, 111, 102, 109),
    ]
    highs, lows = confirmed_swings(bars, left=1, right=1)
    assert latest_liquidity_sweep(bars, highs, lows) == "HIGH_SWEEP_RECLAIM"


def test_insufficient_data_degrades_without_invented_structure():
    ctx = analyze_technical_structure("abcusd", [b(0, 100, 101, 99, 100)], decision_at=BASE)
    assert ctx.bias == BIAS_INSUFFICIENT
    assert ctx.advisory_only is True
    assert ctx.bullish_break_level is None


def test_breakout_retest_can_be_reported_as_held():
    bars = [
        b(0, 100, 101, 99, 100), b(1, 100, 110, 99, 108), b(2, 108, 109, 103, 104),
        b(3, 104, 106, 101, 102), b(4, 102, 111, 102, 111), b(5, 111, 112, 109, 110.5),
        b(6, 110.5, 114, 110, 113),
    ]
    ctx = analyze_technical_structure("TESTUSD", bars, decision_at=bars[-1].observed_at, swing_left=1, swing_right=1)
    assert ctx.bullish_break_level == 110
    assert ctx.retest_state == RETEST_HELD


def test_repeatability():
    bars = [b(i, 100+i, 102+i, 99+i, 101+i) for i in range(8)]
    a = analyze_technical_structure("XUSD", bars, decision_at=bars[-1].observed_at)
    c = analyze_technical_structure("XUSD", bars, decision_at=bars[-1].observed_at)
    assert a == c
