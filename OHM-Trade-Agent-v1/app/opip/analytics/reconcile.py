"""Bounded, retention-aware reconciliation of the projection against evidence.

A clean reader query is not proof of anything. This module re-opens the
canonical source, re-hashes sampled records and compares counts before it will
call a projection reconciled.

Retention is not drift. Intentionally purged analytical history is recorded in
``retention_coverage`` and is added back to the expected count, so an authorized
purge is never mistaken for evidence loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.opip.analytics import schema
from app.opip.analytics.ingest import load_checkpoint, sha256_bytes, sha256_path
from app.opip.analytics.sources import DOCUMENT_KINDS, SourceDescriptor
import uuid

STATUS_RECONCILED = "RECONCILED"
STATUS_DRIFT = "DRIFT_DETECTED"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
STATUS_SCHEMA_MISMATCH = "SCHEMA_MISMATCH"

REASON_NO_CHECKPOINT = "NO_INGESTION_CHECKPOINT"
REASON_SOURCE_MISSING = "SOURCE_MISSING"
REASON_SOURCE_TRUNCATED = "SOURCE_SHORTER_THAN_CHECKPOINT"
REASON_COUNT_MISMATCH = "RECORD_COUNT_MISMATCH"
REASON_TIP_HASH_MISMATCH = "TIP_HASH_MISMATCH"
REASON_RECORD_HASH_MISMATCH = "SAMPLED_RECORD_HASH_MISMATCH"
REASON_QUARANTINE_BACKLOG = "QUARANTINE_BACKLOG"
REASON_SCHEMA = "PROJECTION_SCHEMA_MISMATCH"
REASON_CLEAN = "PROJECTION_MATCHES_EVIDENCE"

#: How many records are re-hashed per source per run. Bounded on purpose.
DEFAULT_SAMPLE_RECORDS = 5


@dataclass
class SourceReconciliation:
    source_id: str
    status: str
    reason_code: str | None
    expected_records: int | None = None
    actual_records: int | None = None
    covered_purged_records: int = 0
    quarantined_records: int = 0
    first_mismatch_offset: int | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "expected_records": self.expected_records,
            "actual_records": self.actual_records,
            "covered_purged_records": self.covered_purged_records,
            "quarantined_records": self.quarantined_records,
            "first_mismatch_offset": self.first_mismatch_offset,
            "detail": self.detail,
        }


def _utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def hash_line_at(path: Path, offset: int, *, limit: int = 256 * 1024) -> str | None:
    """Re-hash the physically stored record starting at ``offset``."""
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(limit)
    except OSError:
        return None
    newline = chunk.find(b"\n")
    if newline < 0:
        return None
    return sha256_bytes(chunk[:newline])


def _covered_purged(conn: Any, source_id: str, generation: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(records_purged), 0) AS total FROM retention_coverage "
        "WHERE source_id = ? AND source_generation = ?",
        (source_id, generation),
    ).fetchone()
    return int(row["total"] or 0) if row is not None else 0



def reconcile_source(
    conn: Any,
    descriptor: SourceDescriptor,
    *,
    now: datetime | None = None,
    sample_records: int = DEFAULT_SAMPLE_RECORDS,
) -> SourceReconciliation:
    """Prove, or refuse to prove, one source's projection."""
    if schema.current_version(conn) != schema.SCHEMA_VERSION:
        return SourceReconciliation(
            descriptor.source_id,
            STATUS_SCHEMA_MISMATCH,
            REASON_SCHEMA,
            detail=f"projection={schema.current_version(conn)} "
            f"expected={schema.SCHEMA_VERSION}",
        )
    path = descriptor.path
    if not path.is_file():
        return SourceReconciliation(
            descriptor.source_id, STATUS_SOURCE_UNAVAILABLE, REASON_SOURCE_MISSING
        )
    checkpoint_row = conn.execute(
        "SELECT 1 FROM ingestion_checkpoints WHERE source_id = ?",
        (descriptor.source_id,),
    ).fetchone()
    if checkpoint_row is None:
        return SourceReconciliation(
            descriptor.source_id, STATUS_UNVERIFIED, REASON_NO_CHECKPOINT
        )
    checkpoint = load_checkpoint(conn, descriptor.source_id)
    try:
        size = path.stat().st_size
    except OSError:
        return SourceReconciliation(
            descriptor.source_id, STATUS_SOURCE_UNAVAILABLE, REASON_SOURCE_MISSING
        )
    if descriptor.record_kind in DOCUMENT_KINDS:
        current = sha256_path(path)
        if current is None or current != checkpoint.last_record_hash:
            return SourceReconciliation(
                descriptor.source_id,
                STATUS_DRIFT,
                REASON_TIP_HASH_MISMATCH,
                detail="document changed since it was projected",
            )
        return SourceReconciliation(
            descriptor.source_id,
            STATUS_RECONCILED,
            REASON_CLEAN,
            expected_records=1,
            actual_records=1,
        )
    if size < checkpoint.last_offset:
        return SourceReconciliation(
            descriptor.source_id,
            STATUS_DRIFT,
            REASON_SOURCE_TRUNCATED,
            expected_records=checkpoint.records_committed,
            actual_records=None,
            first_mismatch_offset=checkpoint.last_offset,
            detail=f"file={size} checkpoint_offset={checkpoint.last_offset}",
        )
    generation = checkpoint.generation
    accepted_row = conn.execute(
        "SELECT COUNT(*) AS total FROM ingested_records "
        "WHERE source_id = ? AND source_generation = ?",
        (descriptor.source_id, generation),
    ).fetchone()
    quarantined_row = conn.execute(
        "SELECT COUNT(*) AS total FROM quarantined_records "
        "WHERE source_id = ? AND source_generation = ?",
        (descriptor.source_id, generation),
    ).fetchone()
    actual = int(accepted_row["total"] or 0) if accepted_row is not None else 0
    quarantined = (
        int(quarantined_row["total"] or 0) if quarantined_row is not None else 0
    )
    covered = _covered_purged(conn, descriptor.source_id, generation)
    expected = checkpoint.records_committed
    if actual + quarantined + covered != expected:
        return SourceReconciliation(
            descriptor.source_id,
            STATUS_DRIFT,
            REASON_COUNT_MISMATCH,
            expected_records=expected,
            actual_records=actual,
            covered_purged_records=covered,
            quarantined_records=quarantined,
            detail=f"accepted={actual} quarantined={quarantined} purged={covered}",
        )
    tip = conn.execute(
        "SELECT record_sha256 FROM ingested_records "
        "WHERE source_id = ? AND source_generation = ? "
        "ORDER BY byte_offset DESC LIMIT 1",
        (descriptor.source_id, generation),
    ).fetchone()
    if (
        tip is not None
        and checkpoint.last_record_hash is not None
        and str(tip["record_sha256"]) != str(checkpoint.last_record_hash)
    ):
        return SourceReconciliation(
            descriptor.source_id,
            STATUS_DRIFT,
            REASON_TIP_HASH_MISMATCH,
            expected_records=expected,
            actual_records=actual,
            covered_purged_records=covered,
            quarantined_records=quarantined,
        )
    sampled = conn.execute(
        "SELECT byte_offset, record_sha256 FROM ingested_records "
        "WHERE source_id = ? AND source_generation = ? "
        "ORDER BY byte_offset LIMIT ?",
        (descriptor.source_id, generation, max(1, int(sample_records))),
    ).fetchall()
    for row in sampled:
        offset = int(row["byte_offset"])
        recomputed = hash_line_at(path, offset)
        if recomputed is None or recomputed != str(row["record_sha256"]):
            return SourceReconciliation(
                descriptor.source_id,
                STATUS_DRIFT,
                REASON_RECORD_HASH_MISMATCH,
                expected_records=expected,
                actual_records=actual,
                covered_purged_records=covered,
                quarantined_records=quarantined,
                first_mismatch_offset=offset,
            )
    if quarantined:
        return SourceReconciliation(
            descriptor.source_id,
            STATUS_UNVERIFIED,
            REASON_QUARANTINE_BACKLOG,
            expected_records=expected,
            actual_records=actual,
            covered_purged_records=covered,
            quarantined_records=quarantined,
            detail="records were quarantined; this source is not clean",
        )
    return SourceReconciliation(
        descriptor.source_id,
        STATUS_RECONCILED,
        REASON_CLEAN,
        expected_records=expected,
        actual_records=actual,
        covered_purged_records=covered,
        quarantined_records=quarantined,
    )



