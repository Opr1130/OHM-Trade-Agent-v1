"""Deterministic ML data-readiness audit for O'Pip Sequence 5 Wave A3."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Any, Iterable, Mapping

from app.opip.learning.linkage import (
    LearningCohort,
    LinkageStatus,
    OutcomeSourceQuality,
    build_learning_linkage_records,
)


class MLReadinessState(str, Enum):
    NOT_READY = "NOT_READY"
    COLLECT_MORE_DATA = "COLLECT_MORE_DATA"
    READY_FOR_OFFLINE_TRAINING = "READY_FOR_OFFLINE_TRAINING"


@dataclass(frozen=True)
class MLReadinessPolicy:
    """Declared quality/support gates; values are explicit and reviewable."""

    minimum_primary_supervised_rows: int = 30
    minimum_exact_linkage_rate: float = 0.98
    maximum_missing_feature_rate: float = 0.05
    require_long_and_short: bool = False

    def __post_init__(self) -> None:
        """Validate readiness policy bounds."""
        if self.minimum_primary_supervised_rows < 2:
            raise ValueError("minimum_primary_supervised_rows must be >= 2")
        for name in ("minimum_exact_linkage_rate",):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite in [0, 1]")
        missing = float(self.maximum_missing_feature_rate)
        if not math.isfinite(missing) or not 0.0 <= missing <= 1.0:
            raise ValueError("maximum_missing_feature_rate must be finite in [0, 1]")


@dataclass(frozen=True)
class FeatureMissingness:
    """Missingness statistics for one feature."""

    missing: int
    observed: int
    rate: float

    def as_dict(self) -> dict[str, Any]:
        """Return serializable missingness statistics."""
        return asdict(self)


@dataclass(frozen=True)
class MLDataReadinessReport:
    """Immutable dashboard/training gate for the current evidence population."""

    readiness_state: MLReadinessState
    blockers: tuple[str, ...]
    total_canonical_evidence_rows: int
    total_ml_feature_snapshots: int
    feature_bearing_snapshots: int
    exact_outcome_linkage_count: int
    exact_outcome_linkage_rate: float
    final_supervised_truth_count: int
    final_supervised_truth_rate: float
    provisional_only_linkage_count: int
    pit_violations: int
    duplicate_identities: int
    malformed_records: int
    missing_feature_values: int
    total_feature_values: int
    overall_missing_feature_rate: float
    missingness_by_feature: Mapping[str, FeatureMissingness]
    censored_labels: int
    ambiguous_labels: int
    data_gap_labels: int
    direction_coverage: Mapping[str, int]
    lane_coverage: Mapping[str, int]
    regime_coverage: Mapping[str, int]
    asset_coverage: Mapping[str, int]
    horizon_availability: Mapping[str, int]
    mfe_available: int
    mae_available: int
    qualified_cohort_count: int
    counterfactual_cohort_count: int
    observation_only_count: int
    primary_supervised_usable_rows: int
    exclusion_reason_counts: Mapping[str, int]
    policy: MLReadinessPolicy
    measurement_only: bool = True
    affects_live_decisions: bool = False
    automatic_training_allowed: bool = False
    automatic_promotion: bool = False
    trade_authority_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return deterministic report payload for CLI/dashboard persistence."""
        row = asdict(self)
        row["readiness_state"] = self.readiness_state.value
        row["blockers"] = list(self.blockers)
        row["missingness_by_feature"] = {
            key: value.as_dict()
            for key, value in sorted(self.missingness_by_feature.items())
        }
        row["policy"] = asdict(self.policy)
        row["direction_coverage"] = dict(sorted(self.direction_coverage.items()))
        row["lane_coverage"] = dict(sorted(self.lane_coverage.items()))
        row["regime_coverage"] = dict(sorted(self.regime_coverage.items()))
        row["asset_coverage"] = dict(sorted(self.asset_coverage.items()))
        row["horizon_availability"] = dict(sorted(self.horizon_availability.items()))
        row["exclusion_reason_counts"] = dict(
            sorted(self.exclusion_reason_counts.items())
        )
        return row


def _finite_number(value: Any) -> bool:
    """Return whether one evidence value is a finite scalar number."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _parse_utc(value: Any) -> datetime | None:
    """Parse one UTC/offset-aware timestamp, returning None when malformed."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _duplicates(rows: Iterable[Mapping[str, Any]], field: str) -> int:
    """Count repeated non-empty identities beyond the first occurrence."""
    counts = Counter(
        str(row.get(field) or "").strip()
        for row in rows
        if str(row.get(field) or "").strip()
    )
    return sum(count - 1 for count in counts.values() if count > 1)


