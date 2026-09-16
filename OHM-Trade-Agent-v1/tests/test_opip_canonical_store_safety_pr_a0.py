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
from pathlib import Path

import pytest

from app.opip.canonical.backup import (
    MANIFEST_SCHEMA_VERSION,
    BackupFormatError,
    BackupProvenanceError,
    assert_rollback_journal_backup,
    backup_database,
    build_backup_manifest,
    hash_file_sha256,
    read_backup_manifest,
    read_sqlite_journal_format,
    verify_backup_manifest,
    write_backup_manifest,
)
from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.recovery import restore_from_backup, run_backup_restore_drill
from app.opip.canonical.schema import (
    CanonicalCheckpointError,
    CanonicalDbValidationError,
    CanonicalStoreBusyError,
    CanonicalStoreLock,
    checkpoint_wal_strict,
    connect,
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
    db = tmp_path / "canonical.sqlite3"
    _seed(db)
    backup, manifest_path, _ = _make_backup(db, tmp_path)

    seen: list[bool] = []
    from app.opip.canonical import recovery as recovery_module

    real_checkpoint = recovery_module._checkpoint_live_wal

    def _probe(live_db):
        # Restore must already own the store at this point.
        seen.append(CanonicalStoreLock(live_db).held)
        return real_checkpoint(live_db)

    recovery_module._checkpoint_live_wal = _probe
    try:
        _restore(db, backup, manifest_path)
    finally:
        recovery_module._checkpoint_live_wal = real_checkpoint
    assert seen == [False]  # a *new* lock cannot be taken while restore owns it
    assert CanonicalStoreLock(db).held is False


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


def test_backup_manifest_rejects_unverified_release_provenance(tmp_path):
    live = tmp_path / "canonical.sqlite3"
    _seed(live)
    backup = tmp_path / "backup.sqlite3"
    backup_database(live, backup)

    for bad in ("x", "UNVERIFIED", "unknown", "HEAD", "main", "808a308", "g" * 40, ""):
        with pytest.raises(BackupProvenanceError, match="40-character"):
            build_backup_manifest(backup_path=backup, source_release_sha=bad)


def test_backup_replace_failure_preserves_previous_backup(tmp_path, monkeypatch):
    live = tmp_path / "canonical.sqlite3"
    _seed(live, count=1)
    backup = tmp_path / "backup.sqlite3"
    backup_database(live, backup)
    first_hash = hash_file_sha256(backup)

    _seed(live, count=1, start=10)
    import app.opip.canonical.backup as backup_module

    real_replace = backup_module.os.replace

    def _boom(src, dst):
        if Path(dst) == backup:
            raise OSError("publish injected failure")
        return real_replace(src, dst)

    monkeypatch.setattr(backup_module.os, "replace", _boom)
    with pytest.raises(OSError, match="publish injected failure"):
        backup_database(live, backup)
    monkeypatch.undo()

    assert hash_file_sha256(backup) == first_hash
    validate_canonical_sqlite(backup)


# --------------------------------------------------------------------------- #
# Manifest publication atomicity
# --------------------------------------------------------------------------- #


def test_manifest_publication_is_atomic_and_durable(tmp_path, monkeypatch):
    import app.opip.canonical.backup as backup_module

    manifest_path = tmp_path / "manifest.json"
    write_backup_manifest({"schema_version": 1, "value": "first"}, manifest_path)

    fsyncs: list[Path] = []
    real_fsync_directory = backup_module.fsync_directory

    def _record(path):
        fsyncs.append(Path(path))
        return real_fsync_directory(path)

    monkeypatch.setattr(backup_module, "fsync_directory", _record)
    write_backup_manifest({"schema_version": 1, "value": "second"}, manifest_path)
    monkeypatch.undo()

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["value"] == "second"
    assert fsyncs == [tmp_path]


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

    # Tamper release provenance.
    backup_database(live, backup)
    manifest = build_backup_manifest(backup_path=backup, source_release_sha=RELEASE_SHA)
    write_backup_manifest(manifest, manifest_path)
    _rewrite_manifest(manifest_path, lambda m: m.update(source_release_sha=OTHER_RELEASE_SHA))
    with pytest.raises(BackupProvenanceError, match="expected release"):
        _restore(live, backup, manifest_path)

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
