# O'Pip Analytics Data Platform

This is a derived, non-authoritative PostgreSQL 17 analytics plane. Production
files remain the durable write-ahead log. The scanner never imports, connects
to, waits for, or fails because of PostgreSQL.

Grafana is the primary intelligence visualization plane on this host and must
use the read-only `opip_dashboard` role only.

## Required host boundary

- deploy only on the separate learning/analytics droplet;
- resize that droplet to at least 2 GiB before bootstrap;
- never deploy PostgreSQL on the 2 GiB trading droplet;
- keep the host free of Kraken credentials, Telegram authority, and paper
  control write authority;
- bind port 5432 only to the private VPC address and firewall it to the
  production private IP;
- use independent SCRAM passwords for admin, shipper, learning, and dashboard;
- require TLS for the Grafana-to-PostgreSQL connection and verify the server
  certificate hostname against `opip-postgres`;
- keep Grafana bound to loopback/private network and front it with a TLS
  reverse proxy (do not expose an unauthenticated public port 3000);
- enable weekly off-host droplet backup before calling the platform complete.

## PostgreSQL TLS material

PostgreSQL and Grafana use host-managed TLS files under
`/etc/opip-data-platform/tls`:

- `postgres-ca.crt` — trusted root CA certificate, readable by Grafana;
- `postgres-server.crt` — PostgreSQL server certificate signed by that CA;
- `postgres-server.key` — PostgreSQL private key.

The server certificate must contain `subjectAltName = DNS:opip-postgres`, because
Grafana connects to the Compose service name and uses `verify-full`. Never commit
TLS private keys or production certificates. Keep the private key inaccessible
to world users; PostgreSQL accepts a `0600` key owned by its runtime user, or a
root-owned `0640` key whose group is the PostgreSQL runtime group. The CA and
server certificate may be `0644`.

The Compose configuration fails closed if the TLS files are absent or invalid:
PostgreSQL is started with `ssl=on` and Grafana trusts only the mounted
`postgres-ca.crt`.

## Deployment

1. Copy `env.example` to `/etc/opip-data-platform.env`, replace every secret,
   use the analytics container hostname `opip-postgres` in admin/shipper DSNs,
   leave `OPIP_GRAFANA_DB_SSLMODE=verify-full`, and set mode `0600`.
2. Provision the three PostgreSQL TLS files above before starting PostgreSQL,
   and verify the server certificate chains to `postgres-ca.crt` and is valid
   for DNS name `opip-postgres`.
3. Set `OPIP_POSTGRES_BIND_ADDRESS` to the analytics droplet's private VPC IP.
4. Run the owner-gated `empty` stage twice with a successful restore drill
   between the first and latest successful empty deployments.
5. Verify the off-host infrastructure copy, then run `offhost-verified` to
   record that independent attestation. Run `rollback-verified` to record the
   two-empty-plus-restore evidence.
6. Advance one explicit stage at a time: `backfill`, `shipper`, and
   `reads-ready`.
7. Only after `reads-ready` succeeds, configure the production dashboard with
   the read-only `opip_dashboard` credential, set
   `OPIP_DATA_PLATFORM_READS_ENABLED=true`, and keep the 1.5 second statement
   timeout. Live tiles remain file-backed.
8. Start Grafana with
   `docker compose --env-file /etc/opip-data-platform.env -f deploy/analytics/docker-compose.yml up -d opip-grafana`,
   then configure TLS reverse proxy routing to the private bind endpoint.

The stages are deliberately non-collapsible. `empty` installs PostgreSQL and
the additive schema; `offhost-verified` records an owner attestation only
after the independent infrastructure copy is verified; `rollback-verified`
requires two successful empty deployments with a restore drill between them;
`backfill` requires all of that durable evidence; `shipper` requires a clean
backfill; and `reads-ready` requires a seven-day shipper soak, clean
reconciliation, lag below five minutes, and no unresolved dead letters. A
failed step leaves the production scanner and its file WAL unchanged.

Nightly custom-format dumps are checksummed locally, but the off-host copy is
an independent infrastructure responsibility. The `offhost-verified` stage
does not create that copy; it records the owner's attestation after the copy has
been verified. After the first dump and after every material schema change, run
`opip-postgres-restore-drill`; it restores
into a temporary database, validates `ops.schema_version`, records evidence,
and drops only that temporary database.

## Intelligence Cockpit provisioning

Grafana assets are provisioned as code:

- `deploy/grafana/provisioning/datasources/opip-postgres.yml`
- `deploy/grafana/provisioning/dashboards/opip-dashboards.yml`
- `deploy/grafana/dashboards/opip-intelligence-cockpit-v1.json`

Datasource credentials are externalized in `/etc/opip-data-platform.env` and
must map to the read-only PostgreSQL role.

## Storage lifecycle policy (fail-closed)

Retention pruning for derived PostgreSQL partitions remains in
`app.opip.data_platform.maintenance` and does not touch canonical source files
or verified archives.

Archive lifecycle tiers:

- HOT (0 to <7 days): active append-only evidence remains directly readable.
- WARM (>=7 to <90 days): immutable compressed segments (`.gz` or `.zst`) with
  checksum and manifest evidence.
- COLD (>=90 days): off-host archival eligibility only after finalized marker,
  checksum validation, manifest inclusion, archive verification, and explicit
  off-host verification evidence.

Use:

```bash
python -m app.opip.data_platform.archive_lifecycle \
  --root /var/lib/opip-learning/data/opip/qualification \
  --fail-if-cold-unverified
```

The lifecycle checker is fail-closed: old local files are never cleanup-eligible
from age alone.
