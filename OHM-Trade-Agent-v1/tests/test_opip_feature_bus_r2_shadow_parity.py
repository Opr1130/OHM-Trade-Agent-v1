"""R2 feature-bus shadow parity and deterministic replay.

These tests prove the existing bus on frozen observations. They do not enable
capture, schedule the pilot, or compare features by widening tolerance.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

import pytest

from app.opip.contracts.enums import (
    CoverageState,
    Missingness,
    PayloadKind,
    RestartState,
)
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.contracts.observation import OBSERVATION_SCHEMA_VERSION
from app.opip.contracts.temporal import TemporalIntegrityError
from app.opip.features.checkpoint_store import checkpoint_from_payload
from app.opip.features.engine import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    FEATURE_WINDOW_INTERVALS,
)
from app.opip.features.pipeline import CycleIdentityMismatch
from app.opip.features.publisher import resolve_feature_bus_mode
from app.opip.features.r2_shadow_parity import (
    CLASSIFICATIONS,
    CYCLE_ORIGIN_COLD_START,
    CYCLE_ORIGIN_RESUMED,
    REPLAY_EVIDENCE_RECORD_TYPE,
    REPLAY_EVIDENCE_SCHEMA_VERSION,
    EvidenceIntegrityError,
    FeatureVersionMismatch,
    WatermarkIntegrityError,
    capture_observation_evidence,
    capture_replay_evidence,
    load_observation_evidence,
    load_replay_evidence,
    replay_captured_evidence,
    replay_cycle,
    replay_feature_snapshot,
    source_evidence_identity,
)
from app.opip.features.replay import compare_resumed_state, reconstruct_state
from app.opip.features.state import (
    RollingState,
    advance_state,
    from_checkpoint,
    initial_state,
    to_checkpoint,
)
from app.opip.market.aggregates import align_minute_observations
from app.opip.market.observations import IntervalRow, normalize_interval_rows

NOW = datetime(2026, 9, 11, 15, 1, 0, 220000, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 11, 15, 1, 0, tzinfo=timezone.utc)
SOURCE = "r2-shadow-parity-test"


def _instrument(**overrides) -> InstrumentVersion:
    params = {
        "venue": "kraken",
        "base_asset": "SOL",
        "quote_currency": "USD",
        "venue_instrument_id": "SOLUSD",
        "version": 1,
        "reference_data_version": "opip-evidence-identity-v1",
        "observed_at_utc": NOW,
        "price_decimals": 2,
        "tick_size": 0.01,
        "min_order_size": 0.2,
    }
    params.update(overrides)
    return InstrumentVersion(**params)


def _rows(
    *,
    count: int,
    end_before: datetime = CUTOFF,
    start_price: float = 100.0,
    step: float = 0.05,
    volume: float = 100.0,
    flat: bool = False,
) -> list[IntervalRow]:
    first = end_before - timedelta(minutes=count)
    epoch = int(first.timestamp())
    rows: list[IntervalRow] = []
    for index in range(count):
        price = start_price if flat else start_price + step * index
        high = price if flat else price * 1.002
        low = price if flat else price * 0.998
        rows.append(
            IntervalRow(
                interval_start_epoch=epoch + 60 * index,
                open=price,
                high=high,
                low=low,
                close=price,
                volume=volume,
            )
        )
    return rows


def _observations(
    rows,
    *,
    instrument: InstrumentVersion | None = None,
    receipt_time: datetime = NOW,
    now: datetime = NOW,
):
    instrument = instrument or _instrument()
    result = normalize_interval_rows(
        rows,
        instrument_version=instrument,
        interval_seconds=60,
        receipt_time=receipt_time,
        now=now,
        source_label=SOURCE,
        source_sequence_prefix="r2-1m",
    )
    return tuple(
        replace(
            item,
            commit_order=ConsumedInputWatermark(
                history_epoch=1, local_sequence=1000 + index
            ),
        )
        for index, item in enumerate(result.observations)
    ), result


def _watermark(observations) -> ConsumedInputWatermark:
    """Highest commit position among the supplied rows, or the zero position."""
    orders = [
        item.commit_order for item in observations if item.commit_order is not None
    ]
    if not orders:
        return ConsumedInputWatermark.zero()
    return max(orders)


def _replay(
    observations,
    *,
    cutoff: datetime = CUTOFF,
    version: str = FEATURE_VERSION,
    evaluated_at: datetime | None = None,
    instrument: InstrumentVersion | None = None,
    watermark: ConsumedInputWatermark | None = None,
    window_start: datetime | None = None,
    source_incomplete: bool = False,
    prior_state=None,
    coverage_only=(),
    committed_write_watermarks=(),
):
    evidence = _capture(
        observations,
        prior_state=prior_state,
        coverage_only=coverage_only,
        committed_write_watermarks=committed_write_watermarks,
        window_start=window_start,
        source_incomplete=source_incomplete,
    )
    if evaluated_at is None:
        evaluated_at = NOW if NOW >= cutoff else cutoff
    return replay_captured_evidence(
        evidence,
        instrument_version=instrument or _instrument(),
        evaluation_cutoff=cutoff,
        evaluated_at_utc=evaluated_at,
        consumed_input_watermark=(
            watermark if watermark is not None else _watermark(observations)
        ),
        source_version=SOURCE,
        declared_feature_version=version,
    )


def _capture(observations, *, prior_state=None, created_at: datetime | None = None, **kwargs):
    """Capture with the observation clock replay now requires for retained state."""
    if isinstance(prior_state, RollingState):
        kwargs["prior_state_created_at_utc"] = (
            created_at if created_at is not None else NOW
        )
    kwargs.setdefault("instrument_version", _instrument())
    return capture_replay_evidence(observations, prior_state=prior_state, **kwargs)


def _replay_evidence_result(evidence, **kwargs):
    """Replay a pre-built capture, preserving its retained-state provenance."""
    watermark = kwargs.pop("watermark", None)
    if watermark is None:
        watermark = ConsumedInputWatermark.zero()
        if evidence.prior_state is None:
            prior = initial_state(_instrument())
        else:
            prior = _retained_state_for_tests(evidence)
        watermark = max(watermark, prior.consumed_input_watermark)
        for rows in (evidence.evidence, evidence.coverage_only):
            if rows:
                watermark = max(watermark, _watermark(load_observation_evidence(rows)))
        for position in evidence.committed_write_watermarks:
            watermark = max(
                watermark, position
            )
    return replay_cycle(
        evidence,
        instrument_version=kwargs.pop("instrument", None) or _instrument(),
        evaluation_cutoff=kwargs.pop("cutoff", CUTOFF),
        evaluated_at_utc=kwargs.pop("evaluated_at", None) or NOW,
        consumed_input_watermark=watermark,
        source_version=SOURCE,
    )


def _retained_state_for_tests(evidence):
    from app.opip.features.r2_shadow_parity import _retained_state

    state, _checkpoint = _retained_state(
        evidence, instrument_version=_instrument(), interval_seconds=60
    )
    return state


def _replay_result(
    observations,
    *,
    prior_state=None,
    coverage_only=(),
    committed_write_watermarks=(),
    created_at: datetime | None = None,
    cutoff: datetime = CUTOFF,
    evaluated_at: datetime | None = None,
    instrument: InstrumentVersion | None = None,
    watermark: ConsumedInputWatermark | None = None,
):
    """Replay a captured cycle and keep the whole result, not just the snapshot.

    When a watermark is not supplied it is derived from the capture, which is
    what the replay contract now requires: the sealed position must equal the
    position the captured evidence supports.
    """
    evidence = _capture(
        observations,
        prior_state=prior_state,
        coverage_only=coverage_only,
        committed_write_watermarks=committed_write_watermarks,
        created_at=created_at,
    )
    return _replay_evidence_result(
        evidence,
        cutoff=cutoff,
        evaluated_at=evaluated_at,
        instrument=instrument,
        watermark=watermark,
    )


def _resumed_case(*, retained_count: int = FEATURE_WINDOW_INTERVALS, tip_count: int = 2):
    """A real resume: retained window plus genuinely new tip intervals."""
    rows = _rows(count=retained_count + tip_count)
    observations, _normalized = _observations(rows)
    retained = advance_state(initial_state(_instrument()), observations[:retained_count]).state
    tip = observations[retained_count:]
    assert retained.interval_count == retained_count
    assert tip, "the tip must contain new intervals"
    assert min(item.source_event_time for item in tip) > max(
        retained.interval_start_at(index)
        for index in range(retained.interval_count)
    )
    return observations, retained, tip


def _row_map(report):
    grouped: dict[str, list] = {}
    for row in report.rows:
        grouped.setdefault(row.feature_name, []).append(row)
    return grouped


def test_complete_observation_matches_indicator_math_and_replays():
    observations, normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS)
    )
    assert normalized.rejected_count == 0
    snapshot, report = _replay(observations)
    payload = report.to_dict()
    assert payload["feature_version"] == FEATURE_VERSION
    assert payload["snapshot_id"] == snapshot.snapshot_id
    assert payload["absolute_tolerance"] == 0.0
    assert payload["counts"]["IMPLEMENTATION_DEFECT"] == 0
    assert payload["counts"]["ARCHITECTURE_GAP"] == 0
    assert set(payload["counts"]) == set(CLASSIFICATIONS) | {"total"}

    grouped = _row_map(report)
    for name in (
        "ema_fast_9",
        "ema_slow_21",
        "atr_pct_14",
        "bandwidth_20",
        "relative_volume_20m",
    ):
        assert grouped[name][0].classification == "MATCH"
        assert grouped[name][0].legacy_value == grouped[name][0].feature_bus_value
        assert grouped[name][0].feature_version == FEATURE_VERSION
        assert grouped[name][0].source_evidence_identity == payload[
            "source_evidence_identity"
        ]

    again, again_report = _replay(observations)
    assert again.snapshot_id == snapshot.snapshot_id
    assert again.content_hash() == snapshot.content_hash()
    assert again_report.to_dict() == payload

    instrument = _instrument()
    resumed = reconstruct_state(
        to_checkpoint(
            advance_state(initial_state(instrument), observations[:100]).state,
            created_at_utc=NOW,
        ),
        observations[100:],
    )
    equivalence = compare_resumed_state(
        uninterrupted=align_minute_observations(observations, cutoff=CUTOFF),
        resumed_state=resumed,
        instrument_version=instrument,
        evaluated_at_utc=NOW,
    )
    assert equivalence.equivalent, equivalence.to_dict()


def test_missing_optional_inputs_still_replay():
    rows = _rows(count=FEATURE_WINDOW_INTERVALS)
    assert all(row.vwap is None and row.trade_count is None for row in rows)
    observations, _normalized = _observations(rows)
    assert all("vwap" not in item.values for item in observations)
    snapshot, report = _replay(observations)
    assert snapshot.values["ema_fast_9"] is not None
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0


def test_missing_required_inputs_fail_closed():
    bad = IntervalRow(
        interval_start_epoch=int((CUTOFF - timedelta(minutes=1)).timestamp()),
        open=100.0,
        high=101.0,
        low=99.0,
        close=0.0,
        volume=10.0,
    )
    _observations_ignored, normalized = _observations([bad])
    assert normalized.rejected_count == 1
    assert normalized.rejected[0].reason == "non_positive_price"
    assert normalized.observations == ()

    snapshot, report = _replay(())
    assert snapshot.values["return_1m"] is None
    assert snapshot.missingness["return_1m"] is Missingness.MISSING
    assert snapshot.values["relative_volume_20m"] is None
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0

    observations, _normalized = _observations(_rows(count=3))
    payload = list(capture_observation_evidence(observations))
    payload[0]["values"] = dict(payload[0]["values"])
    payload[0]["values"]["close"] = None
    with pytest.raises(ValueError, match="missing values"):
        load_observation_evidence(payload)

def test_stale_input_is_visible_and_does_not_change_indicator_parity():
    rows = _rows(count=FEATURE_WINDOW_INTERVALS)
    fresh, _normalized = _observations(rows, receipt_time=NOW)
    stale_receipt = NOW + timedelta(hours=2)
    stale, _normalized = _observations(rows, receipt_time=stale_receipt, now=stale_receipt)
    fresh_snapshot, fresh_report = _replay(fresh)
    stale_snapshot, stale_report = _replay(stale, evaluated_at=stale_receipt)
    alignment = align_minute_observations(stale, cutoff=CUTOFF)
    assert alignment.late_arrivals
    assert fresh_snapshot.values["ema_fast_9"] == stale_snapshot.values["ema_fast_9"]
    assert fresh_report.counts()["IMPLEMENTATION_DEFECT"] == 0
    assert stale_report.counts()["IMPLEMENTATION_DEFECT"] == 0
    assert stale_snapshot.values["staleness_seconds"] == fresh_snapshot.values[
        "staleness_seconds"
    ]


def test_out_of_order_input_replays_to_the_same_snapshot():
    observations, _normalized = _observations(_rows(count=FEATURE_WINDOW_INTERVALS))
    forward, _report = _replay(observations)
    backward, _report = _replay(tuple(reversed(observations)))
    assert backward.snapshot_id == forward.snapshot_id
    assert backward.content_hash() == forward.content_hash()
    assert backward.values == forward.values


def test_revised_input_replaces_the_prior_bar_without_deleting_it():
    observations, _normalized = _observations(_rows(count=FEATURE_WINDOW_INTERVALS))
    original = observations[-1]
    revised_values = dict(original.values)
    revised_values["close"] = float(revised_values["close"]) + 1.0
    revised_values["high"] = max(float(revised_values["high"]), revised_values["close"])
    corrected = replace(
        original,
        revision=2,
        supersedes=original.observation_id,
        values=revised_values,
        ingestion_order=original.ingestion_order + 1,
    )
    snapshot, report = _replay(observations + (corrected,))
    alignment = align_minute_observations(observations + (corrected,), cutoff=CUTOFF)
    assert original in alignment.superseded
    assert alignment.observations[-1].revision == 2
    assert snapshot.values["return_1m"] is not None
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0
    unchanged, _report = _replay(observations)
    assert snapshot.values["ema_fast_9"] != unchanged.values["ema_fast_9"]


def test_duplicate_input_does_not_double_count():
    rows = _rows(count=30)
    duplicated = rows + [rows[-1]]
    _kept, normalized = _observations(duplicated)
    assert any(item.reason == "duplicate_interval" for item in normalized.rejected)

    observations, _normalized = _observations(rows)
    once, once_report = _replay(observations)
    twice, twice_report = _replay(observations + (observations[-1],))
    assert twice.snapshot_id == once.snapshot_id
    assert twice.values == once.values
    assert twice_report.counts() == once_report.counts()


def test_boundary_numeric_values_match_exactly():
    observations, normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS, start_price=1e-8, step=1e-10, volume=1e-6)
    )
    assert normalized.rejected_count == 0
    _snapshot, report = _replay(observations)
    grouped = _row_map(report)
    for name in ("ema_fast_9", "atr_pct_14", "bandwidth_20", "relative_volume_20m"):
        row = grouped[name][0]
        assert row.classification == "MATCH"
        assert row.legacy_value == row.feature_bus_value


def test_zero_volume_sparse_market_matches_production_volume_ratio():
    observations, normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS, volume=0.0)
    )
    assert normalized.rejected_count == 0
    snapshot, report = _replay(observations)
    grouped = _row_map(report)
    volume = grouped["relative_volume_20m"][0]
    assert volume.classification == "MATCH"
    assert volume.legacy_value == 0.0
    assert snapshot.values["relative_volume_20m"] == 0.0
    assert snapshot.values["book_depth_imbalance"] is None
    assert snapshot.missingness["book_depth_imbalance"] is Missingness.NOT_RETAINED
    retained = grouped["book_depth_imbalance"][0]
    assert retained.classification == "INTENTIONAL_SEMANTIC_DIFFERENCE"


def test_timestamp_boundary_is_grid_exact():
    observations, _normalized = _observations(_rows(count=5))
    snapshot, _report = _replay(observations, cutoff=CUTOFF)
    assert snapshot.evaluation_cutoff == CUTOFF
    with pytest.raises(ValueError, match="grid"):
        _replay(observations, cutoff=CUTOFF + timedelta(seconds=1))
    with pytest.raises(ValueError, match="grid"):
        _replay(observations, cutoff=CUTOFF.replace(microsecond=1))


def test_same_evidence_replayed_twice_is_byte_identical():
    observations, _normalized = _observations(_rows(count=FEATURE_WINDOW_INTERVALS))
    payload = capture_observation_evidence(observations)
    loaded = load_observation_evidence(payload)
    assert capture_observation_evidence(loaded) == payload
    first, first_report = _replay(observations)
    second, second_report = _replay(loaded)
    assert first.to_dict() == second.to_dict()
    assert first_report.to_dict() == second_report.to_dict()


def test_feature_version_mismatch_refuses_replay():
    observations, _normalized = _observations(_rows(count=5))
    with pytest.raises(FeatureVersionMismatch, match="replay refused"):
        replay_feature_snapshot(
            observations,
            instrument_version=_instrument(),
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
            declared_feature_version="features-v0",
        )


def test_flat_series_percentile_difference_is_intentional():
    observations, _normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS, flat=True)
    )
    _snapshot, report = _replay(observations)
    grouped = _row_map(report)
    scan_rows = [
        row
        for row in grouped["bandwidth_percentile"]
        if row.legacy_source == "app.services.signal_features.percentile_rank"
    ]
    assert len(scan_rows) == 1
    assert scan_rows[0].classification == "INTENTIONAL_SEMANTIC_DIFFERENCE"
    assert scan_rows[0].feature_bus_value == 100.0
    assert scan_rows[0].legacy_value == 50.0
    indicator_rows = [
        row
        for row in grouped["bandwidth_percentile"]
        if row.legacy_source == "app.indicators.technical.percentile_rank"
    ]
    assert indicator_rows[0].classification == "MATCH"
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0


def test_fields_without_a_legacy_emitter_stay_unavailable():
    observations, _normalized = _observations(_rows(count=FEATURE_WINDOW_INTERVALS))
    _snapshot, report = _replay(observations)
    unavailable = {
        row.feature_name
        for row in report.rows
        if row.classification == "LEGACY_EVIDENCE_UNAVAILABLE"
    }
    assert "return_1m" in unavailable
    assert "compression_state" in unavailable
    assert "ema_fast_9" not in unavailable
    assert all(row.legacy_value is None for row in report.rows if row.feature_name in unavailable)


def test_feature_bus_mode_stays_off_and_cycle_does_not_call_it():
    assert resolve_feature_bus_mode(type("S", (), {"opip_feature_bus_mode": "off"})()) == "off"
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose
    cycle = (root / "app" / "jobs" / "run_cycle.py").read_text(encoding="utf-8")
    assert "run_feature_bus_pilot" not in cycle
    assert "opip.features" not in cycle


# --------------------------------------------------------------------------- #
# Finding 1 - replay fails closed on instrument identity mismatch
# --------------------------------------------------------------------------- #


def test_replay_refuses_foreign_instrument_version_id():
    observations, _normalized = _observations(_rows(count=5))
    foreign = _instrument(version=2)
    with pytest.raises(
        CycleIdentityMismatch, match="instrument_version_id|instrument version"
    ):
        _replay(observations, instrument=foreign)


def test_replay_refuses_foreign_venue():
    observations, _normalized = _observations(_rows(count=5))
    tampered = tuple(replace(item, venue="binance") for item in observations)
    # instrument_version_id already encodes the venue, so this row is corrupt
    # evidence whose derived id must be reconciled, not relabelled.
    assert all(item.instrument_version_id == _instrument().instrument_version_id for item in tampered)
    with pytest.raises(CycleIdentityMismatch, match="venue"):
        _replay(tampered)


def test_replay_refuses_foreign_venue_instrument_id():
    observations, _normalized = _observations(_rows(count=5))
    tampered = tuple(
        replace(item, venue_instrument_id="BTCUSD") for item in observations
    )
    with pytest.raises(CycleIdentityMismatch, match="venue_instrument_id"):
        _replay(tampered)


def test_replay_refuses_mixed_instrument_evidence_before_alignment():
    first, _normalized = _observations(_rows(count=5))
    second, _normalized = _observations(
        _rows(count=5), instrument=_instrument(version=2)
    )
    # Identical timestamps across two instruments are exactly the collision
    # hazard: identity is refused rather than deduplicated into one series.
    assert {item.source_event_time for item in first} == {
        item.source_event_time for item in second
    }
    with pytest.raises(CycleIdentityMismatch, match="instrument_version_id"):
        _replay(first + second)


def test_matching_instrument_evidence_still_replays_identically():
    observations, _normalized = _observations(_rows(count=FEATURE_WINDOW_INTERVALS))
    first, first_report = _replay(observations)
    again, again_report = _replay(observations)
    assert again.snapshot_id == first.snapshot_id
    assert again.content_hash() == first.content_hash()
    assert again_report.to_dict() == first_report.to_dict()


# --------------------------------------------------------------------------- #
# Finding 2 - parity describes the exact snapshot it names
# --------------------------------------------------------------------------- #


def test_parity_uses_snapshot_values_when_the_last_bar_precedes_cutoff():
    observations, _normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS, end_before=CUTOFF - timedelta(minutes=10))
    )
    snapshot, report = _replay(observations, cutoff=CUTOFF)
    staleness = snapshot.values["staleness_seconds"]
    assert staleness == 600.0
    rows = {
        row.feature_name: row
        for row in report.rows
        if row.feature_name == "staleness_seconds"
    }
    assert rows["staleness_seconds"].feature_bus_value == 600.0
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0
    assert report.snapshot_id == snapshot.snapshot_id
    assert report.source_evidence_identity == source_evidence_identity(
        _capture(observations), evaluation_cutoff=CUTOFF
    )
    assert report.feature_version == snapshot.feature_version
    indicator_rows = {
        row.feature_name: row
        for row in report.rows
        if row.feature_name in ("ema_fast_9", "atr_pct_14", "bandwidth_20")
    }
    assert tuple(indicator_rows[name].feature_bus_value for name in indicator_rows) == tuple(
        snapshot.values[name] for name in indicator_rows
    )
    assert all(row.classification == "MATCH" for row in indicator_rows.values())
    assert set(snapshot.values) == set(FEATURE_NAMES)


# --------------------------------------------------------------------------- #
# Finding 3 - the consumed input watermark cannot lie
# --------------------------------------------------------------------------- #


def test_watermark_equal_to_max_captured_commit_order_is_allowed():
    observations, _normalized = _observations(_rows(count=5))
    highest = max(item.commit_order for item in observations)
    snapshot, _report = _replay(observations, watermark=highest)
    assert snapshot.consumed_input_watermark == highest


def test_watermark_later_than_the_captured_position_is_refused():
    """A position the capture does not support may not be sealed."""
    observations, _normalized = _observations(_rows(count=5))
    later = ConsumedInputWatermark(history_epoch=1, local_sequence=99999)
    with pytest.raises(WatermarkIntegrityError, match="is not the position"):
        _replay(observations, watermark=later)


def test_declared_canonical_write_position_is_allowed():
    """A cycle that wrote must declare the write it consumed."""
    observations, _normalized = _observations(_rows(count=5))
    written = ConsumedInputWatermark(history_epoch=1, local_sequence=99999)
    snapshot, _report = _replay(
        observations, watermark=written, committed_write_watermarks=(written,)
    )
    assert snapshot.consumed_input_watermark == written


def test_watermark_earlier_than_captured_evidence_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    earlier = ConsumedInputWatermark(history_epoch=1, local_sequence=0)
    with pytest.raises(WatermarkIntegrityError, match="is not the position"):
        _replay(observations, watermark=earlier)


def test_mixed_commit_order_evidence_cannot_prove_its_watermark():
    observations, _normalized = _observations(_rows(count=5))
    payload = _capture(observations).to_dict()
    payload["evidence"][0] = dict(payload["evidence"][0])
    payload["evidence"][0]["history_epoch"] = None
    payload["evidence"][0]["local_sequence"] = None
    loaded = load_observation_evidence(payload["evidence"])
    assert loaded[0].commit_order is None
    assert loaded[-1].commit_order is not None
    with pytest.raises(WatermarkIntegrityError, match="mixes committed"):
        replay_captured_evidence(
            payload,
            instrument_version=_instrument(),
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
        )


def test_uncommitted_forming_bar_does_not_break_the_watermark():
    rows = _rows(count=5) + [
        IntervalRow(
            interval_start_epoch=int(CUTOFF.timestamp()),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.2,
            volume=10.0,
        )
    ]
    observations, _normalized = _observations(rows, now=CUTOFF)
    forming = tuple(item for item in observations if item.interval_forming)
    closed = tuple(item for item in observations if not item.interval_forming)
    assert len(forming) == 1
    assert max(item.commit_order for item in closed) < forming[0].commit_order
    payload = _capture(observations).to_dict()
    for index, row in enumerate(payload["evidence"]):
        if row["observation_id"] == forming[0].observation_id:
            row = dict(row)
            row["history_epoch"] = None
            row["local_sequence"] = None
            payload["evidence"][index] = row
    loaded = load_observation_evidence(payload["evidence"])
    assert tuple(item.commit_order for item in loaded if item.interval_forming) == (None,)
    snapshot, report = replay_captured_evidence(
        payload,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=_watermark(closed),
        source_version=SOURCE,
    )
    assert snapshot.consumed_input_watermark == _watermark(closed)
    assert snapshot.values["return_1m"] is not None
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0


def test_fully_uncommitted_evidence_carries_no_watermark_claim():
    observations, _normalized = _observations(_rows(count=5))
    payload = _capture(observations).to_dict()
    for index, row in enumerate(payload["evidence"]):
        row = dict(row)
        row["history_epoch"] = None
        row["local_sequence"] = None
        payload["evidence"][index] = row
    assert all(
        item.commit_order is None
        for item in load_observation_evidence(payload["evidence"])
    )
    snapshot, _report = replay_captured_evidence(
        payload,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=ConsumedInputWatermark(0, 0),
        source_version=SOURCE,
    )
    committed, _report = _replay(observations)
    assert snapshot.consumed_input_watermark == ConsumedInputWatermark(0, 0)
    assert snapshot.values == committed.values


# --------------------------------------------------------------------------- #
# Findings A and B - cutoff population and consumed-input lineage
# --------------------------------------------------------------------------- #


def _wrong_cadence(observation, *, epoch: datetime, ingestion_offset: int = 10):
    """A snapshot-affecting misaligned row: right shape, wrong cadence."""
    return replace(
        observation,
        aggregate_interval_seconds=300,
        source_event_time=epoch,
        ingestion_order=observation.ingestion_order + ingestion_offset,
    )


def _ticker(observation, *, epoch: datetime, ingestion_offset: int = 10):
    """A snapshot-affecting misaligned row that carries no aggregate at all."""
    return replace(
        observation,
        payload_kind=PayloadKind.TICKER,
        aggregate_interval_seconds=None,
        source_event_time=epoch,
        ingestion_order=observation.ingestion_order + ingestion_offset,
    )


def test_post_cutoff_wrong_cadence_row_is_refused():
    observations, _normalized = _observations(_rows(count=5))
    post = _wrong_cadence(observations[-1], epoch=CUTOFF + timedelta(minutes=4))
    assert post.source_event_time > CUTOFF
    assert post.receipt_time <= NOW
    with pytest.raises(TemporalIntegrityError, match="misaligned fact that closes"):
        _replay(observations + (post,))


def test_post_cutoff_non_aggregate_row_is_refused():
    observations, _normalized = _observations(_rows(count=5))
    post = _ticker(observations[-1], epoch=CUTOFF + timedelta(minutes=2))
    assert post.source_event_time > CUTOFF
    assert post.receipt_time <= NOW
    with pytest.raises(TemporalIntegrityError, match="misaligned fact that closes"):
        _replay(observations + (post,))


def test_pre_cutoff_misaligned_row_is_part_of_the_population():
    observations, _normalized = _observations(_rows(count=5))
    pre_cutoff = _wrong_cadence(observations[2], epoch=CUTOFF - timedelta(minutes=6))
    alignment = align_minute_observations(observations + (pre_cutoff,), cutoff=CUTOFF)
    assert alignment.excluded_misaligned == 1
    assert alignment.excluded_misaligned_rows == (pre_cutoff,)
    snapshot, report = _replay(observations + (pre_cutoff,))
    assert snapshot.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0


def test_post_cutoff_closed_aggregate_cannot_change_the_snapshot():
    observations, _normalized = _observations(_rows(count=5))
    # Cadence and grid are correct, so this is only excluded as unclosed.
    extra = replace(
        observations[-1],
        source_event_time=CUTOFF + timedelta(minutes=1),
        ingestion_order=observations[-1].ingestion_order + 10,
    )
    alignment = align_minute_observations(observations + (extra,), cutoff=CUTOFF)
    assert alignment.excluded_unclosed == 1
    assert alignment.excluded_misaligned == 0
    baseline, _report = _replay(observations)
    with_extra, _report = _replay(observations + (extra,))
    assert with_extra.coverage is baseline.coverage
    assert with_extra.values == baseline.values
    assert with_extra.content_hash() == baseline.content_hash()


def test_misaligned_row_open_at_the_cutoff_is_refused():
    observations, _normalized = _observations(_rows(count=7))
    # A five-minute bar starting one minute before the cutoff is still open at
    # it, even though its source event time is inside the population.
    opened = _wrong_cadence(observations[6], epoch=CUTOFF - timedelta(minutes=1))
    assert opened.source_event_time <= CUTOFF
    assert opened.interval_end is not None and opened.interval_end > CUTOFF
    with pytest.raises(TemporalIntegrityError, match="closes at"):
        _replay(observations + (opened,))


def test_same_epoch_at_a_different_cadence_is_not_a_conflict():
    observations, _normalized = _observations(_rows(count=7))
    admitted = observations[1]
    assert admitted.source_event_time == CUTOFF - timedelta(minutes=6)
    overlapping = replace(
        _wrong_cadence(admitted, epoch=admitted.source_event_time, ingestion_offset=0),
        values={**dict(admitted.values), "volume": float(admitted.values["volume"]) + 5.0},
    )
    assert overlapping.aggregate_interval_seconds != admitted.aggregate_interval_seconds
    assert overlapping.ingestion_order == admitted.ingestion_order
    assert overlapping.interval_end <= CUTOFF
    snapshot, report = _replay(observations + (overlapping,))
    assert snapshot.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0


def test_availability_matches_the_production_derivation():
    observations, _normalized = _observations(_rows(count=5))
    later = NOW + timedelta(minutes=30)
    # A coverage-affecting row received after the admitted bars. Production
    # derives availability from the contiguous feature tail of the cycle
    # alignment, so it does not move the sealed stamp; replay must reproduce
    # that derivation rather than invent a wider one.
    covered = replace(
        _wrong_cadence(observations[2], epoch=CUTOFF - timedelta(minutes=6)),
        receipt_time=later,
    )
    assert covered.receipt_time > max(item.receipt_time for item in observations)
    result = _replay_result(
        observations + (covered,),
        evaluated_at=later + timedelta(minutes=1),
    )
    assert result.snapshot.coverage is CoverageState.INCOMPLETE_COVERAGE
    tail = result.cycle_alignment.observations
    assert result.snapshot.availability.visible_at_utc == max(
        item.availability_stamp(source_version=SOURCE).visible_at_utc for item in tail
    )
    assert result.snapshot.availability.visible_at_utc == NOW


def test_roll_forward_uses_retained_state_not_the_flat_fetch():
    """A resumed cycle must not be reproduced from the tip rows alone."""
    _all, retained, tip = _resumed_case()
    cold = _replay_result(tip)
    resumed = _replay_result(tip, prior_state=retained)
    # The flat-fetch reconstruction cannot compute a slow EMA from two bars.
    assert cold.snapshot.values["ema_slow_21"] is None
    assert cold.state.interval_count == len(tip)
    # The resumed replay rebuilds the full retained window, exactly as production.
    assert resumed.state.interval_count == FEATURE_WINDOW_INTERVALS
    assert resumed.snapshot.values["ema_slow_21"] is not None
    assert resumed.snapshot.values["ema_slow_21"] != cold.snapshot.values["ema_slow_21"]
    assert resumed.snapshot.values["contiguous_intervals"] == FEATURE_WINDOW_INTERVALS
    assert resumed.snapshot.restart_state is RestartState.WARM
    assert resumed.snapshot.content_hash() != cold.snapshot.content_hash()


def test_replay_matches_the_production_cycle_snapshot():
    """The replayed snapshot is byte-identical to the production path's."""
    from app.opip.features import pipeline

    _all, retained, tip = _resumed_case()
    result = _replay_result(tip, prior_state=retained)
    # Production's own snapshot builder, driven with the same inputs run_cycle
    # would derive in dry-run mode: watermark advanced by the consumed rows.
    candidate = result.state
    expected, _checkpoint = pipeline._build_snapshot_and_checkpoint(
        state=candidate,
        alignment=result.cycle_alignment,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        source_version=SOURCE,
    )
    assert result.snapshot.to_dict() == expected.to_dict()
    assert result.snapshot.content_hash() == expected.content_hash()
    assert result.snapshot.snapshot_id == expected.snapshot_id


