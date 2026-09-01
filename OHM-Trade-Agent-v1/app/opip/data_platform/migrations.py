from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Iterable

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect


MIGRATION_RE = re.compile(r"^(?P<version>[0-9]{3})_(?P<name>[a-z0-9_]+)\.sql$")
MIGRATION_LOCK_ID = 6_270_051_701
MIGRATION_ROOT = Path(__file__).with_name("migrations")
PARTITIONED_TABLES = (
    ("market", "screening", "observed_at"),
    ("market", "observation", "observed_at"),
    ("lifecycle", "stage_transition", "occurred_at"),
    ("signal", "intelligence_event", "observed_at"),
    ("raw", "ingested_event", "observed_at"),
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sha256: str


def discover_migrations(root: Path = MIGRATION_ROOT) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(root.glob("*.sql")):
        match = MIGRATION_RE.match(path.name)
        if not match:
            raise ValueError(f"invalid migration filename: {path.name}")
        payload = path.read_bytes()
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                path=path,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    versions = [item.version for item in migrations]
    if versions != sorted(set(versions)):
        raise ValueError("migration versions must be unique and ordered")
    return migrations


def apply_migrations(connection: Any, *, root: Path = MIGRATION_ROOT) -> list[int]:
    applied: list[int] = []
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            cursor.execute(
                """
                CREATE SCHEMA IF NOT EXISTS ops;
                CREATE TABLE IF NOT EXISTS ops.schema_version (
                    version integer PRIMARY KEY,
                    name text NOT NULL,
                    sha256 text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute("SELECT version, sha256 FROM ops.schema_version")
            existing = {int(row[0]): str(row[1]) for row in cursor.fetchall()}
            for migration in discover_migrations(root):
                known = existing.get(migration.version)
                if known is not None:
                    if known != migration.sha256:
                        raise RuntimeError(
                            f"migration {migration.version} checksum changed"
                        )
                    continue
                cursor.execute(migration.path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO ops.schema_version(version, name, sha256) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.sha256),
                )
                applied.append(migration.version)
    return applied


def _month_start(value: datetime) -> datetime:
    stamp = value.astimezone(timezone.utc)
    return stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(value: datetime, months: int) -> datetime:
    total = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(total, 12)
    return value.replace(year=year, month=month_index + 1)


def ensure_monthly_partitions(
    connection: Any,
    *,
    anchor: datetime | None = None,
    months_before: int = 3,
    months_after: int = 2,
) -> list[str]:
    from psycopg import sql

    current = _month_start(anchor or datetime.now(timezone.utc))
    created: list[str] = []
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID + 1,))
            for offset in range(-months_before, months_after + 1):
                start = _add_months(current, offset)
                end = _add_months(start, 1)
                for schema_name, table_name, _time_column in PARTITIONED_TABLES:
                    child = f"{table_name}_{start:%Y%m}"
                    cursor.execute(
                        sql.SQL(
                            "CREATE TABLE IF NOT EXISTS {}.{} PARTITION OF {}.{} "
                            "FOR VALUES FROM ({}) TO ({})"
                        ).format(
                            sql.Identifier(schema_name),
                            sql.Identifier(child),
                            sql.Identifier(schema_name),
                            sql.Identifier(table_name),
                            sql.Literal(start),
                            sql.Literal(end),
                        ),
                    )
                    created.append(f"{schema_name}.{child}")
    return created


def refresh_materialized_views(connection: Any) -> None:
    from psycopg import sql

    views = (
        ("signal", "intelligence_daily_mv"),
        ("market", "attrition_daily_mv"),
        ("lifecycle", "rejection_mix_daily_mv"),
    )
    populated_views: list[tuple[str, str, bool]] = []
    with connection.cursor() as cursor:
        for schema_name, view_name in views:
            cursor.execute(
                "SELECT c.relispopulated FROM pg_class c "
                "WHERE c.oid = %s::regclass",
                (f"{schema_name}.{view_name}",),
            )
            populated_views.append(
                (schema_name, view_name, bool(cursor.fetchone()[0]))
            )

    # A population-state SELECT starts an implicit transaction on the normal
    # admin connection. PostgreSQL forbids CONCURRENTLY inside that block.
    connection.commit()
    prior_autocommit = bool(connection.autocommit)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            for schema_name, view_name, populated in populated_views:
                concurrently = sql.SQL("CONCURRENTLY ") if populated else sql.SQL("")
                cursor.execute(
                    sql.SQL("REFRESH MATERIALIZED VIEW {}{}.{}").format(
                        concurrently,
                        sql.Identifier(schema_name),
                        sql.Identifier(view_name),
                    )
                )
    finally:
        connection.autocommit = prior_autocommit


def provision_login_roles(connection: Any, passwords: dict[str, str]) -> None:
    from psycopg import sql

    expected = {"opip_shipper", "opip_learning", "opip_dashboard"}
    if set(passwords) != expected:
        raise ValueError("all O'Pip role passwords are required")
    if any(len(value) < 20 for value in passwords.values()):
        raise ValueError("O'Pip database passwords must be at least 20 characters")
    with connection.transaction():
        with connection.cursor() as cursor:
            for role, password in passwords.items():
                cursor.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        sql.Identifier(role),
                        sql.Literal(password),
                    )
                )


def _admin_dsn() -> str:
    dsn = os.getenv("OPIP_ANALYTICS_ADMIN_DATABASE_URL")
    if not dsn:
        raise RuntimeError("OPIP_ANALYTICS_ADMIN_DATABASE_URL is required")
    return dsn


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the O'Pip analytics schema")
    parser.add_argument(
        "command",
        choices=("migrate", "partitions", "refresh-views", "provision-roles"),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    config = DataPlatformConfig.from_env()
    with connect(
        _admin_dsn(),
        connect_timeout_seconds=config.connect_timeout_seconds,
        application_name="opip-data-platform-admin",
    ) as connection:
        if args.command == "migrate":
            applied = apply_migrations(connection)
            ensure_monthly_partitions(connection)
            print(f"O'Pip migrations applied: {applied or 'none'}")
        elif args.command == "partitions":
            created = ensure_monthly_partitions(connection)
            print(f"O'Pip partitions ensured: {len(created)}")
        elif args.command == "refresh-views":
            refresh_materialized_views(connection)
            print("O'Pip materialized views refreshed")
        else:
            provision_login_roles(
                connection,
                {
                    "opip_shipper": os.environ["OPIP_SHIPPER_PASSWORD"],
                    "opip_learning": os.environ["OPIP_LEARNING_DATABASE_PASSWORD"],
                    "opip_dashboard": os.environ["OPIP_DASHBOARD_PASSWORD"],
                },
            )
            print("O'Pip least-privilege login roles provisioned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
