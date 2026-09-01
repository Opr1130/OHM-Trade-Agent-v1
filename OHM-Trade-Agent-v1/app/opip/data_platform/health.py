from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from typing import Iterable

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect
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
        row["lag_seconds"] <= maximum_lag_seconds
        and row["unresolved_dead_letters"] == 0
        and row["last_reconciliation_status"] in {None, "CLEAN"}
        for row in required_rows
    )
    return required_streams, missing_required, healthy


def build_health(connection, *, maximum_lag_seconds: int = 1800) -> dict:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                """
                SELECT stream_name, lag_seconds, unresolved_dead_letters,
                       last_reconciliation_status, rows_ingested
                FROM ops.platform_health_v ORDER BY stream_name
                """
            )
            streams = [
                {
                    "stream_name": row[0],
                    "lag_seconds": int(row[1]),
                    "unresolved_dead_letters": int(row[2]),
                    "last_reconciliation_status": row[3],
                    "rows_ingested": int(row[4]),
                }
                for row in cursor.fetchall()
            ]
            cursor.execute("SELECT pg_database_size(current_database())")
            database_bytes = int(cursor.fetchone()[0])
            cursor.execute("SELECT max(version) FROM ops.schema_version")
            schema_version = int(cursor.fetchone()[0] or 0)
    required_streams, missing_required, healthy = _required_stream_readiness(
        streams,
        maximum_lag_seconds=maximum_lag_seconds,
    )
    return {
        "status": "OK" if healthy else "DEGRADED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": schema_version,
        "maximum_lag_seconds": maximum_lag_seconds,
        "database_bytes": database_bytes,
        "streams": streams,
        "required_streams": required_streams,
        "missing_required_streams": missing_required,
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
        help="enforce the five-minute rollout-readiness lag threshold",
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
        health = build_health(
            connection,
            maximum_lag_seconds=300 if args.require_ready else 1800,
        )
    print(json.dumps(health, sort_keys=True))
    return 0 if health["status"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
