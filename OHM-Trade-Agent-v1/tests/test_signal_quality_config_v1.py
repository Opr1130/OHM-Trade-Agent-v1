"""Signal Quality v1 configuration boundaries.

The priors are only safe while they stay internally ordered. A band ladder that
inverts would silently turn a gate into a no-op, so Settings refuses to boot on
one rather than degrading quietly.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.signal_scoring import SignalQualityConfig


BASE = {"webhook_secret": "test-webhook-secret"}


def _settings(**overrides):
    return Settings(**BASE, **overrides)


def test_phase_1_ships_dark_by_default():
    settings = _settings()

    assert settings.signal_quality_v1_enabled is False
    assert settings.signal_quality_early_alerts_enabled is False


def test_default_priors_match_the_documented_design():
    settings = _settings()

    assert settings.signal_quality_min_liquidity_usd == 100_000.0
    assert settings.signal_quality_observation_liquidity_usd == 250_000.0
    assert settings.signal_quality_preferred_liquidity_usd == 1_000_000.0
    assert settings.signal_quality_history_scans == 8
    assert settings.signal_quality_scan_interval_seconds == 600
    assert settings.signal_quality_max_cards_per_scan == 4

    assert settings.signal_quality_early_building_opportunity == 55
    assert settings.signal_quality_breakout_opportunity == 70
    assert settings.signal_quality_actionable_opportunity == 80
    assert settings.signal_quality_actionable_min_persistence_scans == 3
    assert settings.signal_quality_actionable_max_exhaustion == 20


def test_misordered_liquidity_bands_are_rejected():
    with pytest.raises(ValidationError, match="liquidity bands must be ordered"):
        _settings(
            signal_quality_min_liquidity_usd=500_000.0,
            signal_quality_observation_liquidity_usd=250_000.0,
        )


def test_misordered_stage_ladder_is_rejected():
    with pytest.raises(ValidationError, match="stage thresholds must increase"):
        _settings(signal_quality_breakout_opportunity=90, signal_quality_actionable_opportunity=80)


def test_persistence_ladder_cannot_invert():
    with pytest.raises(ValidationError, match="MIN_PERSISTENCE_SCANS"):
        _settings(
            signal_quality_breakout_min_persistence_scans=4,
            signal_quality_actionable_min_persistence_scans=2,
        )


def test_exhaustion_tolerance_cannot_loosen_at_the_top_stage():
    with pytest.raises(ValidationError, match="MAX_EXHAUSTION"):
        _settings(
            signal_quality_breakout_max_exhaustion=20,
            signal_quality_actionable_max_exhaustion=30,
        )


def test_history_must_retain_enough_scans_to_reach_the_top_stage():
    with pytest.raises(ValidationError, match="HISTORY_SCANS"):
        _settings(
            signal_quality_history_scans=3,
            signal_quality_actionable_min_persistence_scans=3,
        )


def test_scoring_config_maps_cleanly_from_flat_settings():
    settings = _settings(
        signal_quality_v1_enabled=True,
        signal_quality_min_liquidity_usd=200_000.0,
        signal_quality_observation_liquidity_usd=300_000.0,
        signal_quality_max_cards_per_scan=7,
    )
    config = SignalQualityConfig.from_settings(settings)

    assert config.enabled is True
    assert config.min_liquidity_usd == 200_000.0
    assert config.observation_liquidity_usd == 300_000.0
    assert config.max_cards_per_scan == 7
    # Composition weights stay in code, not in environment drift surface.
    assert config.weights.opportunity_tradeability == 0.25


def test_scoring_config_falls_back_to_priors_for_an_incomplete_settings_object():
    """A partial settings object must not silently produce zeroed gates."""

    class Partial:
        signal_quality_v1_enabled = True

    config = SignalQualityConfig.from_settings(Partial())

    assert config.enabled is True
    assert config.min_liquidity_usd == 100_000.0
    assert config.breakout_opportunity == 70.0


def test_composition_weights_sum_to_one():
    weights = SignalQualityConfig().weights

    explosion = (
        weights.explosion_price_acceleration
        + weights.explosion_volume_acceleration
        + weights.explosion_relative_strength
        + weights.explosion_persistence
        + weights.explosion_structural_breakout
    )
    opportunity = (
        weights.opportunity_explosion_potential
        + weights.opportunity_tradeability
        + weights.opportunity_pattern_strength
        + weights.opportunity_relative_strength
        + weights.opportunity_persistence
    )
    pattern = (
        weights.pattern_price_acceleration
        + weights.pattern_structural_expansion
        + weights.pattern_near_high
        + weights.pattern_quality_bonus
    )
    relative = (
        weights.relative_price_change_percentile + weights.relative_structural_percentile
    )

    assert explosion == pytest.approx(1.0)
    assert opportunity == pytest.approx(1.0)
    assert pattern == pytest.approx(1.0)
    assert relative == pytest.approx(1.0)
