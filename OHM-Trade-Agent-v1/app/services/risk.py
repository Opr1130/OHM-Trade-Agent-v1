from app.models.signal import RiskPlan, TradingSignal
from app.services.drawdown_guard import DrawdownPolicy, evaluate_drawdown_guard


def build_risk_plan(
    signal: TradingSignal,
    equity: float,
    risk_pct: float,
    *,
    peak_equity: float | None = None,
    drawdown_policy: DrawdownPolicy = DrawdownPolicy(),
) -> RiskPlan:
    """Build a stop-based position plan with optional portfolio drawdown control.

    Existing callers remain unchanged. When ``peak_equity`` is supplied, the
    portfolio-level drawdown guard can reduce risk or halt new entries before
    signal-level reward/risk validation is applied.
    """
    if equity < 0:
        raise ValueError("equity must be non-negative")
    if risk_pct < 0:
        raise ValueError("risk_pct must be non-negative")

    size_multiplier = 1.0
    if peak_equity is not None:
        drawdown = evaluate_drawdown_guard(
            current_equity=equity,
            peak_equity=peak_equity,
            policy=drawdown_policy,
        )
        if not drawdown.allow_new_entries:
            return RiskPlan(
                risk_dollars=0,
                position_size=0,
                reward_to_risk=0,
                allowed=False,
                rejection_reason=f"Portfolio risk halted: {drawdown.reason}",
            )
        size_multiplier = drawdown.size_multiplier

    stop_distance = abs(signal.price - signal.stop_price)
    reward_distance = abs(signal.target_price - signal.price)
    risk_dollars = equity * (risk_pct / 100) * size_multiplier

    if stop_distance <= 0:
        return RiskPlan(
            risk_dollars=0,
            position_size=0,
            reward_to_risk=0,
            allowed=False,
            rejection_reason="Invalid stop distance",
        )

    position_size = risk_dollars / stop_distance
    reward_to_risk = reward_distance / stop_distance
    allowed = reward_to_risk >= 2.0

    return RiskPlan(
        risk_dollars=round(risk_dollars, 2),
        position_size=round(position_size, 8),
        reward_to_risk=round(reward_to_risk, 2),
        allowed=allowed,
        rejection_reason=None if allowed else "Reward-to-risk is below 2.0",
    )
