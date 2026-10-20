"""R1 migration scenarios: large datasets, archive-only verification, interruption,
manifest corruption, canonical recovery gating and newer-history protection.

Maps to mandated scenarios 5, 6, 7, 8 and 9.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import opip_bundle as bundle
from opip_recovery import restore_canonical

R1 = Path(__file__).resolve().parents[2]


def test_large_dataset_is_fully_exported_and_staged(exported_bundle, tmp_path):
    """Scenario 6: >2000 files in one dataset must survive export, verify and stage."""
    manifest = json.loads((exported_bundle / bundle.MANIFEST_NAME).read_text(encoding="utf-8"))
    events = next(d for d in manifest["datasets"] if d["id"] == "event_intelligence")
    assert events["status"] == "EXPORTED"
    assert events["file_count"] >= 2600, events["file_count"]
    assert len(events["files"]) == events["file_count"], "every file must be listed"

    report = bundle.verify_bundle(bundle_dir=exported_bundle)
    assert report["verdict"] == "PASS", [c for c in report["checks"] if c["status"] == "FAIL"]
    assert next(c for c in report["checks"] if c["id"] == "FILE_LIST_COMPLETE")["status"] == "PASS"

    staging = tmp_path / "stage"
    receipt = bundle.stage_bundle(exported_bundle, staging)
    assert receipt["copied"] == manifest["totals"]["files"]
    assert len(list((staging / "data" / "opip" / "events" / "segments").glob("*.jsonl"))) == 2600


def test_archive_alone_is_verifiable_after_transfer(exported_bundle, tmp_path):
    """The transferred archive must carry manifest + digest (R0 omitted them)."""
    archive = next(exported_bundle.glob("*.tar"))
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    shutil.copy2(archive, transfer / archive.name)
    shutil.copy2(exported_bundle / bundle.ARCHIVE_DIGEST_NAME, transfer / bundle.ARCHIVE_DIGEST_NAME)

    report = bundle.verify_bundle(archive=transfer / archive.name, workdir=tmp_path / "extracted")
    assert report["verdict"] == "PASS", [c for c in report["checks"] if c["status"] == "FAIL"]
    ids = {c["id"] for c in report["checks"]}
    assert {"ARCHIVE_DIGEST", "MANIFEST_SELF_HASH", "FILE_LIST_COMPLETE", "PAYLOAD_INTEGRITY"} <= ids


def test_corrupted_manifest_is_detected(exported_bundle, tmp_path):
    """Scenario 8: a tampered manifest must fail closed."""
    copy = tmp_path / "corrupt-manifest"
    shutil.copytree(exported_bundle, copy)
    manifest_path = copy / bundle.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["datasets"][2]["files"][0]["size"] = 1
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = bundle.verify_bundle(bundle_dir=copy)
    assert report["verdict"] == "FAIL"
    failed = {c["id"] for c in report["checks"] if c["status"] == "FAIL"}
    assert {"MANIFEST_SELF_HASH", "MANIFEST_CONTENT_DIGEST"} <= failed


def test_truncated_payload_is_detected(exported_bundle, tmp_path):
    copy = tmp_path / "truncated"
    shutil.copytree(exported_bundle, copy)
    (copy / "payload" / "data" / "trade_outcomes.json").write_bytes(b"{}")
    report = bundle.verify_bundle(bundle_dir=copy)
    assert report["verdict"] == "FAIL"
    assert "PAYLOAD_INTEGRITY" in {c["id"] for c in report["checks"] if c["status"] == "FAIL"}
def test_interrupted_export_resumes_without_loss(exported_bundle, source_tree, canonical_dir, spec):
    """Scenario 7: deleting payload files then re-exporting restores every file."""
    payload = exported_bundle / "payload"
    victims = list((payload / "data" / "opip" / "events" / "segments").glob("*.jsonl"))[:5]
    for victim in victims:
        victim.unlink()

    result = bundle.export(
        source_root=source_tree, spec=spec, out_dir=exported_bundle.parent,
        bundle_name=exported_bundle.name, canonical_dir=canonical_dir,
        cutoff_utc="2026-10-19T06:00:00Z",
        source_identity={"droplet_id": "589794133", "ip": "161.35.106.207", "identity_proof": "FIXTURE"},
        allow_synthetic_canonical=True,
    )
    for victim in victims:
        assert (payload / "data" / "opip" / "events" / "segments" / victim.name).is_file()
    assert bundle.verify_bundle(bundle_dir=result.bundle_dir)["verdict"] == "PASS"


def test_interrupted_import_resumes_idempotently(exported_bundle, tmp_path):
    staging = tmp_path / "stage"
    first = bundle.stage_bundle(exported_bundle, staging)
    assert first["copied"] > 2600
    second = bundle.stage_bundle(exported_bundle, staging)
    assert second["copied"] == 0
    assert second["skipped_identical"] == first["copied"]


def test_promotion_requires_recorded_canonical_recovery(exported_bundle, tmp_path):
    """Scenario 5: file copy must never be reported as database recovery.

    HF-02 tightened this: a caller flag is not evidence, so promotion refuses
    unless a persisted, re-verified recovery receipt exists (see
    tests/scenarios/test_hf02_recovery_gate.py).
    """
    staging = tmp_path / "stage"
    bundle.stage_bundle(exported_bundle, staging)
    target = tmp_path / "live"
    target.mkdir()

    with pytest.raises(SystemExit) as refused:
        bundle.promote(staging, target, canonical_recovered=True, authorize=True)
    assert "receipt" in str(refused.value)
    assert list(target.rglob("*")) == []


def test_existing_newer_canonical_history_is_refused(exported_bundle, tmp_path):
    """Scenario 9: never overwrite newer V2 history."""
    import sqlite3

    live = tmp_path / "live" / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    live.parent.mkdir(parents=True)
    generation = next((exported_bundle / "payload").glob("**/canonical/backups/*.sqlite3"))
    shutil.copy2(generation, live)
    conn = sqlite3.connect(live)
    try:
        conn.execute("UPDATE meta SET history_epoch = 9, next_local_sequence = 42 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    result = restore_canonical(bundle_dir=exported_bundle, app_root=R1, live_db=live, dry_run=True)
    assert result["verdict"] == "REFUSED"
    assert next(c for c in result["checks"] if c["id"] == "NEWER_HISTORY_GUARD")["status"] == "FAIL"


@pytest.mark.skipif(sys.platform != "win32", reason="durability refusal is Windows-specific")
def test_canonical_restore_reports_platform_block(exported_bundle, tmp_path):
    """A file copy exists, but recovery is NOT claimed where durability is unprovable."""
    live = tmp_path / "fresh" / "opip_canonical_v1.sqlite3"
    live.parent.mkdir(parents=True)
    result = restore_canonical(bundle_dir=exported_bundle, app_root=R1, live_db=live, authorize=True)
    assert result["verdict"] == "PLATFORM_BLOCKED"
    assert result["canonical_recovered"] is False
    assert result["file_copy"] is False


def test_cli_exit_codes(exported_bundle, source_tree):
    script = R1 / "scripts" / "opip_bundle.py"
    dry = subprocess.run([sys.executable, str(script), "export", "--source-root", str(source_tree),
                          "--spec", str(R1 / "drafts" / "v1-dataset-spec.json"),
                          "--out-dir", str(exported_bundle.parent), "--bundle-name", "dry-run"],
                         capture_output=True, text=True)
    assert dry.returncode == 77, dry.stdout[-300:]
    ok = subprocess.run([sys.executable, str(script), "verify", "--bundle", str(exported_bundle)],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout[-300:]

