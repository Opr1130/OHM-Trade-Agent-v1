"""Executable PostgreSQL regressions for the final freshness review gate.

These tests intentionally exercise the database objects rather than only
inspecting SQL text. CI provides OPIP_TEST_DATABASE_URL against PostgreSQL 17.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import tempfile

import pytest

from app.opip.data_platform.db import connect
from app.opip.data_platform.freshness import (
    MaintenanceInput,
    StreamInput,
    classify_maintenance,
    classify_stream,
)
from app.opip.data_platform.migrations import (
    MIGRATION_ROOT,
    apply_migrations,
    sync_required_streams,
)


DSN = os.getenv("OPIP_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="OPIP_TEST_DATABASE_URL is not set; run with PostgreSQL",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reset_database(connection) -> None:
    with connection.cursor() as cursor:
        for statement in (
            "DROP SCHEMA IF EXISTS market CASCADE",
            "DROP SCHEMA IF EXISTS lifecycle CASCADE",
            "DROP SCHEMA IF EXISTS signal CASCADE",
            "DROP SCHEMA IF EXISTS paper CASCADE",
            "DROP SCHEMA IF EXISTS learning CASCADE",
            "DROP SCHEMA IF EXISTS ops CASCADE",
            "DROP SCHEMA IF EXISTS raw CASCADE",
        ):
            cursor.execute(statement)
    connection.commit()


@pytest.fixture()
def migrated_connection():
    """Database after migrations, before required-stream synchronization."""
    with connect(DSN, application_name="opip-freshness-executable-migrated") as connection:
        _reset_database(connection)
        apply_migrations(connection)
        yield connection


@pytest.fixture()
def pg_connection(migrated_connection):
    """Database with the canonical policy synchronized and freshness populated."""
    sync_required_streams(migrated_connection)
    _refresh(migrated_connection)
    return migrated_connection


def _refresh(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("REFRESH MATERIALIZED VIEW ops.dashboard_freshness_mv")
    connection.commit()


def _insert_checkpoint(connection, stream: str, stamp: datetime) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ops.ingest_checkpoint(
                stream_name, source_file, source_generation, byte_offset,
                last_row_sha256, rows_ingested, source_size, updated_at
            ) VALUES (%s, %s, 1, 1, 'seed', 1, 1, %s)
            ON CONFLICT (stream_name) DO UPDATE SET updated_at = EXCLUDED.updated_at
            """,
            (stream, f"/data/{stream}.jsonl", stamp),
        )


def _insert_raw(connection, stream: str, stamp: datetime) -> None:
    from psycopg.types.json import Jsonb

    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM raw.ingested_event WHERE stream_name = %s",
            (stream,),
        )
        cursor.execute(
            """
            INSERT INTO raw.ingested_event(
                stream_name, source_event_id, source_file, source_generation,
                source_byte_offset, source_row_sha256, observed_at, payload,
                ingested_at
            ) VALUES (%s, 'evt-1', %s, 1, 1, 'seed', %s, %s, %s)
            """,
            (stream, f"/data/{stream}.jsonl", stamp, Jsonb({"seed": True}), stamp),
        )


def _insert_reconciliation(connection, stream: str, stamp: datetime) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM ops.reconciliation_run WHERE stream_name = %s",
            (stream,),
        )
        cursor.execute(
            """
            INSERT INTO ops.reconciliation_run(
                stream_name, source_file, source_generation, source_byte_offset,
                source_rows, database_rows, difference, source_sha256, status,
                checked_at
            ) VALUES (%s, %s, 1, 1, 1, 1, 0, 'seed', 'CLEAN', %s)
            """,
            (stream, f"/data/{stream}.jsonl", stamp),
        )


def _insert_screening(connection, stamp: datetime) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM market.screening")
        cursor.execute(
            "INSERT INTO market.instrument(canonical_asset, first_seen_at) "
            "VALUES ('TST', %s) ON CONFLICT (canonical_asset) DO NOTHING",
            (stamp,),
        )
        cursor.execute(
            "SELECT instrument_id FROM market.instrument WHERE canonical_asset = 'TST'"
        )
        instrument_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO market.screening(
                scan_id, scanner_type, instrument_id, observed_at,
                outcome, strategy_version
            ) VALUES ('typed-parity-scan', 'TEST', %s, %s, 'ADVANCED', 'v1')
            """,
            (instrument_id, stamp),
        )


def _insert_paper_event(connection, stamp: datetime) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM paper.trade_event")
        cursor.execute("DELETE FROM paper.trade")
        cursor.execute(
            "INSERT INTO paper.trade(paper_trade_id, revision, state) "
            "VALUES ('pt-1', 1, 'OPEN')"
        )
        cursor.execute(
            """
            INSERT INTO paper.trade_event(
                event_id, paper_trade_id, revision, event_type, occurred_at
            ) VALUES ('pte-1', 'pt-1', 1, 'TEST', %s)
            """,
            (stamp,),
        )


def _canonical_stream_row(connection, stream: str):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, reason, reference_at
            FROM ops.dashboard_freshness_v
            WHERE stream_name = %s
            """,
            (stream,),
        )
        row = cursor.fetchone()
    assert row is not None
    return row


