from app.models.signal import RiskPlan, TradingSignal


def build_risk_plan(signal: TradingSignal, equity: float, risk_pct: float) -> RiskPlan:
    stop_distance = abs(signal.price - signal.stop_price)
    reward_distance = abs(signal.target_price - signal.price)
    risk_dollars = equity * (risk_pct / 100)

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
