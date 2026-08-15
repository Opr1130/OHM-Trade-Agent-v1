from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean

from app.services.market_intelligence_models import (
    AVAILABLE,
    DerivativesSnapshot,
    MarketIntelligenceBundle,
)


@dataclass(frozen=True)
class MarketIntelligenceAssessment:
    status: str
    context_score: int
    derivatives_bias: str
    volatility_regime: str
    macro_regime: str
    flow_bias: str
    sentiment_bias: str
    strengths: tuple[str, ...]
    warnings: tuple[str, ...]


def _avg(values: list[float]) -> float | None:
    return mean(values) if values else None


def _available_derivatives(bundle: MarketIntelligenceBundle) -> list[DerivativesSnapshot]:
    return [item for item in bundle.derivatives if item.status == AVAILABLE]


def assess_market_intelligence(
    bundle: MarketIntelligenceBundle,
    *,
    direction: str,
    now: datetime | None = None,
    stale_after_seconds: int = 900,
) -> MarketIntelligenceAssessment:
    """Score normalized external evidence without turning it into probability.

    Missing providers are warnings, never fabricated neutral observations. The
    result is contextual evidence only and cannot override economic, portfolio,
    execution, drawdown, or human-approval gates.
    """
    now = now or datetime.now(timezone.utc)
    direction = direction.upper()
    score = 50
    strengths: list[str] = []
    warnings: list[str] = []

    derivatives = _available_derivatives(bundle)
    funding = _avg([d.funding_rate_pct for d in derivatives if d.funding_rate_pct is not None])
    oi_change = _avg([
        d.open_interest_change_24h_pct
        for d in derivatives
        if d.open_interest_change_24h_pct is not None
    ])

    derivatives_bias = "UNKNOWN"
    if derivatives:
        if funding is not None and oi_change is not None:
            crowded_long = funding >= 0.03 and oi_change >= 5.0
            crowded_short = funding <= -0.03 and oi_change >= 5.0
            if crowded_long:
                derivatives_bias = "CROWDED_LONG"
                if direction == "LONG":
                    score -= 10
                    warnings.append("derivatives positioning is crowded long")
                else:
                    score += 5
                    strengths.append("crowded-long positioning supports short caution thesis")
            elif crowded_short:
                derivatives_bias = "CROWDED_SHORT"
                if direction == "SHORT":
                    score -= 10
                    warnings.append("derivatives positioning is crowded short")
                else:
                    score += 5
                    strengths.append("crowded-short positioning supports long squeeze potential")
            elif oi_change >= 5.0:
                derivatives_bias = "RISING_OI"
                warnings.append("open interest is expanding without clear crowding edge")
            else:
                derivatives_bias = "BALANCED"
        else:
            derivatives_bias = "PARTIAL"
            warnings.append("derivatives evidence is incomplete")
    else:
        warnings.append("derivatives evidence unavailable")

    volatility_regime = "UNKNOWN"
    vol_values = [
        v.implied_volatility_index
        for v in bundle.volatility
        if v.status == AVAILABLE and v.implied_volatility_index is not None
    ]
    if vol_values:
        iv = mean(vol_values)
        if iv >= 80:
            volatility_regime = "EXTREME"
            score -= 10
            warnings.append("implied volatility regime is extreme")
        elif iv >= 60:
            volatility_regime = "HIGH"
            score -= 5
            warnings.append("implied volatility regime is high")
        elif iv <= 35:
            volatility_regime = "LOW"
            strengths.append("implied volatility regime is relatively calm")
        else:
            volatility_regime = "NORMAL"
    else:
        warnings.append("implied volatility evidence unavailable")

    macro_regime = "UNKNOWN"
    if bundle.macro and bundle.macro.status == AVAILABLE:
        signal = (bundle.macro.liquidity_signal or "").upper()
        if signal in {"RISK_ON", "EXPANDING"}:
            macro_regime = "SUPPORTIVE"
            if direction == "LONG":
                score += 5
                strengths.append("macro liquidity context supports risk assets")
            else:
                score -= 5
                warnings.append("macro liquidity context opposes short direction")
        elif signal in {"RISK_OFF", "TIGHTENING"}:
            macro_regime = "DEFENSIVE"
            if direction == "SHORT":
                score += 5
                strengths.append("macro liquidity context supports defensive positioning")
            else:
                score -= 5
                warnings.append("macro liquidity context opposes long direction")
        else:
            macro_regime = "NEUTRAL"
    else:
        warnings.append("macro evidence unavailable")

    flow_bias = "UNKNOWN"
    available_flows = [f for f in bundle.flows if f.status == AVAILABLE]
    if available_flows:
        inflows = sum(float(f.exchange_inflow_usd_1h or 0.0) for f in available_flows)
        outflows = sum(float(f.exchange_outflow_usd_1h or 0.0) for f in available_flows)
        if inflows > outflows * 1.5 and inflows > 0:
            flow_bias = "EXCHANGE_INFLOW"
            if direction == "LONG":
                score -= 5
                warnings.append("large exchange inflows may increase sell-side supply")
        elif outflows > inflows * 1.5 and outflows > 0:
            flow_bias = "EXCHANGE_OUTFLOW"
            if direction == "LONG":
                score += 3
                strengths.append("exchange outflows reduce immediate sell-side supply context")
        else:
            flow_bias = "BALANCED"
    else:
        warnings.append("whale/on-chain flow evidence unavailable")

    sentiment_bias = "UNKNOWN"
    sentiment_values = [
        s.sentiment_score
        for s in bundle.sentiment
        if s.status == AVAILABLE and s.sentiment_score is not None
    ]
    if sentiment_values:
        sentiment = mean(sentiment_values)
        if sentiment >= 75:
            sentiment_bias = "EUPHORIC"
            score -= 5
            warnings.append("social sentiment is euphoric")
        elif sentiment <= 25:
            sentiment_bias = "FEARFUL"
            warnings.append("social sentiment is fearful")
        else:
            sentiment_bias = "BALANCED"
    else:
        warnings.append("sentiment evidence unavailable")

    observed = [
        item.observed_at
        for group in (bundle.derivatives, bundle.volatility, bundle.flows, bundle.sentiment)
        for item in group
        if item.status == AVAILABLE
    ]
    if bundle.macro and bundle.macro.status == AVAILABLE:
        observed.append(bundle.macro.observed_at)
    stale = [ts for ts in observed if (now - ts).total_seconds() > stale_after_seconds]
    if stale:
        warnings.append("one or more market-intelligence observations are stale")
        score -= 5

    status = "AVAILABLE" if observed else "UNAVAILABLE"
    return MarketIntelligenceAssessment(
        status=status,
        context_score=max(0, min(100, score)),
        derivatives_bias=derivatives_bias,
        volatility_regime=volatility_regime,
        macro_regime=macro_regime,
        flow_bias=flow_bias,
        sentiment_bias=sentiment_bias,
        strengths=tuple(strengths),
        warnings=tuple(warnings),
    )
