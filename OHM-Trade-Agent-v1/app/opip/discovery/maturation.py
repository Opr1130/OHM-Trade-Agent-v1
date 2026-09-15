"""Bounded discovery-outcome maturation with immutable revisions.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Mirrors Phase 3C semantics: partial labels stay eligible, later revisions are
appended, consumers read the latest revision per observation_id, and the
screening ledger is indexed across verified archive generations plus the
current HOT file so bounded HOT rotation cannot silently drop observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, BinaryIO, Iterable, Mapping, Sequence

from app.opip.decision.store import screening_evaluations_archive
from app.opip.discovery.constants import (
    DISCOVERY_BOUNDED_CHECKPOINT_ANCHOR_BYTES,
    DISCOVERY_BOUNDED_MAX_ROWS,
    DISCOVERY_BOUNDED_RETRY_DELAY,
    DISCOVERY_FORWARD_READ_GRACE,
    DISCOVERY_HOT_GENERATION_PREFIX_BYTES,
    DISCOVERY_MATURATION_MILESTONES,
    DISCOVERY_PRIMARY_HORIZON,
    DISCOVERY_SCREENING_ARCHIVE_SEGMENTS_PER_CYCLE,
    PENDING_FINALIZATION,
)
from app.opip.discovery.attribution import attribution_record
from app.opip.discovery.earliness import earliness_metrics
from app.opip.discovery.outcomes import label_screening_observation
from app.opip.discovery.store import (
    append_discovery_forward_outcomes_locked,
    dead_letter_discovery_row,
    persist_discovery_attributions,
    read_discovery_forward_outcomes,
)
from app.opip.early.point_in_time import parse_timestamp
from app.opip.storage.bounded_jsonl import BoundedJsonlArchive, repair_truncated_tail
from app.services.registry_io import registry_lock
from app.services.signal_quality_phase2 import build_timelines, read_observations


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_label_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        not in {
            "outcome_record_id",
            "outcome_revision",
            "labeled_at",
        }
    }


def discovery_outcome_record_id(row: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_label_payload(row),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "DOUT:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def discovery_window_complete(row: Mapping[str, Any]) -> bool:
    if bool(row.get("window_complete")):
        return True
    primary = (row.get("horizons") or {}).get(DISCOVERY_PRIMARY_HORIZON) or {}
    return bool(primary.get("window_complete"))


def next_discovery_due_at(
    row: Mapping[str, Any],
    *,
    evaluated_at: datetime,
) -> datetime | None:
    """Incomplete labels remain eligible. Completed windows leave the queue."""
    if discovery_window_complete(row):
        return None
    reference_at = parse_timestamp(
        row.get("reference_at") or row.get("observed_at")
    )
    evaluated = _utc(evaluated_at)
    if reference_at is None:
        return evaluated + DISCOVERY_BOUNDED_RETRY_DELAY
    for delta in DISCOVERY_MATURATION_MILESTONES:
        due = reference_at + delta
        if due > evaluated:
            return due
    return evaluated + DISCOVERY_BOUNDED_RETRY_DELAY


def _bounded_state_path(output_path: Path) -> Path:
    return output_path.parent / f".{output_path.name}.state.sqlite3"


def open_discovery_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_outcomes (
            observation_id TEXT PRIMARY KEY,
            outcome_record_id TEXT NOT NULL,
            outcome_revision INTEGER NOT NULL,
            window_complete INTEGER NOT NULL,
            reference_at TEXT NOT NULL DEFAULT '',
            row_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS observation_queue (
            observation_id TEXT PRIMARY KEY,
            observed_at TEXT NOT NULL,
            next_due_at TEXT NOT NULL,
            venue_instrument_id TEXT NOT NULL DEFAULT '',
            attribution_pending INTEGER NOT NULL DEFAULT 0,
            row_json TEXT NOT NULL
        )
        """
    )
    queue_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(observation_queue)")
    }
    if "venue_instrument_id" not in queue_columns:
        connection.execute(
            "ALTER TABLE observation_queue "
            "ADD COLUMN venue_instrument_id TEXT NOT NULL DEFAULT ''"
        )
    if "attribution_pending" not in queue_columns:
        connection.execute(
            "ALTER TABLE observation_queue "
            "ADD COLUMN attribution_pending INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovery_queue_due "
        "ON observation_queue(next_due_at, observed_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovery_queue_venue "
        "ON observation_queue(venue_instrument_id)"
    )
    connection.commit()
    return connection


def _state_int(connection: sqlite3.Connection, key: str, default: int = 0) -> int:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _set_state_int(connection: sqlite3.Connection, key: str, value: int) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(int(value))),
    )


