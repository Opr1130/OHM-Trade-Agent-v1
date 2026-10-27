"""ATDD-RELEASE-TREE-DEPLOY-v1/AC-010 - canonical cursor read plan.

The release runtime verifier pages through canonical evidence with an exclusive
`(history_epoch, local_sequence)` cursor and `ORDER BY history_epoch,
local_sequence LIMIT n`. When no index serves that order, SQLite materializes
and sorts the ENTIRE per-type backlog just to return one page: production held
~69k rows of ~3 KB payloads (~215 MB) per read and overran the declared
single-read bound (53.794s observed against a 10s bound).

This test pins the plan, not a duration: a page must be an index range seek and
must never build a temp B-tree, and the canonical order/cursor semantics must be
unchanged by the index.
"""

from __future__ import annotations

import pathlib

import pytest

from app.opip.canonical import schema as canonical_schema
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.writer import CanonicalWriter

pytestmark = pytest.mark.acceptance

CURSOR_SQL = (
    "SELECT history_epoch, local_sequence, payload_json FROM events "
    "WHERE event_type = ? AND (history_epoch > ? OR "
    "(history_epoch = ? AND local_sequence > ?)) "
    "ORDER BY history_epoch ASC, local_sequence ASC LIMIT ?"
)

EVENT_TYPES = ("feature.snapshot.recorded", "feasibility.evidence.recorded")


def test_canonical_cursor_page_is_an_index_seek_not_a_full_sort(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-010: both canonical cursor reads are served
    by the ordering index, so one read costs its page and never sorts the whole
    per-type backlog."""
    db = tmp_path / "canonical.sqlite3"
    conn = canonical_schema.connect(db, read_only=False)
    try:
        canonical_schema.initialize_schema(conn, now_iso="2026-01-01T00:00:00Z")
        names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_events_type_cursor" in names

        rows = (
            ("EVT:a", "feature.snapshot.recorded", 1, 1),
            ("EVT:b", "feature.snapshot.recorded", 1, 2),
            ("EVT:c", "feasibility.evidence.recorded", 1, 3),
            ("EVT:d", "feature.snapshot.recorded", 2, 4),
        )
        for event_id, event_type, epoch, sequence in rows:
            conn.execute(
                "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
                "local_sequence, recorded_at, idempotency_key, payload_json) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    SCHEMA_VERSION,
                    event_type,
                    epoch,
                    sequence,
                    "2026-01-01T00:00:00Z",
                    f"idem:{event_id}",
                    '{"pad":"' + "x" * 64 + '"}',
                ),
            )
        conn.commit()

        for event_type in EVENT_TYPES:
            plan = " | ".join(
                str(row[3])
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN " + CURSOR_SQL, (event_type, 1, 1, 0, 1000)
                )
            )
            assert "idx_events_type_cursor" in plan, plan
            assert "TEMP B-TREE" not in plan, plan
    finally:
        conn.close()


def test_the_index_preserves_canonical_order_and_cursor_semantics(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-010: the index changes the plan only; the
    canonical order and the exclusive cursor are unchanged."""
    db = tmp_path / "canonical.sqlite3"
    conn = canonical_schema.connect(db, read_only=False)
    try:
        canonical_schema.initialize_schema(conn, now_iso="2026-01-01T00:00:00Z")
        for event_id, epoch, sequence in (
            ("EVT:a", 1, 1),
            ("EVT:b", 1, 2),
            ("EVT:c", 2, 3),
        ):
            conn.execute(
                "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
                "local_sequence, recorded_at, idempotency_key, payload_json) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    SCHEMA_VERSION,
                    "feature.snapshot.recorded",
                    epoch,
                    sequence,
                    "2026-01-01T00:00:00Z",
                    f"idem:{event_id}",
                    '{"pad":"x"}',
                ),
            )
        conn.commit()
    finally:
        conn.close()

    reader = CanonicalWriter.for_reads(db)
    try:
        envelopes, cursor = reader.read_feature_snapshot_rows(after=(1, 1), limit=10)
    finally:
        reader.close()
    assert [row["event_id"] for row in envelopes] == ["EVT:b", "EVT:c"]
    assert cursor == (2, 3)
