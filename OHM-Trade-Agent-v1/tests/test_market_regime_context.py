from app.scanner.market_regime import (
    NEUTRAL,
    RISK_OFF,
    RISK_ON,
    UNAVAILABLE,
    evaluate_market_regime,
)
from app.scanner.models import MarketSnapshot


def snapshot(
    index: int,
    *,
    above_emas: bool,
    momentum_positive: bool,
    bullish: bool,
) -> MarketSnapshot:
    price = 100.0 + index
    ema = price - 1 if above_emas else price + 1
    momentum = 2.0 if momentum_positive else -2.0
    return MarketSnapshot(
        symbol=f"A{index}USD",
        last_price=price,
        ema20=ema,
        ema50=ema,
        ema200=ema,
        rsi=50,
        macd_line=0,
        macd_signal=0,
        macd_histogram=0,
        atr=1,
        atr_pct=1,
        volume_ratio=1,
        technical_score=50,
        trend="bullish" if bullish else "neutral",
        momentum_24h_pct=momentum,
        momentum_72h_pct=momentum,
    )


def test_risk_on_breadth():
    result = evaluate_market_regime([
        snapshot(index, above_emas=True, momentum_positive=True, bullish=True)
        for index in range(30)
    ])
    assert result.regime == RISK_ON
    assert result.breadth_score == 100.0
    assert result.sample_size == 30


def test_neutral_breadth():
    result = evaluate_market_regime([
        snapshot(index, above_emas=True, momentum_positive=False, bullish=False)
        for index in range(30)
    ])
    assert result.regime == NEUTRAL
    assert result.breadth_score == 50.0


def test_risk_off_breadth():
    result = evaluate_market_regime([
        snapshot(index, above_emas=False, momentum_positive=False, bullish=False)
        for index in range(30)
    ])
    assert result.regime == RISK_OFF
    assert result.breadth_score == 0.0


def test_four_of_six_conditions_reaches_rounded_risk_on_boundary():
    result = evaluate_market_regime([
        snapshot(index, above_emas=True, momentum_positive=False, bullish=True)
        for index in range(30)
    ])
    assert result.breadth_score == 66.7
    assert result.regime == RISK_ON


def test_two_of_six_conditions_reaches_rounded_risk_off_boundary():
    result = evaluate_market_regime([
        snapshot(index, above_emas=False, momentum_positive=True, bullish=False)
        for index in range(30)
    ])
    assert result.breadth_score == 33.3
    assert result.regime == RISK_OFF


def test_insufficient_sample_is_unavailable():
    result = evaluate_market_regime([
        snapshot(index, above_emas=True, momentum_positive=True, bullish=True)
        for index in range(29)
    ])
    assert result.status == UNAVAILABLE
    assert result.regime == UNAVAILABLE
    assert result.breadth_score is None


def test_regime_uses_full_validated_universe_not_eight_finalists():
    finalists = [
        snapshot(index, above_emas=False, momentum_positive=False, bullish=False)
        for index in range(8)
    ]
    remainder = [
        snapshot(index, above_emas=True, momentum_positive=True, bullish=True)
        for index in range(8, 40)
    ]
    result = evaluate_market_regime(finalists + remainder)
    assert result.sample_size == 40
    assert result.pct_above_ema20 == 80.0
    assert result.regime == RISK_ON
