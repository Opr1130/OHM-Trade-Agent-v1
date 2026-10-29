"""Scan result manifest with integrity validation (R06/R07).

Two-phase completion: the manifest is written atomically, then a sentinel
carrying its SHA-256 and row count is written atomically. A consumer accepts a
manifest only when both the sentinel and the checksum agree, so a torn or
truncated manifest can never be mistaken for a full scan.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
import tempfile


SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
SENTINEL_NAME = "COMPLETE"


class ManifestError(RuntimeError):
    """Manifest could not be trusted."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


class ManifestIncomplete(ManifestError):
    """No completion sentinel: the producer never finished (or crashed)."""


class ManifestCorrupt(ManifestError):
    """Sentinel exists but does not describe the manifest on disk."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_manifest(
    directory: Path | str,
    payload: Mapping[str, Any],
    *,
    complete: bool,
    row_count: int | None = None,
) -> dict[str, Any]:
    """Persist ``payload`` and, only afterwards, its completion sentinel."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body.setdefault("schema_version", SCHEMA_VERSION)
    body["complete"] = bool(complete)
    body["written_at"] = _now().isoformat()

    manifest_path = target / MANIFEST_NAME
    _write_atomic(
        manifest_path,
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        ),
    )
    if row_count is None:
        snapshots = body.get("snapshots")
        row_count = len(snapshots) if isinstance(snapshots, list) else 0
    sentinel = {
        "schema_version": SCHEMA_VERSION,
        "sha256": sha256_path(manifest_path),
        "rows": int(row_count),
        "complete": bool(complete),
        "written_at": body["written_at"],
    }
    _write_atomic(
        target / SENTINEL_NAME,
        json.dumps(sentinel, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )
    return body


def read_manifest(directory: Path | str) -> dict[str, Any]:
    """Return a verified manifest or raise a typed ``ManifestError``."""
    target = Path(directory)
    sentinel_path = target / SENTINEL_NAME
    manifest_path = target / MANIFEST_NAME
    if not sentinel_path.exists() or not manifest_path.exists():
        raise ManifestIncomplete("COMPLETION_SENTINEL_MISSING")
    try:
        sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestCorrupt("SENTINEL_UNREADABLE") from exc
    if not isinstance(sentinel, dict):
        raise ManifestCorrupt("SENTINEL_NOT_AN_OBJECT")
    actual = sha256_path(manifest_path)
    if str(sentinel.get("sha256") or "") != actual:
        raise ManifestCorrupt("MANIFEST_CHECKSUM_MISMATCH")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestCorrupt("MANIFEST_UNREADABLE") from exc
    if not isinstance(manifest, dict):
        raise ManifestCorrupt("MANIFEST_NOT_AN_OBJECT")
    if bool(manifest.get("complete")) != bool(sentinel.get("complete")):
        raise ManifestCorrupt("COMPLETENESS_DISAGREEMENT")
    return manifest


def is_complete(manifest: Mapping[str, Any]) -> bool:
    """Return whether the manifest describes a genuinely complete scan."""
    if not bool(manifest.get("complete")):
        return False
    coverage = manifest.get("coverage")
    if not isinstance(coverage, Mapping):
        return False
    try:
        requested = int(coverage.get("requested"))
        analyzed = int(coverage.get("analyzed"))
        failed = int(coverage.get("failed"))
        skipped = int(coverage.get("skipped"))
    except (TypeError, ValueError):
        return False
    return requested > 0 and analyzed == requested and failed == 0 and skipped == 0
