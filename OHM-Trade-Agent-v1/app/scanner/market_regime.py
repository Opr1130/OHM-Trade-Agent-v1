from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from app.scanner.models import MarketSnapshot


AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
RISK_ON = "RISK_ON"
NEUTRAL = "NEUTRAL"
RISK_OFF = "RISK_OFF"

# Breadth from fewer assets is too sensitive to individual markets to represent
# the broad Kraken universe. This is informational context, not a trading gate.
MIN_REGIME_SAMPLE_SIZE = 30

# Each condition contributes one-sixth of the breadth score. These v1 levels
# express whether a simple majority of the validated universe is supportive.
SUPPORT_ABOVE_EMA20_PCT = 55.0
SUPPORT_ABOVE_EMA50_PCT = 55.0
SUPPORT_ABOVE_EMA200_PCT = 50.0
SUPPORT_POSITIVE_MOMENTUM_24H_PCT = 55.0
SUPPORT_POSITIVE_MOMENTUM_72H_PCT = 55.0
SUPPORT_BULLISH_TREND_PCT = 50.0
RISK_ON_BREADTH_SCORE = 67.0
RISK_OFF_BREADTH_SCORE = 33.0


@dataclass(frozen=True)
class MarketRegimeContext:
    status: str
    sample_size: int
    regime: str
    breadth_score: float | None
    pct_above_ema20: float | None
    pct_above_ema50: float | None
    pct_above_ema200: float | None
    pct_positive_momentum_24h: float | None
    pct_positive_momentum_72h: float | None
    pct_bullish_trend: float | None
    median_momentum_24h_pct: float | None
    median_momentum_72h_pct: float | None
    warnings: tuple[str, ...] = ()


def _percentage(count: int, total: int) -> float:
    return count / total * 100.0


def evaluate_market_regime(
    snapshots: list[MarketSnapshot],
) -> MarketRegimeContext:
    """Build deterministic breadth from all validated Kraken snapshots."""
    sample_size = len(snapshots)
    if sample_size < MIN_REGIME_SAMPLE_SIZE:
        return MarketRegimeContext(
            status=UNAVAILABLE,
            sample_size=sample_size,
            regime=UNAVAILABLE,
            breadth_score=None,
            pct_above_ema20=None,
            pct_above_ema50=None,
            pct_above_ema200=None,
            pct_positive_momentum_24h=None,
            pct_positive_momentum_72h=None,
            pct_bullish_trend=None,
            median_momentum_24h_pct=None,
            median_momentum_72h_pct=None,
            warnings=(
                f"Market regime requires at least {MIN_REGIME_SAMPLE_SIZE} "
                "validated assets",
            ),
        )

    pct_above_ema20 = _percentage(
        sum(item.last_price > item.ema20 for item in snapshots), sample_size
    )
    pct_above_ema50 = _percentage(
        sum(item.last_price > item.ema50 for item in snapshots), sample_size
    )
    pct_above_ema200 = _percentage(
        sum(item.last_price > item.ema200 for item in snapshots), sample_size
    )
    pct_positive_24h = _percentage(
        sum(item.momentum_24h_pct > 0 for item in snapshots), sample_size
    )
    pct_positive_72h = _percentage(
        sum(item.momentum_72h_pct > 0 for item in snapshots), sample_size
    )
    pct_bullish = _percentage(
        sum(item.trend.lower() == "bullish" for item in snapshots), sample_size
    )

    supportive_conditions = (
        pct_above_ema20 >= SUPPORT_ABOVE_EMA20_PCT,
        pct_above_ema50 >= SUPPORT_ABOVE_EMA50_PCT,
        pct_above_ema200 >= SUPPORT_ABOVE_EMA200_PCT,
        pct_positive_24h >= SUPPORT_POSITIVE_MOMENTUM_24H_PCT,
        pct_positive_72h >= SUPPORT_POSITIVE_MOMENTUM_72H_PCT,
        pct_bullish >= SUPPORT_BULLISH_TREND_PCT,
    )
    breadth_score = sum(supportive_conditions) / len(supportive_conditions) * 100
    classification_score = round(breadth_score)
    if classification_score >= RISK_ON_BREADTH_SCORE:
        regime = RISK_ON
    elif classification_score <= RISK_OFF_BREADTH_SCORE:
        regime = RISK_OFF
    else:
        regime = NEUTRAL

    return MarketRegimeContext(
        status=AVAILABLE,
        sample_size=sample_size,
        regime=regime,
        breadth_score=round(breadth_score, 1),
        pct_above_ema20=round(pct_above_ema20, 1),
        pct_above_ema50=round(pct_above_ema50, 1),
        pct_above_ema200=round(pct_above_ema200, 1),
        pct_positive_momentum_24h=round(pct_positive_24h, 1),
        pct_positive_momentum_72h=round(pct_positive_72h, 1),
        pct_bullish_trend=round(pct_bullish, 1),
        median_momentum_24h_pct=round(
            float(median(item.momentum_24h_pct for item in snapshots)), 3
        ),
        median_momentum_72h_pct=round(
            float(median(item.momentum_72h_pct for item in snapshots)), 3
        ),
    )
