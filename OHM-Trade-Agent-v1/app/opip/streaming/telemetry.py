"""Bounded in-memory runtime telemetry for O'Pip BUILD 4.2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.opip.streaming.contract import StreamProvider, StreamTransportState
from app.opip.streaming.queueing import QueueSnapshot


@dataclass(frozen=True)
class ProviderTelemetrySnapshot:
    provider: str
    transport_state: str
    connect_attempts: int
    successful_connections: int
    disconnects: int
    reconnects: int
    heartbeat_sent: int
    heartbeat_timeouts: int
    connection_errors: int
    stale_connection_frames: int
    last_message_monotonic: float | None
    last_heartbeat_monotonic: float | None


@dataclass(frozen=True)
class RuntimeTelemetrySnapshot:
    queue: QueueSnapshot
    providers: tuple[ProviderTelemetrySnapshot, ...]
    raw_frames_received: int
    raw_frames_enqueued: int
    raw_frames_dropped_newest: int
    raw_frames_dropped_shutdown: int
    frames_processed: int
    malformed_frames: int
    normalized_observations: int
    processing_errors: int
    observation_sink_errors: int
    window_sink_errors: int
    resource_sample_errors: int
    symbol_limit_rejections: int
    runtime_failed: bool
    fatal_error_type: str | None
    last_processing_latency_seconds: float | None
    last_processed_at_utc: str | None
    sequence_gaps: int
    sequence_out_of_order: int
    sequence_unsupported: int
    reconnect_boundaries: int
    windows_opened: int
    windows_sealed: int
    late_frames: int
    degraded_windows: int
    incomplete_windows: int
    active_window_count: int
    active_symbol_count: int
    rss_bytes: int | None
    cpu_fraction: float | None
    event_loop_lag_seconds: float | None
    resource_degraded: bool
    resource_reasons: tuple[str, ...]


class _ProviderTelemetry:
    def __init__(self, provider: StreamProvider) -> None:
        self.provider = provider
        self.transport_state = StreamTransportState.DISCONNECTED
        self.connect_attempts = 0
        self.successful_connections = 0
        self.disconnects = 0
        self.reconnects = 0
        self.heartbeat_sent = 0
        self.heartbeat_timeouts = 0
        self.connection_errors = 0
        self.stale_connection_frames = 0
        self.last_message_monotonic: float | None = None
        self.last_heartbeat_monotonic: float | None = None

    def snapshot(self) -> ProviderTelemetrySnapshot:
        return ProviderTelemetrySnapshot(
            provider=self.provider.value,
            transport_state=self.transport_state.value,
            connect_attempts=self.connect_attempts,
            successful_connections=self.successful_connections,
            disconnects=self.disconnects,
            reconnects=self.reconnects,
            heartbeat_sent=self.heartbeat_sent,
            heartbeat_timeouts=self.heartbeat_timeouts,
            connection_errors=self.connection_errors,
            stale_connection_frames=self.stale_connection_frames,
            last_message_monotonic=self.last_message_monotonic,
            last_heartbeat_monotonic=self.last_heartbeat_monotonic,
        )


class RuntimeTelemetry:
    """Mutable counters owned by one runtime; callers receive frozen snapshots."""

    def __init__(self, providers: tuple[StreamProvider, ...]) -> None:
        self.provider: dict[StreamProvider, _ProviderTelemetry] = {
            item: _ProviderTelemetry(item) for item in providers
        }
        self.raw_frames_received = 0
        self.raw_frames_enqueued = 0
        self.raw_frames_dropped_newest = 0
        self.raw_frames_dropped_shutdown = 0
        self.frames_processed = 0
        self.malformed_frames = 0
        self.normalized_observations = 0
        self.processing_errors = 0
        self.observation_sink_errors = 0
        self.window_sink_errors = 0
        self.resource_sample_errors = 0
        self.symbol_limit_rejections = 0
        self.runtime_failed = False
        self.fatal_error_type: str | None = None
        self.last_processing_latency_seconds: float | None = None
        self.last_processed_at_utc: datetime | None = None
        self.sequence_gaps = 0
        self.sequence_out_of_order = 0
        self.sequence_unsupported = 0
        self.reconnect_boundaries = 0
        self.windows_opened = 0
        self.windows_sealed = 0
        self.late_frames = 0
        self.degraded_windows = 0
        self.incomplete_windows = 0
        self.rss_bytes: int | None = None
        self.cpu_fraction: float | None = None
        self.event_loop_lag_seconds: float | None = None
        self.resource_degraded = False
        self.resource_reasons: tuple[str, ...] = ()

    def snapshot(
        self,
        *,
        queue: QueueSnapshot,
        active_window_count: int,
        active_symbol_count: int,
    ) -> RuntimeTelemetrySnapshot:
        providers = tuple(
            self.provider[item].snapshot()
            for item in sorted(self.provider, key=lambda value: value.value)
        )
        return RuntimeTelemetrySnapshot(
            queue=queue,
            providers=providers,
            raw_frames_received=self.raw_frames_received,
            raw_frames_enqueued=self.raw_frames_enqueued,
            raw_frames_dropped_newest=self.raw_frames_dropped_newest,
            raw_frames_dropped_shutdown=self.raw_frames_dropped_shutdown,
            frames_processed=self.frames_processed,
            malformed_frames=self.malformed_frames,
            normalized_observations=self.normalized_observations,
            processing_errors=self.processing_errors,
            observation_sink_errors=self.observation_sink_errors,
            window_sink_errors=self.window_sink_errors,
            resource_sample_errors=self.resource_sample_errors,
            symbol_limit_rejections=self.symbol_limit_rejections,
            runtime_failed=self.runtime_failed,
            fatal_error_type=self.fatal_error_type,
            last_processing_latency_seconds=self.last_processing_latency_seconds,
            last_processed_at_utc=(
                self.last_processed_at_utc.isoformat()
                if self.last_processed_at_utc is not None
                else None
            ),
            sequence_gaps=self.sequence_gaps,
            sequence_out_of_order=self.sequence_out_of_order,
            sequence_unsupported=self.sequence_unsupported,
            reconnect_boundaries=self.reconnect_boundaries,
            windows_opened=self.windows_opened,
            windows_sealed=self.windows_sealed,
            late_frames=self.late_frames,
            degraded_windows=self.degraded_windows,
            incomplete_windows=self.incomplete_windows,
            active_window_count=active_window_count,
            active_symbol_count=active_symbol_count,
            rss_bytes=self.rss_bytes,
            cpu_fraction=self.cpu_fraction,
            event_loop_lag_seconds=self.event_loop_lag_seconds,
            resource_degraded=self.resource_degraded,
            resource_reasons=self.resource_reasons,
        )
