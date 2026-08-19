from types import SimpleNamespace

from app.services.target_attainability_v2_shadow import (
    build_target_v2_shadow_summary,
    evaluate_target_v2_shadow,
)


def _snapshot(**overrides):
    values = {
        "symbol": "BTCUSD",
        "trade_direction": "LONG",
        "last_price": 100.0,
        "ema20": 98.0,
        "ema50": 95.0,
        "trend": "bullish",
        "volume_ratio": 1.3,
        "technical_score": 78,
        "momentum_6h_pct": 1.0,
        "momentum_24h_pct": 2.0,
        "momentum_72h_pct": 4.0,
        "realized_range_24h_pct": 5.0,
        "rolling_24h_range_median_pct": 4.5,
        "rolling_24h_upside_median_pct": 2.0,
        "rolling_24h_upside_p75_pct": 3.2,
        "rolling_24h_upside_p90_pct": 5.0,
        "rolling_72h_upside_median_pct": 4.0,
        "rolling_72h_upside_p75_pct": 6.0,
        "rolling_72h_upside_p90_pct": 8.0,
        "rolling_24h_downside_median_pct": 2.0,
        "rolling_24h_downside_p75_pct": 3.0,
        "rolling_24h_downside_p90_pct": 5.0,
        "rolling_72h_downside_median_pct": 4.0,
        "rolling_72h_downside_p75_pct": 6.0,
        "rolling_72h_downside_p90_pct": 8.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_strong_context_proposes_full_target_shadow_only():
    result = evaluate_target_v2_shadow(_snapshot())

    assert result.target_class == "FULL_TARGET"
    assert result.proposed_t1_move_pct == 2.0
    assert result.proposed_t2_move_pct == 6.0
    assert result.confidence_score >= 70
    assert result.shadow_only is True
    assert result.production_authoritative is False


def test_weaker_context_reduces_target_instead_of_forcing_full_target():
    result = evaluate_target_v2_shadow(
        _snapshot(
            volume_ratio=0.9,
            technical_score=62,
            momentum_6h_pct=-0.2,
            momentum_24h_pct=1.0,
            momentum_72h_pct=2.0,
            trend="neutral",
            ema20=99.0,
            ema50=101.0,
        )
    )

    assert result.target_class in {"REDUCED_TARGET", "SCALP_TARGET", "WATCH_FOR_ENTRY"}
    assert result.production_authoritative is False


def test_short_uses_downside_percentiles():
    result = evaluate_target_v2_shadow(
        _snapshot(
            trade_direction="SHORT",
            last_price=95.0,
            ema20=97.0,
            ema50=100.0,
            trend="bearish",
            momentum_6h_pct=-1.0,
            momentum_24h_pct=-2.0,
            momentum_72h_pct=-3.0,
        )
    )

    assert result.direction == "SHORT"
    assert result.target_class == "FULL_TARGET"
    assert result.proposed_t1_move_pct == 2.0
    assert result.proposed_t2_move_pct == 6.0


def test_missing_history_rejects_shadow_without_touching_production():
    result = evaluate_target_v2_shadow(
        _snapshot(
            rolling_24h_upside_median_pct=0.0,
            rolling_24h_upside_p75_pct=0.0,
            rolling_24h_upside_p90_pct=0.0,
        )
    )

    assert result.target_class == "REJECT"
    assert result.shadow_only is True
    assert result.production_authoritative is False


def test_summary_uses_existing_shadow_price_observations_for_validation():
    records = [
        {
            "market_intelligence": {
                "target_v2_shadow": {
                    "target_class": "REDUCED_TARGET",
                    "confidence_score": 64,
                    "proposed_t1_move_pct": 2.0,
                }
            },
            "observations": {
                "1h": {"directional_move_pct": 1.0},
                "24h": {"directional_move_pct": 2.5},
            },
        },
        {
            "market_intelligence": {
                "target_v2_shadow": {
                    "target_class": "REDUCED_TARGET",
                    "confidence_score": 60,
                    "proposed_t1_move_pct": 2.0,
                }
            },
            "observations": {
                "24h": {"directional_move_pct": 1.5},
            },
        },
    ]

    summary = build_target_v2_shadow_summary(records)

    assert summary["status"] == "OK"
    assert summary["samples"] == 2
    assert summary["validated_samples"] == 2
    assert summary["by_target_class"]["REDUCED_TARGET"]["t1_shadow_hit_rate_pct"] == 50.0
    assert summary["production_target_gate_changed"] is False
    assert summary["automatic_promotion"] is False
