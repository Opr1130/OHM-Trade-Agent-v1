# OHM Remote Operations Control Plane

This directory contains the server-side pieces for audited GitHub-driven deployment.

## Safety properties

- GitHub Actions may request only `deploy <40-char-sha>` through the forced SSH key.
- The forced SSH key cannot open an interactive shell.
- The server deploy command accepts only the exact commit currently at `origin/main`.
- Deployments are serialized with a filesystem lock.
- Tracked dirty production worktrees are rejected.
- The application container is rebuilt and `/health` must return `{"status":"ok"}`.
- Failed health validation triggers rollback to the last known-good SHA.
- No Kraken credentials, permissions, order placement, order confirmation, cancellation, or modification are added by this control plane.

## CI environment

The pytest workflow explicitly sets `APP_ENV=test`. Running tests through the production Docker Compose service without overriding `APP_ENV` will intentionally activate conservative production gates, including execution-evidence requirements, and can make unit tests written for test mode fail.