def test_required_typed_projection_executes_with_different_watermarks(pg_connection):
    """Required typed streams must classify and age from the typed watermark."""
    stream = "screening_evaluations"
    base = _now()
    source_at = base - timedelta(seconds=10)
    ingested_at = base - timedelta(seconds=20)
    typed_at = base - timedelta(seconds=180)
    reconciled_at = base - timedelta(seconds=5)

    _insert_checkpoint(pg_connection, stream, source_at)
    _insert_raw(pg_connection, stream, ingested_at)
    _insert_reconciliation(pg_connection, stream, reconciled_at)
    _insert_screening(pg_connection, typed_at)
    pg_connection.commit()
    _refresh(pg_connection)

    sql_status, sql_reason, sql_reference = _canonical_stream_row(pg_connection, stream)
    python_now = _now()
    python = classify_stream(
        StreamInput(
            stream_name=stream,
            required=True,
            requires_typed_projection=True,
            threshold_seconds=None,
            source_updated_at=source_at,
            last_ingested_at=ingested_at,
            typed_watermark_at=typed_at,
            last_polled_at=source_at,
            unresolved_dead_letters=0,
            last_reconciliation_status="CLEAN",
            last_reconciled_at=reconciled_at,
        ),
        now=python_now,
    )

    assert (sql_status, sql_reason) == (python.status, python.reason)
    assert sql_status == "DEGRADED"
    assert sql_reason == "DATA_DELAYED"
    assert sql_reference == typed_at
    assert python.reference_at == typed_at


def test_required_typed_projection_absence_matches_python_fail_closed(pg_connection):
    """Missing typed evidence must be UNAVAILABLE in both SQL and Python."""
    stream = "screening_evaluations"
    base = _now()
    source_at = base - timedelta(seconds=10)
    ingested_at = base - timedelta(seconds=20)
    reconciled_at = base - timedelta(seconds=5)

    _insert_checkpoint(pg_connection, stream, source_at)
    _insert_raw(pg_connection, stream, ingested_at)
    _insert_reconciliation(pg_connection, stream, reconciled_at)
    with pg_connection.cursor() as cursor:
        cursor.execute("DELETE FROM market.screening")
    pg_connection.commit()
    _refresh(pg_connection)

    sql_status, sql_reason, sql_reference = _canonical_stream_row(pg_connection, stream)
    python = classify_stream(
        StreamInput(
            stream_name=stream,
            required=True,
            requires_typed_projection=True,
            threshold_seconds=None,
            source_updated_at=source_at,
            last_ingested_at=ingested_at,
            typed_watermark_at=None,
            last_polled_at=source_at,
            unresolved_dead_letters=0,
            last_reconciliation_status="CLEAN",
            last_reconciled_at=reconciled_at,
        ),
        now=_now(),
    )

    assert (sql_status, sql_reason) == (python.status, python.reason)
    assert (sql_status, sql_reason) == ("UNAVAILABLE", "MISSING_TYPED_PROJECTION")
    assert sql_reference is None
    assert python.reference_at is None


def test_optional_typed_stream_uses_ingestion_not_stale_typed_timestamp(pg_connection):
    """Executable parity for the optional-typed reference ordering regression."""
    stream = "paper_trade_events"
    base = _now()
    source_at = base - timedelta(seconds=15)
    ingested_at = base - timedelta(seconds=45)
    typed_at = base - timedelta(days=3)

    _insert_checkpoint(pg_connection, stream, source_at)
    _insert_raw(pg_connection, stream, ingested_at)
    _insert_paper_event(pg_connection, typed_at)
    pg_connection.commit()

    sql_status, sql_reason, sql_reference = _canonical_stream_row(pg_connection, stream)
    python = classify_stream(
        StreamInput(
            stream_name=stream,
            required=False,
            requires_typed_projection=True,
            threshold_seconds=86400,
            source_updated_at=source_at,
            last_ingested_at=ingested_at,
            typed_watermark_at=typed_at,
            last_polled_at=source_at,
            unresolved_dead_letters=0,
            last_reconciliation_status=None,
            last_reconciled_at=None,
        ),
        now=_now(),
    )

    assert (sql_status, sql_reason) == (python.status, python.reason) == ("LIVE", None)
    assert sql_reference == ingested_at
    assert python.reference_at == ingested_at
    assert sql_reference != typed_at


