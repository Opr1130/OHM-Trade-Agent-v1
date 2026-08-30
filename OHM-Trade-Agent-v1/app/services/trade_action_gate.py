from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.active_trade_registry import get_active_trades
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.trade_decision_intelligence import (
    TradeDecisionIntelligence,
    evaluate_trade_decision,
)


@dataclass(frozen=True)
class ActionGateDecision:
    allowed: bool
    reason: str
    intelligence: TradeDecisionIntelligence | None = None


def apply_action_gate(
    *,
    candidate: dict[str, Any],
    plan: EntryExitPlan,
    account_capital: float,
    active_trades: list[Any] | None = None,
) -> ActionGateDecision:
    """Evaluate capital/portfolio eligibility before notification.

    This is intentionally upstream of Telegram. The notifier consumes the
    resulting decision; it does not decide portfolio eligibility itself.
    """
    if candidate.get("economic_qualified") is not True:
        candidate["action_gate_evaluated"] = True
        candidate["action_gate_allowed"] = True
        candidate["portfolio_risk_allowed"] = True
        candidate["portfolio_risk_reason"] = "economic allocation not required"
        return ActionGateDecision(True, "economic allocation not required")

    trades = list(active_trades) if active_trades is not None else get_active_trades()
    try:
        intelligence = evaluate_trade_decision(
            candidate=candidate,
            plan=plan,
            account_capital=account_capital,
            active_trades=trades,
        )
    except Exception as exc:
        candidate["action_gate_evaluated"] = True
        candidate["action_gate_allowed"] = False
        candidate["portfolio_risk_allowed"] = False
        candidate["portfolio_risk_reason"] = (
            f"action gate unavailable: {type(exc).__name__}: {exc}"
        )
        return ActionGateDecision(
            False,
            candidate["portfolio_risk_reason"],
            None,
        )

    candidate["recommended_capital"] = intelligence.allocation.recommended_capital
    candidate["recommended_risk_dollars"] = intelligence.allocation.risk_dollars
    candidate["projected_net_edge_pct"] = intelligence.projected_net_edge_pct
    candidate["calibration_status"] = intelligence.calibration_status
    candidate["calibration_multiplier"] = intelligence.calibration_multiplier
    candidate["portfolio_risk_allowed"] = intelligence.portfolio_risk.allowed
    candidate["portfolio_risk_reason"] = intelligence.portfolio_risk.reason
    candidate["action_gate_evaluated"] = True
    candidate["action_gate_allowed"] = intelligence.allowed

    reason = (
        "capital and portfolio guardrails passed"
        if intelligence.allowed
        else intelligence.portfolio_risk.reason
        or intelligence.allocation.reason
    )
    return ActionGateDecision(
        intelligence.allowed,
        reason,
        intelligence,
    )
