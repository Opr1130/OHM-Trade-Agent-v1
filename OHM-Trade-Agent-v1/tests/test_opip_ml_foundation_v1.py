from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path

import pytest

from app.opip.ml.contracts import (
    FeatureSnapshot,
    FeatureValue,
    ModelHealth,
    ModelLifecycle,
    ModelRegistryRecord,
    stable_hash,
)
from app.opip.ml.dataset import build_dataset_manifest
from app.opip.ml.labels import HorizonClose, PriceBar, resolve_barrier_labels
from app.opip.ml.registry import mark_statistical_degradation, transition_model
from app.opip.ml.snapshot import seal_feature_snapshot
from app.opip.ml.temporal import AvailabilityStamp, TemporalIntegrityError
from app.opip.ml.validation import TemporalSample, purged_chronological_split


NOW = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _stamp(*, visible_offset_seconds: int = 0) -> AvailabilityStamp:
    return AvailabilityStamp(
        source_at_utc=NOW - timedelta(seconds=2),
        ingested_at_utc=NOW - timedelta(seconds=1),
        visible_at_utc=NOW + timedelta(seconds=visible_offset_seconds),
        source_version="test-v1",
    )


def _snapshot(*, direction: str = "LONG", candidate_id: str | None = "OPIPC:test"):
    return seal_feature_snapshot(
        episode_id="EP:test",
        candidate_id=candidate_id,
        decision_at_utc=NOW,
        canonical_asset_id="bitcoin",
        venue="KRAKEN",
        pair="BTCUSD",
        direction=direction,
        lane="PAPER",
        regime="TREND",
        feature_values={"volume_24h": 1000.0, "lift_from_low_pct": 5.0},
        availability={
            "volume_24h": _stamp(),
            "lift_from_low_pct": _stamp(),
        },
        feature_schema_version="ml-features-v1",
        feature_calc_version="test-sha",
        feature_dag_hash="dag:test",
        source_versions={"kraken": "test-v1"},
        audit_deterministic_engine_version="decision-v1",
        audit_deterministic_score=91.0,
        audit_deterministic_classification="QUALIFIED",
    )


def _bar(
    *,
    start_minutes: int = 0,
    end_minutes: int = 60,
    visible_delay_seconds: int = 0,
    high: float = 106.0,
    low: float = 99.0,
    close: float = 105.0,
) -> PriceBar:
    end = NOW + timedelta(minutes=end_minutes)
    return PriceBar(
        interval_start_utc=NOW + timedelta(minutes=start_minutes),
        interval_end_utc=end,
        visible_at_utc=end + timedelta(seconds=visible_delay_seconds),
        high=high,
        low=low,
        close=close,
    )


def _close(
    price: float,
    *,
    horizon_minutes: int = 60,
    visible_delay_seconds: int = 0,
) -> HorizonClose:
    horizon = NOW + timedelta(minutes=horizon_minutes)
    return HorizonClose(
        horizon_at_utc=horizon,
        visible_at_utc=horizon + timedelta(seconds=visible_delay_seconds),
        price=price,
    )


def _clean_label(
    snapshot,
    *,
    direction: str | None = None,
    candidate_id: str | None = None,
    label_calc_version: str = "labels-v1",
    fee_model_version: str = "fees-v1",
    slippage_model_version: str = "slip-v1",
    close_price: float = 105.0,
    bar_high: float = 106.0,
    bar_low: float = 99.0,
):
    actual_direction = direction or snapshot.direction
    actual_candidate = (
        snapshot.candidate_id if candidate_id is None else candidate_id
    )
    if actual_direction == "LONG":
        tp1, tp2, stop = 105.0, 110.0, 95.0
    else:
        tp1, tp2, stop = 95.0, 90.0, 105.0
    return resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=actual_candidate,
        decision_at_utc=NOW,
        direction=actual_direction,
        entry_price=100.0,
        tp1_price=tp1,
        tp2_price=tp2,
        sl_price=stop,
        bars=[_bar(high=bar_high, low=bar_low, close=close_price)],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": _close(close_price)},
        computed_at_utc=NOW + HOUR,
        label_calc_version=label_calc_version,
        fee_model_version=fee_model_version,
        slippage_model_version=slippage_model_version,
    )


