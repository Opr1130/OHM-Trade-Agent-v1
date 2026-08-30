from types import SimpleNamespace

from app.services.capital_efficiency_ranking import rank_capital_efficiency
from app.services.profit_ranking import RankedOpportunity


def ranked(
    symbol: str,
    *,
    base_score: float = 80.0,
    target_move_pct: float = 8.0,
    net_profit: float = 160.0,
    capital: float = 2000.0,
    stop_pct: float = 4.0,
    hourly_range_pct: float = 2.0,
    rr2: float = 3.0,
    original_rank: int = 1,
):
    snapshot = SimpleNamespace(
        symbol=symbol,
        average_hourly_range_24h_pct=hourly_range_pct,
        atr_pct=hourly_range_pct,
        execution_validation=SimpleNamespace(
            estimated_visible_round_trip_market_drag_pct=0.2,
        ),
    )
    economic = SimpleNamespace(
        recommended_capital=capital,
        target_2_net_profit=net_profit,
        target_2_move_pct=target_move_pct,
        stop_pct=stop_pct,
    )
    plan = SimpleNamespace(reward_to_risk_2=rr2)
    opportunity = SimpleNamespace(
        snapshot=snapshot,
        economic_quality=economic,
        plan=plan,
    )
    profit_ranking = SimpleNamespace(total_score=base_score)
    return RankedOpportunity(
        rank=original_rank,
        opportunity=opportunity,
        profit_ranking=profit_ranking,
    )


def test_faster_same_quality_opportunity_uses_capital_better():
    slow = ranked(
        "SLOWUSD",
        hourly_range_pct=1.0,
        original_rank=1,
    )
    fast = ranked(
        "FASTUSD",
        hourly_range_pct=4.0,
        original_rank=2,
    )

    result = rank_capital_efficiency([slow, fast])

    assert result[0].capital_efficiency.symbol == "FASTUSD"
    assert result[0].capital_efficiency.hold_proxy_hours < (
        result[1].capital_efficiency.hold_proxy_hours
    )
    assert result[0].capital_efficiency.net_return_velocity_pct_per_hour > (
        result[1].capital_efficiency.net_return_velocity_pct_per_hour
    )


def test_lower_stop_risk_wins_when_other_inputs_match():
    wide = ranked(
        "WIDEUSD",
        stop_pct=8.0,
        original_rank=1,
    )
    tight = ranked(
        "TIGHTUSD",
        stop_pct=3.0,
        original_rank=2,
    )

    result = rank_capital_efficiency([wide, tight])

    assert result[0].capital_efficiency.symbol == "TIGHTUSD"
    assert result[0].capital_efficiency.risk_efficiency_ratio > (
        result[1].capital_efficiency.risk_efficiency_ratio
    )


def test_base_quality_still_materially_affects_global_rank():
    high_quality = ranked(
        "QUALITYUSD",
        base_score=95.0,
        hourly_range_pct=2.0,
        original_rank=1,
    )
    weak = ranked(
        "WEAKUSD",
        base_score=55.0,
        hourly_range_pct=2.5,
        original_rank=2,
    )

    result = rank_capital_efficiency([weak, high_quality])

    assert result[0].capital_efficiency.symbol == "QUALITYUSD"


def test_score_is_bounded_and_rank_is_deterministic():
    rows = [
        ranked("BBBUSD", original_rank=2),
        ranked("AAAUSD", original_rank=1),
    ]

    first = rank_capital_efficiency(rows)
    second = rank_capital_efficiency(rows)

    assert [item.capital_efficiency.symbol for item in first] == [
        item.capital_efficiency.symbol for item in second
    ]
    assert all(
        0.0 <= item.capital_efficiency.total_score <= 100.0
        for item in first
    )
