import pytest

from app.services import profitability_learning
from app.services.market_intelligence_attribution import (
    build_market_intelligence_attribution,
    extract_market_intelligence_features,
    preview_proposed_market_intelligence_multiplier,
)


def _evidence(
    *,
    score: int,
    derivatives: str,
    volatility: str,
    macro: str = "NEUTRAL",
    flow: str = "BALANCED",
    sentiment: str = "BALANCED",
    status: str = "AVAILABLE",
):
    return {
        "direction": "LONG",
        "assessment": {
            "status": status,
            "context_score": score,
            "derivatives_bias": derivatives,
            "volatility_regime": volatility,
            "macro_regime": macro,
            "flow_bias": flow,
            "sentiment_bias": sentiment,
        },
        "authority": "context_only_no_gate_override",
        "context_score_is_probability": False,
    }


SUPPORTIVE = _evidence(
    score=65,
    derivatives="BALANCED",
    volatility="LOW",
    macro="SUPPORTIVE",
)
HEADWIND = _evidence(
    score=35,
    derivatives="CROWDED_LONG",
    volatility="EXTREME",
    macro="DEFENSIVE",
)


def _outcome(i: int, *, pnl: float, evidence: dict):
    return {
        "trade_id": f"T{i}",
        "entered_trade": True,
        "terminal_status": "closed",
        "direction": "LONG",
        "net_pnl": pnl,
        "market_intelligence": evidence,
    }


def test_feature_extraction_is_direction_aware_and_pairwise_bounded():
    features = extract_market_intelligence_features(SUPPORTIVE, direction="LONG")

    assert "single:direction=LONG|context=SUPPORTIVE" in features
    assert "single:direction=LONG|macro_regime=SUPPORTIVE" in features
    assert (
        "combo:direction=LONG|derivatives_bias=BALANCED&volatility_regime=LOW"
        in features
    )
    assert len([feature for feature in features if feature.startswith("single:")]) == 6
    assert len([feature for feature in features if feature.startswith("combo:")]) == 10
    assert not any("context=" in feature for feature in features if feature.startswith("combo:"))


def test_unavailable_or_malformed_evidence_never_creates_features():
    unavailable = _evidence(
        score=50,
        derivatives="UNKNOWN",
        volatility="UNKNOWN",
        status="UNAVAILABLE",
    )

    assert extract_market_intelligence_features(unavailable, direction="LONG") == ()
    assert extract_market_intelligence_features(None, direction="LONG") == ()
    assert extract_market_intelligence_features(SUPPORTIVE, direction="SHORT") == ()
    assert extract_market_intelligence_features(SUPPORTIVE, direction="UNKNOWN") == ()


def test_actual_attribution_requires_thirty_evaluable_trades_before_proposals():
    outcomes = [_outcome(i, pnl=2.0, evidence=SUPPORTIVE) for i in range(29)]

    result = build_market_intelligence_attribution(outcomes=outcomes, shadows=[])
    actual = result["actual_trade_attribution"]

    assert result["status"] == "INSUFFICIENT_DATA"
    assert actual["samples"] == 29
    assert actual["proposed_weights"] == {}
    assert result["promotion"]["automatically_applied"] is False
    assert result["promotion"]["status"] == "NOT_ELIGIBLE_INSUFFICIENT_DATA"


def test_actual_signal_and_combination_proposals_are_bounded_and_review_only():
    outcomes = [
        *[_outcome(i, pnl=2.0, evidence=SUPPORTIVE) for i in range(20)],
        *[_outcome(i, pnl=-1.0, evidence=HEADWIND) for i in range(20, 40)],
    ]

    result = build_market_intelligence_attribution(outcomes=outcomes, shadows=[])
    weights = result["proposed_weights"]

    assert result["status"] == "CALIBRATED"
    assert weights["single:direction=LONG|context=SUPPORTIVE"] == pytest.approx(1.15)
    assert weights["single:direction=LONG|context=HEADWIND"] == pytest.approx(0.85)
    assert weights[
        "combo:direction=LONG|derivatives_bias=BALANCED&volatility_regime=LOW"
    ] == pytest.approx(1.15)
    assert result["promotion"] == {
        "status": "HUMAN_REVIEW_REQUIRED",
        "automatically_applied": False,
        "live_ranking_changed": False,
        "live_sizing_changed": False,
    }
    assert result["guardrails"]["leverage_mutable"] is False
    assert result["guardrails"]["automatic_strategy_promotion"] is False

    preview = preview_proposed_market_intelligence_multiplier(
        result,
        evidence=SUPPORTIVE,
        direction="LONG",
    )
    assert preview == pytest.approx(1.25)


def test_shadow_attribution_confirms_signals_but_cannot_propose_weights():
    shadows = [
        {
            "direction": "LONG",
            "market_intelligence": SUPPORTIVE,
            "observations": {"24h": {"directional_move_pct": 2.5}},
        }
        for _ in range(12)
    ]

    result = build_market_intelligence_attribution(outcomes=[], shadows=shadows)
    shadow = result["shadow_confirmation"]
    signal = shadow["signals"]["single:direction=LONG|context=SUPPORTIVE"]

    assert shadow["status"] == "OK"
    assert shadow["samples"] == 12
    assert signal["qualified_for_confirmation"] is True
    assert signal["positive_rate_pct"] == pytest.approx(100.0)
    assert signal["can_propose_live_weight"] is False
    assert result["proposed_weights"] == {}


def test_daily_profitability_profile_embeds_wave8_1_attribution_without_promotion():
    outcomes = [
        *[_outcome(i, pnl=2.0, evidence=SUPPORTIVE) for i in range(20)],
        *[_outcome(i, pnl=-1.0, evidence=HEADWIND) for i in range(20, 40)],
    ]

    profile = profitability_learning.build_profitability_profile(
        outcomes=outcomes,
        executions=[],
        shadows=[],
        persist=False,
    )

    assert profile["schema_version"] == 2
    assert profile["market_intelligence_attribution"]["status"] == "CALIBRATED"
    assert profile["guardrails"]["market_intelligence_auto_promotion"] is False
    assert profile["guardrails"]["market_intelligence_human_review_required"] is True
    assert profile["guardrails"]["leverage_mutable"] is False
