from app.scanner.intelligence_ranking import rank_candidates_with_context
from app.scanner.models import MarketSnapshot
from app.services.market_intelligence import MarketIntelligenceAssessment


def _candidate(symbol: str, technical: int) -> MarketSnapshot:
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
        trend="bullish",
        momentum_24h_pct=3,
        momentum_72h_pct=5,
        distance_to_24h_high_pct=0.5,
        distance_to_24h_low_pct=5,
        combined_24h_liquidity_usd=100_000_000,
        trade_direction="LONG",
    )


def _assessment(score: int, status: str = "AVAILABLE") -> MarketIntelligenceAssessment:
    return MarketIntelligenceAssessment(
        status=status,
        context_score=score,
        derivatives_bias="BALANCED",
        volatility_regime="NORMAL",
        macro_regime="NEUTRAL",
        flow_bias="BALANCED",
        sentiment_bias="BALANCED",
        strengths=(),
        warnings=(),
    )


def test_external_market_evidence_is_bounded_to_ten_points():
    candidate = _candidate("BTCUSD", 80)
    base = rank_candidates_with_context([candidate])[0]
    boosted = rank_candidates_with_context(
        [candidate],
        external_market_intelligence={"BTCUSD": _assessment(100)},
    )[0]
    assert boosted.intelligence_score - base.intelligence_score == 10
    assert boosted.external_market_score == 100


def test_unavailable_external_market_evidence_does_not_move_rank_score():
    candidate = _candidate("BTCUSD", 80)
    base = rank_candidates_with_context([candidate])[0]
    unavailable = rank_candidates_with_context(
        [candidate],
        external_market_intelligence={"BTCUSD": _assessment(0, status="UNAVAILABLE")},
    )[0]
    assert unavailable.intelligence_score == base.intelligence_score
