from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PortfolioRiskDecision:
    allowed: bool
    reason: str
    open_positions: int
    gross_exposure: float
    proposed_exposure: float
    proposed_total_exposure: float

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_portfolio_risk(*, active_trades: list[Any], proposed_symbol: str, proposed_direction: str, proposed_capital: float, account_capital: float, max_positions: int = 3, max_gross_exposure_pct: float = 50.0, max_same_direction: int = 2) -> PortfolioRiskDecision:
    if account_capital <= 0 or proposed_capital < 0:
        raise ValueError("invalid capital")
    positions = [t for t in active_trades if getattr(t, "status", "active") == "active"]
    gross = sum(float(getattr(t, "capital", 0.0) or 0.0) * float(getattr(t, "margin_leverage", 1.0) or 1.0) for t in positions)
    proposed = proposed_capital
    total = gross + proposed
    base = dict(open_positions=len(positions), gross_exposure=round(gross, 2), proposed_exposure=round(proposed, 2), proposed_total_exposure=round(total, 2))
    if any(str(getattr(t, "symbol", "")).upper() == proposed_symbol.upper() for t in positions):
        return PortfolioRiskDecision(False, "symbol already active", **base)
    if len(positions) >= max_positions:
        return PortfolioRiskDecision(False, "maximum simultaneous positions reached", **base)
    same_direction = sum(str(getattr(t, "direction", "LONG")).upper() == proposed_direction.upper() for t in positions)
    if same_direction >= max_same_direction:
        return PortfolioRiskDecision(False, "same-direction concentration limit reached", **base)
    if total / account_capital * 100 > max_gross_exposure_pct:
        return PortfolioRiskDecision(False, "gross exposure limit exceeded", **base)
    return PortfolioRiskDecision(True, "portfolio risk limits satisfied", **base)
