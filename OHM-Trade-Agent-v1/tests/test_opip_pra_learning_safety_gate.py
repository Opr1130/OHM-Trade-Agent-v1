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

#: The instant every runtime lookup in this module resolves at. The promotion
#: resolver is timestamp-based (`moment >= expires_at_utc` expires), so the
#: runtime clock is frozen at its lookup location instead of letting a pinned
#: absolute window drift past the machine's date.
FROZEN_NOW = NOW


class _FrozenDatetime(datetime):
    """`datetime` whose ``now``/``utcnow`` report the pinned module instant.

    Everything else - parsing, arithmetic, comparison - is inherited unchanged,
    so the real resolver, the real persistence, the real profile matching and
    the real multiplier calculation all keep executing.
    """

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - mirrors the stdlib signature
        if tz is None:
            return FROZEN_NOW.astimezone(timezone.utc).replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)

    @classmethod
    def utcnow(cls):  # noqa: D102 - mirrors the stdlib signature
        return FROZEN_NOW.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    """Isolate both the promotion registry and the calibration profile.

    The runtime clock is also pinned at its lookup location
    (`trade_decision_intelligence.datetime`), so an approved-and-effective
    promotion is evaluated at the module's fixed instant rather than at the
    machine's date. The clock used for *boundary* assertions is the real
    ``datetime`` - see the expiry tests, which pass an explicit ``now`` to the
    real resolver.
    """
    monkeypatch.setenv("OPIP_LEARNING_GOVERNANCE_DIR", str(tmp_path))
    monkeypatch.setattr(
        profitability_learning, "PROFILE_FILE", tmp_path / "strategy_calibration_profile.json"
    )
    monkeypatch.setattr(
        profitability_learning, "LOCK_FILE", tmp_path / ".strategy_calibration_profile.lock"
    )
    monkeypatch.setattr(intel, "datetime", _FrozenDatetime)
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


@pytest.mark.acceptance
def test_approved_and_effective_authorizes_multiplier(learning_env):
    """ATDD-PRA-TEST-CLOCK-DETERMINISM-v1/AC-001: at the pinned instant an approved, effective promotion authorizes."""

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


# ---------------------------------------------------------------------------
# Expiry boundaries (real resolver, explicit instants; no wall clock involved)
# ---------------------------------------------------------------------------


def _frozen_at(moment):
    """A `datetime` whose `now` reports `moment`, for controlled-clock tests."""

    class _At(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102 - mirrors the stdlib signature
            if tz is None:
                return moment.astimezone(timezone.utc).replace(tzinfo=None)
            return moment.astimezone(tz)

    return _At


def _resolve_at(moment):
    """The real resolver, evaluated at an explicit instant."""
    return resolve_calibration_promotion(
        promotion=load_approved_calibration_promotion(),
        active_profile_id=profitability_learning.active_profile_id(),
        now=moment,
    )


def _save_expiring_promotion(tmp_path, *, multiplier=1.10):
    profile = _write_profile(tmp_path, {"direction:LONG": multiplier})
    save_approved_calibration_promotion(
        _approved(
            profile["profile_id"],
            approved_at_utc=PAST,
            effective_at_utc=PAST,
            expires_at_utc=FUTURE,
        )
    )
    return profile


@pytest.mark.acceptance
def test_approved_promotion_authorizes_immediately_before_expiry(learning_env):
    """ATDD-PRA-TEST-CLOCK-DETERMINISM-v1/AC-002: one second before expiry the real resolver still authorizes."""

    _save_expiring_promotion(learning_env)
    resolution = _resolve_at(FUTURE - timedelta(seconds=1))
    assert resolution.active is True
    assert resolution.reason == PROMOTION_REASON_APPROVED_AND_EFFECTIVE


@pytest.mark.acceptance
def test_approved_promotion_is_not_effective_exactly_at_expiry(learning_env):
    """ATDD-PRA-TEST-CLOCK-DETERMINISM-v1/AC-002: `moment >= expires_at_utc` expires, so the boundary instant is refused."""

    _save_expiring_promotion(learning_env)
    resolution = _resolve_at(FUTURE)
    assert resolution.active is False
    assert resolution.reason == PROMOTION_REASON_EXPIRED


@pytest.mark.acceptance
def test_approved_promotion_is_expired_after_expiry(learning_env):
    """ATDD-PRA-TEST-CLOCK-DETERMINISM-v1/AC-002: one second after expiry the real resolver refuses the promotion."""

    _save_expiring_promotion(learning_env)
    resolution = _resolve_at(FUTURE + timedelta(seconds=1))
    assert resolution.active is False
    assert resolution.reason == PROMOTION_REASON_EXPIRED


@pytest.mark.acceptance
def test_runtime_is_neutral_when_the_clock_is_past_expiry(learning_env, monkeypatch):
    """ATDD-PRA-TEST-CLOCK-DETERMINISM-v1/AC-003: controlled reproduction of the observed failure, asserted as fail-neutral.

    With the runtime clock pinned *past* the promotion's absolute expiry, an
    otherwise approved and effective promotion must yield neutral influence. This
    is exactly the condition CI hit once the fixture's absolute window lapsed;
    here it is a controlled, deterministic assertion of the correct behaviour
    rather than a date-dependent failure. The real resolver, persistence, profile
    matching and multiplier calculation all still execute.
    """

    _save_expiring_promotion(learning_env)
    monkeypatch.setattr(intel, "datetime", _frozen_at(FUTURE + timedelta(seconds=1)))
    multiplier, status = _runtime()
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == PROMOTION_REASON_EXPIRED


@pytest.mark.acceptance
def test_runtime_authorizes_at_the_pinned_instant(learning_env):
    """ATDD-PRA-TEST-CLOCK-DETERMINISM-v1/AC-001: the positive path evaluates at the pinned instant and yields the profile."""

    _save_expiring_promotion(learning_env)
    multiplier, status = _runtime()
    assert multiplier == pytest.approx(1.10)
    assert status.startswith(PROMOTION_REASON_APPROVED_AND_EFFECTIVE)

