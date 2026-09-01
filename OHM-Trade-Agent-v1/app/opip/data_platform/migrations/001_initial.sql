CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS lifecycle;
CREATE SCHEMA IF NOT EXISTS signal;
CREATE SCHEMA IF NOT EXISTS paper;
CREATE SCHEMA IF NOT EXISTS learning;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS ops.schema_version (
    version integer PRIMARY KEY,
    name text NOT NULL,
    sha256 text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market.instrument (
    instrument_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_asset text NOT NULL UNIQUE,
    display_name text,
    first_seen_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS market.instrument_alias (
    instrument_id bigint NOT NULL REFERENCES market.instrument(instrument_id),
    venue text NOT NULL,
    venue_symbol text NOT NULL,
    quote_currency text,
    mapping_status text NOT NULL CHECK (
        mapping_status IN ('UNIQUE', 'AMBIGUOUS', 'UNRESOLVED')
    ),
    resolved_at timestamptz NOT NULL,
    PRIMARY KEY (venue, venue_symbol)
);

CREATE TABLE IF NOT EXISTS market.screening (
    scan_id text NOT NULL,
    scanner_type text NOT NULL,
    instrument_id bigint NOT NULL REFERENCES market.instrument(instrument_id),
    observed_at timestamptz NOT NULL,
    last_price numeric(24,10),
    technical_score numeric,
    long_score numeric,
    short_score numeric,
    outcome text NOT NULL CHECK (
        outcome IN (
            'ADVANCED', 'BELOW_THRESHOLD', 'BELOW_COARSE_THRESHOLD',
            'COARSE_RANK_LIMIT', 'DATA_UNAVAILABLE', 'EXCLUDED_MARKET'
        )
    ),
    reason_code text,
    strategy_version text NOT NULL,
    metadata jsonb,
    PRIMARY KEY (observed_at, scan_id, scanner_type, instrument_id),
    CONSTRAINT screening_reason_required CHECK (
        outcome = 'ADVANCED' OR reason_code IS NOT NULL
    )
) PARTITION BY RANGE (observed_at);

CREATE TABLE IF NOT EXISTS market.observation (
    observation_key text NOT NULL,
    instrument_id bigint NOT NULL REFERENCES market.instrument(instrument_id),
    observed_at timestamptz NOT NULL,
    capture_reason text,
    last_price numeric(24,10),
    volume_24h numeric,
    notional_24h_usd numeric,
    high_24h numeric(24,10),
    low_24h numeric(24,10),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (observed_at, observation_key)
) PARTITION BY RANGE (observed_at);

CREATE TABLE IF NOT EXISTS lifecycle.episode (
    episode_id text PRIMARY KEY,
    cohort_id text,
    instrument_id bigint REFERENCES market.instrument(instrument_id),
    decision_at timestamptz NOT NULL,
    current_stage text NOT NULL,
    terminal_reason text,
    strategy_version text NOT NULL,
    config_version text NOT NULL,
    model_version text
);

CREATE TABLE IF NOT EXISTS lifecycle.candidate (
    candidate_id text PRIMARY KEY,
    episode_id text REFERENCES lifecycle.episode(episode_id),
    scan_id text NOT NULL,
    instrument_id bigint REFERENCES market.instrument(instrument_id),
    direction text NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    decision_at timestamptz NOT NULL,
    decision text NOT NULL,
    terminal_reason_code text,
    terminal_reason text,
    strategy_version text NOT NULL,
    config_version text NOT NULL,
    model_version text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT candidate_reason_required CHECK (
        decision IN ('QUALIFIED', 'ADVANCED') OR terminal_reason_code IS NOT NULL
    )
);

CREATE TABLE IF NOT EXISTS lifecycle.stage_transition (
    transition_key text NOT NULL,
    episode_id text NOT NULL,
    candidate_id text,
    occurred_at timestamptz NOT NULL,
    from_stage text,
    to_stage text NOT NULL,
    outcome text NOT NULL,
    reason_code text,
    gate_name text,
    measured_value numeric,
    threshold_value numeric,
    evidence jsonb,
    PRIMARY KEY (occurred_at, transition_key),
    CONSTRAINT transition_reason_required CHECK (
        outcome IN ('ADVANCED', 'PASS', 'QUALIFIED') OR reason_code IS NOT NULL
    )
) PARTITION BY RANGE (occurred_at);

CREATE TABLE IF NOT EXISTS signal.intelligence_event (
    event_key text NOT NULL,
    observed_at timestamptz NOT NULL,
    event_type text NOT NULL,
    journey_id text,
    signal_id text,
    instrument_id bigint REFERENCES market.instrument(instrument_id),
    delivered boolean,
    admitted boolean,
    reason_code text,
    strategy_version text,
    config_version text,
    model_version text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (observed_at, event_key)
) PARTITION BY RANGE (observed_at);

CREATE TABLE IF NOT EXISTS paper.trade (
    paper_trade_id text PRIMARY KEY,
    signal_id text,
    instrument_id bigint REFERENCES market.instrument(instrument_id),
    state text NOT NULL,
    opened_at timestamptz,
    closed_at timestamptz,
    realised_pnl numeric,
    reconciliation_status text NOT NULL DEFAULT 'UNVERIFIED',
    strategy_version text,
    config_version text,
    model_version text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS paper.trade_event (
    event_id text PRIMARY KEY,
    paper_trade_id text NOT NULL REFERENCES paper.trade(paper_trade_id),
    revision integer NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS learning.feature_snapshot (
    snapshot_id text PRIMARY KEY,
    episode_id text,
    observed_at timestamptz NOT NULL,
    strategy_version text NOT NULL,
    config_version text NOT NULL,
    model_version text,
    features jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS learning.outcome_label (
    label_id text PRIMARY KEY,
    episode_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    authority text NOT NULL,
    outcome jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.ingested_event (
    stream_name text NOT NULL,
    source_event_id text NOT NULL,
    source_file text NOT NULL,
    source_generation bigint NOT NULL CHECK (source_generation > 0),
    source_byte_offset bigint NOT NULL,
    source_row_sha256 text NOT NULL,
    observed_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (observed_at, stream_name, source_event_id, source_generation)
) PARTITION BY RANGE (observed_at);

CREATE TABLE IF NOT EXISTS ops.ingest_checkpoint (
    stream_name text PRIMARY KEY,
    source_file text NOT NULL,
    source_generation bigint NOT NULL CHECK (source_generation > 0),
    byte_offset bigint NOT NULL CHECK (byte_offset >= 0),
    last_row_sha256 text NOT NULL,
    rows_ingested bigint NOT NULL CHECK (rows_ingested >= 0),
    source_size bigint NOT NULL CHECK (source_size >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.dead_letter (
    dead_letter_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_name text NOT NULL,
    source_file text NOT NULL,
    source_generation bigint NOT NULL CHECK (source_generation > 0),
    source_byte_offset bigint NOT NULL,
    source_row_sha256 text NOT NULL,
    raw_text text NOT NULL,
    error_type text NOT NULL,
    error_message text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    UNIQUE (stream_name, source_generation, source_row_sha256, source_byte_offset)
);

CREATE TABLE IF NOT EXISTS ops.reconciliation_run (
    reconciliation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_name text NOT NULL,
    source_file text NOT NULL,
    source_generation bigint NOT NULL CHECK (source_generation > 0),
    source_byte_offset bigint NOT NULL,
    source_rows bigint NOT NULL,
    database_rows bigint NOT NULL,
    difference bigint NOT NULL,
    source_sha256 text NOT NULL,
    status text NOT NULL CHECK (status IN ('CLEAN', 'MISMATCH', 'ERROR')),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS raw_event_observed_at_idx
    ON raw.ingested_event (observed_at DESC);
CREATE INDEX IF NOT EXISTS raw_event_source_offset_idx
    ON raw.ingested_event (stream_name, source_byte_offset);
CREATE INDEX IF NOT EXISTS screening_instrument_time_idx
    ON market.screening (instrument_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS observation_instrument_time_idx
    ON market.observation (instrument_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS screening_attrition_idx
    ON market.screening (observed_at DESC)
    WHERE outcome <> 'ADVANCED';
CREATE INDEX IF NOT EXISTS transition_episode_time_idx
    ON lifecycle.stage_transition (episode_id, occurred_at);
CREATE INDEX IF NOT EXISTS transition_reason_time_idx
    ON lifecycle.stage_transition (reason_code, occurred_at DESC);
CREATE INDEX IF NOT EXISTS episode_instrument_time_idx
    ON lifecycle.episode (instrument_id, decision_at DESC);

DO $$
DECLARE
    month_start date := date_trunc('month', current_date)::date;
    next_start date;
    table_name text;
    schema_name text;
    time_column text;
    partition_name text;
    i integer;
BEGIN
    FOR i IN -3..2 LOOP
        next_start := (month_start + make_interval(months => i + 1))::date;
        FOR schema_name, table_name, time_column IN
            VALUES
                ('market', 'screening', 'observed_at'),
                ('market', 'observation', 'observed_at'),
                ('lifecycle', 'stage_transition', 'occurred_at'),
                ('signal', 'intelligence_event', 'observed_at'),
                ('raw', 'ingested_event', 'observed_at')
        LOOP
            partition_name := format('%s_%s', table_name, to_char(month_start + make_interval(months => i), 'YYYYMM'));
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                schema_name, partition_name, schema_name, table_name,
                month_start + make_interval(months => i), next_start
            );
        END LOOP;
    END LOOP;
END $$;
