from types import SimpleNamespace

import pytest

from app.exchanges.kraken import BookLevel, PreTradeBook, KrakenAPIError
from app.scanner.directional_candidates import select_directional_candidates
from app.scanner.execution_validation import evaluate_execution
from app.scanner.margin_eligibility import (
    validate_short_margin_eligibility,
    keep_margin_tradeable_candidates,
    recover_margin_rejected_long,
)
from app.scanner.models import MarketSnapshot
from app.scanner.short_execution_quality import short_execution_is_tradeable
from app.scanner.short_technical_scorer import score_short_snapshot
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.entry_exit_advisor import build_entry_exit_plan
from app.services.short_target_attainability import evaluate_short_target_attainability


def snapshot(**overrides):
    values = dict(
        symbol="TESTUSD",
        last_price=90.0,
        ema20=92.0,
        ema50=95.0,
        ema200=100.0,
        rsi=45.0,
        macd_line=-2.0,
        macd_signal=-1.0,
        macd_histogram=-1.0,
        atr=2.0,
        atr_pct=2.22,
        volume_ratio=1.6,
        technical_score=10,
        trend="bearish",
        recent_24h_high=99.0,
        recent_24h_low=82.0,
        recent_72h_high=108.0,
        recent_72h_low=74.0,
        momentum_6h_pct=-1.0,
        momentum_24h_pct=-3.0,
        momentum_72h_pct=-6.0,
        rolling_24h_range_median_pct=10.0,
        realized_range_24h_pct=10.0,
        rolling_24h_downside_median_pct=5.0,
        rolling_24h_downside_p75_pct=8.0,
        rolling_24h_downside_p90_pct=12.0,
        rolling_72h_downside_median_pct=8.0,
        rolling_72h_downside_p75_pct=12.0,
        rolling_72h_downside_p90_pct=18.0,
        underlying_asset="TEST",
        primary_pair="TESTUSD",
        primary_quote_currency="USD",
        combined_24h_liquidity_usd=1_000_000.0,
        ticker_last=90.0,
        kraken_public_symbol="TEST/USD",
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def test_strong_bearish_structure_scores_high():
    card = score_short_snapshot(snapshot())
    assert card.score >= 80
    assert "Bearish trend" in card.strengths


def test_oversold_short_is_not_chased_with_full_score():
    card = score_short_snapshot(snapshot(rsi=20.0))
    assert card.score < 100
    assert any("oversold" in reason.lower() for reason in card.weaknesses)


def test_directional_shortlist_selects_best_direction_once():
    bearish = snapshot(symbol="BEARUSD", underlying_asset="BEAR", technical_score=20)
    bullish = snapshot(
        symbol="BULLUSD",
        underlying_asset="BULL",
        last_price=110,
        ema20=105,
        ema50=100,
        ema200=95,
        rsi=55,
        macd_line=2,
        macd_signal=1,
        trend="bullish",
        technical_score=95,
    )
    selected = select_directional_candidates([bearish, bullish])
    assert {(x.symbol, x.trade_direction) for x in selected} == {
        ("BEARUSD", "SHORT"),
        ("BULLUSD", "LONG"),
    }
    assert len({x.underlying_asset for x in selected}) == len(selected)


def test_directional_shortlist_never_exceeds_eight():
    items = [snapshot(symbol=f"S{i}USD", underlying_asset=f"S{i}") for i in range(20)]
    selected = select_directional_candidates(items)
    assert len(selected) <= 8
    assert sum(x.trade_direction == "SHORT" for x in selected) <= 5


def test_short_entry_exit_geometry_and_no_chase_boundary():
    plan = build_entry_exit_plan(snapshot(), "low", direction="SHORT")
    entry = (plan.entry_low + plan.entry_high) / 2
    assert plan.direction == "SHORT"
    assert plan.stop_price > entry > plan.target_1 > plan.target_2 > 0
    assert plan.chase_limit < snapshot().last_price
    assert plan.reward_to_risk_2 == pytest.approx(3.0)


def test_short_extended_downward_waits_for_rebound():
    s = snapshot(last_price=80.0, ema20=90.0, atr=2.0)
    plan = build_entry_exit_plan(s, "low", direction="SHORT")
    assert not plan.valid_now
    assert plan.entry_style == "wait_for_rebound"


def test_short_target_quality_uses_downside_history():
    s = snapshot()
    plan = build_entry_exit_plan(s, "low", direction="SHORT")
    result = evaluate_short_target_attainability(plan, s)
    assert result.target_1_move_pct > 0
    assert result.target_2_move_pct > result.target_1_move_pct
    assert result.attainability_score >= 0


def test_short_target_rejects_move_materially_beyond_downside_p90():
    s = snapshot(
        rolling_24h_downside_median_pct=0.2,
        rolling_24h_downside_p75_pct=0.3,
        rolling_24h_downside_p90_pct=0.4,
        rolling_72h_downside_median_pct=0.3,
        rolling_72h_downside_p75_pct=0.4,
        rolling_72h_downside_p90_pct=0.5,
    )
    result = evaluate_short_target_attainability(
        build_entry_exit_plan(s, "low", direction="SHORT"), s
    )
    assert not result.qualified
    assert any("downside p90" in reason for reason in result.rejection_reasons)


def test_short_economic_gate_accounts_for_leverage_margin_cost_and_risk():
    plan = build_entry_exit_plan(snapshot(), "low", direction="SHORT")
    result = evaluate_economic_quality(
        plan,
        2_000,
        direction="SHORT",
        leverage=2.0,
        estimated_margin_cost_pct=0.28,
        max_account_risk_at_stop_pct=10.0,
    )
    assert result.direction == "SHORT"
    assert result.position_notional == 4_000
    assert result.estimated_margin_cost_pct == pytest.approx(0.28)
    assert result.target_2_move_pct > 0
    assert result.estimated_costs > 0


def test_short_economic_gate_can_reject_excess_leveraged_stop_risk():
    plan = build_entry_exit_plan(snapshot(atr=4.0), "medium", direction="SHORT")
    result = evaluate_economic_quality(
        plan,
        2_000,
        direction="SHORT",
        leverage=2.0,
        estimated_margin_cost_pct=0.28,
        max_account_risk_at_stop_pct=1.0,
    )
    assert not result.qualified
    assert "stop exposure" in (result.rejection_reason or "")


class MarginClient:
    def __init__(self, result=None, error=False):
        self.result = result or {}
        self.error = error
        self.calls = []

    def get_asset_pairs(self, execution_venue=None):
        self.calls.append(execution_venue)
        if self.error:
            raise KrakenAPIError("boom")
        return self.result


def test_margin_pair_presence_is_required_and_bounded_by_account_ceiling():
    s = snapshot(trade_direction="SHORT")
    client = MarginClient({
        "TESTUSD:BTNL": {
            "altname": "TESTUSD:BTNL",
            "wsname": "TEST/USD:BTNL",
            "leverage_sell": [2, 3, 5],
        }
    })
    summary = validate_short_margin_eligibility(
        [s], account_leverage_ceiling=3.0, client=client
    )
    assert summary.eligible == 1
    assert s.margin_eligible
    assert s.margin_max_leverage == 3.0
    assert ":BTNL" in (s.margin_venue_symbol or "")
    assert client.calls == ["bitnomial_exchange"]


def test_margin_missing_pair_rejects_short():
    s = snapshot(trade_direction="SHORT")
    summary = validate_short_margin_eligibility([s], client=MarginClient({}))
    assert summary.ineligible == 1
    assert not s.margin_eligible
    assert keep_margin_tradeable_candidates([s]) == []


def test_margin_api_failure_fails_closed_for_short():
    s = snapshot(trade_direction="SHORT")
    summary = validate_short_margin_eligibility([s], client=MarginClient(error=True))
    assert summary.unavailable == 1
    assert s.margin_validation_status == "UNAVAILABLE"
    assert keep_margin_tradeable_candidates([s]) == []


def _book(spread=0.02):
    bid = 100.0 - spread / 2
    ask = 100.0 + spread / 2
    bids = [BookLevel(bid - i * 0.01, 100.0) for i in range(10)]
    asks = [BookLevel(ask + i * 0.01, 100.0) for i in range(10)]
    return PreTradeBook("TEST/USD", bids=bids, asks=asks)


def test_strict_short_execution_requires_fresh_two_sided_coverage():
    s = snapshot(trade_direction="SHORT")
    execution = evaluate_execution(
        _book(),
        validation_notional_usd=2_000,
        ticker_last=100,
        trades=[],
    )
    # Empty PostTrade becomes WARN and must fail strict short quality.
    s.execution_validation = execution
    ok, reasons = short_execution_is_tradeable(s)
    assert not ok
    assert any("not fresh" in reason for reason in reasons)



def test_margin_rejected_short_recovers_independently_qualified_long():
    selected = select_directional_candidates(
        [snapshot(symbol="FALLBACKUSD", underlying_asset="FALLBACK", technical_score=82)]
    )
    assert len(selected) == 1
    preferred = selected[0]
    assert preferred.trade_direction == "SHORT"
    assert preferred.directional_long_score == 82
    assert preferred.directional_short_score == preferred.technical_score

    validate_short_margin_eligibility([preferred], client=MarginClient({}))
    fallback = recover_margin_rejected_long(preferred)

    assert fallback is not None
    assert fallback.trade_direction == "LONG"
    assert fallback.technical_score == 82
    assert fallback.margin_validation_status == "NOT_REQUIRED"
    assert fallback.margin_eligible is False


def test_margin_rejected_short_does_not_promote_below_threshold_long():
    preferred = snapshot(
        symbol="NOFALLBACKUSD",
        underlying_asset="NOFALLBACK",
        trade_direction="SHORT",
        technical_score=90,
        directional_long_score=79,
        directional_short_score=90,
        margin_validation_status="INELIGIBLE",
        margin_eligible=False,
    )
    assert recover_margin_rejected_long(preferred) is None


def test_margin_eligible_short_never_falls_back_to_long():
    preferred = snapshot(
        symbol="SHORTOKUSD",
        underlying_asset="SHORTOK",
        trade_direction="SHORT",
        technical_score=90,
        directional_long_score=85,
        directional_short_score=90,
        margin_validation_status="ELIGIBLE",
        margin_eligible=True,
    )
    assert recover_margin_rejected_long(preferred) is None
