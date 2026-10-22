import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
LEGACY_SHA = "8b3cc2712432ca21007be4db0667301d48b89d97"
V2_SHA = "a26d664972075b2a220a466215cf71e389decb86"
LEGACY_GEN = "gen-b1f2c171c5c64601"
V2_GEN = "gen-bd1613ddd16a449b"


def _run(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(APP_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "app.services.learning_derived_state", *args],
        cwd=APP_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _data(tmp_path: Path, *, v2_inputs: bool) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    source = V2_SHA if v2_inputs else LEGACY_SHA
    (data / "manifest.env").write_text(
        f"production_deployed_sha={source}\n", encoding="utf-8"
    )
    (data / "p1_evidence_ledger.jsonl").write_bytes(b"" if v2_inputs else b"legacy\n")
    (data / "opip/qualification").mkdir(parents=True)
    (data / "opip/qualification/screening_evaluations.jsonl").write_bytes(
        b"current-screening\n" if v2_inputs else b"legacy-screening\n"
    )
    db = data / ".p1_evidence_ledger.jsonl.dedup.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO metadata VALUES('indexed_offset', '133851714')")
    connection.execute("CREATE TABLE snapshot_ids(snapshot_id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO snapshot_ids VALUES('legacy-snapshot')")
    connection.commit()
    connection.close()
    (data / "phase3c_forward_outcomes.jsonl").write_bytes(b"legacy-outcome\n")
    return data


def _retire(data: Path, archive: Path, *, input_lineage: str, input_sha: str):
    return _run(
        "retire",
        "--data-root",
        str(data),
        "--archive-root",
        str(archive),
        "--lineage-id",
        "LINEAGE-A-LEGACY",
        "--source-sha",
        LEGACY_SHA,
        "--generation-id",
        LEGACY_GEN,
        "--active-lineage",
        "LINEAGE-B-V2-GENESIS",
        "--input-lineage",
        input_lineage,
        "--input-source-sha",
        input_sha,
        "--input-manifest",
        str(data / "manifest.env"),
        "--reason",
        "PARTIALLY_COMMITTED_RECOVERY",
    )


@pytest.mark.acceptance
def test_fresh_lineage_transition_cli_archives_verified_outgoing_inputs(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: real fresh-transition CLI."""
    data = _data(tmp_path, v2_inputs=False)
    archive = tmp_path / "archive"
    result = _retire(
        data, archive, input_lineage="LINEAGE-A-LEGACY", input_sha=LEGACY_SHA
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "VERIFIED"
    assert (data / "p1_evidence_ledger.jsonl").read_bytes() == b"legacy\n"
    assert not (data / ".p1_evidence_ledger.jsonl.dedup.sqlite3").exists()
    receipt = json.loads((archive / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["derived_lineage_id"] == "LINEAGE-A-LEGACY"
    assert receipt["input_lineage_id"] == "LINEAGE-A-LEGACY"
    archived = archive / "payload/.p1_evidence_ledger.jsonl.dedup.sqlite3"
    connection = sqlite3.connect(f"file:{archived}?mode=ro", uri=True)
    try:
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='indexed_offset'"
        ).fetchone()[0] == "133851714"
        assert connection.execute("SELECT count(*) FROM snapshot_ids").fetchone()[0] == 1
    finally:
        connection.close()
    assert _run("verify", "--archive-root", str(archive)).returncode == 0

@pytest.mark.acceptance
def test_partially_committed_resume_cli_preserves_v2_inputs(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: real partial-resume CLI."""
    data = _data(tmp_path, v2_inputs=True)
    archive = tmp_path / "archive"
    result = _retire(
        data, archive, input_lineage="LINEAGE-B-V2-GENESIS", input_sha=V2_SHA
    )
    assert result.returncode == 0, result.stderr
    assert (data / "p1_evidence_ledger.jsonl").read_bytes() == b""
    assert (data / "opip/qualification/screening_evaluations.jsonl").read_bytes() == (
        b"current-screening\n"
    )
    receipt = json.loads((archive / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["derived_lineage_id"] == "LINEAGE-A-LEGACY"
    assert receipt["input_lineage_id"] == "LINEAGE-B-V2-GENESIS"
    assert receipt["input_source_release_sha"] == V2_SHA
    manifest = json.loads(
        (archive / "archive_manifest.json").read_text(encoding="utf-8")
    )
    for artifact in manifest["artifacts"]:
        if artifact["category"] == "input":
            assert artifact["artifact_lineage_id"] == "LINEAGE-B-V2-GENESIS"
        else:
            assert artifact["artifact_lineage_id"] == "LINEAGE-A-LEGACY"
    marked = _run(
        "mark-lineage",
        "--data-root",
        str(data),
        "--lineage-id",
        "LINEAGE-B-V2-GENESIS",
        "--source-sha",
        V2_SHA,
        "--generation-id",
        V2_GEN,
    )
    assert marked.returncode == 0, marked.stderr
    marker = json.loads((data / ".lineage.json").read_text(encoding="utf-8"))
    assert marker["source_release_sha"] == V2_SHA
    assert marker["lineage_generation_id"] == V2_GEN


@pytest.mark.acceptance
def test_cli_refuses_unauthenticated_or_missing_input_provenance(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: no mutation on refusal."""
    data = _data(tmp_path, v2_inputs=True)
    archive = tmp_path / "archive"
    mismatched = _retire(
        data, archive, input_lineage="LINEAGE-A-LEGACY", input_sha=LEGACY_SHA
    )
    assert mismatched.returncode != 0
    assert not archive.exists()
    assert (data / ".p1_evidence_ledger.jsonl.dedup.sqlite3").exists()
    missing = _run(
        "retire",
        "--data-root",
        str(data),
        "--archive-root",
        str(tmp_path / "other"),
        "--lineage-id",
        "LINEAGE-A-LEGACY",
        "--source-sha",
        LEGACY_SHA,
        "--generation-id",
        LEGACY_GEN,
        "--active-lineage",
        "LINEAGE-B-V2-GENESIS",
    )
    assert missing.returncode != 0
    assert "INPUT_PROVENANCE_REQUIRED" in missing.stderr
    assert not (tmp_path / "other").exists()
