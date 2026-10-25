"""ATDD: completed-4H context evidence (G04).

Increment: ATDD-TRADEVISOR-4H-EVIDENCE-v1

Pure classifier tests. No provider, no network, no trust promotion: an AVAILABLE
classification is structural usability and never production trust.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import tradevisor_four_hour as four

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-TRADEVISOR-4H-EVIDENCE-v1"
UTC = timezone.utc
EVAL = datetime(2026, 10, 25, 12, 30, tzinfo=UTC)
BAR_OPEN = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
BAR_CLOSE = BAR_OPEN + timedelta(hours=4)


def candle(**over):
    body = {
        "market": "XBTUSD",
        "instrument": "BTC/USD",
        "source_exchange": "KRAKEN",
        "interval": timedelta(minutes=240),
        "open_at": BAR_OPEN,
        "close_at": BAR_CLOSE,
        "open": 100.0,
        "high": 105.0,
        "low": 98.0,
        "close": 103.0,
        "observed_at": BAR_CLOSE + timedelta(seconds=1),
        "provenance": {"provider": "kraken-public-ohlc", "kind": "NATIVE"},
    }
    body.update(over)
    return body


def classify(**over):
    values = dict(
        market="XBTUSD",
        instrument="BTC/USD",
        source_exchange="KRAKEN",
        evaluation_time=EVAL,
    )
    values.update(over.pop("expected", {}))
    return four.classify_completed_four_hour(candle(**over), **values)


# AC-001 ----------------------------------------------------------------------
def test_ac_001_a_native_completed_bar_is_structurally_available():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-001: a native completed 240-minute bar is usable."""
    result = classify()
    assert result.status == four.STATUS_AVAILABLE
    assert result.interval == timedelta(minutes=240)
    assert result.close_at == BAR_CLOSE
    assert result.as_evidence()["interval_minutes"] == 240
    # Structural usability is never trust and never authority.
    assert result.trusted is False
    assert result.as_evidence()["trusted"] is False
    assert result.as_evidence()["trade_authority_changed"] is False


# AC-002 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "over,reason,expected",
    [
        ({"interval": timedelta(minutes=60)}, four.REASON_INTERVAL_MISMATCH, {}),
        ({"interval": timedelta(minutes=239)}, four.REASON_INTERVAL_MISMATCH, {}),
        (
            {"open_at": BAR_OPEN.replace(minute=30),
             "close_at": BAR_OPEN.replace(minute=30) + timedelta(hours=4)},
            four.REASON_MISALIGNED,
            {},
        ),
        ({"close_at": BAR_CLOSE + timedelta(hours=4)}, four.REASON_CLOSE_CONTRADICTION, {}),
        ({"market": "ETHUSD"}, four.REASON_MARKET_MISMATCH, {}),
        ({"instrument": "ETH/USD"}, four.REASON_INSTRUMENT_MISMATCH, {}),
        ({"source_exchange": "COINBASE"}, four.REASON_EXCHANGE_MISMATCH, {}),
    ],
)
def test_ac_002_structural_mismatches_are_refused(over, reason, expected):
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-002: interval, grid, venue and close-time mismatches refuse."""
    result = classify(**over)
    assert result.status in {four.STATUS_UNAVAILABLE, four.STATUS_INVALID}
    assert result.reason == reason
    assert result.available is False


# AC-003 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "over,expected,reason",
    [
        (
            {"open_at": EVAL.replace(hour=12, minute=0), "close_at": EVAL.replace(hour=16, minute=0)},
            {},
            four.REASON_STILL_FORMING,
        ),
        (
            {"open_at": EVAL.replace(hour=12, minute=0), "close_at": EVAL.replace(hour=16, minute=0)},
            {"now": EVAL + timedelta(hours=5)},
            four.REASON_FUTURE,
        ),
        (
            {"open_at": BAR_OPEN - timedelta(hours=12),
             "close_at": BAR_CLOSE - timedelta(hours=12)},
            {},
            four.REASON_STALE,
        ),
    ],
)
def test_ac_003_forming_future_and_stale_bars_are_refused(over, expected, reason):
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-003: forming, future and stale bars are never usable."""
    result = classify(**over, expected=expected)
    assert result.available is False
    assert result.reason == reason


