from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from app.opip.decision.store import screening_evaluations_archive
from app.opip.learning.empty_export_attestation import (
    EMPTY_EXPORT_ATTESTATION_FILENAME,
    production_empty_export_attestation_eligible,
    replica_leftover_index_blocks_empty_recovery,
    write_empty_export_attestation,
)
from app.opip.learning.replica_archive_repair import (
    reconcile_qualification_replica_archives,
)


NOW = datetime(2026, 9, 6, 6, 0, tzinfo=timezone.utc)


def _orphan_incomplete_empty_index_state() -> dict:
    return {
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


def _write_orphan_incomplete_empty_index(archive) -> None:
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    archive.window_index_state_file.write_text(
        json.dumps(_orphan_incomplete_empty_index_state(), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prior_manifest_index_state() -> dict:
    return {
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
    }


def _write_prior_manifest_index_without_canonical_files(archive) -> None:
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    archive.window_index_state_file.write_text(
        json.dumps(_prior_manifest_index_state(), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_matching_empty_export_attestation(archive) -> None:
    write_empty_export_attestation(
        archive,
        exported_at_utc=NOW.isoformat(),
        production_deployed_sha="a" * 40,
    )


def _plant_empty_export_attestation(archive, **overrides) -> None:
    payload = {
        "schema_version": 1,
        "kind": "empty_export_attestation_v1",
        "archive_prefix": archive.archive_prefix,
        "hot_bytes": 0,
        "segment_count": 0,
        "manifest_present": False,
        "signature_present": False,
        "exported_at_utc": NOW.isoformat(),
        "production_deployed_sha": "a" * 40,
    }
    payload.update(overrides)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    (archive.archive_dir / EMPTY_EXPORT_ATTESTATION_FILENAME).write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


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
    advance = source.index("advance_accountability_handoff_backfill(")
    pending = source.index("pending_accountability_outcomes()")

    assert repair < advance < pending
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


def test_orphan_incomplete_empty_index_without_attestation_fails_closed(tmp_path):
    """Leftover derived index is not proof the authoritative export was empty."""
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    before = archive.window_index_state_file.read_bytes()
    assert archive.ensure_window_index_locked() is False
    assert production_empty_export_attestation_eligible(archive) is False

    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_orphan_incomplete_empty_index_is_certified_with_export_attestation(
    tmp_path,
):
    """Canonical-empty export plus hashed attestation unblocks leftover index."""
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive)
    assert archive.ensure_window_index_locked() is False
    assert production_empty_export_attestation_eligible(archive) is False

    result = reconcile_qualification_replica_archives(tmp_path)

    assert result["screening"] == "EMPTY_CERTIFIED_FROM_EXPORT_ATTESTATION"
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


def test_prior_lineage_later_absent_fails_closed_without_attestation(tmp_path):
    """Prior manifest/segment lineage in the leftover index stays fail-closed."""
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_prior_manifest_index_without_canonical_files(archive)
    before = archive.window_index_state_file.read_bytes()
    assert replica_leftover_index_blocks_empty_recovery(archive) is True
    assert production_empty_export_attestation_eligible(archive) is False
    assert archive.ensure_window_index_locked() is False

    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_prior_lineage_later_absent_fails_closed_with_planted_attestation(
    tmp_path,
):
    """Planted empty proof cannot override leftover prior-manifest lineage."""
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_prior_manifest_index_without_canonical_files(archive)
    _plant_empty_export_attestation(archive)
    before = archive.window_index_state_file.read_bytes()

    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_stale_attestation_fails_closed_when_hot_jsonl_is_present(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    archive.data_file.parent.mkdir(parents=True, exist_ok=True)
    archive.data_file.write_text("{}\n", encoding="utf-8")
    _plant_empty_export_attestation(archive, hot_bytes=0)
    before = archive.window_index_state_file.read_bytes()

    with pytest.raises(RuntimeError, match="LEGACY_COVERAGE_DISCONTINUITY_REQUIRED"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False
    # Must not enter empty-attestation certification for HOT-present legacy state.
    assert not archive._window_index_state_proves_empty_archive_without_manifest()



def test_mismatched_attestation_prefix_fails_closed(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive, archive_prefix="funnel_events")
    before = archive.window_index_state_file.read_bytes()

    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_mismatched_attestation_segment_count_fails_closed(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive, segment_count=1)
    before = archive.window_index_state_file.read_bytes()

    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


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


def test_replica_empty_certify_refuses_hot_jsonl(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    archive.data_file.parent.mkdir(parents=True, exist_ok=True)
    archive.data_file.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hot JSONL is present"):
        archive.certify_empty_replica_window_index_locked()
    assert archive.ensure_window_index_locked() is False


def test_replica_empty_certify_refuses_prior_manifest_index(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    _write_prior_manifest_index_without_canonical_files(archive)
    before = archive.window_index_state_file.read_bytes()
    with pytest.raises(RuntimeError, match="orphan incomplete empty leftover"):
        archive.certify_empty_replica_window_index_locked()
    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_replica_empty_certify_refuses_extra_window_index_files(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    (archive.window_index_dir / "2026-09-01.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="orphan incomplete empty leftover"):
        archive.certify_empty_replica_window_index_locked()
    assert archive.ensure_window_index_locked() is False
    assert replica_leftover_index_blocks_empty_recovery(archive) is True
    assert production_empty_export_attestation_eligible(archive) is False


def test_leftover_index_day_file_fails_closed_even_with_planted_attestation(
    tmp_path,
):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    (archive.window_index_dir / "2026-09-01.json").write_text("{}\n", encoding="utf-8")
    _plant_empty_export_attestation(archive)
    before = archive.window_index_state_file.read_bytes()

    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_attestation_with_unexpected_keys_fails_closed(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive, note="stale")
    before = archive.window_index_state_file.read_bytes()

    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_empty_export_attestation_is_not_eligible_when_segments_remain(tmp_path):
    archive, _segment = _write_verified_screening_segment(tmp_path)
    assert production_empty_export_attestation_eligible(archive) is False


def test_write_empty_export_attestation_refuses_prior_manifest_lineage(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_prior_manifest_index_without_canonical_files(archive)
    with pytest.raises(RuntimeError, match="trusted empty archive"):
        _write_matching_empty_export_attestation(archive)


def test_replica_attestation_invalid_schema_fails_closed(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive, schema_version=2)
    before = archive.window_index_state_file.read_bytes()
    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)
    assert archive.window_index_state_file.read_bytes() == before


def test_replica_attestation_invalid_kind_fails_closed(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive, kind="empty_export_attestation_v0")
    before = archive.window_index_state_file.read_bytes()
    with pytest.raises(RuntimeError, match="could not be certified"):
        reconcile_qualification_replica_archives(tmp_path)
    assert archive.window_index_state_file.read_bytes() == before


def test_replica_attestation_fails_closed_when_gzip_appears(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive)
    (archive.archive_dir / "screening_evaluations-late.jsonl.gz").write_bytes(b"late")
    before = archive.window_index_state_file.read_bytes()
    with pytest.raises(RuntimeError):
        reconcile_qualification_replica_archives(tmp_path)
    assert archive.window_index_state_file.read_bytes() == before
    assert archive.ensure_window_index_locked() is False


def test_replica_attestation_fails_closed_when_signature_appears(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive)
    archive.manifest_signature_file.write_text(
        f"{'a' * 64}  {archive.manifest_file.name}\n",
        encoding="utf-8",
    )
    before = archive.window_index_state_file.read_bytes()
    with pytest.raises(RuntimeError, match="manifest missing with signature present"):
        reconcile_qualification_replica_archives(tmp_path)
    assert archive.window_index_state_file.read_bytes() == before


def test_replica_attestation_fails_closed_when_manifest_appears(tmp_path):
    archive = screening_evaluations_archive(
        tmp_path / "opip/qualification/screening_evaluations.jsonl"
    )
    _write_orphan_incomplete_empty_index(archive)
    _plant_empty_export_attestation(archive)
    archive.manifest_file.write_text("{}\n", encoding="utf-8")
    before = archive.window_index_state_file.read_bytes()
    with pytest.raises(RuntimeError):
        reconcile_qualification_replica_archives(tmp_path)
    assert archive.window_index_state_file.read_bytes() == before
