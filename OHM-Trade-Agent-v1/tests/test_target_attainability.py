import json
from types import SimpleNamespace

from app.jobs import scan_opportunities
from app.scanner.market_scanner import (
    ScanResult,
    _percentage_change,
    _percentile,
    _rolling_range_percentiles,
    _rolling_upside_excursions,
    _rolling_upside_percentiles,
    _window_metrics,
)
from app.scanner.models import MarketSnapshot
from app.services.economic_quality_gate import evaluate_economic_quality
from app.services.entry_exit_advisor import EntryExitPlan, build_entry_exit_plan
from app.services import chief_analyst
from app.services.target_attainability import evaluate_target_attainability


def snapshot(**overrides) -> MarketSnapshot:
    values = dict(
        symbol="SOL/USD", last_price=100.0, ema20=99.0, ema50=95.0, ema200=90.0,
        rsi=58.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5,
        atr=2.0, atr_pct=2.0, volume_ratio=1.5, technical_score=90, trend="bullish",
        recent_24h_high=110.0, recent_24h_low=94.0,
        recent_72h_high=116.0, recent_72h_low=88.0,
        momentum_6h_pct=1.0, momentum_24h_pct=3.0, momentum_72h_pct=7.0,
        distance_to_24h_high_pct=10.0, distance_to_72h_high_pct=16.0,
        realized_range_24h_pct=16.0, realized_range_72h_pct=28.0,
        average_hourly_range_24h_pct=1.0, average_hourly_range_72h_pct=1.1,
        rolling_24h_range_median_pct=8.0,
        rolling_24h_range_p75_pct=10.0,
        rolling_24h_range_p90_pct=12.0,
        rolling_72h_range_median_pct=12.0,
        rolling_72h_range_p75_pct=16.0,
        rolling_72h_range_p90_pct=20.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def plan(target_1=103.0, target_2=106.0) -> EntryExitPlan:
    return EntryExitPlan(
        symbol="SOL/USD", valid_now=True, entry_style="pullback_or_retest",
        entry_low=99.0, entry_high=101.0, chase_limit=102.0, stop_price=97.0,
        target_1=target_1, target_2=target_2, reward_to_risk_1=2.0,
        reward_to_risk_2=3.0, risk_level="low", reason="test",
    )


def test_hourly_history_metrics_are_deterministic():
    highs = [101.0, 103.0, 105.0]
    lows = [99.0, 98.0, 100.0]
    closes = [100.0, 102.0, 104.0]
    high, low, range_pct, average_hourly_pct = _window_metrics(
        highs, lows, closes, 3
    )
    assert high == 105.0
    assert low == 98.0
    assert range_pct == 7 / 104 * 100
    assert average_hourly_pct > 0
    assert _percentage_change(104.0, 100.0) == 4.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    rolling = _rolling_range_percentiles(
        [100.0 + index for index in range(80)],
        [98.0 + index for index in range(80)],
        [99.0 + index for index in range(80)],
        24,
    )
    assert rolling[0] <= rolling[1] <= rolling[2]


def test_upside_windows_are_complete_and_counts_are_exact():
    closes = [100.0 + index * 0.1 for index in range(200)]
    highs = [close + 0.5 for close in closes]
    excursions_24h = _rolling_upside_excursions(highs, closes, 24)
    excursions_72h = _rolling_upside_excursions(highs, closes, 72)
    assert len(excursions_24h) == 176
    assert len(excursions_72h) == 128
    assert excursions_24h[-1] == (
        max(highs[176:200]) - closes[175]
    ) / closes[175] * 100
    assert excursions_72h[-1] == (
        max(highs[128:200]) - closes[127]
    ) / closes[127] * 100


def test_downside_range_does_not_masquerade_as_long_upside():
    closes = [100.0 - index * 0.2 for index in range(80)]
    highs = list(closes)
    lows = [close - 5.0 for close in closes]
    full_range = _rolling_range_percentiles(highs, lows, closes, 24)
    upside = _rolling_upside_percentiles(highs, closes, 24)
    assert full_range[0] > 10.0
    assert upside == (0.0, 0.0, 0.0)

    market = snapshot(
        rolling_24h_range_median_pct=full_range[0],
        rolling_24h_range_p75_pct=full_range[1],
        rolling_24h_range_p90_pct=full_range[2],
        rolling_24h_upside_median_pct=0.1,
        rolling_24h_upside_p75_pct=0.2,
        rolling_24h_upside_p90_pct=0.3,
        rolling_72h_upside_median_pct=0.2,
        rolling_72h_upside_p75_pct=0.3,
        rolling_72h_upside_p90_pct=0.4,
    )
    result = evaluate_target_attainability(
        build_entry_exit_plan(market, "low"), market
    )
    assert result.qualified is False
    assert any("historical p90" in reason for reason in result.rejection_reasons)


def test_genuine_upside_history_has_ordered_larger_percentiles():
    closes = [100.0 + index * 0.2 for index in range(200)]
    highs = [close + 0.5 for close in closes]
    upside_24h = _rolling_upside_percentiles(highs, closes, 24)
    upside_72h = _rolling_upside_percentiles(highs, closes, 72)
    assert upside_24h[0] <= upside_24h[1] <= upside_24h[2]
    assert upside_72h[0] <= upside_72h[1] <= upside_72h[2]
    assert upside_72h[0] > upside_24h[0]


def test_same_full_range_but_genuine_upside_scores_better():
    plan_market = snapshot(recent_24h_high=120.0, recent_72h_high=130.0)
    generated = build_entry_exit_plan(plan_market, "low")
    downside = snapshot(
        recent_24h_high=120.0,
        recent_72h_high=130.0,
        rolling_24h_range_median_pct=10.0,
        rolling_72h_range_median_pct=18.0,
        rolling_24h_upside_median_pct=1.0,
        rolling_24h_upside_p75_pct=2.0,
        rolling_24h_upside_p90_pct=3.0,
        rolling_72h_upside_median_pct=2.0,
        rolling_72h_upside_p75_pct=3.0,
        rolling_72h_upside_p90_pct=4.0,
    )
    upside = snapshot(
        recent_24h_high=120.0,
        recent_72h_high=130.0,
        rolling_24h_range_median_pct=10.0,
        rolling_72h_range_median_pct=18.0,
        rolling_24h_upside_median_pct=8.0,
        rolling_24h_upside_p75_pct=10.0,
        rolling_24h_upside_p90_pct=12.0,
        rolling_72h_upside_median_pct=12.0,
        rolling_72h_upside_p75_pct=16.0,
        rolling_72h_upside_p90_pct=20.0,
    )
    downside_result = evaluate_target_attainability(generated, downside)
    upside_result = evaluate_target_attainability(generated, upside)
    assert downside.realized_range_24h_pct == upside.realized_range_24h_pct
    assert "Current 24h range is 1.60x rolling median" in downside_result.volatility_context
    assert "Current 24h range is 1.60x rolling median" in upside_result.volatility_context
    assert upside_result.attainability_score >= downside_result.attainability_score + 25


def test_chief_payload_labels_hypothetical_economics_and_uses_one_call(monkeypatch):
    class FakeResponses:
        def __init__(self):
            self.calls = 0
            self.input = None

        def create(self, **kwargs):
            self.calls += 1
            self.input = kwargs["input"]
            return SimpleNamespace(output_text=json.dumps({
                "market_view": "",
                "recommended_action": "no_trade",
                "top_candidates": [],
                "summary": "",
            }))

    responses = FakeResponses()
    monkeypatch.setattr(
        chief_analyst,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )
    chief_analyst.review_candidates([snapshot()], "model", "key", 2_000)
    payload = json.loads(responses.input[1]["content"])
    low_quality = payload["candidates"][0][
        "deterministic_quality_by_risk_level"
    ]["low"]
    assert responses.calls == 1
    assert low_quality["economic_assumed_capital"] == 2_000
    assert "hypothetical_target_2_net_profit_at_assumed_capital" in low_quality
    assert "target_2_net_profit" not in low_quality


def test_clean_target_scores_well_and_passes():
    result = evaluate_target_attainability(plan(), snapshot())
    assert result.qualified is True
    assert result.attainability_score >= 85
    assert result.target_2_atr_multiple == 3.0


def test_nearby_resistance_receives_meaningful_penalty_or_rejection():
    clean = evaluate_target_attainability(plan(), snapshot())
    blocked = evaluate_target_attainability(
        plan(), snapshot(recent_24h_high=106.2, recent_72h_high=106.3)
    )
    assert blocked.attainability_score <= clean.attainability_score - 10
    assert blocked.warnings


def test_historically_unrealistic_target_is_rejected():
    generated = build_entry_exit_plan(
        snapshot(
            rolling_24h_upside_median_pct=2.0,
            rolling_24h_upside_p75_pct=2.5,
            rolling_24h_upside_p90_pct=3.0,
            rolling_72h_upside_median_pct=3.0,
            rolling_72h_upside_p75_pct=4.0,
            rolling_72h_upside_p90_pct=5.0,
        ),
        "low",
    )
    result = evaluate_target_attainability(generated, snapshot(
        rolling_24h_upside_median_pct=2.0,
        rolling_24h_upside_p75_pct=2.5,
        rolling_24h_upside_p90_pct=3.0,
        rolling_72h_upside_median_pct=3.0,
        rolling_72h_upside_p75_pct=4.0,
        rolling_72h_upside_p90_pct=5.0,
    ))
    assert result.qualified is False
    assert any("historical p90" in reason for reason in result.rejection_reasons)


def test_recent_range_realism_changes_score():
    normal = evaluate_target_attainability(plan(), snapshot())
    abnormal = evaluate_target_attainability(
        plan(), snapshot(
            rolling_24h_upside_median_pct=2.0,
            rolling_24h_upside_p75_pct=3.0,
            rolling_24h_upside_p90_pct=4.0,
            rolling_72h_upside_median_pct=3.0,
            rolling_72h_upside_p75_pct=4.0,
            rolling_72h_upside_p90_pct=5.0,
        )
    )
    assert normal.attainability_score >= abnormal.attainability_score + 20


def test_positive_momentum_improves_quality_over_conflicting_momentum():
    healthy = evaluate_target_attainability(plan(), snapshot())
    conflicting = evaluate_target_attainability(
        plan(), snapshot(momentum_6h_pct=-1.0, momentum_24h_pct=2.0, momentum_72h_pct=-3.0)
    )
    assert healthy.attainability_score >= conflicting.attainability_score + 15


def test_breakout_credit_requires_deterministic_confirmation():
    confirmed_market = snapshot(
        recent_24h_high=102.0,
        recent_72h_high=105.0,
        volume_ratio=1.5,
    )
    unconfirmed_market = snapshot(
        recent_24h_high=102.0,
        recent_72h_high=105.0,
        volume_ratio=0.7,
    )
    confirmed = evaluate_target_attainability(plan(), confirmed_market)
    unconfirmed = evaluate_target_attainability(plan(), unconfirmed_market)
    assert confirmed.attainability_score > unconfirmed.attainability_score
    assert any("without deterministic breakout confirmation" in warning
               for warning in unconfirmed.warnings)


def test_generated_low_and_medium_plans_have_no_geometry_score_bias():
    market = snapshot(recent_24h_high=120.0, recent_72h_high=130.0)
    low = evaluate_target_attainability(
        build_entry_exit_plan(market, "low"), market
    )
    medium = evaluate_target_attainability(
        build_entry_exit_plan(market, "medium"), market
    )
    assert low.qualified is True
    assert medium.qualified is True
    assert abs(low.attainability_score - medium.attainability_score) <= 5
    assert low.target_2_atr_multiple != medium.target_2_atr_multiple


def test_same_generated_geometry_scores_differ_by_market_evidence():
    supportive = snapshot(recent_24h_high=120.0, recent_72h_high=130.0)
    hostile = snapshot(
        recent_24h_high=104.0,
        recent_72h_high=106.0,
        momentum_6h_pct=-1.0,
        momentum_24h_pct=-2.0,
        momentum_72h_pct=-3.0,
        volume_ratio=0.6,
        realized_range_24h_pct=2.0,
    )
    supportive_plan = build_entry_exit_plan(supportive, "low")
    hostile_plan = build_entry_exit_plan(hostile, "low")
    assert supportive_plan.target_1 == hostile_plan.target_1
    assert supportive_plan.target_2 == hostile_plan.target_2
    good = evaluate_target_attainability(supportive_plan, supportive)
    bad = evaluate_target_attainability(hostile_plan, hostile)
    assert good.attainability_score >= bad.attainability_score + 35


def test_generated_historically_normal_target_passes():
    market = snapshot(recent_24h_high=120.0, recent_72h_high=130.0)
    result = evaluate_target_attainability(
        build_entry_exit_plan(market, "medium"), market
    )
    assert result.qualified is True
    assert result.attainability_score >= 90


def test_below_ema20_behavior_remains_wait_and_invalid():
    result = build_entry_exit_plan(snapshot(last_price=98.0, ema20=100.0), "low")
    assert result.entry_style == "wait"
    assert result.valid_now is False


def test_economic_gate_regression_defaults_unchanged():
    result = evaluate_economic_quality(plan(target_1=105.0, target_2=108.0), 2_000)
    assert result.qualified is True
    assert result.target_2_move_pct == 8.0
    assert result.target_2_net_profit == 148.0


def test_target_quality_failure_suppresses_telegram(monkeypatch):
    weak = snapshot(
        atr=1.0,
        recent_24h_high=103.0,
        recent_72h_high=103.0,
        momentum_6h_pct=-1.0,
        momentum_24h_pct=-2.0,
        momentum_72h_pct=-3.0,
    )
    settings = SimpleNamespace(
        openai_model="model", openai_api_key="key", account_equity=2_000,
        telegram_bot_token="token", telegram_chat_id="chat",
    )
    monkeypatch.setattr(scan_opportunities, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scan_opportunities, "scan_market",
        lambda limit: ScanResult([weak], 1, 1, 0, 0, [], []),
    )
    monkeypatch.setattr(scan_opportunities, "select_candidates", lambda items: items)
    def unavailable_reference(candidates, *args, **kwargs):
        candidates[0].independent_market_reference = SimpleNamespace(
            status="UNAVAILABLE", coingecko_id=None, reference_price_usd=None,
            kraken_normalized_price_usd=None, price_divergence_pct=None,
            age_seconds=None, market_cap_rank=None,
        )
        return SimpleNamespace(
            requested=1, available=0, unavailable=1, ambiguous=0,
            api_mode="KEYLESS",
        )
    monkeypatch.setattr(
        scan_opportunities,
        "validate_finalist_references",
        unavailable_reference,
    )
    monkeypatch.setattr(
        scan_opportunities, "review_candidates",
        lambda *args: {"top_candidates": [{
            "symbol": weak.symbol, "risk_level": "medium", "confidence": 90,
            "decision": "alert",
        }], "summary": "summary"},
    )
    monkeypatch.setattr(
        scan_opportunities, "qualified_alerts", lambda review: review["top_candidates"]
    )
    sent = []
    monkeypatch.setattr(scan_opportunities, "send_trade_plan", lambda **kwargs: sent.append(kwargs))
    monkeypatch.setattr(scan_opportunities, "add_pending_setup", lambda setup: None)

    scan_opportunities.main()
    assert sent == []
