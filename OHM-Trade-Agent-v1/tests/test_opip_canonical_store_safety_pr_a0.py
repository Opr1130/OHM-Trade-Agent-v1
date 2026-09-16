"""PR-A0 canonical store safety: exclusivity, backup artifact, restore provenance.

Core behavior is exercised against real temporary SQLite databases. Monkeypatching
is used only for deterministic fault injection (replace/fsync/copy/checkpoint
results), never to substitute for SQLite behavior.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from app.opip.canonical.backup import (
    DEFAULT_BACKUP_STEM,
    MANIFEST_SCHEMA_VERSION,
    BackupFormatError,
    BackupProvenanceError,
    assert_rollback_journal_backup,
    backup_database,
    backup_generation_paths,
    build_backup_manifest,
    hash_file_sha256,
    new_backup_generation_id,
    publish_backup_generation,
    read_backup_manifest,
    read_sqlite_journal_format,
    require_generation_component,
    verify_backup_manifest,
    write_backup_manifest,
    write_backup_manifest_create_only,
)
from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.recovery import restore_from_backup, run_backup_restore_drill
from app.opip.canonical.schema import (
    CanonicalCheckpointError,
    CanonicalDbValidationError,
    CanonicalDurabilityError,
    CanonicalStoreBusyError,
    CanonicalStoreLock,
    canonical_store_path,
    checkpoint_wal_strict,
    connect,
    fsync_directory_required,
    fsync_file_required,
    fsync_path,
    initialize_schema,
    store_lock_path,
    validate_canonical_sqlite,
)
from app.opip.canonical.writer import CanonicalWriter

RELEASE_SHA = "808a308cd274d30b55fef47b382c229b761e07df"
OTHER_RELEASE_SHA = "1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _intent(key: str, identity: str = "EARLY_MOVER:SAFE") -> WriterIntent:
    return WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="NORMAL",
        idempotency_key=key,
        event_type="alert_governor.transition.recorded",
        payload={
            "identity": identity,
            "transition_key": f"READY:{key}",
            "message_id": 1000,
            "created_new": True,
            "scan_id": "pr-a0",
        },
        ops_handoff={
            "operation": "RECORD",
            "identity": identity,
            "transition_key": f"READY:{key}",
            "message_id": 1000,
            "created_new": True,
            "reservation_token": f"tok:{key}",
            "state_family": "early_watch",
        },
    )


def _seed(db: Path, *, count: int = 1, start: int = 0) -> None:
    writer = CanonicalWriter(db)
    try:
        for idx in range(start, start + count):
            ack = writer.submit(_intent(f"k:{idx}", f"EARLY_MOVER:K{idx}"))
            assert ack.status == "OK", ack
        writer.checkpoint_wal()
    finally:
        writer.close()


def _epoch_and_seq(db: Path) -> tuple[int, int]:
    conn = connect(db, read_only=True)
    try:
        row = conn.execute(
            "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    return int(row["history_epoch"]), int(row["next_local_sequence"])


def _event_count(db: Path) -> int:
    conn = connect(db, read_only=True)
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])
    finally:
        conn.close()


def _make_backup(live: Path, tmp_path: Path, *, name: str = "backup"):
    """A conforming backup plus a verified manifest."""
    backup = tmp_path / f"{name}.sqlite3"
    manifest_path = tmp_path / f"{name}.manifest.json"
    backup_database(live, backup)
    manifest = build_backup_manifest(backup_path=backup, source_release_sha=RELEASE_SHA)
    write_backup_manifest(manifest, manifest_path)
    return backup, manifest_path, manifest


def _restore(live: Path, backup: Path, manifest_path: Path, **overrides):
    params = {
        "backup_db": backup,
        "live_db": live,
        "manifest_path": manifest_path,
        "expected_source_release_sha": RELEASE_SHA,
    }
    params.update(overrides)
    return restore_from_backup(**params)


def _rewrite_manifest(manifest_path: Path, mutate) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Exclusivity lock matrix
# --------------------------------------------------------------------------- #


def test_writer_acquires_lock_and_second_writer_is_refused(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    try:
        assert CanonicalStoreLock(db).held is False
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(db)
    finally:
        writer.close()


def test_lock_releases_after_writer_close_and_lock_file_is_not_deleted(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    writer.close()
    # Ownership is the kernel lock; the pathname deliberately survives release
    # so the lock identity can never be split by an inode replace.
    assert store_lock_path(db).exists()
    reopened = CanonicalWriter(db)
    reopened.close()


def test_stale_lock_file_without_kernel_lock_does_not_block(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    lock_path = store_lock_path(db)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("stale, not owned\n", encoding="utf-8")

    writer = CanonicalWriter(db)
    writer.close()


def test_distinct_databases_do_not_share_a_lock(tmp_path):
    first = tmp_path / "one.sqlite3"
    second = tmp_path / "two.sqlite3"
    writer_a = CanonicalWriter(first)
    try:
        writer_b = CanonicalWriter(second)
        writer_b.close()
    finally:
        writer_a.close()


def test_writer_constructor_failure_releases_ownership(tmp_path):
    from app.opip.canonical.schema import SchemaVersionError

    db = tmp_path / "canonical.sqlite3"
    writer = CanonicalWriter(db)
    writer.close()
    conn = connect(db, read_only=False)
    try:
        conn.execute("UPDATE meta SET schema_version = 99 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(SchemaVersionError):
        CanonicalWriter(db)
    # A failed constructor must not leave the store permanently inaccessible.
    with pytest.raises(SchemaVersionError):
        CanonicalWriter(db)
    assert CanonicalStoreLock(db).held is False


def test_active_writer_blocks_restore(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)

    writer = CanonicalWriter(db)
    before = db.read_bytes()
    try:
        with pytest.raises(CanonicalStoreBusyError):
            _restore(db, backup, manifest_path)
        assert db.read_bytes() == before
    finally:
        writer.close()

    # Once the writer is gone, restore can take ownership.
    result = _restore(db, backup, manifest_path)
    assert result["history_epoch"] == 2


def test_restore_holds_lock_while_running(tmp_path):
    """Inside restore's locked section, conflicting ownership is refused.

    This proves restore actually owns the OS lock, rather than merely observing
    that some new lock object is unheld. Control flow is deterministic: the
    contention attempt happens inside restore's own checkpoint step.
    """
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)

    refusals: list[str] = []
    from app.opip.canonical import recovery as recovery_module

    real_checkpoint = recovery_module._checkpoint_live_wal

    def _probe(live_db):
        # Restore must already own the live store here.
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalStoreLock(live_db).acquire()
        refusals.append("lock")
        # A second writable authority must be refused just as well.
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(live_db)
        refusals.append("writer")
        return real_checkpoint(live_db)

    recovery_module._checkpoint_live_wal = _probe
    try:
        result = _restore(db, backup, manifest_path)
    finally:
        recovery_module._checkpoint_live_wal = real_checkpoint

    assert refusals == ["lock", "writer"]
    assert result["history_epoch"] == 2
    # Ownership is released once restore completes, so acquisition succeeds.
    lock = CanonicalStoreLock(db)
    lock.acquire()
    lock.release()
    writer = CanonicalWriter(db)
    writer.close()


def test_restore_failure_releases_lock(tmp_path, monkeypatch):
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)

    def _boom(*_a, **_k):
        raise OSError("copy injected failure")

    monkeypatch.setattr("app.opip.canonical.recovery.shutil.copy2", _boom)
    with pytest.raises(OSError, match="copy injected failure"):
        _restore(db, backup, manifest_path)
    assert CanonicalStoreLock(db).held is False

    monkeypatch.undo()
    reopened = CanonicalWriter(db)
    reopened.close()


def _child_hold_writer(db: str, ready, release) -> None:  # pragma: no cover - subprocess
    writer = CanonicalWriter(Path(db))
    ready.set()
    release.wait(timeout=60)
    writer.close()


def test_process_death_releases_kernel_lock(tmp_path):
    """Ownership is kernel state: an ungracefully terminated owner frees it."""
    db = tmp_path / "canonical.sqlite3"
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    child = ctx.Process(target=_child_hold_writer, args=(str(db), ready, release))
    child.start()
    try:
        assert ready.wait(timeout=60), "child never acquired the store"
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(db)
    finally:
        child.terminate()
        child.join(timeout=60)

    assert child.exitcode is not None
    # No PID guessing, no stale-lock cleanup: the OS released the advisory lock.
    writer = CanonicalWriter(db)
    writer.close()


# --------------------------------------------------------------------------- #
# Backup artifact contract
# --------------------------------------------------------------------------- #


def test_backup_is_self_contained_delete_mode(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)
    backup = tmp_path / "backup.sqlite3"
    backup_database(live, backup)

    write_format, read_format = read_sqlite_journal_format(backup)
    assert (write_format, read_format) == (1, 1)
    assert not Path(f"{backup}-wal").exists()
    assert not Path(f"{backup}-shm").exists()
    validate_canonical_sqlite(backup)
    assert_rollback_journal_backup(backup)


def test_live_database_stays_wal_mode(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    writer = CanonicalWriter(live)
    try:
        conn = connect(live, read_only=False)
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()
        assert mode == "wal"
    finally:
        writer.close()


def test_backup_reads_and_manifest_do_not_create_sidecars(tmp_path):
    """The defect that motivated PR-A0: inspecting a backup must not need a WAL."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)
    backup = tmp_path / "backup.sqlite3"
    backup_database(live, backup)

    validate_canonical_sqlite(backup)
    manifest = build_backup_manifest(backup_path=backup, source_release_sha=RELEASE_SHA)
    manifest_path = tmp_path / "manifest.json"
    write_backup_manifest(manifest, manifest_path)
    assert read_backup_manifest(manifest_path)["sha256"] == manifest["sha256"]

    assert not Path(f"{backup}-wal").exists()
    assert not Path(f"{backup}-shm").exists()


