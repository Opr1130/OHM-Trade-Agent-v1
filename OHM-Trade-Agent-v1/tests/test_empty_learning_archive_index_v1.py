from datetime import datetime, timedelta, timezone
import json

from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    parse_json_object_line,
)


NOW = datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc)


def _archive(tmp_path):
    """Build an isolated screening-evidence archive for regression tests."""
    hot = tmp_path / "screening_evaluations.jsonl"
    return BoundedJsonlArchive(
        data_file=hot,
        archive_dir=tmp_path / "screening_evaluations_archive",
        max_bytes=100_000,
        keep_lines=2,
        archive_prefix="screening_evaluations",
        parse_line=parse_json_object_line,
        visible_at=lambda row: datetime.fromisoformat(row["observed_at"]),
    )


def test_derived_only_archive_directory_is_complete_without_manifest(tmp_path):
    """A genuinely empty derived-only archive is certified complete."""
    archive = _archive(tmp_path)

    assert archive.ensure_window_index_locked() is True
    assert archive.window_index_dir.exists()
    assert not archive.manifest_file.exists()

    # A second pass sees the derived directory created by the first pass.
    assert archive.ensure_window_index_locked() is True

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert selection.paths == ()
    assert selection.warnings == ()


def test_real_archive_segment_without_manifest_remains_fail_closed(tmp_path):
    """An orphan gzip segment without a manifest remains incomplete."""
    archive = _archive(tmp_path)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    (archive.archive_dir / "screening_evaluations-orphan.jsonl.gz").write_bytes(
        b"orphan"
    )

    assert archive.ensure_window_index_locked() is False

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert "ARCHIVE_WINDOW_INDEX_INCOMPLETE" in selection.warnings