def test_coverage_only_rows_are_not_treated_as_feature_evidence():
    """A withheld tip re-poll keeps continuity without adding feature values."""
    observations, _normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS)
    )
    retained = advance_state(initial_state(_instrument()), observations).state
    # The retained tip re-polled unchanged: production keeps it coverage-only.
    repoll = observations[-1:]
    watermark = max(
        retained.consumed_input_watermark, _watermark(observations), _watermark(repoll)
    )
    plain = _replay_result((), prior_state=retained, watermark=watermark)
    withheld = _replay_result(
        (), prior_state=retained, coverage_only=repoll, watermark=watermark
    )
    # Coverage-only continuity fills the expected window but adds no bar.
    assert withheld.snapshot.values["contiguous_intervals"] == retained.interval_count
    assert withheld.snapshot.values["coverage_ratio"] == 1.0
    assert plain.snapshot.values["coverage_ratio"] == 0.0
    assert withheld.snapshot.values["missing_intervals"] == 0
    # And it is never folded into feature evidence: the retained series is the
    # same, so every rolling value is unchanged.
    assert withheld.state.interval_count == retained.interval_count
    for name, value in plain.snapshot.values.items():
        if name in ("coverage_ratio", "missing_intervals", "late_arrival_count"):
            continue
        assert withheld.snapshot.values[name] == value, name
    assert withheld.snapshot.content_hash() != plain.snapshot.content_hash()


