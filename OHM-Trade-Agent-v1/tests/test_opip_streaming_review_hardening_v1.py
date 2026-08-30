"""Regression tests for Sequence 4 pre-production review hardening."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import orjson
import pytest

from app.opip.streaming.adapter import (
    NormalizedStreamObservation,
    QueuedRawFrame,
    RawProviderFrame,
)
from app.opip.streaming.binance import BinancePublicAdapter
from app.opip.streaming.bybit import BybitPublicAdapter
from app.opip.streaming.contract import EvidenceQualityState, StreamProvider, StreamType
from app.opip.streaming.feature_accumulator import CrossVenueFeatureAccumulator
from app.opip.streaming.quality import COMPLETE
from app.opip.streaming.sinks import SealedWindowNotice
from app.opip.streaming.windows import WindowBounds


INGEST = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _binance_trade(*, trade_id: int, timestamp_ms: int):
    adapter = BinancePublicAdapter(symbols=("BTCUSDT",))
    payload = {
        "e": "aggTrade",
        "E": timestamp_ms,
        "a": trade_id,
        "s": "BTCUSDT",
        "p": "60000",
        "q": "0.1",
        "T": timestamp_ms,
        "m": False,
    }
    queued = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=RawProviderFrame(
            stream_type=StreamType.AGG_TRADE,
            provider_symbol="BTCUSDT",
            payload=orjson.dumps(payload),
        ),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=1.0,
        ingest_timestamp_utc=INGEST,
    )
    return adapter.normalize(queued)


def _bybit_trade_message(trade_id: str):
    return {
        "topic": "publicTrade.BTCUSDT",
        "ts": 1788048001000,
        "data": [
            {
                "T": 1788048001000,
                "s": "BTCUSDT",
                "S": "Buy",
                "v": "0.1",
                "p": "60000",
                "i": trade_id,
                "seq": 100,
            }
        ],
    }


def test_bybit_pending_buffer_fails_closed_and_accounts_discarded_frames():
    adapter = BybitPublicAdapter(
        symbols=("BTCUSDT",),
        pending_maxsize=1,
    )
    assert adapter._ingest_payload(_bybit_trade_message("one")) is False
    with pytest.raises(BufferError):
        adapter._ingest_payload(_bybit_trade_message("two"))
    assert len(adapter._pending) == 1
    assert adapter.discarded_frame_count == 1

    asyncio.run(adapter.close())
    assert len(adapter._pending) == 0
    assert adapter.discarded_frame_count == 2


def test_quiet_venue_window_expires_as_incomplete_not_capacity_drop():
    accumulator = CrossVenueFeatureAccumulator()
    first = _binance_trade(trade_id=1, timestamp_ms=1788048001000)
    accumulator.record(first)

    bounds = WindowBounds.for_timestamp(
        asset="bitcoin",
        venue="BINANCE",
        timestamp_utc=first.envelope.provider_timestamp_utc,
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

    later = _binance_trade(trade_id=2, timestamp_ms=1788048016000)
    accumulator.record(later)
    rows = accumulator.drain_ready()

    assert len(rows) == 1
    assert rows[0].evidence_quality == EvidenceQualityState.INCOMPLETE.value
    assert "MISSING_VENUE" in rows[0].degradations
    assert accumulator.missing_venue_windows == 1
    assert accumulator.dropped_buckets == 0


def test_malformed_feature_payload_is_rejected_without_sink_exception():
    accumulator = CrossVenueFeatureAccumulator()
    normalized = _binance_trade(trade_id=1, timestamp_ms=1788048001000)
    broken_envelope = replace(
        normalized.envelope,
        payload={"aggressor_side": "BUY"},
    )
    broken = NormalizedStreamObservation(
        envelope=broken_envelope,
        sequence=normalized.sequence,
    )

    accumulator.record(broken)
    assert accumulator.invalid_payload_observations == 1
    assert accumulator._buckets == {}
