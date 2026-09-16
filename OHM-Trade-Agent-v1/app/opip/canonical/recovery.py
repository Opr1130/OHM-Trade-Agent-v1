"""Restore drills and recovery helpers for the canonical store."""

from __future__ import annotations

import contextlib
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from app.opip.canonical.backup import (
    BackupProvenanceError,
    assert_regular_file,
    assert_rollback_journal_backup,
    assert_sidecar_free,
    backup_database,
    build_backup_manifest,
    hash_file_sha256,
    normalize_to_rollback_journal,
    read_backup_manifest,
    verify_backup_manifest,
    write_backup_manifest,
)
from app.opip.canonical.schema import (
    CanonicalCheckpointError,
    CanonicalDbValidationError,
    CanonicalStoreLock,
    checkpoint_wal_strict,
    connect,
    fsync_directory_required,
    fsync_file_required,
    remove_sqlite_sidecars,
    store_lock_path,
    validate_canonical_sqlite,
)
from app.opip.canonical.writer import CanonicalWriter

#: Staging artifacts are private to one restore invocation and always share this
#: marker, which also keeps them distinguishable from any real canonical file.
_STAGING_MARKER = ".restore-staging."


def _cleanup_staging_artifacts(staged: Path) -> None:
    """Remove artifacts created by this invocation for one private staging path.

    Scoped strictly to the caller's own random staging path: the staged database,
    its SQLite sidecars, and the *ephemeral* staging ``.writer.lock`` that
    ``CanonicalWriter(staged)`` creates. The canonical live store's lock file is a
    different path and is never touched here.

    Best-effort by design: these are private, regenerable scratch artifacts, and
    failing to delete one must never mask the real error or risk canonical
    evidence.
    """
    if _STAGING_MARKER not in staged.name:
        # Refuse to delete anything that is not recognisably our own staging
        # artifact, so this helper can never be pointed at a real canonical file.
        return
    with contextlib.suppress(OSError):
        if staged.exists():
            staged.unlink()
    with contextlib.suppress(OSError):
        remove_sqlite_sidecars(staged)
    with contextlib.suppress(OSError):
        staging_lock = store_lock_path(staged)
        if staging_lock.exists():
            staging_lock.unlink()



