from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CapitalAllocation:
    recommended_capital: float
    risk_dollars: float
    position_pct: float
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def recommend_capital(
    *,
    available_capital: float,
    stop_distance_pct: float,
    confidence_score: float,
    net_edge_pct: float,
    calibration_multiplier: float = 1.0,
    max_position_pct: float = 20.0,
    risk_per_trade_pct: float = 0.75,
    leverage: float = 1.0,
    quality_score: float | None = None,
) -> CapitalAllocation:
    values = {
        "available_capital": available_capital,
        "stop_distance_pct": stop_distance_pct,
        "confidence_score": confidence_score,
        "net_edge_pct": net_edge_pct,
        "calibration_multiplier": calibration_multiplier,
        "max_position_pct": max_position_pct,
        "risk_per_trade_pct": risk_per_trade_pct,
        "leverage": leverage,
    }
    if quality_score is not None:
        values["quality_score"] = quality_score
    non_finite = [name for name, value in values.items() if not math.isfinite(float(value))]
    if non_finite:
        raise ValueError(f"capital allocation inputs must be finite: {', '.join(non_finite)}")

    if available_capital <= 0 or stop_distance_pct <= 0:
        raise ValueError("available_capital and stop_distance_pct must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if not 0 < max_position_pct <= 100:
        raise ValueError("max_position_pct must be in (0, 100]")
    if risk_per_trade_pct <= 0:
        raise ValueError("risk_per_trade_pct must be positive")
    if net_edge_pct <= 0:
        return CapitalAllocation(0.0, 0.0, 0.0, "non-positive projected net edge")

    # Prefer a deterministic quality score (Profit Rank / technical quality)
    # when supplied. AI confidence remains a comparative review signal, not a
    # probability, so it is only a backward-compatible fallback.
    raw_quality = quality_score if quality_score is not None else confidence_score
    quality = max(0.0, min(100.0, raw_quality)) / 100.0
    edge_factor = max(0.25, min(1.0, net_edge_pct / 2.0))
    calibration = max(0.75, min(1.25, calibration_multiplier))

    risk_budget = available_capital * risk_per_trade_pct / 100.0
    risk_sized_capital = risk_budget / ((stop_distance_pct / 100.0) * leverage)
    cap = available_capital * max_position_pct / 100.0
    hard_cap = min(cap, risk_sized_capital)

    recommended = hard_cap * quality * edge_factor * calibration
    recommended = max(0.0, min(hard_cap, recommended))
    risk_dollars = recommended * leverage * stop_distance_pct / 100.0
    risk_dollars = min(risk_budget, risk_dollars)

    return CapitalAllocation(
        round(recommended, 2),
        round(risk_dollars, 2),
        round(recommended / available_capital * 100, 2),
        "risk-, quality-, edge-, leverage-, and calibration-adjusted; hard risk budget enforced",
    )