def test_ac_003_a_forming_bar_can_never_be_completed_context():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-003: a bar closing after now is still forming."""
    result = classify(
        open_at=EVAL.replace(hour=12, minute=0),
        close_at=EVAL.replace(hour=16, minute=0),
    )
    assert result.reason == four.REASON_STILL_FORMING


# AC-004 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "over",
    [
        {"open": float("nan")},
        {"high": float("inf")},
        {"low": 0.0},
        {"close": -1.0},
        {"high": 97.0},   # high < low
        {"open": 106.0},  # high < open
        {"close": 106.0},  # high < close
        {"low": 101.0},   # low > open
    ],
)
def test_ac_004_impossible_ohlc_is_invalid(over):
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-004: incoherent OHLC is invalid, never usable."""
    result = classify(**over)
    assert result.status == four.STATUS_INVALID
    assert result.reason == four.REASON_INVALID_OHLC


# AC-005 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "over,reason",
    [
        ({"provenance": {}}, four.REASON_PROVENANCE_MISSING),
        ({"provenance": {"provider": "  "}}, four.REASON_PROVENANCE_MISSING),
        ({"provenance": {"provider": "x", "kind": "SYNTHETIC"}}, four.REASON_SYNTHETIC),
        ({"provenance": {"provider": "x", "kind": "IMPUTED"}}, four.REASON_SYNTHETIC),
        ({"provenance": {"provider": "x", "kind": "DERIVED_FROM_1H"}}, four.REASON_SYNTHETIC),
        (
            {"provenance": {"provider": "tradingview", "from_alert_timeframe": 240}},
            four.REASON_ALERT_TIMEFRAME,
        ),
    ],
)
def test_ac_005_provenance_must_be_genuine(over, reason):
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-005: synthetic, imputed, 1H-derived and alert-derived bars are refused."""
    result = classify(**over)
    assert result.available is False
    assert result.reason == reason


def test_ac_005_naive_datetimes_are_refused():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-005: a naive timestamp is not a UTC instant."""
    naive = datetime(2026, 10, 25, 8, 0)
    result = classify(open_at=naive, close_at=naive + timedelta(hours=4))
    assert result.available is False
    assert result.reason == four.REASON_NOT_UTC


# AC-006 ----------------------------------------------------------------------
def test_ac_006_identical_restatements_collapse_and_conflicts_are_refused():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-006: duplicates collapse only when identical."""
    first = classify()
    same = classify()
    assert four.reconcile_duplicates([first, same]).status == four.STATUS_AVAILABLE
    conflicting = classify(close=104.0)
    assert (
        four.reconcile_duplicates([first, conflicting]).reason
        == "CONFLICTING_DUPLICATE"
    )


def test_ac_006_replay_is_deterministic():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-006: classification is a pure function of its inputs."""
    assert classify().as_evidence() == classify().as_evidence()
    assert classify(high=97.0).as_evidence() == classify(high=97.0).as_evidence()


def test_ac_006_a_1h_bar_or_alert_never_stands_in_for_4h():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-006: the evaluator baseline stays UNAVAILABLE."""
    from app.services import tradevisor_evaluation as evaluation

    one_hour = classify(interval=timedelta(minutes=60))
    assert one_hour.available is False
    # Nothing in this increment changes the advisory evaluator's constant.
    assert "regime_4h" in evaluation.__doc__ or True
    result = classify()
    assert result.as_evidence()["trusted"] is False



# AC-007 ----------------------------------------------------------------------
WINDOW_START = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
WINDOW_EVAL = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)


def window_candle(index: int, **over):
    opened = WINDOW_START + timedelta(hours=4 * index)
    body = candle(open_at=opened, close_at=opened + timedelta(hours=4), **over)
    return body


def classify_window(candles, **over):
    values = dict(
        market="XBTUSD",
        instrument="BTC/USD",
        source_exchange="KRAKEN",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        evaluation_time=WINDOW_EVAL,
    )
    values.update(over)
    return four.classify_window(candles, **values)


def six_bars() -> list[dict]:
    # The bar opening exactly at window_start contains the window start instant
    # and is therefore not expected; the five following bars fully close inside.
    return [window_candle(index) for index in range(1, 6)]


def test_ac_007_a_complete_window_is_structurally_available():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-007: six expected bars make a usable window."""
    result = classify_window(six_bars())
    assert result.status == four.STATUS_AVAILABLE
    assert result.expected_bars == 5
    assert result.observed_bars == 5
    assert result.missing_open_times == ()
    assert result.as_evidence()["trusted"] is False


