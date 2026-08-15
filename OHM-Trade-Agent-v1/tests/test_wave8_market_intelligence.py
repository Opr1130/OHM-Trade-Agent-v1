from datetime import datetime, timedelta, timezone

from app.services.market_intelligence import assess_market_intelligence
from app.services.market_intelligence_models import (
    DerivativesSnapshot,
    FlowSnapshot,
    MacroSnapshot,
    MarketIntelligenceBundle,
    SentimentSnapshot,
    VolatilitySnapshot,
)
from app.services.market_intelligence_provider import collect_market_intelligence


def test_missing_external_feeds_are_warnings_not_fake_zeroes():
    result = assess_market_intelligence(
        MarketIntelligenceBundle(symbol="BTCUSD"),
        direction="LONG",
    )
    assert result.status == "UNAVAILABLE"
    assert result.context_score == 50
    assert "derivatives evidence unavailable" in result.warnings
    assert "macro evidence unavailable" in result.warnings


def test_crowded_long_high_volatility_penalizes_long_context():
    now = datetime.now(timezone.utc)
    bundle = MarketIntelligenceBundle(
        symbol="BTCUSD",
        derivatives=(
            DerivativesSnapshot(
                symbol="BTCUSD",
                provider="coinalyze",
                observed_at=now,
                funding_rate_pct=0.05,
                open_interest_change_24h_pct=8.0,
            ),
        ),
        volatility=(
            VolatilitySnapshot(
                asset="BTC",
                provider="deribit",
                observed_at=now,
                implied_volatility_index=85.0,
            ),
        ),
    )
    result = assess_market_intelligence(bundle, direction="LONG", now=now)
    assert result.derivatives_bias == "CROWDED_LONG"
    assert result.volatility_regime == "EXTREME"
    assert result.context_score < 50


def test_supportive_macro_and_exchange_outflow_can_add_long_context():
    now = datetime.now(timezone.utc)
    bundle = MarketIntelligenceBundle(
        symbol="ETHUSD",
        macro=MacroSnapshot(
            provider="fred",
            observed_at=now,
            liquidity_signal="RISK_ON",
        ),
        flows=(
            FlowSnapshot(
                asset="ETH",
                provider="whale_alert",
                observed_at=now,
                exchange_inflow_usd_1h=1_000_000,
                exchange_outflow_usd_1h=3_000_000,
            ),
        ),
        sentiment=(
            SentimentSnapshot(
                asset="ETH",
                provider="lunarcrush",
                observed_at=now,
                sentiment_score=55,
            ),
        ),
    )
    result = assess_market_intelligence(bundle, direction="LONG", now=now)
    assert result.macro_regime == "SUPPORTIVE"
    assert result.flow_bias == "EXCHANGE_OUTFLOW"
    assert result.context_score > 50


def test_stale_evidence_is_explicitly_penalized():
    now = datetime.now(timezone.utc)
    bundle = MarketIntelligenceBundle(
        symbol="BTCUSD",
        derivatives=(
            DerivativesSnapshot(
                symbol="BTCUSD",
                provider="coinalyze",
                observed_at=now - timedelta(hours=1),
                funding_rate_pct=0.0,
                open_interest_change_24h_pct=0.0,
            ),
        ),
    )
    result = assess_market_intelligence(bundle, direction="LONG", now=now)
    assert "one or more market-intelligence observations are stale" in result.warnings


def test_provider_failure_is_isolated():
    class Good:
        name = "good"

        def fetch(self, symbol):
            return MarketIntelligenceBundle(symbol=symbol)

    class Bad:
        name = "bad"

        def fetch(self, symbol):
            raise RuntimeError("provider down")

    results = collect_market_intelligence("BTCUSD", [Good(), Bad()])
    assert results[0].ok is True
    assert results[1].ok is False
    assert "provider down" in results[1].error
