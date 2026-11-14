"""Measurement-tool correctness for the canonical scale probe.

Each test cites its criterion in ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1. They exist
because two defects made the probe's numbers untrustworthy: a raw file copy that
omits committed-but-uncheckpointed WAL frames, and an index "timing" that could be
the cost of a no-op ``CREATE INDEX IF NOT EXISTS``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

from app.opip.canonical import writer as writer_module
from app.opip.canonical.writer import CanonicalWriter

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "tools"))
try:
    import opip_canonical_scale_probe as probe
finally:
    sys.path.pop(0)


def _wal_source(path: Path, rows: int = 50) -> sqlite3.Connection:
    """A WAL store whose committed rows live ONLY in the WAL (autocheckpoint off).

    The returned connection stays open, so SQLite cannot checkpoint on close.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, "
        "history_epoch INTEGER NOT NULL, local_sequence INTEGER NOT NULL, "
        "payload_json TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE idempotency_keys (k TEXT PRIMARY KEY)")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for index in range(rows):
        conn.execute(
            "INSERT INTO events VALUES (?, 'x.y', 1, ?, '{}')", (f"e{index}", index + 1)
        )
        conn.execute("INSERT INTO idempotency_keys VALUES (?)", (f"k{index}",))
        conn.commit()
    return conn


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def _events(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-001 - committed WAL data is in the snapshot (and a raw copy would lose it)
# ---------------------------------------------------------------------------


def test_ac_001_a_raw_copy_loses_uncheckpointed_commits_the_snapshot_keeps_them(
    tmp_path,
):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-001: the snapshot contains every committed row, including those only in the WAL."""
    source = tmp_path / "src" / "c.sqlite3"
    live = _wal_source(source, rows=50)
    try:
        assert Path(str(source) + "-wal").stat().st_size > 0
        raw = tmp_path / "raw.sqlite3"
        shutil.copyfile(source, raw)
        # The defect being fixed: the old probe's copy would have measured this.
        assert _events(raw) == 0

        info = probe.snapshot_database(source, tmp_path / "snap" / "s.sqlite3")
        assert info["events"] == 50
        assert _events(tmp_path / "snap" / "s.sqlite3") == 50
        assert info["tables"] == {"events": 50, "idempotency_keys": 50}
        assert info["integrity"] == "ok"
    finally:
        live.close()


def test_ac_001_the_snapshot_is_a_single_self_contained_file(tmp_path):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-001: no WAL or shm sidecar is needed to read the snapshot."""
    source = tmp_path / "src" / "c.sqlite3"
    live = _wal_source(source, rows=10)
    try:
        target = tmp_path / "snap" / "s.sqlite3"
        probe.snapshot_database(source, target)
        assert not Path(str(target) + "-wal").exists()
        assert not Path(str(target) + "-shm").exists()
        conn = sqlite3.connect(str(target))
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        finally:
            conn.close()
    finally:
        live.close()


# ---------------------------------------------------------------------------
# AC-002 - the source is never modified, checkpointed or blocked
# ---------------------------------------------------------------------------


def test_ac_002_the_source_database_and_wal_are_byte_identical_afterwards(tmp_path):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-002: snapshotting writes nothing to the source and does not checkpoint it."""
    source = tmp_path / "src" / "c.sqlite3"
    live = _wal_source(source, rows=30)
    try:
        wal = Path(str(source) + "-wal")
        before = (_sha(source), _sha(wal), wal.stat().st_size)
        probe.snapshot_database(source, tmp_path / "snap" / "s.sqlite3")
        assert (_sha(source), _sha(wal), wal.stat().st_size) == before
    finally:
        live.close()


def test_ac_002_a_live_writer_keeps_committing_during_and_after_the_snapshot(tmp_path):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-002: the snapshot takes only a read transaction, so writers are never blocked and the copy is self-consistent."""
    source = tmp_path / "src" / "c.sqlite3"
    live = _wal_source(source, rows=200)
    stop = threading.Event()
    errors: list[BaseException] = []

    def keep_writing() -> None:
        writer = sqlite3.connect(str(source), timeout=30.0)
        index = 10_000
        try:
            while not stop.is_set():
                writer.execute(
                    "INSERT INTO events VALUES (?, 'x.y', 1, ?, '{}')",
                    (f"w{index}", index),
                )
                writer.execute("INSERT INTO idempotency_keys VALUES (?)", (f"wk{index}",))
                writer.commit()
                index += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            writer.close()

    thread = threading.Thread(target=keep_writing)
    thread.start()
    try:
        for round_index in range(5):
            target = tmp_path / "snap" / f"s{round_index}.sqlite3"
            info = probe.snapshot_database(source, target)
            # One consistent view: events and their keys always agree in the copy.
            assert info["tables"]["events"] == info["tables"]["idempotency_keys"]
            assert info["events"] >= 200
    finally:
        stop.set()
        thread.join(timeout=30)
        live.close()
    assert errors == [], errors


# ---------------------------------------------------------------------------
# AC-003 - fail closed
# ---------------------------------------------------------------------------


def test_ac_003_a_count_mismatch_fails_closed_and_leaves_no_snapshot(
    tmp_path, monkeypatch
):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-003: if source and snapshot disagree, no snapshot is left behind."""
    source = tmp_path / "src" / "c.sqlite3"
    live = _wal_source(source, rows=5)
    try:
        real = probe._table_counts
        calls = {"n": 0}

        def skewed(conn, tables):
            calls["n"] += 1
            counts = real(conn, tables)
            if calls["n"] == 2:  # the destination read
                counts["events"] += 1
            return counts

        monkeypatch.setattr(probe, "_table_counts", skewed)
        target = tmp_path / "snap" / "s.sqlite3"
        with pytest.raises(probe.SnapshotError, match="counts differ"):
            probe.snapshot_database(source, target)
        assert not target.exists()
    finally:
        live.close()


def test_ac_003_unreliable_sources_are_refused_without_a_file_copy_fallback(tmp_path):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-003: missing, non-canonical and corrupt sources and an existing destination all fail closed."""
    with pytest.raises(probe.SnapshotError, match="does not exist"):
        probe.snapshot_database(tmp_path / "nope.sqlite3", tmp_path / "o.sqlite3")

    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE t (a)")
    conn.commit()
    conn.close()
    with pytest.raises(probe.SnapshotError, match="not a canonical store"):
        probe.snapshot_database(other, tmp_path / "o2.sqlite3")
    assert not (tmp_path / "o2.sqlite3").exists()

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(probe.SnapshotError):
        probe.snapshot_database(corrupt, tmp_path / "o3.sqlite3")
    assert not (tmp_path / "o3.sqlite3").exists()

    source = tmp_path / "src" / "c.sqlite3"
    live = _wal_source(source, rows=3)
    try:
        existing = tmp_path / "exists.sqlite3"
        existing.write_text("keep me")
        with pytest.raises(probe.SnapshotError, match="overwrite"):
            probe.snapshot_database(source, existing)
        assert existing.read_text() == "keep me"
    finally:
        live.close()

    text = (APP_ROOT / "tools" / "opip_canonical_scale_probe.py").read_text(
        encoding="utf-8"
    )
    assert "shutil" not in text, "a raw file copy fallback must not exist"
    with pytest.raises(SystemExit):
        probe.snapshot_database(
            Path("/app/data/canonical/c.sqlite3"), tmp_path / "x.sqlite3"
        )


# ---------------------------------------------------------------------------
# AC-004 - index timings only describe indexes that were really created
# ---------------------------------------------------------------------------


def _canonical_store(path: Path, *, drop_indexes: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    CanonicalWriter(path).close()
    if drop_indexes:
        conn = sqlite3.connect(str(path))
        for name in probe.INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()
        conn.close()


def _args(db: Path, **extra) -> argparse.Namespace:
    return argparse.Namespace(db=str(db), workdir=None, prepare=False, **extra)


def test_ac_004_missing_indexes_are_created_and_timed(tmp_path, monkeypatch):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-004: a store lacking the indexes reports CREATED with a build time for each."""
    db = tmp_path / "c" / "c.sqlite3"
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(db.parent))
    _canonical_store(db, drop_indexes=True)
    out = probe.cmd_indexes(_args(db))
    assert out["created"] == len(probe.INDEXES)
    for name in probe.INDEXES:
        assert out["indexes"][name]["status"] == "CREATED"
        assert out["indexes"][name]["build_seconds"] >= 0.0
    # The source itself was never built into.
    conn = sqlite3.connect(str(db))
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    assert not (set(probe.INDEXES) & names)


def test_ac_004_existing_indexes_never_produce_a_build_time(
    tmp_path, monkeypatch, capsys
):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-004: when every target index already exists the probe refuses instead of timing a no-op."""
    db = tmp_path / "c" / "c.sqlite3"
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(db.parent))
    _canonical_store(db, drop_indexes=False)
    with pytest.raises(SystemExit, match="NO_INDEX_CREATED"):
        probe.cmd_indexes(_args(db))
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["created"] == 0
    for name in probe.INDEXES:
        assert report["indexes"][name] == {
            "status": "ALREADY_EXISTS",
            "build_seconds": None,
        }


def test_ac_004_a_partly_indexed_store_times_only_what_it_built(tmp_path, monkeypatch):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-004: a mix of present and missing indexes reports each honestly."""
    db = tmp_path / "c" / "c.sqlite3"
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(db.parent))
    _canonical_store(db, drop_indexes=False)
    conn = sqlite3.connect(str(db))
    conn.execute("DROP INDEX idx_events_episode_identity")
    conn.commit()
    conn.close()
    out = probe.cmd_indexes(_args(db))
    assert out["created"] == 1
    assert out["indexes"]["idx_events_episode_identity"]["status"] == "CREATED"
    others = [n for n in probe.INDEXES if n != "idx_events_episode_identity"]
    for name in others:
        assert out["indexes"][name]["build_seconds"] is None


def test_ac_004_prepare_drops_only_in_the_snapshot_never_in_the_source(
    tmp_path, monkeypatch
):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-004: --prepare makes a real build measurable on a store that has the indexes, without touching the source."""
    db = tmp_path / "c" / "c.sqlite3"
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(db.parent))
    _canonical_store(db, drop_indexes=False)
    before = _sha(db)
    args = _args(db)
    args.prepare = True
    out = probe.cmd_indexes(args)
    assert sorted(out["prepared_dropped"]) == sorted(probe.INDEXES)
    assert out["created"] == len(probe.INDEXES)
    assert _sha(db) == before
    conn = sqlite3.connect(str(db))
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    assert set(probe.INDEXES) <= names