def _manifest(snapshot, labels, **overrides):
    args = {
        "snapshots": [snapshot],
        "labels": labels,
        "created_at_utc": NOW + timedelta(hours=2),
        "cutoff_at_utc": NOW + timedelta(hours=2),
        "feature_schema_version": "ml-features-v1",
        "feature_calc_version": "test-sha",
        "label_schema_version": 1,
        "label_calc_version": "labels-v1",
        "cohort_filter": {"lane": "PAPER"},
        "embargo_seconds": 3600,
        "fee_model_version": "fees-v1",
        "slippage_model_version": "slip-v1",
        "training_code_hash": "code",
        "environment_hash": "env",
        "random_seed": 7,
    }
    args.update(overrides)
    return build_dataset_manifest(**args)


def test_feature_snapshot_preserves_missing_provider_source_time():
    stamp = AvailabilityStamp(
        source_at_utc=None,
        ingested_at_utc=NOW - timedelta(seconds=1),
        visible_at_utc=NOW,
        source_version="kraken-rest-ticker-v1",
    )
    snapshot = seal_feature_snapshot(
        episode_id="EP:no-source-time",
        candidate_id=None,
        decision_at_utc=NOW,
        canonical_asset_id="bitcoin",
        venue="KRAKEN",
        pair="BTCUSD",
        direction="LONG",
        lane="PAPER",
        regime=None,
        feature_values={"last_price": 100.0},
        availability={"last_price": stamp},
        feature_schema_version="v1",
        feature_calc_version="calc",
        feature_dag_hash="dag",
    )
    row = snapshot.to_dict()["features"][0]
    assert row["source_at_utc"] is None
    assert row["visible_at_utc"] == NOW.isoformat()


def test_feature_snapshot_rejects_future_visible_input():
    with pytest.raises(TemporalIntegrityError):
        seal_feature_snapshot(
            episode_id="EP:test",
            candidate_id=None,
            decision_at_utc=NOW,
            canonical_asset_id="bitcoin",
            venue="KRAKEN",
            pair="BTCUSD",
            direction="LONG",
            lane="PAPER",
            regime=None,
            feature_values={"volume_24h": 1.0},
            availability={"volume_24h": _stamp(visible_offset_seconds=1)},
            feature_schema_version="v1",
            feature_calc_version="calc",
            feature_dag_hash="dag",
        )


def test_deterministic_outputs_are_audit_only_not_ml_features():
    snapshot = _snapshot()
    assert snapshot.audit_deterministic_score == 91.0
    assert "deterministic_score" not in snapshot.ml_feature_mapping()
    with pytest.raises(ValueError, match="excluded"):
        seal_feature_snapshot(
            episode_id="EP:test",
            candidate_id=None,
            decision_at_utc=NOW,
            canonical_asset_id="bitcoin",
            venue="KRAKEN",
            pair="BTCUSD",
            direction="LONG",
            lane="PAPER",
            regime=None,
            feature_values={"deterministic_score": 91.0},
            availability={"deterministic_score": _stamp()},
            feature_schema_version="v1",
            feature_calc_version="calc",
            feature_dag_hash="dag",
        )


def test_snapshot_hash_is_reproducible():
    assert _snapshot().snapshot_id == _snapshot().snapshot_id


def test_snapshot_builder_populates_optional_defaults_before_hashing():
    snapshot = FeatureSnapshot.build(
        episode_id="EP:defaults",
        candidate_id=None,
        decision_at_utc=NOW,
        canonical_asset_id="bitcoin",
        venue="KRAKEN",
        pair="BTCUSD",
        direction="LONG",
        lane="PAPER",
        regime=None,
        feature_schema_version="v1",
        feature_calc_version="calc",
        feature_dag_hash="dag",
        serialization_version=1,
        features=(
            FeatureValue(name="volume", value=1.0, availability=_stamp()),
        ),
    )
    assert dict(snapshot.source_versions) == {}
    assert snapshot.audit_deterministic_score is None
    assert snapshot.snapshot_id.startswith("MLSNAP:")


