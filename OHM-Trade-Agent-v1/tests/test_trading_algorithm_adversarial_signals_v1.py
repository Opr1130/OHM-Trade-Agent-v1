from __future__ import annotations

import math

from app.scanner.models import MarketSnapshot
from app.services.explosion_state import build_explosion_state_vector
from app.services.movement_discovery_v2 import CoarseMover, discover_coarse_movers, evaluate_early_mover
from app.services.price_movement_radar import evaluate_price_movement


def _snapshot(**overrides) -> MarketSnapshot:
    values = dict(
        symbol="TESTUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=95.0,
        ema200=90.0,
        rsi=58.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.6,
        technical_score=85,
        trend="bullish",
        movement_data_status="AVAILABLE",
        movement_timeframe="15M",
        movement_volume_ratio=1.6,
        confirmed_close=100.0,
        confirmed_price_change_1h_pct=0.2,
        momentum_6h_pct=3.0,
        momentum_24h_pct=5.0,
        momentum_72h_pct=8.0,
        bollinger_bandwidth_percentile=12.0,
        atr_percentile=18.0,
        distance_to_24h_high_pct=1.0,
        distance_to_24h_low_pct=7.0,
        prior_24h_range_high=101.0,
        prior_24h_range_low=95.0,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def _coarse() -> CoarseMover:
    return CoarseMover(
        base_asset="TEST",
        primary_pair="TESTUSD",
        kraken_public_symbol="TEST/USD",
        last_price=100.0,
        volume_24h=10_000.0,
        notional_24h_usd_approx=1_000_000.0,
        high_24h=101.0,
        low_24h=90.0,
        lift_from_24h_low_pct=11.1,
        distance_from_24h_high_pct=1.0,
        coarse_score=80.0,
    )


def _all_numeric_values_are_finite(payload: dict) -> bool:
    for value in payload.values():
        if isinstance(value, float) and not math.isfinite(value):
            return False
    return True


class _NonFiniteTickerKraken:
    def get_asset_pairs(self):
        return {
            "BADUSD": {
                "altname": "BADUSD",
                "wsname": "BAD/USD",
                "status": "online",
            }
        }

    def get_tickers(self, pair_ids):
        return {
            "BADUSD": {
                "last": math.inf,
                "high_24h": math.inf,
                "low_24h": 1.0,
                "volume_24h": 100_000.0,
            }
        }


def test_coarse_discovery_rejects_non_finite_ticker_values():
    movers = discover_coarse_movers(
        _NonFiniteTickerKraken(),
        min_notional_usd=0.0,
    )
    assert movers == []


def test_early_mover_non_finite_core_feature_can_never_be_alert_eligible():
    signal = evaluate_early_mover(
        _snapshot(
            confirmed_price_change_1h_pct=math.nan,
            momentum_6h_pct=5.0,
            momentum_24h_pct=9.0,
            movement_volume_ratio=2.8,
            volume_ratio=2.8,
        ),
        _coarse(),
    )
    assert signal is None or signal.alert_eligible is False
    if signal is not None:
        assert _all_numeric_values_are_finite(signal.as_dict())


def test_explosion_state_never_persists_non_finite_decision_features():
    vector = build_explosion_state_vector(
        _snapshot(
            confirmed_price_change_1h_pct=math.nan,
            atr_percentile=math.inf,
            bollinger_bandwidth_percentile=math.nan,
        )
    )
    payload = vector.as_dict()
    assert _all_numeric_values_are_finite(payload)
    assert vector.phase == "DORMANT"
    assert vector.phase_score == 0
    assert any("non-finite" in warning.lower() for warning in vector.warnings)


def test_price_movement_radar_ignores_non_finite_external_derivatives():
    snapshot = _snapshot()
    baseline = evaluate_price_movement(snapshot)
    poisoned = evaluate_price_movement(
        snapshot,
        market_intelligence={
            "bundle": {
                "derivatives": [
                    {
                        "status": "AVAILABLE",
                        "funding_rate_pct": math.inf,
                        "open_interest_change_15m_pct": math.inf,
                        "open_interest_change_1h_pct": math.inf,
                        "open_interest_change_24h_pct": math.inf,
                        "open_interest_acceleration_zscore": math.inf,
                        "liquidations_1h_usd": math.inf,
                        "liquidations_24h_usd": 1.0,
                        "long_short_ratio": math.inf,
                    }
                ]
            }
        },
    )
    assert (poisoned is None) == (baseline is None)
    if poisoned is not None and baseline is not None:
        assert poisoned.readiness_score == baseline.readiness_score
        assert poisoned.components == baseline.components


def test_price_movement_radar_non_finite_snapshot_feature_fails_closed():
    signal = evaluate_price_movement(
        _snapshot(
            bollinger_bandwidth_percentile=math.nan,
            atr_percentile=math.nan,
            movement_volume_ratio=math.nan,
        )
    )
    assert signal is None
