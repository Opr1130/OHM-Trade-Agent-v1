"""ATDD: absolute-coordinate screening ingestion over a verified stream frame.

Proves the production incident cannot recur silently: a rotated stream resumes
from a proven absolute position, unread archived evidence is consumed before
HOT, replays are idempotent, and every unprovable state fails closed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.discovery.maturation import open_discovery_state
from app.opip.discovery.stream_ingest import (
    ensure_checkpoint_schema,
    ingest_framed,
    load_checkpoint,
)
from app.opip.storage.screening_stream_frame import (
    StreamContinuityError,
    StreamFrame,
    StreamPart,
    frame_sha256,
)

NOW = datetime(2026, 10, 23, 0, 30, tzinfo=timezone.utc)
STREAM_ID = hashlib.sha256(b"opip:v1:screening_evaluations:epoch").hexdigest()


def _row(index: int) -> bytes:
    return (
        json.dumps(
            {
                "scanner_type": "BROAD_SEARCH",
                "outcome": "COMPLETED",
                "observed_at": f"2026-10-22T21:{index:02d}:00+00:00",
                "venue_instrument_id": f"VENUE-{index}",
                "metadata": {"observation_id": f"obs-{index}"},
            },
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _part(tmp_path: Path, name: str, payload: bytes, start: int, *, archived: bool):
    if archived:
        path = tmp_path / f"{name}.jsonl.gz"
        with gzip.open(path, "wb") as handle:
            handle.write(payload)
    else:
        path = tmp_path / f"{name}.jsonl"
        path.write_bytes(payload)
    return StreamPart(
        path=path,
        start=start,
        end=start + len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        raw_sha256=hashlib.sha256(payload).hexdigest(),
        archived=archived,
        row_count=payload.count(b"\n") if archived else None,
    )


def _frame(parts, retained_start: int = 0) -> StreamFrame:
    end = parts[-1].end if parts else retained_start
    return StreamFrame(
        stream_id=STREAM_ID,
        retained_start=retained_start,
        end=end,
        parts=tuple(parts),
    )


def _state(tmp_path: Path):
    return open_discovery_state(tmp_path / ".state.sqlite3")


def _queue(connection) -> list[str]:
    rows = connection.execute(
        "SELECT observation_id FROM observation_queue ORDER BY observation_id"
    ).fetchall()
    return [row[0] for row in rows]


def test_archived_evidence_is_consumed_before_hot(tmp_path):
    first, second, hot = _row(1) + _row(2), _row(3), _row(4)
    parts = [
        _part(tmp_path, "one", first, 0, archived=True),
        _part(tmp_path, "two", second, len(first), archived=True),
        _part(tmp_path, "hot", hot, len(first) + len(second), archived=False),
    ]
    frame = _frame(parts)
    connection = _state(tmp_path)
    try:
        summary = ingest_framed(
            connection,
            frame=frame,
            expected_frame_sha256=frame_sha256(frame),
            lineage_id="LINEAGE-B-V2-GENESIS",
            now=NOW,
        )
        assert summary["records_applied"] == 4
        assert summary["checkpoint"] == frame.end
        assert _queue(connection) == ["obs-1", "obs-2", "obs-3", "obs-4"]
    finally:
        connection.close()


def test_replay_is_idempotent(tmp_path):
    payload = _row(5)
    parts = [_part(tmp_path, "hot", payload, 0, archived=False)]
    frame = _frame(parts)
    sha = frame_sha256(frame)
    connection = _state(tmp_path)
    try:
        ingest_framed(
            connection,
            frame=frame,
            expected_frame_sha256=sha,
            lineage_id="LINEAGE-B-V2-GENESIS",
            now=NOW,
        )
        again = ingest_framed(
            connection,
            frame=frame,
            expected_frame_sha256=sha,
            lineage_id="LINEAGE-B-V2-GENESIS",
            now=NOW,
        )
        assert again["records_applied"] == 0
        assert again["resumed_from"] == frame.end
        assert _queue(connection) == ["obs-5"]
    finally:
        connection.close()



def test_rotation_after_consumption_resumes_at_absolute_position(tmp_path):
    archived, tail = _row(1) + _row(2), _row(3)
    before = _frame(
        [
            _part(tmp_path, "one", archived, 0, archived=True),
            _part(tmp_path, "hot", tail, len(archived), archived=False),
        ]
    )
    connection = _state(tmp_path)
    try:
        ingest_framed(
            connection,
            frame=before,
            expected_frame_sha256=frame_sha256(before),
            lineage_id="LINEAGE-B-V2-GENESIS",
            now=NOW,
        )
        appended = _row(4)
        after = _frame(
            [
                _part(tmp_path, "one", archived, 0, archived=True),
                _part(tmp_path, "two", tail, len(archived), archived=True),
                _part(
                    tmp_path,
                    "hot2",
                    appended,
                    len(archived) + len(tail),
                    archived=False,
                ),
            ]
        )
        summary = ingest_framed(
            connection,
            frame=after,
            expected_frame_sha256=frame_sha256(after),
            lineage_id="LINEAGE-B-V2-GENESIS",
            now=NOW,
        )
        assert summary["resumed_from"] == len(archived) + len(tail)
        assert summary["records_applied"] == 1
        assert _queue(connection) == ["obs-1", "obs-2", "obs-3", "obs-4"]
    finally:
        connection.close()


def test_legacy_offset_is_never_promoted(tmp_path):
    parts = [_part(tmp_path, "hot", _row(6), 0, archived=False)]
    frame = _frame(parts)
    connection = _state(tmp_path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("screening_indexed_offset", "61049583"),
        )
        connection.commit()
        with pytest.raises(StreamContinuityError, match="LEGACY_COORDINATE_UNPROVEN"):
            ingest_framed(
                connection,
                frame=frame,
                expected_frame_sha256=frame_sha256(frame),
                lineage_id="LINEAGE-B-V2-GENESIS",
                now=NOW,
            )
        assert load_checkpoint(connection, lineage_id="LINEAGE-B-V2-GENESIS") is None
    finally:
        connection.close()


def test_retention_floor_prunes_checkpoint(tmp_path):
    payload = _row(7) + _row(8)
    parts = [_part(tmp_path, "hot", payload, 10, archived=False)]
    frame = _frame(parts, retained_start=10)
    connection = _state(tmp_path)
    try:
        ensure_checkpoint_schema(connection)
        connection.execute(
            """
            INSERT INTO consumer_checkpoint(
                consumer_name, lineage_id, stream_id, absolute_offset,
                anchor_sha256, anchor_bytes, verified_frame_sha256,
                source_release_sha, updated_at_utc
            ) VALUES (?, ?, ?, ?, NULL, 0, ?, '', ?)
            """,
            (
                "screening_evaluations",
                "LINEAGE-B-V2-GENESIS",
                STREAM_ID,
                0,
                frame_sha256(frame),
                "2026-10-23T00:00:00+00:00",
            ),
        )
        connection.commit()
        with pytest.raises(StreamContinuityError, match="EVIDENCE_PRUNED"):
            ingest_framed(
                connection,
                frame=frame,
                expected_frame_sha256=frame_sha256(frame),
                lineage_id="LINEAGE-B-V2-GENESIS",
                now=NOW,
            )
    finally:
        connection.close()


def test_equal_length_hot_replacement_is_refused(tmp_path):
    original = _row(9)
    frame = _frame([_part(tmp_path, "hot", original, 0, archived=False)])
    connection = _state(tmp_path)
    try:
        ingest_framed(
            connection,
            frame=frame,
            expected_frame_sha256=frame_sha256(frame),
            lineage_id="LINEAGE-B-V2-GENESIS",
            now=NOW,
        )
        replacement = _row(7)
        assert len(replacement) == len(original)
        swapped = _frame([_part(tmp_path, "hot2", replacement, 0, archived=False)])
        with pytest.raises(StreamContinuityError, match="anchor mismatch"):
            ingest_framed(
                connection,
                frame=swapped,
                expected_frame_sha256=frame_sha256(swapped),
                lineage_id="LINEAGE-B-V2-GENESIS",
                now=NOW,
            )
    finally:
        connection.close()


def test_untrusted_frame_digest_is_refused(tmp_path):
    frame = _frame([_part(tmp_path, "hot", _row(11), 0, archived=False)])
    connection = _state(tmp_path)
    try:
        with pytest.raises(StreamContinuityError, match="FRAME_AUTHENTICITY_MISMATCH"):
            ingest_framed(
                connection,
                frame=frame,
                expected_frame_sha256="0" * 64,
                lineage_id="LINEAGE-B-V2-GENESIS",
                now=NOW,
            )
    finally:
        connection.close()
