"""Incremental, idempotent ingestion of canonical evidence into the projection.

Guarantees this module is written to keep:

* Only registered sources are read, one complete newline-delimited record at a
  time. A trailing partial line is never ingested and never advances the offset.
* A record is applied and the checkpoint advanced in the *same* transaction, so
  a crash either loses both or keeps both. Re-running after a crash re-reads the
  same bytes and de-duplicates them by ``(source, generation, byte_offset)``.
* An existing verified row is never overwritten: entity writes are
  ``INSERT OR IGNORE`` and a conflicting replay is counted as a duplicate.
* A malformed or schema-invalid record is quarantined with its reason and its
  raw excerpt, and the source is then reported as quarantined - never clean.
* A replaced or truncated file starts a new generation instead of silently
  resuming at an offset that no longer means anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from app.opip.analytics import schema
from app.opip.analytics.sources import (
    DOCUMENT_KINDS,
    SourceDescriptor,
    file_identity,
)
from app.opip.analytics.store import write_transaction

#: One record larger than this is quarantined rather than loaded.
MAX_RECORD_BYTES = 256 * 1024
#: A whole-document source larger than this is deferred, not buffered.
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
EXCERPT_CHARS = 512
READ_CHUNK_BYTES = 64 * 1024

STATUS_INGESTED = "INGESTED"
STATUS_NO_CHANGE = "NO_CHANGE"
STATUS_DEFERRED = "DEFERRED"
STATUS_QUARANTINED = "QUARANTINED"
STATUS_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
STATUS_MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
STATUS_SCHEMA_MISMATCH = "SCHEMA_MISMATCH"

REASON_SOURCE_MISSING = "SOURCE_MISSING"
REASON_ATTEMPT_IN_PROGRESS = "ATTEMPT_IN_PROGRESS"
REASON_MANIFEST_HASH_MISMATCH = "MANIFEST_HASH_MISMATCH"
REASON_MANIFEST_UNREADABLE = "MANIFEST_UNREADABLE"
REASON_MANIFEST_HASH_UNVERIFIABLE = "MANIFEST_HASH_UNVERIFIABLE"
REASON_DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
REASON_IDENTITY_CHANGED = "SOURCE_IDENTITY_CHANGED"
REASON_TRUNCATED = "SOURCE_TRUNCATED"

#: Fixed internal write targets. A table name is never taken from a record.
TARGET_ATTEMPT_EVENTS = "scanner_attempt_events"
TARGET_SNAPSHOTS = "scanner_snapshots"
TARGET_COMPONENTS = "scoring_components"
TARGET_COMPARISONS = "scoring_comparisons"
TARGET_TELEMETRY = "operational_telemetry"

_PROJECTORS: dict[str, Any] = {}


class RecordRejected(ValueError):
    """A well-formed JSON record that cannot be projected."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    """Normalize a recorded timestamp to ISO-8601 UTC, or ``None``."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_path(path: Path, *, limit_bytes: int = 128 * 1024 * 1024) -> str | None:
    """Hash a file in bounded chunks; ``None`` when it exceeds the cap."""
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit_bytes:
                    return None
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


@dataclass
class IngestOutcome:
    """What one bounded ingestion run did to one source."""

    source_id: str
    status: str
    reason_code: str | None = None
    records_read: int = 0
    records_inserted: int = 0
    records_duplicate: int = 0
    records_quarantined: int = 0
    bytes_read: int = 0
    last_offset: int = 0
    generation: int = 0
    has_more: bool = False
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "records_read": self.records_read,
            "records_inserted": self.records_inserted,
            "records_duplicate": self.records_duplicate,
            "records_quarantined": self.records_quarantined,
            "bytes_read": self.bytes_read,
            "last_offset": self.last_offset,
            "generation": self.generation,
            "has_more": self.has_more,
            "detail": self.detail,
        }

@dataclass
class Projected:
    """The rows one accepted record produces."""

    entity_key: str
    observed_at_utc: str | None
    inserts: list[tuple[str, dict[str, Any]]]


