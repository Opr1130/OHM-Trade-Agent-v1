"""Reproducible dataset-manifest construction with leakage guards."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Mapping

from app.opip.ml.contracts import (
    DatasetManifest,
    FeatureSnapshot,
    SupervisedLabelRecord,
    stable_hash,
)
from app.opip.ml.temporal import require_utc


def build_dataset_manifest(
    *,
    snapshots: Iterable[FeatureSnapshot],
    labels: Iterable[SupervisedLabelRecord],
    created_at_utc: datetime,
    cutoff_at_utc: datetime,
    feature_schema_version: str,
    feature_calc_version: str,
    label_calc_version: str,
    cohort_filter: Mapping[str, object],
    embargo_seconds: int,
    fee_model_version: str,
    slippage_model_version: str,
    training_code_hash: str,
    environment_hash: str,
    random_seed: int,
    serialization_version: int = 1,
) -> DatasetManifest:
    created = require_utc(created_at_utc, field_name="created_at_utc")
    cutoff = require_utc(cutoff_at_utc, field_name="cutoff_at_utc")

    labels_by_snapshot: dict[str, list[SupervisedLabelRecord]] = defaultdict(list)
    exact_labels: dict[
        tuple[str, str, str, str], SupervisedLabelRecord
    ] = {}
    for label in labels:
        labels_by_snapshot[label.snapshot_id].append(label)
        key = (
            label.snapshot_id,
            label.label_calc_version,
            label.fee_model_version,
            label.slippage_model_version,
        )
        prior = exact_labels.get(key)
        if prior is not None and prior != label:
            raise ValueError(
                "conflicting duplicate label records for "
                f"snapshot={label.snapshot_id} version={label.label_calc_version} "
                f"fee={label.fee_model_version} slippage={label.slippage_model_version}"
            )
        exact_labels[key] = label

    included: list[str] = []
    included_labels: list[str] = []
    excluded: dict[str, str] = {}

    for snapshot in sorted(snapshots, key=lambda row: row.snapshot_id):
        reason = None
        label = exact_labels.get(
            (
                snapshot.snapshot_id,
                label_calc_version,
                fee_model_version,
                slippage_model_version,
            )
        )
        historical = labels_by_snapshot.get(snapshot.snapshot_id, [])

        if snapshot.feature_schema_version != feature_schema_version:
            reason = "FEATURE_SCHEMA_MISMATCH"
        elif snapshot.feature_calc_version != feature_calc_version:
            reason = "FEATURE_CALC_VERSION_MISMATCH"
        elif snapshot.decision_at_utc > cutoff:
            reason = "DECISION_AFTER_CUTOFF"
        elif label is None and not historical:
            reason = "LABEL_MISSING"
        elif label is None and not any(
            row.label_calc_version == label_calc_version for row in historical
        ):
            reason = "LABEL_CALC_VERSION_MISMATCH"
        elif label is None and not any(
            row.label_calc_version == label_calc_version
            and row.fee_model_version == fee_model_version
            for row in historical
        ):
            reason = "FEE_MODEL_VERSION_MISMATCH"
        elif label is None and not any(
            row.label_calc_version == label_calc_version
            and row.fee_model_version == fee_model_version
            and row.slippage_model_version == slippage_model_version
            for row in historical
        ):
            reason = "SLIPPAGE_MODEL_VERSION_MISMATCH"
        elif label is None:
            reason = "LABEL_VERSION_SELECTION_FAILED"
        elif label.label_available_at_utc > cutoff:
            reason = "LABEL_NOT_AVAILABLE_AT_CUTOFF"
        elif label.execution_path_ambiguous:
            reason = "EXECUTION_PATH_AMBIGUOUS"
        elif label.data_gap:
            reason = "LABEL_DATA_GAP"
        elif label.censored:
            reason = "LABEL_CENSORED"

        if reason is None and label is not None:
            included.append(snapshot.snapshot_id)
            included_labels.append(label.label_id)
        else:
            excluded[snapshot.snapshot_id] = reason or "UNKNOWN_EXCLUSION"

    payload = {
        "created_at_utc": created,
        "cutoff_at_utc": cutoff,
        "feature_schema_version": feature_schema_version,
        "feature_calc_version": feature_calc_version,
        "label_calc_version": label_calc_version,
        "cohort_filter": dict(cohort_filter),
        "embargo_seconds": embargo_seconds,
        "fee_model_version": fee_model_version,
        "slippage_model_version": slippage_model_version,
        "training_code_hash": training_code_hash,
        "environment_hash": environment_hash,
        "serialization_version": serialization_version,
        "random_seed": random_seed,
        "included_snapshot_ids": included,
        "included_label_ids": included_labels,
        "excluded_snapshot_ids": excluded,
    }
    return DatasetManifest(
        dataset_id=stable_hash("MLDATA", payload),
        created_at_utc=created,
        cutoff_at_utc=cutoff,
        feature_schema_version=feature_schema_version,
        feature_calc_version=feature_calc_version,
        label_schema_version=1,
        label_calc_version=label_calc_version,
        cohort_filter=dict(cohort_filter),
        exclusion_policy={
            "censored": "EXCLUDE",
            "execution_path_ambiguous": "EXCLUDE",
            "data_gap": "EXCLUDE",
            "deterministic_audit_fields": "EXCLUDE_FROM_FEATURES",
            "label_version_match": "REQUIRE_EXACT",
            "fee_model_version_match": "REQUIRE_EXACT",
            "slippage_model_version_match": "REQUIRE_EXACT",
        },
        embargo_seconds=embargo_seconds,
        censoring_policy="EXCLUDE_UNRESOLVED_OR_AMBIGUOUS",
        overlap_handling_policy="PURGE_OVERLAPPING_LABEL_INTERVALS",
        fee_model_version=fee_model_version,
        slippage_model_version=slippage_model_version,
        training_code_hash=training_code_hash,
        environment_hash=environment_hash,
        serialization_version=serialization_version,
        random_seed=random_seed,
        included_snapshot_ids=tuple(included),
        included_label_ids=tuple(included_labels),
        excluded_snapshot_ids=excluded,
    )
