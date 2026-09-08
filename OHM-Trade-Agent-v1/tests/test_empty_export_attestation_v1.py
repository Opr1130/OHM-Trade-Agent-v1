from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.opip.decision.store import screening_evaluations_archive
from app.opip.learning.empty_export_attestation import (
    production_empty_export_attestation_eligible,
    write_empty_export_attestation,
)


NOW = datetime(2026, 9, 6, 6, 0, tzinfo=timezone.utc)
_PRODUCTION_SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = ROOT / "deploy" / "remote" / "export-opip-learning-evidence.sh"


def _archive(tmp_path):
    return screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )


def _write_state(archive, payload: dict) -> None:
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    archive.window_index_state_file.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _incomplete_zero_coverage_state(*, complete: bool) -> dict:
    return {
        "schema_version": 1,
        "manifest_present": False,
        "manifest_size": 0,
        "manifest_mtime_ns": 0,
        "manifest_sha256": "",
        "complete": complete,
        "coverage_start_day": None,
        "coverage_through_day": None,
        "coverage_day_count": 0,
        "shard_sha256": {},
        "updated_at_utc": NOW.isoformat(),
    }


def _write_attestation(archive):
    return write_empty_export_attestation(
        archive,
        exported_at_utc=NOW.isoformat(),
        production_deployed_sha=_PRODUCTION_SHA,
    )


def test_clean_empty_source_allows_production_empty_attestation(tmp_path):
    archive = _archive(tmp_path)
    assert production_empty_export_attestation_eligible(archive) is True
    path = _write_attestation(archive)
    assert path.is_file()
    assert archive.ensure_window_index_locked() is True


def test_certified_empty_source_allows_production_empty_attestation(tmp_path):
    archive = _archive(tmp_path)
    assert archive.ensure_window_index_locked() is True
    state = json.loads(archive.window_index_state_file.read_text(encoding="utf-8"))
    assert state["complete"] is True
    assert production_empty_export_attestation_eligible(archive) is True
    assert _write_attestation(archive).is_file()


def test_orphan_gzip_history_blocks_production_empty_attestation(tmp_path):
    archive = _archive(tmp_path)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    gzip_path = archive.archive_dir / "screening_evaluations-orphan.jsonl.gz"
    gzip_path.write_bytes(b"orphan")
    assert archive.ensure_window_index_locked() is False
    state = json.loads(archive.window_index_state_file.read_text(encoding="utf-8"))
    assert state["complete"] is False
    gzip_path.unlink()
    assert not archive.manifest_file.exists()
    assert not archive.manifest_signature_file.exists()
    assert production_empty_export_attestation_eligible(archive) is False
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_attestation(archive)
    assert archive.ensure_window_index_locked() is False


def test_orphan_signature_history_blocks_production_empty_attestation(tmp_path):
    archive = _archive(tmp_path)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    archive.manifest_signature_file.write_text(
        f"{'d' * 64}  {archive.manifest_file.name}\n",
        encoding="utf-8",
    )
    assert archive.ensure_window_index_locked() is False
    state = json.loads(archive.window_index_state_file.read_text(encoding="utf-8"))
    assert state["complete"] is False
    archive.manifest_signature_file.unlink()
    assert not archive.manifest_file.exists()
    assert production_empty_export_attestation_eligible(archive) is False
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_attestation(archive)
    assert archive.ensure_window_index_locked() is False


def test_complete_false_zero_index_blocks_production_empty_attestation(tmp_path):
    archive = _archive(tmp_path)
    _write_state(archive, _incomplete_zero_coverage_state(complete=False))
    assert production_empty_export_attestation_eligible(archive) is False
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_attestation(archive)
    assert archive.ensure_window_index_locked() is False


def test_malformed_index_blocks_production_empty_attestation(tmp_path):
    archive = _archive(tmp_path)
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    archive.window_index_state_file.write_text("not-json\n", encoding="utf-8")
    assert production_empty_export_attestation_eligible(archive) is False
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_attestation(archive)


def test_incomplete_complete_true_fragment_blocks_production_empty_attestation(
    tmp_path,
):
    """A bare complete=true fragment must not mint hashed empty proof."""
    archive = _archive(tmp_path)
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    archive.window_index_state_file.write_text(
        '{"complete":true}\n', encoding="utf-8"
    )
    assert production_empty_export_attestation_eligible(archive) is False
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_attestation(archive)


def test_extra_window_index_files_block_production_empty_attestation(tmp_path):
    archive = _archive(tmp_path)
    _write_state(archive, _incomplete_zero_coverage_state(complete=True))
    (archive.window_index_dir / "2026-09-01.json").write_text("{}\n", encoding="utf-8")
    assert production_empty_export_attestation_eligible(archive) is False
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_attestation(archive)


def test_prior_manifest_coverage_index_blocks_production_empty_attestation(
    tmp_path,
):
    archive = _archive(tmp_path)
    _write_state(
        archive,
        {
            "schema_version": 1,
            "manifest_present": True,
            "manifest_size": 123,
            "manifest_mtime_ns": 456,
            "manifest_sha256": "a" * 64,
            "complete": True,
            "coverage_start_day": "2026-09-01",
            "coverage_through_day": "2026-09-02",
            "coverage_day_count": 2,
            "shard_sha256": {"2026-09-01": "b" * 64, "2026-09-02": "c" * 64},
            "updated_at_utc": NOW.isoformat(),
        },
    )
    assert production_empty_export_attestation_eligible(archive) is False
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_attestation(archive)
    assert archive.ensure_window_index_locked() is False


def _extract_state_json_validator_bash() -> str:
    source = EXPORT_SCRIPT.read_text(encoding="utf-8")
    start = source.index("state_json_is_certified_empty_without_manifest()")
    end = source.index("\nwrite_empty_export_attestation_if_canonical()")
    return source[start:end]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_export_shell_rejects_incomplete_complete_true_fragment(tmp_path):
    """Bash exporter must refuse the same incomplete fragment Python refuses."""
    validator = _extract_state_json_validator_bash()
    good = tmp_path / "good.json"
    bad = tmp_path / "bad.json"
    good.write_text(
        json.dumps(_incomplete_zero_coverage_state(complete=True), sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    bad.write_text('{"complete":true}\n', encoding="utf-8")
    script = f"""
set -Eeuo pipefail
{validator}
state_json_is_certified_empty_without_manifest "{good.as_posix()}"
! state_json_is_certified_empty_without_manifest "{bad.as_posix()}"
! state_json_is_certified_empty_without_manifest "{tmp_path.as_posix()}/missing.json"
"""
    completed = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