def test_snapshot_nested_evidence_is_immutable_after_hashing():
    source_versions = {"kraken": "test-v1"}
    snapshot = seal_feature_snapshot(
        episode_id="EP:immutable",
        candidate_id=None,
        decision_at_utc=NOW,
        canonical_asset_id="bitcoin",
        venue="KRAKEN",
        pair="BTCUSD",
        direction="LONG",
        lane="PAPER",
        regime=None,
        feature_values={"nested": {"a": [1, 2]}},
        availability={"nested": _stamp()},
        feature_schema_version="v1",
        feature_calc_version="calc",
        feature_dag_hash="dag",
        source_versions=source_versions,
    )
    source_versions["kraken"] = "mutated"
    assert snapshot.source_versions["kraken"] == "test-v1"
    with pytest.raises(TypeError):
        snapshot.source_versions["kraken"] = "mutated"
    with pytest.raises(TypeError):
        snapshot.features[0].value["a"] = (3,)


def test_same_bar_tp_sl_collision_is_ambiguous_not_forced_sl():
    snapshot = _snapshot()
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=[_bar(high=106.0, low=94.0, close=100.0)],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": _close(100.0)},
        computed_at_utc=NOW + HOUR,
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
        total_cost_bps=10.0,
    )
    assert label.execution_path_ambiguous is True
    assert label.censored is True
    assert label.tp1_before_sl is None
    assert label.sl_before_tp1 is None


def test_direction_aware_short_return_is_positive_when_price_falls():
    snapshot = _snapshot(direction="SHORT")
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="SHORT",
        entry_price=100.0,
        tp1_price=95.0,
        tp2_price=90.0,
        sl_price=105.0,
        bars=[_bar(high=101.0, low=94.0, close=95.0)],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": _close(95.0)},
        computed_at_utc=NOW + HOUR,
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
        total_cost_bps=10.0,
    )
    assert label.tp1_before_sl is True
    assert label.net_returns_bps["1h"] == pytest.approx(490.0)


@pytest.mark.parametrize(
    ("direction", "bar", "tp1_price", "tp2_price", "sl_price", "close_price"),
    [
        ("LONG", _bar(high=107.0, low=106.0, close=106.5), 105.0, 110.0, 95.0, 106.5),
        ("SHORT", _bar(high=94.0, low=93.0, close=93.5), 95.0, 90.0, 105.0, 93.5),
    ],
)
def test_price_gaps_beyond_target_count_as_barrier_crossings(
    direction, bar, tp1_price, tp2_price, sl_price, close_price
):
    snapshot = _snapshot(direction=direction)
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction=direction,
        entry_price=100.0,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        sl_price=sl_price,
        bars=[bar],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": _close(close_price)},
        computed_at_utc=NOW + HOUR,
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )
    assert label.tp1_before_sl is True
    assert label.sl_before_tp1 is False


def test_incomplete_bar_path_is_censored_even_with_horizon_close():
    snapshot = _snapshot()
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=[
            _bar(start_minutes=0, end_minutes=10, high=101.0, low=99.0, close=100.0),
            _bar(start_minutes=20, end_minutes=60, high=101.0, low=99.0, close=100.0),
        ],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": _close(100.0)},
        computed_at_utc=NOW + HOUR,
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )
    assert label.data_gap is True
    assert label.censored is True
    assert label.tp1_before_sl is None


