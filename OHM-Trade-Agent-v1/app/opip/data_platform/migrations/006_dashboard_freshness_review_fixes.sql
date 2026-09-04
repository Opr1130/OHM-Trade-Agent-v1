-- Review remediation for the canonical dashboard freshness contract.
--
-- The materialized layer stores evidence only. Time-relative status and age
-- are evaluated in ops.dashboard_freshness_v against the current clock so a
-- stopped refresher can never leave a frozen LIVE status behind.

DROP VIEW IF EXISTS ops.platform_health_v;
DROP VIEW IF EXISTS ops.dashboard_freshness_v;
DROP MATERIALIZED VIEW IF EXISTS ops.dashboard_freshness_mv;

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
        WHEN greatest(
            coalesce(p_source_updated_at, '-infinity'::timestamptz),
            coalesce(p_last_ingested_at, '-infinity'::timestamptz),
            coalesce(p_typed_watermark_at, '-infinity'::timestamptz),
            coalesce(p_last_polled_at, '-infinity'::timestamptz),
            coalesce(p_last_reconciled_at, '-infinity'::timestamptz)
        ) > p_now + interval '300 seconds' THEN 'INVALID_TIMESTAMPS'
        WHEN NOT p_required THEN CASE
            WHEN coalesce(p_last_ingested_at, p_source_updated_at) IS NOT NULL
                AND p_now - coalesce(p_last_ingested_at, p_source_updated_at)
                    <= interval '86400 seconds'
                THEN NULL
            ELSE 'STALE_DATA'
        END
        WHEN p_last_ingested_at IS NULL THEN 'MISSING_STREAM_ROW'
        WHEN p_requires_typed AND p_typed_watermark_at IS NULL
            THEN 'MISSING_TYPED_PROJECTION'
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

CREATE MATERIALIZED VIEW ops.dashboard_freshness_mv AS
WITH policy_components AS (
    SELECT
        policy.stream_name,
        policy.required,
        policy.requires_typed_projection,
        policy.threshold_seconds,
        true AS policy_present,
        checkpoint.updated_at AS source_updated_at,
        raw_watermark.last_ingested_at,
        typed_watermark.watermark_at AS typed_watermark_at,
        checkpoint.updated_at AS last_polled_at,
        coalesce(dead.dead_letters, 0)::bigint AS unresolved_dead_letters,
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
),
unknown_names AS (
    SELECT stream_name FROM ops.ingest_checkpoint
    UNION
    SELECT stream_name FROM raw.ingested_event
    EXCEPT
    SELECT stream_name FROM ops.required_stream
),
unknown_components AS (
    SELECT
        unknown.stream_name,
        false AS required,
        false AS requires_typed_projection,
        86400::integer AS threshold_seconds,
        false AS policy_present,
        checkpoint.updated_at AS source_updated_at,
        raw_watermark.last_ingested_at,
        NULL::timestamptz AS typed_watermark_at,
        checkpoint.updated_at AS last_polled_at,
        0::bigint AS unresolved_dead_letters,
        NULL::text AS last_reconciliation_status,
        NULL::timestamptz AS last_reconciled_at
    FROM unknown_names unknown
    LEFT JOIN ops.ingest_checkpoint checkpoint
        ON checkpoint.stream_name = unknown.stream_name
    LEFT JOIN LATERAL (
        SELECT max(item.ingested_at) AS last_ingested_at
        FROM raw.ingested_event item
        WHERE item.stream_name = unknown.stream_name
    ) raw_watermark ON true
)
SELECT *, now() AS snapshot_refreshed_at FROM policy_components
UNION ALL
SELECT *, now() AS snapshot_refreshed_at FROM unknown_components
WITH NO DATA;

CREATE UNIQUE INDEX dashboard_freshness_mv_stream_idx
    ON ops.dashboard_freshness_mv (stream_name);

