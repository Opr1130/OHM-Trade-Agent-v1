"""Durable, bounded storage for canonical O'Pip events.

HOT storage is bounded JSONL. Before any HOT compaction, removed canonical rows
are written to an immutable gzip archive, checksummed, reopened, parsed and
verified. Only after archive finalization succeeds may HOT be replaced.

This keeps the production footprint bounded without destroying replay evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from app.opip.events.contract import IngestOutcome, OPipEvent, require_utc
from app.services.registry_io import registry_lock


logger = logging.getLogger(__name__)

EVENT_DIR = Path("/app/data/opip/events")
EVENT_FILE = EVENT_DIR / "events.jsonl"
DEAD_LETTER_FILE = EVENT_DIR / "event_dead_letter.jsonl"
ARCHIVE_DIR = EVENT_DIR / "archive"
LOCK_FILE = EVENT_DIR / ".events.lock"

EVENTS_MAX_BYTES = 32 * 1024 * 1024
EVENTS_KEEP_LINES = 100_000
# Compact to 80% of the line cap rather than exactly the cap. Otherwise a
# full HOT file would create one tiny gzip archive on every subsequent append.
HOT_COMPACTION_FRACTION = 0.80
DEAD_LETTER_MAX_BYTES = 4 * 1024 * 1024
DEAD_LETTER_KEEP_LINES = 2_000


@dataclass(frozen=True)
class AppendResult:
    outcome: IngestOutcome
    event: OPipEvent | None
    existing_event_id: str | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _repair_truncated_tail(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        return
    with path.open("rb") as reader:
        reader.seek(-1, os.SEEK_END)
        if reader.read(1) == b"\n":
            return
    with path.open("ab") as handle:
        handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_lines(path: Path) -> list[bytes]:
    if not path.exists():
        return []
    return [line for line in path.read_bytes().splitlines(keepends=True) if line.strip()]


def _parse_event_line(line: bytes) -> OPipEvent:
    payload = json.loads(line.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("event JSONL row must be an object")
    return OPipEvent.from_dict(payload)


def _write_atomic_lines(path: Path, lines: Iterable[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for line in lines:
                handle.write(line if line.endswith(b"\n") else line + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        _fsync_dir(path.parent)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


class EventStore:
    def __init__(
        self,
        *,
        event_file: Path = EVENT_FILE,
        archive_dir: Path = ARCHIVE_DIR,
        dead_letter_file: Path = DEAD_LETTER_FILE,
        lock_file: Path = LOCK_FILE,
        max_bytes: int = EVENTS_MAX_BYTES,
        keep_lines: int = EVENTS_KEEP_LINES,
    ) -> None:
        self.event_file = event_file
        self.archive_dir = archive_dir
        self.dead_letter_file = dead_letter_file
        self.lock_file = lock_file
        self.max_bytes = int(max_bytes)
        self.keep_lines = int(keep_lines)
        if self.max_bytes < 1 or self.keep_lines < 1:
            raise ValueError("event retention limits must be positive")

    def _hot_index(self) -> tuple[set[str], dict[str, str]]:
        event_ids: set[str] = set()
        latest_by_dedupe: dict[str, str] = {}
        for line in _read_lines(self.event_file):
            try:
                event = _parse_event_line(line)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            event_ids.add(event.event_id)
            latest_by_dedupe[event.dedupe_key] = event.event_id
        return event_ids, latest_by_dedupe

    def append(
        self,
        event: OPipEvent,
        *,
        persisted_at: datetime | None = None,
    ) -> AppendResult:
        if event.persisted_at_utc is not None or event.decision_visible_at_utc is not None:
            raise ValueError("EventStore.append expects an unpersisted canonical event")
        self.event_file.parent.mkdir(parents=True, exist_ok=True)

        with registry_lock(self.lock_file):
            _repair_truncated_tail(self.event_file)
            event_ids, latest_by_dedupe = self._hot_index()
            if event.event_id in event_ids:
                return AppendResult(
                    outcome=IngestOutcome.DUPLICATE,
                    event=None,
                    existing_event_id=event.event_id,
                )

            previous = latest_by_dedupe.get(event.dedupe_key)
            outcome = IngestOutcome.NORMALIZED
            candidate = event
            if previous and previous != event.event_id:
                candidate = replace(event, revision_of=previous)
                outcome = IngestOutcome.REVISION

            stamp = persisted_at or datetime.now(timezone.utc)
            persisted = candidate.with_persistence(
                require_utc(stamp, field_name="persisted_at")
            )
            encoded = (
                json.dumps(
                    persisted.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")

            with self.event_file.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

            # Archive failure must never delete HOT evidence. The event itself
            # has already been durably appended; retention is a separate
            # fail-safe maintenance concern.
            try:
                self._archive_before_compact_locked()
            except Exception:
                logger.exception(
                    "O'Pip event archive/compaction failed; HOT evidence preserved"
                )

            return AppendResult(
                outcome=outcome,
                event=persisted,
                existing_event_id=previous,
            )

    def _archive_before_compact_locked(self) -> Path | None:
        if not self.event_file.exists():
            return None
        current_size = self.event_file.stat().st_size
        lines = _read_lines(self.event_file)
        if current_size <= self.max_bytes and len(lines) <= self.keep_lines:
            return None
        if len(lines) <= 1:
            return None

        if len(lines) > self.keep_lines:
            keep_count = max(
                1,
                min(
                    self.keep_lines,
                    int(self.keep_lines * HOT_COMPACTION_FRACTION),
                ),
            )
        else:
            # Byte pressure with fewer than keep_lines: archive the oldest half
            # so the next append does not immediately trigger another rotation.
            keep_count = max(1, len(lines) // 2)

        archive_lines = lines[:-keep_count]
        hot_lines = lines[-keep_count:]
        if not archive_lines:
            return None

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        content_digest = hashlib.sha256(b"".join(archive_lines)).hexdigest()[:12]
        archive = self.archive_dir / (
            f"events-{stamp}-{content_digest}.jsonl.gz"
        )
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        archive_tmp = archive.with_name(f".{archive.name}.tmp")
        checksum_tmp = checksum.with_name(f".{checksum.name}.tmp")

        try:
            with gzip.open(archive_tmp, "wb") as handle:
                for line in archive_lines:
                    handle.write(line if line.endswith(b"\n") else line + b"\n")

            # Make the compressed bytes durable before verification/finalization.
            with archive_tmp.open("rb") as handle:
                os.fsync(handle.fileno())

            digest = _sha256_file(archive_tmp)
            verified_rows = 0
            with gzip.open(archive_tmp, "rb") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    _parse_event_line(line)
                    verified_rows += 1
            if verified_rows != len(archive_lines):
                raise RuntimeError(
                    "event archive row-count verification failed "
                    f"expected={len(archive_lines)} actual={verified_rows}"
                )

            checksum_tmp.write_text(
                f"{digest}  {archive.name}\n",
                encoding="utf-8",
            )
            with checksum_tmp.open("rb") as handle:
                os.fsync(handle.fileno())

            os.replace(archive_tmp, archive)
            os.replace(checksum_tmp, checksum)
            _fsync_dir(self.archive_dir)

            # Only after an immutable, verified archive and checksum are final
            # may HOT evidence be compacted.
            _write_atomic_lines(self.event_file, hot_lines)
            return archive
        except Exception:
            archive_tmp.unlink(missing_ok=True)
            checksum_tmp.unlink(missing_ok=True)
            # If archive finalization succeeded but HOT compaction failed,
            # leaving duplicated rows in HOT is safe and replay de-duplicates
            # them by event_id.
            raise

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
        if not self.archive_dir.exists():
            return
        for archive in sorted(self.archive_dir.glob("events-*.jsonl.gz")):
            checksum = archive.with_suffix(archive.suffix + ".sha256")
            if not checksum.exists():
                logger.warning("Skipping O'Pip event archive without checksum: %s", archive)
                continue
            try:
                expected = checksum.read_text(encoding="utf-8").split()[0]
                if _sha256_file(archive) != expected:
                    logger.error("Skipping O'Pip event archive checksum mismatch: %s", archive)
                    continue
                with gzip.open(archive, "rb") as handle:
                    for line in handle:
                        if line.strip():
                            yield _parse_event_line(line)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                logger.exception("Skipping unreadable O'Pip event archive: %s", archive)

    def iter_events(self, *, include_archive: bool = True) -> Iterable[OPipEvent]:
        seen: set[str] = set()
        if include_archive:
            for event in self._iter_archive_events():
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                yield event

        for line in _read_lines(self.event_file):
            try:
                event = _parse_event_line(line)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            yield event

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
        return any(
            str(value or "").strip().casefold() == wanted
            for value in values
        )

    def get_visible_events(
        self,
        *,
        asset_id: str,
        decision_at: datetime,
        include_expired: bool = False,
        include_archive: bool = True,
    ) -> tuple[OPipEvent, ...]:
        cutoff = require_utc(decision_at, field_name="decision_at")
        result: list[OPipEvent] = []
        for event in self.iter_events(include_archive=include_archive):
            visible = event.decision_visible_at_utc
            if visible is None or visible > cutoff:
                continue
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
            require_utc(through, field_name="through")
            if through is not None
            else None
        )
        result = [
            event
            for event in self.iter_events(include_archive=True)
            if event.decision_visible_at_utc is not None
            and (cutoff is None or event.decision_visible_at_utc <= cutoff)
        ]
        result.sort(
            key=lambda item: (
                item.decision_visible_at_utc
                or datetime.min.replace(tzinfo=timezone.utc),
                item.provider,
                item.dedupe_key,
                item.event_id,
            )
        )
        return tuple(result)
