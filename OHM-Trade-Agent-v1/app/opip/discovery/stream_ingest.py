"""Absolute-coordinate discovery ingestion over a verified evidence stream.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

The legacy reader stored an offset relative to a mutable HOT file. That offset
becomes meaningless the moment the producer rotates. This module resumes from
a producer-defined absolute coordinate instead, consumes unread archived
segments before the HOT tail, and commits queue effects together with the new
checkpoint in one SQLite transaction.

It never promotes a legacy hot-file offset. A stream that still carries one is
refused until an owner-authorized transition receipt exists.
"""
from __future__ import annotations

import gzip
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterator

from app.opip.discovery.maturation import apply_screening_snapshot_line
from app.opip.storage.screening_stream_frame import (
    StreamCheckpoint,
    StreamContinuityError,
    StreamFrame,
    frame_sha256,
    plan_resume,
    read_absolute,
)

CONSUMER_NAME = "screening_evaluations"
LEGACY_OFFSET_KEY = "screening_indexed_offset"
ANCHOR_BYTES = 4096

CHECKPOINT_SCHEMA = """
CREATE TABLE IF NOT EXISTS consumer_checkpoint (
    consumer_name TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    absolute_offset INTEGER NOT NULL CHECK (absolute_offset >= 0),
    anchor_sha256 TEXT,
    anchor_bytes INTEGER NOT NULL DEFAULT 0,
    verified_frame_sha256 TEXT NOT NULL,
    source_release_sha TEXT,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (consumer_name, lineage_id, stream_id)
)
"""


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def ensure_checkpoint_schema(connection: sqlite3.Connection) -> None:
    connection.execute(CHECKPOINT_SCHEMA)


