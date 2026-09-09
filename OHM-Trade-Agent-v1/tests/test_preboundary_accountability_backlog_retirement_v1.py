"""Coverage-aware legacy accountability handoff backlog retirement.

Active ``accountability_handoff`` is a work queue, not the historical system of
record. ``latest_outcomes`` / outcome JSONL remain durable evidence. Under a
valid coverage-discontinuity epoch, pre-boundary / straddling legacy rows must
receive durable ``UNRESOLVED_COVERAGE_DISCONTINUITY`` and must not circulate
indefinitely in the active queue. Post-boundary rows stay on the normal path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.jobs.build_phase3c_forward_outcomes import (
    acknowledge_accountability_outcomes,
    advance_accountability_handoff_backfill,
    pending_accountability_outcomes,
)
from app.jobs import build_phase3c_forward_outcomes as outcomes_job
from app.opip.decision.store import screening_evaluations_archive
from app.opip.learning.coverage_discontinuity import (
    OUTCOME_DISPOSITION_UNRESOLVED,
    coverage_epoch_path,
    establish_coverage_discontinuity_epoch,
    load_coverage_epoch,
    outcome_window_crosses_discontinuity,
)
from app.opip.learning.empty_export_attestation import (
    EMPTY_EXPORT_ATTESTATION_FILENAME,
)
from app.services.opportunity_accountability import (
    ACCOUNTABILITY_ARCHIVE_WINDOW_PAD,
    resolved_accountability_outcomes,
)


BOUNDARY = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
PRE = BOUNDARY - timedelta(days=2)
STRADDLE = BOUNDARY + timedelta(minutes=5)  # window start = ref - pad < boundary
POST = BOUNDARY + timedelta(hours=6)
PROD_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _orphan_incomplete_empty_index_state() -> dict:
    return {
        "schema_version": 1,
        "manifest_present": False,
        "manifest_size": 0,
        "manifest_mtime_ns": 0,
        "manifest_sha256": "",
        "complete": False,
        "coverage_start_day": None,
        "coverage_through_day": None,
        "coverage_day_count": 0,
        "shard_sha256": {},
        "updated_at_utc": "2026-09-03T05:14:33.012395+00:00",
    }


def _plant_condition_c(tmp_path: Path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    archive = screening_evaluations_archive(hot)
    archive.data_file.parent.mkdir(parents=True, exist_ok=True)
    archive.data_file.write_text("{}\n", encoding="utf-8")
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_orphan_incomplete_empty_index_state(), sort_keys=True) + "\n"
    archive.window_index_state_file.write_text(payload, encoding="utf-8")
    state_bytes = archive.window_index_state_file.read_bytes()
    (tmp_path / "manifest.env").write_text(
        f"production_deployed_sha={PROD_SHA}\n"
        f"exported_at_utc={BOUNDARY.isoformat()}\n"
        "schema_version=4\n",
        encoding="utf-8",
    )
    return archive, state_bytes


def _establish_epoch(tmp_path: Path):
    archive, state_bytes = _plant_condition_c(tmp_path)
    return establish_coverage_discontinuity_epoch(
        tmp_path,
        archive,
        expected_legacy_state_sha256=hashlib.sha256(state_bytes).hexdigest(),
        boundary_at_utc=BOUNDARY,
        production_deployed_sha=PROD_SHA,
        exported_at_utc=BOUNDARY.isoformat(),
    )


def _outcome_row(
    snapshot_id: str,
    reference_at: datetime,
    *,
    revision: int = 1,
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "episode_id": f"E-{snapshot_id}",
        "cohort_id": "C1",
        "cohort_size": 1,
        "pair": "XBT/USD",
        "symbol": "XBT/USD",
        "decision_at_utc": reference_at.isoformat(),
        "reference_at": reference_at.isoformat(),
        "outcome_record_id": f"OUT:{snapshot_id}",
        "outcome_revision": revision,
        "window_complete": True,
        "label_schema_version": 1,
    }


def _seed_latest_outcomes(
    state: Path,
    rows: list[dict],
    *,
    cursor: dict | None = None,
    complete: bool = False,
) -> None:
    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS latest_outcomes (
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accountability_handoff (
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
            CREATE TABLE IF NOT EXISTS snapshot_queue (
                snapshot_id TEXT PRIMARY KEY,
                decision_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                row_json TEXT NOT NULL
            )
            """
        )
        for row in rows:
            connection.execute(
                """
                INSERT OR REPLACE INTO latest_outcomes(
                    snapshot_id, outcome_record_id, outcome_revision,
                    window_complete, reference_at, label_schema_version, row_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["snapshot_id"],
                    row["outcome_record_id"],
                    int(row["outcome_revision"]),
                    1,
                    row["reference_at"],
                    int(row.get("label_schema_version") or 0),
                    json.dumps(row, sort_keys=True),
                ),
            )
        if cursor is not None:
            connection.execute(
                """
                INSERT OR REPLACE INTO metadata(key, value)
                VALUES ('accountability_handoff_backfill_cursor_v2', ?)
                """,
                (json.dumps(cursor, sort_keys=True, separators=(",", ":")),),
            )
        if complete:
            connection.execute(
                """
                INSERT OR REPLACE INTO metadata(key, value)
                VALUES ('accountability_handoff_backfill_v2', '1')
                """
            )
        connection.commit()
    finally:
        connection.close()


def _handoff_ids(state: Path) -> list[str]:
    connection = sqlite3.connect(state)
    try:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT snapshot_id FROM accountability_handoff "
                "ORDER BY reference_at, snapshot_id"
            ).fetchall()
        ]
    finally:
        connection.close()


def _latest_ids(state: Path) -> set[str]:
    connection = sqlite3.connect(state)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT snapshot_id FROM latest_outcomes"
            ).fetchall()
        }
    finally:
        connection.close()


def _disposition_count(
    accountability_state: Path,
    *,
    disposition: str = OUTCOME_DISPOSITION_UNRESOLVED,
) -> int:
    connection = sqlite3.connect(accountability_state)
    try:
        row = connection.execute(
            "SELECT count(*) FROM outcome_disposition WHERE disposition = ?",
            (disposition,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()


def _metadata_value(state: Path, key: str) -> str | None:
    connection = sqlite3.connect(state)
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row[0])
    finally:
        connection.close()


def _backfill_cursor(state: Path) -> dict | None:
    raw = _metadata_value(state, "accountability_handoff_backfill_cursor_v2")
    if raw is None:
        return None
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload


def _backfill_complete_flag(state: Path) -> bool:
    return _metadata_value(state, "accountability_handoff_backfill_v2") == "1"


def test_preboundary_upgrade_terminalizes_without_active_queue_debt(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    epoch = _establish_epoch(data_root)
    assert epoch.measurement_only is True

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"

    pre = _outcome_row("PRE-1", PRE)
    _seed_latest_outcomes(state, [pre])

    result = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        data_root=data_root,
        ledger_path=ledger,
        accountability_state_path=acct_state,
    )
    assert result["batch_rows"] == 1
    assert result["enqueued_handoff"] == 0
    assert result["terminalized_coverage_discontinuity"] == 1
    assert result["complete"] is True
    assert "PRE-1" in _latest_ids(state)
    assert _handoff_ids(state) == []
    assert _disposition_count(acct_state) == 1
    assert pending_accountability_outcomes(
        output_path=output, state_path=state
    ) == []

    # Idempotent: no duplicate dispositions, no resurrected handoff.
    again = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        data_root=data_root,
        ledger_path=ledger,
        accountability_state_path=acct_state,
    )
    assert again["already_complete"] is True
    assert again["terminalized_coverage_discontinuity"] == 0
    assert _disposition_count(acct_state) == 1
    assert _handoff_ids(state) == []
    assert load_coverage_epoch(data_root).boundary_at_utc == BOUNDARY
    assert not (data_root / EMPTY_EXPORT_ATTESTATION_FILENAME).exists()


def test_mixed_pre_post_boundary_only_retires_governed_rows(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _establish_epoch(data_root)

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"

    pre = _outcome_row("PRE-MIX", PRE)
    post = _outcome_row("POST-MIX", POST)
    assert outcome_window_crosses_discontinuity(
        PRE, BOUNDARY, pad=ACCOUNTABILITY_ARCHIVE_WINDOW_PAD
    )
    assert not outcome_window_crosses_discontinuity(
        POST, BOUNDARY, pad=ACCOUNTABILITY_ARCHIVE_WINDOW_PAD
    )
    _seed_latest_outcomes(state, [pre, post])

    result = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        data_root=data_root,
        ledger_path=ledger,
        accountability_state_path=acct_state,
        batch_size=10,
    )
    assert result["enqueued_handoff"] == 1
    assert result["terminalized_coverage_discontinuity"] == 1
    assert _handoff_ids(state) == ["POST-MIX"]
    assert _latest_ids(state) == {"PRE-MIX", "POST-MIX"}
    assert _disposition_count(acct_state) == 1

    pending = pending_accountability_outcomes(output_path=output, state_path=state)
    assert [row["snapshot_id"] for row in pending] == ["POST-MIX"]


def test_straddling_window_uses_coverage_helper_semantics(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _establish_epoch(data_root)

    assert outcome_window_crosses_discontinuity(
        STRADDLE, BOUNDARY, pad=ACCOUNTABILITY_ARCHIVE_WINDOW_PAD
    )

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"
    row = _outcome_row("STRADDLE-1", STRADDLE)
    _seed_latest_outcomes(state, [row])

    result = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        data_root=data_root,
        ledger_path=ledger,
        accountability_state_path=acct_state,
    )
    assert result["enqueued_handoff"] == 0
    assert result["terminalized_coverage_discontinuity"] == 1
    assert _handoff_ids(state) == []
    assert "STRADDLE-1" in _latest_ids(state)


def test_no_epoch_preserves_legacy_enqueue_semantics(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    # No coverage epoch planted.

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    pre = _outcome_row("LEGACY-NO-EPOCH", PRE)
    _seed_latest_outcomes(state, [pre])

    result = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        data_root=data_root,
    )
    assert result["enqueued_handoff"] == 1
    assert result["terminalized_coverage_discontinuity"] == 0
    assert _handoff_ids(state) == ["LEGACY-NO-EPOCH"]


def test_missing_reference_at_with_coverage_boundary_fails_closed(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _establish_epoch(data_root)

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"

    missing = _outcome_row("MISS-REF", PRE)
    missing["reference_at"] = ""
    missing["decision_at_utc"] = ""
    _seed_latest_outcomes(state, [missing])
    assert _backfill_cursor(state) is None
    assert _backfill_complete_flag(state) is False

    with pytest.raises(
        RuntimeError, match="ACCOUNTABILITY_BACKFILL_INVALID_REFERENCE_AT"
    ):
        advance_accountability_handoff_backfill(
            output_path=output,
            state_path=state,
            data_root=data_root,
            ledger_path=ledger,
            accountability_state_path=acct_state,
        )

    assert _handoff_ids(state) == []
    assert _backfill_cursor(state) is None
    assert _backfill_complete_flag(state) is False
    assert "MISS-REF" in _latest_ids(state)
    assert not acct_state.exists() or _disposition_count(acct_state) == 0


def test_malformed_reference_at_with_coverage_boundary_fails_closed(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _establish_epoch(data_root)

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"

    bad = _outcome_row("BAD-REF", PRE)
    bad["reference_at"] = "not-a-timestamp"
    bad["decision_at_utc"] = "not-a-timestamp"
    _seed_latest_outcomes(state, [bad])

    with pytest.raises(
        RuntimeError, match="ACCOUNTABILITY_BACKFILL_INVALID_REFERENCE_AT"
    ):
        advance_accountability_handoff_backfill(
            output_path=output,
            state_path=state,
            data_root=data_root,
            ledger_path=ledger,
            accountability_state_path=acct_state,
        )

    assert _handoff_ids(state) == []
    assert _backfill_cursor(state) is None
    assert _backfill_complete_flag(state) is False
    assert "BAD-REF" in _latest_ids(state)
    assert not acct_state.exists() or _disposition_count(acct_state) == 0


def test_mixed_batch_invalid_timestamp_is_atomic(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _establish_epoch(data_root)

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"

    valid = _outcome_row("VALID-MIX", POST)
    bad = _outcome_row("BAD-MIX", PRE + timedelta(minutes=1))
    bad["reference_at"] = "not-a-timestamp"
    bad["decision_at_utc"] = "not-a-timestamp"
    # Sort key places valid post-boundary row before malformed row so a
    # non-atomic implementation would enqueue VALID-MIX first.
    _seed_latest_outcomes(state, [valid, bad])

    with pytest.raises(
        RuntimeError, match="ACCOUNTABILITY_BACKFILL_INVALID_REFERENCE_AT"
    ):
        advance_accountability_handoff_backfill(
            output_path=output,
            state_path=state,
            data_root=data_root,
            ledger_path=ledger,
            accountability_state_path=acct_state,
        )

    assert _handoff_ids(state) == []
    assert _backfill_cursor(state) is None
    assert _backfill_complete_flag(state) is False
    assert {"VALID-MIX", "BAD-MIX"} <= _latest_ids(state)
    assert not acct_state.exists() or _disposition_count(acct_state) == 0


def test_no_epoch_empty_reference_at_preserves_skip_semantics(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    empty = _outcome_row("EMPTY-NO-EPOCH", PRE)
    empty["reference_at"] = ""
    empty["decision_at_utc"] = ""
    _seed_latest_outcomes(state, [empty])

    result = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        data_root=data_root,
    )
    assert result["enqueued_handoff"] == 0
    assert result["terminalized_coverage_discontinuity"] == 0
    assert result["complete"] is True
    assert _handoff_ids(state) == []
    assert "EMPTY-NO-EPOCH" in _latest_ids(state)
    assert _backfill_complete_flag(state) is True


def test_valid_timestamps_still_terminalize_and_enqueue(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _establish_epoch(data_root)

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"

    pre = _outcome_row("VALID-PRE", PRE)
    post = _outcome_row("VALID-POST", POST)
    _seed_latest_outcomes(state, [pre, post])

    result = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        data_root=data_root,
        ledger_path=ledger,
        accountability_state_path=acct_state,
    )
    assert result["enqueued_handoff"] == 1
    assert result["terminalized_coverage_discontinuity"] == 1
    assert _handoff_ids(state) == ["VALID-POST"]
    assert _disposition_count(acct_state) == 1
    assert {"VALID-PRE", "VALID-POST"} <= _latest_ids(state)


def test_invalid_epoch_fails_closed(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _plant_condition_c(data_root)
    path = coverage_epoch_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archive_prefix": "screening_evaluations",
                "boundary_at_utc": BOUNDARY.isoformat(),
                "reason": "NOT_A_VALID_REASON",
                "measurement_only": True,
                "trade_authority_changed": False,
                "policy_change_authorized": False,
                "legacy_state_sha256": "b" * 64,
                "production_deployed_sha": PROD_SHA,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    _seed_latest_outcomes(state, [_outcome_row("X", PRE)])

    with pytest.raises(RuntimeError, match="coverage epoch"):
        advance_accountability_handoff_backfill(
            output_path=output,
            state_path=state,
            data_root=data_root,
        )
    assert _handoff_ids(state) == []


def test_bounded_migration_processes_at_most_one_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(outcomes_job, "ACCOUNTABILITY_HANDOFF_BACKFILL_BATCH_SIZE", 2)
    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    rows = [
        _outcome_row(f"B{i}", PRE + timedelta(minutes=i))
        for i in range(5)
    ]
    _seed_latest_outcomes(state, rows)

    first = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        batch_size=2,
    )
    assert first["batch_rows"] == 2
    assert first["complete"] is False
    assert len(_handoff_ids(state)) == 2

    # Ordinary opens must not advance further batches.
    assert pending_accountability_outcomes(output_path=output, state_path=state)
    assert len(_handoff_ids(state)) == 2

    second = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        batch_size=2,
    )
    assert second["batch_rows"] == 2
    assert len(_handoff_ids(state)) == 4


def test_partial_cursor_resume_and_completion_marker(tmp_path):
    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    rows = [
        _outcome_row("C0", PRE + timedelta(minutes=0)),
        _outcome_row("C1", PRE + timedelta(minutes=1)),
        _outcome_row("C2", PRE + timedelta(minutes=2)),
    ]
    _seed_latest_outcomes(
        state,
        rows,
        cursor={
            "reference_at": rows[0]["reference_at"],
            "snapshot_id": "C0",
        },
    )

    mid = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        batch_size=2,
    )
    assert mid["batch_rows"] == 2
    assert mid["complete"] is False
    assert _handoff_ids(state) == ["C1", "C2"]
    assert "C0" not in _handoff_ids(state)
    assert "C0" in _latest_ids(state)

    done = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        batch_size=2,
    )
    assert done["complete"] is True
    connection = sqlite3.connect(state)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
    finally:
        connection.close()
    assert metadata["accountability_handoff_backfill_v2"] == "1"
    assert "accountability_handoff_backfill_cursor_v2" not in metadata

    noop = advance_accountability_handoff_backfill(
        output_path=output,
        state_path=state,
        batch_size=2,
    )
    assert noop["already_complete"] is True


def test_ack_only_after_durable_disposition_for_existing_handoff_debt(tmp_path):
    """Already-enqueued pre-boundary debt: disposition first, then ack."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    _establish_epoch(data_root)

    output = tmp_path / "outcomes.jsonl"
    state = tmp_path / "outcomes.state.sqlite3"
    ledger = tmp_path / "accountability.jsonl"
    acct_state = tmp_path / "accountability.state.sqlite3"

    pre = _outcome_row("DEBT-1", PRE)
    _seed_latest_outcomes(state, [pre], complete=True)
    connection = sqlite3.connect(state)
    try:
        connection.execute(
            """
            INSERT INTO accountability_handoff(
                snapshot_id, outcome_record_id, outcome_revision,
                reference_at, row_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                pre["snapshot_id"],
                pre["outcome_record_id"],
                1,
                pre["reference_at"],
                json.dumps(pre, sort_keys=True),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    pending = pending_accountability_outcomes(output_path=output, state_path=state)
    assert [row["snapshot_id"] for row in pending] == ["DEBT-1"]

    # No disposition yet → not resolved → must not ack.
    assert (
        resolved_accountability_outcomes(
            pending, ledger_path=ledger, state_path=acct_state
        )
        == []
    )
    assert (
        acknowledge_accountability_outcomes(
            [], output_path=output, state_path=state
        )
        == 0
    )
    assert _handoff_ids(state) == ["DEBT-1"]

    from app.services.opportunity_accountability import (
        build_incremental_from_outcomes,
    )

    screening = data_root / "opip/qualification/screening_evaluations.jsonl"
    funnel = data_root / "opip/qualification/funnel_events.jsonl"
    funnel.parent.mkdir(parents=True, exist_ok=True)
    funnel.write_text("", encoding="utf-8")
    summary = build_incremental_from_outcomes(
        pending,
        ledger_path=ledger,
        state_path=acct_state,
        screening_path=screening,
        screening_archive=screening.parent / "screening_evaluations_archive",
        funnel_path=funnel,
        funnel_archive=funnel.parent / "funnel_events_archive",
        intelligence_event_path=tmp_path / "intelligence_learning/events.jsonl",
        summary_path=tmp_path / "opip/opportunity_accountability_summary.json",
        replica_mode=True,
    )
    assert summary.get("measurement_only", True) is True
    assert summary["batch_disposition"]["unresolved_coverage_discontinuity"] == 1

    resolved = resolved_accountability_outcomes(
        pending, ledger_path=ledger, state_path=acct_state
    )
    assert len(resolved) == 1
    assert _disposition_count(acct_state) == 1

    acked = acknowledge_accountability_outcomes(
        resolved, output_path=output, state_path=state
    )
    assert acked == 1
    assert _handoff_ids(state) == []
    assert "DEBT-1" in _latest_ids(state)


def test_outcomes_cycle_invokes_backfill_once_before_pending():
    source = (
        Path(__file__).resolve().parents[1]
        / "app/jobs/run_opportunity_intelligence_cycle.py"
    ).read_text(encoding="utf-8")
    advance = source.index("advance_accountability_handoff_backfill(")
    pending = source.index("pending_accountability_outcomes()")
    build = source.index(
        "build_incremental_from_outcomes(outcomes, replica_mode=True)"
    )
    resolved = source.index("resolved_accountability_outcomes(outcomes)")
    ack = source.index("acknowledge_accountability_outcomes(resolved)")
    assert advance < pending < build < resolved < ack
    assert source.count("advance_accountability_handoff_backfill(") == 1


def test_open_bounded_state_does_not_mention_backfill_advancement():
    source = (
        Path(__file__).resolve().parents[1]
        / "app/jobs/build_phase3c_forward_outcomes.py"
    ).read_text(encoding="utf-8")
    open_fn = source.index("def _open_bounded_state(")
    next_def = source.index("\ndef _accountability_handoff_backfill_complete(")
    open_body = source[open_fn:next_def]
    assert "accountability_handoff_backfill_cursor_v2" not in open_body
    assert "INSERT INTO accountability_handoff" not in open_body


def test_no_trading_authority_surface_in_hotfix_modules():
    roots = [
        Path(__file__).resolve().parents[1] / "app/jobs/build_phase3c_forward_outcomes.py",
        Path(__file__).resolve().parents[1]
        / "app/jobs/run_opportunity_intelligence_cycle.py",
    ]
    for path in roots:
        raw = path.read_text(encoding="utf-8")
        assert "trade_authority_changed\": True" not in raw
        assert "policy_change_authorized\": True" not in raw
        assert "measurement_only\": False" not in raw
        lower = raw.lower()
        assert "place_order" not in lower
        assert "cancel_order" not in lower
        assert "kraken" not in lower


def test_normal_post_boundary_cycle_measurement_flags(tmp_path, monkeypatch):
    import app.jobs.run_opportunity_intelligence_cycle as cycle
    import app.opip.learning.job_disposition as jd

    monkeypatch.setattr(cycle, "_DEFAULT_DATA_ROOT", tmp_path)

    def _advance(**kwargs):
        return {
            "batch_rows": 0,
            "enqueued_handoff": 0,
            "terminalized_coverage_discontinuity": 0,
            "complete": True,
            "already_complete": True,
        }

    def _build():
        return [{"snapshot_id": "POST-NEW"}]

    def _incremental(outcomes, replica_mode=True):
        return {"population": {"n": 1}, "opportunity_capture_rate_pct": 0.0}

    def _resolved(outcomes):
        return list(outcomes)

    def _ack(resolved):
        return len(resolved)

    monkeypatch.setattr(cycle, "advance_accountability_handoff_backfill", _advance)
    monkeypatch.setattr(cycle, "build_outcomes_bounded", _build)
    monkeypatch.setattr(cycle, "build_incremental_from_outcomes", _incremental)
    monkeypatch.setattr(cycle, "resolved_accountability_outcomes", _resolved)
    monkeypatch.setattr(cycle, "acknowledge_accountability_outcomes", _ack)

    # First pending empty → build → second pending still empty after our stub.
    calls = {"n": 0}

    def _pending_then_built():
        calls["n"] += 1
        if calls["n"] == 1:
            return []
        return [{"snapshot_id": "POST-NEW", "outcome_record_id": "OUT:POST-NEW"}]

    monkeypatch.setattr(cycle, "pending_accountability_outcomes", _pending_then_built)
    cycle.main()
    summary = jd.read_consumption_summary(tmp_path, "outcomes")
    assert summary["disposition"] == jd.CONSUMED_OK
    assert summary["measurement_only"] is True
    assert summary["trade_authority_changed"] is False
    assert summary["policy_change_authorized"] is False
    assert "accountability_handoff_backfill" in summary


def test_terminalized_backfill_only_cycle_is_consumed_ok(tmp_path, monkeypatch):
    """Coverage retirement without pending handoff is not CONSUMED_EMPTY."""
    import app.jobs.run_opportunity_intelligence_cycle as cycle
    import app.opip.learning.job_disposition as jd

    monkeypatch.setattr(cycle, "_DEFAULT_DATA_ROOT", tmp_path)

    def _advance(**kwargs):
        return {
            "batch_rows": 3,
            "enqueued_handoff": 0,
            "terminalized_coverage_discontinuity": 3,
            "complete": False,
            "already_complete": False,
        }

    monkeypatch.setattr(cycle, "advance_accountability_handoff_backfill", _advance)
    monkeypatch.setattr(cycle, "pending_accountability_outcomes", lambda: [])
    monkeypatch.setattr(cycle, "build_outcomes_bounded", lambda: [])
    monkeypatch.setattr(
        cycle,
        "build_incremental_from_outcomes",
        lambda outcomes, replica_mode=True: {
            "population": {},
            "opportunity_capture_rate_pct": None,
        },
    )
    monkeypatch.setattr(cycle, "resolved_accountability_outcomes", lambda outcomes: [])
    monkeypatch.setattr(cycle, "acknowledge_accountability_outcomes", lambda resolved: 0)

    cycle.main()
    summary = jd.read_consumption_summary(tmp_path, "outcomes")
    assert summary["disposition"] == jd.CONSUMED_OK
    assert summary["accountability_handoff_backfill"][
        "terminalized_coverage_discontinuity"
    ] == 3
    assert summary["measurement_only"] is True
    assert summary["trade_authority_changed"] is False
    assert summary["policy_change_authorized"] is False


def test_invalid_epoch_backfill_error_still_writes_summary(tmp_path, monkeypatch):
    import app.jobs.run_opportunity_intelligence_cycle as cycle
    import app.opip.learning.job_disposition as jd

    monkeypatch.setattr(cycle, "_DEFAULT_DATA_ROOT", tmp_path)

    def _advance(**kwargs):
        raise RuntimeError("coverage epoch reason is unsupported")

    monkeypatch.setattr(cycle, "advance_accountability_handoff_backfill", _advance)
    monkeypatch.setattr(cycle, "pending_accountability_outcomes", lambda: [])
    monkeypatch.setattr(cycle, "build_outcomes_bounded", lambda: [])
    monkeypatch.setattr(
        cycle,
        "build_incremental_from_outcomes",
        lambda outcomes, replica_mode=True: {
            "population": {},
            "opportunity_capture_rate_pct": None,
        },
    )
    monkeypatch.setattr(cycle, "resolved_accountability_outcomes", lambda outcomes: [])
    monkeypatch.setattr(cycle, "acknowledge_accountability_outcomes", lambda resolved: 0)

    with pytest.raises(RuntimeError, match="coverage epoch"):
        cycle.main()
    summary = jd.read_consumption_summary(tmp_path, "outcomes")
    assert summary is not None
    assert summary["status"] == "ERROR"
    assert "coverage epoch" in summary["accountability_handoff_backfill"]["error"]
    assert summary["measurement_only"] is True
    assert summary["trade_authority_changed"] is False
    assert summary["policy_change_authorized"] is False