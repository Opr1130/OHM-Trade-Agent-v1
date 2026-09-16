"""SQLite online backup + off-host manifest helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.opip.canonical.schema import (
    checkpoint_wal_strict,
    connect,
    fsync_directory,
    fsync_path,
    remove_sqlite_sidecars,
    sqlite_sidecar_paths,
    validate_canonical_sqlite,
)

#: Manifest layout this build can verify. Bump only with a matching verifier.
MANIFEST_SCHEMA_VERSION = 1

_RELEASE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Fields describing the SQLite snapshot itself, verified against the file.
_SNAPSHOT_FACT_FIELDS = (
    "history_epoch",
    "next_local_sequence",
    "max_local_sequence",
    "event_count",
)

#: SQLite file header facts (offsets 18/19 of a 100-byte header).
SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"
_SQLITE_FORMAT_WRITE_OFFSET = 18
_SQLITE_FORMAT_READ_OFFSET = 19
SQLITE_LEGACY_JOURNAL_FORMAT = 1
SQLITE_WAL_FORMAT = 2

#: The journal mode a published canonical recovery artifact must report.
BACKUP_JOURNAL_MODE = "delete"

_HASH_CHUNK_BYTES = 1024 * 1024


class BackupProvenanceError(RuntimeError):
    """Backup provenance could not be verified, so recovery is not authorized."""


class BackupFormatError(BackupProvenanceError):
    """A backup artifact is not a self-contained rollback-journal database.

    A non-conforming artifact can never be authorized for recovery, so this is a
    specialization of provenance refusal with its own diagnosable message.
    """


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hash_file_sha256(path: Path) -> str:
    """SHA-256 of a file, streamed so a backup is never fully buffered."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_regular_file(path: Path, *, field_name: str, error_type: type[Exception]):
    """Reject missing, directory, or otherwise non-regular recovery inputs."""
    target = Path(path)
    if not target.exists():
        raise error_type(f"{field_name} does not exist: {target}")
    if not target.is_file():
        raise error_type(f"{field_name} is not a regular file: {target}")
    return target


def read_sqlite_journal_format(path: Path) -> tuple[int, int]:
    """Return SQLite's ``(format write version, format read version)`` bytes.

    Reads the fixed 100-byte file header directly and never opens SQLite, so
    inspecting a candidate backup cannot itself create ``-wal``/``-shm``
    sidecars. This is the non-mutating preflight that lets recovery decide
    whether a backup is a self-contained rollback-journal artifact *before* any
    connection is made.
    """
    target = assert_regular_file(
        path, field_name="backup database", error_type=BackupFormatError
    )
    try:
        with target.open("rb") as handle:
            header = handle.read(20)
    except OSError as exc:
        raise BackupFormatError(f"cannot read SQLite header of {target}: {exc}") from exc
    if len(header) < 20 or not header.startswith(SQLITE_HEADER_MAGIC):
        raise BackupFormatError(
            f"not a SQLite database file (missing header magic): {target}"
        )
    return (
        header[_SQLITE_FORMAT_WRITE_OFFSET],
        header[_SQLITE_FORMAT_READ_OFFSET],
    )


def assert_rollback_journal_backup(path: Path) -> None:
    """Require a backup to be in self-contained rollback-journal format.

    Absence of sidecars is not sufficient: a database can still be marked WAL
    format while no sidecar happens to exist at that instant, and it would then
    depend on a WAL the moment it is opened. The artifact itself must be
    rollback-journal format.
    """
    write_format, read_format = read_sqlite_journal_format(path)
    if write_format != SQLITE_LEGACY_JOURNAL_FORMAT or (
        read_format != SQLITE_LEGACY_JOURNAL_FORMAT
    ):
        raise BackupFormatError(
            "backup is not a self-contained rollback-journal artifact "
            f"(file format write/read versions = {write_format}/{read_format}, "
            f"expected {SQLITE_LEGACY_JOURNAL_FORMAT}/"
            f"{SQLITE_LEGACY_JOURNAL_FORMAT}); a WAL-format backup cannot be "
            "restored because its completeness would depend on a sidecar"
        )


def assert_sidecar_free(path: Path, *, field_name: str, error_type: type[Exception]):
    """Reject a recovery input that arrives with adjacent WAL/SHM files.

    A compliant canonical backup is a single file. Sidecars are never guessed
    to be empty, stale, or harmless, and are never deleted to make a
    non-conforming artifact look acceptable.
    """
    sidecars = sqlite_sidecar_paths(path)
    if sidecars:
        raise error_type(
            f"{field_name} is not self-contained; adjacent SQLite sidecar files "
            f"are present: {[str(side) for side in sidecars]}"
        )
    return Path(path)


def require_release_sha(value: Any, *, field_name: str) -> str:
    """Return a normalized full 40-character hexadecimal git SHA, or fail closed.

    Release provenance is verified locally and deterministically; no network or
    GitHub lookup is ever performed during recovery.
    """
    sha = str(value or "").strip().lower()
    if not _RELEASE_SHA_PATTERN.fullmatch(sha):
        raise BackupProvenanceError(
            f"{field_name} must be a full 40-character hexadecimal git SHA, "
            f"got {value!r}"
        )
    return sha


