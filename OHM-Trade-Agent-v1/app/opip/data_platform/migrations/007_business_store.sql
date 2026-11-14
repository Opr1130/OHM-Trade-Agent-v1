-- Phase 1: authoritative business-record store for NEW production records.
--
-- Additive only: no existing table, view, role grant or checksum is touched. The store is inert
-- until an approved cutover flips ops.cutover_authority from FILES to POSTGRES; while the
-- authority is FILES the database itself refuses every business write, so a half-enabled
-- deployment can never create a second authoritative store (split-brain).

-- ---------------------------------------------------------------------------------------------
-- Roles. The application writer cannot change the authority; only the cutover admin can.
-- ---------------------------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opip_app_writer') THEN
        CREATE ROLE opip_app_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opip_cutover_admin') THEN
        CREATE ROLE opip_cutover_admin NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;

-- ---------------------------------------------------------------------------------------------
-- Cutover authority: exactly one row, monotonic epoch, full audit trail.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops.cutover_authority (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    authority text NOT NULL DEFAULT 'FILES' CHECK (authority IN ('FILES', 'POSTGRES')),
    epoch bigint NOT NULL DEFAULT 1 CHECK (epoch > 0),
    changed_at timestamptz NOT NULL DEFAULT now(),
    changed_by text NOT NULL DEFAULT 'migration-007',
    reason text NOT NULL DEFAULT 'initial state: files remain authoritative'
);

INSERT INTO ops.cutover_authority (singleton) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ops.cutover_transition (
    transition_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_authority text NOT NULL,
    to_authority text NOT NULL,
    from_epoch bigint NOT NULL,
    to_epoch bigint NOT NULL CHECK (to_epoch > from_epoch),
    changed_at timestamptz NOT NULL DEFAULT now(),
    changed_by text NOT NULL,
    reason text NOT NULL
);

CREATE OR REPLACE FUNCTION ops.cutover_authority_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ops.cutover_authority row cannot be deleted';
    END IF;
    IF NEW.epoch <= OLD.epoch THEN
        RAISE EXCEPTION 'cutover epoch must increase (% -> %)', OLD.epoch, NEW.epoch;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cutover_authority_guard ON ops.cutover_authority;
CREATE TRIGGER cutover_authority_guard
    BEFORE UPDATE OR DELETE ON ops.cutover_authority
    FOR EACH ROW EXECUTE FUNCTION ops.cutover_authority_guard();

