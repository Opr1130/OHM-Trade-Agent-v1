from datetime import datetime, timezone
import math

import pytest

from app.exchanges.kraken import Candle
from app.scanner import market_scanner
from app.jobs import scan_opportunities
from app.scanner.market_scanner import ScanResult
from app.scanner.universe import UniverseAsset
from app.scanner.market_data_validation import PASS, REJECT, WARN, validate_market_data


NOW = datetime(2026, 8, 9, 12, 30, tzinfo=timezone.utc)


def candles(count=200, *, end=None):
    end = int((end or NOW).timestamp())
    start = end - (count - 1) * 3600
    return [
        Candle(start + index * 3600, 100, 101, 99, 100, 100, 10, 5)
        for index in range(count)
    ]


def validate(items, ticker=100, now=NOW):
    return validate_market_data(items, ticker, 60, now)


def test_valid_ohlc_passes():
    assert validate(candles()).status == PASS


def test_zero_trade_flat_candle_with_zero_vwap_is_valid():
    items = candles()
    items[-1] = Candle(
        timestamp=items[-1].timestamp,
        open=0.08655,
        high=0.08655,
        low=0.08655,
        close=0.08655,
        vwap=0.0,
        volume=0.0,
        trade_count=0,
    )
    result = validate(items, ticker=0.08655)
    assert result.qualified
    assert result.invalid_ohlc_count == 0


@pytest.mark.parametrize("volume,trade_count", [(1.0, 0), (0.0, 1), (1.0, 1)])
def test_zero_vwap_with_trading_activity_rejects(volume, trade_count):
    items = candles()
    items[-1] = Candle(**(items[-1].__dict__ | {
        "vwap": 0.0,
        "volume": volume,
        "trade_count": trade_count,
    }))
    result = validate(items)
    assert result.status == REJECT
    assert result.invalid_ohlc_count == 1


def test_mixed_history_accepts_legitimate_zero_trade_candles():
    items = candles()
    for index in (25, 100, 175):
        items[index] = Candle(**(items[index].__dict__ | {
            "open": 100,
            "high": 100,
            "low": 100,
            "close": 100,
            "vwap": 0.0,
            "volume": 0.0,
            "trade_count": 0,
        }))
    result = validate(items)
    assert result.status == PASS
    assert result.invalid_ohlc_count == 0


@pytest.mark.parametrize("field,value", [
    ("open", 0), ("close", -1), ("high", math.nan),
    ("low", math.inf), ("volume", -1),
])
def test_invalid_core_values_reject(field, value):
    items = candles()
    original = items[-1]
    values = original.__dict__ | {field: value}
    items[-1] = Candle(**values)
    result = validate(items)
    assert result.status == REJECT


@pytest.mark.parametrize("changes", [
    {"high": 98, "low": 99},
    {"high": 99.5, "open": 100},
    {"high": 99.5, "close": 100},
    {"low": 100.5, "open": 100},
    {"low": 100.5, "close": 100},
])
def test_invalid_ohlc_geometry_rejects(changes):
    items = candles()
    items[-1] = Candle(**(items[-1].__dict__ | changes))
    assert validate(items).status == REJECT


def test_duplicate_timestamp_detected_and_rejected():
    items = candles()
    items[-1] = Candle(**(items[-1].__dict__ | {"timestamp": items[-2].timestamp}))
    result = validate(items)
    assert result.duplicate_timestamp_count == 1
    assert result.status == REJECT


def test_recent_severe_gap_rejects():
    items = candles()
    for index in range(150, len(items)):
        items[index] = Candle(**(items[index].__dict__ | {
            "timestamp": items[index].timestamp + 7200
        }))
    assert validate(items, now=datetime.fromtimestamp(items[-1].timestamp, timezone.utc)).status == REJECT


def test_irrelevant_old_gap_warns_without_rejecting():
    items = candles()
    for index in range(25, len(items)):
        items[index] = Candle(**(items[index].__dict__ | {
            "timestamp": items[index].timestamp + 7200
        }))
    result = validate(items, now=datetime.fromtimestamp(items[-1].timestamp, timezone.utc))
    assert result.status == WARN
    assert result.qualified


def test_fresh_and_hour_boundary_candles_pass():
    assert validate(candles()).qualified
    boundary = datetime(2026, 8, 9, 13, 0, 1, tzinfo=timezone.utc)
    items = candles(end=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc))
    assert validate(items, now=boundary).qualified


def test_stale_latest_candle_rejects():
    items = candles(end=datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc))
    assert validate(items).status == REJECT


@pytest.mark.parametrize("ticker,expected", [(100.5, PASS), (109, WARN), (121, REJECT)])
def test_ticker_ohlc_consistency_levels(ticker, expected):
    assert validate(candles(), ticker=ticker).status == expected


def test_unusual_latest_candle_is_warning_not_rejection():
    items = candles()
    items[-1] = Candle(**(items[-1].__dict__ | {
        "open": 100, "high": 112, "low": 99, "close": 110, "vwap": 105
    }))
    result = validate(items, ticker=110)
    assert result.suspicious_spike_detected
    assert result.status == WARN
    assert result.qualified


def test_naive_now_is_rejected_by_api_contract():
    with pytest.raises(ValueError):
        validate_market_data(candles(), 100, 60, datetime(2026, 8, 9))


def test_data_rejection_occurs_before_technical_scoring(monkeypatch):
    invalid = candles()
    invalid[-1] = Candle(**(invalid[-1].__dict__ | {"close": 0}))
    class Client:
        def get_ohlc(self, symbol, interval): return invalid
    monkeypatch.setattr(market_scanner, "KrakenClient", Client)
    monkeypatch.setattr(
        market_scanner, "score_snapshot",
        lambda value: (_ for _ in ()).throw(AssertionError("score must not run")),
    )
    asset = UniverseAsset(
        base_asset="SOL", primary_pair="SOLUSD", primary_ticker_last=100,
        primary_ticker_bid=99.9, primary_ticker_ask=100.1,
        primary_kraken_symbol="SOL/USD",
    )
    status, result, reason = market_scanner.analyze_symbol("SOLUSD", asset)
    assert status == "data_reject"
    assert result is None
    assert "invalid" in reason


def test_data_rejected_market_never_reaches_chief(monkeypatch):
    monkeypatch.setattr(
        scan_opportunities, "get_settings", lambda: object()
    )
    monkeypatch.setattr(
        scan_opportunities, "scan_market",
        lambda limit: ScanResult(
            [], 1, 0, 0, 0, [], [], data_quality_rejected=1,
            data_quality_rejections=["BADUSD: invalid OHLC"],
        ),
    )
    monkeypatch.setattr(
        scan_opportunities, "review_candidates",
        lambda *args: (_ for _ in ()).throw(AssertionError("Chief must not run")),
    )
    scan_opportunities.main()
