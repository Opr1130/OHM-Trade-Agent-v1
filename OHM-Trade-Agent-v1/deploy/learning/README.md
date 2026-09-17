# O'Pip Learning Plane

## Purpose

The learning plane moves non-authoritative evidence processing off the
production trading droplet. Production retains deterministic trading,
streaming, Freqtrade dry-run workers, the dashboard, and a lightweight
copy-only evidence export. ML capture and Phase 3C outcome maturation execute
only on the separate learning worker.

## Production contract

Production MUST NOT schedule:

- `app.jobs.run_opip_ml_capture`
- `app.jobs.build_phase3c_forward_outcomes`

The legacy `p1_shadow_outbox.jsonl` is retired and MUST NOT be recreated,
exported, synced, backfilled, or treated as an active learning stream.
Historical evidence already persisted in `p1_evidence_ledger.jsonl` remains
eligible for governed outcomes/accountability consumption.

Production exports copy-only, non-authoritative JSON/JSONL evidence:

- `full_market_observations.jsonl`
- `p1_evidence_ledger.jsonl`
- `intelligence_learning/events.jsonl`
- `opip/qualification/{screening_evaluations,funnel_events,scan_summaries}.jsonl`
- the three corresponding checksummed `*_archive/` trees
- `paper_trading/events.jsonl`
- `telegram_delivery_events.jsonl`
- `decision_telemetry.jsonl`
- `opip_trade_quality_evidence_v1.jsonl`
- `candidate_trace.jsonl`

## Canonical learning replica (PR-A)

The JSON/JSONL artifacts above are **not** the authority for terminal paper
outcomes. Canonical terminal paper economic truth lives in the production
canonical SQLite store, and it reaches this plane as a verified **copy-only
read-only replica** carried inside the same export generation.

Three distinct things must not be conflated:

| Thing | Role |
| --- | --- |
| Production canonical SQLite | **Economic evidence authority.** The only canonical writer. |
| Learning replica generation | **Read-only replica.** Authoritative for nothing; never a second authority, never an execution authority. |
| `paper_trading/state.json` + `evidence_gap_spool.json` | **Completeness companions.** They carry outbox delivery state and unresolved evidence gaps, without which a complete population cannot be certified. |

The bundle is self-contained and bound by `replica_manifest.json`:

```
canonical_learning_replica/
    replica_manifest.json
    opip/canonical/opip_canonical_v1.sqlite3
    paper_trading/state.json
    paper_trading/evidence_gap_spool.json
```

All three inputs must come from **one** generation. Readiness resolves the
verified bundle first and takes its canonical store, lifecycle state and gap
spool from that single object, so a canonical row can never be judged complete
using another generation's state.

### Transport

- The production exporter takes the SQLite snapshot with the PR-A0 online-backup
  path (never a raw copy of the live WAL store), normalizes it to
  rollback-journal, asserts it is sidecar-free, and publishes the whole stage as
  one atomic directory replacement.
- `manifest.env` is written **last** and is the generation commit marker. It
  carries `canonical_learning_replica_version=1` plus a tree byte count and
  digest. The marker is omitted entirely when no authoritative deployed SHA
  exists, so provenance is never minted for an unknown release.
- The forced reader takes its shared publish lock **before** reading the marker
  and emits the bundle and `manifest.env` in one tar, so they always come from
  the same committed generation.
- An export with no marker is still served exactly as before, which keeps the
  production-then-learning deployment order safe during release drift.

### Install and read

- `opip-learning-sync.sh` validates the outer transport digest, then delegates
  inner provenance entirely to Python running **inside** `OPIP_LEARNING_IMAGE`.
  A bundle can be internally consistent at the tar layer and still be rejected as
  invalid evidence; the two layers are checked independently.
- Validation completes before any publication, and generation activation happens
  after the data manifest is published, inside the plane lock — no learning
  consumer can run during sync, so this cannot expose an uncommitted generation.
  If activation fails, the previous `current` generation remains and learning
  fails closed.
- The store root is `/var/lib/opip-learning/canonical-replica`, deliberately
  **outside** the writable data root, and sync refuses to start if it resolves
  beneath it.
- Learning job containers receive exactly one resolved generation mounted
  read-only at `/app/canonical-replica`. Only the sync installer, a privileged
  administrative path, is ever given a writable store mount.
- Generations are immutable; `current` is an atomically replaced pointer file
  naming one generation. Retention is bounded to the active generation plus one
  previous known-good, and the active generation is never pruned.

### Cadence and freshness

- Production export runs roughly every **2 minutes**; learning sync every
  **2 minutes** offset from it.
- Replica freshness limit is **1800 seconds** (30 minutes), roughly fifteen
  export cycles: above the cadence enough to tolerate transient sync failures,
  below a daily window enough to notice a bridge that has stopped. A stale
  replica is unusable for supervised readiness even when its checksum is valid.

### Failure behaviour

A replica that cannot be proven — missing root, missing or malformed pointer,
missing or corrupt manifest, hash or byte mismatch, release mismatch, corrupt
SQLite, unavailable lifecycle state, or a stale snapshot — yields
`CANONICAL_OUTCOME_SOURCE_UNAVAILABLE` with **no** supervised eligibility. Legacy
lifecycle state is never substituted for the authority plane, and a verified
empty outcome stream remains a valid empty population rather than an error.

- `manifest.env`

