"""Export-time proof that a qualification archive was canonically empty.

Production export remains copy-only of durable files. After that copy, the
export tree may gain ``empty_export_attestation_v1.json`` at the archive-dir
root (sibling of ``window_index_v1``, never inside it). The file is bound by
the existing tree SHA. Replica repair may recertify a leftover incomplete
derived index only when this proof is present and consistent with replica
canonical files. Missing, stale, or mismatched proof stays fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from app.opip.storage.bounded_jsonl import BoundedJsonlArchive, write_atomic_lines


EMPTY_EXPORT_ATTESTATION_FILENAME = "empty_export_attestation_v1.json"
EMPTY_EXPORT_ATTESTATION_KIND = "empty_export_attestation_v1"
EMPTY_EXPORT_ATTESTATION_SCHEMA_VERSION = 1
_PRODUCTION_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "archive_prefix",
        "hot_bytes",
        "segment_count",
        "manifest_present",
        "signature_present",
        "exported_at_utc",
        "production_deployed_sha",
    }
)


@dataclass(frozen=True)
class CanonicalArchiveFacts:
    hot_bytes: int
    segment_count: int
    manifest_present: bool
    signature_present: bool

    @property
    def is_empty(self) -> bool:
        return (
            self.hot_bytes == 0
            and self.segment_count == 0
            and not self.manifest_present
            and not self.signature_present
        )


def empty_export_attestation_path(archive: BoundedJsonlArchive):
    return archive.archive_dir / EMPTY_EXPORT_ATTESTATION_FILENAME


def inspect_canonical_archive_files(
    archive: BoundedJsonlArchive,
) -> CanonicalArchiveFacts:
    """Inspect HOT JSONL, gzip segments, manifest, and signature only."""
    try:
        hot_bytes = (
            archive.data_file.stat().st_size if archive.data_file.exists() else 0
        )
    except OSError as exc:
        raise RuntimeError("canonical hot JSONL is unreadable") from exc
    segment_count = 0
    if archive.archive_dir.exists():
        try:
            segment_count = sum(
                1 for _ in archive.archive_dir.rglob(archive.archive_glob)
            )
        except OSError as exc:
            raise RuntimeError("canonical archive segments are unreadable") from exc
    return CanonicalArchiveFacts(
        hot_bytes=hot_bytes,
        segment_count=segment_count,
        manifest_present=archive.manifest_file.exists(),
        signature_present=archive.manifest_signature_file.exists(),
    )


def leftover_index_blocks_empty_attestation(archive: BoundedJsonlArchive) -> bool:
    """True when leftover derived index still records prior canonical lineage.

    Incomplete zero-coverage leftover and a previously certified empty index
    do not block attestation. Extra index files, prior-manifest coverage,
    shards, or an unreadable/ambiguous index do.
    """
    if not archive.window_index_dir.exists():
        return False
    if archive._window_index_state_is_orphan_incomplete_empty_without_manifest():
        return False
    if archive._window_index_state_proves_empty_archive_without_manifest():
        return False
    return True


def empty_export_attestation_eligible(archive: BoundedJsonlArchive) -> bool:
    """True only from canonical files plus a leftover index that is not lineage."""
    try:
        facts = inspect_canonical_archive_files(archive)
    except RuntimeError:
        return False
    if not facts.is_empty:
        return False
    if leftover_index_blocks_empty_attestation(archive):
        return False
    return True


def build_empty_export_attestation_payload(
    archive: BoundedJsonlArchive,
    *,
    exported_at_utc: str,
    production_deployed_sha: str = "",
) -> dict[str, Any]:
    facts = inspect_canonical_archive_files(archive)
    if not facts.is_empty:
        raise RuntimeError("refusing empty export attestation; canonical files remain")
    if leftover_index_blocks_empty_attestation(archive):
        raise RuntimeError(
            "refusing empty export attestation; leftover index records prior lineage"
        )
    if production_deployed_sha and _PRODUCTION_SHA_RE.fullmatch(
        production_deployed_sha
    ) is None:
        raise RuntimeError("empty export attestation production SHA is invalid")
    if BoundedJsonlArchive._parse_manifest_time(exported_at_utc) is None:
        raise RuntimeError("empty export attestation exported_at_utc is invalid")
    return {
        "schema_version": EMPTY_EXPORT_ATTESTATION_SCHEMA_VERSION,
        "kind": EMPTY_EXPORT_ATTESTATION_KIND,
        "archive_prefix": archive.archive_prefix,
        "hot_bytes": 0,
        "segment_count": 0,
        "manifest_present": False,
        "signature_present": False,
        "exported_at_utc": exported_at_utc,
        "production_deployed_sha": production_deployed_sha,
    }


def write_empty_export_attestation(
    archive: BoundedJsonlArchive,
    *,
    exported_at_utc: str,
    production_deployed_sha: str = "",
):
    """Write attestation into the archive dir (export tree / tests only)."""
    payload = build_empty_export_attestation_payload(
        archive,
        exported_at_utc=exported_at_utc,
        production_deployed_sha=production_deployed_sha,
    )
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    path = empty_export_attestation_path(archive)
    write_atomic_lines(
        path,
        [
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        ],
    )
    return path


def verify_replica_empty_export_attestation(
    archive: BoundedJsonlArchive,
) -> dict[str, Any]:
    """Require hashed export-time empty proof consistent with replica files."""
    path = empty_export_attestation_path(archive)
    if not path.exists():
        raise RuntimeError("empty export attestation is missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("empty export attestation is unreadable") from exc
    if not isinstance(raw, dict) or set(raw) != _REQUIRED_KEYS:
        raise RuntimeError("empty export attestation keys are unexpected")
    if (
        type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != EMPTY_EXPORT_ATTESTATION_SCHEMA_VERSION
    ):
        raise RuntimeError("empty export attestation schema is unexpected")
    if raw.get("kind") != EMPTY_EXPORT_ATTESTATION_KIND:
        raise RuntimeError("empty export attestation kind is unexpected")
    if raw.get("archive_prefix") != archive.archive_prefix:
        raise RuntimeError("empty export attestation archive prefix is mismatched")
    if leftover_index_blocks_empty_attestation(archive):
        raise RuntimeError(
            "empty export attestation conflicts with leftover index lineage"
        )
    facts = inspect_canonical_archive_files(archive)
    if type(raw.get("hot_bytes")) is not int or raw.get("hot_bytes") != facts.hot_bytes:
        raise RuntimeError("empty export attestation hot_bytes is mismatched")
    if (
        type(raw.get("segment_count")) is not int
        or raw.get("segment_count") != facts.segment_count
    ):
        raise RuntimeError("empty export attestation segment_count is mismatched")
    if raw.get("manifest_present") is not facts.manifest_present:
        raise RuntimeError("empty export attestation manifest_present is mismatched")
    if raw.get("signature_present") is not facts.signature_present:
        raise RuntimeError("empty export attestation signature_present is mismatched")
    if not facts.is_empty:
        raise RuntimeError(
            "empty export attestation is stale; canonical files are present"
        )
    production_sha = raw.get("production_deployed_sha")
    if type(production_sha) is not str:
        raise RuntimeError("empty export attestation production SHA is invalid")
    if production_sha and _PRODUCTION_SHA_RE.fullmatch(production_sha) is None:
        raise RuntimeError("empty export attestation production SHA is invalid")
    exported_at = raw.get("exported_at_utc")
    if not isinstance(exported_at, str):
        raise RuntimeError("empty export attestation exported_at_utc is invalid")
    if BoundedJsonlArchive._parse_manifest_time(exported_at) is None:
        raise RuntimeError("empty export attestation exported_at_utc is invalid")
    return raw
