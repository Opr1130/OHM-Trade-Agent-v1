"""Phase 0A Stage-0 observability (Issue #223, root cause E).

Before this build, a rank-limited coarse candidate persisted only
``{"universe_count": N}``. That is not enough to reconstruct why an igniting
asset lost its deep-analysis slot, which made every retrospective conclusion
about the RAY episode unfalsifiable.

This module builds additive ``ScreeningEvaluation.metadata`` payloads. It is
strictly evidence: nothing here participates in ranking, truncation,
thresholds, alerting, or trading authority. ``ScreeningEvaluation.metadata`` is
already a free-form mapping, so no schema migration is required and every
existing reader keeps working.

Unavailable values are never fabricated. A feature that could not be measured
is emitted as ``None`` and named in ``unavailable_features`` so a reader can
distinguish "zero" from "not measured".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
from typing import Any, Mapping

from app.opip.early.point_in_time import assert_point_in_time_safe, parse_timestamp

STAGE0_EVIDENCE_SCHEMA_VERSION = 1

#: Selector identity for the production coarse ranker.
SELECTOR_PRODUCTION_COARSE = "PRODUCTION_COARSE_V2_1"
#: Authoritative reserved-cohort selector. Dark unless the human flag is on.
SELECTOR_PROMOTED_COHORT = "EARLY_RESERVED_COHORT_V1"

#: Invariants asserted on every row this module emits.
_MEASUREMENT_ONLY = {
    "measurement_only": True,
    "trade_authority_changed": False,
    "production_selection_changed": False,
}


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _iso(value: datetime | str | None) -> str | None:
    parsed = parse_timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def _taxonomy_token(value: Any) -> str | None:
    """Persist canonical taxonomy tokens, never ``str(enum)`` class names.

    ``MarketPhase.IGNITION.value`` is ``IGNITION``. ``str(MarketPhase.IGNITION)``
    can be ``MarketPhase.IGNITION``, which ``coerce_market_phase`` cannot
    round-trip and silently falls back to ``DORMANT``. Plain strings are
    stored as-is (with a defensive strip of an accidental class prefix).
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        raw = getattr(value, "value", None)
        text = str(raw).strip() if raw is not None else ""
        return text or None
    text = str(value).strip()
    if not text:
        return None
    if "." in text:
        prefix, suffix = text.rsplit(".", 1)
        if prefix in {"MarketPhase", "EvidenceGrade", "OperatorDisposition"} and suffix:
            return suffix
    return text