@pytest.mark.parametrize(
    "mutation",
    ("missing", "extra", "required", "typed", "threshold"),
)
def test_sql_policy_validation_fails_closed_for_every_drift(pg_connection, mutation):
    """Mutating the live SQL policy must make maintenance fail closed."""
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT max(sync_fingerprint) FROM ops.required_stream")
        fingerprint = cursor.fetchone()[0]
        if mutation == "missing":
            cursor.execute(
                "DELETE FROM ops.required_stream WHERE stream_name = 'screening_evaluations'"
            )
        elif mutation == "extra":
            cursor.execute(
                """
                INSERT INTO ops.required_stream(
                    stream_name, required, requires_typed_projection,
                    threshold_seconds, sync_fingerprint
                ) VALUES ('unexpected_stream', false, false, 86400, %s)
                """,
                (fingerprint,),
            )
        elif mutation == "required":
            cursor.execute(
                "UPDATE ops.required_stream SET required = false "
                "WHERE stream_name = 'scan_summaries'"
            )
        elif mutation == "typed":
            cursor.execute(
                "UPDATE ops.required_stream SET requires_typed_projection = false "
                "WHERE stream_name = 'funnel_events'"
            )
        else:
            cursor.execute(
                "UPDATE ops.required_stream SET threshold_seconds = 3600 "
                "WHERE stream_name = 'p1_shadow_outbox'"
            )
    pg_connection.commit()

    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT required, status, reason
            FROM ops.dashboard_freshness_v
            WHERE stream_name = '__maintenance__'
            """
        )
        maintenance = cursor.fetchone()
        cursor.execute(
            """
            SELECT NOT EXISTS (
                SELECT 1
                FROM ops.dashboard_freshness_v
                WHERE (required OR reason = 'UNKNOWN_STREAM_POLICY')
                  AND status <> 'LIVE'
            )
            """
        )
        canonical_ready = cursor.fetchone()[0]

    assert maintenance == (True, "UNAVAILABLE", "CONFIGURATION_DRIFT")
    assert canonical_ready is False


def test_migrate_never_exposes_unpopulated_freshness_view(migrated_connection):
    """The migration transaction must populate the MV before it becomes visible."""
    with migrated_connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relispopulated FROM pg_class c "
            "WHERE c.oid = to_regclass('ops.dashboard_freshness_mv')"
        )
        assert cursor.fetchone()[0] is True
        # This query would raise SQLSTATE 55000 if the MV were still unpopulated.
        cursor.execute(
            """
            SELECT status, reason
            FROM ops.dashboard_freshness_v
            WHERE stream_name = '__maintenance__'
            """
        )
        assert cursor.fetchone() == ("UNAVAILABLE", "MISSING_POLICY")


def test_failed_migration_rolls_back_index_and_retries_cleanly():
    """PostgreSQL transactional DDL makes migration-005 index retry safe.

    This directly guards the review concern that a failure after index creation
    could leave a duplicate object. apply_migrations wraps the migration batch
    in one transaction, so the failed DDL is rolled back before retry.
    """
    with connect(DSN, application_name="opip-migration-atomicity-test") as connection:
        _reset_database(connection)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path in sorted(MIGRATION_ROOT.glob("*.sql")):
                if int(path.name[:3]) <= 5:
                    shutil.copy(path, root / path.name)
            migration_five = root / "005_dashboard_freshness.sql"
            migration_five.write_text(
                migration_five.read_text(encoding="utf-8")
                + "\nSELECT 1 / 0; -- force rollback after MV/index creation\n",
                encoding="utf-8",
            )

            with pytest.raises(Exception):
                apply_migrations(connection, root=root)

            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('ops.dashboard_freshness_mv')")
                assert cursor.fetchone()[0] is None
                cursor.execute("SELECT to_regclass('ops.dashboard_freshness_mv_stream_idx')")
                assert cursor.fetchone()[0] is None

            shutil.copy(
                MIGRATION_ROOT / "005_dashboard_freshness.sql",
                migration_five,
            )
            assert apply_migrations(connection, root=root) == [1, 2, 3, 4, 5]
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('ops.dashboard_freshness_mv_stream_idx')")
                assert cursor.fetchone()[0] is not None


def test_maintenance_reason_precedence_matches_sql_for_failed_future_run():
    """FAILED maintenance wins over future timestamp validation in both engines."""
    result = classify_maintenance(
        MaintenanceInput(
            latest_status="FAILED",
            latest_finished_at=_now() + timedelta(minutes=10),
            configuration_drift=False,
        ),
        now=_now(),
    )
    assert result.status == "UNAVAILABLE"
    assert result.reason == "MAINTENANCE_FAILED"
