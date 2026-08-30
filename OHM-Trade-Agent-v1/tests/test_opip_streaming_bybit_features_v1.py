"""BUILD 4.4 Bybit and cross-venue feature tests."""
from __future__ import annotations

from datetime import datetime, timezone

import orjson
import pytest

from app.opip.events.contract import MappingStatus
from app.opip.streaming.adapter import QueuedRawFrame, RawProviderFrame
from app.opip.streaming.bybit import BybitPublicAdapter
from app.opip.streaming.contract import (
    EvidenceQualityState,
    SequenceStatus,
    StreamProvider,
    StreamType,
)
from app.opip.streaming.feature_accumulator import (
    CrossVenueFeatureAccumulator,
    SealedWindowNotice,
)
from app.opip.streaming.quality import COMPLETE
from app.opip.streaming.windows import WindowBounds


NOW = datetime(2026, 8, 30, 0, 0, 1, tzinfo=timezone.utc)


def _queued(stream_type, symbol, item, *, epoch=0):
    return QueuedRawFrame(
        provider=StreamProvider.BYBIT,
        frame=RawProviderFrame(
            stream_type=stream_type,
            provider_symbol=symbol,
            payload=orjson.dumps({"topic": "x", "ts": 1, "item": item}),
        ),
        connection_id=f"bybit-{epoch}",
        reconnect_epoch=epoch,
        received_monotonic=1.0,
        ingest_timestamp_utc=NOW,
    )


def _trade(symbol="BTCUSDT", side="Buy", seq=100, trade_id="t1", price="60000"):
    return {
        "T": 1788048001000,
        "s": symbol,
        "S": side,
        "v": "0.1",
        "p": price,
        "i": trade_id,
        "seq": seq,
    }


def _liq(symbol="BTCUSDT", side="Buy", ts=1788048001000):
    return {
        "T": ts,
        "s": symbol,
        "S": side,
        "v": "0.1",
        "p": "60000",
    }


def test_bybit_repeated_sequence_is_valid_contiguous():
    adapter = BybitPublicAdapter(symbols=("BTCUSDT",))
    first = adapter.normalize(
        _queued(StreamType.AGG_TRADE, "BTCUSDT", _trade(seq=100, trade_id="a"))
    )
    second = adapter.normalize(
        _queued(StreamType.AGG_TRADE, "BTCUSDT", _trade(seq=100, trade_id="b"))
    )
    assert first.sequence.status is SequenceStatus.FIRST
    assert second.sequence.status is SequenceStatus.CONTIGUOUS
    assert second.envelope.duplicate is False


def test_bybit_sequence_decrease_is_out_of_order():
    adapter = BybitPublicAdapter(symbols=("BTCUSDT",))
    adapter.normalize(
        _queued(StreamType.AGG_TRADE, "BTCUSDT", _trade(seq=100, trade_id="a"))
    )
    result = adapter.normalize(
        _queued(StreamType.AGG_TRADE, "BTCUSDT", _trade(seq=99, trade_id="b"))
    )
    assert result.sequence.status is SequenceStatus.OUT_OF_ORDER


def test_bybit_trade_uses_taker_side_and_notional():
    adapter = BybitPublicAdapter(symbols=("BTCUSDT",))
    result = adapter.normalize(
        _queued(StreamType.AGG_TRADE, "BTCUSDT", _trade(side="Sell"))
    )
    assert result.envelope.identity_status is MappingStatus.UNIQUE
    assert result.envelope.payload["aggressor_side"] == "SELL"
    assert result.envelope.payload["notional_usd"] == pytest.approx(6000.0)


@pytest.mark.parametrize(
    ("position_side", "expected"),
    [("Buy", "LONG_LIQUIDATION"), ("Sell", "SHORT_LIQUIDATION")],
)
def test_bybit_liquidation_position_side_mapping(position_side, expected):
    adapter = BybitPublicAdapter(symbols=("BTCUSDT",))
    result = adapter.normalize(
        _queued(
            StreamType.LIQUIDATION,
            "BTCUSDT",
            _liq(side=position_side),
        )
    )
    assert result.envelope.payload["liquidation_side"] == expected


