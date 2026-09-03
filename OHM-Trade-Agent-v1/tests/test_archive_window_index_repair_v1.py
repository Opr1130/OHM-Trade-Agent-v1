from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    encode_row,
    parse_json_object_line,
)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _archive(tmp_path: Path) -> BoundedJsonlArchive:
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = BoundedJsonlArchive(
        data_file=hot,
        archive_dir=tmp_path / "screening_evaluations_archive",
        max_bytes=100_000,
        keep_lines=2,
        archive_prefix="screening_evaluations",
        parse_line=parse_json_object_line,
        visible_at=lambda row: datetime.fromisoformat(row["observed_at"]),
    )
    archive.append_encoded_many_locked(
        encode_row(
            {
                "observed_at": (NOW + timedelta(seconds=index)).isoformat(),
                "row": index,
            }
        )
        for index in range(5)
    )
    assert archive.compact_locked() is not None
    return archive


def _resign_manifest(archive: BoundedJsonlArchive) -> None:
    digest = hashlib.sha256(archive.manifest_file.read_bytes()).hexdigest()
    archive.manifest_signature_file.write_text(
        f"{digest}  {archive.manifest_file.name}\n",
        encoding="utf-8",
    )




def test_derived_only_archive_directory_remains_complete_without_manifest(
    tmp_path,
):
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = BoundedJsonlArchive(
        data_file=hot,
        archive_dir=tmp_path / "screening_evaluations_archive",
        max_bytes=100_000,
        keep_lines=2,
        archive_prefix="screening_evaluations",
        parse_line=parse_json_object_line,
        visible_at=lambda row: datetime.fromisoformat(row["observed_at"]),
    )

    assert archive.ensure_window_index_locked() is True
    assert archive.window_index_dir.exists()
    assert archive.manifest_file.exists() is False

    # The first maintenance pass creates only derived index state. A second
    # pass must not misclassify that derived directory as archived evidence.
    assert archive.ensure_window_index_locked() is True
    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert selection.paths == ()


def test_real_archive_segment_without_manifest_remains_fail_closed(tmp_path):
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = BoundedJsonlArchive(
        data_file=hot,
        archive_dir=tmp_path / "screening_evaluations_archive",
        max_bytes=100_000,
        keep_lines=2,
        archive_prefix="screening_evaluations",
        parse_line=parse_json_object_line,
        visible_at=lambda row: datetime.fromisoformat(row["observed_at"]),
    )
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    (archive.archive_dir / "screening_evaluations-orphan.jsonl.gz").write_bytes(
        b"orphan"
    )

    assert archive.ensure_window_index_locked() is False

def test_repair_window_index_backfills_legacy_visibility_metadata(tmp_path):
    archive = _archive(tmp_path)
    manifest = json.loads(archive.manifest_file.read_text(encoding="utf-8"))
    segment = next(iter(manifest["segments"].values()))
    segment["first_visible_at_utc"] = None
    segment["last_visible_at_utc"] = None
    archive.manifest_file.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _resign_manifest(archive)

    state = json.loads(
        archive.window_index_state_file.read_text(encoding="utf-8")
    )
    state["complete"] = False
    state["manifest_sha256"] = hashlib.sha256(
        archive.manifest_file.read_bytes()
    ).hexdigest()
    archive.window_index_state_file.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assert archive.repair_window_index_locked() is True

    repaired = json.loads(archive.manifest_file.read_text(encoding="utf-8"))
    repaired_segment = next(iter(repaired["segments"].values()))
    assert repaired_segment["first_visible_at_utc"] is not None
    assert repaired_segment["last_visible_at_utc"] is not None

    selection = archive.archive_paths_for_visible_window(
        start=NOW - timedelta(minutes=1),
        through=NOW + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert len(selection.paths) == 1


def test_repair_window_index_fails_closed_when_canonical_segment_is_missing(
    tmp_path,
):
    archive = _archive(tmp_path)
    manifest = json.loads(archive.manifest_file.read_text(encoding="utf-8"))
    segment = next(iter(manifest["segments"].values()))
    segment["first_visible_at_utc"] = None
    segment["last_visible_at_utc"] = None
    archive.manifest_file.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _resign_manifest(archive)

    for segment_path in archive.archive_dir.glob("*.jsonl.gz"):
        segment_path.unlink()

    assert archive.repair_window_index_locked() is False


def test_learning_export_repairs_indexes_before_publish_snapshot():
    exporter = (
        ROOT / "deploy" / "remote" / "export-opip-learning-evidence.sh"
    ).read_text(encoding="utf-8")

    repair = "python -m app.jobs.repair_opip_archive_window_indexes"
    assert repair in exporter
    assert "docker compose exec -T ohm-trade-agent" in exporter
    assert exporter.index(repair) < exporter.index('touch "$PUBLISH_LOCK"')
    assert exporter.index(repair) < exporter.index("copy_locked_jsonl()")
