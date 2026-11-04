"""Bounded analytical maintenance entrypoint for the O'Pip analytical projection.

This job reads canonical evidence that other services already wrote and
maintains a derived SQLite projection. It is deliberately **not** scheduled by
Compose in this increment: adding a scheduler entry changes the trading host's
runtime shape and needs its own approved deployment change.

Usage (from ``OHM-Trade-Agent-v1/``)::

    python -m app.jobs.opip_analytics_maintenance ingest
    python -m app.jobs.opip_analytics_maintenance reconcile
    python -m app.jobs.opip_analytics_maintenance retention --days 30
    python -m app.jobs.opip_analytics_maintenance retention --days 30 --apply

``retention`` is dry-run unless ``--apply`` is passed *and* the environment
gate ``OPIP_ENABLE_RETENTION_DELETE`` is set. Neither flag can reach canonical
JSONL: retention only ever touches the derived projection.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.core.config import get_settings
from app.opip.analytics import reconcile, retention, runtime
from app.opip.analytics import schema as analytics_schema
from app.opip.analytics import sources as source_registry
from app.opip.analytics.ingest import ingest_all
from app.opip.analytics.store import connect_writer
from app.opip.config import staging as config_staging

EXIT_OK = 0
EXIT_REFUSED = 78


def _connect():
    settings = get_settings()
    conn = connect_writer(
        source_registry.analytics_db_path(settings),
        busy_timeout_ms=int(
            getattr(settings, "opip_analytics_busy_timeout_ms", 5_000)
        ),
    )
    analytics_schema.ensure_schema(conn)
    return settings, conn


def _ingest(settings: Any, conn: Any) -> dict[str, Any]:
    results = ingest_all(
        conn,
        source_registry.all_sources(settings),
        batch_records=int(
            getattr(settings, "opip_analytics_ingest_batch_records", 500)
        ),
        max_bytes=int(getattr(settings, "opip_analytics_ingest_max_bytes", 2_097_152)),
    )
    return {"command": "ingest", "sources": results}


def _reconcile(settings: Any, conn: Any) -> dict[str, Any]:
    result = reconcile.reconcile_all(conn, source_registry.all_sources(settings))
    result["command"] = "reconcile"
    return result


def _retention(
    settings: Any, conn: Any, *, days: int | None, apply: bool
) -> dict[str, Any]:
    resolved = retention.validate_retention_days(
        days if days is not None else getattr(settings, "opip_analytics_retention_days", 30)
    )
    result = retention.housekeeping(
        conn,
        retention_days=resolved,
        dry_run=not apply,
        allow_delete=bool(getattr(settings, "opip_enable_retention_delete", False)),
        batch_size=int(getattr(settings, "opip_analytics_ingest_batch_records", 500)),
        actor="cli:opip-analytics-maintenance",
    )
    result["command"] = "retention"
    return result


def _registry(_settings: Any, conn: Any) -> dict[str, Any]:
    return {"command": "registry", "definitions": config_staging.sync_registry(conn)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ingest", "reconcile", "retention", "registry"))
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    parsed = parser.parse_args(argv)
    settings, conn = _connect()
    try:
        if parsed.command == "ingest":
            payload = _ingest(settings, conn)
        elif parsed.command == "reconcile":
            payload = _reconcile(settings, conn)
        elif parsed.command == "retention":
            payload = _retention(settings, conn, days=parsed.days, apply=parsed.apply)
            if payload.get("status") == retention.STATUS_REFUSED:
                payload["generated_at_utc"] = runtime._utc().isoformat()
                print(json.dumps(payload, sort_keys=True, default=str))
                return EXIT_REFUSED
        else:
            payload = _registry(settings, conn)
    finally:
        conn.close()
    payload["generated_at_utc"] = runtime._utc().isoformat()
    print(json.dumps(payload, sort_keys=True, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
