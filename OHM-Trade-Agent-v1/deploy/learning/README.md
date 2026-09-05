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

Production exports only copy-only, non-authoritative JSONL evidence:

- `p1_shadow_outbox.jsonl`
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
- ML capture: every 5 minutes
- Phase 3C outcomes: every 10 minutes

Timers are staggered and every service shares the same learning-plane lock, so
sync/capture/outcomes cannot overlap.

## Deployment sequence

1. Merge the reviewed release and obtain the exact main SHA.
2. Deploy production at that exact SHA (`/deploy <sha>`).
3. **Matching learning worker deploy is required** before compute is healthy:
   owner-gated `/deploy-learning <40-char-sha>` on issue 64 (exact `main` +
   successful `pytest.yml`). Core `/deploy` does **not** update the learning
   worker.
4. Create the learning droplet in the production region/VPC (first time only).
5. Run initial bootstrap (first time only):

   ```bash
   sudo bash deploy/learning/bootstrap-opip-learning-worker.sh \
     <EXACT_MAIN_SHA> <PRODUCTION_PRIVATE_IP> opiplearn
   ```

6. Copy the public key printed by bootstrap.
7. On production, authorize it:

   ```bash
   sudo bash deploy/remote/configure-opip-learning-reader.sh \
     'ssh-ed25519 AAAA... opip-learning-worker' \
     10.116.0.4/32
   ```

8. On the learning worker, verify the production SSH host fingerprint and add
   the private production address to `/root/.ssh/known_hosts`.
9. Start one-shot validation in order:

   ```bash
   sudo systemctl start opip-learning-sync.service
   sudo systemctl start opip-learning-capture.service
   sudo systemctl start opip-learning-outcomes.service

   sudo systemctl status --no-pager \
     opip-learning-sync.service \
     opip-learning-capture.service \
     opip-learning-outcomes.service
   ```

10. Verify no job containers remain:

   ```bash
   sudo docker ps -a --filter label=com.opip.learning.job
   ```

   Expected: no containers.

11. Enable timers only after all one-shot checks pass:

   ```bash
   sudo systemctl enable --now \
     opip-learning-sync.timer \
     opip-learning-capture.timer \
     opip-learning-outcomes.timer

   systemctl list-timers 'opip-learning-*'
   ```

Subsequent exact-SHA updates use `/deploy-learning <sha>` which runs
`deploy/learning/run-gated-learning-deploy.sh` (rebuild image, refresh
`OPIP_DEPLOYED_SHA` / `OPIP_LEARNING_IMAGE`, restart enabled timers). Do not
place Kraken or Telegram credentials on the worker.

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
