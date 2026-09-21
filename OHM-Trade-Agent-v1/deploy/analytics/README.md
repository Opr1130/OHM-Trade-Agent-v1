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
9. The `reads-ready` stage starts `opip-cockpit` (the read-only B/C-4 Cockpit) and
   proves host-loopback reachability. Configure the reverse proxy for the Cockpit
   paths below before treating the Cockpit as operator-available.

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

## Read-only Cockpit exposure

The B/C-4 Cockpit (`opip-cockpit`) answers Paper-v2 analytical questions from the
verified canonical replica. It is exposed using the **same model as Grafana**: a
loopback-published service behind the host's TLS reverse proxy. No new proxy platform
is introduced, and this repository does not version-control the host proxy
configuration.

### Required reverse-proxy routes

The external endpoint must terminate TLS and forward these four paths to the
host-loopback Cockpit port. All four are GET-only and must not be cached.

| External path | Proxied to |
| --- | --- |
| `/cockpit` | `http://127.0.0.1:${OPIP_COCKPIT_HOST_PORT}/cockpit` |
| `/api/cockpit/overview` | `http://127.0.0.1:${OPIP_COCKPIT_HOST_PORT}/api/cockpit/overview` |
| `/api/cockpit/trades` | `http://127.0.0.1:${OPIP_COCKPIT_HOST_PORT}/api/cockpit/trades` |
| `/api/cockpit/trades/*` | `http://127.0.0.1:${OPIP_COCKPIT_HOST_PORT}/api/cockpit/trades/*` (path parameter) |

Requirements:

- Terminate TLS at the proxy; do **not** expose `OPIP_COCKPIT_HOST_PORT` to the
  Internet, and do not publish it on a public or VPC interface.
- Restrict the proxied methods to GET. The API is read-only, and the edge should say
  so rather than relying on the application alone.
- Do not cache. Analytical responses are point-in-time and carry an `as_of`.
- The four paths are the whole surface. `/api/cockpit/trades/*` is a prefix match for
  the Trade Detail path parameter and exposes nothing beyond that route.

### Authentication

`/cockpit` is a public static shell; every `/api/cockpit/*` read requires the
Cockpit's own secret in the `x-webhook-secret` header. The proxy must forward that
header and must not inject or store the secret. A request without it returns 401.

**The Cockpit secret must be a distinct value from the trading host's
`WEBHOOK_SECRET`.** The trading host's secret is not merely a dashboard credential: it
also gates `POST /operator/mode`, `POST /operator/orders` and
`PATCH /operator/orders/{trade_id}`, so it carries order creation and modification
authority. Copying it here would place an order-capable credential on an externally
reachable read-only surface. The Cockpit therefore uses its own read-only
`OPIP_COCKPIT_SECRET`, and bootstrap refuses to run at all if the sealed analytics env
file contains `WEBHOOK_SECRET`, `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` or
`TELEGRAM_BOT_TOKEN`.

An unset or empty Cockpit secret fails closed: every read returns 401 rather than the
surface becoming open.

### Reachability proof (and what it is not)

The container healthcheck is **container-local liveness only**. It runs inside the
container, so it would pass even when the analytics network being internal (and the
port being unpublished) leaves the service unreachable from the host. A green
healthcheck is therefore **not** evidence that an operator can reach the Cockpit. That
was the original defect: a passing healthcheck with no host-reachable endpoint.

The `reads-ready` stage runs a separate host-side preflight that fails closed:

1. `OPIP_COCKPIT_BIND_ADDRESS` must be host loopback; any other value is refused,
   because the raw HTTP service must never be exposed beyond the host.
2. The container must report `healthy` (necessary, **not** sufficient).
3. The host-loopback endpoint **must be published**: `ss -ltn` must show a listener on
   `127.0.0.1:${OPIP_COCKPIT_HOST_PORT}`. Before the fix nothing listened here at all,
   which is exactly what this check exists to catch.
4. The port **must not** be bound on any public interface: `ss -ltn` must show no
   `0.0.0.0` / `[::]` / `*` listener on that port. This enforces the exposure rule at
   runtime, not only in the compose file.
5. The listener **must belong to `opip-cockpit`**: `docker port opip-cockpit` must
   report the loopback mapping. A stale or unrelated process holding the port would
   not produce that mapping, so a green listener alone can never stand in for the
   intended service.

Where each app-level claim is proven:

| Claim | Proven by |
| --- | --- |
| The page serves 200 on `/cockpit` | the container healthcheck, which issues a real GET inside the container |
| The API returns 401 without the operator secret, 200 with it | the test suite, driving the real ASGI app |
| Non-GET methods are rejected | the test suite |
| The service is published on host loopback and only there | the preflight (steps 3-5) |

The reachability proof is deliberately taken at the **socket layer** rather than by
issuing an application request from the shell. What the reverse proxy needs is a TCP
endpoint on host loopback that belongs to this container, so proving exactly that - and
that no public endpoint exists - is a direct proof of the contract that broke.
Application behaviour is proven where it actually lives (healthcheck and suite), and the
preflight prints `cockpit_app_behaviour=verified_by_healthcheck_and_test_suite` so the
split is explicit to an operator.

`COCKPIT_READY_AT_UTC` / `COCKPIT_READY_SHA` are written only after all five
conditions hold, so a passing container healthcheck alone can never mark the Cockpit
ready.

### Secret surface

The Cockpit is reachable through the reverse proxy, so it must not receive credentials
it has no use for. `/etc/opip-cockpit.env` is derived by bootstrap from the sealed
analytics env file with a strict allowlist:

```
OPIP_COCKPIT_SECRET          (gates every /api/cockpit/* read; distinct from the
                              trading host's order-capable WEBHOOK_SECRET)
OPIP_COCKPIT_BIND_ADDRESS
OPIP_COCKPIT_HOST_PORT
OPIP_COCKPIT_HTTP_PORT
```

The sealed file also holds the PostgreSQL admin, shipper, learning and dashboard
credentials and the privileged database URLs; none of those reach the Cockpit. This
follows the existing filtered-env pattern already used for Grafana.

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
