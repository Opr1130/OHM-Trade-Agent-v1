from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
import re
from typing import Any, Iterable

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect
from app.opip.data_platform.freshness import policy_fingerprint
from app.opip.data_platform.migrations import (
    ensure_monthly_partitions,
    refresh_materialized_views,
)
from app.opip.data_platform.streams import STREAM_SPECS


PARTITION_RE = re.compile(
    r"^(screening|observation|stage_transition|intelligence_event|ingested_event)_([0-9]{6})$"
)


def record_maintenance_run(
    connection: Any,
    *,
    status: str,
    detail: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Record one maintenance cycle as freshness evidence.

    Runs under the administrative/maintenance role only; the shipper and
    dashboard roles hold no write grant on ``ops.maintenance_run``.
    """
    if status not in {"SUCCESS", "FAILED", "SKIPPED"}:
        raise ValueError(f"invalid maintenance status: {status}")
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ops.maintenance_run(
                    status, detail, policy_fingerprint, started_at, finished_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    status,
                    (detail or "")[:4000] or None,
                    policy_fingerprint(),
                    started_at.astimezone(timezone.utc),
                    finished_at.astimezone(timezone.utc),
                ),
            )


def prune_analytical_retention(
    connection: Any,
    *,
    now: datetime | None = None,
    raw_months: int = 6,
    ops_days: int = 90,
) -> list[str]:
    """Drop only derived DB partitions after rollups and clean reconciliation.

    Source files and verified file archives are never touched here.
    """
    from psycopg import sql

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_total = current.year * 12 + current.month - 1 - raw_months
    dropped: list[str] = []
    required_streams = [item.name for item in STREAM_SPECS if item.required]
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status <> 'CLEAN'),
                    count(DISTINCT stream_name) FILTER (WHERE status = 'CLEAN')
                FROM ops.reconciliation_run
                WHERE checked_at >= now() - interval '7 days'
                  AND stream_name = ANY(%s)
                """,
                (required_streams,),
            )
            non_clean, clean_streams = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM ops.dead_letter WHERE resolved_at IS NULL"
            )
            unresolved = int(cursor.fetchone()[0])
            if (
                int(clean_streams) != len(required_streams)
                or int(non_clean) > 0
                or unresolved > 0
            ):
                return []
            cursor.execute(
                """
                SELECT ns.nspname, child.relname
                FROM pg_inherits inheritance
                JOIN pg_class child ON child.oid = inheritance.inhrelid
                JOIN pg_namespace ns ON ns.oid = child.relnamespace
                JOIN pg_class parent ON parent.oid = inheritance.inhparent
                WHERE ns.nspname IN ('market', 'lifecycle', 'signal', 'raw')
                  AND parent.relname IN (
                      'screening', 'observation', 'stage_transition',
                      'intelligence_event', 'ingested_event'
                  )
                """
            )
            for schema_name, child_name in cursor.fetchall():
                match = PARTITION_RE.match(str(child_name))
                if not match:
                    continue
                year_month = match.group(2)
                child_total = int(year_month[:4]) * 12 + int(year_month[4:]) - 1
                if child_total >= cutoff_total:
                    continue
                cursor.execute(
                    sql.SQL("DROP TABLE {}.{}").format(
                        sql.Identifier(str(schema_name)),
                        sql.Identifier(str(child_name)),
                    )
                )
                dropped.append(f"{schema_name}.{child_name}")
            cursor.execute(
                "DELETE FROM ops.dead_letter WHERE first_seen_at < now() - make_interval(days => %s) AND resolved_at IS NOT NULL",
                (ops_days,),
            )
            cursor.execute(
                "DELETE FROM ops.reconciliation_run WHERE checked_at < now() - make_interval(days => %s)",
                (ops_days,),
            )
    return dropped


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain the O'Pip analytical store")
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    config = DataPlatformConfig.from_env()
    dsn = os.getenv("OPIP_ANALYTICS_ADMIN_DATABASE_URL") or config.database_url
    if not dsn:
        raise RuntimeError("OPIP_ANALYTICS_DATABASE_URL is required")
    started_at = datetime.now(timezone.utc)
    status = "SUCCESS"
    detail: str | None = None
    dropped: list[str] = []
    try:
        with connect(
            dsn,
            connect_timeout_seconds=config.connect_timeout_seconds,
            application_name="opip-maintenance",
        ) as connection:
            ensure_monthly_partitions(connection)
            refresh_materialized_views(connection)
            dropped = (
                prune_analytical_retention(connection) if args.prune else []
            )
            record_maintenance_run(
                connection,
                status="SUCCESS",
                detail=f"dropped_partitions={dropped}",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
    except Exception as error:
        status = "FAILED"
        detail = f"{type(error).__name__}: {error}"
        try:
            with connect(
                dsn,
                connect_timeout_seconds=config.connect_timeout_seconds,
                application_name="opip-maintenance",
            ) as connection:
                record_maintenance_run(
                    connection,
                    status=status,
                    detail=detail,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                )
        except Exception:
            pass
        print(f"O'Pip maintenance failed: {detail}")
        return 2
    print(f"O'Pip maintenance complete; dropped_partitions={dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