def _text(value: Any, limit: int = 200) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _num(value: Any) -> float | int | None:
    """A finite number, or ``None``. Booleans are not scores."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not (value == value and abs(value) != float("inf")):
        return None
    return value


def _json_or_none(value: Any, limit: int = 16 * 1024) -> str | None:
    if value is None:
        return None
    try:
        text = json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return text[:limit]


def _project_attempt_event(
    record: dict, *, record_sha: str, ctx: Mapping[str, Any]
) -> Projected:
    attempt_id = _text(record.get("attempt_id"))
    if not attempt_id:
        raise RecordRejected("ATTEMPT_ID_MISSING")
    seq = record.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        seq = None
    event_key = (
        f"{attempt_id}:{seq}"
        if seq is not None
        else f"{attempt_id}:sha:{record_sha[:16]}"
    )
    observed = _iso(record.get("at") or record.get("created_at"))
    values = {
        "event_key": event_key,
        "attempt_id": attempt_id,
        "event_seq": seq,
        "event_type": _text(record.get("event")),
        "state": _text(record.get("state")),
        "outcome": _text(record.get("outcome")),
        "reason_code": _text(record.get("reason_code")),
        "fence": seq,
        "attempt_created_at_utc": _iso(record.get("created_at")),
        "finished_at_utc": _iso(record.get("finished_at")),
        "container_id": _text(record.get("container_id")),
        "evidence_json": _json_or_none(record.get("evidence")),
    }
    return Projected(event_key, observed, [(TARGET_ATTEMPT_EVENTS, values)])

def _project_snapshot(
    record: dict, *, record_sha: str, ctx: Mapping[str, Any]
) -> Projected:
    symbol = _text(record.get("symbol"))
    if not symbol:
        raise RecordRejected("SNAPSHOT_SYMBOL_MISSING")
    attempt_id = str(ctx.get("attempt_id") or "unknown")
    snapshot_key = f"{attempt_id}:{symbol}"
    # A market snapshot carries no per-symbol timestamp of its own. The attempt
    # time recorded by the worker's manifest is used, and the Control Center
    # labels the column "attempt time" rather than inventing a quote time.
    observed = ctx.get("batch_observed_at")
    values = {
        "snapshot_key": snapshot_key,
        "attempt_id": attempt_id,
        "symbol": symbol,
        "underlying_asset": _text(record.get("underlying_asset")),
        "trade_direction": _text(record.get("trade_direction")),
        "timeframe": _text(record.get("movement_timeframe"), 32),
        "technical_score": _num(record.get("technical_score")),
        "technical_score_version": _text(record.get("technical_score_version"), 32),
        "directional_long_score": _num(record.get("directional_long_score")),
        "directional_short_score": _num(record.get("directional_short_score")),
        "v2_long_score": _num(record.get("v2_long_score")),
        "v2_short_score": _num(record.get("v2_short_score")),
        "v2_feature_status": _text(record.get("v2_feature_status"), 96) or "NOT_COMPUTED",
        "v2_profile_version": _text(record.get("v2_profile_version"), 64),
        "v2_profile_digest": _text(record.get("v2_profile_digest"), 64),
        "outcome_maturity": "NOT_MATURED",
        "rejection_reason": None,
    }
    inserts: list[tuple[str, dict[str, Any]]] = [(TARGET_SNAPSHOTS, values)]
    for direction, field in (
        ("LONG", "v2_long_components"),
        ("SHORT", "v2_short_components"),
    ):
        components = record.get(field)
        if not isinstance(components, dict):
            continue
        for name, points in sorted(components.items())[:32]:
            inserts.append(
                (
                    TARGET_COMPONENTS,
                    {
                        "snapshot_key": snapshot_key,
                        "direction": direction,
                        "component": str(name)[:64],
                        "points": _num(points),
                        "observed_at_utc": observed,
                    },
                )
            )
    return Projected(snapshot_key, observed, inserts)

def _project_comparison(
    record: dict, *, record_sha: str, ctx: Mapping[str, Any]
) -> Projected:
    scan_id = _text(record.get("scan_id"))
    if not scan_id:
        raise RecordRejected("COMPARISON_SCAN_ID_MISSING")
    observed = _iso(record.get("observed_at_utc") or record.get("observed_at"))
    comparison_key = f"{scan_id}:{record_sha[:16]}"
    values = {
        "comparison_key": comparison_key,
        "scan_id": scan_id,
        "model_authority": _text(record.get("model_authority"), 64),
        "v1_threshold": _num(record.get("v1_threshold")),
        "v2_threshold": _num(record.get("v2_threshold")),
        "scanned": _num(record.get("scanned")),
        "v2_ready": _num(record.get("v2_ready")),
        "v1_top_json": _json_or_none(record.get("v1_top")),
        "v2_top_json": _json_or_none(record.get("v2_top")),
        "v1_only_json": _json_or_none(record.get("v1_only")),
        "v2_only_json": _json_or_none(record.get("v2_only")),
        "outcome_status": _text(record.get("outcome_status"), 32) or "UNRECORDED",
        "schema_version": _num(record.get("schema_version")),
    }
    return Projected(comparison_key, observed, [(TARGET_COMPARISONS, values)])


_PROJECTORS.update(
    {
        "SCAN_ATTEMPT_EVENT": _project_attempt_event,
        "SCAN_SNAPSHOT": _project_snapshot,
        "SCORING_COMPARISON": _project_comparison,
    }
)


@dataclass
class Checkpoint:
    generation: int = 0
    device_id: str | None = None
    file_size: int = 0
    last_offset: int = 0
    last_record_hash: str | None = None
    records_committed: int = 0


def load_checkpoint(conn: Any, source_id: str) -> Checkpoint:
    row = conn.execute(
        "SELECT generation, device_id, file_size, last_offset, "
        "last_record_hash, records_committed FROM ingestion_checkpoints "
        "WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        return Checkpoint()
    return Checkpoint(
        generation=int(row["generation"] or 0),
        device_id=row["device_id"],
        file_size=int(row["file_size"] or 0),
        last_offset=int(row["last_offset"] or 0),
        last_record_hash=row["last_record_hash"],
        records_committed=int(row["records_committed"] or 0),
    )


def save_checkpoint(
    conn: Any, source_id: str, checkpoint: Checkpoint, *, stamp: str, verified: bool
) -> None:
    conn.execute(
        """
        INSERT INTO ingestion_checkpoints(
            source_id, generation, device_id, file_size, last_offset,
            last_record_hash, records_committed, last_verified_at_utc, updated_at_utc
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            generation = excluded.generation,
            device_id = excluded.device_id,
            file_size = excluded.file_size,
            last_offset = excluded.last_offset,
            last_record_hash = excluded.last_record_hash,
            records_committed = excluded.records_committed,
            last_verified_at_utc = COALESCE(excluded.last_verified_at_utc, ingestion_checkpoints.last_verified_at_utc),
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            source_id,
            checkpoint.generation,
            checkpoint.device_id,
            checkpoint.file_size,
            checkpoint.last_offset,
            checkpoint.last_record_hash,
            checkpoint.records_committed,
            stamp if verified else None,
            stamp,
        ),
    )


