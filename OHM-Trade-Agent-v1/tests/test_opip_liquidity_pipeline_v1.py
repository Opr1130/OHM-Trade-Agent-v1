"""ATDD - ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1 slices P1, P2-publisher and P5.

Pure contract tests over injected clocks and temp directories: no network, no
real sleeps, no Telegram, no AI, no registry writes outside tmp_path.
"""

from __future__ import annotations
import pytest

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import liquidity_verification as lv
from app.services import opportunity_evidence as oe
from app.services import protection_health as ph

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
SHA = "a" * 40


# --------------------------------------------------------------------- P5
def _obs(value, *, status="PASS", venue="KRAKEN", instrument="DOTUSD"):
    return lv.DepthObservation(NOW, value, status, venue, instrument)


def test_p5_persisting_depth_is_stable():
    verdict = lv.assess_depth_stability([_obs(900_000), _obs(880_000), _obs(910_000)])
    assert verdict.status == lv.STABLE
    assert verdict.minimum_weaker_side_usd == 880_000


@pytest.mark.acceptance
def test_p5_vanishing_liquidity_cannot_qualify():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-113.

    The proof obligation: apparent liquidity that vanishes must be refused."""
    verdict = lv.assess_depth_stability([_obs(900_000), _obs(150_000), _obs(890_000)])
    assert verdict.status == lv.VANISHING
    assert verdict.reason == lv.REASON_LIQUIDITY_VANISHING
    assert verdict.stable is False


def test_p5_a_large_drop_refuses_even_above_threshold():
    verdict = lv.assess_depth_stability(
        [_obs(2_000_000), _obs(700_000), _obs(900_000)], max_drop_pct=35.0
    )
    assert verdict.status == lv.VANISHING


def test_p5_too_few_observations_is_unknown():
    verdict = lv.assess_depth_stability([_obs(900_000), _obs(900_000)])
    assert verdict.status == lv.UNKNOWN
    assert verdict.reason == lv.REASON_STABILITY_UNKNOWN


def test_p5_non_pass_observation_or_mixed_identity_refuses():
    assert lv.assess_depth_stability(
        [_obs(9e5), _obs(9e5, status="UNKNOWN"), _obs(9e5)]
    ).status == lv.UNKNOWN
    assert lv.assess_depth_stability(
        [_obs(9e5), _obs(9e5, instrument="ETHUSD"), _obs(9e5)]
    ).reason == lv.REASON_INCONSISTENT_IDENTITY


def test_p5_deadline_prevents_unbounded_waiting():
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds

    def monotonic():
        return clock["t"]

    verdict = lv.collect_depth_observations(
        lambda: _obs(900_000), required=3, spacing_seconds=30.0,
        deadline_seconds=45.0, sleep=sleep, monotonic=monotonic,
    )
    assert verdict.status == lv.UNKNOWN
    assert verdict.reason == lv.REASON_DEADLINE_EXCEEDED


def test_p5_full_sequence_without_waiting_succeeds():
    clock = {"t": 0.0}
    verdict = lv.collect_depth_observations(
        lambda: _obs(900_000), required=3, spacing_seconds=10.0,
        deadline_seconds=45.0,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        monotonic=lambda: clock["t"],
    )
    assert verdict.status == lv.STABLE


def test_p5_a_failing_measurement_refuses():
    def boom():
        raise RuntimeError("venue down")

    verdict = lv.collect_depth_observations(
        boom, required=1, deadline_seconds=10.0,
        sleep=lambda s: None, monotonic=lambda: 0.0,
    )
    assert verdict.status == lv.UNKNOWN
    assert verdict.reason == lv.REASON_OBSERVATION_FAILED


# --------------------------------------------------------------------- P1
def _evidence(**overrides):
    fields = dict(
        symbol="DOTUSD", venue="KRAKEN", instrument="DOTUSD",
        observed_at_utc=NOW.isoformat(), episode_id="ep-1",
        scan_id="scan-1", policy_version="opip-actionable-only-v1",
        depth_status="PASS", weaker_side_depth_usd=850_000.0,
        order_notional_usd=2_500.0, admission_decision="SUPPRESSED",
        suppression_reason="CONFIDENCE_NOT_CALIBRATED",
    )
    fields.update(overrides)
    return oe.OpportunityEvidence(**fields)


@pytest.mark.acceptance
def test_p1_a_finalist_evaluation_produces_a_valid_record(tmp_path):
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-114.

    A finalist evaluation yields a complete, valid evidence record."""
    path = tmp_path / "evidence.jsonl"
    assert oe.append_record(path, _evidence()) is True
    rows = oe.read_records(path)
    assert len(rows) == 1
    assert oe.missing_required(rows[0]) == ()
    assert rows[0]["record_type"] == oe.RECORD_TYPE


