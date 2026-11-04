# O’Pip Operational Control Center V2

Evidence-led operator console for the O’Pip signal system. Every figure it shows
is either read from canonical evidence (projected into a derived SQLite store) or
rendered as an explicit `UNAVAILABLE`, `NO_EVIDENCE`, `STALE`, `INSUFFICIENT DATA`
or `OUTCOME NOT MATURED` state.

It has **no** trading authority: no funded order path, no F11 override, no scanner
admission change, no learning or Committee activation, and no canonical-evidence
write path.

## What the console does

| Workspace | Source | Notes |
| --- | --- | --- |
| Persistent header | `/api/opip/control-center/health` | Environment, F11, reconciliation, scanner heartbeat, learning CPU/RAM, analytics DB size, config digest, deployed revision, sync age, auth state. |
| KPI ribbon | same | Live V2 signals, V1/V2 attribution, AI agreement, strategy performance, retention & storage. |
| Overview | `/api/analytics/summary`, `/api/analytics/intelligence`, `/api/opip/zero-trade-explanation`, `/api/opip/scoring-comparison` | Existing recorded payloads, unchanged. |
| V1 / V2 Evidence | `/api/opip/control-center/signals` | Per-symbol V1 vs V2 with divergence classification, filters, sorting, keyset pagination, CSV/JSON export, forensics drawer. |
| Qualification | `/api/opip/control-center/scanner` | Attempt-by-attempt funnel; stages without an evidence source are named, not filled in. |
| Intelligence | `/api/opip/control-center/ai` | Real inference rows or an informative empty state; no invented curves. |
| Operations | header payload | F11, reconciliation, scanner heartbeat and revision as separate observable facts. |
| Data & Retention | `/api/opip/control-center/data` | Source coverage, ingestion lag, quarantine, aging, retention policy and bounded maintenance actions. |
| Config Admin | `/api/opip/control-center/config` | Schema-driven eight-domain registry, staging with audit note and optimistic concurrency, plus the existing protected advisory activation/rollback. |

## Deliberate non-claims

- **No matured attribution.** Avoided losses, missed winners, agreement rate,
  win rate and expectancy stay `OUTCOME NOT MATURED` / `INSUFFICIENT DATA` until
  matched outcome evidence is projected.
- **No qualification inference.** Qualified and rejected candidate counts have no
  registered source, so they render `UNAVAILABLE` rather than being derived from
  score thresholds.
- **No F11 claim.** There is no persisted protection artifact on the host, so the
  header states `UNVERIFIED`. Only an artifact at `OPIP_F11_STATE_PATH` changes it.
- **No learning-droplet telemetry.** There is no cross-host channel, so CPU/RAM
  read `UNAVAILABLE` instead of an assumed value.
- **No activation from this console.** Configuration staging is inert; activation
  and rollback stay with the protected `/api/opip/strategy-admin/*` endpoints.
- **No deletion.** Retention runs dry-run unless `OPIP_ENABLE_RETENTION_DELETE`
  is separately authorized.
- **Brand asset.** `src/format.mjs` declares the approved transparent logo path
  `/assets/opip-control-center-logo-transparent.png`. If that file is absent the
  header renders an explicit missing-asset mark — it never substitutes another
  logo.

## Local development (never production)

```bash
cd OHM-Trade-Agent-v1/control-center-v2
npm install
npm test          # node unit tests for the pure presentation/API helpers
npm run build     # production bundle
npm run dev       # Vite dev server, /api proxied to 127.0.0.1:8000
```

`npm run test:e2e` runs the Playwright smoke suite against the dev server.

## Deployment

Not deployed by this increment. The V2 edge overlay is opt-in and the only
production path is the owner-gated UI-only release controller with an exact
merged SHA; it deploys `ohm-dashboard-edge` only and leaves the trading core, the
scanner, the canonical writer, F11, learning and the paper engines untouched.
