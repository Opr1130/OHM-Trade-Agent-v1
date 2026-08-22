import math
from dataclasses import dataclass

from app.services.entry_exit_advisor import EntryExitPlan


DEFAULT_MAX_ACCOUNT_RISK_AT_STOP_PCT = 5.0
DEFAULT_MAX_CAPITAL_FRACTION = 0.20


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


def _invalid_result(direction: str, leverage: float, reason: str) -> EconomicGateResult:
    safe_leverage = leverage if math.isfinite(float(leverage)) and leverage > 0 else 1.0
    return EconomicGateResult(
        False, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        reason, direction=direction, leverage=safe_leverage,
    )


def evaluate_economic_quality(
    plan: EntryExitPlan,
    available_capital: float,
    *,
    min_target_2_move_pct: float = 4.0,
    preferred_target_2_move_pct: float = 7.0,
    min_net_profit: float = 75.0,
    min_reward_to_risk: float = 2.5,
    estimated_round_trip_cost_pct: float = 0.60,
    max_capital_fraction: float = DEFAULT_MAX_CAPITAL_FRACTION,
    direction: str | None = None,
    leverage: float = 1.0,
    estimated_margin_cost_pct: float = 0.0,
    max_account_risk_at_stop_pct: float | None = DEFAULT_MAX_ACCOUNT_RISK_AT_STOP_PCT,
) -> EconomicGateResult:
    direction = (direction or getattr(plan, "direction", "LONG") or "LONG").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    scalar_values = (
        available_capital,
        min_target_2_move_pct,
        preferred_target_2_move_pct,
        min_net_profit,
        min_reward_to_risk,
        estimated_round_trip_cost_pct,
        max_capital_fraction,
        leverage,
        estimated_margin_cost_pct,
    )
    if not all(math.isfinite(float(value)) for value in scalar_values):
        return _invalid_result(direction, leverage, "Invalid non-finite economic-gate input")
    if max_account_risk_at_stop_pct is not None and not math.isfinite(float(max_account_risk_at_stop_pct)):
        return _invalid_result(direction, leverage, "Invalid non-finite account-risk limit")
    if available_capital <= 0:
        return _invalid_result(direction, leverage, "Available capital must be positive")
    if leverage <= 0:
        return _invalid_result(direction, leverage, "Leverage must be positive")
    if not 0 < max_capital_fraction <= 1:
        return _invalid_result(direction, leverage, "Capital fraction must be between 0 and 1")
    if estimated_round_trip_cost_pct < 0 or estimated_margin_cost_pct < 0:
        return _invalid_result(direction, leverage, "Estimated costs cannot be negative")

    plan_values = (
        plan.entry_low,
        plan.entry_high,
        plan.chase_limit,
        plan.stop_price,
        plan.target_1,
        plan.target_2,
        plan.reward_to_risk_1,
        plan.reward_to_risk_2,
    )
    if not all(math.isfinite(float(value)) for value in plan_values):
        return _invalid_result(direction, leverage, "Invalid non-finite plan geometry")

    entry_reference = (plan.entry_low + plan.entry_high) / 2
    if entry_reference <= 0 or min(plan.entry_low, plan.entry_high, plan.chase_limit, plan.stop_price, plan.target_1, plan.target_2) <= 0:
        return _invalid_result(direction, leverage, "Invalid non-positive plan geometry")

    if direction == "SHORT":
        geometry_valid = 0 < plan.target_2 < plan.target_1 < entry_reference < plan.stop_price
    else:
        geometry_valid = 0 < plan.stop_price < entry_reference < plan.target_1 < plan.target_2
    if not geometry_valid:
        return _invalid_result(direction, leverage, "Invalid target/stop geometry")

    stop_pct = abs(entry_reference - plan.stop_price) / entry_reference * 100
    if direction == "SHORT":
        target_1_move_pct = (entry_reference - plan.target_1) / entry_reference * 100
        target_2_move_pct = (entry_reference - plan.target_2) / entry_reference * 100
    else:
        target_1_move_pct = (plan.target_1 - entry_reference) / entry_reference * 100
        target_2_move_pct = (plan.target_2 - entry_reference) / entry_reference * 100

    calculated = (stop_pct, target_1_move_pct, target_2_move_pct)
    if not all(math.isfinite(value) and value > 0 for value in calculated):
        return _invalid_result(direction, leverage, "Invalid calculated economic geometry")

    # Economic validation must use the same maximum posted-capital envelope as
    # the production allocator. Previously the default was 100% of equity while
    # allocation is capped at 20%, which could overstate the dollar profit floor
    # by roughly 5x.
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
            f"Projected net profit ${target_2_net_profit:.2f} at the "
            f"{max_capital_fraction * 100:.0f}% capital envelope is below minimum "
            f"${min_net_profit:.2f}"
        )
    elif (
        max_account_risk_at_stop_pct is not None
        and account_risk_at_stop_pct > max_account_risk_at_stop_pct
    ):
        rejection_reason = (
            f"stop exposure {account_risk_at_stop_pct:.2f}% exceeds "
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
