from app.scanner.models import MarketSnapshot
from app.services.explosion_state import build_explosion_state_vector
from app.services.native_flow_evidence import NativeFlowMetrics


def _snapshot(**overrides):
    values = {
        "symbol": "TESTUSD",
        "last_price": 110.0,
        "ema20": 106.0,
        "ema50": 102.0,
        "ema200": 95.0,
        "rsi": 62.0,
        "macd_line": 1.0,
        "macd_signal": 0.6,
        "macd_histogram": 0.4,
        "atr": 2.0,
        "atr_pct": 1.8,
        "volume_ratio": 1.4,
        "technical_score": 72,
        "trend": "bullish",
        "movement_volume_ratio": 1.8,
        "confirmed_price_change_1h_pct": 1.4,
        "momentum_6h_pct": 3.6,
        "momentum_24h_pct": 6.0,
        "momentum_72h_pct": 8.0,
        "bollinger_bandwidth_percentile": 28.0,
        "atr_percentile": 32.0,
        "distance_to_24h_high_pct": 1.2,
        "distance_to_24h_low_pct": 7.0,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


def _flow(**overrides):
    values = {
        "symbol": "TEST/USD",
        "midpoint": 110.0,
        "buy_notional": 7000.0,
        "sell_notional": 3000.0,
        "neutral_notional": 0.0,
        "buy_share": 0.70,
        "aggressor_imbalance": 0.40,
        "trade_count_acceleration": 1.8,
        "large_print_concentration": 0.25,
        "book_notional_imbalance": 0.20,
        "recent_trade_count": 120,
        "sample_trades": 100,
    }
    values.update(overrides)
    return NativeFlowMetrics(**values)


def test_explosion_state_detects_expansion_without_trade_authority():
    result = build_explosion_state_vector(_snapshot(), native_flow=_flow())

    assert result.phase in {"EARLY_EXPANSION", "CONFIRMED_EXPANSION"}
    assert result.phase_score >= 42
    assert result.momentum_acceleration > 0
    assert result.trade_count_acceleration == 1.8
    assert result.flow_available is True
    assert result.shadow_only is True
    assert result.production_decision_changed is False


def test_explosion_state_flags_late_extension_separately_from_early_detection():
    result = build_explosion_state_vector(
        _snapshot(
            confirmed_price_change_1h_pct=3.0,
            momentum_6h_pct=10.0,
            momentum_24h_pct=22.0,
            distance_to_24h_high_pct=0.2,
        ),
        native_flow=_flow(),
    )

    assert result.phase == "LATE_EXTENSION"
    assert any("extended" in warning for warning in result.warnings)


def test_explosion_state_uses_previous_observation_for_persistence_only():
    first = build_explosion_state_vector(_snapshot(), native_flow=None)
    second = build_explosion_state_vector(
        _snapshot(
            confirmed_price_change_1h_pct=1.7,
            momentum_6h_pct=4.2,
            momentum_24h_pct=7.0,
        ),
        native_flow=None,
        previous=first,
    )

    assert first.persistence_score == 0
    assert second.persistence_score >= 3
    assert second.reference_price == 110.0
    assert second.production_decision_changed is False
