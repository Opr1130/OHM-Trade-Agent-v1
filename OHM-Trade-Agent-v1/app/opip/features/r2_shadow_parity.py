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

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Mapping, Sequence

from app.opip.contracts.enums import CoverageState, PayloadKind
from app.opip.contracts.features import FeatureSnapshot
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


def source_evidence_identity(
    observations: Sequence[Observation],
    *,
    evaluation_cutoff: datetime,
) -> str:
    cutoff = iso_z(evaluation_cutoff, field_name="evaluation_cutoff")
    return stable_hash(
        "R2EV",
        {
            "evaluation_cutoff": cutoff,
            "observations": [item.to_dict() for item in observations],
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
) -> None:
    """Same identity semantics the live feature-bus cycle enforces.

    The rule is owned by ``pipeline._assert_cycle_identity`` and reused here so
    replay cannot accept evidence for a different instrument than the one the
    snapshot will be labelled with.
    """
    _assert_cycle_identity(
        observations,
        instrument_version=instrument_version,
        state=None,
        interval_seconds=DEFAULT_INTERVAL_SECONDS,
    )


def _require_consumed_watermark(
    eligible: Sequence[Observation],
    *,
    consumed_input_watermark: ConsumedInputWatermark,
) -> None:
    """Refuse a watermark that is earlier than consumed captured evidence.

    A snapshot may not claim it has not consumed evidence that is in its own
    values. Only rows that can change the sealed snapshot are checked:
    alignment-admitted winners, their deduped losers, and the misaligned
    exclusions that decide coverage. A forming or unclosed row is discarded by
    alignment, cannot change a sealed field, and therefore makes no
    consumed-input claim. Evidence with no commit order at all carries no
    claim; partially committed snapshot-affecting evidence cannot prove its
    watermark and fails closed.
    """
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
            f"captured evidence commit order {highest.to_dict()}"
        )


