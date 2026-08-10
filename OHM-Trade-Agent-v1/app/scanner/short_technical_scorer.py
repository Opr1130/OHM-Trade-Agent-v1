from app.scanner.models import MarketSnapshot, ScoreCard


def score_short_snapshot(snapshot: MarketSnapshot) -> ScoreCard:
    """Independent bearish continuation score; not an inversion of long score."""
    score = 0
    strengths: list[str] = []
    weaknesses: list[str] = []
    warnings: list[str] = []

    if snapshot.last_price < snapshot.ema20:
        score += 10
        strengths.append("Price below EMA20")
    else:
        weaknesses.append("Price not below EMA20")

    if snapshot.ema20 < snapshot.ema50:
        score += 15
        strengths.append("EMA20 below EMA50")
    else:
        weaknesses.append("EMA20 not below EMA50")

    if snapshot.ema50 < snapshot.ema200:
        score += 15
        strengths.append("EMA50 below EMA200")
    else:
        weaknesses.append("EMA50 not below EMA200")

    if 35 <= snapshot.rsi <= 55:
        score += 15
        strengths.append("RSI in healthy bearish momentum range")
    elif 28 <= snapshot.rsi < 35:
        score += 8
        warnings.append("RSI bearish but approaching oversold")
    elif 55 < snapshot.rsi <= 65:
        score += 6
        warnings.append("RSI only mildly bearish")
    elif snapshot.rsi < 28:
        weaknesses.append("RSI too oversold to chase safely")
    else:
        weaknesses.append("RSI does not support bearish continuation")

    if snapshot.macd_line < snapshot.macd_signal:
        score += 5
        strengths.append("MACD bearish confirmation")
    else:
        weaknesses.append("MACD bearish confirmation absent")

    if snapshot.volume_ratio >= 1.5:
        score += 20
        strengths.append("Strong volume expansion")
    elif snapshot.volume_ratio >= 1.1:
        score += 14
        strengths.append("Volume above average")
    elif snapshot.volume_ratio >= 0.8:
        score += 7
        warnings.append("Volume near average")
    else:
        weaknesses.append("Volume below average")

    if 0.5 <= snapshot.atr_pct <= 3.0:
        score += 10
        strengths.append("ATR in tradable range")
    elif 0.25 <= snapshot.atr_pct < 0.5:
        score += 5
        warnings.append("ATR slightly low")
    else:
        weaknesses.append("ATR outside preferred range")

    if snapshot.trend == "bearish":
        score += 10
        strengths.append("Bearish trend")
    elif snapshot.trend == "neutral":
        score += 4
        warnings.append("Neutral trend")
    else:
        weaknesses.append("Bullish trend")

    return ScoreCard(
        score=min(score, 100),
        strengths=strengths,
        weaknesses=weaknesses,
        warnings=warnings,
    )
