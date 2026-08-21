from app.services.explosion_precursor import evaluate_explosion_precursor
from app.services.explosion_state import ExplosionStateVector


def _vector(**overrides):
    data = dict(
        version="explosion-state-v1",
        symbol="TESTUSD",
        reference_price=1.0,
        momentum_1h_pct=0.2,
        momentum_6h_pct=1.2,
        momentum_24h_pct=2.0,
        momentum_72h_pct=3.0,
        momentum_velocity_1h=0.2,
        momentum_velocity_6h=0.2,
        momentum_acceleration=0.05,
        relative_volume=0.8,
        volume_expansion=0.0,
        relative_volume_change=0.2,
        atr_pct=1.0,
        atr_percentile=25.0,
        atr_percentile_change=6.0,
        bandwidth_percentile=20.0,
        bandwidth_percentile_change=7.0,
        compression_release_score=0.3,
        distance_to_24h_high_pct=3.0,
        distance_to_24h_low_pct=5.0,
        distance_to_high_velocity_pct=0.8,
        base_displacement_pct=5.0,
        base_displacement_velocity_pct=0.5,
        trend="bullish",
        ema_structure_score=1,
        trade_count_acceleration=1.7,
        aggressor_imbalance=0.2,
        large_print_concentration=0.1,
        book_notional_imbalance=0.05,
        flow_available=True,
        persistence_score=1,
        phase="DORMANT",
        phase_score=20,
        reasons=(),
        warnings=(),
    )
    data.update(overrides)
    return ExplosionStateVector(**data)


def test_quiet_but_strengthening_dormant_state_becomes_precursor_watch():
    previous = _vector(
        momentum_acceleration=-0.25,
        phase_score=8,
        relative_volume_change=None,
        atr_percentile_change=None,
        bandwidth_percentile_change=None,
        distance_to_high_velocity_pct=None,
        base_displacement_velocity_pct=None,
        trade_count_acceleration=1.0,
        aggressor_imbalance=0.0,
    )
    current = _vector()

    result = evaluate_explosion_precursor(current, previous=previous)

    assert result.watch is True
    assert result.score >= 36
    assert result.evidence_count >= 3
    assert result.production_decision_changed is False
    assert result.shadow_only is True


def test_compression_alone_does_not_create_precursor_watch():
    current = _vector(
        momentum_6h_pct=0.0,
        ema_structure_score=0,
        distance_to_24h_high_pct=12.0,
        relative_volume_change=None,
        atr_percentile_change=None,
        bandwidth_percentile_change=None,
        distance_to_high_velocity_pct=None,
        base_displacement_velocity_pct=None,
        trade_count_acceleration=None,
        aggressor_imbalance=None,
    )

    result = evaluate_explosion_precursor(current)

    assert result.watch is False
    assert result.evidence_count < 3


def test_already_expanding_state_is_not_recast_as_latent_precursor():
    current = _vector(phase="EARLY_EXPANSION", phase_score=55)

    result = evaluate_explosion_precursor(current)

    assert result.watch is False
    assert any("already beyond" in warning for warning in result.warnings)