def _eligible_evidence(alignment: AlignmentResult) -> tuple[Observation, ...]:
    """Committed inputs whose existence can change the sealed snapshot.

    Admitted winners and their deduped losers set values, gaps and lateness.
    ``excluded_misaligned_rows`` carry no values but still decide
    ``AlignmentResult.coverage``, so they belong to the same population: a
    snapshot may not record incomplete coverage from a row it claims not to
    have consumed.

    Forming and unclosed rows are deliberately absent. Alignment reaches them
    only through ``excluded_forming`` and ``excluded_unclosed``, and ``coverage``
    reads neither; they are also excluded from ``present``, from the window
    origin, and from lateness, so they cannot change any sealed field.
    """
    return (
        *alignment.observations,
        *alignment.superseded,
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


def _assert_visibility_covers_inputs(
    snapshot: FeatureSnapshot,
    eligible: Sequence[Observation],
    *,
    source_version: str,
) -> None:
    """The sealed stamp must not understate the evidence the snapshot depends on.

    ``build_feature_snapshot`` derives availability from the contiguous feature
    tail, while coverage and lateness come from the whole alignment. A
    snapshot-affecting row outside that tail can therefore be received later
    than the ``visible_at_utc`` the snapshot declares, letting a consumer read
    the snapshot as available before the evidence its own coverage state
    depends on.

    Widening availability's scope would change canonical evidence for the live
    path, and recomputing it only here would give replay a second availability
    semantics that no longer equals the engine's output for the same inputs.
    Replay therefore refuses evidence the sealed stamp cannot cover.
    """
    if not eligible:
        return
    stamps = tuple(
        item.availability_stamp(source_version=source_version) for item in eligible
    )
    latest_visible = max(stamp.visible_at_utc for stamp in stamps)
    if latest_visible > snapshot.availability.visible_at_utc:
        raise TemporalIntegrityError(
            "replay evidence became visible at "
            f"{iso_z(latest_visible, field_name='visible_at_utc')}, later than the "
            "sealed snapshot visibility "
            f"{iso_z(snapshot.availability.visible_at_utc, field_name='visible_at_utc')}; "
            "the snapshot would understate the evidence it depends on"
        )
    declared_source = snapshot.availability.source_at_utc
    if declared_source is None:
        return
    latest_source = max(
        (
            stamp.source_at_utc
            for stamp in stamps
            if stamp.source_at_utc is not None
        ),
        default=None,
    )
    if latest_source is not None and latest_source > declared_source:
        raise TemporalIntegrityError(
            "replay evidence carries a source event at "
            f"{iso_z(latest_source, field_name='source_at_utc')}, later than the "
            "sealed snapshot source time "
            f"{iso_z(declared_source, field_name='source_at_utc')}; the snapshot "
            "would understate the evidence it depends on"
        )


def _require_consistent_content(eligible: Sequence[Observation]) -> None:
    """One observation identity must not carry two different stories.

    ``observation_idempotency_key`` keys a market observation by its identity
    alone, and ``_idempotency_payload_json`` strips no field for
    ``MARKET_OBSERVATION_RECORDED``, so the canonical writer holds exactly one
    payload per identity and treats any divergence as integrity corruption.
    Replay must refuse the same evidence instead of letting alignment pick a
    winner by process-local ingestion order.

    The identity already encodes instrument, cadence, interval and revision, so
    keys are scoped by cadence without a second rule: a one-minute bar and a
    five-minute bar sharing an epoch are different facts, and alignment never
    lets them compete because it tests cadence before it ranks revisions.
    """
    fingerprints: dict[str, str] = {}
    payloads: dict[str, bytes] = {}
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

    The snapshot's own point-in-time guard only sees the contiguous feature
    tail, while coverage and lateness are derived from the full alignment. A
    row alignment discards can still change the coverage verdict, so this
    check covers every captured row rather than only the admitted ones. It is
    not a silent filter: an invisible row is refused, never dropped.
    """
    if not observations:
        return
    stamps = tuple(
        item.availability_stamp(source_version=source_version) for item in observations
    )
    assert_point_in_time(stamps, decision_at_utc=evaluated_at_utc)


def replay_feature_snapshot(
    observations: Sequence[Observation],
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    consumed_input_watermark: ConsumedInputWatermark,
    source_version: str,
    declared_feature_version: str = FEATURE_VERSION,
) -> FeatureSnapshot:
    """Feed frozen observations through the existing feature bus.

    A declared feature version other than this engine's ``FEATURE_VERSION``
    is refused before any value is computed. Evidence that does not belong to
    the supplied ``InstrumentVersion``, or that was not visible at
    ``evaluated_at_utc``, is refused before alignment. No fact still open at
    ``evaluation_cutoff`` may reach the sealed snapshot through coverage. The
    evidence the snapshot then depends on must be self-consistent, must be
    covered by ``consumed_input_watermark``, and must be visible no later than
    the snapshot itself claims.
    """
    _require_feature_version(declared_feature_version)
    _assert_replay_identity(observations, instrument_version=instrument_version)
    _assert_replay_visibility(
        observations, evaluated_at_utc=evaluated_at_utc, source_version=source_version
    )
    alignment = align_minute_observations(observations, cutoff=evaluation_cutoff)
    _assert_cutoff_population(alignment, evaluation_cutoff=evaluation_cutoff)
    eligible = _eligible_evidence(alignment)
    _require_consistent_content(eligible)
    _require_consumed_watermark(
        eligible,
        consumed_input_watermark=consumed_input_watermark,
    )
    snapshot = build_feature_snapshot(
        alignment,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        source_version=source_version,
    )
    _assert_visibility_covers_inputs(
        snapshot, eligible, source_version=source_version
    )
    return snapshot


def replay_captured_evidence(
    payload: Sequence[Mapping[str, Any]],
    *,
    instrument_version: InstrumentVersion,
    evaluation_cutoff: datetime,
    evaluated_at_utc: datetime,
    consumed_input_watermark: ConsumedInputWatermark,
    source_version: str,
    declared_feature_version: str = FEATURE_VERSION,
) -> tuple[FeatureSnapshot, ClassifiedParityReport]:
    """Load captured evidence, replay it twice, and classify parity."""
    _require_feature_version(declared_feature_version)
    loaded = load_observation_evidence(payload)
    first = replay_feature_snapshot(
        loaded,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        source_version=source_version,
        declared_feature_version=declared_feature_version,
    )
    second = replay_feature_snapshot(
        loaded,
        instrument_version=instrument_version,
        evaluation_cutoff=evaluation_cutoff,
        evaluated_at_utc=evaluated_at_utc,
        consumed_input_watermark=consumed_input_watermark,
        source_version=source_version,
        declared_feature_version=declared_feature_version,
    )
    assert_snapshot_determinism((first, second))
    report = classify_shadow_parity(
        align_minute_observations(loaded, cutoff=evaluation_cutoff),
        bus_values=first.values,
        instrument_version=instrument_version,
        evaluated_at_utc=evaluated_at_utc,
        evidence_identity=source_evidence_identity(
            loaded, evaluation_cutoff=evaluation_cutoff
        ),
        feature_version=first.feature_version,
        snapshot_id=first.snapshot_id,
    )
    return first, report


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
        instrument_version_id=str(raw["instrument_version_id"]),
        venue=str(raw["venue"]),
        venue_instrument_id=str(raw["venue_instrument_id"]),
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
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field_name} must be a Zulu timestamp")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    return require_utc(parsed, field_name=field_name)


__all__ = [
    "CLASSIFICATIONS",
    "EXACT_EQUALITY_RULE",
    "CycleIdentityMismatch",
    "EvidenceIntegrityError",
    "FeatureVersionMismatch",
    "WatermarkIntegrityError",
    "ClassifiedParityReport",
    "ClassifiedParityRow",
    "capture_observation_evidence",
    "classify_shadow_parity",
    "load_observation_evidence",
    "replay_captured_evidence",
    "replay_feature_snapshot",
    "source_evidence_identity",
]