def _state_text(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _set_state_text(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def _file_anchor(path: Path, offset: int) -> tuple[int, int, str | None]:
    if offset <= 0:
        return 0, 0, None
    start = max(0, offset - DISCOVERY_BOUNDED_CHECKPOINT_ANCHOR_BYTES)
    size = offset - start
    with path.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(size)
    if len(payload) != size:
        raise RuntimeError("DISCOVERY_SCREENING_CHECKPOINT_SHORT_READ")
    return start, size, hashlib.sha256(payload).hexdigest()


def _state_checkpoint_matches(
    connection: sqlite3.Connection,
    path: Path,
    prefix: str,
    offset: int,
) -> bool:
    if offset <= 0:
        return True
    expected = _state_text(connection, f"{prefix}_anchor_sha256")
    start = _state_int(connection, f"{prefix}_anchor_start", -1)
    size = _state_int(connection, f"{prefix}_anchor_size", -1)
    if not expected or start < 0 or size <= 0 or start + size != offset:
        return False
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read(size)
    except OSError:
        return False
    return len(payload) == size and hashlib.sha256(payload).hexdigest() == expected


def _set_state_checkpoint(
    connection: sqlite3.Connection,
    path: Path,
    prefix: str,
    offset: int,
) -> None:
    _set_state_int(connection, f"{prefix}_indexed_offset", offset)
    if offset <= 0:
        _set_state_int(connection, f"{prefix}_anchor_start", 0)
        _set_state_int(connection, f"{prefix}_anchor_size", 0)
        _set_state_text(connection, f"{prefix}_anchor_sha256", "")
        return
    start, size, digest = _file_anchor(path, offset)
    _set_state_int(connection, f"{prefix}_anchor_start", start)
    _set_state_int(connection, f"{prefix}_anchor_size", size)
    _set_state_text(connection, f"{prefix}_anchor_sha256", digest or "")


def latest_discovery_outcome_row(
    connection: sqlite3.Connection,
    observation_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT row_json FROM latest_outcomes WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def latest_discovery_outcomes_by_observation(
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Resolve the latest append-only revision per observation_id."""
    latest: dict[str, dict[str, Any]] = {}
    for row in outcomes:
        if not isinstance(row, Mapping):
            continue
        observation_id = str(row.get("observation_id") or "").strip()
        if not observation_id:
            continue
        try:
            revision = int(row.get("outcome_revision", 0) or 0)
        except (TypeError, ValueError):
            revision = 0
        current = latest.get(observation_id)
        if current is None:
            latest[observation_id] = dict(row)
            continue
        try:
            current_revision = int(current.get("outcome_revision", 0) or 0)
        except (TypeError, ValueError):
            current_revision = 0
        if revision >= current_revision:
            latest[observation_id] = dict(row)
    return latest


def _upsert_latest_outcome(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
) -> None:
    observation_id = str(row.get("observation_id") or "").strip()
    record_id = str(row.get("outcome_record_id") or "").strip()
    if not observation_id:
        return
    try:
        revision = int(row.get("outcome_revision", 0) or 0)
    except (TypeError, ValueError):
        return
    connection.execute(
        """
        INSERT INTO latest_outcomes(
            observation_id, outcome_record_id, outcome_revision,
            window_complete, reference_at, row_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_id) DO UPDATE SET
            outcome_record_id = excluded.outcome_record_id,
            outcome_revision = excluded.outcome_revision,
            window_complete = excluded.window_complete,
            reference_at = excluded.reference_at,
            row_json = excluded.row_json
        WHERE excluded.outcome_revision >= latest_outcomes.outcome_revision
        """,
        (
            observation_id,
            record_id,
            revision,
            1 if discovery_window_complete(row) else 0,
            str(row.get("reference_at") or row.get("observed_at") or ""),
            json.dumps(dict(row), sort_keys=True, allow_nan=False),
        ),
    )


def _schedule_after_evaluation(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
    *,
    evaluated_at: datetime,
) -> None:
    observation_id = str(row.get("observation_id") or "").strip()
    if not observation_id:
        return
    next_due = next_discovery_due_at(row, evaluated_at=evaluated_at)
    if next_due is None:
        connection.execute(
            "DELETE FROM observation_queue WHERE observation_id = ?",
            (observation_id,),
        )
        return
    connection.execute(
        "UPDATE observation_queue SET next_due_at = ? WHERE observation_id = ?",
        (next_due.isoformat(), observation_id),
    )


def _set_attribution_pending(
    connection: sqlite3.Connection,
    observation_id: str,
    pending: bool,
) -> None:
    observation_id = str(observation_id or "").strip()
    if not observation_id:
        return
    connection.execute(
        "UPDATE observation_queue SET attribution_pending = ? "
        "WHERE observation_id = ?",
        (1 if pending else 0, observation_id),
    )


def _attribution_is_pending(
    connection: sqlite3.Connection,
    observation_id: str,
) -> bool:
    observation_id = str(observation_id or "").strip()
    if not observation_id:
        return False
    row = connection.execute(
        "SELECT attribution_pending FROM observation_queue "
        "WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()
    if row is None:
        return False
    try:
        return int(row[0] or 0) == 1
    except (TypeError, ValueError):
        return False


def _requeue_immediately(
    connection: sqlite3.Connection,
    observation_id: str,
    *,
    due_at: datetime,
) -> None:
    observation_id = str(observation_id or "").strip()
    if not observation_id:
        return
    connection.execute(
        "UPDATE observation_queue SET next_due_at = ? WHERE observation_id = ?",
        (due_at.isoformat(), observation_id),
    )


_SQLITE_IN_CHUNK = 400


def _queue_rows_for_venues(
    connection: sqlite3.Connection,
    venues: Sequence[str],
) -> list[tuple[Any, ...]]:
    """Fetch queue JSON for venues without exceeding SQLite variable limits."""
    rows: list[tuple[Any, ...]] = []
    needed = tuple(venues)
    for start in range(0, len(needed), _SQLITE_IN_CHUNK):
        chunk = needed[start : start + _SQLITE_IN_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows.extend(
            connection.execute(
                "SELECT row_json FROM observation_queue "
                f"WHERE venue_instrument_id IN ({placeholders})",
                chunk,
            ).fetchall()
        )
    return rows


def _observation_id_from_screening(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    return str((metadata or {}).get("observation_id") or "").strip()


@dataclass(frozen=True)
class _VerifiedScreeningSegment:
    """Immutable archive identity is content SHA (+ generation timestamp for order)."""

    sha256: str
    path: Path
    generation_timestamp: str
    sort_key: str

    @property
    def identity(self) -> str:
        # Path/tier must never participate — WARM→COLD keeps the same SHA.
        if self.generation_timestamp:
            return f"{self.generation_timestamp}:{self.sha256}"
        return self.sha256


@dataclass
class _ArchiveCycleBudget:
    limit: int
    verified: set[str] = None  # type: ignore[assignment]
    decompressed: set[str] = None  # type: ignore[assignment]
    indexed: set[str] = None  # type: ignore[assignment]
    anchor_read: set[str] = None  # type: ignore[assignment]
    # SHAs already paid in a prior recovery cycle (durable cursor) — free reuse.
    prepaid: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.verified = set() if self.verified is None else self.verified
        self.decompressed = set() if self.decompressed is None else self.decompressed
        self.indexed = set() if self.indexed is None else self.indexed
        self.anchor_read = set() if self.anchor_read is None else self.anchor_read
        self.prepaid = set() if self.prepaid is None else self.prepaid

    def expensive_unique(self) -> set[str]:
        touched = (
            set(self.verified)
            | set(self.decompressed)
            | set(self.indexed)
            | set(self.anchor_read)
        )
        return touched - set(self.prepaid)

    def can_touch(self, sha: str) -> bool:
        if sha in self.prepaid or sha in (
            set(self.verified)
            | set(self.decompressed)
            | set(self.indexed)
            | set(self.anchor_read)
        ):
            return True
        return len(self.expensive_unique()) < int(self.limit)

    def as_dict(self) -> dict[str, int]:
        return {
            "archive_segments_verified": len(self.verified),
            "archive_segments_decompressed": len(self.decompressed),
            "archive_segments_indexed": len(self.indexed),
            "anchor_segments_read": len(self.anchor_read),
            "archive_segments_expensive_unique": len(self.expensive_unique()),
            "archive_segment_budget": int(self.limit),
        }


def _state_json_list(connection: sqlite3.Connection, key: str) -> list[str]:
    raw = _state_text(connection, key)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def _set_state_json_list(
    connection: sqlite3.Connection, key: str, values: Sequence[str]
) -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    _set_state_text(
        connection,
        key,
        json.dumps(unique, separators=(",", ":"), allow_nan=False),
    )


def _state_json_object(connection: sqlite3.Connection, key: str) -> dict[str, Any]:
    raw = _state_text(connection, key)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _set_state_json_object(
    connection: sqlite3.Connection, key: str, payload: Mapping[str, Any]
) -> None:
    _set_state_text(
        connection,
        key,
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False),
    )


@dataclass(frozen=True)
class ScreeningReconcileResult:
    """Typed screening ingest result — callers must inspect recovery_pending."""

    recovery_pending: bool
    continuity_proven: bool
    archive_stats: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovery_pending": self.recovery_pending,
            "continuity_proven": self.continuity_proven,
            **dict(self.archive_stats),
        }


def _manifest_head_sha(manifest_rows: Sequence[Mapping[str, Any]]) -> str:
    if not manifest_rows:
        return ""
    return str(manifest_rows[-1]["sha256"])


def _hot_generation_fingerprint(
    path: Path, *, durable_offset: int
) -> tuple[int, str]:
    """Fingerprint only complete-line checkpointed bytes (never the open tail)."""
    if not path.exists() or int(durable_offset) <= 0:
        return 0, ""
    size = path.stat().st_size
    take = min(size, int(durable_offset), DISCOVERY_HOT_GENERATION_PREFIX_BYTES)
    if take <= 0:
        return 0, ""
    with path.open("rb") as handle:
        payload = handle.read(take)
    return take, hashlib.sha256(payload).hexdigest()


def _hot_generation_matches(
    connection: sqlite3.Connection,
    path: Path,
    *,
    manifest_head_sha: str,
) -> bool:
    del manifest_head_sha  # retained for call-site clarity; peak-size is authoritative
    expected = _state_text(connection, "screening_hot_generation_sha256") or ""
    expected_bytes = _state_int(connection, "screening_hot_generation_bytes", 0)
    if not expected or expected_bytes <= 0:
        return _state_int(connection, "screening_indexed_offset", 0) <= 0
    if not path.exists():
        return False
    size = path.stat().st_size
    if size < expected_bytes:
        return False
    with path.open("rb") as handle:
        payload = handle.read(expected_bytes)
    content_ok = (
        len(payload) == expected_bytes
        and hashlib.sha256(payload).hexdigest() == expected
    )
    if not content_ok:
        return False

    # Peak HOT size for this generation. Compaction replaces HOT with a smaller
    # file even when a crafted retained prefix collides with the fingerprint.
    # Truncated-tail repair may shrink only the uncheckpointed open tail.
    peak = _state_int(connection, "screening_hot_peak_size", 0)
    durable = _state_int(connection, "screening_indexed_offset", 0)
    if peak > 0 and size < peak:
        if size < durable:
            return False
        # Permit exact repair back to the durable complete-line end.
        if size == durable and peak >= durable:
            return True
        return False
    return True


def _set_hot_generation(
    connection: sqlite3.Connection,
    path: Path,
    *,
    durable_offset: int,
    manifest_head_sha: str = "",
) -> None:
    nbytes, digest = _hot_generation_fingerprint(path, durable_offset=durable_offset)
    _set_state_int(connection, "screening_hot_generation_bytes", nbytes)
    _set_state_text(connection, "screening_hot_generation_sha256", digest)
    _set_state_text(
        connection,
        "screening_hot_generation_manifest_head_sha",
        str(manifest_head_sha or ""),
    )
    if path.exists():
        size = path.stat().st_size
        peak = _state_int(connection, "screening_hot_peak_size", 0)
        # Peak tracks observed HOT size for this generation. After a proven
        # remap/recovery the caller clears peak first so compaction can raise it
        # for the new generation without inheriting the prior file's high-water.
        _set_state_int(connection, "screening_hot_peak_size", max(peak, size))


def _reset_hot_generation_peak(connection: sqlite3.Connection) -> None:
    _set_state_int(connection, "screening_hot_peak_size", 0)


def _skip_exact(handle: BinaryIO, nbytes: int) -> None:
    remaining = int(nbytes)
    while remaining > 0:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            raise RuntimeError("DISCOVERY_SCREENING_CHECKPOINT_SHORT_READ")
        remaining -= len(chunk)


def _decompressed_size(path: Path) -> int:
    total = 0
    with gzip.open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
    return total


def _generation_timestamp_from_name(relative: str) -> str:
    """Extract compact stamp from ``prefix-YYYYMMDDThhmmss…Z-digest.jsonl.gz``."""
    name = Path(relative).name
    parts = name.split("-")
    for part in parts:
        if part.endswith("Z") and part[:8].isdigit() and "T" in part:
            return part
    return ""


def _list_manifest_segment_rows(
    archive: BoundedJsonlArchive,
) -> list[dict[str, Any]]:
    """Light manifest listing (no gzip decompress / full-file hash)."""
    if not archive.manifest_file.exists():
        return []
    archive._verified_manifest_signature_for_replica()
    try:
        raw = json.loads(archive.manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_MANIFEST_INVALID") from exc
    segments_raw = raw.get("segments") if isinstance(raw, dict) else None
    if not isinstance(segments_raw, dict):
        return []
    rows: list[dict[str, Any]] = []
    for digest, row in segments_raw.items():
        if not isinstance(row, Mapping):
            continue
        relative = str(row.get("archive") or "").strip()
        sha = str(row.get("sha256") or digest or "").strip()
        if not relative or not sha:
            continue
        generation_timestamp = _generation_timestamp_from_name(relative)
        if not generation_timestamp:
            raise RuntimeError(
                f"DISCOVERY_SCREENING_ARCHIVE_GENERATION_AMBIGUOUS:{relative}"
            )
        rows.append(
            {
                "relative_path": relative,
                "sha256": sha,
                "generation_timestamp": generation_timestamp,
                "sort_key": f"{generation_timestamp}|{sha}",
            }
        )
    rows.sort(key=lambda item: str(item["sort_key"]))
    return rows


def _resolve_segment_path(
    archive: BoundedJsonlArchive, relative: str
) -> Path:
    path = (archive.archive_dir / relative).resolve()
    try:
        path.relative_to(archive.archive_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"DISCOVERY_SCREENING_ARCHIVE_PATH_ESCAPE:{relative}"
        ) from exc
    if path.is_file():
        return path
    cold = (archive.cold_archive_dir / relative).resolve()
    if cold.is_file():
        return cold
    matches = [
        item
        for item in archive.cold_archive_dir.rglob(Path(relative).name)
        if item.is_file()
    ]
    if len(matches) == 1:
        return matches[0]
    # Basename match under warm archive_dir (tiering mid-flight).
    warm_matches = [
        item
        for item in archive.archive_dir.rglob(Path(relative).name)
        if item.is_file() and item.suffixes[-2:] == [".jsonl", ".gz"]
    ]
    if len(warm_matches) == 1:
        return warm_matches[0]
    raise RuntimeError(f"DISCOVERY_SCREENING_ARCHIVE_MISSING:{relative}")



def _read_virtual_range(
    segments: Sequence[_VerifiedScreeningSegment],
    hot_path: Path | None,
    start: int,
    length: int,
    *,
    budget: _ArchiveCycleBudget | None = None,
) -> bytes:
    if length <= 0:
        return b""
    skip = int(start)
    needed = int(length)
    out = bytearray()

    def _consume(handle: BinaryIO) -> None:
        nonlocal skip, needed
        while skip > 0:
            chunk = handle.read(min(1024 * 1024, skip))
            if not chunk:
                return
            skip -= len(chunk)
        while needed > 0:
            chunk = handle.read(min(1024 * 1024, needed))
            if not chunk:
                return
            out.extend(chunk)
            needed -= len(chunk)

    for segment in segments:
        if needed <= 0:
            break
        if budget is not None:
            if not budget.can_touch(segment.sha256):
                raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_BUDGET_EXCEEDED")
            budget.anchor_read.add(segment.sha256)
        with gzip.open(segment.path, "rb") as handle:
            _consume(handle)
    if needed > 0 and hot_path is not None and hot_path.exists():
        with hot_path.open("rb") as handle:
            _consume(handle)
    if needed > 0 or skip > 0:
        raise RuntimeError("DISCOVERY_SCREENING_CHECKPOINT_SHORT_READ")
    return bytes(out)


def _anchor_matches_virtual(
    connection: sqlite3.Connection,
    segments: Sequence[_VerifiedScreeningSegment],
    hot_path: Path | None,
    offset: int,
    *,
    budget: _ArchiveCycleBudget | None = None,
) -> bool:
    if offset <= 0:
        return True
    expected = _state_text(connection, "screening_anchor_sha256")
    start = _state_int(connection, "screening_anchor_start", -1)
    size = _state_int(connection, "screening_anchor_size", -1)
    if not expected or start < 0 or size <= 0 or start + size != offset:
        return False
    try:
        payload = _read_virtual_range(
            segments, hot_path, start, size, budget=budget
        )
    except RuntimeError:
        return False
    return len(payload) == size and hashlib.sha256(payload).hexdigest() == expected


def _materialize_segment(
    archive: BoundedJsonlArchive,
    row: Mapping[str, Any],
    *,
    budget: _ArchiveCycleBudget,
    sizes: dict[str, int],
    require_decompress: bool,
) -> _VerifiedScreeningSegment:
    relative = str(row["relative_path"])
    sha = str(row["sha256"])
    if not budget.can_touch(sha):
        raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_BUDGET_EXCEEDED")
    path = _resolve_segment_path(archive, relative)
    checksum = path.with_suffix(path.suffix + ".sha256")
    if not checksum.exists():
        raise RuntimeError(f"DISCOVERY_SCREENING_ARCHIVE_MISSING:{relative}")
    tokens = checksum.read_text(encoding="utf-8").split()
    if not tokens:
        raise RuntimeError(
            f"DISCOVERY_SCREENING_ARCHIVE_CHECKSUM_MISMATCH:{relative}"
        )
    if sha not in budget.verified and sha not in budget.prepaid:
        if not budget.can_touch(sha):
            raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_BUDGET_EXCEEDED")
        actual = archive._sha256_file(path)
        if actual != tokens[0] or actual != sha:
            raise RuntimeError(
                f"DISCOVERY_SCREENING_ARCHIVE_CHECKSUM_MISMATCH:{relative}"
            )
        budget.verified.add(sha)
    elif sha in budget.prepaid and tokens[0] != sha:
        raise RuntimeError(
            f"DISCOVERY_SCREENING_ARCHIVE_CHECKSUM_MISMATCH:{relative}"
        )
    if require_decompress and sha not in sizes:
        if not budget.can_touch(sha):
            raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_BUDGET_EXCEEDED")
        sizes[sha] = _decompressed_size(path)
        budget.decompressed.add(sha)
    generation_timestamp = str(row.get("generation_timestamp") or "")
    return _VerifiedScreeningSegment(
        sha256=sha,
        path=path,
        generation_timestamp=generation_timestamp,
        sort_key=str(row.get("sort_key") or f"{generation_timestamp}|{sha}"),
    )


def _enqueue_screening_snapshot(
    connection: sqlite3.Connection,
    snapshot: Mapping[str, Any],
    *,
    now: datetime,
) -> None:
    if str(snapshot.get("scanner_type") or "") != "BROAD_SEARCH":
        return
    if str(snapshot.get("outcome") or "") == PENDING_FINALIZATION:
        return
    metadata = snapshot.get("metadata")
    if isinstance(metadata, Mapping) and str(
        metadata.get("production_admission_result") or ""
    ) == PENDING_FINALIZATION:
        return
    observation_id = _observation_id_from_screening(snapshot)
    observed_at = parse_timestamp(snapshot.get("observed_at"))
    if not observation_id or observed_at is None:
        return

    prior = latest_discovery_outcome_row(connection, observation_id)
    if prior is not None and discovery_window_complete(prior):
        connection.execute(
            "DELETE FROM observation_queue WHERE observation_id = ?",
            (observation_id,),
        )
        return
    next_due = (
        next_discovery_due_at(prior, evaluated_at=now)
        if prior is not None
        else observed_at
    )
    if next_due is None:
        connection.execute(
            "DELETE FROM observation_queue WHERE observation_id = ?",
            (observation_id,),
        )
        return
    connection.execute(
        """
        INSERT INTO observation_queue(
            observation_id, observed_at, next_due_at,
            venue_instrument_id, row_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(observation_id) DO UPDATE SET
            row_json = excluded.row_json,
            observed_at = excluded.observed_at,
            venue_instrument_id = excluded.venue_instrument_id
        """,
        (
            observation_id,
            observed_at.isoformat(),
            next_due.isoformat(),
            str(snapshot.get("venue_instrument_id") or ""),
            json.dumps(snapshot, sort_keys=True, allow_nan=False),
        ),
    )


def _index_jsonl_handle(
    connection: sqlite3.Connection,
    handle: BinaryIO,
    *,
    now: datetime,
    start_offset: int = 0,
) -> int:
    if start_offset > 0:
        _skip_exact(handle, start_offset)
    last_complete = int(start_offset)
    while True:
        raw = handle.readline()
        if not raw:
            break
        if not raw.endswith(b"\n"):
            break
        last_complete += len(raw)
        try:
            snapshot = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(snapshot, dict):
            _enqueue_screening_snapshot(connection, snapshot, now=now)
    return last_complete


def _index_hot_from_offset(
    connection: sqlite3.Connection,
    screening_path: Path,
    *,
    now: datetime,
    start_offset: int,
) -> int:
    last_complete = int(start_offset)
    with screening_path.open("rb") as handle:
        last_complete = _index_jsonl_handle(
            connection, handle, now=now, start_offset=start_offset
        )
    _set_state_checkpoint(connection, screening_path, "screening", last_complete)
    manifest_rows = _list_manifest_segment_rows(
        screening_evaluations_archive(screening_path)
    )
    _set_hot_generation(
        connection,
        screening_path,
        durable_offset=last_complete,
        manifest_head_sha=_manifest_head_sha(manifest_rows),
    )
    _set_state_int(connection, "screening_hot_offset", last_complete)
    connection.commit()
    return last_complete


def _index_archive_segment(
    connection: sqlite3.Connection,
    segment: _VerifiedScreeningSegment,
    *,
    now: datetime,
    start_offset: int = 0,
    budget: _ArchiveCycleBudget | None = None,
) -> None:
    if budget is not None:
        if not budget.can_touch(segment.sha256):
            raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_BUDGET_EXCEEDED")
        budget.indexed.add(segment.sha256)
    with gzip.open(segment.path, "rb") as handle:
        _index_jsonl_handle(
            connection, handle, now=now, start_offset=start_offset
        )


def _mark_segment_consumed(
    connection: sqlite3.Connection, segment: _VerifiedScreeningSegment
) -> None:
    consumed = _state_json_list(connection, "screening_consumed_archives")
    if segment.sha256 not in consumed:
        consumed.append(segment.sha256)
    _set_state_json_list(connection, "screening_consumed_archives", consumed)
    known = _state_json_list(connection, "screening_known_archive_shas")
    if segment.sha256 not in known:
        known.append(segment.sha256)
    _set_state_json_list(connection, "screening_known_archive_shas", known)


def _sha_like(token: str) -> bool:
    text = str(token).strip().lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _migrate_screening_checkpoint_state(
    connection: sqlite3.Connection,
    screening_path: Path,
) -> None:
    """Idempotent additive migration from offset-only production state."""
    schema = _state_text(connection, "screening_checkpoint_schema")
    if schema in {"generation_v1", "generation_v2"}:
        if _state_text(connection, "screening_hot_offset") is None:
            _set_state_int(
                connection,
                "screening_hot_offset",
                _state_int(connection, "screening_indexed_offset", 0),
            )
        consumed = [c for c in _state_json_list(connection, "screening_consumed_archives") if _sha_like(c)]
        _set_state_json_list(connection, "screening_consumed_archives", consumed)
        if not _state_json_list(connection, "screening_known_archive_shas"):
            legacy_known = _state_json_list(connection, "screening_generation_archives")
            shas = [item for item in legacy_known if _sha_like(item)]
            _set_state_json_list(
                connection,
                "screening_known_archive_shas",
                list(dict.fromkeys([*consumed, *shas])),
            )
        else:
            known = [
                item
                for item in _state_json_list(connection, "screening_known_archive_shas")
                if _sha_like(item)
            ]
            _set_state_json_list(connection, "screening_known_archive_shas", known)
        if schema != "generation_v2":
            _set_state_text(connection, "screening_checkpoint_schema", "generation_v2")
            connection.commit()
        return

    offset = _state_int(connection, "screening_indexed_offset", 0)
    _set_state_int(connection, "screening_hot_offset", offset)
    if not _state_json_list(connection, "screening_consumed_archives"):
        _set_state_json_list(connection, "screening_consumed_archives", [])
    else:
        _set_state_json_list(
            connection,
            "screening_consumed_archives",
            [
                item
                for item in _state_json_list(connection, "screening_consumed_archives")
                if _sha_like(item)
            ],
        )
    if not _state_json_list(connection, "screening_known_archive_shas"):
        # Empty known set => legacy / unknown prior archives. Recovery must
        # prove the correct newest-to-oldest suffix against the saved anchor.
        _set_state_json_list(connection, "screening_known_archive_shas", [])
    if screening_path.exists() and offset <= screening_path.stat().st_size:
        if offset <= 0 or _state_checkpoint_matches(
            connection, screening_path, "screening", offset
        ):
            head = ""
            try:
                head = _manifest_head_sha(
                    _list_manifest_segment_rows(
                        screening_evaluations_archive(screening_path)
                    )
                )
            except RuntimeError:
                head = ""
            _set_hot_generation(
                connection,
                screening_path,
                durable_offset=offset,
                manifest_head_sha=head,
            )
    _set_state_text(connection, "screening_checkpoint_schema", "generation_v2")
    connection.commit()


def _load_recovery_cursor(connection: sqlite3.Connection) -> dict[str, Any]:
    payload = _state_json_object(connection, "screening_rotation_recovery_v1")
    sequence = payload.get("sequence")
    sizes = payload.get("segment_sizes")
    verified = payload.get("verified_shas")
    return {
        "sequence": [str(x) for x in sequence] if isinstance(sequence, list) else [],
        "active_end_sha": str(payload.get("active_end_sha") or ""),
        "suffix_len_next": int(payload.get("suffix_len_next") or 1),
        "segment_sizes": {
            str(k): int(v)
            for k, v in (sizes.items() if isinstance(sizes, dict) else [])
            if str(k).strip()
        },
        "verified_shas": [str(x) for x in verified] if isinstance(verified, list) else [],
    }


def _save_recovery_cursor(
    connection: sqlite3.Connection, cursor: Mapping[str, Any]
) -> None:
    _set_state_json_object(connection, "screening_rotation_recovery_v1", cursor)


def _clear_recovery_cursor(connection: sqlite3.Connection) -> None:
    _set_state_json_object(connection, "screening_rotation_recovery_v1", {})


def _sync_recovery_sequence(
    cursor: dict[str, Any],
    manifest_shas: Sequence[str],
) -> dict[str, Any]:
    """Extend recovery sequence when manifest grows; fail closed on rewrite.

    Newer archives appended after recovery starts are tracked but do not move
    the pinned proof head — suffix search stays newest-to-oldest ending at
    ``active_end_sha`` so a mid-recovery rotation cannot reset progress.
    """
    prior = [str(x) for x in cursor.get("sequence") or []]
    current = [str(x) for x in manifest_shas]
    if not prior:
        cursor["sequence"] = list(current)
        if current and not cursor.get("active_end_sha"):
            cursor["active_end_sha"] = current[-1]
        cursor["suffix_len_next"] = max(1, int(cursor.get("suffix_len_next") or 1))
        return cursor
    if len(current) < len(prior):
        raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_SEQUENCE_REGRESSED")
    if current[: len(prior)] != prior:
        raise RuntimeError("DISCOVERY_SCREENING_ARCHIVE_SEQUENCE_REORDERED")
    cursor["sequence"] = list(current)
    if not cursor.get("active_end_sha") and prior:
        cursor["active_end_sha"] = prior[-1]
    # Preserve suffix_len_next and caches when the prior immutable prefix is intact.
    return cursor


def _attempt_suffix_recovery(
    connection: sqlite3.Connection,
    archive: BoundedJsonlArchive,
    manifest_rows: Sequence[Mapping[str, Any]],
    screening_path: Path,
    offset: int,
    *,
    now: datetime,
    budget: _ArchiveCycleBudget,
) -> tuple[list[_VerifiedScreeningSegment], int] | None:
    """Newest-to-oldest suffix search with durable cross-cycle progress."""
    del now  # indexing happens after proof in reconcile_screening_queue
    by_sha = {str(row["sha256"]): row for row in manifest_rows}
    cursor = _load_recovery_cursor(connection)
    sizes = {
        str(k): int(v) for k, v in (cursor.get("segment_sizes") or {}).items()
    }
    prepaid = set(str(x) for x in (cursor.get("verified_shas") or [])) | set(sizes)
    budget.prepaid |= prepaid

    sequence_shas = [str(row["sha256"]) for row in manifest_rows]
    cursor = _sync_recovery_sequence(cursor, sequence_shas)
    _save_recovery_cursor(connection, cursor)
    connection.commit()

    if offset <= 0:
        _clear_recovery_cursor(connection)
        connection.commit()
        return [], 0

    hot_size = screening_path.stat().st_size if screening_path.exists() else 0
    suffix_next = max(1, int(cursor.get("suffix_len_next") or 1))
    seq = [str(x) for x in cursor.get("sequence") or []]
    active_end = str(cursor.get("active_end_sha") or "")
    if active_end and active_end in seq:
        end_idx = seq.index(active_end)
        proof_seq = seq[: end_idx + 1]
    else:
        proof_seq = list(seq)
        if proof_seq:
            cursor["active_end_sha"] = proof_seq[-1]

    if not proof_seq:
        raise RuntimeError(
            "DISCOVERY_SCREENING_LEDGER_DIVERGED"
            if offset <= hot_size
            else "DISCOVERY_SCREENING_LEDGER_TRUNCATED"
        )

    while suffix_next <= len(proof_seq):
        suffix_shas = proof_seq[-suffix_next:]
        materialized: list[_VerifiedScreeningSegment] = []
        blocked = False
        for sha in suffix_shas:
            row = by_sha.get(sha)
            if row is None:
                raise RuntimeError(f"DISCOVERY_SCREENING_ARCHIVE_MISSING:{sha}")
            need_decompress = sha not in sizes
            need_verify = sha not in budget.verified and sha not in budget.prepaid
            if (need_decompress or need_verify) and not budget.can_touch(sha):
                blocked = True
                break
            segment = _materialize_segment(
                archive,
                row,
                budget=budget,
                sizes=sizes,
                require_decompress=True,
            )
            materialized.append(segment)
        if blocked:
            cursor["suffix_len_next"] = suffix_next
            cursor["segment_sizes"] = sizes
            cursor["verified_shas"] = sorted(
                set(cursor.get("verified_shas") or [])
                | set(budget.verified)
                | set(sizes)
            )
            _save_recovery_cursor(connection, cursor)
            connection.commit()
            return None

        prefix_bytes = sum(sizes[sha] for sha in suffix_shas)
        if prefix_bytes + hot_size >= offset:
            try:
                matched = _anchor_matches_virtual(
                    connection,
                    materialized,
                    screening_path,
                    offset,
                    budget=budget,
                )
            except RuntimeError as exc:
                if "BUDGET_EXCEEDED" in str(exc):
                    cursor["suffix_len_next"] = suffix_next
                    cursor["segment_sizes"] = sizes
                    cursor["verified_shas"] = sorted(
                        set(cursor.get("verified_shas") or [])
                        | set(budget.verified)
                        | set(sizes)
                    )
                    _save_recovery_cursor(connection, cursor)
                    connection.commit()
                    return None
                raise
            if matched:
                hot_offset = max(0, offset - prefix_bytes)
                cursor["segment_sizes"] = sizes
                cursor["verified_shas"] = sorted(
                    set(cursor.get("verified_shas") or [])
                    | set(budget.verified)
                    | set(sizes)
                )
                _save_recovery_cursor(connection, cursor)
                connection.commit()
                return materialized, hot_offset

        suffix_next += 1
        cursor["suffix_len_next"] = suffix_next
        cursor["segment_sizes"] = sizes
        cursor["verified_shas"] = sorted(
            set(cursor.get("verified_shas") or [])
            | set(budget.verified)
            | set(sizes)
        )
        _save_recovery_cursor(connection, cursor)
        connection.commit()

    raise RuntimeError(
        "DISCOVERY_SCREENING_LEDGER_DIVERGED"
        if offset <= hot_size
        else "DISCOVERY_SCREENING_LEDGER_TRUNCATED"
    )


def reconcile_screening_queue(
    connection: sqlite3.Connection,
    screening_path: Path,
    *,
    now: datetime,
) -> ScreeningReconcileResult:
    """Index BROAD_SEARCH screening rows across verified archives + HOT.

    Contract:
    * Same HOT generation resumes from the durable byte offset.
    * Legacy/rotated HOT recovery proves the correct newest-to-oldest archive
      suffix against the saved checkpoint anchor before remapping HOT.
    * Archive identity is content SHA (WARM→COLD safe).
    * Per-cycle unique expensive archive touches stay within budget.
    * Queue upserts are idempotent on ``observation_id``.
    * Incomplete bounded recovery returns ``recovery_pending=True`` — callers
      must not treat that as a healthy completed ingest.
    """
    _migrate_screening_checkpoint_state(connection, screening_path)
    budget = _ArchiveCycleBudget(limit=DISCOVERY_SCREENING_ARCHIVE_SEGMENTS_PER_CYCLE)
    archive = screening_evaluations_archive(screening_path)
    manifest_rows = _list_manifest_segment_rows(archive)
    consumed = set(_state_json_list(connection, "screening_consumed_archives"))
    known = set(_state_json_list(connection, "screening_known_archive_shas"))
    indexed_offset = _state_int(connection, "screening_indexed_offset", 0)
    manifest_head = _manifest_head_sha(manifest_rows)

    def _done(
        *, recovery_pending: bool, continuity_proven: bool
    ) -> ScreeningReconcileResult:
        stats = budget.as_dict()
        _set_state_json_object(connection, "screening_archive_cycle_stats", stats)
        _set_state_text(
            connection,
            "screening_rotation_recovery_pending",
            "1" if recovery_pending else "0",
        )
        connection.commit()
        return ScreeningReconcileResult(
            recovery_pending=recovery_pending,
            continuity_proven=continuity_proven,
            archive_stats=stats,
        )

    if not screening_path.exists():
        if indexed_offset and not any(
            str(row["sha256"]) not in consumed for row in manifest_rows
        ):
            raise RuntimeError("DISCOVERY_SCREENING_LEDGER_TRUNCATED")
        return _done(recovery_pending=False, continuity_proven=indexed_offset <= 0)

    size = screening_path.stat().st_size
    generation_ok = _hot_generation_matches(
        connection, screening_path, manifest_head_sha=manifest_head
    )
    anchor_ok = _state_checkpoint_matches(
        connection, screening_path, "screening", indexed_offset
    )

    if indexed_offset <= size and generation_ok and (indexed_offset == 0 or anchor_ok):
        known_set = set(known)
        if known_set:
            catchup = [
                row
                for row in manifest_rows
                if str(row["sha256"]) not in consumed
                and str(row["sha256"]) not in known_set
            ]
            for row in catchup:
                sha = str(row["sha256"])
                if not budget.can_touch(sha):
                    break
                sizes: dict[str, int] = {}
                segment = _materialize_segment(
                    archive,
                    row,
                    budget=budget,
                    sizes=sizes,
                    require_decompress=False,
                )
                _index_archive_segment(
                    connection, segment, now=now, start_offset=0, budget=budget
                )
                _mark_segment_consumed(connection, segment)
                known_set.add(sha)
                consumed.add(sha)
                connection.commit()
            # Never mark budget-skipped catchup SHAs as known.
            current_shas = {str(row["sha256"]) for row in manifest_rows}
            _set_state_json_list(
                connection,
                "screening_known_archive_shas",
                sorted(
                    (known_set | set(_state_json_list(connection, "screening_consumed_archives")))
                    & current_shas
                ),
            )
        else:
            # Empty known on a healthy HOT generation: adopt current inventory as
            # the baseline so historical archives are not falsely treated as new.
            _set_state_json_list(
                connection,
                "screening_known_archive_shas",
                [str(row["sha256"]) for row in manifest_rows],
            )
        _index_hot_from_offset(
            connection, screening_path, now=now, start_offset=indexed_offset
        )
        _clear_recovery_cursor(connection)
        return _done(recovery_pending=False, continuity_proven=True)

    if indexed_offset <= size and generation_ok and not anchor_ok:
        raise RuntimeError("DISCOVERY_SCREENING_LEDGER_DIVERGED")

    proof = _attempt_suffix_recovery(
        connection,
        archive,
        manifest_rows,
        screening_path,
        indexed_offset,
        now=now,
        budget=budget,
    )
    if proof is None:
        unconsumed = [
            row for row in manifest_rows if str(row["sha256"]) not in consumed
        ]
        if unconsumed or _load_recovery_cursor(connection).get("sequence"):
            return _done(recovery_pending=True, continuity_proven=False)
        if indexed_offset > size:
            raise RuntimeError("DISCOVERY_SCREENING_LEDGER_TRUNCATED")
        raise RuntimeError("DISCOVERY_SCREENING_LEDGER_DIVERGED")

    prefix, hot_offset = proof
    sizes_map = {
        str(k): int(v)
        for k, v in (
            _load_recovery_cursor(connection).get("segment_sizes") or {}
        ).items()
    }
    preceding = 0
    for segment in prefix:
        seg_size = int(
            sizes_map.get(segment.sha256) or _decompressed_size(segment.path)
        )
        if segment.sha256 not in sizes_map:
            if budget.can_touch(segment.sha256):
                budget.decompressed.add(segment.sha256)
            sizes_map[segment.sha256] = seg_size
        if indexed_offset >= preceding + seg_size:
            local_start = seg_size
        elif indexed_offset > preceding:
            local_start = indexed_offset - preceding
        else:
            local_start = 0
        if segment.sha256 not in consumed:
            if local_start < seg_size:
                if not budget.can_touch(segment.sha256):
                    return _done(recovery_pending=True, continuity_proven=False)
                _index_archive_segment(
                    connection,
                    segment,
                    now=now,
                    start_offset=local_start,
                    budget=budget,
                )
            _mark_segment_consumed(connection, segment)
            connection.commit()
            consumed.add(segment.sha256)
        preceding += seg_size

    # Account for proven/consumed/prior-known SHAs only. Archives newer than the
    # pinned proof head remain unknown so normal catchup can index them later.
    cursor_before_clear = _load_recovery_cursor(connection)
    active_end = str(cursor_before_clear.get("active_end_sha") or "")
    current = [str(row["sha256"]) for row in manifest_rows]
    accounted = set(known) | set(consumed) | {segment.sha256 for segment in prefix}
    if active_end and active_end in current:
        end_idx = current.index(active_end)
        accounted.update(current[: end_idx + 1])
    _set_state_json_list(
        connection,
        "screening_known_archive_shas",
        [sha for sha in current if sha in accounted],
    )
    # Remap HOT before clearing recovery cursor so a crash mid-remap can resume.
    _reset_hot_generation_peak(connection)
    _index_hot_from_offset(
        connection, screening_path, now=now, start_offset=hot_offset
    )
    _clear_recovery_cursor(connection)
    return _done(recovery_pending=False, continuity_proven=True)


def due_observation_batch(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT row_json
        FROM observation_queue
        WHERE next_due_at <= ?
        ORDER BY observed_at, observation_id
        LIMIT ?
        """,
        (_utc(now).isoformat(), int(limit)),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for (raw,) in rows:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def mature_discovery_outcomes_bounded(
    *,
    screening_path: Path,
    observation_path: Path,
    output_dir: Path,
    max_rows: int = DISCOVERY_BOUNDED_MAX_ROWS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Label a bounded due queue. Incomplete IDs remain eligible for revision."""
    labeled_at = _utc(now or datetime.now(timezone.utc))
    outcomes_path = output_dir / "forward_outcomes.jsonl"
    state_path = _bounded_state_path(outcomes_path)
    summary = {
        "measurement_only": True,
        "trade_authority_changed": False,
        "evaluated": 0,
        "written_outcomes": 0,
        "reused_current_revision": 0,
        "queued_due": 0,
        "completed": 0,
        "still_incomplete": 0,
        "written_attributions": 0,
        "invalid_outcomes_dead_lettered": 0,
    }
    if max_rows < 1:
        return summary

    output_dir.mkdir(parents=True, exist_ok=True)
    lock = output_dir / ".forward_outcomes.jsonl.lock"
    with registry_lock(lock):
        repair_truncated_tail(outcomes_path)
        repair_truncated_tail(screening_path)
        connection = open_discovery_state(state_path)
        try:
            reconcile = reconcile_screening_queue(
                connection, screening_path, now=labeled_at
            )
            if reconcile.recovery_pending:
                summary.update(
                    {
                        "error": "DISCOVERY_SCREENING_ROTATION_RECOVERY_PENDING",
                        "rotation_recovery_pending": True,
                        "continuity_proven": False,
                        "archive_stats": dict(reconcile.archive_stats),
                    }
                )
                connection.commit()
                return summary
            pending = due_observation_batch(
                connection, now=labeled_at, limit=max_rows
            )
            summary["queued_due"] = len(pending)
            if not pending:
                connection.commit()
                return summary

            symbols = {
                str(row.get("venue_instrument_id") or "").upper()
                for row in pending
                if str(row.get("venue_instrument_id") or "").strip()
            }
            times = [
                parsed
                for parsed in (
                    parse_timestamp(row.get("observed_at")) for row in pending
                )
                if parsed is not None
            ]
            latest_decision = max(times) if times else labeled_at
            earliest = min(times) if times else labeled_at
            ingestion = read_observations(
                observation_path,
                symbols=symbols or None,
                start_at=earliest,
                end_at=latest_decision + DISCOVERY_FORWARD_READ_GRACE,
            )
            timelines = build_timelines(ingestion.observations)

            by_instrument: dict[str, list[dict[str, Any]]] = {}
            needed = tuple(
                venue
                for venue in {
                    str(row.get("venue_instrument_id") or "")
                    for row in pending
                }
                if venue
            )
            if needed:
                queued = _queue_rows_for_venues(connection, needed)
                for (raw,) in queued:
                    try:
                        item = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(item, dict):
                        continue
                    venue_id = str(item.get("venue_instrument_id") or "")
                    if venue_id:
                        by_instrument.setdefault(venue_id, []).append(item)

            new_rows: list[dict[str, Any]] = []
            attribution_retries: list[dict[str, Any]] = []
            attributions_path = output_dir / "attributions.jsonl"
            for row in pending:
                observation_id = _observation_id_from_screening(row)
                venue_id = str(row.get("venue_instrument_id") or "").upper()
                timeline = timelines.get(venue_id)
                outcome = label_screening_observation(
                    row, timeline, labeled_at=labeled_at
                )
                primary = (outcome.get("horizons") or {}).get(
                    DISCOVERY_PRIMARY_HORIZON
                ) or {}
                direction = str(
                    outcome.get("realized_opportunity_direction")
                    or outcome.get("production_preferred_direction")
                    or "LONG"
                )
                directional_prefix = "long_" if direction == "LONG" else "short_"
                favorable_at = (
                    primary.get(f"{directional_prefix}favorable_barrier_at")
                    or primary.get(f"{directional_prefix}mfe_at")
                    or primary.get("favorable_barrier_at")
                    or primary.get("mfe_at")
                )
                favorable_price = None
                last_forward = primary.get(
                    f"{directional_prefix}last_forward_price"
                ) or primary.get("last_forward_price")
                directional_mfe = primary.get(f"{directional_prefix}mfe_pct")
                if directional_mfe is None:
                    directional_mfe = primary.get("mfe_pct")
                if directional_mfe is not None and outcome.get("reference_price"):
                    ref = float(outcome["reference_price"])
                    mfe = float(directional_mfe)
                    if direction == "SHORT":
                        favorable_price = ref * (1.0 - mfe / 100.0)
                    else:
                        favorable_price = ref * (1.0 + mfe / 100.0)
                outcome["earliness"] = earliness_metrics(
                    by_instrument.get(
                        str(row.get("venue_instrument_id") or ""), []
                    ),
                    direction=direction,
                    favorable_price=favorable_price or last_forward,
                    favorable_at=favorable_at,
                )
                outcome["window_complete"] = discovery_window_complete(outcome)
                try:
                    record_id = discovery_outcome_record_id(outcome)
                except (TypeError, ValueError) as exc:
                    dead_letter_discovery_row(
                        outcome,
                        path=outcomes_path,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                    connection.execute(
                        "DELETE FROM observation_queue WHERE observation_id = ?",
                        (observation_id,),
                    )
                    summary["invalid_outcomes_dead_lettered"] += 1
                    continue
                prior = latest_discovery_outcome_row(connection, observation_id)
                if (
                    prior is not None
                    and str(prior.get("outcome_record_id") or "") == record_id
                ):
                    summary["reused_current_revision"] += 1
                    if _attribution_is_pending(connection, observation_id):
                        pending_attr = attribution_record(
                            row, labeled_at=labeled_at
                        )
                        if pending_attr.get("canonical_terminal"):
                            attribution_retries.append(pending_attr)
                            # Keep the queue row until attribution succeeds.
                            _requeue_immediately(
                                connection, observation_id, due_at=labeled_at
                            )
                        else:
                            _set_attribution_pending(
                                connection, observation_id, False
                            )
                            _schedule_after_evaluation(
                                connection, prior, evaluated_at=labeled_at
                            )
                    else:
                        _schedule_after_evaluation(
                            connection, prior, evaluated_at=labeled_at
                        )
                    if discovery_window_complete(prior):
                        summary["completed"] += 1
                    else:
                        summary["still_incomplete"] += 1
                    continue

                revision = (
                    int(prior.get("outcome_revision", 0) or 0) + 1
                    if prior is not None
                    else 1
                )
                stamped = {
                    **outcome,
                    "outcome_record_id": record_id,
                    "outcome_revision": revision,
                    "append_only": True,
                }
                new_rows.append(stamped)

            written = 0
            written_attr = 0
            persisted_new_rows: list[dict[str, Any]] = []
            attribution_rows: list[dict[str, Any]] = []
            rejected_outcome_ids: set[str] = set()
            if new_rows:
                written = append_discovery_forward_outcomes_locked(
                    new_rows,
                    path=outcomes_path,
                    rejected_observation_ids=rejected_outcome_ids,
                )
                for observation_id in rejected_outcome_ids:
                    connection.execute(
                        "DELETE FROM observation_queue WHERE observation_id = ?",
                        (observation_id,),
                    )
                summary["invalid_outcomes_dead_lettered"] += len(
                    rejected_outcome_ids
                )

                # Resolve which logical rows are actually durable before
                # advancing SQLite state. This also tolerates a prior crash
                # after JSONL append but before the state commit.
                persisted_record_ids = {
                    str(item.get("outcome_record_id") or "")
                    for item in read_discovery_forward_outcomes(
                        path=outcomes_path,
                        limit=max(2_000, len(new_rows) * 4),
                    )
                    if item.get("outcome_record_id")
                }
                persisted_new_rows = [
                    item
                    for item in new_rows
                    if str(item.get("observation_id") or "")
                    not in rejected_outcome_ids
                    and str(item.get("outcome_record_id") or "")
                    in persisted_record_ids
                ]
                for stamped in persisted_new_rows:
                    _upsert_latest_outcome(connection, stamped)
                    if discovery_window_complete(stamped):
                        summary["completed"] += 1
                    else:
                        summary["still_incomplete"] += 1

                persisted_ids = {
                    str(item.get("observation_id") or "")
                    for item in persisted_new_rows
                    if item.get("observation_id")
                }
                attribution_rows = [
                    attribution_record(row, labeled_at=labeled_at)
                    for row in pending
                    if _observation_id_from_screening(row) in persisted_ids
                ]
                attribution_rows = [
                    row
                    for row in attribution_rows
                    if bool(row.get("canonical_terminal"))
                ]

            pending_attributions = attribution_rows + attribution_retries
            persisted_attr_ids: set[str] = set()
            if pending_attributions:
                try:
                    persisted_attr_ids, written_attr = (
                        persist_discovery_attributions(
                            pending_attributions,
                            path=attributions_path,
                        )
                    )
                except Exception as exc:
                    summary["attribution_persist_error"] = type(exc).__name__
                    persisted_attr_ids = set()
                    written_attr = 0

            attr_by_observation = {
                str(item.get("observation_id") or ""): item
                for item in pending_attributions
                if item.get("observation_id")
                and bool(item.get("canonical_terminal"))
            }
            attribution_incomplete = False

            for stamped in persisted_new_rows:
                observation_id = str(stamped.get("observation_id") or "")
                if not observation_id:
                    continue
                required = attr_by_observation.get(observation_id)
                required_id = (
                    str(required.get("attribution_record_id") or "")
                    if required is not None
                    else ""
                )
                attr_ok = required is None or (
                    bool(required_id) and required_id in persisted_attr_ids
                )
                if attr_ok:
                    _set_attribution_pending(connection, observation_id, False)
                    _schedule_after_evaluation(
                        connection, stamped, evaluated_at=labeled_at
                    )
                else:
                    attribution_incomplete = True
                    _set_attribution_pending(connection, observation_id, True)
                    _requeue_immediately(
                        connection, observation_id, due_at=labeled_at
                    )

            retry_ids = {
                str(item.get("observation_id") or "")
                for item in attribution_retries
                if item.get("observation_id")
            }
            for observation_id in retry_ids:
                required = attr_by_observation.get(observation_id)
                required_id = (
                    str(required.get("attribution_record_id") or "")
                    if required is not None
                    else ""
                )
                attr_ok = required is not None and bool(required_id) and (
                    required_id in persisted_attr_ids
                )
                if attr_ok:
                    prior = latest_discovery_outcome_row(
                        connection, observation_id
                    )
                    if prior is not None:
                        _set_attribution_pending(
                            connection, observation_id, False
                        )
                        _schedule_after_evaluation(
                            connection, prior, evaluated_at=labeled_at
                        )
                else:
                    attribution_incomplete = True
                    _set_attribution_pending(
                        connection, observation_id, True
                    )
                    _requeue_immediately(
                        connection, observation_id, due_at=labeled_at
                    )

            summary["evaluated"] = len(pending)
            summary["written_outcomes"] = written
            summary["written_attributions"] = written_attr
            if attribution_incomplete:
                summary["attribution_persist_incomplete"] = True
            connection.commit()
            return summary
        finally:
            connection.close()
