"""Regression coverage for the canonical O'Pip dashboard freshness contract.

Every known false-LIVE condition is reproduced against a real PostgreSQL
database (when available) and mirrored through the Python classifier so the
two engines cannot diverge.  Tests skip cleanly when no local PostgreSQL is
reachable; CI runs them against a disposable instance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from typing import Any

import pytest

from app.opip.data_platform.config import DataPlatformConfig  # noqa: F401
from app.opip.data_platform.db import connect
from app.opip.data_platform.freshness import (
    DEFAULT_MAX_AGE_SECONDS,
    MaintenanceInput,
    StreamInput,
    classify_freshness,
    policy_fingerprint,
    stream_policy_snapshot,
)
from app.opip.data_platform.health import build_freshness, main as health_main
from app.opip.data_platform.migrations import (
    apply_migrations,
    discover_migrations,
    sync_required_streams,
)
from app.opip.data_platform.streams import STREAM_SPECS, TYPED_PROJECTION_KINDS


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("OPIP_TEST_DATABASE_URL", "")


def _now() -> datetime:
    """Real current UTC time; the canonical SQL contract evaluates now()."""
    return datetime.now(timezone.utc)


REQUIRED = [spec.name for spec in STREAM_SPECS if spec.required]

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="OPIP_TEST_DATABASE_URL is not set; run with a local PostgreSQL",
)


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
def pg_connection():
    """Freshly migrated database plus synchronized freshness policy."""
    with connect(DSN, application_name="opip-freshness-test") as connection:
        _reset_database(connection)
        apply_migrations(connection)
        sync_required_streams(connection)
        _refresh_freshness(connection)
        yield connection


def _refresh_freshness(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "REFRESH MATERIALIZED VIEW ops.dashboard_freshness_mv"
        )
    connection.commit()


def _insert_checkpoint(
    connection, stream: str, *, updated_at: datetime | None
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ops.ingest_checkpoint(
                stream_name, source_file, source_generation, byte_offset,
                last_row_sha256, rows_ingested, source_size, updated_at
            ) VALUES (%s, %s, 1, 0, 'seed', 0, 0, %s)
            ON CONFLICT (stream_name) DO UPDATE SET updated_at = EXCLUDED.updated_at
            """,
            (stream, f"/data/{stream}.jsonl", updated_at or _now()),
        )


