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
) -> EconomicGateResult:

    entry_reference = (
        plan.entry_low + plan.entry_high
    ) / 2

    if entry_reference <= 0:
        return EconomicGateResult(
            qualified=False,
            entry_reference=0,
            stop_pct=0,
            target_1_move_pct=0,
            target_2_move_pct=0,
            recommended_capital=0,
            target_1_gross_profit=0,
            target_2_gross_profit=0,
            estimated_costs=0,
            target_1_net_profit=0,
            target_2_net_profit=0,
            rejection_reason="Invalid entry reference",
        )

    stop_pct = abs(
        entry_reference - plan.stop_price
    ) / entry_reference * 100

    target_1_move_pct = (
        (plan.target_1 - entry_reference)
        / entry_reference
        * 100
    )

    target_2_move_pct = (
        (plan.target_2 - entry_reference)
        / entry_reference
        * 100
    )

    recommended_capital = (
        available_capital
        * max_capital_fraction
    )

    target_1_gross_profit = (
        recommended_capital
        * target_1_move_pct
        / 100
    )

    target_2_gross_profit = (
        recommended_capital
        * target_2_move_pct
        / 100
    )

    estimated_costs = (
        recommended_capital
        * estimated_round_trip_cost_pct
        / 100
    )

    target_1_net_profit = (
        target_1_gross_profit
        - estimated_costs
    )

    target_2_net_profit = (
        target_2_gross_profit
        - estimated_costs
    )

    rejection_reason = None

    if plan.reward_to_risk_2 < min_reward_to_risk:
        rejection_reason = (
            f"Reward/risk {plan.reward_to_risk_2:.2f}:1 "
            f"is below minimum "
            f"{min_reward_to_risk:.2f}:1"
        )

    elif target_2_move_pct < min_target_2_move_pct:
        rejection_reason = (
            f"Projected Target 2 move "
            f"{target_2_move_pct:.2f}% "
            f"is below minimum "
            f"{min_target_2_move_pct:.2f}%"
        )

    elif target_2_net_profit < min_net_profit:
        rejection_reason = (
            f"Projected net profit "
            f"${target_2_net_profit:.2f} "
            f"is below minimum "
            f"${min_net_profit:.2f}"
        )

    qualified = rejection_reason is None

    return EconomicGateResult(
        qualified=qualified,
        entry_reference=round(
            entry_reference,
            8,
        ),
        stop_pct=round(
            stop_pct,
            2,
        ),
        target_1_move_pct=round(
            target_1_move_pct,
            2,
        ),
        target_2_move_pct=round(
            target_2_move_pct,
            2,
        ),
        recommended_capital=round(
            recommended_capital,
            2,
        ),
        target_1_gross_profit=round(
            target_1_gross_profit,
            2,
        ),
        target_2_gross_profit=round(
            target_2_gross_profit,
            2,
        ),
        estimated_costs=round(
            estimated_costs,
            2,
        ),
        target_1_net_profit=round(
            target_1_net_profit,
            2,
        ),
        target_2_net_profit=round(
            target_2_net_profit,
            2,
        ),
        rejection_reason=rejection_reason,
    )
