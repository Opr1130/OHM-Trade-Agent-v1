# O'Pip Grafana Intelligence Cockpit

Grafana runs on the isolated analytics droplet and reads O'Pip PostgreSQL with the
`opip_dashboard` read-only role.

## Security and deployment boundaries

- Use `/etc/opip-data-platform.env` for all secrets and credentials.
- Keep `OPIP_GRAFANA_BIND_ADDRESS=127.0.0.1` unless a private VPC bind is explicitly required.
- Front Grafana with a TLS reverse proxy; do not expose unauthenticated Grafana directly.
- Never grant Grafana a write-capable PostgreSQL role.
- Require `verify-full` TLS for Grafana's PostgreSQL datasource and mount the trusted
  PostgreSQL CA certificate at `/etc/grafana/certs/postgres-ca.crt`.
- Keep `OPIP_GRAFANA_POSTGRES_HOST=opip-postgres`; the PostgreSQL server certificate
  must contain `DNS:opip-postgres` in its subjectAltName.
- Keep persistent state at `/var/lib/opip-data-platform/grafana`.
- Do not deploy Grafana or PostgreSQL on the production trading droplet.

## Canonical freshness contract

The cockpit must consume `ops.dashboard_freshness_v` for freshness and readiness
state. It must not recreate freshness thresholds from `raw.ingested_event`,
`ops.platform_health_v`, Grafana refresh cadence, or dashboard response time.

Canonical statuses are `LIVE`, `DEGRADED`, `STALE`, and `UNAVAILABLE`. Required
streams and the `__maintenance__` row determine the top-level freshness state.
Panels should surface the canonical `reason`, `age_seconds`, policy threshold,
reconciliation state, dead-letter evidence, and freshness-view age directly.

Grafana's 30-second refresh interval means only that the page asks PostgreSQL for
new results every 30 seconds. It is never evidence that the underlying O'Pip data
is fresh.

## Provisioned as code

- Datasource: `deploy/grafana/provisioning/datasources/opip-postgres.yml`
- Dashboard provider: `deploy/grafana/provisioning/dashboards/opip-dashboards.yml`
- Dashboard definition: `deploy/grafana/dashboards/opip-intelligence-cockpit-v1.json`

## Rollout gate

Provisioning code may be merged before the analytics plane is ready for production
reads, but the cockpit must not be treated as authoritative until the analytics
rollout reaches `reads-ready`. That stage requires the non-collapsible analytics
deployment gates, including the real seven-day shipper soak and a clean canonical
freshness result. Do not bypass or simulate that elapsed-time evidence.

## Start/upgrade

Compose interpolation requires the sealed analytics environment file as well as
the service-level `env_file` declaration:

```bash
docker compose --env-file /etc/opip-data-platform.env \
  -f deploy/analytics/docker-compose.yml \
  up -d opip-grafana
```

## Reverse proxy

Set `GF_SERVER_ROOT_URL`/`OPIP_GRAFANA_ROOT_URL` to your TLS endpoint (for example
`https://analytics.example.com/grafana`) and publish Grafana only behind that proxy.