def test_label_availability_tracks_delayed_input_visibility():
    snapshot = _snapshot()
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=[_bar(visible_delay_seconds=300)],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": _close(105.0, visible_delay_seconds=600)},
        computed_at_utc=NOW + HOUR + timedelta(minutes=20),
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )
    assert label.label_available_at_utc == NOW + HOUR + timedelta(minutes=10)
    manifest = _manifest(
        snapshot,
        [label],
        cutoff_at_utc=NOW + HOUR + timedelta(minutes=7),
    )
    assert (
        manifest.excluded_snapshot_ids[snapshot.snapshot_id]
        == "LABEL_NOT_AVAILABLE_AT_CUTOFF"
    )


def test_dataset_requires_exact_fee_and_slippage_versions():
    snapshot = _snapshot()
    wrong_fee = _clean_label(snapshot, fee_model_version="fees-v2")
    manifest = _manifest(snapshot, [wrong_fee])
    assert manifest.excluded_snapshot_ids[snapshot.snapshot_id] == "FEE_MODEL_VERSION_MISMATCH"

    wrong_slippage = _clean_label(snapshot, slippage_model_version="slip-v2")
    manifest = _manifest(snapshot, [wrong_slippage])
    assert manifest.excluded_snapshot_ids[snapshot.snapshot_id] == "SLIPPAGE_MODEL_VERSION_MISMATCH"


def test_dataset_requires_exact_label_schema_version():
    snapshot = _snapshot()
    label = _clean_label(snapshot)
    payload = label.hash_payload()
    payload["schema_version"] = 2
    schema_v2 = replace(
        label,
        schema_version=2,
        label_id=stable_hash("MLLBL", payload),
    )
    manifest = _manifest(snapshot, [schema_v2])
    assert (
        manifest.excluded_snapshot_ids[snapshot.snapshot_id]
        == "LABEL_SCHEMA_VERSION_MISMATCH"
    )


def test_dataset_rejects_direction_and_candidate_linkage_mismatch():
    snapshot = _snapshot()
    wrong_direction = _clean_label(
        snapshot,
        direction="SHORT",
        close_price=95.0,
        bar_high=101.0,
        bar_low=94.0,
    )
    manifest = _manifest(snapshot, [wrong_direction])
    assert (
        manifest.excluded_snapshot_ids[snapshot.snapshot_id]
        == "LABEL_DIRECTION_MISMATCH"
    )

    wrong_candidate = _clean_label(snapshot, candidate_id="OPIPC:other")
    manifest = _manifest(snapshot, [wrong_candidate])
    assert (
        manifest.excluded_snapshot_ids[snapshot.snapshot_id]
        == "LABEL_CANDIDATE_MISMATCH"
    )


def test_dataset_label_version_selection_is_input_order_independent():
    snapshot = _snapshot()
    requested = _clean_label(snapshot, label_calc_version="labels-v1")
    historical = _clean_label(snapshot, label_calc_version="labels-v2")
    forward = _manifest(snapshot, [requested, historical])
    reverse = _manifest(snapshot, [historical, requested])
    assert forward.dataset_id == reverse.dataset_id
    assert forward.included_snapshot_ids == (snapshot.snapshot_id,)
    assert forward.included_label_ids == (requested.label_id,)


def test_conflicting_duplicate_label_version_fails_closed():
    snapshot = _snapshot()
    first = _clean_label(snapshot, close_price=105.0)
    second = _clean_label(snapshot, close_price=106.0, bar_high=107.0)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        _manifest(snapshot, [first, second])


def test_stale_label_id_is_rejected_when_payload_changes():
    snapshot = _snapshot()
    label = _clean_label(snapshot)
    with pytest.raises(ValueError, match="label_id"):
        replace(label, net_returns_bps={"1h": 999.0})


def test_dataset_manifest_excludes_ambiguous_labels_with_specific_reason():
    snapshot = _snapshot()
    ambiguous = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=[_bar(high=106.0, low=94.0, close=100.0)],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": _close(100.0)},
        computed_at_utc=NOW + HOUR,
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )
    manifest = _manifest(snapshot, [ambiguous])
    assert manifest.included_snapshot_ids == ()
    assert (
        manifest.excluded_snapshot_ids[snapshot.snapshot_id]
        == "EXECUTION_PATH_AMBIGUOUS"
    )


