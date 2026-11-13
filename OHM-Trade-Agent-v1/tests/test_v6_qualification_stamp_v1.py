"""Acceptance checks for the O'Pip v6 qualification record stamp (v6 slice D).

Slice A named the version block (strategy, model, gate policy fingerprint); this
slice labels new qualification records with it. The stamp is additive: it never
overwrites a value a record already carries, it never changes a gate, threshold
or decision, and records written before the stamp existed keep loading exactly
as they were recorded.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.opip.decision import store
from app.opip.decision.funnel import QualificationFunnel
from app.opip.decision.summary import build_scan_summary
from app.opip.decision.versioning import gate_policy_fingerprint, version_stamp

STAMP_KEYS = (
    "schema_version",
    "strategy_version",
    "intelligence_version",
    "gate_policy_version",
    "gate_policy_fingerprint",
    "feature_schema_version",
    "model_version",
)

SCREENING_ROW = {
    "record_type": "OPIP_SCREENING_EVALUATION",
    "scan_id": "OPIPS:stamp",
    "venue_instrument_id": "XXBTZUSD",
    "observed_at": "2026-11-12T12:00:00+00:00",
    "outcome": "ADVANCED",
}

MILESTONE_ROW = {
    "record_type": "OPIP_EARLY_TIMING_MILESTONE",
    "scan_id": "OPIPS:stamp",
    "candidate_id": "OPIP-CAND-1",
    "milestone": "FIRST_SEEN",
    "observed_at": "2026-11-12T12:00:00+00:00",
}

LEGACY_SCREENING_ROW = {
    "record_type": "OPIP_SCREENING_EVALUATION",
    "scan_id": "OPIPS:legacy",
    "venue_instrument_id": "XXBTZUSD",
    "observed_at": "2026-11-01T00:00:00+00:00",
    "outcome": "REJECTED",
}

LEGACY_SUMMARY_ROW = {
    "record_type": "OPIP_QUALIFICATION_SUMMARY",
    "scan": {"scan_id": "OPIPS:legacy", "decision_at_utc": "2026-11-01T00:00:00+00:00"},
    "funnel": {"entered": 0, "qualified": 0},
    "terminal": {"deepest_stage_reached": "OVERVIEW"},
}

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def _funnel() -> QualificationFunnel:
    funnel = QualificationFunnel(
        scan_id="OPIPS:stamp",
        decision_at=NOW,
        cohort_id="C1",
    )
    funnel.register(
        symbol="XXBTZUSD",
        direction="LONG",
        asset="XBT",
        pair="XXBTZUSD",
        episode_id="OPIP-EP-1",
    )
    return funnel


@pytest.mark.acceptance
def test_written_qualification_records_carry_the_version_stamp(tmp_path) -> None:
    """ATDD-OPIP-V6-QUALIFICATION-STAMP-v1/AC-001"""
    stamp = version_stamp()
    screening_path = tmp_path / "screening_evaluations.jsonl"
    milestones_path = tmp_path / "early_timing_milestones.jsonl"
    funnel_path = tmp_path / "funnel_events.jsonl"
    summaries_path = tmp_path / "scan_summaries.jsonl"
    dead_letter_path = tmp_path / "dead_letter.jsonl"

    funnel = _funnel()

    # 1. Screening evaluations: written by the scanners, stamped by the writer.
    assert (
        store.append_screening_evaluations(
            [dict(SCREENING_ROW)], path=screening_path, enabled=True
        )
        == 1
    )
    screening_row = store.read_jsonl(screening_path)[0]
    for key in STAMP_KEYS:
        assert key in screening_row, key
        assert screening_row[key] == stamp[key], key
    # The stamp is additive: the measured payload is untouched.
    for key, value in SCREENING_ROW.items():
        assert screening_row[key] == value, key
    assert screening_row["gate_policy_fingerprint"] == gate_policy_fingerprint()
    assert screening_row["model_version"] is None

    # 2. Early timing milestones: the second stream without a stamping builder.
    assert (
        store.append_early_timing_milestones(
            [dict(MILESTONE_ROW)], path=milestones_path, enabled=True
        )
        == 1
    )
    milestone_row = store.read_jsonl(milestones_path)[0]
    for key in STAMP_KEYS:
        assert milestone_row[key] == stamp[key], key
    for key, value in MILESTONE_ROW.items():
        assert milestone_row[key] == value, key

    # 3. Funnel rows and scan summaries built by the funnel already stamp
    #    themselves; persisting them keeps exactly one stamp block.
    assert (
        store.append_funnel_events(
            funnel.funnel_events(),
            path=funnel_path,
            dead_letter_path=dead_letter_path,
            enabled=True,
        )
        == 1
    )
    for row in store.read_jsonl(funnel_path):
        for key in STAMP_KEYS:
            assert row[key] == stamp[key], key

    summary = build_scan_summary(funnel, scan_context={"analyzed": 1})
    assert (
        store.append_scan_summary(
            summary,
            path=summaries_path,
            dead_letter_path=dead_letter_path,
            enabled=True,
        )
        == 1
    )
    summary_row = store.read_latest_scan_summary(path=summaries_path)
    assert summary_row is not None
    for key in STAMP_KEYS:
        assert summary_row[key] == stamp[key], key

    # 4. A value the caller already captured wins over the live stamp, so
    #    replayed history is labelled with the policy that produced it.
    historical = dict(SCREENING_ROW)
    historical.update(
        {
            "scan_id": "OPIPS:historical",
            "strategy_version": "OPIP-STRATEGY-V0",
            "gate_policy_fingerprint": "GPF:0000000000000000",
        }
    )
    assert (
        store.append_screening_evaluations(
            [historical], path=screening_path, enabled=True
        )
        == 1
    )
    replayed = store.read_jsonl(screening_path)[-1]
    assert replayed["strategy_version"] == "OPIP-STRATEGY-V0"
    assert replayed["gate_policy_fingerprint"] == "GPF:0000000000000000"
    # Everything the caller did not set is filled in additively.
    assert replayed["model_version"] is None
    assert replayed["schema_version"] == stamp["schema_version"]
    # The qualification record itself is unchanged by stamping.
    assert replayed["outcome"] == SCREENING_ROW["outcome"]


@pytest.mark.acceptance
def test_records_written_without_the_stamp_still_load(tmp_path) -> None:
    """ATDD-OPIP-V6-QUALIFICATION-STAMP-v1/AC-002"""
    screening_path = tmp_path / "screening_evaluations.jsonl"
    summaries_path = tmp_path / "scan_summaries.jsonl"
    screening_path.write_text(
        json.dumps(LEGACY_SCREENING_ROW, sort_keys=True) + "\n", encoding="utf-8"
    )
    summaries_path.write_text(
        json.dumps(LEGACY_SUMMARY_ROW, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Legacy rows load exactly as recorded: no field is injected on read.
    assert store.read_jsonl(screening_path) == [LEGACY_SCREENING_ROW]
    for key in STAMP_KEYS:
        assert key not in store.read_jsonl(screening_path)[0], key
    assert store.read_latest_scan_summary(path=summaries_path) == LEGACY_SUMMARY_ROW
    assert store.read_screening_evaluations_for_scan(
        "OPIPS:legacy", path=screening_path
    ) == [LEGACY_SCREENING_ROW]
    assert store.read_funnel_events_for_scan(
        "OPIPS:legacy", path=tmp_path / "absent.jsonl"
    ) == []

    # A new stamped record appended after a legacy row is additive: both rows
    # load, the old one keeps its original shape and the new one is labelled.
    assert (
        store.append_screening_evaluations(
            [dict(SCREENING_ROW)], path=screening_path, enabled=True
        )
        == 1
    )
    rows = store.read_jsonl(screening_path)
    assert len(rows) == 2
    assert rows[0] == LEGACY_SCREENING_ROW
    assert rows[1]["scan_id"] == SCREENING_ROW["scan_id"]
    assert rows[1]["gate_policy_fingerprint"] == gate_policy_fingerprint()
