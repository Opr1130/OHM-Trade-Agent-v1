"""HF-06 regression: provenance must bind identity, cutoff, manifest, archive and datasets."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import opip_bundle as bundle
import opip_provenance as provenance

CERTIFIED_SHA = bundle.CERTIFIED_SHA
V1 = {"droplet_id": "589794133", "ip": "161.35.106.207"}


def _evidence(**overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    evidence = {**V1, "hostname": "opip-v1", "sampled_at_utc": now}
    evidence.update(overrides)
    return evidence


def _rewrite(bundle_dir: Path, mutate) -> dict:
    path = bundle_dir / bundle.MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest["dataset_digest"] = bundle.dataset_digest(manifest)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (bundle_dir / bundle.MANIFEST_DIGEST_NAME).write_text(
        bundle.sha256_file(path) + "\n", encoding="utf-8")
    return manifest


def _final_like(bundle_dir: Path) -> None:
    def mutate(manifest: dict) -> None:
        manifest["source"]["identity_proof"] = "host-evidence"
        manifest["canonical"]["synthetic"] = False
        manifest["consistency_cutoff_utc"] = datetime.now(timezone.utc).isoformat()

    _rewrite(bundle_dir, mutate)


def test_export_refuses_default_identity(tmp_path, source_tree, spec):
    with pytest.raises(SystemExit, match="trusted host evidence"):
        bundle.export(source_root=source_tree, spec=spec, out_dir=tmp_path, bundle_name="x",
                      canonical_dir=None, cutoff_utc="2026-10-19T00:00:00Z",
                      source_identity={**V1, "identity_proof": "UNPROVEN"},
                      allow_synthetic_canonical=True)


def test_export_refuses_fixture_identity_without_rehearsal_flag(tmp_path, source_tree, spec):
    with pytest.raises(SystemExit, match="trusted host evidence"):
        bundle.export(source_root=source_tree, spec=spec, out_dir=tmp_path, bundle_name="x",
                      canonical_dir=None, cutoff_utc="2026-10-19T00:00:00Z",
                      source_identity={**V1, "identity_proof": "fixture"},
                      allow_synthetic_canonical=False)


def test_fixture_bundle_is_not_accepted_as_final(exported_bundle):
    result = provenance.verify_provenance(bundle_dir=exported_bundle, evidence=_evidence())
    assert result["verdict"] == "FAIL"
    assert "PROOF_SOURCE_TRUSTED" in {c["id"] for c in result["checks"] if c["status"] == "FAIL"}


def test_synthetic_generation_blocks_final_acceptance(exported_bundle):
    result = provenance.verify_provenance(bundle_dir=exported_bundle, evidence=_evidence(), allow_fixture=True)
    assert result["verdict"] == "FAIL"
    assert "CANONICAL_NOT_SYNTHETIC" in {c["id"] for c in result["checks"] if c["status"] == "FAIL"}


def test_final_like_manifest_passes(exported_bundle, tmp_path):
    import shutil

    copy = tmp_path / "final-like"
    shutil.copytree(exported_bundle, copy)
    _final_like(copy)
    result = provenance.verify_provenance(bundle_dir=copy, evidence=_evidence())
    assert result["verdict"] == "PASS", [c for c in result["checks"] if c["status"] == "FAIL"]
def test_stale_cutoff_is_rejected(exported_bundle, tmp_path):
    import shutil

    copy = tmp_path / "stale"
    shutil.copytree(exported_bundle, copy)
    _final_like(copy)

    def mutate(manifest: dict) -> None:
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        manifest["consistency_cutoff_utc"] = old

    _rewrite(copy, mutate)
    result = provenance.verify_provenance(bundle_dir=copy, evidence=_evidence(), max_age_hours=24)
    assert result["verdict"] == "FAIL"
    assert "CUTOFF_FRESH" in {c["id"] for c in result["checks"] if c["status"] == "FAIL"}


def test_identity_mismatch_is_rejected(exported_bundle, tmp_path):
    import shutil

    copy = tmp_path / "mismatch"
    shutil.copytree(exported_bundle, copy)
    _final_like(copy)
    result = provenance.verify_provenance(
        bundle_dir=copy, evidence=_evidence(droplet_id="589794133", ip="10.0.0.9"))
    assert result["verdict"] == "FAIL"
    assert "MANIFEST_IDENTITY_MATCHES_EVIDENCE" in {c["id"] for c in result["checks"] if c["status"] == "FAIL"}


def test_wrong_droplet_is_rejected(exported_bundle, tmp_path):
    import shutil

    copy = tmp_path / "wrong-droplet"
    shutil.copytree(exported_bundle, copy)
    _final_like(copy)
    result = provenance.verify_provenance(
        bundle_dir=copy, evidence=_evidence(droplet_id="610307364", ip="178.128.147.125"))
    assert result["verdict"] == "FAIL"
    assert "IDENTITY_DROPLET" in {c["id"] for c in result["checks"] if c["status"] == "FAIL"}


def test_tampered_archive_digest_is_rejected(exported_bundle, tmp_path):
    import shutil

    copy = tmp_path / "tampered"
    shutil.copytree(exported_bundle, copy)
    _final_like(copy)
    sidecar = copy / bundle.ARCHIVE_DIGEST_NAME
    declared = json.loads(sidecar.read_text(encoding="utf-8"))
    declared["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(declared), encoding="utf-8")
    result = provenance.verify_provenance(bundle_dir=copy, evidence=_evidence())
    assert result["verdict"] == "FAIL"
    assert "ARCHIVE_DIGEST" in {c["id"] for c in result["checks"] if c["status"] == "FAIL"}


def test_incomplete_dataset_entries_are_rejected(exported_bundle, tmp_path):
    import shutil

    copy = tmp_path / "incomplete"
    shutil.copytree(exported_bundle, copy)
    _final_like(copy)

    def mutate(manifest: dict) -> None:
        manifest["totals"]["files"] = int(manifest["totals"]["files"]) + 1

    _rewrite(copy, mutate)
    result = provenance.verify_provenance(bundle_dir=copy, evidence=_evidence())
    assert result["verdict"] == "FAIL"
    assert "DATASET_ENTRIES_COMPLETE" in {c["id"] for c in result["checks"] if c["status"] == "FAIL"}


def test_cli_exit_codes(exported_bundle, tmp_path):
    import subprocess
    import sys

    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_evidence()), encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "opip_provenance.py"
    fixture_result = subprocess.run(
        [sys.executable, str(script), "--bundle", str(exported_bundle), "--evidence", str(evidence)],
        capture_output=True, text=True)
    assert fixture_result.returncode == 1, fixture_result.stdout[-300:]
    assert "PROOF_SOURCE_TRUSTED" in fixture_result.stdout

