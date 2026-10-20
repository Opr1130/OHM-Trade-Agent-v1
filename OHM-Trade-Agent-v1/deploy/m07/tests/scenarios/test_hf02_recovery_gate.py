"""HF-02 regression: promotion requires a persisted, re-verified native-recovery receipt."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

import opip_bundle as bundle
from opip_recovery import restore_canonical

CERTIFIED_SHA = bundle.CERTIFIED_SHA


def _staged(exported_bundle: Path, tmp_path: Path) -> Path:
    staging = tmp_path / "stage"
    if not staging.exists():
        bundle.stage_bundle(exported_bundle, staging)
    return staging


def _db_facts(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        epoch, seq = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1").fetchone()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    return {"history_epoch": int(epoch), "next_local_sequence": int(seq), "event_count": int(events)}


def _live_target(staging: Path, tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "live"
    live_db = target / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    live_db.parent.mkdir(parents=True, exist_ok=True)
    generation = next(staging.glob("data/opip/canonical/backups/*.sqlite3"))
    live_db.write_bytes(generation.read_bytes())
    return target, live_db


def _receipt(staging: Path, live_db: Path, path: Path, **overrides) -> Path:
    generation = next(staging.glob("data/opip/canonical/backups/*.sqlite3"))
    record = {
        "receipt_schema": 1,
        "operation": "canonical-native-restore",
        "completed": True,
        "verdict": "PASS",
        "restored_at_utc": "2026-10-19T07:00:00+00:00",
        "host": "v2",
        "bundle_dir": str(staging),
        "bundle_manifest_sha256": "",
        "generation_sha256": hashlib.sha256(generation.read_bytes()).hexdigest(),
        "destination_db": str(live_db.resolve()),
        "source_release_sha": CERTIFIED_SHA,
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "restored": {**_db_facts(live_db), "integrity_check": "ok"},
    }
    record.update(overrides)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def test_caller_flag_cannot_substitute_for_receipt(exported_bundle, tmp_path):
    """MANDATORY: staging says canonical_recovered=false; caller says true -> refuse, write nothing."""
    staging = _staged(exported_bundle, tmp_path)
    staged = json.loads((staging / "staging-receipt.json").read_text(encoding="utf-8"))
    assert staged["canonical_recovered"] is False
    target = tmp_path / "live-empty"
    target.mkdir()

    with pytest.raises(SystemExit) as refused:
        bundle.promote(staging, target, canonical_recovered=True, authorize=True)
    assert "receipt" in str(refused.value)
    assert list(target.rglob("*")) == [], "nothing may be written to the target"


def test_missing_receipt_is_refused(exported_bundle, tmp_path):
    staging = _staged(exported_bundle, tmp_path)
    target = tmp_path / "live"
    target.mkdir()
    with pytest.raises(SystemExit, match="receipt"):
        bundle.promote(staging, target, recovery_receipt=tmp_path / "absent.json", authorize=True)
    assert list(target.rglob("*")) == []


def test_forged_receipt_for_another_destination_is_refused(exported_bundle, tmp_path):
    staging = _staged(exported_bundle, tmp_path)
    target, live_db = _live_target(staging, tmp_path)
    receipt = _receipt(staging, live_db, tmp_path / "receipt.json",
                       destination_db=str((tmp_path / "somewhere-else.sqlite3").resolve()))
    with pytest.raises(SystemExit, match="RECEIPT_DESTINATION"):
        bundle.promote(staging, target, recovery_receipt=receipt, authorize=True)


def test_stale_receipt_is_refused(exported_bundle, tmp_path):
    staging = _staged(exported_bundle, tmp_path)
    target, live_db = _live_target(staging, tmp_path)
    staged = json.loads((staging / "staging-receipt.json").read_text(encoding="utf-8"))
    receipt = _receipt(staging, live_db, tmp_path / "receipt.json",
                       bundle_manifest_sha256=staged["bundle_manifest_sha256"])
    record = json.loads(receipt.read_text(encoding="utf-8"))
    record["restored"]["history_epoch"] += 7
    receipt.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit, match="EPOCH_MATCHES_RECEIPT"):
        bundle.promote(staging, target, recovery_receipt=receipt, authorize=True)
def test_incomplete_receipt_is_refused(exported_bundle, tmp_path):
    staging = _staged(exported_bundle, tmp_path)
    target, live_db = _live_target(staging, tmp_path)
    receipt = _receipt(staging, live_db, tmp_path / "receipt.json", completed=False)
    with pytest.raises(SystemExit, match="RECEIPT_COMPLETED"):
        bundle.promote(staging, target, recovery_receipt=receipt, authorize=True)


def test_valid_receipt_allows_promotion(exported_bundle, tmp_path):
    staging = _staged(exported_bundle, tmp_path)
    target, live_db = _live_target(staging, tmp_path)
    staged = json.loads((staging / "staging-receipt.json").read_text(encoding="utf-8"))
    receipt = _receipt(staging, live_db, tmp_path / "receipt.json",
                       bundle_manifest_sha256=staged["bundle_manifest_sha256"])

    result = bundle.promote(staging, target, recovery_receipt=receipt, authorize=True)
    assert result["verdict"] == "PASS"
    assert result["recovery_gate"]["verdict"] == "PASS"
    assert result["canonical_recovered"] is True
    assert (target / "data" / "trade_outcomes.json").is_file()


def test_receipt_bound_to_a_different_bundle_is_refused(exported_bundle, tmp_path):
    staging = _staged(exported_bundle, tmp_path)
    target, live_db = _live_target(staging, tmp_path)
    receipt = _receipt(staging, live_db, tmp_path / "receipt.json", bundle_manifest_sha256="f" * 64)
    with pytest.raises(SystemExit, match="RECEIPT_BUNDLE_IDENTITY"):
        bundle.promote(staging, target, recovery_receipt=receipt, authorize=True)


def test_promotion_requires_authorization(exported_bundle, tmp_path):
    staging = _staged(exported_bundle, tmp_path)
    target = tmp_path / "live"
    target.mkdir()
    with pytest.raises(SystemExit, match="Authorize"):
        bundle.promote(staging, target)
    assert list(target.rglob("*")) == []


def test_restore_requires_authorization(exported_bundle, tmp_path):
    live = tmp_path / "db" / "opip_canonical_v1.sqlite3"
    live.parent.mkdir(parents=True)
    result = restore_canonical(bundle_dir=exported_bundle, app_root=tmp_path, live_db=live)
    assert result["verdict"] == "REFUSED"
    assert next(c for c in result["checks"] if c["id"] == "AUTHORIZATION")["status"] == "FAIL"
    assert not live.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="durability refusal is Windows-specific")
def test_failed_restore_overwrites_a_stale_pass_receipt(exported_bundle, tmp_path):
    live = tmp_path / "db" / "opip_canonical_v1.sqlite3"
    live.parent.mkdir(parents=True)
    receipt = tmp_path / "recovery-receipt.json"
    receipt.write_text(json.dumps({"receipt_schema": 1, "completed": True, "verdict": "PASS"}),
                       encoding="utf-8")

    result = restore_canonical(bundle_dir=exported_bundle, app_root=tmp_path, live_db=live,
                               authorize=True, receipt_out=receipt)
    assert result["verdict"] == "PLATFORM_BLOCKED"
    written = json.loads(receipt.read_text(encoding="utf-8"))
    assert written["completed"] is False, "a failed run must not leave a stale PASS receipt"
    assert written["verdict"] == "PLATFORM_BLOCKED"


def test_cli_promote_refuses_without_receipt(exported_bundle, tmp_path):
    import subprocess

    staging = _staged(exported_bundle, tmp_path)
    target = tmp_path / "live-cli"
    target.mkdir()
    script = Path(__file__).resolve().parents[2] / "scripts" / "opip_bundle.py"
    completed = subprocess.run(
        [sys.executable, str(script), "promote", "--staging", str(staging),
         "--target", str(target), "--canonical-recovered", "--authorize"],
        capture_output=True, text=True)
    assert completed.returncode == 3, completed.stdout[-300:]
    assert "receipt" in completed.stdout

