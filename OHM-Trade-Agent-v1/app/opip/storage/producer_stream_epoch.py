"""Opt-in producer stream epoch and compaction-coordinate ledger.

This deliberately does NOT auto-migrate a populated legacy HOT file or archive.
Callers hold the existing source writer lock while using these functions.
A state file created on an empty stream defines a new, stable coordinate epoch;
legacy transitions require an externally authorized discontinuity receipt.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any, Mapping

from app.opip.storage.bounded_jsonl import sha256_file, write_atomic_lines

SCHEMA = 1
STATE_SUFFIX = ".stream_state_v1.json"


class StreamEpochError(RuntimeError):
    """Producer cannot prove source-coordinate continuity."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def state_path(hot: Path) -> Path:
    return hot.with_name(hot.name + STATE_SUFFIX)


def _save(path: Path, state: Mapping[str, Any]) -> None:
    write_atomic_lines(
        path,
        [json.dumps(state, sort_keys=True, separators=(",", ":"),
                    allow_nan=False).encode("utf-8")],
    )


def load(hot: Path) -> dict[str, Any] | None:
    path = state_path(hot)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StreamEpochError("STREAM_EPOCH_STATE_UNREADABLE") from exc
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA:
        raise StreamEpochError("STREAM_EPOCH_SCHEMA_INVALID")
    if not isinstance(state.get("epoch_uuid"), str):
        raise StreamEpochError("STREAM_EPOCH_UUID_INVALID")
    try:
        uuid.UUID(state["epoch_uuid"])
    except (ValueError, AttributeError) as exc:
        raise StreamEpochError("STREAM_EPOCH_UUID_INVALID") from exc
    if type(state.get("hot_base")) is not int or state["hot_base"] < 0:
        raise StreamEpochError("STREAM_EPOCH_HOT_BASE_INVALID")
    if not isinstance(state.get("segments"), list):
        raise StreamEpochError("STREAM_EPOCH_SEGMENTS_INVALID")
    if state.get("pending") is not None and not isinstance(state["pending"], dict):
        raise StreamEpochError("STREAM_EPOCH_PENDING_INVALID")
    return state


def initialize_empty_locked(hot: Path, archive_dir: Path) -> dict[str, Any]:
    """Create an epoch ONLY for an empty, unarchived stream under its lock."""
    existing = load(hot)
    if existing is not None:
        return existing
    if hot.exists() and hot.stat().st_size:
        raise StreamEpochError("LEGACY_HOT_REQUIRES_AUTHORIZED_CUTOVER")
    if archive_dir.exists() and any(archive_dir.rglob("*.jsonl.gz")):
        raise StreamEpochError("LEGACY_ARCHIVE_REQUIRES_AUTHORIZED_CUTOVER")
    if (archive_dir / "manifest.json").exists():
        raise StreamEpochError("LEGACY_MANIFEST_REQUIRES_AUTHORIZED_CUTOVER")
    state: dict[str, Any] = {
        "schema_version": SCHEMA,
        "epoch_uuid": str(uuid.uuid4()),
        "hot_base": 0,
        "segments": [],
        "pending": None,
    }
    _save(state_path(hot), state)
    return state


def stream_id_for(state: Mapping[str, Any], stream_name: str) -> str:
    if not stream_name or "/" in stream_name or "\\" in stream_name:
        raise StreamEpochError("STREAM_NAME_INVALID")
    return _sha(
        f"opip:v1:{stream_name}:{state['epoch_uuid']}".encode("ascii")
    )


