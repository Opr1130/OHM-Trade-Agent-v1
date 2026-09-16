"""SQLite DDL for the PR 2 canonical store."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from app.opip.canonical.paths import SCHEMA_VERSION

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL,
    history_epoch INTEGER NOT NULL,
    next_local_sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    history_epoch INTEGER NOT NULL,
    local_sequence INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    event_time TEXT,
    causation_id TEXT,
    correlation_id TEXT,
    idempotency_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (history_epoch, local_sequence),
    UNIQUE (idempotency_key)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    committed_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS alert_identity_projection (
    state_family TEXT NOT NULL,
    identity TEXT NOT NULL,
    transition_key TEXT NOT NULL,
    last_action TEXT NOT NULL,
    message_id INTEGER,
    last_event_id TEXT NOT NULL,
    last_history_epoch INTEGER NOT NULL,
    last_local_sequence INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (state_family, identity)
);

CREATE TABLE IF NOT EXISTS watermarks (
    stream TEXT PRIMARY KEY,
    history_epoch INTEGER NOT NULL,
    local_sequence INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_ops_handoffs (
    event_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    identity TEXT NOT NULL,
    transition_key TEXT NOT NULL,
    message_id INTEGER,
    created_new INTEGER NOT NULL,
    reservation_token TEXT,
    state_file TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_handoffs_pending
    ON alert_ops_handoffs(status)
    WHERE status = 'PENDING';
"""


class SchemaVersionError(RuntimeError):
    """Canonical DB schema_version does not match this code build."""


class CanonicalDbValidationError(RuntimeError):
    """Staged/canonical SQLite file failed open/schema/integrity validation."""


class CanonicalStoreBusyError(RuntimeError):
    """The canonical store is already owned exclusively by another writer/restore."""


class CanonicalCheckpointError(RuntimeError):
    """A WAL checkpoint could not be proven complete and durable."""


#: Sibling file holding the OS advisory lock that proves store ownership.
CANONICAL_STORE_LOCK_SUFFIX = ".writer.lock"


def store_lock_path(db_path: Path) -> Path:
    """Sibling lock-file path used to prove exclusive canonical-store ownership."""
    target = Path(db_path)
    return target.with_name(f"{target.name}{CANONICAL_STORE_LOCK_SUFFIX}")


def sqlite_sidecar_paths(db_path: Path) -> tuple[Path, ...]:
    """Existing ``-wal``/``-shm`` companions of ``db_path`` (possibly empty)."""
    target = Path(db_path)
    return tuple(
        side
        for side in (Path(f"{target}-wal"), Path(f"{target}-shm"))
        if side.exists()
    )


def _lock_handle_exclusive(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CanonicalStoreLock:
    """Process-lifetime exclusive ownership of one canonical SQLite store.

    Ownership is an OS advisory lock on a sibling lock file, held until explicit
    release or process death. Because ownership is the live OS lock rather than
    the file's existence, a lock file left behind by a dead process never reads
    as busy. Advisory locks conflict between separate file descriptions, so a
    second owner inside the same process is refused as well.

    Acquisition fails closed: there is no wait-and-retry, because restoring or
    writing against a store whose owner is unknown is never safe.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.lock_path = store_lock_path(self.db_path)
        self._handle: Any | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            _lock_handle_exclusive(handle)
        except OSError as exc:
            try:
                handle.close()
            except OSError:
                pass
            raise CanonicalStoreBusyError(
                "canonical store is already owned exclusively by another "
                f"writer or restore: {self.db_path}"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            _unlock_handle(handle)
        except OSError:
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def __enter__(self) -> "CanonicalStoreLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> bool:
        self.release()
        return False


def checkpoint_wal_strict(connection: sqlite3.Connection) -> None:
    """Checkpoint the WAL and prove every frame reached the database file.

    ``PRAGMA wal_checkpoint`` returns ``(busy, log_frames, checkpointed_frames)``.
    Successful SQL execution is not proof of a completed checkpoint, so a busy,
    incomplete, unreadable, or errored result all fail closed. Callers may
    therefore treat a return from this helper as evidence that the database file
    alone is a complete snapshot.
    """
    try:
        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as exc:
        raise CanonicalCheckpointError(f"wal_checkpoint failed: {exc}") from exc
    if row is None:
        raise CanonicalCheckpointError("wal_checkpoint returned no status row")
    values = tuple(row)
    if len(values) < 3:
        raise CanonicalCheckpointError(
            f"wal_checkpoint returned an unusable status row: {values!r}"
        )
    try:
        busy = int(values[0])
        log_frames = int(values[1])
        checkpointed_frames = int(values[2])
    except (TypeError, ValueError) as exc:
        raise CanonicalCheckpointError(
            f"wal_checkpoint status is ambiguous: {values!r}"
        ) from exc
    if busy != 0:
        raise CanonicalCheckpointError(
            "wal_checkpoint reported busy; WAL contents are not safely in the "
            "database file"
        )
    if log_frames != checkpointed_frames:
        raise CanonicalCheckpointError(
            "wal_checkpoint did not complete: "
            f"{checkpointed_frames}/{log_frames} frames checkpointed"
        )


def connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    else:
        # Writer service uses a dedicated worker thread plus short-lived
        # control RPCs; serialize via CanonicalWriter._lock.
        connection = sqlite3.connect(
            str(db_path),
            timeout=5.0,
            check_same_thread=False,
        )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if not read_only:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
    return connection


def initialize_schema(connection: sqlite3.Connection, *, now_iso: str) -> None:
    connection.executescript(DDL)
    row = connection.execute(
        "SELECT id, schema_version FROM meta WHERE id = 1"
    ).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO meta (
                id, schema_version, history_epoch, next_local_sequence,
                created_at, updated_at
            ) VALUES (1, ?, 1, 1, ?, ?)
            """,
            (SCHEMA_VERSION, now_iso, now_iso),
        )
        connection.commit()
        return
    existing = int(row["schema_version"])
    if existing != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"canonical schema_version={existing} incompatible with "
            f"code SCHEMA_VERSION={SCHEMA_VERSION}"
        )
    connection.commit()


def validate_canonical_sqlite(db_path: Path) -> None:
    """Fail-closed open + schema + integrity validation for a SQLite file."""
    target = Path(db_path)
    if not target.is_file():
        raise CanonicalDbValidationError(f"database missing: {target}")
    try:
        conn = connect(target, read_only=True)
    except sqlite3.Error as exc:
        raise CanonicalDbValidationError(f"cannot open database: {exc}") from exc
    try:
        try:
            row = conn.execute(
                "SELECT schema_version FROM meta WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise CanonicalDbValidationError(f"meta unreadable: {exc}") from exc
        if row is None:
            raise CanonicalDbValidationError("meta row missing")
        existing = int(row["schema_version"])
        if existing != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"canonical schema_version={existing} incompatible with "
                f"code SCHEMA_VERSION={SCHEMA_VERSION}"
            )
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            raise CanonicalDbValidationError(f"integrity_check failed: {exc}") from exc
        if check is None or str(check[0]).lower() != "ok":
            raise CanonicalDbValidationError(f"integrity_check={check!r}")
    finally:
        conn.close()


def fsync_path(path: Path) -> None:
    """Best-effort fsync of a file for durability before replace."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            # Windows and some filesystems reject fsync on read-only fds.
            return
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def fsync_directory(path: Path) -> None:
    """Best-effort directory fsync (may be unsupported on some platforms)."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            return
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def remove_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
