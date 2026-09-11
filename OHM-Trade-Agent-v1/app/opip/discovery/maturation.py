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
    relative_path: str
    sha256: str
    path: Path
    sort_key: str


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


def _hot_generation_fingerprint(path: Path) -> tuple[int, str]:
    if not path.exists():
        return 0, ""
    size = path.stat().st_size
    take = min(size, DISCOVERY_HOT_GENERATION_PREFIX_BYTES)
    with path.open("rb") as handle:
        payload = handle.read(take)
    return take, hashlib.sha256(payload).hexdigest()


def _hot_generation_matches(connection: sqlite3.Connection, path: Path) -> bool:
    expected = _state_text(connection, "screening_hot_generation_sha256") or ""
    expected_bytes = _state_int(connection, "screening_hot_generation_bytes", 0)
    if not expected or expected_bytes <= 0:
        return _state_int(connection, "screening_indexed_offset", 0) <= 0
    if not path.exists():
        return False
    if path.stat().st_size < expected_bytes:
        return False
    with path.open("rb") as handle:
        payload = handle.read(expected_bytes)
    return (
        len(payload) == expected_bytes
        and hashlib.sha256(payload).hexdigest() == expected
    )


def _set_hot_generation(connection: sqlite3.Connection, path: Path) -> None:
    nbytes, digest = _hot_generation_fingerprint(path)
    _set_state_int(connection, "screening_hot_generation_bytes", nbytes)
    _set_state_text(connection, "screening_hot_generation_sha256", digest)


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


def _read_virtual_range(
    segments: Sequence[_VerifiedScreeningSegment],
    hot_path: Path | None,
    start: int,
    length: int,
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
) -> bool:
    if offset <= 0:
        return True
    expected = _state_text(connection, "screening_anchor_sha256")
    start = _state_int(connection, "screening_anchor_start", -1)
    size = _state_int(connection, "screening_anchor_size", -1)
    if not expected or start < 0 or size <= 0 or start + size != offset:
        return False
    try:
        payload = _read_virtual_range(segments, hot_path, start, size)
    except RuntimeError:
        return False
    return len(payload) == size and hashlib.sha256(payload).hexdigest() == expected


def _verified_screening_segments(
    archive: BoundedJsonlArchive,
) -> list[_VerifiedScreeningSegment]:
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
    out: list[_VerifiedScreeningSegment] = []
    for digest, row in segments_raw.items():
        if not isinstance(row, Mapping):
            continue
        relative = str(row.get("archive") or "").strip()
        sha = str(row.get("sha256") or digest or "").strip()
        if not relative or not sha:
            continue
        path = (archive.archive_dir / relative).resolve()
        try:
            path.relative_to(archive.archive_dir.resolve())
        except ValueError as exc:
            raise RuntimeError(
                f"DISCOVERY_SCREENING_ARCHIVE_PATH_ESCAPE:{relative}"
            ) from exc
        if not path.is_file():
            cold = (archive.cold_archive_dir / relative).resolve()
            if cold.is_file():
                path = cold
            else:
                matches = [
                    item
                    for item in archive.cold_archive_dir.rglob(Path(relative).name)
                    if item.is_file()
                ]
                if len(matches) != 1:
                    raise RuntimeError(
                        f"DISCOVERY_SCREENING_ARCHIVE_MISSING:{relative}"
                    )
                path = matches[0]
        checksum = path.with_suffix(path.suffix + ".sha256")
        if not checksum.exists():
            raise RuntimeError(f"DISCOVERY_SCREENING_ARCHIVE_MISSING:{relative}")
        tokens = checksum.read_text(encoding="utf-8").split()
        if not tokens:
            raise RuntimeError(
                f"DISCOVERY_SCREENING_ARCHIVE_CHECKSUM_MISMATCH:{relative}"
            )
        actual = archive._sha256_file(path)
        if actual != tokens[0] or actual != sha:
            raise RuntimeError(
                f"DISCOVERY_SCREENING_ARCHIVE_CHECKSUM_MISMATCH:{relative}"
            )
        sort_key = (
            str(row.get("first_visible_at_utc") or "")
            or str(row.get("verified_at_utc") or "")
            or relative
        )
        out.append(
            _VerifiedScreeningSegment(
                relative_path=relative,
                sha256=sha,
                path=path,
                sort_key=f"{sort_key}|{relative}",
            )
        )
    out.sort(key=lambda item: item.sort_key)
    return out


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
    _set_hot_generation(connection, screening_path)
    # Keep explicit hot-offset alias in sync for generation-aware readers.
    _set_state_int(connection, "screening_hot_offset", last_complete)
    connection.commit()
    return last_complete