-- The only supported way to change the authority: compare-and-set on the epoch, audited.
CREATE OR REPLACE FUNCTION ops.set_cutover_authority(
    p_to text, p_expected_epoch bigint, p_changed_by text, p_reason text
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
    current_row ops.cutover_authority%ROWTYPE;
BEGIN
    IF p_to NOT IN ('FILES', 'POSTGRES') THEN
        RAISE EXCEPTION 'unknown authority %', p_to;
    END IF;
    IF coalesce(btrim(p_changed_by), '') = '' OR coalesce(btrim(p_reason), '') = '' THEN
        RAISE EXCEPTION 'changed_by and reason are required';
    END IF;
    SELECT * INTO current_row FROM ops.cutover_authority WHERE singleton FOR UPDATE;
    IF current_row.epoch <> p_expected_epoch THEN
        RAISE EXCEPTION 'cutover epoch conflict: expected %, found %', p_expected_epoch, current_row.epoch;
    END IF;
    IF current_row.authority = p_to THEN
        RAISE EXCEPTION 'authority is already %', p_to;
    END IF;
    UPDATE ops.cutover_authority
       SET authority = p_to, epoch = current_row.epoch + 1, changed_at = now(),
           changed_by = p_changed_by, reason = p_reason
     WHERE singleton;
    INSERT INTO ops.cutover_transition (from_authority, to_authority, from_epoch, to_epoch, changed_by, reason)
    VALUES (current_row.authority, p_to, current_row.epoch, current_row.epoch + 1, p_changed_by, p_reason);
    RETURN current_row.epoch + 1;
END $$;

-- ---------------------------------------------------------------------------------------------
-- Ordered, idempotent business-event ledger. One row per business fact, gap-free per aggregate.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops.aggregate_head (
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    last_seq bigint NOT NULL CHECK (last_seq >= 0),
    last_event_key text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (aggregate_type, aggregate_id)
);

CREATE TABLE IF NOT EXISTS ops.business_event (
    idempotency_key text PRIMARY KEY,
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    aggregate_seq bigint NOT NULL CHECK (aggregate_seq > 0),
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    authority_epoch bigint NOT NULL CHECK (authority_epoch > 0),
    schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    UNIQUE (aggregate_type, aggregate_id, aggregate_seq)
);

CREATE INDEX IF NOT EXISTS business_event_occurred_idx ON ops.business_event (occurred_at DESC);
CREATE INDEX IF NOT EXISTS business_event_type_idx ON ops.business_event (event_type, occurred_at DESC);

-- The database itself refuses a business write unless PostgreSQL is the authority and the
-- writer holds the current epoch. Rolling back to FILES (or any epoch bump) therefore stops
-- every PostgreSQL writer immediately, with no application cooperation required.
CREATE OR REPLACE FUNCTION ops.business_write_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
    held ops.cutover_authority%ROWTYPE;
BEGIN
    SELECT * INTO held FROM ops.cutover_authority WHERE singleton FOR SHARE;
    IF held.authority IS DISTINCT FROM 'POSTGRES' THEN
        RAISE EXCEPTION 'business write refused: authority is %, not POSTGRES', held.authority
            USING ERRCODE = 'P0002';
    END IF;
    IF NEW.authority_epoch IS DISTINCT FROM held.epoch THEN
        RAISE EXCEPTION 'business write refused: writer epoch % is not current epoch %',
            NEW.authority_epoch, held.epoch USING ERRCODE = 'P0003';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS business_write_guard ON ops.business_event;
CREATE TRIGGER business_write_guard
    BEFORE INSERT ON ops.business_event
    FOR EACH ROW EXECUTE FUNCTION ops.business_write_guard();

-- Append one event atomically: authority and epoch are checked by the trigger above, a replay of
-- the same key and payload is a no-op, the same key with a different payload is an error, and the
-- per-aggregate sequence is assigned under a row lock so concurrent writers stay gap-free.
CREATE OR REPLACE FUNCTION ops.append_business_event(
    p_idempotency_key text,
    p_aggregate_type text,
    p_aggregate_id text,
    p_event_type text,
    p_occurred_at timestamptz,
    p_payload jsonb,
    p_payload_sha256 text,
    p_authority_epoch bigint,
    p_expected_prev_seq bigint DEFAULT NULL
) RETURNS TABLE (status text, aggregate_seq bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
    existing ops.business_event%ROWTYPE;
    head_seq bigint;
    next_seq bigint;
BEGIN
    SELECT * INTO existing FROM ops.business_event WHERE idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF existing.payload_sha256 <> p_payload_sha256
           OR existing.aggregate_type <> p_aggregate_type
           OR existing.aggregate_id <> p_aggregate_id
           OR existing.event_type <> p_event_type
           OR existing.occurred_at <> p_occurred_at THEN
            RAISE EXCEPTION 'idempotency conflict for key %', p_idempotency_key USING ERRCODE = 'P0004';
        END IF;
        RETURN QUERY SELECT 'DUPLICATE'::text, existing.aggregate_seq;
        RETURN;
    END IF;

    INSERT INTO ops.aggregate_head (aggregate_type, aggregate_id, last_seq)
    VALUES (p_aggregate_type, p_aggregate_id, 0)
    ON CONFLICT (aggregate_type, aggregate_id) DO NOTHING;
    SELECT h.last_seq INTO head_seq FROM ops.aggregate_head h
     WHERE h.aggregate_type = p_aggregate_type AND h.aggregate_id = p_aggregate_id FOR UPDATE;

    -- Re-check under the aggregate lock: a concurrent writer may have inserted this key.
    SELECT * INTO existing FROM ops.business_event WHERE idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF existing.payload_sha256 <> p_payload_sha256
           OR existing.aggregate_type <> p_aggregate_type
           OR existing.aggregate_id <> p_aggregate_id
           OR existing.event_type <> p_event_type
           OR existing.occurred_at <> p_occurred_at THEN
            RAISE EXCEPTION 'idempotency conflict for key %', p_idempotency_key USING ERRCODE = 'P0004';
        END IF;
        RETURN QUERY SELECT 'DUPLICATE'::text, existing.aggregate_seq;
        RETURN;
    END IF;

    IF p_expected_prev_seq IS NOT NULL AND p_expected_prev_seq <> head_seq THEN
        RAISE EXCEPTION 'sequence conflict for %/%: expected previous %, found %',
            p_aggregate_type, p_aggregate_id, p_expected_prev_seq, head_seq USING ERRCODE = 'P0005';
    END IF;

    next_seq := head_seq + 1;
    INSERT INTO ops.business_event (
        idempotency_key, aggregate_type, aggregate_id, aggregate_seq, event_type, occurred_at,
        payload, payload_sha256, authority_epoch
    ) VALUES (
        p_idempotency_key, p_aggregate_type, p_aggregate_id, next_seq, p_event_type, p_occurred_at,
        p_payload, p_payload_sha256, p_authority_epoch
    );
    UPDATE ops.aggregate_head h
       SET last_seq = next_seq, last_event_key = p_idempotency_key, updated_at = now()
     WHERE h.aggregate_type = p_aggregate_type AND h.aggregate_id = p_aggregate_id;
    RETURN QUERY SELECT 'INSERTED'::text, next_seq;
END $$;

-- ---------------------------------------------------------------------------------------------
-- Typed projections. Each row is written in the same transaction as its ledger event.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal.delivery_event (
    idempotency_key text PRIMARY KEY REFERENCES ops.business_event (idempotency_key),
    alert_id text NOT NULL,
    alert_family text NOT NULL,
    event_type text NOT NULL,
    status text NOT NULL,
    delivered boolean NOT NULL,
    attempted_at timestamptz NOT NULL,
    signal_id text,
    journey_id text,
    trade_id text,
    symbol text,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    suppression_reason text,
    failure_reason text,
    suppressed_since_last_row integer CHECK (suppressed_since_last_row IS NULL OR suppressed_since_last_row >= 0),
    evidence jsonb
);

CREATE INDEX IF NOT EXISTS delivery_event_time_idx ON signal.delivery_event (attempted_at DESC);
CREATE INDEX IF NOT EXISTS delivery_event_family_status_idx
    ON signal.delivery_event (alert_family, status, attempted_at DESC);
CREATE INDEX IF NOT EXISTS delivery_event_signal_idx ON signal.delivery_event (signal_id) WHERE signal_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS signal.ai_call (
    idempotency_key text PRIMARY KEY REFERENCES ops.business_event (idempotency_key),
    observed_at timestamptz NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    purpose text,
    status text NOT NULL CHECK (status IN ('SUCCESS', 'FAILURE')),
    error_type text,
    input_tokens bigint CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens IS NULL OR output_tokens >= 0),
    total_tokens bigint NOT NULL CHECK (total_tokens >= 0),
    signal_id text,
    journey_id text,
    CONSTRAINT ai_call_failure_has_error CHECK (status = 'SUCCESS' OR error_type IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ai_call_time_idx ON signal.ai_call (observed_at DESC);
CREATE INDEX IF NOT EXISTS ai_call_model_idx ON signal.ai_call (provider, model, observed_at DESC);

CREATE TABLE IF NOT EXISTS learning.signal_outcome (
    idempotency_key text PRIMARY KEY REFERENCES ops.business_event (idempotency_key),
    signal_id text NOT NULL,
    horizon_seconds integer NOT NULL CHECK (horizon_seconds > 0),
    evaluated_at timestamptz NOT NULL,
    outcome_class text NOT NULL CHECK (
        outcome_class IN ('REALIZED', 'PAPER', 'HYPOTHETICAL', 'INSUFFICIENT_EVIDENCE')
    ),
    verdict text NOT NULL CHECK (
        verdict IN ('TARGET_FIRST', 'STOP_FIRST', 'NEITHER', 'AMBIGUOUS_ORDER', 'INSUFFICIENT_EVIDENCE')
    ),
    gross_return_pct numeric,
    net_return_pct numeric,
    assumed_fee_pct numeric CHECK (assumed_fee_pct IS NULL OR assumed_fee_pct >= 0),
    assumed_slippage_pct numeric CHECK (assumed_slippage_pct IS NULL OR assumed_slippage_pct >= 0),
    evidence jsonb NOT NULL,
    -- Missing target/stop ordering can never become a verdict or a return.
    CONSTRAINT outcome_insufficient_is_consistent CHECK (
        (outcome_class = 'INSUFFICIENT_EVIDENCE') = (verdict = 'INSUFFICIENT_EVIDENCE')
    ),
    CONSTRAINT outcome_insufficient_has_no_return CHECK (
        verdict <> 'INSUFFICIENT_EVIDENCE' OR (gross_return_pct IS NULL AND net_return_pct IS NULL)
    ),
    UNIQUE (signal_id, horizon_seconds, outcome_class)
);

CREATE INDEX IF NOT EXISTS signal_outcome_signal_idx ON learning.signal_outcome (signal_id);

-- A typed row may only be written while PostgreSQL is the authority, for a ledger event recorded
-- under the current epoch, of the matching aggregate and event type, and in the SAME transaction
-- that appended that event (the ledger row's xmin is this transaction). Without these checks a role
-- holding INSERT on a typed table could write after a rollback to FILES, attach a typed row to an
-- unrelated or older ledger event, or commit a projection separately from its ledger append.
CREATE OR REPLACE FUNCTION ops.projection_write_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ops, pg_temp AS $$
DECLARE
    held ops.cutover_authority%ROWTYPE;
    ledger_epoch bigint;
    ledger_aggregate_type text;
    ledger_aggregate_id text;
    ledger_event_type text;
    ledger_same_txn boolean;
    want_aggregate_type text;
    want_event_type text;
    want_aggregate_id text;
BEGIN
    SELECT * INTO held FROM ops.cutover_authority WHERE singleton FOR SHARE;
    IF held.authority IS DISTINCT FROM 'POSTGRES' THEN
        RAISE EXCEPTION 'projection write refused: authority is %, not POSTGRES', held.authority
            USING ERRCODE = 'P0002';
    END IF;
    SELECT e.authority_epoch, e.aggregate_type, e.aggregate_id, e.event_type,
           (e.xmin = pg_current_xact_id()::xid)
      INTO ledger_epoch, ledger_aggregate_type, ledger_aggregate_id, ledger_event_type, ledger_same_txn
      FROM ops.business_event e
     WHERE e.idempotency_key = NEW.idempotency_key;
    IF ledger_epoch IS DISTINCT FROM held.epoch THEN
        RAISE EXCEPTION 'projection write refused: ledger event epoch % is not current epoch %',
            ledger_epoch, held.epoch USING ERRCODE = 'P0003';
    END IF;

    IF TG_TABLE_NAME = 'delivery_event' THEN
        want_aggregate_type := 'alert'; want_event_type := 'TELEGRAM_DELIVERY_EVENT';
        want_aggregate_id := NEW.alert_id;
    ELSIF TG_TABLE_NAME = 'ai_call' THEN
        want_aggregate_type := 'ai_call'; want_event_type := 'AI_CALL_RECORDED';
    ELSIF TG_TABLE_NAME = 'signal_outcome' THEN
        want_aggregate_type := 'signal_outcome'; want_event_type := 'SIGNAL_OUTCOME_RECORDED';
        want_aggregate_id := NEW.signal_id;
    ELSE
        RAISE EXCEPTION 'projection write refused: no ledger identity defined for %', TG_TABLE_NAME
            USING ERRCODE = '23514';
    END IF;
    IF ledger_aggregate_type <> want_aggregate_type OR ledger_event_type <> want_event_type
       OR (want_aggregate_id IS NOT NULL AND ledger_aggregate_id <> want_aggregate_id) THEN
        RAISE EXCEPTION 'projection write refused: ledger event %/% (%) does not match %',
            ledger_aggregate_type, ledger_aggregate_id, ledger_event_type, TG_TABLE_NAME
            USING ERRCODE = '23514';
    END IF;
    IF ledger_same_txn IS NOT TRUE THEN
        RAISE EXCEPTION 'projection write refused: the ledger event was not appended in this transaction'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS projection_write_guard ON signal.delivery_event;
CREATE TRIGGER projection_write_guard BEFORE INSERT ON signal.delivery_event
    FOR EACH ROW EXECUTE FUNCTION ops.projection_write_guard();
DROP TRIGGER IF EXISTS projection_write_guard ON signal.ai_call;
CREATE TRIGGER projection_write_guard BEFORE INSERT ON signal.ai_call
    FOR EACH ROW EXECUTE FUNCTION ops.projection_write_guard();
DROP TRIGGER IF EXISTS projection_write_guard ON learning.signal_outcome;
CREATE TRIGGER projection_write_guard BEFORE INSERT ON learning.signal_outcome
    FOR EACH ROW EXECUTE FUNCTION ops.projection_write_guard();

-- ---------------------------------------------------------------------------------------------
-- Grants: the application writer appends and reads; it never changes authority or history.
-- ---------------------------------------------------------------------------------------------
GRANT USAGE ON SCHEMA ops, signal, learning TO opip_app_writer;
GRANT SELECT ON ops.cutover_authority TO opip_app_writer;
-- The ledger and its per-aggregate head are written only through ops.append_business_event
-- (SECURITY DEFINER), so no role can insert an out-of-order sequence or bypass the head.
GRANT SELECT ON ops.aggregate_head TO opip_app_writer;
GRANT SELECT ON ops.business_event TO opip_app_writer;
REVOKE EXECUTE ON FUNCTION ops.append_business_event(
    text, text, text, text, timestamptz, jsonb, text, bigint, bigint
) FROM PUBLIC;
GRANT SELECT, INSERT ON signal.delivery_event, signal.ai_call, learning.signal_outcome TO opip_app_writer;
GRANT EXECUTE ON FUNCTION ops.append_business_event(
    text, text, text, text, timestamptz, jsonb, text, bigint, bigint
) TO opip_app_writer;
REVOKE EXECUTE ON FUNCTION ops.set_cutover_authority(text, bigint, text, text) FROM PUBLIC;
GRANT USAGE ON SCHEMA ops TO opip_cutover_admin;
GRANT SELECT ON ops.cutover_authority, ops.cutover_transition, ops.business_event TO opip_cutover_admin;
GRANT EXECUTE ON FUNCTION ops.set_cutover_authority(text, bigint, text, text) TO opip_cutover_admin;
GRANT SELECT ON ops.cutover_authority, ops.cutover_transition TO opip_shipper, opip_learning;
