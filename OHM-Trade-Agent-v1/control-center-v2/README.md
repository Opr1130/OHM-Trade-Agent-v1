# O’Pip Control Center V2 — repository-native UI review candidate

This React/Vite console consumes **existing authenticated O’Pip endpoints**. It adds no production source writer, new database, scanner dependency, risk authority, funded-trading function or retention-deletion path. The old dashboard stays untouched.

## Scope already wired
- Operator-authenticated production summary, intelligence and zero-trade explanation
- V1/V2 comparison from actual recorded evidence, with explicit NO_EVIDENCE and recorded model authority
- Scanner/qualification exploration and JSON forensic detail
- Advisory-only strategy draft read, optimistic-digest staging, activation and rollback through the existing two-header protected backend
- Memory-only credentials, no stored secrets, CSV formula-injection hardening, JSON detail, responsive premium shell, and a CI build workflow.

## Nonclaims and remaining gates
- **Not yet mounted** at /dashboard/v2 on Nginx, and **not deployed**. The Vite base is a target output path, not a deployment.
- The F10 eight-page parity contract remains unsatisfied: dashboard read availability is not equivalent to canonical count/PnL parity. Semantically unavailable metrics remain unavailable.
- There is no self-service live risk/F11 authority. Production strategy admin only permits an allowlisted advisory-policy diff; a separate server-side flag controls activation.
- The extra re-entry field in the UI is a local user confirmation; server-side authorization uses the existing X-Webhook-Secret and X-Strategy-Admin-Secret. Do not describe it as a third independent backend credential.
- The existing learning-droplet PostgreSQL platform must be runtime-inventoried before any standalone SQLite ingestion is authorized.
- Automated 30-day retention for a new data plane is **not installed** by this PR; the standalone ZIP candidate remains isolated pending data-plane arbitration.
- Cross-plane edge routing, TLS/auth, CI, browser E2E, canonical reconciliation, latency budgets and private deployment must be certified before activation.

## Local development (a review environment, never production)
```bash
cd OHM-Trade-Agent-v1/control-center-v2
npm install
npm test
npm run build
npm run dev
```
Vite dev proxies /api to the existing O'Pip backend on 127.0.0.1:8000; it is for development only.

## Deployment restriction
No automatic deployment is defined. Owner-approved exact-SHA release workflow remains the only production path. Do not merge or publish this UI until CI and parity-related security gates are complete.
