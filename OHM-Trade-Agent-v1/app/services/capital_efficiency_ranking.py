from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from app.services.profit_ranking import RankedOpportunity


# Capacity is intentionally conservative and versioned. The ranker may use at
# most 10 bps of observed 24h liquidity and at most 10% of visible +/-0.50%
# book depth. A candidate can still be considered at reduced size, but it may
# not receive full capital-deployability credit.
MAX_POSITION_TO_24H_LIQUIDITY_FRACTION = 0.001
MAX_POSITION_TO_HALF_PERCENT_DEPTH_FRACTION = 0.10
MIN_EXECUTABLE_NOTIONAL_USD = 100.0


@dataclass(frozen=True)
class CapitalEfficiencyResult:
    symbol: str
    total_score: float
    base_quality_score: float
    capital_velocity_score: float
    risk_efficiency_score: float
    reward_risk_score: float
    capital_deployability_score: float
    net_return_pct: float
    hold_proxy_hours: float
    net_return_velocity_pct_per_hour: float
    risk_efficiency_ratio: float
    capacity_eligible: bool
    capacity_status: str
    required_notional_usd: float
    liquidity_capacity_ceiling_usd: float | None
    capacity_utilization_pct: float | None
    capacity_scalable_fraction: float


@dataclass(frozen=True)
class CapitalEfficientOpportunity:
    rank: int
    original_rank: int
    ranked_opportunity: RankedOpportunity
    capital_efficiency: CapitalEfficiencyResult


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _capacity_metrics(
    *,
    ranked: RankedOpportunity,
    required_notional: float,
) -> tuple[bool, str, float | None, float | None, float]:
    snapshot = ranked.opportunity.snapshot
    liquidity_24h = max(
        0.0,
        _finite(getattr(snapshot, "combined_24h_liquidity_usd", 0.0)),
    )
    execution = getattr(snapshot, "execution_validation", None)
    bid_depth = max(
        0.0,
        _finite(getattr(execution, "bid_depth_050_usd", 0.0)),
    )
    ask_depth = max(
        0.0,
        _finite(getattr(execution, "ask_depth_050_usd", 0.0)),
    )

    limits: list[float] = []
    if liquidity_24h > 0:
        limits.append(
            liquidity_24h * MAX_POSITION_TO_24H_LIQUIDITY_FRACTION
        )
    if bid_depth > 0 and ask_depth > 0:
        limits.append(
            min(bid_depth, ask_depth)
            * MAX_POSITION_TO_HALF_PERCENT_DEPTH_FRACTION
        )

    if not limits:
        return False, "UNKNOWN", None, None, 0.0

    ceiling = min(limits)
    if required_notional <= 0:
        return False, "INVALID_NOTIONAL", ceiling, None, 0.0

    utilization = required_notional / ceiling * 100.0 if ceiling > 0 else None
    scalable_fraction = (
        max(0.0, min(1.0, ceiling / required_notional))
        if required_notional > 0 and ceiling > 0
        else 0.0
    )
    if ceiling < MIN_EXECUTABLE_NOTIONAL_USD:
        return (
            False,
            "BELOW_MINIMUM_EXECUTABLE_CAPACITY",
            ceiling,
            utilization,
            scalable_fraction,
        )
    return (
        True,
        "PASS" if scalable_fraction >= 1.0 else "CAPPED",
        ceiling,
        utilization,
        scalable_fraction,
    )


def _raw_metrics(ranked: RankedOpportunity) -> dict[str, Any]:
    opportunity = ranked.opportunity
    economic = opportunity.economic_quality
    snapshot = opportunity.snapshot
    plan = opportunity.plan

    capital = max(
        0.0,
        _finite(getattr(economic, "recommended_capital", 0.0)),
    )
    leverage = max(
        1.0,
        _finite(getattr(economic, "leverage", 1.0), 1.0),
    )
    required_notional = max(
        0.0,
        _finite(
            getattr(economic, "position_notional", 0.0),
            capital * leverage,
        ),
    )
    net_profit = max(
        0.0,
        _finite(getattr(economic, "target_2_net_profit", 0.0)),
    )
    if capital > 0:
        net_return_pct = net_profit / capital * 100.0
    else:
        net_return_pct = max(
            0.0,
            _finite(getattr(economic, "target_2_move_pct", 0.0))
            - _finite(
                getattr(
                    getattr(snapshot, "execution_validation", None),
                    "estimated_visible_round_trip_market_drag_pct",
                    0.0,
                )
            ),
        )

    target_move_pct = max(
        0.0,
        _finite(getattr(economic, "target_2_move_pct", 0.0)),
    )
    hourly_range = max(
        _finite(getattr(snapshot, "average_hourly_range_24h_pct", 0.0)),
        _finite(getattr(snapshot, "atr_pct", 0.0)),
        0.10,
    )
    hold_proxy = max(1.0, min(24.0, target_move_pct / hourly_range))
    velocity = net_return_pct / hold_proxy if hold_proxy > 0 else 0.0

    stop_pct = max(
        0.01,
        _finite(getattr(economic, "stop_pct", 0.0), 0.01),
    )
    risk_efficiency = net_return_pct / stop_pct
    rr2 = max(0.0, _finite(getattr(plan, "reward_to_risk_2", 0.0)))
    (
        capacity_eligible,
        capacity_status,
        capacity_ceiling,
        capacity_utilization_pct,
        capacity_scalable_fraction,
    ) = _capacity_metrics(
        ranked=ranked,
        required_notional=required_notional,
    )

    return {
        "base": max(
            0.0,
            min(100.0, _finite(ranked.profit_ranking.total_score)),
        ),
        "net_return_pct": net_return_pct,
        "hold_proxy_hours": hold_proxy,
        "velocity": velocity,
        "risk_efficiency": risk_efficiency,
        "rr2": rr2,
        "required_notional": required_notional,
        "capacity_eligible": capacity_eligible,
        "capacity_status": capacity_status,
        "capacity_ceiling": capacity_ceiling,
        "capacity_utilization_pct": capacity_utilization_pct,
        "capacity_scalable_fraction": capacity_scalable_fraction,
    }


