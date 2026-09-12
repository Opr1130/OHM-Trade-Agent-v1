"""Rolling feature state, checkpointing and restart semantics (PR3 slice E).

A restart is not a data loss event, and it is also not a non-event. This module
keeps the two facts separable:

* ``NEW_LISTING_COLD_START`` — O'Pip has never had history for this instrument.
* ``INSUFFICIENT_HISTORY`` — history exists but is shorter than the slowest
  window, so slow features are absent by construction.
* ``RESTART_WARMUP`` — state was resumed from a checkpoint and is still short.
* ``WARM`` — the retained window is complete for every declared supported feature.

Material feed gaps reset persistence evidence. A compression run that "lasted
90 minutes" across a 40-minute hole did not last 90 minutes, so the run is
restarted rather than carried over.

Retained state is bounded and contiguous by construction, which is what lets a
checkpoint stay small enough for the canonical writer's payload bound while
still reproducing the same supported feature state as uninterrupted processing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from app.opip.contracts.enums import CoverageState, PayloadKind, RestartState
from app.opip.contracts.features import FeatureStateCheckpoint
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.observation import Observation
from app.opip.contracts.serialization import canonical_json_bytes
from app.opip.features.engine import (
    FEATURE_VERSION,
    FEATURE_WINDOW_INTERVALS,
    MINIMUM_WARMUP_INTERVALS,
)
from app.opip.features.indicators import safe_ema
from app.opip.market.aggregates import (
    DEFAULT_INTERVAL_SECONDS,
    AlignmentResult,
    contiguous_tail,
)
from app.opip.market.observations import aggregate_content_fingerprint

AGGREGATE_DEPENDENCY = f"fixed_interval_aggregate:{DEFAULT_INTERVAL_SECONDS}s"


@dataclass(frozen=True)
class RollingState:
    """Bounded, contiguous retained history for one instrument version.

    ``interval_starts`` is not stored: the window is contiguous by
    construction, so the first interval start plus the interval length is
    enough. That is one fewer array in every checkpoint payload.

    ``venue`` is persisted explicitly; never reconstructed by splitting opaque
    instrument version IDs.
    """

    instrument_version_id: str
    venue: str
    venue_instrument_id: str
    feature_version: str
    closes: tuple[float, ...] = ()
    opens: tuple[float, ...] = ()
    highs: tuple[float, ...] = ()
    lows: tuple[float, ...] = ()
    volumes: tuple[float, ...] = ()
    revisions: tuple[int, ...] = ()
    opens_known: tuple[bool, ...] = ()
    content_fingerprints: tuple[str, ...] = ()
    first_interval_epoch: int | None = None
    last_receipt_epoch: float | None = None
    persistence_intervals: int = 0
    gap_resets: int = 0
    last_gap_epoch: int | None = None
    restart_state: RestartState = RestartState.NEW_LISTING_COLD_START
    resumed_from_checkpoint: bool = False
    # Aggregate trust: True only when every retained open slot is known.
    opens_retained: bool = True
    consumed_input_watermark: ConsumedInputWatermark = ConsumedInputWatermark.zero()
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if not str(self.venue or "").strip():
            raise ValueError("venue is required")
        if self.opens and len(self.opens) != len(self.closes):
            raise ValueError("retained series must have equal lengths")
        if not self.opens and self.closes:
            object.__setattr__(self, "opens", tuple(self.closes))
            object.__setattr__(self, "opens_known", tuple(False for _ in self.closes))
            object.__setattr__(self, "opens_retained", False)
        if not self.opens_known and self.closes:
            # Legacy path without per-slot trust: all unknown when opens_retained false.
            known = tuple(bool(self.opens_retained) for _ in self.closes)
            object.__setattr__(self, "opens_known", known)
        if not self.content_fingerprints and self.closes:
            object.__setattr__(
                self, "content_fingerprints", tuple("" for _ in self.closes)
            )
        lengths = {
            len(self.closes),
            len(self.opens),
            len(self.highs),
            len(self.lows),
            len(self.volumes),
            len(self.revisions),
            len(self.opens_known),
            len(self.content_fingerprints),
        }
        if self.closes and len(lengths) != 1:
            raise ValueError("retained series must have equal lengths")
        if self.closes:
            object.__setattr__(self, "opens_retained", all(self.opens_known))

    @property
    def interval_count(self) -> int:
        return len(self.closes)

    @property
    def warm(self) -> bool:
        return self.interval_count >= MINIMUM_WARMUP_INTERVALS

    @property
    def window_complete(self) -> bool:
        return self.interval_count >= FEATURE_WINDOW_INTERVALS

    @property
    def last_interval_epoch(self) -> int | None:
        if self.first_interval_epoch is None or self.interval_count == 0:
            return None
        return self.first_interval_epoch + self.interval_seconds * (
            self.interval_count - 1
        )

    def interval_start_at(self, index: int) -> datetime:
        if self.first_interval_epoch is None:
            raise ValueError("state has no retained intervals")
        epoch = self.first_interval_epoch + self.interval_seconds * index
        return datetime.fromtimestamp(epoch, tz=timezone.utc)


def initial_state(
    instrument_version: InstrumentVersion,
    *,
    feature_version: str = FEATURE_VERSION,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> RollingState:
    return RollingState(
        instrument_version_id=instrument_version.instrument_version_id,
        venue=instrument_version.venue,
        venue_instrument_id=instrument_version.venue_instrument_id,
        feature_version=feature_version,
        restart_state=RestartState.NEW_LISTING_COLD_START,
        interval_seconds=interval_seconds,
    )


@dataclass(frozen=True)
class AdvanceResult:
    state: RollingState
    applied: int = 0
    ignored_stale: int = 0
    gap_detected: bool = False
    gap_intervals: int = 0

    @property
    def persistence_reset(self) -> bool:
        return self.gap_detected


def _resolve_restart_state(state: RollingState) -> RestartState:
    if state.interval_count == 0:
        return (
            RestartState.RESTART_WARMUP
            if state.resumed_from_checkpoint
            else RestartState.NEW_LISTING_COLD_START
        )
    if state.warm:
        return RestartState.WARM
    if state.resumed_from_checkpoint:
        return RestartState.RESTART_WARMUP
    if state.restart_state is RestartState.NEW_LISTING_COLD_START:
        return RestartState.NEW_LISTING_COLD_START
    return RestartState.INSUFFICIENT_HISTORY


def _clear_series() -> tuple[
    list[float],
    list[float],
    list[float],
    list[float],
    list[float],
    list[int],
    list[bool],
    list[str],
]:
    return [], [], [], [], [], [], [], []


def revise_against_retained(
    observations: Sequence[Observation],
    state: RollingState,
) -> tuple[Observation, ...]:
    """Mint a superseding revision when a re-polled bar differs from retained evidence.

    Normalization always emits revision 1. Without this step a later poll with
    corrected OHLC for an interval still in the retained window collides on the
    same observation_id, the writer returns DUPLICATE_OK for the old payload,
    and RollingState ignores the equal revision. Canonical history stays
    append-only; this only assigns the next revision identity before publish.

    Unchanged tip re-polls are retained for alignment/coverage so a tip-bounded
    expected window does not invent false gaps, but they are not revision-minted.
    Equal or older revisions are ignored by ``advance_state``; publish relies on
    writer ``DUPLICATE_OK`` for identical observation identities.
    """
    if not observations or state.interval_count == 0 or state.first_interval_epoch is None:
        return tuple(observations)

    revised: list[Observation] = []
    for observation in observations:
        if (
            observation.interval_forming
            or observation.aggregate_interval_seconds != state.interval_seconds
            or observation.instrument_version_id != state.instrument_version_id
        ):
            revised.append(observation)
            continue
        epoch = int(observation.source_event_time.timestamp())
        delta = epoch - int(state.first_interval_epoch)
        if delta < 0 or delta % state.interval_seconds != 0:
            revised.append(observation)
            continue
        index = delta // state.interval_seconds
        if index < 0 or index >= state.interval_count:
            revised.append(observation)
            continue
        retained_revision = int(state.revisions[index])
        incoming_fp = aggregate_content_fingerprint(dict(observation.values))
        retained_fp = (
            state.content_fingerprints[index]
            if index < len(state.content_fingerprints)
            else ""
        )
        if retained_fp and retained_fp == incoming_fp:
            # Unchanged: keep for coverage; do not mint a superseding revision.
            revised.append(observation)
            continue
        if retained_fp and retained_fp != incoming_fp:
            changed = True
        elif state.opens_known[index] if index < len(state.opens_known) else False:
            live_retained = (
                float(state.opens[index]),
                float(state.highs[index]),
                float(state.lows[index]),
                float(state.closes[index]),
                float(state.volumes[index]),
            )
            incoming = (
                float(observation.values["open"]),
                float(observation.values["high"]),
                float(observation.values["low"]),
                float(observation.values["close"]),
                float(observation.values["volume"]),
            )
            changed = incoming != live_retained
        else:
            # Legacy unknown open: never mint on open alone.
            live_retained = (
                float(state.highs[index]),
                float(state.lows[index]),
                float(state.closes[index]),
                float(state.volumes[index]),
            )
            incoming = (
                float(observation.values["high"]),
                float(observation.values["low"]),
                float(observation.values["close"]),
                float(observation.values["volume"]),
            )
            changed = incoming != live_retained
        if not changed:
            revised.append(observation)
            continue
        if int(observation.revision) > retained_revision:
            revised.append(observation)
            continue
        next_revision = retained_revision + 1
        prior_id = (
            f"OBS:{state.instrument_version_id}"
            f":{state.interval_seconds}s:{epoch}:{retained_revision}"
        )
        revised.append(
            replace(
                observation,
                revision=next_revision,
                supersedes=prior_id,
            )
        )
    return tuple(revised)


def advance_state(
    state: RollingState,
    observations: Iterable[Observation],
) -> AdvanceResult:
    """Fold closed intervals into rolling state, in strict grid order.

    A non-contiguous interval is a material gap: the retained window always
    restarts at the new interval instead of concatenating across the hole.

    A higher revision for an interval still inside the retained horizon replaces
    the prior bar in place. Equal or older revisions never rewind state.
    """
    ordered = sorted(
        observations,
        key=lambda item: (item.source_event_time, item.revision, item.ingestion_order),
    )
    closes = list(state.closes)
    opens = list(state.opens)
    highs = list(state.highs)
    lows = list(state.lows)
    volumes = list(state.volumes)
    revisions = list(state.revisions)
    opens_known = list(state.opens_known)
    content_fingerprints = list(state.content_fingerprints)
    first_epoch = state.first_interval_epoch
    persistence = state.persistence_intervals
    gap_resets = state.gap_resets
    last_gap_epoch = state.last_gap_epoch
    last_receipt = state.last_receipt_epoch
    applied = 0
    ignored = 0
    gap_detected = False
    gap_intervals = 0

    for observation in ordered:
        if observation.interval_forming:
            ignored += 1
            continue
        if observation.aggregate_interval_seconds != state.interval_seconds:
            ignored += 1
            continue
        epoch = int(observation.source_event_time.timestamp())
        fingerprint = aggregate_content_fingerprint(dict(observation.values))

        # Superseding revision for an interval still inside the retained window.
        if first_epoch is not None and closes:
            delta = epoch - first_epoch
            if delta >= 0 and delta % state.interval_seconds == 0:
                index = delta // state.interval_seconds
                if 0 <= index < len(closes):
                    if int(observation.revision) <= int(revisions[index]):
                        ignored += 1
                        continue
                    opens[index] = float(observation.values["open"])
                    closes[index] = float(observation.values["close"])
                    highs[index] = float(observation.values["high"])
                    lows[index] = float(observation.values["low"])
                    volumes[index] = float(observation.values["volume"])
                    revisions[index] = int(observation.revision)
                    opens_known[index] = True
                    content_fingerprints[index] = fingerprint
                    last_receipt = observation.receipt_time.timestamp()
                    applied += 1
                    if observation.commit_order is not None:
                        state = replace(
                            state,
                            consumed_input_watermark=state.consumed_input_watermark.advanced_to(
                                history_epoch=observation.commit_order.history_epoch,
                                local_sequence=observation.commit_order.local_sequence,
                            ),
                        )
                    continue

        expected = (
            None
            if not closes or first_epoch is None
            else first_epoch + state.interval_seconds * len(closes)
        )
        if expected is not None and epoch < expected:
            # Already left the retained window, or stale relative to tip.
            ignored += 1
            continue
        if expected is not None and epoch > expected:
            missing = (epoch - expected) // state.interval_seconds
            gap_detected = True
            gap_intervals += int(missing)
            last_gap_epoch = expected
            gap_resets += 1
            (
                opens,
                closes,
                highs,
                lows,
                volumes,
                revisions,
                opens_known,
                content_fingerprints,
            ) = _clear_series()
            first_epoch = None
            persistence = 0

        if not closes:
            first_epoch = epoch
        opens.append(float(observation.values["open"]))
        closes.append(float(observation.values["close"]))
        highs.append(float(observation.values["high"]))
        lows.append(float(observation.values["low"]))
        volumes.append(float(observation.values["volume"]))
        revisions.append(int(observation.revision))
        opens_known.append(True)
        content_fingerprints.append(fingerprint)
        persistence += 1
        applied += 1
        last_receipt = observation.receipt_time.timestamp()
        if observation.commit_order is not None:
            state = replace(
                state,
                consumed_input_watermark=state.consumed_input_watermark.advanced_to(
                    history_epoch=observation.commit_order.history_epoch,
                    local_sequence=observation.commit_order.local_sequence,
                ),
            )

        overflow = len(closes) - FEATURE_WINDOW_INTERVALS
        if overflow > 0:
            del opens[:overflow]
            del closes[:overflow]
            del highs[:overflow]
            del lows[:overflow]
            del volumes[:overflow]
            del revisions[:overflow]
            del opens_known[:overflow]
            del content_fingerprints[:overflow]
            first_epoch = (first_epoch or epoch) + state.interval_seconds * overflow

    advanced = replace(
        state,
        opens=tuple(opens),
        closes=tuple(closes),
        highs=tuple(highs),
        lows=tuple(lows),
        volumes=tuple(volumes),
        revisions=tuple(revisions),
        opens_known=tuple(opens_known),
        content_fingerprints=tuple(content_fingerprints),
        first_interval_epoch=first_epoch,
        last_receipt_epoch=last_receipt,
        persistence_intervals=persistence,
        gap_resets=gap_resets,
        last_gap_epoch=last_gap_epoch,
        opens_retained=all(opens_known) if closes else True,
    )
    advanced = replace(advanced, restart_state=_resolve_restart_state(advanced))
    return AdvanceResult(
        state=advanced,
        applied=applied,
        ignored_stale=ignored,
        gap_detected=gap_detected,
        gap_intervals=gap_intervals,
    )


def advance_from_alignment(
    state: RollingState, alignment: AlignmentResult
) -> AdvanceResult:
    """Fold the contiguous tail of an alignment result into rolling state."""
    return advance_state(state, contiguous_tail(alignment))


def to_checkpoint(
    state: RollingState,
    *,
    created_at_utc: datetime | None = None,
) -> FeatureStateCheckpoint:
    """Serialize resumable state.

    ``ema_fast`` and ``window_complete`` are diagnostic summary only, matching
    the v1.2 fixture layout. Resume recomputes from the retained series and
    ignores them, so a stale summary can never poison a resumed feature value.
    """
    rolling: dict[str, Any] = {
        "opens": list(state.opens),
        "opens_known": list(state.opens_known),
        "opens_retained": state.opens_retained,
        "closes": list(state.closes),
        "highs": list(state.highs),
        "lows": list(state.lows),
        "volumes": list(state.volumes),
        "revisions": list(state.revisions),
        "content_fingerprints": list(state.content_fingerprints),
        "venue": state.venue,
        "first_interval_epoch": state.first_interval_epoch,
        "last_receipt_epoch": state.last_receipt_epoch,
        "interval_seconds": state.interval_seconds,
        "interval_count": state.interval_count,
        "persistence_intervals": state.persistence_intervals,
        "gap_resets": state.gap_resets,
        "last_gap_epoch": state.last_gap_epoch,
        "window_complete": state.window_complete,
        "ema_fast": safe_ema(state.closes, 9),
    }
    return FeatureStateCheckpoint(
        instrument_version_id=state.instrument_version_id,
        venue_instrument_id=state.venue_instrument_id,
        feature_version=state.feature_version,
        consumed_input_watermark=state.consumed_input_watermark,
        rolling_state=rolling,
        restart_state=state.restart_state,
        reconstruction_dependencies=(
            AGGREGATE_DEPENDENCY,
            f"retained_intervals:{FEATURE_WINDOW_INTERVALS}",
        ),
        created_at_utc=created_at_utc,
    )


def from_checkpoint(checkpoint: FeatureStateCheckpoint) -> RollingState:
    """Restore rolling state. The result is explicitly a resumed state."""
    rolling = dict(checkpoint.rolling_state)
    closes = tuple(float(value) for value in rolling.get("closes") or ())
    raw_opens = rolling.get("opens")
    raw_known = rolling.get("opens_known")
    if raw_opens is None:
        opens = closes
        opens_known = tuple(False for _ in closes)
    else:
        opens = tuple(float(value) for value in raw_opens)
        if raw_known is None:
            # Pre-opens_known checkpoint: trust only if legacy flag said retained.
            trusted = bool(rolling.get("opens_retained", False))
            opens_known = tuple(trusted for _ in closes)
        else:
            opens_known = tuple(bool(value) for value in raw_known)
    highs = tuple(float(value) for value in rolling.get("highs") or ())
    lows = tuple(float(value) for value in rolling.get("lows") or ())
    volumes = tuple(float(value) for value in rolling.get("volumes") or ())
    raw_revisions = rolling.get("revisions")
    if raw_revisions is None:
        revisions = tuple(1 for _ in closes)
    else:
        revisions = tuple(int(value) for value in raw_revisions)
    if len(revisions) != len(closes):
        raise ValueError("checkpoint revisions length mismatch")
    raw_fps = rolling.get("content_fingerprints")
    if raw_fps is None:
        content_fingerprints = tuple("" for _ in closes)
    else:
        content_fingerprints = tuple(str(value) for value in raw_fps)
    venue = str(rolling.get("venue") or "").strip()
    if not venue:
        raise ValueError("checkpoint rolling_state.venue is required")
    first_epoch = rolling.get("first_interval_epoch")
    last_receipt = rolling.get("last_receipt_epoch")
    state = RollingState(
        instrument_version_id=checkpoint.instrument_version_id,
        venue=venue,
        venue_instrument_id=checkpoint.venue_instrument_id,
        feature_version=checkpoint.feature_version,
        opens=opens,
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        revisions=revisions,
        opens_known=opens_known,
        content_fingerprints=content_fingerprints,
        first_interval_epoch=int(first_epoch) if first_epoch is not None else None,
        last_receipt_epoch=float(last_receipt) if last_receipt is not None else None,
        persistence_intervals=int(rolling.get("persistence_intervals") or 0),
        gap_resets=int(rolling.get("gap_resets") or 0),
        last_gap_epoch=(
            int(rolling["last_gap_epoch"])
            if rolling.get("last_gap_epoch") is not None
            else None
        ),
        resumed_from_checkpoint=True,
        opens_retained=all(opens_known) if closes else True,
        consumed_input_watermark=checkpoint.consumed_input_watermark,
        interval_seconds=int(
            rolling.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS
        ),
    )
    return replace(state, restart_state=_resolve_restart_state(state))


def alignment_from_state(
    state: RollingState,
    *,
    source_label: str = "feature_state_checkpoint",
) -> AlignmentResult:
    """Rebuild an alignment view from retained state, for replay comparison.

    The rebuilt observations carry synthetic receipt times derived from each
    interval's close, because receipt provenance is per-fetch evidence that a
    checkpoint deliberately does not retain. Only rolling features are
    comparable across this boundary; freshness features are not.
    """
    observations: list[Observation] = []
    for index in range(state.interval_count):
        start = state.interval_start_at(index)
        end = start + timedelta(seconds=state.interval_seconds)
        revision = int(state.revisions[index])
        supersedes = (
            f"retained-prior:{state.instrument_version_id}:{int(start.timestamp())}"
            f":r{revision - 1}"
            if revision > 1
            else None
        )
        observations.append(
            Observation(
                instrument_version_id=state.instrument_version_id,
                venue=state.venue,
                venue_instrument_id=state.venue_instrument_id,
                source_event_time=start,
                receipt_time=end,
                ingestion_order=index + 1,
                payload_kind=PayloadKind.FIXED_INTERVAL_AGGREGATE,
                aggregate_interval_seconds=state.interval_seconds,
                revision=revision,
                supersedes=supersedes,
                values={
                    "open": state.opens[index],
                    "high": state.highs[index],
                    "low": state.lows[index],
                    "close": state.closes[index],
                    "volume": state.volumes[index],
                },
                coverage=CoverageState.COMPLETE,
                provenance={"source": source_label},
            )
        )
    return AlignmentResult(
        observations=tuple(observations),
        expected_intervals=len(observations),
    )


def restart_disposition(state: RollingState) -> dict[str, Any]:
    """Durable record of why this state is or is not warm."""
    return {
        "instrument_version_id": state.instrument_version_id,
        "venue": state.venue,
        "feature_version": state.feature_version,
        "restart_state": state.restart_state.value,
        "resumed_from_checkpoint": state.resumed_from_checkpoint,
        "interval_count": state.interval_count,
        "persistence_intervals": state.persistence_intervals,
        "gap_resets": state.gap_resets,
        "window_complete": state.window_complete,
        "minimum_warmup_intervals": MINIMUM_WARMUP_INTERVALS,
        "consumed_input_watermark": state.consumed_input_watermark.to_dict(),
    }


def checkpoint_payload_bytes(checkpoint: FeatureStateCheckpoint) -> int:
    """Serialized size, so the writer's payload bound is a tested property."""
    return len(canonical_json_bytes(checkpoint.to_dict()))


__all__ = [
    "AGGREGATE_DEPENDENCY",
    "AdvanceResult",
    "RollingState",
    "advance_from_alignment",
    "advance_state",
    "alignment_from_state",
    "checkpoint_payload_bytes",
    "from_checkpoint",
    "initial_state",
    "restart_disposition",
    "revise_against_retained",
    "to_checkpoint",
]