def test_resumed_cycle_without_retained_state_fails_closed():
    observations, _normalized = _observations(_rows(count=5))
    # A hand-written envelope cannot claim a resume and omit the retained state.
    payload = _capture(observations).to_dict()
    payload["cycle_origin"] = CYCLE_ORIGIN_RESUMED
    with pytest.raises(ValueError, match="requires prior retained state"):
        load_replay_evidence(payload)
    # Nor can it omit the field entirely.
    payload = _capture(observations).to_dict()
    del payload["prior_state"]
    with pytest.raises(ValueError, match="missing required keys"):
        load_replay_evidence(payload)


def test_cold_start_cannot_declare_retained_state_or_coverage_only():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    payload = _capture(observations, prior_state=retained).to_dict()
    payload["cycle_origin"] = CYCLE_ORIGIN_COLD_START
    with pytest.raises(ValueError, match="cold start but carries prior"):
        load_replay_evidence(payload)
    payload = _capture(observations).to_dict()
    payload["coverage_only"] = [dict(item) for item in payload["evidence"]]
    with pytest.raises(ValueError, match="cold start with coverage-only"):
        load_replay_evidence(payload)
    with pytest.raises(ValueError, match="not one of"):
        load_replay_evidence(
            {**_capture(observations).to_dict(), "cycle_origin": "mid"}
        )


