"""Connections to the derived analytical projection.

Two connection shapes exist and must stay separate:

* a **reader** opened ``mode=ro`` with ``query_only`` set, used by every
  dashboard and analytical read path, and
* a **writer** used only by ingestion, reconciliation, retention and
  configuration staging.

A reader that could write would let a GET request mutate evidence. A writer that
was shared with the read path would let a long dashboard query hold the write
lock. Both are prevented here rather than by convention.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

DEFAULT_BUSY_TIMEOUT_MS = 5_000


class AnalyticsStoreError(RuntimeError):
    """The projection could not be opened in the requested shape."""


def _is_memory(path: Path | str) -> bool:
    text = str(path)
    return text in {":memory:", ""} or text.startswith("file::memory:")


def connect_reader(
    path: Path | str,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Open a strictly read-only connection.

    ``mode=ro`` is refused for a database that does not exist yet: that is a
    deployment fact the caller must report, not an implicit empty dataset.
    """
    if _is_memory(path):
        conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        return conn
    target = Path(path)
    if not target.is_file():
        raise AnalyticsStoreError("ANALYTICS_DB_UNAVAILABLE")
    conn = sqlite3.connect(
        f"file:{target.as_posix()}?mode=ro",
        uri=True,
        isolation_level=None,
        check_same_thread=False,
        timeout=max(0.1, busy_timeout_ms / 1000.0),
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA query_only = ON")
    return conn


def connect_writer(
    path: Path | str,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Open the single writable connection shape used by maintenance jobs."""
    if _is_memory(path):
        conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    else:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(target),
            isolation_level=None,
            check_same_thread=False,
            timeout=max(0.1, busy_timeout_ms / 1000.0),
        )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA foreign_keys = ON")
    if not _is_memory(path):
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One bounded immediate transaction, rolled back on any failure.

    ``BEGIN IMMEDIATE`` takes the write lock up front so two concurrent
    ingestions cannot both believe they advanced the same checkpoint.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def database_size_bytes(path: Path | str) -> tuple[int | None, int | None]:
    """Return ``(main_bytes, wal_bytes)``; ``None`` means not measurable."""
    if _is_memory(path):
        return None, None
    target = Path(path)
    main = None
    wal = None
    try:
        if target.is_file():
            main = target.stat().st_size
    except OSError:
        main = None
    try:
        sidecar = target.with_name(target.name + "-wal")
        if sidecar.is_file():
            wal = sidecar.stat().st_size
    except OSError:
        wal = None
    return main, wal
