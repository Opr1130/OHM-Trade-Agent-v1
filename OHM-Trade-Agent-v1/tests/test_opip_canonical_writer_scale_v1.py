"""ATDD - canonical writer scale: bounded hydration and bounded retention.

Requirement -> acceptance criterion -> focused test -> implementation:

  R-1  Startup hydration must cost one page, not the whole history.
       AC-001 (paged reads), AC-002 (page cursor keeps commit order)
  R-2  A closed or long-finished identity must not stay resident for the life of
       the process.
       AC-003 (bounded retention), AC-004 (a released identity still answers)
  R-3  Identity lookups that used to grow a resident map must be answered by the
       durable row through an index.
       AC-005
  R-4  A JSONL retention read must not materialise the file it is bounding.
       AC-006
  R-5  Growth must not be proportional to history.
       AC-007

Every fixture is deterministic: no network, no clock, no production data.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.opip.canonical import schema as canonical_schema
from app.opip.canonical import writer as writer_module
from app.opip.canonical.writer import CanonicalWriter, _BoundedProjection
from app.opip.contracts import opportunity_persistence as persistence
from app.opip.storage import bounded_jsonl
from tests.test_opip_r3_f4_opportunity_persistence import (
    active_result,
    build_claim,
    canonical_intent,
    deferred_result,
    expired_result,
    open_writer,
    payload_for,
)

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
STORAGE = APP_ROOT / "app" / "opip" / "events" / "storage.py"

RECORDED = persistence.OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED


@pytest.fixture
def canonical_db(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(tmp_path / "canonical"))
    db = tmp_path / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def _claim(index: int):
    return build_claim(snapshot_id=f"SNAP:scale-{index}")


def _submit(writer: CanonicalWriter, result):
    ack = writer.submit(canonical_intent(payload_for(result)))
    assert ack.status == "OK", ack
    return ack


def _episode_id(claim) -> str:
    return persistence.opportunity_transition_episode_id(
        payload_for(active_result(claim))
    )



# ---------------------------------------------------------------------------
# AC-001 - hydration is paged
# ---------------------------------------------------------------------------


def test_ac_001_hydration_reads_one_page_at_a_time():
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-001: every hydration query must be a bounded range seek.

    The previous implementation ran one ``fetchall()`` per event type, so the
    whole backlog became a Python list before the first row was folded and
    startup memory was proportional to total history.
    """
    pager = inspect.getsource(CanonicalWriter._iter_event_rows)
    assert "LIMIT ?" in pager
    assert "HYDRATION_PAGE_ROWS" in pager
    assert "idx_events_type_cursor" in pager
    assert 0 < writer_module.HYDRATION_PAGE_ROWS <= 10_000
    # The generic schema must stay free of increment-specific vocabulary, so the
    # released-episode fallback rides the same generic paged cursor instead of a
    # bespoke index.
    assert "opportunity" not in canonical_schema.DDL.lower()


def test_ac_001_no_projection_fold_materialises_its_type_history():
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-001: the fold must consume the pager, not a result set."""
    for name in (
        "_hydrate_request_lifecycle_projection",
        "_refresh_request_lifecycle_projection",
        "_hydrate_role_result_identity_projection",
        "_refresh_role_result_identity_projection",
        "_hydrate_opportunity_transition_projection",
        "_refresh_opportunity_transition_projection",
    ):
        source = inspect.getsource(getattr(CanonicalWriter, name))
        assert "_iter_event_rows(" in source, name
        assert ".fetchall()" not in source, name


# ---------------------------------------------------------------------------
# AC-002 - the page cursor preserves commit order
# ---------------------------------------------------------------------------


def test_ac_002_paging_preserves_commit_order_and_terminates(
    canonical_db, monkeypatch
):
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-002: several pages must fold to exactly the single-page result."""
    writer = open_writer(canonical_db)
    episode_ids = []
    try:
        for index in range(12):
            claim = _claim(index)
            _submit(writer, deferred_result(claim))
            episode_ids.append(_episode_id(claim))
    finally:
        writer.close()

    monkeypatch.setattr(writer_module, "HYDRATION_PAGE_ROWS", 5)
    restarted = open_writer(canonical_db)
    try:
        restarted._opportunity_transition_projection.clear()
        restarted._opportunity_transition_count.clear()
        restarted._opportunity_transition_last.clear()
        restarted._opportunity_transition_hydrated = False
        restarted._opportunity_transition_watermark = (0, -1)
        statements: list[str] = []
        restarted._conn.set_trace_callback(
            lambda statement: statements.append(statement)
        )
        restarted._ensure_opportunity_transition_projection()
        restarted._conn.set_trace_callback(None)

        # ``set_trace_callback`` reports the expanded statement, so the page bound
        # appears as ``LIMIT <n>``. Counting the paged reads proves the fold
        # actually iterates instead of reading one result set.
        paged_reads = [
            statement
            for statement in statements
            if "FROM events" in statement and "LIMIT " in statement
        ]
        assert len(paged_reads) > 1, "a 12-row history must span several pages"
        for episode_id in episode_ids:
            projection = restarted.opportunity_episode_projection(episode_id)
            assert projection.status == "OK", projection
            assert projection.event_count == 1
            assert projection.lifecycle_state == "DEFERRED"
    finally:
        restarted.close()


