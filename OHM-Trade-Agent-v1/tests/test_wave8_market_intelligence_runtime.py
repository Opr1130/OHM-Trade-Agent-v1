from datetime import datetime, timezone

from app.services.market_intelligence_models import (
    DerivativesSnapshot,
    MacroSnapshot,
    MarketIntelligenceBundle,
)
from app.services.market_intelligence_runtime import (
    build_configured_market_intelligence_providers,
    build_market_intelligence_assessments,
    build_market_intelligence_evidence,
    merge_market_intelligence_results,
    serialize_market_intelligence_evidence,
)
from app.services.market_intelligence_provider import ProviderResult


def test_runtime_builds_coinalyze_only_when_key_is_present():
    assert build_configured_market_intelligence_providers({}) == ()

    providers = build_configured_market_intelligence_providers(
        {"COINALYZE_API_KEY": "runtime-only-secret"}
    )
    assert len(providers) == 1
    assert providers[0].name == "coinalyze"


def test_merge_preserves_independent_provider_evidence():
    now = datetime.now(timezone.utc)
    results = (
        ProviderResult(
            provider="derivatives",
            ok=True,
            bundle=MarketIntelligenceBundle(
                symbol="BTCUSD",
                derivatives=(
                    DerivativesSnapshot(
                        symbol="BTCUSDT_PERP.A",
                        provider="coinalyze",
                        observed_at=now,
                        funding_rate_pct=0.02,
                    ),
                ),
            ),
        ),
        ProviderResult(
            provider="macro",
            ok=True,
            bundle=MarketIntelligenceBundle(
                symbol="BTCUSD",
                macro=MacroSnapshot(
                    provider="fred",
                    observed_at=now,
                    liquidity_signal="RISK_ON",
                ),
            ),
        ),
    )

    merged = merge_market_intelligence_results("BTCUSD", results)
    assert len(merged.derivatives) == 1
    assert merged.macro is not None
    assert merged.macro.provider == "fred"


def test_runtime_caps_enrichment_to_finalist_budget():
    class FakeProvider:
        name = "fake"

        def __init__(self):
            self.seen = []

        def fetch(self, symbol):
            self.seen.append(symbol)
            return MarketIntelligenceBundle(
                symbol=symbol,
                derivatives=(
                    DerivativesSnapshot(
                        symbol=symbol,
                        provider=self.name,
                        funding_rate_pct=0.0,
                        open_interest_change_24h_pct=0.0,
                    ),
                ),
            )

    provider = FakeProvider()
    candidates = [(f"COIN{i}USD", "LONG") for i in range(20)]

    assessments = build_market_intelligence_assessments(
        candidates,
        providers=(provider,),
        max_candidates=8,
    )

    assert len(assessments) == 8
    assert len(provider.seen) == 8
    assert provider.seen == [f"COIN{i}USD" for i in range(8)]


def test_runtime_retains_json_safe_evidence_without_provider_credentials():
    now = datetime.now(timezone.utc)

    class FakeProvider:
        name = "fake"
        api_key = "must-never-be-serialized"

        def fetch(self, symbol):
            return MarketIntelligenceBundle(
                symbol=symbol,
                derivatives=(
                    DerivativesSnapshot(
                        symbol="BTCUSDT_PERP.A",
                        provider=self.name,
                        observed_at=now,
                        funding_rate_pct=0.01,
                        open_interest_usd=123_000_000,
                        open_interest_change_24h_pct=2.5,
                    ),
                ),
            )

    evidence = build_market_intelligence_evidence(
        [("BTCUSD", "LONG")],
        providers=(FakeProvider(),),
    )["BTCUSD"]
    serialized = serialize_market_intelligence_evidence(evidence)

    assert serialized["direction"] == "LONG"
    assert serialized["assessment"]["status"] == "AVAILABLE"
    assert serialized["bundle"]["derivatives"][0]["open_interest_usd"] == 123_000_000
    assert serialized["bundle"]["derivatives"][0]["observed_at"] == now.isoformat()
    assert serialized["authority"] == "context_only_no_gate_override"
    assert serialized["context_score_is_probability"] is False
    assert "must-never-be-serialized" not in repr(serialized)


def test_runtime_sanitizes_provider_failure_text_before_persistence():
    class BrokenProvider:
        name = "broken"

        def fetch(self, symbol):
            raise RuntimeError("secret-like-value-must-not-persist")

    evidence = build_market_intelligence_evidence(
        [("BTCUSD", "LONG")],
        providers=(BrokenProvider(),),
    )["BTCUSD"]
    serialized = serialize_market_intelligence_evidence(evidence)

    assert evidence.assessment.status == "UNAVAILABLE"
    assert serialized["provider_errors"] == ["broken: provider unavailable"]
    assert "secret-like-value-must-not-persist" not in repr(serialized)


def test_runtime_is_noop_without_configured_providers():
    assessments = build_market_intelligence_assessments(
        [("BTCUSD", "LONG")],
        providers=(),
    )
    assert assessments == {}
