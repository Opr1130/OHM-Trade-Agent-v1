from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from typing import Any, Iterable

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect
from app.opip.data_platform.freshness import (
    TYPED_WATERMARK_SQL,
    MaintenanceInput,
    StreamInput,
    classify_freshness,
    stream_policy_snapshot,
)
from app.opip.data_platform.streams import STREAM_SPECS


def _required_stream_readiness(
    streams: list[dict],
    *,
    maximum_lag_seconds: int,
) -> tuple[list[str], list[str], bool]:
    required_streams = sorted(item.name for item in STREAM_SPECS if item.required)
    required_stream_set = set(required_streams)
    present_streams = {str(item["stream_name"]) for item in streams}
    missing_required = sorted(required_stream_set - present_streams)
    required_rows = [
        row for row in streams if str(row["stream_name"]) in required_stream_set
    ]
    healthy = not missing_required and all(
        row["lag_seconds"] is not None
        and row["lag_seconds"] <= maximum_lag_seconds
        and row["unresolved_dead_letters"] == 0
        and row["last_reconciliation_status"] in {None, "CLEAN"}
        for row in required_rows
    )
    return required_streams, missing_required, healthy


def _datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _kind_for(stream_name: str) -> str:
    return next(spec.kind for spec in STREAM_SPECS if spec.name == stream_name)


def _blank_evidence() -> dict[str, Any]:
    return {
        "source_updated_at": None,
        "last_ingested_at": None,
        "typed_watermark_at": None,
        "last_polled_at": None,
        "unresolved_dead_letters": 0,
        "last_reconciliation_status": None,
        "last_reconciled_at": None,
    }


def _canonical_stream_inputs(
    connection: Any,
    policy: dict[str, dict[str, Any]],
) -> list[StreamInput]:
    """Collect per-stream evidence without substituting ingestion for typed data."""
    evidence: dict[str, dict[str, Any]] = {}
    unknown_streams: set[str] = set()
    with connection.cursor() as cursor:
        cursor.execute("SELECT stream_name, updated_at FROM ops.ingest_checkpoint")
        for name, updated_at in cursor.fetchall():
            name = str(name)
            if name not in policy:
                unknown_streams.add(name)
                continue
            evidence.setdefault(name, _blank_evidence())
            evidence[name]["source_updated_at"] = _datetime(updated_at)
            evidence[name]["last_polled_at"] = _datetime(updated_at)

        cursor.execute(
            """
            SELECT stream_name, max(ingested_at)
            FROM raw.ingested_event
            WHERE observed_at >= now() - interval '7 days'
            GROUP BY stream_name
            """
        )
        for name, ingested_at in cursor.fetchall():
            name = str(name)
            if name not in policy:
                unknown_streams.add(name)
                continue
            evidence.setdefault(name, _blank_evidence())
            evidence[name]["last_ingested_at"] = _datetime(ingested_at)

        cursor.execute(
            """
            SELECT stream_name, count(*)
            FROM ops.dead_letter
            WHERE resolved_at IS NULL
            GROUP BY stream_name
            """
        )
        for name, count in cursor.fetchall():
            name = str(name)
            if name not in policy:
                unknown_streams.add(name)
                continue
            evidence.setdefault(name, _blank_evidence())
            evidence[name]["unresolved_dead_letters"] = int(count)

        cursor.execute(
            """
            SELECT DISTINCT ON (stream_name) stream_name, status, checked_at
            FROM ops.reconciliation_run
            ORDER BY stream_name, checked_at DESC
            """
        )
        for name, status, checked_at in cursor.fetchall():
            name = str(name)
            if name not in policy:
                unknown_streams.add(name)
                continue
            evidence.setdefault(name, _blank_evidence())
            evidence[name]["last_reconciliation_status"] = status
            evidence[name]["last_reconciled_at"] = _datetime(checked_at)

        watermark_queries = dict(TYPED_WATERMARK_SQL)
        for name, stream_policy in policy.items():
            if not stream_policy["requires_typed_projection"]:
                continue
            evidence.setdefault(name, _blank_evidence())
            cursor.execute(watermark_queries[_kind_for(name)])
            evidence[name]["typed_watermark_at"] = _datetime(cursor.fetchone()[0])

    for name in policy:
        evidence.setdefault(name, _blank_evidence())

    inputs = [
        StreamInput(
            stream_name=name,
            required=bool(policy[name]["required"]),
            requires_typed_projection=bool(policy[name]["requires_typed_projection"]),
            threshold_seconds=policy[name]["threshold_seconds"],
            source_updated_at=row["source_updated_at"],
            last_ingested_at=row["last_ingested_at"],
            typed_watermark_at=row["typed_watermark_at"],
            last_polled_at=row["last_polled_at"],
            unresolved_dead_letters=row["unresolved_dead_letters"],
            last_reconciliation_status=row["last_reconciliation_status"],
            last_reconciled_at=row["last_reconciled_at"],
            policy_present=True,
        )
        for name, row in evidence.items()
    ]
    inputs.extend(
        StreamInput(
            stream_name=name,
            required=False,
            requires_typed_projection=False,
            threshold_seconds=None,
            source_updated_at=None,
            last_ingested_at=None,
            typed_watermark_at=None,
            last_polled_at=None,
            unresolved_dead_letters=0,
            last_reconciliation_status=None,
            last_reconciled_at=None,
            policy_present=False,
        )
        for name in sorted(unknown_streams)
    )
    return inputs


