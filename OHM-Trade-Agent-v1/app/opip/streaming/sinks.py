"""Provider-neutral runtime sink contracts for Sequence 4."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.opip.streaming.adapter import NormalizedStreamObservation
from app.opip.streaming.contract import StreamType
from app.opip.streaming.quality import EvidenceQuality


@dataclass(frozen=True)
class SealedWindowNotice:
    provider: str
    stream_type: StreamType
    canonical_asset_id: str | None
    window_seconds: int
    start_utc: datetime
    end_utc: datetime
    quality: EvidenceQuality


class ObservationSink(Protocol):
    def __call__(self, observation: NormalizedStreamObservation) -> None:
        ...


class SealedWindowSink(Protocol):
    def __call__(self, notice: SealedWindowNotice) -> None:
        ...
