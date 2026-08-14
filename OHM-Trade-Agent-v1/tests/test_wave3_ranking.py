from app.scanner.intelligence_ranking import rank_candidates_with_context
from app.scanner.market_regime import AVAILABLE, MarketRegimeContext, RISK_ON
from app.scanner.models import MarketSnapshot


def _candidate(symbol: str, *, technical: int, liquidity: float, direction: str = "LONG") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        last_price=105,
        ema20=100,
        ema50=95,
        ema200=90,
        rsi=60,
        macd_line=2,
        macd_signal=1,
        macd_histogram=1,
        atr=2,
        atr_pct=2,
        volume_ratio=1.3,
        technical_score=technical,
        trend="bullish" if direction == "LONG" else "bearish",
        momentum_24h_pct=3 if direction == "LONG" else -3,
        momentum_72h_pct=5 if direction == "LONG" else -5,
        distance_to_24h_high_pct=0.5,
        distance_to_24h_low_pct=0.5,
        combined_24h_liquidity_usd=liquidity,
        trade_direction=direction,
    )


def _risk_on() -> MarketRegimeContext:
    return MarketRegimeContext(
        status=AVAILABLE,
        sample_size=50,
        regime=RISK_ON,
        breadth_score=83.3,
        pct_above_ema20=70,
        pct_above_ema50=68,
        pct_above_ema200=60,
        pct_positive_momentum_24h=72,
        pct_positive_momentum_72h=70,
        pct_bullish_trend=65,
        median_momentum_24h_pct=2,
        median_momentum_72h_pct=4,
    )


def test_context_ranking_prefers_aligned_liquid_candidate():
    aligned = _candidate("BTCUSD", technical=80, liquidity=100_000_000)
    opposed = _candidate("ETHUSD", technical=90, liquidity=100_000_000, direction="SHORT")
    ranked = rank_candidates_with_context([opposed, aligned], _risk_on())
    assert ranked[0].candidate.symbol == "BTCUSD"
    assert ranked[0].regime_alignment == "ALIGNED"


def test_context_ranking_never_changes_candidate_direction():
    short = _candidate("ETHUSD", technical=85, liquidity=20_000_000, direction="SHORT")
    ranked = rank_candidates_with_context([short], _risk_on())
    assert ranked[0].candidate.trade_direction == "SHORT"
