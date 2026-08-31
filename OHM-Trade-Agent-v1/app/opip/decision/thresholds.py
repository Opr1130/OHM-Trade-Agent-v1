"""Read-only mirror of the production qualification thresholds.

Build 1 must not duplicate decision policy. Every value exposed here is
*imported* from the module that already owns it, so the O'Pip Decision Engine
and the production path can never drift apart. Nothing in this module defines a
new threshold, and nothing here is writable at runtime.
"""

from __future__ import annotations

from typing import Any

from app.scanner.candidates import MAX_CANDIDATES, MIN_TECHNICAL_SCORE
from app.scanner.directional_candidates import MAX_PER_DIRECTION
from app.scanner.short_execution_quality import (
    MAX_SHORT_ROUND_TRIP_DRAG_PCT,
    MAX_SHORT_SPREAD_BPS,
    MIN_VISIBLE_COVERAGE_PCT,
)
from app.services.chief_analyst import (
    SHORT_MARGIN_COST_RESERVE_PCT,
    SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
    SHORT_VALIDATION_LEVERAGE,
)
from app.services.economic_quality_gate import (
    MIN_NET_PROFIT,
    MIN_REWARD_TO_RISK,
    MIN_TARGET_2_MOVE_PCT,
    PRODUCTION_MAX_CAPITAL_FRACTION,
)
from app.services.recommendation_gate import (
    ALLOWED_DIRECTIONS,
    ALLOWED_RISK_LEVELS,
    MIN_CONFIDENCE,
)
from app.services.target_attainability import (
    MAX_LONG_ROUND_TRIP_DRAG_PCT,
    MAX_LONG_SPREAD_BPS,
    MIN_QUALIFYING_SCORE,
)


__all__ = [
    "AI_MIN_CONFIDENCE",
    "ALLOWED_DIRECTIONS",
    "ALLOWED_RISK_LEVELS",
    "MAX_CANDIDATES",
    "MAX_PER_DIRECTION",
    "MIN_TECHNICAL_SCORE",
    "PRODUCTION_MAX_CAPITAL_FRACTION",
    "TARGET_MIN_QUALIFYING_SCORE",
    "gate_policy_constants",
]


# Alias only: the authoritative value stays in app.services.recommendation_gate.
AI_MIN_CONFIDENCE = MIN_CONFIDENCE
TARGET_MIN_QUALIFYING_SCORE = MIN_QUALIFYING_SCORE


def gate_policy_constants() -> dict[str, Any]:
    """Return the live threshold values, for version fingerprinting.

    Values are read at call time rather than snapshotted at import so a test
    that legitimately monkeypatches a threshold sees a different fingerprint.
    """
    return {
        "ai_min_confidence": int(MIN_CONFIDENCE),
        "allowed_directions": sorted(ALLOWED_DIRECTIONS),
        "allowed_risk_levels": sorted(ALLOWED_RISK_LEVELS),
        "max_candidates": int(MAX_CANDIDATES),
        "max_per_direction": int(MAX_PER_DIRECTION),
        "min_technical_score": int(MIN_TECHNICAL_SCORE),
        "production_max_capital_fraction": float(PRODUCTION_MAX_CAPITAL_FRACTION),
        "economic_min_target_2_move_pct": float(MIN_TARGET_2_MOVE_PCT),
        "economic_min_net_profit": float(MIN_NET_PROFIT),
        "economic_min_reward_to_risk": float(MIN_REWARD_TO_RISK),
        "target_min_qualifying_score": int(MIN_QUALIFYING_SCORE),
        "max_long_spread_bps": float(MAX_LONG_SPREAD_BPS),
        "max_long_round_trip_drag_pct": float(MAX_LONG_ROUND_TRIP_DRAG_PCT),
        "max_short_spread_bps": float(MAX_SHORT_SPREAD_BPS),
        "max_short_round_trip_drag_pct": float(MAX_SHORT_ROUND_TRIP_DRAG_PCT),
        "min_visible_coverage_pct": float(MIN_VISIBLE_COVERAGE_PCT),
        "short_validation_leverage": float(SHORT_VALIDATION_LEVERAGE),
        "short_margin_cost_reserve_pct": float(SHORT_MARGIN_COST_RESERVE_PCT),
        "short_max_account_risk_at_stop_pct": float(
            SHORT_MAX_ACCOUNT_RISK_AT_STOP_PCT
        ),
    }