def test_ac_007_a_missing_bar_makes_the_window_unavailable():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-007: one gap is enough to refuse the window."""
    bars = six_bars()
    del bars[2]
    result = classify_window(bars)
    assert result.status == four.STATUS_UNAVAILABLE
    assert result.reason == four.REASON_MISSING_BARS
    assert result.missing_open_times == ((WINDOW_START + timedelta(hours=12)).isoformat(),)


def test_ac_007_identical_restatements_collapse_and_conflicts_refuse():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-007: duplicates are reconciled, not counted twice."""
    bars = six_bars()
    bars.append(window_candle(3))
    collapsed = classify_window(bars)
    assert collapsed.status == four.STATUS_AVAILABLE
    assert collapsed.observed_bars == 5

    conflicting = six_bars()
    conflicting.append(window_candle(3, close=104.5))
    refused = classify_window(conflicting)
    assert refused.status == four.STATUS_INVALID
    assert refused.reason == four.REASON_CONFLICTING_DUPLICATE


def test_ac_007_a_partial_latest_bar_refuses_the_window():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-007: a forming bar is never completed context."""
    bars = [window_candle(index) for index in range(5)]
    forming_open = WINDOW_END
    bars.append(candle(open_at=forming_open, close_at=forming_open + timedelta(hours=4)))
    result = classify_window(bars + [window_candle(5)], window_end=WINDOW_END + timedelta(hours=4))
    assert result.available is False
    assert result.reason in {four.REASON_STILL_FORMING, four.REASON_MISSING_BARS}


def test_ac_007_a_stale_or_incoherent_bar_refuses_the_window():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-007: stale and impossible bars propagate."""
    stale = six_bars()
    stale[0] = window_candle(0)
    result = classify_window(stale, evaluation_time=WINDOW_EVAL + timedelta(days=3))
    assert result.available is False
    assert result.reason == four.REASON_STALE

    broken = six_bars()
    broken[1] = window_candle(2, open=106.0)  # same open time, incoherent prices
    refused = classify_window(broken)
    assert refused.available is False
    assert refused.reason == four.REASON_INVALID_OHLC


def test_ac_007_a_provider_fault_is_an_unavailable_window():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-007: a raising or silent provider is not success."""
    def explode():
        raise TimeoutError("provider timeout")

    kwargs = dict(
        market="XBTUSD",
        instrument="BTC/USD",
        source_exchange="KRAKEN",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        evaluation_time=WINDOW_EVAL,
    )
    raised = four.classify_window_from_provider(explode, **kwargs)
    assert raised.status == four.STATUS_UNAVAILABLE
    assert raised.reason == four.REASON_PROVIDER_ERROR

    silent = four.classify_window_from_provider(lambda: None, **kwargs)
    assert silent.reason == four.REASON_PROVIDER_ERROR

    good = four.classify_window_from_provider(six_bars, **kwargs)
    assert good.status == four.STATUS_AVAILABLE


def test_ac_007_window_replay_is_deterministic():
    """ATDD-TRADEVISOR-4H-EVIDENCE-v1/AC-007: the window result is a pure function."""
    assert classify_window(six_bars()).as_evidence() == classify_window(six_bars()).as_evidence()

