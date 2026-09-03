import json
import sqlite3
from pathlib import Path
import subprocess

import pytest

from app.jobs import build_phase3c_forward_outcomes as outcomes_job


ROOT = Path(__file__).resolve().parents[1]


def _legacy_state(connection: sqlite3.Connection) -> None:
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
            reference_at TEXT NOT NULL DEFAULT '',
            label_schema_version INTEGER NOT NULL DEFAULT 0,
            row_json TEXT NOT NULL
        )
        """
    )


def test_latest_outcome_index_migration_is_batched_and_clears_cursor(tmp_path):
    state = tmp_path / "outcomes.state.sqlite3"
    connection = sqlite3.connect(state)
    connection.execute("PRAGMA journal_mode=WAL")
    _legacy_state(connection)

    payloads = [
        {"reference_at": "2026-09-01T00:00:00+00:00", "label_schema_version": 2},
        {"decision_at_utc": "2026-09-01T00:01:00+00:00", "label_schema_version": 3},
        {"reference_at": "2026-09-01T00:02:00+00:00", "label_schema_version": True},
        {"reference_at": "2026-09-01T00:03:00+00:00", "label_schema_version": "4"},
        ["not", "a", "mapping"],
        None,
        {"decision_at_utc": "2026-09-01T00:06:00+00:00"},
    ]
    serialized = [
        json.dumps(payloads[0]),
        json.dumps(payloads[1]),
        json.dumps(payloads[2]),
        json.dumps(payloads[3]),
        json.dumps(payloads[4]),
        "{malformed",
        json.dumps(payloads[6]),
    ]
    for index, row_json in enumerate(serialized):
        connection.execute(
            """
            INSERT INTO latest_outcomes(
                snapshot_id,
                outcome_record_id,
                outcome_revision,
                window_complete,
                row_json
            ) VALUES (?, ?, 1, 1, ?)
            """,
            (f"S{index}", f"OUT:{index}", row_json),
        )
    connection.commit()

    outcomes_job._migrate_latest_outcome_index_fields(
        connection,
        batch_size=2,
    )

    rows = connection.execute(
        """
        SELECT snapshot_id, reference_at, label_schema_version
        FROM latest_outcomes
        ORDER BY rowid
        """
    ).fetchall()
    assert rows == [
        ("S0", "2026-09-01T00:00:00+00:00", 2),
        ("S1", "2026-09-01T00:01:00+00:00", 3),
        ("S2", "2026-09-01T00:02:00+00:00", 0),
        ("S3", "2026-09-01T00:03:00+00:00", 0),
        ("S4", "", 0),
        ("S5", "", 0),
        ("S6", "2026-09-01T00:06:00+00:00", 0),
    ]

    metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
    assert metadata["latest_outcomes_index_fields_v1"] == "1"
    assert metadata["latest_outcomes_schema_type_v2"] == "1"
    assert (
        outcomes_job._LATEST_OUTCOME_INDEX_MIGRATION_CURSOR_KEY
        not in metadata
    )

    checkpoint = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    assert checkpoint[1] == 0
    connection.close()


def test_latest_outcome_index_migration_resumes_after_committed_cursor(tmp_path):
    state = tmp_path / "outcomes.state.sqlite3"
    connection = sqlite3.connect(state)
    connection.execute("PRAGMA journal_mode=WAL")
    _legacy_state(connection)

    for index in range(5):
        row = {
            "reference_at": f"2026-09-01T00:0{index}:00+00:00",
            "label_schema_version": 2,
        }
        connection.execute(
            """
            INSERT INTO latest_outcomes(
                snapshot_id,
                outcome_record_id,
                outcome_revision,
                window_complete,
                row_json
            ) VALUES (?, ?, 1, 1, ?)
            """,
            (f"S{index}", f"OUT:{index}", json.dumps(row)),
        )

    first_two = connection.execute(
        "SELECT rowid FROM latest_outcomes ORDER BY rowid LIMIT 2"
    ).fetchall()
    cursor = first_two[-1][0]
    connection.execute(
        """
        UPDATE latest_outcomes
        SET reference_at = 'already-migrated',
            label_schema_version = 7
        WHERE rowid <= ?
        """,
        (cursor,),
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            outcomes_job._LATEST_OUTCOME_INDEX_MIGRATION_CURSOR_KEY,
            str(cursor),
        ),
    )
    connection.commit()

    outcomes_job._migrate_latest_outcome_index_fields(
        connection,
        batch_size=2,
    )

    rows = connection.execute(
        """
        SELECT reference_at, label_schema_version
        FROM latest_outcomes
        ORDER BY rowid
        """
    ).fetchall()
    assert rows[:2] == [
        ("already-migrated", 7),
        ("already-migrated", 7),
    ]
    assert rows[2:] == [
        ("2026-09-01T00:02:00+00:00", 2),
        ("2026-09-01T00:03:00+00:00", 2),
        ("2026-09-01T00:04:00+00:00", 2),
    ]
    connection.close()



def test_latest_outcome_index_migration_stops_when_wal_checkpoint_is_blocked(
    tmp_path,
):
    state = tmp_path / "outcomes.state.sqlite3"
    writer = sqlite3.connect(state)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA busy_timeout=50")
    _legacy_state(writer)
    for index in range(5):
        row = {
            "reference_at": f"2026-09-01T00:0{index}:00+00:00",
            "label_schema_version": 2,
        }
        writer.execute(
            """
            INSERT INTO latest_outcomes(
                snapshot_id,
                outcome_record_id,
                outcome_revision,
                window_complete,
                row_json
            ) VALUES (?, ?, 1, 1, ?)
            """,
            (f"S{index}", f"OUT:{index}", json.dumps(row)),
        )
    writer.commit()

    reader = sqlite3.connect(state)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM latest_outcomes").fetchone()

    with pytest.raises(
        RuntimeError,
        match="LATEST_OUTCOMES_MIGRATION_WAL_CHECKPOINT_BLOCKED",
    ):
        outcomes_job._migrate_latest_outcome_index_fields(
            writer,
            batch_size=2,
        )

    cursor = writer.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (outcomes_job._LATEST_OUTCOME_INDEX_MIGRATION_CURSOR_KEY,),
    ).fetchone()
    assert cursor is not None
    assert int(cursor[0]) == 2
    assert writer.execute(
        "SELECT count(*) FROM latest_outcomes WHERE reference_at != ''"
    ).fetchone()[0] == 2

    reader.rollback()
    reader.close()

    outcomes_job._migrate_latest_outcome_index_fields(
        writer,
        batch_size=2,
    )
    assert writer.execute(
        "SELECT count(*) FROM latest_outcomes WHERE reference_at != ''"
    ).fetchone()[0] == 5
    writer.close()

def test_learning_worker_prepare_bounds_derived_docker_storage():
    bootstrap = (
        ROOT / "deploy" / "learning" / "bootstrap-opip-learning-worker.sh"
    ).read_text(encoding="utf-8")

    assert "prune_stale_learning_storage" in bootstrap
    assert "docker builder prune -af" in bootstrap
    assert 'target_image="opip-learning:$TARGET_SHA"' in bootstrap
    assert 'configured_image=""' in bootstrap
    assert "configured_image_known=false" in bootstrap
    assert 'source "$ENV_FILE"' in bootstrap
    assert "configured_image_known=true" in bootstrap
    assert 'docker image rm "$image"' in bootstrap
    assert '"$image" != "$configured_image"' in bootstrap
    assert '"$image" != "$target_image"' in bootstrap
    assert "docker image prune -f" in bootstrap
    assert "apt-get clean" in bootstrap

    prune_call = bootstrap.index("\nprune_stale_learning_storage\n")
    apt_update = bootstrap.index("\napt-get update\n")
    assert prune_call < apt_update

    assert 'rm -rf "$DATA_ROOT"' not in bootstrap
    assert "outcomes.state.sqlite3" not in bootstrap
    assert "p1_shadow_outbox.jsonl" not in bootstrap



def test_learning_worker_configured_image_parsing_normalizes_shell_quotes(tmp_path):
    env_file = tmp_path / "opip-learning.env"
    env_file.write_text(
        'OPIP_LEARNING_IMAGE="opip-learning:rollback"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -Eeuo pipefail; source "$1"; printf "%s" "$OPIP_LEARNING_IMAGE"',
            "_",
            str(env_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "opip-learning:rollback"
