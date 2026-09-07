"""Phase 1 reserved-cohort early selector (Issue #223, root causes A/B).

Production Stage-0 ranks approximately
``lift_from_24h_low_pct * 4 + max(0, 6 - distance_to_high) * 2 + liquidity``
and takes the top 40. That expression contains no prior observation, delta,
acceleration or trajectory term, so an igniting asset competes for its
analysis slot against assets whose move already completed — and loses.

This selector fixes the *allocation*, not the thresholds. It keeps the same
total candidate budget and reserves slots for cohorts that can only be
identified from change/delta features, so already-extended movers cannot
consume every slot. Nothing here lowers ``MIN_COARSE_LIFT``, widens the
distance-to-high ceiling, or increases the candidate count.

Feature discipline follows the review: CHANGE/DELTA features drive
observation and selection; LEVEL/PERSISTENCE features drive qualification
(see :mod:`app.opip.early.validation_parity`).

The selector is shadow by default. ``OPIP_EARLY_SELECTOR_PROMOTED`` must stay
dark until :mod:`app.opip.early.promotion` gates pass on real evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence

SELECTOR_VERSION = "opip-early-cohort-selector-v1"

COHORT_IGNITION = "IGNITION"
COHORT_COMPRESSION_RELEASE = "COMPRESSION_RELEASE"
COHORT_EARLY_ACCELERATION = "EARLY_ACCELERATION"
COHORT_CURRENT_MOMENTUM = "CURRENT_MOMENTUM"

#: Same total budget as ``movement_discovery_v2.DEFAULT_DEEP_CANDIDATES``.
#: Reallocating slots is the intervention; widening the budget is not.
DEFAULT_TOTAL_CANDIDATES = 40

#: Reserved slots per cohort. Early cohorts are filled first so a field of
#: extended movers cannot starve them, and the sum equals the total budget.
DEFAULT_COHORT_QUOTAS: Mapping[str, int] = {
    COHORT_IGNITION: 8,
    COHORT_COMPRESSION_RELEASE: 6,
    COHORT_EARLY_ACCELERATION: 10,
    COHORT_CURRENT_MOMENTUM: 16,
}

#: Fill order. Reserved early cohorts are allocated before current momentum.
COHORT_FILL_ORDER = (
    COHORT_IGNITION,
    COHORT_COMPRESSION_RELEASE,
    COHORT_EARLY_ACCELERATION,
    COHORT_CURRENT_MOMENTUM,
)

#: Liquidity floor for a ranked candidate, matching the shadow v2.2 selector.
MIN_RANKED_NOTIONAL_USD = 50_000.0
#: Lift beyond which a candidate is treated as already-extended for cohort
#: admission. Mirrors ``movement_discovery_v2`` extension semantics.
EXTENDED_LIFT_CEILING_PCT = 15.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class EarlyCandidateFeatures:
    """Point-in-time features for one candidate.

    Delta fields are ``None`` on a first sighting. A cohort that requires a
    delta simply does not admit the candidate; no value is ever invented.
    """

    identifier: str
    base_asset: str
    lift_from_24h_low_pct: float
    distance_from_24h_high_pct: float
    notional_usd: float
    relative_volume: float | None = None
    relative_volume_change: float | None = None
    #: Uncalibrated scan-to-scan change in rolling 24h volume. Not interval
    #: relative-volume acceleration; kept distinct so ignition can use it as a
    #: proxy without pretending it is true rvol change.
    rolling_24h_volume_change: float | None = None
    trade_count_acceleration: float | None = None
    momentum_acceleration: float | None = None
    atr_percentile: float | None = None
    atr_percentile_change: float | None = None
    bandwidth_percentile: float | None = None
    bandwidth_percentile_change: float | None = None
    compression_release_score: float | None = None
    distance_to_high_velocity_pct: float | None = None
    base_displacement_velocity_pct: float | None = None
    persistence_score: float | None = None
    relative_strength_percentile: float | None = None
    rank_velocity: float | None = None
    production_coarse_score: float | None = None

    @property
    def extended(self) -> bool:
        return _finite(self.lift_from_24h_low_pct) >= EXTENDED_LIFT_CEILING_PCT

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "base_asset": self.base_asset,
            "lift_from_24h_low_pct": _finite_optional(self.lift_from_24h_low_pct),
            "distance_from_24h_high_pct": _finite_optional(self.distance_from_24h_high_pct),
            "notional_usd": _finite_optional(self.notional_usd),
            "relative_volume": _finite_optional(self.relative_volume),
            "relative_volume_change": _finite_optional(self.relative_volume_change),
            "rolling_24h_volume_change": _finite_optional(self.rolling_24h_volume_change),
            "trade_count_acceleration": _finite_optional(self.trade_count_acceleration),
            "momentum_acceleration": _finite_optional(self.momentum_acceleration),
            "atr_percentile_change": _finite_optional(self.atr_percentile_change),
            "bandwidth_percentile_change": _finite_optional(self.bandwidth_percentile_change),
            "compression_release_score": _finite_optional(self.compression_release_score),
            "distance_to_high_velocity_pct": _finite_optional(self.distance_to_high_velocity_pct),
            "base_displacement_velocity_pct": _finite_optional(
                self.base_displacement_velocity_pct
            ),
            "persistence_score": _finite_optional(self.persistence_score),
            "relative_strength_percentile": _finite_optional(self.relative_strength_percentile),
            "rank_velocity": _finite_optional(self.rank_velocity),
            "production_coarse_score": _finite_optional(self.production_coarse_score),
            "extended": self.extended,
        }


@dataclass(frozen=True)
class CohortAssignment:
    """One candidate admitted to one cohort with its within-cohort score."""

    identifier: str
    base_asset: str
    cohort: str
    cohort_score: float
    admission_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "base_asset": self.base_asset,
            "cohort": self.cohort,
            "cohort_score": round(float(self.cohort_score), 6),
            "admission_reasons": list(self.admission_reasons),
        }


@dataclass(frozen=True)
class SelectedCandidate:
    """A candidate selected by the challenger, with its allocation evidence."""

    identifier: str
    base_asset: str
    cohort: str
    cohort_score: float
    cohort_rank: int
    slot_kind: str  # RESERVED | BACKFILL

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "base_asset": self.base_asset,
            "cohort": self.cohort,
            "cohort_score": round(float(self.cohort_score), 6),
            "cohort_rank": int(self.cohort_rank),
            "slot_kind": self.slot_kind,
        }


@dataclass(frozen=True)
class CohortSelection:
    """Full challenger selection with per-cohort accounting."""

    version: str
    selected: tuple[SelectedCandidate, ...]
    quotas: Mapping[str, int]
    filled: Mapping[str, int]
    eligible: Mapping[str, int]
    universe_count: int
    total_candidates: int
    shadow_only: bool = True
    production_selection_changed: bool = False
    assignments: tuple[CohortAssignment, ...] = field(default_factory=tuple)

    @property
    def selected_identifiers(self) -> tuple[str, ...]:
        return tuple(item.identifier for item in self.selected)

    def cohort_members(self, cohort: str) -> tuple[str, ...]:
        return tuple(item.identifier for item in self.selected if item.cohort == cohort)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "universe_count": int(self.universe_count),
            "total_candidates": int(self.total_candidates),
            "quotas": dict(self.quotas),
            "filled": dict(self.filled),
            "eligible": dict(self.eligible),
            "selected": [item.as_dict() for item in self.selected],
            "shadow_only": self.shadow_only,
            "production_selection_changed": self.production_selection_changed,
            "trade_authority_changed": False,
        }


def _liquidity_component(notional: float) -> float:
    return max(0.0, min(18.0, math.log10(max(notional, 1.0)) * 2.5))


def _ignition_assignment(row: EarlyCandidateFeatures) -> CohortAssignment | None:
    """Earliest cohort: acceleration or volume expansion before the move runs.

    Requires at least one delta feature, so it cannot admit a candidate purely
    because its level is already high. ``rolling_24h_volume_change`` is an
    uncalibrated proxy from consecutive full-market scans — not true interval
    relative-volume acceleration. ``trade_count_acceleration`` and genuine
    ``relative_volume_change`` stay ``None`` unless a real source populates them.
    """
    acceleration = _finite_optional(row.momentum_acceleration)
    volume_change = _finite_optional(row.relative_volume_change)
    rolling_volume_change = _finite_optional(row.rolling_24h_volume_change)
    trade_accel = _finite_optional(row.trade_count_acceleration)
    if (
        acceleration is None
        and volume_change is None
        and rolling_volume_change is None
        and trade_accel is None
    ):
        return None
    if row.extended:
        return None

    reasons: list[str] = []
    score = 0.0
    if acceleration is not None and acceleration >= 0.25:
        score += min(30.0, acceleration * 20.0)
        reasons.append(f"momentum acceleration {acceleration:+.2f}")
    if volume_change is not None and volume_change >= 0.20:
        score += min(24.0, volume_change * 24.0)
        reasons.append(f"relative volume change {volume_change:+.2f}x")
    elif rolling_volume_change is not None and rolling_volume_change >= 0.20:
        score += min(18.0, rolling_volume_change * 18.0)
        reasons.append(
            f"rolling 24h volume change {rolling_volume_change:+.2f}x (uncalibrated proxy)"
        )
    if trade_accel is not None and trade_accel >= 1.25:
        score += min(20.0, (trade_accel - 1.0) * 16.0)
        reasons.append(f"trade-count acceleration {trade_accel:.2f}x")
    if not reasons:
        return None
    score += _liquidity_component(_finite(row.notional_usd))
    return CohortAssignment(
        row.identifier, row.base_asset, COHORT_IGNITION, score, tuple(reasons)
    )


def _compression_release_assignment(row: EarlyCandidateFeatures) -> CohortAssignment | None:
    """Volatility transitioning out of compression toward expansion."""
    release = _finite_optional(row.compression_release_score)
    atr_change = _finite_optional(row.atr_percentile_change)
    bandwidth_change = _finite_optional(row.bandwidth_percentile_change)
    if release is None and atr_change is None and bandwidth_change is None:
        return None
    if row.extended:
        return None

    reasons: list[str] = []
    score = 0.0
    if release is not None and release >= 0.35:
        score += release * 40.0
        reasons.append(f"compression release {release:.2f}")
    expansion = max(
        value for value in (atr_change, bandwidth_change, 0.0) if value is not None
    )
    if expansion >= 8.0:
        score += min(20.0, expansion / 2.0)
        reasons.append(f"volatility percentile expanded {expansion:+.1f} points")
    if not reasons:
        return None
    score += _liquidity_component(_finite(row.notional_usd))
    return CohortAssignment(
        row.identifier, row.base_asset, COHORT_COMPRESSION_RELEASE, score, tuple(reasons)
    )


def _early_acceleration_assignment(row: EarlyCandidateFeatures) -> CohortAssignment | None:
    """Move under way but not yet extended, closing on its own range high."""
    distance_velocity = _finite_optional(row.distance_to_high_velocity_pct)
    displacement_velocity = _finite_optional(row.base_displacement_velocity_pct)
    if distance_velocity is None and displacement_velocity is None:
        return None
    lift = _finite(row.lift_from_24h_low_pct)
    if lift >= EXTENDED_LIFT_CEILING_PCT:
        return None

    reasons: list[str] = []
    score = 0.0
    if distance_velocity is not None and distance_velocity >= 0.5:
        score += min(28.0, distance_velocity * 8.0)
        reasons.append(f"closed {distance_velocity:.2f} points of distance to the high")
    if displacement_velocity is not None and displacement_velocity >= 0.5:
        score += min(20.0, displacement_velocity * 4.0)
        reasons.append(f"base displacement velocity {displacement_velocity:+.2f}")
    if not reasons:
        return None
    score += min(12.0, max(0.0, lift) * 1.5)
    score += _liquidity_component(_finite(row.notional_usd))
    return CohortAssignment(
        row.identifier, row.base_asset, COHORT_EARLY_ACCELERATION, score, tuple(reasons)
    )


def _current_momentum_assignment(row: EarlyCandidateFeatures) -> CohortAssignment | None:
    """The legacy cohort: level lift near the range high.

    This intentionally reproduces production's bias so the challenger can be
    compared like-for-like, but it is capped at its own quota.
    """
    lift = _finite(row.lift_from_24h_low_pct)
    distance = max(0.0, _finite(row.distance_from_24h_high_pct))
    if lift < 2.0 or distance > 6.0:
        return None
    score = (
        min(lift, EXTENDED_LIFT_CEILING_PCT) * 3.0
        + max(0.0, 6.0 - distance) * 2.0
        + _liquidity_component(_finite(row.notional_usd))
    )
    return CohortAssignment(
        row.identifier,
        row.base_asset,
        COHORT_CURRENT_MOMENTUM,
        score,
        (f"lift {lift:.2f}% within {distance:.2f}% of the 24h high",),
    )


_COHORT_BUILDERS = {
    COHORT_IGNITION: _ignition_assignment,
    COHORT_COMPRESSION_RELEASE: _compression_release_assignment,
    COHORT_EARLY_ACCELERATION: _early_acceleration_assignment,
    COHORT_CURRENT_MOMENTUM: _current_momentum_assignment,
}


def assign_cohorts(
    rows: Iterable[EarlyCandidateFeatures],
    *,
    min_notional_usd: float = MIN_RANKED_NOTIONAL_USD,
) -> dict[str, list[CohortAssignment]]:
    """Assign every candidate to each cohort it qualifies for."""
    by_cohort: dict[str, list[CohortAssignment]] = {name: [] for name in COHORT_FILL_ORDER}
    for row in rows:
        if _finite(row.notional_usd) < float(min_notional_usd):
            continue
        for cohort, builder in _COHORT_BUILDERS.items():
            assignment = builder(row)
            if assignment is not None:
                by_cohort[cohort].append(assignment)
    for cohort in by_cohort:
        by_cohort[cohort].sort(
            key=lambda item: (-item.cohort_score, item.base_asset, item.identifier)
        )
    return by_cohort


def select_early_candidates(
    rows: Sequence[EarlyCandidateFeatures],
    *,
    total_candidates: int = DEFAULT_TOTAL_CANDIDATES,
    quotas: Mapping[str, int] | None = None,
    min_notional_usd: float = MIN_RANKED_NOTIONAL_USD,
    universe_count: int | None = None,
) -> CohortSelection:
    """Select candidates with reserved per-cohort quotas.

    Allocation order matters: reserved early cohorts are filled before
    ``CURRENT_MOMENTUM``, so a universe dominated by already-extended movers
    still yields ignition candidates. Unused reserved slots are backfilled by
    score, so the total budget is never wasted and never exceeded.
    """
    budget = max(0, int(total_candidates))
    effective_quotas = dict(quotas or DEFAULT_COHORT_QUOTAS)
    by_cohort = assign_cohorts(rows, min_notional_usd=min_notional_usd)

    selected: list[SelectedCandidate] = []
    claimed_assets: set[str] = set()
    filled: dict[str, int] = {name: 0 for name in COHORT_FILL_ORDER}

    for cohort in COHORT_FILL_ORDER:
        quota = max(0, int(effective_quotas.get(cohort, 0)))
        for assignment in by_cohort.get(cohort, ()):
            if filled[cohort] >= quota or len(selected) >= budget:
                break
            if assignment.base_asset in claimed_assets:
                continue
            filled[cohort] += 1
            claimed_assets.add(assignment.base_asset)
            selected.append(
                SelectedCandidate(
                    identifier=assignment.identifier,
                    base_asset=assignment.base_asset,
                    cohort=cohort,
                    cohort_score=assignment.cohort_score,
                    cohort_rank=filled[cohort],
                    slot_kind="RESERVED",
                )
            )

    # Backfill unused reserved capacity by best remaining cohort score.
    if len(selected) < budget:
        remaining: list[CohortAssignment] = [
            assignment
            for cohort in COHORT_FILL_ORDER
            for assignment in by_cohort.get(cohort, ())
            if assignment.base_asset not in claimed_assets
        ]
        remaining.sort(key=lambda item: (-item.cohort_score, item.base_asset, item.identifier))
        for assignment in remaining:
            if len(selected) >= budget:
                break
            if assignment.base_asset in claimed_assets:
                continue
            claimed_assets.add(assignment.base_asset)
            filled[assignment.cohort] = filled.get(assignment.cohort, 0) + 1
            selected.append(
                SelectedCandidate(
                    identifier=assignment.identifier,
                    base_asset=assignment.base_asset,
                    cohort=assignment.cohort,
                    cohort_score=assignment.cohort_score,
                    cohort_rank=filled[assignment.cohort],
                    slot_kind="BACKFILL",
                )
            )

    all_assignments = tuple(
        assignment
        for cohort in COHORT_FILL_ORDER
        for assignment in by_cohort.get(cohort, ())
    )
    return CohortSelection(
        version=SELECTOR_VERSION,
        selected=tuple(selected),
        quotas=effective_quotas,
        filled=filled,
        eligible={cohort: len(by_cohort.get(cohort, ())) for cohort in COHORT_FILL_ORDER},
        universe_count=int(universe_count if universe_count is not None else len(rows)),
        total_candidates=budget,
        assignments=all_assignments,
    )


def compare_with_production(
    *,
    production_identifiers: Sequence[str],
    challenger: CohortSelection,
) -> dict[str, Any]:
    """Side-by-side A/B evidence for a later promotion decision.

    Measurement only: this never mutates the production selection.
    """
    production = list(dict.fromkeys(str(item) for item in production_identifiers))
    challenger_ids = list(challenger.selected_identifiers)
    production_set = set(production)
    challenger_set = set(challenger_ids)
    return {
        "selector_version": challenger.version,
        "production_count": len(production),
        "challenger_count": len(challenger_ids),
        "shared": sorted(production_set & challenger_set),
        "production_only": sorted(production_set - challenger_set),
        "challenger_only": sorted(challenger_set - production_set),
        "challenger_cohort_fill": dict(challenger.filled),
        "reserved_slot_count": sum(
            1 for item in challenger.selected if item.slot_kind == "RESERVED"
        ),
        "shadow_only": True,
        "production_selection_changed": False,
        "trade_authority_changed": False,
    }