def _legacy_offset(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (LEGACY_OFFSET_KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        raise StreamContinuityError("LEGACY_OFFSET_INVALID") from None


def load_checkpoint(
    connection: sqlite3.Connection,
    *,
    lineage_id: str,
    consumer_name: str = CONSUMER_NAME,
) -> dict[str, Any] | None:
    ensure_checkpoint_schema(connection)
    row = connection.execute(
        """
        SELECT stream_id, absolute_offset, anchor_sha256, anchor_bytes,
               verified_frame_sha256
        FROM consumer_checkpoint
        WHERE consumer_name = ? AND lineage_id = ?
        """,
        (consumer_name, lineage_id),
    ).fetchone()
    if not row:
        return None
    return {
        "stream_id": row[0],
        "absolute_offset": int(row[1]),
        "anchor_sha256": row[2],
        "anchor_bytes": int(row[3]),
        "verified_frame_sha256": row[4],
    }


def _anchor_window(frame: StreamFrame, position: int) -> tuple[str | None, int]:
    if position <= frame.retained_start:
        return None, 0
    start = max(frame.retained_start, position - ANCHOR_BYTES)
    payload = read_absolute(frame, start, position)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def iter_frame_lines(
    frame: StreamFrame,
    start: int,
) -> Iterator[tuple[bytes, int]]:
    """Yield ``(line, absolute_end)`` from ``start`` across archived then HOT.

    Archived segments are consumed before the HOT tail, which is what lets a
    consumer that fell behind during one or more rotations catch up without
    skipping evidence.
    """
    if start < frame.retained_start or start > frame.end:
        raise StreamContinuityError("checkpoint outside retained stream")
    for part in frame.verify_layout():
        if part.end <= start:
            continue
        offset = max(start, part.start)
        handle = gzip.open(part.path, "rb") if part.archived else part.path.open("rb")
        with handle:
            if offset > part.start:
                handle.seek(offset - part.start)
            position = offset
            for raw in handle:
                if not raw.endswith(b"\n"):
                    return
                position += len(raw)
                yield raw, position


def _resolve_start(
    connection: sqlite3.Connection,
    *,
    frame: StreamFrame,
    lineage_id: str,
    consumer_name: str,
    authorized_cutover_receipt: str | None,
) -> tuple[StreamCheckpoint, int]:
    """Return the checkpoint to resume from and its previous committed offset."""
    existing = load_checkpoint(
        connection, lineage_id=lineage_id, consumer_name=consumer_name
    )
    if existing is not None:
        return (
            StreamCheckpoint(
                stream_id=existing["stream_id"],
                position=existing["absolute_offset"],
                anchor_sha256=existing["anchor_sha256"] or "",
                anchor_bytes=existing["anchor_bytes"],
            ),
            existing["absolute_offset"],
        )
    # No framed checkpoint yet. A populated legacy hot offset is never promoted.
    if _legacy_offset(connection):
        if not authorized_cutover_receipt:
            raise StreamContinuityError("LEGACY_COORDINATE_UNPROVEN")
    position = frame.retained_start
    anchor, size = _anchor_window(frame, position)
    return (
        StreamCheckpoint(
            stream_id=frame.stream_id,
            position=position,
            anchor_sha256=anchor or "",
            anchor_bytes=size,
        ),
        0,
    )


def ingest_framed(
    connection: sqlite3.Connection,
    *,
    frame: StreamFrame,
    expected_frame_sha256: str,
    lineage_id: str,
    now: datetime,
    consumer_name: str = CONSUMER_NAME,
    source_release_sha: str = "",
    verify_bytes: bool = False,
    authorized_cutover_receipt: str | None = None,
) -> dict[str, Any]:
    """Consume one verified frame; commit effects and checkpoint atomically.

    Rows are applied and the checkpoint advanced inside a single IMMEDIATE
    transaction, so a crash can never leave effects behind a stale checkpoint
    or a checkpoint ahead of its effects. The optimistic WHERE clause refuses a
    concurrent advance instead of silently overwriting it.
    """
    ensure_checkpoint_schema(connection)
    checkpoint, previous_offset = _resolve_start(
        connection,
        frame=frame,
        lineage_id=lineage_id,
        consumer_name=consumer_name,
        authorized_cutover_receipt=authorized_cutover_receipt,
    )
    plan = plan_resume(
        frame,
        checkpoint,
        expected_frame_sha256=expected_frame_sha256,
        verify_bytes=verify_bytes,
    )

    start = checkpoint.position
    position = start
    applied = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for raw, absolute_end in iter_frame_lines(frame, start):
            apply_screening_snapshot_line(connection, raw, now=now)
            position = absolute_end
            applied += 1
        anchor, anchor_bytes = _anchor_window(frame, position)
        changed = connection.execute(
            """
            INSERT INTO consumer_checkpoint(
                consumer_name, lineage_id, stream_id, absolute_offset,
                anchor_sha256, anchor_bytes, verified_frame_sha256,
                source_release_sha, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(consumer_name, lineage_id, stream_id) DO UPDATE SET
                absolute_offset = excluded.absolute_offset,
                anchor_sha256 = excluded.anchor_sha256,
                anchor_bytes = excluded.anchor_bytes,
                verified_frame_sha256 = excluded.verified_frame_sha256,
                source_release_sha = excluded.source_release_sha,
                updated_at_utc = excluded.updated_at_utc
            WHERE consumer_checkpoint.absolute_offset = ?
            """,
            (
                consumer_name,
                lineage_id,
                frame.stream_id,
                position,
                anchor,
                anchor_bytes,
                frame_sha256(frame),
                source_release_sha,
                _utc(now),
                previous_offset,
            ),
        ).rowcount
        if changed != 1:
            raise StreamContinuityError("CHECKPOINT_CONCURRENT_ADVANCE")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "measurement_only": True,
        "trade_authority_changed": False,
        "consumer": consumer_name,
        "resumed_from": start,
        "checkpoint": position,
        "records_applied": applied,
        "parts_planned": len(plan),
        "stream_id": frame.stream_id,
    }

