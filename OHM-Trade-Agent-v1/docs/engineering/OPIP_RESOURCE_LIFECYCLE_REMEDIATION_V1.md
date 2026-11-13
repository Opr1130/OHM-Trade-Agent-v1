# O'Pip — scheduler and resource-lifecycle remediation (v1)

Scope: Work Package **A** of the resource-remediation mission — the unified
cycle's runtime containment, terminal evidence, bound derivation, non-critical
work budget and read-only overdue/orphan observability.

This document is the requirement → acceptance test → implementation → result
record for the change. It adds no authority, changes no release profile and
touches no trading, paper-admission, learning or exchange path.

## 1. Defects addressed

| ID | Severity | Defect | Evidence |
| --- | --- | --- | --- |
| A-1 | P0 | The authorized runtime bound was enforced only on the **host launcher**. `timeout` around `docker compose exec` signals the Compose *client*; the exec'd Python process is a child of the container's exec runtime, so it survives the client's death. A cycle that outlived the bound kept running inside the container holding `/app/data/.unified_cycle.lock`, and every following minute logged `LOCK_CONTENTION_SKIPPED` — protection and discovery both stalled while the host lock looked free. | Repository-derived from `deploy/cron.d/ohm-unified-cycle` + `app/services/registry_io.registry_lock` |
| A-2 | P0 | Missing bound tooling degraded **open**: if the image lost `timeout`, the entry fell through to an unguarded `exec`, i.e. exactly the unbounded run A-1 describes. | `deploy/cron.d/ohm-unified-cycle` |
| A-3 | P0 | A bound-killed cycle left **no truthful terminal evidence**. Python's default `SIGTERM` disposition skips every `finally`, so the log could not distinguish "killed" from "died". | `app/jobs/run_cycle.py` |
| A-4 | P1 | The release controller and the diagnostics derived the bound by running the derivation expression over the **whole file**, so a comment quoting the long-form flags became the derived authority (it either replaced the real bound or produced two matches and failed the deploy). | `deploy/remote/ohm-deploy::scheduler_hard_bound_seconds`, `deploy/remote/diagnose-opip-learning.sh` |
| A-5 | P1 | Non-critical workloads (event intelligence, learning, dormant target spine, Tradevisor advisory) were bounded only by the container's hard bound, so a slow tail could occupy the whole authorization and starve the next protection pass. | `app/jobs/run_cycle.py::_run_cycle_once` |
| A-6 | P1 | An overdue or orphaned cycle was not identifiable from the receipt: the diagnostics reported lock state and a log tail but never combined them. | `deploy/remote/diagnose-opip-learning.sh` |
| A-7 | P2 | Transport response caching was bounded by freshness (TTL) only, never by footprint. | `app/services/kraken_transport.py` (carried from the in-flight branch) |

## 2. Requirement → test → implementation → result

Tests: `tests/test_opip_resource_lifecycle_remediation_v1.py` (28 passed,
2 skipped locally — the skipped two execute real `bash`), plus the pre-existing
contract suites `tests/test_production_scheduler_v1.py`,
`tests/test_opip_unified_cycle_diagnostics_v1.py`,
`tests/test_opip_deploy_verify_budget_v1.py`.