def record_ingestion_event(conn: Any, outcome: IngestOutcome, *, run_id: str, stamp: str) -> None:
    # ``event_id`` is derived from the run and the source, so a repeated run at
    # the same instant is the *same* run and updates its row instead of failing.
    conn.execute(
        """
        INSERT INTO ingestion_events(
            event_id, source_id, run_id, started_at_utc, finished_at_utc,
            status, records_read, records_inserted, records_duplicate,
            records_quarantined, bytes_read, reason_code, detail
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            finished_at_utc = excluded.finished_at_utc,
            status = excluded.status,
            records_read = excluded.records_read,
            records_inserted = excluded.records_inserted,
            records_duplicate = excluded.records_duplicate,
            records_quarantined = excluded.records_quarantined,
            bytes_read = excluded.bytes_read,
            reason_code = excluded.reason_code,
            detail = excluded.detail
        """,
        (
            f"{run_id}:{outcome.source_id}",
            outcome.source_id,
            run_id,
            stamp,
            stamp,
            outcome.status,
            outcome.records_read,
            outcome.records_inserted,
            outcome.records_duplicate,
            outcome.records_quarantined,
            outcome.bytes_read,
            outcome.reason_code,
            outcome.detail,
        ),
    )


def _quarantine(
    conn: Any,
    descriptor: SourceDescriptor,
    *,
    generation: int,
    byte_offset: int,
    line_number: int,
    raw: bytes,
    reason_code: str,
    detail: str,
    stamp: str,
) -> None:
    record_sha = sha256_bytes(raw)[:64]
    quarantine_id = f"{descriptor.source_id}:{generation}:{byte_offset}:{record_sha[:12]}"
    excerpt = raw[:EXCERPT_CHARS].decode("utf-8", errors="replace")
    conn.execute(
        """
        INSERT OR IGNORE INTO quarantined_records(
            quarantine_id, source_id, source_generation, byte_offset, line_number,
            record_sha256, reason_code, raw_excerpt, observed_at_utc,
            ingested_at_utc, is_verified, is_audit_locked
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
        """,
        (
            quarantine_id,
            descriptor.source_id,
            generation,
            byte_offset,
            line_number,
            record_sha,
            reason_code,
            excerpt,
            None,
            stamp,
        ),
    )