def test_retained_state_identity_and_version_mismatch_fails_closed():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    other = advance_state(initial_state(_instrument(version=2)), observations).state
    with pytest.raises(CycleIdentityMismatch, match="instrument_version_id"):
        _replay(observations, prior_state=other)
    payload = _capture(observations, prior_state=retained).to_dict()
    payload["prior_state"] = dict(payload["prior_state"])
    payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
    payload["prior_state"]["checkpoint"]["feature_version"] = "features-v0"
    # The checkpoint id binds the feature version, so a bare edit is caught as a
    # tampered identity rather than accepted.
    with pytest.raises(EvidenceIntegrityError, match="checkpoint_id"):
        _replay(observations, prior_state=load_replay_evidence(payload).prior_state)
    # A consistently stamped older engine version is refused by the identity rule.
    older = to_checkpoint(
        replace(retained, feature_version="features-v0"), created_at_utc=NOW
    )
    with pytest.raises(CycleIdentityMismatch, match="feature_version"):
        _replay(observations, prior_state=_capture(
            observations, prior_state=older
        ).prior_state)
    # Corrupt retained state is refused by the canonical loader, not repaired.
    payload = _capture(observations, prior_state=retained).to_dict()
    payload["prior_state"] = dict(payload["prior_state"])
    payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
    payload["prior_state"]["checkpoint"]["consumed_input_watermark"] = {
        "history_epoch": True
    }
    with pytest.raises(EvidenceIntegrityError, match="retained state"):
        _replay_result(
            observations,
            prior_state=load_replay_evidence(payload).prior_state,
        )


def test_checkpoint_created_after_the_replay_instant_is_refused():
    """Retained evidence must have existed at the replay instant."""
    observations, _normalized = _observations(_rows(count=30))
    base = advance_state(initial_state(_instrument()), observations).state
    late = NOW + timedelta(hours=2)
    checkpoint = to_checkpoint(
        replace(base, resumed_from_checkpoint=True, restart_state=RestartState.RESTART_WARMUP),
        created_at_utc=late,
    )
    assert checkpoint.created_at_utc == late
    captured = _capture((), prior_state=checkpoint)
    assert captured.prior_state_resumed_from_checkpoint is True
    with pytest.raises(TemporalIntegrityError, match="not available at the replay"):
        _replay_evidence_result(captured)
    # The same retained state is admissible once the replay instant has passed it.
    later = late + timedelta(minutes=1)
    result = _replay_evidence_result(captured, evaluated_at=later)
    assert result.snapshot.restart_state is RestartState.RESTART_WARMUP
    # And an in-time checkpoint is unaffected.
    in_time = to_checkpoint(base, created_at_utc=NOW)
    assert (
        _replay_evidence_result(
            _capture((), prior_state=in_time)
        ).state.interval_count
        == base.interval_count
    )


def test_unsupported_nested_rolling_state_is_refused():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    for mutate, message in (
        (lambda rs: rs.update({"future_field": "drift"}), "unexpected keys"),
        (lambda rs: rs.pop("ema_fast"), "missing keys"),
        (lambda rs: rs.update({"venue": 7}), None),
        # A fractional horizon must be refused before any conversion, not
        # truncated into a different interval the durable payload never declared.
        (
            lambda rs: rs.update(
                {"first_interval_epoch": rs["first_interval_epoch"] + 0.5}
            ),
            "first_interval_epoch",
        ),
        (
            lambda rs: rs.update(
                {"first_interval_epoch": str(rs["first_interval_epoch"])}
            ),
            "first_interval_epoch",
        ),
        (lambda rs: rs.update({"first_interval_epoch": True}), "first_interval_epoch"),
        (lambda rs: rs.update({"interval_seconds": 60.0}), "interval_seconds"),
        (lambda rs: rs.update({"interval_seconds": "60"}), "interval_seconds"),
        (lambda rs: rs.update({"interval_seconds": True}), "interval_seconds"),
        (lambda rs: rs.update({"interval_seconds": 0}), "interval_seconds"),
        (
            lambda rs: rs.update({"last_receipt_epoch": float("nan")}),
            "last_receipt_epoch",
        ),
        (lambda rs: rs.update({"last_receipt_epoch": True}), "last_receipt_epoch"),
    ):
        payload = _capture(observations, prior_state=retained).to_dict()
        payload["prior_state"] = dict(payload["prior_state"])
        payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
        rolling = dict(payload["prior_state"]["checkpoint"]["rolling_state"])
        mutate(rolling)
        payload["prior_state"]["checkpoint"]["rolling_state"] = rolling
        if message is None:
            # A mistyped venue is refused by the contract, not reinterpreted.
            with pytest.raises(ValueError):
                _replay_evidence_result(load_replay_evidence(payload))
            continue
        with pytest.raises(ValueError, match=message):
            load_replay_evidence(payload)
    # An intact retained horizon still round-trips and replays identically.
    intact = _capture(observations, prior_state=retained)
    restored = load_replay_evidence(intact.to_dict())
    assert restored.prior_state is not None
    assert (
        _replay_evidence_result(intact).snapshot.to_dict()
        == _replay_evidence_result(restored).snapshot.to_dict()
    )


