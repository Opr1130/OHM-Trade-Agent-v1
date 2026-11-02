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
