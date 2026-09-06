"""Fail-closed repair of legacy bounded-archive metadata on the learning replica.

Production evidence export remains copy-only. Some historical qualification
archive trees predate the canonical ``manifest.json`` + signature sidecar. The
learning worker receives those trees through the SSH export contract, validates
the complete tree checksum before promotion, and may then reconstruct only the
missing *replica* manifest from immutable gzip segments whose own checksum
sidecars and JSONL contents verify.

Existing manifests are never replaced or repaired here. Any checksum, path,
manifest, or index ambiguity raises and keeps learning compute fail-closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.opip.decision.store import (
    funnel_events_archive,
    scan_summaries_archive,
    screening_evaluations_archive,
)
from app.opip.storage.bounded_jsonl import (
    BoundedJsonlArchive,
    sha256_file,
    write_atomic_lines,
)


QUALIFICATION_RELATIVE_DIR = Path("opip/qualification")


def _segment_tier(archive: BoundedJsonlArchive, path: Path) -> str:
    parent = path.parent.resolve()
    if parent == archive.archive_dir.resolve():
        return "WARM"
    if parent == archive.cold_archive_dir.resolve():
        return "COLD"
    raise RuntimeError(f"unexpected replica archive segment path: {path}")


def _verified_segments(archive: BoundedJsonlArchive):
    if not archive.archive_dir.exists():
        return []

    segments = tuple(sorted(archive.archive_dir.rglob(archive.archive_glob)))
    sidecars = {
        path.resolve()
        for path in archive.archive_dir.rglob(
            f"{archive.archive_prefix}-*.jsonl.gz.sha256"
        )
    }
    expected_sidecars = {
        path.with_suffix(path.suffix + ".sha256").resolve() for path in segments
    }
    if sidecars != expected_sidecars:
        raise RuntimeError("replica archive checksum sidecar set is inconsistent")

    verified = []
    seen_digests: dict[str, str] = {}
    for path in segments:
        verification = archive.verify_archive_file(
            path,
            tier=_segment_tier(archive, path),
        )
        prior = seen_digests.get(verification.sha256)
        if prior is not None and prior != verification.archive:
            raise RuntimeError("duplicate replica archive digest has multiple paths")
        seen_digests[verification.sha256] = verification.archive
        verified.append(verification)
    return verified


def _reconstruct_missing_replica_manifest(archive: BoundedJsonlArchive) -> str:
    """Reconstruct only an absent replica manifest from verified segments."""
    if archive.manifest_file.exists():
        rebuilt = archive.rebuild_window_index_from_verified_manifest_locked()
        if not rebuilt:
            raise RuntimeError("existing replica archive manifest is incomplete")
        return "EXISTING_VERIFIED"

    # A signature without its canonical manifest is lineage evidence, not an
    # invitation to replace the missing manifest. Preserve the ambiguity and
    # fail closed rather than synthesizing a different document under it.
    if archive.manifest_signature_file.exists():
        raise RuntimeError("replica archive manifest missing with signature present")

    verified = _verified_segments(archive)
    if not verified:
        if not archive.ensure_window_index_locked():
            raise RuntimeError("empty replica archive could not be certified")
        return "EMPTY_CERTIFIED"

    recorded = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "updated_at_utc": recorded,
        "replica_reconstructed_from_verified_segments": True,
        "segments": {},
    }
    entries = manifest["segments"]
    assert isinstance(entries, dict)
    for verification in verified:
        entries[verification.sha256] = {
            "archive": verification.archive,
            "tier": verification.tier,
            "sha256": verification.sha256,
            "row_count": verification.row_count,
            "bytes": verification.bytes,
            "first_visible_at_utc": verification.first_visible_at_utc,
            "last_visible_at_utc": verification.last_visible_at_utc,
            "verified_at_utc": recorded,
            "replica_reconstructed": True,
        }

    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    write_atomic_lines(
        archive.manifest_file,
        [
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        ],
    )
    digest = sha256_file(archive.manifest_file)
    write_atomic_lines(
        archive.manifest_signature_file,
        [f"{digest}  {archive.manifest_file.name}\n".encode("utf-8")],
    )

    if not archive.rebuild_window_index_from_verified_manifest_locked():
        raise RuntimeError("reconstructed replica archive index is incomplete")
    return "RECONSTRUCTED_VERIFIED"


def reconcile_qualification_replica_archives(
    data_root: Path = Path("/app/data"),
) -> dict[str, str]:
    """Repair legacy qualification replica metadata without touching production."""
    qualification = data_root / QUALIFICATION_RELATIVE_DIR
    archives = {
        "screening": screening_evaluations_archive(
            qualification / "screening_evaluations.jsonl"
        ),
        "funnel": funnel_events_archive(qualification / "funnel_events.jsonl"),
        "summaries": scan_summaries_archive(
            qualification / "scan_summaries.jsonl"
        ),
    }
    return {
        name: _reconstruct_missing_replica_manifest(archive)
        for name, archive in archives.items()
    }