@dataclass(frozen=True)
class Stage0DecisionFeatures:
    """Decision-time features for one coarse candidate.

    Every field is optional. ``None`` means "not measurable at decision time",
    never "zero". Forward-looking fields are structurally absent: the field
    names are validated against
    :func:`app.opip.early.point_in_time.assert_point_in_time_safe`.
    """

    lift_from_24h_low_pct: float | None = None
    distance_from_24h_high_pct: float | None = None
    notional_usd: float | None = None
    last_price: float | None = None
    volume_24h: float | None = None
    relative_volume: float | None = None
    relative_volume_change: float | None = None
    trade_count_acceleration: float | None = None
    momentum_acceleration: float | None = None
    bandwidth_percentile: float | None = None
    atr_percentile: float | None = None
    prior_observation_at: str | None = None
    observed_at: str | None = None
    decision_at: str | None = None
    prior_observation_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise with explicit unavailability, never fabricated values."""
        numeric = {
            "lift_from_24h_low_pct": _finite_optional(self.lift_from_24h_low_pct),
            "distance_from_24h_high_pct": _finite_optional(self.distance_from_24h_high_pct),
            "notional_usd": _finite_optional(self.notional_usd),
            "last_price": _finite_optional(self.last_price),
            "volume_24h": _finite_optional(self.volume_24h),
            "relative_volume": _finite_optional(self.relative_volume),
            "relative_volume_change": _finite_optional(self.relative_volume_change),
            "trade_count_acceleration": _finite_optional(self.trade_count_acceleration),
            "momentum_acceleration": _finite_optional(self.momentum_acceleration),
            "bandwidth_percentile": _finite_optional(self.bandwidth_percentile),
            "atr_percentile": _finite_optional(self.atr_percentile),
        }
        timestamps = {
            "prior_observation_at": _iso(self.prior_observation_at),
            "observed_at": _iso(self.observed_at),
            "decision_at": _iso(self.decision_at),
        }
        counts = {
            "prior_observation_count": (
                int(self.prior_observation_count)
                if self.prior_observation_count is not None
                else None
            ),
        }
        payload: dict[str, Any] = {**numeric, **timestamps, **counts}
        assert_point_in_time_safe(payload)
        payload["unavailable_features"] = sorted(
            name for name, value in payload.items() if value is None
        )
        return payload


@dataclass(frozen=True)
class CoarseRankContext:
    """Where one candidate sat in the coarse ranking when it was truncated."""

    selector: str
    rank_position: int
    selected_count: int
    universe_count: int
    ranked_count: int
    candidate_score: float | None
    cutoff_score: float | None
    cohort: str | None = None

    @property
    def margin_to_cutoff(self) -> float | None:
        """Signed distance to the last selected score.

        Negative means the candidate missed the cut by that much. ``None``
        means one of the two scores was not measurable.
        """
        candidate = _finite_optional(self.candidate_score)
        cutoff = _finite_optional(self.cutoff_score)
        if candidate is None or cutoff is None:
            return None
        return candidate - cutoff

    def as_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "cohort": self.cohort,
            "rank_position": int(self.rank_position),
            "selected_count": int(self.selected_count),
            "universe_count": int(self.universe_count),
            "ranked_count": int(self.ranked_count),
            "candidate_score": _finite_optional(self.candidate_score),
            "cutoff_score": _finite_optional(self.cutoff_score),
            "margin_to_cutoff": self.margin_to_cutoff,
        }


@dataclass(frozen=True)
class ScoreComponents:
    """Which scoring components fired, and the threshold that was required."""

    achieved_score: float | None = None
    required_score: float | None = None
    components: Mapping[str, float] = field(default_factory=dict)
    failed_predicates: tuple[str, ...] = ()
    blocking_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        achieved = _finite_optional(self.achieved_score)
        required = _finite_optional(self.required_score)
        return {
            "achieved_score": achieved,
            "required_score": required,
            "score_margin": (
                achieved - required if achieved is not None and required is not None else None
            ),
            "components": {
                str(name): _finite_optional(value)
                for name, value in dict(self.components).items()
            },
            "failed_predicates": list(self.failed_predicates),
            "blocking_reason": self.blocking_reason,
        }


def _envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stage0_evidence_schema_version": STAGE0_EVIDENCE_SCHEMA_VERSION,
        **_MEASUREMENT_ONLY,
        **dict(payload),
    }


def build_coarse_status_metadata(
    *,
    universe_count: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Schema envelope for coarse EXCLUDED_MARKET / DATA_UNAVAILABLE rows."""
    payload: dict[str, Any] = {"universe_count": int(universe_count)}
    if extra:
        payload.update(dict(extra))
    return _envelope(payload)


def build_selector_comparison_metadata(
    *,
    legacy_selected: bool,
    promoted_selected: bool,
    legacy_rank: Mapping[str, Any] | None = None,
    authoritative_selector: str,
) -> dict[str, Any]:
    """Measurement-only comparison of legacy rank vs promoted selection.

    Never a second final screening outcome. Forensic replay reads
    ``authoritative`` to pick exactly one Stage-0 decision per instrument.
    """
    legacy_outcome = "SELECTED" if legacy_selected else "COARSE_RANK_LIMIT"
    promoted_outcome = "SELECTED" if promoted_selected else "REJECTED"
    return _envelope(
        {
            "selector_comparison": {
                "legacy_selector": {
                    "selector": SELECTOR_PRODUCTION_COARSE,
                    "selected": bool(legacy_selected),
                    "outcome": legacy_outcome,
                    "rank": dict(legacy_rank) if isinstance(legacy_rank, Mapping) else None,
                },
                "promoted_selector": {
                    "selector": SELECTOR_PROMOTED_COHORT,
                    "selected": bool(promoted_selected),
                    "outcome": promoted_outcome,
                },
                "authoritative_selector": str(authoritative_selector),
            },
            "authoritative": True,
            "authoritative_selector": str(authoritative_selector),
            "production_selection_changed": True,
        }
    )


def build_rank_limit_metadata(
    *,
    rank_context: CoarseRankContext,
    features: Stage0DecisionFeatures | None = None,
    base_metadata: Mapping[str, Any] | None = None,
    selector: str = SELECTOR_PRODUCTION_COARSE,
) -> dict[str, Any]:
    """Metadata for a ``COARSE_RANK_LIMIT`` screening row.

    This is the row that could not previously be reconstructed. With rank,
    cutoff and margin persisted, a replay can answer exactly how far a
    candidate was from its deep-analysis slot.
    """
    payload: dict[str, Any] = dict(base_metadata or {})
    payload["universe_count"] = int(rank_context.universe_count)
    payload["selector"] = selector
    payload["coarse_rank"] = rank_context.as_dict()
    payload["decision_features"] = (features or Stage0DecisionFeatures()).as_dict()
    return _envelope(payload)


