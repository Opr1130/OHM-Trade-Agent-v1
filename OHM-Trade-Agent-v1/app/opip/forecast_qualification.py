"""F6 model-qualification and offline-evaluation contracts (research only).

NOT A TRUST PROMOTION. This module never registers a model, never writes the
production registry and never authorises a forecast. It answers two questions a
future owner decision would need:

1. ``qualify_model_record`` - is a *qualification record* complete, temporally
   sound and reproducible enough that a human could consider the model?
2. ``evaluate_walk_forward`` - what do deterministic scoring rules report for a
   set of scored predictions, and do the splits leak?

Everything is pure: no clock, no filesystem, no network, no randomness. A record
that passes is ``QUALIFICATION_CANDIDATE`` - never ``TRUSTED``. Candidate
thresholds are labelled UNAPPROVED because the owner has set none.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

QUALIFICATION_CANDIDATE = "QUALIFICATION_CANDIDATE"
QUALIFICATION_REJECTED = "REJECTED"
QUALIFICATION_INCOMPLETE = "INCOMPLETE"

#: Candidate policy thresholds. UNAPPROVED: the owner has set none of these.
CANDIDATE_MIN_OUT_OF_SAMPLE_PREDICTIONS = 500
CANDIDATE_MAX_BRIER_SCORE = 0.25
CANDIDATE_MAX_CALIBRATION_ERROR = 0.05
CANDIDATE_MAX_DRIFT_PSI = 0.2
CANDIDATE_THRESHOLDS = {
    "min_out_of_sample_predictions": CANDIDATE_MIN_OUT_OF_SAMPLE_PREDICTIONS,
    "max_brier_score": CANDIDATE_MAX_BRIER_SCORE,
    "max_calibration_error": CANDIDATE_MAX_CALIBRATION_ERROR,
    "max_drift_psi": CANDIDATE_MAX_DRIFT_PSI,
    "status": "UNAPPROVED",
}

REQUIRED_RECORD_FIELDS = (
    "model_kind",
    "model_version",
    "artifact_fingerprint",
    "training_dataset_fingerprint",
    "validation_dataset_fingerprint",
    "test_dataset_fingerprint",
    "feature_definitions_fingerprint",
    "horizon_minutes",
    "venue_instrument_id",
    "training_cutoff",
    "validation_start",
    "test_start",
    "calibration_report_id",
    "reproducibility_manifest",
    "drift_monitor",
    "cost_model",
)

FORBIDDEN_PROVENANCE_KINDS = frozenset(
    {"SYNTHETIC", "FIXTURE", "IMPUTED", "BACKFILLED_WITHOUT_SOURCE"}
)


@dataclass(frozen=True)
class QualificationResult:
    status: str
    reasons: tuple[str, ...]
    thresholds: Mapping[str, Any]

    def as_evidence(self) -> dict[str, Any]:
        return {
            "schema_version": "f6-qualification-v1",
            "status": self.status,
            "reasons": list(self.reasons),
            "thresholds": dict(self.thresholds),
            # A candidate is never trust and never authority.
            "trusted": False,
            "model_registered": False,
            "trade_authority_changed": False,
        }


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def qualify_model_record(record: Any) -> QualificationResult:
    """Assess one qualification record. Never registers or trusts anything."""
    if not isinstance(record, Mapping):
        return QualificationResult(
            QUALIFICATION_INCOMPLETE, ("NOT_A_RECORD",), CANDIDATE_THRESHOLDS
        )

    reasons: list[str] = []
    missing = [
        name for name in REQUIRED_RECORD_FIELDS if record.get(name) in (None, "", {})
    ]
    reasons.extend(f"MISSING_{name.upper()}" for name in missing)

    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping) or not _text(provenance.get("provider")):
        reasons.append("PROVENANCE_MISSING")
    else:
        kind = _text(provenance.get("kind")).upper()
        if kind in FORBIDDEN_PROVENANCE_KINDS:
            reasons.append(f"FORBIDDEN_PROVENANCE_{kind}")
        if provenance.get("point_in_time_verified") is not True:
            reasons.append("POINT_IN_TIME_UNVERIFIED")

    if reasons:
        return QualificationResult(
            QUALIFICATION_INCOMPLETE, tuple(reasons), CANDIDATE_THRESHOLDS
        )

    training_cutoff = _utc(record.get("training_cutoff"))
    validation_start = _utc(record.get("validation_start"))
    test_start = _utc(record.get("test_start"))
    if training_cutoff is None or validation_start is None or test_start is None:
        return QualificationResult(
            QUALIFICATION_REJECTED, ("TIMESTAMP_NOT_UTC",), CANDIDATE_THRESHOLDS
        )

    if not (training_cutoff < validation_start < test_start):
        reasons.append("PERIODS_NOT_ORDERED")

    embargo = record.get("embargo_seconds")
    horizon = record.get("horizon_minutes")
    if isinstance(embargo, bool) or not isinstance(embargo, (int, float)):
        reasons.append("EMBARGO_MISSING")
    elif isinstance(horizon, (int, float)) and horizon > 0:
        # An embargo shorter than the label horizon leaves overlapping labels.
        if float(embargo) < float(horizon) * 60.0:
            reasons.append("EMBARGO_SHORTER_THAN_LABEL_HORIZON")
    if record.get("purging_applied") is not True:
        reasons.append("PURGING_NOT_PROVEN")

    manifest = record.get("reproducibility_manifest")
    if not isinstance(manifest, Mapping) or not _text(manifest.get("code_sha")):
        reasons.append("REPRODUCIBILITY_MANIFEST_INCOMPLETE")
    drift = record.get("drift_monitor")
    if not isinstance(drift, Mapping) or drift.get("defined") is not True:
        reasons.append("DRIFT_MONITOR_UNDEFINED")
    costs = record.get("cost_model")
    if not isinstance(costs, Mapping) or costs.get("spread_included") is not True:
        reasons.append("COST_MODEL_INCOMPLETE")

    if reasons:
        return QualificationResult(
            QUALIFICATION_REJECTED, tuple(reasons), CANDIDATE_THRESHOLDS
        )
    return QualificationResult(QUALIFICATION_CANDIDATE, (), CANDIDATE_THRESHOLDS)



# ---------------------------------------------------------------------------
# Offline evaluation primitives (deterministic, dataset-free)
# ---------------------------------------------------------------------------


def _label(outcome: Any) -> float | None:
    """A binary label, or ``None``. A boolean is a flag, not a label."""
    if isinstance(outcome, bool):
        return None
    if outcome not in (0, 1):
        return None
    return float(outcome)


def _probability(probability: Any) -> float | None:
    """A finite probability in [0, 1], or ``None``. Never raises."""
    if isinstance(probability, bool):
        return None
    try:
        value = float(probability)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value


def brier_score(predictions: Sequence[tuple[float, int]]) -> float | None:
    """Mean squared error of probabilities against binary outcomes.

    ``None`` when there is no prediction or any probability/label is not
    scoreable: an unscoreable set is reported, never guessed.
    """
    if not predictions:
        return None
    total = 0.0
    for probability, outcome in predictions:
        value = _probability(probability)
        label = _label(outcome)
        if value is None or label is None:
            return None
        total += (value - label) ** 2
    return total / len(predictions)


def expected_calibration_error(
    predictions: Sequence[tuple[float, int]], *, bins: int = 10
) -> float | None:
    """Mean |predicted - observed| across equal-width probability bins."""
    if not predictions or bins < 1:
        return None
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for probability, outcome in predictions:
        value = _probability(probability)
        label = _label(outcome)
        if value is None or label is None:
            return None
        buckets[min(bins - 1, int(value * bins))].append((value, label))
    total = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        mean_outcome = sum(item[1] for item in bucket) / len(bucket)
        total += abs(mean_probability - mean_outcome) * (len(bucket) / len(predictions))
    return total


def walk_forward_splits(
    timestamps: Sequence[datetime],
    *,
    folds: int = 3,
    embargo_seconds: int = 0,
) -> list[tuple[list[int], list[int]]]:
    """Temporally ordered train/test index pairs with an embargo gap.

    Index 0 is the earliest observation. A non-UTC or unsorted timestamp raises
    instead of silently producing a leaking fold.
    """
    if folds < 1:
        raise ValueError("folds must be positive")
    if len(timestamps) < folds + 1:
        raise ValueError("not enough observations for the requested folds")
    parsed: list[datetime] = []
    for value in timestamps:
        instant = _utc(value)
        if instant is None:
            raise ValueError("timestamps must be UTC-aware")
        parsed.append(instant)
    if any(later < earlier for earlier, later in zip(parsed, parsed[1:])):
        raise ValueError("timestamps must be sorted")
    size = len(parsed) // (folds + 1)
    if size < 1:
        raise ValueError("not enough observations for the requested folds")
    splits: list[tuple[list[int], list[int]]] = []
    for fold in range(folds):
        train_end = size * (fold + 1)
        test_end = size * (fold + 2) if fold + 1 < folds else len(parsed)
        split_point = train_end
        while (
            split_point < test_end
            and (parsed[split_point] - parsed[train_end - 1]).total_seconds()
            < embargo_seconds
        ):
            split_point += 1
        if split_point >= test_end:
            continue
        splits.append((list(range(0, train_end)), list(range(split_point, test_end))))
    return splits


def evaluate_walk_forward(
    samples: Sequence[Mapping[str, Any]],
    *,
    folds: int = 3,
    embargo_seconds: int = 0,
) -> dict[str, Any]:
    """Score the out-of-sample folds and report whether the split leaked.

    Descriptive only: never promotes a model, never invents a metric when the
    data cannot support one, and always reports DATASET_BLOCKED when no real
    dataset is supplied.
    """
    stamps = [sample.get("observed_at") for sample in samples]
    try:
        splits = walk_forward_splits(stamps, folds=folds, embargo_seconds=embargo_seconds)
    except ValueError:
        # An empty or too-short dataset cannot produce a fold. That is a blocked
        # dataset with zero observations, not a crash and not a metric.
        return {
            "schema_version": "f6-offline-evaluation-v1",
            "folds": 0,
            "out_of_sample_predictions": 0,
            "brier_score": None,
            "expected_calibration_error": None,
            "split_leak_detected": False,
            "thresholds": dict(CANDIDATE_THRESHOLDS),
            "promotable": False,
            "dataset": "NONE_PROVIDED",
            "trust_verdict": "DATASET_BLOCKED",
        }
    scored: list[tuple[float, int]] = []
    leaking = False
    for train, test in splits:
        last_train = _utc(stamps[train[-1]])
        first_test = _utc(stamps[test[0]])
        if last_train is None or first_test is None or first_test <= last_train:
            leaking = True
        for index in test:
            sample = samples[index]
            scored.append((sample.get("probability"), sample.get("outcome")))
    return {
        "schema_version": "f6-offline-evaluation-v1",
        "folds": len(splits),
        "out_of_sample_predictions": len(scored),
        "brier_score": brier_score(scored),
        "expected_calibration_error": expected_calibration_error(scored),
        "split_leak_detected": leaking,
        "thresholds": dict(CANDIDATE_THRESHOLDS),
        "promotable": False,
        "dataset": "NONE_PROVIDED",
        "trust_verdict": "DATASET_BLOCKED",
    }


__all__ = [
    "CANDIDATE_THRESHOLDS",
    "QUALIFICATION_CANDIDATE",
    "QUALIFICATION_INCOMPLETE",
    "QUALIFICATION_REJECTED",
    "QualificationResult",
    "brier_score",
    "evaluate_walk_forward",
    "expected_calibration_error",
    "qualify_model_record",
    "reconcile_qualification_with_artifact",
    "walk_forward_splits",
]



def reconcile_qualification_with_artifact(
    record: Any, artifact: Any
) -> tuple[bool, tuple[str, ...]]:
    """Prove a qualification record describes the artifact that would execute.

    The record duplicates model-kind, version, horizon and artifact identity so a
    human can review it standalone. This function is the single place that proves
    the two agree, so a record can never qualify a *different* model than the one
    the engine would actually load. It reconciles; it never registers anything.
    """
    if not isinstance(record, Mapping):
        return False, ("RECORD_NOT_A_MAPPING",)
    if artifact is None:
        return False, ("ARTIFACT_MISSING",)

    reasons: list[str] = []
    kind = getattr(getattr(artifact, "model_kind", None), "value", None)
    if not kind or _text(record.get("model_kind")) != str(kind):
        reasons.append("MODEL_KIND_MISMATCH")
    if _text(record.get("model_version")) != _text(
        getattr(artifact, "model_version", None)
    ):
        reasons.append("MODEL_VERSION_MISMATCH")
    if _text(record.get("artifact_fingerprint")) != _text(
        getattr(artifact, "artifact_id", None)
    ):
        reasons.append("ARTIFACT_IDENTITY_MISMATCH")

    horizon = getattr(artifact, "horizon_contract", None)
    seconds = getattr(horizon, "path_horizon_seconds", None)
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        reasons.append("ARTIFACT_HORIZON_UNREADABLE")
    else:
        minutes = float(seconds) / 60.0
        declared = record.get("horizon_minutes")
        if not isinstance(declared, (int, float)) or isinstance(declared, bool):
            reasons.append("RECORD_HORIZON_UNREADABLE")
        elif abs(float(declared) - minutes) > 1e-9:
            reasons.append("HORIZON_MISMATCH")

    if reasons:
        return False, tuple(reasons)
    return True, ()

