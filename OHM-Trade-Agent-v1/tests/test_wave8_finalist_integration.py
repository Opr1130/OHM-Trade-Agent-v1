from types import SimpleNamespace

from app.scanner.intelligence_ranking import RankedCandidate
from app.scanner.market_regime import MarketRegimeContext
from app.services.market_intelligence import MarketIntelligenceAssessment
from app.services.market_intelligence_integration import (
    enrich_finalist_market_intelligence,
)
from app.services.market_intelligence_models import MarketIntelligenceBundle
from app.services.market_intelligence_runtime import MarketIntelligenceEvidence


def _regime() -> MarketRegimeContext:
    return MarketRegimeContext(
        status="AVAILABLE",
        sample_size=100,
        regime="NEUTRAL",
        breadth_score=50.0,
        pct_above_ema20=52.0,
        pct_above_ema50=51.0,
        pct_above_ema200=49.0,
        pct_positive_momentum_24h=53.0,
        pct_positive_momentum_72h=48.0,
        pct_bullish_trend=50.0,
        median_momentum_24h_pct=0.4,
        median_momentum_72h_pct=-0.2,
        warnings=(),
    )


def _assessment(score: int = 60, status: str = "AVAILABLE") -> MarketIntelligenceAssessment:
    return MarketIntelligenceAssessment(
        status=status,
        context_score=score,
        derivatives_bias="BALANCED",
        volatility_regime="UNKNOWN",
        macro_regime="UNKNOWN",
        flow_bias="UNKNOWN",
        sentiment_bias="UNKNOWN",
        strengths=(),
        warnings=(),
    )


def _candidate(symbol: str, direction: str = "LONG", score: float = 70.0):
    return SimpleNamespace(
        symbol=symbol,
        trade_direction=direction,
        technical_score=score,
    )


def test_no_provider_evidence_preserves_exact_upstream_order_and_chief_context(monkeypatch):
    candidates = [_candidate("AUSD"), _candidate("BUSD", "SHORT"), _candidate("CUSD")]
    regime = _regime()
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.build_market_intelligence_evidence",
        lambda *args, **kwargs: {},
    )

    result = enrich_finalist_market_intelligence(candidates, regime)

    assert list(result.candidates) == candidates
    assert result.assessments == {}
    assert result.evidence == {}
    assert result.ranked_finalists == ()
    assert result.chief_market_regime_context is regime


def test_no_provider_supports_legacy_minimal_market_regime_namespace(monkeypatch):
    candidates = [_candidate("AUSD")]
    legacy_regime = SimpleNamespace(
        sample_size=1,
        regime="NEUTRAL",
        breadth_score=50.0,
        pct_above_ema20=50.0,
        pct_above_ema50=50.0,
        pct_above_ema200=50.0,
        pct_positive_momentum_24h=50.0,
        pct_positive_momentum_72h=50.0,
        pct_bullish_trend=50.0,
    )
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.build_market_intelligence_evidence",
        lambda *args, **kwargs: {},
    )

    result = enrich_finalist_market_intelligence(candidates, legacy_regime)

    assert result.chief_market_regime_context is legacy_regime
    assert list(result.candidates) == candidates


def test_available_external_evidence_reorders_only_bounded_prefix_and_preserves_direction(monkeypatch):
    candidates = [
        _candidate("AUSD", "LONG", 70),
        _candidate("BUSD", "SHORT", 80),
        _candidate("CUSD", "LONG", 60),
        _candidate("TAILUSD", "SHORT", 99),
    ]
    evidence = {
        "AUSD": MarketIntelligenceEvidence(
            direction="LONG",
            bundle=MarketIntelligenceBundle(symbol="AUSD"),
            assessment=_assessment(55),
        ),
        "BUSD": MarketIntelligenceEvidence(
            direction="SHORT",
            bundle=MarketIntelligenceBundle(symbol="BUSD"),
            assessment=_assessment(65),
        ),
        "CUSD": MarketIntelligenceEvidence(
            direction="LONG",
            bundle=MarketIntelligenceBundle(symbol="CUSD"),
            assessment=_assessment(50),
        ),
    }
    seen = {}

    monkeypatch.setattr(
        "app.services.market_intelligence_integration.build_market_intelligence_evidence",
        lambda candidate_pairs, **kwargs: evidence,
    )

    def fake_rank(finalists, *, market_regime, external_market_intelligence):
        seen["symbols"] = [(item.symbol, item.trade_direction) for item in finalists]
        return [
            RankedCandidate(
                candidate=finalists[1],
                intelligence_score=90,
                setup_type="TEST",
                regime_alignment="NEUTRAL",
                external_market_score=65,
            ),
            RankedCandidate(
                candidate=finalists[0],
                intelligence_score=80,
                setup_type="TEST",
                regime_alignment="NEUTRAL",
                external_market_score=55,
            ),
            RankedCandidate(
                candidate=finalists[2],
                intelligence_score=70,
                setup_type="TEST",
                regime_alignment="NEUTRAL",
                external_market_score=50,
            ),
        ]

    monkeypatch.setattr(
        "app.services.market_intelligence_integration.rank_candidates_with_context",
        fake_rank,
    )

    result = enrich_finalist_market_intelligence(candidates, _regime(), max_candidates=3)

    assert seen["symbols"] == [("AUSD", "LONG"), ("BUSD", "SHORT"), ("CUSD", "LONG")]
    assert [(item.symbol, item.trade_direction) for item in result.candidates] == [
        ("BUSD", "SHORT"),
        ("AUSD", "LONG"),
        ("CUSD", "LONG"),
        ("TAILUSD", "SHORT"),
    ]
    assert set(item.symbol for item in result.candidates) == {
        "AUSD", "BUSD", "CUSD", "TAILUSD"
    }
    assert result.candidates[-1] is candidates[-1]


