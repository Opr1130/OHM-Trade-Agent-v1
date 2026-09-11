"""SQLite online backup + off-host manifest helpers."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.opip.canonical.schema import (
    connect,
    fsync_directory,
    fsync_path,
    remove_sqlite_sidecars,
    validate_canonical_sqlite,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def backup_database(source_db: Path, dest_db: Path) -> Path:
    """
    Consistent snapshot via the SQLite backup API (not VACUUM INTO / file copy).

    Never deletes the last known-good final backup until a staged snapshot has
    been written, closed, validated, and atomically published.
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

        validate_canonical_sqlite(staged)
        fsync_path(staged)
        os.replace(str(staged), str(dest_db))
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
    """
    digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
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

    sha = (
        str(source_release_sha).strip()
        if source_release_sha
        else str(os.environ.get("OPIP_SOURCE_RELEASE_SHA") or "").strip()
    )
    if not sha:
        sha = "UNVERIFIED"

    return {
        "schema_version": 1,
        "created_at": _utc_now(),
        "backup_file": backup_path.name,
        "sha256": digest,
        "source_release_sha": sha,
        "history_epoch": history_epoch,
        "next_local_sequence": int(meta["next_local_sequence"]),
        "max_local_sequence": int(max_seq["m"]),
        "event_count": int(count_row["n"]),
        "rpo_target_seconds": 300,
        "rto_target_seconds": 1800,
    }


def write_backup_manifest(manifest: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