def _snapshot_facts(backup_path: Path) -> dict[str, int]:
    """Read the snapshot-describing facts from the backup file alone."""
    conn = connect(backup_path, read_only=True)
    try:
        meta = conn.execute(
            "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
        ).fetchone()
        if meta is None:
            raise RuntimeError("backup meta missing")
        history_epoch = int(meta["history_epoch"])
        count_row = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        max_seq = conn.execute(
            """
            SELECT COALESCE(MAX(local_sequence), 0) AS m
            FROM events
            WHERE history_epoch = ?
            """,
            (history_epoch,),
        ).fetchone()
    finally:
        conn.close()
    return {
        "history_epoch": history_epoch,
        "next_local_sequence": int(meta["next_local_sequence"]),
        "max_local_sequence": int(max_seq["m"]) if max_seq is not None else 0,
        "event_count": int(count_row["n"]) if count_row is not None else 0,
    }


def normalize_to_rollback_journal(path: Path) -> None:
    """Checkpoint a SQLite file and switch it to self-contained ``DELETE`` mode.

    The online backup API can leave part of a snapshot in a WAL, and opening a
    canonical file read-write forces WAL mode. Both leave a file whose
    completeness depends on a sidecar, so callers that must produce (or install)
    an independently recoverable artifact normalize it here.

    Normalization is proven, not assumed — SQLite must report ``delete``, and the
    file must be free of non-empty sidecars afterwards.
    """
    connection = connect(path, read_only=False)
    try:
        checkpoint_wal_strict(connection)
        connection.commit()
        row = connection.execute(
            f"PRAGMA journal_mode={BACKUP_JOURNAL_MODE.upper()}"
        ).fetchone()
        if row is None:
            raise BackupFormatError(
                "journal_mode normalization returned no result for "
                f"{path}; refusing to treat it as self-contained"
            )
        reported = str(row[0]).lower()
        if reported != BACKUP_JOURNAL_MODE:
            raise BackupFormatError(
                "database did not reach the required journal mode: "
                f"reported {reported!r}, required {BACKUP_JOURNAL_MODE!r} ({path})"
            )
        connection.commit()
    finally:
        connection.close()

    # A clean close in DELETE mode removes the sidecars. Anything left must be
    # empty to be ignorable; non-empty leftovers mean the file is not
    # self-contained.
    for side in sqlite_sidecar_paths(path):
        try:
            size = side.stat().st_size
        except OSError as exc:
            raise BackupFormatError(
                f"cannot inspect SQLite sidecar {side}: {exc}"
            ) from exc
        if size:
            raise BackupFormatError(
                f"database is not self-contained after normalization: {side} "
                f"still holds {size} bytes"
            )
        try:
            side.unlink()
        except OSError as exc:
            raise BackupFormatError(
                f"cannot remove empty SQLite sidecar {side}: {exc}"
            ) from exc


def _finalize_backup_snapshot(staged: Path) -> None:
    """Make a staged backup a genuinely self-contained rollback-journal artifact."""
    normalize_to_rollback_journal(staged)


def backup_database(source_db: Path, dest_db: Path) -> Path:
    """
    Consistent snapshot via the SQLite backup API (not VACUUM INTO / file copy).

    Never deletes the last known-good final backup until a staged snapshot has
    been written, closed, finalized into a self-contained file, validated, and
    atomically published.
    """
    dest_db = Path(dest_db)
    source_db = Path(source_db)
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    staged = dest_db.with_name(
        f".{dest_db.name}.staging.{os.getpid()}.{uuid.uuid4().hex}.sqlite3"
    )
    try:
        if staged.exists():
            staged.unlink()
        remove_sqlite_sidecars(staged)

        source = connect(source_db, read_only=True)
        try:
            dest = sqlite3.connect(str(staged))
            try:
                source.backup(dest)
                dest.commit()
            finally:
                dest.close()
        finally:
            source.close()

        _finalize_backup_snapshot(staged)
        assert_rollback_journal_backup(staged)
        validate_canonical_sqlite(staged)
        fsync_path(staged)
        os.replace(str(staged), str(dest_db))
        # Only after a successful cutover: any sidecar left beside the
        # destination belonged to the artifact just replaced and can never pair
        # with the new (provably sidecar-free) main file again. Removing it
        # before the replace would risk corrupting the previous backup if the
        # replace then failed.
        remove_sqlite_sidecars(dest_db)
        fsync_directory(dest_db.parent)
    except Exception:
        if staged.exists():
            try:
                staged.unlink()
            except OSError:
                pass
        remove_sqlite_sidecars(staged)
        raise
    return dest_db


