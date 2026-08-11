from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CapitalAllocation:
    recommended_capital: float
    risk_dollars: float
    position_pct: float
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def recommend_capital(*, available_capital: float, stop_distance_pct: float, confidence_score: float, net_edge_pct: float, calibration_multiplier: float = 1.0, max_position_pct: float = 20.0, risk_per_trade_pct: float = 0.75) -> CapitalAllocation:
    if available_capital <= 0 or stop_distance_pct <= 0:
        raise ValueError("available_capital and stop_distance_pct must be positive")
    if net_edge_pct <= 0:
        return CapitalAllocation(0.0, 0.0, 0.0, "non-positive projected net edge")
    confidence = max(0.0, min(100.0, confidence_score)) / 100.0
    edge_factor = max(0.25, min(1.0, net_edge_pct / 2.0))
    calibration = max(0.75, min(1.25, calibration_multiplier))
    risk_budget = available_capital * risk_per_trade_pct / 100.0
    risk_sized = risk_budget / (stop_distance_pct / 100.0)
    cap = available_capital * max_position_pct / 100.0
    recommended = min(cap, risk_sized) * confidence * edge_factor * calibration
    recommended = max(0.0, min(cap, recommended))
    risk_dollars = recommended * stop_distance_pct / 100.0
    return CapitalAllocation(round(recommended, 2), round(risk_dollars, 2), round(recommended / available_capital * 100, 2), "risk-, confidence-, edge-, and calibration-adjusted")
