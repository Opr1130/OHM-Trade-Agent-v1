from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.backfill import archive_paths
from app.opip.data_platform.backfill import _verified_archive_sha256
from app.opip.data_platform.migrations import discover_migrations
from app.opip.data_platform.read_model import read_historical_snapshot
from app.opip.data_platform.shipper import (
    Checkpoint,
    checkpoint_is_continuous,
    iter_lines,
    observed_at,
    source_event_id,
)
from app.opip.data_platform.streams import STREAM_SPECS


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


@pytest.mark.parametrize(
    "field",
    ("observed_at", "recorded_at", "updated_at", "attempted_at", "scan_at"),
)
def test_common_stream_timestamps_are_supported(field):
    stamp = observed_at({field: "2026-09-01T10:11:12Z"})
    assert stamp == datetime(2026, 9, 1, 10, 11, 12, tzinfo=timezone.utc)


def test_migrations_are_additive_checksum_ordered_and_architecture_bounded():
    migrations = discover_migrations()
    assert [item.version for item in migrations] == [1, 2, 3]
    sql = "\n".join(item.path.read_text(encoding="utf-8") for item in migrations)
    for schema in ("market", "lifecycle", "signal", "paper", "learning", "ops", "raw"):
        assert f"CREATE SCHEMA IF NOT EXISTS {schema}" in sql
    assert "PARTITION BY RANGE" in sql
    assert "strategy_version text NOT NULL" in sql
    assert "opip_dashboard" in sql
    assert "opip_shipper" in sql
    assert "timescaledb" not in sql.lower()
    assert "vector" not in sql.lower()


def test_all_streams_are_exported_and_manifest_validated_before_promotion():
    exporter = (ROOT / "deploy/remote/export-opip-learning-evidence.sh").read_text()
    sync = (ROOT / "deploy/learning/opip-learning-sync.sh").read_text()
    for spec in STREAM_SPECS:
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
    assert "empty|backfill|shipper|reads-ready" in bootstrap
    assert "EMPTY_DEPLOY_COUNT" in bootstrap
    assert "OPIP_EMPTY_ROLLBACK_VERIFIED_AT_UTC" in bootstrap
    assert "7 * 86400" in bootstrap
    assert "health --require-ready" in bootstrap
    assert "OPIP_OFFHOST_BACKUP_VERIFIED_AT_UTC" in bootstrap
    assert "OPIP_RESTORE_DRILL_VERIFIED_AT_UTC" in bootstrap
    assert "analytics host must be resized to at least 2 GiB" in bootstrap
    assert "opip-learning-plane.lock" in bootstrap
    assert "remote_main" in bootstrap and "TARGET_SHA" in bootstrap
    restore = (ROOT / "deploy/analytics/opip-postgres-restore-drill.sh").read_text()
    assert "pg_restore --exit-on-error" in restore
    assert "opip_restore_drill_" in restore
    assert "ops.schema_version" in restore
    assert "dropdb --if-exists --force" in restore


def test_config_json_round_trip_has_no_authority_fields():
    payload = json.dumps(DataPlatformConfig().__dict__, default=str)
    assert "kraken" not in payload.lower()
    assert "telegram" not in payload.lower()
    assert "leverage" not in payload.lower()
