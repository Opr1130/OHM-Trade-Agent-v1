from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from statistics import mean
from typing import Any


MIN_ACTUAL_SAMPLES = 30
MIN_ACTUAL_BUCKET_SAMPLES = 8
MIN_SHADOW_BUCKET_SAMPLES = 12
MAX_PROPOSED_WEIGHT_ADJUSTMENT = 0.15
MIN_COMBINED_MULTIPLIER = 0.75
MAX_COMBINED_MULTIPLIER = 1.25

_ASSESSMENT_FIELDS: tuple[str, ...] = (
    "derivatives_bias",
    "volatility_regime",
    "macro_regime",
    "flow_bias",
    "sentiment_bias",
)
_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "derivatives_bias": frozenset(
        {"CROWDED_LONG", "CROWDED_SHORT", "RISING_OI", "BALANCED"}
    ),
    "volatility_regime": frozenset({"EXTREME", "HIGH", "LOW", "NORMAL"}),
    "macro_regime": frozenset({"SUPPORTIVE", "DEFENSIVE", "NEUTRAL"}),
    "flow_bias": frozenset({"EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW", "BALANCED"}),
    "sentiment_bias": frozenset({"EUPHORIC", "FEARFUL", "BALANCED"}),
}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _assessment(evidence: Any) -> dict[str, Any] | None:
    if not isinstance(evidence, dict):
        return None
    assessment = evidence.get("assessment")
    if not isinstance(assessment, dict):
        return None
    if str(assessment.get("status") or "").upper() != "AVAILABLE":
        return None
    return assessment


def _context_bucket(value: Any) -> str | None:
    score = _num(value)
    if score is None or score < 0 or score > 100:
        return None
    if score <= 40:
        return "HEADWIND"
    if score >= 60:
        return "SUPPORTIVE"
    return "NEUTRAL"


def extract_market_intelligence_features(
    evidence: Any,
    *,
    direction: str,
) -> tuple[str, ...]:
    """Return bounded, direction-aware single signals and pair combinations.

    The overall context-score bucket is retained as a single diagnostic only.
    Pair combinations use the underlying independent assessment dimensions so
    the derived context score is not combined with the inputs that created it.
    """
    assessment = _assessment(evidence)
    if assessment is None:
        return ()

    normalized_direction = str(direction or "UNKNOWN").upper()
    if normalized_direction not in {"LONG", "SHORT"}:
        return ()
    evidence_direction = str(evidence.get("direction") or normalized_direction).upper()
    if evidence_direction != normalized_direction:
        return ()
    prefix = f"direction={normalized_direction}|"
    singles: list[str] = []
    context_bucket = _context_bucket(assessment.get("context_score"))
    if context_bucket:
        singles.append(f"single:{prefix}context={context_bucket}")

    dimension_values: list[tuple[str, str]] = []
    for field in _ASSESSMENT_FIELDS:
        value = str(assessment.get(field) or "").upper()
        if value not in _ALLOWED_VALUES[field]:
            continue
        dimension_values.append((field, value))
        singles.append(f"single:{prefix}{field}={value}")

    pair_features = [
        f"combo:{prefix}{left_field}={left_value}&{right_field}={right_value}"
        for (left_field, left_value), (right_field, right_value) in combinations(
            dimension_values,
            2,
        )
    ]
    return tuple(singles + pair_features)


def _bounded_weight(delta: float) -> float:
    bounded = max(
        -MAX_PROPOSED_WEIGHT_ADJUSTMENT,
        min(MAX_PROPOSED_WEIGHT_ADJUSTMENT, delta),
    )
    return round(1.0 + bounded, 4)


