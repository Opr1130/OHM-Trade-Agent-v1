from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.services.capital_allocation import CapitalAllocation, recommend_capital
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.portfolio_risk import PortfolioRiskDecision, evaluate_portfolio_risk
from app.services.self_calibration import calibration_model, calibrated_multiplier
from app.services.trade_outcome_registry import get_outcomes


@dataclass(frozen=True)
class TradeDecisionIntelligence:
    calibration_status: str
    calibration_multiplier: float
    projected_net_edge_pct: float
    quality_score: float
    allocation: CapitalAllocation
    portfolio_risk: PortfolioRiskDecision

    @property
    def allowed(self) -> bool:
        return self.allocation.recommended_capital > 0 and self.portfolio_risk.allowed

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed"] = self.allowed
        return data


def _projected_net_edge_pct(candidate: dict[str, Any], account_capital: float) -> float:
    net_t2 = candidate.get("economic_validation_net_t2")
    if isinstance(net_t2, (int, float)) and account_capital > 0:
        return max(0.0, float(net_t2) / account_capital * 100.0)
    gross_move = candidate.get("economic_target_2_move_pct")
    if isinstance(gross_move, (int, float)):
        # Conservative fallback when the economic gate did not expose dollar net.
        return max(0.0, float(gross_move) - 0.8)
    return 0.0


def evaluate_trade_decision(
    *,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    account_capital: float,
    active_trades: list[Any],
    outcomes: list[dict[str, Any]] | None = None,
) -> TradeDecisionIntelligence:
    direction = str(candidate.get("direction") or plan.direction or "LONG").upper()
    leverage = float(candidate.get("margin_leverage") or (2.0 if direction == "SHORT" else 1.0))
    entry_reference = (plan.entry_low + plan.entry_high) / 2.0
    stop_distance_pct = abs(entry_reference - plan.stop_price) / entry_reference * 100.0
    net_edge_pct = _projected_net_edge_pct(candidate, account_capital)

    records = outcomes if outcomes is not None else get_outcomes()
    model = calibration_model(records)
    multiplier = calibrated_multiplier(
        model,
        direction=direction,
        regime=candidate.get("market_regime"),
    )

    # Profit Rank is deterministic and preferred for sizing. Technical score is
    # the fallback. AI confidence is not treated as a probability.
    quality_score = candidate.get("profit_rank_score")
    if not isinstance(quality_score, (int, float)):
        quality_score = candidate.get("technical_score")
    if not isinstance(quality_score, (int, float)):
        quality_score = 50.0

    allocation = recommend_capital(
        available_capital=account_capital,
        stop_distance_pct=stop_distance_pct,
        confidence_score=float(candidate.get("confidence") or 0.0),
        quality_score=float(quality_score),
        net_edge_pct=net_edge_pct,
        calibration_multiplier=multiplier,
        leverage=leverage,
    )
    portfolio = evaluate_portfolio_risk(
        active_trades=active_trades,
        proposed_symbol=plan.symbol,
        proposed_direction=direction,
        proposed_capital=allocation.recommended_capital,
        proposed_leverage=leverage,
        account_capital=account_capital,
    )

    return TradeDecisionIntelligence(
        calibration_status=str(model.get("status") or "UNKNOWN"),
        calibration_multiplier=multiplier,
        projected_net_edge_pct=round(net_edge_pct, 4),
        quality_score=round(float(quality_score), 2),
        allocation=allocation,
        portfolio_risk=portfolio,
    )