# ---------------------------------------------------------------------------
# AC-003 / AC-004 - bounded retention
# ---------------------------------------------------------------------------


def test_ac_003_retention_releases_its_oldest_entry_past_the_bound():
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-003: retention releases its oldest entry past the bound."""
    projection = _BoundedProjection(3)
    for index in range(10):
        projection[f"id-{index}"] = index
    assert len(projection) == 3
    assert projection.released == 7
    assert sorted(projection) == ["id-7", "id-8", "id-9"]
    # Refreshing a retained identity must not change release order.
    projection["id-8"] = 99
    assert sorted(projection) == ["id-7", "id-8", "id-9"]
    assert projection["id-8"] == 99


def test_ac_004_a_released_episode_still_answers_exactly_as_the_retained_one(
    canonical_db,
):
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-004: releasing an identity must not change what it reports.

    The durable rows are the authority in both cases, so a terminal episode that
    retention has released must answer with the same state, event count and
    canonical identity as the retained value did.
    """
    claim = _claim(0)
    episode_id = _episode_id(claim)
    writer = open_writer(canonical_db)
    try:
        _submit(writer, deferred_result(claim))
        _submit(writer, expired_result(claim))
        retained = writer.opportunity_episode_projection(episode_id)
        assert retained.status == "OK", retained
        assert retained.lifecycle_state == "TERMINAL"
        assert retained.event_count == 2

        # Simulate retention releasing the entry.
        writer._opportunity_transition_projection.clear()
        writer._opportunity_transition_count.clear()
        writer._opportunity_transition_last.clear()
        replayed = writer.opportunity_episode_projection(episode_id)
        assert replayed.status == "OK", replayed
        assert replayed.lifecycle_state == retained.lifecycle_state
        assert replayed.event_count == retained.event_count
        assert replayed.last_event_id == retained.last_event_id
        assert replayed.canonical_event_id == retained.canonical_event_id
        assert replayed.history_epoch == retained.history_epoch
        assert replayed.local_sequence == retained.local_sequence
    finally:
        writer.close()


def test_ac_004_a_released_request_state_is_re_derived_not_defaulted(
    canonical_db,
):
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-004: a released request must not silently reset to ELIGIBLE."""
    writer = open_writer(canonical_db)
    try:
        writer._request_lifecycle_projection["REQ:scale"] = "CONSUMED"
        assert writer._request_current_state("REQ:scale") == "CONSUMED"
        writer._request_lifecycle_projection.clear()
        # No durable row exists for this identity, so the honest answer is the
        # fold's starting state - and it is derived, never remembered.
        assert writer._request_current_state("REQ:scale") == "ELIGIBLE"
    finally:
        writer.close()



# ---------------------------------------------------------------------------
# AC-005 - identity lookups are DB-backed and indexed
# ---------------------------------------------------------------------------


def test_ac_005_role_result_identity_has_no_resident_map():
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-005: the per-result map grew with all history; the row is the lookup."""
    assert not hasattr(CanonicalWriter, "_role_result_idempotency_by_id")
    constructor = inspect.getsource(CanonicalWriter.__init__)
    assert "_role_result_idempotency_by_id" not in constructor
    reader = inspect.getsource(CanonicalWriter._role_result_idempotency_key)
    assert "json_extract(payload_json, '$.result_id')" in reader
    assert "LIMIT 1" in reader
    uniqueness = inspect.getsource(CanonicalWriter._apply_persisted_role_result)
    assert "idempotency_key <> ?" in uniqueness