def build_backup_manifest(
    *,
    backup_path: Path,
    source_release_sha: str | None = None,
) -> dict[str, Any]:
    """
    Derive manifest metadata from the immutable backup file only.

    Never read the live source DB here — post-snapshot writes must not alter
    the signed snapshot description.

    A manifest authorizes restore, so it must carry real provenance and must
    describe a conforming artifact: a missing or malformed release SHA fails
    closed instead of emitting ``UNVERIFIED``, and a WAL-format, sidecar-bearing,
    or non-canonical backup is refused rather than given a valid-looking
    manifest.
    """
    backup_path = Path(backup_path)
    assert_regular_file(
        backup_path, field_name="backup database", error_type=BackupFormatError
    )
    assert_sidecar_free(
        backup_path, field_name="backup database", error_type=BackupFormatError
    )
    assert_rollback_journal_backup(backup_path)
    validate_canonical_sqlite(backup_path)

    facts = _snapshot_facts(backup_path)
    # Computed over the final, normalized artifact: normalization has already
    # completed and the file bytes are stable at this point.
    digest = hash_file_sha256(backup_path)

    candidates = source_release_sha
    if candidates is None or not str(candidates).strip():
        candidates = os.environ.get("OPIP_SOURCE_RELEASE_SHA")
    sha = require_release_sha(candidates, field_name="source_release_sha")

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "backup_file": backup_path.name,
        "sha256": digest,
        "source_release_sha": sha,
        "history_epoch": facts["history_epoch"],
        "next_local_sequence": facts["next_local_sequence"],
        "max_local_sequence": facts["max_local_sequence"],
        "event_count": facts["event_count"],
        "rpo_target_seconds": 300,
        "rto_target_seconds": 1800,
    }


def read_backup_manifest(path: Path) -> dict[str, Any]:
    """Load a backup manifest, failing closed on missing or malformed content."""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise BackupProvenanceError(
            f"backup manifest is missing or unreadable: {target}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackupProvenanceError(
            f"backup manifest is not valid JSON: {target}"
        ) from exc
    if not isinstance(payload, dict):
        raise BackupProvenanceError(
            f"backup manifest must be a JSON object: {target}"
        )
    return payload


def verify_backup_manifest(
    manifest: Mapping[str, Any],
    *,
    backup_path: Path,
    expected_source_release_sha: str,
) -> dict[str, Any]:
    """Prove the manifest describes exactly this backup, or fail closed.

    Every value describing the SQLite snapshot is re-derived from the backup
    file and must agree, so a truncated, substituted, or hand-edited backup
    cannot be restored on the strength of its filename alone.
    """
    backup_path = Path(backup_path)
    assert_regular_file(
        backup_path, field_name="backup database", error_type=BackupProvenanceError
    )
    # Provenance is only meaningful for a conforming artifact, and this must be
    # established before any SQLite connection is opened.
    assert_sidecar_free(
        backup_path, field_name="backup database", error_type=BackupProvenanceError
    )
    assert_rollback_journal_backup(backup_path)

    version = manifest.get("schema_version")
    if type(version) is not int or version != MANIFEST_SCHEMA_VERSION:
        raise BackupProvenanceError(
            "unsupported backup manifest schema_version: "
            f"{version!r} (supported {MANIFEST_SCHEMA_VERSION})"
        )

    expected_sha = require_release_sha(
        expected_source_release_sha, field_name="expected_source_release_sha"
    )
    manifest_sha = require_release_sha(
        manifest.get("source_release_sha"), field_name="manifest source_release_sha"
    )
    if manifest_sha != expected_sha:
        raise BackupProvenanceError(
            "backup source_release_sha does not match the expected release: "
            f"{manifest_sha} != {expected_sha}"
        )

    recorded_name = str(manifest.get("backup_file") or "")
    if recorded_name != backup_path.name:
        raise BackupProvenanceError(
            "backup manifest was written for a different file: "
            f"{recorded_name!r} != {backup_path.name!r}"
        )

    recorded_digest = str(manifest.get("sha256") or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(recorded_digest):
        raise BackupProvenanceError(
            f"backup manifest sha256 is malformed: {manifest.get('sha256')!r}"
        )
    actual_digest = hash_file_sha256(backup_path)
    if recorded_digest != actual_digest:
        raise BackupProvenanceError(
            "backup content does not match the manifest sha256: "
            f"{actual_digest} != {recorded_digest}"
        )

    facts = _snapshot_facts(backup_path)
    for field_name in _SNAPSHOT_FACT_FIELDS:
        recorded = manifest.get(field_name)
        if type(recorded) is not int:
            raise BackupProvenanceError(
                f"backup manifest {field_name} must be an integer, got {recorded!r}"
            )
        if recorded != facts[field_name]:
            raise BackupProvenanceError(
                f"backup manifest {field_name} does not match the backup: "
                f"{recorded} != {facts[field_name]}"
            )
    return dict(manifest)


def write_backup_manifest(manifest: dict[str, Any], path: Path) -> Path:
    """Publish a manifest atomically so a crash cannot destroy the previous one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        fsync_directory(path.parent)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return path
