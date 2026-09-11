"""O'Pip canonical writer package (PR 2).

One long-lived writer process owns the operational SQLite WAL database.
Producers talk over a local Unix domain socket. Default mode is off.
"""

from __future__ import annotations

from app.opip.canonical.paths import (
    CANONICAL_DIR,
    DB_PATH,
    GAP_SPOOL_PATH,
    SOCKET_PATH,
    canonical_dir,
    db_path,
    gap_spool_path,
    socket_path,
)

__all__ = [
    "CANONICAL_DIR",
    "DB_PATH",
    "GAP_SPOOL_PATH",
    "SOCKET_PATH",
    "canonical_dir",
    "db_path",
    "gap_spool_path",
    "socket_path",
]
