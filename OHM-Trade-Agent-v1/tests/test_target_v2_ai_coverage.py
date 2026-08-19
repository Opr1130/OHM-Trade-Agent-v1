from types import SimpleNamespace

from app.services import shadow_decision_capture as capture
from app.services.target_v2_coverage_summary import build_target_v2_coverage_summary


def _snapshot():
    return SimpleNamespace(
        symbol="BTCUSD",
        trade_direction="LONG",
        last_price=100.0,
        ema20=98.0,
        ema50=95.0,
        trend="bullish",
        volume_ratio=1.4,
        technical_score=78,
        momentum_6h_pct=1.0,
        momentum_24h_pct=2.0,
        momentum_72h_pct=4.0,
        realized_range_24h_pct=5.0,
        rolling_24h_range_median_pct=4.5,
        rolling_24h_upside_median_pct=2.0,
        rolling_24h_upside_p75_pct=3.2,
        rolling_24h_upside_p90_pct=5.0,
        rolling_72h_upside_median_pct=4.0,
        rolling_72h_upside_p75_pct=6.0,
        rolling_72h_upside_p90_pct=8.0,
        rolling_24h_downside_median_pct=2.0,
        rolling_24h_downside_p75_pct=3.0,
        rolling_24h_downside_p90_pct=5.0,
        rolling_72h_downside_median_pct=4.0,
        rolling_72h_downside_p75_pct=6.0,
        rolling_72h_downside_p90_pct=8.0,
        execution_validation=None,
        price_movement_signal=None,
        _wave8_market_intelligence={"assessment": {"context_score": 50}},
    )


def test_chief_ai_review_capture_gets_separate_target_v2_shadow(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        capture,
        "record_shadow_candidate",
        lambda **kwargs: recorded.append(kwargs) or kwargs,
    )
    snapshot = _snapshot()
    original_intelligence = snapshot._wave8_market_intelligence

    assert capture.capture_snapshot_decision(
        snapshot,
        decision="AI_WATCH",
        market_regime="RISK_OFF",
        reason="watch",
        source="chief_ai_review",
    )

    assert recorded[0]["market_intelligence"] == original_intelligence
    assert recorded[0]["target_v2_shadow"]["shadow_only"] is True
    assert recorded[0]["target_v2_shadow"]["production_authoritative"] is False


def test_coverage_summary_segments_ai_target_and_survivor_records():
    payload = {
        "target_class": "REDUCED_TARGET",
        "confidence_score": 64,
        "proposed_t1_move_pct": 2.0,
    }
    records = [
        {
            "decision": "AI_WATCH",
            "source": "chief_ai_review",
            "target_v2_shadow": payload,
            "observations": {"24h": {"directional_move_pct": 2.5}},
        },
        {
            "decision": "TARGET_REJECT",
            "source": "target_quality_gate",
            "target_v2_shadow": payload,
            "observations": {"24h": {"directional_move_pct": 1.0}},
        },
        {
            "decision": "ENTER_NOW",
            "source": "qualified_profit_rank",
            "target_v2_shadow": payload,
            "observations": {"24h": {"directional_move_pct": 3.0}},
        },
    ]

    summary = build_target_v2_coverage_summary(records)

    assert summary["overall"]["samples"] == 3
    assert summary["overall"]["validated"] == 3
    assert summary["segments"]["AI_NON_ENTRY"]["samples"] == 1
    assert summary["segments"]["AI_NON_ENTRY"]["t1_shadow_hit_rate_pct"] == 100.0
    assert summary["segments"]["TARGET_V1_REJECT"]["t1_shadow_hit_rate_pct"] == 0.0
    assert summary["segments"]["QUALIFIED_SURVIVOR"]["t1_shadow_hit_rate_pct"] == 100.0
    assert summary["ai_decisions"]["AI_WATCH"]["samples"] == 1
    assert summary["production_target_gate_changed"] is False
    assert summary["production_decisions_changed"] is False
    assert summary["automatic_promotion"] is False
    assert summary["shadow_only"] is True


def test_coverage_summary_reads_legacy_nested_v2_payloads():
    summary = build_target_v2_coverage_summary([
        {
            "decision": "TARGET_REJECT",
            "source": "target_quality_gate",
            "market_intelligence": {
                "target_v2_shadow": {
                    "target_class": "SCALP_TARGET",
                    "confidence_score": 45,
                    "proposed_t1_move_pct": 1.0,
                }
            },
            "observations": {"24h": {"directional_move_pct": 1.2}},
        }
    ])

    assert summary["overall"]["samples"] == 1
    assert summary["overall"]["t1_shadow_hit_rate_pct"] == 100.0
