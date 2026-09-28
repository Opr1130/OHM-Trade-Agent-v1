"""R2 shadow parity and deterministic replay.

This module proves the existing feature bus on frozen observation evidence.
It does not compute a second set of indicators, publish canonical events,
read ``OPIP_FEATURE_BUS_MODE``, or admit anything to ranking, risk, paper,
or alerts.

Identity stays on the existing contracts: ``Observation.to_dict`` is the
captured input, ``FeatureSnapshot.snapshot_id`` / ``feature_version`` identify
the output, and ``source_evidence_identity`` only names the input payload the
report was computed from.

Replay is fail-closed about admissibility. It refuses evidence that is not this
instrument's, that was not visible at the replay instant, that carries a
post-cutoff misaligned fact capable of moving coverage, that conflicts with its
own captured identity, or that is not covered by the declared consumed-input
watermark.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence

from app.opip.contracts.enums import CoverageState, PayloadKind, RestartState
from app.opip.contracts.features import (
    FEATURE_BUS_SCHEMA_VERSION,
    FEATURE_CHECKPOINT_RECORD_TYPE,
    FeatureSnapshot,
    FeatureStateCheckpoint,
)
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.observation import (
    AGGREGATE_REQUIRED_KEYS,
    OBSERVATION_RECORD_TYPE,
    OBSERVATION_SCHEMA_VERSION,
    Observation,
)
from app.opip.contracts.serialization import canonical_json_bytes, iso_z, stable_hash
from app.opip.contracts.temporal import (
    TemporalIntegrityError,
    assert_point_in_time,
    require_utc,
)
from app.opip.features.checkpoint_store import checkpoint_from_payload
from app.opip.features.engine import (
    ATR_PERIOD,
    BANDWIDTH_PERIOD,
    FEATURE_NAMES,
    FEATURE_VERSION,
    FEATURE_WINDOW_INTERVALS,
    PERCENTILE_LOOKBACK_INTERVALS,
    VALUE_PRECISION,
    build_feature_snapshot,
)
from app.opip.features.indicators import (
    safe_atr_percentage_series,
    safe_bandwidth_series,
    safe_percentile_rank,
)
from app.opip.features.parity import (
    ABSOLUTE_TOLERANCE,
    compare_against_production_indicators,
    percentile_definition_divergence,
)
from app.opip.features.pipeline import CycleIdentityMismatch, _assert_cycle_identity
from app.opip.features.replay import assert_snapshot_determinism
from app.opip.features.state import (
    AGGREGATE_DEPENDENCY,
    RollingState,
    advance_state,
    alignment_from_state,
    from_checkpoint,
    initial_state,
    to_checkpoint,
)
from app.opip.market.aggregates import (
    DEFAULT_INTERVAL_SECONDS,
    AlignmentResult,
    align_minute_observations,
    contiguous_tail,
)
from app.opip.market.observations import (
    IntervalRow,
    _validate_row,
    aggregate_content_fingerprint,
)

EXACT_EQUALITY_RULE = "exact_absolute_tolerance_0"
NOT_COMPARABLE_RULE = "not_comparable_legacy_value_absent"

REPLAY_EVIDENCE_RECORD_TYPE = "FeatureBusReplayEvidence"
REPLAY_EVIDENCE_SCHEMA_VERSION = 2

#: A capture either started from nothing or resumed retained history. The two
#: produce different snapshots from identical rows, so the envelope states which
#: it was instead of leaving replay to assume one.
CYCLE_ORIGIN_COLD_START = "cold_start"
CYCLE_ORIGIN_RESUMED = "resumed"
CYCLE_ORIGINS: tuple[str, ...] = (CYCLE_ORIGIN_COLD_START, CYCLE_ORIGIN_RESUMED)

_REPLAY_EVIDENCE_KEYS: tuple[str, ...] = (
    "record_type",
    "schema_version",
    "cycle_origin",
    "prior_state",
    "window_start",
    "source_incomplete",
    "committed_write_watermarks",
    "evidence",
    "coverage_only",
)

#: Exactly ``to_checkpoint``'s ``rolling_state`` payload. The nested durable
#: state is versioned by the same envelope, so an unmodelled field there is
#: producer drift that replay must refuse rather than ignore.
_ROLLING_STATE_KEYS: tuple[str, ...] = (
    "opens",
    "opens_known",
    "opens_retained",
    "closes",
    "highs",
    "lows",
    "volumes",
    "revisions",
    "content_fingerprints",
    "venue",
    "first_interval_epoch",
    "last_receipt_epoch",
    "interval_seconds",
    "interval_count",
    "persistence_intervals",
    "gap_resets",
    "last_gap_epoch",
    "window_complete",
    "ema_fast",
)

_PRIOR_STATE_KEYS: tuple[str, ...] = ("checkpoint", "resumed_from_checkpoint")

#: Exactly ``FeatureStateCheckpoint.to_dict()``. A durable checkpoint that this
#: replay does not fully model must be refused rather than reinterpreted under
#: today's rolling-state assumptions.
_CHECKPOINT_EVIDENCE_KEYS: tuple[str, ...] = (
    "record_type",
    "schema_version",
    "checkpoint_id",
    "feature_version",
    "instrument_version_id",
    "consumed_input_watermark",
    "reconstruction_dependencies",
    "rolling_state",
    "restart_state",
    "venue_instrument_id",
    "created_at_utc",
)

CLASSIFICATIONS: tuple[str, ...] = (
    "MATCH",
    "INTENTIONAL_SEMANTIC_DIFFERENCE",
    "LEGACY_EVIDENCE_UNAVAILABLE",
    "IMPLEMENTATION_DEFECT",
    "ARCHITECTURE_GAP",
)

#: Production indicator functions the bus already calls. Compared by
#: ``compare_against_production_indicators``; this module does not recompute them.
INDICATOR_EQUIVALENT_FEATURES: tuple[str, ...] = (
    "ema_fast_9",
    "ema_slow_21",
    "atr_pct_14",
    "bandwidth_20",
    "relative_volume_20m",
)

#: Bus percentiles use ``app.indicators.technical.percentile_rank``. The scan
#: percentile is a different function and is reported beside it, not folded in.
PERCENTILE_FEATURES: tuple[str, ...] = (
    "atr_pct_percentile",
    "bandwidth_percentile",
)

#: Contract B: book depth and trade tape are not retained on this path.
NOT_RETAINED_FEATURES: tuple[str, ...] = (
    "book_depth_imbalance",
    "trade_tape_intensity",
)

_EVIDENCE_KEYS: tuple[str, ...] = (
    "record_type",
    "schema_version",
    "instrument_version_id",
    "venue",
    "venue_instrument_id",
    "observation_id",
    "source_event_time",
    "receipt_time",
    "ingestion_order",
    "source_sequence",
    "history_epoch",
    "local_sequence",
    "aggregate_interval_seconds",
    "payload_kind",
    "coverage",
    "supersedes",
    "revision",
    "interval_forming",
    "values",
    "provenance",
)


class FeatureVersionMismatch(ValueError):
    """Replay refused because the declared feature version is not this engine."""


class WatermarkIntegrityError(ValueError):
    """Replay refused because the consumed watermark cannot be proven honest."""


class EvidenceIntegrityError(ValueError):
    """Replay refused because a captured row conflicts with its own identity."""


@dataclass(frozen=True)
class ClassifiedParityRow:
    feature_name: str
    legacy_value: Any
    feature_bus_value: Any
    equality_rule: str
    classification: str
    reason: str
    source_evidence_identity: str
    feature_version: str
    legacy_source: str

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(f"unknown parity classification: {self.classification}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "legacy_value": self.legacy_value,
            "feature_bus_value": self.feature_bus_value,
            "equality_rule": self.equality_rule,
            "classification": self.classification,
            "reason": self.reason,
            "source_evidence_identity": self.source_evidence_identity,
            "feature_version": self.feature_version,
            "legacy_source": self.legacy_source,
        }


@dataclass(frozen=True)
class ClassifiedParityReport:
    rows: tuple[ClassifiedParityRow, ...]
    source_evidence_identity: str
    feature_version: str
    snapshot_id: str | None

    def counts(self) -> dict[str, int]:
        totals = dict.fromkeys(CLASSIFICATIONS, 0)
        for row in self.rows:
            totals[row.classification] += 1
        totals["total"] = len(self.rows)
        return totals

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts()
        return {
            "source_evidence_identity": self.source_evidence_identity,
            "feature_version": self.feature_version,
            "snapshot_id": self.snapshot_id,
            "equality_rule": EXACT_EQUALITY_RULE,
            "absolute_tolerance": ABSOLUTE_TOLERANCE,
            "counts": counts,
            "rows": [row.to_dict() for row in self.rows],
            "implementation_defects": [
                row.feature_name
                for row in self.rows
                if row.classification == "IMPLEMENTATION_DEFECT"
            ],
            "architecture_gaps": [
                row.feature_name
                for row in self.rows
                if row.classification == "ARCHITECTURE_GAP"
            ],
            "legacy_evidence_unavailable": [
                row.feature_name
                for row in self.rows
                if row.classification == "LEGACY_EVIDENCE_UNAVAILABLE"
            ],
        }


def capture_observation_evidence(
    observations: Sequence[Observation],
) -> tuple[dict[str, Any], ...]:
    """Stable replayable form of existing observation records.

    The payload is ``Observation.to_dict`` only. Nothing here is a second
    market history.
    """
    return tuple(item.to_dict() for item in observations)


@dataclass(frozen=True)
class ReplayEvidence:
    """One captured cycle: retained state, evidence roles, alignment context.

    ``run_cycle`` does not compute a snapshot from the fetched tuple alone. It
    partitions the fetch into evidence and coverage-only rows, advances the
    prior ``RollingState``, and builds the snapshot from that retained candidate
    state, using the fetch only for freshness. A capture that recorded just one
    flat row tuple could not reproduce a resumed cycle, so this envelope records
    which of the two it was and carries everything the production path needs.

    ``prior_state`` is a ``FeatureStateCheckpoint`` payload — the existing
    durable form of retained state — and is ``None`` only for a declared cold
    start. ``coverage_only`` holds rows the live planner withheld from feature
    evidence; they keep continuity and coverage honest without contributing
    feature values, exactly as in production.

    ``prior_state_resumed_from_checkpoint`` records whether the retained state
    came from durable evidence or was still in process memory. The checkpoint
    contract cannot express the latter, and ``from_checkpoint`` assumes a
    resume, so without it a short in-process cold start would be reclassified.
    A ``FeatureStateCheckpoint`` or a validated checkpoint payload is durable by
    definition and is always captured as a resume; only an in-memory
    ``RollingState`` can be either.
    """

    cycle_origin: str
    evidence: tuple[Mapping[str, Any], ...] = ()
    coverage_only: tuple[Mapping[str, Any], ...] = ()
    prior_state: Mapping[str, Any] | None = None
    prior_state_resumed_from_checkpoint: bool = False
    #: Canonical write positions the cycle's own writes returned, folded into the
    #: sealed consumed-input watermark. Empty for a dry-run capture.
    committed_write_watermarks: tuple[ConsumedInputWatermark, ...] = ()
    window_start: datetime | None = None
    source_incomplete: bool = False

    @property
    def resumed(self) -> bool:
        return self.cycle_origin == CYCLE_ORIGIN_RESUMED

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": REPLAY_EVIDENCE_RECORD_TYPE,
            "schema_version": REPLAY_EVIDENCE_SCHEMA_VERSION,
            "cycle_origin": self.cycle_origin,
            "prior_state": (
                {
                    "checkpoint": dict(self.prior_state),
                    "resumed_from_checkpoint": bool(
                        self.prior_state_resumed_from_checkpoint
                    ),
                }
                if self.prior_state is not None
                else None
            ),
            "window_start": (
                iso_z(self.window_start, field_name="window_start")
                if self.window_start is not None
                else None
            ),
            "source_incomplete": bool(self.source_incomplete),
            "committed_write_watermarks": [
                item.to_dict() for item in self.committed_write_watermarks
            ],
            "evidence": [dict(item) for item in self.evidence],
            "coverage_only": [dict(item) for item in self.coverage_only],
        }


def capture_replay_evidence(
    evidence: Sequence[Observation],
    *,
    prior_state: RollingState | FeatureStateCheckpoint | Mapping[str, Any] | None = None,
    prior_state_created_at_utc: datetime | None = None,
    coverage_only: Sequence[Observation] = (),
    committed_write_watermarks: Sequence[ConsumedInputWatermark] = (),
    window_start: datetime | None = None,
    source_incomplete: bool = False,
) -> ReplayEvidence:
    """Capture everything one replay needs to reconstruct the same cycle.

    The origin is derived from ``prior_state`` rather than declared, so a
    capture cannot claim a cold start while holding retained history.
    ``prior_state_created_at_utc`` is required for an in-memory state and records
    when it was observed; without it replay cannot bound when the retained
    values arrived. ``committed_write_watermarks`` are the canonical positions
    the cycle's own writes returned.
    """
    captured_state: Mapping[str, Any] | None = None
    resumed_from_checkpoint = False
    if isinstance(prior_state, RollingState):
        # Only an in-memory state can be either; its own flag decides.
        if prior_state_created_at_utc is None:
            raise ValueError(
                "capturing an in-memory RollingState requires "
                "prior_state_created_at_utc; retained state without an "
                "observation clock cannot be bounded by replay"
            )
        captured_state = _checkpoint_evidence(
            to_checkpoint(prior_state, created_at_utc=prior_state_created_at_utc).to_dict()
        )
        resumed_from_checkpoint = bool(prior_state.resumed_from_checkpoint)
    elif isinstance(prior_state, FeatureStateCheckpoint):
        captured_state = _checkpoint_evidence(prior_state.to_dict())
        resumed_from_checkpoint = True
    elif isinstance(prior_state, Mapping):
        # A validated checkpoint payload is durable evidence by definition.
        captured_state = _checkpoint_evidence(prior_state)
        resumed_from_checkpoint = True
    elif prior_state is not None:
        raise ValueError(
            "prior_state must be a RollingState, a FeatureStateCheckpoint "
            f"payload, or None (got {type(prior_state).__name__})"
        )
    return ReplayEvidence(
        cycle_origin=(
            CYCLE_ORIGIN_RESUMED if captured_state is not None else CYCLE_ORIGIN_COLD_START
        ),
        evidence=capture_observation_evidence(evidence),
        coverage_only=capture_observation_evidence(coverage_only),
        prior_state=captured_state,
        prior_state_resumed_from_checkpoint=resumed_from_checkpoint,
        committed_write_watermarks=tuple(
            ConsumedInputWatermark(
                history_epoch=int(item.history_epoch),
                local_sequence=int(item.local_sequence),
            )
            for item in committed_write_watermarks
        ),
        window_start=window_start,
        source_incomplete=bool(source_incomplete),
    )


def _checkpoint_evidence(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a captured checkpoint payload before it is reconstructed.

    ``checkpoint_from_payload`` accepts any positive schema version and ignores
    fields it does not model, so a durable checkpoint written by a future
    engine would be silently reinterpreted under today's rolling-state
    assumptions. Replay refuses it instead.
    """
    missing = [key for key in _CHECKPOINT_EVIDENCE_KEYS if key not in payload]
    if missing:
        raise ValueError(
            f"captured retained state missing required keys: {sorted(missing)}"
        )
    unexpected = sorted(set(payload) - set(_CHECKPOINT_EVIDENCE_KEYS))
    if unexpected:
        raise ValueError(
            f"captured retained state has unexpected keys: {unexpected}"
        )
    if payload["record_type"] != FEATURE_CHECKPOINT_RECORD_TYPE:
        raise ValueError(
            "captured retained state record_type is not FeatureStateCheckpoint"
        )
    schema = _require_int(payload["schema_version"], "prior_state.schema_version")
    if schema != FEATURE_BUS_SCHEMA_VERSION:
        raise ValueError(
            "replay refused: captured retained state schema_version "
            f"{schema} is not the supported {FEATURE_BUS_SCHEMA_VERSION}"
        )
    _rolling_state_evidence(payload)
    _assert_retained_ohlcv(payload)
    _assert_reconstruction_dependencies(payload)
    if payload["created_at_utc"] is None:
        raise ValueError(
            "replay refused: captured retained state must declare created_at_utc; "
            "without an observation clock replay cannot bound when its values arrived"
        )
    _parse_time(payload["created_at_utc"], "created_at_utc")
    return dict(payload)


