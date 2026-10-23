"""Contract tests for fail-closed source-coordinate verification.

This is an isolated proof primitive, NOT a live checkpoint migration test.
"""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from app.opip.storage.screening_stream_frame import (
    StreamCheckpoint,
    StreamContinuityError,
    StreamFrame,
    StreamPart,
    plan_resume,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _evidence(tmp_path: Path) -> tuple[StreamFrame, bytes, tuple[bytes, ...]]:
    blocks = (b'{"id":1}\n{"id":2}\n', b'{"id":3}\n{"id":4}\n', b'{"id":5}\n')
    paths = (tmp_path / "one.gz", tmp_path / "two.gz", tmp_path / "hot.jsonl")
    parts = []
    cursor = 0
    for path, raw in zip(paths, blocks):
        if path.suffix == ".gz":
            with gzip.open(path, "wb") as handle:
                handle.write(raw)
        else:
            path.write_bytes(raw)
        parts.append(
            StreamPart(
                path=path,
                start=cursor,
                end=cursor + len(raw),
                sha256=_sha(path.read_bytes()),
                raw_sha256=_sha(raw),
                archived=path.suffix == ".gz",
                row_count=raw.count(b"\n") if path.suffix == ".gz" else None,
            )
        )
        cursor += len(raw)
    frame = StreamFrame(_sha(b"stable producer identity"), 0, cursor, tuple(parts))
    return frame, b"".join(blocks), blocks


def _cp(frame: StreamFrame, pos: int, raw: bytes) -> StreamCheckpoint:
    return StreamCheckpoint(
        frame.stream_id, pos, _sha(raw[max(0, pos - 4):pos]) if pos else "",
        min(4, pos),
    )


@pytest.mark.acceptance
def test_archive_to_archive_transition(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, data, blocks = _evidence(tmp_path)
    plan = plan_resume(frame, _cp(frame, len(blocks[0]), data))
    assert [p.path.name for p, _ in plan] == ["two.gz", "hot.jsonl"]
    assert plan[0][1] == 0


@pytest.mark.acceptance
def test_archive_to_hot_transition(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, data, blocks = _evidence(tmp_path)
    plan = plan_resume(frame, _cp(frame, len(blocks[0]) + len(blocks[1]), data))
    assert len(plan) == 1
    assert plan[0][0].path.name == "hot.jsonl" and plan[0][1] == 0


@pytest.mark.acceptance
def test_checkpoint_at_end_is_idempotent(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, data, _ = _evidence(tmp_path)
    assert plan_resume(frame, _cp(frame, len(data), data)) == ()


@pytest.mark.acceptance
def test_unframed_legacy_offset_cannot_be_promoted(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, data, _ = _evidence(tmp_path)
    bad = StreamCheckpoint("", 10, _sha(data[6:10]), 4)
    with pytest.raises(StreamContinuityError, match="identity mismatch"):
        plan_resume(frame, bad)


@pytest.mark.acceptance
def test_pruned_checkpoint_fails_closed(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, data, blocks = _evidence(tmp_path)
    retained = StreamFrame(frame.stream_id, len(blocks[0]), frame.end, frame.parts[1:])
    with pytest.raises(StreamContinuityError, match="EVIDENCE_PRUNED"):
        plan_resume(retained, _cp(frame, 4, data))


@pytest.mark.acceptance
def test_missing_segment_fails_closed(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, _, _ = _evidence(tmp_path)
    frame = StreamFrame(frame.stream_id, 0, frame.end, (frame.parts[0], frame.parts[2]))
    with pytest.raises(StreamContinuityError, match="gap"):
        plan_resume(frame, StreamCheckpoint(frame.stream_id, 0, "", 0))


@pytest.mark.acceptance
def test_overlapping_segments_fail_closed(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, _, _ = _evidence(tmp_path)
    p = frame.parts[1]
    p = StreamPart(p.path, p.start - 1, p.end, p.sha256, p.raw_sha256, True, p.row_count)
    overlapping = StreamFrame(frame.stream_id, 0, frame.end, (frame.parts[0], p, frame.parts[2]))
    with pytest.raises(StreamContinuityError, match="overlap"):
        plan_resume(overlapping, StreamCheckpoint(frame.stream_id, 0, "", 0))


@pytest.mark.acceptance
def test_corrupt_archive_fails_before_consume(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, _, _ = _evidence(tmp_path)
    frame.parts[0].path.write_bytes(b"corrupt")
    with pytest.raises(StreamContinuityError, match="digest mismatch"):
        plan_resume(frame, StreamCheckpoint(frame.stream_id, 0, "", 0))


@pytest.mark.acceptance
def test_anchor_mismatch_refuses_rebaseline(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, data, blocks = _evidence(tmp_path)
    cp = StreamCheckpoint(frame.stream_id, len(blocks[0]), _sha(b"wrong"), 4)
    with pytest.raises(StreamContinuityError, match="anchor mismatch"):
        plan_resume(frame, cp)


@pytest.mark.acceptance
def test_checkpoint_must_be_complete_record(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, data, blocks = _evidence(tmp_path)
    with pytest.raises(StreamContinuityError, match="row aligned"):
        plan_resume(frame, _cp(frame, len(blocks[0]) - 1, data))


@pytest.mark.acceptance
def test_checkpoint_beyond_end_fails(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, _, _ = _evidence(tmp_path)
    with pytest.raises(StreamContinuityError, match="beyond end"):
        plan_resume(frame, StreamCheckpoint(frame.stream_id, frame.end + 1, _sha(b"abc"), 3))


@pytest.mark.acceptance
def test_stable_identity_not_derived_from_mutable_contents(tmp_path):
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-015: verify framed input safety."""
    frame, raw, _ = _evidence(tmp_path)
    assert frame.stream_id != _sha(raw)
