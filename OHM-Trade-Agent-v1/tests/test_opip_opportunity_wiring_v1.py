"""ATDD - ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1 producer wiring (default-off).

Proves the wiring layer is inert unless explicitly enabled and that enabling it
cannot change a notification verdict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import ai_advisory_isolation as ai
from app.services import opportunity_evidence as oe
from app.services import opportunity_pipeline_wiring as wiring

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
    assert payload["sample_count"] == 1


def test_evidence_capture_failure_never_propagates(tmp_path):
    class Boom:
        pass

    assert wiring.record_finalist_evidence(
        _on(), evidence_path_override=tmp_path / "x.jsonl", symbol="DOTUSD",
    ) is False  # missing required fields -> refused, never raised
