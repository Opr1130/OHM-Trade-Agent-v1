"""Replay and reconstruction proofs for the feature bus (PR3 slice G).

Two claims are made testable here rather than asserted in prose:

1. **Reconstruction.** Checkpoint plus retained aggregate deltas produces the
   same supported rolling feature state as uninterrupted processing.
2. **Detector replay preparation.** A persisted ``FeatureSnapshot`` reproduces
   the exact detector input, byte for byte, so a future detector evaluation is
   reproducible from evidence alone.

Freshness features (coverage, lateness, staleness) are per-fetch facts and are
deliberately excluded from equivalence: a checkpoint does not retain receipt
provenance, and pretending otherwise would make the proof meaningless.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from app.opip.contracts.features import FeatureSnapshot, FeatureStateCheckpoint
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.observation import Observation
from app.opip.contracts.serialization import canonical_json_bytes, stable_hash
from app.opip.features.engine import (
    FRESHNESS_FEATURE_NAMES,
    ROLLING_FEATURE_NAMES,
    compute_features,
)
from app.opip.features.state import (
    RollingState,
    advance_state,
    alignment_from_state,
    from_checkpoint,
)
from app.opip.market.aggregates import AlignmentResult


def reconstruct_state(
    checkpoint: FeatureStateCheckpoint,
    deltas: Iterable[Observation] = (),
) -> RollingState:
    """Resume from a checkpoint and apply retained aggregate deltas."""
    resumed = from_checkpoint(checkpoint)
    return advance_state(resumed, deltas).state


@dataclass(frozen=True)
class EquivalenceResult:
    """Whether resumed state reproduces uninterrupted state, and where not."""

    compared: tuple[str, ...]
    mismatches: Mapping[str, tuple[Any, Any]]
    excluded: tuple[str, ...] = FRESHNESS_FEATURE_NAMES

    @property
    def equivalent(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "equivalent": self.equivalent,
            "compared_features": len(self.compared),
            "mismatches": {
                name: {"uninterrupted": pair[0], "resumed": pair[1]}
                for name, pair in self.mismatches.items()
            },
            "excluded_features": list(self.excluded),
        }


def rolling_feature_values(
    alignment: AlignmentResult,
    *,
    instrument_version: InstrumentVersion,
    evaluated_at_utc: datetime,
) -> dict[str, Any]:
    computation = compute_features(
        alignment,
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
    )
    return {name: computation.values[name] for name in ROLLING_FEATURE_NAMES}


def compare_resumed_state(
    *,
    uninterrupted: AlignmentResult,
    resumed_state: RollingState,
    instrument_version: InstrumentVersion,
    evaluated_at_utc: datetime,
) -> EquivalenceResult:
    """Exact comparison. There is no tolerance knob to loosen."""
    live = rolling_feature_values(
        uninterrupted,
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
    )
    replayed = rolling_feature_values(
        alignment_from_state(resumed_state),
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
    )
    mismatches = {
        name: (live[name], replayed[name])
        for name in ROLLING_FEATURE_NAMES
        if live[name] != replayed[name]
    }
    return EquivalenceResult(
        compared=ROLLING_FEATURE_NAMES, mismatches=mismatches
    )


def detector_replay_input(snapshot: FeatureSnapshot) -> dict[str, Any]:
    """Exactly what a detector would receive from this snapshot.

    Nothing beyond the snapshot is consulted. If a future detector needs a fact
    that is not here, that is a contract gap to fix, not something to read from
    a live client at evaluation time.
    """
    return {
        "snapshot_id": snapshot.snapshot_id,
        "instrument_version_id": snapshot.instrument_version_id,
        "feature_version": snapshot.feature_version,
        "feature_dag_hash": snapshot.feature_dag_hash,
        "evaluation_cutoff": snapshot.to_dict()["evaluation_cutoff"],
        "evaluation_grid_seconds": snapshot.evaluation_grid_seconds,
        "consumed_input_watermark": snapshot.consumed_input_watermark.to_dict(),
        "coverage": snapshot.coverage.value,
        "restart_state": snapshot.restart_state.value,
        "missingness": {
            key: value.value for key, value in snapshot.missingness.items()
        },
        "values": dict(snapshot.values),
    }


def detector_input_fingerprint(snapshot: FeatureSnapshot) -> str:
    return stable_hash("DETIN", detector_replay_input(snapshot))


def snapshot_bytes(snapshot: FeatureSnapshot) -> int:
    return len(canonical_json_bytes(snapshot.to_dict()))


def assert_snapshot_determinism(snapshots: Sequence[FeatureSnapshot]) -> None:
    """All snapshots built from identical evidence must be byte-identical."""
    if len(snapshots) < 2:
        return
    reference = snapshots[0]
    expected_bytes = canonical_json_bytes(reference.to_dict())
    expected_id = reference.snapshot_id
    expected_hash = reference.content_hash()
    expected_input = detector_input_fingerprint(reference)
    for candidate in snapshots[1:]:
        if candidate.snapshot_id != expected_id:
            raise AssertionError("snapshot_id is not deterministic")
        if candidate.content_hash() != expected_hash:
            raise AssertionError("snapshot content hash is not deterministic")
        if detector_input_fingerprint(candidate) != expected_input:
            raise AssertionError("detector replay input is not deterministic")
        if canonical_json_bytes(candidate.to_dict()) != expected_bytes:
            raise AssertionError("canonical snapshot bytes are not identical")


__all__ = [
    "EquivalenceResult",
    "assert_snapshot_determinism",
    "compare_resumed_state",
    "detector_input_fingerprint",
    "detector_replay_input",
    "reconstruct_state",
    "rolling_feature_values",
    "snapshot_bytes",
]