def _validate_policy_sync(connection: Any) -> bool:
    """Validate complete required-stream policy synchronization."""
    expected_policy = stream_policy_snapshot()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT stream_name, required, requires_typed_projection, threshold_seconds
            FROM ops.required_stream
            """
        )
        db_rows = {row[0]: row[1:] for row in cursor.fetchall()}

    if len(db_rows) != len(expected_policy):
        return False

    for stream_name, expected in expected_policy.items():
        if stream_name not in db_rows:
            return False
        db_required, db_typed, db_threshold = db_rows[stream_name]
        if (
            db_required != expected["required"]
            or db_typed != expected["requires_typed_projection"]
            or db_threshold != expected["threshold_seconds"]
        ):
            return False

    return True


def _maintenance_input(connection: Any) -> MaintenanceInput:
    """Return latest maintenance evidence and compare only current fingerprints."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, finished_at, policy_fingerprint
            FROM ops.maintenance_run
            ORDER BY finished_at DESC, maintenance_id DESC
            LIMIT 1
            """
        )
        latest = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*), count(DISTINCT sync_fingerprint), max(sync_fingerprint)
            FROM ops.required_stream
            """
        )
        policy_count, fingerprint_count, current_fingerprint = cursor.fetchone()

    if int(policy_count) == 0:
        return MaintenanceInput(None, None, False, policy_empty=True)

    # Validate complete policy synchronization
    if not _validate_policy_sync(connection):
        return MaintenanceInput(None, None, True)

    if latest is None:
        return MaintenanceInput(None, None, False)

    drift = (
        int(fingerprint_count) != 1
        or latest[2] is None
        or str(latest[2]) != str(current_fingerprint)
    )
    return MaintenanceInput(str(latest[0]), _datetime(latest[1]), drift)


def build_freshness(connection: Any) -> dict[str, Any]:
    """Build canonical current-time freshness from protected policy and evidence."""
    policy = stream_policy_snapshot()
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM ops.required_stream")
        if not int(cursor.fetchone()[0]):
            policy = {}
    inputs = _canonical_stream_inputs(connection, policy)
    maintenance = _maintenance_input(connection)
    now = datetime.now(timezone.utc)
    result = classify_freshness(inputs, maintenance, now=now)
    streams = {
        name: {
            "status": assessment.status,
            "reason": assessment.reason,
            "reference_at_utc": (
                assessment.reference_at.isoformat()
                if assessment.reference_at is not None
                else None
            ),
            "age_seconds": assessment.age_seconds,
            "invalid_timestamps": sorted(assessment.invalid_timestamps),
        }
        for name, assessment in sorted(result["streams"].items())
    }
    return {
        "status": result["status"],
        "ready": result["ready"],
        "reason": result["reason"],
        "problems": result["problems"],
        "evaluated_at_utc": now.isoformat(),
        "streams": streams,
        "maintenance": {
            "status": result["maintenance"].status,
            "reason": result["maintenance"].reason,
            "configuration_drift": result["maintenance"].configuration_drift,
        },
        "required_streams": sorted(
            name for name, policy_row in policy.items() if policy_row["required"]
        ),
    }


def build_health(connection, *, maximum_lag_seconds: int = 1800) -> dict:
    """Read one transactionally consistent, read-only health snapshot."""
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")

        freshness = build_freshness(connection)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stream_name, lag_seconds, unresolved_dead_letters,
                       last_reconciliation_status, last_reconciled_at,
                       rows_ingested, freshness_status, freshness_reason
                FROM ops.platform_health_v ORDER BY stream_name
                """
            )
            streams = [
                {
                    "stream_name": row[0],
                    "lag_seconds": int(row[1]) if row[1] is not None else None,
                    "unresolved_dead_letters": int(row[2]),
                    "last_reconciliation_status": row[3],
                    "last_reconciled_at": (
                        row[4].isoformat() if isinstance(row[4], datetime) else None
                    ),
                    "rows_ingested": int(row[5]) if row[5] is not None else 0,
                    "freshness_status": row[6],
                    "freshness_reason": row[7],
                }
                for row in cursor.fetchall()
            ]
            cursor.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cursor.fetchone()[0])
            cursor.execute("SELECT max(version) FROM ops.schema_version")
            schema_version = int(cursor.fetchone()[0] or 0)

    return {
        "status": "OK" if freshness["ready"] else "DEGRADED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": schema_version,
        "maximum_lag_seconds": maximum_lag_seconds,
        "database_bytes": database_bytes,
        "streams": streams,
        "required_streams": freshness["required_streams"],
        "missing_required_streams": sorted(
            name
            for name, row in freshness["streams"].items()
            if row["reason"] == "MISSING_STREAM_ROW"
        ),
        "freshness": freshness,
        "last_reconciled_at": {
            row["stream_name"]: row["last_reconciled_at"] for row in streams
        },
        "derived_replica": True,
        "production_files_authoritative": True,
        "kraken_credentials_present": False,
        "telegram_authority_present": False,
        "exchange_write_authority": False,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check O'Pip analytics health")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="fail closed unless the canonical freshness contract is LIVE",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    config = DataPlatformConfig.from_env()
    dsn = config.database_url
    if not dsn:
        raise RuntimeError("OPIP_ANALYTICS_DATABASE_URL is required")
    with connect(
        dsn,
        connect_timeout_seconds=config.connect_timeout_seconds,
        application_name="opip-data-health",
    ) as connection:
        health = build_health(connection)
    print(json.dumps(health, sort_keys=True, default=str))
    if args.require_ready:
        return 0 if health["freshness"]["ready"] else 2
    return 0 if health["status"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