def test_backup_manifest_hash_is_over_final_normalized_artifact(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup = tmp_path / "backup.sqlite3"
    backup_database(live, backup)

    manifest = build_backup_manifest(backup_path=backup, source_release_sha=RELEASE_SHA)
    assert manifest["sha256"] == hash_file_sha256(backup)
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["backup_file"] == backup.name


def test_backup_manifest_refuses_nonconforming_artifacts(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)

    # WAL-format artifact (header says WAL) must be refused even without sidecars.
    wal_candidate = tmp_path / "wal-candidate.sqlite3"
    shutil.copy2(live, wal_candidate)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{wal_candidate}{suffix}")
        if side.exists():
            side.unlink()
    with pytest.raises(BackupFormatError, match="rollback-journal"):
        build_backup_manifest(backup_path=wal_candidate, source_release_sha=RELEASE_SHA)

    # Sidecar-bearing artifact must be refused.
    ok = tmp_path / "ok.sqlite3"
    backup_database(live, ok)
    Path(f"{ok}-wal").write_bytes(b"ambiguous")
    with pytest.raises(BackupFormatError, match="self-contained"):
        build_backup_manifest(backup_path=ok, source_release_sha=RELEASE_SHA)


def test_backup_manifest_rejects_unverified_release_provenance(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup = tmp_path / "backup.sqlite3"
    backup_database(live, backup)

    # build_backup_manifest falls back to OPIP_SOURCE_RELEASE_SHA when the
    # explicit argument is blank; isolate the environment so the blank/None cases
    # genuinely exercise the refusal path instead of reading ambient provenance.
    monkeypatch.delenv("OPIP_SOURCE_RELEASE_SHA", raising=False)

    for bad in ("x", "UNVERIFIED", "unknown", "HEAD", "main", "808a308", "g" * 40, ""):
        with pytest.raises(BackupProvenanceError, match="40-character"):
            build_backup_manifest(backup_path=backup, source_release_sha=bad)
    # Explicit None must also be refused rather than silently accepted.
    with pytest.raises(BackupProvenanceError, match="40-character"):
        build_backup_manifest(backup_path=backup, source_release_sha=None)


def test_backup_publish_failure_preserves_previous_generation(tmp_path, monkeypatch):
    """A failed new generation must not damage the previous committed one."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup_dir = tmp_path / "backups"
    first = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    first_db_bytes = first.backup_path.read_bytes()
    first_manifest_bytes = first.manifest_path.read_bytes()

    _seed(live, count=1, start=10)
    import app.opip.canonical.backup as backup_module

    def _boom(*_a, **_k):
        raise OSError("publish injected failure")

    # Inject at the no-replace publication seam (the authoritative step).
    monkeypatch.setattr(backup_module, "_claim_immutable_path", _boom)
    with pytest.raises(OSError, match="publish injected failure"):
        publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    monkeypatch.undo()

    # Previous generation is byte-identical and still verifies.
    assert first.backup_path.read_bytes() == first_db_bytes
    assert first.manifest_path.read_bytes() == first_manifest_bytes
    verify_backup_manifest(
        read_backup_manifest(first.manifest_path),
        backup_path=first.backup_path,
        expected_source_release_sha=RELEASE_SHA,
    )
    # The failed attempt left no partial or orphan artifact behind.
    assert sorted(p.name for p in backup_dir.iterdir()) == sorted(
        [first.backup_path.name, first.manifest_path.name]
    )


def test_backup_database_is_create_only(tmp_path):
    """The primitive refuses to replace an existing published backup."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    published = tmp_path / "published.sqlite3"
    backup_database(live, published)
    before = published.read_bytes()

    _seed(live, count=1, start=5)
    with pytest.raises(BackupProvenanceError, match="immutable|refusing to replace"):
        backup_database(live, published)

    assert published.read_bytes() == before
    validate_canonical_sqlite(published)


# --------------------------------------------------------------------------- #
# Manifest publication atomicity
# --------------------------------------------------------------------------- #


def test_manifest_publication_succeeds_and_replaces_prior(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    write_backup_manifest({"schema_version": 1, "value": "first"}, manifest_path)
    write_backup_manifest({"schema_version": 1, "value": "second"}, manifest_path)

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["value"] == "second"
    # No partial JSON or temp staging left at the authoritative path.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manifest.json"]


def test_manifest_replace_failure_preserves_previous_manifest(tmp_path, monkeypatch):
    import app.opip.canonical.backup as backup_module

    manifest_path = tmp_path / "manifest.json"
    write_backup_manifest({"schema_version": 1, "value": "keep-me"}, manifest_path)

    def _boom(*_a, **_k):
        raise OSError("manifest replace injected failure")

    monkeypatch.setattr(backup_module.os, "replace", _boom)
    with pytest.raises(OSError, match="manifest replace injected failure"):
        write_backup_manifest({"schema_version": 1, "value": "never"}, manifest_path)
    monkeypatch.undo()

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["value"] == "keep-me"
    # No partial JSON or temp staging left behind at the authoritative path.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manifest.json"]


# --------------------------------------------------------------------------- #
# Strict WAL checkpoint semantics
# --------------------------------------------------------------------------- #


class _StubCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _StubConnection:
    def __init__(self, *, row=None, exc=None):
        self._row = row
        self._exc = exc

    def execute(self, _sql):
        if self._exc is not None:
            raise self._exc
        return _StubCursor(self._row)


def test_strict_checkpoint_accepts_complete_result():
    checkpoint_wal_strict(_StubConnection(row=(0, 7, 7)))


@pytest.mark.parametrize(
    "row, match",
    [
        ((1, 7, 7), "busy"),
        ((0, 7, 3), "did not complete"),
        ((0,), "unusable"),
        (None, "no status row"),
        (("x", "y", "z"), "ambiguous"),
    ],
)
def test_strict_checkpoint_rejects_ambiguous_results(row, match):
    with pytest.raises(CanonicalCheckpointError, match=match):
        checkpoint_wal_strict(_StubConnection(row=row))


def test_strict_checkpoint_rejects_sqlite_error():
    with pytest.raises(CanonicalCheckpointError, match="wal_checkpoint failed"):
        checkpoint_wal_strict(
            _StubConnection(exc=sqlite3.OperationalError("database is locked"))
        )


def test_strict_checkpoint_preserves_committed_live_wal_data(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=3)
    # Committed data must survive a strict checkpoint followed by sidecar removal
    # (the destructive step restore performs before cutover).
    writer = CanonicalWriter(live)
    try:
        writer.checkpoint_wal()
    finally:
        writer.close()
    for suffix in ("-wal", "-shm"):
        side = Path(f"{live}{suffix}")
        if side.exists():
            side.unlink()
    assert _event_count(live) == 3
    validate_canonical_sqlite(live)


# --------------------------------------------------------------------------- #
# Restore provenance: artifact and manifest rejection
# --------------------------------------------------------------------------- #


def test_restore_rejects_wal_format_backup_without_sidecars(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    candidate = tmp_path / "wal-backup.sqlite3"
    shutil.copy2(live, candidate)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{candidate}{suffix}")
        if side.exists():
            side.unlink()
    assert read_sqlite_journal_format(candidate) == (2, 2)

    before = live.read_bytes()
    with pytest.raises(BackupProvenanceError, match="rollback-journal"):
        _restore(live, candidate, tmp_path / "absent-manifest.json")
    assert live.read_bytes() == before


def test_restore_rejects_backup_sidecars(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    Path(f"{backup}-wal").write_bytes(b"ambiguous")

    before = live.read_bytes()
    with pytest.raises(BackupProvenanceError, match="self-contained"):
        _restore(live, backup, manifest_path)
    assert live.read_bytes() == before


@pytest.mark.parametrize(
    "corrupt, match",
    [
        (lambda p: p.write_bytes(b"not-a-sqlite-db"), "header magic"),
        (lambda p: p.write_bytes(b""), "header magic"),
    ],
)
def test_restore_rejects_non_sqlite_backup(tmp_path, corrupt, match):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    corrupt(backup)

    before = live.read_bytes()
    with pytest.raises(BackupProvenanceError, match=match):
        _restore(live, backup, manifest_path)
    assert live.read_bytes() == before


def test_restore_rejects_corrupt_sqlite_header(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)

    raw = bytearray(backup.read_bytes())
    raw[18] = 3  # unsupported file-format write version
    backup.write_bytes(bytes(raw))

    before = live.read_bytes()
    with pytest.raises(BackupProvenanceError, match="rollback-journal"):
        _restore(live, backup, manifest_path)
    assert live.read_bytes() == before


def test_restore_rejects_directory_and_missing_inputs(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    a_directory = tmp_path / "a-directory"
    a_directory.mkdir()

    with pytest.raises(BackupProvenanceError):
        _restore(live, a_directory, manifest_path)
    with pytest.raises(BackupProvenanceError):
        _restore(live, tmp_path / "missing.sqlite3", manifest_path)


def test_restore_rejects_backup_path_equal_to_live_path(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    _, manifest_path, _ = _make_backup(live, tmp_path)

    with pytest.raises(BackupProvenanceError, match="same path"):
        _restore(live, live, manifest_path)


def test_restore_rejects_unsafe_epoch_opt_out(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    before = live.read_bytes()

    with pytest.raises(BackupProvenanceError, match="advance_epoch=False"):
        _restore(live, backup, manifest_path, advance_epoch=False)
    assert live.read_bytes() == before


# --------------------------------------------------------------------------- #
# Manifest validation matrix
# --------------------------------------------------------------------------- #


def _valid_manifest_and_backup(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    return live, backup, manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))


def test_verify_manifest_accepts_valid_manifest(tmp_path):
    _, backup, _, manifest = _valid_manifest_and_backup(tmp_path)
    verified = verify_backup_manifest(
        manifest, backup_path=backup, expected_source_release_sha=RELEASE_SHA
    )
    assert verified["sha256"] == manifest["sha256"]


def _rollback_journal_sqlite(path: Path, *, with_meta_row: bool) -> Path:
    """A structurally valid, rollback-journal SQLite file that is not canonical.

    Passes the header/preflight artifact checks, so it reaches the later
    snapshot-fact and canonical-validation stages.
    """
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        if with_meta_row:
            # Readable by the snapshot-fact query, but without the canonical
            # `schema_version` column that canonical validation requires.
            connection.execute(
                "CREATE TABLE meta ("
                "id INTEGER PRIMARY KEY, history_epoch INTEGER NOT NULL, "
                "next_local_sequence INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO meta (id, history_epoch, next_local_sequence) "
                "VALUES (1, 1, 1)"
            )
        else:
            connection.execute("CREATE TABLE unrelated (x INTEGER)")
        connection.execute(
            "CREATE TABLE events (history_epoch INTEGER NOT NULL, "
            "local_sequence INTEGER NOT NULL)"
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_verify_manifest_raises_provenance_error_for_unreadable_snapshot_facts(tmp_path):
    """FINDING: snapshot-fact failures must honour the provenance error contract.

    A file with valid SQLite magic and rollback-journal format passes every
    earlier artifact check, so a malformed/absent canonical schema previously let
    a raw ``sqlite3.Error`` escape from the public verification path.
    """
    backup = _rollback_journal_sqlite(tmp_path / "no-meta.sqlite3", with_meta_row=False)
    # Earlier artifact checks genuinely pass, proving we reach the facts stage.
    assert_rollback_journal_backup(backup)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "backup_file": backup.name,
        "sha256": hash_file_sha256(backup),
        "source_release_sha": RELEASE_SHA,
        "history_epoch": 1,
        "next_local_sequence": 1,
        "max_local_sequence": 0,
        "event_count": 0,
    }

    with pytest.raises(BackupProvenanceError, match="snapshot facts are unreadable") as exc_info:
        verify_backup_manifest(
            manifest, backup_path=backup, expected_source_release_sha=RELEASE_SHA
        )
    # The underlying cause is preserved, not swallowed.
    assert exc_info.value.__cause__ is not None
    assert not isinstance(exc_info.value, BackupFormatError)


def test_verify_manifest_raises_provenance_error_for_missing_meta_row(tmp_path):
    """The missing-meta-row path (a RuntimeError source) also fails as provenance."""
    backup = _rollback_journal_sqlite(tmp_path / "empty-meta.sqlite3", with_meta_row=False)
    connection = sqlite3.connect(str(backup))
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            "CREATE TABLE meta (id INTEGER PRIMARY KEY, history_epoch INTEGER, "
            "next_local_sequence INTEGER, schema_version INTEGER)"
        )
        connection.commit()
    finally:
        connection.close()

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "backup_file": backup.name,
        "sha256": hash_file_sha256(backup),
        "source_release_sha": RELEASE_SHA,
        "history_epoch": 1,
        "next_local_sequence": 1,
        "max_local_sequence": 0,
        "event_count": 0,
    }

    with pytest.raises(BackupProvenanceError, match="snapshot facts are unreadable") as exc_info:
        verify_backup_manifest(
            manifest, backup_path=backup, expected_source_release_sha=RELEASE_SHA
        )
    assert exc_info.value.__cause__ is not None


def test_verify_manifest_existing_provenance_errors_are_not_rewrapped(tmp_path):
    """An existing provenance failure must surface unchanged (not double-wrapped)."""
    _, backup, _, manifest = _valid_manifest_and_backup(tmp_path)
    manifest["event_count"] = int(manifest["event_count"]) + 1

    with pytest.raises(BackupProvenanceError, match="event_count") as exc_info:
        verify_backup_manifest(
            manifest, backup_path=backup, expected_source_release_sha=RELEASE_SHA
        )
    # Field-mismatch errors originate from the comparison, not the facts wrapper.
    assert "snapshot facts are unreadable" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_verify_manifest_valid_backup_behavior_unchanged(tmp_path):
    """The tightening must not alter acceptance of a valid backup."""
    live, backup, _, manifest = _valid_manifest_and_backup(tmp_path)
    verified = verify_backup_manifest(
        manifest, backup_path=backup, expected_source_release_sha=RELEASE_SHA
    )
    assert verified["sha256"] == manifest["sha256"]
    assert verified["event_count"] == 2
    assert verified["history_epoch"] == 1
    validate_canonical_sqlite(backup)
    assert live.exists()


def test_read_manifest_missing_and_malformed(tmp_path):
    with pytest.raises(BackupProvenanceError, match="missing or unreadable"):
        read_backup_manifest(tmp_path / "absent.json")

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(BackupProvenanceError, match="not valid JSON"):
        read_backup_manifest(bad)

    list_root = tmp_path / "list.json"
    list_root.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(BackupProvenanceError, match="JSON object"):
        read_backup_manifest(list_root)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda m: m.pop("schema_version"), "schema_version"),
        (lambda m: m.update(schema_version=99), "schema_version"),
        (lambda m: m.update(schema_version="1"), "schema_version"),
        (lambda m: m.update(schema_version=True), "schema_version"),
        (lambda m: m.pop("backup_file"), "different file"),
        (lambda m: m.update(backup_file="other.sqlite3"), "different file"),
        (lambda m: m.update(backup_file=123), "different file"),
        (lambda m: m.pop("sha256"), "malformed"),
        (lambda m: m.update(sha256="abc"), "malformed"),
        (lambda m: m.update(sha256="z" * 64), "malformed"),
        (lambda m: m.update(sha256="0" * 64), "does not match"),
        (lambda m: m.pop("source_release_sha"), "40-character"),
        (lambda m: m.update(source_release_sha="UNVERIFIED"), "40-character"),
        (lambda m: m.update(source_release_sha="808a308"), "40-character"),
        (lambda m: m.update(source_release_sha=OTHER_RELEASE_SHA), "expected release"),
        (lambda m: m.update(history_epoch=m["history_epoch"] + 1), "history_epoch"),
        (lambda m: m.update(next_local_sequence=m["next_local_sequence"] + 1), "next_local_sequence"),
        (lambda m: m.update(max_local_sequence=m["max_local_sequence"] + 1), "max_local_sequence"),
        (lambda m: m.update(event_count=m["event_count"] + 1), "event_count"),
        (lambda m: m.update(event_count=True), "event_count"),
        (lambda m: m.update(history_epoch="1"), "history_epoch"),
    ],
)
def test_verify_manifest_rejects_invalid_provenance(tmp_path, mutate, match):
    _, backup, _, manifest = _valid_manifest_and_backup(tmp_path)
    mutate(manifest)
    with pytest.raises(BackupProvenanceError, match=match):
        verify_backup_manifest(
            manifest, backup_path=backup, expected_source_release_sha=RELEASE_SHA
        )


def test_restore_rejects_tampered_backup_and_manifest_without_live_mutation(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    before = live.read_bytes()

    # One byte changed in the backup -> SHA mismatch.
    original = backup.read_bytes()
    tampered = bytearray(original)
    tampered[-1] = tampered[-1] ^ 0xFF
    backup.write_bytes(bytes(tampered))
    with pytest.raises(BackupProvenanceError, match="does not match"):
        _restore(live, backup, manifest_path)
    assert live.read_bytes() == before

    # Restore correct bytes, then tamper metadata.
    backup.write_bytes(original)
    _rewrite_manifest(manifest_path, lambda m: m.update(event_count=999))
    with pytest.raises(BackupProvenanceError, match="event_count"):
        _restore(live, backup, manifest_path)

    # Tamper release provenance, using a fresh generation for the clean pair.
    backup_b, manifest_path_b, _ = _make_backup(live, tmp_path, name="bak-prov")
    _rewrite_manifest(manifest_path_b, lambda m: m.update(source_release_sha=OTHER_RELEASE_SHA))
    with pytest.raises(BackupProvenanceError, match="expected release"):
        _restore(live, backup_b, manifest_path_b)

    assert live.read_bytes() == before


def test_restore_requires_manifest(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    manifest_path.unlink()
    before = live.read_bytes()
    with pytest.raises(BackupProvenanceError, match="missing or unreadable"):
        _restore(live, backup, manifest_path)
    assert live.read_bytes() == before


# --------------------------------------------------------------------------- #
# Failure preservation and lock release
# --------------------------------------------------------------------------- #


def test_restore_final_replace_failure_preserves_live_logical_state(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    before_epoch, _ = _epoch_and_seq(live)

    # Advance the live store so the restored coordinates would differ.
    _seed(live, count=1, start=50)
    live_events = _event_count(live)
    live_bytes = live.read_bytes()

    import app.opip.canonical.recovery as recovery_module

    real_replace = recovery_module.os.replace

    def _boom(src, dst):
        if Path(dst) == live:
            raise OSError("cutover injected failure")
        return real_replace(src, dst)

    monkeypatch.setattr(recovery_module.os, "replace", _boom)
    with pytest.raises(OSError, match="cutover injected failure"):
        _restore(live, backup, manifest_path)
    monkeypatch.undo()

    assert live.read_bytes() == live_bytes
    assert _event_count(live) == live_events
    assert _epoch_and_seq(live)[0] == before_epoch
    validate_canonical_sqlite(live)
    assert CanonicalStoreLock(live).held is False
    # Store can be reopened normally and still accept a new canonical event.
    writer = CanonicalWriter(live)
    try:
        ack = writer.submit(_intent("post-failure", "EARLY_MOVER:POSTFAIL"))
        assert ack.status == "OK"
    finally:
        writer.close()


def test_restore_staged_hash_mismatch_preserves_live(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    before = live.read_bytes()

    import app.opip.canonical.recovery as recovery_module

    real_copy = recovery_module.shutil.copy2

    def _copy_then_corrupt(src, dst, *args, **kwargs):
        result = real_copy(src, dst, *args, **kwargs)
        with Path(dst).open("r+b") as handle:
            handle.seek(0, os.SEEK_END)
            handle.write(b"corruption")
        return result

    monkeypatch.setattr(recovery_module.shutil, "copy2", _copy_then_corrupt)
    with pytest.raises(BackupProvenanceError, match="staged restore copy"):
        _restore(live, backup, manifest_path)
    monkeypatch.undo()

    assert live.read_bytes() == before
    validate_canonical_sqlite(live)
    assert CanonicalStoreLock(live).held is False


def test_restore_validation_failure_preserves_live(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    before = live.read_bytes()

    def _boom(path):
        raise CanonicalDbValidationError("validation injected failure")

    monkeypatch.setattr("app.opip.canonical.recovery.validate_canonical_sqlite", _boom)
    with pytest.raises(CanonicalDbValidationError, match="validation injected failure"):
        _restore(live, backup, manifest_path)
    monkeypatch.undo()

    assert live.read_bytes() == before
    validate_canonical_sqlite(live)
    assert CanonicalStoreLock(live).held is False


def test_restore_epoch_advance_failure_preserves_live(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    before = live.read_bytes()

    def _boom(self, minimum_epoch=None):
        raise RuntimeError("epoch injected failure")

    monkeypatch.setattr(
        CanonicalWriter, "advance_history_epoch_for_restore", _boom
    )
    with pytest.raises(RuntimeError, match="epoch injected failure"):
        _restore(live, backup, manifest_path)
    monkeypatch.undo()

    assert live.read_bytes() == before
    assert CanonicalStoreLock(live).held is False


def test_restore_strict_live_checkpoint_failure_preserves_live(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    before = live.read_bytes()

    def _busy(_connection):
        raise CanonicalCheckpointError("wal_checkpoint reported busy")

    monkeypatch.setattr("app.opip.canonical.recovery.checkpoint_wal_strict", _busy)
    with pytest.raises(CanonicalCheckpointError, match="busy"):
        _restore(live, backup, manifest_path)
    monkeypatch.undo()

    assert live.read_bytes() == before
    validate_canonical_sqlite(live)
    assert CanonicalStoreLock(live).held is False


# --------------------------------------------------------------------------- #
# Epoch monotonicity
# --------------------------------------------------------------------------- #


def _force_live_epoch(db: Path, target: int) -> None:
    """Advance a live store's epoch to ``target`` using the canonical path."""
    current, _ = _epoch_and_seq(db)
    while current < target:
        writer = CanonicalWriter(db)
        try:
            current = writer.advance_history_epoch_for_restore()
        finally:
            writer.close()


@pytest.mark.parametrize(
    "backup_epoch, live_epoch, expected",
    [(1, 1, 2), (1, 4, 5), (4, 2, 5), (7, 7, 8)],
)
def test_restore_epoch_is_monotonic(tmp_path, backup_epoch, live_epoch, expected):
    backup_db = tmp_path / "snap" / "canonical.sqlite3"
    backup_db.parent.mkdir(parents=True, exist_ok=True)

    _seed(backup_db, count=2)
    _force_live_epoch(backup_db, backup_epoch)
    assert _epoch_and_seq(backup_db)[0] == backup_epoch

    backup = tmp_path / "snap" / "backup.sqlite3"
    manifest_path = tmp_path / "snap" / "manifest.json"
    manifest = backup_database(backup_db, backup) and build_backup_manifest(
        backup_path=backup, source_release_sha=RELEASE_SHA
    )
    write_backup_manifest(manifest, manifest_path)

    live = tmp_path / "live" / "canonical.sqlite3"
    live.parent.mkdir(parents=True, exist_ok=True)
    _seed(live, count=1)
    _force_live_epoch(live, live_epoch)
    assert _epoch_and_seq(live)[0] == live_epoch

    result = _restore(live, backup, manifest_path)

    assert result["history_epoch"] == expected
    assert result["previous_live_epoch"] == live_epoch
    epoch, next_seq = _epoch_and_seq(live)
    assert epoch == expected
    assert next_seq == 1
    validate_canonical_sqlite(live)


def test_restore_preserves_historical_event_coordinates(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=3)
    backup, manifest_path, _ = _make_backup(live, tmp_path)

    conn = connect(backup, read_only=True)
    try:
        before = sorted(
            (int(r["history_epoch"]), int(r["local_sequence"]))
            for r in conn.execute("SELECT history_epoch, local_sequence FROM events")
        )
    finally:
        conn.close()

    _restore(live, backup, manifest_path)

    conn = connect(live, read_only=True)
    try:
        after = sorted(
            (int(r["history_epoch"]), int(r["local_sequence"]))
            for r in conn.execute("SELECT history_epoch, local_sequence FROM events")
        )
    finally:
        conn.close()
    assert after == before


def test_post_restore_first_event_uses_new_epoch(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    _force_live_epoch(live, 4)
    backup, manifest_path, _ = _make_backup(live, tmp_path)

    _seed(live, count=1, start=7)
    result = _restore(live, backup, manifest_path)
    assert result["history_epoch"] == 5

    writer = CanonicalWriter(live)
    try:
        # Opening the writer resumes normal WAL operation on the restored store.
        conn = connect(live, read_only=False)
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()
        assert mode == "wal"
        ack = writer.submit(_intent("post-restore", "EARLY_MOVER:POST"))
        assert ack.status == "OK"
    finally:
        writer.close()

    conn = connect(live, read_only=True)
    try:
        row = conn.execute(
            "SELECT history_epoch, local_sequence FROM events "
            "WHERE idempotency_key = ?",
            ("post-restore",),
        ).fetchone()
    finally:
        conn.close()
    assert (int(row["history_epoch"]), int(row["local_sequence"])) == (5, 1)


def test_restore_into_empty_target_uses_backup_epoch_plus_one(tmp_path):
    source = tmp_path / "source" / "canonical.sqlite3"
    source.parent.mkdir(parents=True, exist_ok=True)
    _seed(source, count=2)
    backup = tmp_path / "source" / "backup.sqlite3"
    manifest_path = tmp_path / "source" / "manifest.json"
    backup_database(source, backup)
    manifest = build_backup_manifest(backup_path=backup, source_release_sha=RELEASE_SHA)
    write_backup_manifest(manifest, manifest_path)

    target = tmp_path / "fresh" / "canonical.sqlite3"
    result = _restore(target, backup, manifest_path)
    assert result["history_epoch"] == 2
    assert result["previous_live_epoch"] is None
    assert _epoch_and_seq(target) == (2, 1)


def test_restore_refuses_ambiguous_existing_live_store(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup, manifest_path, _ = _make_backup(live, tmp_path)

    # A live file whose epoch cannot be established must not be treated as new.
    corrupt_live = b"SQLite format 3\x00" + b"\x00" * 100
    live.write_bytes(corrupt_live)
    with pytest.raises((BackupProvenanceError, CanonicalDbValidationError)):
        _restore(live, backup, manifest_path)
    # Restore did not touch the ambiguous live file.
    assert live.read_bytes() == corrupt_live


# --------------------------------------------------------------------------- #
# End-to-end drill and active-writer refusal
# --------------------------------------------------------------------------- #


def test_end_to_end_backup_restore_with_provenance(tmp_path):
    live = tmp_path / "live" / "opip_canonical_v1.sqlite3"
    _seed(live, count=4)
    epoch_before, _ = _epoch_and_seq(live)
    events_before = _event_count(live)

    backup, manifest_path, manifest = _make_backup(live, tmp_path)
    assert manifest["event_count"] == events_before
    assert manifest["history_epoch"] == epoch_before
    assert_rollback_journal_backup(backup)

    # Live advances past the snapshot.
    _seed(live, count=2, start=20)
    _force_live_epoch(live, epoch_before + 3)

    result = _restore(live, backup, manifest_path)
    assert result["history_epoch"] == (epoch_before + 3) + 1
    assert result["source_release_sha"] == RELEASE_SHA
    assert result["backup_sha256"] == manifest["sha256"]
    assert _event_count(live) == events_before
    assert _epoch_and_seq(live)[1] == 1
    validate_canonical_sqlite(live)


def test_recovery_drill_uses_authoritative_contract(tmp_path):
    drill = run_backup_restore_drill(tmp_path / "drill", seed_events=2)
    # The drill restores into a fresh path, so there is no previous live epoch.
    assert drill["restore"]["history_epoch"] == 2
    assert drill["restore"]["previous_live_epoch"] is None
    assert drill["manifest"]["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert drill["restore"]["source_release_sha"] == drill["manifest"]["source_release_sha"]
    assert drill["restore"]["backup_sha256"] == drill["manifest"]["sha256"]
    validate_canonical_sqlite(Path(drill["restore"]["restored_path"]))


def test_backup_is_possible_while_a_writer_owns_the_store(tmp_path):
    """Online backup reads the live store; it must not require the writer lock."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    writer = CanonicalWriter(live)
    try:
        ack = writer.submit(_intent("during-backup", "EARLY_MOVER:DURING"))
        assert ack.status == "OK"
        backup = tmp_path / "online.sqlite3"
        backup_database(live, backup)
    finally:
        writer.close()

    assert_rollback_journal_backup(backup)
    assert not Path(f"{backup}-wal").exists()
    validate_canonical_sqlite(backup)


def test_direct_writer_can_be_restored_over_with_new_epoch(tmp_path):
    """A restore over a live store leaves it openable and writable at the new epoch."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)
    backup, manifest_path, _ = _make_backup(live, tmp_path)
    _force_live_epoch(live, 3)

    result = _restore(live, backup, manifest_path)
    assert result["history_epoch"] == 4

    writer = CanonicalWriter(live)
    try:
        ack = writer.submit(_intent("after-restore", "EARLY_MOVER:AFTER"))
        assert ack.status == "OK"
        writer.checkpoint_wal()
    finally:
        writer.close()
    validate_canonical_sqlite(live)


# --------------------------------------------------------------------------- #
# Constructor failure cleanup (no ownership leak, no masked error)
# --------------------------------------------------------------------------- #


class _HydrationBoom(RuntimeError):
    """Sentinel for an injected post-lock constructor failure."""


def test_constructor_lifecycle_hydration_failure_releases_lock(tmp_path, monkeypatch):
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()

    def _boom(self):
        raise _HydrationBoom("request lifecycle hydration failed")

    monkeypatch.setattr(
        CanonicalWriter, "_hydrate_request_lifecycle_projection", _boom
    )
    with pytest.raises(_HydrationBoom, match="request lifecycle hydration failed"):
        CanonicalWriter(db)
    monkeypatch.undo()

    # Ownership was released, so the store is immediately reacquirable.
    writer = CanonicalWriter(db)
    writer.close()


def test_constructor_role_result_hydration_failure_releases_lock(tmp_path, monkeypatch):
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()

    def _boom(self):
        raise _HydrationBoom("role result hydration failed")

    monkeypatch.setattr(
        CanonicalWriter, "_hydrate_role_result_identity_projection", _boom
    )
    with pytest.raises(_HydrationBoom, match="role result hydration failed"):
        CanonicalWriter(db)
    monkeypatch.undo()

    writer = CanonicalWriter(db)
    writer.close()


def test_constructor_projection_refresh_failure_releases_lock(tmp_path, monkeypatch):
    """The incremental refresh path is also inside the cleanup boundary."""
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()

    def _boom(self):
        raise _HydrationBoom("projection refresh failed")

    monkeypatch.setattr(
        CanonicalWriter, "_refresh_request_lifecycle_projection", _boom
    )
    writer = CanonicalWriter(db)
    try:
        # Reaching the refresh path directly must still leave a healthy writer.
        with pytest.raises(_HydrationBoom, match="projection refresh failed"):
            writer._refresh_request_lifecycle_projection()
    finally:
        writer.close()
    monkeypatch.undo()
    CanonicalWriter(db).close()


def test_constructor_connection_failure_releases_ownership(tmp_path, monkeypatch):
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()

    def _boom(*_a, **_k):
        raise OSError("connection injected failure")

    monkeypatch.setattr("app.opip.canonical.writer.connect", _boom)
    with pytest.raises(OSError, match="connection injected failure"):
        CanonicalWriter(db)
    monkeypatch.undo()

    CanonicalWriter(db).close()


def test_constructor_cleanup_failure_does_not_mask_original_error(tmp_path, monkeypatch):
    """A failing cleanup must not replace the actionable construction error."""
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()

    def _boom(self):
        raise _HydrationBoom("original construction failure")

    def _cleanup_boom(self):
        raise RuntimeError("cleanup exploded")

    monkeypatch.setattr(
        CanonicalWriter, "_hydrate_request_lifecycle_projection", _boom
    )
    monkeypatch.setattr(
        CanonicalWriter, "_close_connection_best_effort", _cleanup_boom
    )
    monkeypatch.setattr(
        CanonicalWriter, "_release_store_lock_best_effort", _cleanup_boom
    )
    with pytest.raises(_HydrationBoom, match="original construction failure"):
        CanonicalWriter(db)
    monkeypatch.undo()

    CanonicalWriter(db).close()


def test_constructor_failure_during_hydration_leaves_store_reacquirable(tmp_path, monkeypatch):
    """Repeated failures must not accumulate ownership or connections."""
    db = tmp_path / "canonical.sqlite3"
    CanonicalWriter(db).close()

    def _boom(self):
        raise _HydrationBoom("repeated hydration failure")

    monkeypatch.setattr(
        CanonicalWriter, "_hydrate_role_result_identity_projection", _boom
    )
    for _ in range(3):
        with pytest.raises(_HydrationBoom):
            CanonicalWriter(db)
    monkeypatch.undo()

    writer = CanonicalWriter(db)
    try:
        ack = writer.submit(_intent("after-hydration-failures", "EARLY_MOVER:HYD"))
        assert ack.status == "OK"
    finally:
        writer.close()


# --------------------------------------------------------------------------- #
# Required durability synchronization (strict, never silently best-effort)
# --------------------------------------------------------------------------- #


def test_required_file_durability_raises_for_missing_file(tmp_path):
    with pytest.raises(CanonicalDurabilityError, match="cannot open file"):
        fsync_file_required(tmp_path / "absent.sqlite3")


def test_required_directory_durability_raises_for_missing_directory(tmp_path):
    # POSIX opens the directory and fails on the open; Windows raises its
    # unsupported-platform refusal before any filesystem access is attempted.
    expected = "unsupported" if os.name == "nt" else "cannot open directory"
    with pytest.raises(CanonicalDurabilityError, match=expected):
        fsync_directory_required(tmp_path / "absent-directory")


def test_required_file_durability_succeeds_on_real_file(tmp_path):
    target = tmp_path / "payload.bin"
    target.write_bytes(b"durable")
    fsync_file_required(target)  # real flush, no monkeypatching


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "authoritative directory durability is fail-closed on Windows; "
        "see test_windows_real_authoritative_directory_durability_is_fail_closed"
    ),
)
def test_required_directory_durability_succeeds_on_real_directory(tmp_path):
    """POSIX: the production authoritative mechanism really establishes durability."""
    fsync_directory_required(tmp_path)  # real flush, no monkeypatching


def test_best_effort_fsync_swallows_while_required_fsync_raises(tmp_path, monkeypatch):
    """The defect being fixed: best-effort hid real durability failures.

    On Windows a read-only descriptor makes ``os.fsync`` fail (EBADF), so the
    legacy helper silently performed no durability work. The required helper
    must surface the same underlying failure.
    """
    target = tmp_path / "payload.bin"
    target.write_bytes(b"x")

    def _fsync_boom(_fd):
        raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr("app.opip.canonical.schema.os.fsync", _fsync_boom)
    fsync_path(target)  # best-effort: documented to swallow
    with pytest.raises(CanonicalDurabilityError, match="file durability flush failed"):
        fsync_file_required(target)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only: exercises the real os.fsync(dirfd) failure path",
)
def test_required_directory_durability_raises_when_flush_fails(tmp_path, monkeypatch):
    import app.opip.canonical.schema as schema_module

    def _boom(_directory):
        raise CanonicalDurabilityError("directory durability flush failed")

    monkeypatch.setattr(schema_module, "_flush_directory_posix", _boom)
    with pytest.raises(CanonicalDurabilityError, match="directory durability flush failed"):
        fsync_directory_required(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX uses os.fsync for directory durability")
def test_required_directory_durability_raises_on_real_syscall_failure(tmp_path, monkeypatch):
    """POSIX: the real syscall itself fails, so no seam is involved."""
    monkeypatch.setattr(
        "app.opip.canonical.schema.os.fsync",
        lambda _fd: (_ for _ in ()).throw(OSError(5, "I/O error")),
    )
    with pytest.raises(CanonicalDurabilityError, match="directory durability flush failed"):
        fsync_directory_required(tmp_path)


def test_backup_fails_when_staged_file_durability_fails(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup = tmp_path / "backup.sqlite3"

    monkeypatch.setattr(
        "app.opip.canonical.schema.os.fsync",
        lambda _fd: (_ for _ in ()).throw(OSError(5, "I/O error")),
    )
    with pytest.raises(CanonicalDurabilityError):
        backup_database(live, backup)
    monkeypatch.undo()

    # A failed publication must not leave a partial artifact behind.
    assert not backup.exists()


def test_backup_reports_failure_when_parent_directory_durability_fails(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup = tmp_path / "backup.sqlite3"
    import app.opip.canonical.backup as backup_module

    def _boom(_directory):
        raise CanonicalDurabilityError("parent directory durability failed")

    # Patch the authoritative call-site contract, so this proves the sequencing
    # invariant on every platform.
    monkeypatch.setattr(backup_module, "fsync_directory_required", _boom)
    with pytest.raises(CanonicalDurabilityError, match="parent directory durability failed"):
        backup_database(live, backup)
    monkeypatch.undo()

    # The artifact may already be visible (os.replace happened first); the point
    # is that durability was NOT reported as success.
    assert not Path(f"{backup}-wal").exists()


def test_restore_fails_when_staged_file_durability_fails(tmp_path, monkeypatch):
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)
    before = db.read_bytes()

    monkeypatch.setattr(
        "app.opip.canonical.schema.os.fsync",
        lambda _fd: (_ for _ in ()).throw(OSError(5, "I/O error")),
    )
    with pytest.raises(CanonicalDurabilityError):
        _restore(db, backup, manifest_path)
    monkeypatch.undo()

    assert db.read_bytes() == before
    validate_canonical_sqlite(db)
    assert CanonicalStoreLock(db).held is False


def test_restore_reports_failure_when_parent_durability_fails_after_cutover(
    tmp_path, monkeypatch
):
    """Post-replace directory durability failure must never report success."""
    db = tmp_path / "canonical.sqlite3"
    _seed(db, count=1)
    backup, manifest_path, _ = _make_backup(db, tmp_path)
    _seed(db, count=1, start=5)

    import app.opip.canonical.recovery as recovery_module

    def _boom(_directory):
        raise CanonicalDurabilityError("post-cutover directory durability failed")

    monkeypatch.setattr(recovery_module, "fsync_directory_required", _boom)
    with pytest.raises(CanonicalDurabilityError, match="post-cutover"):
        _restore(db, backup, manifest_path)
    monkeypatch.undo()

    # Fail-closed: no fake rollback, and ownership is still released.
    assert CanonicalStoreLock(db).held is False
    assert Path(f"{db}{'.writer.lock'}").exists()


def test_manifest_publication_fails_when_temp_file_durability_fails(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    write_backup_manifest({"schema_version": 1, "value": "keep-me"}, manifest_path)

    monkeypatch.setattr(
        "os.fsync",
        lambda _fd: (_ for _ in ()).throw(OSError(5, "I/O error")),
    )
    with pytest.raises(OSError, match="I/O error"):
        write_backup_manifest({"schema_version": 1, "value": "never"}, manifest_path)
    monkeypatch.undo()

    # Prior valid manifest survives; no partial JSON at the authoritative path.
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["value"] == "keep-me"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manifest.json"]


def test_manifest_publication_reports_failure_when_directory_durability_fails(
    tmp_path, monkeypatch
):
    manifest_path = tmp_path / "manifest.json"
    import app.opip.canonical.backup as backup_module

    def _boom(_directory):
        raise CanonicalDurabilityError("manifest directory durability failed")

    monkeypatch.setattr(backup_module, "fsync_directory_required", _boom)
    with pytest.raises(CanonicalDurabilityError, match="manifest directory"):
        write_backup_manifest({"schema_version": 1, "value": "x"}, manifest_path)


# --------------------------------------------------------------------------- #
# Ephemeral restore-staging lock cleanup
# --------------------------------------------------------------------------- #


def _staging_lock_artifacts(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.iterdir()
        if ".restore-staging." in path.name and path.name.endswith(".writer.lock")
    ]


def test_no_staging_lock_remains_after_successful_restore(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)

    _restore(db, backup, manifest_path)

    assert _staging_lock_artifacts(tmp_path) == []
    # The live store's own lock file is a different, permanent path.
    assert store_lock_path(db).exists()
    assert not list(tmp_path.glob("*.restore-staging.*.sqlite3"))


def test_no_staging_lock_remains_after_staging_failure(tmp_path, monkeypatch):
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)

    def _boom(self, minimum_epoch=None):
        raise RuntimeError("epoch injected failure after staged writer creation")

    monkeypatch.setattr(
        CanonicalWriter, "advance_history_epoch_for_restore", _boom
    )
    with pytest.raises(RuntimeError, match="epoch injected failure"):
        _restore(db, backup, manifest_path)
    monkeypatch.undo()

    assert _staging_lock_artifacts(tmp_path) == []
    assert not list(tmp_path.glob("*.restore-staging.*.sqlite3"))
    assert store_lock_path(db).exists()
    assert CanonicalStoreLock(db).held is False


def test_staging_lock_cleanup_never_touches_live_lock_file(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)
    live_lock = store_lock_path(db)

    _restore(db, backup, manifest_path)

    assert live_lock.exists()
    # Ownership still works on the permanent path after cleanup ran.
    writer = CanonicalWriter(db)
    writer.close()


def test_staging_cleanup_helper_refuses_non_staging_paths(tmp_path):
    """The cleanup helper can never be redirected at a real canonical file."""
    from app.opip.canonical.recovery import _cleanup_staging_artifacts

    real_db = tmp_path / "opip_canonical_v1.sqlite3"
    _seed(real_db)
    live_lock = store_lock_path(real_db)

    _cleanup_staging_artifacts(real_db)

    assert real_db.exists()
    assert live_lock.exists()


# --------------------------------------------------------------------------- #
# Canonical store path identity (alias bypass)
# --------------------------------------------------------------------------- #


def test_canonical_store_path_is_idempotent_and_collapses_aliases(tmp_path):
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    target = real / "sub" / "canonical.sqlite3"  # does not exist yet

    canonical = canonical_store_path(target)
    assert canonical == canonical_store_path(canonical)
    assert canonical == canonical_store_path(real / "." / "sub" / "canonical.sqlite3")
    assert canonical == canonical_store_path(real / ".." / real.name / "sub" / "canonical.sqlite3")
    assert canonical == canonical_store_path(str(target))
    assert canonical.is_absolute()


def test_relative_and_absolute_references_share_one_lock(tmp_path, monkeypatch):
    """A relative reference must contend on the same lock as the absolute one."""
    real = tmp_path / "store"
    real.mkdir()
    absolute = real / "canonical.sqlite3"

    writer = CanonicalWriter(absolute)
    try:
        monkeypatch.chdir(tmp_path)
        relative = Path("store") / "canonical.sqlite3"
        assert relative != absolute
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(relative)
    finally:
        writer.close()
        monkeypatch.undo()


def test_dotdot_alias_shares_one_lock(tmp_path):
    real = tmp_path / "nested" / "store"
    real.mkdir(parents=True)
    absolute = real / "canonical.sqlite3"
    dotdot_alias = real / ".." / "store" / "canonical.sqlite3"
    assert dotdot_alias != absolute

    writer = CanonicalWriter(absolute)
    try:
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(dotdot_alias)
    finally:
        writer.close()

    # After release the alias can acquire the same store.
    alias_writer = CanonicalWriter(dotdot_alias)
    alias_writer.close()


def _make_symlink(link: Path, target: Path, *, directory: bool) -> bool:
    try:
        link.symlink_to(target, target_is_directory=directory)
        return True
    except (OSError, NotImplementedError):
        return False


def test_symlink_alias_blocks_in_both_directions(tmp_path):
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    alias_root = tmp_path / "alias"
    if not _make_symlink(alias_root, real, directory=True):
        pytest.skip("platform/permissions cannot create symlinks")
    real_db = real / "sub" / "canonical.sqlite3"
    alias_db = alias_root / "sub" / "canonical.sqlite3"
    assert alias_db != real_db

    # real -> blocks alias
    writer = CanonicalWriter(real_db)
    try:
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(alias_db)
    finally:
        writer.close()

    # alias -> blocks real
    writer = CanonicalWriter(alias_db)
    try:
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(real_db)
    finally:
        writer.close()


def test_symlink_alias_cannot_bypass_restore_ownership(tmp_path):
    """Restore through an alias must contend with a writer on the real path."""
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    alias_root = tmp_path / "alias"
    if not _make_symlink(alias_root, real, directory=True):
        pytest.skip("platform/permissions cannot create symlinks")
    real_db = real / "sub" / "canonical.sqlite3"
    alias_db = alias_root / "sub" / "canonical.sqlite3"

    _seed(real_db)
    backup, manifest_path, _ = _make_backup(real_db, tmp_path)

    writer = CanonicalWriter(real_db)
    before = real_db.read_bytes()
    try:
        with pytest.raises(CanonicalStoreBusyError):
            _restore(alias_db, backup, manifest_path)
        assert real_db.read_bytes() == before
    finally:
        writer.close()


def test_restore_through_symlink_alias_replaces_canonical_target(tmp_path):
    """Cutover must hit the canonical file, never replace the alias entry."""
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    alias_root = tmp_path / "alias"
    if not _make_symlink(alias_root, real, directory=True):
        pytest.skip("platform/permissions cannot create symlinks")
    real_db = real / "sub" / "canonical.sqlite3"
    alias_db = alias_root / "sub" / "canonical.sqlite3"

    _seed(real_db)
    backup, manifest_path, _ = _make_backup(real_db, tmp_path)

    result = _restore(alias_db, backup, manifest_path)

    # The canonical target is the restored store...
    assert result["restored_path"] == str(canonical_store_path(real_db))
    validate_canonical_sqlite(real_db)
    assert _epoch_and_seq(real_db)[0] == 2
    # ...and the alias is still a symlink directory, not a replaced entry.
    assert alias_root.is_symlink()
    assert alias_db.exists()


def test_windows_case_variant_shares_one_lock(tmp_path):
    """On case-insensitive filesystems a case variant is the same store."""
    if os.name != "nt":
        pytest.skip("case-insensitive path identity is Windows-specific")
    store = tmp_path / "StoreDir"
    store.mkdir()
    lower = tmp_path / "storedir" / "canonical.sqlite3"
    upper = store / "canonical.sqlite3"
    assert canonical_store_path(lower) == canonical_store_path(upper)

    writer = CanonicalWriter(upper)
    try:
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(lower)
    finally:
        writer.close()


def test_permanent_lock_path_is_stable_and_never_deleted(tmp_path):
    db = tmp_path / "canonical.sqlite3"
    expected_lock = store_lock_path(canonical_store_path(db))

    writer = CanonicalWriter(db)
    writer.close()
    # The lock pathname is stable across the writer lifecycle and after release.
    assert expected_lock.exists()
    assert CanonicalStoreLock(db).lock_path == expected_lock
    writer = CanonicalWriter(db)
    writer.close()
    assert expected_lock.exists()


# --------------------------------------------------------------------------- #
# Backup generations (immutable, manifest-committed pairs)
# --------------------------------------------------------------------------- #


def _generation_files(backup_dir: Path) -> list[str]:
    return sorted(p.name for p in backup_dir.iterdir())


def test_publish_backup_generation_commits_a_verified_pair(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)
    backup_dir = tmp_path / "generations"

    published = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)

    assert published.backup_path.exists()
    assert published.manifest_path.exists()
    assert published.backup_path.name == f"opip_canonical_v1.backup.{published.generation_id}.sqlite3"
    assert published.manifest_path.name == (
        f"opip_canonical_v1.backup.{published.generation_id}.manifest.json"
    )
    assert_rollback_journal_backup(published.backup_path)
    assert not Path(f"{published.backup_path}-wal").exists()
    assert not Path(f"{published.backup_path}-shm").exists()

    manifest = read_backup_manifest(published.manifest_path)
    assert manifest["backup_file"] == published.backup_path.name
    assert manifest["sha256"] == hash_file_sha256(published.backup_path)
    assert manifest["sha256"] == published.sha256
    assert manifest["event_count"] == 2
    verify_backup_manifest(
        manifest, backup_path=published.backup_path, expected_source_release_sha=RELEASE_SHA
    )

    target = tmp_path / "restored" / "canonical.sqlite3"
    result = _restore(target, published.backup_path, published.manifest_path)
    assert result["history_epoch"] == 2
    validate_canonical_sqlite(target)


def test_generation_ids_are_unique_and_not_second_resolution(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup_dir = tmp_path / "generations"

    ids = {
        publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA).generation_id
        for _ in range(5)
    }
    assert len(ids) == 5


def test_second_generation_preserves_first_generation(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup_dir = tmp_path / "generations"
    first = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    first_db_bytes = first.backup_path.read_bytes()
    first_manifest_bytes = first.manifest_path.read_bytes()

    _seed(live, count=1, start=50)
    second = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)

    assert first.backup_path != second.backup_path
    assert first.backup_path.read_bytes() == first_db_bytes
    assert first.manifest_path.read_bytes() == first_manifest_bytes
    # Both generations remain independently verifiable and restorable.
    for generation in (first, second):
        verify_backup_manifest(
            read_backup_manifest(generation.manifest_path),
            backup_path=generation.backup_path,
            expected_source_release_sha=RELEASE_SHA,
        )
    assert read_backup_manifest(first.manifest_path)["event_count"] == 1
    assert read_backup_manifest(second.manifest_path)["event_count"] == 2


def test_generation_failure_before_db_publication_preserves_previous(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup_dir = tmp_path / "generations"
    first = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    first_db_bytes = first.backup_path.read_bytes()
    files_before = _generation_files(backup_dir)

    import app.opip.canonical.backup as backup_module

    def _boom(*_a, **_k):
        raise OSError("db publish injected failure")

    monkeypatch.setattr(backup_module, "_claim_immutable_path", _boom)
    with pytest.raises(OSError, match="db publish injected failure"):
        publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    monkeypatch.undo()

    assert first.backup_path.read_bytes() == first_db_bytes
    assert _generation_files(backup_dir) == files_before
    verify_backup_manifest(
        read_backup_manifest(first.manifest_path),
        backup_path=first.backup_path,
        expected_source_release_sha=RELEASE_SHA,
    )


def test_generation_db_without_manifest_is_not_committed(tmp_path, monkeypatch):
    """Failure between DB publish and manifest publish leaves an orphan, not a backup."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup_dir = tmp_path / "generations"
    first = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    first_db_bytes = first.backup_path.read_bytes()
    first_manifest_bytes = first.manifest_path.read_bytes()

    import app.opip.canonical.backup as backup_module

    real_write = backup_module.write_backup_manifest_create_only

    def _boom(*_a, **_k):
        raise OSError("manifest publish injected failure")

    monkeypatch.setattr(backup_module, "write_backup_manifest_create_only", _boom)
    with pytest.raises(OSError, match="manifest publish injected failure"):
        publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    monkeypatch.undo()
    backup_module.write_backup_manifest_create_only = real_write

    # Previous generation is fully intact.
    assert first.backup_path.read_bytes() == first_db_bytes
    assert first.manifest_path.read_bytes() == first_manifest_bytes
    verify_backup_manifest(
        read_backup_manifest(first.manifest_path),
        backup_path=first.backup_path,
        expected_source_release_sha=RELEASE_SHA,
    )

    # The orphaned new generation database has no manifest, so it is not
    # committed and best-effort cleanup removed it: only generation A remains.
    assert _generation_files(backup_dir) == sorted(
        [first.backup_path.name, first.manifest_path.name]
    )

    # The acceptance property itself: a database without its manifest can never
    # be authorized for restore, even if the bytes are perfectly valid.
    orphan_db = backup_dir / "opip_canonical_v1.backup.ORPHAN.sqlite3"
    shutil.copy2(first.backup_path, orphan_db)
    assert_rollback_journal_backup(orphan_db)
    with pytest.raises(BackupProvenanceError, match="missing or unreadable"):
        _restore(
            tmp_path / "target.sqlite3",
            orphan_db,
            backup_dir / "opip_canonical_v1.backup.ORPHAN.manifest.json",
        )
    # Generation A is still the recoverable one.
    verify_backup_manifest(
        read_backup_manifest(first.manifest_path),
        backup_path=first.backup_path,
        expected_source_release_sha=RELEASE_SHA,
    )


def test_generation_preserved_on_manifest_durability_and_replace_failure(
    tmp_path, monkeypatch
):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup_dir = tmp_path / "generations"
    first = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    first_db_bytes = first.backup_path.read_bytes()
    first_manifest_bytes = first.manifest_path.read_bytes()
    files_before = _generation_files(backup_dir)

    # (a) manifest no-replace publication conflict/failure
    import app.opip.canonical.backup as backup_module

    def _boom(*_a, **_k):
        raise OSError("manifest replace injected failure")

    monkeypatch.setattr(backup_module, "write_backup_manifest_create_only", _boom)
    with pytest.raises(OSError, match="manifest replace injected failure"):
        publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    monkeypatch.undo()

    # (b) manifest durability (fsync) failure
    monkeypatch.setattr(
        "app.opip.canonical.schema.os.fsync",
        lambda _fd: (_ for _ in ()).throw(OSError(5, "I/O error")),
    )
    with pytest.raises(CanonicalDurabilityError):
        publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
    monkeypatch.undo()

    assert first.backup_path.read_bytes() == first_db_bytes
    assert first.manifest_path.read_bytes() == first_manifest_bytes
    assert _generation_files(backup_dir) == files_before
    verify_backup_manifest(
        read_backup_manifest(first.manifest_path),
        backup_path=first.backup_path,
        expected_source_release_sha=RELEASE_SHA,
    )


def test_repeated_generations_keep_earlier_ones_valid(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    backup_dir = tmp_path / "generations"
    published = []
    for index in range(4):
        _seed(live, count=1, start=index * 10)
        published.append(
            publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)
        )

    assert len({g.backup_path for g in published}) == 4
    for index, generation in enumerate(published):
        verify_backup_manifest(
            read_backup_manifest(generation.manifest_path),
            backup_path=generation.backup_path,
            expected_source_release_sha=RELEASE_SHA,
        )
        assert read_backup_manifest(generation.manifest_path)["event_count"] == index + 1


def test_generation_a_manifest_cannot_authorize_generation_b_database(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup_dir = tmp_path / "generations"
    first = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)

    _seed(live, count=1, start=50)
    second = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)

    target = tmp_path / "target.sqlite3"
    before = live.read_bytes()

    # A's manifest names A's file, so it cannot authorize B's database.
    with pytest.raises(BackupProvenanceError, match="different file"):
        _restore(target, second.backup_path, first.manifest_path)

    # And B's manifest cannot authorize A's database.
    with pytest.raises(BackupProvenanceError, match="different file"):
        _restore(target, first.backup_path, second.manifest_path)

    assert live.read_bytes() == before


def test_generation_publisher_uses_the_authoritative_contract(tmp_path):
    """The publisher produces a restore-ready pair with no manual manifest work."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=3)
    backup_dir = tmp_path / "generations"

    published = publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)

    assert published.source_release_sha == RELEASE_SHA
    target = tmp_path / "restored" / "canonical.sqlite3"
    result = _restore(target, published.backup_path, published.manifest_path)
    assert result["backup_sha256"] == published.sha256
    assert result["source_release_sha"] == RELEASE_SHA
    validate_canonical_sqlite(target)


def test_backup_generation_paths_are_derived_from_the_generation_id(tmp_path):
    database, manifest = backup_generation_paths(
        tmp_path, generation_id="GEN123", stem="custom_stem"
    )
    assert database == tmp_path / "custom_stem.backup.GEN123.sqlite3"
    assert manifest == tmp_path / "custom_stem.backup.GEN123.manifest.json"


def test_new_backup_generation_id_is_unique(tmp_path):
    ids = {new_backup_generation_id() for _ in range(50)}
    assert len(ids) == 50


# --------------------------------------------------------------------------- #
# Race-safe immutable publication (no-replace claims)
# --------------------------------------------------------------------------- #


def test_claim_immutable_path_refuses_to_replace(tmp_path):
    """The publication primitive must never overwrite an existing artifact."""
    from app.opip.canonical.backup import _claim_immutable_path

    staged = tmp_path / ".staged"
    final = tmp_path / "final.sqlite3"
    staged.write_bytes(b"new")
    final.write_bytes(b"committed")

    with pytest.raises(BackupProvenanceError, match="already published|immutable"):
        _claim_immutable_path(staged, final)

    # The committed bytes are byte-for-byte intact.
    assert final.read_bytes() == b"committed"
    assert staged.read_bytes() == b"new"


def test_backup_database_conflict_preserves_existing_bytes(tmp_path):
    """A second attempt at a published path fails and changes nothing."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    published = tmp_path / "published.sqlite3"
    backup_database(live, published)
    before = published.read_bytes()

    _seed(live, count=1, start=50)
    with pytest.raises(BackupProvenanceError, match="immutable|already exists"):
        backup_database(live, published)
    assert published.read_bytes() == before
    validate_canonical_sqlite(published)


def test_manifest_create_only_refuses_to_replace(tmp_path):
    manifest_path = tmp_path / "gen.manifest.json"
    write_backup_manifest_create_only({"schema_version": 1, "value": "winner"}, manifest_path)
    before = manifest_path.read_bytes()

    with pytest.raises(BackupProvenanceError, match="already published|immutable"):
        write_backup_manifest_create_only({"schema_version": 1, "value": "loser"}, manifest_path)

    assert manifest_path.read_bytes() == before
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["value"] == "winner"


def test_concurrent_same_generation_id_publishes_exactly_one_pair(tmp_path):
    """Two publishers racing on one generation id must yield one valid pair.

    Deterministic: both threads are released at the same point (after staging,
    immediately before the real no-replace claim), so they genuinely contend on
    the publication syscall rather than being serialised by a sleep. The
    no-replace property itself is never mocked.
    """
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)
    backup_dir = tmp_path / "generations"
    import app.opip.canonical.backup as backup_module

    real_validate = backup_module.validate_canonical_sqlite
    barrier = threading.Barrier(2, timeout=30)
    lock = threading.Lock()

    def _synchronised(path):  # noqa: ANN001
        # Staging exists and is complete; hold both publishers here so they
        # reach the claim together.
        try:
            barrier.wait()
        except threading.BrokenBarrierError:  # pragma: no cover - defensive
            pass
        with lock:
            return real_validate(path)

    monkeypatch_holder = {"module": backup_module}
    monkeypatch_holder["module"].validate_canonical_sqlite = _synchronised

    results: dict[str, str] = {}

    def _publish(name: str) -> None:
        try:
            publish_backup_generation(
                live, backup_dir, source_release_sha=RELEASE_SHA, generation_id="SAME-ID"
            )
            results[name] = "OK"
        except Exception as exc:  # noqa: BLE001 - classified below
            results[name] = type(exc).__name__

    threads = [threading.Thread(target=_publish, args=(name,)) for name in ("A", "B")]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        monkeypatch_holder["module"].validate_canonical_sqlite = real_validate

    # Exactly one publisher wins; the other is refused, never silently overwriting.
    assert sorted(results.values()) == ["BackupProvenanceError", "OK"], results

    database, manifest = backup_generation_paths(backup_dir, generation_id="SAME-ID")
    assert database.exists() and manifest.exists()

    manifest_payload = read_backup_manifest(manifest)
    assert manifest_payload["backup_file"] == database.name
    assert manifest_payload["sha256"] == hash_file_sha256(database)
    verify_backup_manifest(
        manifest_payload, backup_path=database, expected_source_release_sha=RELEASE_SHA
    )
    # The winning pair is a real, complete generation.
    assert manifest_payload["event_count"] == 2
    target = tmp_path / "restored" / "canonical.sqlite3"
    result = _restore(target, database, manifest)
    assert result["history_epoch"] == 2
    validate_canonical_sqlite(target)


def test_losing_publisher_does_not_delete_winner_artifacts(tmp_path):
    """A refused publisher must not clean up a peer's committed pair."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup_dir = tmp_path / "generations"
    winner = publish_backup_generation(
        live, backup_dir, source_release_sha=RELEASE_SHA, generation_id="SAME-ID"
    )
    db_bytes = winner.backup_path.read_bytes()
    manifest_bytes = winner.manifest_path.read_bytes()

    # The loser reaches the same generation id and is refused at the DB claim, so
    # its cleanup path never runs against the winner's artifacts.
    with pytest.raises(BackupProvenanceError):
        publish_backup_generation(
            live, backup_dir, source_release_sha=RELEASE_SHA, generation_id="SAME-ID"
        )

    assert winner.backup_path.read_bytes() == db_bytes
    assert winner.manifest_path.read_bytes() == manifest_bytes
    verify_backup_manifest(
        read_backup_manifest(winner.manifest_path),
        backup_path=winner.backup_path,
        expected_source_release_sha=RELEASE_SHA,
    )
    assert _generation_files(backup_dir) == sorted(
        [winner.backup_path.name, winner.manifest_path.name]
    )


def test_manifest_conflict_after_db_claim_does_not_remove_peer_manifest(tmp_path, monkeypatch):
    """A manifest conflict must not delete a peer's manifest, nor fake success."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup_dir = tmp_path / "generations"
    database, manifest = backup_generation_paths(backup_dir, generation_id="SAME-ID")
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Pre-existing manifest for this generation (as if a peer already committed).
    manifest.write_text(json.dumps({"schema_version": 1, "owner": "peer"}), encoding="utf-8")
    committed = manifest.read_bytes()

    with pytest.raises(BackupProvenanceError, match="already published|immutable"):
        publish_backup_generation(
            live, backup_dir, source_release_sha=RELEASE_SHA, generation_id="SAME-ID"
        )

    assert manifest.read_bytes() == committed
    # The peer's manifest was not removed, and no uncommitted DB was left behind.
    assert not database.exists()


# --------------------------------------------------------------------------- #
# Bounded staging names (long-path safety)
# --------------------------------------------------------------------------- #


def test_staging_basename_is_bounded_and_destination_independent(tmp_path):
    """Staging length must not depend on stem/generation length."""
    from app.opip.canonical.backup import (
        _DB_STAGING_PREFIX,
        _DB_STAGING_SUFFIX,
        _MANIFEST_STAGING_PREFIX,
        _MANIFEST_STAGING_SUFFIX,
        _short_staging_path,
    )

    lengths = set()
    for _ in range(5):
        staged = _short_staging_path(
            tmp_path, prefix=_DB_STAGING_PREFIX, suffix=_DB_STAGING_SUFFIX
        )
        lengths.add(len(staged.name))
        assert staged.name.startswith(_DB_STAGING_PREFIX)
        assert staged.name.endswith(_DB_STAGING_SUFFIX)
    # Constant across draws, and bounded regardless of any destination name.
    assert len(lengths) == 1
    assert lengths.pop() <= 48

    manifest_stage = _short_staging_path(
        tmp_path, prefix=_MANIFEST_STAGING_PREFIX, suffix=_MANIFEST_STAGING_SUFFIX
    )
    assert manifest_stage.name.startswith(_MANIFEST_STAGING_PREFIX)
    assert len(manifest_stage.name) <= 48


def test_staging_name_never_contains_the_generation_filename(tmp_path, monkeypatch):
    """The actual staging path used by a publish must not embed the destination."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup_dir = tmp_path / "generations"
    import app.opip.canonical.backup as backup_module

    seen: list[str] = []
    real_validate = backup_module.validate_canonical_sqlite

    def _record(path):  # noqa: ANN001
        seen.append(Path(path).name)
        return real_validate(path)

    monkeypatch.setattr(backup_module, "validate_canonical_sqlite", _record)
    published = publish_backup_generation(
        live, backup_dir, source_release_sha=RELEASE_SHA, generation_id="GEN-VERY-LONG-" + "X" * 40
    )
    monkeypatch.undo()

    staging_names = [name for name in seen if name.startswith(".opip-bk.")]
    assert staging_names, seen
    for name in staging_names:
        assert published.generation_id not in name
        assert published.backup_path.name not in name
        assert len(name) <= 48


@pytest.mark.skipif(os.name != "nt", reason="Windows path-length limit regression")
def test_publish_generation_succeeds_in_deeply_nested_windows_directory(tmp_path):
    """Real SQLite open at a depth where the old staging strategy failed.

    The final generation filename is still usable, but embedding it in the
    staging name pushed the path past what SQLite could open
    ("unable to open database file"). The bounded staging name must survive it.
    """
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=2)

    # Nest deep enough that the old staging strategy would exceed the practical
    # Windows limit, while the final artifact path itself stays openable.
    deep = tmp_path / ("d" * 45) / ("e" * 45)
    deep.mkdir(parents=True, exist_ok=True)
    assert 150 <= len(str(deep)) < 200

    published = publish_backup_generation(live, deep, source_release_sha=RELEASE_SHA)

    # The final artifact is usable...
    assert_rollback_journal_backup(published.backup_path)
    validate_canonical_sqlite(published.backup_path)
    # ...but the previous staging strategy embedded the full destination name
    # again and would have pushed the staging path past what SQLite can open.
    old_style = published.backup_path.with_name(
        f".{published.backup_path.name}.staging.1234.{'a' * 32}.sqlite3"
    )
    assert len(str(old_style)) > 260
    assert len(str(published.backup_path)) < 260

    manifest = read_backup_manifest(published.manifest_path)
    verify_backup_manifest(
        manifest, backup_path=published.backup_path, expected_source_release_sha=RELEASE_SHA
    )
    target = tmp_path / "restored" / "canonical.sqlite3"
    result = _restore(target, published.backup_path, published.manifest_path)
    assert result["history_epoch"] == 2


# --------------------------------------------------------------------------- #
# Windows authoritative directory durability is fail-closed (real behaviour)
#
# These tests deliberately opt out of the shared Windows durability stub (see the
# `test_windows_real_*` prefix in tests/conftest.py) so they observe the real
# primitive, real publication and real restore.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.name != "nt", reason="Windows platform policy test")
def test_windows_real_authoritative_directory_durability_is_fail_closed(tmp_path):
    """The authoritative primitive must refuse, not claim an unprovable guarantee."""
    with pytest.raises(CanonicalDurabilityError) as exc_info:
        fsync_directory_required(tmp_path)

    message = str(exc_info.value)
    assert "unsupported" in message
    assert "Windows" in message
    # The refusal explains itself precisely rather than reporting a flush failure.
    assert "directory durability flush failed" not in message


@pytest.mark.skipif(os.name != "nt", reason="Windows platform policy test")
def test_windows_real_publication_cannot_report_success_without_durability(
    tmp_path,
):
    """Authoritative publication must fail closed rather than claim durability."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup_dir = tmp_path / "generations"

    # This test opts out of the Windows logic stub, so the real authoritative
    # primitive is in force for the whole publication path.
    with pytest.raises(CanonicalDurabilityError):
        publish_backup_generation(live, backup_dir, source_release_sha=RELEASE_SHA)

    # No generation was committed and no success receipt exists.
    manifests = list(backup_dir.glob("*.manifest.json")) if backup_dir.exists() else []
    assert manifests == []
    # The live store is untouched.
    validate_canonical_sqlite(live)


@pytest.mark.skipif(os.name != "nt", reason="Windows platform policy test")
def test_windows_real_restore_failure_preserves_live_store(tmp_path, monkeypatch):
    """A restore cannot report success, and must leave the live store intact."""
    db = tmp_path / "canonical.sqlite3"
    _seed(db, count=2)

    # Build the verified backup pair with durability stubbed: this test is about
    # the restore cutover, and fixture construction is not the subject.
    import app.opip.canonical.backup as backup_module

    with monkeypatch.context() as setup_stub:
        setup_stub.setattr(
            backup_module, "fsync_directory_required", lambda _directory: None
        )
        backup, manifest_path, _ = _make_backup(db, tmp_path)

    before = db.read_bytes()
    events_before = _event_count(db)

    # Real authoritative durability now applies to the cutover, so the restore
    # genuinely refuses instead of reporting success.
    with pytest.raises(CanonicalDurabilityError):
        _restore(db, backup, manifest_path)

    # Post-replace semantics: the atomic cutover precedes the namespace-durability
    # step, so the verified snapshot may already be visible. PR-A0 does not fake a
    # rollback. The invariants that must hold are: no success was reported, the
    # installed store is a valid canonical database at the restored epoch, and
    # ownership was released. Byte-equality with the pre-restore file is only the
    # invariant for failures *before* the replace (covered separately by
    # test_restore_fails_when_staged_file_durability_fails).
    assert db.read_bytes() != before
    assert events_before == 2
    validate_canonical_sqlite(db)
    assert _epoch_and_seq(db) == (2, 1)
    assert CanonicalStoreLock(db).held is False


# --------------------------------------------------------------------------- #
# Generation component validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    ["", "   ", ".", "..", "a/b", "a\\b", "nul\x00byte", "x" * 65],
)
def test_generation_component_validation_rejects_unsafe_values(tmp_path, bad):
    with pytest.raises(BackupProvenanceError):
        backup_generation_paths(tmp_path, generation_id=bad)


def test_generation_component_validation_rejects_unsafe_stem(tmp_path):
    with pytest.raises(BackupProvenanceError):
        backup_generation_paths(tmp_path, generation_id="ok", stem="../escape")


def test_publisher_rejects_unsafe_generation_id(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    for bad in ("../escape", "a/b", "", "x" * 65):
        with pytest.raises(BackupProvenanceError):
            publish_backup_generation(
                live, tmp_path / "generations", source_release_sha=RELEASE_SHA, generation_id=bad
            )


def test_default_generation_id_and_stem_still_work(tmp_path):
    """Generated defaults are unaffected by the new validation."""
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    published = publish_backup_generation(live, tmp_path / "generations", source_release_sha=RELEASE_SHA)
    assert published.backup_path.exists()
    assert published.manifest_path.exists()
    require_generation_component(published.generation_id, field_name="generation_id")
    require_generation_component(DEFAULT_BACKUP_STEM, field_name="stem")


def test_initialize_schema_rejects_incompatible_version(tmp_path):
    """Guard against silent compatibility drift in the store this PR hardens."""
    db = tmp_path / "canonical.sqlite3"
    conn = connect(db, read_only=False)
    try:
        initialize_schema(conn, now_iso="2026-01-01T00:00:00Z")
        conn.execute("UPDATE meta SET schema_version = 42 WHERE id = 1")
        conn.commit()
        with pytest.raises(Exception):
            initialize_schema(conn, now_iso="2026-01-01T00:00:00Z")
    finally:
        conn.close()
