"""O'Pip Sequence 4 BUILD 4.2 runtime-foundation tests."""
from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import json

import pytest

from app.opip.events.contract import MappingStatus, parse_utc
from app.opip.streaming.adapter import (
    NormalizedStreamObservation,
    QueuedRawFrame,
    RawProviderFrame,
)
from app.opip.streaming.backoff import BackoffPolicy
from app.opip.streaming.contract import (
    SequenceStatus,
    StreamProvider,
    StreamType,
)
from app.opip.streaming.envelope import StreamEnvelope
from app.opip.streaming.queueing import DropNewestQueue
from app.opip.streaming.resources import ResourceGuardConfig, assess_resources
from app.opip.streaming.runtime import StreamingRuntime, StreamingRuntimeConfig
from app.opip.streaming.sequencing import StrictIncrementingSequenceTracker


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _raw(sequence: int, *, symbol: str = "BTCUSDT") -> RawProviderFrame:
    payload = json.dumps(
        {
            "provider_timestamp_utc": NOW.isoformat(),
            "sequence": sequence,
            "asset": "bitcoin",
        }
    ).encode("utf-8")
    return RawProviderFrame(
        stream_type=StreamType.AGG_TRADE,
        provider_symbol=symbol,
        payload=payload,
    )


class FakeAdapter:
    provider = StreamProvider.BINANCE

    def __init__(self, frames=None) -> None:
        self.frames = list(frames or [])
        self.connect_calls = 0
        self.subscribe_calls = 0
        self.heartbeat_calls = 0
        self.close_calls = 0
        self._tracker = StrictIncrementingSequenceTracker()

    async def connect(self, *, connection_id: str, reconnect_epoch: int) -> None:
        self.connect_calls += 1

    async def subscribe(self) -> None:
        self.subscribe_calls += 1

    async def receive(self) -> RawProviderFrame:
        if self.frames:
            await asyncio.sleep(0)
            return self.frames.pop(0)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def heartbeat(self) -> None:
        self.heartbeat_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    def normalize(self, frame: QueuedRawFrame) -> NormalizedStreamObservation:
        data = json.loads(frame.frame.payload.decode("utf-8"))
        provider_ts = parse_utc(
            data["provider_timestamp_utc"],
            field_name="provider_timestamp_utc",
        )
        assert provider_ts is not None
        seq = self._tracker.observe(
            str(data["sequence"]),
            reconnect_epoch=frame.reconnect_epoch,
        )
        flags = {
            SequenceStatus.GAP: {"gap_before": True},
            SequenceStatus.OUT_OF_ORDER: {"out_of_order": True},
            SequenceStatus.DUPLICATE: {"duplicate": True},
        }.get(seq.status, {})
        envelope = StreamEnvelope(
            provider=frame.provider,
            stream_type=frame.frame.stream_type,
            provider_symbol=frame.frame.provider_symbol,
            provider_timestamp_utc=provider_ts,
            ingest_timestamp_utc=frame.ingest_timestamp_utc,
            connection_id=frame.connection_id,
            reconnect_epoch=frame.reconnect_epoch,
            provider_sequence=seq.sequence_value,
            sequence_status=seq.status,
            is_aggregate=True,
            identity_status=MappingStatus.UNIQUE,
            canonical_asset_id=str(data["asset"]),
            payload={"sequence": data["sequence"]},
            **flags,
        )
        return NormalizedStreamObservation(envelope=envelope, sequence=seq)


def _runtime_config(**overrides):
    values = dict(
        queue_maxsize=5,
        heartbeat_interval_seconds=0.05,
        heartbeat_timeout_seconds=0.02,
        window_grace_seconds=0.001,
        sealed_window_retention_seconds=30.0,
        shutdown_drain_timeout_seconds=0.5,
        consumer_idle_seconds=0.005,
        resource_sample_interval_seconds=0,
        max_symbols=5,
        backoff=BackoffPolicy(
            minimum_seconds=0.001,
            maximum_seconds=0.005,
            multiplier=2.0,
            jitter_ratio=0.0,
        ),
    )
    values.update(overrides)
    return StreamingRuntimeConfig(**values)


def _queued(epoch: int = 0) -> QueuedRawFrame:
    return QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=_raw(1),
        connection_id=f"binance-{epoch}",
        reconnect_epoch=epoch,
        received_monotonic=1.0,
        ingest_timestamp_utc=NOW,
    )


def test_drop_raw_newest_never_evicts_accepted_frames():
    queue = DropNewestQueue(maxsize=2)
    first = _queued()
    second = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=_raw(2),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=2.0,
        ingest_timestamp_utc=NOW,
    )
    third = QueuedRawFrame(
        provider=StreamProvider.BINANCE,
        frame=_raw(3),
        connection_id="binance-0",
        reconnect_epoch=0,
        received_monotonic=3.0,
        ingest_timestamp_utc=NOW,
    )
    assert queue.offer(first) is True
    assert queue.offer(second) is True
    assert queue.offer(third) is False
    snap = queue.snapshot()
    assert snap.depth == 2
    assert snap.accepted == 2
    assert snap.dropped_newest == 1
    assert snap.high_watermark == 2


