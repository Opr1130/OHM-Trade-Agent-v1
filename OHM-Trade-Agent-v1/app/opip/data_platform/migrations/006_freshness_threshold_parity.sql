-- Follow-up to 005_dashboard_freshness.sql. Migration 005 may already be
-- recorded as applied in real databases, so its file is left byte-for-byte
-- unchanged (mutating it would break checksum validation for those
-- environments); this migration instead replaces the function in place.
--
-- Fix: ops.freshness_reason() hard-coded an 86400-second bound for
-- optional/non-required streams instead of honoring the per-stream
-- `threshold_seconds` that is already part of the synchronized policy (and
-- its fingerprint). app.opip.data_platform.freshness.classify_stream() had
-- the identical bug on the Python side (fixed in the same commit as this
-- migration) -- both now use threshold_seconds when it is non-null and
-- fall back to 86400 seconds only when it is null, keeping Python and SQL
-- classification identical for every optional stream.
--
-- CREATE OR REPLACE FUNCTION is idempotent: a fresh database applying
-- 005 then 006 in sequence, and an already-migrated database applying only
-- 006, both end up with the exact same function body.

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
                    <= make_interval(secs => coalesce(p_threshold_seconds, 86400))
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
            > interval '3600 seconds' THEN 'PER_STREAM_THRESHOLD_EXCEEDED'
        WHEN p_now - coalesce(p_typed_watermark_at, p_last_ingested_at)
            > interval '300 seconds' THEN 'STALE_DATA'
        WHEN p_now - coalesce(p_typed_watermark_at, p_last_ingested_at)
            > interval '120 seconds' THEN 'DATA_DELAYED'
        ELSE NULL
    END
$$;
