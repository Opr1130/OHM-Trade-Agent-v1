"""End-to-end feature-bus cycle: source to canonical evidence (PR3 slice G).

This wires the slices together for one instrument and one evaluation cutoff:

    observations -> grid alignment -> rolling state -> FeatureSnapshot
                 -> FeatureStateCheckpoint -> canonical writer

Persistence is off by default. With capture disabled the cycle still computes
and returns everything, which is what makes a dry run meaningful: the evidence
is produced and inspectable without being written anywhere.

Canonical-first rule: when the publisher is enabled and observations are to be
persisted, every observation consumed by the candidate state must commit
(OK / DUPLICATE_OK with a writer watermark) before RollingState is promoted or
snapshot/checkpoint/restart evidence is published. Dependent writes then commit
in order: coverage gaps, snapshot, required restart, and only then checkpoint.
A resumable checkpoint must never become durable before those earlier gates
succeed; otherwise prior state is retained and the disposition names the
deferred dependent evidence.

Committed observation revisions are retained in a RevisionLedger even when
feature-state promotion is deferred, so a later correction cannot reuse a
durable observation_id.

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
from app.opip.features.engine import FEATURE_VERSION, build_feature_snapshot
from app.opip.features.publisher import FeatureBusPublisher, PublishOutcome
from app.opip.features.revision_ledger import RevisionLedger
from app.opip.features.state import (
    RollingState,
    advance_state,
    alignment_from_state,
    initial_state,
    plan_revisions,
    restart_disposition,
    to_checkpoint,
)
from app.opip.market.aggregates import (
    DEFAULT_INTERVAL_SECONDS,
    AlignmentResult,
    align_minute_observations,
)

DISPOSITION_OK = "OK"
DISPOSITION_DRY_RUN = "DRY_RUN"
DISPOSITION_DEFERRED_UNCOMMITTED = "DEFERRED_UNCOMMITTED_OBSERVATIONS"
DISPOSITION_DEFERRED_DEPENDENT = "DEFERRED_UNCOMMITTED_SNAPSHOT_OR_CHECKPOINT"


class CycleIdentityMismatch(ValueError):
    """Observations or retained state do not belong to the requested version."""


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
    disposition: str = DISPOSITION_OK
    promoted: bool = True
    revision_ledger: RevisionLedger | None = None

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
            "disposition": self.disposition,
            "promoted": self.promoted,
            "revision_ledger_entries": (
                len(self.revision_ledger.entries) if self.revision_ledger else 0
            ),
            "outcomes": [
                {"event_type": item.event_type, "status": item.status}
                for item in self.outcomes
            ],
        }


def _assert_cycle_identity(
    observations: Sequence[Observation],
    *,
    instrument_version: InstrumentVersion,
    state: RollingState | None,
    interval_seconds: int,
) -> None:
    expected_id = instrument_version.instrument_version_id
    expected_venue = instrument_version.venue
    expected_venue_instrument = instrument_version.venue_instrument_id
    for observation in observations:
        if observation.instrument_version_id != expected_id:
            raise CycleIdentityMismatch(
                f"observation instrument_version_id "
                f"{observation.instrument_version_id!r} != {expected_id!r}"
            )
        if observation.venue != expected_venue:
            raise CycleIdentityMismatch(
                f"observation venue {observation.venue!r} != {expected_venue!r}"
            )
        if observation.venue_instrument_id != expected_venue_instrument:
            raise CycleIdentityMismatch(
                f"observation venue_instrument_id "
                f"{observation.venue_instrument_id!r} != {expected_venue_instrument!r}"
            )
    if state is None:
        return
    if state.instrument_version_id != expected_id:
        raise CycleIdentityMismatch(
            f"state instrument_version_id "
            f"{state.instrument_version_id!r} != {expected_id!r}"
        )
    if state.venue != expected_venue:
        raise CycleIdentityMismatch(
            f"state venue {state.venue!r} != {expected_venue!r}"
        )
    if state.venue_instrument_id != expected_venue_instrument:
        raise CycleIdentityMismatch(
            f"state venue_instrument_id "
            f"{state.venue_instrument_id!r} != {expected_venue_instrument!r}"
        )
    if state.feature_version != FEATURE_VERSION:
        raise CycleIdentityMismatch(
            f"state feature_version {state.feature_version!r} != {FEATURE_VERSION!r}"
        )
    if int(state.interval_seconds) != int(interval_seconds):
        raise CycleIdentityMismatch(
            f"state interval_seconds {state.interval_seconds!r} != {interval_seconds!r}"
        )


def _advance_watermark_from_outcomes(
    state: RollingState, outcomes: Sequence[PublishOutcome]
) -> RollingState:
    """Fold committed observation publish watermarks into retained state."""
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


def _observations_fully_committed(
    observations: Sequence[Observation],
    outcomes: Sequence[PublishOutcome],
) -> bool:
    """Every consumed observation must commit with a canonical watermark."""
    if len(outcomes) != len(observations):
        return False
    return all(
        outcome.committed and outcome.watermark is not None for outcome in outcomes
    )


def _build_snapshot_and_checkpoint(
    *,
    state: RollingState,
    alignment: AlignmentResult,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    source_version: str,
) -> tuple[FeatureSnapshot, FeatureStateCheckpoint]:
    snapshot = build_feature_snapshot(
        alignment_from_state(state),
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=state.consumed_input_watermark,
        restart_state=state.restart_state,
        source_version=source_version,
        freshness_alignment=alignment,
    )
    checkpoint = to_checkpoint(state, created_at_utc=evaluated_at_utc)
    return snapshot, checkpoint


def run_cycle(
    observations: Sequence[Observation],
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    state: RollingState | None = None,
    revision_ledger: RevisionLedger | None = None,
    publisher: FeatureBusPublisher | None = None,
    source_version: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    window_start: datetime | None = None,
    publish_observations: bool = True,
    source_coverage: CoverageState | None = None,
) -> CycleResult:
    """Run one evaluation for one instrument.

    Candidate state is always computed. Promotion and dependent canonical
    publication require every observation to commit when capture is enabled.
    Snapshot and checkpoint must also commit before promotion is claimed.
    """
    _assert_cycle_identity(
        observations,
        instrument_version=instrument_version,
        state=state,
        interval_seconds=interval_seconds,
    )
    previous = state or initial_state(
        instrument_version, interval_seconds=interval_seconds
    )
    ledger = revision_ledger or RevisionLedger.empty(
        instrument_version, interval_seconds=interval_seconds
    )
    if ledger.instrument_version_id != instrument_version.instrument_version_id:
        raise CycleIdentityMismatch(
            f"revision_ledger instrument_version_id "
            f"{ledger.instrument_version_id!r} != "
            f"{instrument_version.instrument_version_id!r}"
        )
    capture_enabled = publisher is not None and publisher.enabled
    if capture_enabled and not publish_observations:
        raise ValueError(
            "publish_observations=False is not allowed when capture is enabled; "
            "PR3 refuses unverified observation watermarks for dependent promotion"
        )
    plan = plan_revisions(observations, previous, ledger=ledger)
    alignment = align_minute_observations(
        plan.evidence,
        cutoff=evaluation_cutoff,
        interval_seconds=interval_seconds,
        window_start=window_start,
        coverage_only=plan.coverage_only,
    )
    if source_coverage is CoverageState.INCOMPLETE_COVERAGE:
        alignment = replace(alignment, source_incomplete=True)
    advance = advance_state(previous, alignment.observations)
    candidate = advance.state

    publish_set = tuple(
        item
        for item in alignment.observations
        if item.observation_id not in plan.already_committed_ids
    )

    outcomes: list[PublishOutcome] = []
    must_commit_observations = (
        capture_enabled and publish_observations and bool(publish_set)
    )

    observation_outcomes: list[PublishOutcome] = []
    if publisher is not None and publish_observations and publish_set:
        observation_outcomes = publisher.publish_observations(publish_set)
        outcomes.extend(observation_outcomes)

    active_ledger = ledger.with_committed(publish_set, observation_outcomes)
    active_ledger = active_ledger.pruned_to(candidate.first_interval_epoch)

    observations_committed = (
        True
        if not must_commit_observations
        else _observations_fully_committed(publish_set, observation_outcomes)
    )

    if must_commit_observations and not observations_committed:
        snapshot, checkpoint = _build_snapshot_and_checkpoint(
            state=candidate,
            alignment=alignment,
            instrument_version=instrument_version,
            evaluation_cutoff=evaluation_cutoff,
            evaluated_at_utc=evaluated_at_utc,
            source_version=source_version,
        )
        return CycleResult(
            instrument_version=instrument_version,
            alignment=alignment,
            state=previous,
            snapshot=snapshot,
            checkpoint=checkpoint,
            gap_detected=advance.gap_detected or bool(alignment.gaps),
            restart_recorded=False,
            outcomes=tuple(outcomes),
            disposition=DISPOSITION_DEFERRED_UNCOMMITTED,
            promoted=False,
            revision_ledger=active_ledger,
        )

    watermarked = candidate
    if capture_enabled and publish_observations and observation_outcomes:
        watermarked = _advance_watermark_from_outcomes(
            candidate, observation_outcomes
        )

    snapshot, checkpoint = _build_snapshot_and_checkpoint(
        state=watermarked,
        alignment=alignment,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        source_version=source_version,
    )

    restart_recorded = False
    if capture_enabled:
        # Publish order is fail-closed: never make a resumable checkpoint durable
        # until every other required write for this cycle has committed.
        def _deferred_dependent(*, restart_ok: bool = False) -> CycleResult:
            return CycleResult(
                instrument_version=instrument_version,
                alignment=alignment,
                state=previous,
                snapshot=snapshot,
                checkpoint=checkpoint,
                gap_detected=advance.gap_detected or bool(alignment.gaps),
                restart_recorded=restart_ok,
                outcomes=tuple(outcomes),
                disposition=DISPOSITION_DEFERRED_DEPENDENT,
                promoted=False,
                # Observation revisions already durable must survive deferral.
                revision_ledger=active_ledger,
            )

        if alignment.gaps:
            gap_outcomes = publisher.publish_coverage_gaps(
                alignment.gaps,
                instrument_version_id=instrument_version.instrument_version_id,
                venue_instrument_id=instrument_version.venue_instrument_id,
                detected_at_utc=evaluated_at_utc,
            )
            outcomes.extend(gap_outcomes)
            if not all(item.committed for item in gap_outcomes):
                return _deferred_dependent()
        snapshot_outcome = publisher.publish_snapshot(snapshot)
        outcomes.append(snapshot_outcome)
        if not snapshot_outcome.committed:
            return _deferred_dependent()
        restart_required = (
            watermarked.restart_state is not RestartState.WARM
            or advance.gap_detected
        )
        if restart_required:
            restart_outcome = publisher.publish_restart(
                restart_disposition(watermarked),
                watermark=watermarked.consumed_input_watermark,
                recorded_at_utc=evaluated_at_utc,
            )
            outcomes.append(restart_outcome)
            if not restart_outcome.committed:
                return _deferred_dependent()
            restart_recorded = True
        checkpoint_outcome = publisher.publish_checkpoint(checkpoint)
        outcomes.append(checkpoint_outcome)
        if not checkpoint_outcome.committed:
            return _deferred_dependent(restart_ok=restart_recorded)

    disposition = DISPOSITION_DRY_RUN if not capture_enabled else DISPOSITION_OK
    return CycleResult(
        instrument_version=instrument_version,
        alignment=alignment,
        state=watermarked,
        snapshot=snapshot,
        checkpoint=checkpoint,
        gap_detected=advance.gap_detected or bool(alignment.gaps),
        restart_recorded=restart_recorded,
        outcomes=tuple(outcomes),
        disposition=disposition,
        promoted=True,
        revision_ledger=active_ledger,
    )


__all__ = [
    "CycleIdentityMismatch",
    "CycleResult",
    "DISPOSITION_DEFERRED_DEPENDENT",
    "DISPOSITION_DEFERRED_UNCOMMITTED",
    "DISPOSITION_DRY_RUN",
    "DISPOSITION_OK",
    "run_cycle",
]
