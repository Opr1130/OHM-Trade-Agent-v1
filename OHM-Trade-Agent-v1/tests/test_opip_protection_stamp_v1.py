"""ATDD - ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1 slice P2 (F11 admission stamp).

Pure contract tests: no filesystem, no clock of their own, no authority.
"""

from __future__ import annotations

import pytest

from datetime import datetime, timedelta, timezone

from app.services import protection_health as ph


NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def _payload(**overrides):
    base = dict(
        release_sha=SHA, policy_version="f11-v1",
        observed_at=NOW - timedelta(seconds=10), sequence=5,
        admissions_suspended=False, coverage_complete=True,
        incidents_healthy=True, reasons=[], freshness_bound_s=300.0,
        sample_count=25,
    )
    base.update(overrides)
    return ph.build_admission_stamp_payload(**base)


def _read(payload=None, **kwargs):
    args = dict(now=NOW, running_release_sha=SHA)
    args.update(kwargs)
    return ph.read_admission_stamp(payload, **args)


def test_f11_bound_is_derived_not_hardcoded():
    """Correction 3: the bound comes from observed successful intervals."""
    assert ph.derive_freshness_bound([]) is None
    times = [NOW + timedelta(seconds=60 * i) for i in range(25)]
    assert ph.derive_freshness_bound(times) == 120.0
    wider = [NOW + timedelta(seconds=300 * i) for i in range(25)]
    assert ph.derive_freshness_bound(wider) == 600.0


@pytest.mark.acceptance
def test_f11_below_min_samples_is_unproven():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-110.

    Twenty samples are a preliminary baseline, not a reliable p99."""
    times = [NOW + timedelta(seconds=60 * i) for i in range(ph.STAMP_MIN_SAMPLES - 1)]
    assert ph.derive_freshness_bound(times) is None
    stamp = _read(_payload(sample_count=ph.STAMP_MIN_SAMPLES - 1))
    assert stamp.fresh is False and stamp.admissions_suspended is True


def test_f1_fresh_healthy_stamp_is_usable():
    stamp = _read(_payload())
    assert stamp.fresh is True
    assert stamp.admissions_suspended is False
    assert stamp.usable is True


def test_f2_missing_or_unreadable_stamp_fails_closed():
    for payload in (None, {}, "not a mapping", 123):
        stamp = _read(payload)
        assert stamp.fresh is False
        assert stamp.admissions_suspended is True
        assert stamp.usable is False


def test_f3_stale_stamp_fails_closed():
    stamp = _read(_payload(), now=NOW + timedelta(seconds=400))
    assert stamp.fresh is False and stamp.usable is False


def test_ec09_a_future_observation_fails_closed():
    stamp = _read(_payload(observed_at=NOW + timedelta(seconds=60)))
    assert stamp.fresh is False and stamp.usable is False


def test_f4_f5_corrupt_or_mismatched_stamps_fail_closed():
    assert _read(_payload(contract_version="other-v9")).usable is False
    assert _read(_payload(), running_release_sha="b" * 40).usable is False
    assert _read(_payload(observed_at=NOW.replace(tzinfo=None))).usable is False
    assert _read(_payload(freshness_bound_s=0)).usable is False
    assert _read(_payload(freshness_bound_s=ph.STAMP_ABSOLUTE_CEILING_SECONDS + 1)).usable is False


def test_ec11_sequence_regression_fails_closed():
    stamp = _read(_payload(sequence=4), previous_sequence=5)
    assert stamp.usable is False
    assert _read(_payload(sequence=6), previous_sequence=5).usable is True


def test_f11_unhealthy_observation_refuses_even_when_unsuspended():
    for overrides in ({"coverage_complete": False},
                      {"incidents_healthy": False},
                      {"incidents_healthy": None},
                      {"reasons": ["UNMANAGED_EXPOSURE"]}):
        stamp = _read(_payload(**overrides))
        assert stamp.admissions_suspended is True, overrides
        assert stamp.usable is False


def test_f1_suspension_is_reported_as_suspended():
    stamp = _read(_payload(admissions_suspended=True))
    assert stamp.fresh is True
    assert stamp.admissions_suspended is True
    assert stamp.reason == ph.REASON_STAMP_SUSPENDED