def _index_archive_segment(
    connection: sqlite3.Connection,
    segment: _VerifiedScreeningSegment,
    *,
    now: datetime,
    start_offset: int = 0,
) -> None:
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
    known = _state_json_list(connection, "screening_generation_archives")
    if segment.relative_path not in known:
        known.append(segment.relative_path)
    _set_state_json_list(connection, "screening_generation_archives", known)


def _migrate_screening_checkpoint_state(
    connection: sqlite3.Connection,
    screening_path: Path,
) -> None:
    """Idempotent additive migration from offset-only production state."""
    if _state_text(connection, "screening_checkpoint_schema") == "generation_v1":
        if _state_text(connection, "screening_hot_offset") is None:
            _set_state_int(
                connection,
                "screening_hot_offset",
                _state_int(connection, "screening_indexed_offset", 0),
            )
        return

    offset = _state_int(connection, "screening_indexed_offset", 0)
    _set_state_int(connection, "screening_hot_offset", offset)
    if not _state_json_list(connection, "screening_consumed_archives"):
        _set_state_json_list(connection, "screening_consumed_archives", [])
    if not _state_json_list(connection, "screening_generation_archives"):
        # Empty means "unknown prior set" so rotation recovery may consider
        # all verified segments when proving continuity after HOT shrink.
        _set_state_json_list(connection, "screening_generation_archives", [])
    if screening_path.exists() and offset <= screening_path.stat().st_size:
        if offset <= 0 or _state_checkpoint_matches(
            connection, screening_path, "screening", offset
        ):
            _set_hot_generation(connection, screening_path)
    _set_state_text(connection, "screening_checkpoint_schema", "generation_v1")
    connection.commit()


def _prove_rotation_prefix(
    connection: sqlite3.Connection,
    segments: Sequence[_VerifiedScreeningSegment],
    screening_path: Path,
    offset: int,
) -> tuple[list[_VerifiedScreeningSegment], int] | None:
    """Return (archive_prefix, hot_offset) when concat(prefix)+HOT matches anchor.

    Rotation always archives a HOT prefix, so candidate reconstructions are
    chronological *suffixes* of verified segments plus the current HOT file.
    """
    if offset <= 0:
        return [], 0
    hot_size = screening_path.stat().st_size if screening_path.exists() else 0
    items = list(segments)
    for suffix_len in range(1, len(items) + 1):
        prefix = items[-suffix_len:]
        prefix_bytes = sum(_decompressed_size(item.path) for item in prefix)
        if prefix_bytes + hot_size < offset:
            continue
        if _anchor_matches_virtual(connection, prefix, screening_path, offset):
            return prefix, max(0, offset - prefix_bytes)
    return None


