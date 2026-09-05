# O'Pip Analytics Data Platform

This is a derived, non-authoritative PostgreSQL 17 analytics plane. Production
files remain the durable write-ahead log. The scanner never imports, connects
to, waits for, or fails because of PostgreSQL.

## Required host boundary

- deploy only on the separate learning/analytics droplet;
- resize that droplet to at least 2 GiB before bootstrap;
- never deploy PostgreSQL on the 2 GiB trading droplet;
- keep the host free of Kraken credentials, Telegram authority, and paper
  control write authority;
- bind port 5432 only to the private VPC address and firewall it to the
  production private IP;
- use independent SCRAM passwords for admin, shipper, learning, and dashboard;
- enable weekly off-host droplet backup before calling the platform complete.

## Deployment

1. Copy `env.example` to `/etc/opip-data-platform.env`, replace every secret,
   use the analytics container hostname `opip-postgres` in admin/shipper DSNs,
   and set mode `0600`.
2. Set `OPIP_POSTGRES_BIND_ADDRESS` to the analytics droplet's private VPC IP.
3. Run the owner-gated `empty` stage twice with a successful restore drill
   between the first and latest successful empty deployments.
4. Verify the off-host infrastructure copy, then run `offhost-verified` to
   record that independent attestation. Run `rollback-verified` to record the
   two-empty-plus-restore evidence.
5. Advance one explicit stage at a time: `backfill`, `shipper`, and
   `reads-ready`.
6. Only after `reads-ready` succeeds, configure the production dashboard with
   the read-only `opip_dashboard` credential, set
   `OPIP_DATA_PLATFORM_READS_ENABLED=true`, and keep the 1.5 second statement
   timeout. Live tiles remain file-backed.

The stages are deliberately non-collapsible. `empty` installs PostgreSQL and
the additive schema; `offhost-verified` records an owner attestation only
after the independent infrastructure copy is verified; `rollback-verified`
requires two successful empty deployments with a restore drill between them;
`backfill` requires all of that durable evidence; `shipper` requires a clean
backfill; and `reads-ready` requires a seven-day shipper soak plus a clean
canonical freshness result (`ops.dashboard_freshness_v` must be LIVE for every
required stream and for maintenance). A failed step leaves the production
scanner and its file WAL unchanged.

## Canonical freshness contract

`ops.dashboard_freshness_v` is the single freshness result consumed by
Grafana, the production API/dashboard status, stale-data selection, and
`health --require-ready`. Its classification logic mirrors
`app/opip/data_platform/freshness.py`; that module is the source of truth and
any policy change must land there first, then in a new migration, with the
parity tests proving PostgreSQL and Python agree.

- Streams that require typed projections are declared in `StreamSpec`
  (`requires_typed_projection`). A missing required typed watermark is
  `UNAVAILABLE`; ingestion time is never substituted for typed evidence.
- `health --require-ready` exits nonzero unless the canonical result is LIVE.
  It fails closed for missing streams, reconciliation ERROR/UNKNOWN, stale
  typed data, maintenance failure, configuration drift, and invalid
  timestamps, and it never depends on the dashboard historical-reads flag.
- Required-stream policy (`ops.required_stream`) is synchronized only by the
  administrative `migrations sync-required-streams` command. The shipper role
  holds no write grant on that table, and no code path falls back to shipper
  credentials for policy mutation.
- Malformed, naive, or materially future timestamps are treated as invalid
  and fail closed identically in PostgreSQL and Python.
- Each canonical row exposes a stable machine-readable `reason`; the aggregate
  health payload carries `freshness.reason` and `freshness.problems`.

Nightly custom-format dumps are checksummed locally, but the off-host copy is
an independent infrastructure responsibility. The `offhost-verified` stage
does not create that copy; it records the owner's attestation after the copy has
been verified. After the first dump and after every material schema change, run
`opip-postgres-restore-drill`; it restores
into a temporary database, validates `ops.schema_version`, records evidence,
and drops only that temporary database.
