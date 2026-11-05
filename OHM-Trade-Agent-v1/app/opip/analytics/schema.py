"""Versioned, indexed schema for the derived analytical projection.

Every table is either

* an evidence projection (a row exists because a canonical record exists), or
* analytical bookkeeping (checkpoints, quarantine, reconciliation and retention
  telemetry) that reconciliation and the Control Center must be able to audit.

Nothing here is a source of truth for trading. ``schema_meta`` records the
projection schema version so an older database is migrated additively and a
newer one is refused instead of being silently rewritten.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

#: Provenance columns every evidence projection carries. ``is_verified = 0`` and
#: ``is_audit_locked = 1`` are both retention protections.
_PROVENANCE = """
    source_id        TEXT    NOT NULL,
    source_generation INTEGER NOT NULL DEFAULT 0,
    byte_offset      INTEGER,
    line_number      INTEGER,
    record_sha256    TEXT    NOT NULL,
    observed_at_utc  TEXT,
    ingested_at_utc  TEXT    NOT NULL,
    is_verified      INTEGER NOT NULL DEFAULT 1,
    is_audit_locked  INTEGER NOT NULL DEFAULT 0
"""

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_sources (
        source_id       TEXT PRIMARY KEY,
        record_kind     TEXT NOT NULL,
        display_name    TEXT NOT NULL,
        path            TEXT NOT NULL,
        manifest_required INTEGER NOT NULL DEFAULT 0,
        registered_at_utc TEXT NOT NULL,
        schema_version  INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
        source_id          TEXT PRIMARY KEY
                           REFERENCES ingestion_sources(source_id),
        generation         INTEGER NOT NULL DEFAULT 0,
        device_id          TEXT,
        file_size          INTEGER NOT NULL DEFAULT 0,
        last_offset        INTEGER NOT NULL DEFAULT 0,
        last_record_hash   TEXT,
        records_committed  INTEGER NOT NULL DEFAULT 0,
        last_verified_at_utc TEXT,
        updated_at_utc     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_events (
        event_id         TEXT PRIMARY KEY,
        source_id        TEXT NOT NULL,
        run_id           TEXT NOT NULL,
        started_at_utc   TEXT NOT NULL,
        finished_at_utc  TEXT,
        status           TEXT NOT NULL,
        records_read     INTEGER NOT NULL DEFAULT 0,
        records_inserted INTEGER NOT NULL DEFAULT 0,
        records_duplicate INTEGER NOT NULL DEFAULT 0,
        records_quarantined INTEGER NOT NULL DEFAULT 0,
        bytes_read       INTEGER NOT NULL DEFAULT 0,
        reason_code      TEXT,
        detail           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ingestion_events_source "
    "ON ingestion_events(source_id, started_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS ingested_records (
        source_id        TEXT NOT NULL,
        source_generation INTEGER NOT NULL,
        byte_offset      INTEGER NOT NULL,
        line_number      INTEGER NOT NULL,
        record_sha256    TEXT NOT NULL,
        record_kind      TEXT NOT NULL,
        entity_key       TEXT,
        observed_at_utc  TEXT,
        ingested_at_utc  TEXT NOT NULL,
        PRIMARY KEY (source_id, source_generation, byte_offset)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ingested_records_observed "
    "ON ingested_records(source_id, observed_at_utc)",
    """
    CREATE TABLE IF NOT EXISTS quarantined_records (
        quarantine_id    TEXT PRIMARY KEY,
        source_id        TEXT NOT NULL,
        source_generation INTEGER NOT NULL DEFAULT 0,
        byte_offset      INTEGER,
        line_number      INTEGER,
        record_sha256    TEXT,
        reason_code      TEXT NOT NULL,
        raw_excerpt      TEXT,
        observed_at_utc  TEXT,
        ingested_at_utc  TEXT NOT NULL,
        is_verified      INTEGER NOT NULL DEFAULT 0,
        is_audit_locked  INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_quarantine_source "
    "ON quarantined_records(source_id, ingested_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS scanner_attempt_events (
        event_key        TEXT PRIMARY KEY,
        attempt_id       TEXT NOT NULL,
        event_seq        INTEGER,
        event_type       TEXT,
        state            TEXT,
        outcome          TEXT,
        reason_code      TEXT,
        fence            INTEGER,
        attempt_created_at_utc TEXT,
        finished_at_utc  TEXT,
        container_id     TEXT,
        evidence_json    TEXT,
    """ + _PROVENANCE + """
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_attempt_events_attempt "
    "ON scanner_attempt_events(attempt_id, event_seq)",
    "CREATE INDEX IF NOT EXISTS ix_attempt_events_observed "
    "ON scanner_attempt_events(observed_at_utc)",
    """
    CREATE TABLE IF NOT EXISTS scanner_snapshots (
        snapshot_key     TEXT PRIMARY KEY,
        attempt_id       TEXT NOT NULL,
        symbol           TEXT NOT NULL,
        underlying_asset TEXT,
        trade_direction  TEXT,
        timeframe        TEXT,
        technical_score  REAL,
        technical_score_version TEXT,
        directional_long_score  REAL,
        directional_short_score REAL,
        v2_long_score    REAL,
        v2_short_score   REAL,
        v2_feature_status TEXT,
        v2_profile_version TEXT,
        v2_profile_digest  TEXT,
        outcome_maturity TEXT,
        rejection_reason TEXT,
    """ + _PROVENANCE + """
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_snapshots_observed "
    "ON scanner_snapshots(observed_at_utc DESC, attempt_id, symbol)",
    "CREATE INDEX IF NOT EXISTS ix_snapshots_symbol "
    "ON scanner_snapshots(symbol, observed_at_utc DESC)",
    "CREATE INDEX IF NOT EXISTS ix_snapshots_v2_status "
    "ON scanner_snapshots(v2_feature_status, observed_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS scoring_components (
        snapshot_key     TEXT NOT NULL,
        direction        TEXT NOT NULL,
        component        TEXT NOT NULL,
        points           REAL,
        observed_at_utc  TEXT,
        source_id        TEXT NOT NULL,
        source_generation INTEGER NOT NULL DEFAULT 0,
        record_sha256    TEXT NOT NULL,
        ingested_at_utc  TEXT NOT NULL,
        is_verified      INTEGER NOT NULL DEFAULT 1,
        is_audit_locked  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (snapshot_key, direction, component)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_components_observed "
    "ON scoring_components(observed_at_utc)",
    """
    CREATE TABLE IF NOT EXISTS scoring_comparisons (
        comparison_key   TEXT PRIMARY KEY,
        scan_id          TEXT NOT NULL,
        model_authority  TEXT,
        v1_threshold     REAL,
        v2_threshold     REAL,
        scanned          INTEGER,
        v2_ready         INTEGER,
        v1_top_json      TEXT,
        v2_top_json      TEXT,
        v1_only_json     TEXT,
        v2_only_json     TEXT,
        outcome_status   TEXT,
        schema_version   INTEGER,
    """ + _PROVENANCE + """
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_comparisons_observed "
    "ON scoring_comparisons(observed_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS qualification_events (
        event_key        TEXT PRIMARY KEY,
        attempt_id       TEXT,
        stage            TEXT NOT NULL,
        state            TEXT,
        count_value      INTEGER,
        reason_code      TEXT,
        evidence_ref     TEXT,
    """ + _PROVENANCE + """
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_qualification_observed "
    "ON qualification_events(observed_at_utc)",
    """
    CREATE TABLE IF NOT EXISTS ai_inferences (
        inference_key    TEXT PRIMARY KEY,
        symbol           TEXT,
        direction        TEXT,
        provider         TEXT,
        model            TEXT,
        model_version    TEXT,
        verdict          TEXT,
        confidence       REAL,
        agreement_state  TEXT,
        duration_ms      REAL,
        outcome_maturity TEXT,
        verified_result  TEXT,
        request_ref      TEXT,
    """ + _PROVENANCE + """
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ai_observed "
    "ON ai_inferences(observed_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS operational_telemetry (
        telemetry_key    TEXT PRIMARY KEY,
        section          TEXT NOT NULL,
        attempt_id       TEXT,
        payload_json     TEXT NOT NULL,
    """ + _PROVENANCE + """
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_telemetry_section "
    "ON operational_telemetry(section, observed_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS reconciliation_runs (
        run_id           TEXT PRIMARY KEY,
        started_at_utc   TEXT NOT NULL,
        finished_at_utc  TEXT,
        status           TEXT NOT NULL,
        sources_checked  INTEGER NOT NULL DEFAULT 0,
        sources_drifted  INTEGER NOT NULL DEFAULT 0,
        sources_unavailable INTEGER NOT NULL DEFAULT 0,
        detail           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_reconciliation_runs_started "
    "ON reconciliation_runs(started_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS reconciliation_audit (
        audit_id         TEXT PRIMARY KEY,
        run_id           TEXT NOT NULL,
        source_id        TEXT NOT NULL,
        status           TEXT NOT NULL,
        expected_records INTEGER,
        actual_records   INTEGER,
        covered_purged_records INTEGER,
        first_mismatch_offset INTEGER,
        quarantined_records INTEGER,
        reason_code      TEXT,
        detail           TEXT,
        recorded_at_utc  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_reconciliation_audit_run "
    "ON reconciliation_audit(run_id, source_id)",
    """
    CREATE TABLE IF NOT EXISTS retention_policies (
        policy_version   TEXT PRIMARY KEY,
        retention_days   INTEGER NOT NULL,
        dry_run          INTEGER NOT NULL DEFAULT 1,
        actor            TEXT,
        note             TEXT,
        recorded_at_utc  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retention_cleanup_logs (
        run_id           TEXT PRIMARY KEY,
        policy_version   TEXT NOT NULL,
        retention_days   INTEGER NOT NULL,
        started_at_utc   TEXT NOT NULL,
        finished_at_utc  TEXT,
        duration_ms      REAL,
        dry_run          INTEGER NOT NULL DEFAULT 1,
        rows_considered  INTEGER NOT NULL DEFAULT 0,
        rows_deleted     INTEGER NOT NULL DEFAULT 0,
        rows_protected_unverified INTEGER NOT NULL DEFAULT 0,
        rows_protected_locked INTEGER NOT NULL DEFAULT 0,
        rows_protected_referenced INTEGER NOT NULL DEFAULT 0,
        bytes_reclaimed  INTEGER,
        memory_peak_kib  INTEGER,
        status           TEXT NOT NULL,
        error_code       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_retention_logs_started "
    "ON retention_cleanup_logs(started_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS retention_coverage (
        coverage_key     TEXT PRIMARY KEY,
        source_id        TEXT NOT NULL,
        source_generation INTEGER NOT NULL,
        purged_through_utc TEXT NOT NULL,
        records_purged   INTEGER NOT NULL,
        recorded_at_utc  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_retention_coverage_source "
    "ON retention_coverage(source_id, source_generation)",
    """
    CREATE TABLE IF NOT EXISTS config_definitions (
        config_key       TEXT PRIMARY KEY,
        domain           TEXT NOT NULL,
        display_name     TEXT NOT NULL,
        data_type        TEXT NOT NULL,
        default_value    TEXT,
        minimum_value    TEXT,
        maximum_value    TEXT,
        step_value       TEXT,
        unit             TEXT,
        allowed_values   TEXT,
        sensitivity      TEXT NOT NULL DEFAULT 'NONE',
        editability      TEXT NOT NULL DEFAULT 'READ_ONLY',
        activation_authority TEXT NOT NULL DEFAULT 'NONE',
        read_only_reason TEXT,
        registry_version INTEGER NOT NULL DEFAULT 1,
        updated_at_utc   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_config_definitions_domain "
    "ON config_definitions(domain, config_key)",
    """
    CREATE TABLE IF NOT EXISTS config_versions (
        version_id       TEXT PRIMARY KEY,
        domain           TEXT NOT NULL,
        status           TEXT NOT NULL,
        document_json    TEXT NOT NULL,
        config_checksum  TEXT NOT NULL,
        parent_version_id TEXT,
        expected_checksum TEXT,
        actor            TEXT NOT NULL,
        audit_note       TEXT NOT NULL,
        validation_json  TEXT,
        preview_json     TEXT,
        source_revision  TEXT,
        registry_version INTEGER NOT NULL DEFAULT 1,
        created_at_utc   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_config_versions_domain "
    "ON config_versions(domain, created_at_utc DESC)",
    """
    CREATE TABLE IF NOT EXISTS config_audit_log (
        audit_id         TEXT PRIMARY KEY,
        version_id       TEXT,
        domain           TEXT NOT NULL,
        action           TEXT NOT NULL,
        actor            TEXT,
        detail           TEXT,
        recorded_at_utc  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_config_audit_domain "
    "ON config_audit_log(domain, recorded_at_utc DESC)",
)

#: Entity tables carrying an ``observed_at_utc`` column that retention may purge.
RETENTION_ELIGIBLE_TABLES: tuple[str, ...] = (
    "scanner_snapshots",
    "scoring_components",
    "scanner_attempt_events",
    "scoring_comparisons",
    "qualification_events",
    "ai_inferences",
    "operational_telemetry",
    "ingested_records",
)

#: Tables retention must never touch: durable evidence, audit and configuration.
RETENTION_PROTECTED_TABLES: tuple[str, ...] = (
    "ingestion_sources",
    "ingestion_checkpoints",
    "ingestion_events",
    "quarantined_records",
    "reconciliation_runs",
    "reconciliation_audit",
    "retention_policies",
    "retention_cleanup_logs",
    "retention_coverage",
    "config_definitions",
    "config_versions",
    "config_audit_log",
    "schema_meta",
)


def current_version(conn: sqlite3.Connection) -> int:
    """Return the recorded projection schema version, or 0 when unmigrated."""
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Create or additively migrate the projection. Returns the version.

    A database written by a newer projection schema is refused rather than
    downgraded: silently rewriting a newer analytical store would destroy
    evidence this build does not understand.
    """
    recorded = current_version(conn)
    if recorded > SCHEMA_VERSION:
        raise ValueError("ANALYTICS_SCHEMA_NEWER_THAN_BUILD")
    for statement in _DDL:
        conn.execute(statement)
    if recorded != SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
    return SCHEMA_VERSION