def _structurally_valid_canonical(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], int]:
    """Retain canonical rows with unique identity and required evidence shape."""
    materialized = list(rows)
    counts = Counter(str(row.get("snapshot_id") or "").strip() for row in materialized)
    valid: list[Mapping[str, Any]] = []
    malformed = 0
    for row in materialized:
        snapshot_id = str(row.get("snapshot_id") or "").strip()
        if (
            not snapshot_id
            or counts[snapshot_id] != 1
            or not str(row.get("episode_id") or "").strip()
        ):
            malformed += 1 if not snapshot_id or not row.get("episode_id") else 0
            continue
        valid.append(row)
    return valid, malformed


def _feature_snapshot_metrics(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[
    list[Mapping[str, Any]],
    int,
    int,
    int,
    dict[str, FeatureMissingness],
    Counter[str],
    Counter[str],
    Counter[str],
    Counter[str],
]:
    """Validate ML wrappers and derive PIT/missingness/coverage measurements."""
    materialized = list(rows)
    canonical_counts = Counter(
        str(row.get("canonical_snapshot_id") or "").strip() for row in materialized
    )
    ml_counts = Counter(str(row.get("ml_snapshot_id") or "").strip() for row in materialized)
    safe: list[Mapping[str, Any]] = []
    pit_violations = 0
    malformed = 0
    missing_by_name: Counter[str] = Counter()
    observed_by_name: Counter[str] = Counter()
    direction: Counter[str] = Counter()
    lanes: Counter[str] = Counter()
    regimes: Counter[str] = Counter()
    assets: Counter[str] = Counter()

    for wrapper in materialized:
        canonical_id = str(wrapper.get("canonical_snapshot_id") or "").strip()
        ml_id = str(wrapper.get("ml_snapshot_id") or "").strip()
        feature_snapshot = wrapper.get("feature_snapshot")
        if (
            not canonical_id
            or not ml_id
            or canonical_counts[canonical_id] != 1
            or ml_counts[ml_id] != 1
            or not isinstance(feature_snapshot, Mapping)
        ):
            malformed += 1 if not canonical_id or not ml_id or not isinstance(feature_snapshot, Mapping) else 0
            continue
        if str(feature_snapshot.get("snapshot_id") or "") != ml_id:
            malformed += 1
            continue

        decision = _parse_utc(feature_snapshot.get("decision_at_utc"))
        max_visible = _parse_utc(feature_snapshot.get("max_visible_at_utc"))
        if decision is None:
            malformed += 1
            continue
        if max_visible is not None and max_visible > decision:
            pit_violations += 1
            continue

        features = feature_snapshot.get("features")
        if not isinstance(features, list) or not features:
            safe.append(wrapper)
            continue

        feature_temporal_violation = False
        for feature in features:
            if not isinstance(feature, Mapping):
                malformed += 1
                continue
            name = str(feature.get("name") or "").strip()
            if not name:
                malformed += 1
                continue
            visible = _parse_utc(feature.get("visible_at_utc"))
            if visible is None or visible > decision:
                feature_temporal_violation = True
            observed_by_name[name] += 1
            missing_flag = feature.get("missing")
            if (
                missing_flag is True
                or feature.get("value") is None
            ):
                missing_by_name[name] += 1
            elif missing_flag not in {False, None}:
                malformed += 1

        if feature_temporal_violation:
            pit_violations += 1
            continue

        direction[str(feature_snapshot.get("direction") or "UNKNOWN").upper()] += 1
        lanes[str(feature_snapshot.get("lane") or "UNKNOWN")] += 1
        regimes[str(feature_snapshot.get("regime") or "UNKNOWN")] += 1
        assets[str(feature_snapshot.get("canonical_asset_id") or "UNKNOWN")] += 1
        safe.append(wrapper)

    missingness: dict[str, FeatureMissingness] = {}
    for name in sorted(observed_by_name):
        observed = observed_by_name[name]
        missing = missing_by_name[name]
        missingness[name] = FeatureMissingness(
            missing=missing,
            observed=observed,
            rate=(missing / observed if observed else 0.0),
        )

    return (
        safe,
        pit_violations,
        malformed,
        sum(missing_by_name.values()),
        missingness,
        direction,
        lanes,
        regimes,
        assets,
    )


def _horizon_counts(linkages: Iterable[Any]) -> Counter[str]:
    """Count non-null normalized outcome horizons."""
    counts: Counter[str] = Counter()
    for linkage in linkages:
        for name, value in linkage.normalized_outcome.horizon_returns.items():
            if value is not None and _finite_number(value):
                counts[str(name)] += 1
    return counts


def build_ml_data_readiness_report(
    *,
    canonical_rows: Iterable[Mapping[str, Any]],
    ml_snapshot_rows: Iterable[Mapping[str, Any]],
    phase3c_outcome_rows: Iterable[Mapping[str, Any]] = (),
    paper_trade_rows: Iterable[Mapping[str, Any]] = (),
    capture_health: Mapping[str, Any] | None = None,
    policy: MLReadinessPolicy | None = None,
) -> MLDataReadinessReport:
    """Build the fail-closed readiness gate from immutable O'Pip evidence."""
    active_policy = policy or MLReadinessPolicy()
    canonical_all = list(canonical_rows)
    ml_all = list(ml_snapshot_rows)
    phase_all = list(phase3c_outcome_rows)
    paper_all = list(paper_trade_rows)

    canonical_safe, canonical_malformed = _structurally_valid_canonical(canonical_all)
    (
        ml_safe,
        derived_pit,
        feature_malformed,
        missing_feature_values,
        missingness,
        direction,
        lanes,
        regimes,
        assets,
    ) = _feature_snapshot_metrics(ml_all)

    duplicate_identities = (
        _duplicates(canonical_all, "snapshot_id")
        + _duplicates(ml_all, "canonical_snapshot_id")
        + _duplicates(ml_all, "ml_snapshot_id")
    )
    health = capture_health or {}
    pit_violations = derived_pit + int(health.get("temporal_violations", 0) or 0)
    phase_malformed = sum(
        1
        for row in phase_all
        if not str(row.get("snapshot_id") or "").strip()
        or not str(row.get("outcome_record_id") or "").strip()
    )
    paper_malformed = sum(
        1
        for row in paper_all
        if not str(row.get("paper_trade_id") or "").strip()
        or not str(row.get("episode_id") or "").strip()
    )
    malformed_records = (
        canonical_malformed
        + feature_malformed
        + phase_malformed
        + paper_malformed
        + int(health.get("malformed", 0) or 0)
    )

    # Conflicting duplicate identities are removed above, so linkage remains
    # deterministic and never silently picks an arbitrary duplicate.
    linkages = build_learning_linkage_records(
        canonical_rows=canonical_safe,
        ml_snapshot_rows=ml_safe,
        phase3c_outcome_rows=phase_all,
        paper_trade_rows=paper_all,
    )

    feature_bearing = sum(
        1
        for row in ml_safe
        if isinstance(row.get("feature_snapshot"), Mapping)
        and isinstance(row["feature_snapshot"].get("features"), list)
        and len(row["feature_snapshot"]["features"]) > 0
    )
    linked = sum(
        row.linkage_status
        in {LinkageStatus.COMPLETE_FINAL, LinkageStatus.COMPLETE_PROVISIONAL}
        for row in linkages
    )
    final_truth = sum(
        row.normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
        for row in linkages
    )
    provisional = sum(
        row.normalized_outcome.source_quality
        is OutcomeSourceQuality.PROVISIONAL_MARKET
        for row in linkages
    )
    qualified = sum(row.cohort is LearningCohort.QUALIFIED_PAPER for row in linkages)
    counterfactual = sum(
        row.cohort is LearningCohort.COUNTERFACTUAL_REJECTED for row in linkages
    )
    observation_only = sum(
        row.cohort is LearningCohort.OBSERVATION_ONLY for row in linkages
    )
    usable = sum(row.primary_supervised_eligible for row in linkages)

    exclusions: Counter[str] = Counter()
    censored = ambiguous = data_gap = mfe_available = mae_available = 0
    for row in linkages:
        exclusions.update(row.exclusion_reasons)
        outcome = row.normalized_outcome
        censored += int(outcome.censored)
        ambiguous += int(outcome.execution_path_ambiguous)
        data_gap += int(outcome.data_gap)
        mfe_available += int(outcome.mfe is not None and _finite_number(outcome.mfe))
        mae_available += int(outcome.mae is not None and _finite_number(outcome.mae))

    total_features = sum(item.observed for item in missingness.values())
    missing_rate = (
        missing_feature_values / total_features if total_features else 0.0
    )
    linkage_denominator = feature_bearing
    linkage_rate = linked / linkage_denominator if linkage_denominator else 0.0
    final_rate = final_truth / linkage_denominator if linkage_denominator else 0.0

    blockers: list[str] = []
    structural = False
    if pit_violations:
        blockers.append("PIT_VIOLATIONS_PRESENT")
        structural = True
    if duplicate_identities:
        blockers.append("DUPLICATE_IDENTITIES_PRESENT")
        structural = True
    if malformed_records:
        blockers.append("MALFORMED_EVIDENCE_PRESENT")
        structural = True
    if feature_bearing == 0:
        blockers.append("NO_FEATURE_BEARING_SNAPSHOTS")
        structural = True
    if missing_rate > active_policy.maximum_missing_feature_rate:
        blockers.append("FEATURE_MISSINGNESS_ABOVE_POLICY")
        structural = True
    trainable_direction_count = direction.get("LONG", 0) + direction.get("SHORT", 0)
    if feature_bearing > 0 and trainable_direction_count == 0:
        blockers.append("NO_TRAINABLE_DIRECTION_FEATURE_SNAPSHOTS")
        structural = True
    if exclusions.get("ML_DIRECTION_NOT_TRAINABLE", 0):
        blockers.append("ML_DIRECTION_LINKAGE_NOT_TRAINABLE")
        structural = True
    if exclusions.get("DIRECTION_LINK_MISMATCH", 0):
        blockers.append("DIRECTION_LINK_MISMATCH_PRESENT")
        structural = True
    if final_truth == 0:
        blockers.append("NO_FINAL_SUPERVISED_TRUTH")
        structural = True
    if provisional > 0 and final_truth == 0:
        blockers.append("PROVISIONAL_OUTCOMES_ONLY")
    if linkage_rate < active_policy.minimum_exact_linkage_rate:
        blockers.append("EXACT_OUTCOME_LINKAGE_BELOW_POLICY")
    if usable < active_policy.minimum_primary_supervised_rows:
        blockers.append("INSUFFICIENT_PRIMARY_SUPERVISED_SUPPORT")
    if active_policy.require_long_and_short:
        if direction.get("LONG", 0) == 0:
            blockers.append("LONG_FEATURE_COVERAGE_MISSING")
        if direction.get("SHORT", 0) == 0:
            blockers.append("SHORT_FEATURE_COVERAGE_MISSING")

    if structural:
        state = MLReadinessState.NOT_READY
    elif blockers:
        state = MLReadinessState.COLLECT_MORE_DATA
    else:
        state = MLReadinessState.READY_FOR_OFFLINE_TRAINING

    return MLDataReadinessReport(
        readiness_state=state,
        blockers=tuple(sorted(set(blockers))),
        total_canonical_evidence_rows=len(canonical_all),
        total_ml_feature_snapshots=len(ml_all),
        feature_bearing_snapshots=feature_bearing,
        exact_outcome_linkage_count=linked,
        exact_outcome_linkage_rate=linkage_rate,
        final_supervised_truth_count=final_truth,
        final_supervised_truth_rate=final_rate,
        provisional_only_linkage_count=provisional,
        pit_violations=pit_violations,
        duplicate_identities=duplicate_identities,
        malformed_records=malformed_records,
        missing_feature_values=missing_feature_values,
        total_feature_values=total_features,
        overall_missing_feature_rate=missing_rate,
        missingness_by_feature=missingness,
        censored_labels=censored,
        ambiguous_labels=ambiguous,
        data_gap_labels=data_gap,
        direction_coverage=dict(direction),
        lane_coverage=dict(lanes),
        regime_coverage=dict(regimes),
        asset_coverage=dict(assets),
        horizon_availability=dict(_horizon_counts(linkages)),
        mfe_available=mfe_available,
        mae_available=mae_available,
        qualified_cohort_count=qualified,
        counterfactual_cohort_count=counterfactual,
        observation_only_count=observation_only,
        primary_supervised_usable_rows=usable,
        exclusion_reason_counts=dict(exclusions),
        policy=active_policy,
    )