The export path is `/var/lib/opip-learning-export`. The learning SSH key is bound to a forced read-only export command and the learning droplet's private source address; it cannot open an interactive shell or execute arbitrary commands with that key.

## Learning worker contract

Learning-only minimum host:

- DigitalOcean Basic / Regular
- 1 vCPU
- 1 GiB RAM
- same region and VPC as production

The PostgreSQL analytics stage reuses this isolated host only after it is
resized to at least 2 GiB. PostgreSQL must never be installed on production.

The worker has no Kraken private credentials, no Telegram authority, no paper
control write path, and no live qualification/ranking/execution authority.

Every compute invocation is:

1. serialized by `/var/lock/opip-learning-plane.lock`;
2. admitted only when `MemAvailable` is above a job-specific threshold;
3. run in an ephemeral Docker container with a hard RAM/CPU/PID/runtime limit;
4. networkless;
5. cleaned before start if a stale job container exists;
6. removed on EXIT/INT/TERM;
7. reaped again by systemd `ExecStopPost`;
8. checked for remaining labeled containers before returning.

## Timers

- evidence sync: every 2 minutes
- ML capture disposition: every 5 minutes
- Phase 3C outcomes: every 10 minutes

Timers are staggered and every service shares the same learning-plane lock, so
sync/capture/outcomes cannot overlap. With the P1 shadow outbox retired, the
capture invocation records governed `CONSUMED_EMPTY` rather than launching a
container for the deleted source.

## Deployment sequence

1. Merge the reviewed release and obtain the exact main SHA.
2. Deploy production at that exact SHA (`/deploy <sha>`).
3. Create the learning droplet in the production region/VPC (first time only).
4. Run initial bootstrap (first time only):

   ```bash
   sudo bash deploy/learning/bootstrap-opip-learning-worker.sh \
     <EXACT_MAIN_SHA> <PRODUCTION_PRIVATE_IP> opiplearn
   ```

5. Copy the public key printed by bootstrap.
6. On production, authorize it:

   ```bash
   sudo bash deploy/remote/configure-opip-learning-reader.sh \
     'ssh-ed25519 AAAA... opip-learning-worker' \
     10.116.0.4/32
   ```

7. On the learning worker, verify the production SSH host fingerprint and add
   the private production address to `/root/.ssh/known_hosts`.
8. Start one-shot validation in order:

   ```bash
   sudo systemctl start opip-learning-sync.service
   sudo systemctl start opip-learning-capture.service
   sudo systemctl start opip-learning-outcomes.service

   sudo systemctl status --no-pager \
     opip-learning-sync.service \
     opip-learning-capture.service \
     opip-learning-outcomes.service
   ```

9. Verify no job containers remain:

   ```bash
   sudo docker ps -a --filter label=com.opip.learning.job
   ```

   Expected: no containers.

10. Enable timers only after all one-shot checks pass:

   ```bash
   sudo systemctl enable --now \
     opip-learning-sync.timer \
     opip-learning-capture.timer \
     opip-learning-outcomes.timer

   systemctl list-timers 'opip-learning-*'
   ```

### Subsequent exact-SHA updates (already bootstrapped)

After every core `/deploy`, run matching owner-gated
`/deploy-learning <40-char-sha>` on issue 64 (exact `main` + successful
`pytest.yml`). Core `/deploy` does **not** update the learning worker.
`/deploy-learning` requires an existing bootstrapped host (`/etc/opip-learning.env`)
and runs `deploy/learning/run-gated-learning-deploy.sh`.

If all three learning timers are already enabled, the gated deploy rebuilds the
image, refreshes `OPIP_DEPLOYED_SHA` / `OPIP_LEARNING_IMAGE`, and restarts the
timers. If the host is bootstrapped but timers were never enabled, the gated
deploy completes bootstrap fail-closed: it runs sync, verifies the exact
production SHA and schema-4 P1 retirement marker, requires capture
`CONSUMED_EMPTY`, requires outcomes `CONSUMED_OK` or `CONSUMED_EMPTY`, syncs
again to publish the fresh heartbeat, verifies no orphan learning containers,
and only then enables all three timers. Any failed validation leaves recurring
compute disabled.

Do not place Kraken or Telegram credentials on the worker.

## Release compatibility (exact SHA)

Capture and outcomes admit only when worker `OPIP_DEPLOYED_SHA` equals the
production SHA published in synced `manifest.env` as `production_deployed_sha`
(`CURRENT`). On `RELEASE_DRIFT` or `UNVERIFIED`, compute fails closed with
disposition `BLOCKED_RELEASE_DRIFT` (non-zero). Evidence sync may still run
under drift for diagnostics. Busy/memory skips write durable dispositions
(`SKIPPED_BUSY` / `SKIPPED_CAPACITY`) and must not be silent.

## Guaranteed consumption (MVP)

Every job invocation records a terminal disposition under
`/var/lib/opip-learning/state/*.disposition.env`. Outcomes also write
`data/.learning_consumption/outcomes.json` including accountability pending
counts. Accountability handoff ack remains the artifact-level checkpoint;
dispositions make skips/blocks/consumption lag visible in
`diagnose-opip-learning`.

## Failure policy

Learning failures are non-authoritative. A failed, skipped, timed-out, or
OOM-killed learning job must never alter production trading decisions or funded
exchange authority. Skips and release-drift blocks must leave a durable
disposition; silent exit-0 capacity skips are defects. The next invocation
begins with stale-container cleanup and restart-safe local checkpoints.
