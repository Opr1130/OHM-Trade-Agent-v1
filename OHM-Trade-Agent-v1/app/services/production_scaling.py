from __future__ import annotations

from dataclasses import dataclass


STAGE_CAPS = {
    "PAPER": 0.0,
    "TINY_LIVE": 0.05,
    "LIMITED_LIVE": 0.15,
    "SCALED_LIVE": 0.30,
}
STAGE_ORDER = tuple(STAGE_CAPS)


@dataclass(frozen=True)
class ProductionStageDecision:
    current_stage: str
    recommended_stage: str
    advance_allowed: bool
    max_capital_fraction: float
    reason: str


def evaluate_production_stage(
    *,
    current_stage: str,
    edge_evidence_ready: bool,
    out_of_sample_profit_factor: float | None,
    max_drawdown_pct: float | None,
    execution_adverse_fill_rate_pct: float | None,
    operations_ready: bool,
    human_approval: bool,
    minimum_profit_factor: float = 1.20,
    maximum_drawdown_pct: float = 8.0,
    maximum_adverse_fill_rate_pct: float = 35.0,
) -> ProductionStageDecision:
    """Allow at most one controlled production stage advancement at a time.

    Human approval is mandatory for every live-capital increase. The returned
    capital fraction is a ceiling, never a sizing recommendation.
    """
    stage = current_stage.upper()
    if stage not in STAGE_CAPS:
        raise ValueError("unknown production stage")

    blockers: list[str] = []
    if not edge_evidence_ready:
        blockers.append("edge evidence is not ready")
    if out_of_sample_profit_factor is None or out_of_sample_profit_factor < minimum_profit_factor:
        blockers.append("out-of-sample profit factor is below threshold")
    if max_drawdown_pct is None or max_drawdown_pct > maximum_drawdown_pct:
        blockers.append("drawdown exceeds threshold")
    if (
        execution_adverse_fill_rate_pct is None
        or execution_adverse_fill_rate_pct > maximum_adverse_fill_rate_pct
    ):
        blockers.append("execution quality is below threshold")
    if not operations_ready:
        blockers.append("operations are not ready")
    if not human_approval:
        blockers.append("explicit human approval is required")

    if blockers:
        return ProductionStageDecision(
            current_stage=stage,
            recommended_stage=stage,
            advance_allowed=False,
            max_capital_fraction=STAGE_CAPS[stage],
            reason="; ".join(blockers),
        )

    index = STAGE_ORDER.index(stage)
    if index == len(STAGE_ORDER) - 1:
        return ProductionStageDecision(
            current_stage=stage,
            recommended_stage=stage,
            advance_allowed=False,
            max_capital_fraction=STAGE_CAPS[stage],
            reason="maximum controlled production stage already reached",
        )
    next_stage = STAGE_ORDER[index + 1]
    return ProductionStageDecision(
        current_stage=stage,
        recommended_stage=next_stage,
        advance_allowed=True,
        max_capital_fraction=STAGE_CAPS[next_stage],
        reason="all evidence, execution, operations, and approval gates passed",
    )