def test_p1_capture_is_idempotent(tmp_path):
    path = tmp_path / "evidence.jsonl"
    assert oe.append_record(path, _evidence()) is True
    assert oe.append_record(path, _evidence()) is False
    assert len(oe.read_records(path)) == 1


def test_p1_no_future_outcome_may_be_recorded():
    """A record carrying any future-derived field is rejected outright."""
    import pytest

    for field in oe.FORBIDDEN_OUTCOME_FIELDS:
        with pytest.raises(oe.OutcomeLeakageError):
            oe.assert_no_leakage({field: 1.0})
    oe.assert_no_leakage({"symbol": "DOTUSD"})


def test_p1_record_contains_only_decision_time_fields():
    record = oe.build_record(_evidence())
    for forbidden in oe.FORBIDDEN_OUTCOME_FIELDS:
        assert forbidden not in record
    assert "idempotency_key" in record

# --------------------------------------------------------------------- P2 publisher
from app.services import ai_advisory_isolation as ai  # noqa: E402



def _publish(tmp_path, observed_at, **overrides):
    args = dict(
        stamp_path=tmp_path / "stamp.json",
        history_path=tmp_path / "history.json",
        release_sha=SHA, policy_version="f11-v1", observed_at=observed_at,
        admissions_suspended=False, coverage_complete=True,
        incidents_healthy=True, reasons=[],
    )
    args.update(overrides)
    return ph.publish_admission_stamp(**args)


def test_p2_publisher_bootstraps_and_reader_fails_closed(tmp_path):
    """Fewer than 20 observations must never read as permission."""
    payload = _publish(tmp_path, NOW)
    assert payload["sample_count"] == 1
    stamp = ph.read_admission_stamp(payload, now=NOW, running_release_sha=SHA)
    assert stamp.usable is False


@pytest.mark.acceptance
def test_p2_derived_bound_tightens_but_never_widens(tmp_path):
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-115.

    The configured maximum stays authoritative; the derived p99 only tightens it."""
    last = None
    for index in range(25):
        last = _publish(tmp_path, NOW + timedelta(seconds=30 * index))
    assert last["sample_count"] == 25
    assert last["freshness_bound_s"] == 60.0          # 2 x p99(30s), below the 300s cap
    wide = _publish(tmp_path, NOW + timedelta(seconds=30 * 25),
                    max_freshness_seconds=10.0)
    assert wide["freshness_bound_s"] == 10.0          # configured cap still authoritative


def test_p2_publisher_is_atomic_and_leaves_no_temp_file(tmp_path):
    _publish(tmp_path, NOW)
    assert (tmp_path / "stamp.json").is_file()
    assert not (tmp_path / "stamp.json.tmp").exists()
    json.loads((tmp_path / "stamp.json").read_text(encoding="utf-8"))


def test_p2_only_successful_observations_enter_the_history(tmp_path):
    _publish(tmp_path, NOW, coverage_complete=False)
    payload = _publish(tmp_path, NOW + timedelta(seconds=30))
    assert payload["sample_count"] == 1
    # the unhealthy observation is not counted, and the reader still refuses
    stamp = ph.read_admission_stamp(payload, now=NOW + timedelta(seconds=30), running_release_sha=SHA)
    assert stamp.usable is False


# --------------------------------------------------------------------- P6 AI
@pytest.mark.acceptance
def test_p6_ai_failure_preserves_every_deterministic_candidate():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-116.

    An AI outage removes no deterministic candidate."""
    annotated = ai.attach_advisory(
        ["c1", "c2", "c3"], ai.annotation_for_status(ai.AI_STATUS_UNAVAILABLE)
    )
    assert [item.candidate for item in annotated] == ["c1", "c2", "c3"]
    assert all(item.ai_available is False for item in annotated)
    assert all(item.advisory_reason == ai.REASON_AI_UNAVAILABLE for item in annotated)


def test_p6_each_failure_mode_has_a_distinct_reason():
    for status, reason in (
        (ai.AI_STATUS_TIMEOUT, ai.REASON_AI_TIMEOUT),
        (ai.AI_STATUS_BUDGET_EXHAUSTED, ai.REASON_AI_BUDGET_EXHAUSTED),
        (ai.AI_STATUS_ERROR, ai.REASON_AI_ERROR),
        (ai.AI_STATUS_UNAVAILABLE, ai.REASON_AI_UNAVAILABLE),
    ):
        annotation = ai.annotation_for_status(status)
        assert annotation.reason_code == reason
        assert ai.advisory_cannot_admit(annotation) is True


def test_p6_an_ai_opinion_can_never_admit():
    annotation = ai.annotation_for_status(
        ai.AI_STATUS_OK, recommendation="BUY", confidence_score=95.0
    )
    # available, but it still carries no admission authority and no probability
    assert annotation.available is True
    assert not hasattr(annotation, "calibrated_probability")
    assert annotation.reason_code is None
