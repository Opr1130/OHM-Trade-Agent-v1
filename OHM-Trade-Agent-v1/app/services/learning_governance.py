from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LearningPromotionDecision:
    state: str
    eligible_for_review: bool
    auto_apply_allowed: bool
    reason: str


def evaluate_learning_promotion(
    *,
    samples: int,
    expectancy: float | None,
    profit_factor: float | None,
    max_drawdown_pct: float | None,
    minimum_samples: int = 30,
    minimum_profit_factor: float = 1.20,
    maximum_drawdown_pct: float = 8.0,
) -> LearningPromotionDecision:
    """Govern evidence promotion without allowing self-modifying live behavior."""
    if samples < 0 or minimum_samples <= 0:
        raise ValueError("sample counts must be valid")
    if samples < minimum_samples:
        return LearningPromotionDecision("INSUFFICIENT_DATA", False, False, "minimum sample count not reached")
    if expectancy is None or expectancy <= 0:
        return LearningPromotionDecision("REJECT", False, False, "expectancy is not positive")
    if profit_factor is None or profit_factor < minimum_profit_factor:
        return LearningPromotionDecision("REJECT", False, False, "profit factor below promotion threshold")
    if max_drawdown_pct is None or max_drawdown_pct > maximum_drawdown_pct:
        return LearningPromotionDecision("REJECT", False, False, "drawdown exceeds promotion threshold")
    return LearningPromotionDecision(
        "READY_FOR_HUMAN_REVIEW",
        True,
        False,
        "evidence meets review threshold; explicit human approval remains required",
    )