def _assert_retained_ohlcv(payload: Mapping[str, Any]) -> None:
    """Retained slots must satisfy the same source rules as ingested rows.

    ``from_checkpoint`` accepts any numeric series, so a retained negative
    close or a high below its candle body would be sealed into features that the
    source normalization path would have rejected. Each slot is validated with
    ``observations._validate_row`` for the invariants that do not depend on the
    open, and with the retained open when it is known.

    Stored ``content_fingerprints`` are deliberately not recomputed: retained
    state does not carry ``vwap`` or ``trade_count``, which are part of the
    fingerprint input, so a mismatch there would not distinguish corruption from
    a source that simply reported them.
    """
    rolling = payload["rolling_state"]
    opens = list(rolling.get("opens") or ())
    opens_known = list(rolling.get("opens_known") or ())
    highs = list(rolling.get("highs") or ())
    lows = list(rolling.get("lows") or ())
    closes = list(rolling.get("closes") or ())
    volumes = list(rolling.get("volumes") or ())
    first_epoch = rolling.get("first_interval_epoch")
    interval_seconds = rolling.get("interval_seconds")
    if not closes:
        return
    if first_epoch is None or not isinstance(interval_seconds, int):
        raise ValueError(
            "captured retained state needs first_interval_epoch and interval_seconds "
            "before its series can be validated"
        )
    for index, close in enumerate(closes):
        known_open = bool(opens_known[index]) if index < len(opens_known) else False
        open_value = opens[index] if known_open and index < len(opens) else close
        row = IntervalRow(
            interval_start_epoch=int(first_epoch) + int(interval_seconds) * index,
            open=open_value,
            high=highs[index],
            low=lows[index],
            close=close,
            volume=volumes[index],
        )
        reason = _validate_row(row, interval_seconds=int(interval_seconds))
        if reason is not None:
            raise ValueError(
                "replay refused: retained slot "
                f"{index} rejected by source rules: {reason}"
            )


