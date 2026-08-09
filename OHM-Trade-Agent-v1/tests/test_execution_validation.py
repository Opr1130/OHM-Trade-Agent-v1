from datetime import datetime, timedelta, timezone

import pytest

from app.exchanges.kraken import BookLevel, PreTradeBook, PublicTrade, KrakenClient
from app.scanner.execution_validation import (
    HIGH, INVALID, LOW, MEDIUM, UNAVAILABLE,
    evaluate_execution, simulate_buy, simulate_sell,
)
from app.scanner.market_scanner import deep_validate_candidates
from app.scanner.models import MarketSnapshot


NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


def levels(side, count=10, quantity=10, step=0.1):
    return [
        BookLevel(99.9 - index * step if side == "bid" else 100.1 + index * step, quantity)
        for index in range(count)
    ]


def book(bids=None, asks=None):
    return PreTradeBook("SOL/USD", bids if bids is not None else levels("bid"), asks if asks is not None else levels("ask"))


def trade(price=100, age=10):
    timestamp = (NOW - timedelta(seconds=age)).isoformat().replace("+00:00", "Z")
    return PublicTrade(price, 1, timestamp, timestamp)


def evaluate(value=None, notional=500, trades=None):
    return evaluate_execution(value or book(), notional, 100, trades=trades, now=NOW)


def test_normal_book_spread_and_visible_notionals():
    result = evaluate(trades=[trade()])
    assert result.status in {HIGH, MEDIUM}
    assert result.best_bid == 99.9
    assert result.best_ask == 100.1
    assert result.spread_bps == pytest.approx(20)
    assert result.visible_bid_notional == pytest.approx(sum(x.price * x.quantity for x in levels("bid")))
    assert result.visible_ask_notional == pytest.approx(sum(x.price * x.quantity for x in levels("ask")))


@pytest.mark.parametrize("value", [
    book(bids=[]), book(asks=[]),
    book(bids=[BookLevel(100.2, 1)], asks=[BookLevel(100.1, 1)]),
    book(bids=[BookLevel(99.9, 0)]),
    book(asks=[BookLevel(100.1, -1)]),
])
def test_empty_crossed_or_invalid_book_is_invalid(value):
    assert evaluate(value).status == INVALID


def test_depth_completeness_for_top_ten_cutoff():
    narrow = book(
        bids=levels("bid", step=0.01), asks=levels("ask", step=0.01)
    )
    result = evaluate(narrow)
    assert not result.bid_depth_025_complete
    assert not result.ask_depth_025_complete
    assert not result.bid_depth_050_complete
    assert not result.ask_depth_050_complete


def test_depth_complete_when_boundary_is_observed():
    result = evaluate(book(
        bids=levels("bid", step=0.1), asks=levels("ask", step=0.1)
    ))
    assert result.bid_depth_025_complete and result.ask_depth_025_complete
    assert result.bid_depth_050_complete and result.ask_depth_050_complete


def test_short_book_is_complete_and_never_extrapolated():
    value = book(bids=levels("bid", 2), asks=levels("ask", 2))
    result = evaluate(value, notional=10_000)
    assert result.ask_depth_050_complete and result.bid_depth_050_complete
    assert result.visible_ask_notional == pytest.approx(sum(x.price * x.quantity for x in value.asks))
    assert result.buy_visible_coverage_pct < 100
    assert result.buy_vwap is None


def test_one_and_multi_level_buy_fill_and_impact():
    asks = [BookLevel(100, 5), BookLevel(101, 10)]
    one = simulate_buy(asks, 400, 99.5)
    multi = simulate_buy(asks, 1_005, 99.5)
    assert one.fully_covered and one.vwap == 100
    assert multi.fully_covered
    assert multi.vwap == pytest.approx(1005 / 10)
    assert multi.market_impact_pct == pytest.approx(0.5)


def test_buy_exact_boundary_and_insufficient_visible_depth():
    asks = [BookLevel(100, 5)]
    assert simulate_buy(asks, 500, 100).fully_covered
    partial = simulate_buy(asks, 1_000, 100)
    assert not partial.fully_covered
    assert partial.visible_coverage_pct == 50
    assert partial.vwap is None and partial.market_impact_pct is None


