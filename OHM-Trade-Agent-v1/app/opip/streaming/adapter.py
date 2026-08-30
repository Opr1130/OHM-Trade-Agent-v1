"""Provider-neutral adapter boundary for O'Pip streaming runtime.

BUILD 4.2 defines the runtime contract only. Concrete Binance and Bybit
implementations arrive in later builds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.opip.events.contract import require_utc
from app.opip.streaming.contract import StreamProvider, StreamType
from app.opip.streaming.envelope import StreamEnvelope
from app.opip.streaming.sequencing import SequenceObservation


@dataclass(frozen=True)
class RawProviderFrame:
    """One provider message before venue-specific normalization."""

    stream_type: StreamType
    provider_symbol: str
    payload: bytes

    def __post_init__(self) -> None:
        if not str(self.provider_symbol or "").strip():
            raise ValueError("provider_symbol is required")
        if not isinstance(self.payload, bytes):
            raise TypeError("payload must be bytes")


@dataclass(frozen=True)
class QueuedRawFrame:
    """Raw frame plus immutable runtime/session provenance."""

    provider: StreamProvider
    frame: RawProviderFrame
    connection_id: str
    reconnect_epoch: int
    received_monotonic: float
    ingest_timestamp_utc: datetime

    def __post_init__(self) -> None:
        if not str(self.connection_id or "").strip():
            raise ValueError("connection_id is required")
        if int(self.reconnect_epoch) < 0:
            raise ValueError("reconnect_epoch cannot be negative")
        if float(self.received_monotonic) < 0:
            raise ValueError("received_monotonic cannot be negative")
        require_utc(self.ingest_timestamp_utc, field_name="ingest_timestamp_utc")


@dataclass(frozen=True)
class NormalizedStreamObservation:
    envelope: StreamEnvelope
    sequence: SequenceObservation


class StreamProviderAdapter(Protocol):
    """Provider-specific transport/normalization surface.

    Generic runtime code owns lifecycle, backpressure, supervision and window
    state. Adapters own URLs, subscriptions, heartbeat wire format, payload
    parsing and provider-specific sequencing.
    """

    @property
    def provider(self) -> StreamProvider:
        ...

    async def connect(self, *, connection_id: str, reconnect_epoch: int) -> None:
        ...

    async def subscribe(self) -> None:
        ...

    async def receive(self) -> RawProviderFrame:
        ...

    async def heartbeat(self) -> None:
        ...

    async def close(self) -> None:
        ...

    def normalize(self, frame: QueuedRawFrame) -> NormalizedStreamObservation:
        ...
