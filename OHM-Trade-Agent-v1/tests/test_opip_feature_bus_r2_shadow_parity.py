"""R2 feature-bus shadow parity and deterministic replay.

These tests prove the existing bus on frozen observations. They do not enable
capture, schedule the pilot, or compare features by widening tolerance.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.opip.contracts.enums import Missingness
from app.opip.contracts.identity import ConsumedInputWatermark, InstrumentVersion
from app.opip.features.engine import FEATURE_VERSION, FEATURE_WINDOW_INTERVALS
from app.opip.features.publisher import resolve_feature_bus_mode
from app.opip.features.r2_shadow_parity import (
    CLASSIFICATIONS,
    FeatureVersionMismatch,
    capture_observation_evidence,
    load_observation_evidence,
    replay_captured_evidence,
    replay_feature_snapshot,
)
from app.opip.features.replay import compare_resumed_state, reconstruct_state
from app.opip.features.state import advance_state, initial_state, to_checkpoint
from app.opip.market.aggregates import align_minute_observations
from app.opip.market.observations import IntervalRow, normalize_interval_rows

NOW = datetime(2026, 9, 11, 15, 1, 0, 220000, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 11, 15, 1, 0, tzinfo=timezone.utc)
SOURCE = "r2-shadow-parity-test"


def _instrument() -> InstrumentVersion:
    return InstrumentVersion(
        venue="kraken",
        base_asset="SOL",
        quote_currency="USD",
        venue_instrument_id="SOLUSD",
        version=1,
        reference_data_version="opip-evidence-identity-v1",
        observed_at_utc=NOW,
        price_decimals=2,
        tick_size=0.01,
        min_order_size=0.2,
    )


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


def _observations(rows, *, receipt_time: datetime = NOW, now: datetime = NOW):
    instrument = _instrument()
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
    orders = [
        item.commit_order for item in observations if item.commit_order is not None
    ]
    if not orders:
        return ConsumedInputWatermark(history_epoch=1, local_sequence=0)
    return max(orders)


def _replay(
    observations,
    *,
    cutoff: datetime = CUTOFF,
    version: str = FEATURE_VERSION,
    evaluated_at: datetime | None = None,
):
    payload = capture_observation_evidence(observations)
    if evaluated_at is None:
        evaluated_at = NOW if NOW >= cutoff else cutoff
    return replay_captured_evidence(
        payload,
        instrument_version=_instrument(),
        evaluation_cutoff=cutoff,
        evaluated_at_utc=evaluated_at,
        consumed_input_watermark=_watermark(observations),
        source_version=SOURCE,
        declared_feature_version=version,
    )


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
    extra = replace(observations[-1], ingestion_order=observations[-1].ingestion_order + 50)
    once, _report = _replay(observations)
    twice, _report = _replay(observations + (extra,))
    assert twice.snapshot_id == once.snapshot_id
    assert twice.values == once.values


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