def test_prior_manifest_index_state_without_manifest_remains_fail_closed(tmp_path):
    """Prior manifest-backed index evidence is preserved and fails closed."""
    archive = _archive(tmp_path)
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    prior_state = {
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
    archive.window_index_state_file.write_text(
        json.dumps(prior_state, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = archive.window_index_state_file.read_bytes()

    assert archive.ensure_window_index_locked() is False
    assert archive.window_index_state_file.read_bytes() == before

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert "ARCHIVE_MANIFEST_UNAVAILABLE" in selection.warnings


def test_manifest_signature_without_manifest_remains_fail_closed(tmp_path):
    """A manifest signature sidecar without its manifest is incomplete."""
    archive = _archive(tmp_path)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    archive.manifest_signature_file.write_text(
        f'{"d" * 64}  {archive.manifest_file.name}\n',
        encoding="utf-8",
    )

    assert archive.ensure_window_index_locked() is False
    state = json.loads(archive.window_index_state_file.read_text(encoding="utf-8"))
    assert state["complete"] is False

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert "ARCHIVE_WINDOW_INDEX_INCOMPLETE" in selection.warnings


def test_incomplete_zero_coverage_state_is_not_reclassified_as_empty(tmp_path):
    """An incomplete zero-coverage state is never promoted to complete."""
    archive = _archive(tmp_path)
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
    before = archive.window_index_state_file.read_bytes()

    assert archive.ensure_window_index_locked() is False
    assert archive.window_index_state_file.read_bytes() == before


def test_certified_empty_state_is_invalidated_when_orphan_segment_appears(tmp_path):
    """New orphan segment evidence invalidates a prior empty certification."""
    archive = _archive(tmp_path)
    assert archive.ensure_window_index_locked() is True

    (archive.archive_dir / "screening_evaluations-late-orphan.jsonl.gz").write_bytes(
        b"orphan"
    )

    assert archive.ensure_window_index_locked() is False
    state = json.loads(archive.window_index_state_file.read_text(encoding="utf-8"))
    assert state["manifest_present"] is False
    assert state["complete"] is False
    assert state["coverage_day_count"] == 0

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert "ARCHIVE_WINDOW_INDEX_INCOMPLETE" in selection.warnings


def test_certified_empty_state_is_invalidated_when_signature_appears(tmp_path):
    """New signature evidence invalidates a prior empty certification."""
    archive = _archive(tmp_path)
    assert archive.ensure_window_index_locked() is True

    archive.manifest_signature_file.write_text(
        f'{"e" * 64}  {archive.manifest_file.name}\n',
        encoding="utf-8",
    )

    assert archive.ensure_window_index_locked() is False
    state = json.loads(archive.window_index_state_file.read_text(encoding="utf-8"))
    assert state["manifest_present"] is False
    assert state["complete"] is False

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert "ARCHIVE_WINDOW_INDEX_INCOMPLETE" in selection.warnings


def test_boolean_numeric_metadata_cannot_certify_empty_archive(tmp_path):
    """Boolean numeric metadata cannot pass canonical empty-state validation."""
    boolean_cases = {
        "schema_version": True,
        "manifest_size": False,
        "manifest_mtime_ns": False,
        "coverage_day_count": False,
    }

    for field, malformed_value in boolean_cases.items():
        case_dir = tmp_path / field
        archive = _archive(case_dir)
        archive.window_index_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "schema_version": 1,
            "manifest_present": False,
            "manifest_size": 0,
            "manifest_mtime_ns": 0,
            "manifest_sha256": "",
            "complete": True,
            "coverage_start_day": None,
            "coverage_through_day": None,
            "coverage_day_count": 0,
            "shard_sha256": {},
            "updated_at_utc": NOW.isoformat(),
        }
        state[field] = malformed_value
        archive.window_index_state_file.write_text(
            json.dumps(state, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        before = archive.window_index_state_file.read_bytes()

        assert archive.ensure_window_index_locked() is False
        assert archive.window_index_state_file.read_bytes() == before

        selection = archive.archive_paths_for_visible_window(
            start=NOW - timedelta(minutes=1),
            through=NOW + timedelta(minutes=1),
            max_segments=8,
        )
        assert selection.complete is False
        assert "ARCHIVE_WINDOW_INDEX_INVALID" in selection.warnings


def test_reader_rejects_certified_empty_state_when_orphan_evidence_appears(tmp_path):
    """The read path independently rejects stale empty certification."""
    archive = _archive(tmp_path)
    assert archive.ensure_window_index_locked() is True

    (archive.archive_dir / "screening_evaluations-direct-orphan.jsonl.gz").write_bytes(
        b"orphan"
    )

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert "ARCHIVE_WINDOW_INDEX_INVALID" in selection.warnings


def test_reader_rejects_certified_empty_state_when_manifest_appears(tmp_path):
    """A newly published manifest invalidates stale empty certification."""
    archive = _archive(tmp_path)
    assert archive.ensure_window_index_locked() is True

    archive.manifest_file.write_text(
        json.dumps({"schema_version": 1, "segments": {}}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert selection.paths == ()
    assert "ARCHIVE_WINDOW_INDEX_INVALID" in selection.warnings


def test_noncanonical_empty_state_fields_cannot_certify_archive(tmp_path):
    """Missing or falsy noncanonical fields cannot certify an empty archive."""
    base_state = {
        "schema_version": 1,
        "manifest_present": False,
        "manifest_size": 0,
        "manifest_mtime_ns": 0,
        "manifest_sha256": "",
        "complete": True,
        "coverage_start_day": None,
        "coverage_through_day": None,
        "coverage_day_count": 0,
        "shard_sha256": {},
        "updated_at_utc": NOW.isoformat(),
    }
    cases = (
        ("missing_manifest_sha256", "manifest_sha256", None, True),
        ("missing_coverage_start", "coverage_start_day", None, True),
        ("missing_coverage_through", "coverage_through_day", None, True),
        ("missing_updated_at", "updated_at_utc", None, True),
        ("false_manifest_sha256", "manifest_sha256", False, False),
        ("false_coverage_start", "coverage_start_day", False, False),
        ("false_coverage_through", "coverage_through_day", False, False),
        ("empty_updated_at", "updated_at_utc", "", False),
    )

    for name, field, value, omit in cases:
        archive = _archive(tmp_path / name)
        archive.window_index_dir.mkdir(parents=True, exist_ok=True)
        state = dict(base_state)
        if omit:
            state.pop(field)
        else:
            state[field] = value
        archive.window_index_state_file.write_text(
            json.dumps(state, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        before = archive.window_index_state_file.read_bytes()

        assert archive.ensure_window_index_locked() is False
        assert archive.window_index_state_file.read_bytes() == before

        selection = archive.archive_paths_for_visible_window(
            start=NOW - timedelta(minutes=1),
            through=NOW + timedelta(minutes=1),
            max_segments=8,
        )
        assert selection.complete is False
        assert "ARCHIVE_WINDOW_INDEX_INVALID" in selection.warnings


def test_invalid_updated_at_cannot_certify_empty_archive(tmp_path):
    """Invalid state timestamps remain unchanged and fail closed on reads."""
    archive = _archive(tmp_path)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": 1,
        "manifest_present": False,
        "manifest_size": 0,
        "manifest_mtime_ns": 0,
        "manifest_sha256": "",
        "complete": True,
        "coverage_start_day": None,
        "coverage_through_day": None,
        "coverage_day_count": 0,
        "shard_sha256": {},
        "updated_at_utc": "not-a-timestamp",
    }
    archive.window_index_state_file.write_text(
        json.dumps(state, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = archive.window_index_state_file.read_bytes()

    assert archive.ensure_window_index_locked() is False
    assert archive.window_index_state_file.read_bytes() == before

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert selection.paths == ()
    assert "ARCHIVE_WINDOW_INDEX_INVALID" in selection.warnings


def test_overflowing_updated_at_cannot_certify_empty_archive(tmp_path):
    """UTC normalization overflow is treated as invalid archive state."""
    archive = _archive(tmp_path)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": 1,
        "manifest_present": False,
        "manifest_size": 0,
        "manifest_mtime_ns": 0,
        "manifest_sha256": "",
        "complete": True,
        "coverage_start_day": None,
        "coverage_through_day": None,
        "coverage_day_count": 0,
        "shard_sha256": {},
        "updated_at_utc": "0001-01-01T00:00:00+23:59",
    }
    archive.window_index_state_file.write_text(
        json.dumps(state, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before = archive.window_index_state_file.read_bytes()

    assert archive.ensure_window_index_locked() is False
    assert archive.window_index_state_file.read_bytes() == before

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is False
    assert selection.paths == ()
    assert "ARCHIVE_WINDOW_INDEX_INVALID" in selection.warnings