def _run_status(results: list[SourceReconciliation]) -> str:
    statuses = {result.status for result in results}
    if not results:
        return STATUS_UNVERIFIED
    if STATUS_SCHEMA_MISMATCH in statuses:
        return STATUS_SCHEMA_MISMATCH
    if STATUS_DRIFT in statuses:
        return STATUS_DRIFT
    if STATUS_SOURCE_UNAVAILABLE in statuses or STATUS_UNVERIFIED in statuses:
        return STATUS_UNVERIFIED
    if STATUS_IN_PROGRESS in statuses:
        return STATUS_IN_PROGRESS
    return STATUS_RECONCILED


def reconcile_all(
    conn: Any,
    descriptors: list[SourceDescriptor],
    *,
    now: datetime | None = None,
    sample_records: int = DEFAULT_SAMPLE_RECORDS,
) -> dict[str, Any]:
    """Reconcile every registered source and record an auditable run."""
    from app.opip.analytics.store import write_transaction

    moment = _utc(now)
    stamp = moment.isoformat()
    run_id = f"reconcile:{stamp}:{uuid.uuid4().hex[:8]}"
    results = [
        reconcile_source(conn, descriptor, now=moment, sample_records=sample_records)
        for descriptor in descriptors
    ]
    status = _run_status(results)
    drifted = sum(1 for item in results if item.status == STATUS_DRIFT)
    unavailable = sum(
        1 for item in results if item.status == STATUS_SOURCE_UNAVAILABLE
    )
    detail = "; ".join(
        f"{item.source_id}={item.status}"
        + (f"({item.reason_code})" if item.reason_code else "")
        for item in results
    )[:2000]
    with write_transaction(conn):
        conn.execute(
            """
            INSERT INTO reconciliation_runs(
                run_id, started_at_utc, finished_at_utc, status,
                sources_checked, sources_drifted, sources_unavailable, detail
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                stamp,
                stamp,
                status,
                len(results),
                drifted,
                unavailable,
                detail,
            ),
        )
        for item in results:
            conn.execute(
                """
                INSERT INTO reconciliation_audit(
                    audit_id, run_id, source_id, status, expected_records,
                    actual_records, covered_purged_records, first_mismatch_offset,
                    quarantined_records, reason_code, detail, recorded_at_utc
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{run_id}:{item.source_id}",
                    run_id,
                    item.source_id,
                    item.status,
                    item.expected_records,
                    item.actual_records,
                    item.covered_purged_records,
                    item.first_mismatch_offset,
                    item.quarantined_records,
                    item.reason_code,
                    item.detail,
                    stamp,
                ),
            )
    return {
        "run_id": run_id,
        "status": status,
        "reconciled_at_utc": stamp,
        "sources_checked": len(results),
        "sources_drifted": drifted,
        "sources_unavailable": unavailable,
        "sources": [item.as_dict() for item in results],
    }


