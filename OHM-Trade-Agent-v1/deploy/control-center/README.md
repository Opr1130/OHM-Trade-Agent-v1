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
- The Nginx base config and the V2 snippet are mounted from an **immutable,
  digest-verified per-release copy** at
  `/var/lib/ohm-deploy/ui-releases/<sha>/tree/OHM-Trade-Agent-v1`, promoted from
  the manifest-verified candidate tree staged at
  `/var/lib/ohm-deploy/staging/<sha>/tree/OHM-Trade-Agent-v1`. The staging
  directory is build input only: the staging provider deletes and re-extracts it,
  so it is never what the live container mounts. `/dashboard/v2` therefore
  arrives **without** deploying the older-running core, and a repeat deploy of
  the same SHA reuses the retained copy untouched.
- Preflight (fail closed, no mutation): candidate files + include, the frozen
  artifact's digest, Compose graph (image pin, exact mounts, `read_only`, ports
  443/8443 only), `nginx -t` with the real certificate mount, disk/RAM budget,
  and a **provable** rollback vehicle — the actual running edge's kind, immutable
  image identity and, for a V2 edge, the retained release tree it mounts. A
  missing or digest-inconsistent artifact refuses the cutover here.
- Post-cutover gates: `/dashboard` (200 + legacy marker + 308 redirect),
  `/dashboard/v2/` (200 + bundle), one hashed asset, unauthenticated
  `/api/opip/*` refusals for the exact allowlisted methods, a denied
  out-of-allowlist method, and unchanged cgroup/port isolation.
- Two **independent** ingress checks on their own ports: the dashboard on 8443
  and the Tradevisor webhook vhost on 443. The 443 answer is captured as a
  baseline before cutover, and a listener that answered before and answers
  nothing afterwards fails the release even when 8443 is perfect. Both probes are
  GET only — no synthetic webhook POST is ever sent, and a method-restricted 403
  is accepted as a healthy listener+SNI+route proof.
- Any failure restores the ACTUAL previous edge: the V2 overlay plus the previous
  V2 image and tree for a V2→V2 restore, the tracked base file for a legacy edge,
  with the recreated container's image identity verified against the captured
  one. Both ingress paths are re-verified after the restore.
- Receipts: `/var/lib/ohm-deploy/ui/receipts/<sha>.<attempt>.env` (one immutable,
  atomically written file per attempt, with a nanosecond+PID attempt identity so
  two attempts cannot collide) plus an append-only `history.log`.

One-time owner prerequisite **after all approvals and merges**: the live
installed release predates this script and is not a Git working tree. Therefore
do NOT invoke a nonexistent script from `/opt/OHM-Trade-Agent-v1`, and do NOT
run a core deployment just to install the UI controller. The existing, trusted
bare Git object cache `/srv/opip-v2/cache/repo.git` is the source of the new
script and all four controller components. From an owner-authorized host console
only, first establish the **exact final merged main commit** from reviewed CI,
then use this fail-closed bootstrap. Every command below runs on the droplet;
the named cache must already exist. No curl-to-shell, checkout mutation or
container changes:

```bash
set -euo pipefail
EXPECTED_SHA="<final reviewed merged origin/main 40-hex SHA>"
CACHE="/srv/opip-v2/cache/repo.git"
REPO_URL="https://github.com/Opr1130/OHM-Trade-Agent-v1.git"
test "$EXPECTED_SHA" != "<final reviewed merged origin/main 40-hex SHA>"
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(sudo -n git --git-dir="$CACHE" remote get-url origin)" = "$REPO_URL"
sudo -n git --git-dir="$CACHE" fetch --prune --no-tags origin main
test "$(sudo -n git --git-dir="$CACHE" ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')" = "$EXPECTED_SHA"
sudo -n git --git-dir="$CACHE" cat-file -e "${EXPECTED_SHA}^{commit}"
staged="$(mktemp)"
trap 'rm -f -- "$staged"' EXIT
sudo -n git --git-dir="$CACHE" show "$EXPECTED_SHA:OHM-Trade-Agent-v1/deploy/remote/bootstrap-ui-release-controller.sh" > "$staged"
test -s "$staged"
bash -n "$staged"
sudo -n bash "$staged" --check --sha "$EXPECTED_SHA"
sudo -n bash "$staged" --apply --sha "$EXPECTED_SHA"
```

The initial bootstrap script is extracted from **that reviewed Git commit**.
It uses the same bare cache to install all four components, not the running
release source or a nonexistent checkout `.git`. The previous release-tree
bootstrap's `/var/lib/ohm-deploy/controller-bootstrap-previous` is a regular
file and MUST remain intact; the UI bootstrap stores its backup separately in
`/var/lib/ohm-deploy/ui-controller-bootstrap-previous/`.

Inspect the output and STOP if a trust, SHA, gateway, restore or receipt
verification fails. Never switch to the general core deploy as a workaround.

That installs, atomically and with a bounded backup of the previous set:
`/usr/local/sbin/ohm-deploy`, `/usr/local/sbin/ohm-deploy-ssh`,
`/usr/local/lib/opip/opip_release_tree.sh` and
`/usr/local/lib/opip/opip_ui_release.sh`. It verifies each asset's bytes, mode,
ownership and required behaviour, refuses a SHA that is not current `origin/main`,
refuses a deploy key not wired to `/usr/local/sbin/ohm-deploy-ssh`, is idempotent
(`NOT_NEEDED`), and reports `RESTORE=VERIFIED` or `RESTORE=UNPROVEN` on failure.
It does **not** rewrite `authorized_keys` and does **not** rely on a later core
release, because the UI-only path never performs one.

