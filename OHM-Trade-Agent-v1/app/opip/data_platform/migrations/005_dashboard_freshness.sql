-- Canonical dashboard freshness contract.
--
-- ops.dashboard_freshness_v is the single classification consumed by Grafana,
-- the API/dashboard read model, stale-data selection, and
-- `health --require-ready`.  The classification constants mirror
-- app/opip/data_platform/freshness.py exactly; that module is the source of
-- truth and any policy change requires a new migration plus the parity tests
-- in tests/test_opip_dashboard_freshness_v1.py.

-- Required-stream policy is protected configuration: the shipper role only
-- reads it.  Synchronization runs under an administrative or maintenance role
-- through `migrations sync-required-streams`.
CREATE TABLE IF NOT EXISTS ops.required_stream (
    stream_name text PRIMARY KEY,
    required boolean NOT NULL,
    requires_typed_projection boolean NOT NULL,
    threshold_seconds integer CHECK (threshold_seconds IS NULL OR threshold_seconds > 0),
    sync_fingerprint text NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON ops.required_stream FROM PUBLIC;
REVOKE ALL ON ops.required_stream FROM opip_shipper;
REVOKE ALL ON ops.required_stream FROM opip_learning;
REVOKE ALL ON ops.required_stream FROM opip_dashboard;
GRANT SELECT ON ops.required_stream TO opip_shipper;
GRANT SELECT ON ops.required_stream TO opip_learning;
GRANT SELECT ON ops.required_stream TO opip_dashboard;

-- Maintenance evidence recorded by the analytics maintenance wrapper.  Both
-- the shipper (no writes) and the dashboard role (read-only) are denied
-- mutation; only the maintenance/admin role records runs.
CREATE TABLE IF NOT EXISTS ops.maintenance_run (
    maintenance_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('SUCCESS', 'FAILED', 'SKIPPED')),
    detail text,
    policy_fingerprint text,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL
);
REVOKE ALL ON ops.maintenance_run FROM PUBLIC;
REVOKE ALL ON ops.maintenance_run FROM opip_shipper;
REVOKE ALL ON ops.maintenance_run FROM opip_learning;
REVOKE ALL ON ops.maintenance_run FROM opip_dashboard;
GRANT SELECT ON ops.maintenance_run TO opip_shipper;
GRANT SELECT ON ops.maintenance_run TO opip_learning;
GRANT SELECT ON ops.maintenance_run TO opip_dashboard;

CREATE OR REPLACE FUNCTION ops.freshness_reason(
    p_required boolean,
    p_requires_typed boolean,
    p_threshold_seconds integer,
    p_policy_present boolean,
    p_source_updated_at timestamptz,
    p_last_ingested_at timestamptz,
    p_typed_watermark_at timestamptz,
    p_last_polled_at timestamptz,
    p_unresolved_dead_letters bigint,
    p_reconciliation_status text,
    p_last_reconciled_at timestamptz,
    p_now timestamptz
) RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN NOT p_policy_present THEN 'UNKNOWN_STREAM_POLICY'
        WHEN NOT p_required THEN CASE
            WHEN p_last_ingested_at IS NOT NULL
                AND p_last_ingested_at <= p_now
                AND p_now - p_last_ingested_at <= interval '86400 seconds'
                THEN NULL
            WHEN p_last_ingested_at IS NULL
                AND p_source_updated_at IS NOT NULL
                AND p_source_updated_at <= p_now
                AND p_now - p_source_updated_at <= interval '86400 seconds'
                THEN NULL
            ELSE 'STALE_DATA'
        END
        WHEN p_last_ingested_at IS NULL THEN 'MISSING_STREAM_ROW'
        WHEN p_requires_typed AND p_typed_watermark_at IS NULL
            THEN 'MISSING_TYPED_PROJECTION'
        WHEN greatest(
            coalesce(p_source_updated_at, '-infinity'::timestamptz),
            coalesce(p_last_ingested_at, '-infinity'::timestamptz),
            coalesce(p_typed_watermark_at, '-infinity'::timestamptz),
            coalesce(p_last_polled_at, '-infinity'::timestamptz),
            coalesce(p_last_reconciled_at, '-infinity'::timestamptz)
        ) > p_now + interval '300 seconds' THEN 'INVALID_TIMESTAMPS'
        WHEN p_reconciliation_status = 'ERROR' THEN 'RECONCILIATION_ERROR'
        WHEN p_reconciliation_status IS NULL OR p_reconciliation_status <> 'CLEAN'
            THEN 'RECONCILIATION_UNKNOWN'
        WHEN p_last_reconciled_at IS NULL THEN 'MISSING_RECONCILIATION'
        WHEN p_unresolved_dead_letters > 0 THEN 'UNRESOLVED_DEAD_LETTERS'
        WHEN p_now - coalesce(p_typed_watermark_at, p_last_ingested_at)
            > interval '3600 seconds' THEN CASE
                WHEN p_threshold_seconds IS NULL
                    OR p_now - coalesce(p_typed_watermark_at, p_last_ingested_at)
                        > make_interval(secs => p_threshold_seconds)
                    THEN 'PER_STREAM_THRESHOLD_EXCEEDED'
                ELSE 'STALE_DATA'
            END
        ELSE NULL
    END