def reconcile_screening_queue(
    connection: sqlite3.Connection,
    screening_path: Path,
    *,
    now: datetime,
) -> None:
    """Index BROAD_SEARCH screening rows across verified archives + HOT.

    Contract:
    * Same HOT generation resumes from the durable byte offset.
    * Verified bounded-JSONL rotation drains archived predecessor bytes before
      initializing the new HOT generation.
    * Unexplained truncation / divergence still fail closed.
    * Queue upserts are idempotent on ``observation_id``.
    """
    _migrate_screening_checkpoint_state(connection, screening_path)
    archive = screening_evaluations_archive(screening_path)
    all_segments = _verified_screening_segments(archive)
    consumed = set(_state_json_list(connection, "screening_consumed_archives"))
    unconsumed = [item for item in all_segments if item.sha256 not in consumed]

    # Bounded drain of verified archive generations (historical + rotation).
    drained = 0
    for segment in unconsumed:
        if drained >= DISCOVERY_SCREENING_ARCHIVE_SEGMENTS_PER_CYCLE:
            break
        _index_archive_segment(connection, segment, now=now, start_offset=0)
        _mark_segment_consumed(connection, segment)
        connection.commit()
        drained += 1
        consumed.add(segment.sha256)

    unconsumed = [item for item in all_segments if item.sha256 not in consumed]
    indexed_offset = _state_int(connection, "screening_indexed_offset", 0)

    if not screening_path.exists():
        if indexed_offset and not unconsumed:
            raise RuntimeError("DISCOVERY_SCREENING_LEDGER_TRUNCATED")
        return

    size = screening_path.stat().st_size
    generation_ok = _hot_generation_matches(connection, screening_path)
    anchor_ok = _state_checkpoint_matches(
        connection, screening_path, "screening", indexed_offset
    )

    # Case 1 — same HOT generation continuity.
    if indexed_offset <= size and generation_ok and (indexed_offset == 0 or anchor_ok):
        _index_hot_from_offset(
            connection, screening_path, now=now, start_offset=indexed_offset
        )
        return

    if indexed_offset <= size and generation_ok and not anchor_ok:
        raise RuntimeError("DISCOVERY_SCREENING_LEDGER_DIVERGED")

    # Case 2 — HOT rotated (generation changed and/or file shrank). A later
    # append onto the new HOT can make size >= old offset again, so generation
    # mismatch must attempt verified rotation proof before fail-closed.
    proof = _prove_rotation_prefix(
        connection, all_segments, screening_path, indexed_offset
    )
    if proof is None:
        known = set(_state_json_list(connection, "screening_generation_archives"))
        if not known:
            candidates = list(all_segments)
        else:
            candidates = [
                item
                for item in all_segments
                if item.relative_path not in known or item.sha256 not in consumed
            ]
        proof = _prove_rotation_prefix(
            connection, candidates, screening_path, indexed_offset
        )

    if proof is None:
        if unconsumed:
            # More verified segments remain for a later bounded cycle.
            return
        if indexed_offset > size:
            raise RuntimeError("DISCOVERY_SCREENING_LEDGER_TRUNCATED")
        raise RuntimeError("DISCOVERY_SCREENING_LEDGER_DIVERGED")

    prefix, hot_offset = proof
    # Ensure every prefix segment is indexed (idempotent) and marked consumed.
    preceding = 0
    for segment in prefix:
        seg_size = _decompressed_size(segment.path)
        if indexed_offset >= preceding + seg_size:
            local_start = seg_size  # already fully covered by prior HOT progress
        elif indexed_offset > preceding:
            local_start = indexed_offset - preceding
        else:
            local_start = 0
        if segment.sha256 not in consumed:
            if local_start < seg_size:
                _index_archive_segment(
                    connection, segment, now=now, start_offset=local_start
                )
            _mark_segment_consumed(connection, segment)
            connection.commit()
            consumed.add(segment.sha256)
        preceding += seg_size

    # Initialize new HOT generation and continue from mapped offset.
    # Generation fingerprint is committed inside _index_hot_from_offset together
    # with the remapped byte offset so a crash cannot strand a new generation
    # identity against a stale offset.
    _set_state_json_list(
        connection,
        "screening_generation_archives",
        [item.relative_path for item in all_segments],
    )
    _index_hot_from_offset(
        connection, screening_path, now=now, start_offset=hot_offset
    )


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
        connection = open_discovery_state(state_path)
        try:
            reconcile_screening_queue(
                connection, screening_path, now=labeled_at
            )
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
