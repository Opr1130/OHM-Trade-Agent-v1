from dataclasses import dataclass

from app.services.entry_exit_advisor import EntryExitPlan


@dataclass
class EconomicGateResult:
    qualified: bool
    entry_reference: float
    stop_pct: float
    target_1_move_pct: float
    target_2_move_pct: float
    recommended_capital: float
    target_1_gross_profit: float
    target_2_gross_profit: float
    estimated_costs: float
    target_1_net_profit: float
    target_2_net_profit: float
    rejection_reason: str | None = None
    direction: str = "LONG"
    leverage: float = 1.0
    position_notional: float = 0.0
    account_risk_at_stop_pct: float = 0.0
    estimated_margin_cost_pct: float = 0.0


def evaluate_economic_quality(
    plan: EntryExitPlan,
    available_capital: float,
    *,
    min_target_2_move_pct: float = 4.0,
    preferred_target_2_move_pct: float = 7.0,
    min_net_profit: float = 75.0,
    min_reward_to_risk: float = 2.5,
    estimated_round_trip_cost_pct: float = 0.60,
    max_capital_fraction: float = 1.0,
    direction: str | None = None,
    leverage: float = 1.0,
    estimated_margin_cost_pct: float = 0.0,
    max_account_risk_at_stop_pct: float | None = None,
) -> EconomicGateResult:
    direction = (direction or getattr(plan, "direction", "LONG") or "LONG").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if leverage <= 0:
        raise ValueError("leverage must be positive")

    entry_reference = (plan.entry_low + plan.entry_high) / 2
    if entry_reference <= 0:
        return EconomicGateResult(
            False, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            "Invalid entry reference", direction=direction, leverage=leverage,
        )

    stop_pct = abs(entry_reference - plan.stop_price) / entry_reference * 100
    if direction == "SHORT":
        target_1_move_pct = (entry_reference - plan.target_1) / entry_reference * 100
        target_2_move_pct = (entry_reference - plan.target_2) / entry_reference * 100
    else:
        target_1_move_pct = (plan.target_1 - entry_reference) / entry_reference * 100
        target_2_move_pct = (plan.target_2 - entry_reference) / entry_reference * 100

    recommended_capital = available_capital * max_capital_fraction
    position_notional = recommended_capital * leverage
    target_1_gross_profit = position_notional * target_1_move_pct / 100
    target_2_gross_profit = position_notional * target_2_move_pct / 100
    total_cost_pct = estimated_round_trip_cost_pct + estimated_margin_cost_pct
    estimated_costs = position_notional * total_cost_pct / 100
    target_1_net_profit = target_1_gross_profit - estimated_costs
    target_2_net_profit = target_2_gross_profit - estimated_costs
    account_risk_at_stop_pct = stop_pct * leverage * max_capital_fraction

    rejection_reason = None
    if plan.reward_to_risk_2 < min_reward_to_risk:
        rejection_reason = (
            f"Reward/risk {plan.reward_to_risk_2:.2f}:1 is below minimum "
            f"{min_reward_to_risk:.2f}:1"
        )
    elif target_2_move_pct < min_target_2_move_pct:
        rejection_reason = (
            f"Projected Target 2 move {target_2_move_pct:.2f}% is below minimum "
            f"{min_target_2_move_pct:.2f}%"
        )
    elif target_2_net_profit < min_net_profit:
        rejection_reason = (
            f"Projected net profit ${target_2_net_profit:.2f} is below minimum "
            f"${min_net_profit:.2f}"
        )
    elif (
        max_account_risk_at_stop_pct is not None
        and account_risk_at_stop_pct > max_account_risk_at_stop_pct
    ):
        rejection_reason = (
            f"Leveraged stop exposure {account_risk_at_stop_pct:.2f}% exceeds "
            f"maximum {max_account_risk_at_stop_pct:.2f}% of account equity"
        )

    return EconomicGateResult(
        qualified=rejection_reason is None,
        entry_reference=round(entry_reference, 8),
        stop_pct=round(stop_pct, 2),
        target_1_move_pct=round(target_1_move_pct, 2),
        target_2_move_pct=round(target_2_move_pct, 2),
        recommended_capital=round(recommended_capital, 2),
        target_1_gross_profit=round(target_1_gross_profit, 2),
        target_2_gross_profit=round(target_2_gross_profit, 2),
        estimated_costs=round(estimated_costs, 2),
        target_1_net_profit=round(target_1_net_profit, 2),
        target_2_net_profit=round(target_2_net_profit, 2),
        rejection_reason=rejection_reason,
        direction=direction,
        leverage=round(leverage, 2),
        position_notional=round(position_notional, 2),
        account_risk_at_stop_pct=round(account_risk_at_stop_pct, 2),
        estimated_margin_cost_pct=round(estimated_margin_cost_pct, 4),
    )
