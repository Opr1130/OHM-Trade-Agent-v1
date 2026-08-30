import json

import pytest
from datetime import datetime, timedelta, timezone

from app.jobs.build_phase3c_forward_outcomes import build_outcomes
from app.services.signal_quality_phase3c import (
    canonical_capture_coverage,
    join_point_in_time_evidence,
)


BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _snapshot(snapshot_id="S1", episode_id="E1", cohort_id="C1", cohort_size=1):
    return {
        "record_type": "CANONICAL_EPISODE_SNAPSHOT",
        "snapshot_id": snapshot_id,
        "episode_id": episode_id,
        "cohort_id": cohort_id,
        "cohort_size": cohort_size,
        "decision_status": "NOT_SCORED",
        "decision_at_utc": BASE.isoformat(),
        "symbol": "TESTUSD",
        "candidate_rank": None,
        "reference_price": 10.0,
        "liquidity_24h_usd_approx": 1_000_000.0,
        "stage": "NOT_SCORED",
        "suppressed": None,
    }


def _observation(at, price):
    return {
        "record_type": "FULL_MARKET_OBSERVATION",
        "observed_at": at.isoformat(),
        "symbol": "TESTUSD",
        "last_price": price,
        "volume_24h": 1000.0,
        "notional_24h_usd_approx": price * 1000.0,
        "high_24h": price,
        "low_24h": price,
        "lift_from_24h_low_pct": 0.0,
        "distance_from_24h_high_pct": 0.0,
    }


def test_outcome_maturation_is_append_only_and_idempotent(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        "\n".join(
            json.dumps(_observation(BASE + delta, price))
            for delta, price in (
                (timedelta(0), 10.0),
                (timedelta(minutes=15), 10.2),
                (timedelta(minutes=30), 10.3),
                (timedelta(hours=1), 10.4),
                (timedelta(hours=4), 10.6),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    first = build_outcomes(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
    )
    assert len(first) == 1
    assert first[0]["maturation_status"] == "PARTIAL_FORWARD_WINDOW"
    assert first[0]["outcome_revision"] == 1
    assert len(outcomes.read_text().splitlines()) == 1

    second = build_outcomes(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
    )
    assert second[0]["outcome_revision"] == 1
    assert len(outcomes.read_text().splitlines()) == 1

    with observations.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(_observation(BASE + timedelta(hours=24), 11.0)) + "\n"
        )

    third = build_outcomes(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
    )
    assert third[0]["maturation_status"] == "MATURE_24H"
    assert third[0]["outcome_revision"] == 2
    assert third[0]["horizon_returns_pct"]["24h"] == pytest.approx(10.0)
    assert len(outcomes.read_text().splitlines()) == 2


def test_canonical_capture_coverage_detects_missing_pair():
    snapshots = [
        {
            **_snapshot("S1", "E1", "C1", 2),
            "symbol": "AUSD",
        }
    ]
    rows = join_point_in_time_evidence(snapshots)
    coverage = canonical_capture_coverage(rows)
    assert coverage["canonical_cohorts"] == 1
    assert coverage["expected_episode_rows"] == 2
    assert coverage["captured_unique_episode_rows"] == 1
    assert coverage["coverage"] == 0.5
    assert coverage["meets_target"] is False


def test_outcome_maturation_repairs_only_truncated_final_record(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n",
        encoding="utf-8",
    )

    build_outcomes(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
    )
    valid_first = outcomes.read_bytes()
    assert valid_first.endswith(b"\n")

    with outcomes.open("ab") as handle:
        handle.write(b'{"snapshot_id":"BROKEN"')

    rebuilt = build_outcomes(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
    )
    assert len(rebuilt) == 1
    payload = outcomes.read_bytes()
    assert payload == valid_first
    assert b"BROKEN" not in payload