def latest_status(conn: Any) -> dict[str, Any]:
    """Return the most recent reconciliation verdict for the global header.

    No run at all is ``UNVERIFIED``, never ``RECONCILED``: absence of a
    reconciliation is not evidence that the projection matches its sources.
    """
    try:
        row = conn.execute(
            "SELECT run_id, started_at_utc, finished_at_utc, status, "
            "sources_checked, sources_drifted, sources_unavailable, detail "
            "FROM reconciliation_runs ORDER BY started_at_utc DESC LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 - an unreadable projection is unverified
        return {
            "state": STATUS_UNVERIFIED,
            "reason_code": "RECONCILIATION_UNREADABLE",
            "observed_at_utc": None,
        }
    if row is None:
        return {
            "state": STATUS_UNVERIFIED,
            "reason_code": "NO_RECONCILIATION_RUN",
            "observed_at_utc": None,
        }
    drift: list[dict[str, Any]] = []
    if int(row["sources_drifted"] or 0):
        try:
            drift_rows = conn.execute(
                "SELECT source_id, reason_code, expected_records, actual_records, "
                "first_mismatch_offset FROM reconciliation_audit "
                "WHERE run_id = ? AND status = ? ORDER BY source_id",
                (row["run_id"], STATUS_DRIFT),
            ).fetchall()
        except Exception:  # noqa: BLE001
            drift_rows = []
        drift = [
            {
                "source_id": item["source_id"],
                "reason_code": item["reason_code"],
                "expected_records": item["expected_records"],
                "actual_records": item["actual_records"],
                "first_mismatch_offset": item["first_mismatch_offset"],
            }
            for item in drift_rows
        ]
    return {
        "state": str(row["status"]),
        "run_id": str(row["run_id"]),
        "observed_at_utc": row["finished_at_utc"] or row["started_at_utc"],
        "sources_checked": int(row["sources_checked"] or 0),
        "sources_drifted": int(row["sources_drifted"] or 0),
        "sources_unavailable": int(row["sources_unavailable"] or 0),
        "drift": drift,
        "detail": row["detail"],
    }
