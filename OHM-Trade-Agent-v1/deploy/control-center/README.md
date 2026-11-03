# O'Pip V2 optional side-by-side edge overlay

The default Compose and Nginx remain legacy-only. The base Nginx config includes
`/etc/nginx/opip-control-center/*.conf`, which is empty without this overlay.
The overlay **never creates a new port or independent upstream**. It creates one
new image with the precompiled Vite static files and mounts exact-path routes
behind the existing TLS dashboard edge on port 8443.

Only the production **release controller**, after owner-approved change
scope and successful exact-head CI, may apply the overlay. Never run a stand-alone
`docker compose -f ...v2.yml up` or copy unsigned UI artifacts to the host.
`OPIP_DEPLOYED_SHA` must be the verified release commit; the overlay is inert
until it is included by an authorized controller.

Production acceptance gates:
- Verify Nginx `-t` with the real cert mount; `GET /dashboard` remains unchanged.
- Confirm `/dashboard/v2/` serves the exact CI-reviewed static bundle and
  `/dashboard/v2/assets/*` with successful TLS/CSP; unlisted paths return 404.
- Confirm all exact `/api/opip/*` routes require the existing viewer/admin
  credentials and that forbidden methods fail closed.
- Capture CPU, RAM and disk budgets for both droplets; verify no scanner overlap.
- Confirm a rollback omits the overlay, restores the original nginx image, and
  that the original `/dashboard` and 443 Tradevisor virtual host remain online.
- Verify live V1/V2 paired evidence and the already installed analytics platform
  independently before declaring actual data parity.
- New analytical retention is **not activated** by this overlay.

Rollback is the inverse, through the same owner-gated exact-SHA controller.
Never manually modify or rewrite the tracked Nginx source on a live host.

## Owner-gated UI-only release (existing controller, edge service only)

The merged source does **not** need a full trading-stack deploy to become
reachable. The existing controller gained one bounded mode:

| Owner action (comment on the approval issue, owner only) | Host command | Effect |
| --- | --- | --- |
| `/deploy-ui <40-hex main SHA>` | `deploy-ui` → `ohm-deploy ui-deploy <sha>` | stage, build, recreate **only** `ohm-dashboard-edge` |
| `/rollback-ui <40-hex active UI SHA>` | `rollback-ui` → `ohm-deploy rollback-ui <sha>` | restore the captured previous edge |

Guarantees of that path:

- Same forced-command SSH key, same `/var/lock/ohm-deploy.lock`, same
  exact-`main`-SHA owner approval and green-CI requirement as `/deploy`.
- It never names `ohm-trade-agent`, `opip-canonical-writer`, the scanner, F11,
  paper/Freqtrade, learning or analytics; it never passes `--remove-orphans` and
  never runs a bare `docker compose -f ...v2.yml up`.
- The Nginx base config and the V2 snippet are mounted from the
  manifest-verified candidate tree staged at
  `/var/lib/ohm-deploy/staging/<sha>/tree/OHM-Trade-Agent-v1`, so the
  `/dashboard/v2` include arrives **without** deploying the older-running core.
- Preflight (fail closed, no mutation): candidate files + include, Compose
  graph (image pin, exact mounts, `read_only`, ports 443/8443 only), `nginx -t`
  with the real certificate mount, disk/RAM budget, and a captured rollback
  vehicle.
- Post-cutover gates: `/dashboard` (200 + legacy marker + 308 redirect),
  `/dashboard/v2/` (200 + bundle), one hashed asset, unauthenticated
  `/api/opip/*` refusals for the exact allowlisted methods, a denied
  out-of-allowlist method, the 443 vhost (GET only, never a webhook POST), and
  unchanged cgroup/port isolation. Any failure restores the previous edge and
  re-verifies the legacy dashboard.
- Receipts: `/var/lib/ohm-deploy/ui/receipts/<sha>.<utc>.env` (immutable per
  attempt) plus an append-only `history.log`.

One-time owner prerequisite after merge: the host still runs the pre-merge
controller, so install the merged controller pair once from the droplet console
(no deployment is performed by that step):

```bash
sudo -n bash /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1/deploy/remote/bootstrap-release-tree-controller.sh --apply --sha <merged 40-hex>
```

That installs `ohm-deploy` and `ohm-deploy-ssh`; `reconcile-scheduler.sh` then
keeps `opip_ui_release.sh` installed beside the controller on every later core
release. After it, the single approval comment above is the whole release.