def test_cold_start_capture_with_committed_write_watermarks_round_trips():
    """A first cycle publishes its own observations, so it has writes too."""
    observations, _normalized = _observations(_rows(count=5))
    written = ConsumedInputWatermark(history_epoch=1, local_sequence=99999)
    captured = _capture(
        observations, committed_write_watermarks=(written,)
    )
    assert captured.cycle_origin == CYCLE_ORIGIN_COLD_START
    restored = load_replay_evidence(captured.to_dict())
    assert restored.committed_write_watermarks == (written,)
    first = _replay_evidence_result(captured)
    second = _replay_evidence_result(restored)
    assert first.snapshot.to_dict() == second.snapshot.to_dict()
    assert first.snapshot.consumed_input_watermark == written
    # And the position genuinely has to be declared.
    with pytest.raises(WatermarkIntegrityError, match="is not the position"):
        _replay(observations, watermark=written)


def test_domain_invalid_retained_slots_are_refused():
    """Retained values must satisfy the source rules, not just be numeric."""
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    for field, mutate, reason in (
        ("closes", lambda rolling: rolling["closes"].__setitem__(-1, -1.0), "non_positive_price"),
        ("volumes", lambda rolling: rolling["volumes"].__setitem__(-1, -5.0), "negative_volume"),
        (
            "highs",
            # Between the low and the close: legal against the low, below the body.
            lambda rolling: rolling["highs"].__setitem__(
                -1, rolling["closes"][-1] * 0.999
            ),
            "high_below_body",
        ),
    ):
        payload = _capture(observations, prior_state=retained).to_dict()
        payload["prior_state"] = dict(payload["prior_state"])
        payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
        rolling = {
            key: list(value) if isinstance(value, (list, tuple)) else value
            for key, value in payload["prior_state"]["checkpoint"]["rolling_state"].items()
        }
        mutate(rolling)
        payload["prior_state"]["checkpoint"]["rolling_state"] = rolling
        with pytest.raises(ValueError, match=reason):
            load_replay_evidence(payload)
    # An intact retained series still round-trips.
    assert load_replay_evidence(
        _capture(observations, prior_state=retained).to_dict()
    ).prior_state is not None


def test_retained_state_received_after_the_replay_instant_is_refused():
    """A retained state may not outrun either of its own clocks."""
    later = CUTOFF + timedelta(minutes=5)
    observations, _normalized = _observations(
        _rows(count=30, end_before=later), receipt_time=later, now=later
    )
    retained = advance_state(initial_state(_instrument()), observations).state
    assert retained.last_receipt_epoch == later.timestamp()
    # A receipt later than the state's own creation clock is impossible.
    with pytest.raises(TemporalIntegrityError, match="had not yet received"):
        _replay_result(
            (),
            prior_state=retained,
            cutoff=later,
            evaluated_at=later,
            created_at=NOW,
            watermark=retained.consumed_input_watermark,
        )
    # Visible at the later instant the same state is admissible, and replaying it
    # at an earlier instant is refused because the state did not exist yet.
    result = _replay_result(
        (),
        prior_state=retained,
        cutoff=later,
        evaluated_at=later,
        created_at=later,
        watermark=retained.consumed_input_watermark,
    )
    assert result.snapshot.values["contiguous_intervals"] == retained.interval_count
    with pytest.raises(TemporalIntegrityError, match="not available at the replay"):
        _replay_result(
            (),
            prior_state=retained,
            cutoff=later,
            evaluated_at=later - timedelta(minutes=4),
            created_at=later,
            watermark=retained.consumed_input_watermark,
        )


def test_unsupported_reconstruction_dependency_is_refused():
    """A checkpoint that names evidence replay cannot supply must be refused."""
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    for declared in (
        ["trade_tape"],
        ["fixed_interval_aggregate:60s"],
        [],
    ):
        payload = _capture(observations, prior_state=retained).to_dict()
        payload["prior_state"] = dict(payload["prior_state"])
        payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
        payload["prior_state"]["checkpoint"]["reconstruction_dependencies"] = declared
        with pytest.raises(ValueError, match="reconstruction"):
            load_replay_evidence(payload)
    # The declared set round-trips untouched.
    assert load_replay_evidence(
        _capture(observations, prior_state=retained).to_dict()
    ).prior_state is not None


def test_tampered_checkpoint_id_is_refused():
    """The declared canonical checkpoint identity must match its contents."""
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    payload = _capture(observations, prior_state=retained).to_dict()
    payload["prior_state"] = dict(payload["prior_state"])
    payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
    payload["prior_state"]["checkpoint"]["checkpoint_id"] = "FSC:1:solusd:features-v1:1-1"
    with pytest.raises(EvidenceIntegrityError, match="checkpoint_id"):
        _replay_evidence_result(load_replay_evidence(payload))
    # A missing or mistyped id is refused too.
    for value in (None, 7):
        payload = _capture(observations, prior_state=retained).to_dict()
        payload["prior_state"] = dict(payload["prior_state"])
        payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
        payload["prior_state"]["checkpoint"]["checkpoint_id"] = value
        with pytest.raises(EvidenceIntegrityError, match="checkpoint_id"):
            _replay_evidence_result(load_replay_evidence(payload))


def test_receipt_later_than_checkpoint_creation_is_refused():
    """A cycle cannot retain a receipt it had not yet received."""
    observations, _normalized = _observations(_rows(count=30))
    retained = advance_state(initial_state(_instrument()), observations).state
    assert retained.last_receipt_epoch is not None
    early = datetime.fromtimestamp(
        retained.last_receipt_epoch, tz=timezone.utc
    ) - timedelta(minutes=5)
    payload = _capture(
        (), prior_state=retained, created_at=early
    ).to_dict()
    assert payload["prior_state"]["checkpoint"]["created_at_utc"] is not None
    with pytest.raises(TemporalIntegrityError, match="had not yet received"):
        _replay_evidence_result(load_replay_evidence(payload))
    # A creation clock at or after the receipt is fine.
    assert (
        _replay_evidence_result(
            load_replay_evidence(_capture((), prior_state=retained).to_dict())
        ).state.interval_count
        == retained.interval_count
    )


def test_write_watermark_entry_shape_is_validated():
    observations, _normalized = _observations(_rows(count=5))
    written = ConsumedInputWatermark(history_epoch=1, local_sequence=99999)
    good = _capture(
        observations, committed_write_watermarks=(written,)
    ).to_dict()
    assert good["committed_write_watermarks"] == [
        {"history_epoch": 1, "local_sequence": 99999}
    ]
    for mutate, message in (
        (lambda entry: entry.update({"extra": True}), "unexpected keys"),
        (lambda entry: entry.pop("local_sequence"), "missing keys"),
        (lambda entry: entry.update({"local_sequence": "1"}), "must be an integer"),
    ):
        payload = _capture(
            observations, committed_write_watermarks=(written,)
        ).to_dict()
        entry = dict(payload["committed_write_watermarks"][0])
        mutate(entry)
        payload["committed_write_watermarks"] = [entry]
        with pytest.raises(ValueError, match=message):
            load_replay_evidence(payload)


def test_reference_metadata_is_bound_to_the_capture():
    """Reference metadata drives tick_size_pct, so it must be verified."""
    observations, _normalized = _observations(_rows(count=5))
    captured = _capture(observations)
    assert captured.reference_fingerprint == _instrument().reference_fingerprint()
    assert captured.instrument_version_id == _instrument().instrument_version_id
    # Different reference metadata under the same ids is refused.
    drifted = _instrument(tick_size=0.05)
    assert drifted.instrument_version_id == _instrument().instrument_version_id
    assert drifted.reference_fingerprint() != _instrument().reference_fingerprint()
    with pytest.raises(CycleIdentityMismatch, match="reference fingerprint"):
        replay_cycle(
            captured,
            instrument_version=drifted,
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
        )
    # And the drift really would have changed sealed values.
    drifted_snapshot = replay_cycle(
        _capture(observations, instrument_version=drifted),
        instrument_version=drifted,
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=_watermark(observations),
        source_version=SOURCE,
    ).snapshot
    baseline = _replay_result(observations).snapshot
    assert (
        drifted_snapshot.values["tick_size_pct"]
        != baseline.values["tick_size_pct"]
    )
    assert drifted_snapshot.snapshot_id == baseline.snapshot_id

    # ``observed_at_utc`` is excluded from the fingerprint, so matching metadata
    # seen at a different instant still has to be refused: a future version whose
    # fingerprint matches must not certify a historical snapshot.
    earlier = _instrument(observed_at_utc=NOW - timedelta(minutes=1))
    assert earlier.reference_fingerprint() == _instrument().reference_fingerprint()
    assert earlier.instrument_version_id == _instrument().instrument_version_id
    stale_capture = _capture(observations, instrument_version=earlier)
    assert stale_capture.reference_observed_at_utc == NOW - timedelta(minutes=1)
    with pytest.raises(CycleIdentityMismatch, match="reference metadata"):
        replay_cycle(
            stale_capture,
            instrument_version=_instrument(),
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
        )
    # Metadata observed in time by the supplied version still replays.
    in_time = _instrument(observed_at_utc=NOW)
    assert (
        replay_cycle(
            _capture(observations, instrument_version=in_time),
            instrument_version=in_time,
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
        ).snapshot.values["tick_size_pct"]
        is not None
    )


def test_future_reference_metadata_is_refused():
    observations, _normalized = _observations(_rows(count=5))
    future = NOW + timedelta(hours=1)
    version = _instrument(observed_at_utc=future)
    captured = _capture(observations, instrument_version=version)
    assert captured.reference_observed_at_utc == future
    with pytest.raises(TemporalIntegrityError, match="reference metadata"):
        replay_cycle(
            captured,
            instrument_version=version,
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
        )
    # Observed in time, the same version replays.
    ok = _instrument(observed_at_utc=NOW)
    assert (
        replay_cycle(
            _capture(observations, instrument_version=ok),
            instrument_version=ok,
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
        ).snapshot.values["tick_size_pct"]
        is not None
    )


