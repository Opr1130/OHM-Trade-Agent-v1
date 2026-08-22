from __future__ import annotations

import math
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


def test_target_v2_rejects_unknown_trade_direction():
    result = evaluate_target_v2_shadow(_snapshot(trade_direction="SIDEWAYS"))
    assert result.target_class == "REJECT"
    assert result.proposed_t1_move_pct is None
    assert result.proposed_t2_move_pct is None
    assert any("direction" in text.lower() for text in result.reasons + result.warnings)


def test_target_v2_rejects_non_finite_excursion_percentiles():
    result = evaluate_target_v2_shadow(
        _snapshot(rolling_24h_upside_median_pct=math.nan)
    )
    assert result.target_class == "REJECT"
    assert result.confidence_score == 0
    assert result.proposed_t1_move_pct is None
    assert result.proposed_t2_move_pct is None


def test_target_v2_never_emits_non_finite_target_or_confidence():
    result = evaluate_target_v2_shadow(
        _snapshot(
            rolling_24h_upside_p75_pct=math.inf,
            rolling_72h_upside_p75_pct=math.inf,
            rolling_72h_upside_p90_pct=math.inf,
        )
    )
    assert math.isfinite(float(result.confidence_score))
    assert result.proposed_t1_move_pct is None or math.isfinite(result.proposed_t1_move_pct)
    assert result.proposed_t2_move_pct is None or math.isfinite(result.proposed_t2_move_pct)


def test_target_v2_summary_does_not_credit_scalp_target_hit_only_after_24h():
    records = [
        {
            "market_intelligence": {
                "target_v2_shadow": {
                    "target_class": "SCALP_TARGET",
                    "confidence_score": 45,
                    "proposed_t1_move_pct": 1.0,
                    "proposed_t2_move_pct": None,
                    "estimated_time_horizon": "UP_TO_24H",
                }
            },
            "observations": {
                "1h": {"directional_move_pct": 0.4},
                "4h": {"directional_move_pct": 0.6},
                "12h": {"directional_move_pct": 0.8},
                "24h": {"directional_move_pct": 0.9},
                "72h": {"directional_move_pct": 3.0},
            },
        }
    ]
    summary = build_target_v2_shadow_summary(records)
    scalp = summary["by_target_class"]["SCALP_TARGET"]
    assert scalp["validated"] == 1
    assert scalp["t1_shadow_hit_rate_pct"] == 0.0


def test_target_v2_summary_ignores_non_finite_observation_values():
    records = [
        {
            "market_intelligence": {
                "target_v2_shadow": {
                    "target_class": "SCALP_TARGET",
                    "confidence_score": 45,
                    "proposed_t1_move_pct": 1.0,
                    "estimated_time_horizon": "UP_TO_24H",
                }
            },
            "observations": {
                "1h": {"directional_move_pct": math.nan},
                "24h": {"directional_move_pct": 0.5},
            },
        }
    ]
    summary = build_target_v2_shadow_summary(records)
    scalp = summary["by_target_class"]["SCALP_TARGET"]
    assert scalp["validated"] == 1
    assert scalp["t1_shadow_hit_rate_pct"] == 0.0
