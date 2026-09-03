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
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Iterable, Iterator, Mapping


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

    @property
    def window_index_dir(self) -> Path:
        return self.archive_dir / "window_index_v1"

    @property
    def window_index_state_file(self) -> Path:
        return self.window_index_dir / "state.json"

    @property
    def manifest_signature_file(self) -> Path:
        return self.manifest_file.with_suffix(self.manifest_file.suffix + ".sha256")

    def _read_manifest_signature(self) -> str | None:
        try:
            tokens = self.manifest_signature_file.read_text(
                encoding="utf-8"
            ).split()
        except OSError:
            return None
        if not tokens:
            return None
        digest = str(tokens[0]).lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            return None
        return digest

    def _write_manifest_signature_locked(self, digest: str) -> None:
        write_atomic_lines(
            self.manifest_signature_file,
            [f"{digest}  {self.manifest_file.name}\n".encode("utf-8")],
        )

    def _verified_manifest_signature_locked(self) -> str:
        if not self.manifest_file.exists():
            raise RuntimeError("archive manifest is unavailable")
        actual = sha256_file(self.manifest_file)
        expected = self._read_manifest_signature()
        if expected is None:
            # One-time legacy bootstrap under the source writer lock.
            self._write_manifest_signature_locked(actual)
            return actual
        if actual != expected:
            raise RuntimeError("archive manifest signature mismatch")
        return actual

    @staticmethod
    def _window_day_keys(start: datetime, through: datetime) -> tuple[str, ...]:
        current = start.astimezone(timezone.utc).date()
        final = through.astimezone(timezone.utc).date()
        keys: list[str] = []
        while current <= final:
            keys.append(current.isoformat())
            current += timedelta(days=1)
        return tuple(keys)

    @staticmethod
    def _verification_days(
        verification: ArchiveVerification,
    ) -> tuple[str, ...]:
        first = BoundedJsonlArchive._parse_manifest_time(
            verification.first_visible_at_utc
        )
        last = BoundedJsonlArchive._parse_manifest_time(
            verification.last_visible_at_utc
        )
        if first is None or last is None or first > last:
            return ()
        return BoundedJsonlArchive._window_day_keys(first, last)

    def _window_index_state_payload(
        self,
        *,
        complete: bool,
        coverage_start_day: str | None = None,
        coverage_through_day: str | None = None,
        shard_sha256: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        manifest_present = self.manifest_file.exists()
        if manifest_present:
            stat = self.manifest_file.stat()
            manifest_size = stat.st_size
            manifest_mtime_ns = stat.st_mtime_ns
            manifest_sha256 = self._read_manifest_signature()
            if manifest_sha256 is None:
                manifest_sha256 = self._verified_manifest_signature_locked()
        else:
            manifest_size = 0
            manifest_mtime_ns = 0
            manifest_sha256 = ""
        coverage_day_count = 0
        if coverage_start_day and coverage_through_day:
            try:
                coverage_day_count = max(
                    0,
                    (
                        datetime.fromisoformat(coverage_through_day).date()
                        - datetime.fromisoformat(coverage_start_day).date()
                    ).days
                    + 1,
                )
            except ValueError:
                coverage_day_count = 0
        return {
            "schema_version": 1,
            "manifest_present": manifest_present,
            "manifest_size": manifest_size,
            "manifest_mtime_ns": manifest_mtime_ns,
            "manifest_sha256": manifest_sha256,
            "complete": bool(complete),
            "coverage_start_day": coverage_start_day,
            "coverage_through_day": coverage_through_day,
            "coverage_day_count": coverage_day_count,
            "shard_sha256": dict(sorted((shard_sha256 or {}).items())),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    def _write_window_index_state_locked(
        self,
        *,
        complete: bool,
        coverage_start_day: str | None = None,
        coverage_through_day: str | None = None,
        shard_sha256: Mapping[str, str] | None = None,
    ) -> None:
        self.window_index_dir.mkdir(parents=True, exist_ok=True)
        write_atomic_lines(
            self.window_index_state_file,
            [
                json.dumps(
                    self._window_index_state_payload(
                        complete=complete,
                        coverage_start_day=coverage_start_day,
                        coverage_through_day=coverage_through_day,
                        shard_sha256=shard_sha256,
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            ],
        )

    def _update_window_index_verification_locked(
        self,
        verification: ArchiveVerification,
    ) -> tuple[bool, str | None, str | None, dict[str, str]]:
        days = self._verification_days(verification)
        if not days:
            return False, None, None, {}

        state: dict[str, Any] = {}
        if self.window_index_state_file.exists():
            try:
                raw_state = json.loads(
                    self.window_index_state_file.read_text(encoding="utf-8")
                )
                if isinstance(raw_state, dict):
                    state = raw_state
            except (OSError, ValueError, json.JSONDecodeError):
                return False, None, None, {}

        old_start = str(state.get("coverage_start_day") or "") or None
        old_through = str(state.get("coverage_through_day") or "") or None
        raw_digests = state.get("shard_sha256")
        shard_digests = (
            {
                str(day): str(digest)
                for day, digest in raw_digests.items()
                if str(day) and str(digest)
            }
            if isinstance(raw_digests, dict)
            else {}
        )

        new_start = min([day for day in days] + ([old_start] if old_start else []))
        new_through = max(
            [day for day in days] + ([old_through] if old_through else [])
        )

        entry = {
            "archive": verification.archive,
            "tier": verification.tier,
            "sha256": verification.sha256,
            "row_count": verification.row_count,
            "bytes": verification.bytes,
            "first_visible_at_utc": verification.first_visible_at_utc,
            "last_visible_at_utc": verification.last_visible_at_utc,
        }
        self.window_index_dir.mkdir(parents=True, exist_ok=True)

        def _day_start(day: str) -> datetime:
            return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)

        # Only create/check the newly extended edges of the dense coverage.
        # Historical certified days are not rehashed on every archive rotation.
        extension_days: list[str] = []
        if old_start and old_through:
            if new_start < old_start:
                extension_days.extend(
                    self._window_day_keys(
                        _day_start(new_start),
                        _day_start(old_start) - timedelta(days=1),
                    )
                )
            if new_through > old_through:
                extension_days.extend(
                    self._window_day_keys(
                        _day_start(old_through) + timedelta(days=1),
                        _day_start(new_through),
                    )
                )
        else:
            extension_days.extend(
                self._window_day_keys(
                    _day_start(new_start),
                    _day_start(new_through),
                )
            )

        for day in extension_days:
            shard = self.window_index_dir / f"{day}.json"
            if shard.exists():
                # An uncertified pre-existing shard is ambiguous. Keep the
                # index incomplete so ensure_window_index_locked() rebuilds it
                # from the canonical master manifest on the next writer pass.
                return False, old_start, old_through, shard_digests
            write_atomic_lines(
                shard,
                [
                    json.dumps(
                        {
                            "schema_version": 1,
                            "day": day,
                            "segments": {},
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    + b"\n"
                ],
            )
            shard_digests[day] = sha256_file(shard)

        for day in days:
            shard = self.window_index_dir / f"{day}.json"
            expected = shard_digests.get(day)
            if expected is None:
                return False, old_start, old_through, shard_digests
            try:
                actual_digest = sha256_file(shard)
            except OSError:
                return False, old_start, old_through, shard_digests
            if actual_digest != expected:
                return False, old_start, old_through, shard_digests
            try:
                raw = json.loads(shard.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                return False, old_start, old_through, shard_digests
            if not isinstance(raw, dict):
                return False, old_start, old_through, shard_digests
            segments = raw.get("segments")
            if not isinstance(segments, dict):
                return False, old_start, old_through, shard_digests
            segments[verification.sha256] = entry
            write_atomic_lines(
                shard,
                [
                    json.dumps(
                        {
                            "schema_version": 1,
                            "day": day,
                            "segments": segments,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    + b"\n"
                ],
            )
            shard_digests[day] = sha256_file(shard)

        return True, new_start, new_through, shard_digests


    def _manifest_verification_from_row(
        self,
        row: Mapping[str, Any],
    ) -> ArchiveVerification:
        return ArchiveVerification(
            archive=str(row.get("archive") or ""),
            tier=str(row.get("tier") or ""),
            sha256=str(row.get("sha256") or ""),
            row_count=int(row.get("row_count") or 0),
            bytes=int(row.get("bytes") or 0),
            first_visible_at_utc=(
                str(row.get("first_visible_at_utc"))
                if row.get("first_visible_at_utc") is not None
                else None
            ),
            last_visible_at_utc=(
                str(row.get("last_visible_at_utc"))
                if row.get("last_visible_at_utc") is not None
                else None
            ),
        )

    def _repair_legacy_manifest_verification_locked(
        self,
        segment_key: str,
        row: Mapping[str, Any],
        verification: ArchiveVerification,
    ) -> ArchiveVerification:
        """Re-verify one legacy segment to recover missing visibility metadata."""
        rel = str(verification.archive or "").strip()
        tier = str(verification.tier or "").strip()
        expected_sha = str(verification.sha256 or segment_key).strip()
        if not rel or not tier or not expected_sha:
            raise RuntimeError("legacy archive manifest identity is incomplete")

        candidate = (self.archive_dir / rel).resolve()
        try:
            candidate.relative_to(self.archive_dir.resolve())
        except ValueError as exc:
            raise RuntimeError("legacy archive manifest path is invalid") from exc

        repaired = self.verify_archive_file(candidate, tier=tier)
        if repaired.sha256 != expected_sha or str(segment_key) != repaired.sha256:
            raise RuntimeError("legacy archive manifest checksum identity mismatch")
        if verification.row_count > 0 and repaired.row_count != verification.row_count:
            raise RuntimeError("legacy archive manifest row-count mismatch")
        if verification.bytes > 0 and repaired.bytes != verification.bytes:
            raise RuntimeError("legacy archive manifest byte-count mismatch")
        return repaired


    def ensure_window_index_locked(
        self,
        *,
        force_rebuild: bool = False,
        repair_legacy_visibility: bool = False,
    ) -> bool:
        """Backfill/repair the day-sharded manifest index once per manifest version.

        This method may scan the master manifest only when the index is absent
        or stale. Steady-state window selection never loads the master manifest.
        """
        if not self.manifest_file.exists():
            complete = not self.archive_dir.exists()
            self._write_window_index_state_locked(complete=complete)
            return complete

        manifest_signature = self._read_manifest_signature()
        if manifest_signature is None:
            # Legacy/bootstrap path only. Steady-state batches read the tiny
            # signature sidecar instead of hashing the lifetime manifest.
            try:
                manifest_signature = self._verified_manifest_signature_locked()
            except RuntimeError:
                self._write_window_index_state_locked(complete=False)
                return False
        if self.window_index_state_file.exists():
            try:
                state = json.loads(
                    self.window_index_state_file.read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                state = None
            version_matches = (
                isinstance(state, dict)
                and state.get("schema_version") == 1
                and bool(state.get("manifest_present"))
                and str(state.get("manifest_sha256") or "") == manifest_signature
            )
            if version_matches:
                if not bool(state.get("complete", False)):
                    # Cache a failed/incomplete build for this immutable
                    # manifest version. A bounded recovery caller may force one
                    # checksum-verified rebuild of a copied legacy archive.
                    if not force_rebuild:
                        return False
                coverage_start = str(state.get("coverage_start_day") or "") or None
                coverage_through = str(state.get("coverage_through_day") or "") or None
                raw_digests = state.get("shard_sha256")
                digest_map = raw_digests if isinstance(raw_digests, dict) else {}
                expected_count = int(state.get("coverage_day_count") or 0)
                if coverage_start and coverage_through:
                    if (
                        expected_count > 0
                        and len(digest_map) == expected_count
                        and all(
                            isinstance(digest, str) and len(digest) == 64
                            for digest in digest_map.values()
                        )
                    ):
                        return True
                elif expected_count == 0 and not digest_map:
                    return True

        try:
            if self._verified_manifest_signature_locked() != manifest_signature:
                raise RuntimeError("archive manifest signature changed during rebuild")
            raw = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError):
            self._write_window_index_state_locked(complete=False)
            return False
        segments = raw.get("segments") if isinstance(raw, dict) else None
        if not isinstance(segments, dict):
            self._write_window_index_state_locked(complete=False)
            return False

        shards: dict[str, dict[str, dict[str, Any]]] = {}
        shard_digests: dict[str, str] = {}
        complete = True
        manifest_repaired = False
        coverage_start_day: str | None = None
        coverage_through_day: str | None = None
        for segment_key, row in list(segments.items()):
            if not isinstance(row, dict):
                complete = False
                continue
            try:
                verification = self._manifest_verification_from_row(row)
            except (TypeError, ValueError):
                complete = False
                continue
            days = self._verification_days(verification)
            if (
                not days
                and repair_legacy_visibility
                and verification.archive
                and verification.sha256
            ):
                try:
                    verification = self._repair_legacy_manifest_verification_locked(
                        str(segment_key),
                        row,
                        verification,
                    )
                except (OSError, RuntimeError, ValueError, KeyError):
                    complete = False
                    continue
                repaired_row = dict(row)
                repaired_row.update(
                    {
                        "archive": verification.archive,
                        "tier": verification.tier,
                        "sha256": verification.sha256,
                        "row_count": verification.row_count,
                        "bytes": verification.bytes,
                        "first_visible_at_utc": verification.first_visible_at_utc,
                        "last_visible_at_utc": verification.last_visible_at_utc,
                        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                    }
                )
                segments[str(segment_key)] = repaired_row
                manifest_repaired = True
                days = self._verification_days(verification)
            if not days or not verification.archive or not verification.sha256:
                complete = False
                continue
            entry = {
                "archive": verification.archive,
                "tier": verification.tier,
                "sha256": verification.sha256,
                "row_count": verification.row_count,
                "bytes": verification.bytes,
                "first_visible_at_utc": verification.first_visible_at_utc,
                "last_visible_at_utc": verification.last_visible_at_utc,
            }
            for day in days:
                shards.setdefault(day, {})[verification.sha256] = entry
                coverage_start_day = (
                    day
                    if coverage_start_day is None
                    else min(coverage_start_day, day)
                )
                coverage_through_day = (
                    day
                    if coverage_through_day is None
                    else max(coverage_through_day, day)
                )

        if manifest_repaired:
            repaired_manifest = {
                "schema_version": 1,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "segments": segments,
            }
            write_atomic_lines(
                self.manifest_file,
                [
                    json.dumps(
                        repaired_manifest,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    + b"\n"
                ],
            )
            self._write_manifest_signature_locked(sha256_file(self.manifest_file))

        if self.window_index_dir.exists():
            shutil.rmtree(self.window_index_dir)
        self.window_index_dir.mkdir(parents=True, exist_ok=True)
        if coverage_start_day and coverage_through_day:
            dense_days = self._window_day_keys(
                datetime.fromisoformat(coverage_start_day).replace(
                    tzinfo=timezone.utc
                ),
                datetime.fromisoformat(coverage_through_day).replace(
                    tzinfo=timezone.utc
                ),
            )
        else:
            dense_days = ()
        for day in dense_days:
            shard = self.window_index_dir / f"{day}.json"
            write_atomic_lines(
                shard,
                [
                    json.dumps(
                        {
                            "schema_version": 1,
                            "day": day,
                            "segments": shards.get(day, {}),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    + b"\n"
                ],
            )
            shard_digests[day] = sha256_file(shard)
        self._write_window_index_state_locked(
            complete=complete,
            coverage_start_day=coverage_start_day,
            coverage_through_day=coverage_through_day,
            shard_sha256=shard_digests,
        )
        return complete

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
        # Backfill legacy segments once before mutating the master manifest.
        # The source writer lock held by callers makes the rebuild deterministic.
        manifest_existed = self.manifest_file.exists()
        prior_complete = (
            self.ensure_window_index_locked()
            if manifest_existed
            else True
        )
        if manifest_existed:
            # Mutation is infrequent (archive rotation/tiering), so authenticate
            # the full canonical manifest here before carrying forward entries.
            self._verified_manifest_signature_locked()
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
        self._write_manifest_signature_locked(sha256_file(self.manifest_file))
        if not prior_complete:
            # The manifest version just changed. Rebuild an incomplete index
            # once from the canonical manifest instead of retrying on every
            # append batch.
            self.ensure_window_index_locked()
            return
        (
            indexed,
            coverage_start_day,
            coverage_through_day,
            shard_digests,
        ) = self._update_window_index_verification_locked(verification)
        if not indexed:
            # The incremental index detected missing/corrupt certified state.
            # Rebuild once from the just-written canonical manifest. If that
            # manifest is itself structurally incomplete, the rebuild records
            # complete=false and that manifest version is then safely cached.
            self.window_index_state_file.unlink(missing_ok=True)
            self.ensure_window_index_locked()
            return
        self._write_window_index_state_locked(
            complete=True,
            coverage_start_day=coverage_start_day,
            coverage_through_day=coverage_through_day,
            shard_sha256=shard_digests,
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

        warnings: list[str] = []
        try:
            state = json.loads(
                self.window_index_state_file.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return ArchiveWindowSelection(
                paths=(),
                complete=False,
                truncated=False,
                warnings=("ARCHIVE_WINDOW_INDEX_UNAVAILABLE",),
            )
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            return ArchiveWindowSelection(
                paths=(),
                complete=False,
                truncated=False,
                warnings=("ARCHIVE_WINDOW_INDEX_INVALID",),
            )

        complete = bool(state.get("complete", False))
        if not complete:
            warnings.append("ARCHIVE_WINDOW_INDEX_INCOMPLETE")

        if bool(state.get("manifest_present")):
            try:
                manifest_stat = self.manifest_file.stat()
            except OSError:
                return ArchiveWindowSelection(
                    paths=(),
                    complete=False,
                    truncated=False,
                    warnings=("ARCHIVE_MANIFEST_UNAVAILABLE",),
                )
            try:
                manifest_sha256 = sha256_file(self.manifest_file)
            except OSError:
                return ArchiveWindowSelection(
                    paths=(),
                    complete=False,
                    truncated=False,
                    warnings=("ARCHIVE_MANIFEST_UNAVAILABLE",),
                )
            if (
                int(state.get("manifest_size") or -1) != manifest_stat.st_size
                or str(state.get("manifest_sha256") or "") != manifest_sha256
            ):
                return ArchiveWindowSelection(
                    paths=(),
                    complete=False,
                    truncated=False,
                    warnings=("ARCHIVE_WINDOW_INDEX_STALE",),
                )

        coverage_start_day = str(
            state.get("coverage_start_day") or ""
        ) or None
        coverage_through_day = str(
            state.get("coverage_through_day") or ""
        ) or None
        raw_digests = state.get("shard_sha256")
        shard_digests = raw_digests if isinstance(raw_digests, dict) else {}
        if coverage_start_day and coverage_through_day and not shard_digests:
            complete = False
            warnings.append("ARCHIVE_WINDOW_DIGESTS_UNAVAILABLE")

        rows_by_sha: dict[str, dict[str, Any]] = {}
        for day in self._window_day_keys(start, through):
            shard = self.window_index_dir / f"{day}.json"
            if not shard.exists():
                if (
                    coverage_start_day
                    and coverage_through_day
                    and coverage_start_day <= day <= coverage_through_day
                ):
                    complete = False
                    warnings.append("ARCHIVE_WINDOW_SHARD_MISSING")
                continue
            expected_digest = str(shard_digests.get(day) or "")
            if not expected_digest:
                complete = False
                warnings.append("ARCHIVE_WINDOW_SHARD_DIGEST_MISSING")
                continue
            try:
                actual_digest = sha256_file(shard)
            except OSError:
                complete = False
                warnings.append("ARCHIVE_WINDOW_SHARD_UNREADABLE")
                continue
            if actual_digest != expected_digest:
                complete = False
                warnings.append("ARCHIVE_WINDOW_SHARD_DIGEST_MISMATCH")
                continue
            try:
                raw = json.loads(shard.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                complete = False
                warnings.append("ARCHIVE_WINDOW_SHARD_UNREADABLE")
                continue
            segments = raw.get("segments") if isinstance(raw, dict) else None
            if not isinstance(segments, dict):
                complete = False
                warnings.append("ARCHIVE_WINDOW_SHARD_INVALID")
                continue
            for sha256, row in segments.items():
                if isinstance(row, dict):
                    rows_by_sha[str(sha256)] = row
                else:
                    complete = False
                    warnings.append("ARCHIVE_WINDOW_ROW_INVALID")

        candidates: list[tuple[datetime, datetime, Path]] = []
        for row in rows_by_sha.values():
            first = self._parse_manifest_time(row.get("first_visible_at_utc"))
            last = self._parse_manifest_time(row.get("last_visible_at_utc"))
            rel = str(row.get("archive") or "").strip()
            if first is None or last is None or not rel:
                complete = False
                warnings.append("ARCHIVE_WINDOW_RANGE_MISSING")
                continue
            if last < start or first > through:
                continue
            candidate = (self.archive_dir / rel).resolve()
            try:
                candidate.relative_to(self.archive_dir.resolve())
            except ValueError:
                complete = False
                warnings.append("ARCHIVE_WINDOW_PATH_INVALID")
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
