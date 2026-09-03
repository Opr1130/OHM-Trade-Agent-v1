from datetime import datetime, timedelta, timezone

from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    parse_json_object_line,
)


NOW = datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc)


def _archive(tmp_path):
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
