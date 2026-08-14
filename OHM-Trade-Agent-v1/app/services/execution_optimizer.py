from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPlan:
    style: str
    max_slippage_pct: float
    urgency: str
    reason: str


def choose_execution_plan(
    *,
    spread_pct: float,
    observed_slippage_pct: float | None,
    liquidity_tier: str,
    catalyst_risk: str,
) -> ExecutionPlan:
    """Choose conservative execution posture from observed market quality."""
    if spread_pct < 0:
        raise ValueError("spread_pct must be non-negative")
    liquidity = liquidity_tier.upper()
    catalyst = catalyst_risk.upper()
    slippage = max(0.0, float(observed_slippage_pct or 0.0))
    if liquidity in {"VERY_THIN", "THIN"} or spread_pct > 0.35 or slippage > 0.25:
        return ExecutionPlan("PASSIVE_LIMIT", 0.20, "LOW", "market quality requires passive execution")
    if catalyst in {"IMMINENT", "NEAR_TERM"}:
        return ExecutionPlan("WAIT_OR_PASSIVE_LIMIT", 0.15, "LOW", "near catalyst; avoid urgency and price chasing")
    if liquidity == "DEEP" and spread_pct <= 0.10 and slippage <= 0.10:
        return ExecutionPlan("LIMIT", 0.15, "NORMAL", "deep liquidity and tight spread")
    return ExecutionPlan("LIMIT", 0.20, "NORMAL", "standard conservative limit execution")