def build_below_threshold_metadata(
    *,
    score: ScoreComponents,
    universe_count: int,
    features: Stage0DecisionFeatures | None = None,
    base_metadata: Mapping[str, Any] | None = None,
    selector: str = SELECTOR_PRODUCTION_COARSE,
) -> dict[str, Any]:
    """Metadata for ``BELOW_THRESHOLD`` / ``BELOW_COARSE_THRESHOLD`` rows."""
    payload: dict[str, Any] = dict(base_metadata or {})
    payload["universe_count"] = int(universe_count)
    payload["selector"] = selector
    payload["score_evidence"] = score.as_dict()
    payload["decision_features"] = (features or Stage0DecisionFeatures()).as_dict()
    return _envelope(payload)


def build_advanced_metadata(
    *,
    universe_count: int,
    features: Stage0DecisionFeatures | None = None,
    market_phase: Any = None,
    evidence_grade: Any = None,
    operator_disposition: Any = None,
    validation_results: Mapping[str, Any] | None = None,
    extension_comparison: Mapping[str, Any] | None = None,
    score: ScoreComponents | None = None,
    base_metadata: Mapping[str, Any] | None = None,
    selector: str = SELECTOR_PRODUCTION_COARSE,
) -> dict[str, Any]:
    """Metadata for an ``ADVANCED`` row: features, phase and validations."""
    payload: dict[str, Any] = dict(base_metadata or {})
    payload["universe_count"] = int(universe_count)
    payload["selector"] = selector
    payload["decision_features"] = (features or Stage0DecisionFeatures()).as_dict()
    payload["market_phase"] = _taxonomy_token(market_phase)
    payload["evidence_grade"] = _taxonomy_token(evidence_grade)
    payload["operator_disposition"] = _taxonomy_token(operator_disposition)
    if score is not None:
        payload["score_evidence"] = score.as_dict()
    if validation_results is not None:
        payload["validation_results"] = dict(validation_results)
    if extension_comparison is not None:
        payload["extension_comparison"] = dict(extension_comparison)
    return _envelope(payload)


def rank_contexts_for_ranked(
    *,
    ranked_scores: list[tuple[str, float]],
    selected_count: int,
    universe_count: int,
    selector: str = SELECTOR_PRODUCTION_COARSE,
) -> dict[str, CoarseRankContext]:
    """Build a rank context for every ranked identifier, selected or not.

    Promoted rejection of a legacy-selected name still needs rank, cutoff and
    margin so the forensic row is reconstructable. Truncation-only maps omit
    those in-cutoff names.
    """
    ranked_count = len(ranked_scores)
    bounded_selected = max(0, min(int(selected_count), ranked_count))
    cutoff_score: float | None = None
    if bounded_selected > 0:
        cutoff_score = _finite_optional(ranked_scores[bounded_selected - 1][1])

    contexts: dict[str, CoarseRankContext] = {}
    for index, (identifier, candidate_score) in enumerate(ranked_scores):
        contexts[str(identifier)] = CoarseRankContext(
            selector=selector,
            rank_position=index + 1,
            selected_count=bounded_selected,
            universe_count=int(universe_count),
            ranked_count=ranked_count,
            candidate_score=_finite_optional(candidate_score),
            cutoff_score=cutoff_score,
        )
    return contexts


def rank_contexts_for_truncation(
    *,
    ranked_scores: list[tuple[str, float]],
    selected_count: int,
    universe_count: int,
    selector: str = SELECTOR_PRODUCTION_COARSE,
) -> dict[str, CoarseRankContext]:
    """Build a rank context per truncated identifier.

    ``ranked_scores`` must already be in the selector's own descending order
    so ``rank_position`` matches the production ordering exactly. The cutoff
    is the score of the last *selected* candidate, which is the value a
    truncated candidate needed to beat.
    """
    return {
        identifier: context
        for identifier, context in rank_contexts_for_ranked(
            ranked_scores=ranked_scores,
            selected_count=selected_count,
            universe_count=universe_count,
            selector=selector,
        ).items()
        if context.rank_position > context.selected_count
    }
