from app.services.execution_optimizer import choose_execution_plan


def test_thin_market_forces_passive_execution():
    plan = choose_execution_plan(
        spread_pct=0.20,
        observed_slippage_pct=0.05,
        liquidity_tier="THIN",
        catalyst_risk="NONE_KNOWN",
    )
    assert plan.style == "PASSIVE_LIMIT"
    assert plan.urgency == "LOW"


def test_near_catalyst_avoids_urgent_execution():
    plan = choose_execution_plan(
        spread_pct=0.05,
        observed_slippage_pct=0.03,
        liquidity_tier="DEEP",
        catalyst_risk="IMMINENT",
    )
    assert plan.style == "WAIT_OR_PASSIVE_LIMIT"


def test_deep_tight_market_uses_normal_limit():
    plan = choose_execution_plan(
        spread_pct=0.05,
        observed_slippage_pct=0.04,
        liquidity_tier="DEEP",
        catalyst_risk="NONE_KNOWN",
    )
    assert plan.style == "LIMIT"
    assert plan.urgency == "NORMAL"
