from __future__ import annotations

from dataclasses import asdict, dataclass
import math
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


def evaluate_portfolio_risk(
    *,
    active_trades: list[Any],
    proposed_symbol: str,
    proposed_direction: str,
    proposed_capital: float,
    account_capital: float,
    proposed_leverage: float = 1.0,
    max_positions: int = 3,
    max_gross_exposure_pct: float = 50.0,
    max_same_direction: int = 2,
) -> PortfolioRiskDecision:
    direction = proposed_direction.upper()
    numeric = (account_capital, proposed_capital, proposed_leverage, max_gross_exposure_pct)
    if not all(math.isfinite(float(value)) for value in numeric):
        raise ValueError("invalid non-finite capital or leverage")
    if account_capital <= 0 or proposed_capital < 0 or proposed_leverage <= 0:
        raise ValueError("invalid capital or leverage")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("proposed_direction must be LONG or SHORT")

    positions = [
        t for t in active_trades
        if getattr(t, "status", "active") == "active"
    ]

    known_exposures: list[float] = []
    unknown_exposure = False
    for trade in positions:
        capital = getattr(trade, "capital", None)
        leverage = getattr(trade, "margin_leverage", 1.0) or 1.0
        try:
            leverage_value = float(leverage)
        except (TypeError, ValueError):
            unknown_exposure = True
            continue
        if capital is None:
            unknown_exposure = True
            continue
        try:
            capital_value = float(capital)
        except (TypeError, ValueError):
            unknown_exposure = True
            continue
        if (
            not math.isfinite(capital_value)
            or not math.isfinite(leverage_value)
            or capital_value < 0
            or leverage_value <= 0
        ):
            unknown_exposure = True
            continue
        known_exposures.append(capital_value * leverage_value)

    gross = sum(known_exposures)
    proposed = proposed_capital * proposed_leverage
    total = gross + proposed
    base = dict(
        open_positions=len(positions),
        gross_exposure=round(gross, 2),
        proposed_exposure=round(proposed, 2),
        proposed_total_exposure=round(total, 2),
    )

    if any(
        str(getattr(t, "symbol", "")).upper() == proposed_symbol.upper()
        for t in positions
    ):
        return PortfolioRiskDecision(False, "symbol already active", **base)
    if unknown_exposure:
        return PortfolioRiskDecision(
            False,
            "active position exposure is unknown; refusing additional capital until reconciled",
            **base,
        )
    if len(positions) >= max_positions:
        return PortfolioRiskDecision(False, "maximum simultaneous positions reached", **base)
    same_direction = sum(
        str(getattr(t, "direction", "LONG")).upper() == direction
        for t in positions
    )
    if same_direction >= max_same_direction:
        return PortfolioRiskDecision(False, "same-direction concentration limit reached", **base)
    if total / account_capital * 100 > max_gross_exposure_pct:
        return PortfolioRiskDecision(False, "gross exposure limit exceeded", **base)
    return PortfolioRiskDecision(True, "portfolio risk limits satisfied", **base)