| Req | Acceptance test | Implementation | Result |
| --- | --- | --- | --- |
| Containment on the real process | `test_ac_001_the_container_side_process_is_bounded_not_only_the_launcher`, `..._the_container_bound_is_strictly_inside_the_release_bound` | `deploy/cron.d/ohm-unified-cycle`: inner `timeout -s TERM -k 5s 3540` wraps `python -m app.jobs.run_cycle` **inside** the container | PASS |
| Fail closed when tooling is absent | `test_ac_001_a_missing_timeout_binary_fails_closed_instead_of_running_unbounded`, `test_ac_001_the_fail_closed_marker_cannot_be_read_as_a_completion` | Both layers guard with `command -v timeout` and emit `OPIP_UNIFIED_CYCLE_STATUS=CONTAINMENT_UNAVAILABLE` + `exit 98` (host) / `exit 97` (container); no path reaches an unguarded `exec` | PASS |
| Truthful termination evidence | `test_ac_003_a_shutdown_signal_terminates_with_durable_evidence`, `test_ac_004_*` | `app/jobs/run_cycle.py`: `_install_shutdown_handlers`, `_CycleTerminated(BaseException)`, `_record_termination` — `STATUS=TERMINATED`, `TERMINATED_AT`, `TERMINATED_REASON`, and **no** `COMPLETED_AT` | PASS |
| Survives every fail-open boundary | `test_ac_004_a_shutdown_request_survives_every_fail_open_boundary` | `_CycleTerminated` derives from `BaseException`, so no `except Exception` phase boundary can swallow it | PASS |
| Stale jobs cannot hold the lock indefinitely | A-1 + A-5 combined; `test_ac_011_*` | The in-container bound releases the lock at the container's own deadline; the cycle budget stops a runaway tail from consuming the whole bound | PASS (mechanism); host liveness UNVERIFIED |
| One authoritative bound, prose-insensitive | `test_ac_010_neither_reader_matches_the_entry_verbatim`, `test_ac_010_the_controller_derives_the_real_bound_past_a_decoy_comment`, `test_ac_002_*` | Both readers strip comment and `NAME=value` lines, require **exactly one** occurrence, and the controller additionally requires the match on the line that runs `python -m app.jobs.run_cycle` | PASS; the bash-executed cases run in Linux CI |
| Release verification and scheduler reconciliation stay compatible | `test_ac_002_the_entry_is_still_one_canonical_scheduler`, `tests/test_production_scheduler_v1.py`, `tests/test_opip_deploy_verify_budget_v1.py` | One job line, one scheduler, `flock` and the long-form bound literal unchanged; `observe_unified_cycle` still accepts only `SUCCESS\|DEGRADED` | PASS |
| Non-critical work cannot block critical protection | `test_ac_011_the_budget_gates_only_noncritical_phases`, `..._a_zero_budget_disables_the_gate_and_bad_values_never_open_it`, `..._the_default_budget_expires_well_inside_the_container_bound`, `..._every_noncritical_phase_is_actually_gated_in_the_cycle_body` | `_CycleBudget` + `NONCRITICAL_PHASES` in `app/jobs/run_cycle.py`; default 2700 s (`OPIP_UNIFIED_CYCLE_BUDGET_SECONDS`), `0` disables, an unparseable value never means unlimited; each skip emits `OPIP_UNIFIED_CYCLE_PHASE_SKIPPED_BUDGET=<phase>` and the cycle reports `DEGRADED` | PASS |
| Overdue / orphan detectable from existing supervision | `test_ac_012_the_assessment_reports_overdue_and_orphan_from_existing_state`, `test_ac_012_the_probe_verdict_is_available_to_the_assessment` | `deploy/remote/diagnose-opip-learning.sh`: `unified_cycle_lock_state`, `unified_cycle_holder_elapsed_seconds`, `unified_cycle_holder_overdue`, `unified_cycle_in_flight`, `unified_cycle_orphan_suspected`, derived from the existing non-destructive lock probe, its lock-free owner lookup and the bounded log tail | PASS (contract); host values UNVERIFIED |
| No authority widening | `test_ac_009_containment_grants_no_trading_learning_or_paper_authority` | No funded/paper/learning/order surface touched | PASS |
| Transport cache footprint bound | `test_ac_006_*`, `test_ac_007_*`, `test_ac_008_*` | `app/services/kraken_transport.py`: entry + byte caps, LRU eviction, telemetry | PASS |



## 3. Behavioural impact and safety statement (AGENTS.md principle 20)

1. **Production behaviour that changes.**
   - The cycle process is now terminated at 3540 s *inside* the container
     instead of at 3600 s on the host launcher only. A cycle that would
     previously have run past its authorization as an unsupervised orphan now
     stops and releases its lock.
   - If the container image loses `timeout`, the cycle **does not run** and the
     log records `CONTAINMENT_UNAVAILABLE`. This is deliberate: an unbounded
     run is the P0 being fixed. The exposure is bounded by the next minute's
     retry and is loudly visible (marker + exit code 97/98).
   - Work cycles longer than 2700 s begin reporting `DEGRADED` and skip
     event-intelligence/learning/target-spine/Tradevisor-advisory phases. Both
     `SUCCESS` and `DEGRADED` are accepted by the release observer, so this
     does not change the release gate.
2. **Trading authority.** Unchanged. No risk sizing, R:R minimum, position
   verification, admission, notification-qualification or exchange path is
   touched. `ACTIVE_POSITION_PROTECTION`, Paper-v2 protection, pending
   lifecycle, qualified-alert recovery and broad discovery are **never** gated.
