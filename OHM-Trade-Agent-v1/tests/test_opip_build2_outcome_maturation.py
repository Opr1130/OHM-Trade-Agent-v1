import hashlib
import json
import sqlite3

import pytest
from datetime import datetime, timedelta, timezone

from app.jobs import build_phase3c_forward_outcomes as outcomes_job
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
from app.services.phase3c_outcomes import (
    FORWARD_OUTCOME_LABEL_SCHEMA_VERSION,
    REQUIRED_FORWARD_HORIZONS,
    outcome_label_is_current,
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



@pytest.mark.parametrize("malformed_version", [2.8, "2", True, False])
def test_outcome_label_current_rejects_coerced_schema_versions(malformed_version):
    row = {
        "label_schema_version": malformed_version,
        "horizon_returns_pct": {
            label: 0.0 for label in REQUIRED_FORWARD_HORIZONS
        },
        "horizon_observed": {
            label: True for label in REQUIRED_FORWARD_HORIZONS
        },
    }
    assert outcome_label_is_current(row) is False


def test_outcome_label_current_rejects_future_schema_versions():
    row = {
        "label_schema_version": FORWARD_OUTCOME_LABEL_SCHEMA_VERSION + 1,
        "horizon_returns_pct": {
            label: 0.0 for label in REQUIRED_FORWARD_HORIZONS
        },
        "horizon_observed": {
            label: True for label in REQUIRED_FORWARD_HORIZONS
        },
    }
    assert outcome_label_is_current(row) is False


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


def test_malformed_outcome_identity_never_enters_accountability_handoff(tmp_path):
    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    malformed = {
        **_snapshot(),
        "reference_at": BASE.isoformat(),
        "snapshot_id": "BAD-S1",
        "outcome_record_id": "",
        "outcome_revision": 1,
        "window_complete": True,
    }
    output.write_text(
        json.dumps(malformed, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert pending_accountability_outcomes(
        output_path=output,
        state_path=state,
    ) == []

    connection = sqlite3.connect(state)
    try:
        assert connection.execute(
            "SELECT count(*) FROM latest_outcomes WHERE snapshot_id = 'BAD-S1'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM accountability_handoff"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_legacy_malformed_handoff_is_skipped_and_cleaned_on_upgrade(tmp_path):
    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE latest_outcomes (
                snapshot_id TEXT PRIMARY KEY,
                outcome_record_id TEXT NOT NULL,
                outcome_revision INTEGER NOT NULL,
                window_complete INTEGER NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE accountability_handoff (
                snapshot_id TEXT PRIMARY KEY,
                outcome_record_id TEXT NOT NULL,
                outcome_revision INTEGER NOT NULL,
                reference_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE snapshot_queue (
                snapshot_id TEXT PRIMARY KEY,
                decision_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        malformed = {
            **_snapshot(),
            "reference_at": BASE.isoformat(),
            "snapshot_id": "LEGACY-BAD",
            "outcome_record_id": "",
            "outcome_revision": 2,
            "window_complete": True,
        }
        connection.execute(
            """
            INSERT INTO latest_outcomes(
                snapshot_id, outcome_record_id, outcome_revision,
                window_complete, row_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "LEGACY-BAD",
                "",
                2,
                1,
                json.dumps(malformed, sort_keys=True),
            ),
        )
        connection.execute(
            """
            INSERT INTO accountability_handoff(
                snapshot_id, outcome_record_id, outcome_revision,
                reference_at, row_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "LEGACY-BAD",
                "",
                2,
                BASE.isoformat(),
                json.dumps(malformed, sort_keys=True),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    assert pending_accountability_outcomes(
        output_path=output,
        state_path=state,
    ) == []

    connection = sqlite3.connect(state)
    try:
        assert connection.execute(
            "SELECT count(*) FROM accountability_handoff"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_legacy_handoff_backfill_is_bounded_and_resumable(tmp_path, monkeypatch):
    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    monkeypatch.setattr(
        outcomes_job,
        "ACCOUNTABILITY_HANDOFF_BACKFILL_BATCH_SIZE",
        2,
    )

    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE latest_outcomes (
                snapshot_id TEXT PRIMARY KEY,
                outcome_record_id TEXT NOT NULL,
                outcome_revision INTEGER NOT NULL,
                window_complete INTEGER NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE snapshot_queue (
                snapshot_id TEXT PRIMARY KEY,
                decision_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        legacy_ids = ("LEGACY-Z", " LEGACY-A", "LEGACY-M")
        for index, snapshot_id in enumerate(legacy_ids):
            row = {
                **_snapshot(snapshot_id, f"E{index}"),
                "reference_at": (
                    BASE + timedelta(minutes=index)
                ).isoformat(),
                "outcome_record_id": f"OUT:{snapshot_id}",
                "outcome_revision": 1,
                "window_complete": True,
            }
            connection.execute(
                """
                INSERT INTO latest_outcomes(
                    snapshot_id, outcome_record_id, outcome_revision,
                    window_complete, row_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    row["outcome_record_id"],
                    1,
                    1,
                    json.dumps(row, sort_keys=True),
                ),
            )
        connection.commit()
    finally:
        connection.close()

    first = pending_accountability_outcomes(
        output_path=output,
        state_path=state,
    )
    assert [row["snapshot_id"] for row in first] == [
        "LEGACY-Z",
        " LEGACY-A",
    ]

    connection = sqlite3.connect(state)
    try:
        metadata = dict(connection.execute(
            "SELECT key, value FROM metadata"
        ).fetchall())
        cursor = json.loads(
            metadata["accountability_handoff_backfill_cursor_v2"]
        )
        assert cursor == {
            "reference_at": (BASE + timedelta(minutes=1)).isoformat(),
            "snapshot_id": " LEGACY-A",
        }
        assert "accountability_handoff_backfill_v2" not in metadata
    finally:
        connection.close()

    assert acknowledge_accountability_outcomes(
        first,
        output_path=output,
        state_path=state,
    ) == 2

    second = pending_accountability_outcomes(
        output_path=output,
        state_path=state,
    )
    assert [row["snapshot_id"] for row in second] == ["LEGACY-M"]

    connection = sqlite3.connect(state)
    try:
        metadata = dict(connection.execute(
            "SELECT key, value FROM metadata"
        ).fetchall())
        assert metadata["accountability_handoff_backfill_v2"] == "1"
        assert "accountability_handoff_backfill_cursor_v2" not in metadata
    finally:
        connection.close()


def test_existing_outcome_state_backfills_accountability_handoff_on_upgrade(tmp_path):
    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    legacy_row = {
        **_snapshot(),
        "reference_at": BASE.isoformat(),
        "snapshot_id": "LEGACY-S1",
        "outcome_record_id": "OUT:LEGACY-S1",
        "outcome_revision": 3,
        "window_complete": True,
    }

    output_bytes = (
        json.dumps(legacy_row, sort_keys=True) + "\n"
    ).encode("utf-8")
    output.write_bytes(output_bytes)
    output_size = len(output_bytes)
    output_sha256 = hashlib.sha256(output_bytes).hexdigest()

    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE latest_outcomes (
                snapshot_id TEXT PRIMARY KEY,
                outcome_record_id TEXT NOT NULL,
                outcome_revision INTEGER NOT NULL,
                window_complete INTEGER NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE snapshot_queue (
                snapshot_id TEXT PRIMARY KEY,
                decision_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO latest_outcomes(
                snapshot_id,
                outcome_record_id,
                outcome_revision,
                window_complete,
                row_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                legacy_row["snapshot_id"],
                legacy_row["outcome_record_id"],
                legacy_row["outcome_revision"],
                1,
                json.dumps(legacy_row, sort_keys=True),
            ),
        )
        for key, value in (
            ("output_indexed_offset", str(output_size)),
            ("output_anchor_start", "0"),
            ("output_anchor_size", str(output_size)),
            ("output_anchor_sha256", output_sha256),
        ):
            connection.execute(
                """
                INSERT INTO metadata(key, value)
                VALUES (?, ?)
                """,
                (key, value),
            )
        connection.commit()
    finally:
        connection.close()

    pending = pending_accountability_outcomes(
        output_path=output,
        state_path=state,
    )
    assert [row["snapshot_id"] for row in pending] == ["LEGACY-S1"]
    assert pending[0]["outcome_record_id"] == "OUT:LEGACY-S1"


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


def test_completed_v1_outcome_requeues_for_new_12h_horizon(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshot = _snapshot()
    snapshots.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n"
        + json.dumps(_observation(BASE + timedelta(hours=12), 10.5)) + "\n"
        + json.dumps(_observation(BASE + timedelta(hours=24), 11.0)) + "\n",
        encoding="utf-8",
    )

    legacy = {
        **snapshot,
        "label_schema_version": 1,
        "reference_at": BASE.isoformat(),
        "reference_price": 10.0,
        "horizon_returns_pct": {
            "5m": None,
            "15m": None,
            "30m": None,
            "60m": None,
            "4h": None,
            "8h": None,
            "24h": 10.0,
        },
        "horizon_observed": {
            "5m": False,
            "15m": False,
            "30m": False,
            "60m": False,
            "4h": False,
            "8h": False,
            "24h": True,
        },
        "mfe_pct": 10.0,
        "mfe_at": (BASE + timedelta(hours=24)).isoformat(),
        "time_to_mfe_seconds": 24 * 60 * 60,
        "mae_pct": 0.0,
        "mae_at": (BASE + timedelta(hours=12)).isoformat(),
        "time_to_mae_seconds": 12 * 60 * 60,
        "max_adverse_excursion_pct": 0.0,
        "window_complete": True,
        "maturation_status": "MATURE_24H",
        "outcome_record_type": "FORWARD_OUTCOME_MATURATION",
        "outcome_record_id": "OUT:LEGACY-V1",
        "outcome_revision": 1,
        "append_only": True,
    }
    outcomes.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")

    rows = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=24, minutes=1),
    )

    assert len(rows) == 1
    current = rows[0]
    assert current["snapshot_id"] == "S1"
    assert current["label_schema_version"] == 2
    assert "12h" in current["horizon_returns_pct"]
    assert "12h" in current["horizon_observed"]
    assert current["outcome_revision"] == 2
    assert current["window_complete"] is True

    # Once migrated to the current schema, the completed row retires again.
    assert build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=25),
    ) == []


def test_due_snapshot_batch_never_mixes_live_and_schema_migration(tmp_path):
    state = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            CREATE TABLE snapshot_queue (
                snapshot_id TEXT PRIMARY KEY,
                decision_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        live = {
            **_snapshot("LIVE-S1", "LIVE-E1"),
            "decision_at_utc": BASE.isoformat(),
        }
        migration = {
            **_snapshot("OLD-S1", "OLD-E1"),
            "decision_at_utc": (
                BASE - timedelta(days=365)
            ).isoformat(),
            "schema_migration_only": True,
        }
        for row, queue_time in (
            (live, BASE),
            (migration, BASE + timedelta(minutes=1)),
        ):
            connection.execute(
                """
                INSERT INTO snapshot_queue(
                    snapshot_id, decision_at, next_due_at, row_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    row["snapshot_id"],
                    queue_time.isoformat(),
                    BASE.isoformat(),
                    json.dumps(row, sort_keys=True),
                ),
            )
        connection.commit()

        first = outcomes_job._due_snapshot_batch(
            connection,
            now=BASE + timedelta(minutes=2),
            limit=500,
        )
        assert [row["snapshot_id"] for row in first] == ["LIVE-S1"]

        connection.execute(
            "DELETE FROM snapshot_queue WHERE snapshot_id = 'LIVE-S1'"
        )
        connection.commit()
        second = outcomes_job._due_snapshot_batch(
            connection,
            now=BASE + timedelta(minutes=2),
            limit=500,
        )
        assert [row["snapshot_id"] for row in second] == ["OLD-S1"]
    finally:
        connection.close()


def test_stale_schema_seed_is_reference_time_local(tmp_path):
    state = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            CREATE TABLE latest_outcomes (
                snapshot_id TEXT PRIMARY KEY,
                outcome_record_id TEXT NOT NULL,
                outcome_revision INTEGER NOT NULL,
                window_complete INTEGER NOT NULL,
                reference_at TEXT NOT NULL,
                label_schema_version INTEGER NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE snapshot_queue (
                snapshot_id TEXT PRIMARY KEY,
                decision_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        for snapshot_id, at in (
            ("OLD-DAY-1", BASE - timedelta(days=365)),
            ("OLD-DAY-2", BASE - timedelta(days=360)),
        ):
            prior = {
                **_snapshot(snapshot_id, f"E-{snapshot_id}"),
                "reference_at": at.isoformat(),
                "decision_at_utc": at.isoformat(),
                "reference_price": 10.0,
                "label_schema_version": 1,
                "window_complete": True,
            }
            connection.execute(
                """
                INSERT INTO latest_outcomes(
                    snapshot_id, outcome_record_id, outcome_revision,
                    window_complete, reference_at, label_schema_version,
                    row_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    f"OUT:{snapshot_id}",
                    1,
                    1,
                    at.isoformat(),
                    1,
                    json.dumps(prior, sort_keys=True),
                ),
            )
        connection.commit()

        seeded = outcomes_job._seed_stale_schema_snapshot_queue(
            connection,
            now=BASE,
            limit=500,
        )
        assert seeded == 1
        queued = connection.execute(
            "SELECT snapshot_id, row_json FROM snapshot_queue"
        ).fetchall()
        assert [row[0] for row in queued] == ["OLD-DAY-1"]
        assert json.loads(queued[0][1])["schema_migration_only"] is True
    finally:
        connection.close()


def test_stale_schema_attempt_advances_past_exhausted_reference_window(tmp_path):
    state = tmp_path / "state.sqlite3"
    connection = outcomes_job._open_bounded_state(state)
    try:
        for snapshot_id, at in (
            ("EXHAUSTED", BASE - timedelta(days=365)),
            ("NEXT", BASE - timedelta(days=360)),
        ):
            prior = {
                **_snapshot(snapshot_id, f"E-{snapshot_id}"),
                "reference_at": at.isoformat(),
                "decision_at_utc": at.isoformat(),
                "reference_price": 10.0,
                "label_schema_version": 1,
                "window_complete": True,
                "outcome_record_id": f"OUT:{snapshot_id}",
                "outcome_revision": 1,
            }
            connection.execute(
                """
                INSERT INTO latest_outcomes(
                    snapshot_id, outcome_record_id, outcome_revision,
                    window_complete, reference_at, label_schema_version,
                    row_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    prior["outcome_record_id"],
                    1,
                    1,
                    at.isoformat(),
                    1,
                    json.dumps(prior, sort_keys=True),
                ),
            )
        connection.commit()

        assert outcomes_job._seed_stale_schema_snapshot_queue(
            connection,
            now=BASE,
            limit=500,
        ) == 1
        first = connection.execute(
            "SELECT snapshot_id FROM snapshot_queue ORDER BY snapshot_id"
        ).fetchall()
        assert first == [("EXHAUSTED",)]

        outcomes_job._record_schema_migration_attempt(
            connection,
            snapshot_id="EXHAUSTED",
            attempted_at=BASE,
            reason="NO_LABEL_FROM_RETAINED_OBSERVATIONS",
        )
        connection.execute("DELETE FROM snapshot_queue")
        connection.commit()

        assert outcomes_job._seed_stale_schema_snapshot_queue(
            connection,
            now=BASE + timedelta(minutes=1),
            limit=500,
        ) == 1
        second = connection.execute(
            "SELECT snapshot_id FROM snapshot_queue ORDER BY snapshot_id"
        ).fetchall()
        assert second == [("NEXT",)]
    finally:
        connection.close()


def test_incomplete_schema_migration_preserves_completed_latest_outcome(tmp_path):
    outcomes = tmp_path / "outcomes.jsonl"
    observations = tmp_path / "observations.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    legacy = {
        **_snapshot("LEGACY-INCOMPLETE", "E-LEGACY"),
        "label_schema_version": 1,
        "reference_at": (BASE - timedelta(days=365)).isoformat(),
        "decision_at_utc": (BASE - timedelta(days=365)).isoformat(),
        "reference_price": 10.0,
        "horizon_returns_pct": {"24h": 5.0},
        "horizon_observed": {"24h": True},
        "mfe_pct": 5.0,
        "mae_pct": -1.0,
        "window_complete": True,
        "maturation_status": "MATURE_24H",
        "outcome_record_type": "FORWARD_OUTCOME_MATURATION",
        "outcome_record_id": "OUT:LEGACY-INCOMPLETE",
        "outcome_revision": 1,
        "append_only": True,
    }
    outcomes.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
    observations.write_text("", encoding="utf-8")

    built = build_outcomes_bounded(
        snapshot_path=tmp_path / "missing-snapshots.jsonl",
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE,
    )
    assert built == []
    assert len(outcomes.read_text(encoding="utf-8").splitlines()) == 1

    connection = sqlite3.connect(state)
    try:
        latest = connection.execute(
            """
            SELECT outcome_record_id, outcome_revision, window_complete,
                   label_schema_version
            FROM latest_outcomes
            WHERE snapshot_id = 'LEGACY-INCOMPLETE'
            """
        ).fetchone()
        assert latest == ("OUT:LEGACY-INCOMPLETE", 1, 1, 1)
        attempt = connection.execute(
            """
            SELECT target_schema_version, reason
            FROM schema_migration_attempt
            WHERE snapshot_id = 'LEGACY-INCOMPLETE'
            """
        ).fetchone()
        assert attempt is not None
        assert attempt[0] == FORWARD_OUTCOME_LABEL_SCHEMA_VERSION
        assert attempt[1] in {
            "NO_LABEL_FROM_RETAINED_OBSERVATIONS",
            "INCOMPLETE_MIGRATION_LABEL",
        }
    finally:
        connection.close()

    # The exhausted row is not reseeded forever on the next cycle.
    assert build_outcomes_bounded(
        snapshot_path=tmp_path / "missing-snapshots.jsonl",
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(minutes=10),
    ) == []


def test_completed_v1_outcome_migrates_with_snapshot_cursor_already_at_eof(
    tmp_path,
):
    snapshots = tmp_path / "snapshots.jsonl"
    observations = tmp_path / "observations.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"

    snapshot = _snapshot()
    snapshot_bytes = (json.dumps(snapshot, sort_keys=True) + "\n").encode("utf-8")
    snapshots.write_bytes(snapshot_bytes)
    observations.write_text(
        json.dumps(_observation(BASE, 10.0)) + "\n"
        + json.dumps(_observation(BASE + timedelta(hours=12), 10.5)) + "\n"
        + json.dumps(_observation(BASE + timedelta(hours=24), 11.0)) + "\n",
        encoding="utf-8",
    )

    legacy = {
        **snapshot,
        "label_schema_version": 1,
        "reference_at": BASE.isoformat(),
        "reference_price": 10.0,
        "horizon_returns_pct": {
            "5m": None,
            "15m": None,
            "30m": None,
            "60m": None,
            "4h": None,
            "8h": None,
            "24h": 10.0,
        },
        "horizon_observed": {
            "5m": False,
            "15m": False,
            "30m": False,
            "60m": False,
            "4h": False,
            "8h": False,
            "24h": True,
        },
        "mfe_pct": 10.0,
        "mfe_at": (BASE + timedelta(hours=24)).isoformat(),
        "time_to_mfe_seconds": 24 * 60 * 60,
        "mae_pct": 0.0,
        "mae_at": (BASE + timedelta(hours=12)).isoformat(),
        "time_to_mae_seconds": 12 * 60 * 60,
        "max_adverse_excursion_pct": 0.0,
        "window_complete": True,
        "maturation_status": "MATURE_24H",
        "outcome_record_type": "FORWARD_OUTCOME_MATURATION",
        "outcome_record_id": "OUT:LEGACY-EOF",
        "outcome_revision": 1,
        "append_only": True,
    }
    outcome_bytes = (json.dumps(legacy, sort_keys=True) + "\n").encode("utf-8")
    outcomes.write_bytes(outcome_bytes)

    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE latest_outcomes (
                snapshot_id TEXT PRIMARY KEY,
                outcome_record_id TEXT NOT NULL,
                outcome_revision INTEGER NOT NULL,
                window_complete INTEGER NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE snapshot_queue (
                snapshot_id TEXT PRIMARY KEY,
                decision_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO latest_outcomes(
                snapshot_id, outcome_record_id, outcome_revision,
                window_complete, row_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "S1",
                legacy["outcome_record_id"],
                1,
                1,
                json.dumps(legacy, sort_keys=True),
            ),
        )

        checkpoint_values = {
            "output_indexed_offset": str(len(outcome_bytes)),
            "output_anchor_start": "0",
            "output_anchor_size": str(len(outcome_bytes)),
            "output_anchor_sha256": hashlib.sha256(outcome_bytes).hexdigest(),
            "snapshot_indexed_offset": str(len(snapshot_bytes)),
            "snapshot_anchor_start": "0",
            "snapshot_anchor_size": str(len(snapshot_bytes)),
            "snapshot_anchor_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        }
        for key, value in checkpoint_values.items():
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (key, value),
            )
        connection.commit()
    finally:
        connection.close()

    rows = build_outcomes_bounded(
        snapshot_path=snapshots,
        observation_path=observations,
        output_path=outcomes,
        state_path=state,
        now=BASE + timedelta(hours=24, minutes=1),
    )
    assert len(rows) == 1
    assert rows[0]["label_schema_version"] == 2
    assert rows[0]["horizon_returns_pct"]["12h"] == pytest.approx(5.0)
    assert rows[0]["window_complete"] is True

    connection = sqlite3.connect(state)
    try:
        metadata = dict(
            connection.execute("SELECT key, value FROM metadata").fetchall()
        )
        # Production migration is seeded from latest_outcomes; no snapshot
        # rewind is needed even though the persisted cursor remains at EOF.
        assert int(metadata["snapshot_indexed_offset"]) == len(snapshot_bytes)

        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(latest_outcomes)"
            ).fetchall()
        }
        assert {"reference_at", "label_schema_version"} <= columns

        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(latest_outcomes)"
            ).fetchall()
        }
        assert "idx_latest_outcomes_reference" in indexes
        assert "idx_latest_outcomes_schema_reference" in indexes

        latest = connection.execute(
            """
            SELECT reference_at, label_schema_version
            FROM latest_outcomes
            WHERE snapshot_id = 'S1'
            """
        ).fetchone()
        assert latest == (BASE.isoformat(), 2)
    finally:
        connection.close()


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
