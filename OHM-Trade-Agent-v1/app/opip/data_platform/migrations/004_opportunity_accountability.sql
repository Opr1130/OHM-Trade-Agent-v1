CREATE OR REPLACE VIEW learning.opportunity_accountability_latest_v AS
SELECT DISTINCT ON (payload->>'accountability_id')
    payload->>'accountability_id' AS accountability_id,
    observed_at,
    payload
FROM raw.ingested_event
WHERE stream_name = 'opportunity_accountability'
  AND coalesce(payload->>'accountability_id', '') <> ''
ORDER BY
    payload->>'accountability_id',
    CASE
        WHEN coalesce(payload->>'revision', '') ~ '^[0-9]+$'
            THEN (payload->>'revision')::integer
        ELSE 0
    END DESC,
    observed_at DESC;

CREATE MATERIALIZED VIEW IF NOT EXISTS learning.opportunity_accountability_daily_mv AS
SELECT
    date_trunc('day', observed_at) AS day,
    count(*) AS directional_evaluations,
    count(*) FILTER (
        WHERE coalesce((payload->>'outcome_complete')::boolean, false)
    ) AS completed_forward_outcomes,
    count(*) FILTER (
        WHERE coalesce((payload->>'market_winner')::boolean, false)
    ) AS market_winner_candidates,
    count(*) FILTER (
        WHERE payload->>'opportunity_classification' = 'CAPTURED_WINNER'
    ) AS captured_winners,
    count(*) FILTER (
        WHERE coalesce((payload->>'executable_false_negative')::boolean, false)
    ) AS executable_false_negatives,
    count(*) FILTER (
        WHERE payload->>'opportunity_classification' = 'THRESHOLD_70_79_MISS_CANDIDATE'
    ) AS threshold_70_79_miss_candidates,
    count(*) FILTER (
        WHERE payload->>'opportunity_classification' = 'RANKING_OR_CAP_MISS_CANDIDATE'
    ) AS ranking_or_cap_miss_candidates,
    count(*) FILTER (
        WHERE payload->>'opportunity_classification' = 'OPERATIONAL_EXECUTABLE_MISS'
    ) AS operational_executable_misses,
    sum(
        CASE
            WHEN coalesce(payload->>'estimated_missed_move_pct', '') ~ '^-?[0-9]+(\.[0-9]+)?$'
                THEN (payload->>'estimated_missed_move_pct')::numeric
            ELSE 0
        END
    ) AS estimated_missed_move_pct_sum,
    count(*) FILTER (
        WHERE coalesce(payload#>>'{latency,decision_latency_ms}', '') ~ '^[0-9]+(\.[0-9]+)?
                THEN (payload#>>'{latency,decision_latency_ms}')::numeric
            ELSE NULL
        END
    ) AS mean_decision_latency_ms
FROM learning.opportunity_accountability_latest_v
GROUP BY date_trunc('day', observed_at)
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS opportunity_accountability_daily_mv_day_idx
    ON learning.opportunity_accountability_daily_mv (day);

GRANT USAGE ON SCHEMA learning TO opip_dashboard;
GRANT SELECT ON
    learning.opportunity_accountability_latest_v,
    learning.opportunity_accountability_daily_mv
TO opip_dashboard;

    ) AS decision_latency_samples,
    avg(
        CASE
            WHEN coalesce(payload#>>'{latency,decision_latency_ms}', '') ~ '^[0-9]+(\.[0-9]+)?
                THEN (payload#>>'{latency,decision_latency_ms}')::numeric
            ELSE NULL
        END
    ) AS mean_decision_latency_ms
FROM learning.opportunity_accountability_latest_v
GROUP BY date_trunc('day', observed_at)
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS opportunity_accountability_daily_mv_day_idx
    ON learning.opportunity_accountability_daily_mv (day);

GRANT USAGE ON SCHEMA learning TO opip_dashboard;
GRANT SELECT ON
    learning.opportunity_accountability_latest_v,
    learning.opportunity_accountability_daily_mv
TO opip_dashboard;

                THEN (payload#>>'{latency,decision_latency_ms}')::numeric
            ELSE NULL
        END
    ) AS mean_decision_latency_ms
FROM learning.opportunity_accountability_latest_v
GROUP BY date_trunc('day', observed_at)
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS opportunity_accountability_daily_mv_day_idx
    ON learning.opportunity_accountability_daily_mv (day);

GRANT USAGE ON SCHEMA learning TO opip_dashboard;
GRANT SELECT ON
    learning.opportunity_accountability_latest_v,
    learning.opportunity_accountability_daily_mv
TO opip_dashboard;