3. **Durable evidence.** Additive only: three new terminal markers
   (`TERMINATED*`, `CONTAINMENT_UNAVAILABLE`) and one skip marker
   (`PHASE_SKIPPED_BUDGET`). No existing marker is renamed or removed, so
   existing log consumers keep working. No canonical store, registry or
   historical record is written, migrated or deleted.
4. **Authority/activation.** No release-profile mode, paper/Freqtrade flag or
   learning-worker compatibility field is changed.
5. **Out of scope.** Anything requiring the trading host: the actual runtime
   bound observed in production, the realised cycle duration distribution, and
   whether an orphan exists right now (see §5).

## 4. Rollback and deployment dependencies

- **Deploy prerequisites.** Nothing new. The change ships in the existing core
  image and the existing `/deploy` path; no new secret, service, volume or cron
  entry is introduced, and the scheduler reconciliation script is untouched.
- **Ordering.** `deploy/cron.d/ohm-unified-cycle` is installed by the same
  release transaction that ships `app/jobs/run_cycle.py`. The two are
  deliberately compatible in both directions:
  * new entry + old image: the in-container wrapper still bounds the process;
    the old `run_cycle` has no signal handler, so the bound still ends it (via
    `SIGKILL` after 5 s) — containment holds, only the extra evidence is absent.
  * new image + old entry: the process behaves exactly as before (host bound
    only) plus the cycle budget; nothing depends on the new entry.
- **Rollback.** Restore the previous `main` release as usual; the entry, the
  image and the diagnostics ship together. Because every new marker is additive
  and no old marker changed, no data migration or evidence repair is required.
- **Diagnostics compatibility.** `diagnose-opip-learning.sh` is rollback-safe:
  it reports `UNKNOWN`/`NONE` for anything it cannot derive, so an older

## 5. Read-only production diagnosis — what remains unverified

This workstation has **no** SSH path to the trading droplet, no analytics-plane
database access and no log export. The following are therefore explicitly
**UNVERIFIED**, and must be answered read-only from the host before release:

```bash
# 1. Is the container-side bound actually installed?
sudo grep -n 'timeout' /etc/cron.d/ohm-unified-cycle
#    Expect exactly ONE long-form occurrence and ONE short-form (-s TERM -k 5s)
#    occurrence, on the same job line.

# 2. Is a cycle stuck right now?
sudo bash <app-root>/deploy/remote/diagnose-opip-learning.sh | grep -E \
  'unified_cycle_(host_lock|lock_state|holder_elapsed_seconds|holder_overdue|in_flight|orphan_suspected|last_phase|log_latest_status|log_age_seconds)'
#    Interpret:
#      holder_overdue=YES              -> the bound is not taking effect (P0 not fixed)
#      orphan_suspected=YES            -> unsupervised process holding the lock
#      in_flight=YES + host_lock=HELD  -> a normal cycle is running

# 3. Is anything inside the container still running a cycle?
sudo docker compose -f <app-root>/../docker-compose.yml exec -T \
  ohm-trade-agent sh -c 'ps -eo pid,etimes,args | grep -c "[r]un_cycle"'
#    0 expected when no cycle is in flight.

# 4. How long do normal cycles actually take? (needed to judge the 2700 s budget)
sudo grep -E 'OPIP_UNIFIED_CYCLE_(STATUS|COMPLETED_AT|PHASE=)' \
  /var/log/ohm-unified-cycle.log | tail -n 200
```

None of these commands starts, signals, restarts, locks or writes anything; #3
observes only. **If the host cannot be reached, the code change is still
independently verified by its acceptance tests, but the runtime bound, the
realised duration distribution and the current orphan state stay unverified —
they are production measurements, not code defects.**

## 6. Explicitly not changed

- No new daemon, scheduler or supervision process was added; the existing
  `timeout` + `flock` supervision was made to work where the process lives.
- `MIN_NET_PROFIT`, `MIN_REWARD_TO_RISK`, `risk_per_trade_pct`, alert
  qualification and Early Watch cadence are untouched.
- No merge, deploy or production activation was performed by this change.

  diagnostics build run against a newer entry (or the reverse) reports UNKNOWN
  rather than a wrong bound.
