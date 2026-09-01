CREATE MATERIALIZED VIEW IF NOT EXISTS signal.intelligence_daily_mv AS
SELECT
    date_trunc('day', observed_at) AS day,
    count(*) AS events,
    count(DISTINCT journey_id) FILTER (
        WHERE event_type IN ('EARLY_WATCH', 'EARLY_MOVER', 'BROAD_WATCH')
    ) AS early_watch_journeys,
    count(DISTINCT signal_id) FILTER (
        WHERE event_type = 'QUALIFIED_SIGNAL'
    ) AS qualified_signals,
    count(DISTINCT signal_id) FILTER (
        WHERE event_type = 'PAPER_OUTCOME'
    ) AS paper_outcomes
FROM signal.intelligence_event
GROUP BY date_trunc('day', observed_at)
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS intelligence_daily_mv_day_idx
    ON signal.intelligence_daily_mv (day);

CREATE MATERIALIZED VIEW IF NOT EXISTS market.attrition_daily_mv AS
SELECT
    date_trunc('day', observed_at) AS day,
    scanner_type,
    outcome,
    coalesce(reason_code, 'NONE') AS reason_code,
    count(*) AS evaluations,
    count(DISTINCT instrument_id) AS instruments
FROM market.screening
GROUP BY date_trunc('day', observed_at), scanner_type, outcome, coalesce(reason_code, 'NONE')
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS attrition_daily_mv_key_idx
    ON market.attrition_daily_mv (day, scanner_type, outcome, reason_code);

CREATE MATERIALIZED VIEW IF NOT EXISTS lifecycle.rejection_mix_daily_mv AS
SELECT
    date_trunc('day', occurred_at) AS day,
    coalesce(gate_name, 'NONE') AS gate_name,
    coalesce(reason_code, 'NONE') AS reason_code,
    count(*) AS transitions
FROM lifecycle.stage_transition
WHERE outcome NOT IN ('ADVANCED', 'PASS', 'QUALIFIED')
GROUP BY date_trunc('day', occurred_at), coalesce(gate_name, 'NONE'), coalesce(reason_code, 'NONE')
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS rejection_mix_daily_mv_key_idx
    ON lifecycle.rejection_mix_daily_mv (day, gate_name, reason_code);

CREATE OR REPLACE VIEW ops.platform_health_v AS
SELECT
    checkpoint.stream_name,
    checkpoint.source_file,
    checkpoint.byte_offset,
    checkpoint.rows_ingested,
    checkpoint.source_size,
    checkpoint.updated_at,
    extract(epoch FROM (now() - checkpoint.updated_at))::bigint AS lag_seconds,
    coalesce(dead.dead_letters, 0) AS unresolved_dead_letters,
    reconcile.status AS last_reconciliation_status,
    reconcile.checked_at AS last_reconciled_at
FROM ops.ingest_checkpoint checkpoint
LEFT JOIN LATERAL (
    SELECT count(*) AS dead_letters
    FROM ops.dead_letter item
    WHERE item.stream_name = checkpoint.stream_name
      AND item.resolved_at IS NULL
) dead ON true
LEFT JOIN LATERAL (
    SELECT item.status, item.checked_at
    FROM ops.reconciliation_run item
    WHERE item.stream_name = checkpoint.stream_name
    ORDER BY item.checked_at DESC
    LIMIT 1
) reconcile ON true;
