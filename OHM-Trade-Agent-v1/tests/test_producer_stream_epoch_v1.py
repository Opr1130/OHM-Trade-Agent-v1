"""Producer epoch contract: archive-before-delete with absolute offsets.

All tests are isolated and never authorize migration of a populated legacy
source; production activation of a framed epoch is independently owner-gated.
"""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from app.opip.decision.store import _append_rows, _archive_for
from app.opip.storage.bounded_jsonl import write_atomic_lines
from app.opip.storage.producer_stream_epoch import (
    StreamEpochError,
    initialize_empty_locked,
    load,
    prepare_compaction_locked,
    recover_compaction_locked,
    snapshot_locked,
    state_path,
    stream_id_for,
)


@pytest.mark.acceptance
def test_nonempty_legacy_stream_refuses_implicit_epoch(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: legacy coordinates are not minted."""
    hot = tmp_path / "screening_evaluations.jsonl"
    hot.write_bytes(b'{"observation_id":"old"}\n')
    with pytest.raises(StreamEpochError, match="LEGACY_HOT"):
        initialize_empty_locked(hot, tmp_path / "screening_evaluations_archive")
    assert not state_path(hot).exists()


@pytest.mark.acceptance
def test_existing_archive_refuses_implicit_epoch(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: archived legacy bytes block genesis."""
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = tmp_path / "screening_evaluations_archive"
    archive.mkdir()
    with gzip.open(archive / "screening_evaluations-test.jsonl.gz", "wb") as writer:
        writer.write(b'{"observation_id":"legacy"}\n')
    with pytest.raises(StreamEpochError, match="LEGACY_ARCHIVE"):
        initialize_empty_locked(hot, archive)


@pytest.mark.acceptance
def test_stream_epoch_identity_is_stable_on_repeated_initialization(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: process restart keeps same epoch."""
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = tmp_path / "screening_evaluations_archive"
    first = initialize_empty_locked(hot, archive)
    second = initialize_empty_locked(hot, archive)
    assert first == second
    assert stream_id_for(first, hot.stem) == stream_id_for(second, hot.stem)


@pytest.mark.acceptance
def test_two_rotations_preserve_absolute_position_and_raw_evidence(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: compaction never resets coordinates."""
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = tmp_path / "screening_evaluations_archive"
    initial = initialize_empty_locked(hot, archive)
    all_rows: list[bytes] = []
    for batch in range(3):
        rows = [
            {"observation_id": f"{batch}-{i}", "observed_at": "2026-10-23T00:00:00Z"}
            for i in range(4)
        ]
        count = _append_rows(
            hot, rows, max_bytes=400, keep_lines=3,
            dead_letter_path=tmp_path / "dead.jsonl",
        )
        assert count == len(rows)
        from app.opip.storage.bounded_jsonl import encode_row
        all_rows.extend(encode_row(row) for row in rows)
        frame = snapshot_locked(hot, archive, hot.stem)
        assert frame["stream_end"] == sum(len(row) for row in all_rows)
        assert frame["hot"]["start"] == load(hot)["hot_base"]
        assert frame["hot"]["end"] == frame["stream_end"]
        assert frame["stream_id"] == stream_id_for(initial, hot.stem)
    assert len(load(hot)["segments"]) >= 2
    raw_archived = b""
    for segment in load(hot)["segments"]:
        path = archive / segment["archive"]
        with gzip.open(path, "rb") as reader:
            raw = reader.read()
        assert len(raw) == segment["uncompressed_bytes"]
        assert hashlib.sha256(raw).hexdigest() == segment["raw_sha256"]
        raw_archived += raw
    assert raw_archived + hot.read_bytes() == b"".join(all_rows)


@pytest.mark.acceptance
def test_uncommitted_compaction_with_original_hot_can_retry(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: pre-rewrite crash is idempotent."""
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = tmp_path / "screening_evaluations_archive"
    initialize_empty_locked(hot, archive)
    data = b'{"id":1}\n{"id":2}\n'
    hot.write_bytes(data)
    prepare_compaction_locked(hot, archive_bytes=b'{"id":1}\n', tail_bytes=b'{"id":2}\n')
    assert load(hot)["pending"] is not None
    recover_compaction_locked(hot, archive)
    assert load(hot)["pending"] is None
    assert load(hot)["hot_base"] == 0
    assert hot.read_bytes() == data


@pytest.mark.acceptance
def test_post_rewrite_crash_recovers_with_verified_archive(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: recover exactly one origin shift."""
    hot = tmp_path / "screening_evaluations.jsonl"
    archive_dir = tmp_path / "screening_evaluations_archive"
    initialize_empty_locked(hot, archive_dir)
    prefix = b'{"observed_at":"2026-10-23T00:00:00Z","id":1}\n'
    tail = b'{"observed_at":"2026-10-23T00:01:00Z","id":2}\n'
    hot.write_bytes(prefix + tail)
    prepare_compaction_locked(hot, archive_bytes=prefix, tail_bytes=tail)
    archive_dir.mkdir()
    archived = archive_dir / "screening_evaluations-proof.jsonl.gz"
    with gzip.open(archived, "wb") as writer:
        writer.write(prefix)
    digest = hashlib.sha256(archived.read_bytes()).hexdigest()
    archived.with_suffix(".gz.sha256").write_text(digest + "\n")
    store = _archive_for(hot, max_bytes=32, keep_lines=2)
    verification = store.verify_archive_file(archived, tier="WARM")
    store.update_manifest_locked(verification)
    write_atomic_lines(hot, [tail])
    recover_compaction_locked(hot, archive_dir)
    frame = snapshot_locked(hot, archive_dir, hot.stem)
    assert frame["hot"]["start"] == len(prefix)
    assert frame["stream_end"] == len(prefix + tail)
    assert len(frame["segments"]) == 1
    recover_compaction_locked(hot, archive_dir)
    assert len(load(hot)["segments"]) == 1


@pytest.mark.acceptance
def test_ambiguous_partial_rewrite_refuses_checkpoint_advance(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: unknown HOT state fails closed."""
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = tmp_path / "screening_evaluations_archive"
    initialize_empty_locked(hot, archive)
    hot.write_bytes(b'{"id":1}\n{"id":2}\n')
    prepare_compaction_locked(hot, archive_bytes=b'{"id":1}\n', tail_bytes=b'{"id":2}\n')
    hot.write_bytes(b'{"id":999}\n')
    with pytest.raises(StreamEpochError, match="DIVERGED"):
        recover_compaction_locked(hot, archive)
    assert load(hot)["pending"] is not None


@pytest.mark.acceptance
def test_snapshot_refuses_pending_compaction(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: exporter cannot mint mixed frame."""
    hot = tmp_path / "screening_evaluations.jsonl"
    archive = tmp_path / "screening_evaluations_archive"
    initialize_empty_locked(hot, archive)
    hot.write_bytes(b'{"id":1}\n{"id":2}\n')
    prepare_compaction_locked(hot, archive_bytes=b'{"id":1}\n', tail_bytes=b'{"id":2}\n')
    with pytest.raises(StreamEpochError, match="NOT_COMMITTED"):
        snapshot_locked(hot, archive, hot.stem)