def prepare_compaction_locked(
    hot: Path, *, archive_bytes: bytes, tail_bytes: bytes,
) -> bool:
    """Durably establish the only permitted HOT rewrite before archiving."""
    state = load(hot)
    if state is None:
        return False  # Existing production remains unframed; no auto-cutover.
    if state["pending"] is not None:
        raise StreamEpochError("UNRESOLVED_STREAM_COMPACTION")
    original = hot.read_bytes()
    if not archive_bytes or not tail_bytes or archive_bytes + tail_bytes != original:
        raise StreamEpochError("INVALID_COMPACTION_PARTITION")
    if original[-1:] != b"\n" or archive_bytes[-1:] != b"\n":
        raise StreamEpochError("INVALID_COMPACTION_RECORD_BOUNDARY")
    state["pending"] = {
        "old_hot_base": state["hot_base"],
        "old_hot_bytes": len(original),
        "old_hot_sha256": _sha(original),
        "prefix_bytes": len(archive_bytes),
        "prefix_sha256": _sha(archive_bytes),
        "prefix_rows": archive_bytes.count(b"\n"),
        "tail_bytes": len(tail_bytes),
        "tail_sha256": _sha(tail_bytes),
    }
    _save(state_path(hot), state)
    return True


def _manifest_segment(archive_dir: Path, raw_sha: str, raw_len: int,
                      row_count: int) -> tuple[str, str]:
    """Find a verified archive and its signed-manifest entry.

    The signature file authenticates manifest consistency, not sender identity:
    the authenticated export generation binds these files to the producer.
    """
    manifest_path = archive_dir / "manifest.json"
    signature = archive_dir / "manifest.json.sha256"
    if not manifest_path.is_file() or not signature.is_file():
        raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_MISSING")
    expected = signature.read_text(encoding="utf-8").split()
    if not expected or expected[0] != sha256_file(manifest_path):
        raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_DIGEST_MISMATCH")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest["segments"]
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_INVALID") from exc
    if not isinstance(entries, dict):
        raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_INVALID")
    matches = []
    for digest, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        relative = entry.get("archive")
        if not isinstance(relative, str):
            continue
        path = archive_dir / relative
        try:
            if not path.resolve().is_relative_to(archive_dir.resolve()):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            checksum = path.with_suffix(path.suffix + ".sha256")
            tokens = checksum.read_text(encoding="utf-8").split()
            if not tokens or tokens[0] != digest or sha256_file(path) != digest:
                continue
            with gzip.open(path, "rb") as reader:
                raw = reader.read()
        except (OSError, ValueError, EOFError):
            continue
        if len(raw) == raw_len and _sha(raw) == raw_sha and raw.count(b"\n") == row_count:
            matches.append((digest, relative))
    if len(matches) != 1:
        raise StreamEpochError("STREAM_ARCHIVE_PREFIX_UNPROVEN")
    return matches[0]


def finish_compaction_locked(hot: Path, archive_dir: Path) -> None:
    """Finalize a verified HOT rewrite or reject an ambiguous crash state."""
    state = load(hot)
    if state is None or state["pending"] is None:
        raise StreamEpochError("STREAM_COMPACTION_NOT_PREPARED")
    pending = state["pending"]
    current = hot.read_bytes()
    if len(current) != pending["tail_bytes"] or _sha(current) != pending["tail_sha256"]:
        raise StreamEpochError("STREAM_HOT_REWRITE_UNPROVEN")
    digest, relative = _manifest_segment(
        archive_dir,
        pending["prefix_sha256"],
        pending["prefix_bytes"],
        pending["prefix_rows"],
    )
    start = pending["old_hot_base"]
    if start != state["hot_base"]:
        raise StreamEpochError("STREAM_BASE_CHANGED_DURING_COMPACTION")
    end = start + pending["prefix_bytes"]
    state["segments"].append({
        "archive": relative,
        "sha256": digest,
        "raw_sha256": pending["prefix_sha256"],
        "uncompressed_bytes": pending["prefix_bytes"],
        "row_count": pending["prefix_rows"],
        "start": start,
        "end": end,
    })
    state["hot_base"] = end
    state["pending"] = None
    _save(state_path(hot), state)