def _live_history_epoch(live_db: Path) -> int | None:
    """Return the live store's current ``history_epoch``, or ``None`` if absent.

    Ambiguity fails closed: if a live database exists but its epoch cannot be
    read, the caller cannot prove that canonical commit order will not regress,
    so recovery must not proceed.
    """
    if not live_db.is_file():
        return None
    try:
        conn = connect(live_db, read_only=True)
    except Exception as exc:  # noqa: BLE001 — re-raised as fail-closed provenance
        raise BackupProvenanceError(
            f"cannot read the live canonical store to prove epoch monotonicity: "
            f"{live_db}: {exc}"
        ) from exc
    try:
        row = conn.execute(
            "SELECT history_epoch FROM meta WHERE id = 1"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — re-raised as fail-closed provenance
        raise BackupProvenanceError(
            f"live canonical meta is unreadable; refusing to restore over an "
            f"ambiguous live store: {live_db}: {exc}"
        ) from exc
    finally:
        conn.close()
    if row is None:
        raise BackupProvenanceError(
            f"live canonical meta row is missing; refusing to restore over an "
            f"ambiguous live store: {live_db}"
        )
    return int(row["history_epoch"])


def _checkpoint_live_wal(live_db: Path) -> None:
    """Merge committed live WAL frames into the live database file.

    Restore deletes ``-wal``/``-shm`` before the atomic cutover, so the frames
    must already be in the file: otherwise a failed replacement would leave a
    live database that has silently lost committed state. Runs before any
    destructive sidecar handling, and fails closed when the checkpoint cannot be
    proven complete.
    """
    if not live_db.is_file():
        return
    try:
        connection = connect(live_db, read_only=False)
    except Exception as exc:  # noqa: BLE001 — re-raised as fail-closed
        raise CanonicalCheckpointError(
            f"cannot open the live canonical store to checkpoint its WAL: {exc}"
        ) from exc
    try:
        checkpoint_wal_strict(connection)
        connection.commit()
    finally:
        connection.close()


def _assert_no_staged_sidecars(staged: Path) -> None:
    """A staged cutover source must not still hold unfinalized WAL frames.

    Only the WAL is inspected: ``-shm`` is a shared-memory index that carries no
    evidence, and a missing WAL is normal for a cleanly checkpointed database.
    """
    wal = Path(f"{staged}-wal")
    if not wal.exists():
        return
    try:
        size = wal.stat().st_size
    except OSError as exc:
        raise CanonicalDbValidationError(
            f"cannot inspect staged SQLite sidecar {wal}: {exc}"
        ) from exc
    if size:
        raise CanonicalCheckpointError(
            f"staged restore source still holds unfinalized WAL frames: {wal} "
            f"({size} bytes)"
        )


def restore_from_backup(
    *,
    backup_db: Path,
    live_db: Path,
    manifest_path: Path,
    expected_source_release_sha: str,
    advance_epoch: bool = True,
) -> dict[str, Any]:
    """Restore a verified backup snapshot into the live canonical path.

    Safety semantics, in order:

    1. ``advance_epoch=False`` is refused. Canonical commit order
       ``(history_epoch, local_sequence)`` must never regress on a live restore.
    2. The backup must be self-contained; adjacent ``-wal``/``-shm`` files make
       the snapshot ambiguous and are refused rather than ignored.
    3. The manifest is loaded and verified against the backup file and the
       caller's expected release SHA before anything is touched.
    4. The canonical-store exclusivity lock is acquired, so an active writer
       (which holds the same lock for its lifetime) blocks restore.
    5. The live WAL is strictly checkpointed, which both proves the live file is
       a complete snapshot and makes later sidecar cleanup non-destructive.
    6. Any failure before the final cutover leaves the previous live database
       valid and untouched.
    """
    started = time.perf_counter()
    backup_db = Path(backup_db)
    live_db = Path(live_db)
    manifest_path = Path(manifest_path)

    if not advance_epoch:
        raise BackupProvenanceError(
            "advance_epoch=False would leave canonical commit order "
            "(history_epoch, local_sequence) able to regress; refusing unsafe "
            "live restore"
        )

    # Path safety: the restore caller selects the backup, and the manifest may
    # only confirm that choice. Nothing may redirect recovery onto the live
    # store or onto another artifacts' path.
    if backup_db.resolve() == live_db.resolve():
        raise BackupProvenanceError(
            "backup database and live database resolve to the same path; "
            "restoring a store over itself is never a recovery operation"
        )
    if manifest_path.resolve() == live_db.resolve():
        raise BackupProvenanceError(
            "manifest path resolves to the live database; refusing to use a "
            "database as a manifest"
        )
    assert_regular_file(
        backup_db, field_name="backup database", error_type=BackupProvenanceError
    )

    # Non-mutating artifact preflight, before anything opens SQLite: the backup
    # must be a single self-contained rollback-journal file. Sidecar absence and
    # the file's own journal format are both required, and neither check creates
    # ``-wal``/``-shm`` beside the artifact.
    assert_sidecar_free(
        backup_db, field_name="backup database", error_type=BackupProvenanceError
    )
    assert_rollback_journal_backup(backup_db)

    manifest = read_backup_manifest(manifest_path)
    verify_backup_manifest(
        manifest,
        backup_path=backup_db,
        expected_source_release_sha=expected_source_release_sha,
    )

    live_db.parent.mkdir(parents=True, exist_ok=True)
    staged = live_db.with_name(
        f".{live_db.name}.restore-staging.{os.getpid()}.{uuid.uuid4().hex}.sqlite3"
    )
    if staged.resolve() == live_db.resolve():
        raise BackupProvenanceError("restore staging path collides with the live database")

    store_lock = CanonicalStoreLock(live_db)
    store_lock.acquire()
    try:
        # Prove epoch monotonicity and make the live file self-contained before
        # anything destructive happens to live sidecars.
        previous_live_epoch = _live_history_epoch(live_db)
        _checkpoint_live_wal(live_db)

        try:
            if staged.exists():
                staged.unlink()
            remove_sqlite_sidecars(staged)

            shutil.copy2(backup_db, staged)
            # TOCTOU: the bytes that will actually be installed must match the
            # manifest, not merely the source bytes inspected earlier.
            staged_digest = hash_file_sha256(staged)
            if staged_digest != str(manifest["sha256"]):
                raise BackupProvenanceError(
                    "staged restore copy does not match the verified manifest "
                    f"sha256: {staged_digest} != {manifest['sha256']}"
                )
            validate_canonical_sqlite(staged)

            writer = CanonicalWriter(staged)
            try:
                epoch = writer.advance_history_epoch_for_restore(
                    minimum_epoch=previous_live_epoch
                )
                writer.checkpoint_wal()
            finally:
                writer.close()
            # The writer forces WAL mode. Normalize the staged artifact back to
            # self-contained DELETE mode so what gets installed never depends on
            # a WAL sidecar; the next live writer re-enables WAL on open.
            normalize_to_rollback_journal(staged)
            assert_rollback_journal_backup(staged)
            _assert_no_staged_sidecars(staged)
            validate_canonical_sqlite(staged)
            # Required durability before cutover: the bytes about to be installed
            # must be on durable storage, not just in page cache.
            fsync_file_required(staged)

            # Cutover. Safe only because the live WAL was checkpointed above.
            remove_sqlite_sidecars(live_db)
            os.replace(str(staged), str(live_db))
            # Staged path no longer holds the database; drop its orphaned staging
            # artifacts (including the ephemeral staging writer lock).
            _cleanup_staging_artifacts(staged)
            # Required parent-directory durability so the rename itself survives.
            fsync_directory_required(live_db.parent)
        except Exception:
            with contextlib.suppress(Exception):
                _cleanup_staging_artifacts(staged)
            raise

        # Prove the installed snapshot is the verified one at the expected epoch.
        assert_rollback_journal_backup(live_db)
        installed_epoch = _live_history_epoch(live_db)
        if installed_epoch != epoch:
            raise CanonicalDbValidationError(
                "restored store epoch does not match the epoch written to the "
                f"staged snapshot: {installed_epoch!r} != {epoch!r}"
            )
        validate_canonical_sqlite(live_db)
    finally:
        store_lock.release()

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "restored_path": str(live_db),
        "history_epoch": epoch,
        "previous_live_epoch": previous_live_epoch,
        "source_release_sha": str(manifest["source_release_sha"]),
        "backup_sha256": str(manifest["sha256"]),
        "elapsed_ms": elapsed_ms,
        "rto_budget_ms": 30 * 60 * 1000,
    }


