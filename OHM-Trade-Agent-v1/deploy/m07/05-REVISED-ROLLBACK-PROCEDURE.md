# 05 — Revised Rollback Procedure (R1)

## Why R0 was unsafe

R0 step 4 asserted `/var/lib/ohm-deploy/safe-baseline-rollback.override.yml`. The certified controller **deletes that file** in `cleanup_snapshot()` (`ohm-deploy:291`) after a successful release — the exact moment a rollback is most likely to be needed. The plan would have failed at its first meaningful step, after the scheduler had already been frozen.

## R1 design

`scripts/opip_recovery.py::plan_rollback()` generates and verifies its **own** durable overlay:

```
/var/lib/opip-rollback/<previous_sha>/safe-baseline.override.yml     (mode 0640)
```

Content is the certified SAFE_BASELINE profile (`OPIP_RELEASE_PROFILE=SAFE_BASELINE`, feature bus / writer / spine `off`, paper `off`, committee `off`, notional `0.0`). The planner re-reads the file, hashes it, and records the digest in the plan — a partial write is detected before any container is touched.

Properties:

* `depends_on_transient_override: false`
* no reference to the controller's state directory
* post-cutover canonical writes are copied to `/var/lib/opip-cutover-evidence/<utc>/` **before** the profile changes
* refusal when the live database advanced in **epoch, sequence or event count** since prepare
  (equal-epoch-but-newer-sequence is refused; epoch alone is not evidence)
* the pre-rollback canonical snapshot uses the SQLite backup API under quiescence, then
  verifies `integrity_check`, foreign keys and records the snapshot SHA-256

## Procedure

```
# 1. plan (never mutates V2; reads the live epoch/sequence/event count on the host)
pwsh -File scripts/Invoke-V2Rollback.ps1 -Phase Prepare -Kind safe-baseline -PreviousSha <40-hex>
#    exit 77 = plan printed; exit 3 = refused (newer history / bad SHA)

# 2. authorise and execute the printed steps on V2 (opipadmin + sudo)
pwsh -File scripts/Invoke-V2Rollback.ps1 -Phase Execute -Kind safe-baseline -PreviousSha <40-hex>
```

Executed order (printed by the planner):

1. freeze automation — remove the cycle cron entry (never two schedulers);
2. stop `opip-canonical-writer` and prove it stopped (quiesce the single writer);
3. take a WAL-safe SQLite snapshot into the cutover-evidence directory, verify integrity and
   record the SHA-256 (never a raw `cp` of an active database);
4. stop worker, core and edge;
5. apply the durable overlay (`-f docker-compose.yml -f /var/lib/opip-rollback/<sha>/safe-baseline.override.yml`) and recreate core, writer and edge;
6. verify uniqueness: one writer, one cycle cron line, one Telegram consumer;
7. revalidate `/health`.

## Scenario coverage

| Scenario | Result |
|---|---|
| Rollback after a **successful** deployment | plan PASS, durable overlay present, transient file absent by construction |
| **Interrupted** deployment (controller aborted) | same plan; scheduler snapshot may also be restored from `/var/lib/ohm-deploy/scheduler-recovery.*` (preserved by the controller, `:304-319`) |
| **Failed service activation** | steps 1-7 unchanged; writer is stopped before any profile change so no producer can race the restore |
| Missing rollback profile | planner refuses a malformed SHA; the overlay itself is generated, so "missing profile" cannot occur |
| Newer canonical history | refused unless the owner explicitly accepts the loss (no `-AcceptNewerHistory` flag exists: the only path is to keep the newer generation and roll back code only) |

## What a rollback does NOT do

* It never restarts V1 (that would resume a competing writer, scheduler and Telegram consumer).
* It never restores an older canonical generation over newer data.
* It never re-enables Paper V2 or funded authority: SAFE_BASELINE is strictly narrower than EVIDENCE_SHADOW.

## Verified tonight

`test_rollback_plan_is_durable_and_independent_of_transient_override`, `test_rollback_refuses_newer_history_and_bad_profile`, plus script runs: plan → 77, newer-history → 3, malformed SHA → 3. The container-level execution remains **PENDING LIVE VALIDATION** because it requires a running V2.
