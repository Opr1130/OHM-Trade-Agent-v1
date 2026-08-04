from app.models.signal import TradingSignal
from app.services.risk import build_risk_plan
from app.services.scoring import score_signal


def sample_signal() -> TradingSignal:
    return TradingSignal(
        symbol="BTCUSD",
        asset_class="crypto",
        timeframe="4h",
        side="long",
        price=100,
        stop_price=95,
        target_price=112,
        rsi=60,
        volume_ratio=1.7,
        ema_fast=101,
        ema_slow=98,
        breakout=True,
        market_regime="bullish",
    )


def test_strong_signal_scores_high() -> None:
    score, reasons = score_signal(sample_signal())
    assert score == 100
    assert len(reasons) >= 4


def test_risk_plan_requires_two_to_one() -> None:
    plan = build_risk_plan(sample_signal(), equity=10_000, risk_pct=0.35)
    assert plan.allowed is True
    assert plan.risk_dollars == 35
    assert plan.reward_to_risk == 2.4