def test_chief_context_preserves_breadth_and_contains_context_only_serialized_evidence(monkeypatch):
    candidate = _candidate("BTCUSD")
    evidence = MarketIntelligenceEvidence(
        direction="LONG",
        bundle=MarketIntelligenceBundle(symbol="BTCUSD"),
        assessment=_assessment(60),
    )
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.build_market_intelligence_evidence",
        lambda *args, **kwargs: {"BTCUSD": evidence},
    )
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.rank_candidates_with_context",
        lambda finalists, **kwargs: [
            RankedCandidate(
                candidate=finalists[0],
                intelligence_score=75,
                setup_type="TEST",
                regime_alignment="NEUTRAL",
                external_market_score=60,
            )
        ],
    )

    result = enrich_finalist_market_intelligence([candidate], _regime())
    context = result.chief_market_regime_context
    serialized = context.external_market_intelligence["BTCUSD"]

    assert context.sample_size == 100
    assert context.breadth_score == 50.0
    assert context.pct_above_ema20 == 52.0
    assert serialized["assessment"]["context_score"] == 60
    assert serialized["authority"] == "context_only_no_gate_override"
    assert serialized["context_score_is_probability"] is False


def test_evidence_present_supports_legacy_minimal_market_regime_namespace(monkeypatch):
    candidate = _candidate("BTCUSD")
    legacy_regime = SimpleNamespace(
        sample_size=1,
        regime="NEUTRAL",
        breadth_score=50.0,
        pct_above_ema20=50.0,
        pct_above_ema50=50.0,
        pct_above_ema200=50.0,
        pct_positive_momentum_24h=50.0,
        pct_positive_momentum_72h=50.0,
        pct_bullish_trend=50.0,
    )
    evidence = MarketIntelligenceEvidence(
        direction="LONG",
        bundle=MarketIntelligenceBundle(symbol="BTCUSD"),
        assessment=_assessment(60),
    )
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.build_market_intelligence_evidence",
        lambda *args, **kwargs: {"BTCUSD": evidence},
    )
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.rank_candidates_with_context",
        lambda finalists, **kwargs: [
            RankedCandidate(
                candidate=finalists[0],
                intelligence_score=75,
                setup_type="TEST",
                regime_alignment="NEUTRAL",
                external_market_score=60,
            )
        ],
    )

    result = enrich_finalist_market_intelligence([candidate], legacy_regime)
    context = result.chief_market_regime_context

    assert context.status == "AVAILABLE"
    assert context.sample_size == 1
    assert context.median_momentum_24h_pct is None
    assert context.median_momentum_72h_pct is None
    assert context.warnings == ()
    assert "BTCUSD" in context.external_market_intelligence


def test_unavailable_evidence_is_retained_but_cannot_reorder(monkeypatch):
    candidates = [_candidate("AUSD"), _candidate("BUSD", "SHORT")]
    evidence = {
        "AUSD": MarketIntelligenceEvidence(
            direction="LONG",
            bundle=MarketIntelligenceBundle(symbol="AUSD"),
            assessment=_assessment(50, status="UNAVAILABLE"),
            provider_errors=("fake: upstream unavailable",),
        )
    }
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.build_market_intelligence_evidence",
        lambda *args, **kwargs: evidence,
    )
    monkeypatch.setattr(
        "app.services.market_intelligence_integration.rank_candidates_with_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unavailable evidence must not reorder finalists")
        ),
    )

    result = enrich_finalist_market_intelligence(candidates, _regime())

    assert list(result.candidates) == candidates
    assert result.evidence["AUSD"]["provider_errors"] == [
        "fake: upstream unavailable"
    ]