CREATE OR REPLACE VIEW ops.dashboard_freshness_v AS
WITH evaluated AS (
    SELECT
        evidence.*,
        reason_eval.reason,
        CASE
            WHEN reason_eval.reason IS NULL THEN 'LIVE'
            WHEN reason_eval.reason = 'STALE_DATA' THEN 'STALE'
            ELSE 'UNAVAILABLE'
        END AS status,
        CASE
            WHEN evidence.requires_typed_projection
                THEN evidence.typed_watermark_at
            WHEN evidence.required
                THEN evidence.last_ingested_at
            ELSE coalesce(evidence.last_ingested_at, evidence.source_updated_at)
        END AS reference_at
    FROM ops.dashboard_freshness_mv evidence
    CROSS JOIN LATERAL (
        SELECT ops.freshness_reason(
            evidence.required,
            evidence.requires_typed_projection,
            evidence.threshold_seconds,
            evidence.policy_present,
            evidence.source_updated_at,
            evidence.last_ingested_at,
            evidence.typed_watermark_at,
            evidence.last_polled_at,
            evidence.unresolved_dead_letters,
            evidence.last_reconciliation_status,
            evidence.last_reconciled_at,
            now()
        ) AS reason
    ) reason_eval
),
maintenance_latest AS (
    SELECT status, finished_at, policy_fingerprint
    FROM ops.maintenance_run
    ORDER BY finished_at DESC, maintenance_id DESC
    LIMIT 1
),
policy_meta AS (
    SELECT
        count(*) > 0 AS present,
        count(DISTINCT sync_fingerprint) = 1 AS uniform_fingerprint,
        max(sync_fingerprint) AS current_fingerprint
    FROM ops.required_stream
),
maintenance_eval AS (
    SELECT
        CASE
            WHEN NOT meta.present THEN 'UNAVAILABLE'
            WHEN NOT meta.uniform_fingerprint THEN 'UNAVAILABLE'
            WHEN latest.status IS NULL THEN 'UNAVAILABLE'
            WHEN latest.policy_fingerprint IS DISTINCT FROM meta.current_fingerprint
                THEN 'UNAVAILABLE'
            WHEN latest.status <> 'SUCCESS' THEN 'UNAVAILABLE'
            WHEN latest.finished_at > now() + interval '300 seconds'
                THEN 'UNAVAILABLE'
            WHEN now() - latest.finished_at > interval '3600 seconds'
                THEN 'UNAVAILABLE'
            ELSE 'LIVE'
        END AS status,
        CASE
            WHEN NOT meta.present THEN 'MISSING_POLICY'
            WHEN NOT meta.uniform_fingerprint THEN 'CONFIGURATION_DRIFT'
            WHEN latest.status IS NULL THEN 'MAINTENANCE_NEVER_RAN'
            WHEN latest.policy_fingerprint IS DISTINCT FROM meta.current_fingerprint
                THEN 'CONFIGURATION_DRIFT'
            WHEN latest.status <> 'SUCCESS' THEN 'MAINTENANCE_FAILED'
            WHEN latest.finished_at > now() + interval '300 seconds'
                THEN 'INVALID_TIMESTAMPS'
            WHEN now() - latest.finished_at > interval '3600 seconds'
                THEN 'MAINTENANCE_STALE'
            ELSE NULL
        END AS reason,
        latest.finished_at AS reference_at
    FROM policy_meta meta
    LEFT JOIN maintenance_latest latest ON true
)
SELECT
    stream_name,
    required,
    status,
    reason,
    reference_at,
    unresolved_dead_letters,
    last_reconciliation_status,
    last_reconciled_at,
    CASE
        WHEN reference_at IS NULL THEN NULL
        ELSE greatest(0, extract(epoch FROM (now() - reference_at)))::bigint
    END AS age_seconds,
    snapshot_refreshed_at
FROM evaluated
UNION ALL
SELECT
    '__maintenance__'::text,
    true,
    status,
    reason,
    reference_at,
    0::bigint,
    NULL::text,
    NULL::timestamptz,
    CASE
        WHEN reference_at IS NULL THEN NULL
        ELSE greatest(0, extract(epoch FROM (now() - reference_at)))::bigint
    END,
    NULL::timestamptz
FROM maintenance_eval;

GRANT SELECT ON ops.dashboard_freshness_mv TO opip_dashboard;
GRANT SELECT ON ops.dashboard_freshness_mv TO opip_shipper;
GRANT SELECT ON ops.dashboard_freshness_mv TO opip_learning;
GRANT SELECT ON ops.dashboard_freshness_v TO opip_dashboard;
GRANT SELECT ON ops.dashboard_freshness_v TO opip_shipper;
GRANT SELECT ON ops.dashboard_freshness_v TO opip_learning;

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
    freshness.reason AS freshness_reason,
    freshness.required
FROM ops.dashboard_freshness_v freshness
LEFT JOIN ops.ingest_checkpoint checkpoint
    ON checkpoint.stream_name = freshness.stream_name
WHERE freshness.stream_name <> '__maintenance__';

GRANT SELECT ON ops.platform_health_v TO opip_dashboard;
GRANT SELECT ON ops.platform_health_v TO opip_shipper;
GRANT SELECT ON ops.platform_health_v TO opip_learning;