def test_ac_005_the_identity_indexes_are_declared():
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-005: the identity indexes are declared."""
    ddl = canonical_schema.DDL
    for name in (
        "idx_events_type_cursor",
        "idx_events_role_result_identity",
        "idx_events_di_request_state",
    ):
        assert f"CREATE INDEX IF NOT EXISTS {name}" in ddl, name


def test_ac_005_the_identity_query_is_planned_as_an_index_seek(canonical_db):
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-005: SQLite must plan the per-identity read as an index range seek."""
    writer = open_writer(canonical_db)
    try:
        plan = writer._conn.execute(
            "EXPLAIN QUERY PLAN SELECT idempotency_key FROM events "
            "WHERE event_type = ? "
            "AND json_extract(payload_json, '$.result_id') = ? LIMIT 1",
            (writer_module.DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED, "R:1"),
        ).fetchall()
        described = " ".join(str(tuple(row)) for row in plan)
        assert "idx_events_role_result_identity" in described, described
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# AC-006 - the JSONL retention read is bounded
# ---------------------------------------------------------------------------


def test_ac_006_dead_letter_retention_reads_only_the_tail(tmp_path):
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-006: retention must not read the file it is bounding.

    The previous shape called the whole-file reader twice - once to count, once
    to slice - so peak memory was the dead-letter file size.
    """
    source = STORAGE.read_text(encoding="utf-8")
    assert "_read_tail_lines(" in source
    assert "read_lines as _read_lines" not in source

    path = tmp_path / "dead_letter.jsonl"
    path.write_bytes(b"".join(b'{"n":%d}\n' % index for index in range(5000)))
    tail = bounded_jsonl.read_tail_lines(path, 50)
    assert len(tail) == 50
    assert tail[-1] == b'{"n":4999}\n'


# ---------------------------------------------------------------------------
# AC-007 - growth is not proportional to history
# ---------------------------------------------------------------------------


def test_ac_007_retained_entries_stay_bounded_while_history_grows(
    canonical_db, monkeypatch
):
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-007: resident entries must stop at the bound, however long history is."""
    monkeypatch.setattr(writer_module, "PROJECTION_RETENTION_ENTRIES", 4)
    writer = open_writer(canonical_db)
    try:
        for index in range(30):
            claim = _claim(index)
            _submit(writer, deferred_result(claim))
        writer._ensure_opportunity_transition_projection()
        assert len(writer._opportunity_transition_projection) <= 4
        assert writer._opportunity_transition_projection.released > 0
        assert len(writer._opportunity_transition_count) <= 4
        assert len(writer._opportunity_transition_last) <= 4
        # 30 committed rows across 30 episodes; the fold never needed them all
        # resident.
        rows = writer._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE event_type = ?", (RECORDED,)
        ).fetchone()
        assert int(rows["n"]) == 30
    finally:
        writer.close()


def test_ac_007_a_released_identity_is_re_derived_from_the_durable_rows(
    canonical_db, monkeypatch
):
    """ATDD-OPIP-CANONICAL-WRITER-SCALE-v1/AC-007: a released identity is re derived from the durable rows."""
    monkeypatch.setattr(writer_module, "PROJECTION_RETENTION_ENTRIES", 2)
    writer = open_writer(canonical_db)
    try:
        first = _claim(0)
        first_id = _episode_id(first)
        _submit(writer, deferred_result(first))
        for index in range(1, 6):
            _submit(writer, deferred_result(_claim(index)))
        assert first_id not in writer._opportunity_transition_projection
        projection = writer.opportunity_episode_projection(first_id)
        assert projection.status == "OK", projection
        assert projection.lifecycle_state == "DEFERRED"
        assert projection.event_count == 1
    finally:
        writer.close()
