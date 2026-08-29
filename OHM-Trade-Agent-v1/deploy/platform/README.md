# O'Pip Platform Foundation — Sequence 1A

## Purpose

Sequence 1A hardens the existing production platform without changing trading logic, signal thresholds, ranking, AI authority, Kraken permissions, or paper/real execution boundaries.

This build establishes the minimum operational base required before the Event Intelligence and streaming work begins.

## Capacity profile

Current production:

- 1 vCPU
- 1 GB RAM
- 25 GB disk

Approved starting target for the next capacity step:

- 1 vCPU
- 2 GB RAM
- 50 GB disk
- DigitalOcean Basic Regular, approximately $12/month

Scale to 2 vCPU / 4 GB only when telemetry demonstrates the need. Suggested review triggers:

- sustained RAM use above 75%
- recurring swap use under normal load
- CPU saturation affecting scan-cycle latency
- event or job backlog growth
- container restarts / OOM events
- storage growth that materially reduces operational headroom

Do not resize production from repository automation. Capacity changes require explicit production approval and a backup/snapshot checkpoint.

## Sequence 1A scope

1. Host/platform health baseline.
2. Bounded Docker log retention.
3. Application container healthcheck.
4. Process-count hardening.
5. Local data backup utility with SQLite-safe copies.
6. Restore-verification utility.
7. Backup/restore runbook and acceptance criteria.

The production resize to the 2 GB tier exposed a pre-existing paper-sidecar constraint: the two Freqtrade workers shared a 384 MB cgroup limit and repeatedly triggered cgroup OOM kills while the second worker initialized. Sequence 1A therefore raises only that paper-sidecar limit to 768 MB while retaining the existing 0.40 CPU cap and elevated OOM score. Core application memory remains uncapped until a stable post-resize baseline is observed.

## Platform check

Run from the production application root:

```bash
deploy/platform/opip-platform-check.sh
```

The check reports memory, swap, disk, Docker availability, the core container state, and the HTTP application health endpoint.

Thresholds are configurable with environment variables:

- `OPIP_MEM_WARN_PCT` (default 75)
- `OPIP_MEM_CRIT_PCT` (default 90)
- `OPIP_SWAP_WARN_PCT` (default 35)
- `OPIP_SWAP_CRIT_PCT` (default 70)
- `OPIP_DISK_WARN_PCT` (default 75)
- `OPIP_DISK_CRIT_PCT` (default 90)

A critical result exits non-zero. Warnings are reported but do not fail by default.

## Local data backup

Run:

```bash
python3 tools/opip_platform_backup.py --source data --destination /var/backups/opip --retention 14
```

The backup utility never copies `.env`, skips transient lock/temp files and live SQLite `-wal`, `-shm`, and `-journal` sidecars, uses SQLite's online backup API for each SQLite database, writes a SHA-256 manifest for every archived file, writes a checksum for the final archive, and prunes only old O'Pip archives beyond the configured retention count.

The resulting archive is a local recovery checkpoint only. It is not disaster recovery because it lives on the same Droplet.

## Restore verification

Run:

```bash
python3 tools/opip_platform_restore_verify.py /var/backups/opip/opip-data-<timestamp>.tar.gz
```

Verification performs archive checksum verification, safe temporary extraction, per-file hash and size verification, and SQLite `PRAGMA integrity_check`. It never overwrites production data.

## Off-host disaster recovery

Sequence 1 requires an off-host recovery path in addition to local archives. DigitalOcean's current API response does not show an active backup policy for the production Droplet, so enabling platform backups/snapshots remains an explicit production-control-plane action.

Before any capacity resize:

1. Run the local backup utility.
2. Verify the backup.
3. Take or confirm an off-host DigitalOcean snapshot or backup.
4. Record the current production commit SHA.
5. Perform the resize in a controlled maintenance window.
6. Verify Docker, scheduler, health endpoint, Telegram, and paper state after boot.

## Exit criteria for Sequence 1A

Sequence 1A is complete when:

- platform check is green on production
- Docker logs are bounded
- the application container healthcheck is healthy
- a local backup is created successfully
- restore verification passes against that backup
- off-host backup/snapshot policy is confirmed
- the paper sidecar remains healthy with both USD and USDT workers under the 768 MB cap
- a rollback/recovery runbook is validated
- no trading authority, thresholds, or advisory behavior changed
