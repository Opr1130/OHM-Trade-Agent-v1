"""The canonical, already-written evidence this projection is allowed to read.

Registration is explicit. Ingestion never walks the whole data tree looking for
something to parse: a source exists in the projection only because this module
declared it, with a stable ``source_id`` and a record kind whose parser is
tested. An undeclared file is not evidence the Control Center may show.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Iterable

#: Append-only scan attempt lifecycle ledger owned by the core process.
KIND_ATTEMPT_EVENT = "SCAN_ATTEMPT_EVENT"
#: One acquisition batch written by the isolated scan worker.
KIND_SCAN_SNAPSHOT = "SCAN_SNAPSHOT"
#: Paired V1/V2 comparison row written by the promotion path.
KIND_SCORING_COMPARISON = "SCORING_COMPARISON"
#: Worker scoring-provenance document written beside the manifest.
KIND_WORKER_STATUS = "WORKER_STATUS"

JSONL_KINDS = frozenset({KIND_ATTEMPT_EVENT, KIND_SCAN_SNAPSHOT, KIND_SCORING_COMPARISON})
DOCUMENT_KINDS = frozenset({KIND_WORKER_STATUS})

#: Bounded file enumeration. A projection must not walk an unbounded tree.
MAX_DISCOVERED_BATCHES = 200


@dataclass(frozen=True)
class SourceDescriptor:
    """One registered canonical source."""

    source_id: str
    record_kind: str
    display_name: str
    path: Path
    attempt_id: str | None = None
    manifest_path: Path | None = None
    #: A snapshot batch is only ingested once its manifest exists: the worker
    #: writes snapshots, then the manifest, so an absent manifest means the
    #: attempt is still acquiring.
    requires_manifest: bool = False

    @property
    def is_jsonl(self) -> bool:
        return self.record_kind in JSONL_KINDS


def _utc(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def scan_state_dir(settings: Any) -> Path:
    """Resolve the scan evidence root, following the scanner's own setting."""
    explicit = str(getattr(settings, "opip_analytics_scan_state_dir", "") or "").strip()
    if explicit:
        return Path(explicit)
    return Path(str(getattr(settings, "opip_scan_state_dir", "/app/data/scan")))


def comparison_ledger_path(settings: Any) -> Path:
    configured = str(
        getattr(settings, "opip_analytics_scoring_comparison_path", "") or ""
    ).strip()
    return Path(
        configured or "/app/data/opip/qualification/scoring_comparison.jsonl"
    )


def analytics_db_path(settings: Any) -> Path:
    configured = str(getattr(settings, "opip_analytics_db_path", "") or "").strip()
    return Path(configured or "/app/data/opip/analytics/opip_analytics.db")


def attempt_dir_name(attempt_id: str) -> str:
    safe = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(attempt_id)
    )
    return safe.strip("-.") or "attempt"


def static_sources(settings: Any) -> list[SourceDescriptor]:
    """The two ledgers that exist independently of any single attempt."""
    root = scan_state_dir(settings)
    return [
        SourceDescriptor(
            source_id="scan_attempt_ledger",
            record_kind=KIND_ATTEMPT_EVENT,
            display_name="Scan attempt ledger (core-owned, append-only)",
            path=root / "scan_attempts.jsonl",
        ),
        SourceDescriptor(
            source_id="scoring_comparison_ledger",
            record_kind=KIND_SCORING_COMPARISON,
            display_name="Paired V1/V2 scoring comparison ledger",
            path=comparison_ledger_path(settings),
        ),
    ]


def discover_batch_sources(
    settings: Any, *, limit: int = MAX_DISCOVERED_BATCHES
) -> list[SourceDescriptor]:
    """Enumerate acquisition batches, newest first, bounded by ``limit``.

    Enumeration order is by ``snapshots.jsonl`` mtime so a fresh attempt is
    always considered even when the staging tree has grown large. Older
    batches remain fully ingestable; they are simply not re-listed forever.
    """
    staging = scan_state_dir(settings) / "staging"
    if not staging.is_dir():
        return []
    found: list[tuple[float, Path]] = []
    try:
        for child in staging.iterdir():
            if not child.is_dir():
                continue
            snapshots = child / "snapshots.jsonl"
            try:
                if not snapshots.is_file():
                    continue
                found.append((snapshots.stat().st_mtime, child))
            except OSError:
                continue
    except OSError:
        return []
    found.sort(key=lambda item: item[0], reverse=True)
    limit = max(1, min(int(limit), MAX_DISCOVERED_BATCHES))
    descriptors: list[SourceDescriptor] = []
    for _, directory in found[:limit]:
        name = directory.name
        descriptors.append(
            SourceDescriptor(
                source_id=f"scan_snapshots:{name}",
                record_kind=KIND_SCAN_SNAPSHOT,
                display_name=f"Acquisition snapshots for attempt {name}",
                path=directory / "snapshots.jsonl",
                attempt_id=name,
                manifest_path=directory / "manifest.json",
                requires_manifest=True,
            )
        )
        status = directory / "worker_status.json"
        if status.is_file():
            descriptors.append(
                SourceDescriptor(
                    source_id=f"worker_status:{name}",
                    record_kind=KIND_WORKER_STATUS,
                    display_name=f"Worker scoring provenance for attempt {name}",
                    path=status,
                    attempt_id=name,
                )
            )
    return descriptors


def all_sources(
    settings: Any, *, limit: int = MAX_DISCOVERED_BATCHES
) -> list[SourceDescriptor]:
    return static_sources(settings) + discover_batch_sources(settings, limit=limit)


def register_sources(
    conn: Any, descriptors: Iterable[SourceDescriptor], *, now: datetime | None = None
) -> int:
    """Upsert source registration rows. Returns how many rows were written.

    Registration is metadata only: it never claims a file was ingested.
    """
    stamp = _utc(now)
    written = 0
    for descriptor in descriptors:
        conn.execute(
            """
            INSERT INTO ingestion_sources(
                source_id, record_kind, display_name, path,
                manifest_required, registered_at_utc, schema_version
            ) VALUES(?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source_id) DO UPDATE SET
                record_kind = excluded.record_kind,
                display_name = excluded.display_name,
                path = excluded.path,
                manifest_required = excluded.manifest_required
            """,
            (
                descriptor.source_id,
                descriptor.record_kind,
                descriptor.display_name,
                str(descriptor.path),
                1 if descriptor.requires_manifest else 0,
                stamp,
            ),
        )
        written += 1
    return written


def file_identity(path: Path) -> str | None:
    """Return a device/inode identity string, or ``None`` when unmeasurable.

    Inode plus offset alone does not establish trust across a replacement or a
    re-export, so ingestion also compares the recorded generation and the
    checkpoint's file size before it resumes.
    """
    try:
        stat = os.stat(str(path))
    except OSError:
        return None
    return f"{stat.st_dev}:{stat.st_ino}"

