"""R4: native restore epoch/sequence semantics and the previous failure.

The certified contract is explicit: a live restore advances ``history_epoch`` and
resets ``next_local_sequence`` to 1, while every existing event coordinate is
preserved. The prior V2 rehearsal asserted that the sequence was unchanged, which
contradicts the contract; the restore itself is not defective. These tests pin
the contract, reproduce the old assertion's failure, and cover repeated, failed,
incomplete and conflicting recoveries.

The real restore requires POSIX durability primitives, so those cases run on
Linux only and fail closed elsewhere by design.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

import opip_recovery
import opip_rollback_guard

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
LINUX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="native restore requires POSIX durability"
)


def _seed(db: Path, events: int = 4, *, next_sequence: int | None = None) -> None:
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION
    from app.opip.canonical.writer import CanonicalWriter

    db.parent.mkdir(parents=True, exist_ok=True)
    writer = CanonicalWriter(db)
    try:
        for index in range(events):
            writer.submit(
                WriterIntent(
                    schema_version=SCHEMA_VERSION,
                    priority="NORMAL",
                    idempotency_key=f"r4:seed:{index}",
                    event_type="alert_governor.transition.recorded",
                    payload={
                        "identity": f"EARLY_MOVER:R4-{index}",
                        "transition_key": f"READY:r4:{index}",
                        "message_id": 4000 + index,
                        "created_new": True,
                        "scan_id": "r4-scan",
                    },
                    ops_handoff={
                        "operation": "RECORD",
                        "identity": f"EARLY_MOVER:R4-{index}",
                        "transition_key": f"READY:r4:{index}",
                        "message_id": 4000 + index,
                        "created_new": True,
                        "reservation_token": f"r4tok{index}",
                        "state_family": "early_watch",
                    },
                )
            )
        writer.checkpoint_wal()
    finally:
        writer.close()
    if next_sequence is not None:
        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE meta SET next_local_sequence = ? WHERE id = 1", (next_sequence,))
            conn.commit()
        finally:
            conn.close()


def _facts(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        epoch, sequence = conn.execute(
            "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
        ).fetchone()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        conn.close()
    return {
        "history_epoch": int(epoch),
        "next_local_sequence": int(sequence),
        "event_count": int(events),
        "integrity_check": integrity,
        "foreign_key_violations": fk,
    }


def test_history_epoch_advance_resets_sequence_and_preserves_events(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    db = tmp_path / "live" / "opip_canonical_v1.sqlite3"
    _seed(db, events=4)
    before = _facts(db)
    assert before["history_epoch"] == 1
    assert before["next_local_sequence"] == 5
    assert before["event_count"] == 4

    writer = CanonicalWriter(db)
    try:
        epoch = writer.advance_history_epoch_for_restore()
        writer.checkpoint_wal()
    finally:
        writer.close()

    after = _facts(db)
    assert epoch == 2
    assert after["history_epoch"] == 2
    assert after["next_local_sequence"] == 1, "restore resets the local sequence"
    assert after["event_count"] == 4, "existing event coordinates are preserved"
    assert after["integrity_check"] == "ok"
    assert after["foreign_key_violations"] == 0

    writer = CanonicalWriter(db)
    try:
        epoch = writer.advance_history_epoch_for_restore(minimum_epoch=5)
    finally:
        writer.close()
    assert epoch == 6, "the new epoch always dominates the replaced live epoch"
    assert _facts(db)["next_local_sequence"] == 1


def _published(tmp_path: Path, *, next_sequence: int | None = None) -> tuple[Path, Path, dict]:
    """Publish one certified-shape backup generation from a synthetic store."""
    from app.opip.canonical.backup import publish_backup_generation

    source = tmp_path / "src" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    _seed(source, events=4, next_sequence=next_sequence)
    backups = source.parent / "backups"
    publish_backup_generation(
        source, backups, source_release_sha=CERTIFIED_SHA, generation_id="R4TEST"
    )
    manifest_path = next(backups.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return backups / manifest["backup_file"], manifest_path, manifest


def _bundle(tmp_path: Path, backups: Path) -> Path:
    import shutil

    bundle = tmp_path / "bundle"
    destination = bundle / "payload" / "data" / "opip" / "canonical" / "backups"
    shutil.copytree(backups, destination)
    (bundle / "BUNDLE-MANIFEST.json").write_text(
        json.dumps({"canonical": {"synthetic": True}}), encoding="utf-8"
    )
    return bundle


@LINUX_ONLY
def test_native_restore_advances_epoch_resets_sequence_and_preserves_events(tmp_path):
    from conftest import CERT_SRC

    backup_db, _manifest_path, manifest = _published(tmp_path)
    live = tmp_path / "live" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    receipt = tmp_path / "receipts" / "canonical-recovery-receipt.json"
    result = opip_recovery.restore_canonical(
        bundle_dir=_bundle(tmp_path, backup_db.parent),
        app_root=CERT_SRC,
        live_db=live,
        authorize=True,
        receipt_out=receipt,
    )
    assert result["verdict"] == "PASS", result
    assert result["canonical_recovered"] is True
    restored = _facts(live)
    assert restored["history_epoch"] == int(manifest["history_epoch"]) + 1
    assert restored["next_local_sequence"] == 1
    assert restored["event_count"] == int(manifest["event_count"])
    assert restored["integrity_check"] == "ok"
    assert restored["foreign_key_violations"] == 0

    record = json.loads(receipt.read_text(encoding="utf-8"))
    assert record["completed"] is True and record["verdict"] == "PASS"
    gate = opip_rollback_guard.writer_gate(receipt_path=receipt, db=live)
    assert gate["verdict"] == "PASS", gate


@LINUX_ONLY
def test_the_previous_rehearsal_assertion_contradicted_the_contract(tmp_path):
    """The prior `Next sequence changed` assertion assumed preservation.

    Evidence limitation: the ad-hoc PHASE4B script was not preserved, so the
    exact assertion text is reconstructed from its recorded failure line. The
    certified contract (epoch+1, sequence 1, events preserved) is proven here;
    the old expectation is reproduced and shown to fail.
    """
    from conftest import CERT_SRC

    backup_db, _manifest_path, manifest = _published(tmp_path, next_sequence=42)
    live = tmp_path / "live" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    result = opip_recovery.restore_canonical(
        bundle_dir=_bundle(tmp_path, backup_db.parent),
        app_root=CERT_SRC,
        live_db=live,
        authorize=True,
        receipt_out=tmp_path / "receipt.json",
    )
    assert result["verdict"] == "PASS", result
    restored = _facts(live)
    assert manifest["next_local_sequence"] == 42
    assert restored["next_local_sequence"] == 1
    with pytest.raises(AssertionError):
        assert restored["next_local_sequence"] == manifest["next_local_sequence"]


@LINUX_ONLY
def test_repeated_recovery_advances_the_epoch_each_time(tmp_path):
    from app.opip.canonical.recovery import restore_from_backup

    backup_db, manifest_path, _manifest = _published(tmp_path)
    live = tmp_path / "live" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    first = restore_from_backup(
        backup_db=backup_db, live_db=live, manifest_path=manifest_path,
        expected_source_release_sha=CERTIFIED_SHA, advance_epoch=True,
    )
    second = restore_from_backup(
        backup_db=backup_db, live_db=live, manifest_path=manifest_path,
        expected_source_release_sha=CERTIFIED_SHA, advance_epoch=True,
    )
    assert first["history_epoch"] == 2
    assert second["history_epoch"] == 3
    facts = _facts(live)
    assert facts["history_epoch"] == 3 and facts["next_local_sequence"] == 1
    assert facts["event_count"] == 4


@LINUX_ONLY
def test_failed_recovery_leaves_the_live_store_untouched(tmp_path):
    import hashlib

    from app.opip.canonical.backup import BackupProvenanceError
    from app.opip.canonical.recovery import restore_from_backup

    backup_db, manifest_path, _manifest = _published(tmp_path)
    live = tmp_path / "live" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    restore_from_backup(
        backup_db=backup_db, live_db=live, manifest_path=manifest_path,
        expected_source_release_sha=CERTIFIED_SHA, advance_epoch=True,
    )
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    backup_db.write_bytes(backup_db.read_bytes() + b"corrupt")
    with pytest.raises(BackupProvenanceError):
        restore_from_backup(
            backup_db=backup_db, live_db=live, manifest_path=manifest_path,
            expected_source_release_sha=CERTIFIED_SHA, advance_epoch=True,
        )
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before
    assert _facts(live)["event_count"] == 4


@LINUX_ONLY
def test_incomplete_and_sidecar_generations_are_refused(tmp_path):
    from app.opip.canonical.backup import BackupProvenanceError
    from app.opip.canonical.recovery import restore_from_backup
    from conftest import CERT_SRC

    backup_db, manifest_path, _manifest = _published(tmp_path)
    live = tmp_path / "live" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "BUNDLE-MANIFEST.json").write_text(
        json.dumps({"canonical": {}}), encoding="utf-8"
    )
    (incomplete / "payload").mkdir()
    result = opip_recovery.restore_canonical(
        bundle_dir=incomplete, app_root=CERT_SRC, live_db=live,
        authorize=True, receipt_out=tmp_path / "incomplete.json",
    )
    assert result["verdict"] == "FAIL"
    assert not live.exists()

    sidecar = Path(f"{backup_db}-wal")
    sidecar.write_bytes(b"unexpected")
    try:
        with pytest.raises(BackupProvenanceError):
            restore_from_backup(
                backup_db=backup_db, live_db=live, manifest_path=manifest_path,
                expected_source_release_sha=CERTIFIED_SHA, advance_epoch=True,
            )
    finally:
        sidecar.unlink()
    assert not live.exists()


@LINUX_ONLY
def test_equal_epoch_destination_data_is_refused(tmp_path):
    import hashlib

    from conftest import CERT_SRC

    backup_db, _manifest_path, manifest = _published(tmp_path)
    live = tmp_path / "live" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    _seed(live, events=2)
    assert _facts(live)["history_epoch"] == int(manifest["history_epoch"])
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    result = opip_recovery.restore_canonical(
        bundle_dir=_bundle(tmp_path, backup_db.parent), app_root=CERT_SRC,
        live_db=live, authorize=True, receipt_out=tmp_path / "refused.json",
    )
    assert result["verdict"] == "REFUSED"
    guard = next(c for c in result["checks"] if c["id"] == "NEWER_HISTORY_GUARD")
    assert guard["status"] == "FAIL"
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before


