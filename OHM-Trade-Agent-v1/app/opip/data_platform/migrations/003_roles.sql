DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opip_shipper') THEN
        CREATE ROLE opip_shipper NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opip_learning') THEN
        CREATE ROLE opip_learning NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opip_dashboard') THEN
        CREATE ROLE opip_dashboard NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA market, lifecycle, signal, paper, learning, ops, raw
    TO opip_shipper;
GRANT SELECT ON ALL TABLES IN SCHEMA market, lifecycle, signal, paper, learning, ops, raw
    TO opip_shipper;
GRANT INSERT, UPDATE ON
    market.instrument,
    market.instrument_alias,
    market.screening,
    market.observation,
    lifecycle.episode,
    lifecycle.candidate,
    lifecycle.stage_transition,
    signal.intelligence_event,
    paper.trade,
    paper.trade_event,
    raw.ingested_event,
    ops.ingest_checkpoint,
    ops.dead_letter,
    ops.reconciliation_run
    TO opip_shipper;
REVOKE INSERT, UPDATE, DELETE ON ops.schema_version FROM opip_shipper;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA market, lifecycle, signal, paper, learning, ops, raw
    TO opip_shipper;

GRANT USAGE ON SCHEMA market, lifecycle, signal, paper, learning, ops
    TO opip_learning;
GRANT SELECT ON ALL TABLES IN SCHEMA market, lifecycle, signal, paper, learning, ops
    TO opip_learning;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA learning TO opip_learning;

GRANT USAGE ON SCHEMA market, lifecycle, signal, ops TO opip_dashboard;
GRANT SELECT ON signal.intelligence_daily_mv,
    market.attrition_daily_mv,
    lifecycle.rejection_mix_daily_mv,
    ops.platform_health_v TO opip_dashboard;

ALTER DEFAULT PRIVILEGES IN SCHEMA market, lifecycle, signal, paper, learning, ops, raw
    GRANT SELECT ON TABLES TO opip_shipper;
ALTER DEFAULT PRIVILEGES IN SCHEMA market, lifecycle, signal, paper, learning, ops, raw
    GRANT USAGE, SELECT ON SEQUENCES TO opip_shipper;