def test_missing_reference_binding_fails_closed():
    observations, _normalized = _observations(_rows(count=5))
    payload = _capture(observations).to_dict()
    payload["reference_fingerprint"] = None
    with pytest.raises(ValueError, match="reference_fingerprint"):
        load_replay_evidence(payload)
    payload = _capture(observations).to_dict()
    del payload["instrument_version_id"]
    with pytest.raises(ValueError, match="missing required keys"):
        load_replay_evidence(payload)
    # A null observation time is not a usable binding: the fingerprint cannot
    # stand in for it, so the payload is refused rather than treated as timeless.
    payload = _capture(observations).to_dict()
    payload["reference_observed_at_utc"] = None
    with pytest.raises(ValueError, match="reference_observed_at_utc"):
        load_replay_evidence(payload)


def test_directly_constructed_evidence_is_validated():
    """A hand-built instance must not bypass the envelope checks."""
    from dataclasses import replace as dataclass_replace

    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    captured = _capture(observations, prior_state=retained)
    # Mutating the retained series while keeping the envelope intact is caught.
    poisoned = dict(captured.prior_state)
    rolling = dict(poisoned["rolling_state"])
    closes = list(rolling["closes"])
    closes[-1] = -1.0
    rolling["closes"] = closes
    poisoned["rolling_state"] = rolling
    tampered = dataclass_replace(captured, prior_state=poisoned)
    with pytest.raises(ValueError, match="non_positive_price"):
        _replay_evidence_result(tampered)
    # An instance that lost a required field is caught too.
    with pytest.raises(ValueError, match="reference_fingerprint"):
        _replay_evidence_result(
            dataclass_replace(captured, reference_fingerprint=None)
        )
    # And the unmodified instance still replays.
    assert _replay_evidence_result(captured).snapshot.snapshot_id


def test_retained_state_at_a_different_cadence_is_refused():
    """The interval check must use the cycle's cadence, not the state's own."""
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    assert retained.interval_seconds == 60
    evidence = _capture(observations, prior_state=retained)
    with pytest.raises(CycleIdentityMismatch, match="interval_seconds"):
        replay_cycle(
            evidence,
            instrument_version=_instrument(),
            evaluation_cutoff=CUTOFF,
            evaluated_at_utc=NOW,
            consumed_input_watermark=_watermark(observations),
            source_version=SOURCE,
            interval_seconds=300,
        )
    # The state's own cadence is still accepted.
    result = replay_cycle(
        evidence,
        instrument_version=_instrument(),
        evaluation_cutoff=CUTOFF,
        evaluated_at_utc=NOW,
        consumed_input_watermark=_watermark(observations),
        source_version=SOURCE,
        interval_seconds=60,
    )
    assert result.state.interval_seconds == 60


def test_watermark_may_not_precede_the_resumed_state():
    observations, _normalized = _observations(_rows(count=FEATURE_WINDOW_INTERVALS))
    retained = advance_state(initial_state(_instrument()), observations).state
    assert retained.consumed_input_watermark.history_epoch == 1
    below = ConsumedInputWatermark(
        history_epoch=1, local_sequence=retained.consumed_input_watermark.local_sequence - 1
    )
    with pytest.raises(WatermarkIntegrityError, match="is not the position"):
        _replay(observations[-2:], prior_state=retained, watermark=below)


# --------------------------------------------------------------------------- #
# restart_state - cold start, warm, and checkpoint warmup
# --------------------------------------------------------------------------- #


def test_cold_start_short_history_reports_new_listing_cold_start():
    observations, _normalized = _observations(_rows(count=5))
    result = _replay_result(observations)
    assert result.snapshot.restart_state is RestartState.NEW_LISTING_COLD_START
    assert result.prior_state.interval_count == 0
    assert result.state.restart_state is RestartState.NEW_LISTING_COLD_START
    assert result.snapshot.values["ema_slow_21"] is None


def test_warm_state_reports_warm():
    observations, _normalized = _observations(_rows(count=FEATURE_WINDOW_INTERVALS))
    retained = advance_state(initial_state(_instrument()), observations).state
    assert retained.warm
    cold = _replay_result(observations)
    resumed = _replay_result(observations, prior_state=retained)
    assert resumed.snapshot.restart_state is RestartState.WARM
    assert cold.snapshot.restart_state is RestartState.WARM
    # Identical rows, identical restart state here, but replay proves the pass-through.
    assert resumed.snapshot.restart_state is resumed.state.restart_state


def _durable_resume_state(observations):
    """A state as the pilot would load it from canonical evidence."""
    base = advance_state(initial_state(_instrument()), observations).state
    payload = to_checkpoint(base, created_at_utc=NOW).to_dict()
    return from_checkpoint(checkpoint_from_payload(payload)), base


def test_checkpoint_resume_reports_restart_warmup():
    # Enough history for the fast EMA, but short of the warm threshold.
    observations, _normalized = _observations(_rows(count=30))
    durable, base = _durable_resume_state(observations)
    assert durable.resumed_from_checkpoint is True
    assert not durable.warm
    result = _replay_result((), prior_state=durable)
    assert result.prior_state.resumed_from_checkpoint is True
    assert result.snapshot.restart_state is RestartState.RESTART_WARMUP
    assert result.state.restart_state is RestartState.RESTART_WARMUP
    # And the snapshot keeps the state production passes, not a WARM default.
    assert result.snapshot.restart_state is not RestartState.WARM
    assert result.snapshot.values["ema_fast_9"] is not None
    assert result.snapshot.values["ema_slow_21"] is not None


def test_in_process_resume_keeps_its_cold_start_provenance():
    """An in-process state is not a checkpoint resume and must not become one."""
    observations, _normalized = _observations(_rows(count=30))
    in_process = advance_state(initial_state(_instrument()), observations).state
    assert in_process.resumed_from_checkpoint is False
    assert in_process.restart_state is RestartState.NEW_LISTING_COLD_START
    captured = _capture(observations, prior_state=in_process)
    assert captured.prior_state_resumed_from_checkpoint is False
    result = _replay_result(observations, prior_state=in_process)
    # The checkpoint contract cannot express this, so the envelope carries it.
    assert result.prior_state.resumed_from_checkpoint is False
    assert result.snapshot.restart_state is RestartState.NEW_LISTING_COLD_START
    assert result.state.restart_state is RestartState.NEW_LISTING_COLD_START
    round_tripped = load_replay_evidence(captured.to_dict())
    replay_again = _replay_evidence_result(round_tripped)
    assert replay_again.snapshot.to_dict() == result.snapshot.to_dict()
    # A durable resume of the same bars is a different, correctly-classified state.
    durable, _base = _durable_resume_state(observations)
    diverted = _replay_result((), prior_state=durable)
    assert diverted.snapshot.restart_state is RestartState.RESTART_WARMUP


def test_restart_state_survives_the_evidence_round_trip():
    observations, _normalized = _observations(_rows(count=30))
    retained = advance_state(initial_state(_instrument()), observations).state
    captured = _capture(observations, prior_state=retained)
    restored = load_replay_evidence(captured.to_dict())
    first = _replay_result(observations, prior_state=retained)
    second = _replay_evidence_result(restored)
    assert first.snapshot.to_dict() == second.snapshot.to_dict()
    assert first.snapshot.restart_state is second.snapshot.restart_state
    assert first.snapshot.content_hash() == second.snapshot.content_hash()
    # A durable resume of the same bars keeps its own provenance across the
    # envelope, because a checkpoint payload is durable by definition.
    durable, _base = _durable_resume_state(observations)
    durable_capture = _capture((), prior_state=durable)
    assert durable_capture.prior_state_resumed_from_checkpoint is True
    durable_first = _replay_result((), prior_state=durable)
    durable_second = _replay_evidence_result(
        load_replay_evidence(durable_capture.to_dict())
    )
    assert durable_first.snapshot.to_dict() == durable_second.snapshot.to_dict()
    assert durable_second.snapshot.restart_state is RestartState.RESTART_WARMUP
    # And a validated checkpoint payload is treated as a resume too.
    as_payload = _capture(
        (), prior_state=to_checkpoint(durable, created_at_utc=NOW).to_dict()
    )
    assert as_payload.prior_state_resumed_from_checkpoint is True
    assert (
        _replay_evidence_result(as_payload).snapshot.restart_state
        is RestartState.RESTART_WARMUP
    )
    checkpoint_object = checkpoint_from_payload(
        to_checkpoint(durable, created_at_utc=NOW).to_dict()
    )
    as_object = _capture((), prior_state=checkpoint_object)
    assert as_object.prior_state_resumed_from_checkpoint is True
    assert (
        _replay_evidence_result(as_object).snapshot.restart_state
        is RestartState.RESTART_WARMUP
    )


def test_corrupt_restart_state_evidence_fails_closed():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    for value in ("NOT_A_STATE", None, 7):
        payload = _capture(observations, prior_state=retained).to_dict()
        payload["prior_state"] = dict(payload["prior_state"])
        payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
        payload["prior_state"]["checkpoint"]["restart_state"] = value
        with pytest.raises(EvidenceIntegrityError, match="retained state"):
            _replay_result(
                observations,
                prior_state=load_replay_evidence(payload).prior_state,
            )
    # A retained state missing its restart-state evidence is refused, not
    # defaulted to WARM.
    payload = _capture(observations, prior_state=retained).to_dict()
    payload["prior_state"] = dict(payload["prior_state"])
    payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
    del payload["prior_state"]["checkpoint"]["restart_state"]
    with pytest.raises(ValueError, match="missing required keys"):
        load_replay_evidence(payload)


def test_unsupported_retained_checkpoint_schema_is_refused():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    for mutate, message in (
        (
            lambda cp: cp.update({"schema_version": 999}),
            "schema_version",
        ),
        (lambda cp: cp.update({"future_field": "drift"}), "unexpected keys"),
        (lambda cp: cp.update({"record_type": "Other"}), "record_type"),
    ):
        payload = _capture(observations, prior_state=retained).to_dict()
        payload["prior_state"] = dict(payload["prior_state"])
        payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
        mutate(payload["prior_state"]["checkpoint"])
        with pytest.raises(ValueError, match=message):
            load_replay_evidence(payload)
    # The supported schema still round-trips.
    good = _capture(observations, prior_state=retained).to_dict()
    assert load_replay_evidence(good).prior_state is not None