def _assert_reconstruction_dependencies(payload: Mapping[str, Any]) -> None:
    """A checkpoint names what a resume still needs; replay must already have it.

    ``FeatureStateCheckpoint.reconstruction_dependencies`` exists so a
    checkpoint is never treated as self-sufficient when it is not. Replay reads
    the retained series and nothing else, so it can satisfy exactly the set
    ``to_checkpoint`` declares; anything else means the capture is missing
    evidence this engine does not supply, and certifying from it would assert a
    snapshot the durable record says is not reconstructable.
    """
    required = {
        AGGREGATE_DEPENDENCY,
        f"retained_intervals:{FEATURE_WINDOW_INTERVALS}",
    }
    raw = payload["reconstruction_dependencies"]
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValueError(
            "captured retained state reconstruction_dependencies must be a sequence"
        )
    declared = {str(item) for item in raw}
    if declared != required:
        raise ValueError(
            "replay refused: captured retained state requires reconstruction "
            f"evidence {sorted(declared)} that replay does not supply "
            f"({sorted(required)})"
        )


def _rolling_state_evidence(payload: Mapping[str, Any]) -> None:
    """The nested retained series is versioned evidence too, not a bag of keys."""
    rolling = payload.get("rolling_state")
    if not isinstance(rolling, Mapping):
        raise ValueError("captured retained state rolling_state must be an object")
    missing = [key for key in _ROLLING_STATE_KEYS if key not in rolling]
    if missing:
        raise ValueError(
            f"captured retained state rolling_state missing keys: {sorted(missing)}"
        )
    unexpected = sorted(set(rolling) - set(_ROLLING_STATE_KEYS))
    if unexpected:
        raise ValueError(
            f"captured retained state rolling_state has unexpected keys: {unexpected}"
        )