def run_backup_restore_drill(work_dir: Path, *, seed_events: int = 3) -> dict[str, Any]:
    """End-to-end backup + restore drill used by PR 2 acceptance tests."""
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION

    drill_release_sha = "808a308cd274d30b55fef47b382c229b761e07df"
    live = work_dir / "live" / "opip_canonical_v1.sqlite3"
    backup = work_dir / "backup" / "opip_canonical_v1.backup.sqlite3"
    manifest_path = work_dir / "backup" / "manifest.json"
    restored = work_dir / "restored" / "opip_canonical_v1.sqlite3"

    writer = CanonicalWriter(live)
    try:
        for idx in range(seed_events):
            intent = WriterIntent(
                schema_version=SCHEMA_VERSION,
                priority="NORMAL",
                idempotency_key=f"drill:seed:{idx}",
                event_type="alert_governor.transition.recorded",
                payload={
                    "identity": f"EARLY_MOVER:DRILL{idx}",
                    "transition_key": f"READY:drill:{idx}",
                    "message_id": 1000 + idx,
                    "created_new": True,
                    "scan_id": "drill-scan",
                },
                ops_handoff={
                    "operation": "RECORD",
                    "identity": f"EARLY_MOVER:DRILL{idx}",
                    "transition_key": f"READY:drill:{idx}",
                    "message_id": 1000 + idx,
                    "created_new": True,
                    "reservation_token": f"tok{idx}",
                    "state_family": "early_watch",
                },
            )
            ack = writer.submit(intent)
            assert ack.status == "OK", ack
        writer.checkpoint_wal()
    finally:
        writer.close()

    backup_started = time.perf_counter()
    backup_database(live, backup)
    backup_ms = (time.perf_counter() - backup_started) * 1000.0
    manifest = build_backup_manifest(
        backup_path=backup,
        source_release_sha=drill_release_sha,
    )
    write_backup_manifest(manifest, manifest_path)

    restore_result = restore_from_backup(
        backup_db=backup,
        live_db=restored,
        manifest_path=manifest_path,
        expected_source_release_sha=drill_release_sha,
        advance_epoch=True,
    )
    return {
        "backup_elapsed_ms": backup_ms,
        "restore": restore_result,
        "manifest": manifest,
        "rpo_target_seconds": 300,
        "rto_target_seconds": 1800,
        "backup_within_rpo": backup_ms < 300_000,
        "restore_within_rto": restore_result["elapsed_ms"] < 1_800_000,
    }
