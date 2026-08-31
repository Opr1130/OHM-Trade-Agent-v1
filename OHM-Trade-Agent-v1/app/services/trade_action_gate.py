from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any

from app.services.active_trade_registry import get_active_trades
from app.services.capital_efficiency_ranking import MIN_EXECUTABLE_NOTIONAL_USD
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.economic_quality_gate import MIN_NET_PROFIT
from app.services.portfolio_risk import evaluate_portfolio_risk
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
        reason = "economic qualification is required for an actionable trade"
        candidate["action_gate_evaluated"] = True
        candidate["action_gate_allowed"] = False
        candidate["portfolio_risk_allowed"] = False
        candidate["portfolio_risk_reason"] = reason
        return ActionGateDecision(False, reason)

    try:
        account_capital = float(account_capital)
    except (TypeError, ValueError):
        account_capital = math.nan
    if not math.isfinite(account_capital) or account_capital <= 0:
        reason = "account capital is unavailable or invalid"
        candidate["action_gate_evaluated"] = True
        candidate["action_gate_allowed"] = False
        candidate["portfolio_risk_allowed"] = False
        candidate["portfolio_risk_reason"] = reason
        return ActionGateDecision(False, reason)

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

    capacity_reason: str | None = None
    direction = str(
        candidate.get("direction") or plan.direction or "LONG"
    ).upper()
    try:
        leverage = float(
            candidate.get("margin_leverage")
            or (2.0 if direction == "SHORT" else 1.0)
        )
    except (TypeError, ValueError):
        leverage = math.nan
    if direction not in {"LONG", "SHORT"} or not math.isfinite(leverage) or leverage <= 0:
        capacity_reason = "invalid direction or leverage at action gate"

    ceiling = candidate.get("liquidity_capacity_ceiling_usd")
    if (
        capacity_reason is None
        and isinstance(ceiling, (int, float))
        and math.isfinite(float(ceiling))
        and float(ceiling) > 0
    ):
        max_capital_by_capacity = float(ceiling) / leverage
        original_capital = float(
            intelligence.allocation.recommended_capital
        )
        if original_capital > max_capital_by_capacity:
            capped_capital = max(0.0, max_capital_by_capacity)
            ratio = (
                capped_capital / original_capital
                if original_capital > 0
                else 0.0
            )
            capped_allocation = replace(
                intelligence.allocation,
                recommended_capital=round(capped_capital, 2),
                risk_dollars=round(
                    intelligence.allocation.risk_dollars * ratio,
                    2,
                ),
                position_pct=round(
                    capped_capital / account_capital * 100.0,
                    2,
                ),
                reason=(
                    intelligence.allocation.reason
                    + "; capped by Wave 9 liquidity capacity"
                ),
            )
            capped_portfolio = evaluate_portfolio_risk(
                active_trades=trades,
                proposed_symbol=plan.symbol,
                proposed_direction=direction,
                proposed_capital=capped_allocation.recommended_capital,
                proposed_leverage=leverage,
                account_capital=account_capital,
            )
            intelligence = replace(
                intelligence,
                allocation=capped_allocation,
                portfolio_risk=capped_portfolio,
            )
            candidate["liquidity_capacity_capped"] = True
            candidate["liquidity_capacity_max_capital"] = round(
                max_capital_by_capacity,
                2,
            )

            validation_capital = candidate.get(
                "economic_validation_capital"
            )
            validation_net = candidate.get(
                "economic_validation_net_t2"
            )
            if (
                isinstance(validation_capital, (int, float))
                and float(validation_capital) > 0
                and isinstance(validation_net, (int, float))
            ):
                economic_scale = min(
                    1.0,
                    capped_capital / float(validation_capital),
                )
                adjusted_net = float(validation_net) * economic_scale
                candidate["capacity_adjusted_validation_net_t2"] = round(
                    adjusted_net,
                    2,
                )
                if adjusted_net < MIN_NET_PROFIT:
                    capacity_reason = (
                        f"capacity-capped validation net profit "
                        f"USD {adjusted_net:.2f} is below economic minimum "
                        f"USD {MIN_NET_PROFIT:.2f}"
                    )
        else:
            candidate["liquidity_capacity_capped"] = False
    final_notional = (
        float(intelligence.allocation.recommended_capital) * leverage
        if math.isfinite(leverage) and leverage > 0
        else 0.0
    )
    candidate["recommended_position_notional"] = round(final_notional, 2)
    if (
        capacity_reason is None
        and (
            not math.isfinite(final_notional)
            or final_notional < MIN_EXECUTABLE_NOTIONAL_USD
        )
    ):
        capacity_reason = (
            f"recommended position notional USD {final_notional:.2f} is below "
            f"minimum executable notional USD {MIN_EXECUTABLE_NOTIONAL_USD:.2f}"
        )

    candidate["recommended_capital"] = intelligence.allocation.recommended_capital
    candidate["recommended_risk_dollars"] = intelligence.allocation.risk_dollars
    candidate["projected_net_edge_pct"] = intelligence.projected_net_edge_pct
    candidate["calibration_status"] = intelligence.calibration_status
    candidate["calibration_multiplier"] = intelligence.calibration_multiplier
    candidate["portfolio_risk_allowed"] = intelligence.portfolio_risk.allowed
    candidate["portfolio_risk_reason"] = intelligence.portfolio_risk.reason
    candidate["action_gate_evaluated"] = True
    allowed = intelligence.allowed and capacity_reason is None
    candidate["action_gate_allowed"] = allowed

    reason = (
        capacity_reason
        if capacity_reason is not None
        else (
            "capital, liquidity-capacity, and portfolio guardrails passed"
            if intelligence.allowed
            else intelligence.portfolio_risk.reason
            or intelligence.allocation.reason
        )
    )
    return ActionGateDecision(
        allowed,
        reason,
        intelligence,
    )