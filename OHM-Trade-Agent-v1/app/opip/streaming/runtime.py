"""Isolated asyncio streaming runtime foundation for O'Pip BUILD 4.2.

No live provider implementation is present here. Generic runtime code owns
bounded ingress, supervision, liveness, PIT window state and telemetry only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import random
import time
from typing import Awaitable, Callable, Mapping

from app.opip.streaming.adapter import (
    NormalizedStreamObservation,
    QueuedRawFrame,
    RawProviderFrame,
    StreamProviderAdapter,
)
from app.opip.streaming.backoff import BackoffPolicy
from app.opip.streaming.contract import (
    EvidenceQualityState,
    SequenceStatus,
    StreamProvider,
    StreamTransportState,
    StreamType,
)
from app.opip.streaming.quality import assess_window_quality
from app.opip.streaming.queueing import DropNewestQueue
from app.opip.streaming.resources import (
    ResourceGuardConfig,
    assess_resources,
    current_rss_bytes,
)
from app.opip.streaming.sinks import (
    ObservationSink,
    SealedWindowNotice,
    SealedWindowSink,
)
from app.opip.streaming.telemetry import RuntimeTelemetry, RuntimeTelemetrySnapshot
from app.opip.streaming.windows import WindowAccumulator, WindowBounds, empty_window


class _HeartbeatTimeout(ConnectionError):
    pass


class _SymbolLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamingRuntimeConfig:
    queue_maxsize: int = 5000
    heartbeat_interval_seconds: float = 20.0
    heartbeat_timeout_seconds: float = 10.0
    window_grace_seconds: float = 2.0
    sealed_window_retention_seconds: float = 30.0
    shutdown_drain_timeout_seconds: float = 5.0
    consumer_idle_seconds: float = 0.10
    resource_sample_interval_seconds: float = 5.0
    max_symbols: int = 5
    window_seconds: tuple[int, ...] = (1, 15)
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)
    resource_guard: ResourceGuardConfig = field(default_factory=ResourceGuardConfig)

    def __post_init__(self) -> None:
        positive = (
            ("queue_maxsize", self.queue_maxsize),
            ("heartbeat_interval_seconds", self.heartbeat_interval_seconds),
            ("heartbeat_timeout_seconds", self.heartbeat_timeout_seconds),
            ("window_grace_seconds", self.window_grace_seconds),
            ("sealed_window_retention_seconds", self.sealed_window_retention_seconds),
            ("shutdown_drain_timeout_seconds", self.shutdown_drain_timeout_seconds),
            ("consumer_idle_seconds", self.consumer_idle_seconds),
            ("max_symbols", self.max_symbols),
        )
        for name, value in positive:
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if (
            not math.isfinite(float(self.resource_sample_interval_seconds))
            or self.resource_sample_interval_seconds < 0
        ):
            raise ValueError(
                "resource_sample_interval_seconds must be finite and non-negative"
            )
        normalized = tuple(int(item) for item in self.window_seconds)
        if not normalized or any(item <= 0 for item in normalized):
            raise ValueError("window_seconds must contain positive values")
        object.__setattr__(self, "window_seconds", normalized)


class StreamingRuntime:
    """One isolated runtime supervising one or more public-data adapters."""

    def __init__(
        self,
        adapters: Mapping[StreamProvider, StreamProviderAdapter],
        *,
        config: StreamingRuntimeConfig | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        process_time: Callable[[], float] = time.process_time,
        utc_now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter_source: Callable[[], float] = random.random,
        observation_sink: ObservationSink | None = None,
        sealed_window_sink: SealedWindowSink | None = None,
    ) -> None:
        self.config = config or StreamingRuntimeConfig()
        self._adapters = dict(adapters)
        if not self._adapters:
            raise ValueError("at least one adapter is required")
        for provider, adapter in self._adapters.items():
            if adapter.provider is not provider:
                raise ValueError("adapter mapping key must match adapter.provider")

        self._monotonic = monotonic
        self._process_time = process_time
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._jitter_source = jitter_source
        self._observation_sink = observation_sink
        self._sealed_window_sink = sealed_window_sink
        self._queue = DropNewestQueue(maxsize=self.config.queue_maxsize)
        self._telemetry = RuntimeTelemetry(tuple(self._adapters))
        self._stop_event = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._epochs: dict[StreamProvider, int] = {
            provider: -1 for provider in self._adapters
        }
        self._accepting = False
        self._running = False
        self._fatal_exception: BaseException | None = None
        self._windows: dict[
            tuple[str, str, str, int, datetime], WindowAccumulator
        ] = {}
        self._active_symbols: set[str] = set()

    @property
    def running(self) -> bool:
        return self._running and self._fatal_exception is None

    @property
    def fatal_exception(self) -> BaseException | None:
        return self._fatal_exception

    def snapshot(self) -> RuntimeTelemetrySnapshot:
        return self._telemetry.snapshot(
            queue=self._queue.snapshot(),
            active_window_count=len(self._windows),
            active_symbol_count=len(self._active_symbols),
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._accepting = True
        self._stop_event.clear()
        self._fatal_exception = None

        consumer = asyncio.create_task(
            self._consumer_loop(), name="opip-stream-consumer"
        )
        consumer.add_done_callback(self._owned_task_done)
        self._tasks["consumer"] = consumer

        if self.config.resource_sample_interval_seconds > 0:
            resource_task = asyncio.create_task(
                self._resource_monitor_loop(), name="opip-stream-resource"
            )
            resource_task.add_done_callback(self._owned_task_done)
            self._tasks["resource"] = resource_task

        for provider in sorted(self._adapters, key=lambda item: item.value):
            task = asyncio.create_task(
                self._provider_supervisor(provider),
                name=f"opip-stream-{provider.value.lower()}",
            )
            task.add_done_callback(self._owned_task_done)
            self._tasks[f"provider:{provider.value}"] = task

    async def stop(self) -> None:
        if not self._running:
            return
        self._accepting = False
        self._stop_event.set()

        provider_tasks = [
            task
            for name, task in self._tasks.items()
            if name.startswith("provider:")
        ]
        for task in provider_tasks:
            task.cancel()
        if provider_tasks:
            await asyncio.gather(*provider_tasks, return_exceptions=True)

        try:
            await asyncio.wait_for(
                self._queue.join(),
                timeout=self.config.shutdown_drain_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._telemetry.raw_frames_dropped_shutdown += self._queue.discard_all()

        for name in ("consumer", "resource"):
            task = self._tasks.get(name)
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *[
                task
                for name, task in self._tasks.items()
                if name in {"consumer", "resource"}
            ],
            return_exceptions=True,
        )

        for provider in self._telemetry.provider:
            self._telemetry.provider[provider].transport_state = (
                StreamTransportState.STOPPED
            )
        self._tasks.clear()
        self._running = False

    def _owned_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None and self._fatal_exception is None:
            self._fatal_exception = error
            self._telemetry.runtime_failed = True
            self._telemetry.fatal_error_type = type(error).__name__
            self._stop_event.set()
            self._accepting = False

    async def _provider_supervisor(self, provider: StreamProvider) -> None:
        adapter = self._adapters[provider]
        telemetry = self._telemetry.provider[provider]
        retry_attempt = 0

        try:
            while not self._stop_event.is_set():
                self._epochs[provider] += 1
                epoch = self._epochs[provider]
                connection_id = f"{provider.value.lower()}-{epoch}"
                telemetry.connect_attempts += 1
                telemetry.transport_state = StreamTransportState.CONNECTING
                received_on_connection = False

                try:
                    await adapter.connect(
                        connection_id=connection_id,
                        reconnect_epoch=epoch,
                    )
                    await adapter.subscribe()
                    telemetry.successful_connections += 1
                    if epoch > 0:
                        telemetry.reconnects += 1
                    telemetry.transport_state = StreamTransportState.CONNECTED

                    while not self._stop_event.is_set():
                        raw = await self._receive_with_liveness(
                            adapter=adapter,
                            provider=provider,
                        )
                        received_on_connection = True
                        retry_attempt = 0
                        telemetry.last_message_monotonic = self._monotonic()
                        self._telemetry.raw_frames_received += 1
                        queued = QueuedRawFrame(
                            provider=provider,
                            frame=raw,
                            connection_id=connection_id,
                            reconnect_epoch=epoch,
                            received_monotonic=self._monotonic(),
                            ingest_timestamp_utc=self._require_runtime_utc(),
                        )
                        self._enqueue_if_current(queued)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Adapter faults are isolated to this provider session.
                    # The supervisor records the failure and reconnects.
                    telemetry.connection_errors += 1
                finally:
                    try:
                        await adapter.close()
                    except Exception:
                        telemetry.connection_errors += 1
                    if not self._stop_event.is_set():
                        telemetry.disconnects += 1
                        telemetry.transport_state = StreamTransportState.BACKOFF

                if self._stop_event.is_set():
                    break

                jitter = (2.0 * float(self._jitter_source())) - 1.0
                delay = self.config.backoff.delay_for(
                    retry_attempt,
                    jitter_unit=max(-1.0, min(1.0, jitter)),
                )
                if received_on_connection:
                    retry_attempt = 0
                else:
                    retry_attempt += 1
                await self._sleep(delay)
        finally:
            telemetry.transport_state = StreamTransportState.STOPPED

    async def _receive_with_liveness(
        self,
        *,
        adapter: StreamProviderAdapter,
        provider: StreamProvider,
    ) -> RawProviderFrame:
        telemetry = self._telemetry.provider[provider]
        try:
            return await asyncio.wait_for(
                adapter.receive(),
                timeout=self.config.heartbeat_interval_seconds,
            )
        except asyncio.TimeoutError:
            await adapter.heartbeat()
            telemetry.heartbeat_sent += 1
            telemetry.last_heartbeat_monotonic = self._monotonic()
            try:
                return await asyncio.wait_for(
                    adapter.receive(),
                    timeout=self.config.heartbeat_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                telemetry.heartbeat_timeouts += 1
                raise _HeartbeatTimeout("stream liveness timeout") from exc

    def _enqueue_if_current(self, frame: QueuedRawFrame) -> bool:
        telemetry = self._telemetry.provider[frame.provider]
        if (
            not self._accepting
            or frame.reconnect_epoch != self._epochs.get(frame.provider)
        ):
            telemetry.stale_connection_frames += 1
            return False
        accepted = self._queue.offer(frame)
        if accepted:
            self._telemetry.raw_frames_enqueued += 1
            if telemetry.transport_state is StreamTransportState.OVERFLOW:
                telemetry.transport_state = StreamTransportState.CONNECTED
            return True
        self._telemetry.raw_frames_dropped_newest += 1
        telemetry.transport_state = StreamTransportState.OVERFLOW
        return False

    async def _consumer_loop(self) -> None:
        while not self._stop_event.is_set() or self._queue.depth > 0:
            try:
                frame = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self.config.consumer_idle_seconds,
                )
            except asyncio.TimeoutError:
                self._seal_and_prune(self._require_runtime_utc())
                continue

            started = self._monotonic()
            try:
                if frame.reconnect_epoch != self._epochs.get(frame.provider):
                    self._telemetry.provider[
                        frame.provider
                    ].stale_connection_frames += 1
                    continue

                adapter = self._adapters[frame.provider]
                try:
                    normalized = adapter.normalize(frame)
                except (ValueError, TypeError, KeyError):
                    self._telemetry.malformed_frames += 1
                    continue
                except Exception:
                    self._telemetry.processing_errors += 1
                    continue

                self._validate_normalized(frame, normalized)
                self._telemetry.normalized_observations += 1
                self._record_sequence(normalized)
                self._record_windows(normalized)
                if self._observation_sink is not None:
                    try:
                        self._observation_sink(normalized)
                    except Exception:
                        self._telemetry.observation_sink_errors += 1
                self._telemetry.frames_processed += 1
                self._telemetry.last_processed_at_utc = (
                    normalized.envelope.ingest_timestamp_utc
                )
                self._seal_and_prune(self._require_runtime_utc())
            except _SymbolLimitExceeded:
                self._telemetry.symbol_limit_rejections += 1
            except (ValueError, TypeError):
                self._telemetry.processing_errors += 1
            finally:
                self._telemetry.last_processing_latency_seconds = max(
                    0.0, self._monotonic() - started
                )
                self._queue.task_done()

    def _validate_normalized(
        self,
        queued: QueuedRawFrame,
        normalized: NormalizedStreamObservation,
    ) -> None:
        envelope = normalized.envelope
        if envelope.provider is not queued.provider:
            raise ValueError("normalized provider differs from queued provider")
        if envelope.connection_id != queued.connection_id:
            raise ValueError("normalized connection_id differs from runtime session")
        if envelope.reconnect_epoch != queued.reconnect_epoch:
            raise ValueError("normalized reconnect_epoch differs from runtime session")
        if envelope.ingest_timestamp_utc != queued.ingest_timestamp_utc:
            raise ValueError("normalized ingest timestamp differs from runtime capture")
        if normalized.sequence.reconnect_epoch != queued.reconnect_epoch:
            raise ValueError("sequence reconnect_epoch differs from runtime session")

    def _record_sequence(
        self, normalized: NormalizedStreamObservation
    ) -> None:
        status = normalized.sequence.status
        if status is SequenceStatus.GAP:
            self._telemetry.sequence_gaps += 1
            self._telemetry.provider[
                normalized.envelope.provider
            ].transport_state = StreamTransportState.GAPPED
        elif status is SequenceStatus.OUT_OF_ORDER:
            self._telemetry.sequence_out_of_order += 1
        elif status is SequenceStatus.UNSUPPORTED:
            self._telemetry.sequence_unsupported += 1
        elif status is SequenceStatus.RESET_NEW_EPOCH:
            self._telemetry.reconnect_boundaries += 1

    def _record_windows(
        self, normalized: NormalizedStreamObservation
    ) -> None:
        envelope = normalized.envelope
        asset = envelope.canonical_asset_id or envelope.provider_symbol
        if asset not in self._active_symbols:
            if len(self._active_symbols) >= self.config.max_symbols:
                raise _SymbolLimitExceeded("streaming max_symbols bound exceeded")
            self._active_symbols.add(asset)

        for seconds in self.config.window_seconds:
            bounds = WindowBounds.for_timestamp(
                asset=asset,
                venue=envelope.provider.value,
                timestamp_utc=envelope.provider_timestamp_utc,
                window_seconds=seconds,
            )
            key = (
                envelope.provider.value,
                envelope.stream_type.value,
                asset,
                seconds,
                bounds.start_utc,
            )
            current = self._windows.get(key)
            if current is None:
                current = empty_window(bounds)
                self._telemetry.windows_opened += 1
            if current.sealed:
                self._windows[key] = current.record_late_frame()
                self._telemetry.late_frames += 1
            else:
                self._windows[key] = current.record(
                    envelope,
                    normalized.sequence,
                )

    def _seal_and_prune(self, now_utc: datetime) -> None:
        retention = timedelta(
            seconds=self.config.sealed_window_retention_seconds
        )
        for key, window in tuple(self._windows.items()):
            current = window
            if (
                not current.sealed
                and current.bounds.is_sealable(
                    now_utc=now_utc,
                    grace_seconds=self.config.window_grace_seconds,
                )
            ):
                current = current.seal()
                self._windows[key] = current
                self._telemetry.windows_sealed += 1
                quality = assess_window_quality(current)
                if self._sealed_window_sink is not None:
                    try:
                        self._sealed_window_sink(
                            SealedWindowNotice(
                                provider=key[0],
                                stream_type=StreamType(key[1]),
                                canonical_asset_id=(
                                    key[2] if current.bounds.asset == key[2] else None
                                ),
                                window_seconds=key[3],
                                start_utc=current.bounds.start_utc,
                                end_utc=current.bounds.end_utc,
                                quality=quality,
                            )
                        )
                    except Exception:
                        self._telemetry.window_sink_errors += 1
                if quality.state is EvidenceQualityState.DEGRADED:
                    self._telemetry.degraded_windows += 1
                elif quality.state in {
                    EvidenceQualityState.INCOMPLETE,
                    EvidenceQualityState.UNUSABLE,
                }:
                    self._telemetry.incomplete_windows += 1

            if (
                current.sealed
                and now_utc >= current.bounds.end_utc + retention
            ):
                self._windows.pop(key, None)

        active_assets = {
            key[2] for key in self._windows
        }
        self._active_symbols.intersection_update(active_assets)

    async def _resource_monitor_loop(self) -> None:
        interval = self.config.resource_sample_interval_seconds
        last_wall = self._monotonic()
        last_cpu = self._process_time()
        while not self._stop_event.is_set():
            target = self._monotonic() + interval
            await self._sleep(interval)
            try:
                last_wall, last_cpu = self._sample_resources_once(
                    target=target,
                    last_wall=last_wall,
                    last_cpu=last_cpu,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Resource telemetry is advisory. Sampling failures must be
                # visible but must never disable market-data ingestion.
                self._telemetry.resource_sample_errors += 1
                last_wall = self._monotonic()
                last_cpu = self._process_time()

    def _sample_resources_once(
        self,
        *,
        target: float,
        last_wall: float,
        last_cpu: float,
    ) -> tuple[float, float]:
        wall_now = self._monotonic()
        cpu_now = self._process_time()
        lag = max(0.0, wall_now - target)
        wall_delta = wall_now - last_wall
        cpu_fraction = (
            max(0.0, (cpu_now - last_cpu) / wall_delta)
            if wall_delta > 0
            else None
        )
        queue_snapshot = self._queue.snapshot()
        assessment = assess_resources(
            config=self.config.resource_guard,
            rss_bytes=current_rss_bytes(),
            loop_lag_seconds=lag,
            cpu_fraction=cpu_fraction,
            queue_utilization_pct=queue_snapshot.utilization_pct,
        )
        self._telemetry.rss_bytes = assessment.rss_bytes
        self._telemetry.cpu_fraction = assessment.cpu_fraction
        self._telemetry.event_loop_lag_seconds = assessment.loop_lag_seconds
        self._telemetry.resource_degraded = assessment.degraded
        self._telemetry.resource_reasons = assessment.reasons
        return wall_now, cpu_now

    def _require_runtime_utc(self) -> datetime:
        value = self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime UTC clock must be timezone-aware")
        return value.astimezone(timezone.utc)