def _manifest_state(
    descriptor: SourceDescriptor,
) -> tuple[str, str | None, str | None, str | None]:
    """Verify the worker's manifest before a batch is trusted (fail closed).

    Returns ``(state, reason, started_at, attempt_id)``. The attempt identity is
    read from the manifest because the staging directory name is a sanitised
    filesystem form of the ledger's ``SCAN:`` identity; projecting the sanitised
    form would make the batch impossible to join to its attempt.
    """
    manifest = descriptor.manifest_path
    if manifest is None or not manifest.is_file():
        return "PENDING", REASON_ATTEMPT_IN_PROGRESS, None, None
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return "UNREADABLE", REASON_MANIFEST_UNREADABLE, None, None
    if not isinstance(doc, dict):
        return "UNREADABLE", REASON_MANIFEST_UNREADABLE, None, None
    started = _iso(doc.get("started_at"))
    attempt_id = _text(doc.get("attempt_id"), 64)
    snapshots = doc.get("snapshots")
    expected = snapshots.get("sha256") if isinstance(snapshots, dict) else None
    if not isinstance(expected, str) or len(expected) != 64:
        return "UNVERIFIABLE", REASON_MANIFEST_HASH_UNVERIFIABLE, started, attempt_id
    actual = sha256_path(descriptor.path)
    if actual is None:
        return "UNVERIFIABLE", REASON_MANIFEST_HASH_UNVERIFIABLE, started, attempt_id
    if actual != expected.strip().lower():
        return "MISMATCH", REASON_MANIFEST_HASH_MISMATCH, started, attempt_id
    return "OK", None, started, attempt_id


def _file_mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def table_columns(conn: Any, table: str, cache: dict[str, frozenset[str]]) -> frozenset[str]:
    if table not in cache:
        cache[table] = frozenset(
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")
        )
    return cache[table]


def insert_row(
    conn: Any, table: str, values: Mapping[str, Any], cache: dict[str, frozenset[str]]
) -> None:
    """Insert one projection row, never overwriting an existing verified row."""
    allowed = table_columns(conn, table, cache)
    usable = {key: value for key, value in values.items() if key in allowed}
    if not usable:
        raise RecordRejected("NO_INSERTABLE_COLUMNS", table)
    columns = ",".join(sorted(usable))
    placeholders = ",".join("?" for _ in usable)
    conn.execute(
        f"INSERT OR IGNORE INTO {table}({columns}) VALUES({placeholders})",
        tuple(usable[key] for key in sorted(usable)),
    )


def _provenance(
    descriptor: SourceDescriptor,
    *,
    generation: int,
    byte_offset: int,
    line_number: int,
    record_sha: str,
    stamp: str,
) -> dict[str, Any]:
    return {
        "source_id": descriptor.source_id,
        "source_generation": generation,
        "byte_offset": byte_offset,
        "line_number": line_number,
        "record_sha256": record_sha,
        "ingested_at_utc": stamp,
        "is_verified": 1,
        "is_audit_locked": 0,
    }

def _quarantine_line(
    conn: Any,
    descriptor: SourceDescriptor,
    *,
    generation: int,
    byte_offset: int,
    line_number: int,
    raw: bytes,
    reason_code: str,
    detail: str,
    stamp: str,
) -> str:
    _quarantine(
        conn,
        descriptor,
        generation=generation,
        byte_offset=byte_offset,
        line_number=line_number,
        raw=raw,
        reason_code=reason_code,
        detail=detail,
        stamp=stamp,
    )
    return "QUARANTINED"


