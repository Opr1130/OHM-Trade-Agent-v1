"""Configurable, bounded retention for derived analytical records only.

The retention window applies to verified, derived projections. It can never
reach canonical JSONL, quarantine evidence, reconciliation or configuration
audit history, unverified rows, or audit-locked rows.

Three invariants are load-bearing:

1. **Dry run is the default.** ``allow_delete=False`` refuses a delete request
   instead of pretending to succeed.
2. **Coverage is written with the deletion, in the same transaction.** Otherwise
   reconciliation would later read an authorized purge as evidence loss.
3. **Reclaimed bytes are only reported when they were actually measured.**
   Freed pages inside a WAL database are not returned to the filesystem, so this
   module reports ``None`` rather than a reclaimable-space estimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.opip.analytics.schema import (
    RETENTION_ELIGIBLE_TABLES,
    RETENTION_PROTECTED_TABLES,
)
from app.opip.analytics.store import write_transaction

ALLOWED_RETENTION_DAYS: tuple[int, ...] = (7, 15, 30, 60, 90)
DEFAULT_RETENTION_DAYS = 30
DEFAULT_BATCH_SIZE = 1_000
DEFAULT_MAX_BATCHES = 20

STATUS_DRY_RUN = "DRY_RUN_COMPLETE"
STATUS_DELETED = "DELETE_APPLIED"
STATUS_REFUSED = "DELETE_NOT_AUTHORIZED"
STATUS_FAILED = "RETENTION_FAILED"

REASON_VERIFIED_ONLY = "ONLY_VERIFIED_DERIVED_ROWS_ELIGIBLE"


class RetentionPolicyError(ValueError):
    """An unsupported retention policy was requested."""


def _utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def validate_retention_days(value: Any) -> int:
    """Return one of exactly 7, 15, 30, 60 or 90. Anything else is refused."""
    if isinstance(value, bool):
        raise RetentionPolicyError("INVALID_RETENTION_DAYS")
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise RetentionPolicyError("INVALID_RETENTION_DAYS") from exc
    if days not in ALLOWED_RETENTION_DAYS:
        raise RetentionPolicyError("RETENTION_DAYS_NOT_ALLOWED")
    return days


def policy_version(days: int) -> str:
    return f"policy:{validate_retention_days(days)}d"


def current_policy(conn: Any) -> dict[str, Any]:
    """Return the recorded policy, or the documented default."""
    try:
        row = conn.execute(
            "SELECT policy_version, retention_days, dry_run, actor, note, "
            "recorded_at_utc FROM retention_policies "
            "ORDER BY recorded_at_utc DESC LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 - an unreadable projection has no policy
        row = None
    if row is None:
        return {
            "policy_version": policy_version(DEFAULT_RETENTION_DAYS),
            "retention_days": DEFAULT_RETENTION_DAYS,
            "dry_run": 1,
            "recorded": False,
            "source": "DOCUMENTED_DEFAULT",
            "allowed_days": list(ALLOWED_RETENTION_DAYS),
        }
    return {
        "policy_version": str(row["policy_version"]),
        "retention_days": int(row["retention_days"]),
        "dry_run": int(row["dry_run"]),
        "recorded": True,
        "source": "RETENTION_POLICIES",
        "actor": row["actor"],
        "note": row["note"],
        "recorded_at_utc": row["recorded_at_utc"],
        "allowed_days": list(ALLOWED_RETENTION_DAYS),
    }


def record_policy(
    conn: Any,
    *,
    retention_days: int,
    actor: str,
    note: str,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record a policy choice. Recording a policy deletes nothing."""
    days = validate_retention_days(retention_days)
    stamp = _utc(now).isoformat()
    version = policy_version(days)
    with write_transaction(conn):
        conn.execute(
            """
            INSERT INTO retention_policies(
                policy_version, retention_days, dry_run, actor, note, recorded_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(policy_version) DO UPDATE SET
                dry_run = excluded.dry_run,
                actor = excluded.actor,
                note = excluded.note,
                recorded_at_utc = excluded.recorded_at_utc
            """,
            (version, days, 1 if dry_run else 0, actor[:120], note[:500], stamp),
        )
    return {"policy_version": version, "retention_days": days, "recorded_at_utc": stamp}


_PROTECTED_COLUMNS = frozenset({"is_verified", "is_audit_locked"})

