"""Asynchronously mature Phase 3C forward outcomes from persisted evidence.

The output is an append-only outcome-maturation ledger. Re-running the job is
idempotent for unchanged labels; partial rows may receive later immutable
revisions until the 24h window is complete. The job performs no market scan,
no Telegram action, and no trading-state mutation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from app.services.phase3c_outcomes import (
    build_forward_outcome_labels,
    outcome_label_is_current,
)
from app.services.registry_io import registry_lock
from app.services.signal_quality_phase2 import DEFAULT_OBSERVATION_FILE, read_observations
from app.services.signal_quality_phase3c import read_jsonl


DEFAULT_SNAPSHOT_LEDGER = Path("/app/data/p1_evidence_ledger.jsonl")
DEFAULT_OUTPUT = Path("/app/data/phase3c_forward_outcomes.jsonl")


def _repair_truncated_jsonl_tail(path: Path) -> bool:
    """Discard only an incomplete final JSONL record left by abrupt termination."""
    if not path.exists() or path.stat().st_size == 0:
        return False

    with path.open("r+b") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return False

        end = handle.tell()
        cursor = end
        last_newline = -1
        chunk_size = 4096
        while cursor > 0 and last_newline < 0:
            start = max(0, cursor - chunk_size)
            handle.seek(start)
            chunk = handle.read(cursor - start)
            pos = chunk.rfind(b"\n")
            if pos >= 0:
                last_newline = start + pos
                break
            cursor = start

        tail_start = last_newline + 1 if last_newline >= 0 else 0
        handle.seek(tail_start)
        tail = handle.read()

        # A complete JSON object may have reached disk before the writer was
        # killed while emitting only the trailing newline. Preserve that valid
        # record and normalize its terminator; discard only an unparsable tail.
        try:
            parsed = json.loads(tail.decode("utf-8"))
            valid_complete_record = isinstance(parsed, dict)
        except (UnicodeDecodeError, json.JSONDecodeError):
            valid_complete_record = False

        if valid_complete_record:
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
        else:
            handle.truncate(tail_start)

        handle.flush()
        os.fsync(handle.fileno())
    return True


def _canonical_label_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"outcome_record_id", "outcome_revision"}
    }


def _outcome_record_id(row: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_label_payload(row),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "OUT:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _latest_by_snapshot(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        snapshot_id = str(row.get("snapshot_id", "") or "")
        if snapshot_id:
            latest[snapshot_id] = row
    return latest



BOUNDED_MAX_SNAPSHOTS = 500
BOUNDED_BASELINE_LOOKBACK = timedelta(hours=24)
BOUNDED_FORWARD_GRACE = timedelta(hours=25)
BOUNDED_RETRY_DELAY = timedelta(hours=1)
BOUNDED_CHECKPOINT_ANCHOR_BYTES = 4096
ACCOUNTABILITY_HANDOFF_BACKFILL_BATCH_SIZE = 500


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_state_path(output_path: Path) -> Path:
    return output_path.parent / f".{output_path.name}.state.sqlite3"


def _open_bounded_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
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
        "CREATE INDEX IF NOT EXISTS idx_accountability_handoff_reference "
        "ON accountability_handoff(reference_at, snapshot_id)"
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
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshot_queue_due "
        "ON snapshot_queue(next_due_at, decision_at)"
    )

    backfill_marker = connection.execute(
        "SELECT value FROM metadata "
        "WHERE key = 'accountability_handoff_backfill_v2'"
    ).fetchone()
    if backfill_marker is None:
        cursor_row = connection.execute(
            "SELECT value FROM metadata "
            "WHERE key = 'accountability_handoff_backfill_cursor_v2'"
        ).fetchone()
        cursor_reference = ""
        cursor_snapshot = ""
        if cursor_row is not None:
            try:
                cursor_payload = json.loads(str(cursor_row[0]))
            except (TypeError, json.JSONDecodeError):
                cursor_payload = {}
            if isinstance(cursor_payload, dict):
                cursor_reference = str(
                    cursor_payload.get("reference_at") or ""
                )
                cursor_snapshot = str(
                    cursor_payload.get("snapshot_id") or ""
                )

        legacy_rows = connection.execute(
            """
            WITH ordered AS (
                SELECT
                    snapshot_id,
                    outcome_record_id,
                    outcome_revision,
                    row_json,
                    coalesce(
                        CASE
                            WHEN json_valid(row_json)
                            THEN json_extract(row_json, '$.reference_at')
                            ELSE ''
                        END,
                        ''
                    ) AS sort_reference_at
                FROM latest_outcomes
            )
            SELECT
                snapshot_id,
                outcome_record_id,
                outcome_revision,
                row_json,
                sort_reference_at
            FROM ordered
            WHERE sort_reference_at > ?
               OR (
                    sort_reference_at = ?
                    AND snapshot_id > ?
               )
            ORDER BY sort_reference_at, snapshot_id
            LIMIT ?
            """,
            (
                cursor_reference,
                cursor_reference,
                cursor_snapshot,
                ACCOUNTABILITY_HANDOFF_BACKFILL_BATCH_SIZE,
            ),
        ).fetchall()

        last_reference = cursor_reference
        last_snapshot_id = cursor_snapshot
        for (
            snapshot_id,
            outcome_record_id,
            outcome_revision,
            row_json,
            sort_reference_at,
        ) in legacy_rows:
            snapshot_id = str(snapshot_id or "").strip()
            outcome_record_id = str(outcome_record_id or "").strip()
            last_reference = str(sort_reference_at or "")
            if snapshot_id:
                last_snapshot_id = snapshot_id
            if not snapshot_id or not outcome_record_id:
                continue
            try:
                revision = int(outcome_revision)
            except (TypeError, ValueError):
                continue
            try:
                parsed = json.loads(row_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            reference_at = str(
                parsed.get("reference_at")
                or parsed.get("decision_at_utc")
                or ""
            )
            if not reference_at:
                continue
            connection.execute(
                """
                INSERT INTO accountability_handoff(
                    snapshot_id,
                    outcome_record_id,
                    outcome_revision,
                    reference_at,
                    row_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    outcome_record_id = excluded.outcome_record_id,
                    outcome_revision = excluded.outcome_revision,
                    reference_at = excluded.reference_at,
                    row_json = excluded.row_json
                WHERE excluded.outcome_revision
                      >= accountability_handoff.outcome_revision
                """,
                (
                    snapshot_id,
                    outcome_record_id,
                    revision,
                    reference_at,
                    row_json,
                ),
            )

        if legacy_rows:
            connection.execute(
                """
                INSERT INTO metadata(key, value)
                VALUES ('accountability_handoff_backfill_cursor_v2', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (
                    json.dumps(
                        {
                            "reference_at": last_reference,
                            "snapshot_id": last_snapshot_id,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.commit()

        if len(legacy_rows) < ACCOUNTABILITY_HANDOFF_BACKFILL_BATCH_SIZE:
            connection.execute(
                """
                INSERT INTO metadata(key, value)
                VALUES ('accountability_handoff_backfill_v2', '1')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )
            connection.execute(
                "DELETE FROM metadata "
                "WHERE key = 'accountability_handoff_backfill_cursor_v2'"
            )
    connection.commit()
    return connection


def _state_int(connection: sqlite3.Connection, key: str, default: int = 0) -> int:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _set_state_int(connection: sqlite3.Connection, key: str, value: int) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(int(value))),
    )


def _state_text(
    connection: sqlite3.Connection,
    key: str,
) -> str | None:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _set_state_text(
    connection: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def _file_anchor(path: Path, offset: int) -> tuple[int, int, str | None]:
    if offset <= 0:
        return 0, 0, None
    start = max(0, offset - BOUNDED_CHECKPOINT_ANCHOR_BYTES)
    size = offset - start
    with path.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(size)
    if len(payload) != size:
        raise RuntimeError("LEDGER_CHECKPOINT_SHORT_READ")
    return start, size, hashlib.sha256(payload).hexdigest()


def _state_checkpoint_matches(
    connection: sqlite3.Connection,
    path: Path,
    prefix: str,
    offset: int,
) -> bool:
    if offset <= 0:
        return True
    expected = _state_text(connection, f"{prefix}_anchor_sha256")
    start = _state_int(connection, f"{prefix}_anchor_start", -1)
    size = _state_int(connection, f"{prefix}_anchor_size", -1)
    if (
        not expected
        or start < 0
        or size <= 0
        or start + size != offset
    ):
        return False
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read(size)
    except OSError:
        return False
    return (
        len(payload) == size
        and hashlib.sha256(payload).hexdigest() == expected
    )


def _set_state_checkpoint(
    connection: sqlite3.Connection,
    path: Path,
    prefix: str,
    offset: int,
) -> None:
    _set_state_int(connection, f"{prefix}_indexed_offset", offset)
    if offset <= 0:
        _set_state_int(connection, f"{prefix}_anchor_start", 0)
        _set_state_int(connection, f"{prefix}_anchor_size", 0)
        _set_state_text(connection, f"{prefix}_anchor_sha256", "")
        return
    start, size, digest = _file_anchor(path, offset)
    _set_state_int(connection, f"{prefix}_anchor_start", start)
    _set_state_int(connection, f"{prefix}_anchor_size", size)
    _set_state_text(connection, f"{prefix}_anchor_sha256", digest or "")


def _upsert_latest_outcome(
    connection: sqlite3.Connection,
    row: dict[str, Any],
) -> None:
    snapshot_id = str(row.get("snapshot_id", "") or "").strip()
    outcome_record_id = str(
        row.get("outcome_record_id", "") or ""
    ).strip()
    if not snapshot_id:
        return
    try:
        revision = int(row.get("outcome_revision", 0) or 0)
    except (TypeError, ValueError):
        return
    latest_cursor = connection.execute(
        """
        INSERT INTO latest_outcomes(
            snapshot_id,
            outcome_record_id,
            outcome_revision,
            window_complete,
            row_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
            outcome_record_id = excluded.outcome_record_id,
            outcome_revision = excluded.outcome_revision,
            window_complete = excluded.window_complete,
            row_json = excluded.row_json
        WHERE excluded.outcome_revision >= latest_outcomes.outcome_revision
        """,
        (
            snapshot_id,
            outcome_record_id,
            revision,
            1 if bool(row.get("window_complete", False)) else 0,
            json.dumps(row, sort_keys=True, allow_nan=False),
        ),
    )
    reference_at = str(
        row.get("reference_at")
        or row.get("decision_at_utc")
        or ""
    )
    if not outcome_record_id:
        # Delete only when this malformed revision actually became the latest
        # stored revision. A stale malformed ledger row must never erase a
        # newer valid unacknowledged handoff.
        if int(latest_cursor.rowcount or 0) > 0:
            connection.execute(
                "DELETE FROM accountability_handoff WHERE snapshot_id = ?",
                (snapshot_id,),
            )
        return
    connection.execute(
        """
        INSERT INTO accountability_handoff(
            snapshot_id,
            outcome_record_id,
            outcome_revision,
            reference_at,
            row_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
            outcome_record_id = excluded.outcome_record_id,
            outcome_revision = excluded.outcome_revision,
            reference_at = excluded.reference_at,
            row_json = excluded.row_json
        WHERE excluded.outcome_revision >= accountability_handoff.outcome_revision
        """,
        (
            snapshot_id,
            outcome_record_id,
            revision,
            reference_at,
            json.dumps(row, sort_keys=True, allow_nan=False),
        ),
    )


def _reconcile_output_state(
    connection: sqlite3.Connection,
    output_path: Path,
) -> None:
    indexed_offset = _state_int(connection, "output_indexed_offset", 0)
    if not output_path.exists():
        if indexed_offset:
            raise RuntimeError("OUTCOME_LEDGER_TRUNCATED")
        return

    size = output_path.stat().st_size
    if indexed_offset > size:
        raise RuntimeError("OUTCOME_LEDGER_TRUNCATED")
    if not _state_checkpoint_matches(
        connection,
        output_path,
        "output",
        indexed_offset,
    ):
        raise RuntimeError("OUTCOME_LEDGER_DIVERGED")

    last_complete = indexed_offset
    with output_path.open("rb") as handle:
        handle.seek(indexed_offset)
        while True:
            raw = handle.readline()
            if not raw:
                break
            end = handle.tell()
            if not raw.endswith(b"\n"):
                break
            last_complete = end
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                _upsert_latest_outcome(connection, row)

    _set_state_checkpoint(
        connection,
        output_path,
        "output",
        last_complete,
    )
    connection.commit()


def _next_due_at(
    row: dict[str, Any],
    *,
    evaluated_at: datetime,
) -> datetime | None:
    if (
        bool(row.get("window_complete", False))
        and outcome_label_is_current(row)
    ):
        return None
    if bool(row.get("window_complete", False)):
        # Completed under an older label schema: re-evaluate immediately once
        # so newly introduced horizons become available historically.
        return evaluated_at
    reference_at = _parse_utc(
        row.get("reference_at", row.get("decision_at_utc"))
    )
    if reference_at is None:
        return evaluated_at + BOUNDED_RETRY_DELAY

    milestones = (
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(minutes=30),
        timedelta(minutes=60),
        timedelta(hours=4),
        timedelta(hours=8),
        timedelta(hours=12),
        timedelta(hours=24),
    )
    for delta in milestones:
        due = reference_at + delta
        if due > evaluated_at:
            return due
    if evaluated_at >= reference_at + BOUNDED_FORWARD_GRACE:
        return None
    return evaluated_at + BOUNDED_RETRY_DELAY


def _latest_outcome_row(
    connection: sqlite3.Connection,
    snapshot_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT row_json FROM latest_outcomes WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def pending_accountability_outcomes(
    *,
    output_path: Path = DEFAULT_OUTPUT,
    state_path: Path | None = None,
    limit: int = BOUNDED_MAX_SNAPSHOTS,
) -> list[dict[str, Any]]:
    """Return durable, unacknowledged outcome revisions for accountability."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    state_db = state_path or _bounded_state_path(output_path)
    lock = output_path.parent / f".{output_path.name}.lock"
    with registry_lock(lock):
        _repair_truncated_jsonl_tail(output_path)
        connection = _open_bounded_state(state_db)
        try:
            _reconcile_output_state(connection, output_path)
            connection.execute(
                """
                DELETE FROM accountability_handoff
                WHERE trim(snapshot_id) = ''
                   OR trim(outcome_record_id) = ''
                """
            )
            connection.commit()
            rows = connection.execute(
                """
                SELECT row_json
                FROM accountability_handoff
                ORDER BY reference_at, snapshot_id
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            connection.close()

    result: list[dict[str, Any]] = []
    for (raw,) in rows:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def acknowledge_accountability_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    output_path: Path = DEFAULT_OUTPUT,
    state_path: Path | None = None,
) -> int:
    """Acknowledge only revisions durably consumed by accountability."""
    if not outcomes:
        return 0
    state_db = state_path or _bounded_state_path(output_path)
    lock = output_path.parent / f".{output_path.name}.lock"
    deleted = 0
    with registry_lock(lock):
        connection = _open_bounded_state(state_db)
        try:
            _reconcile_output_state(connection, output_path)
            for row in outcomes:
                snapshot_id = str(
                    row.get("snapshot_id", "") or ""
                ).strip()
                record_id = str(
                    row.get("outcome_record_id", "") or ""
                ).strip()
                if not snapshot_id or not record_id:
                    continue
                cursor = connection.execute(
                    """
                    DELETE FROM accountability_handoff
                    WHERE snapshot_id = ? AND outcome_record_id = ?
                    """,
                    (snapshot_id, record_id),
                )
                deleted += max(0, int(cursor.rowcount or 0))
            connection.commit()
        finally:
            connection.close()
    return deleted


def _reconcile_snapshot_queue(
    connection: sqlite3.Connection,
    snapshot_path: Path,
    *,
    now: datetime,
) -> None:
    indexed_offset = _state_int(connection, "snapshot_indexed_offset", 0)
    if not snapshot_path.exists():
        if indexed_offset:
            raise RuntimeError("SNAPSHOT_LEDGER_TRUNCATED")
        return

    size = snapshot_path.stat().st_size
    if indexed_offset > size:
        raise RuntimeError("SNAPSHOT_LEDGER_TRUNCATED")
    if not _state_checkpoint_matches(
        connection,
        snapshot_path,
        "snapshot",
        indexed_offset,
    ):
        raise RuntimeError("SNAPSHOT_LEDGER_DIVERGED")

    last_complete = indexed_offset
    with snapshot_path.open("rb") as handle:
        handle.seek(indexed_offset)
        while True:
            raw = handle.readline()
            if not raw:
                break
            end = handle.tell()
            if not raw.endswith(b"\n"):
                break
            last_complete = end
            try:
                snapshot = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(snapshot, dict):
                continue

            snapshot_id = str(snapshot.get("snapshot_id", "") or "")
            decision_at = _parse_utc(snapshot.get("decision_at_utc"))
            if not snapshot_id or decision_at is None:
                continue

            normalized_snapshot = {
                **snapshot,
                "decision_at_utc": decision_at.isoformat(),
            }

            prior = _latest_outcome_row(connection, snapshot_id)
            if (
                prior is not None
                and bool(prior.get("window_complete", False))
                and outcome_label_is_current(prior)
            ):
                connection.execute(
                    "DELETE FROM snapshot_queue WHERE snapshot_id = ?",
                    (snapshot_id,),
                )
                continue

            next_due = (
                _next_due_at(prior, evaluated_at=now)
                if prior is not None
                else decision_at
            )
            if next_due is None:
                connection.execute(
                    "DELETE FROM snapshot_queue WHERE snapshot_id = ?",
                    (snapshot_id,),
                )
                continue
            connection.execute(
                """
                INSERT INTO snapshot_queue(
                    snapshot_id,
                    decision_at,
                    next_due_at,
                    row_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    row_json = excluded.row_json,
                    decision_at = excluded.decision_at
                """,
                (
                    snapshot_id,
                    decision_at.isoformat(),
                    next_due.isoformat(),
                    json.dumps(
                        normalized_snapshot,
                        sort_keys=True,
                        allow_nan=False,
                    ),
                ),
            )

    _set_state_checkpoint(
        connection,
        snapshot_path,
        "snapshot",
        last_complete,
    )
    connection.commit()


def _due_snapshot_batch(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT row_json
        FROM snapshot_queue
        WHERE next_due_at <= ?
        ORDER BY decision_at, snapshot_id
        LIMIT ?
        """,
        (now.isoformat(), int(limit)),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for (raw,) in rows:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _schedule_snapshot_after_evaluation(
    connection: sqlite3.Connection,
    row: dict[str, Any],
    *,
    evaluated_at: datetime,
) -> None:
    snapshot_id = str(row.get("snapshot_id", "") or "")
    if not snapshot_id:
        return
    next_due = _next_due_at(row, evaluated_at=evaluated_at)
    if next_due is None:
        connection.execute(
            "DELETE FROM snapshot_queue WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        return
    connection.execute(
        "UPDATE snapshot_queue SET next_due_at = ? WHERE snapshot_id = ?",
        (next_due.isoformat(), snapshot_id),
    )


def build_outcomes_bounded(
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_LEDGER,
    observation_path: Path = DEFAULT_OBSERVATION_FILE,
    output_path: Path = DEFAULT_OUTPUT,
    state_path: Path | None = None,
    max_snapshots: int = BOUNDED_MAX_SNAPSHOTS,
    now: datetime | None = None,
) -> list[dict]:
    """Mature only a bounded due queue and a bounded observation time slice.

    Snapshot/output cursors live in a disk-backed SQLite sidecar. Historical
    JSONL files are scanned incrementally and never expanded wholesale into
    Python objects on each timer run. Completed 24h labels leave the active
    queue permanently; partial labels are revisited only at the next useful
    horizon milestone.
    """
    if max_snapshots < 1:
        raise ValueError("max_snapshots must be >= 1")
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock = output_path.parent / f".{output_path.name}.lock"
    state_db = state_path or _bounded_state_path(output_path)

    with registry_lock(lock):
        _repair_truncated_jsonl_tail(output_path)
        connection = _open_bounded_state(state_db)
        try:
            _reconcile_output_state(connection, output_path)
            _reconcile_snapshot_queue(
                connection,
                snapshot_path,
                now=evaluated_at,
            )
            snapshots = _due_snapshot_batch(
                connection,
                now=evaluated_at,
                limit=max_snapshots,
            )
            if not snapshots:
                return []

            decision_times = [
                parsed
                for parsed in (
                    _parse_utc(row.get("decision_at_utc"))
                    for row in snapshots
                )
                if parsed is not None
            ]
            symbols = {
                str(row.get("symbol", "") or "").upper()
                for row in snapshots
                if str(row.get("symbol", "") or "").strip()
            }
            if not decision_times or not symbols:
                for snapshot in snapshots:
                    connection.execute(
                        "UPDATE snapshot_queue SET next_due_at = ? WHERE snapshot_id = ?",
                        (
                            (evaluated_at + BOUNDED_RETRY_DELAY).isoformat(),
                            str(snapshot.get("snapshot_id", "") or ""),
                        ),
                    )
                connection.commit()
                return []

            ingestion = read_observations(
                observation_path,
                symbols=symbols,
                start_at=min(decision_times) - BOUNDED_BASELINE_LOOKBACK,
                end_at=max(decision_times) + BOUNDED_FORWARD_GRACE,
            )
            labels = build_forward_outcome_labels(
                snapshots,
                ingestion.observations,
            )
            labels_by_snapshot = {
                str(row.get("snapshot_id", "") or ""): row
                for row in labels
                if str(row.get("snapshot_id", "") or "")
            }

            current: list[dict[str, Any]] = []
            pending: list[dict[str, Any]] = []
            for snapshot in snapshots:
                snapshot_id = str(snapshot.get("snapshot_id", "") or "")
                label = labels_by_snapshot.get(snapshot_id)
                if label is None:
                    next_due = _next_due_at(
                        snapshot,
                        evaluated_at=evaluated_at,
                    )
                    if next_due is None:
                        connection.execute(
                            "DELETE FROM snapshot_queue WHERE snapshot_id = ?",
                            (snapshot_id,),
                        )
                    else:
                        connection.execute(
                            "UPDATE snapshot_queue SET next_due_at = ? "
                            "WHERE snapshot_id = ?",
                            (next_due.isoformat(), snapshot_id),
                        )
                    continue

                record_id = _outcome_record_id(label)
                prior = _latest_outcome_row(connection, snapshot_id)
                if (
                    prior is not None
                    and str(prior.get("outcome_record_id", "") or "") == record_id
                ):
                    current.append(prior)
                    _schedule_snapshot_after_evaluation(
                        connection,
                        prior,
                        evaluated_at=evaluated_at,
                    )
                    continue

                revision = (
                    int(prior.get("outcome_revision", 0) or 0) + 1
                    if prior is not None
                    else 1
                )
                row = {
                    **label,
                    "outcome_record_type": "FORWARD_OUTCOME_MATURATION",
                    "outcome_record_id": record_id,
                    "outcome_revision": revision,
                    "append_only": True,
                }
                pending.append(row)
                current.append(row)

            if pending:
                with output_path.open("ab") as handle:
                    for row in pending:
                        handle.write(
                            (
                                json.dumps(
                                    row,
                                    sort_keys=True,
                                    allow_nan=False,
                                )
                                + "\n"
                            ).encode("utf-8")
                        )
                    handle.flush()
                    os.fsync(handle.fileno())
                    end_offset = handle.tell()

                for row in pending:
                    _upsert_latest_outcome(connection, row)
                    _schedule_snapshot_after_evaluation(
                        connection,
                        row,
                        evaluated_at=evaluated_at,
                    )
                _set_state_checkpoint(
                    connection,
                    output_path,
                    "output",
                    end_offset,
                )

            connection.commit()
            return sorted(
                current,
                key=lambda row: (
                    str(row.get("reference_at", "")),
                    str(row.get("symbol", "")),
                    str(row.get("snapshot_id", "")),
                ),
            )
        finally:
            connection.close()

def build_outcomes(
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_LEDGER,
    observation_path: Path = DEFAULT_OBSERVATION_FILE,
    output_path: Path = DEFAULT_OUTPUT,
) -> list[dict]:
    """Compute current labels and append only new immutable maturation states."""
    snapshots = read_jsonl(snapshot_path)
    ingestion = read_observations(observation_path)
    labels = build_forward_outcome_labels(snapshots, ingestion.observations)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock = output_path.parent / f".{output_path.name}.lock"
    current: list[dict[str, Any]] = []

    # Dedup/revision selection and append share one lock. Two overlapping
    # scheduler invocations therefore cannot both decide that the same
    # maturation state is new and append duplicate revisions.
    with registry_lock(lock):
        # A SIGKILL/OOM can interrupt the final append between bytes. Repair
        # only that incomplete tail before parsing so the next append cannot
        # concatenate onto a corrupt partial JSON record.
        _repair_truncated_jsonl_tail(output_path)
        existing = read_jsonl(output_path)
        latest = _latest_by_snapshot(existing)
        revisions: dict[str, int] = {}
        for row in existing:
            snapshot_id = str(row.get("snapshot_id", "") or "")
            if not snapshot_id:
                continue
            revisions[snapshot_id] = max(
                revisions.get(snapshot_id, 0),
                int(row.get("outcome_revision", 0) or 0),
            )

        pending: list[dict[str, Any]] = []
        for label in labels:
            snapshot_id = str(label.get("snapshot_id", "") or "")
            if not snapshot_id:
                continue
            record_id = _outcome_record_id(label)
            prior = latest.get(snapshot_id)
            if prior and str(prior.get("outcome_record_id", "") or "") == record_id:
                current.append(prior)
                continue

            revision = revisions.get(snapshot_id, 0) + 1
            row = {
                **label,
                "outcome_record_type": "FORWARD_OUTCOME_MATURATION",
                "outcome_record_id": record_id,
                "outcome_revision": revision,
                "append_only": True,
            }
            pending.append(row)
            current.append(row)

        if pending:
            with output_path.open("a", encoding="utf-8") as handle:
                for row in pending:
                    handle.write(
                        json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
                    )
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass

    return sorted(
        current,
        key=lambda row: (
            str(row.get("reference_at", "")),
            str(row.get("symbol", "")),
            str(row.get("snapshot_id", "")),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mature OHM Phase 3C forward outcome labels"
    )
    parser.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOT_LEDGER)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATION_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    labels = build_outcomes_bounded(
        snapshot_path=args.snapshots,
        observation_path=args.observations,
        output_path=args.output,
    )
    complete = sum(1 for row in labels if row.get("window_complete"))
    canonical_episodes = {
        row.get("canonical_episode_id")
        for row in labels
        if row.get("canonical_episode_id")
    }
    print(
        json.dumps(
            {
                "labels": len(labels),
                "complete_24h_windows": complete,
                "canonical_episodes": len(canonical_episodes),
                "source": "PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS",
                "output": str(args.output),
                "append_only": True,
                "trade_authority_changed": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
