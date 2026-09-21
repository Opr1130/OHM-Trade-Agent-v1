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
   `reads-ready`. The replica-backed Cockpit has its own independent stage,
   `cockpit-ready`, which neither waits for nor advances this sequence.
7. Only after `reads-ready` succeeds, configure the production dashboard with
   the read-only `opip_dashboard` credential, set
   `OPIP_DATA_PLATFORM_READS_ENABLED=true`, and keep the 1.5 second statement
   timeout. Live tiles remain file-backed.
8. Start Grafana with
   `docker compose --env-file /etc/opip-data-platform.env -f deploy/analytics/docker-compose.yml up -d opip-grafana`,
   then configure TLS reverse proxy routing to the private bind endpoint.
9. Start the read-only B/C-4 Cockpit with
   `/deploy-analytics <40-char-sha> cockpit-ready`, or as part of `reads-ready`.
   Both stages start it through the same shared primitive and prove host-loopback
   reachability. Configure the reverse proxy for the Cockpit paths below before
   treating the Cockpit as operator-available.

The stages are deliberately non-collapsible. `empty` installs PostgreSQL and
the additive schema; `offhost-verified` records an owner attestation only
after the independent infrastructure copy is verified; `rollback-verified`
requires two successful empty deployments with a restore drill between them;
`backfill` requires all of that durable evidence; `shipper` requires a clean
backfill; and `reads-ready` requires a seven-day shipper soak plus a clean
canonical freshness result (`ops.dashboard_freshness_v` must be LIVE for every
required stream and for maintenance). A failed step leaves the production
scanner and its file WAL unchanged.

## Two independent readiness planes

Analytics readiness here is two separate claims, and neither implies the other.

| | `reads-ready` | `cockpit-ready` |
| --- | --- | --- |
| Scope | PostgreSQL/Grafana historical analytics | Replica-backed read-only Cockpit |
| Evidence written | `READS_READY_AT_UTC`, `READS_READY_SHA` (in `rollout.env`) | `COCKPIT_READY_AT_UTC`, `COCKPIT_READY_SHA` (in `cockpit-ready.env`) |
| Seven-day shipper soak | Required | Not involved |
| PostgreSQL health/reconciliation gates | Required | Not involved |
| PostgreSQL started | Yes | **No** |
| Grafana/shipper started | As staged | **No** |
| Historical analytics granted | Yes | **No** |

`cockpit-ready` exists because the Cockpit reads only the verified canonical replica
and needs no PostgreSQL historical read. Before it existed, the only way to start the
Cockpit was the `reads-ready` stage, which made an otherwise valid operational Cockpit
wait on a seven-day PostgreSQL shipper soak it does not depend on. The fix separates
the two claims; it does **not** relax either one, and it does not shorten the soak.

`COCKPIT_READY_*` means exactly one thing: **the replica verified for this release, and
the read-only Cockpit is reachable on host loopback**. It is therefore published only by
`cockpit-ready`. The shared start primitive also serves `reads-ready`, which must not
depend on the replica plane and does not verify it; that path passes `unverified` and
publishes no `COCKPIT_READY_*` evidence, because container health and a loopback binding
establish neither replica integrity, freshness, nor release binding. `reads-ready` grants
only `READS_READY_*`, which is its own claim.

### The Cockpit has its own Compose surface

The Cockpit is defined in `deploy/analytics/docker-compose.cockpit.yml`, **not** in
`docker-compose.yml`. Docker Compose interpolates the **entire file** it is given before
selecting a single service, so a Cockpit-only deployment driven from the shared file
failed on this host with:

```
error while interpolating services.opip-grafana.environment.GF_SECURITY_ADMIN_USER:
required variable OPIP_GRAFANA_ADMIN_USER is missing a value:
set in /etc/opip-data-platform.env
```

`cockpit-ready` was already dispatched before the PostgreSQL/Grafana shell plane and
executed none of it, but the shared file's Grafana and PostgreSQL services carry
mandatory `${...:?}` variables, and `docker compose -f docker-compose.yml build
opip-cockpit` interpolated all of them. Shell-level isolation cannot isolate Compose
interpolation; only a separate Compose file can.

`docker-compose.cockpit.yml` therefore contains the Cockpit service and its network and
nothing else. It interpolates only `OPIP_COCKPIT_*` and `OPIP_DEPLOYED_SHA`, all of which
have defaults, so it renders and runs with every PostgreSQL/Grafana variable absent. It
deliberately keeps `name: opip-data-platform` and an identical `opip-analytics` network
definition, so the Cockpit stays in the same Compose project and joins the same internal
network rather than creating a second network on an overlapping subnet.

The service is defined exactly once. A second copy in the shared file would be dead
configuration that could silently drift from the hardening here, so the Cockpit is not
declared there; the shared file keeps the whole PostgreSQL/Grafana plane and its strict
requirements unchanged.

Every Cockpit build/start/status operation goes through the Cockpit-only surface
(`cockpit_compose` in bootstrap); the PostgreSQL/Grafana stages keep using the shared
file. To inspect or start the Cockpit manually:

```bash
docker compose --env-file /etc/opip-data-platform.env \
  -f deploy/analytics/docker-compose.cockpit.yml ps
```