_ELIGIBLE_WHERE = "observed_at_utc IS NOT NULL AND observed_at_utc < ?"

_PROTECTED_WHERE = (
    "observed_at_utc IS NOT NULL AND observed_at_utc < ? "
    "AND is_verified = 1 AND is_audit_locked = 0"
)

#: The newest ingested record per (source, generation) is retained so the
#: checkpoint's tip hash can always be re-verified. Purging it would turn an
#: authorized retention run into a future false drift report. ``_TIP_GUARD`` is
#: therefore true only for a row that has a newer sibling: those are eligible.
_TIP_GUARD = """
    AND EXISTS (
        SELECT 1 FROM ingested_records tip
        WHERE tip.source_id = ingested_records.source_id
          AND tip.source_generation = ingested_records.source_generation
          AND tip.byte_offset > ingested_records.byte_offset
    )
"""

#: The complementary predicate: rows that *are* the newest for their source and
#: are therefore protected from purging.
_TIP_ONLY = """
    NOT EXISTS (
        SELECT 1 FROM ingested_records tip
        WHERE tip.source_id = ingested_records.source_id
          AND tip.source_generation = ingested_records.source_generation
          AND tip.byte_offset > ingested_records.byte_offset
    )
"""


def _columns(conn: Any, table: str) -> frozenset[str]:
    return frozenset(
        str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")
    )


def _table_clause(conn: Any, table: str) -> tuple[str, str]:
    """Return ``(where, extra)`` for one eligible table."""
    if table not in RETENTION_ELIGIBLE_TABLES:
        raise RetentionPolicyError("TABLE_NOT_RETENTION_ELIGIBLE")
    columns = _columns(conn, table)
    if table == "ingested_records":
        return _ELIGIBLE_WHERE, _TIP_GUARD
    if _PROTECTED_COLUMNS.issubset(columns):
        return _PROTECTED_WHERE, ""
    raise RetentionPolicyError("TABLE_PROTECTION_COLUMNS_MISSING")


@dataclass
class RetentionPreview:
    retention_days: int
    cutoff_utc: str
    dry_run: bool
    eligible: dict[str, int] = field(default_factory=dict)
    protected_unverified: int = 0
    protected_locked: int = 0
    protected_newest_tip: int = 0
    tables_considered: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "retention_days": self.retention_days,
            "cutoff_utc": self.cutoff_utc,
            "dry_run": self.dry_run,
            "eligible": dict(self.eligible),
            "eligible_total": sum(self.eligible.values()),
            "protected_unverified": self.protected_unverified,
            "protected_locked": self.protected_locked,
            "protected_newest_tip": self.protected_newest_tip,
            "tables_considered": self.tables_considered,
            "protected_tables": list(RETENTION_PROTECTED_TABLES),
        }


def preview(
    conn: Any,
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> RetentionPreview:
    """Count what a purge *would* remove. Never deletes and never estimates bytes."""
    days = validate_retention_days(
        retention_days
        if retention_days is not None
        else current_policy(conn)["retention_days"]
    )
    cutoff = (_utc(now) - timedelta(days=days)).isoformat()
    result = RetentionPreview(days, cutoff, True)
    for table in RETENTION_ELIGIBLE_TABLES:
        try:
            where, extra = _table_clause(conn, table)
        except RetentionPolicyError:
            continue
        result.tables_considered += 1
        row = conn.execute(
            f"SELECT COUNT(*) AS total FROM {table} WHERE {where} {extra}",  # nosec B608 - table allowlisted, values bound
            (cutoff,),
        ).fetchone()
        result.eligible[table] = int(row["total"] or 0) if row else 0
        columns = _columns(conn, table)
        if _PROTECTED_COLUMNS.issubset(columns):
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM "  # nosec B608 - table is allowlisted, values are bound
                f"{table} WHERE {_ELIGIBLE_WHERE} AND is_verified = 0",
                (cutoff,),
            ).fetchone()
            result.protected_unverified += int(row["total"] or 0) if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM "  # nosec B608 - table is allowlisted, values are bound
                f"{table} WHERE {_ELIGIBLE_WHERE} AND is_audit_locked = 1",
                (cutoff,),
            ).fetchone()
            result.protected_locked += int(row["total"] or 0) if row else 0
        if table == "ingested_records":
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM ingested_records "  # nosec B608 - fixed table, values are bound
                f"WHERE {_ELIGIBLE_WHERE} AND {_TIP_ONLY}",
                (cutoff,),
            ).fetchone()
            result.protected_newest_tip += int(row["total"] or 0) if row else 0
    return result