def test_ac_004_the_probe_index_definitions_match_production(tmp_path):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-004: drift guard - the probe builds exactly the indexes the writer and schema declare."""
    episode = writer_module._EPISODE_IDENTITY_INDEX_SQL.replace("IF NOT EXISTS ", "")
    assert probe.INDEXES["idx_events_episode_identity"] == episode

    from app.opip.canonical import schema

    for name in ("idx_events_role_result_identity", "idx_events_di_request_state"):
        compact = " ".join(probe.INDEXES[name].split())
        declared = " ".join(
            schema.DDL[schema.DDL.index(f"CREATE INDEX IF NOT EXISTS {name}"):]
            .split(";")[0]
            .replace("IF NOT EXISTS ", "")
            .split()
        )
        assert compact == declared, (name, compact, declared)


# ---------------------------------------------------------------------------
# AC-005 - creation, initialization and hydration are reported separately
# ---------------------------------------------------------------------------


def test_ac_005_measure_reports_each_phase_separately(tmp_path, monkeypatch):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-005: writer initialization and each hydration are timed on their own, on a snapshot, leaving the source untouched."""
    db = tmp_path / "c" / "c.sqlite3"
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(db.parent))
    _canonical_store(db, drop_indexes=False)
    before = _sha(db)
    out = probe.cmd_measure(
        argparse.Namespace(db=str(db), workdir=None, rederive=0)
    )
    assert out["op"] == "measure"
    phases = out["phases"]
    for key in (
        "schema_initialization_seconds",
        "hydrate_request_seconds",
        "hydrate_role_result_seconds",
        "hydrate_opportunity_seconds",
        "other_init_seconds",
    ):
        assert key in phases and phases[key] >= 0.0, key
    assert out["snapshot_events"] == 0
    assert _sha(db) == before