def _actual_trade_attribution(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    eligible: list[tuple[dict[str, Any], float, tuple[str, ...]]] = []
    for row in outcomes:
        pnl = _num(row.get("net_pnl"))
        if not row.get("entered_trade") or not row.get("terminal_status") or pnl is None:
            continue
        features = extract_market_intelligence_features(
            row.get("market_intelligence"),
            direction=str(row.get("direction") or "UNKNOWN"),
        )
        if features:
            eligible.append((row, pnl, features))

    sample_count = len(eligible)
    calibrated = sample_count >= MIN_ACTUAL_SAMPLES
    if not eligible:
        return {
            "status": "INSUFFICIENT_DATA",
            "samples": 0,
            "minimum_required": MIN_ACTUAL_SAMPLES,
            "baseline_avg_net_pnl": None,
            "baseline_net_win_rate": None,
            "signals": {},
            "combinations": {},
            "proposed_weights": {},
        }

    pnl_values = [pnl for _, pnl, _ in eligible]
    baseline = mean(pnl_values)
    scale = max(1.0, mean(abs(value) for value in pnl_values))
    buckets: dict[str, list[float]] = defaultdict(list)
    for _, pnl, features in eligible:
        for feature in features:
            buckets[feature].append(pnl)

    signals: dict[str, Any] = {}
    pairwise: dict[str, Any] = {}
    proposed_weights: dict[str, float] = {}
    for feature, values in sorted(buckets.items()):
        samples = len(values)
        bucket_expectancy = mean(values)
        normalized_delta = (bucket_expectancy - baseline) / scale
        eligible_for_proposal = calibrated and samples >= MIN_ACTUAL_BUCKET_SAMPLES
        proposed_weight = _bounded_weight(normalized_delta) if eligible_for_proposal else None
        evidence = {
            "samples": samples,
            "net_win_rate": round(sum(value > 0 for value in values) / samples, 4),
            "avg_net_pnl": round(bucket_expectancy, 8),
            "net_pnl_sum": round(sum(values), 8),
            "expectancy_delta_normalized": round(normalized_delta, 6),
            "minimum_bucket_samples": MIN_ACTUAL_BUCKET_SAMPLES,
            "eligible_for_proposal": eligible_for_proposal,
            "proposed_weight": proposed_weight,
        }
        target = pairwise if feature.startswith("combo:") else signals
        target[feature] = evidence
        if proposed_weight is not None:
            proposed_weights[feature] = proposed_weight

    return {
        "status": "CALIBRATED" if calibrated else "INSUFFICIENT_DATA",
        "samples": sample_count,
        "minimum_required": MIN_ACTUAL_SAMPLES,
        "baseline_avg_net_pnl": round(baseline, 8),
        "baseline_net_win_rate": round(
            sum(value > 0 for value in pnl_values) / sample_count,
            4,
        ),
        "objective": "realized_net_pnl_expectancy_after_costs",
        "signals": signals,
        "combinations": pairwise,
        "proposed_weights": proposed_weights,
    }


def _shadow_move(row: dict[str, Any]) -> float | None:
    observations = row.get("observations") or {}
    for horizon in ("24h", "4h", "1h"):
        observation = observations.get(horizon)
        if isinstance(observation, dict):
            move = _num(observation.get("directional_move_pct"))
            if move is not None:
                return move
    return None


def _shadow_attribution(shadows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible: list[tuple[float, tuple[str, ...]]] = []
    for row in shadows:
        move = _shadow_move(row)
        if move is None:
            continue
        features = extract_market_intelligence_features(
            row.get("market_intelligence"),
            direction=str(row.get("direction") or "UNKNOWN"),
        )
        if features:
            eligible.append((move, features))

    buckets: dict[str, list[float]] = defaultdict(list)
    for move, features in eligible:
        for feature in features:
            buckets[feature].append(move)

    signals: dict[str, Any] = {}
    pairwise: dict[str, Any] = {}
    for feature, values in sorted(buckets.items()):
        samples = len(values)
        evidence = {
            "samples": samples,
            "avg_directional_move_pct": round(mean(values), 6),
            "positive_rate_pct": round(
                sum(value > 0 for value in values) / samples * 100.0,
                2,
            ),
            "minimum_bucket_samples": MIN_SHADOW_BUCKET_SAMPLES,
            "qualified_for_confirmation": samples >= MIN_SHADOW_BUCKET_SAMPLES,
            "can_propose_live_weight": False,
        }
        target = pairwise if feature.startswith("combo:") else signals
        target[feature] = evidence

    return {
        "status": "OK" if eligible else "NO_DATA",
        "samples": len(eligible),
        "signals": signals,
        "combinations": pairwise,
        "weight_authority": "diagnostic_only",
    }


def build_market_intelligence_attribution(
    *,
    outcomes: list[dict[str, Any]],
    shadows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build reviewable attribution evidence without changing live behavior."""
    actual = _actual_trade_attribution(outcomes)
    shadow = _shadow_attribution(shadows)
    has_proposals = bool(actual["proposed_weights"])
    if actual["status"] != "CALIBRATED":
        promotion_status = "NOT_ELIGIBLE_INSUFFICIENT_DATA"
    elif not has_proposals:
        promotion_status = "NOT_ELIGIBLE_NO_QUALIFIED_BUCKETS"
    else:
        promotion_status = "HUMAN_REVIEW_REQUIRED"
    return {
        "schema_version": 1,
        "status": actual["status"],
        "paid_ai_calls": 0,
        "feature_model": "direction_aware_single_signals_and_pairwise_combinations",
        "actual_trade_attribution": actual,
        "shadow_confirmation": shadow,
        "proposed_weights": dict(actual["proposed_weights"]),
        "promotion": {
            "status": promotion_status,
            "automatically_applied": False,
            "live_ranking_changed": False,
            "live_sizing_changed": False,
        },
        "guardrails": {
            "minimum_actual_samples": MIN_ACTUAL_SAMPLES,
            "minimum_actual_bucket_samples": MIN_ACTUAL_BUCKET_SAMPLES,
            "minimum_shadow_bucket_samples": MIN_SHADOW_BUCKET_SAMPLES,
            "max_proposed_weight_adjustment": MAX_PROPOSED_WEIGHT_ADJUSTMENT,
            "combined_multiplier_min": MIN_COMBINED_MULTIPLIER,
            "combined_multiplier_max": MAX_COMBINED_MULTIPLIER,
            "hard_risk_rules_mutable": False,
            "leverage_mutable": False,
            "strategy_self_rewrite_allowed": False,
            "automatic_strategy_promotion": False,
        },
    }


def preview_proposed_market_intelligence_multiplier(
    attribution: dict[str, Any],
    *,
    evidence: Any,
    direction: str,
) -> float:
    """Preview bounded proposals for human review; never called by live logic."""
    weights = attribution.get("proposed_weights") or {}
    if not isinstance(weights, dict):
        return 1.0
    product = 1.0
    for feature in extract_market_intelligence_features(evidence, direction=direction):
        value = _num(weights.get(feature))
        if value is not None:
            product *= value
    return round(
        max(MIN_COMBINED_MULTIPLIER, min(MAX_COMBINED_MULTIPLIER, product)),
        4,
    )