def _log_run(
    conn: Any,
    *,
    run_id: str,
    version: str,
    days: int,
    stamp: str,
    dry_run: bool,
    considered: int,
    deleted: int,
    unverified: int,
    locked: int,
    status: str,
    error_code: str | None = None,
    duration_ms: float | None = None,
    memory_peak_kib: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO retention_cleanup_logs(
            run_id, policy_version, retention_days, started_at_utc, finished_at_utc,
            duration_ms, dry_run, rows_considered, rows_deleted,
            rows_protected_unverified, rows_protected_locked,
            rows_protected_referenced, bytes_reclaimed, memory_peak_kib,
            status, error_code
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)
        """,
        (
            run_id,
            version,
            days,
            stamp,
            _utc().isoformat(),
            duration_ms,
            1 if dry_run else 0,
            considered,
            deleted,
            unverified,
            locked,
            memory_peak_kib,
            status,
            error_code,
        ),
    )


def housekeeping(
    conn: Any,
    *,
    retention_days: int | None = None,
    dry_run: bool = True,
    allow_delete: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
    actor: str = "control-center",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one bounded housekeeping pass. Deleting requires explicit authority."""
    import time
    import tracemalloc
    import uuid

    started = time.monotonic()
    tracing_here = False
    if not tracemalloc.is_tracing():
        tracemalloc.start()
        tracing_here = True
    moment = _utc(now)
    stamp = moment.isoformat()
    days = validate_retention_days(
        retention_days
        if retention_days is not None
        else current_policy(conn)["retention_days"]
    )
    version = policy_version(days)
    # A run id must be unique even when two runs share a timestamp; a repeated
    # run is a new auditable event, not an overwrite of the previous one.
    run_id = f"retention:{stamp}:{uuid.uuid4().hex[:8]}"
    batch_size = max(1, min(int(batch_size), 10_000))
    max_batches = max(1, min(int(max_batches), 200))
    snapshot = preview(conn, retention_days=days, now=moment)
    considered = sum(snapshot.eligible.values())
    delete_requested = not dry_run

    if delete_requested and not allow_delete:
        with write_transaction(conn):
            _log_run(
                conn,
                run_id=run_id,
                version=version,
                days=days,
                stamp=stamp,
                dry_run=True,
                considered=considered,
                deleted=0,
                unverified=snapshot.protected_unverified,
                locked=snapshot.protected_locked,
                status=STATUS_REFUSED,
                error_code=STATUS_REFUSED,
            )
        if tracing_here:
            tracemalloc.stop()
        return {
            "run_id": run_id,
            "status": STATUS_REFUSED,
            "reason_code": STATUS_REFUSED,
            "retention_days": days,
            "cutoff_utc": snapshot.cutoff_utc,
            "dry_run": True,
            "rows_considered": considered,
            "rows_deleted": 0,
            "bytes_reclaimed": None,
            "detail": "retention deletion is disabled until it is explicitly authorized",
            "preview": snapshot.as_dict(),
        }

    if not delete_requested:
        with write_transaction(conn):
            _log_run(
                conn,
                run_id=run_id,
                version=version,
                days=days,
                stamp=stamp,
                dry_run=True,
                considered=considered,
                deleted=0,
                unverified=snapshot.protected_unverified,
                locked=snapshot.protected_locked,
                status=STATUS_DRY_RUN,
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        memory_peak = (
            tracemalloc.get_traced_memory()[1] // 1024 if tracing_here else None
        )
        if tracing_here:
            tracemalloc.stop()
        return {
            "run_id": run_id,
            "status": STATUS_DRY_RUN,
            "retention_days": days,
            "cutoff_utc": snapshot.cutoff_utc,
            "dry_run": True,
            "rows_considered": considered,
            "rows_deleted": 0,
            "bytes_reclaimed": None,
            "memory_peak_kib": memory_peak,
            "detail": REASON_VERIFIED_ONLY,
            "preview": snapshot.as_dict(),
        }


    deleted_total = 0
    coverage: dict[tuple[str, int], dict[str, Any]] = {}
    with write_transaction(conn):
        for table in RETENTION_ELIGIBLE_TABLES:
            try:
                where, extra = _table_clause(conn, table)
            except RetentionPolicyError:
                continue
            for _ in range(max_batches):
                if table == "ingested_records":
                    ids = [
                        int(row["rowid"])
                        for row in conn.execute(
                            "SELECT rowid FROM ingested_records "  # nosec B608 - fixed table, values are bound
                            f"WHERE {where} {extra} LIMIT ?",
                            (snapshot.cutoff_utc, batch_size),
                        )
                    ]
                    if not ids:
                        break
                    placeholders = ",".join("?" for _ in ids)
                    summary = conn.execute(
                        "SELECT source_id, source_generation, COUNT(*) AS total, "  # nosec B608 - fixed table, ids bound
                        "MAX(observed_at_utc) AS tip FROM ingested_records "
                        f"WHERE rowid IN ({placeholders}) "
                        "GROUP BY source_id, source_generation",
                        ids,
                    ).fetchall()
                    for row in summary:
                        key = (str(row["source_id"]), int(row["source_generation"]))
                        entry = coverage.setdefault(
                            key, {"records": 0, "through": snapshot.cutoff_utc}
                        )
                        entry["records"] += int(row["total"] or 0)
                        tip = row["tip"]
                        if tip and str(tip) > str(entry["through"]):
                            entry["through"] = str(tip)
                    cursor = conn.execute(
                        f"DELETE FROM ingested_records WHERE rowid IN ({placeholders})",  # nosec B608 - placeholders only
                        ids,
                    )
                else:
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE rowid IN ("  # nosec B608 - table allowlisted, values bound
                        f"SELECT rowid FROM {table} WHERE {where} {extra} LIMIT ?)",
                        (snapshot.cutoff_utc, batch_size),
                    )
                deleted = int(cursor.rowcount or 0)
                deleted_total += deleted
                if deleted < batch_size:
                    break
        for (source_id, generation), entry in coverage.items():
            if not entry["records"]:
                continue
            conn.execute(
                """
                INSERT INTO retention_coverage(
                    coverage_key, source_id, source_generation, purged_through_utc,
                    records_purged, recorded_at_utc
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(coverage_key) DO UPDATE SET
                    records_purged = retention_coverage.records_purged
                                     + excluded.records_purged,
                    purged_through_utc = excluded.purged_through_utc,
                    recorded_at_utc = excluded.recorded_at_utc
                """,
                (
                    f"{source_id}:{generation}",
                    source_id,
                    generation,
                    entry["through"],
                    entry["records"],
                    stamp,
                ),
            )
        _log_run(
            conn,
            run_id=run_id,
            version=version,
            days=days,
            stamp=stamp,
            dry_run=False,
            considered=considered,
            deleted=deleted_total,
            unverified=snapshot.protected_unverified,
            locked=snapshot.protected_locked,
            status=STATUS_DELETED,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )
    memory_peak = tracemalloc.get_traced_memory()[1] // 1024 if tracing_here else None
    if tracing_here:
        tracemalloc.stop()
    return {
        "run_id": run_id,
        "status": STATUS_DELETED,
        "retention_days": days,
        "cutoff_utc": snapshot.cutoff_utc,
        "dry_run": False,
        "rows_considered": considered,
        "rows_deleted": deleted_total,
        "rows_protected_unverified": snapshot.protected_unverified,
        "rows_protected_locked": snapshot.protected_locked,
        "bytes_reclaimed": None,
        "memory_peak_kib": memory_peak,
        "detail": "DELETE_APPLIED_WITHOUT_VACUUM; reclaimed bytes not measured",
        "coverage": [
            {"source_id": key[0], "source_generation": key[1], **value}
            for key, value in coverage.items()
        ],
        "preview": snapshot.as_dict(),
    }


def recent_runs(conn: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    """Bounded tail of housekeeping telemetry for the Data & Retention pane."""
    limit = max(1, min(int(limit), 50))
    rows = conn.execute(
        "SELECT run_id, policy_version, retention_days, started_at_utc, "
        "duration_ms, dry_run, rows_considered, rows_deleted, "
        "rows_protected_unverified, rows_protected_locked, bytes_reclaimed, "
        "status, error_code FROM retention_cleanup_logs "
        "ORDER BY started_at_utc DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]

