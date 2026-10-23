"""Stable-coordinate verification for bounded learning evidence streams.

Pure verification/planning primitive. Producer/export MUST authenticate the
frame before use. Legacy hot-file offsets cannot be promoted automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
from pathlib import Path


class StreamContinuityError(ValueError):
    """The evidence cannot prove a safe stream resume."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise StreamContinuityError(f"{name}: invalid digest")


@dataclass(frozen=True)
class StreamPart:
    path: Path
    start: int
    end: int
    sha256: str
    raw_sha256: str
    archived: bool
    row_count: int | None = None

    def validate_header(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise StreamContinuityError("invalid part boundaries")
        _require_digest(self.sha256, "part.sha256")
        _require_digest(self.raw_sha256, "part.raw_sha256")
        if self.archived and (self.row_count is None or self.row_count <= 0):
            raise StreamContinuityError("missing archive row count")


@dataclass(frozen=True)
class StreamFrame:
    # Stable stream ID must never depend on moving counters or manifest hashes.
    stream_id: str
    retained_start: int
    end: int
    parts: tuple[StreamPart, ...]

    def verify_layout(self) -> tuple[StreamPart, ...]:
        _require_digest(self.stream_id, "stream_id")
        if self.retained_start < 0 or self.end < self.retained_start:
            raise StreamContinuityError("invalid stream range")
        if not self.parts:
            if self.retained_start != self.end:
                raise StreamContinuityError("missing stream contents")
            return ()
        ordered = tuple(sorted(self.parts, key=lambda p: p.start))
        seen: set[str] = set()
        next_byte = self.retained_start
        for index, part in enumerate(ordered):
            part.validate_header()
            if part.start != next_byte:
                raise StreamContinuityError(
                    "stream overlap" if part.start < next_byte else "stream gap"
                )
            if part.sha256 in seen:
                raise StreamContinuityError("duplicate stream part")
            seen.add(part.sha256)
            if index != len(ordered) - 1 and not part.archived:
                raise StreamContinuityError("nonterminal hot file")
            next_byte = part.end
        if ordered[-1].archived:
            raise StreamContinuityError("missing terminal hot file")
        if next_byte != self.end:
            raise StreamContinuityError("incorrect stream end")
        return ordered


@dataclass(frozen=True)
class StreamCheckpoint:
    stream_id: str
    position: int
    anchor_sha256: str
    anchor_bytes: int


def verify_part_bytes(part: StreamPart, *, chunk_size: int = 128 * 1024) -> None:
    """Verify physical bytes against externally authenticated producer hashes.

    This scans complete parts. Production should reuse a trusted replica
    verification receipt rather than decompress 500MB every two minutes.
    """
    part.validate_header()
    compressed = hashlib.sha256()
    raw = hashlib.sha256()
    length = rows = 0
    last = b""
    try:
        with part.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                compressed.update(chunk)
        if compressed.hexdigest() != part.sha256:
            raise StreamContinuityError("source digest mismatch")
        reader = gzip.open(part.path, "rb") if part.archived else part.path.open("rb")
        with reader:
            for chunk in iter(lambda: reader.read(chunk_size), b""):
                raw.update(chunk)
                length += len(chunk)
                rows += chunk.count(b"\n")
                last = chunk[-1:]
    except StreamContinuityError:
        raise
    except (OSError, EOFError, ValueError) as exc:
        raise StreamContinuityError("unreadable part") from exc
    if raw.hexdigest() != part.raw_sha256:
        raise StreamContinuityError("uncompressed digest mismatch")
    if length != part.end - part.start:
        raise StreamContinuityError("uncompressed length mismatch")
    if last != b"\n":
        raise StreamContinuityError("partial final record")
    if part.archived and rows != part.row_count:
        raise StreamContinuityError("archive row count mismatch")


def _read_absolute(frame: StreamFrame, start: int, end: int) -> bytes:
    if start < frame.retained_start or end > frame.end or start > end:
        raise StreamContinuityError("anchor outside retention")
    chunks = []
    for part in frame.verify_layout():
        lo, hi = max(start, part.start), min(end, part.end)
        if lo >= hi:
            continue
        try:
            reader = gzip.open(part.path, "rb") if part.archived else part.path.open("rb")
            with reader:
                reader.seek(lo - part.start)
                chunk = reader.read(hi - lo)
        except (OSError, EOFError, ValueError) as exc:
            raise StreamContinuityError("cannot read anchor") from exc
        if len(chunk) != hi - lo:
            raise StreamContinuityError("short anchor")
        chunks.append(chunk)
    result = b"".join(chunks)
    if len(result) != end - start:
        raise StreamContinuityError("anchor discontinuity")
    return result


def plan_resume(
    frame: StreamFrame,
    checkpoint: StreamCheckpoint,
    *,
    verify_bytes: bool = True,
) -> tuple[tuple[StreamPart, int], ...]:
    """Locate a proven checkpoint; never guess a legacy hot-file origin."""
    parts = frame.verify_layout()
    if checkpoint.stream_id != frame.stream_id:
        raise StreamContinuityError("stream identity mismatch")
    if checkpoint.position < frame.retained_start:
        raise StreamContinuityError("EVIDENCE_PRUNED_BEFORE_CHECKPOINT")
    if checkpoint.position > frame.end:
        raise StreamContinuityError("checkpoint beyond end")
    if checkpoint.position == 0:
        if checkpoint.anchor_bytes or checkpoint.anchor_sha256:
            raise StreamContinuityError("unexpected genesis anchor")
    else:
        if not 0 < checkpoint.anchor_bytes <= 4096:
            raise StreamContinuityError("missing anchor")
        _require_digest(checkpoint.anchor_sha256, "checkpoint.anchor_sha256")
        if checkpoint.position - checkpoint.anchor_bytes < frame.retained_start:
            raise StreamContinuityError("checkpoint anchor pruned")
    if verify_bytes:
        for part in parts:
            verify_part_bytes(part)
        if checkpoint.position:
            anchor = _read_absolute(
                frame,
                checkpoint.position - checkpoint.anchor_bytes,
                checkpoint.position,
            )
            if _sha(anchor) != checkpoint.anchor_sha256:
                raise StreamContinuityError("anchor mismatch")
            if anchor[-1:] != b"\n":
                raise StreamContinuityError("checkpoint not row aligned")
    return tuple(
        (part, max(0, checkpoint.position - part.start))
        for part in parts if part.end > checkpoint.position
    )
