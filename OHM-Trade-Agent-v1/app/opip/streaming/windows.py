"""Deterministic point-in-time sliding-window contracts.

A window is a fixed-size, grid-aligned bucket keyed by (asset, venue,
window_size_seconds, start). Grid alignment (rather than "first event starts
the window") makes window boundaries reproducible from evidence alone, with
no dependency on arrival order.

Window state is a bounded O(1)-per-window accumulator, never a raw event
list — this build assumes the future worker folds each observation into the
accumulator as it arrives rather than buffering raw events for later
aggregation, per the production resource constraints.

Closure policy (see route_observation): a window becomes eligible for sealing
once its temporal boundary plus a grace period has passed on the *local ingest
clock*, not the provider clock — a provider clock anomaly must not prevent
closure. Once sealed, a window is never mutated by a late-arriving
observation; late evidence is classified and reported, never silently
dropped and never used to retroactively change history.

Every function here takes time as an explicit parameter. Nothing in this
module reads the wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import math

from app.opip.events.contract import require_utc
from app.opip.streaming.contract import ArrivalDecision, SequenceStatus
from app.opip.streaming.envelope import StreamEnvelope
from app.opip.streaming.sequencing import SequenceObservation

SUPPORTED_WINDOW_SECONDS: tuple[int, ...] = (1, 15)


@dataclass(frozen=True)
class WindowBounds:
    asset: str
    venue: str
    window_seconds: int
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self) -> None:
        if not str(self.asset or "").strip():
            raise ValueError("asset is required")
        if not str(self.venue or "").strip():
            raise ValueError("venue is required")
        if int(self.window_seconds) <= 0:
            raise ValueError("window_seconds must be positive")
        require_utc(self.start_utc, field_name="start_utc")
        require_utc(self.end_utc, field_name="end_utc")
        if self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")
        expected_end = self.start_utc + timedelta(seconds=int(self.window_seconds))
        if self.end_utc != expected_end:
            raise ValueError("end_utc must equal start_utc + window_seconds")

    @classmethod
    def for_timestamp(
        cls,
        *,
        asset: str,
        venue: str,
        timestamp_utc: datetime,
        window_seconds: int,
    ) -> "WindowBounds":
        """Grid-align a timestamp to its window, independent of arrival order.

        Alignment is anchored to the UTC epoch so the same instant always
        maps to the same window regardless of which asset/venue/run computes
        it, and so window boundaries are reproducible in replay.
        """
        moment = require_utc(timestamp_utc, field_name="timestamp_utc")
        if int(window_seconds) <= 0:
            raise ValueError("window_seconds must be positive")
        moment_utc = moment.astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = moment_utc - epoch
        total_microseconds = (
            (delta.days * 86_400 + delta.seconds) * 1_000_000
            + delta.microseconds
        )
        window_microseconds = int(window_seconds) * 1_000_000
        bucket_start_microseconds = (
            total_microseconds // window_microseconds
        ) * window_microseconds
        start = epoch + timedelta(microseconds=bucket_start_microseconds)
        end = start + timedelta(seconds=int(window_seconds))
        return cls(
            asset=asset,
            venue=venue,
            window_seconds=int(window_seconds),
            start_utc=start,
            end_utc=end,
        )

    def contains(self, timestamp_utc: datetime) -> bool:
        moment = require_utc(timestamp_utc, field_name="timestamp_utc")
        return self.start_utc <= moment < self.end_utc

    def is_sealable(self, *, now_utc: datetime, grace_seconds: float) -> bool:
        """Physical closure is controlled by local ingest/clock time so a
        provider clock anomaly cannot prevent a window from ever closing."""
        now = require_utc(now_utc, field_name="now_utc")
        if not math.isfinite(float(grace_seconds)) or grace_seconds < 0:
            raise ValueError("grace_seconds must be finite and non-negative")
        return now >= self.end_utc + timedelta(seconds=grace_seconds)


@dataclass(frozen=True)
class WindowAccumulator:
    """Bounded O(1) aggregate state for one window. Never holds raw events."""

    bounds: WindowBounds
    sealed: bool = False
    observation_count: int = 0
    first_provider_timestamp_utc: datetime | None = None
    last_provider_timestamp_utc: datetime | None = None
    first_ingest_timestamp_utc: datetime | None = None
    last_ingest_timestamp_utc: datetime | None = None
    contiguous_count: int = 0
    gap_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    unsupported_sequence_count: int = 0
    reconnect_boundary_count: int = 0
    dropped_frame_count: int = 0
    late_frame_count: int = 0

    @property
    def has_sequence_gap(self) -> bool:
        return self.gap_count > 0

    @property
    def is_empty(self) -> bool:
        return self.observation_count == 0

    def record(
        self, envelope: StreamEnvelope, seq_obs: SequenceObservation
    ) -> "WindowAccumulator":
        """Fold one accepted, in-window observation into a new accumulator.

        Pure: returns a new instance rather than mutating in place, so a
        caller can never accidentally rewrite a sealed window's history by
        reusing a stale reference.
        """
        if self.sealed:
            raise ValueError("cannot record into a sealed window")
        if not self.bounds.contains(envelope.provider_timestamp_utc):
            raise ValueError("envelope provider timestamp is outside this window")

        counters = {
            SequenceStatus.CONTIGUOUS: "contiguous_count",
            SequenceStatus.FIRST: "contiguous_count",
            SequenceStatus.GAP: "gap_count",
            SequenceStatus.DUPLICATE: "duplicate_count",
            SequenceStatus.OUT_OF_ORDER: "out_of_order_count",
            SequenceStatus.UNSUPPORTED: "unsupported_sequence_count",
            SequenceStatus.RESET_NEW_EPOCH: "reconnect_boundary_count",
        }
        field_name = counters[seq_obs.status]

        return replace(
            self,
            observation_count=self.observation_count + 1,
            first_provider_timestamp_utc=(
                envelope.provider_timestamp_utc
                if self.first_provider_timestamp_utc is None
                else min(self.first_provider_timestamp_utc, envelope.provider_timestamp_utc)
            ),
            last_provider_timestamp_utc=(
                envelope.provider_timestamp_utc
                if self.last_provider_timestamp_utc is None
                else max(self.last_provider_timestamp_utc, envelope.provider_timestamp_utc)
            ),
            first_ingest_timestamp_utc=(
                envelope.ingest_timestamp_utc
                if self.first_ingest_timestamp_utc is None
                else min(self.first_ingest_timestamp_utc, envelope.ingest_timestamp_utc)
            ),
            last_ingest_timestamp_utc=(
                envelope.ingest_timestamp_utc
                if self.last_ingest_timestamp_utc is None
                else max(self.last_ingest_timestamp_utc, envelope.ingest_timestamp_utc)
            ),
            **{field_name: getattr(self, field_name) + 1},
        )

    def record_dropped_frame(self) -> "WindowAccumulator":
        """Account for one dropped raw frame without folding its payload."""
        return self.record_dropped_frames(1)

    def record_dropped_frames(self, count: int) -> "WindowAccumulator":
        """Account for one or more dropped frames in O(1) bounded state."""
        amount = int(count)
        if amount <= 0:
            raise ValueError("dropped frame count must be positive")
        # Like late_frame_count, drop counters are post-seal quality metadata.
        # Updating them during the retention interval never changes the sealed
        # aggregate values; it only prevents false COMPLETE classification.
        return replace(self, dropped_frame_count=self.dropped_frame_count + amount)

    def record_late_frame(self) -> "WindowAccumulator":
        """Account for a late frame without mutating the sealed aggregate
        it arrived too late for."""
        return replace(self, late_frame_count=self.late_frame_count + 1)

    def seal(self) -> "WindowAccumulator":
        if self.sealed:
            return self
        return replace(self, sealed=True)


def empty_window(bounds: WindowBounds) -> WindowAccumulator:
    return WindowAccumulator(bounds=bounds)


def route_observation(
    *,
    current: WindowAccumulator | None,
    envelope: StreamEnvelope,
    seq_obs: SequenceObservation,
    window_seconds: int,
) -> tuple[ArrivalDecision, WindowBounds]:
    """Decide which window an observation belongs to.

    This only resolves *placement* (does the observation belong to the
    caller-held window, does a new one need to start, or is the target
    already sealed). Sealing/grace-period decisions are the caller's
    responsibility via WindowBounds.is_sealable — kept separate so a caller
    can seal on its own schedule (e.g. once per cycle) rather than this
    function silently sealing as a side effect.
    """
    target = WindowBounds.for_timestamp(
        asset=envelope.canonical_asset_id or envelope.provider_symbol,
        venue=envelope.provider.value,
        timestamp_utc=envelope.provider_timestamp_utc,
        window_seconds=window_seconds,
    )
    if current is None:
        return ArrivalDecision.ACCEPTED_NEW_WINDOW, target
    if current.bounds == target and not current.sealed:
        return ArrivalDecision.ACCEPTED_OPEN, target
    if current.bounds == target and current.sealed:
        return ArrivalDecision.LATE_AFTER_SEAL, target
    return ArrivalDecision.ACCEPTED_NEW_WINDOW, target