`cockpit-ready` performs no PostgreSQL work and depends on no PostgreSQL/Grafana
setting. That is enforced structurally rather than by scattered guards: the Cockpit
stage is dispatched **before** the PostgreSQL/Grafana plane, so a Cockpit run returns
before the PostgreSQL DSN/password validations, the Grafana env derivation, the
PostgreSQL data and state directories, the PGDATA ownership fix, `config/pg_hba.conf`,
`rollout.env`, the PostgreSQL capacity floor, and the backup/maintenance timers are
reached at all. A Cockpit deployment therefore cannot require unrelated PostgreSQL
configuration, and cannot mutate PostgreSQL host state. Cockpit state is written to its
own `$STATE_ROOT/cockpit-ready.env`, never to `rollout.env`, so "this stage did not touch
PostgreSQL rollout evidence" is provable by inspection.

Idempotent: the image tag is release-pinned, `compose up -d` recreates only when the
definition changed, and the replica, container health and reachability are re-proven on
every run. `COCKPIT_READY_AT_UTC` and `COCKPIT_READY_SHA` are committed together by a
single rename, so an interruption can never leave a new timestamp paired with an older
release. Readiness is recorded only after every step succeeds, and a failed attempt
leaves no marker.

For `cockpit-ready`, the owner-gated workflow requires the
`analytics-production` GitHub environment secret `OPIP_COCKPIT_SECRET`. The
workflow constructs a minimal four-key Cockpit provisioning payload containing that
secret plus the fixed loopback bind and Cockpit ports; it does **not** reuse or decode
`OPIP_ANALYTICS_ENV_B64` for this stage. The remote runner validates that payload and
atomically merges only the four `OPIP_COCKPIT_*` settings into the installed host
environment before bootstrap. It refuses a missing, duplicate, placeholder, malformed,
or low-entropy Cockpit secret and rejects trading/order credentials. This provisions
the Cockpit independently without rerunning the PostgreSQL `empty` stage or rotating
unrelated PostgreSQL/Grafana credentials. No PostgreSQL container is started, and
`cockpit-ready` does not read, satisfy, advance or imply the soak or `reads-ready`.

Operator prerequisite: create `OPIP_COCKPIT_SECRET` in the
`analytics-production` environment before the first `cockpit-ready` run. Use a
fresh URL-safe value of at least 24 characters and never place the value in repository
files, issue/PR comments, or workflow logs.

### What `cockpit-ready` verifies before starting the Cockpit

1. The exact `opip-data-platform:$TARGET_SHA` image is built from the checked-out
   release. The tag is release-pinned, so a stale image from an earlier rollout cannot
   satisfy a different SHA, and PostgreSQL is not pulled or started to build it.
2. The committed replica generation is resolved. The replica root is a *repository*:
   installed bundles live under `generations/<id>` and are selected by a plain-text
   `current` pointer, so the parent directory holds no manifest and no canonical
   database. Resolution is delegated to the existing resolver rather than reimplemented,
   and the resolved generation - never the parent - becomes the root the Cockpit reads.
3. That generation is verified by invoking the **existing** verifier CLI - the same
   `verify` subcommand the learning sync already uses - inside a one-off container from
   that exact image, with `--network none` and the parent mounted read-only. It
   re-checks: manifest presence/readability, replica schema version, source release SHA
   equal to `TARGET_SHA`, canonical snapshot presence, self-containment and
   hash/structure, companion artifacts, and freshness against the existing replica
   freshness contract. The shell reproduces none of those rules, and
   `--max-age-seconds` is deliberately not passed, so this stage cannot widen the
   freshness bound.
4. The resolved generation is written into `/etc/opip-cockpit.env` as
   `OPIP_CANONICAL_REPLICA_ROOT`, so the Cockpit process reads the same bundle that was
   verified. It is deliberately not pinned in compose, because a pinned parent is not a
   bundle.
5. The container is started, and a bounded health wait must observe `healthy` before the
   host-loopback preflight below runs. `compose up -d` returns while the container is
   still `starting`, so proving reachability immediately would fail a first deployment
   even though the service becomes healthy moments later.
6. Only then are `COCKPIT_READY_AT_UTC` and `COCKPIT_READY_SHA` recorded.

The health wait never replaces the preflight, and neither replaces the verifier: a
missing, unreadable, structurally invalid, SHA-mismatched or stale replica, or an
unresolvable `current` pointer, fails the stage closed before the Cockpit is started.

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
host-loopback Cockpit port. The raw host-loopback endpoint is not reachable from
outside the host: an HTTPS/TLS reverse proxy is still required for external access.
All four paths are GET-only and must not be cached.

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

Both `cockpit-ready` and `reads-ready` run a separate host-side preflight (through the
shared `cockpit_start` primitive) that fails closed:

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

`COCKPIT_READY_AT_UTC` / `COCKPIT_READY_SHA` are recorded in
`$STATE_ROOT/cockpit-ready.env` - never in the PostgreSQL rollout evidence - and only
after the exact target image is built, the replica is verified, the container is
healthy and all five conditions above hold. A passing container healthcheck alone can
never mark the Cockpit ready, and a failed attempt leaves no marker at all.

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
