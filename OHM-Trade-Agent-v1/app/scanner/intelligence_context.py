from __future__ import annotations

from dataclasses import asdict, dataclass

from app.scanner.market_regime import AVAILABLE as REGIME_AVAILABLE
from app.scanner.market_regime import NEUTRAL, RISK_OFF, RISK_ON, MarketRegimeContext
from app.scanner.models import MarketSnapshot


@dataclass(frozen=True)
class IntelligenceContext:
    setup_type: str
    regime_alignment: str
    liquidity_tier: str
    catalyst_risk: str
    news_activity: str
    context_score: int
    strengths: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def classify_setup(candidate: MarketSnapshot) -> str:
    """Classify a candidate using only deterministic market structure evidence."""
    direction = candidate.trade_direction.upper()
    if direction == "SHORT":
        near_break = candidate.distance_to_24h_low_pct <= 1.0
        momentum = candidate.momentum_24h_pct < 0 and candidate.momentum_72h_pct < 0
        trend = candidate.trend.lower() == "bearish"
        pullback = candidate.last_price < candidate.ema20 < candidate.ema50
    else:
        near_break = candidate.distance_to_24h_high_pct <= 1.0
        momentum = candidate.momentum_24h_pct > 0 and candidate.momentum_72h_pct > 0
        trend = candidate.trend.lower() == "bullish"
        pullback = candidate.last_price > candidate.ema20 > candidate.ema50

    if near_break and momentum and candidate.volume_ratio >= 1.2:
        return "BREAKOUT_CONTINUATION"
    if pullback and trend and candidate.volume_ratio >= 0.8:
        return "TREND_PULLBACK"
    if trend and momentum:
        return "MOMENTUM_CONTINUATION"
    return "MIXED_STRUCTURE"


def _regime_alignment(candidate: MarketSnapshot, regime: MarketRegimeContext | None) -> str:
    if regime is None or regime.status != REGIME_AVAILABLE:
        return "UNAVAILABLE"
    if regime.regime == NEUTRAL:
        return "NEUTRAL"
    direction = candidate.trade_direction.upper()
    aligned = (direction == "LONG" and regime.regime == RISK_ON) or (
        direction == "SHORT" and regime.regime == RISK_OFF
    )
    return "ALIGNED" if aligned else "OPPOSED"


def _liquidity_tier(candidate: MarketSnapshot) -> str:
    liquidity = max(0.0, float(candidate.combined_24h_liquidity_usd or 0.0))
    if liquidity >= 50_000_000:
        return "DEEP"
    if liquidity >= 10_000_000:
        return "GOOD"
    if liquidity >= 2_000_000:
        return "THIN"
    return "VERY_THIN"


def _catalyst_risk(candidate: MarketSnapshot) -> str:
    context = candidate.scheduled_catalyst_context
    if context is None or context.status in {"UNAVAILABLE", "UNRESOLVED"}:
        return "UNKNOWN"
    event = context.nearest_event
    if event is None or event.time_to_event_seconds is None:
        return "NONE_KNOWN"
    hours = event.time_to_event_seconds / 3600.0
    if hours <= 6:
        return "IMMINENT"
    if hours <= 24:
        return "NEAR_TERM"
    return "UPCOMING"


def _news_activity(candidate: MarketSnapshot) -> str:
    context = candidate.news_context
    if context is None or context.status != "AVAILABLE":
        return "UNKNOWN"
    if context.recent_post_count_6h >= 3:
        return "ELEVATED"
    if context.recent_post_count_24h >= 1:
        return "ACTIVE"
    return "QUIET"


def build_intelligence_context(
    candidate: MarketSnapshot,
    market_regime: MarketRegimeContext | None = None,
) -> IntelligenceContext:
    """Combine Wave 3 context without converting it into win probability.

    The score is a deterministic review aid only. It must not bypass target,
    economic, portfolio, execution, or drawdown gates.
    """
    setup = classify_setup(candidate)
    alignment = _regime_alignment(candidate, market_regime)
    liquidity = _liquidity_tier(candidate)
    catalyst = _catalyst_risk(candidate)
    news = _news_activity(candidate)

    score = 50
    strengths: list[str] = []
    warnings: list[str] = []

    if setup in {"BREAKOUT_CONTINUATION", "TREND_PULLBACK", "MOMENTUM_CONTINUATION"}:
        score += 10
        strengths.append(f"coherent setup: {setup.lower()}")
    else:
        score -= 10
        warnings.append("mixed market structure")

    if alignment == "ALIGNED":
        score += 10
        strengths.append("candidate direction aligns with broad regime")
    elif alignment == "OPPOSED":
        score -= 15
        warnings.append("candidate direction opposes broad regime")

    if liquidity == "DEEP":
        score += 10
        strengths.append("deep observed 24h liquidity")
    elif liquidity == "GOOD":
        score += 5
    elif liquidity == "THIN":
        score -= 5
        warnings.append("thin observed 24h liquidity")
    else:
        score -= 15
        warnings.append("very thin observed 24h liquidity")

    if catalyst == "IMMINENT":
        score -= 15
        warnings.append("scheduled catalyst inside 6 hours")
    elif catalyst == "NEAR_TERM":
        score -= 8
        warnings.append("scheduled catalyst inside 24 hours")
    elif catalyst == "UNKNOWN":
        warnings.append("scheduled catalyst evidence unavailable")

    if news == "ELEVATED":
        score -= 5
        warnings.append("elevated recent news activity")
    elif news == "UNKNOWN":
        warnings.append("news activity evidence unavailable")

    return IntelligenceContext(
        setup_type=setup,
        regime_alignment=alignment,
        liquidity_tier=liquidity,
        catalyst_risk=catalyst,
        news_activity=news,
        context_score=max(0, min(100, score)),
        strengths=tuple(strengths),
        warnings=tuple(warnings),
    )