def recover_compaction_locked(hot: Path, archive_dir: Path) -> None:
    """Recover only provable states; refuse conflicting or missing evidence."""
    state = load(hot)
    if state is None or state["pending"] is None:
        return
    pending = state["pending"]
    current = hot.read_bytes()
    if len(current) == pending["old_hot_bytes"] and _sha(current) == pending["old_hot_sha256"]:
        # Archival may have finished, but HOT was never changed. Retrying
        # the existing archive-before-delete logic will verify/reuse it.
        state["pending"] = None
        _save(state_path(hot), state)
        return
    if len(current) == pending["tail_bytes"] and _sha(current) == pending["tail_sha256"]:
        finish_compaction_locked(hot, archive_dir)
        return
    raise StreamEpochError("STREAM_PENDING_COMPACTION_DIVERGED")


def snapshot_locked(hot: Path, archive_dir: Path, stream_name: str) -> dict[str, Any]:
    """Return frame metadata while holding the source lock.

    The exporter must bind its canonical JSON digest to its independently
    authenticated transport manifest before consumers trust this result.
    """
    state = load(hot)
    if state is None:
        raise StreamEpochError("STREAM_EPOCH_NOT_ESTABLISHED")
    if state["pending"] is not None:
        raise StreamEpochError("STREAM_COMPACTION_NOT_COMMITTED")
    # Do not emit a frame from stale bookkeeping. The live manifest is the
    # authority for WARM/COLD location after tier migration, and its detached
    # digest must match. The export then authenticates this whole snapshot.
    manifest_entries: dict[str, Any] = {}
    if state["segments"]:
        manifest = archive_dir / "manifest.json"
        signature = manifest.with_suffix(".json.sha256")
        if not manifest.is_file() or not signature.is_file():
            raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_MISSING")
        tokens = signature.read_text(encoding="utf-8").split()
        if not tokens or tokens[0] != sha256_file(manifest):
            raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_DIGEST_MISMATCH")
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_entries = parsed["segments"]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_INVALID") from exc
        if not isinstance(manifest_entries, dict):
            raise StreamEpochError("STREAM_ARCHIVE_MANIFEST_INVALID")
    offset = 0
    segments = []
    for entry in state["segments"]:
        if not isinstance(entry, dict) or entry.get("start") != offset:
            raise StreamEpochError("STREAM_SEGMENT_RANGE_GAP")
        if type(entry.get("end")) is not int or entry["end"] <= offset:
            raise StreamEpochError("STREAM_SEGMENT_RANGE_INVALID")
        if entry["end"] - offset != entry.get("uncompressed_bytes"):
            raise StreamEpochError("STREAM_SEGMENT_SIZE_INVALID")
        sha = entry.get("sha256")
        live = manifest_entries.get(sha)
        if not isinstance(live, dict) or live.get("sha256") != sha:
            raise StreamEpochError("STREAM_SEGMENT_MANIFEST_MISSING")
        if live.get("row_count") != entry.get("row_count"):
            raise StreamEpochError("STREAM_SEGMENT_ROW_COUNT_DIVERGED")
        rel = live.get("archive")
        if not isinstance(rel, str):
            raise StreamEpochError("STREAM_SEGMENT_LOCATION_INVALID")
        path = archive_dir / rel
        if (not path.resolve().is_relative_to(archive_dir.resolve())
                or path.is_symlink() or not path.is_file()):
            raise StreamEpochError("STREAM_SEGMENT_FILE_UNAVAILABLE")
        if sha256_file(path) != sha:
            raise StreamEpochError("STREAM_SEGMENT_DIGEST_MISMATCH")
        segments.append({**entry, "archive": rel})
        offset = entry["end"]
    if offset != state["hot_base"]:
        raise StreamEpochError("STREAM_HOT_BASE_DIVERGED")
    content = hot.read_bytes() if hot.exists() else b""
    if content and content[-1:] != b"\n":
        raise StreamEpochError("STREAM_HOT_INCOMPLETE_RECORD")
    return {
        "schema_version": SCHEMA,
        "stream_name": stream_name,
        "stream_id": stream_id_for(state, stream_name),
        "producer_epoch_uuid": state["epoch_uuid"],
        "retained_start": 0,
        "stream_end": offset + len(content),
        "segments": segments,
        "hot": {
            "start": offset,
            "end": offset + len(content),
            "bytes": len(content),
            "sha256": _sha(content),
        },
    }
