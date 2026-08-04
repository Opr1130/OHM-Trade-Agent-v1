from app.models.signal import TradingSignal


def score_signal(signal: TradingSignal) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    trend_ok = signal.ema_fast > signal.ema_slow if signal.side == "long" else signal.ema_fast < signal.ema_slow
    if trend_ok:
        score += 30
        reasons.append("EMA trend aligned")

    momentum_ok = 50 <= signal.rsi <= 68 if signal.side == "long" else 32 <= signal.rsi <= 50
    if momentum_ok:
        score += 20
        reasons.append("RSI momentum healthy")

    if signal.volume_ratio >= 1.5:
        score += 20
        reasons.append("Volume confirmation")
    elif signal.volume_ratio >= 1.1:
        score += 10
        reasons.append("Moderate volume support")

    if signal.breakout:
        score += 15
        reasons.append("Breakout confirmed")

    favorable_regime = (
        signal.market_regime == "bullish" and signal.side == "long"
    ) or (
        signal.market_regime == "bearish" and signal.side == "short"
    )
    if favorable_regime:
        score += 15
        reasons.append("Market regime aligned")
    elif signal.market_regime == "neutral":
        score += 5

    return min(score, 100), reasons
