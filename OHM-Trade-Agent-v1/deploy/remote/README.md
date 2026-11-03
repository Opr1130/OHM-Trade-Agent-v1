# OHM Remote Operations Control Plane

This directory contains the server-side pieces for audited GitHub-driven deployment.

## Safety properties

- GitHub Actions may request only `deploy <40-char-sha>`, `deploy-ui
  <40-char-sha>`, `rollback-ui <40-char-sha>` or `diagnose-learning` through the
  forced SSH key. `deploy-ui`/`rollback-ui` are owner-gated and can only stage,
  build and recreate the `ohm-dashboard-edge` service; they never name a trading,
  writer, scanner, F11, paper, learning or analytics service.
- The forced SSH key cannot open an interactive shell.
- The server deploy command accepts only the exact commit currently at `origin/main`.
- Deployments are serialized with a filesystem lock.
- Tracked dirty production worktrees are rejected.
- The application container is rebuilt and `/health` must return `{"status":"ok"}`.
- Failed health validation triggers rollback to the last known-good SHA.
- No Kraken credentials, permissions, order placement, order confirmation, cancellation, or modification are added by this control plane.

## Installed component set

The owner-gated UI-only release needs four installed, root-owned components at
stable paths, because the controller and library are resolved from those paths
rather than from the (possibly older) running release tree:

| Path | Provided by |
| --- | --- |
| `/usr/local/sbin/ohm-deploy` | core deploy, or `bootstrap-ui-release-controller.sh` |
| `/usr/local/sbin/ohm-deploy-ssh` | `reconcile-scheduler.sh` on a core deploy, or the bootstrap |
| `/usr/local/lib/opip/opip_release_tree.sh` | core deploy, or the bootstrap |
| `/usr/local/lib/opip/opip_ui_release.sh` | `reconcile-scheduler.sh` on a core deploy, or the bootstrap |

`bootstrap-ui-release-controller.sh` is the one-time, owner-operated first-use
bootstrap. A UI-only release never performs a core release, so `reconcile-scheduler.sh`
would not run before the first `/deploy-ui`; that is exactly the gap the bootstrap
closes. It installs all four atomically from one exact `origin/main` commit, keeps
a bounded backup of the previous set, reports `SUCCESS` / `NOT_NEEDED` with a
receipt, restores the previous set (or reports `RESTORE=UNPROVEN`) on failure, is
never reachable through the forced-command gateway, and does not rewrite
`authorized_keys`, `sudoers`, cron or the scheduler.

## CI environment

The pytest workflow explicitly sets `APP_ENV=test`. Running tests through the production Docker Compose service without overriding `APP_ENV` will intentionally activate conservative production gates, including execution-evidence requirements, and can make unit tests written for test mode fail.