def test_cross_venue_accumulator_emits_only_after_both_trade_windows_seal():
    from app.opip.streaming.binance import BinancePublicAdapter

    accumulator = CrossVenueFeatureAccumulator()
    binance = BinancePublicAdapter(symbols=("BTCUSDT",))
    bybit = BybitPublicAdapter(symbols=("BTCUSDT",))

    b_payload = {
        "e": "aggTrade", "E": 1, "a": 1, "s": "BTCUSDT",
        "p": "60000", "q": "0.1", "T": 1788048001000, "m": False,
    }
    b_frame = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=RawProviderFrame(
            stream_type=StreamType.AGG_TRADE,
            provider_symbol="BTCUSDT",
            payload=orjson.dumps(b_payload),
        ),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=1.0,
        ingest_timestamp_utc=NOW,
    )
    binance_obs = binance.normalize(b_frame)
    bybit_obs = bybit.normalize(
        _queued(StreamType.AGG_TRADE, "BTCUSDT", _trade(side="Buy"))
    )
    accumulator.record(binance_obs)
    accumulator.record(bybit_obs)

    bounds = WindowBounds.for_timestamp(
        asset="bitcoin",
        venue="BINANCE",
        timestamp_utc=binance_obs.envelope.provider_timestamp_utc,
        window_seconds=15,
    )
    accumulator.seal(
        SealedWindowNotice(
            provider="BINANCE",
            stream_type=StreamType.AGG_TRADE,
            canonical_asset_id="bitcoin",
            window_seconds=15,
            start_utc=bounds.start_utc,
            end_utc=bounds.end_utc,
            quality=COMPLETE,
        )
    )
    assert accumulator.drain_ready() == ()

    accumulator.seal(
        SealedWindowNotice(
            provider="BYBIT",
            stream_type=StreamType.AGG_TRADE,
            canonical_asset_id="bitcoin",
            window_seconds=15,
            start_utc=bounds.start_utc,
            end_utc=bounds.end_utc,
            quality=COMPLETE,
        )
    )
    rows = accumulator.drain_ready()
    assert len(rows) == 1
    assert rows[0].canonical_asset_id == "bitcoin"
    assert rows[0].evidence_quality == EvidenceQualityState.COMPLETE.value
    assert rows[0].cvd_signed_notional_usd == pytest.approx(12000.0)
    assert rows[0].venue_agreement == "ALIGNED_POSITIVE"


def test_cross_venue_liquidation_sync_is_bounded_state():
    from app.opip.streaming.binance import BinancePublicAdapter

    accumulator = CrossVenueFeatureAccumulator()
    binance = BinancePublicAdapter(symbols=("BTCUSDT",))
    bybit = BybitPublicAdapter(symbols=("BTCUSDT",))

    binance_liq = {
        "e": "forceOrder",
        "E": 1788048001000,
        "o": {
            "s": "BTCUSDT", "S": "SELL", "q": "0.1", "p": "60000",
            "ap": "60000", "z": "0.1", "T": 1788048001000,
        },
    }
    b_frame = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=RawProviderFrame(
            stream_type=StreamType.LIQUIDATION,
            provider_symbol="BTCUSDT",
            payload=orjson.dumps(binance_liq),
        ),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=1.0,
        ingest_timestamp_utc=NOW,
    )
    accumulator.record(binance.normalize(b_frame))
    accumulator.record(
        bybit.normalize(
            _queued(
                StreamType.LIQUIDATION,
                "BTCUSDT",
                _liq(side="Buy", ts=1788048002000),
            )
        )
    )
    assert len(accumulator._buckets) == 1
    bucket = next(iter(accumulator._buckets.values()))
    assert bucket.synchronized_seen is True


def test_bybit_connect_and_subscribe_contract(monkeypatch):
    import asyncio
    import app.opip.streaming.bybit as module

    class FakeWs:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(payload)

        async def close(self):
            return None

    fake_ws = FakeWs()
    captured = {}

    async def fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return fake_ws

    monkeypatch.setattr(module, "connect", fake_connect)

    async def scenario():
        adapter = BybitPublicAdapter(symbols=("BTCUSDT", "ETHUSDT"))
        await adapter.connect(connection_id="bybit-0", reconnect_epoch=0)
        await adapter.subscribe()
        await adapter.close()

    asyncio.run(scenario())
    assert captured["url"] == "wss://stream.bybit.com/v5/public/linear"
    request = orjson.loads(fake_ws.sent[0])
    assert request["op"] == "subscribe"
    assert set(request["args"]) == {
        "publicTrade.BTCUSDT",
        "allLiquidation.BTCUSDT",
        "publicTrade.ETHUSDT",
        "allLiquidation.ETHUSDT",
    }
    assert captured["kwargs"]["max_queue"] == 16
