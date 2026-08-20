from app.exchanges.kraken import BookLevel, Candle, PreTradeBook, PublicTrade
from app.services.native_flow_evidence import evaluate_native_flow_shadow


class FakeKraken:
    def __init__(self, *, bullish=True):
        self.bullish = bullish
        self.ohlc_symbols = []

    def get_pre_trade(self, symbol):
        if self.bullish:
            bids = [BookLevel(100.0, 10), BookLevel(99.9, 8)]
            asks = [BookLevel(100.2, 4), BookLevel(100.3, 4)]
        else:
            bids = [BookLevel(100.0, 3), BookLevel(99.9, 3)]
            asks = [BookLevel(100.2, 12), BookLevel(100.3, 10)]
        return PreTradeBook(symbol=symbol, bids=bids, asks=asks)

    def get_post_trade(self, symbol, count=100):
        if self.bullish:
            prices = [100.3] * 8 + [99.9] * 2
        else:
            prices = [100.3] * 2 + [99.9] * 8
        return [
            PublicTrade(price=price, quantity=1.0 + index * 0.01, trade_timestamp=str(index))
            for index, price in enumerate(prices)
        ]

    def get_ohlc(self, symbol, interval=60):
        self.ohlc_symbols.append(symbol)
        rows = []
        for index in range(20):
            trade_count = 10 if index < 16 else 25
            rows.append(Candle(index, 100, 101, 99, 100, 100, 10, trade_count))
        return rows


def test_bullish_native_flow_produces_bullish_shadow_evidence():
    client = FakeKraken(bullish=True)
    result = evaluate_native_flow_shadow("FAST/USD", client=client)
    assert result.evidence.available is True
    assert result.evidence.bias == "BULLISH"
    assert result.evidence.strength > 0
    assert result.metrics is not None
    assert result.metrics.buy_share > 0.5
    assert result.metrics.book_notional_imbalance > 0
    assert client.ohlc_symbols == ["FASTUSD"]
    assert result.shadow_only is True
    assert result.production_score_changed is False


def test_bearish_native_flow_produces_bearish_shadow_evidence():
    result = evaluate_native_flow_shadow("FAST/USD", client=FakeKraken(bullish=False))
    assert result.evidence.available is True
    assert result.evidence.bias == "BEARISH"
    assert result.metrics is not None
    assert result.metrics.buy_share < 0.5
    assert result.metrics.book_notional_imbalance < 0


class BrokenKraken:
    def get_pre_trade(self, symbol):
        raise RuntimeError("unavailable")


def test_native_flow_fails_open_to_unavailable_shadow_evidence():
    result = evaluate_native_flow_shadow("FAST/USD", client=BrokenKraken())
    assert result.evidence.available is False
    assert result.evidence.bias == "NEUTRAL"
    assert result.metrics is None
    assert result.production_score_changed is False