def _write_watermarks_from_evidence(raw: Any) -> tuple[ConsumedInputWatermark, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValueError(
            "replay evidence committed_write_watermarks must be a sequence"
        )
    positions: list[ConsumedInputWatermark] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(
                "replay evidence committed_write_watermarks entries must be objects"
            )
        positions.append(
            ConsumedInputWatermark(
                history_epoch=_require_int(
                    item.get("history_epoch"), "committed_write_watermarks.history_epoch"
                ),
                local_sequence=_require_int(
                    item.get("local_sequence"),
                    "committed_write_watermarks.local_sequence",
                ),
            )
        )
    return tuple(positions)


def load_replay_evidence(
    payload: ReplayEvidence | Mapping[str, Any],
) -> ReplayEvidence:
    """Rebuild captured evidence, refusing anything outside the declared fields."""
    if isinstance(payload, ReplayEvidence):
        return payload
    if not isinstance(payload, Mapping):
        raise ValueError("replay evidence must be a captured evidence object")
    missing = [key for key in _REPLAY_EVIDENCE_KEYS if key not in payload]
    if missing:
        raise ValueError(f"replay evidence missing required keys: {sorted(missing)}")
    unexpected = sorted(set(payload) - set(_REPLAY_EVIDENCE_KEYS))
    if unexpected:
        raise ValueError(f"replay evidence has unexpected keys: {unexpected}")
    if payload["record_type"] != REPLAY_EVIDENCE_RECORD_TYPE:
        raise ValueError("replay evidence record_type is not FeatureBusReplayEvidence")
    schema = _require_int(payload["schema_version"], "schema_version")
    if schema != REPLAY_EVIDENCE_SCHEMA_VERSION:
        raise ValueError(
            "replay refused: captured evidence schema_version "
            f"{schema} is not the supported {REPLAY_EVIDENCE_SCHEMA_VERSION}"
        )
    cycle_origin = payload["cycle_origin"]
    if cycle_origin not in CYCLE_ORIGINS:
        raise ValueError(
            f"replay evidence cycle_origin {cycle_origin!r} is not one of "
            f"{list(CYCLE_ORIGINS)}"
        )
    source_incomplete = _require_bool(payload["source_incomplete"], "source_incomplete")
    raw_window = payload["window_start"]
    window_start = None if raw_window is None else _parse_time(raw_window, "window_start")
    prior_state = _prior_state_from_evidence(
        payload["prior_state"], cycle_origin=cycle_origin
    )
    evidence = _observation_rows(payload["evidence"], "evidence")
    coverage_only = _observation_rows(payload["coverage_only"], "coverage_only")
    committed_writes = _write_watermarks_from_evidence(
        payload["committed_write_watermarks"]
    )
    if cycle_origin == CYCLE_ORIGIN_COLD_START and coverage_only:
        raise ValueError(
            "replay evidence declares a cold start with coverage-only rows; a "
            "cold-start planner has no retained history to keep a re-poll "
            "coverage-only, so the evidence-role context is inconsistent"
        )
    return ReplayEvidence(
        cycle_origin=cycle_origin,
        evidence=evidence,
        coverage_only=coverage_only,
        prior_state=prior_state[0],
        prior_state_resumed_from_checkpoint=prior_state[1],
        committed_write_watermarks=committed_writes,
        window_start=window_start,
        source_incomplete=source_incomplete,
    )


def _prior_state_from_evidence(
    raw: Any, *, cycle_origin: str
) -> tuple[Mapping[str, Any] | None, bool]:
    """Retained state and its provenance, mandatory exactly when resumed."""
    if raw is None:
        if cycle_origin == CYCLE_ORIGIN_RESUMED:
            raise ValueError(
                "replay refused: a resumed cycle requires prior retained state, "
                "and the captured evidence does not carry prior_state"
            )
        return None, False
    if not isinstance(raw, Mapping):
        raise ValueError("replay evidence prior_state must be an object or null")
    missing = [key for key in _PRIOR_STATE_KEYS if key not in raw]
    if missing:
        raise ValueError(
            f"replay evidence prior_state missing required keys: {sorted(missing)}"
        )
    unexpected = sorted(set(raw) - set(_PRIOR_STATE_KEYS))
    if unexpected:
        raise ValueError(
            f"replay evidence prior_state has unexpected keys: {unexpected}"
        )
    if cycle_origin != CYCLE_ORIGIN_RESUMED:
        raise ValueError(
            "replay evidence declares a cold start but carries prior retained "
            "state; the captured cycle origin is inconsistent"
        )
    checkpoint = raw["checkpoint"]
    if not isinstance(checkpoint, Mapping):
        raise ValueError("replay evidence prior_state.checkpoint must be an object")
    resumed_from_checkpoint = _require_bool(
        raw["resumed_from_checkpoint"], "prior_state.resumed_from_checkpoint"
    )
    return _checkpoint_evidence(checkpoint), resumed_from_checkpoint


def _assert_retained_state_availability(
    checkpoint: FeatureStateCheckpoint,
    *,
    evaluated_at_utc: datetime,
) -> None:
    """Retained evidence must have existed at the replay instant.

    The checkpoint's ``created_at_utc`` is the sound bound: a state written at
    that instant contains only facts already received. ``last_receipt_epoch`` is
    checked too, but it is the receipt of the last interval folded in
    *source-time* order, not the maximum receipt across the window, so it is a
    necessary condition rather than a sufficient one — the creation clock is
    what actually closes the gap. ``alignment_from_state`` replaces receipts with
    the interval close, which is why this cannot be checked after alignment.
    """
    created = checkpoint.created_at_utc
    if created is None:
        raise EvidenceIntegrityError(
            "replay refused: captured retained state has no creation clock, so "
            "the availability of its values cannot be bounded"
        )
    if created > evaluated_at_utc:
        raise TemporalIntegrityError(
            "captured retained state was created at "
            f"{iso_z(created, field_name='created_at_utc')}, after "
            f"{iso_z(evaluated_at_utc, field_name='evaluated_at_utc')}; it was "
            "not available at the replay instant"
        )
    last_receipt = checkpoint.rolling_state.get("last_receipt_epoch")
    if last_receipt is None:
        return
    received = datetime.fromtimestamp(float(last_receipt), tz=timezone.utc)
    if received > evaluated_at_utc:
        raise TemporalIntegrityError(
            "captured retained state holds an interval received at "
            f"{iso_z(received, field_name='last_receipt_epoch')}, after "
            f"{iso_z(evaluated_at_utc, field_name='evaluated_at_utc')}; its "
            "values were not available at the replay instant"
        )


def _assert_checkpoint_identity(
    captured: Mapping[str, Any], checkpoint: FeatureStateCheckpoint
) -> None:
    """The declared canonical checkpoint identity must match the reconstruction.

    ``checkpoint_from_payload`` rebuilds a checkpoint without consulting the
    supplied ``checkpoint_id``, and that id binds the schema version, venue
    instrument, feature version, and consumed watermark. A stale or tampered id
    would otherwise let replay certify retained evidence whose declared
    canonical identity disagrees with its contents.
    """
    declared = captured["checkpoint_id"]
    if not isinstance(declared, str) or declared != checkpoint.checkpoint_id:
        raise EvidenceIntegrityError(
            f"captured retained state checkpoint_id {declared!r} does not match "
            f"the reconstructed identity {checkpoint.checkpoint_id!r}"
        )


def _observation_rows(raw: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValueError(f"replay evidence {field_name} must be a sequence")
    return tuple(raw)


def _retained_state(
    evidence: ReplayEvidence,
    *,
    instrument_version: InstrumentVersion,
    interval_seconds: int,
) -> RollingState:
    """The prior state the cycle actually resumed from, or a declared cold start.

    ``from_checkpoint`` assumes durable evidence and recomputes ``restart_state``
    from that assumption, so the captured provenance is restored afterwards:
    those two values are inputs to production's own ``_resolve_restart_state``,
    not feature values, and dropping them would reclassify an in-process cold
    start as a restart warmup.
    """
    if evidence.prior_state is None:
        return (
            initial_state(instrument_version, interval_seconds=interval_seconds),
            None,
        )
    try:
        checkpoint = checkpoint_from_payload(evidence.prior_state)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceIntegrityError(
            "replay refused: captured retained state cannot be reconstructed "
            f"({type(exc).__name__}); refusing to synthesize the missing context"
        ) from exc
    _assert_checkpoint_identity(evidence.prior_state, checkpoint)
    if checkpoint.instrument_version_id != instrument_version.instrument_version_id:
        raise CycleIdentityMismatch(
            "retained state instrument_version_id "
            f"{checkpoint.instrument_version_id!r} != "
            f"{instrument_version.instrument_version_id!r}"
        )
    if checkpoint.feature_version != FEATURE_VERSION:
        raise CycleIdentityMismatch(
            f"retained state feature_version {checkpoint.feature_version!r} != "
            f"{FEATURE_VERSION!r}"
        )
    try:
        restored = from_checkpoint(checkpoint)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceIntegrityError(
            "replay refused: captured retained state cannot be resumed "
            f"({type(exc).__name__}); refusing to synthesize the missing context"
        ) from exc
    return (
        replace(
            restored,
            resumed_from_checkpoint=evidence.prior_state_resumed_from_checkpoint,
            restart_state=checkpoint.restart_state,
        ),
        checkpoint,
    )


def _assert_retained_state_population(
    prior_state: RollingState,
    *,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
) -> None:
    """Retained history must sit inside the replay boundaries.

    Production resumes from a checkpoint written by an earlier cycle, so its
    retained window always ends at or before the current cutoff. Replay must not
    accept a capture whose retained state already reaches past
    ``evaluation_cutoff``: rolling values would come from a fact that did not
    exist at the cutoff while freshness came from the cycle alignment, sealing a
    ``COMPLETE`` snapshot that production could not have produced. Retained
    intervals carry synthetic receipts derived from their close, so bounding the
    horizon by the cutoff also keeps them visible at ``evaluated_at_utc``.
    """
    if prior_state.interval_count == 0:
        return
    first_epoch = prior_state.first_interval_epoch
    interval_seconds = int(prior_state.interval_seconds)
    if first_epoch is None or interval_seconds <= 0:
        raise EvidenceIntegrityError(
            "replay refused: retained state declares no usable interval horizon"
        )
    if int(first_epoch) % interval_seconds != 0:
        raise EvidenceIntegrityError(
            "replay refused: retained state horizon is not grid aligned"
        )
    horizon = datetime.fromtimestamp(
        int(first_epoch) + interval_seconds * prior_state.interval_count,
        tz=timezone.utc,
    )
    if horizon > evaluation_cutoff:
        raise TemporalIntegrityError(
            "retained state reaches "
            f"{iso_z(horizon, field_name='retained_horizon')}, past evaluation_cutoff "
            f"{iso_z(evaluation_cutoff, field_name='evaluation_cutoff')}; its "
            "rolling values would come from a fact that did not exist at the cutoff"
        )
    if horizon > evaluated_at_utc:
        raise TemporalIntegrityError(
            "retained state reaches "
            f"{iso_z(horizon, field_name='retained_horizon')}, past "
            f"{iso_z(evaluated_at_utc, field_name='evaluated_at_utc')}; its "
            "intervals were not visible at the replay instant"
        )


def source_evidence_identity(
    evidence: ReplayEvidence,
    *,
    evaluation_cutoff: datetime,
) -> str:
    """Identity of the exact captured cycle, not just its rows.

    The retained state, the evidence roles, and the alignment context all change
    the sealed snapshot from identical rows, so all of them are bound here.
    """
    cutoff = iso_z(evaluation_cutoff, field_name="evaluation_cutoff")
    return stable_hash(
        "R2EV",
        {
            "evaluation_cutoff": cutoff,
            "cycle_origin": evidence.cycle_origin,
            "prior_state": (
                {
                    "checkpoint": dict(evidence.prior_state),
                    "resumed_from_checkpoint": bool(
                        evidence.prior_state_resumed_from_checkpoint
                    ),
                }
                if evidence.prior_state is not None
                else None
            ),
            "window_start": (
                iso_z(evidence.window_start, field_name="window_start")
                if evidence.window_start is not None
                else None
            ),
            "source_incomplete": bool(evidence.source_incomplete),
            "committed_write_watermarks": [
                item.to_dict() for item in evidence.committed_write_watermarks
            ],
            "evidence": [dict(item) for item in evidence.evidence],
            "coverage_only": [dict(item) for item in evidence.coverage_only],
        },
    )


def load_observation_evidence(
    payload: Sequence[Mapping[str, Any]],
) -> tuple[Observation, ...]:
    """Rebuild observations from a captured payload.

    Every row must carry its ``observation_id`` and that id must equal the id
    the contract derives from the reconstructed record. Missing or mismatched
    identity fails closed; the serialized id is never trusted on its own.
    """
    if isinstance(payload, (str, bytes)) or not isinstance(payload, Sequence):
        raise ValueError("replay evidence must be a sequence of observation records")
    return tuple(_observation_from_evidence(item) for item in payload)


def _assert_replay_identity(
    observations: Sequence[Observation],
    *,
    instrument_version: InstrumentVersion,
    state: RollingState | None = None,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Same identity semantics the live feature-bus cycle enforces.

    The rule is owned by ``pipeline._assert_cycle_identity`` and reused here so
    replay cannot accept evidence for a different instrument than the one the
    snapshot will be labelled with, nor retained state whose identity, feature
    version, or interval disagrees with the cycle being replayed.

    ``interval_seconds`` is the interval the cycle is replayed at, passed in by
    the caller. It must not be read back off ``state``: that would compare the
    retained state against itself and let a checkpoint taken at a different
    cadence through, which production refuses.
    """
    _assert_cycle_identity(
        observations,
        instrument_version=instrument_version,
        state=state,
        interval_seconds=interval_seconds,
    )


def _require_consumed_watermark(
    eligible: Sequence[Observation],
    *,
    prior_state: RollingState,
    candidate_state: RollingState,
    committed_write_watermarks: Sequence[ConsumedInputWatermark],
    consumed_input_watermark: ConsumedInputWatermark,
) -> None:
    """The sealed watermark must be the one production derives, exactly.

    A snapshot may not claim it has consumed evidence that is in its own values,
    nor less than the state it resumed from, and it may not claim more than the
    cycle actually consumed. Production derives the position from the candidate
    state and folds in the canonical write positions its own writes returned, so
    the captured value must equal that maximum — a lower-bound-only check would
    accept an invented position and seal consumption of canonical evidence the
    capture does not contain.

    The checked population is every row that can change a sealed field:
    alignment-admitted winners, their deduped losers, coverage-only continuity
    rows, and the misaligned exclusions that decide coverage. A forming or
    unclosed row is discarded by alignment, cannot change a sealed field, and
    therefore makes no consumed-input claim. Evidence with no commit order at
    all carries no claim; partially committed snapshot-affecting evidence cannot
    prove its watermark and fails closed.
    """
    expected = max(prior_state.consumed_input_watermark, candidate_state.consumed_input_watermark)
    for position in committed_write_watermarks:
        expected = max(expected, position)
    if consumed_input_watermark != expected:
        raise WatermarkIntegrityError(
            f"consumed input watermark {consumed_input_watermark.to_dict()} is not the "
            f"position this cycle consumed {expected.to_dict()}; refusing to seal a "
            "position the captured evidence does not support"
        )
    committed = [
        item.commit_order for item in eligible if item.commit_order is not None
    ]
    if not committed:
        return
    if len(committed) != len(eligible):
        raise WatermarkIntegrityError(
            "replay evidence mixes committed and uncommitted observations; "
            "the consumed input watermark cannot be proven"
        )
    highest = max(committed)
    if consumed_input_watermark < highest:
        raise WatermarkIntegrityError(
            f"consumed input watermark {consumed_input_watermark.to_dict()} precedes "
            f"consumed evidence position {highest.to_dict()}"
        )


def _eligible_evidence(alignment: AlignmentResult) -> tuple[Observation, ...]:
    """Committed inputs whose existence can change the sealed snapshot.

    Admitted winners and their deduped losers set values, gaps and lateness.
    Coverage-only rows fill expected-window continuity, so they move
    ``coverage_ratio`` and ``missing_intervals``. ``excluded_misaligned_rows``
    carry no values but still decide ``AlignmentResult.coverage``. All three
    belong to the same population: a snapshot may not record coverage from rows
    it claims not to have consumed.

    Forming and unclosed rows are deliberately absent. Alignment reaches them
    only through ``excluded_forming`` and ``excluded_unclosed``, and ``coverage``
    reads neither; they are also excluded from ``present``, from the window
    origin, and from lateness, so they cannot change any sealed field.
    """
    return (
        *alignment.observations,
        *alignment.superseded,
        *alignment.coverage_only,
        *alignment.excluded_misaligned_rows,
    )


def _assert_cutoff_population(
    alignment: AlignmentResult, *, evaluation_cutoff: datetime
) -> None:
    """No fact that was still open at the cutoff may reach the snapshot.

    Alignment classifies a non-aggregate, wrong-cadence, or off-grid row as
    misaligned, and that classification is what ``AlignmentResult.coverage``
    consumes. It is the only excluded category whose rows can still change a
    sealed field, so such a row would let a fact that did not exist at the
    cutoff time alter an earlier snapshot.

    The boundary is the fact's own close, not its start:
    ``_admit_closed_intervals`` tests cadence before ``interval_end``, so a
    wrong-cadence aggregate can start before the cutoff and still be open at it.
    A row without an interval is bounded by its source event time. The row is
    refused, never dropped.

    Every other category is already bounded by the cutoff: admitted rows and
    their superseded losers only qualify when their interval closed at or
    before it, and forming and unclosed rows cannot change a sealed field.
    """
    for item in alignment.excluded_misaligned_rows:
        closed_at = item.interval_end or item.source_event_time
        if closed_at > evaluation_cutoff:
            raise TemporalIntegrityError(
                "replay evidence contains a misaligned fact that closes at "
                f"{iso_z(closed_at, field_name='closed_at')} after "
                "evaluation_cutoff "
                f"{iso_z(evaluation_cutoff, field_name='evaluation_cutoff')}; it "
                "cannot belong to the cutoff population"
            )


def _assert_availability_matches_production(
    snapshot: FeatureSnapshot,
    cycle_alignment: AlignmentResult,
    rolling_alignment: AlignmentResult,
    *,
    source_version: str,
) -> None:
    """The sealed stamp must be the one production derives from these inputs.

    ``build_feature_snapshot`` takes availability from the contiguous tail of
    the *cycle* alignment, falling back to the rolling alignment when the cycle
    contributed no admitted rows. Rolling values come from retained state, so a
    replay that assembles the snapshot itself — rather than through the
    production path — can quietly seal a different ``visible_at_utc`` for the
    same evidence. Recomputing it here mirrors that exact fallback: it is a
    wiring assertion, not a second semantics.
    """
    inputs = contiguous_tail(cycle_alignment)[-FEATURE_WINDOW_INTERVALS:] or (
        contiguous_tail(rolling_alignment)[-FEATURE_WINDOW_INTERVALS:]
    )
    if not inputs:
        return
    expected = max(
        item.availability_stamp(source_version=source_version).visible_at_utc
        for item in inputs
    )
    if snapshot.availability.visible_at_utc != expected:
        raise TemporalIntegrityError(
            "sealed snapshot visibility "
            f"{iso_z(snapshot.availability.visible_at_utc, field_name='visible_at_utc')} "
            "does not match the production derivation from these inputs "
            f"{iso_z(expected, field_name='visible_at_utc')}"
        )


def _require_consistent_content(
    captured: Sequence[Observation], eligible: Sequence[Observation]
) -> None:
    """One observation identity must not carry two different stories.

    ``observation_idempotency_key`` keys a market observation by its identity
    alone, and ``_idempotency_payload_json`` strips no field for
    ``MARKET_OBSERVATION_RECORDED``, so the canonical writer holds exactly one
    payload per identity and treats any divergence as integrity corruption.
    Replay must refuse the same evidence instead of letting alignment pick a
    winner by process-local ingestion order.

    The identity check spans every captured row, because excluding a row from
    the sealed snapshot does not stop it from colliding with one that is in it:
    a forming and a closed version of one interval share an identity while only
    the closed one is eligible. ``eligible`` bounds the values-level check,
    which is about rows competing for one interval.

    The identity already encodes instrument, cadence, interval and revision, so
    keys are scoped by cadence without a second rule: a one-minute bar and a
    five-minute bar sharing an epoch are different facts, and alignment never
    lets them compete because it tests cadence before it ranks revisions.
    """
    fingerprints: dict[str, str] = {}
    for item in eligible:
        if item.payload_kind is not PayloadKind.FIXED_INTERVAL_AGGREGATE:
            continue
        identity = item.observation_id
        fingerprint = aggregate_content_fingerprint(dict(item.values))
        prior_fingerprint = fingerprints.get(identity)
        if prior_fingerprint is not None and prior_fingerprint != fingerprint:
            raise EvidenceIntegrityError(
                "replay evidence has conflicting content for observation "
                f"identity {identity!r}; refusing to hide incompatible "
                "canonical evidence"
            )
        fingerprints[identity] = fingerprint

    payloads: dict[str, bytes] = {}
    for item in captured:
        identity = item.observation_id
        payload = canonical_json_bytes(item.to_dict())
        prior_payload = payloads.get(identity)
        if prior_payload is not None and prior_payload != payload:
            raise EvidenceIntegrityError(
                "replay evidence has conflicting payloads under one observation "
                f"identity {identity!r}; canonical history holds one payload per "
                "identity, so the winner is not determined by identity"
            )
        payloads[identity] = payload


def _assert_replay_visibility(
    observations: Sequence[Observation],
    *,
    evaluated_at_utc: datetime,
    source_version: str,
) -> None:
    """No captured input may have become visible after the replay instant.

    The snapshot's own point-in-time guard only sees the inputs its availability
    tail covers, while coverage and lateness come from the whole alignment. A
    row alignment discards can still change the coverage verdict, so this check
    covers every captured row rather than only the admitted ones. It is not a
    silent filter: an invisible row is refused, never dropped.
    """
    if not observations:
        return
    stamps = tuple(
        item.availability_stamp(source_version=source_version) for item in observations
    )
    assert_point_in_time(stamps, decision_at_utc=evaluated_at_utc)


@dataclass(frozen=True)
class ReplayResult:
    """One replayed cycle: the sealed snapshot plus the inputs it came from."""

    snapshot: FeatureSnapshot
    prior_state: RollingState
    state: RollingState
    cycle_alignment: AlignmentResult
    rolling_alignment: AlignmentResult


def replay_cycle(
    evidence: ReplayEvidence,
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    consumed_input_watermark: ConsumedInputWatermark,
    source_version: str,
    declared_feature_version: str = FEATURE_VERSION,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> ReplayResult:
    """Replay one captured cycle through the production Feature Bus path.

    The order mirrors ``pipeline.run_cycle`` exactly, minus publication:

        prior state -> align(evidence, coverage_only) -> advance state
                    -> snapshot from retained candidate state

    The snapshot is therefore built from ``alignment_from_state(candidate)``
    with the cycle alignment supplied as freshness and
    ``candidate.restart_state`` passed through, which is what production does.
    Building it from the fetched rows alone would misreport any resumed cycle.

    A declared feature version other than this engine's ``FEATURE_VERSION``, or
    retained state that is missing when the capture declares a resume, is
    refused before any value is computed.
    """
    _require_feature_version(declared_feature_version)
    prior, prior_checkpoint = _retained_state(
        evidence,
        instrument_version=instrument_version,
        interval_seconds=interval_seconds,
    )
    if prior_checkpoint is not None:
        _assert_retained_state_availability(
            prior_checkpoint, evaluated_at_utc=evaluated_at_utc
        )
    _assert_retained_state_population(
        prior,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
    )
    loaded_evidence = load_observation_evidence(evidence.evidence)
    loaded_coverage_only = load_observation_evidence(evidence.coverage_only)
    captured = (*loaded_evidence, *loaded_coverage_only)
    _assert_replay_identity(
        captured,
        instrument_version=instrument_version,
        state=prior,
        interval_seconds=interval_seconds,
    )
    _assert_replay_visibility(
        captured, evaluated_at_utc=evaluated_at_utc, source_version=source_version
    )
    alignment = _replay_alignment(
        loaded_evidence,
        coverage_only=loaded_coverage_only,
        evaluation_cutoff=evaluation_cutoff,
        interval_seconds=interval_seconds,
        window_start=evidence.window_start,
        source_incomplete=evidence.source_incomplete,
    )
    _assert_cutoff_population(alignment, evaluation_cutoff=evaluation_cutoff)
    eligible = _eligible_evidence(alignment)
    _require_consistent_content(captured, eligible)
    candidate = advance_state(prior, alignment.observations).state
    _require_consumed_watermark(
        eligible,
        prior_state=prior,
        candidate_state=candidate,
        committed_write_watermarks=evidence.committed_write_watermarks,
        consumed_input_watermark=consumed_input_watermark,
    )
    rolling = alignment_from_state(candidate)
    snapshot = build_feature_snapshot(
        rolling,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        restart_state=candidate.restart_state,
        source_version=source_version,
        freshness_alignment=alignment,
    )
    _assert_availability_matches_production(
        snapshot, alignment, rolling, source_version=source_version
    )
    return ReplayResult(
        snapshot=snapshot,
        prior_state=prior,
        state=candidate,
        cycle_alignment=alignment,
        rolling_alignment=rolling,
    )


def replay_feature_snapshot(
    observations: Sequence[Observation],
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    consumed_input_watermark: ConsumedInputWatermark,
    source_version: str,
    declared_feature_version: str = FEATURE_VERSION,
    window_start: datetime | None = None,
    source_incomplete: bool = False,
) -> FeatureSnapshot:
    """Replay a declared cold start whose whole history is in one fetch.

    This is the convenience form for a first cycle: every row is feature
    evidence, no retained state exists, and the capture says so. A resumed
    cycle must go through :func:`replay_cycle` with its retained state, because
    identical rows produce a different snapshot once history is retained.
    """
    evidence = capture_replay_evidence(
        observations,
        window_start=window_start,
        source_incomplete=source_incomplete,
    )
    return replay_cycle(
        evidence,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        source_version=source_version,
        declared_feature_version=declared_feature_version,
    ).snapshot


def _replay_alignment(
    observations: Sequence[Observation],
    *,
    coverage_only: Sequence[Observation] = (),
    evaluation_cutoff: datetime,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    window_start: datetime | None,
    source_incomplete: bool,
) -> AlignmentResult:
    """Alignment exactly as the production cycle would have derived it.

    ``coverage_only`` keeps expected-window continuity without contributing
    feature evidence, and ``window_start`` bounds gap detection; omitting
    either derives a different coverage verdict from the same rows.
    """
    alignment = align_minute_observations(
        observations,
        cutoff=evaluation_cutoff,
        interval_seconds=interval_seconds,
        window_start=window_start,
        coverage_only=coverage_only,
    )
    if source_incomplete:
        return replace(alignment, source_incomplete=True)
    return alignment


def replay_captured_evidence(
    payload: ReplayEvidence | Mapping[str, Any],
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    consumed_input_watermark: ConsumedInputWatermark,
    source_version: str,
    declared_feature_version: str = FEATURE_VERSION,
) -> tuple[FeatureSnapshot, ClassifiedParityReport]:
    """Replay a captured cycle twice and classify parity on the sealed snapshot."""
    _require_feature_version(declared_feature_version)
    evidence = load_replay_evidence(payload)
    first = replay_cycle(
        evidence,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        source_version=source_version,
        declared_feature_version=declared_feature_version,
    )
    second = replay_cycle(
        evidence,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        source_version=source_version,
        declared_feature_version=declared_feature_version,
    )
    assert_snapshot_determinism(
        (first.snapshot, second.snapshot)
    )
    report = classify_shadow_parity(
        first.rolling_alignment,
        bus_values=first.snapshot.values,
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
        evidence_identity=source_evidence_identity(
            evidence, evaluation_cutoff=evaluation_cutoff
        ),
        feature_version=first.snapshot.feature_version,
        snapshot_id=first.snapshot.snapshot_id,
    )
    return first.snapshot, report


def classify_shadow_parity(
    alignment: AlignmentResult,
    *,
    bus_values: Mapping[str, Any],
    instrument_version: InstrumentVersion,
    evaluated_at_utc: datetime,
    evidence_identity: str,
    feature_version: str,
    snapshot_id: str | None = None,
) -> ClassifiedParityReport:
    """Field-level parity for one sealed snapshot.

    ``bus_values`` must be the ``FeatureSnapshot.values`` being reported, so
    the artifact describes the exact snapshot it names. The feature-bus side is
    never recomputed here; only the legacy reference values are produced, by
    calling the existing production indicator functions.
    """
    if not _exactly_equal(ABSOLUTE_TOLERANCE, 0.0):
        raise AssertionError("R2 parity refuses a non-zero tolerance")
    if set(bus_values) != set(FEATURE_NAMES):
        raise AssertionError("parity bus values do not cover FEATURE_NAMES")
    indicator_report = compare_against_production_indicators(
        alignment,
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
    )
    indicator_by_name = {check.feature: check for check in indicator_report.checks}
    if set(indicator_by_name) != set(INDICATOR_EQUIVALENT_FEATURES):
        raise AssertionError("indicator parity surface drifted from the R2 catalog")

    percentile_refs = _percentile_references(alignment)
    rows: list[ClassifiedParityRow] = []
    for name in FEATURE_NAMES:
        bus_value = bus_values[name]
        if name in indicator_by_name:
            rows.append(
                _indicator_row(
                    indicator_by_name[name],
                    bus_value=bus_value,
                    evidence_identity=evidence_identity,
                    feature_version=feature_version,
                )
            )
            continue
        if name in PERCENTILE_FEATURES:
            rows.extend(
                _percentile_rows(
                    name,
                    feature_bus_value=bus_value,
                    reference=percentile_refs[name],
                    evidence_identity=evidence_identity,
                    feature_version=feature_version,
                )
            )
            continue
        if name in NOT_RETAINED_FEATURES:
            rows.append(
                _not_retained_row(
                    name,
                    feature_bus_value=bus_value,
                    evidence_identity=evidence_identity,
                    feature_version=feature_version,
                )
            )
            continue
        rows.append(
            _unavailable_row(
                name,
                feature_bus_value=bus_value,
                evidence_identity=evidence_identity,
                feature_version=feature_version,
            )
        )

    primary = {row.feature_name for row in rows}
    if primary != set(FEATURE_NAMES):
        raise AssertionError("classified parity did not cover FEATURE_NAMES")
    return ClassifiedParityReport(
        rows=tuple(rows),
        source_evidence_identity=evidence_identity,
        feature_version=feature_version,
        snapshot_id=snapshot_id,
    )


def _require_feature_version(declared_feature_version: str) -> None:
    if declared_feature_version != FEATURE_VERSION:
        raise FeatureVersionMismatch(
            f"replay refused: declared feature version {declared_feature_version!r} "
            f"does not equal engine {FEATURE_VERSION!r}"
        )


def _exactly_equal(left: Any, right: Any) -> bool:
    """Exact equality under the frozen zero-tolerance parity rule.

    ``math.isclose`` with both tolerances pinned to the module's
    non-configurable ``ABSOLUTE_TOLERANCE`` states the comparison rule
    directly, so no hidden epsilon is introduced and absence only matches
    absence.
    """
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(
        float(left), float(right), rel_tol=0.0, abs_tol=ABSOLUTE_TOLERANCE
    )


def _indicator_row(
    check: Any,
    *,
    bus_value: Any,
    evidence_identity: str,
    feature_version: str,
) -> ClassifiedParityRow:
    bus = _round(bus_value)
    reference = check.reference_value
    matched = _exactly_equal(bus, reference)
    return ClassifiedParityRow(
        feature_name=check.feature,
        legacy_value=reference,
        feature_bus_value=bus,
        equality_rule=EXACT_EQUALITY_RULE,
        classification="MATCH" if matched else "IMPLEMENTATION_DEFECT",
        reason=(
            f"Snapshot value equals {check.reference} "
            f"under absolute tolerance {ABSOLUTE_TOLERANCE}."
            if matched
            else (
                f"Snapshot value differs from {check.reference} "
                f"under absolute tolerance {ABSOLUTE_TOLERANCE}."
            )
        ),
        source_evidence_identity=evidence_identity,
        feature_version=feature_version,
        legacy_source=str(check.reference),
    )


def _percentile_rows(
    name: str,
    *,
    feature_bus_value: Any,
    reference: _PercentileReference,
    evidence_identity: str,
    feature_version: str,
) -> tuple[ClassifiedParityRow, ...]:
    bus = _round(feature_bus_value)
    indicator = reference.indicator_value
    if _exactly_equal(bus, indicator):
        primary = ClassifiedParityRow(
            feature_name=name,
            legacy_value=indicator,
            feature_bus_value=bus,
            equality_rule=EXACT_EQUALITY_RULE,
            classification="MATCH",
            reason=(
                "Feature bus percentile equals "
                "app.indicators.technical.percentile_rank on the same series."
            ),
            source_evidence_identity=evidence_identity,
            feature_version=feature_version,
            legacy_source="app.indicators.technical.percentile_rank",
        )
    else:
        primary = ClassifiedParityRow(
            feature_name=name,
            legacy_value=indicator,
            feature_bus_value=bus,
            equality_rule=EXACT_EQUALITY_RULE,
            classification="IMPLEMENTATION_DEFECT",
            reason=(
                "Feature bus percentile differs from "
                "app.indicators.technical.percentile_rank on the same series."
            ),
            source_evidence_identity=evidence_identity,
            feature_version=feature_version,
            legacy_source="app.indicators.technical.percentile_rank",
        )
    if reference.scan_value is None:
        return (primary,)
    scan_equal = _exactly_equal(bus, reference.scan_value)
    secondary = ClassifiedParityRow(
        feature_name=name,
        legacy_value=reference.scan_value,
        feature_bus_value=bus,
        equality_rule=EXACT_EQUALITY_RULE,
        classification="MATCH" if scan_equal else "INTENTIONAL_SEMANTIC_DIFFERENCE",
        reason=(
            "Scan percentile_rank agrees with the feature bus on this sample."
            if scan_equal
            else (
                "app.services.signal_features.percentile_rank uses midpoint ties "
                "and 4-decimal rounding. The feature bus uses "
                "app.indicators.technical.percentile_rank. The difference is "
                "reported and is not forced equal."
            )
        ),
        source_evidence_identity=evidence_identity,
        feature_version=feature_version,
        legacy_source="app.services.signal_features.percentile_rank",
    )
    return (primary, secondary)


def _not_retained_row(
    name: str,
    *,
    feature_bus_value: Any,
    evidence_identity: str,
    feature_version: str,
) -> ClassifiedParityRow:
    return ClassifiedParityRow(
        feature_name=name,
        legacy_value=None,
        feature_bus_value=feature_bus_value,
        equality_rule=NOT_COMPARABLE_RULE,
        classification="INTENTIONAL_SEMANTIC_DIFFERENCE",
        reason=(
            "Contract B does not retain book depth or trade tape on the feature "
            "bus. The value is absent with Missingness.NOT_RETAINED. There is "
            "no legacy minute-bar value to force into the snapshot."
        ),
        source_evidence_identity=evidence_identity,
        feature_version=feature_version,
        legacy_source="app.opip.features.engine.NOT_RETAINED_INPUTS",
    )


def _unavailable_row(
    name: str,
    *,
    feature_bus_value: Any,
    evidence_identity: str,
    feature_version: str,
) -> ClassifiedParityRow:
    return ClassifiedParityRow(
        feature_name=name,
        legacy_value=None,
        feature_bus_value=feature_bus_value,
        equality_rule=NOT_COMPARABLE_RULE,
        classification="LEGACY_EVIDENCE_UNAVAILABLE",
        reason=(
            "No function in app.indicators.technical or the live technical "
            "scorer emits this field from the retained minute series. The live "
            "scorer consumes a precomputed MarketSnapshot. A legacy stand-in "
            "is not invented."
        ),
        source_evidence_identity=evidence_identity,
        feature_version=feature_version,
        legacy_source="none",
    )


@dataclass(frozen=True)
class _PercentileReference:
    indicator_value: float | None
    scan_value: float | None


def _percentile_references(
    alignment: AlignmentResult,
) -> dict[str, _PercentileReference]:
    tail = contiguous_tail(alignment)[-FEATURE_WINDOW_INTERVALS:]
    closes = [float(item.values["close"]) for item in tail]
    highs = [float(item.values["high"]) for item in tail]
    lows = [float(item.values["low"]) for item in tail]
    atr_series = safe_atr_percentage_series(highs, lows, closes, ATR_PERIOD)
    atr_window = (
        list(atr_series[-PERCENTILE_LOOKBACK_INTERVALS:]) if atr_series else None
    )
    bandwidth_window = closes[-(PERCENTILE_LOOKBACK_INTERVALS + BANDWIDTH_PERIOD) :]
    bandwidth_series = safe_bandwidth_series(bandwidth_window, BANDWIDTH_PERIOD)
    return {
        "atr_pct_percentile": _rank_pair(atr_window),
        "bandwidth_percentile": _rank_pair(
            list(bandwidth_series) if bandwidth_series else None
        ),
    }


def _rank_pair(series: Sequence[float] | None) -> _PercentileReference:
    if not series:
        return _PercentileReference(indicator_value=None, scan_value=None)
    indicator = _round(safe_percentile_rank(series))
    divergence = percentile_definition_divergence(series)
    return _PercentileReference(
        indicator_value=indicator,
        scan_value=_round(divergence.scan_definition),
    )


def _round(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value), VALUE_PRECISION)


def _require_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"replay evidence {field_name} must be an integer")
    return int(value)


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name)


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"replay evidence {field_name} must be a boolean")
    return value


def _required_string(raw: Mapping[str, Any], field_name: str) -> str:
    """Identity fields are persisted as strings; a numeric twin is not one.

    Coercing ``7`` to ``"7"`` would let a rewritten record pass the identity
    check, and ``source_evidence_identity`` would then name the reconstruction
    rather than the durable payload supplied.
    """
    value = raw[field_name]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"replay evidence {field_name} must be a non-empty string"
        )
    return value


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"replay evidence {field_name} must be a string or null")
    return value


def _commit_order_from_evidence(
    raw: Mapping[str, Any],
) -> ConsumedInputWatermark | None:
    history_epoch = raw["history_epoch"]
    local_sequence = raw["local_sequence"]
    if history_epoch is None and local_sequence is None:
        return None
    if history_epoch is None or local_sequence is None:
        raise ValueError("replay evidence commit order is incomplete")
    return ConsumedInputWatermark(
        history_epoch=_require_int(history_epoch, "history_epoch"),
        local_sequence=_require_int(local_sequence, "local_sequence"),
    )


def _provenance_from_evidence(raw: Mapping[str, Any]) -> dict[str, str]:
    """Provenance is ``Mapping[str, str]``; malformed evidence is refused.

    Coercing a key or value would rewrite the record, and because an aggregate's
    ``observation_id`` does not bind provenance, the identity check would still
    pass and ``source_evidence_identity`` would hash something the caller never
    supplied.
    """
    provenance = raw["provenance"]
    if not isinstance(provenance, Mapping):
        raise ValueError("replay evidence provenance must be an object")
    cleaned: dict[str, str] = {}
    for key, value in provenance.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                "replay evidence provenance keys and values must be strings"
            )
        cleaned[key] = value
    return cleaned


def _observation_from_evidence(raw: Mapping[str, Any]) -> Observation:
    if not isinstance(raw, Mapping):
        raise ValueError("replay evidence row must be an object")
    missing = [key for key in _EVIDENCE_KEYS if key not in raw]
    if missing:
        raise ValueError(f"replay evidence missing required keys: {sorted(missing)}")
    unexpected = sorted(set(raw) - set(_EVIDENCE_KEYS))
    if unexpected:
        # An unmodelled field would be dropped by reconstruction, so
        # ``source_evidence_identity`` would name a rewritten payload rather
        # than the durable evidence actually supplied.
        raise ValueError(f"replay evidence has unexpected keys: {unexpected}")
    if raw["record_type"] != OBSERVATION_RECORD_TYPE:
        raise ValueError("replay evidence record_type is not Observation")
    declared_schema = _require_int(raw["schema_version"], "schema_version")
    if declared_schema != OBSERVATION_SCHEMA_VERSION:
        raise ValueError(
            "replay refused: captured observation schema_version "
            f"{declared_schema} is not the supported {OBSERVATION_SCHEMA_VERSION}"
        )
    values = raw["values"]
    if not isinstance(values, Mapping):
        raise ValueError("replay evidence values must be an object")
    payload_kind = PayloadKind(str(raw["payload_kind"]))
    source_event_time = _parse_time(raw["source_event_time"], "source_event_time")
    interval_seconds = _optional_int(
        raw["aggregate_interval_seconds"], "aggregate_interval_seconds"
    )
    if payload_kind is PayloadKind.FIXED_INTERVAL_AGGREGATE:
        absent = [key for key in AGGREGATE_REQUIRED_KEYS if values.get(key) is None]
        if absent:
            raise ValueError(f"aggregate is missing values: {sorted(absent)}")
        if interval_seconds is None:
            raise ValueError(
                "replay evidence aggregate requires aggregate_interval_seconds"
            )
        _require_source_valid_aggregate(
            values,
            interval_seconds=interval_seconds,
            source_event_time=source_event_time,
        )
    commit_order = _commit_order_from_evidence(raw)
    captured_id = raw["observation_id"]
    if not isinstance(captured_id, str) or not captured_id.strip():
        raise ValueError("replay evidence observation_id must be a non-empty string")
    reconstructed = Observation(
        instrument_version_id=_required_string(raw, "instrument_version_id"),
        venue=_required_string(raw, "venue"),
        venue_instrument_id=_required_string(raw, "venue_instrument_id"),
        source_event_time=source_event_time,
        receipt_time=_parse_time(raw["receipt_time"], "receipt_time"),
        ingestion_order=_require_int(raw["ingestion_order"], "ingestion_order"),
        payload_kind=payload_kind,
        values=dict(values),
        coverage=CoverageState(str(raw["coverage"])),
        aggregate_interval_seconds=interval_seconds,
        source_sequence=_optional_str(raw["source_sequence"], "source_sequence"),
        revision=_require_int(raw["revision"], "revision"),
        supersedes=_optional_str(raw["supersedes"], "supersedes"),
        interval_forming=_require_bool(raw["interval_forming"], "interval_forming"),
        provenance=_provenance_from_evidence(raw),
        commit_order=commit_order,
        schema_version=declared_schema,
    )
    if reconstructed.observation_id != captured_id:
        raise ValueError(
            "replay evidence observation_id does not match the reconstructed "
            f"record: captured {captured_id!r} != reconstructed "
            f"{reconstructed.observation_id!r}"
        )
    return reconstructed


def _require_source_valid_aggregate(
    values: Mapping[str, Any],
    *,
    interval_seconds: int,
    source_event_time: datetime,
) -> None:
    """Apply the source normalization rules to persisted aggregate values.

    ``Observation.__post_init__`` checks finiteness only. The venue-row rules
    that reject a negative close, a high below its body, or a malformed trade
    count live in ``observations._validate_row``; replay reuses that single
    implementation so a corrupted row cannot retain its original
    ``observation_id`` and still be sealed into a snapshot.
    """
    row = IntervalRow(
        interval_start_epoch=int(source_event_time.timestamp()),
        open=values.get("open"),
        high=values.get("high"),
        low=values.get("low"),
        close=values.get("close"),
        volume=values.get("volume"),
        vwap=values.get("vwap"),
        trade_count=values.get("trade_count"),
    )
    reason = _validate_row(row, interval_seconds=int(interval_seconds))
    if reason is not None:
        raise ValueError(f"replay evidence rejected by source rules: {reason}")


def _parse_time(value: Any, field_name: str) -> datetime:
    """Parse a Zulu timestamp and require the canonical spelling back.

    ``datetime.fromisoformat`` also accepts forms such as ``15:00Z`` or a space
    separator, which ``Observation.to_dict`` would rewrite. Accepting them would
    give a rewritten payload the same evidence identity as canonical bytes.
    """
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field_name} must be a Zulu timestamp")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    canonical = require_utc(parsed, field_name=field_name)
    if iso_z(canonical, field_name=field_name) != value:
        raise ValueError(
            f"replay evidence {field_name} {value!r} is not the canonical "
            f"{iso_z(canonical, field_name=field_name)!r}"
        )
    return canonical


__all__ = [
    "CLASSIFICATIONS",
    "CYCLE_ORIGIN_COLD_START",
    "CYCLE_ORIGIN_RESUMED",
    "CYCLE_ORIGINS",
    "EXACT_EQUALITY_RULE",
    "REPLAY_EVIDENCE_RECORD_TYPE",
    "REPLAY_EVIDENCE_SCHEMA_VERSION",
    "CycleIdentityMismatch",
    "EvidenceIntegrityError",
    "FeatureVersionMismatch",
    "ReplayEvidence",
    "ReplayResult",
    "WatermarkIntegrityError",
    "ClassifiedParityReport",
    "ClassifiedParityRow",
    "capture_observation_evidence",
    "capture_replay_evidence",
    "classify_shadow_parity",
    "load_observation_evidence",
    "load_replay_evidence",
    "replay_captured_evidence",
    "replay_cycle",
    "replay_feature_snapshot",
    "source_evidence_identity",
]