def test_retained_state_past_the_cutoff_is_refused():
    """Rolling values may not come from a fact that postdates the cutoff."""
    later = CUTOFF + timedelta(minutes=5)
    observations, _normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS, end_before=later),
        receipt_time=later,
        now=later,
    )
    retained = advance_state(initial_state(_instrument()), observations).state
    horizon = retained.first_interval_epoch + retained.interval_seconds * (
        retained.interval_count
    )
    assert horizon > int(CUTOFF.timestamp())
    assert horizon == int(later.timestamp())
    with pytest.raises(TemporalIntegrityError, match="past evaluation_cutoff"):
        _replay_result(
            (),
            prior_state=retained,
            cutoff=CUTOFF,
            evaluated_at=later,
            created_at=later,
            watermark=retained.consumed_input_watermark,
        )
    # The same state is admissible once the cutoff is past its horizon.
    result = _replay_result(
        (),
        prior_state=retained,
        cutoff=later,
        evaluated_at=later,
        created_at=later,
        watermark=retained.consumed_input_watermark,
    )
    assert result.snapshot.evaluation_cutoff == later
    assert result.snapshot.values["contiguous_intervals"] == retained.interval_count
    assert result.snapshot.coverage is CoverageState.COMPLETE


def test_retained_state_must_be_grid_aligned_and_bounded():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    payload = _capture(observations, prior_state=retained).to_dict()
    payload["prior_state"] = dict(payload["prior_state"])
    payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
    rolling = dict(payload["prior_state"]["checkpoint"]["rolling_state"])
    rolling["first_interval_epoch"] = int(rolling["first_interval_epoch"]) + 30
    payload["prior_state"]["checkpoint"]["rolling_state"] = rolling
    # A misaligned retained horizon is refused by slot validation.
    with pytest.raises(ValueError, match="grid_aligned"):
        load_replay_evidence(payload)
    # And by the population check, for a retention window that is aligned but
    # unreachable for the declared interval.
    skewed = dict(rolling)
    skewed["interval_seconds"] = 45
    payload["prior_state"]["checkpoint"]["rolling_state"] = skewed
    with pytest.raises((ValueError, CycleIdentityMismatch)):
        _replay_evidence_result(load_replay_evidence(payload))


def test_retained_state_needs_an_observation_clock():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    # An in-memory state cannot be captured without the instant it was observed.
    with pytest.raises(ValueError, match="prior_state_created_at_utc"):
        capture_replay_evidence(observations, prior_state=retained)
    # And a checkpoint payload without a creation clock is refused.
    payload = _capture(observations, prior_state=retained).to_dict()
    payload["prior_state"] = dict(payload["prior_state"])
    payload["prior_state"]["checkpoint"] = dict(payload["prior_state"]["checkpoint"])
    payload["prior_state"]["checkpoint"]["created_at_utc"] = None
    with pytest.raises(ValueError, match="created_at_utc"):
        load_replay_evidence(payload)


def test_replayed_snapshot_is_deterministic_for_a_resumed_cycle():
    _all, retained, tip = _resumed_case()
    evidence = _capture(tip, prior_state=retained)
    first, first_report = _replay(tip, prior_state=retained)
    second, second_report = _replay(tip, prior_state=retained)
    assert first.to_dict() == second.to_dict()
    assert first_report.to_dict() == second_report.to_dict()
    assert first_report.snapshot_id == first.snapshot_id
    assert first_report.source_evidence_identity == source_evidence_identity(
        evidence, evaluation_cutoff=CUTOFF
    )
    assert first_report.counts()["IMPLEMENTATION_DEFECT"] == 0
    # The parity report describes the retained-state series the snapshot used,
    # not just the two fetched tip rows.
    result = _replay_result(tip, prior_state=retained)
    assert len(result.rolling_alignment.observations) == FEATURE_WINDOW_INTERVALS
    assert result.rolling_alignment.expected_intervals == FEATURE_WINDOW_INTERVALS
    assert len(result.cycle_alignment.observations) == len(tip)


def test_retained_state_changes_the_evidence_identity():
    observations, _normalized = _observations(_rows(count=5))
    retained = advance_state(initial_state(_instrument()), observations).state
    cold = _capture(observations)
    resumed = _capture(observations, prior_state=retained)
    assert cold.cycle_origin == CYCLE_ORIGIN_COLD_START
    assert resumed.cycle_origin == CYCLE_ORIGIN_RESUMED
    assert source_evidence_identity(
        cold, evaluation_cutoff=CUTOFF
    ) != source_evidence_identity(resumed, evaluation_cutoff=CUTOFF)


def test_committed_misaligned_row_above_the_watermark_is_refused():
    observations, _normalized = _observations(_rows(count=5))
    position = ConsumedInputWatermark(history_epoch=1, local_sequence=5000)
    misaligned = replace(
        _wrong_cadence(observations[2], epoch=CUTOFF - timedelta(minutes=6)),
        commit_order=position,
    )
    below = max(item.commit_order for item in observations)
    with pytest.raises(WatermarkIntegrityError, match="is not the position"):
        _replay(
            observations + (misaligned,),
            watermark=below,
            committed_write_watermarks=(position,),
        )


def test_misaligned_row_equal_to_the_watermark_is_accepted():
    observations, _normalized = _observations(_rows(count=5))
    highest = ConsumedInputWatermark(history_epoch=1, local_sequence=5000)
    misaligned = replace(
        _wrong_cadence(observations[2], epoch=CUTOFF - timedelta(minutes=6)),
        commit_order=highest,
    )
    snapshot, _report = _replay(
        observations + (misaligned,),
        watermark=highest,
        committed_write_watermarks=(highest,),
    )
    assert snapshot.consumed_input_watermark == highest
    assert snapshot.coverage is CoverageState.INCOMPLETE_COVERAGE


def test_misaligned_row_below_a_later_declared_write_is_accepted():
    observations, _normalized = _observations(_rows(count=5))
    misaligned = replace(
        _wrong_cadence(observations[2], epoch=CUTOFF - timedelta(minutes=6)),
        commit_order=ConsumedInputWatermark(history_epoch=1, local_sequence=5000),
    )
    later = ConsumedInputWatermark(history_epoch=1, local_sequence=99999)
    snapshot, _report = _replay(
        observations + (misaligned,),
        watermark=later,
        committed_write_watermarks=(later,),
    )
    assert snapshot.consumed_input_watermark == later


def test_uncommitted_misaligned_row_fails_closed():
    observations, _normalized = _observations(_rows(count=5))
    misaligned = replace(
        _wrong_cadence(observations[2], epoch=CUTOFF - timedelta(minutes=6)),
        commit_order=None,
    )
    with pytest.raises(WatermarkIntegrityError, match="mixes committed"):
        _replay(observations + (misaligned,))


def test_forming_row_above_the_watermark_cannot_change_the_snapshot():
    rows = _rows(count=5) + [
        IntervalRow(
            interval_start_epoch=int(CUTOFF.timestamp()),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.2,
            volume=10.0,
        )
    ]
    observations, _normalized = _observations(rows, now=CUTOFF)
    closed = tuple(item for item in observations if not item.interval_forming)
    forming = tuple(item for item in observations if item.interval_forming)
    assert len(forming) == 1
    above = ConsumedInputWatermark(history_epoch=1, local_sequence=99999)
    claimed_forming = replace(forming[0], commit_order=above)
    baseline, _report = _replay(closed)
    with_forming, _report = _replay(
        closed + (claimed_forming,), watermark=_watermark(closed)
    )
    assert with_forming.consumed_input_watermark == _watermark(closed)
    assert with_forming.values == baseline.values
    assert with_forming.content_hash() == baseline.content_hash()


def test_unclosed_row_above_the_watermark_cannot_change_the_snapshot():
    observations, _normalized = _observations(_rows(count=5))
    unclosed = replace(
        observations[-1],
        source_event_time=CUTOFF,
        ingestion_order=observations[-1].ingestion_order + 10,
        commit_order=ConsumedInputWatermark(history_epoch=1, local_sequence=99999),
    )
    closed = observations
    baseline, _report = _replay(closed)
    with_unclosed, _report = _replay(
        closed + (unclosed,), watermark=_watermark(closed)
    )
    assert with_unclosed.consumed_input_watermark == _watermark(closed)
    assert with_unclosed.content_hash() == baseline.content_hash()


# --------------------------------------------------------------------------- #
# Findings 4-6 - captured identity and replay admissibility
# --------------------------------------------------------------------------- #


def test_same_identity_with_a_divergent_payload_is_rejected():
    observations, _normalized = _observations(_rows(count=7))
    original = observations[-1]
    # Same identity, same values, different coverage: canonical history holds
    # one payload per identity, so this cannot be resolved by arrival order.
    divergent = replace(
        original,
        coverage=CoverageState.INCOMPLETE_COVERAGE,
        ingestion_order=original.ingestion_order + 10,
    )
    assert divergent.observation_id == original.observation_id
    with pytest.raises(EvidenceIntegrityError, match="one observation identity"):
        _replay(observations + (divergent,))


def test_identical_duplicate_row_is_not_a_conflict():
    observations, _normalized = _observations(_rows(count=5))
    once, once_report = _replay(observations)
    twice, twice_report = _replay(observations + (observations[-1],))
    assert twice.values == once.values
    assert twice.content_hash() == once.content_hash()
    assert twice_report.counts() == once_report.counts()


def test_duplicated_non_aggregate_identity_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    first = _ticker(observations[2], epoch=observations[2].source_event_time)
    second = replace(first, receipt_time=NOW + timedelta(minutes=5))
    # Same source_sequence and revision means the same observation identity.
    assert first.observation_id == second.observation_id
    assert first.to_dict() != second.to_dict()
    with pytest.raises(EvidenceIntegrityError, match="one observation identity"):
        _replay(
            observations + (first, second),
            evaluated_at=NOW + timedelta(minutes=6),
        )


def test_window_start_is_replayed_and_bound_into_the_identity():
    rows = _rows(count=5)
    observations, _normalized = _observations(rows)
    window_start = CUTOFF - timedelta(minutes=30)
    with_window = _capture(observations, window_start=window_start)
    assert with_window.window_start == window_start
    assert load_replay_evidence(with_window.to_dict()) == with_window
    left, left_report = _replay(observations)
    right, right_report = _replay(observations, window_start=window_start)
    # A declared window with leading intervals absent is an outage, never COMPLETE.
    assert left.coverage is not CoverageState.INCOMPLETE_COVERAGE
    assert right.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert right.values["coverage_ratio"] != left.values["coverage_ratio"]
    assert right.values["missing_intervals"] != left.values["missing_intervals"]
    assert left_report.source_evidence_identity != right_report.source_evidence_identity
    assert (
        right_report.source_evidence_identity
        == source_evidence_identity(with_window, evaluation_cutoff=CUTOFF)
    )


