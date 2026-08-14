from types import SimpleNamespace

from app.scanner.intelligence_context import build_intelligence_context, classify_setup
from app.scanner.market_regime import AVAILABLE, MarketRegimeContext, RISK_OFF, RISK_ON
from app.scanner.models import MarketSnapshot


def _candidate(direction="LONG", liquidity=60_000_000):
    return MarketSnapshot(
        symbol="BTCUSD",
        last_price=110.0 if direction == "LONG" else 90.0,
        ema20=105.0 if direction == "LONG" else 95.0,
        ema50=100.0 if direction == "LONG" else 100.0,
        ema200=90.0 if direction == "LONG" else 110.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5 if direction == "LONG" else -0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.4,
        technical_score=85,
        trend="bullish" if direction == "LONG" else "bearish",
        momentum_24h_pct=3.0 if direction == "LONG" else -3.0,
        momentum_72h_pct=6.0 if direction == "LONG" else -6.0,
        distance_to_24h_high_pct=0.5 if direction == "LONG" else 8.0,
        distance_to_24h_low_pct=8.0 if direction == "LONG" else 0.5,
        trade_direction=direction,
        combined_24h_liquidity_usd=liquidity,
    )


def _regime(value):
    return MarketRegimeContext(
        status=AVAILABLE,
        sample_size=50,
        regime=value,
        breadth_score=83.3,
        pct_above_ema20=70.0,
        pct_above_ema50=68.0,
        pct_above_ema200=65.0,
        pct_positive_momentum_24h=70.0,
        pct_positive_momentum_72h=68.0,
        pct_bullish_trend=65.0,
        median_momentum_24h_pct=2.0,
        median_momentum_72h_pct=4.0,
    )


def test_classify_breakout_continuation_long_and_short():
    assert classify_setup(_candidate("LONG")) == "BREAKOUT_CONTINUATION"
    assert classify_setup(_candidate("SHORT")) == "BREAKOUT_CONTINUATION"


def test_context_rewards_regime_alignment_and_liquidity():
    context = build_intelligence_context(_candidate("LONG"), _regime(RISK_ON))
    assert context.regime_alignment == "ALIGNED"
    assert context.liquidity_tier == "DEEP"
    assert context.context_score == 80


def test_context_penalizes_opposed_regime_and_thin_liquidity():
    context = build_intelligence_context(_candidate("LONG", liquidity=1_000_000), _regime(RISK_OFF))
    assert context.regime_alignment == "OPPOSED"
    assert context.liquidity_tier == "VERY_THIN"
    assert context.context_score == 30


def test_imminent_catalyst_is_caution_not_automatic_trade_signal():
    candidate = _candidate("LONG")
    candidate.scheduled_catalyst_context = SimpleNamespace(
        status="AVAILABLE",
        nearest_event=SimpleNamespace(time_to_event_seconds=2 * 3600),
    )
    context = build_intelligence_context(candidate, _regime(RISK_ON))
    assert context.catalyst_risk == "IMMINENT"
    assert context.context_score == 65
    assert any("scheduled catalyst" in item for item in context.warnings)


def test_missing_news_and_catalysts_warn_without_fabricating_evidence():
    context = build_intelligence_context(_candidate("SHORT"), _regime(RISK_OFF))
    assert context.news_activity == "UNKNOWN"
    assert context.catalyst_risk == "UNKNOWN"
    assert any("news activity evidence unavailable" == item for item in context.warnings)
    assert any("scheduled catalyst evidence unavailable" == item for item in context.warnings)
