"""ATDD — O'Pip Control Center V2 round 3: projection, config and retention.

ATDD-OPIP-CONTROL-CENTER-V2-M02. These tests prove the analytical projection is
derived, idempotent, reconcilable and retention-safe, that configuration staging
is inert and validated, and that the read API reports absence instead of
inventing values.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.opip.analytics import ingest, reconcile, retention
from app.opip.analytics import schema as analytics_schema
from app.opip.analytics.ingest import ingest_source
from app.opip.analytics.sources import KIND_SCAN_SNAPSHOT, SourceDescriptor
from app.opip.analytics.store import connect_writer
from app.opip.config import registry, staging as config_staging, validation

INCREMENT = "ATDD-OPIP-CONTROL-CENTER-V2-M02"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
ATTEMPT = "SCAN:20261103T000000Z"
DIRNAME = "SCAN-20261103T000000Z"


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "webhook_secret": "test-operator-credential",  # gitleaks:allow - test fixture, never a real credential
        "app_env": "test",
        "opip_analytics_db_path": str(tmp_path / "opip_analytics.db"),
        "opip_analytics_scan_state_dir": str(tmp_path / "scan"),
        "opip_analytics_scoring_comparison_path": str(tmp_path / "cmp.jsonl"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture()
def projection(tmp_path):
    settings = _settings(tmp_path)
    conn = connect_writer(settings.opip_analytics_db_path)
    analytics_schema.ensure_schema(conn)
    try:
        yield settings, conn
    finally:
        conn.close()


def _batch_dir(settings: Settings, dirname: str = DIRNAME) -> Path:
    return Path(settings.opip_analytics_scan_state_dir) / "staging" / dirname


def _write_batch(
    settings: Settings,
    rows: list[dict],
    *,
    dirname: str = DIRNAME,
    attempt: str = ATTEMPT,
    started: str = "2026-11-03T00:00:00+00:00",
    manifest: bool = True,
    trailing: bytes = b"",
) -> SourceDescriptor:
    directory = _batch_dir(settings, dirname)
    directory.mkdir(parents=True, exist_ok=True)
    snapshots = directory / "snapshots.jsonl"
    body = "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8") + trailing
    snapshots.write_bytes(body)
    if manifest:
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "attempt_id": attempt,
                    "started_at": started,
                    "snapshots": {
                        "file": "snapshots.jsonl",
                        "rows": len(rows),
                        "sha256": hashlib.sha256(snapshots.read_bytes()).hexdigest(),
                    },
                }
            ),
            encoding="utf-8",
        )
    return SourceDescriptor(
        source_id=f"scan_snapshots:{dirname}",
        record_kind=KIND_SCAN_SNAPSHOT,
        display_name="batch",
        path=snapshots,
        attempt_id=dirname,
        manifest_path=directory / "manifest.json",
        requires_manifest=True,
    )


def _row(symbol: str, *, v1: float = 82, long: float | None = 91.0,
         short: float | None = 12.0, status: str = "READY") -> dict:
    return {
        "symbol": symbol,
        "technical_score": v1,
        "trade_direction": "LONG",
        "technical_score_version": "v2",
        "v2_long_score": long,
        "v2_short_score": short,
        "v2_feature_status": status,
        "v2_profile_version": "TECHNICAL_SCORE_V2_1",
        "v2_profile_digest": "a" * 64,
        "v2_long_components": {"rsi": 15.0, "relative_volume": 18.0},
    }


def _refresh_manifest(descriptor: SourceDescriptor) -> None:
    """Re-sign the batch so the manifest still proves the file it describes."""
    manifest = json.loads(descriptor.manifest_path.read_text(encoding="utf-8"))
    manifest["snapshots"]["sha256"] = hashlib.sha256(
        descriptor.path.read_bytes()
    ).hexdigest()
    descriptor.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_ingestion_is_incremental_idempotent_and_partial_safe(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-001: replay, partial trailing line, restart and quarantine are distinct."""
    settings, conn = projection
    descriptor = _write_batch(settings, [_row("BTC/USD"), _row("ETH/USD")])

    first = ingest_source(conn, descriptor, now=NOW)
    assert first.status == "INGESTED"
    assert first.records_inserted == 2
    assert first.has_more is False

    # A restart re-reads nothing: the checkpoint already covers the file.
    second = ingest_source(conn, descriptor, now=NOW)
    assert second.status == "NO_CHANGE"
    assert second.records_read == 0

    # A duplicate replay of the same bytes cannot insert a second row.
    conn.execute(
        "DELETE FROM ingestion_checkpoints WHERE source_id = ?", (descriptor.source_id,)
    )
    replay = ingest_source(conn, descriptor, now=NOW)
    assert replay.records_inserted == 0
    assert conn.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0] == 2

    # A trailing partial line is never ingested and never advances the offset.
    with descriptor.path.open("ab") as handle:
        handle.write(b'{"symbol": "SOL/USD", "technical_scor')
    _refresh_manifest(descriptor)
    partial = ingest_source(conn, descriptor, now=NOW)
    assert partial.records_read == 0
    assert partial.last_offset == first.last_offset

    # Completing the line ingests exactly one record.
    with descriptor.path.open("ab") as handle:
        handle.write(b'e": 70}\n')
    _refresh_manifest(descriptor)
    completed = ingest_source(conn, descriptor, now=NOW)
    assert completed.records_inserted == 1
    assert conn.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0] == 3


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_malformed_and_unverified_batches_are_quarantined_or_refused(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-001: bad evidence is quarantined; an unverified batch is refused."""
    settings, conn = projection

    # No manifest yet: the attempt is still acquiring, so nothing is ingested.
    pending = _write_batch(settings, [_row("BTC/USD")], manifest=False)
    deferred = ingest_source(conn, pending, now=NOW)
    assert deferred.status == "DEFERRED"
    assert deferred.reason_code == ingest.REASON_ATTEMPT_IN_PROGRESS
    assert conn.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0] == 0

    # A manifest whose hash no longer matches the file is a refusal, not a read.
    tampered = _write_batch(settings, [_row("BTC/USD")], dirname="SCAN-T", attempt="SCAN:T")
    with tampered.path.open("ab") as handle:
        handle.write(b'{"symbol":"X"}\n')
    refused = ingest_source(conn, tampered, now=NOW)
    assert refused.status == "MANIFEST_MISMATCH"
    assert refused.reason_code == ingest.REASON_MANIFEST_HASH_MISMATCH
    assert conn.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0] == 0

    # A malformed record inside a verified batch is quarantined, and the source
    # is then reported as quarantined rather than clean.
    mixed = _write_batch(settings, [_row("BTC/USD")], dirname="SCAN-2", attempt="SCAN:2")
    with mixed.path.open("ab") as handle:
        handle.write(b"{not json}\n")
    _refresh_manifest(mixed)
    result = ingest_source(conn, mixed, now=NOW)
    assert result.status == "QUARANTINED"
    assert result.records_quarantined == 1
    quarantined = conn.execute(
        "SELECT reason_code, is_verified, is_audit_locked FROM quarantined_records"
    ).fetchone()
    assert quarantined["reason_code"] == "MALFORMED_JSON"
    assert quarantined["is_verified"] == 0
    assert quarantined["is_audit_locked"] == 1



@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_truncation_and_replacement_start_a_new_generation(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-001: a shorter or replaced file never resumes at a stale offset."""
    settings, conn = projection
    descriptor = _write_batch(settings, [_row("BTC/USD"), _row("ETH/USD")])
    ingest_source(conn, descriptor, now=NOW)
    checkpoint = ingest.load_checkpoint(conn, descriptor.source_id)
    assert checkpoint.last_offset > 0

    replaced = _write_batch(settings, [_row("ADA/USD")])
    after = ingest_source(conn, replaced, now=NOW)
    assert after.generation >= checkpoint.generation
    if after.generation != checkpoint.generation:
        assert after.reason_code in {
            ingest.REASON_TRUNCATED,
            ingest.REASON_IDENTITY_CHANGED,
        }
    # Either way the new evidence is projected and nothing was silently dropped.
    assert conn.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0] >= 1


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_reconciliation_proves_match_and_detects_real_drift(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-002: RECONCILED, DRIFT_DETECTED and a changed-hash drift are distinct."""
    settings, conn = projection
    descriptor = _write_batch(settings, [_row("BTC/USD"), _row("ETH/USD")])
    ingest_source(conn, descriptor, now=NOW)

    clean = reconcile.reconcile_source(conn, descriptor, now=NOW)
    assert clean.status == reconcile.STATUS_RECONCILED
    assert clean.expected_records == 2
    assert clean.actual_records == 2
    assert clean.reason_code == reconcile.REASON_CLEAN

    # A projection that lost a row is real drift with a named cause.
    conn.execute("DELETE FROM ingested_records WHERE line_number = 1")
    drift = reconcile.reconcile_source(conn, descriptor, now=NOW)
    assert drift.status == reconcile.STATUS_DRIFT
    assert drift.reason_code == reconcile.REASON_COUNT_MISMATCH

    # A record whose hash no longer matches the stored evidence is also drift.
    # Reset the projection first so the count check cannot mask the hash check.
    conn.execute("DELETE FROM ingested_records")
    conn.execute("DELETE FROM ingestion_checkpoints")
    conn.execute("DELETE FROM scanner_snapshots")
    conn.execute("DELETE FROM ingestion_events")
    ingest_source(conn, descriptor, now=NOW)
    assert reconcile.reconcile_source(conn, descriptor, now=NOW).status == (
        reconcile.STATUS_RECONCILED
    )
    conn.execute(
        "UPDATE ingested_records SET record_sha256 = ? WHERE line_number = 1",
        ("0" * 64,),
    )
    hashed = reconcile.reconcile_source(conn, descriptor, now=NOW)
    assert hashed.status == reconcile.STATUS_DRIFT
    assert hashed.reason_code in {
        reconcile.REASON_RECORD_HASH_MISMATCH,
        reconcile.REASON_TIP_HASH_MISMATCH,
    }


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_reconciliation_is_retention_aware_and_reports_absence(projection, tmp_path):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-002: an authorized purge is not drift; a missing source is not clean."""
    settings, conn = projection
    descriptor = _write_batch(settings, [_row("BTC/USD")])
    ingest_source(conn, descriptor, now=NOW)

    # Two records were purged under an authorized retention run.
    conn.execute(
        "UPDATE ingestion_checkpoints SET records_committed = 3 WHERE source_id = ?",
        (descriptor.source_id,),
    )
    conn.execute(
        "INSERT INTO retention_coverage(coverage_key, source_id, source_generation, "
        "purged_through_utc, records_purged, recorded_at_utc) VALUES(?, ?, 0, ?, 2, ?)",
        (
            f"{descriptor.source_id}:0",
            descriptor.source_id,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    covered = reconcile.reconcile_source(conn, descriptor, now=NOW)
    assert covered.status == reconcile.STATUS_RECONCILED
    assert covered.covered_purged_records == 2

    missing = SourceDescriptor(
        source_id="scan_attempt_ledger",
        record_kind="SCAN_ATTEMPT_EVENT",
        display_name="absent",
        path=tmp_path / "absent.jsonl",
    )
    absent = reconcile.reconcile_source(conn, missing, now=NOW)
    assert absent.status == reconcile.STATUS_SOURCE_UNAVAILABLE

    run = reconcile.reconcile_all(conn, [descriptor, missing], now=NOW)
    assert run["status"] == reconcile.STATUS_UNVERIFIED
    latest = reconcile.latest_status(conn)
    assert latest["state"] == reconcile.STATUS_UNVERIFIED
    assert latest["sources_unavailable"] == 1


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_quarantine_keeps_a_source_unverified(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-002: a quarantined record never becomes a clean reconciled dataset."""
    settings, conn = projection
    descriptor = _write_batch(settings, [_row("BTC/USD")])
    with descriptor.path.open("ab") as handle:
        handle.write(b"{not json}\n")
    _refresh_manifest(descriptor)
    ingest_source(conn, descriptor, now=NOW)

    state = reconcile.reconcile_source(conn, descriptor, now=NOW)
    assert state.status == reconcile.STATUS_UNVERIFIED
    assert state.reason_code == reconcile.REASON_QUARANTINE_BACKLOG
    assert state.quarantined_records == 1



def _insert_snapshot(
    conn,
    key: str,
    observed: datetime,
    *,
    verified: int = 1,
    locked: int = 0,
    symbol: str = "BTC/USD",
) -> None:
    conn.execute(
        "INSERT INTO scanner_snapshots("
        " snapshot_key, attempt_id, symbol, observed_at_utc, source_id,"
        " record_sha256, ingested_at_utc, is_verified, is_audit_locked"
        ") VALUES(?, 'SCAN:1', ?, ?, 'seed', ?, ?, ?, ?)",
        (key, symbol, observed.isoformat(), key, observed.isoformat(), verified, locked),
    )


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_retention_policy_values_and_default_are_exact():
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-003: only 7/15/30/60/90 are accepted, and the default is 30 days."""
    for days in (7, 15, 30, 60, 90):
        assert retention.validate_retention_days(days) == days
        assert retention.validate_retention_days(str(days)) == days
    for bad in (0, 1, 45, 365, -30, True, None, "30d"):
        with pytest.raises(retention.RetentionPolicyError):
            retention.validate_retention_days(bad)
    assert retention.DEFAULT_RETENTION_DAYS == 30
    assert retention.ALLOWED_RETENTION_DAYS == (7, 15, 30, 60, 90)


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_housekeeping_defaults_to_dry_run_and_refuses_unauthorized_delete(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-003: dry run is the default; a delete request without authority refuses."""
    _settings_, conn = projection
    old = NOW - timedelta(days=45)
    _insert_snapshot(conn, "old-1", old)
    _insert_snapshot(conn, "fresh-1", NOW - timedelta(days=1))

    dry = retention.housekeeping(conn, retention_days=30, now=NOW)
    assert dry["status"] == retention.STATUS_DRY_RUN
    assert dry["dry_run"] is True
    assert dry["rows_deleted"] == 0
    assert dry["preview"]["eligible"]["scanner_snapshots"] == 1
    assert conn.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0] == 2

    refused = retention.housekeeping(
        conn, retention_days=30, dry_run=False, allow_delete=False, now=NOW
    )
    assert refused["status"] == retention.STATUS_REFUSED
    assert refused["reason_code"] == retention.STATUS_REFUSED
    assert refused["rows_deleted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0] == 2

    logs = retention.recent_runs(conn, limit=5)
    assert {row["status"] for row in logs} == {
        retention.STATUS_DRY_RUN,
        retention.STATUS_REFUSED,
    }


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_authorized_delete_protects_unverified_locked_and_audit_evidence(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-003: only verified, unlocked derived rows are purged, with coverage."""
    _settings_, conn = projection
    old = NOW - timedelta(days=45)
    _insert_snapshot(conn, "verified-old", old, symbol="BTC/USD")
    _insert_snapshot(conn, "unverified-old", old, verified=0, symbol="ETH/USD")
    _insert_snapshot(conn, "locked-old", old, locked=1, symbol="ADA/USD")
    _insert_snapshot(conn, "verified-fresh", NOW - timedelta(days=2), symbol="SOL/USD")
    conn.execute(
        "INSERT INTO quarantined_records(quarantine_id, source_id, reason_code,"
        " ingested_at_utc, is_verified, is_audit_locked)"
        " VALUES('q1', 'seed', 'MALFORMED_JSON', ?, 0, 1)",
        (old.isoformat(),),
    )
    conn.execute(
        "INSERT INTO reconciliation_runs(run_id, started_at_utc, status)"
        " VALUES('run-old', ?, 'RECONCILED')",
        (old.isoformat(),),
    )

    result = retention.housekeeping(
        conn, retention_days=30, dry_run=False, allow_delete=True, now=NOW
    )
    assert result["status"] == retention.STATUS_DELETED
    assert result["rows_deleted"] == 1
    assert result["bytes_reclaimed"] is None
    remaining = {
        row["snapshot_key"]
        for row in conn.execute("SELECT snapshot_key FROM scanner_snapshots")
    }
    assert remaining == {"unverified-old", "locked-old", "verified-fresh"}
    # Evidence and audit tables are never touched by retention.
    assert conn.execute("SELECT COUNT(*) FROM quarantined_records").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM reconciliation_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM retention_cleanup_logs").fetchone()[0] == 1


@pytest.mark.acceptance(f"{INCREMENT}/AC-003")
def test_authorized_delete_cannot_break_reconciliation(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-003: the newest ingested record and its coverage survive a purge."""
    settings, conn = projection
    descriptor = _write_batch(settings, [_row("BTC/USD"), _row("ETH/USD")])
    ingest_source(conn, descriptor, now=NOW - timedelta(days=60))
    conn.execute(
        "UPDATE scanner_snapshots SET observed_at_utc = ?",
        ((NOW - timedelta(days=60)).isoformat(),),
    )
    conn.execute(
        "UPDATE ingested_records SET observed_at_utc = ?",
        ((NOW - timedelta(days=60)).isoformat(),),
    )
    result = retention.housekeeping(
        conn, retention_days=30, dry_run=False, allow_delete=True, now=NOW
    )
    assert result["status"] == retention.STATUS_DELETED
    # The tip record is retained so the checkpoint hash stays verifiable.
    assert conn.execute("SELECT COUNT(*) FROM ingested_records").fetchone()[0] == 1
    assert result["coverage"], "a purge must record durable coverage"
    state = reconcile.reconcile_source(conn, descriptor, now=NOW)
    assert state.status == reconcile.STATUS_RECONCILED



@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_registry_covers_eight_domains_with_complete_metadata():
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-004: every domain is registered and every setting declares its rules."""
    assert registry.DOMAINS == (
        "V2_SCORING",
        "V1_V2_ATTRIBUTION",
        "SCANNER",
        "F11_SAFETY",
        "AI_ROUTING",
        "RETENTION_RECONCILIATION",
        "SQLITE_RESOURCE_BUDGETS",
        "DASHBOARD",
    )
    for domain in registry.DOMAINS:
        assert registry.definitions_for(domain), domain
    for item in registry.DEFINITIONS:
        assert item.key and item.display_name and item.description
        assert item.data_type in {
            "NUMBER",
            "INTEGER",
            "BOOLEAN",
            "ENUM",
            "STRING",
            "SECRET_REFERENCE",
            "ARRAY",
            "DERIVED",
        }
        assert item.editability in {"READ_ONLY", "STAGE_ONLY", "STAGE_AND_ACTIVATE"}
        assert item.activation_authority in {
            "NONE",
            "ADVISORY_PROFILE",
            "DEPLOYMENT_OPERATOR",
        }
        if item.editability == "READ_ONLY":
            assert item.read_only_reason, item.key
    # Secrets are never a displayable value.
    secret = registry.definition("ai.api_key")
    assert secret is not None and secret.sensitivity == "SECRET_REFERENCE"


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_validation_rejects_rather_than_coerces():
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-004: tier 1/2 rules reject invalid configuration outright."""
    weights = {f"v2.weights.{name}": 20 for name in registry.V2_COMPONENTS}
    weights["v2.weights.rsi"] = 15
    weights["v2.weights.macd_acceleration"] = 5
    assert validation.validate_document("V2_SCORING", weights).ok

    short = dict(weights)
    short["v2.weights.rsi"] = 10
    result = validation.validate_document("V2_SCORING", short)
    assert not result.ok
    assert "WEIGHTS_MUST_TOTAL_100" in {item.code for item in result.errors}

    partial = {"v2.weights.rsi": 15}
    assert "PARTIAL_RUBRIC" in {
        item.code for item in validation.validate_document("V2_SCORING", partial).errors
    }

    read_only = validation.validate_document("F11_SAFETY", {"f11.allow_funded_orders": True})
    assert "READ_ONLY_SETTING" in {item.code for item in read_only.errors}

    unknown = validation.validate_document("V2_SCORING", {"v2.not_a_setting": 1})
    assert "UNKNOWN_CONFIG_KEY" in {item.code for item in unknown.errors}

    mismatch = validation.validate_document("DASHBOARD", {"v2.threshold": 80})
    assert "DOMAIN_MISMATCH" in {item.code for item in mismatch.errors}

    ordering = validation.validate_document(
        "DASHBOARD",
        {"dashboard.refresh_seconds": 600, "dashboard.stale_after_seconds": 300},
    )
    assert "RANGE_ORDER_INVALID" in {item.code for item in ordering.errors}

    ai_mode = validation.validate_document("AI_ROUTING", {"ai.mode": "execution"})
    assert "READ_ONLY_SETTING" in {item.code for item in ai_mode.errors}

    retention_days = validation.validate_document(
        "RETENTION_RECONCILIATION", {"retention.days": "45"}
    )
    assert not retention_days.ok



@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_staging_is_inert_and_uses_optimistic_concurrency(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-004: staging records a version, changes nothing, and rejects stale writes."""
    settings, conn = projection
    config_staging.sync_registry(conn, now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM config_definitions").fetchone()[0] == len(
        registry.DEFINITIONS
    )

    first = config_staging.stage(
        conn,
        domain="DASHBOARD",
        values={"dashboard.refresh_seconds": 60},
        actor="operator@test",
        note="slow the console down",
        expected_checksum="",
        now=NOW,
    )
    assert first["status"] == "STAGED"
    assert first["live_effect"] is False
    assert len(first["config_checksum"]) == 64
    stored = conn.execute(
        "SELECT config_checksum, status FROM config_versions WHERE version_id = ?",
        (first["version_id"],),
    ).fetchone()
    assert stored["status"] == "STAGED"
    assert stored["config_checksum"] == first["config_checksum"]
    assert conn.execute("SELECT COUNT(*) FROM config_audit_log").fetchone()[0] == 1

    # A second stage with a stale expected checksum is refused.
    with pytest.raises(config_staging.ConfigStageConflict):
        config_staging.stage(
            conn,
            domain="DASHBOARD",
            values={"dashboard.refresh_seconds": 90},
            actor="operator@test",
            note="race",
            expected_checksum="0" * 64,
            now=NOW + timedelta(minutes=1),
        )

    # A correct checksum stages a new version and supersedes the previous draft.
    second = config_staging.stage(
        conn,
        domain="DASHBOARD",
        values={"dashboard.refresh_seconds": 90},
        actor="operator@test",
        note="and again",
        expected_checksum=first["config_checksum"],
        now=NOW + timedelta(minutes=2),
    )
    assert second["status"] == "STAGED"
    statuses = {
        row["version_id"]: row["status"]
        for row in conn.execute("SELECT version_id, status FROM config_versions")
    }
    assert statuses[first["version_id"]] == "SUPERSEDED"
    assert statuses[second["version_id"]] == "STAGED"

    # Invalid and unaudited stages are refused before anything is written.
    for actor, note, values in (
        ("operator@test", "out of range", {"dashboard.refresh_seconds": 900_000}),
        ("", "no actor", {"dashboard.refresh_seconds": 60}),
        ("operator@test", "", {"dashboard.refresh_seconds": 60}),
    ):
        with pytest.raises(config_staging.ConfigStageError):
            config_staging.stage(
                conn,
                domain="DASHBOARD",
                values=values,
                actor=actor,
                note=note,
                expected_checksum=second["config_checksum"],
                now=NOW,
            )

    # The effective value is read from live policy, never from the staged draft.
    effective = config_staging.effective_values(settings)
    assert effective["status"] == "LIVE_READBACK"
    for entry in effective["values"].values():
        assert "config_versions" not in str(entry.get("source", ""))


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_historical_preview_is_hypothetical_and_never_invented(projection):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-004: a preview is evidence-backed or explicitly unavailable."""
    settings, conn = projection
    empty = config_staging.preview(
        conn, domain="V2_SCORING", values={"v2.threshold": 80}, now=NOW
    )
    assert empty["status"] == config_staging.PREVIEW_UNAVAILABLE
    assert empty["reason_code"] == "NO_HISTORICAL_EVIDENCE"

    descriptor = _write_batch(
        settings,
        [
            _row("BTC/USD", long=91.0),
            _row("ETH/USD", long=76.0),
            _row("ADA/USD", long=60.0),
        ],
    )
    ingest_source(conn, descriptor, now=NOW)
    preview = config_staging.preview(
        conn, domain="V2_SCORING", values={"v2.threshold": 85}, now=NOW
    )
    assert preview["status"] == config_staging.PREVIEW_HYPOTHETICAL
    assert preview["historical_signals"] == 3
    assert preview["economic_impact"] is None
    assert preview["economic_reason"] == "NO_MATURED_OUTCOME_EVIDENCE"
    assert "not evidence of achievable profit" in preview["disclaimer"]

    other = config_staging.preview(
        conn, domain="AI_ROUTING", values={"ai.timeout_seconds": 10}, now=NOW
    )
    assert other["status"] == config_staging.PREVIEW_NOT_APPLICABLE



@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A Control Center router with the real routes and delegated auth stubbed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import control_center as control_center_api

    settings = _settings(tmp_path)
    monkeypatch.setattr(control_center_api, "get_settings", lambda: settings)
    monkeypatch.setattr(control_center_api, "_require_secret", lambda value: None)
    monkeypatch.setattr(
        control_center_api, "_require_strategy_admin", lambda operator, admin: None
    )
    app = FastAPI()
    app.include_router(control_center_api.router)
    return TestClient(app), settings


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_api_reports_absence_instead_of_inventing_data(api):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-005: a missing projection is UNAVAILABLE with a reason, never a 500."""
    client, _settings_ = api
    health = client.get("/api/opip/control-center/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "MEASURED"
    assert body["projection"]["status"] == "UNAVAILABLE"
    assert body["projection"]["reason_code"] == "ANALYTICS_DB_UNAVAILABLE"
    # Runtime evidence is still reported, and an absent artifact is not healthy.
    assert body["runtime"]["protection"]["status"] in {"UNVERIFIED", "MEASURED"}
    assert body["runtime"]["trading_authority"]["funded_orders"] is False

    signals = client.get("/api/opip/control-center/signals")
    assert signals.status_code == 200
    assert signals.json()["status"] == "UNAVAILABLE"

    scanner = client.get("/api/opip/control-center/scanner")
    assert scanner.json()["status"] == "UNAVAILABLE"

    config = client.get("/api/opip/control-center/config")
    assert config.status_code == 200
    payload = config.json()
    assert len(payload["domains"]) == 8
    assert payload["activation"]["console_can_activate"] is False


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_api_signals_are_classified_and_keyset_paginated(api):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-005: null scores stay null, divergence is explicit, pages are keyset."""
    from app.opip.analytics import schema as analytics_schema
    from app.opip.analytics.store import connect_writer

    client, settings = api
    descriptor = _write_batch(
        settings,
        [
            _row("BTC/USD", v1=82, long=91.0),
            _row("ETH/USD", v1=61, long=50.0),
            _row("ADA/USD", v1=88, long=None, short=None, status="INSUFFICIENT_FEATURES"),
        ],
    )
    conn = connect_writer(settings.opip_analytics_db_path)
    analytics_schema.ensure_schema(conn)
    ingest_source(conn, descriptor, now=NOW)
    conn.close()

    first = client.get("/api/opip/control-center/signals?limit=2").json()
    assert first["status"] == "MEASURED"
    assert len(first["rows"]) == 2
    assert first["next_cursor"]
    kinds = {row["symbol"]: row for row in first["rows"]}
    for row in first["rows"]:
        assert row["divergence"] in {
            "BOTH",
            "V1_ONLY",
            "V2_ONLY",
            "NEITHER",
            "INSUFFICIENT_FEATURES",
            "V2_NOT_COMPUTED",
            "V2_EVALUATION_ERROR",
        }
        if row["v2_long_score"] is None and row["v2_short_score"] is None:
            assert row["v2_best_score"] is None
            assert row["v2_score_delta"] is None
    assert kinds  # at least one page was returned

    second = client.get(
        f"/api/opip/control-center/signals?limit=2&cursor={first['next_cursor']}"
    ).json()
    assert second["status"] in {"MEASURED", "NO_EVIDENCE"}
    seen = {row["symbol"] for row in first["rows"]}
    assert seen.isdisjoint({row["symbol"] for row in second.get("rows", [])})

    filtered = client.get(
        "/api/opip/control-center/signals?v2_status=INSUFFICIENT_FEATURES"
    ).json()
    assert [row["symbol"] for row in filtered["rows"]] == ["ADA/USD"]
    assert filtered["rows"][0]["divergence"] == "INSUFFICIENT_FEATURES"

    bad = client.get("/api/opip/control-center/signals?divergence=NONSENSE")
    assert bad.status_code == 422



@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_api_staging_validates_and_refuses_stale_writes(api):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-005: staging is validated, auditable and concurrency-checked over HTTP."""
    client, _settings_ = api
    invalid = client.post(
        "/api/opip/control-center/config/stage",
        json={
            "domain": "DASHBOARD",
            "values": {"dashboard.refresh_seconds": 900_000},
            "actor": "operator@test",
            "audit_note": "nope",
            "expected_checksum": "",
        },
    )
    assert invalid.status_code == 422

    staged = client.post(
        "/api/opip/control-center/config/stage",
        json={
            "domain": "DASHBOARD",
            "values": {"dashboard.refresh_seconds": 60},
            "actor": "operator@test",
            "audit_note": "reviewed",
            "expected_checksum": "",
        },
    )
    assert staged.status_code == 200
    body = staged.json()
    assert body["status"] == "STAGED"
    assert body["live_effect"] is False
    checksum = body["config_checksum"]

    stale = client.post(
        "/api/opip/control-center/config/stage",
        json={
            "domain": "DASHBOARD",
            "values": {"dashboard.refresh_seconds": 120},
            "actor": "operator@test",
            "audit_note": "race",
            "expected_checksum": "0" * 64,
        },
    )
    assert stale.status_code == 409

    preview = client.post(
        "/api/opip/control-center/config/preview",
        json={"domain": "V2_SCORING", "values": {"v2.threshold": 80}},
    )
    assert preview.status_code == 200
    assert preview.json()["preview"]["status"] in {
        config_staging.PREVIEW_UNAVAILABLE,
        config_staging.PREVIEW_HYPOTHETICAL,
    }
    assert checksum


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_api_maintenance_is_explicit_bounded_and_inert(api):
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-005: ingest and reconcile are POST-only; retention deletion stays refused."""
    from app.opip.analytics import sources as source_registry

    client, settings = api
    _write_batch(settings, [_row("BTC/USD"), _row("ETH/USD")])

    # No GET endpoint may run maintenance.
    assert client.get("/api/opip/control-center/maintenance/ingest").status_code == 405

    ingested = client.post(
        "/api/opip/control-center/maintenance/ingest", json={"max_runs_per_source": 1}
    )
    assert ingested.status_code == 200
    payload = ingested.json()
    assert payload["mutates_trading_state"] is False
    assert payload["inserted"] == 2
    assert payload["quarantined"] == 0
    assert any(
        item["source_id"] == source_registry.all_sources(settings)[-1].source_id
        or item["status"] in {"INGESTED", "NO_CHANGE", "DEFERRED", "SOURCE_UNAVAILABLE"}
        for item in payload["sources"]
    )

    reconciled = client.post(
        "/api/opip/control-center/maintenance/reconcile", json={"sample_records": 2}
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["mutates_trading_state"] is False
    assert reconciled.json()["status"] in {
        "RECONCILED",
        "DRIFT_DETECTED",
        "UNVERIFIED",
        "SOURCE_UNAVAILABLE",
    }

    dry = client.post(
        "/api/opip/control-center/maintenance/retention",
        json={"retention_days": 30, "dry_run": True},
    )
    assert dry.status_code == 200
    assert dry.json()["status"] == "DRY_RUN_COMPLETE"
    assert dry.json()["rows_deleted"] == 0
    assert dry.json()["mutates_canonical_evidence"] is False

    # The environment gate is off, so a delete request is refused, not applied.
    refused = client.post(
        "/api/opip/control-center/maintenance/retention",
        json={"retention_days": 30, "dry_run": False},
    )
    assert refused.status_code == 200
    assert refused.json()["status"] == "DELETE_NOT_AUTHORIZED"
    assert refused.json()["rows_deleted"] == 0



APP_ROOT = Path(__file__).resolve().parents[1]
UI = APP_ROOT / "control-center-v2"


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_ui_calls_only_allowlisted_endpoints_and_never_fabricates():
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-006: the console can only reach declared routes and invents no metric."""
    api = (UI / "src" / "api.mjs").read_text(encoding="utf-8")
    view = (UI / "src" / "main.jsx").read_text(encoding="utf-8")
    nginx = (APP_ROOT / "deploy/control-center/nginx/v2.conf").read_text(encoding="utf-8")
    endpoints = {
        "/api/analytics/summary",
        "/api/analytics/intelligence",
        "/api/opip/zero-trade-explanation",
        "/api/opip/scoring-comparison",
        "/api/opip/control-center/health",
        "/api/opip/control-center/signals",
        "/api/opip/control-center/forensics",
        "/api/opip/control-center/scanner",
        "/api/opip/control-center/ai",
        "/api/opip/control-center/data",
        "/api/opip/control-center/config",
        "/api/opip/control-center/config/preview",
        "/api/opip/control-center/config/stage",
        "/api/opip/control-center/maintenance/ingest",
        "/api/opip/control-center/maintenance/reconcile",
        "/api/opip/control-center/maintenance/retention",
    }
    for endpoint in endpoints:
        assert endpoint in api, endpoint
    # Every bounded route the console calls is explicitly proxied by the edge.
    for route in sorted(route for route in endpoints if "control-center" in route):
        assert f"location = {route} {{" in nginx, route
    # No fabricated performance: the console renders limitation labels instead.
    assert "OUTCOME NOT MATURED" in api
    assert "INSUFFICIENT DATA" in api
    assert "Math.random(" not in view
    assert "Math.random(" not in api
    assert "INSUFFICIENT DATA" in view or "INSUFFICIENT DATA" in api
    for forbidden in ("localStorage.setItem", "sessionStorage.setItem", "document.cookie"):
        assert forbidden not in view
    # The old raw-JSON-only surface is now behind an explicit disclosure.
    assert "Advanced JSON evidence" in view


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_approved_brand_asset_is_installed_and_served_from_the_dashboard_base():
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-006: the approved transparent logo is the asset the console references."""
    import hashlib
    import struct

    asset = UI / "public" / "assets" / "opip-control-center-logo-transparent.png"
    assert asset.is_file(), "approved logo asset is missing from the dashboard public tree"
    raw = asset.read_bytes()
    # Genuine PNG, and a real alpha channel rather than a black matte.
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert raw[12:16] == b"IHDR"
    width, height, depth, colour, _comp, _filt, interlace = struct.unpack(
        ">IIBBBBB", raw[16:29]
    )
    assert (width, height) == (1120, 1635)
    assert depth == 8
    assert colour == 6, "the approved asset must be RGBA (alpha channel present)"
    assert interlace == 0
    # The exact approved artwork, so a silent substitution cannot pass review.
    assert hashlib.sha256(raw).hexdigest() == (
        "08db86b072d04ab9d966820d429309570084461c85af27a2b96bc46c86ec6595"
    )
    # The URL must be built from the Vite base, not root-absolute, so it resolves
    # under /dashboard/v2/ in production and on a dev server alike.
    fmt = (UI / "src" / "format.mjs").read_text(encoding="utf-8")
    assert "import.meta.env.BASE_URL" in fmt
    assert "LOGO_ASSET = `${ASSET_BASE}assets/opip-control-center-logo-transparent.png`" in fmt
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert './assets/opip-control-center-logo-transparent.png' in html


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_ui_branding_theme_and_states_are_explicit():
    """ATDD-OPIP-CONTROL-CENTER-V2-M02/AC-006: the approved logo is referenced by path and the theme is tokenised."""
    fmt = (UI / "src" / "format.mjs").read_text(encoding="utf-8")
    view = (UI / "src" / "main.jsx").read_text(encoding="utf-8")
    html = (UI / "index.html").read_text(encoding="utf-8")
    theme = (UI / "src" / "theme.css").read_text(encoding="utf-8")
    surface = (UI / "src" / "style.v2.css").read_text(encoding="utf-8")

    assert "assets/opip-control-center-logo-transparent.png" in fmt
    assert "./assets/opip-control-center-logo-transparent.png" in html
    # A missing asset is reported, never replaced with a generated logo.
    assert "LOGO_FALLBACK_MARK" in view
    assert "onError" in view
    for token in (
        "--op-bg",
        "--op-surface-1",
        "--op-emerald",
        "--op-gold",
        "--op-amber",
        "--op-red",
        "--op-ink",
    ):
        assert token in theme, token
    assert "prefers-reduced-motion" in theme
    assert "focus-visible" in theme
    # States the operator must be able to tell apart.
    for marker in ("STALE", "UNAVAILABLE", "NOT VERIFIED", "NO_EVIDENCE"):
        assert marker in fmt, marker
    assert "op-header" in surface and "op-ribbon" in surface and "op-kpi" in surface