def test_backoff_is_bounded_and_deterministic():
    policy = BackoffPolicy(
        minimum_seconds=1,
        maximum_seconds=8,
        multiplier=2,
        jitter_ratio=0.25,
    )
    assert policy.delay_for(0, jitter_unit=0) == 1
    assert policy.delay_for(1, jitter_unit=0) == 2
    assert policy.delay_for(9, jitter_unit=0) == 8
    assert policy.delay_for(1, jitter_unit=-1) == pytest.approx(1.5)
    assert policy.delay_for(1, jitter_unit=1) == pytest.approx(2.5)


def test_resource_guard_is_measurement_only():
    config = ResourceGuardConfig(
        memory_soft_limit_bytes=100,
        loop_lag_soft_limit_seconds=0.1,
        queue_utilization_soft_limit_pct=80,
    )
    result = assess_resources(
        config=config,
        rss_bytes=101,
        loop_lag_seconds=0.2,
        cpu_fraction=0.5,
        queue_utilization_pct=90,
    )
    assert result.degraded is True
    assert set(result.reasons) == {
        "MEMORY_SOFT_LIMIT_EXCEEDED",
        "EVENT_LOOP_LAG_SOFT_LIMIT_EXCEEDED",
        "CPU_SOFT_LIMIT_EXCEEDED",
        "QUEUE_UTILIZATION_SOFT_LIMIT_EXCEEDED",
    }


def test_runtime_processes_fake_frames_without_external_network():
    async def scenario():
        adapter = FakeAdapter([_raw(1), _raw(2)])
        runtime = StreamingRuntime(
            {StreamProvider.BINANCE: adapter},
            config=_runtime_config(),
        )
        await runtime.start()
        for _ in range(100):
            if runtime.snapshot().frames_processed >= 2:
                break
            await asyncio.sleep(0.002)
        snapshot = runtime.snapshot()
        await runtime.stop()
        return adapter, snapshot, runtime.snapshot()

    adapter, running, stopped = asyncio.run(scenario())
    assert adapter.connect_calls >= 1
    assert adapter.subscribe_calls >= 1
    assert running.frames_processed == 2
    assert running.normalized_observations == 2
    assert running.windows_opened == 2
    assert running.queue.capacity == 5
    assert stopped.providers[0].transport_state == "STOPPED"
    assert stopped.active_window_count >= 0


def test_runtime_telemetry_snapshot_is_immutable():
    async def scenario():
        runtime = StreamingRuntime(
            {StreamProvider.BINANCE: FakeAdapter([_raw(1)])},
            config=_runtime_config(),
        )
        await runtime.start()
        for _ in range(100):
            if runtime.snapshot().frames_processed:
                break
            await asyncio.sleep(0.002)
        snap = runtime.snapshot()
        await runtime.stop()
        return snap

    snap = asyncio.run(scenario())
    with pytest.raises(FrozenInstanceError):
        snap.frames_processed = 999


def test_heartbeat_timeout_causes_supervised_reconnect():
    async def scenario():
        adapter = FakeAdapter([])
        runtime = StreamingRuntime(
            {StreamProvider.BINANCE: adapter},
            config=_runtime_config(
                heartbeat_interval_seconds=0.005,
                heartbeat_timeout_seconds=0.005,
            ),
        )
        await runtime.start()
        await asyncio.sleep(0.035)
        snapshot = runtime.snapshot()
        await runtime.stop()
        return adapter, snapshot

    adapter, snapshot = asyncio.run(scenario())
    provider = snapshot.providers[0]
    assert adapter.heartbeat_calls >= 1
    assert provider.heartbeat_timeouts >= 1
    assert provider.connect_attempts >= 2
    assert provider.reconnects >= 1


def test_stale_previous_epoch_frame_is_rejected():
    adapter = FakeAdapter([])
    runtime = StreamingRuntime(
        {StreamProvider.BINANCE: adapter},
        config=_runtime_config(),
    )
    runtime._accepting = True
    runtime._epochs[StreamProvider.BINANCE] = 1
    accepted = runtime._enqueue_if_current(_queued(epoch=0))
    assert accepted is False
    snapshot = runtime.snapshot()
    assert snapshot.providers[0].stale_connection_frames == 1
    assert snapshot.queue.depth == 0


def test_shutdown_owns_and_cleans_long_lived_tasks():
    async def scenario():
        runtime = StreamingRuntime(
            {StreamProvider.BINANCE: FakeAdapter([])},
            config=_runtime_config(),
        )
        await runtime.start()
        assert runtime.running is True
        assert len(runtime._tasks) == 2
        await runtime.stop()
        return runtime

    runtime = asyncio.run(scenario())
    assert runtime.running is False
    assert runtime._tasks == {}
    assert runtime.fatal_exception is None


def test_config_rejects_unbounded_or_invalid_limits():
    with pytest.raises(ValueError):
        StreamingRuntimeConfig(queue_maxsize=0)
    with pytest.raises(ValueError):
        StreamingRuntimeConfig(max_symbols=0)
    with pytest.raises(ValueError):
        StreamingRuntimeConfig(heartbeat_timeout_seconds=float("nan"))
