"""Restore drills and recovery helpers for the canonical store."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from app.opip.canonical.backup import backup_database, build_backup_manifest, write_backup_manifest
from app.opip.canonical.writer import CanonicalWriter


def restore_from_backup(
    *,
    backup_db: Path,
    live_db: Path,
    advance_epoch: bool = True,
) -> dict[str, Any]:
    """
    Restore an older snapshot into the live path.

    When the snapshot is older than current history, callers must advance
    history_epoch before accepting new writes (default: advance).
    """
    started = time.perf_counter()
    live_db.parent.mkdir(parents=True, exist_ok=True)
    if live_db.exists():
        live_db.unlink()
    for suffix in ("-wal", "-shm"):
        side = Path(str(live_db) + suffix)
        if side.exists():
            side.unlink()
    shutil.copy2(backup_db, live_db)
    epoch = None
    if advance_epoch:
        writer = CanonicalWriter(live_db)
        try:
            epoch = writer.advance_history_epoch_for_restore()
        finally:
            writer.close()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "restored_path": str(live_db),
        "history_epoch": epoch,
        "elapsed_ms": elapsed_ms,
        "rto_budget_ms": 30 * 60 * 1000,
    }


def run_backup_restore_drill(work_dir: Path, *, seed_events: int = 3) -> dict[str, Any]:
    """End-to-end backup + restore drill used by PR 2 acceptance tests."""
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION

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
                    "state_file": "/app/data/alert_governor_state.json",
                },
            )
            ack = writer.submit(intent)
            assert ack.status == "OK", ack
    finally:
        writer.close()

    backup_started = time.perf_counter()
    backup_database(live, backup)
    backup_ms = (time.perf_counter() - backup_started) * 1000.0
    manifest = build_backup_manifest(backup_path=backup, source_db=live)
    write_backup_manifest(manifest, manifest_path)

    restore_result = restore_from_backup(
        backup_db=backup,
        live_db=restored,
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
