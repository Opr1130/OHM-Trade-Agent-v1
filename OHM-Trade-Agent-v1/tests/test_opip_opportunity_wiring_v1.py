"""ATDD - ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1 producer wiring (default-off).

Proves the wiring layer is inert unless explicitly enabled and that enabling it
cannot change a notification verdict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.services import ai_advisory_isolation as ai
from app.services import opportunity_evidence as oe
from app.services import opportunity_pipeline_wiring as wiring
from app.services import protection_health as ph

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def _off():
    return SimpleNamespace()


def _on(**overrides):
    base = dict(
        opip_opportunity_evidence_enabled=True,
        opip_ai_advisory_isolation_enabled=True,
        opip_protection_stamp_publish_enabled=True,
        opip_protection_stamp_max_freshness_seconds=300.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fields():
    return dict(
        symbol="DOTUSD", venue="KRAKEN", instrument="DOTUSD",
        observed_at_utc=NOW.isoformat(), episode_id="ep-1",
        depth_status="PASS", weaker_side_depth_usd=850_000.0,
        order_notional_usd=2_500.0, admission_decision="SUPPRESSED",
        suppression_reason="CONFIDENCE_NOT_CALIBRATED",
    )


def test_wiring_is_inert_when_flags_are_off(tmp_path):
    path = tmp_path / "evidence.jsonl"
    assert wiring.record_finalist_evidence(_off(), evidence_path_override=path,
                                           **_fields()) is False
    assert not path.exists()
    assert wiring.publish_stamp_if_enabled(
        _off(), release_sha=SHA, policy_version="v1", observed_at=NOW,
        admissions_suspended=False, coverage_complete=True,
        incidents_healthy=True, reasons=[],
        stamp_path=tmp_path / "s.json", history_path=tmp_path / "h.json",
    ) is None
    assert not (tmp_path / "s.json").exists()


def test_evidence_writes_once_when_enabled(tmp_path):
    path = tmp_path / "evidence.jsonl"
    assert wiring.record_finalist_evidence(_on(), evidence_path_override=path,
                                           **_fields()) is True
    assert wiring.record_finalist_evidence(_on(), evidence_path_override=path,
                                           **_fields()) is False
    rows = oe.read_records(path)
    assert len(rows) == 1


def test_advisory_annotation_never_drops_or_reorders_candidates():
    candidates = ["c1", "c2", "c3"]
    assert wiring.annotate_candidates(_off(), candidates, ai_status="UNAVAILABLE") == (
        "c1", "c2", "c3",
    )
    annotated = wiring.annotate_candidates(
        _on(), candidates, ai_status=ai.AI_STATUS_UNAVAILABLE
    )
    assert [item.candidate for item in annotated] == candidates
    assert all(item.advisory_reason == ai.REASON_AI_UNAVAILABLE for item in annotated)


def test_stamp_publication_bootstraps_fail_closed(tmp_path):
    payload = wiring.publish_stamp_if_enabled(
        _on(), release_sha=SHA, policy_version="v1", observed_at=NOW,
        admissions_suspended=False, coverage_complete=True,
        incidents_healthy=True, reasons=[],
        stamp_path=tmp_path / "s.json", history_path=tmp_path / "h.json",
    )
    assert payload is not None
    # an observation without an explicit F11 status must not establish a cadence sample
    assert payload["sample_count"] == 0
    assert ph.read_admission_stamp(payload, now=NOW, running_release_sha=SHA).usable is False


def test_evidence_capture_failure_never_propagates(tmp_path):
    class Boom:
        pass

    assert wiring.record_finalist_evidence(
        _on(), evidence_path_override=tmp_path / "x.jsonl", symbol="DOTUSD",
    ) is False  # missing required fields -> refused, never raised


def test_stamp_from_observation_maps_a_nested_report(tmp_path):
    """AC-115: the publisher serialises the verdict the observation already holds."""
    observation = SimpleNamespace(
        report={"protection": {"admissions_suspended": True,
                               "coverage_complete": False,
                               "reason_codes": ["UNMANAGED_EXPOSURE"]}},
        incidents_healthy=True,
    )
    payload = wiring.publish_stamp_from_observation(
        _on(), observation, release_sha=SHA, policy_version="v1", observed_at=NOW,
    )
    assert payload is not None
    assert payload["admissions_suspended"] is True
    assert payload["coverage_complete"] is False
    assert payload["reasons"] == ["UNMANAGED_EXPOSURE"]


def test_stamp_from_observation_is_fail_closed_without_a_verdict():
    """A missing verdict must never publish an admission-safe stamp."""
    observation = SimpleNamespace(report={}, incidents_healthy=None)
    payload = wiring.publish_stamp_from_observation(
        _on(), observation, release_sha=SHA, policy_version="v1", observed_at=NOW,
    )
    assert payload is not None
    assert payload["admissions_suspended"] is True


def test_stamp_from_observation_is_inert_when_disabled():
    observation = SimpleNamespace(report={"admissions_suspended": False},
                                  incidents_healthy=True)
    assert wiring.publish_stamp_from_observation(
        _off(), observation, release_sha=SHA, policy_version="v1",
    ) is None

# ---------------------------------------------------------------------------
# Real F11 status semantics: a HEALTHY observation must survive the round trip.
# ---------------------------------------------------------------------------
def _healthy_report():
    return {
        "state": "HEALTHY",
        "admissions_suspended": False,
        "coverage_complete": True,
        "reason_codes": ["PROTECTION_PROVEN"],
    }


def _publish_observation(tmp_path, report=None, incidents=True):
    observation = SimpleNamespace(
        report=_healthy_report() if report is None else report,
        incidents_healthy=incidents,
    )
    args = dict(
        stamp_path=tmp_path / "stamp.json",
        history_path=tmp_path / "history.json",
        release_sha=SHA,
        policy_version="opip-actionable-only-v1",
        observed_at=NOW,
        admissions_suspended=bool((report if report is not None else _healthy_report()).get("admissions_suspended")),
        coverage_complete=(report if report is not None else _healthy_report()).get("coverage_complete"),
        incidents_healthy=incidents,
        reasons=(report if report is not None else _healthy_report()).get("reason_codes") or [],
        state=(report if report is not None else _healthy_report()).get("state"),
    )
    return wiring.publish_stamp_if_enabled(_on(), **args)


def _seed_and_read(tmp_path, report=None, incidents=True, now=None):
    payload = None
    for index in range(25):
        payload = wiring.publish_stamp_if_enabled(
            _on(),
            stamp_path=tmp_path / "stamp.json",
            history_path=tmp_path / "history.json",
            release_sha=SHA,
            policy_version="opip-actionable-only-v1",
            observed_at=NOW - timedelta(seconds=30 * (24 - index)),
            admissions_suspended=bool((report if report is not None else _healthy_report()).get("admissions_suspended")),
            coverage_complete=(report if report is not None else _healthy_report()).get("coverage_complete"),
            incidents_healthy=incidents,
            reasons=(report if report is not None else _healthy_report()).get("reason_codes") or [],
            state=(report if report is not None else _healthy_report()).get("state"),
        )
    return ph.read_admission_stamp(
        payload, now=now or NOW, running_release_sha=SHA
    )


def test_healthy_observation_survives_the_full_stamp_round_trip(tmp_path):
    """PROTECTION_PROVEN is informational; a healthy stamp must be usable."""
    stamp = _seed_and_read(tmp_path)
    assert stamp.fresh is True
    assert stamp.admissions_suspended is False
    assert stamp.usable is True


def test_unsafe_observation_is_refused(tmp_path):
    report = {"state": "UNSAFE", "admissions_suspended": True,
              "coverage_complete": True, "reason_codes": ["UNMANAGED_EXPOSURE"]}
    stamp = _seed_and_read(tmp_path, report=report)
    assert stamp.usable is False


def test_unavailable_observation_is_refused(tmp_path):
    report = {"state": "UNAVAILABLE", "admissions_suspended": True,
              "coverage_complete": False, "reason_codes": []}
    stamp = _seed_and_read(tmp_path, report=report)
    assert stamp.usable is False


def test_healthy_state_contradicted_by_evidence_is_refused(tmp_path):
    """HEALTHY with a real problem code, or with a missing proof, fails closed."""
    contradictory = {"state": "HEALTHY", "admissions_suspended": False,
                     "coverage_complete": True,
                     "reason_codes": ["PROTECTION_PROVEN", "UNMANAGED_EXPOSURE"]}
    assert _seed_and_read(tmp_path, report=contradictory).usable is False

    uncovered = {"state": "HEALTHY", "admissions_suspended": False,
                 "coverage_complete": False,
                 "reason_codes": ["PROTECTION_PROVEN"]}
    assert _seed_and_read(tmp_path, report=uncovered).usable is False


def test_missing_or_malformed_state_is_refused(tmp_path):
    for report in ({}, {"admissions_suspended": False},
                   {"state": "BANANA", "admissions_suspended": False,
                    "coverage_complete": True, "reason_codes": []}):
        assert _seed_and_read(tmp_path, report=report).usable is False


# ---------------------------------------------------------------------------
# Defect 2: evidence must not depend on AI-qualified survivors.
# ---------------------------------------------------------------------------
def test_ai_failure_cannot_remove_pre_ai_finalist_evidence(tmp_path):
    """Deterministic evidence is captured before AI filtering and survives it."""
    path = tmp_path / "evidence.jsonl"
    written = wiring.record_finalist_evidence(
        _on(opip_ai_advisory_isolation_enabled=False),
        evidence_path_override=path,
        symbol="DOTUSD", venue="KRAKEN", instrument="DOTUSD",
        observed_at_utc=NOW.isoformat(), episode_id="scan:1:DOTUSD:LONG",
        scan_id="scan:1", depth_status=None, weaker_side_depth_usd=None,
        order_notional_usd=None, admission_decision="PRE_AI_FINALIST",
    )
    assert written is True
    rows = oe.read_records(path)
    assert len(rows) == 1
    assert rows[0]["admission_decision"] == "PRE_AI_FINALIST"


def test_annotation_flag_is_independent_of_the_evidence_flag():
    """Neither feature may accidentally depend on the other's configuration."""
    evidence_only = SimpleNamespace(opip_opportunity_evidence_enabled=True)
    annotated = wiring.annotate_candidates(
        evidence_only, ["c1"], ai_status=ai.AI_STATUS_UNAVAILABLE
    )
    assert annotated == ("c1",)  # unchanged: annotation flag is off

    annotation_only = SimpleNamespace(opip_ai_advisory_isolation_enabled=True)
    assert wiring.evidence_enabled(annotation_only) is False
    wrapped = wiring.annotate_candidates(
        annotation_only, ["c1"], ai_status=ai.AI_STATUS_UNAVAILABLE
    )
    assert wrapped[0].advisory_reason == ai.REASON_AI_UNAVAILABLE


def test_evidence_capture_precedes_ai_review_in_the_scan_path():
    """The recorder must run before `review_candidates`, or AI can erase it."""
    source = Path("app/jobs/scan_opportunities.py").read_text(encoding="utf-8")
    capture = source.index("pre-AI opportunity evidence")
    review = source.index("review = review_candidates(")
    assert capture < review, "evidence capture must precede the AI review"
    assert "PRE_AI_FINALIST" in source
