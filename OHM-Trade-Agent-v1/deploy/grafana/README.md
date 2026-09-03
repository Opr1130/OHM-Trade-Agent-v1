# O'Pip Grafana Intelligence Cockpit

Grafana runs on the isolated analytics droplet and reads O'Pip PostgreSQL with the
`opip_dashboard` read-only role.

## Security and deployment boundaries

- Use `/etc/opip-data-platform.env` for all secrets and credentials.
- Keep `OPIP_GRAFANA_BIND_ADDRESS=127.0.0.1` unless a private VPC bind is explicitly required.
- Front Grafana with a TLS reverse proxy; do not expose unauthenticated Grafana directly.
- Never grant Grafana a write-capable PostgreSQL role.
- Keep persistent state at `/var/lib/opip-data-platform/grafana`.

## Provisioned as code

- Datasource: `deploy/grafana/provisioning/datasources/opip-postgres.yml`
- Dashboard provider: `deploy/grafana/provisioning/dashboards/opip-dashboards.yml`
- Dashboard definition: `deploy/grafana/dashboards/opip-intelligence-cockpit-v1.json`

## Start/upgrade

```bash
docker compose -f deploy/analytics/docker-compose.yml up -d opip-grafana
```

## Reverse proxy

Set `GF_SERVER_ROOT_URL`/`OPIP_GRAFANA_ROOT_URL` to your TLS endpoint (for example
`https://analytics.example.com/grafana`) and publish Grafana only behind that proxy.
