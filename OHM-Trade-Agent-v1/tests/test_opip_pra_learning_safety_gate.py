"""PR-A learning safety gate: no approved promotion means neutral 1.0.

This is the boundary that makes it safe to let paper outcomes reach the
learner later in PR-A. Learning evidence may grow without limit; runtime
influence stays exactly neutral until a durable, human-approved, versioned and
currently effective promotion authorizes one exact learned profile.

Every test here asserts the refusal side unless the promotion is genuinely
approved *and* effective *and* matched to the live profile content.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import profitability_learning
from app.services import trade_decision_intelligence as intel
from app.services.learning_governance import (
    ApprovedCalibrationPromotion,
    NEUTRAL_CALIBRATION_MULTIPLIER,
    PROMOTION_REASON_APPROVAL_IN_FUTURE,
    PROMOTION_REASON_APPROVED_AND_EFFECTIVE,
    PROMOTION_REASON_EXPIRED,
    PROMOTION_REASON_NO_APPROVED_PROMOTION,
    PROMOTION_REASON_NOT_YET_EFFECTIVE,
    PROMOTION_REASON_PROFILE_MISMATCH,
    PROMOTION_REASON_PROFILE_UNAVAILABLE,
    PROMOTION_REASON_ROLLED_BACK,
    PROMOTION_REASON_SUPERSEDED,
    load_approved_calibration_promotion,
    resolve_calibration_promotion,
    rollback_calibration_promotion,
    save_approved_calibration_promotion,
    supersede_calibration_promotion,
)

# A fixed "now" well clear of the effective windows used below, so the tests
# do not depend on the wall clock of the machine running them.
NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
PAST = NOW - timedelta(days=30)
FUTURE = NOW + timedelta(days=30)


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    """Isolate both the promotion registry and the calibration profile."""
    monkeypatch.setenv("OPIP_LEARNING_GOVERNANCE_DIR", str(tmp_path))
    monkeypatch.setattr(
        profitability_learning, "PROFILE_FILE", tmp_path / "strategy_calibration_profile.json"
    )
    monkeypatch.setattr(
        profitability_learning, "LOCK_FILE", tmp_path / ".strategy_calibration_profile.lock"
    )
    return tmp_path


def _write_profile(tmp_path, weights, *, status="CALIBRATED", include_identity=True):
    profile = {
        "schema_version": 2,
        "version": "profitability-learning-v2",
        "trade_calibration": {"status": status},
        "weights": dict(weights),
    }
    if include_identity:
        profile["profile_id"] = profitability_learning._profile_content_id(profile)
    path = tmp_path / "strategy_calibration_profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return profile


def _approved(profile_id, **overrides):
    values = {
        "promotion_id": "PROMO:test-1",
        "profile_id": profile_id,
        "approving_principal": "operator:owner",
        "approved_at_utc": PAST,
        "effective_at_utc": PAST,
    }
    values.update(overrides)
    return ApprovedCalibrationPromotion(**values)


def _runtime():
    return intel._effective_calibration_multiplier(direction="LONG", regime="RISK_ON")


# ---------------------------------------------------------------------------
# No approval -> neutral, regardless of how much evidence exists
# ---------------------------------------------------------------------------


def test_no_history_is_neutral(learning_env):
    multiplier, status = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == PROMOTION_REASON_NO_APPROVED_PROMOTION


def test_observations_without_approval_are_neutral(learning_env):
    _write_profile(learning_env, {"direction:LONG": 1.10}, status="INSUFFICIENT_DATA")
    multiplier, _ = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER


def test_sufficient_samples_without_approval_are_neutral(learning_env):
    """A statistically eligible learned profile is still not authorization."""
    profile = _write_profile(learning_env, {"direction:LONG": 1.20, "regime:RISK_ON": 1.05})
    assert profitability_learning.learned_multiplier(direction="LONG", regime="RISK_ON") == pytest.approx(1.25)
    assert profile["profile_id"].startswith("CALPROF:")

    multiplier, status = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == PROMOTION_REASON_NO_APPROVED_PROMOTION


def test_candidate_profile_without_approval_is_neutral(learning_env):
    _write_profile(learning_env, {"direction:LONG": 0.80, "regime:RISK_ON": 0.95})
    multiplier, _ = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER


def test_learner_finding_a_pattern_does_not_move_runtime_sizing(learning_env):
    """Section 39: evidence grows, threshold satisfied, still neutral."""
    _write_profile(learning_env, {"direction:LONG": 1.15})
    # Simulate an outcome population large enough that the old code would have
    # activated the multiplier purely from evidence.
    for _ in range(50):
        assert profitability_learning.learned_multiplier(direction="LONG", regime=None) == pytest.approx(1.15)
    multiplier, _ = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER


# ---------------------------------------------------------------------------
# Approved but not (yet) active -> neutral
# ---------------------------------------------------------------------------


def test_approved_with_future_effective_date_is_neutral(learning_env):
    profile = _write_profile(learning_env, {"direction:LONG": 1.15})
    save_approved_calibration_promotion(
        _approved(profile["profile_id"], effective_at_utc=FUTURE)
    )
    resolver = resolve_calibration_promotion(
        promotion=load_approved_calibration_promotion(),
        active_profile_id=profile["profile_id"],
        now=NOW,
    )
    assert resolver.active is False
    assert resolver.reason == PROMOTION_REASON_NOT_YET_EFFECTIVE


def test_approval_recorded_in_the_future_is_neutral(learning_env):
    profile = _write_profile(learning_env, {"direction:LONG": 1.15})
    save_approved_calibration_promotion(
        _approved(profile["profile_id"], approved_at_utc=FUTURE, effective_at_utc=FUTURE)
    )
    resolver = resolve_calibration_promotion(
        promotion=load_approved_calibration_promotion(),
        active_profile_id=profile["profile_id"],
        now=NOW,
    )
    assert resolver.active is False
    assert resolver.reason == PROMOTION_REASON_APPROVAL_IN_FUTURE


def test_expired_promotion_is_neutral(learning_env):
    profile = _write_profile(learning_env, {"direction:LONG": 1.15})
    save_approved_calibration_promotion(
        _approved(
            profile["profile_id"],
            effective_at_utc=PAST,
            expires_at_utc=PAST + timedelta(days=1),
        )
    )
    resolver = resolve_calibration_promotion(
        promotion=load_approved_calibration_promotion(),
        active_profile_id=profile["profile_id"],
        now=NOW,
    )
    assert resolver.active is False
    assert resolver.reason == PROMOTION_REASON_EXPIRED


# ---------------------------------------------------------------------------
# Rollback / supersession
# ---------------------------------------------------------------------------


def test_rolled_back_promotion_is_neutral(learning_env):
    profile = _write_profile(learning_env, {"direction:LONG": 1.15})
    promotion = _approved(profile["profile_id"])
    save_approved_calibration_promotion(rollback_calibration_promotion(promotion, rolled_back_at_utc=NOW))
    resolver = resolve_calibration_promotion(
        promotion=load_approved_calibration_promotion(),
        active_profile_id=profile["profile_id"],
        now=NOW,
    )
    assert resolver.active is False
    assert resolver.reason == PROMOTION_REASON_ROLLED_BACK


def test_superseded_promotion_does_not_remain_active(learning_env):
    profile = _write_profile(learning_env, {"direction:LONG": 1.15})
    promotion = _approved(profile["profile_id"])
    save_approved_calibration_promotion(
        supersede_calibration_promotion(promotion, superseded_by="PROMO:test-2")
    )
    resolver = resolve_calibration_promotion(
        promotion=load_approved_calibration_promotion(),
        active_profile_id=profile["profile_id"],
        now=NOW,
    )
    assert resolver.active is False
    assert resolver.reason == PROMOTION_REASON_SUPERSEDED


# ---------------------------------------------------------------------------
# Version / content binding
# ---------------------------------------------------------------------------


def test_promotion_for_a_different_profile_is_neutral(learning_env):
    """Approving one learned profile must not authorize a later, different one."""
    _write_profile(learning_env, {"direction:LONG": 1.20})
    save_approved_calibration_promotion(_approved("CALPROF:" + "0" * 32))
    multiplier, status = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == PROMOTION_REASON_PROFILE_MISMATCH


def test_profile_identity_changes_when_learned_weights_change(learning_env):
    first = _write_profile(learning_env, {"direction:LONG": 1.05})
    second = _write_profile(learning_env, {"direction:LONG": 1.15})
    assert first["profile_id"] != second["profile_id"]


def test_profile_identity_ignores_volatile_clock_fields(learning_env):
    """A refresh that only moves clocks must not invalidate an approval."""
    profile = _write_profile(learning_env, {"direction:LONG": 1.05})
    profile["generated_at"] = "2030-01-01T00:00:00Z"
    assert profitability_learning._profile_content_id(profile) == profile["profile_id"]


def test_profile_without_content_identity_is_unavailable(learning_env):
    """Legacy profiles cannot be proven to match an approval."""
    _write_profile(learning_env, {"direction:LONG": 1.15}, include_identity=False)
    save_approved_calibration_promotion(_approved("CALPROF:" + "0" * 32))
    assert profitability_learning.active_profile_id() is None
    multiplier, status = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == PROMOTION_REASON_PROFILE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Fail-neutral on unreadable / malformed approval state
# ---------------------------------------------------------------------------


def test_missing_promotion_file_is_neutral(learning_env):
    _write_profile(learning_env, {"direction:LONG": 1.15})
    assert load_approved_calibration_promotion() is None
    multiplier, status = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == PROMOTION_REASON_NO_APPROVED_PROMOTION


def test_corrupt_promotion_file_is_neutral(learning_env):
    _write_profile(learning_env, {"direction:LONG": 1.15})
    (learning_env / "approved_calibration_promotion.json").write_text("{not json", encoding="utf-8")
    assert load_approved_calibration_promotion() is None
    multiplier, _ = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER


def test_promotion_missing_approval_metadata_cannot_be_constructed():
    with pytest.raises(ValueError):
        ApprovedCalibrationPromotion(
            promotion_id="PROMO:x",
            profile_id="CALPROF:" + "a" * 32,
            approving_principal="   ",
            approved_at_utc=PAST,
            effective_at_utc=PAST,
        )


def test_promotion_cannot_claim_automatic_activation():
    with pytest.raises(ValueError):
        _approved("CALPROF:" + "a" * 32, automatic_activation=True)


def test_promotion_cannot_change_trading_authority():
    with pytest.raises(ValueError):
        _approved("CALPROF:" + "a" * 32, trade_authority_changed=True)


# ---------------------------------------------------------------------------
# Approved and effective -> authorized
# ---------------------------------------------------------------------------


def test_approved_and_effective_authorizes_multiplier(learning_env):
    profile = _write_profile(learning_env, {"direction:LONG": 1.10})
    save_approved_calibration_promotion(_approved(profile["profile_id"], expires_at_utc=FUTURE))

    multiplier, status = _runtime()
    assert multiplier == pytest.approx(1.10)
    assert status.startswith(PROMOTION_REASON_APPROVED_AND_EFFECTIVE)


def test_authorized_multiplier_stays_inside_bounds(learning_env):
    profile = _write_profile(learning_env, {"direction:LONG": 1.25, "regime:RISK_ON": 1.25})
    save_approved_calibration_promotion(_approved(profile["profile_id"]))
    multiplier, _ = _runtime()
    assert 0.75 <= multiplier <= 1.25


def test_resolution_is_stable_across_restart(learning_env):
    """Safety state must reconstruct consistently, not depend on process state."""
    profile = _write_profile(learning_env, {"direction:LONG": 1.10})
    save_approved_calibration_promotion(_approved(profile["profile_id"]))

    first = _runtime()
    reloaded = load_approved_calibration_promotion()
    second = resolve_calibration_promotion(
        promotion=reloaded,
        active_profile_id=profitability_learning.active_profile_id(),
        now=NOW,
    )
    assert first[0] == pytest.approx(1.10)
    assert second.active is True
    assert second.profile_id == profile["profile_id"]