def _insert_raw(connection, stream: str, *, observed_at: datetime) -> None:
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
            ) VALUES (
                %s, %s, %s, 1, %s, %s, %s, %s, %s
            ) ON CONFLICT DO NOTHING
            """,
            (
                stream,
                f"{stream}-event-1",
                f"/data/{stream}.jsonl",
                4096,
                f"{stream}-sha",
                observed_at,
                Jsonb({"observed_at": observed_at.isoformat()}),
                observed_at,
            ),
        )


def _insert_typed(connection, stream: str, *, observed_at: datetime) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT requires_typed_projection FROM ops.required_stream "
            "WHERE stream_name = %s",
            (stream,),
        )
        row = cursor.fetchone()
        if row is None or not row[0]:
            return
        spec = next(spec for spec in STREAM_SPECS if spec.name == stream)
        if spec.kind == "screening":
            cursor.execute("DELETE FROM market.screening")
            cursor.execute(
                "INSERT INTO market.instrument(canonical_asset, first_seen_at) "
                "VALUES ('TST', %s) ON CONFLICT (canonical_asset) DO NOTHING",
                (observed_at,),
            )
            cursor.execute(
                "SELECT instrument_id FROM market.instrument "
                "WHERE canonical_asset = 'TST'"
            )
            instrument_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO market.screening(
                    scan_id, scanner_type, instrument_id, observed_at,
                    outcome, strategy_version
                ) VALUES ('scan-1', 'TEST', %s, %s, 'ADVANCED', 'v1')
                ON CONFLICT DO NOTHING
                """,
                (instrument_id, observed_at),
            )
        elif spec.kind == "funnel":
            cursor.execute("DELETE FROM lifecycle.stage_transition")
            cursor.execute(
                """
                INSERT INTO lifecycle.stage_transition(
                    transition_key, episode_id, occurred_at, to_stage, outcome
                ) VALUES ('tr-1', 'ep-1', %s, 'QUALIFIED', 'QUALIFIED')
                ON CONFLICT DO NOTHING
                """,
                (observed_at,),
            )
        elif spec.kind == "intelligence":
            cursor.execute("DELETE FROM signal.intelligence_event")
            cursor.execute(
                """
                INSERT INTO signal.intelligence_event(
                    event_key, observed_at, event_type
                ) VALUES ('evt-1', %s, 'QUALIFIED_SIGNAL')
                ON CONFLICT DO NOTHING
                """,
                (observed_at,),
            )
        elif spec.kind == "market_observation":
            cursor.execute("DELETE FROM market.observation")
            cursor.execute(
                "INSERT INTO market.instrument(canonical_asset, first_seen_at) "
                "VALUES ('TST', %s) ON CONFLICT (canonical_asset) DO NOTHING",
                (observed_at,),
            )
            cursor.execute(
                "SELECT instrument_id FROM market.instrument "
                "WHERE canonical_asset = 'TST'"
            )
            instrument_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO market.observation(
                    observation_key, instrument_id, observed_at, payload
                ) VALUES ('obs-1', %s, %s, '{}')
                ON CONFLICT DO NOTHING
                """,
                (instrument_id, observed_at),
            )


def _insert_reconciliation(
    connection,
    stream: str,
    *,
    status: str,
    checked_at: datetime | None,
) -> None:
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
            ) VALUES (%s, %s, 1, 0, 0, 0, 0, 'seed', %s, %s)
            """,
            (stream, f"/data/{stream}.jsonl", status, checked_at or _now()),
        )