def apply_record(
    conn: Any,
    descriptor: SourceDescriptor,
    *,
    generation: int,
    byte_offset: int,
    line_number: int,
    raw: bytes,
    ctx: Mapping[str, Any],
    stamp: str,
    cache: dict[str, frozenset[str]],
) -> str:
    """Project one complete record. Returns INSERTED | DUPLICATE | QUARANTINED."""
    already = conn.execute(
        "SELECT 1 FROM ingested_records WHERE source_id = ? "
        "AND source_generation = ? AND byte_offset = ?",
        (descriptor.source_id, generation, byte_offset),
    ).fetchone()
    if already is not None:
        return "DUPLICATE"
    if len(raw) > MAX_RECORD_BYTES:
        return _quarantine_line(
            conn, descriptor, generation=generation, byte_offset=byte_offset,
            line_number=line_number, raw=raw, reason_code="RECORD_TOO_LARGE",
            detail=f"{len(raw)} bytes", stamp=stamp,
        )
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _quarantine_line(
            conn, descriptor, generation=generation, byte_offset=byte_offset,
            line_number=line_number, raw=raw, reason_code="MALFORMED_JSON",
            detail="record is not valid UTF-8 JSON", stamp=stamp,
        )
    if not isinstance(record, dict):
        return _quarantine_line(
            conn, descriptor, generation=generation, byte_offset=byte_offset,
            line_number=line_number, raw=raw, reason_code="RECORD_NOT_OBJECT",
            detail=type(record).__name__, stamp=stamp,
        )
    projector = _PROJECTORS.get(descriptor.record_kind)
    if projector is None:
        return _quarantine_line(
            conn, descriptor, generation=generation, byte_offset=byte_offset,
            line_number=line_number, raw=raw,
            reason_code="UNREGISTERED_RECORD_KIND", detail=descriptor.record_kind,
            stamp=stamp,
        )
    record_sha = sha256_bytes(raw)
    try:
        projected = projector(record, record_sha=record_sha, ctx=ctx)
    except RecordRejected as rejected:
        return _quarantine_line(
            conn, descriptor, generation=generation, byte_offset=byte_offset,
            line_number=line_number, raw=raw, reason_code=rejected.reason_code,
            detail=rejected.detail, stamp=stamp,
        )
    provenance = _provenance(
        descriptor,
        generation=generation,
        byte_offset=byte_offset,
        line_number=line_number,
        record_sha=record_sha,
        stamp=stamp,
    )
    for table, values in projected.inserts:
        enriched = dict(values)
        if enriched.get("observed_at_utc") is None:
            enriched["observed_at_utc"] = projected.observed_at_utc
        enriched.update(provenance)
        insert_row(conn, table, enriched, cache)
    conn.execute(
        """
        INSERT INTO ingested_records(
            source_id, source_generation, byte_offset, line_number, record_sha256,
            record_kind, entity_key, observed_at_utc, ingested_at_utc
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            descriptor.source_id,
            generation,
            byte_offset,
            line_number,
            record_sha,
            descriptor.record_kind,
            projected.entity_key,
            projected.observed_at_utc,
            stamp,
        ),
    )
    return "INSERTED"


def _ingest_document(
    conn: Any,
    descriptor: SourceDescriptor,
    *,
    now: datetime | None,
    run_id: str,
    batch_records: int,
) -> IngestOutcome:
    """Ingest a whole-document source (worker scoring provenance).

    Documents have no offsets of their own, so the checkpoint stores the file
    size and the whole-file hash. A document is re-read only when its content
    actually changed, and every distinct version is appended rather than
    replacing the previous one.
    """
    stamp = _utc(now).isoformat()
    path = descriptor.path
    try:
        size = path.stat().st_size
    except OSError:
        return IngestOutcome(descriptor.source_id, STATUS_SOURCE_UNAVAILABLE, REASON_SOURCE_MISSING)
    if size > MAX_DOCUMENT_BYTES:
        return IngestOutcome(
            descriptor.source_id, STATUS_DEFERRED, REASON_DOCUMENT_TOO_LARGE,
            detail=f"{size} bytes",
        )
    try:
        raw = path.read_bytes()
    except OSError:
        return IngestOutcome(descriptor.source_id, STATUS_SOURCE_UNAVAILABLE, REASON_SOURCE_MISSING)
    record_sha = sha256_bytes(raw)
    checkpoint = load_checkpoint(conn, descriptor.source_id)
    if checkpoint.last_record_hash == record_sha and checkpoint.last_offset == len(raw):
        return IngestOutcome(
            descriptor.source_id, STATUS_NO_CHANGE,
            generation=checkpoint.generation, last_offset=checkpoint.last_offset,
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return IngestOutcome(
            descriptor.source_id, STATUS_SCHEMA_MISMATCH, "DOCUMENT_UNPARSABLE",
            detail="document is not valid UTF-8 JSON",
        )
    if not isinstance(document, dict):
        return IngestOutcome(
            descriptor.source_id, STATUS_SCHEMA_MISMATCH, "DOCUMENT_NOT_OBJECT"
        )
    observed = _iso(document.get("finished_at")) or _iso(document.get("started_at"))
    attempt_id = _text(document.get("attempt_id"), 64) or descriptor.attempt_id
    telemetry_key = f"{attempt_id or 'unknown'}:{record_sha[:16]}"
    cache: dict[str, frozenset[str]] = {}
    payload = json.dumps(document, sort_keys=True, allow_nan=False)[:64 * 1024]
    values = {
        "telemetry_key": telemetry_key,
        "section": "worker_status",
        "attempt_id": attempt_id,
        "payload_json": payload,
        "observed_at_utc": observed,
    }
    values.update(
        _provenance(
            descriptor,
            generation=checkpoint.generation,
            byte_offset=0,
            line_number=1,
            record_sha=record_sha,
            stamp=stamp,
        )
    )
    outcome = IngestOutcome(
        descriptor.source_id, STATUS_INGESTED, records_read=1, records_inserted=1,
        bytes_read=len(raw), last_offset=len(raw), generation=checkpoint.generation,
    )
    with write_transaction(conn):
        insert_row(conn, TARGET_TELEMETRY, values, cache)
        save_checkpoint(
            conn,
            descriptor.source_id,
            Checkpoint(
                generation=checkpoint.generation,
                device_id=file_identity(path) or checkpoint.device_id,
                file_size=len(raw),
                last_offset=len(raw),
                last_record_hash=record_sha,
                records_committed=checkpoint.records_committed + 1,
            ),
            stamp=stamp,
            verified=True,
        )
        record_ingestion_event(conn, outcome, run_id=run_id, stamp=stamp)
    return outcome

def ingest_source(
    conn: Any,
    descriptor: SourceDescriptor,
    *,
    batch_records: int = 500,
    max_bytes: int = 2 * 1024 * 1024,
    now: datetime | None = None,
    run_id: str | None = None,
) -> IngestOutcome:
    """Run one bounded, atomic ingestion pass over one source."""
    from app.opip.analytics import sources as source_registry

    stamp = _utc(now).isoformat()
    run_id = run_id or f"ingest:{descriptor.source_id}:{stamp}"
    batch_records = max(1, int(batch_records))
    max_bytes = max(1, int(max_bytes))
    source_registry.register_sources(conn, [descriptor], now=_utc(now))
    path = descriptor.path
    if not path.is_file():
        return IngestOutcome(
            descriptor.source_id, STATUS_SOURCE_UNAVAILABLE, REASON_SOURCE_MISSING
        )
    if descriptor.record_kind in DOCUMENT_KINDS:
        return _ingest_document(
            conn, descriptor, now=now, run_id=run_id, batch_records=batch_records
        )

    ctx: dict[str, Any] = {
        "attempt_id": descriptor.attempt_id,
        "batch_observed_at": _file_mtime_iso(path),
    }
    if descriptor.requires_manifest:
        state, reason, started, manifest_attempt = _manifest_state(descriptor)
        if state == "PENDING":
            return IngestOutcome(descriptor.source_id, STATUS_DEFERRED, reason)
        if state != "OK":
            status = STATUS_MANIFEST_MISMATCH if state == "MISMATCH" else STATUS_DEFERRED
            return IngestOutcome(descriptor.source_id, status, reason)
        ctx["batch_observed_at"] = started or ctx["batch_observed_at"]
        if manifest_attempt:
            ctx["attempt_id"] = manifest_attempt

    try:
        size = path.stat().st_size
    except OSError:
        return IngestOutcome(
            descriptor.source_id, STATUS_SOURCE_UNAVAILABLE, REASON_SOURCE_MISSING
        )
    checkpoint = load_checkpoint(conn, descriptor.source_id)
    identity = file_identity(path)
    generation = checkpoint.generation
    offset = checkpoint.last_offset
    ordinal = checkpoint.records_committed
    last_hash = checkpoint.last_record_hash
    reset_reason: str | None = None
    if identity and checkpoint.device_id and identity != checkpoint.device_id:
        generation += 1
        offset, ordinal, last_hash = 0, 0, None
        reset_reason = REASON_IDENTITY_CHANGED
    elif size < checkpoint.last_offset:
        generation += 1
        offset, ordinal, last_hash = 0, 0, None
        reset_reason = REASON_TRUNCATED
    if offset >= size and reset_reason is None:
        return IngestOutcome(
            descriptor.source_id, STATUS_NO_CHANGE,
            generation=generation, last_offset=offset,
        )


    cache: dict[str, frozenset[str]] = {}
    cursor = offset
    records_read = inserted = duplicates = quarantined = bytes_read = 0
    buffer = b""
    exhausted = False
    with write_transaction(conn):
        with path.open("rb") as handle:
            handle.seek(offset)
            while True:
                budget = max_bytes - bytes_read
                if budget <= 0 or records_read >= batch_records:
                    break
                chunk = handle.read(min(READ_CHUNK_BYTES, budget))
                if not chunk:
                    exhausted = True
                    break
                bytes_read += len(chunk)
                buffer += chunk
                while records_read < batch_records:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    line = buffer[:newline]
                    buffer = buffer[newline + 1 :]
                    start = cursor
                    cursor += newline + 1
                    ordinal += 1
                    if not line.strip():
                        continue
                    records_read += 1
                    result = apply_record(
                        conn,
                        descriptor,
                        generation=generation,
                        byte_offset=start,
                        line_number=ordinal,
                        raw=line,
                        ctx=ctx,
                        stamp=stamp,
                        cache=cache,
                    )
                    if result == "INSERTED":
                        inserted += 1
                        last_hash = sha256_bytes(line)
                    elif result == "DUPLICATE":
                        duplicates += 1
                        last_hash = sha256_bytes(line)
                    else:
                        quarantined += 1
        if quarantined:
            status = STATUS_QUARANTINED
        elif records_read:
            status = STATUS_INGESTED
        else:
            status = STATUS_NO_CHANGE
        outcome = IngestOutcome(
            source_id=descriptor.source_id,
            status=status,
            reason_code=reset_reason,
            records_read=records_read,
            records_inserted=inserted,
            records_duplicate=duplicates,
            records_quarantined=quarantined,
            bytes_read=bytes_read,
            last_offset=cursor,
            generation=generation,
            has_more=not exhausted,
            detail=f"quarantined={quarantined}" if quarantined else None,
        )
        save_checkpoint(
            conn,
            descriptor.source_id,
            Checkpoint(
                generation=generation,
                device_id=identity or checkpoint.device_id,
                file_size=size,
                last_offset=cursor,
                last_record_hash=last_hash,
                records_committed=ordinal,
            ),
            stamp=stamp,
            verified=True,
        )
        record_ingestion_event(conn, outcome, run_id=run_id, stamp=stamp)
    return outcome


def ingest_all(
    conn: Any,
    descriptors: list[SourceDescriptor],
    *,
    batch_records: int = 500,
    max_bytes: int = 2 * 1024 * 1024,
    max_runs_per_source: int = 3,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Ingest every registered source, bounded, newest evidence first.

    A failure on one source never stops the others and never becomes a success:
    the failing source reports ``UNAVAILABLE`` with its reason.
    """
    moment = _utc(now)
    stamp = moment.isoformat()
    results: list[dict[str, Any]] = []
    for descriptor in descriptors:
        for run in range(max(1, int(max_runs_per_source))):
            run_id = f"ingest:{descriptor.source_id}:{stamp}:{run}"
            try:
                outcome = ingest_source(
                    conn,
                    descriptor,
                    batch_records=batch_records,
                    max_bytes=max_bytes,
                    now=moment,
                    run_id=run_id,
                )
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                results.append(
                    {
                        "source_id": descriptor.source_id,
                        "status": STATUS_SOURCE_UNAVAILABLE,
                        "reason_code": f"INGEST_EXCEPTION_{type(exc).__name__.upper()}",
                        "detail": str(exc)[:200],
                    }
                )
                break
            results.append(outcome.as_dict())
            if not outcome.has_more:
                break
    return results


def ensure_projection(conn: Any) -> int:
    """Create or migrate the projection schema."""
    return schema.ensure_schema(conn)