$$;

CREATE MATERIALIZED VIEW IF NOT EXISTS ops.dashboard_freshness_mv AS
WITH maintenance_latest AS (
    SELECT status, finished_at
    FROM ops.maintenance_run
    ORDER BY finished_at DESC
    LIMIT 1
),
maintenance_drift AS (
    SELECT count(*) > 0 AS drift
    FROM ops.maintenance_run
    WHERE policy_fingerprint IS NOT NULL
      AND policy_fingerprint <> (
          SELECT rs.sync_fingerprint FROM ops.required_stream rs LIMIT 1
      )
),
policy_present AS (
    SELECT count(*) > 0 AS present FROM ops.required_stream
),
components AS (
    SELECT
        policy.stream_name,
        CASE
            WHEN ops.freshness_reason(
                policy.required,
                policy.requires_typed_projection,
                policy.threshold_seconds,
                true,
                checkpoint.updated_at,
                raw_watermark.last_ingested_at,
                typed_watermark.watermark_at,
                checkpoint.updated_at,
                coalesce(dead.dead_letters, 0),
                reconcile.status,
                reconcile.checked_at,
                now()
            ) IS NULL THEN 'LIVE'
            WHEN ops.freshness_reason(
                policy.required,
                policy.requires_typed_projection,
                policy.threshold_seconds,
                true,
                checkpoint.updated_at,
                raw_watermark.last_ingested_at,
                typed_watermark.watermark_at,
                checkpoint.updated_at,
                coalesce(dead.dead_letters, 0),
                reconcile.status,
                reconcile.checked_at,
                now()
            ) = 'STALE_DATA' THEN 'STALE'
            ELSE 'UNAVAILABLE'
        END AS status,
        ops.freshness_reason(
            policy.required,
            policy.requires_typed_projection,
            policy.threshold_seconds,
            true,
            checkpoint.updated_at,
            raw_watermark.last_ingested_at,
            typed_watermark.watermark_at,
            checkpoint.updated_at,
            coalesce(dead.dead_letters, 0),
            reconcile.status,
            reconcile.checked_at,
            now()
        ) AS reason,
        CASE
            WHEN policy.requires_typed_projection THEN typed_watermark.watermark_at
            ELSE raw_watermark.last_ingested_at
        END AS reference_at,
        coalesce(dead.dead_letters, 0) AS unresolved_dead_letters,
        reconcile.status AS last_reconciliation_status,
        reconcile.checked_at AS last_reconciled_at
    FROM ops.required_stream policy
    LEFT JOIN ops.ingest_checkpoint checkpoint
        ON checkpoint.stream_name = policy.stream_name
    LEFT JOIN LATERAL (
        SELECT max(item.ingested_at) AS last_ingested_at
        FROM raw.ingested_event item
        WHERE item.stream_name = policy.stream_name
    ) raw_watermark ON true
    LEFT JOIN LATERAL (
        SELECT CASE policy.stream_name
            WHEN 'screening_evaluations'
                THEN (SELECT max(observed_at) FROM market.screening)
            WHEN 'funnel_events'
                THEN (SELECT max(occurred_at) FROM lifecycle.stage_transition)
            WHEN 'intelligence_events'
                THEN (SELECT max(observed_at) FROM signal.intelligence_event)
            WHEN 'full_market_observations'
                THEN (SELECT max(observed_at) FROM market.observation)
            WHEN 'paper_trade_events'
                THEN (SELECT max(occurred_at) FROM paper.trade_event)
            ELSE NULL
        END AS watermark_at
    ) typed_watermark ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS dead_letters
        FROM ops.dead_letter item
        WHERE item.stream_name = policy.stream_name
          AND item.resolved_at IS NULL
    ) dead ON true
    LEFT JOIN LATERAL (
        SELECT item.status, item.checked_at
        FROM ops.reconciliation_run item
        WHERE item.stream_name = policy.stream_name
        ORDER BY item.checked_at DESC
        LIMIT 1
    ) reconcile ON true
    UNION ALL
    SELECT
        checkpoint.stream_name,
        CASE
            WHEN ops.freshness_reason(
                false, false, 86400, false,
                checkpoint.updated_at, NULL, NULL, checkpoint.updated_at,
                0, NULL, NULL, now()
            ) IS NULL THEN 'LIVE'
            ELSE 'UNAVAILABLE'
        END AS status,
        'UNKNOWN_STREAM_POLICY'::text AS reason,
        checkpoint.updated_at AS reference_at,
        0::bigint AS unresolved_dead_letters,
        NULL::text AS last_reconciliation_status,
        NULL::timestamptz AS last_reconciled_at
    FROM ops.ingest_checkpoint checkpoint
    WHERE NOT EXISTS (
        SELECT 1 FROM ops.required_stream policy
        WHERE policy.stream_name = checkpoint.stream_name
    )
    UNION ALL
    SELECT
        '__maintenance__'::text,
        CASE
            WHEN NOT (SELECT present FROM policy_present)
                THEN 'UNAVAILABLE'
            WHEN (SELECT drift FROM maintenance_drift)
                THEN 'UNAVAILABLE'
            WHEN (SELECT status FROM maintenance_latest) IS NULL
                THEN 'UNAVAILABLE'
            WHEN (SELECT status FROM maintenance_latest) <> 'SUCCESS'
                THEN 'UNAVAILABLE'
            WHEN (SELECT finished_at FROM maintenance_latest)
                > now() + interval '300 seconds'
                THEN 'UNAVAILABLE'
            WHEN now() - (SELECT finished_at FROM maintenance_latest)
                > interval '3600 seconds'
                THEN 'UNAVAILABLE'
            ELSE 'LIVE'
        END AS status,
        CASE
            WHEN NOT (SELECT present FROM policy_present)
                THEN 'MISSING_POLICY'
            WHEN (SELECT drift FROM maintenance_drift)
                THEN 'CONFIGURATION_DRIFT'
            WHEN (SELECT status FROM maintenance_latest) IS NULL
                THEN 'MAINTENANCE_NEVER_RAN'
            WHEN (SELECT status FROM maintenance_latest) <> 'SUCCESS'
                THEN 'MAINTENANCE_FAILED'
            WHEN (SELECT finished_at FROM maintenance_latest)
                > now() + interval '300 seconds'
                THEN 'INVALID_TIMESTAMPS'
            WHEN now() - (SELECT finished_at FROM maintenance_latest)
                > interval '3600 seconds'
                THEN 'MAINTENANCE_STALE'
            ELSE NULL
        END AS reason,
        (SELECT finished_at FROM maintenance_latest) AS reference_at,
        0::bigint AS unresolved_dead_letters,
        NULL::text AS last_reconciliation_status,
        NULL::timestamptz AS last_reconciled_at
),
freshness_snapshot AS (
    SELECT
        stream_name,
        status,
        reason,
        reference_at,
        unresolved_dead_letters,
        last_reconciliation_status,
        last_reconciled_at,
        CASE
            WHEN reference_at IS NULL OR reference_at > now() THEN NULL
            ELSE extract(epoch FROM (now() - reference_at))::bigint
        END AS age_seconds
    FROM components
)
SELECT
    stream_name,
    status,
    reason,
    reference_at,
    unresolved_dead_letters,
    last_reconciliation_status,
    last_reconciled_at,
    age_seconds
