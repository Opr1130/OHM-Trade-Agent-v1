from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from app.opip.decision.store import screening_evaluations_archive
from app.opip.learning.replica_archive_repair import (
    reconcile_qualification_replica_archives,
)


NOW = datetime(2026, 9, 6, 6, 0, tzinfo=timezone.utc)


def _write_verified_screening_segment(data_root: Path):
    hot = data_root / "opip/qualification/screening_evaluations.jsonl"
    archive = screening_evaluations_archive(hot)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    segment = archive.archive_dir / "screening_evaluations-legacy.jsonl.gz"
    row = {
        "observed_at": NOW.isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "BTCUSD",
    }
    with gzip.open(segment, "wb") as handle:
        handle.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    digest = hashlib.sha256(segment.read_bytes()).hexdigest()
    segment.with_suffix(segment.suffix + ".sha256").write_text(
        f"{digest}  {segment.name}\n",
        encoding="utf-8",
    )
    return archive, segment


def test_missing_replica_manifest_is_reconstructed_only_from_verified_segments(tmp_path):
    archive, segment = _write_verified_screening_segment(tmp_path)

    result = reconcile_qualification_replica_archives(tmp_path)

    assert result["screening"] == "RECONSTRUCTED_VERIFIED"
    payload = json.loads(archive.manifest_file.read_text(encoding="utf-8"))
    assert payload["replica_reconstructed_from_verified_segments"] is True
    assert len(payload["segments"]) == 1
    assert archive.manifest_signature_file.exists()

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert selection.paths == (segment,)
    assert selection.warnings == ()


def test_corrupt_replica_segment_cannot_create_manifest(tmp_path):
    archive, segment = _write_verified_screening_segment(tmp_path)
    segment.with_suffix(segment.suffix + ".sha256").write_text(
        f"{'0' * 64}  {segment.name}\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        reconcile_qualification_replica_archives(tmp_path)

    assert not archive.manifest_file.exists()
    assert not archive.manifest_signature_file.exists()


def test_existing_unsigned_manifest_is_never_replaced_by_replica_repair(tmp_path):
    archive, _segment = _write_verified_screening_segment(tmp_path)
    original = {
        "schema_version": 1,
        "segments": {},
        "updated_at_utc": NOW.isoformat(),
    }
    archive.manifest_file.write_text(
        json.dumps(original, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = archive.manifest_file.read_bytes()

    with pytest.raises(RuntimeError, match="signature"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.manifest_file.read_bytes() == before
    assert not archive.manifest_signature_file.exists()


def test_orphan_manifest_signature_blocks_replica_reconstruction(tmp_path):
    archive, _segment = _write_verified_screening_segment(tmp_path)
    archive.manifest_signature_file.write_text(
        f"{'a' * 64}  {archive.manifest_file.name}\n",
        encoding="utf-8",
    )
    before = archive.manifest_signature_file.read_bytes()

    with pytest.raises(RuntimeError, match="manifest missing with signature present"):
        reconcile_qualification_replica_archives(tmp_path)

    assert not archive.manifest_file.exists()
    assert archive.manifest_signature_file.read_bytes() == before


def test_outcomes_cycle_repairs_replica_before_reading_pending_handoff():
    source = (
        Path(__file__).resolve().parents[1]
        / "app/jobs/run_opportunity_intelligence_cycle.py"
    ).read_text(encoding="utf-8")
    repair = source.index("reconcile_qualification_replica_archives(data_root)")
    pending = source.index("pending_accountability_outcomes()")

    assert repair < pending
    assert "OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR" in source
    assert '"trade_authority_changed": False' in source
    assert '"policy_change_authorized": False' in source


def test_empty_replica_archives_certify_without_segments(tmp_path):
    result = reconcile_qualification_replica_archives(tmp_path)

    assert result == {
        "screening": "EMPTY_CERTIFIED",
        "funnel": "EMPTY_CERTIFIED",
        "summaries": "EMPTY_CERTIFIED",
    }


def test_orphan_incomplete_empty_index_is_certified_on_replica_only(tmp_path):
    """Leftover derived index after P1-empty export must not block outcomes."""
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    prior_state = {
        "schema_version": 1,
        "manifest_present": False,
        "manifest_size": 0,
        "manifest_mtime_ns": 0,
        "manifest_sha256": "",
        "complete": False,
        "coverage_start_day": None,
        "coverage_through_day": None,
        "coverage_day_count": 0,
        "shard_sha256": {},
        "updated_at_utc": NOW.isoformat(),
    }
    archive.window_index_state_file.write_text(
        json.dumps(prior_state, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert archive.ensure_window_index_locked() is False

    result = reconcile_qualification_replica_archives(tmp_path)

    assert result["screening"] == "EMPTY_CERTIFIED_FROM_ORPHAN_INDEX"
    assert result["funnel"] == "EMPTY_CERTIFIED"
    assert result["summaries"] == "EMPTY_CERTIFIED"
    assert archive.ensure_window_index_locked() is True
    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert selection.paths == ()


def test_replica_empty_certify_refuses_canonical_signature(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    archive.manifest_signature_file.write_text(
        f"{'a' * 64}  {archive.manifest_file.name}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="signature is present"):
        archive.certify_empty_replica_window_index_locked()


def test_replica_empty_certify_refuses_gzip_segments(tmp_path):
    archive, _segment = _write_verified_screening_segment(tmp_path)
    with pytest.raises(RuntimeError, match="archive segments are present"):
        archive.certify_empty_replica_window_index_locked()
