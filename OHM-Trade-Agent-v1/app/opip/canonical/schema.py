"""SQLite DDL for the PR 2 canonical store."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

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
