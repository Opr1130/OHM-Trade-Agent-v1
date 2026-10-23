"""Stable-coordinate verification for bounded learning evidence streams.

Pure verification/planning primitive. Producer/export MUST authenticate the
frame before use. Legacy hot-file offsets cannot be promoted automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
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
                raise StreamContinuityError("DUPLICATE_CONTENT: repeated part digest")
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


def frame_sha256(frame: StreamFrame) -> str:
    """Digest a portable, canonical stream frame.

    The expected digest MUST come from the independently authenticated export
    manifest. This local digest alone does not establish producer provenance.
    Archive path names are logical identities; host-specific parent paths are
    intentionally excluded so staging and worker copies produce the same hash.
    """
    parts = frame.verify_layout()
    document = {
        "schema_version": 1,
        "stream_id": frame.stream_id,
        "retained_start": frame.retained_start,
        "end": frame.end,
        "parts": [
            {
                "name": part.path.name,
                "start": part.start,
                "end": part.end,
                "sha256": part.sha256,
                "raw_sha256": part.raw_sha256,
                "archived": part.archived,
                "row_count": part.row_count,
            }
            for part in parts
        ],
    }
    return _sha(json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8"))


def plan_resume(
    frame: StreamFrame,
    checkpoint: StreamCheckpoint,
    *,
    expected_frame_sha256: str,
    verify_bytes: bool = True,
) -> tuple[tuple[StreamPart, int], ...]:
    """Locate a proven checkpoint using a trusted, manifest-bound frame hash.

    expected_frame_sha256 MUST be supplied from the verified export manifest,
    not calculated by the caller from the untrusted frame itself.
    """
    _require_digest(expected_frame_sha256, "expected_frame_sha256")
    actual_frame_sha256 = frame_sha256(frame)
    if actual_frame_sha256 != expected_frame_sha256:
        raise StreamContinuityError("FRAME_AUTHENTICITY_MISMATCH")
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
    # The 4 KiB anchor is cheap and is what detects an equal-length or
    # in-place replacement of the HOT file, so it is ALWAYS verified.
    # verify_bytes only gates the expensive full-segment decompression.
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
    if verify_bytes:
        for part in parts:
            verify_part_bytes(part)
    return tuple(
        (part, max(0, checkpoint.position - part.start))
        for part in parts if part.end > checkpoint.position
    )


def read_absolute(frame: StreamFrame, start: int, end: int) -> bytes:
    """Public wrapper: read a proven absolute byte range from the frame."""
    return _read_absolute(frame, start, end)


def build_frame_from_snapshot(
    *,
    stream_name: str,
    state_bytes: bytes,
    archive_dir: Path,
    hot_path: Path,
    hot_bytes: bytes,
    expected_state_sha256: str,
    expected_hot_sha256: str,
    expected_archive_manifest_sha256: str | None,
) -> StreamFrame:
    """Rebuild the producer frame from one authenticated export snapshot.

    Every input is bound by the signed export manifest: the producer state
    document, the HOT bytes and (when segments exist) the archive manifest.
    The resulting frame digest is therefore a pure function of authenticated
    data instead of a self-asserted claim, and the caller can hand
    ``frame_sha256(frame)`` to :func:`plan_resume`.

    Nothing here rewrites evidence. A frame that cannot be rebuilt from the
    snapshot fails closed with :class:`StreamContinuityError`.
    """
    _require_digest(expected_state_sha256, "expected_state_sha256")
    _require_digest(expected_hot_sha256, "expected_hot_sha256")
    if _sha(state_bytes) != expected_state_sha256:
        raise StreamContinuityError("STREAM_STATE_DIGEST_MISMATCH")
    if not stream_name or "/" in stream_name or "\\" in stream_name:
        raise StreamContinuityError("STREAM_NAME_INVALID")
    try:
        state = json.loads(state_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StreamContinuityError("STREAM_STATE_UNREADABLE") from exc
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise StreamContinuityError("STREAM_STATE_SCHEMA_INVALID")
    epoch = state.get("epoch_uuid")
    if not isinstance(epoch, str) or not epoch:
        raise StreamContinuityError("STREAM_EPOCH_UUID_INVALID")
    if state.get("pending") is not None:
        raise StreamContinuityError("STREAM_COMPACTION_NOT_COMMITTED")
    hot_base = state.get("hot_base")
    if type(hot_base) is not int or hot_base < 0:
        raise StreamContinuityError("STREAM_HOT_BASE_INVALID")
    entries = state.get("segments")
    if not isinstance(entries, list):
        raise StreamContinuityError("STREAM_SEGMENTS_INVALID")
    if _sha(hot_bytes) != expected_hot_sha256:
        raise StreamContinuityError("STREAM_HOT_DIGEST_MISMATCH")
    if hot_bytes and hot_bytes[-1:] != b"\n":
        raise StreamContinuityError("STREAM_HOT_INCOMPLETE_RECORD")

    manifest_entries: dict[str, dict] = {}
    if entries:
        if expected_archive_manifest_sha256 is None:
            raise StreamContinuityError("STREAM_ARCHIVE_MANIFEST_UNBOUND")
        _require_digest(
            expected_archive_manifest_sha256, "expected_archive_manifest_sha256"
        )
        manifest_path = archive_dir / "manifest.json"
        signature_path = archive_dir / "manifest.json.sha256"
        if not manifest_path.is_file() or not signature_path.is_file():
            raise StreamContinuityError("STREAM_ARCHIVE_MANIFEST_MISSING")
        if _sha(manifest_path.read_bytes()) != expected_archive_manifest_sha256:
            raise StreamContinuityError("STREAM_ARCHIVE_MANIFEST_DIGEST_MISMATCH")
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_entries = parsed["segments"]
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise StreamContinuityError("STREAM_ARCHIVE_MANIFEST_INVALID") from exc
        if not isinstance(manifest_entries, dict):
            raise StreamContinuityError("STREAM_ARCHIVE_MANIFEST_INVALID")

    root = archive_dir.resolve()
    parts: list[StreamPart] = []
    offset = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise StreamContinuityError("STREAM_SEGMENTS_INVALID")
        if entry.get("start") != offset:
            raise StreamContinuityError("STREAM_SEGMENT_RANGE_GAP")
        end = entry.get("end")
        if type(end) is not int or end <= offset:
            raise StreamContinuityError("STREAM_SEGMENT_RANGE_INVALID")
        if end - offset != entry.get("uncompressed_bytes"):
            raise StreamContinuityError("STREAM_SEGMENT_SIZE_INVALID")
        digest = entry.get("sha256")
        raw_digest = entry.get("raw_sha256")
        _require_digest(digest, "segment.sha256")
        _require_digest(raw_digest, "segment.raw_sha256")
        live = manifest_entries.get(digest)
        if not isinstance(live, dict) or live.get("sha256") != digest:
            raise StreamContinuityError("STREAM_SEGMENT_MANIFEST_MISSING")
        row_count = entry.get("row_count")
        if live.get("row_count") != row_count:
            raise StreamContinuityError("STREAM_SEGMENT_ROW_COUNT_DIVERGED")
        relative = live.get("archive")
        if not isinstance(relative, str):
            raise StreamContinuityError("STREAM_SEGMENT_LOCATION_INVALID")
        path = archive_dir / relative
        if (
            not path.resolve().is_relative_to(root)
            or path.is_symlink()
            or not path.is_file()
        ):
            raise StreamContinuityError("STREAM_SEGMENT_FILE_UNAVAILABLE")
        parts.append(
            StreamPart(
                path=path,
                start=offset,
                end=end,
                sha256=digest,
                raw_sha256=raw_digest,
                archived=True,
                row_count=row_count,
            )
        )
        offset = end

    if offset != hot_base:
        raise StreamContinuityError("STREAM_HOT_BASE_DIVERGED")
    parts.append(
        StreamPart(
            path=hot_path,
            start=offset,
            end=offset + len(hot_bytes),
            sha256=_sha(hot_bytes),
            raw_sha256=_sha(hot_bytes),
            archived=False,
        )
    )
    return StreamFrame(
        stream_id=_sha(f"opip:v1:{stream_name}:{epoch}".encode("ascii")),
        retained_start=0,
        end=offset + len(hot_bytes),
        parts=tuple(parts),
    )

