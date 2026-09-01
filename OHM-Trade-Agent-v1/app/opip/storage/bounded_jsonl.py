"""Shared bounded, atomic, corruption-aware JSONL persistence for O'Pip.

This module is the single archive implementation for O'Pip durable evidence.
It was factored out of the proven Sequence 2 event store so that Sequence 3
risk assessments and T0 attribution reuse exactly the same durability
semantics rather than growing a second, subtly different archive.

Guarantees preserved from Sequence 2:

* HOT is bounded JSONL, appended with fsync.
* Before any HOT compaction, removed rows are written to an immutable gzip
  archive, checksummed, reopened, reparsed, row-count verified and recorded in
  a manifest. Only then may HOT be replaced.
* WARM is promoted to local COLD only after the COLD segment is final,
  checksummed, reparsed and represented in the manifest.
* A truncated final row is quarantined for forensics, never silently dropped.
* Archive failure never deletes HOT evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Iterable, Iterator


logger = logging.getLogger(__name__)

# Compact to 80% of the line cap rather than exactly the cap. Otherwise a full
# HOT file would create one tiny gzip archive on every subsequent append.
HOT_COMPACTION_FRACTION = 0.80


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_dir(path: Path) -> None:
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


def reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON numeric token {token}")


def parse_json_object_line(line: bytes) -> dict[str, Any]:
    payload = json.loads(
        line.decode("utf-8"),
        parse_constant=reject_json_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("JSONL row must be an object")
    return payload


def read_lines(path: Path) -> list[bytes]:
    if not path.exists():
        return []
    return [line for line in path.read_bytes().splitlines(keepends=True) if line.strip()]


def write_atomic_lines(path: Path, lines: Iterable[bytes]) -> None:
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
        fsync_dir(path.parent)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def encode_row(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def repair_truncated_tail(
    path: Path,
    *,
    parse_line: Callable[[bytes], Any] | None = None,
) -> None:
    """Repair only the final interrupted JSONL write, preserving evidence."""
    if not path.exists() or path.stat().st_size <= 0:
        return

    raw = path.read_bytes()
    if raw.endswith(b"\n"):
        return

    parser = parse_line or parse_json_object_line
    last_newline = raw.rfind(b"\n")
    prefix_end = last_newline + 1 if last_newline >= 0 else 0
    tail = raw[prefix_end:]
    try:
        parser(tail + b"\n")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = path.with_name(f"{path.name}.truncated-{stamp}.bin")
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{quarantine.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(tail)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, quarantine)
            with path.open("r+b") as handle:
                handle.truncate(prefix_end)
                handle.flush()
                os.fsync(handle.fileno())
            fsync_dir(path.parent)
            logger.critical(
                "Quarantined malformed O'Pip JSONL tail at %s to %s",
                path,
                quarantine,
            )
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return

    # The final row was complete JSON but missed only its newline terminator.
    with path.open("ab") as handle:
        handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True)
class ArchiveVerification:
    archive: str
    tier: str
    sha256: str
    row_count: int
    bytes: int
    first_visible_at_utc: str | None
    last_visible_at_utc: str | None




@dataclass(frozen=True)
class ArchiveWindowSelection:
    paths: tuple[Path, ...]
    complete: bool
    truncated: bool
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class BoundedStorageStats:
    hot_bytes: int
    hot_lines: int
    warm_archive_bytes: int
    warm_archive_segments: int
    cold_archive_bytes: int
    cold_archive_segments: int
    manifest_segments: int


class BoundedJsonlArchive:
    """Bounded HOT JSONL with verified WARM and local COLD gzip archives.

    Callers own their own cross-process lock. Every method whose name ends in
    ``_locked`` assumes that lock is already held.
    """

    def __init__(
        self,
        *,
        data_file: Path,
        archive_dir: Path,
        max_bytes: int,
        keep_lines: int,
        archive_prefix: str,
        parse_line: Callable[[bytes], Any],
        cold_archive_dir: Path | None = None,
        manifest_file: Path | None = None,
        visible_at: Callable[[Any], datetime | None] | None = None,
        sha256_file_hook: Callable[[Path], str] | None = None,
    ) -> None:
        self.data_file = data_file
        self.archive_dir = archive_dir
        self.cold_archive_dir = (
            cold_archive_dir if cold_archive_dir is not None else archive_dir / "cold"
        )
        self.manifest_file = (
            manifest_file if manifest_file is not None else archive_dir / "manifest.json"
        )
        self.max_bytes = int(max_bytes)
        self.keep_lines = int(keep_lines)
        if self.max_bytes < 1 or self.keep_lines < 1:
            raise ValueError("retention limits must be positive")
        self.archive_prefix = str(archive_prefix)
        self.parse_line = parse_line
        self.visible_at = visible_at
        # Injectable so that callers can simulate archive verification failure
        # in tests without reaching into this module's globals.
        self._sha256_file = sha256_file_hook or sha256_file

    @property
    def archive_glob(self) -> str:
        return f"{self.archive_prefix}-*.jsonl.gz"

    def repair_tail(self) -> None:
        repair_truncated_tail(self.data_file, parse_line=self.parse_line)

    def hot_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.data_file.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def append_encoded_locked(self, encoded: bytes) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        with self.data_file.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def append_encoded_many_locked(self, rows: Iterable[bytes]) -> int:
        """Append a batch durably with one open/flush/fsync cycle."""
        pending = list(rows)
        if not pending:
            return 0
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        with self.data_file.open("ab") as handle:
            for encoded in pending:
                handle.write(encoded if encoded.endswith(b"\n") else encoded + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return len(pending)

    def verify_archive_file(self, archive: Path, *, tier: str) -> ArchiveVerification:
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        if not checksum.exists():
            raise RuntimeError(f"missing archive checksum for {archive}")
        tokens = checksum.read_text(encoding="utf-8").split()
        if not tokens:
            raise RuntimeError(f"empty archive checksum for {archive}")
        expected = tokens[0]
        actual = self._sha256_file(archive)
        if actual != expected:
            raise RuntimeError(f"archive checksum mismatch for {archive}")

        rows = 0
        visible_times: list[datetime] = []
        with gzip.open(archive, "rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = self.parse_line(line)
                rows += 1
                if self.visible_at is not None:
                    stamp = self.visible_at(row)
                    if stamp is not None:
                        visible_times.append(stamp)
        if rows <= 0:
            raise RuntimeError(f"archive contained no canonical rows: {archive}")

        return ArchiveVerification(
            archive=str(archive.relative_to(self.archive_dir)),
            tier=tier,
            sha256=actual,
            row_count=rows,
            bytes=archive.stat().st_size,
            first_visible_at_utc=(
                min(visible_times).isoformat() if visible_times else None
            ),
            last_visible_at_utc=(
                max(visible_times).isoformat() if visible_times else None
            ),
        )

    def update_manifest_locked(self, verification: ArchiveVerification) -> None:
        payload: dict[str, Any] = {}
        if self.manifest_file.exists():
            try:
                raw = json.loads(self.manifest_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    payload = raw
            except (OSError, ValueError, json.JSONDecodeError):
                payload = {}
        segments = payload.get("segments")
        if not isinstance(segments, dict):
            segments = {}
        segments[verification.sha256] = {
            "archive": verification.archive,
            "tier": verification.tier,
            "sha256": verification.sha256,
            "row_count": verification.row_count,
            "bytes": verification.bytes,
            "first_visible_at_utc": verification.first_visible_at_utc,
            "last_visible_at_utc": verification.last_visible_at_utc,
            "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        payload = {
            "schema_version": 1,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "segments": segments,
        }
        write_atomic_lines(
            self.manifest_file,
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

    @staticmethod
    def _parse_manifest_time(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)

    def archive_paths_for_visible_window(
        self,
        *,
        start: datetime,
        through: datetime,
        max_segments: int,
    ) -> ArchiveWindowSelection:
        """Select only verified archive segments whose visibility range overlaps.

        The manifest is the bounded lookup index. If it is unavailable or a
        selected path is missing, callers receive incomplete coverage rather
        than silently treating missing evidence as absence of risk.
        """
        if max_segments < 1:
            raise ValueError("max_segments must be positive")
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        if through.tzinfo is None or through.utcoffset() is None:
            raise ValueError("through must be timezone-aware")
        start = start.astimezone(timezone.utc)
        through = through.astimezone(timezone.utc)
        if start > through:
            raise ValueError("start cannot be after through")

        # No archive directory means there is no archived evidence to miss.
        if not self.archive_dir.exists():
            return ArchiveWindowSelection(paths=(), complete=True, truncated=False)

        archive_files = tuple(self.archive_dir.rglob(self.archive_glob))
        if not archive_files:
            return ArchiveWindowSelection(paths=(), complete=True, truncated=False)

        warnings: list[str] = []
        try:
            raw = json.loads(self.manifest_file.read_text(encoding="utf-8"))
            segments = raw.get("segments") if isinstance(raw, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            segments = None
        if not isinstance(segments, dict):
            return ArchiveWindowSelection(
                paths=(),
                complete=False,
                truncated=False,
                warnings=("ARCHIVE_MANIFEST_UNAVAILABLE",),
            )

        candidates: list[tuple[datetime, datetime, Path]] = []
        complete = True
        for row in segments.values():
            if not isinstance(row, dict):
                complete = False
                warnings.append("ARCHIVE_MANIFEST_ROW_INVALID")
                continue
            first = self._parse_manifest_time(row.get("first_visible_at_utc"))
            last = self._parse_manifest_time(row.get("last_visible_at_utc"))
            rel = str(row.get("archive") or "").strip()
            if first is None or last is None or not rel:
                complete = False
                warnings.append("ARCHIVE_MANIFEST_RANGE_MISSING")
                continue
            if last < start or first > through:
                continue
            candidate = (self.archive_dir / rel).resolve()
            try:
                candidate.relative_to(self.archive_dir.resolve())
            except ValueError:
                complete = False
                warnings.append("ARCHIVE_MANIFEST_PATH_INVALID")
                continue
            if not candidate.exists():
                complete = False
                warnings.append("ARCHIVE_SEGMENT_MISSING")
                continue
            candidates.append((first, last, candidate))

        # Prefer newest overlapping segments if a hard cap is reached, then
        # restore chronological order for deterministic revision folding.
        candidates.sort(key=lambda item: (item[1], item[0], str(item[2])))
        truncated = len(candidates) > max_segments
        if truncated:
            complete = False
            warnings.append("ARCHIVE_SEGMENT_CEILING_REACHED")
            candidates = candidates[-max_segments:]
        candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
        return ArchiveWindowSelection(
            paths=tuple(item[2] for item in candidates),
            complete=complete,
            truncated=truncated,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def iter_archive_rows_from_paths(
        self, paths: Iterable[Path], *, strict: bool = False
    ) -> Iterator[Any]:
        """Replay explicit archive paths with checksum verification.

        ``strict`` is used by protection-oriented callers: unreadable evidence
        must surface as incomplete coverage rather than looking like no event.
        The default remains fail-soft for historical/audit traversal.
        """
        for archive in paths:
            checksum = archive.with_suffix(archive.suffix + ".sha256")
            if not checksum.exists():
                if strict:
                    raise RuntimeError(f"missing archive checksum for {archive}")
                logger.warning("Skipping O'Pip archive without checksum: %s", archive)
                continue
            try:
                tokens = checksum.read_text(encoding="utf-8").split()
                if not tokens:
                    raise RuntimeError(f"empty archive checksum for {archive}")
                expected = tokens[0]
                if self._sha256_file(archive) != expected:
                    raise RuntimeError(f"archive checksum mismatch for {archive}")
                with gzip.open(archive, "rb") as handle:
                    for line in handle:
                        if line.strip():
                            yield self.parse_line(line)
            except (OSError, RuntimeError, ValueError, KeyError, IndexError, json.JSONDecodeError):
                if strict:
                    raise
                logger.exception("Skipping unreadable O'Pip archive: %s", archive)

    def tier_warm_archives_locked(
        self,
        *,
        now: datetime,
        cold_after_days: int = 30,
    ) -> int:
        cutoff = now.timestamp() - (max(1, int(cold_after_days)) * 86400)
        moved = 0
        for archive in sorted(self.archive_dir.glob(self.archive_glob)):
            if archive.stat().st_mtime > cutoff:
                continue
            source_verification = self.verify_archive_file(archive, tier="WARM")
            source_checksum = archive.with_suffix(archive.suffix + ".sha256")
            archive_time = datetime.fromtimestamp(
                archive.stat().st_mtime,
                tz=timezone.utc,
            )
            destination_parent = (
                self.cold_archive_dir
                / archive_time.strftime("%Y")
                / archive_time.strftime("%m")
            )
            destination_parent.mkdir(parents=True, exist_ok=True)
            final_segment = destination_parent / f"{archive.name}.segment"
            final_archive = final_segment / archive.name

            if final_segment.exists():
                # A prior crash may have completed the COLD copy but failed
                # before removing WARM. Verify before treating it as
                # authoritative.
                verification = self.verify_archive_file(final_archive, tier="COLD")
                if verification.sha256 != source_verification.sha256:
                    raise RuntimeError(f"cold archive collision for {archive.name}")
                self.update_manifest_locked(verification)
                archive.unlink(missing_ok=True)
                source_checksum.unlink(missing_ok=True)
                fsync_dir(self.archive_dir)
                moved += 1
                continue

            temp_segment = Path(
                tempfile.mkdtemp(dir=destination_parent, prefix=f".{archive.name}.")
            )
            try:
                temp_archive = temp_segment / archive.name
                temp_checksum = temp_archive.with_suffix(
                    temp_archive.suffix + ".sha256"
                )
                shutil.copy2(archive, temp_archive)
                shutil.copy2(source_checksum, temp_checksum)
                with temp_archive.open("r+b") as handle:
                    os.fsync(handle.fileno())
                with temp_checksum.open("r+b") as handle:
                    os.fsync(handle.fileno())

                copied = self.verify_archive_file(temp_archive, tier="COLD")
                if copied.sha256 != source_verification.sha256:
                    raise RuntimeError(f"cold archive copy mismatch for {archive.name}")

                # One directory rename atomically publishes archive+checksum.
                os.replace(temp_segment, final_segment)
                fsync_dir(destination_parent)
                verification = self.verify_archive_file(final_archive, tier="COLD")
                self.update_manifest_locked(verification)

                # WARM is removed only after the COLD segment is final,
                # checksummed, reparsed and represented in the manifest.
                archive.unlink(missing_ok=True)
                source_checksum.unlink(missing_ok=True)
                fsync_dir(self.archive_dir)
                moved += 1
            except Exception:
                if temp_segment.exists():
                    shutil.rmtree(temp_segment, ignore_errors=True)
                raise
        return moved

    def compact_locked(self, *, cold_after_days: int = 30) -> Path | None:
        if not self.data_file.exists():
            return None
        current_size = self.data_file.stat().st_size
        lines = read_lines(self.data_file)
        if current_size <= self.max_bytes and len(lines) <= self.keep_lines:
            return None
        if len(lines) <= 1:
            return None

        if len(lines) > self.keep_lines:
            keep_count = max(
                1,
                min(self.keep_lines, int(self.keep_lines * HOT_COMPACTION_FRACTION)),
            )
        else:
            # Byte pressure with fewer than keep_lines: archive the oldest half
            # so the next append does not immediately rotate again.
            keep_count = max(1, len(lines) // 2)

        archive_lines = lines[:-keep_count]
        hot_lines = lines[-keep_count:]
        if not archive_lines:
            return None

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        content_digest = hashlib.sha256(b"".join(archive_lines)).hexdigest()[:12]

        # A process can die after publishing and manifesting an archive but
        # before replacing HOT.  On restart the same HOT prefix must reuse the
        # verified segment; otherwise every retry creates another immutable
        # copy and archive replay double-counts the evidence.
        for existing in sorted(
            self.archive_dir.glob(
                f"{self.archive_prefix}-*-{content_digest}.jsonl.gz"
            )
        ):
            try:
                verification = self.verify_archive_file(existing, tier="WARM")
                with gzip.open(existing, "rb") as handle:
                    existing_lines = [
                        line for line in handle if line.strip()
                    ]
            except Exception:
                logger.exception(
                    "Ignoring unusable O'Pip retry archive candidate: %s",
                    existing,
                )
                continue
            if existing_lines != [
                line if line.endswith(b"\n") else line + b"\n"
                for line in archive_lines
            ]:
                continue

            # From this point the existing segment is authoritative.  Any
            # manifest/HOT failure must propagate; falling through would mint
            # a duplicate segment for the same evidence on every retry.
            self.update_manifest_locked(verification)
            write_atomic_lines(self.data_file, hot_lines)
            try:
                self.tier_warm_archives_locked(
                    now=datetime.now(timezone.utc),
                    cold_after_days=cold_after_days,
                )
            except Exception:
                logger.exception(
                    "O'Pip WARM-to-COLD archive maintenance failed open"
                )
            return existing

        archive = self.archive_dir / (
            f"{self.archive_prefix}-{stamp}-{content_digest}.jsonl.gz"
        )
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        archive_tmp = archive.with_name(f".{archive.name}.tmp")
        checksum_tmp = checksum.with_name(f".{checksum.name}.tmp")

        try:
            with gzip.open(archive_tmp, "wb") as handle:
                for line in archive_lines:
                    handle.write(line if line.endswith(b"\n") else line + b"\n")

            # Make compressed bytes durable before verification/finalization.
            with archive_tmp.open("r+b") as handle:
                os.fsync(handle.fileno())

            digest = self._sha256_file(archive_tmp)
            verified_rows = 0
            with gzip.open(archive_tmp, "rb") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    self.parse_line(line)
                    verified_rows += 1
            if verified_rows != len(archive_lines):
                raise RuntimeError(
                    "archive row-count verification failed "
                    f"expected={len(archive_lines)} actual={verified_rows}"
                )

            checksum_tmp.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
            with checksum_tmp.open("r+b") as handle:
                os.fsync(handle.fileno())

            os.replace(archive_tmp, archive)
            os.replace(checksum_tmp, checksum)
            fsync_dir(self.archive_dir)

            verification = self.verify_archive_file(archive, tier="WARM")
            self.update_manifest_locked(verification)

            # Only after an immutable, verified archive, checksum and manifest
            # entry are final may HOT evidence be compacted.
            write_atomic_lines(self.data_file, hot_lines)
            try:
                self.tier_warm_archives_locked(
                    now=datetime.now(timezone.utc),
                    cold_after_days=cold_after_days,
                )
            except Exception:
                logger.exception("O'Pip WARM-to-COLD archive maintenance failed open")
            return archive
        except Exception:
            archive_tmp.unlink(missing_ok=True)
            checksum_tmp.unlink(missing_ok=True)
            # If archive finalization succeeded but HOT compaction failed,
            # duplicated rows in HOT are safe: replay de-duplicates them.
            raise

    def iter_archive_rows(self) -> Iterator[Any]:
        if not self.archive_dir.exists():
            return
        seen_checksums: set[str] = set()
        for archive in sorted(self.archive_dir.rglob(self.archive_glob)):
            checksum = archive.with_suffix(archive.suffix + ".sha256")
            if not checksum.exists():
                logger.warning("Skipping O'Pip archive without checksum: %s", archive)
                continue
            try:
                expected = checksum.read_text(encoding="utf-8").split()[0]
                if self._sha256_file(archive) != expected:
                    logger.error(
                        "Skipping O'Pip archive checksum mismatch: %s",
                        archive,
                    )
                    continue
                if expected in seen_checksums:
                    logger.warning(
                        "Skipping duplicate O'Pip archive content: %s",
                        archive,
                    )
                    continue
                seen_checksums.add(expected)
                with gzip.open(archive, "rb") as handle:
                    for line in handle:
                        if line.strip():
                            yield self.parse_line(line)
            except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError):
                logger.exception("Skipping unreadable O'Pip archive: %s", archive)

    def iter_hot_rows(self, *, skip_malformed: bool = True) -> Iterator[Any]:
        for line in read_lines(self.data_file):
            try:
                yield self.parse_line(line)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                if skip_malformed:
                    continue
                raise

    def stats(self) -> BoundedStorageStats:
        warm = list(self.archive_dir.glob(self.archive_glob))
        cold = (
            list(self.cold_archive_dir.rglob(self.archive_glob))
            if self.cold_archive_dir.exists()
            else []
        )
        manifest_segments = 0
        if self.manifest_file.exists():
            try:
                payload = json.loads(self.manifest_file.read_text(encoding="utf-8"))
                segments = payload.get("segments") if isinstance(payload, dict) else None
                if isinstance(segments, dict):
                    manifest_segments = len(segments)
            except (OSError, ValueError, json.JSONDecodeError):
                manifest_segments = 0
        return BoundedStorageStats(
            hot_bytes=(self.data_file.stat().st_size if self.data_file.exists() else 0),
            hot_lines=len(read_lines(self.data_file)),
            warm_archive_bytes=sum(item.stat().st_size for item in warm),
            warm_archive_segments=len(warm),
            cold_archive_bytes=sum(item.stat().st_size for item in cold),
            cold_archive_segments=len(cold),
            manifest_segments=manifest_segments,
        )
