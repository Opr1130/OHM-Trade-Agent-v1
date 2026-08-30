"""BUILD 4.3 Binance public-stream adapter tests."""
from __future__ import annotations

from datetime import datetime, timezone

import orjson
import pytest

from app.opip.events.contract import MappingStatus
from app.opip.streaming.adapter import QueuedRawFrame, RawProviderFrame
from app.opip.streaming.binance import (
    BinancePublicAdapter,
    DEFAULT_BINANCE_PUBLIC_STREAM_URL,
)
from app.opip.streaming.contract import (
    SequenceStatus,
    StreamProvider,
    StreamType,
)


NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _queued(stream_type, payload, *, epoch=0):
    symbol = (
        payload.get("s")
        or (
            payload.get("o", {}).get("s")
            if isinstance(payload.get("o"), dict)
            else None
        )
    )
    return QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=RawProviderFrame(
            stream_type=stream_type,
            provider_symbol=symbol,
            payload=orjson.dumps(payload),
        ),
        connection_id=f"binance-{epoch}",
        reconnect_epoch=epoch,
        received_monotonic=1.0,
        ingest_timestamp_utc=NOW,
    )


def test_binance_uses_current_public_url_path():
    assert DEFAULT_BINANCE_PUBLIC_STREAM_URL == (
        "wss://fstream.binance.com/public/stream"
    )


def test_binance_trade_normalization_and_side():
    adapter = BinancePublicAdapter(symbols=("BTCUSDT",))
    payload = {
        "e": "aggTrade",
        "E": 1788048000100,
        "a": 100,
        "s": "BTCUSDT",
        "p": "60000.0",
        "q": "0.5",
        "T": 1788048000000,
        "m": False,
    }
    result = adapter.normalize(_queued(StreamType.AGG_TRADE, payload))
    env = result.envelope
    assert env.identity_status is MappingStatus.UNIQUE
    assert env.canonical_asset_id == "bitcoin"
    assert env.sequence_status is SequenceStatus.FIRST
    assert env.payload["aggressor_side"] == "BUY"
    assert env.payload["notional_usd"] == pytest.approx(30000.0)


def test_binance_trade_sequence_gap_is_visible():
    adapter = BinancePublicAdapter(symbols=("BTCUSDT",))
    first = {
        "e": "aggTrade", "E": 1, "a": 100, "s": "BTCUSDT",
        "p": "60000", "q": "1", "T": 1788048000000, "m": True,
    }
    later = dict(first, a=103, T=1788048000001)
    adapter.normalize(_queued(StreamType.AGG_TRADE, first))
    result = adapter.normalize(_queued(StreamType.AGG_TRADE, later))
    assert result.sequence.status is SequenceStatus.GAP
    assert result.sequence.gap_size == 2
    assert result.envelope.gap_before is True


@pytest.mark.parametrize(
    ("order_side", "expected"),
    [("SELL", "LONG_LIQUIDATION"), ("BUY", "SHORT_LIQUIDATION")],
)
def test_binance_liquidation_side_is_provider_specific(order_side, expected):
    adapter = BinancePublicAdapter(symbols=("BTCUSDT",))
    payload = {
        "e": "forceOrder",
        "E": 1788048001000,
        "o": {
            "s": "BTCUSDT",
            "S": order_side,
            "q": "0.1",
            "p": "60000",
            "ap": "59990",
            "z": "0.1",
            "T": 1788048000999,
        },
    }
    result = adapter.normalize(_queued(StreamType.LIQUIDATION, payload))
    assert result.sequence.status is SequenceStatus.UNSUPPORTED
    assert result.envelope.payload["liquidation_side"] == expected
    assert result.envelope.payload["notional_usd"] == pytest.approx(5999.0)


def test_unknown_symbol_never_gets_canonical_identity():
    adapter = BinancePublicAdapter(symbols=("XYZUSDT",))
    payload = {
        "e": "aggTrade", "E": 1, "a": 1, "s": "XYZUSDT",
        "p": "1", "q": "1", "T": 1788048000000, "m": False,
    }
    result = adapter.normalize(_queued(StreamType.AGG_TRADE, payload))
    assert result.envelope.identity_status is MappingStatus.UNKNOWN
    assert result.envelope.canonical_asset_id is None


def test_invalid_numeric_evidence_fails_closed():
    adapter = BinancePublicAdapter(symbols=("BTCUSDT",))
    payload = {
        "e": "aggTrade", "E": 1, "a": 1, "s": "BTCUSDT",
        "p": "nan", "q": "1", "T": 1788048000000, "m": False,
    }
    with pytest.raises(ValueError):
        adapter.normalize(_queued(StreamType.AGG_TRADE, payload))