FROM freshness_snapshot
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS dashboard_freshness_mv_stream_idx
    ON ops.dashboard_freshness_mv (stream_name);

CREATE OR REPLACE VIEW ops.dashboard_freshness_v AS
SELECT
    stream_name,
    status,
    reason,
    reference_at,
    unresolved_dead_letters,
    last_reconciliation_status,
    last_reconciled_at,
    age_seconds
FROM ops.dashboard_freshness_mv;

GRANT SELECT ON ops.dashboard_freshness_mv TO opip_dashboard;
GRANT SELECT ON ops.dashboard_freshness_mv TO opip_shipper;
GRANT SELECT ON ops.dashboard_freshness_mv TO opip_learning;

CREATE OR REPLACE VIEW ops.platform_health_v AS
SELECT
    freshness.stream_name,
    checkpoint.source_file,
    checkpoint.byte_offset,
    checkpoint.rows_ingested,
    checkpoint.source_size,
    checkpoint.updated_at,
    CASE
        WHEN checkpoint.updated_at IS NULL OR checkpoint.updated_at > now() THEN NULL
        ELSE extract(epoch FROM (now() - checkpoint.updated_at))::bigint
    END AS lag_seconds,
    freshness.unresolved_dead_letters,
    freshness.last_reconciliation_status,
    freshness.last_reconciled_at,
    freshness.status AS freshness_status,
    freshness.reason AS freshness_reason
FROM ops.dashboard_freshness_v freshness
LEFT JOIN ops.ingest_checkpoint checkpoint
    ON checkpoint.stream_name = freshness.stream_name
WHERE freshness.stream_name <> '__maintenance__';

GRANT SELECT ON ops.platform_health_v TO opip_dashboard;
GRANT SELECT ON ops.platform_health_v TO opip_shipper;
GRANT SELECT ON ops.platform_health_v TO opip_learning;