def test_source_incomplete_is_replayed_and_bound_into_the_identity():
    observations, _normalized = _observations(_rows(count=5))
    plain, plain_report = _replay(observations)
    incomplete, incomplete_report = _replay(observations, source_incomplete=True)
    assert plain.coverage is CoverageState.COMPLETE
    assert incomplete.coverage is CoverageState.INCOMPLETE_COVERAGE
    assert incomplete.content_hash() != plain.content_hash()
    assert (
        incomplete_report.source_evidence_identity
        != plain_report.source_evidence_identity
    )


def test_captured_evidence_envelope_is_strictly_validated():
    observations, _normalized = _observations(_rows(count=5))
    good = _capture(observations).to_dict()
    assert good["record_type"] == REPLAY_EVIDENCE_RECORD_TYPE
    assert good["schema_version"] == REPLAY_EVIDENCE_SCHEMA_VERSION
    assert good["cycle_origin"] == CYCLE_ORIGIN_COLD_START
    assert good["prior_state"] is None
    assert load_replay_evidence(good).cycle_origin == CYCLE_ORIGIN_COLD_START
    for mutate, message in (
        (lambda row: row.pop("window_start"), "missing required keys"),
        (lambda row: row.pop("coverage_only"), "missing required keys"),
        (lambda row: row.update({"notes": "drift"}), "unexpected keys"),
        (lambda row: row.update({"record_type": "Other"}), "record_type"),
        (
            lambda row: row.update({"source_incomplete": "false"}),
            "source_incomplete must be a boolean",
        ),
        (
            lambda row: row.update({"window_start": "2026-09-11T15:00Z"}),
            "not the canonical",
        ),
        (lambda row: row.update({"schema_version": 1}), "schema_version"),
        (lambda row: row.update({"evidence": "nope"}), "evidence must be a sequence"),
        (
            lambda row: row.update({"prior_state": {"checkpoint": {}}}),
            "prior_state missing required keys",
        ),
        (
            lambda row: row.update(
                {"prior_state": {"checkpoint": {}, "extra": True}}
            ),
            "prior_state missing required keys",
        ),
    ):
        payload = _capture(observations).to_dict()
        mutate(payload)
        with pytest.raises(ValueError, match=message):
            load_replay_evidence(payload)


def test_non_canonical_observation_timestamps_are_rejected():
    observations, _normalized = _observations(_rows(count=5))
    for spelling in ("2026-09-11T15:00Z", "2026-09-11 15:00:00Z"):
        payload = [dict(row) for row in capture_observation_evidence(observations)]
        payload[0]["receipt_time"] = spelling
        with pytest.raises(ValueError, match="not the canonical"):
            load_observation_evidence(payload)


def test_forming_and_closed_versions_of_one_identity_are_rejected():
    observations, _normalized = _observations(_rows(count=5))
    closed = observations[-1]
    assert closed.interval_forming is False
    forming = replace(
        closed,
        interval_forming=True,
        coverage=CoverageState.INCOMPLETE_COVERAGE,
    )
    assert forming.observation_id == closed.observation_id
    assert forming.to_dict() != closed.to_dict()
    # The forming twin is excluded from the sealed snapshot, but it still shares
    # an identity with a row that is in it.
    with pytest.raises(EvidenceIntegrityError, match="one observation identity"):
        _replay(observations + (forming,))


def test_non_string_identity_fields_are_rejected():
    observations, _normalized = _observations(_rows(count=5))
    for field, value in (
        ("instrument_version_id", 1),
        ("venue", 7),
        ("venue_instrument_id", 7),
    ):
        payload = [dict(row) for row in capture_observation_evidence(observations)]
        payload[0][field] = value
        with pytest.raises(ValueError, match=f"{field} must be a non-empty string"):
            load_observation_evidence(payload)


def test_unexpected_evidence_keys_are_rejected():
    observations, _normalized = _observations(_rows(count=5))
    payload = [dict(row) for row in capture_observation_evidence(observations)]
    payload[0]["notes"] = "unversioned producer drift"
    with pytest.raises(ValueError, match="unexpected keys"):
        load_observation_evidence(payload)


def test_non_string_provenance_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    for key, value in ((1, "x"), ("source", 7)):
        payload = [dict(row) for row in capture_observation_evidence(observations)]
        payload[0]["provenance"] = dict(payload[0]["provenance"])
        payload[0]["provenance"][key] = value
        with pytest.raises(ValueError, match="provenance keys and values"):
            load_observation_evidence(payload)


def test_non_boolean_interval_forming_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    payload = list(capture_observation_evidence(observations))
    payload[0] = dict(payload[0])
    payload[0]["interval_forming"] = "false"
    with pytest.raises(ValueError, match="interval_forming"):
        load_observation_evidence(payload)


def test_non_integer_numeric_fields_are_rejected():
    observations, _normalized = _observations(_rows(count=5))
    for field, value in (
        ("revision", "2"),
        ("ingestion_order", 2.5),
        ("schema_version", True),
    ):
        payload = [dict(row) for row in capture_observation_evidence(observations)]
        payload[0][field] = value
        with pytest.raises(ValueError, match=field):
            load_observation_evidence(payload)


def test_domain_invalid_aggregate_values_are_rejected():
    observations, _normalized = _observations(_rows(count=5))
    originals = [dict(row) for row in capture_observation_evidence(observations)]
    for field, value, reason in (
        ("close", -1.0, "non_positive_price"),
        ("volume", -5.0, "negative_volume"),
    ):
        payload = [dict(row) for row in originals]
        payload[0]["values"] = dict(payload[0]["values"])
        payload[0]["values"][field] = value
        with pytest.raises(ValueError, match=reason):
            load_observation_evidence(payload)
    payload = [dict(row) for row in originals]
    payload[0]["values"] = dict(payload[0]["values"])
    payload[0]["values"]["close"] = payload[0]["values"]["high"] + 1.0
    with pytest.raises(ValueError, match="high_below_body"):
        load_observation_evidence(payload)


def test_future_misaligned_row_cannot_change_coverage():
    observations, _normalized = _observations(_rows(count=5))
    misaligned = replace(
        observations[0],
        payload_kind=PayloadKind.TICKER,
        aggregate_interval_seconds=None,
        receipt_time=NOW,
    )
    # The misaligned row is discarded but still flips the coverage verdict, so
    # an invisible copy of it must not be accepted.
    snapshot, _report = _replay(observations + (misaligned,))
    assert snapshot.coverage is CoverageState.INCOMPLETE_COVERAGE
    future = replace(misaligned, receipt_time=NOW + timedelta(hours=1))
    with pytest.raises(TemporalIntegrityError, match="visibility"):
        _replay(observations + (future,))


def test_observation_schema_version_must_be_supported():
    observations, _normalized = _observations(_rows(count=5))
    payload = list(capture_observation_evidence(observations))
    assert all(row["schema_version"] == OBSERVATION_SCHEMA_VERSION for row in payload)
    payload[0] = dict(payload[0])
    payload[0]["schema_version"] = OBSERVATION_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="schema_version"):
        load_observation_evidence(payload)


def test_conflicting_content_for_one_interval_revision_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    original = observations[-1]
    conflicting_values = dict(original.values)
    conflicting_values["volume"] = float(conflicting_values["volume"]) + 5.0
    conflicting = replace(
        original,
        values=conflicting_values,
        ingestion_order=original.ingestion_order + 1,
    )
    # Same interval and revision means the same observation_id but different OHLCV.
    assert conflicting.observation_id == original.observation_id
    with pytest.raises(EvidenceIntegrityError, match="conflicting content"):
        _replay(observations + (conflicting,))


def test_input_not_visible_at_the_replay_instant_is_rejected():
    observations, _normalized = _observations(
        _rows(count=FEATURE_WINDOW_INTERVALS + 20)
    )
    future = NOW + timedelta(hours=1)
    # The first bars fall outside the contiguous feature tail, so the snapshot's
    # own availability guard cannot see this receipt; coverage and lateness can.
    late = replace(observations[0], receipt_time=future)
    with pytest.raises(TemporalIntegrityError, match="visibility"):
        _replay(observations[1:] + (late,))
    # The same evidence replays once the receipt is visible at the replay instant.
    snapshot, report = _replay(observations)
    assert snapshot.values["return_1m"] is not None
    assert snapshot.values["late_arrival_count"] is not None
    assert report.counts()["IMPLEMENTATION_DEFECT"] == 0


def test_captured_observation_id_round_trips():
    observations, _normalized = _observations(_rows(count=5))
    payload = capture_observation_evidence(observations)
    assert all(
        row["observation_id"] == item.observation_id
        for row, item in zip(payload, observations)
    )
    loaded = load_observation_evidence(payload)
    assert tuple(item.observation_id for item in loaded) == tuple(
        item.observation_id for item in observations
    )


def test_missing_observation_id_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    payload = [dict(row) for row in capture_observation_evidence(observations)]
    del payload[0]["observation_id"]
    with pytest.raises(ValueError, match="observation_id"):
        load_observation_evidence(payload)


def test_mutated_observation_id_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    payload = [dict(row) for row in capture_observation_evidence(observations)]
    payload[2]["observation_id"] = "OBS:tampered"
    with pytest.raises(ValueError, match="does not match"):
        load_observation_evidence(payload)


def test_identity_bearing_mutation_with_stale_observation_id_is_rejected():
    observations, _normalized = _observations(_rows(count=5))
    payload = [dict(row) for row in capture_observation_evidence(observations)]
    original_id = payload[1]["observation_id"]
    shifted = datetime.fromisoformat(
        payload[1]["source_event_time"][:-1] + "+00:00"
    ) + timedelta(minutes=5)
    payload[1]["source_event_time"] = (
        shifted.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    assert payload[1]["observation_id"] == original_id
    with pytest.raises(ValueError, match="does not match"):
        load_observation_evidence(payload)




