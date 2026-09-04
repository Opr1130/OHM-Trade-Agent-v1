from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path

import pytest

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.backfill import archive_paths
from app.opip.data_platform.backfill import _verified_archive_sha256
from app.opip.data_platform.health import _required_stream_readiness
from app.opip.data_platform.migrations import (
    discover_migrations,
    refresh_materialized_views,
)
from app.opip.data_platform.read_model import (
    _stream_health_is_stale,
    read_historical_snapshot,
)
from app.opip.data_platform.shipper import (
    Checkpoint,
    _validated_revision,
    checkpoint_is_continuous,
    iter_lines,
    observed_at,
    source_event_id,
)
from app.opip.data_platform.streams import STREAM_SPECS
from app.services.dashboard_read_model import _historical_trend_for_scope


ROOT = Path(__file__).resolve().parents[1]


def test_data_platform_is_safe_disabled_without_configuration(monkeypatch):
    for key in (
        "OPIP_DATA_PLATFORM_SHIPPER_ENABLED",
        "OPIP_DATA_PLATFORM_READS_ENABLED",
        "OPIP_ANALYTICS_DATABASE_URL",
        "OPIP_DASHBOARD_DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    config = DataPlatformConfig.from_env()
    assert config.shipper_enabled is False
    assert config.historical_reads_enabled is False
    assert config.dashboard_dsn() is None
    with pytest.raises(RuntimeError, match="disabled"):
        config.require_shipper_dsn()


def test_dashboard_postgres_read_fails_soft_when_disabled():
    result = read_historical_snapshot(DataPlatformConfig())
    assert result["status"] == "DISABLED"
    assert result["available"] is False
    assert result["intelligence_daily"] == []


def test_source_event_ids_are_canonical_and_stream_scoped():
    left = {"observed_at": "2026-09-01T00:00:00Z", "value": 1}
    right = {"value": 1, "observed_at": "2026-09-01T00:00:00Z"}
    assert source_event_id("a", left) == source_event_id("a", right)
    assert source_event_id("a", left) != source_event_id("b", right)


def test_projection_revisions_default_only_when_missing_and_must_be_positive():
    assert _validated_revision({}) == 1
    assert _validated_revision({"revision": "2"}) == 2
    for value in (0, -1, "0"):
        with pytest.raises(ValueError, match="revision must be positive"):
            _validated_revision({"revision": value})


def test_jsonl_reader_retries_torn_tail_and_validates_continuity(tmp_path):
    path = tmp_path / "events.jsonl"
    first = b'{"observed_at":"2026-09-01T00:00:00Z"}\n'
    path.write_bytes(first + b'{"observed_at":"2026-09-01T00:01:00Z"')
    rows = list(iter_lines(path))
    assert len(rows) == 1
    checkpoint = Checkpoint(
        byte_offset=rows[0].end_offset,
        last_row_sha256=rows[0].sha256,
        rows_ingested=1,
        source_size=path.stat().st_size,
    )
    assert checkpoint_is_continuous(path, checkpoint)
    path.write_bytes(b'{"changed":true}\n')
    assert not checkpoint_is_continuous(path, checkpoint)


def test_checkpoint_continuity_supports_rows_larger_than_prior_read_window(tmp_path):
    path = tmp_path / "large-events.jsonl"
    row = b'{"observed_at":"2026-09-01T00:00:00Z","payload":"' + (
        b"x" * (300 * 1024)
    ) + b'"}\n'
    path.write_bytes(row)
    source_line = next(iter_lines(path))
    checkpoint = Checkpoint(
        byte_offset=source_line.end_offset,
        last_row_sha256=source_line.sha256,
        rows_ingested=1,
        source_size=path.stat().st_size,
    )
    assert checkpoint_is_continuous(path, checkpoint)


def test_archive_discovery_is_scoped_to_each_stream(tmp_path):
    screening = next(item for item in STREAM_SPECS if item.name == "screening_evaluations")
    expected = (
        tmp_path
        / "opip/qualification/screening_evaluations_archive"
        / "screening_evaluations-20260901-deadbeef.jsonl.gz"
    )
    collision = (
        tmp_path
        / "other/screening_evaluations_archive"
        / "screening_evaluations-20260901-collision.jsonl.gz"
    )
    expected.parent.mkdir(parents=True)
    collision.parent.mkdir(parents=True)
    expected.write_bytes(b"archive")
    expected.with_suffix(expected.suffix + ".sha256").write_text(
        "0eb3e36b4f6c32c9f244e7a8256b4848b9b44e1c9a9ab6b63e258cb079b56a5d  archive\n"
    )
    collision.write_bytes(b"collision")
    assert archive_paths(tmp_path, screening) == [expected]


def test_archive_checksum_is_required_and_verified(tmp_path):
    archive = tmp_path / "events-1.jsonl.gz"
    archive.write_bytes(b"payload")
    with pytest.raises(RuntimeError, match="missing"):
        _verified_archive_sha256(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5\n"
    )
    assert _verified_archive_sha256(archive).startswith("239f59ed")


def test_archive_checksum_rejects_blank_sidecar(tmp_path):
    archive = tmp_path / "events-blank.jsonl.gz"
    archive.write_bytes(b"payload")
    archive.with_suffix(archive.suffix + ".sha256").write_text("  \n")
    with pytest.raises(RuntimeError, match="checksum is empty"):
        _verified_archive_sha256(archive)


@pytest.mark.parametrize(
    "field",
    ("observed_at", "recorded_at", "updated_at", "attempted_at", "scan_at"),
)
def test_common_stream_timestamps_are_supported(field):
    stamp = observed_at({field: "2026-09-01T10:11:12Z"})
    assert stamp == datetime(2026, 9, 1, 10, 11, 12, tzinfo=timezone.utc)


def test_migrations_are_additive_checksum_ordered_and_architecture_bounded():
    migrations = discover_migrations()
    assert [item.version for item in migrations] == [1, 2, 3, 4, 5]
    sql = "\n".join(item.path.read_text(encoding="utf-8") for item in migrations)
    for schema in ("market", "lifecycle", "signal", "paper", "learning", "ops", "raw"):
        assert f"CREATE SCHEMA IF NOT EXISTS {schema}" in sql
    assert "PARTITION BY RANGE" in sql
    assert "strategy_version text NOT NULL" in sql
    assert "opip_dashboard" in sql
    assert "opip_shipper" in sql
    assert "timescaledb" not in sql.lower()
    assert "vector" not in sql.lower()
    assert "source_generation, source_byte_offset" in sql
    assert "revision integer NOT NULL DEFAULT 1" in sql
    assert "REVOKE INSERT, UPDATE, DELETE ON ops.schema_version" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON ALL TABLES" not in sql
    assert "GRANT SELECT, INSERT, UPDATE ON TABLES TO opip_shipper" not in sql


def test_opportunity_accountability_migration_is_single_well_formed_definition():
    migration = next(
        item for item in discover_migrations()
        if item.version == 4
    )
    sql = migration.path.read_text(encoding="utf-8")
    assert sql.count(
        "CREATE MATERIALIZED VIEW IF NOT EXISTS "
        "learning.opportunity_accountability_daily_mv"
    ) == 1
    assert sql.count("AS decision_latency_samples") == 1
    assert sql.count("AS mean_decision_latency_ms") == 1
    assert sql.count("FROM learning.opportunity_accountability_latest_v") == 1
    assert "outcome_complete" in sql
    assert "count(*) FILTER" in sql
    assert "avg(" in sql


def test_accountability_sql_matches_executable_miss_semantics():
    migration = next(
        item for item in discover_migrations()
        if item.version == 4
    )
    sql = migration.path.read_text(encoding="utf-8")
    prefix = sql.split("AS estimated_missed_move_pct_sum", 1)[0]
    missed_move_case = prefix[prefix.rindex("sum("):]
    assert "executable_false_negative" in missed_move_case


def test_dashboard_hides_outcome_metrics_until_measured():
    html = (ROOT / "app" / "api" / "dashboard.html").read_text(encoding="utf-8")
    assert "const oaMeasured=oa.status==='MEASURED'" in html
    for element_id in (
        "execMissed",
        "oppCapture",
        "thresholdMiss",
        "capMiss",
        "operationalMiss",
    ):
        assert f"$('{element_id}').textContent=oaMeasured?" in html


def test_accountability_read_model_separates_all_time_totals_from_60_day_trend():
    source = (
        ROOT / "app" / "opip" / "data_platform" / "read_model.py"
    ).read_text(encoding="utf-8")
    assert "ORDER BY day DESC LIMIT 60" in source
    assert '"opportunity_accountability_all_time"' in source
    aggregate_start = source.index(
        "coalesce(sum(directional_evaluations), 0)"
    )
    aggregate_end = source.index(
        "FROM learning.opportunity_accountability_daily_mv",
        aggregate_start,
    )
    aggregate_query = source[aggregate_start:aggregate_end]
    assert "LIMIT 60" not in aggregate_query
    assert "decision_latency_samples" in aggregate_query


def test_concurrent_materialized_view_refresh_uses_autocommit():
    class Cursor:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            self.connection.events.append(
                ("execute", self.connection.autocommit, repr(query), params)
            )

        def fetchone(self):
            return (self.connection.population.pop(0),)

    class Connection:
        def __init__(self):
            self.autocommit = False
            self.population = [True, False, True, False, False]
            self.events = []

        def cursor(self):
            return Cursor(self)

        def commit(self):
            self.events.append(("commit", self.autocommit, "", None))

    connection = Connection()
    refresh_materialized_views(connection)
    refreshes = [event for event in connection.events if "REFRESH" in event[2]]
    assert len(refreshes) == 5
    assert all(event[1] is True for event in refreshes)
    assert connection.events.index(refreshes[0]) > next(
        index for index, event in enumerate(connection.events) if event[0] == "commit"
    )
    assert connection.autocommit is False


def test_materialized_view_refresh_skips_unapplied_optional_view():
    class Cursor:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            self.connection.events.append(
                ("execute", self.connection.autocommit, repr(query), params)
            )
            self.connection.last_params = params

        def fetchone(self):
            relation = (self.connection.last_params or ("",))[0]
            if relation == "learning.opportunity_accountability_daily_mv":
                return None
            return (True,)

    class Connection:
        def __init__(self):
            self.autocommit = False
            self.events = []
            self.last_params = None

        def cursor(self):
            return Cursor(self)

        def commit(self):
            self.events.append(("commit", self.autocommit, "", None))

    connection = Connection()
    refresh_materialized_views(connection)
    refreshes = [event for event in connection.events if "REFRESH" in event[2]]
    assert len(refreshes) == 4
    assert all("opportunity_accountability_daily_mv" not in event[2] for event in refreshes)


def test_optional_streams_do_not_block_required_readiness():
    required = [item.name for item in STREAM_SPECS if item.required]
    rows = [
        {
            "stream_name": name,
            "lag_seconds": 1,
            "unresolved_dead_letters": 0,
            "last_reconciliation_status": "CLEAN",
            "required": True,
            "freshness_status": "LIVE",
        }
        for name in required
    ]
    rows.append(
        {
            "stream_name": "paper_trade_events",
            "lag_seconds": 99999,
            "unresolved_dead_letters": 4,
            "last_reconciliation_status": "MISMATCH",
            "required": False,
            "freshness_status": "STALE",
        }
    )
    required_names, missing, healthy = _required_stream_readiness(
        rows,
        maximum_lag_seconds=300,
    )
    assert required_names == sorted(required)
    assert missing == []
    assert healthy is True
    assert _stream_health_is_stale(rows, maximum_lag_seconds=300) is False
    degraded = [dict(row) for row in rows]
    degraded[0]["freshness_status"] = "UNAVAILABLE"
    assert _stream_health_is_stale(degraded, maximum_lag_seconds=300) is True


def test_today_historical_trend_excludes_prior_dates():
    rows = [{"date": "2026-08-31"}, {"date": "2026-09-01"}]
    assert _historical_trend_for_scope(
        rows,
        "today",
        today=date(2026, 9, 1),
    ) == [{"date": "2026-09-01"}]
    assert _historical_trend_for_scope(rows, "all") == rows


def test_all_streams_are_exported_and_manifest_validated_before_promotion():
    exporter = (ROOT / "deploy/remote/export-opip-learning-evidence.sh").read_text()
    sync = (ROOT / "deploy/learning/opip-learning-sync.sh").read_text()
    for spec in STREAM_SPECS:
        # Opportunity accountability is produced on the isolated learning
        # worker after sync; it is not a production-export source stream.
        if spec.name == "opportunity_accountability":
            relative = str(spec.relative_path)
            assert relative not in exporter
            assert relative not in sync
            continue
        relative = str(spec.relative_path)
        assert relative in exporter
        assert relative in sync
    assert "schema_version=3" in exporter
    assert "screening_evaluations_archive" in exporter
    assert "validate_archive" in sync
    assert "tree_sha256" in exporter and "tree_sha256" in sync
    assert '[[ "$schema" == "3" ]]' in sync
    assert sync.index('validate_artifact "p1_shadow_outbox.jsonl"') < sync.index(
        "for name in \\")


def test_analytics_topology_is_separate_bounded_and_without_trade_authority():
    compose = (ROOT / "deploy/analytics/docker-compose.yml").read_text()
    production_compose = (ROOT / "docker-compose.yml").read_text()
    assert "postgres:17-alpine" in compose
    assert "mem_limit: 512m" in compose
    assert "internal: true" in compose
    assert "opip_dashboard" not in production_compose
    assert "postgres:17" not in production_compose
    assert "KRAKEN" not in compose
    assert "TELEGRAM" not in compose
    assert "--network none" not in compose  # shipper needs only its private DB network


def test_rollout_gates_prevent_immediate_cutover():
    bootstrap = (ROOT / "deploy/analytics/bootstrap-opip-data-platform.sh").read_text()
    runner = (ROOT / "deploy/analytics/run-gated-stage.sh").read_text()
    assert "empty|backfill|shipper|reads-ready" in bootstrap
    assert "EMPTY_DEPLOY_COUNT" in bootstrap
    assert "EMPTY_LAST_COMPLETED_AT_UTC" in bootstrap
    assert "7 * 86400" in bootstrap
    assert "health --require-ready" in bootstrap
    assert "offhost-backup.env" in bootstrap
    assert "last-restore-drill.env" in bootstrap
    assert "empty-rollback.env" in bootstrap
    assert "analytics host must be resized to at least 2 GiB" in bootstrap
    assert "opip-learning-plane.lock" in bootstrap
    assert "remote_main" in bootstrap and "TARGET_SHA" in bootstrap
    assert 'if [[ -r "$STATE_ROOT/postgres/PG_VERSION" ]]; then' in bootstrap
    assert 'stat -c \'%u:%g\' "$STATE_ROOT/postgres/PG_VERSION"' in bootstrap
    assert 'chown "$pgdata_owner" "$STATE_ROOT/postgres"' in bootstrap
    assert '"$STATE_ROOT/postgres" \\\n  "$STATE_ROOT/config"' not in bootstrap
    assert bootstrap.index("wait_for_postgres\n") < bootstrap.index(
        "validate_promotion_evidence\n"
    )
    assert "backup_epoch > now_epoch" in bootstrap
    assert "restore_epoch > now_epoch" in bootstrap
    assert "rollback_epoch > now_epoch" in bootstrap
    assert "restore drill must validate the attested PostgreSQL dump" in bootstrap
    assert 'docker compose -f "$COMPOSE" build opip-shipper' in bootstrap
    assert 'build opip-shipper opip-data-admin' not in bootstrap
    assert "explicit empty-stage rollback evidence is required before backfill" in bootstrap
    assert "OPIP_OFFHOST_BACKUP_VERIFIED_AT_UTC" not in bootstrap
    assert "OPIP_RESTORE_DRILL_VERIFIED_AT_UTC" not in bootstrap
    assert "OPIP_EMPTY_ROLLBACK_VERIFIED_AT_UTC" not in bootstrap
    assert "offhost-verified" in runner
    assert "rollback-verified" in runner
    maintenance = (
        ROOT / "deploy/analytics/opip-data-platform-maintenance.sh"
    ).read_text()
    assert "/etc/opip-data-platform.env" in maintenance
    assert "/var/lock/opip-learning-plane.lock" in maintenance
    assert "DEPLOYED_SHA" in maintenance
    restore = (ROOT / "deploy/analytics/opip-postgres-restore-drill.sh").read_text()
    assert "pg_restore --exit-on-error" in restore
    assert "opip_restore_drill_" in restore
    assert "ops.schema_version" in restore
    assert "dropdb --if-exists --force" in restore


def test_maintenance_always_refreshes_freshness_after_reconcile_mismatch():
    """Regression: a non-zero reconcile (MISMATCH) must not skip refresh-freshness.

    The maintenance script runs under `set -e`. If reconcile returns non-zero the
    script must capture that exit status (so it does not abort) and STILL run
    refresh-freshness and health afterwards, then exit with the preserved
    reconcile status so callers observe the mismatch.
    """
    maintenance = (
        ROOT / "deploy/analytics/opip-data-platform-maintenance.sh"
    ).read_text()
    # Reconcile exit status is captured, not allowed to abort the script.
    assert "RECONCILE_STATUS=0" in maintenance
    assert "python -m app.opip.data_platform.reconcile || RECONCILE_STATUS=$?" in maintenance
    # refresh-freshness and health must appear AFTER the reconcile command so a
    # MISMATCH/non-zero return cannot skip the freshness refresh.
    reconcile_pos = maintenance.index("app.opip.data_platform.reconcile")
    refresh_pos = maintenance.index("refresh-freshness")
    health_pos = maintenance.index("app.opip.data_platform.health")
    assert reconcile_pos < refresh_pos < health_pos
    # The preserved reconcile status is the final exit status.
    assert 'exit "$RECONCILE_STATUS"' in maintenance


def test_config_json_round_trip_has_no_authority_fields():
    payload = json.dumps(DataPlatformConfig().__dict__, default=str)
    assert "kraken" not in payload.lower()
    assert "telegram" not in payload.lower()
    assert "leverage" not in payload.lower()
