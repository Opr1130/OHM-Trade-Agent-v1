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
)
from app.opip.ml.dataset import build_dataset_manifest
from app.opip.ml.labels import PriceBar, resolve_barrier_labels
from app.opip.ml.registry import mark_statistical_degradation, transition_model
from app.opip.ml.snapshot import seal_feature_snapshot
from app.opip.ml.temporal import AvailabilityStamp, TemporalIntegrityError
from app.opip.ml.validation import TemporalSample, purged_chronological_split


NOW = datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc)


def _stamp(*, visible_offset_seconds: int = 0) -> AvailabilityStamp:
    return AvailabilityStamp(
        source_at_utc=NOW - timedelta(seconds=2),
        ingested_at_utc=NOW - timedelta(seconds=1),
        visible_at_utc=NOW + timedelta(seconds=visible_offset_seconds),
        source_version="test-v1",
    )


def _snapshot():
    return seal_feature_snapshot(
        episode_id="EP:test",
        candidate_id="OPIPC:test",
        decision_at_utc=NOW,
        canonical_asset_id="bitcoin",
        venue="KRAKEN",
        pair="BTCUSD",
        direction="LONG",
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
            FeatureValue(
                name="volume",
                value=1.0,
                availability=_stamp(),
            ),
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


def test_same_candle_tp_sl_collision_is_ambiguous_not_forced_sl():
    snapshot = _snapshot()
    bar = PriceBar(
        observed_at_utc=NOW + timedelta(minutes=1),
        high=106.0,
        low=94.0,
        close=100.0,
    )
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=[bar],
        horizon_end_utc=NOW + timedelta(hours=1),
        fixed_horizon_closes={"1h": 100.0},
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
    snapshot = _snapshot()
    label = resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="SHORT",
        entry_price=100.0,
        tp1_price=95.0,
        tp2_price=90.0,
        sl_price=105.0,
        bars=[
            PriceBar(
                observed_at_utc=NOW + timedelta(minutes=1),
                high=101.0,
                low=94.0,
                close=95.0,
            )
        ],
        horizon_end_utc=NOW + timedelta(hours=1),
        fixed_horizon_closes={"1h": 95.0},
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
        total_cost_bps=10.0,
    )
    assert label.tp1_before_sl is True
    assert label.net_returns_bps["1h"] == pytest.approx(490.0)


@pytest.mark.parametrize(
    ("direction", "bar", "tp1_price", "tp2_price", "sl_price"),
    [
        (
            "LONG",
            PriceBar(
                observed_at_utc=NOW + timedelta(minutes=1),
                high=107.0,
                low=106.0,
                close=106.5,
            ),
            105.0,
            110.0,
            95.0,
        ),
        (
            "SHORT",
            PriceBar(
                observed_at_utc=NOW + timedelta(minutes=1),
                high=94.0,
                low=93.0,
                close=93.5,
            ),
            95.0,
            90.0,
            105.0,
        ),
    ],
)
def test_price_gaps_beyond_target_count_as_barrier_crossings(
    direction, bar, tp1_price, tp2_price, sl_price
):
    snapshot = _snapshot()
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
        horizon_end_utc=NOW + timedelta(hours=1),
        fixed_horizon_closes={"1h": bar.close},
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )
    assert label.tp1_before_sl is True
    assert label.sl_before_tp1 is False


def _clean_label(
    snapshot,
    *,
    label_calc_version="labels-v1",
    fee_model_version="fees-v1",
    slippage_model_version="slip-v1",
):
    return resolve_barrier_labels(
        snapshot_id=snapshot.snapshot_id,
        candidate_id=snapshot.candidate_id,
        decision_at_utc=NOW,
        direction="LONG",
        entry_price=100.0,
        tp1_price=105.0,
        tp2_price=110.0,
        sl_price=95.0,
        bars=[
            PriceBar(
                observed_at_utc=NOW + timedelta(minutes=1),
                high=106.0,
                low=99.0,
                close=105.0,
            )
        ],
        horizon_end_utc=NOW + timedelta(hours=1),
        fixed_horizon_closes={"1h": 105.0},
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


def test_dataset_requires_exact_fee_and_slippage_versions():
    snapshot = _snapshot()
    wrong_fee = _clean_label(snapshot, fee_model_version="fees-v2")
    manifest = _manifest(snapshot, [wrong_fee])
    assert (
        manifest.excluded_snapshot_ids[snapshot.snapshot_id]
        == "FEE_MODEL_VERSION_MISMATCH"
    )

    wrong_slippage = _clean_label(snapshot, slippage_model_version="slip-v2")
    manifest = _manifest(snapshot, [wrong_slippage])
    assert (
        manifest.excluded_snapshot_ids[snapshot.snapshot_id]
        == "SLIPPAGE_MODEL_VERSION_MISMATCH"
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
    label = _clean_label(snapshot)
    conflicting = replace(label, label_id="MLLBL:conflicting")
    with pytest.raises(ValueError, match="conflicting duplicate"):
        _manifest(snapshot, [label, conflicting])


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
        bars=[
            PriceBar(
                observed_at_utc=NOW + timedelta(minutes=1),
                high=106.0,
                low=94.0,
                close=100.0,
            )
        ],
        horizon_end_utc=NOW + timedelta(hours=1),
        fixed_horizon_closes={"1h": 100.0},
        label_calc_version="labels-v1",
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
    )
    manifest = build_dataset_manifest(
        snapshots=[snapshot],
        labels=[ambiguous],
        created_at_utc=NOW + timedelta(hours=2),
        cutoff_at_utc=NOW + timedelta(hours=2),
        feature_schema_version="ml-features-v1",
        feature_calc_version="test-sha",
        label_calc_version="labels-v1",
        cohort_filter={"lane": "PAPER"},
        embargo_seconds=3600,
        fee_model_version="fees-v1",
        slippage_model_version="slip-v1",
        training_code_hash="code",
        environment_hash="env",
        random_seed=7,
    )
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
        bars=[
            PriceBar(
                observed_at_utc=NOW + timedelta(minutes=1),
                high=101.0,
                low=99.0,
                close=100.0,
            )
        ],
        horizon_end_utc=NOW + timedelta(hours=1),
        fixed_horizon_closes={"1h": None},
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