def test_missing_fixed_horizon_close_marks_label_censored():
    snapshot = _snapshot()
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=[_bar(high=101.0, low=99.0, close=100.0)],
        horizon_end_utc=NOW + HOUR,
        fixed_horizon_closes={"1h": None},
        computed_at_utc=NOW + HOUR,
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )
    assert label.data_gap is True
    assert label.censored is True


def test_purged_split_removes_training_label_overlap():
    samples = [
        TemporalSample("train-safe", NOW, NOW + timedelta(hours=1)),
        TemporalSample("train-overlap", NOW, NOW + timedelta(days=3)),
        TemporalSample(
            "val-safe", NOW + timedelta(days=3), NOW + timedelta(days=3, hours=1)
        ),
        TemporalSample(
            "test-safe", NOW + timedelta(days=6), NOW + timedelta(days=6, hours=1)
        ),
    ]
    split = purged_chronological_split(
        samples,
        train_end_utc=NOW + timedelta(days=1),
        validation_start_utc=NOW + timedelta(days=2),
        validation_end_utc=NOW + timedelta(days=4),
        test_start_utc=NOW + timedelta(days=5),
        test_end_utc=NOW + timedelta(days=7),
        embargo=timedelta(days=1),
    )
    assert split.train_ids == ("train-safe",)
    assert "train-overlap" in split.purged_ids
    assert split.validation_ids == ("val-safe",)
    assert split.test_ids == ("test-safe",)


def _registry_record(lifecycle=ModelLifecycle.REGISTERED):
    return ModelRegistryRecord(
        model_id="model-1",
        model_family="XGBOOST",
        model_version="1",
        artifact_hash="artifact",
        training_code_hash="code",
        environment_hash="env",
        hyperparameters={},
        random_seed=1,
        feature_schema_version="v1",
        feature_calc_version="calc",
        label_schema_version=1,
        label_calc_version="labels-v1",
        training_dataset_id="train",
        validation_dataset_id="validation",
        calibration_dataset_id=None,
        validation_report_id="report",
        lifecycle=lifecycle,
        health=ModelHealth.HEALTHY,
    )


def test_challenger_cannot_be_constructed_without_approval_metadata():
    with pytest.raises(ValueError, match="CHALLENGER"):
        _registry_record(ModelLifecycle.CHALLENGER)


def test_model_promotion_is_manual_and_sequential():
    record = _registry_record()
    record = transition_model(record, target=ModelLifecycle.VALIDATED)
    record = transition_model(record, target=ModelLifecycle.SHADOW)
    with pytest.raises(ValueError, match="approval"):
        transition_model(record, target=ModelLifecycle.CHALLENGER)
    promoted = transition_model(
        record,
        target=ModelLifecycle.CHALLENGER,
        approval_principal="authorized-human",
        approved_at_utc=NOW,
    )
    assert promoted.lifecycle == ModelLifecycle.CHALLENGER
    assert promoted.approval_principal == "authorized-human"


def test_statistical_degradation_requires_minimum_sample_support():
    record = _registry_record(ModelLifecycle.SHADOW)
    assert mark_statistical_degradation(
        record, sample_count=10, minimum_sample_count=100
    ).health == ModelHealth.HEALTHY
    assert mark_statistical_degradation(
        record, sample_count=100, minimum_sample_count=100
    ).health == ModelHealth.DEGRADED


def test_ml_package_has_no_exchange_or_execution_imports():
    root = Path(__file__).resolve().parents[1] / "app" / "opip" / "ml"
    forbidden_fragments = (
        "kraken_private",
        "order",
        "position",
        "telegram",
        "execution",
    )
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.lower() for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [str(node.module or "").lower()]
            else:
                continue
            for name in names:
                assert not any(fragment in name for fragment in forbidden_fragments), (
                    path,
                    name,
                )
