import json

import pytest
from datetime import datetime, timedelta, timezone

from app.jobs.build_phase3c_forward_outcomes import (
    acknowledge_accountability_outcomes,
    build_outcomes,
    build_outcomes_bounded,
    pending_accountability_outcomes,
)
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



def test_outcome_maturation_preserves_valid_final_record_without_newline(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n",
        encoding="utf-8",
    )

    first = build_outcomes(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
    )
    assert len(first) == 1

    valid = outcomes.read_bytes()
    assert valid.endswith(b"\n")
    outcomes.write_bytes(valid[:-1])

    rebuilt = build_outcomes(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
    )
    assert len(rebuilt) == 1
    assert rebuilt[0]["outcome_record_id"] == first[0]["outcome_record_id"]
    assert outcomes.read_bytes() == valid


def test_bounded_outcome_maturation_processes_due_queue_in_batches(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    first_snapshot = _snapshot("S1", "E1")
    second_snapshot = {
        **_snapshot("S2", "E2"),
        "decision_at_utc": (BASE + timedelta(minutes=1)).isoformat(),
    }
    snapshots.write_text(
        json.dumps(first_snapshot) + "\n" + json.dumps(second_snapshot) + "\n",
        encoding="utf-8",
    )

    observation_rows = [
        _observation(BASE - timedelta(hours=1), 9.8),
        _observation(BASE, 10.0),
        _observation(BASE + timedelta(minutes=15), 10.2),
        _observation(BASE + timedelta(hours=1), 10.4),
        _observation(BASE + timedelta(hours=4), 10.6),
        _observation(BASE + timedelta(hours=24), 11.0),
        _observation(BASE + timedelta(hours=25), 11.1),
    ]
    observations.write_text(
        "\n".join(json.dumps(row) for row in observation_rows) + "\n",
        encoding="utf-8",
    )

    first = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        max_snapshots=1,
        now=BASE + timedelta(hours=26),
    )
    assert len(first) == 1
    assert first[0]["snapshot_id"] == "S1"
    assert first[0]["window_complete"] is True

    second = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        max_snapshots=1,
        now=BASE + timedelta(hours=26),
    )
    assert len(second) == 1
    assert second[0]["snapshot_id"] == "S2"
    assert second[0]["window_complete"] is True

    third = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        max_snapshots=1,
        now=BASE + timedelta(hours=26),
    )
    assert third == []
    assert len(outcomes.read_text(encoding="utf-8").splitlines()) == 2


def test_bounded_outcome_handoff_replays_until_accountability_ack(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n"
        + json.dumps(_observation(BASE + timedelta(hours=24), 11.0)) + "\n",
        encoding="utf-8",
    )

    built = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=24, minutes=1),
    )
    assert len(built) == 1
    assert built[0]["window_complete"] is True

    pending = pending_accountability_outcomes(
        output_path=outcomes,
        state_path=state,
    )
    assert [row["snapshot_id"] for row in pending] == ["S1"]

    # The maturation queue has retired S1, but the independent handoff remains
    # durable until the downstream accountability write is acknowledged.
    assert build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=25),
    ) == []
    replay = pending_accountability_outcomes(
        output_path=outcomes,
        state_path=state,
    )
    assert [row["outcome_record_id"] for row in replay] == [
        built[0]["outcome_record_id"]
    ]

    assert acknowledge_accountability_outcomes(
        replay,
        output_path=outcomes,
        state_path=state,
    ) == 1
    assert pending_accountability_outcomes(
        output_path=outcomes,
        state_path=state,
    ) == []


def test_bounded_outcomes_revisit_partial_only_at_next_milestone(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n"
        + json.dumps(_observation(BASE + timedelta(minutes=15), 10.2)) + "\n",
        encoding="utf-8",
    )

    first = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(minutes=16),
    )
    assert len(first) == 1
    assert first[0]["window_complete"] is False

    # At 20m, the next useful milestone is 30m, so no repeated full replay.
    second = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(minutes=20),
    )
    assert second == []

    third = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(minutes=31),
    )
    assert len(third) == 1


def test_bounded_outcomes_normalize_naive_snapshot_timestamp_to_utc(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshot = {
        **_snapshot(),
        "decision_at_utc": BASE.replace(tzinfo=None).isoformat(),
    }
    snapshots.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n"
        + json.dumps(_observation(BASE + timedelta(hours=24), 11.0)) + "\n",
        encoding="utf-8",
    )

    rows = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=24, minutes=1),
    )
    assert len(rows) == 1
    assert rows[0]["snapshot_id"] == "S1"
    assert rows[0]["reference_at"].endswith("+00:00")


def test_bounded_outcomes_retire_terminal_incomplete_snapshot(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n",
        encoding="utf-8",
    )

    first = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=26),
    )
    assert len(first) == 1
    assert first[0]["window_complete"] is False

    second = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=27),
    )
    assert second == []


def test_bounded_snapshot_checkpoint_rejects_rewritten_ledger(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n",
        encoding="utf-8",
    )

    build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(minutes=1),
    )

    replacement = {
        **_snapshot("S-REPLACED", "E-REPLACED"),
        "padding": "x" * 512,
    }
    snapshots.write_text(
        json.dumps(replacement) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="SNAPSHOT_LEDGER_DIVERGED"):
        build_outcomes_bounded(
            snapshot_path=snapshots,
            observation_path=observations,
            output_path=outcomes,
            state_path=state,
            now=BASE + timedelta(minutes=2),
        )


def test_bounded_output_checkpoint_rejects_rewritten_ledger(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshots.write_text(json.dumps(_snapshot()) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n",
        encoding="utf-8",
    )

    first = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(minutes=1),
    )
    assert len(first) == 1

    original = json.loads(outcomes.read_text(encoding="utf-8").strip())
    replacement = {
        **original,
        "outcome_record_id": "REWRITTEN-" + str(original["outcome_record_id"]),
        "padding": "x" * 512,
    }
    outcomes.write_text(
        json.dumps(replacement, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="OUTCOME_LEDGER_DIVERGED"):
        build_outcomes_bounded(
            snapshot_path=snapshots,
            observation_path=observations,
            output_path=outcomes,
            state_path=state,
            now=BASE + timedelta(minutes=2),
        )
