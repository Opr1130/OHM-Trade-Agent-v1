from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from app.services.profit_ranking import RankedOpportunity


@dataclass(frozen=True)
class CapitalEfficiencyResult:
    symbol: str
    total_score: float
    base_quality_score: float
    capital_velocity_score: float
    risk_efficiency_score: float
    reward_risk_score: float
    net_return_pct: float
    hold_proxy_hours: float
    net_return_velocity_pct_per_hour: float
    risk_efficiency_ratio: float


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


def _raw_metrics(ranked: RankedOpportunity) -> dict[str, float]:
    opportunity = ranked.opportunity
    economic = opportunity.economic_quality
    snapshot = opportunity.snapshot
    plan = opportunity.plan

    capital = max(0.0, _finite(getattr(economic, "recommended_capital", 0.0)))
    net_profit = max(0.0, _finite(getattr(economic, "target_2_net_profit", 0.0)))
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

    return {
        "base": max(0.0, min(100.0, _finite(ranked.profit_ranking.total_score))),
        "net_return_pct": net_return_pct,
        "hold_proxy_hours": hold_proxy,
        "velocity": velocity,
        "risk_efficiency": risk_efficiency,
        "rr2": rr2,
    }


def rank_capital_efficiency(
    ranked_opportunities: list[RankedOpportunity],
) -> list[CapitalEfficientOpportunity]:
    """Cross-sectionally rank the already-qualified set for scarce capital.

    This is a deterministic comparative score, not expected return and not a
    probability. It cannot rescue any candidate rejected by prior gates.
    """
    if not ranked_opportunities:
        return []

    raw = [(ranked, _raw_metrics(ranked)) for ranked in ranked_opportunities]
    max_velocity = max((metrics["velocity"] for _, metrics in raw), default=0.0)
    max_risk_efficiency = max(
        (metrics["risk_efficiency"] for _, metrics in raw),
        default=0.0,
    )

    scored: list[tuple[RankedOpportunity, CapitalEfficiencyResult]] = []
    for ranked, metrics in raw:
        base_component = metrics["base"] * 0.55
        velocity_component = (
            20.0 * metrics["velocity"] / max_velocity
            if max_velocity > 0
            else 0.0
        )
        risk_component = (
            15.0 * metrics["risk_efficiency"] / max_risk_efficiency
            if max_risk_efficiency > 0
            else 0.0
        )
        rr_component = 10.0 * min(1.0, metrics["rr2"] / 3.0)
        total = max(
            0.0,
            min(
                100.0,
                base_component
                + velocity_component
                + risk_component
                + rr_component,
            ),
        )
        scored.append(
            (
                ranked,
                CapitalEfficiencyResult(
                    symbol=ranked.opportunity.snapshot.symbol,
                    total_score=round(total, 2),
                    base_quality_score=round(base_component, 2),
                    capital_velocity_score=round(velocity_component, 2),
                    risk_efficiency_score=round(risk_component, 2),
                    reward_risk_score=round(rr_component, 2),
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
                ),
            )
        )

    scored.sort(
        key=lambda item: (
            -item[1].total_score,
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
