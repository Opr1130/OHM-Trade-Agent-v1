"""End-to-end feature-bus cycle: source to canonical evidence (PR3 slice G).

This wires the slices together for one instrument and one evaluation cutoff:

    observations -> grid alignment -> rolling state -> FeatureSnapshot
                 -> FeatureStateCheckpoint -> canonical writer

Persistence is off by default. With capture disabled the cycle still computes
and returns everything, which is what makes a dry run meaningful: the evidence
is produced and inspectable without being written anywhere.

Nothing here schedules itself, ranks candidates, emits alerts, or evaluates a
detector.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Sequence

from app.opip.contracts.enums import CoverageState, RestartState
from app.opip.contracts.features import FeatureSnapshot, FeatureStateCheckpoint
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.observation import Observation
from app.opip.features.engine import build_feature_snapshot
from app.opip.features.publisher import FeatureBusPublisher, PublishOutcome
from app.opip.features.state import (
    RollingState,
    advance_state,
    alignment_from_state,
    initial_state,
    restart_disposition,
    to_checkpoint,
)
from app.opip.market.aggregates import (
    DEFAULT_INTERVAL_SECONDS,
    AlignmentResult,
    align_minute_observations,
)


@dataclass(frozen=True)
class CycleResult:
    """Everything one evaluation produced, whether or not it was persisted."""

    instrument_version: InstrumentVersion
    alignment: AlignmentResult
    state: RollingState
    snapshot: FeatureSnapshot
    checkpoint: FeatureStateCheckpoint
    gap_detected: bool = False
    restart_recorded: bool = False
    outcomes: tuple[PublishOutcome, ...] = field(default_factory=tuple)

    @property
    def coverage(self) -> CoverageState:
        return self.alignment.coverage

    @property
    def persisted(self) -> bool:
        return any(outcome.committed for outcome in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_version_id": self.instrument_version.instrument_version_id,
            "snapshot_id": self.snapshot.snapshot_id,
            "checkpoint_id": self.checkpoint.checkpoint_id,
            "coverage": self.coverage.value,
            "restart_state": self.snapshot.restart_state.value,
            "gap_detected": self.gap_detected,
            "restart_recorded": self.restart_recorded,
            "intervals": self.state.interval_count,
            "persisted": self.persisted,
            "outcomes": [
                {"event_type": item.event_type, "status": item.status}
                for item in self.outcomes
            ],
        }


def _advance_watermark_from_outcomes(
    state: RollingState, outcomes: Sequence[PublishOutcome]
) -> RollingState:
    """Fold committed observation publish watermarks into retained state.

    Live observations do not carry commit_order until the canonical writer
    acks them. Snapshot and checkpoint identity must advance from those acks,
    or every cycle collapses onto watermark 0-0 and collides on idempotency.
    """
    watermark = state.consumed_input_watermark
    for outcome in outcomes:
        if not outcome.committed or outcome.watermark is None:
            continue
        watermark = watermark.advanced_to(
            history_epoch=outcome.watermark.history_epoch,
            local_sequence=outcome.watermark.local_sequence,
        )
    if watermark == state.consumed_input_watermark:
        return state
    return replace(state, consumed_input_watermark=watermark)


def run_cycle(
    observations: Sequence[Observation],
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    state: RollingState | None = None,
    publisher: FeatureBusPublisher | None = None,
    source_version: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    window_start: datetime | None = None,
    publish_observations: bool = True,
) -> CycleResult:
    """Run one evaluation for one instrument.

    Order matters: observations are persisted before the snapshot that consumed
    them, so canonical evidence never references inputs it does not contain.
    Rolling features are computed from retained state after advance, not from
    the current fetch batch alone, so resume/incremental cycles stay warm.
    """
    alignment = align_minute_observations(
        observations,
        cutoff=evaluation_cutoff,
        interval_seconds=interval_seconds,
        window_start=window_start,
    )
    previous = state or initial_state(
        instrument_version, interval_seconds=interval_seconds
    )
    advance = advance_state(previous, alignment.observations)
    advanced = advance.state

    outcomes: list[PublishOutcome] = []
    if publisher is not None and publish_observations:
        observation_outcomes = publisher.publish_observations(alignment.observations)
        outcomes.extend(observation_outcomes)
        advanced = _advance_watermark_from_outcomes(advanced, observation_outcomes)

    # Rolling series from retained state; this cycle's alignment for freshness.
    snapshot = build_feature_snapshot(
        alignment_from_state(advanced),
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=advanced.consumed_input_watermark,
        restart_state=advanced.restart_state,
        source_version=source_version,
        freshness_alignment=alignment,
    )
    checkpoint = to_checkpoint(advanced, created_at_utc=evaluated_at_utc)

    restart_recorded = False
    if publisher is not None:
        if alignment.gaps:
            outcomes.extend(
                publisher.publish_coverage_gaps(
                    alignment.gaps,
                    instrument_version_id=instrument_version.instrument_version_id,
                    venue_instrument_id=instrument_version.venue_instrument_id,
                    detected_at_utc=evaluated_at_utc,
                )
            )
        outcomes.append(publisher.publish_snapshot(snapshot))
        outcomes.append(publisher.publish_checkpoint(checkpoint))
        if advanced.restart_state is not RestartState.WARM or advance.gap_detected:
            outcomes.append(
                publisher.publish_restart(
                    restart_disposition(advanced),
                    watermark=advanced.consumed_input_watermark,
                    recorded_at_utc=evaluated_at_utc,
                )
            )
            restart_recorded = True

    return CycleResult(
        instrument_version=instrument_version,
        alignment=alignment,
        state=advanced,
        snapshot=snapshot,
        checkpoint=checkpoint,
        gap_detected=advance.gap_detected or bool(alignment.gaps),
        restart_recorded=restart_recorded,
        outcomes=tuple(outcomes),
    )


__all__ = ["CycleResult", "run_cycle"]
