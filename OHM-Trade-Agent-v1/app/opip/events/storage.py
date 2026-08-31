"""Durable, bounded storage for canonical O'Pip events.

HOT storage is bounded JSONL. Before any HOT compaction, removed canonical rows
are written to an immutable gzip archive, checksummed, reopened, parsed and
verified. Only after archive finalization succeeds may HOT be replaced.

The archive mechanics now live in app.opip.storage.bounded_jsonl so that every
O'Pip evidence family shares one proven implementation. Event semantics
(dedupe, revision lineage, persistence stamping, point-in-time queries) remain
here and are unchanged.

This keeps the production footprint bounded without destroying replay evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Iterable

from app.opip.events.contract import IngestOutcome, OPipEvent, require_utc
from app.opip.storage.bounded_jsonl import (
    ArchiveVerification,
    BoundedJsonlArchive,
    encode_row,
    fsync_dir as _fsync_dir,
    parse_json_object_line as _parse_json_object_line,
    read_lines as _read_lines,
    repair_truncated_tail,
    sha256_file as _sha256_file,
    write_atomic_lines as _write_atomic_lines,
)
from app.services.registry_io import registry_lock


logger = logging.getLogger(__name__)

EVENT_DIR = Path("/app/data/opip/events")
EVENT_FILE = EVENT_DIR / "events.jsonl"
DEAD_LETTER_FILE = EVENT_DIR / "event_dead_letter.jsonl"
ARCHIVE_DIR = EVENT_DIR / "archive"
COLD_ARCHIVE_DIR = ARCHIVE_DIR / "cold"
ARCHIVE_MANIFEST_FILE = ARCHIVE_DIR / "manifest.json"
LOCK_FILE = EVENT_DIR / ".events.lock"

EVENTS_MAX_BYTES = 32 * 1024 * 1024
EVENTS_KEEP_LINES = 100_000
DEAD_LETTER_MAX_BYTES = 4 * 1024 * 1024
DEAD_LETTER_KEEP_LINES = 2_000

EVENT_ARCHIVE_PREFIX = "events"


def _parse_event_line(line: bytes) -> OPipEvent:
    return OPipEvent.from_dict(_parse_json_object_line(line))


def _repair_truncated_tail(path: Path, *, event_rows: bool = False) -> None:
    repair_truncated_tail(
        path,
        parse_line=_parse_event_line if event_rows else None,
    )


@dataclass(frozen=True)
class AppendResult:
    outcome: IngestOutcome
    event: OPipEvent | None
    existing_event_id: str | None = None




@dataclass(frozen=True)
class EventWindowRead:
    events: tuple[OPipEvent, ...]
    archive_segments_scanned: int
    archive_segments_truncated: bool
    rows_truncated: bool
    coverage_complete: bool
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class EventStorageStats:
    hot_bytes: int
    hot_lines: int
    warm_archive_bytes: int
    warm_archive_segments: int
    cold_archive_bytes: int
    cold_archive_segments: int
    dead_letter_bytes: int
    manifest_segments: int


class EventStore:
    def __init__(
        self,
        *,
        event_file: Path = EVENT_FILE,
        archive_dir: Path = ARCHIVE_DIR,
        cold_archive_dir: Path | None = None,
        archive_manifest_file: Path | None = None,
        dead_letter_file: Path = DEAD_LETTER_FILE,
        lock_file: Path = LOCK_FILE,
        max_bytes: int = EVENTS_MAX_BYTES,
        keep_lines: int = EVENTS_KEEP_LINES,
    ) -> None:
        self.event_file = event_file
        self.archive_dir = archive_dir
        self.cold_archive_dir = (
            cold_archive_dir if cold_archive_dir is not None else archive_dir / "cold"
        )
        self.archive_manifest_file = (
            archive_manifest_file
            if archive_manifest_file is not None
            else archive_dir / "manifest.json"
        )
        self.dead_letter_file = dead_letter_file
        self.lock_file = lock_file
        self.max_bytes = int(max_bytes)
        self.keep_lines = int(keep_lines)
        if self.max_bytes < 1 or self.keep_lines < 1:
            raise ValueError("event retention limits must be positive")
        self._index_loaded = False
        self._event_ids: set[str] = set()
        self._latest_by_dedupe: dict[str, str] = {}
        self._known_hot_signature: tuple[int, int] | None = None
        self._archive = BoundedJsonlArchive(
            data_file=self.event_file,
            archive_dir=self.archive_dir,
            cold_archive_dir=self.cold_archive_dir,
            manifest_file=self.archive_manifest_file,
            max_bytes=self.max_bytes,
            keep_lines=self.keep_lines,
            archive_prefix=EVENT_ARCHIVE_PREFIX,
            parse_line=_parse_event_line,
            visible_at=lambda event: event.decision_visible_at_utc,
            # Resolved through this module's globals at call time so that the
            # existing archive-failure test, which patches _sha256_file here,
            # continues to exercise the real failure path.
            sha256_file_hook=lambda path: _sha256_file(path),
        )

    def _hot_signature(self) -> tuple[int, int] | None:
        return self._archive.hot_signature()

    def _load_logical_index_locked(self) -> None:
        """Index the complete logical store once, including verified archives.

        Subsequent appends are O(1). If another process changes HOT between
        appends, the file signature invalidates this cache under the same
        cross-process writer lock.
        """
        event_ids: set[str] = set()
        latest_by_dedupe: dict[str, str] = {}
        # iter_events preserves canonical append order across finalized
        # archives followed by HOT. The last observed row for a dedupe key is
        # therefore its current revision even if two writes share a timestamp.
        for event in self.iter_events(include_archive=True):
            event_ids.add(event.event_id)
            latest_by_dedupe[event.dedupe_key] = event.event_id

        self._event_ids = event_ids
        self._latest_by_dedupe = latest_by_dedupe
        self._known_hot_signature = self._hot_signature()
        self._index_loaded = True

    def _ensure_logical_index_locked(self) -> None:
        if not self._index_loaded or self._known_hot_signature != self._hot_signature():
            self._load_logical_index_locked()

    def append(
        self,
        event: OPipEvent,
        *,
        persisted_at: datetime | None = None,
    ) -> AppendResult:
        if (
            event.persisted_at_utc is not None
            or event.decision_visible_at_utc is not None
        ):
            raise ValueError("EventStore.append expects an unpersisted canonical event")
        self.event_file.parent.mkdir(parents=True, exist_ok=True)

        with registry_lock(self.lock_file):
            self._archive.repair_tail()
            self._ensure_logical_index_locked()
            if event.event_id in self._event_ids:
                return AppendResult(
                    outcome=IngestOutcome.DUPLICATE,
                    event=None,
                    existing_event_id=event.event_id,
                )

            previous = self._latest_by_dedupe.get(event.dedupe_key)
            outcome = IngestOutcome.NORMALIZED
            candidate = event
            if previous and previous != event.event_id:
                candidate = replace(event, revision_of=previous)
                outcome = IngestOutcome.REVISION

            stamp = persisted_at or datetime.now(timezone.utc)
            persisted = candidate.with_persistence(
                require_utc(stamp, field_name="persisted_at")
            )
            self._archive.append_encoded_locked(encode_row(persisted.to_dict()))

            self._event_ids.add(persisted.event_id)
            self._latest_by_dedupe[persisted.dedupe_key] = persisted.event_id

            # Archive failure must never delete HOT evidence. The event itself
            # has already been durably appended; retention is a separate
            # fail-safe maintenance concern.
            try:
                self._archive_before_compact_locked()
            except Exception:
                logger.exception(
                    "O'Pip event archive/compaction failed; HOT evidence preserved"
                )

            self._known_hot_signature = self._hot_signature()
            return AppendResult(
                outcome=outcome,
                event=persisted,
                existing_event_id=previous,
            )

    def _verify_archive_file(self, archive: Path, *, tier: str) -> ArchiveVerification:
        return self._archive.verify_archive_file(archive, tier=tier)

    def _update_archive_manifest_locked(
        self,
        verification: ArchiveVerification,
    ) -> None:
        self._archive.update_manifest_locked(verification)

    def _tier_warm_archives_locked(
        self,
        *,
        now: datetime,
        cold_after_days: int = 30,
    ) -> int:
        return self._archive.tier_warm_archives_locked(
            now=require_utc(now, field_name="now"),
            cold_after_days=cold_after_days,
        )

    def _archive_before_compact_locked(self) -> Path | None:
        return self._archive.compact_locked(cold_after_days=30)

    def maintain_lifecycle(
        self,
        *,
        now: datetime | None = None,
        cold_after_days: int = 30,
    ) -> int:
        """Move verified WARM segments to local COLD; never purge automatically."""
        current = require_utc(now or datetime.now(timezone.utc), field_name="now")
        with registry_lock(self.lock_file):
            return self._tier_warm_archives_locked(
                now=current,
                cold_after_days=cold_after_days,
            )

    def storage_stats(self) -> EventStorageStats:
        stats = self._archive.stats()
        return EventStorageStats(
            hot_bytes=stats.hot_bytes,
            hot_lines=stats.hot_lines,
            warm_archive_bytes=stats.warm_archive_bytes,
            warm_archive_segments=stats.warm_archive_segments,
            cold_archive_bytes=stats.cold_archive_bytes,
            cold_archive_segments=stats.cold_archive_segments,
            dead_letter_bytes=(
                self.dead_letter_file.stat().st_size
                if self.dead_letter_file.exists()
                else 0
            ),
            manifest_segments=stats.manifest_segments,
        )

    def record_dead_letter(
        self,
        *,
        provider: str,
        reason: str,
        observed_at: datetime,
        provider_event_id: str | None = None,
        payload_hash: str | None = None,
    ) -> None:
        row = {
            "provider": str(provider),
            "reason": str(reason)[:300],
            "observed_at_utc": require_utc(
                observed_at,
                field_name="observed_at",
            ).isoformat(),
            "provider_event_id": provider_event_id,
            "payload_hash": payload_hash,
        }
        encoded = (
            json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("utf-8")
        lock = self.dead_letter_file.parent / f".{self.dead_letter_file.name}.lock"
        with registry_lock(lock):
            self.dead_letter_file.parent.mkdir(parents=True, exist_ok=True)
            _repair_truncated_tail(self.dead_letter_file)
            with self.dead_letter_file.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if (
                self.dead_letter_file.stat().st_size > DEAD_LETTER_MAX_BYTES
                or len(_read_lines(self.dead_letter_file)) > DEAD_LETTER_KEEP_LINES
            ):
                lines = _read_lines(self.dead_letter_file)[-DEAD_LETTER_KEEP_LINES:]
                _write_atomic_lines(self.dead_letter_file, lines)

    def _iter_archive_events(self) -> Iterable[OPipEvent]:
        return self._archive.iter_archive_rows()

    def iter_events(self, *, include_archive: bool = True) -> Iterable[OPipEvent]:
        seen: set[str] = set()
        if include_archive:
            for event in self._iter_archive_events():
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                yield event

        for event in self._archive.iter_hot_rows():
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            yield event

    def read_visible_window(
        self,
        *,
        start: datetime,
        through: datetime,
        max_archive_segments: int = 16,
        max_rows: int = 4_000,
    ) -> EventWindowRead:
        """Read HOT plus only archive segments overlapping a visibility window.

        This is the bounded Sequence 3 path. It uses the verified archive
        manifest as an index so recent evidence remains visible after HOT
        compaction without decompressing every historical archive each cycle.
        """
        start = require_utc(start, field_name="start")
        through = require_utc(through, field_name="through")
        if start > through:
            raise ValueError("start cannot be after through")
        selection = self._archive.archive_paths_for_visible_window(
            start=start,
            through=through,
            max_segments=max(1, int(max_archive_segments)),
        )
        if int(max_rows) < 1:
            raise ValueError("max_rows must be positive")
        seen: set[str] = set()
        rows: list[OPipEvent] = []
        raw_truncated = False
        # Preserve deterministic chronological input order, but bound memory.
        # If the ceiling is reached the caller receives incomplete coverage and
        # therefore may escalate observed risk but must not de-escalate.
        warnings = list(selection.warnings)
        archive_read_complete = True
        for archive in selection.paths:
            try:
                source = self._archive.iter_archive_rows_from_paths((archive,), strict=True)
                for event in source:
                    if event.event_id in seen:
                        continue
                    seen.add(event.event_id)
                    rows.append(event)
                    if len(rows) > int(max_rows):
                        raw_truncated = True
                        rows = rows[-int(max_rows):]
            except Exception:
                logger.exception("O'Pip recent archive unreadable; risk coverage degraded: %s", archive)
                archive_read_complete = False
                warnings.append("ARCHIVE_SEGMENT_UNREADABLE")
        hot_read_complete = True
        try:
            for event in self._archive.iter_hot_rows(skip_malformed=False):
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                rows.append(event)
                if len(rows) > int(max_rows):
                    raw_truncated = True
                    # Keep the newest bounded tail. This may omit older active
                    # evidence, so coverage is explicitly incomplete.
                    rows = rows[-int(max_rows):]
        except Exception:
            logger.exception("O'Pip HOT event evidence unreadable; risk coverage degraded")
            hot_read_complete = False
            warnings.append("HOT_EVENT_ROW_UNREADABLE")
        if raw_truncated:
            warnings.append("EVENT_RAW_ROW_CEILING_REACHED")
        return EventWindowRead(
            events=tuple(rows),
            archive_segments_scanned=len(selection.paths),
            archive_segments_truncated=selection.truncated,
            rows_truncated=raw_truncated,
            coverage_complete=selection.complete and archive_read_complete and hot_read_complete and not raw_truncated,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _matches_asset(event: OPipEvent, asset_id: str) -> bool:
        wanted = str(asset_id or "").strip().casefold()
        if not wanted:
            return True

        # AMBIGUOUS/UNKNOWN evidence is retained for audit and later resolution,
        # but must never silently attach to a canonical asset query merely
        # because a ticker string happens to match.
        if event.identity.mapping_status.value != "UNIQUE":
            return False

        values = (
            event.identity.canonical_asset_id,
            event.identity.canonical_asset_name,
            event.identity.source_symbol,
            event.identity.provider_asset_id,
        )
        return any(str(value or "").strip().casefold() == wanted for value in values)

    def get_visible_events(
        self,
        *,
        asset_id: str,
        decision_at: datetime,
        include_expired: bool = False,
        include_archive: bool = True,
    ) -> tuple[OPipEvent, ...]:
        cutoff = require_utc(decision_at, field_name="decision_at")

        # Fold revisions BEFORE asset matching. If a later visible revision
        # invalidates or changes identity, an older revision must not remain
        # attached merely because it used to map to the requested asset.
        latest_by_dedupe: dict[str, OPipEvent] = {}
        for event in self.iter_events(include_archive=include_archive):
            visible = event.decision_visible_at_utc
            if visible is None or visible > cutoff:
                continue
            latest_by_dedupe[event.dedupe_key] = event

        result: list[OPipEvent] = []
        for event in latest_by_dedupe.values():
            if not include_expired and event.expires_at_utc is not None:
                if event.expires_at_utc < cutoff:
                    continue
            if not self._matches_asset(event, asset_id):
                continue
            result.append(event)

        result.sort(
            key=lambda item: (
                item.decision_visible_at_utc
                or datetime.min.replace(tzinfo=timezone.utc),
                item.source_event_time_utc,
                item.provider,
                item.event_id,
            )
        )
        return tuple(result)

    def replay_events(
        self,
        *,
        through: datetime | None = None,
    ) -> tuple[OPipEvent, ...]:
        cutoff = (
            require_utc(through, field_name="through") if through is not None else None
        )
        # iter_events is the canonical append order (verified archives then
        # HOT). Preserve that order rather than re-sorting equal timestamps,
        # which could invert revision lineage during deterministic replay.
        return tuple(
            event
            for event in self.iter_events(include_archive=True)
            if event.decision_visible_at_utc is not None
            and (cutoff is None or event.decision_visible_at_utc <= cutoff)
        )