def rank_capital_efficiency(
    ranked_opportunities: list[RankedOpportunity],
) -> list[CapitalEfficientOpportunity]:
    """Rank already-qualified opportunities for scarce capital.

    Deterministic comparative score only; not expected return or probability.
    Prior rejection gates remain authoritative. Liquidity capacity is treated
    as a hard observability/executability requirement plus a deployability
    component, so thin assets cannot win purely on theoretical move size.
    """
    if not ranked_opportunities:
        return []

    raw = [
        (ranked, _raw_metrics(ranked))
        for ranked in ranked_opportunities
    ]
    eligible_raw = [
        metrics
        for _, metrics in raw
        if bool(metrics["capacity_eligible"])
    ]
    max_velocity = max(
        (metrics["velocity"] for metrics in eligible_raw),
        default=0.0,
    )
    max_risk_efficiency = max(
        (metrics["risk_efficiency"] for metrics in eligible_raw),
        default=0.0,
    )

    scored: list[tuple[RankedOpportunity, CapitalEfficiencyResult]] = []
    for ranked, metrics in raw:
        capacity_eligible = bool(metrics["capacity_eligible"])
        base_component = metrics["base"] * 0.50
        velocity_component = (
            15.0 * metrics["velocity"] / max_velocity
            if capacity_eligible and max_velocity > 0
            else 0.0
        )
        risk_component = (
            10.0 * metrics["risk_efficiency"] / max_risk_efficiency
            if capacity_eligible and max_risk_efficiency > 0
            else 0.0
        )
        rr_component = (
            10.0 * min(1.0, metrics["rr2"] / 3.0)
            if capacity_eligible
            else 0.0
        )
        deployability_component = (
            15.0 * float(metrics["capacity_scalable_fraction"])
            if capacity_eligible
            else 0.0
        )
        total = max(
            0.0,
            min(
                100.0,
                base_component
                + velocity_component
                + risk_component
                + rr_component
                + deployability_component,
            ),
        )
        if not capacity_eligible:
            total = 0.0

        ceiling = metrics["capacity_ceiling"]
        utilization = metrics["capacity_utilization_pct"]
        result = CapitalEfficiencyResult(
            symbol=ranked.opportunity.snapshot.symbol,
            total_score=round(total, 2),
            base_quality_score=round(base_component, 2),
            capital_velocity_score=round(velocity_component, 2),
            risk_efficiency_score=round(risk_component, 2),
            reward_risk_score=round(rr_component, 2),
            capital_deployability_score=round(
                deployability_component,
                2,
            ),
            net_return_pct=round(metrics["net_return_pct"], 4),
            hold_proxy_hours=round(metrics["hold_proxy_hours"], 2),
            net_return_velocity_pct_per_hour=round(
                metrics["velocity"],
                4,
            ),
            risk_efficiency_ratio=round(
                metrics["risk_efficiency"],
                4,
            ),
            capacity_eligible=capacity_eligible,
            capacity_status=str(metrics["capacity_status"]),
            required_notional_usd=round(
                metrics["required_notional"],
                2,
            ),
            liquidity_capacity_ceiling_usd=(
                round(float(ceiling), 2)
                if ceiling is not None
                else None
            ),
            capacity_utilization_pct=(
                round(float(utilization), 2)
                if utilization is not None
                else None
            ),
            capacity_scalable_fraction=round(
                float(metrics["capacity_scalable_fraction"]),
                4,
            ),
        )
        scored.append((ranked, result))

    scored.sort(
        key=lambda item: (
            not item[1].capacity_eligible,
            -item[1].total_score,
            -item[1].capital_deployability_score,
            -item[1].net_return_velocity_pct_per_hour,
            -item[1].risk_efficiency_ratio,
            item[0].rank,
            item[1].symbol,
        )
    )
    return [
        CapitalEfficientOpportunity(
            rank=index,
            original_rank=ranked.rank,
            ranked_opportunity=ranked,
            capital_efficiency=result,
        )
        for index, (ranked, result) in enumerate(scored, start=1)
    ]