def test_one_and_multi_level_sell_fill_and_impact():
    bids = [BookLevel(100, 5), BookLevel(99, 10)]
    one = simulate_sell(bids, 4, 100.5)
    multi = simulate_sell(bids, 10, 100.5)
    assert one.fully_covered and one.vwap == 100
    assert multi.vwap == pytest.approx(99.5)
    assert multi.market_impact_pct == pytest.approx(0.5)


def test_insufficient_visible_bids_has_no_fabricated_vwap():
    partial = simulate_sell([BookLevel(100, 1)], 2, 100.5)
    assert not partial.fully_covered
    assert partial.visible_coverage_pct == 50
    assert partial.vwap is None


def test_round_trip_drag_uses_fully_visible_vwaps():
    result = evaluate(book(
        bids=[BookLevel(99, 100)], asks=[BookLevel(101, 100)]
    ), notional=1_010)
    assert result.estimated_visible_round_trip_market_drag_pct == pytest.approx((101 - 99) / 101 * 100)


def test_recent_trade_fresh_stale_and_price_anomaly():
    fresh = evaluate(trades=[trade()])
    stale = evaluate(trades=[trade(age=600)])
    anomaly = evaluate(trades=[trade(price=110)])
    assert fresh.recent_trade_status == HIGH
    assert any("stale" in warning for warning in stale.warnings)
    assert any("disagrees" in warning for warning in anomaly.warnings)


def test_post_trade_unavailable_is_context_not_crash():
    result = evaluate(trades=None)
    assert result.recent_trade_status == UNAVAILABLE
    assert result.status in {HIGH, MEDIUM}


def snapshot(index=0):
    return MarketSnapshot(
        symbol=f"A{index}USD", last_price=100, ema20=99, ema50=98,
        ema200=90, rsi=55, macd_line=1, macd_signal=.5,
        macd_histogram=.5, atr=2, atr_pct=2, volume_ratio=1.2,
        technical_score=90, trend="bullish", primary_pair=f"A{index}USD",
        primary_quote_currency="USD", kraken_public_symbol=f"A{index}/USD",
        ticker_last=100,
    )


def test_only_eight_finalists_receive_pretrade_and_posttrade():
    class Client:
        def __init__(self): self.pre = []; self.post = []
        def get_pre_trade(self, symbol): self.pre.append(symbol); return book()
        def get_post_trade(self, symbol, count): self.post.append((symbol, count)); return [trade()]
    client = Client()
    result = deep_validate_candidates([snapshot(i) for i in range(200)], 2_000, 1, client)
    assert len(result) == 8
    assert len(client.pre) == len(client.post) == 8
    assert all(count == 100 for _, count in client.post)


def test_pretrade_failure_is_unavailable_and_posttrade_not_called():
    class Client:
        def get_pre_trade(self, symbol): raise RuntimeError("offline")
        def get_post_trade(self, symbol, count): raise AssertionError
    item = snapshot()
    assert deep_validate_candidates([item], 2_000, 1, Client()) == [item]
    assert item.execution_validation.status == UNAVAILABLE


def test_public_client_methods_use_no_credentials(monkeypatch):
    calls = []
    client = KrakenClient()
    def fake_get(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "PreTrade":
            return {"symbol": "BTC/USD", "bids": [{"price": "99", "qty": "1"}], "asks": [{"price": "101", "qty": "1"}]}
        return {"trades": [{"price": "100", "quantity": "1", "trade_ts": "2026-08-09T12:00:00Z"}]}
    monkeypatch.setattr(client, "_get", fake_get)
    assert client.get_pre_trade("BTC/USD").symbol == "BTC/USD"
    assert len(client.get_post_trade("BTC/USD", 10)) == 1
    assert calls == [("PreTrade", {"symbol": "BTC/USD"}), ("PostTrade", {"symbol": "BTC/USD", "count": 10})]
