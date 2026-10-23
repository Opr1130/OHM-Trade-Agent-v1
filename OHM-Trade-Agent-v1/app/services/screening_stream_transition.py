"""Owner-gated transition away from an unprovable legacy hot-file checkpoint.

The 2026-10-22 checkpoint (offset 61,049,583, anchor not found anywhere in the
retained stream) cannot be mapped to an absolute coordinate. This module never
attempts that mapping. It archives the legacy sidecar, records the unverifiable
interval as an explicit evidence gap, and only then establishes a new framed
checkpoint at a verified stream boundary.

It is idempotent: a second invocation finds the receipt and the checkpoint and
returns without writing anything. It never edits historical observations.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.opip.discovery.stream_ingest import (
    CHECKPOINT_SCHEMA,
    CONSUMER_NAME,
    load_checkpoint,
)
from app.services.fs_durability import fsync_dir, fsync_file

RECEIPT_SCHEMA = "opip_screening_legacy_coordinate_transition/v1"
GAP_SCHEMA = "opip_evidence_gap/v1"
GAP_REASON = "LEGACY_COORDINATE_UNPROVEN"
AUTHORIZE_TOKEN = "LEGACY_COORDINATE_UNPROVEN"


class TransitionRefused(RuntimeError):
    """The transition is not authorized or cannot be proven safe."""


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        fsync_file(path)
        fsync_dir(path.parent)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _snapshot(source: Path, target: Path) -> None:
    """Consistent SQLite copy (backup API), never a raw file copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def transition_legacy_checkpoint(
    *,
    state_db: Path,
    lineage_id: str,
    stream_id: str,
    verified_frame_sha256: str,
    absolute_offset: int,
    archive_root: Path,
    authorize: str,
    source_release_sha: str = "",
    now: datetime | None = None,
    consumer_name: str = CONSUMER_NAME,
) -> dict[str, Any]:
    """Perform (or observe) the authorized transition. Idempotent by receipt."""
    if authorize != AUTHORIZE_TOKEN:
        raise TransitionRefused(
            "transition requires the explicit LEGACY_COORDINATE_UNPROVEN "
            "owner acknowledgement"
        )
    if absolute_offset < 0:
        raise TransitionRefused("absolute_offset must be non-negative")
    for value, name in (
        (stream_id, "stream_id"),
        (verified_frame_sha256, "verified_frame_sha256"),
    ):
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise TransitionRefused(f"{name} must be a sha256 hex digest")

    stamp = _utc(now or datetime.now(timezone.utc))
    transaction = f"{stamp.replace(':', '').replace('-', '')}-{stream_id[:12]}"
    gap_dir = archive_root / "legacy-coordinate-gaps"
    receipt_path = gap_dir / f"{transaction}.json"
    if receipt_path.exists():
        return json.loads(receipt_path.read_text(encoding="utf-8"))

    connection = sqlite3.connect(str(state_db))
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(CHECKPOINT_SCHEMA)
        existing = load_checkpoint(
            connection, lineage_id=lineage_id, consumer_name=consumer_name
        )
        if existing is not None:
            return {
                "schema": RECEIPT_SCHEMA,
                "status": "ALREADY_TRANSITIONED",
                "evidence_continuity": "VERIFIED_FROM_CUTOVER_FORWARD_ONLY",
                "lossless_historical_recovery": False,
                "stream_id": existing["stream_id"],
                "absolute_offset": existing["absolute_offset"],
            }
        rows = connection.execute(
            "SELECT key, value FROM metadata WHERE key LIKE 'screening_%'"
        ).fetchall()
        legacy_meta = {str(row["key"]): str(row["value"]) for row in rows}
        if legacy_meta.get("screening_indexed_offset") and absolute_offset == 0:
            raise TransitionRefused("cutover position must be a verified boundary")

        snapshot_path = gap_dir / f"{transaction}.legacy-state.sqlite3"
        _snapshot(state_db, snapshot_path)
        snapshot_sha = _sha_file(snapshot_path)

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO consumer_checkpoint(
                consumer_name, lineage_id, stream_id, absolute_offset,
                anchor_sha256, anchor_bytes, verified_frame_sha256,
                source_release_sha, updated_at_utc
            ) VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?)
            """,
            (
                consumer_name,
                lineage_id,
                stream_id,
                absolute_offset,
                verified_frame_sha256,
                source_release_sha,
                stamp,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "TRANSITIONED",
        "reason": GAP_REASON,
        "evidence_continuity": "VERIFIED_FROM_CUTOVER_FORWARD_ONLY",
        "lossless_historical_recovery": False,
        "historical_evidence_modified": False,
        "consumer_name": consumer_name,
        "lineage_id": lineage_id,
        "stream_id": stream_id,
        "verified_frame_sha256": verified_frame_sha256,
        "absolute_offset": absolute_offset,
        "legacy_checkpoint": legacy_meta,
        "legacy_state_snapshot": str(snapshot_path),
        "legacy_state_snapshot_sha256": snapshot_sha,
        "source_release_sha": source_release_sha,
        "recorded_at_utc": stamp,
        "note": (
            "The legacy hot-file offset was not recoverable from retained "
            "evidence. This records an explicit gap; it is not a lossless "
            "reconstruction of the missing interval."
        ),
    }
    _write_exclusive(receipt_path, receipt)
    gap = {
        "schema": GAP_SCHEMA,
        "gap_id": transaction,
        "reason": GAP_REASON,
        "interval": {"from": None, "to": stamp},
        "uncertainty": "EXACT_BOUNDARY_UNKNOWN",
        "dataset_eligible": False,
        "receipt": str(receipt_path),
        "recorded_at_utc": stamp,
    }
    _write_exclusive(gap_dir / f"{transaction}.gap.json", gap)
    return receipt