def _record_maintenance(
    connection,
    *,
    status: str,
    finished_at: datetime | None,
    fingerprint: str | None = None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute("TRUNCATE ops.maintenance_run")
        cursor.execute(
            """
            INSERT INTO ops.maintenance_run(
                status, detail, policy_fingerprint, started_at, finished_at
            ) VALUES (%s, NULL, %s, %s, %s)
            """,
            (
                status,
                fingerprint or policy_fingerprint(),
                (finished_at or _now()) - timedelta(seconds=30),
                finished_at or _now(),
            ),
        )


def _seed_healthy_stream(connection, stream: str) -> None:
    _insert_checkpoint(connection, stream, updated_at=_now())
    _insert_raw(connection, stream, observed_at=_now())
    _insert_typed(connection, stream, observed_at=_now())
    _insert_reconciliation(connection, stream, status="CLEAN", checked_at=_now())


def _seed_all_healthy(connection) -> None:
    for stream in stream_policy_snapshot():
        _seed_healthy_stream(connection, stream)
    _record_maintenance(connection, status="SUCCESS", finished_at=_now())


def _canonical_row(connection, stream: str) -> dict:
    _refresh_freshness(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, reason
            FROM ops.dashboard_freshness_v
            WHERE stream_name = %s
            """,
            (stream,),
        )
        row = cursor.fetchone()
    assert row is not None, f"canonical row missing for {stream}"
    return {"status": row[0], "reason": row[1]}


_MISSING = object()


def _python_assessment(
    connection,
    stream: str,
    *,
    required: bool = True,
    requires_typed: bool = False,
    threshold_seconds: int | None = None,
    policy_present: bool = True,
    source_updated_at: datetime | None = None,
    last_ingested_at: datetime | None = None,
    typed_watermark_at: Any = _MISSING,
    unresolved_dead_letters: int = 0,
    reconciliation_status: str | None = "CLEAN",
    last_reconciled_at: datetime | None = None,
    maintenance: MaintenanceInput | None = None,
) -> dict:
    present = _now()
    if source_updated_at is None:
        source_updated_at = present
    if last_ingested_at is None:
        last_ingested_at = present
    if last_reconciled_at is None:
        last_reconciled_at = present
    if typed_watermark_at is _MISSING:
        typed_watermark_at = present if requires_typed else None
    result = classify_freshness(
        [
            StreamInput(
                stream_name=stream,
                required=required,
                requires_typed_projection=requires_typed,
                threshold_seconds=threshold_seconds,
                source_updated_at=source_updated_at,
                last_ingested_at=last_ingested_at,
                typed_watermark_at=typed_watermark_at,
                last_polled_at=source_updated_at,
                unresolved_dead_letters=unresolved_dead_letters,
                last_reconciliation_status=reconciliation_status,
                last_reconciled_at=last_reconciled_at,
                policy_present=policy_present,
            )
        ],
        maintenance
        or MaintenanceInput("SUCCESS", _now(), False),
        now=_now(),
    )
    assessment = result["streams"][stream]
    return {"status": assessment.status, "reason": assessment.reason}


# ---------------------------------------------------------------------------
# Correction 1: one canonical contract; per-stream thresholds match everywhere
# ---------------------------------------------------------------------------


def test_canonical_view_exists_and_is_single_source(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('ops.dashboard_freshness_v') IS NOT NULL"
        )
        assert cursor.fetchone()[0] is True
        cursor.execute(
            """
            SELECT count(*) FROM information_schema.views
            WHERE table_schema = 'ops'
              AND table_name IN ('dashboard_freshness_v', 'platform_health_v')
            """
        )
        assert cursor.fetchone()[0] == 2


def test_every_spec_stream_has_policy_row_and_threshold_matches_python(
    pg_connection,
):
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "SELECT stream_name, required, requires_typed_projection, "
            "threshold_seconds FROM ops.required_stream"
        )
        rows = {row[0]: row[1:] for row in cursor.fetchall()}
    snapshot = stream_policy_snapshot()
    assert set(rows) == set(snapshot)
    for name, (required, typed, threshold) in rows.items():
        assert required == snapshot[name]["required"]
        assert typed == snapshot[name]["requires_typed_projection"]
        assert threshold == snapshot[name]["threshold_seconds"]


def test_required_streams_never_receive_a_stale_enabling_threshold():
    for spec in STREAM_SPECS:
        if spec.required:
            assert spec.kind in TYPED_PROJECTION_KINDS or spec.kind == "generic"
    snapshot = stream_policy_snapshot()
    for name, policy in snapshot.items():
        if policy["required"]:
            assert policy["threshold_seconds"] is None, name


# ---------------------------------------------------------------------------
# Correction 2: no false LIVE for typed streams
# ---------------------------------------------------------------------------


def test_missing_required_typed_projection_is_unavailable(pg_connection):
    stream = "screening_evaluations"
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE market.screening")
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, stream)
    python_result = _python_assessment(
        pg_connection,
        stream,
        requires_typed=True,
        typed_watermark_at=None,
    )
    assert canonical == {"status": "UNAVAILABLE", "reason": "MISSING_TYPED_PROJECTION"}
    assert python_result == canonical


def test_ingestion_time_is_never_substituted_for_typed_projection(pg_connection):
    stream = "full_market_observations"
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE market.observation")
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, stream)
    assert canonical["status"] == "UNAVAILABLE"
    assert canonical["reason"] == "MISSING_TYPED_PROJECTION"


def test_typed_projection_metadata_is_explicit():
    snapshot = stream_policy_snapshot()
    for name, policy in snapshot.items():
        spec = next(spec for spec in STREAM_SPECS if spec.name == name)
        assert policy["requires_typed_projection"] is (
            spec.kind in TYPED_PROJECTION_KINDS
        )


# ---------------------------------------------------------------------------
# Correction 3 + 5: readiness fails closed; timestamps are validated
# ---------------------------------------------------------------------------


def test_missing_required_stream_row_is_unavailable(pg_connection):
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM raw.ingested_event WHERE stream_name = 'scan_summaries'"
        )
        cursor.execute(
            "DELETE FROM ops.ingest_checkpoint "
            "WHERE stream_name = 'scan_summaries'"
        )
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, "scan_summaries")
    assert canonical == {"status": "UNAVAILABLE", "reason": "MISSING_STREAM_ROW"}


def test_reconciliation_error_and_unknown_are_unavailable(pg_connection):
    for status, reason in (("ERROR", "RECONCILIATION_ERROR"), (None, "RECONCILIATION_UNKNOWN"), ("MISMATCH", "RECONCILIATION_UNKNOWN")):
        _seed_all_healthy(pg_connection)
        with pg_connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM ops.reconciliation_run WHERE stream_name = %s",
                ("scan_summaries",),
            )
            if status is not None:
                cursor.execute(
                    """
                    INSERT INTO ops.reconciliation_run(
                        stream_name, source_file, source_generation,
                        source_byte_offset, source_rows, database_rows,
                        difference, source_sha256, status, checked_at
                    ) VALUES ('scan_summaries', '/data/scan.jsonl', 1, 0,
                        0, 0, 0, 'seed', %s, %s)
                    """,
                    (status, _now()),
                )
        pg_connection.commit()
        canonical = _canonical_row(pg_connection, "scan_summaries")
        assert canonical == {"status": "UNAVAILABLE", "reason": reason}, status
        python_result = _python_assessment(
            pg_connection, "scan_summaries", reconciliation_status=status
        )
        assert python_result == canonical


def test_reconciliation_timestamp_missing_stale_or_invalid(pg_connection):
    stream = "scan_summaries"
    # Missing timestamp: reconciliation row cannot be CLEAN without checked_at
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM ops.reconciliation_run WHERE stream_name = %s", (stream,)
        )
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, stream)
    assert canonical["status"] == "UNAVAILABLE"
    assert canonical["reason"] == "RECONCILIATION_UNKNOWN"
    # Future-dated reconciliation timestamp fails closed as invalid
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE ops.reconciliation_run SET checked_at = %s "
            "WHERE stream_name = %s",
            (_now() + timedelta(hours=2), stream),
        )
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, stream)
    assert canonical == {"status": "UNAVAILABLE", "reason": "INVALID_TIMESTAMPS"}
    python_result = _python_assessment(
        pg_connection, stream, last_reconciled_at=_now() + timedelta(hours=2)
    )
    assert python_result == canonical


def test_future_dated_stream_timestamps_fail_closed(pg_connection):
    stream = "scan_summaries"
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE raw.ingested_event SET ingested_at = %s "
            "WHERE stream_name = %s",
            (_now() + timedelta(hours=3), stream),
        )
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, stream)
    assert canonical == {"status": "UNAVAILABLE", "reason": "INVALID_TIMESTAMPS"}
    python_result = _python_assessment(
        pg_connection, stream, last_ingested_at=_now() + timedelta(hours=3)
    )
    assert python_result == canonical


def test_stale_source_ingestion_typed_or_poll_timestamps_are_not_live(
    pg_connection,
):
    stream = "scan_summaries"
    _seed_all_healthy(pg_connection)
    old = _now() - timedelta(seconds=DEFAULT_MAX_AGE_SECONDS + 120)
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE raw.ingested_event SET ingested_at = %s, observed_at = %s "
            "WHERE stream_name = %s",
            (old, old, stream),
        )
        cursor.execute(
            "UPDATE ops.ingest_checkpoint SET updated_at = %s "
            "WHERE stream_name = %s",
            (old, stream),
        )
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, stream)
    assert canonical == {"status": "UNAVAILABLE", "reason": "PER_STREAM_THRESHOLD_EXCEEDED"}
    python_result = _python_assessment(
        pg_connection,
        stream,
        last_ingested_at=old,
        source_updated_at=old,
    )
    assert python_result == canonical


def test_stale_typed_projection_is_not_live(pg_connection):
    stream = "screening_evaluations"
    _seed_all_healthy(pg_connection)
    old = _now() - timedelta(seconds=DEFAULT_MAX_AGE_SECONDS + 120)
    with pg_connection.cursor() as cursor:
        cursor.execute("UPDATE market.screening SET observed_at = %s", (old,))
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, stream)
    assert canonical == {"status": "UNAVAILABLE", "reason": "PER_STREAM_THRESHOLD_EXCEEDED"}
    python_result = _python_assessment(
        pg_connection,
        stream,
        requires_typed=True,
        typed_watermark_at=old,
    )
    assert python_result == canonical


# ---------------------------------------------------------------------------
# Correction 3: maintenance failure, drift, and timestamp validation
# ---------------------------------------------------------------------------


def test_latest_maintenance_failed_or_skipped_is_unavailable(pg_connection):
    for status in ("FAILED", "SKIPPED"):
        _seed_all_healthy(pg_connection)
        with pg_connection.cursor() as cursor:
            cursor.execute("TRUNCATE ops.maintenance_run")
        pg_connection.commit()
        _record_maintenance(pg_connection, status=status, finished_at=_now())
        canonical = _canonical_row(pg_connection, "__maintenance__")
        assert canonical == {"status": "UNAVAILABLE", "reason": "MAINTENANCE_FAILED"}, status


def test_maintenance_configuration_drift_fails_closed(pg_connection):
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE ops.maintenance_run SET policy_fingerprint = %s",
            ("0" * 64,),
        )
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, "__maintenance__")
    assert canonical == {"status": "UNAVAILABLE", "reason": "CONFIGURATION_DRIFT"}
    result = classify_freshness(
        [
            StreamInput(
                stream_name="scan_summaries",
                required=True,
                requires_typed_projection=False,
                threshold_seconds=None,
                source_updated_at=_now(),
                last_ingested_at=_now(),
                typed_watermark_at=None,
                last_polled_at=_now(),
                unresolved_dead_letters=0,
                last_reconciliation_status="CLEAN",
                last_reconciled_at=_now(),
            )
        ],
        MaintenanceInput("SUCCESS", _now(), True),
        now=_now(),
    )
    assert result["maintenance"].reason == "CONFIGURATION_DRIFT"
    assert result["ready"] is False


def test_maintenance_timestamp_missing_stale_or_future(pg_connection):
    _seed_all_healthy(pg_connection)
    # Stale maintenance
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE ops.maintenance_run")
    pg_connection.commit()
    _record_maintenance(
        pg_connection,
        status="SUCCESS",
        finished_at=_now() - timedelta(seconds=DEFAULT_MAX_AGE_SECONDS + 60),
    )
    canonical = _canonical_row(pg_connection, "__maintenance__")
    assert canonical == {"status": "UNAVAILABLE", "reason": "MAINTENANCE_STALE"}
    # Future-dated maintenance
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE ops.maintenance_run")
    pg_connection.commit()
    _record_maintenance(
        pg_connection, status="SUCCESS", finished_at=_now() + timedelta(hours=1)
    )
    canonical = _canonical_row(pg_connection, "__maintenance__")
    assert canonical == {"status": "UNAVAILABLE", "reason": "INVALID_TIMESTAMPS"}
    # Never ran
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE ops.maintenance_run")
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, "__maintenance__")
    assert canonical == {"status": "UNAVAILABLE", "reason": "MAINTENANCE_NEVER_RAN"}


def test_missing_policy_table_content_fails_closed(pg_connection):
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE ops.required_stream")
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, "__maintenance__")
    assert canonical == {"status": "UNAVAILABLE", "reason": "MISSING_POLICY"}


def test_health_require_ready_exit_code_matches_canonical_freshness(
    pg_connection, monkeypatch, capsys
):
    monkeypatch.setenv("OPIP_ANALYTICS_DATABASE_URL", DSN)
    monkeypatch.delenv("OPIP_DATA_PLATFORM_READS_ENABLED", raising=False)
    # Fully healthy -> ready, exit 0
    _seed_all_healthy(pg_connection)
    assert health_main(["--require-ready"]) == 0
    capsys.readouterr()
    # Break one required stream -> nonzero exit
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM raw.ingested_event WHERE stream_name = 'funnel_events'"
        )
        cursor.execute(
            "DELETE FROM ops.ingest_checkpoint "
            "WHERE stream_name = 'funnel_events'"
        )
    pg_connection.commit()
    assert health_main(["--require-ready"]) == 2
    output = capsys.readouterr().out
    assert '"ready": false' in output
    assert '"last_reconciled_at"' in output


def test_require_ready_does_not_depend_on_dashboard_reads_flag(
    pg_connection, monkeypatch, capsys
):
    monkeypatch.setenv("OPIP_ANALYTICS_DATABASE_URL", DSN)
    monkeypatch.delenv("OPIP_DATA_PLATFORM_READS_ENABLED", raising=False)
    _seed_all_healthy(pg_connection)
    assert health_main(["--require-ready"]) == 0
    capsys.readouterr()


# ---------------------------------------------------------------------------
# Correction 4: freshness policy protection
# ---------------------------------------------------------------------------


def test_shipper_role_cannot_mutate_required_stream_policy(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT has_table_privilege('opip_shipper', 'ops.required_stream', 'INSERT'),
                   has_table_privilege('opip_shipper', 'ops.required_stream', 'UPDATE'),
                   has_table_privilege('opip_shipper', 'ops.required_stream', 'DELETE'),
                   has_table_privilege('opip_shipper', 'ops.required_stream', 'SELECT')
            """
        )
        insert_priv, update_priv, delete_priv, select_priv = cursor.fetchone()
    assert insert_priv is False
    assert update_priv is False
    assert delete_priv is False
    assert select_priv is True


def test_shipper_role_cannot_record_maintenance(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT has_table_privilege('opip_shipper', 'ops.maintenance_run', 'INSERT'),
                   has_table_privilege('opip_shipper', 'ops.maintenance_run', 'UPDATE'),
                   has_table_privilege('opip_shipper', 'ops.maintenance_run', 'DELETE')
            """
        )
        assert cursor.fetchone() == (False, False, False)


def test_dashboard_role_is_read_only_on_freshness_contract(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT has_table_privilege('opip_dashboard', 'ops.dashboard_freshness_v', 'SELECT'),
                   has_table_privilege('opip_dashboard', 'ops.required_stream', 'INSERT'),
                   has_table_privilege('opip_dashboard', 'ops.maintenance_run', 'INSERT')
            """
        )
        select_priv, policy_insert, maintenance_insert = cursor.fetchone()
    assert select_priv is True
    assert policy_insert is False
    assert maintenance_insert is False


def test_sync_required_streams_uses_admin_command_not_shipper():
    source = (
        ROOT / "app" / "opip" / "data_platform" / "migrations.py"
    ).read_text(encoding="utf-8")
    sync_body = source[source.index("def sync_required_streams"):]
    sync_body = sync_body[: sync_body.index("\ndef ")]
    assert "OPIP_ANALYTICS_ADMIN_DATABASE_URL" in source
    assert "require_shipper_dsn" not in sync_body
    assert "database_url" not in sync_body


# ---------------------------------------------------------------------------
# Correction 6: actionable canonical explanation
# ---------------------------------------------------------------------------


def test_canonical_result_exposes_stable_reason_fields(pg_connection):
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT stream_name, status, reason
            FROM ops.dashboard_freshness_v
            ORDER BY stream_name
            """
        )
        rows = cursor.fetchall()
    assert rows
    for _name, status, reason in rows:
        assert status in {"LIVE", "STALE", "UNAVAILABLE"}
        if status == "LIVE":
            assert reason is None
        else:
            assert isinstance(reason, str) and reason
    freshness = build_freshness(pg_connection)
    assert freshness["reason"]
    assert isinstance(freshness["problems"], list)
    assert freshness["ready"] is (freshness["status"] == "LIVE")


def test_aggregate_reason_is_actionable_when_unhealthy(pg_connection):
    _seed_all_healthy(pg_connection)
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE market.screening")
    pg_connection.commit()
    freshness = build_freshness(pg_connection)
    assert freshness["ready"] is False
    reasons = {problem["reason"] for problem in freshness["problems"]}
    assert "MISSING_TYPED_PROJECTION" in reasons


# ---------------------------------------------------------------------------
# Parity: PostgreSQL view and Python/API classify identically
# ---------------------------------------------------------------------------


def test_postgres_and_python_classifications_match_for_live_state(
    pg_connection,
):
    _seed_all_healthy(pg_connection)
    canonical = _canonical_row(pg_connection, "scan_summaries")
    assert canonical == {"status": "LIVE", "reason": None}
    python_result = _python_assessment(pg_connection, "scan_summaries")
    assert python_result == canonical
    maintenance = _canonical_row(pg_connection, "__maintenance__")
    assert maintenance == {"status": "LIVE", "reason": None}


def test_python_aggregate_matches_canonical_live(pg_connection):
    _seed_all_healthy(pg_connection)
    freshness = build_freshness(pg_connection)
    assert freshness["status"] == "LIVE"
    assert freshness["ready"] is True
    assert freshness["reason"] == "OK"


def test_migration_five_defines_canonical_contract():
    migration = next(
        item for item in discover_migrations() if item.version == 5
    )
    sql = migration.path.read_text(encoding="utf-8")
    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS ops.dashboard_freshness_mv" in sql
    assert "CREATE OR REPLACE VIEW ops.dashboard_freshness_v AS" in sql
    assert "CREATE TABLE IF NOT EXISTS ops.required_stream" in sql
    assert "CREATE TABLE IF NOT EXISTS ops.maintenance_run" in sql
    assert "REVOKE ALL ON ops.required_stream FROM opip_shipper" in sql
    assert "GRANT SELECT ON ops.required_stream TO opip_dashboard" in sql
    assert "'__maintenance__'::text" in sql
    assert "MISSING_TYPED_PROJECTION" in sql
    assert "CONFIGURATION_DRIFT" in sql
    assert "INVALID_TIMESTAMPS" in sql
    assert "RECONCILIATION_ERROR" in sql
    assert "PER_STREAM_THRESHOLD_EXCEEDED" in sql


def test_shell_and_docs_reference_canonical_contract():
    bootstrap = (
        ROOT / "deploy/analytics/bootstrap-opip-data-platform.sh"
    ).read_text(encoding="utf-8")
    assert "sync-required-streams" in bootstrap
    assert "health --require-ready" in bootstrap
    readme = (ROOT / "deploy/analytics/README.md").read_text(encoding="utf-8")
    assert "ops.dashboard_freshness_v" in readme


def test_real_migration_and_sync_round_trip(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "SELECT max(version) FROM ops.schema_version"
        )
        assert cursor.fetchone()[0] == 5
        cursor.execute("SELECT count(*) FROM ops.required_stream")
        assert cursor.fetchone()[0] == len(STREAM_SPECS)


def test_maintenance_records_success_run(pg_connection):
    from app.opip.data_platform.maintenance import record_maintenance_run

    started = _now() - timedelta(seconds=45)
    record_maintenance_run(
        pg_connection,
        status="SUCCESS",
        detail="dropped_partitions=[]",
        started_at=started,
        finished_at=_now(),
    )
    pg_connection.commit()
    canonical = _canonical_row(pg_connection, "__maintenance__")
    assert canonical == {"status": "LIVE", "reason": None}
