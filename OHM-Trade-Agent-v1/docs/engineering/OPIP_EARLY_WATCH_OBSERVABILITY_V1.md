# O'Pip — Early Watch observability and notification-mode control (v1)

Work Package **C** of the resource-remediation mission, plus the platform-test
cleanup from Work Package **D** (this is the smallest relevant PR for it).

Scope: measurement, an explicit notification-mode configuration decision, and one
production portability defect. **No trading authority, qualification threshold,
risk default or alert-suppression default changes.**

## 1. What this PR does and does not do

| Item | Status |
| --- | --- |
| Per-phase CPU, RSS, cgroup memory and duration instrumentation | **Delivered** (`app/services/phase_resources.py`, off by default) |
| Automatic Early Watch "evidence-only" mode as an explicit release decision | **Delivered** (`app/services/early_watch_mode.py`, defaults to today's behaviour) |
| Suppressed observations preserved for analysis | **Delivered** (distinct reason code + durable marker) |
| Windows portability defect that broke the producer lock on Windows | **Fixed** (D-1) |
| Whether Early Watch meaningfully contributes to core memory pressure | **NOT ANSWERED** — requires production measurement (§4) |
| Isolating expensive Early Watch work | **NOT DONE** — deliberately: there is no measurement to justify it, and doing it on a guess would change production work for no proven reason |
| Actual sources of repeated Telegram alerts | **NOT ATTRIBUTED** — requires the delivered-evidence record from the host (§4) |
| Episode dedup / material-transition / notification-budget changes | **NOT CHANGED** — the existing governor (`notification_policy.should_emit`, cooldown + fingerprint) is already the dedup authority; changing it without the attribution above would tighten live alerting on a guess |
| AI invocation eligibility for unchanged episodes | **NOT CHANGED** — same reason |

## 2. Requirement → test → implementation → result

Tests: `tests/test_opip_early_watch_observability_v1.py` (13 passed, 1 skipped —
the skip is the cgroup probe on a platform without cgroup v2).

| Req | Test | Implementation | Result |
| --- | --- | --- | --- |
| Instrument duration/CPU/RSS/cgroup per phase | `test_ac_001_*` | `PhaseResourceSample` + `measure_phase()`; one `OPIP_PHASE_RESOURCE=<phase> …` line | PASS |
| An unmeasurable value must not be fabricated | `test_ac_001_an_unmeasurable_value_is_reported_as_unknown`, `test_ac_002_*` | `UNKNOWN` for any field whose input is absent | PASS |
| Instrumentation cannot change or break a workload | `test_ac_003_*` | opt-in `OPIP_PHASE_RESOURCE_TELEMETRY` (default off); a raising phase still emits; a broken emitter cannot propagate | PASS |
| Evidence-only mode is explicit and fail-closed | `test_ac_004_*` | `OPIP_EARLY_WATCH_MODE=active\|evidence_only`; any other value resolves to `active` | PASS |
| Only the automatic EARLY_MOVER family is affected | `test_ac_005_*` | check sits at the single `send_early_mover_update` boundary, **after** the eligibility and policy gates; `chief_alert_notifier`, `qualified_alert_outbox`, `telegram_command_center`, `emergency_alert_notifier` never consult the mode | PASS |

### D-1 — real portability defect, fixed in production code

`CaptureProcessLock.acquire()` asserted the POSIX link/ownership/mode invariant
unconditionally. Windows has no POSIX permission bits in `st_mode` (it
synthesizes `0o666`), so `st_mode & 0o022` is always non-zero and **every** lock
file was rejected with `PermissionError` — the producer lock could not be
acquired at all on Windows. The regular-file check still applies everywhere; the
link/ownership/mode check now applies only where its inputs exist (`os.name ==
"posix"`). On Windows the equivalent protection remains the reparse/symlink
rejection already present, and the production runtime is Linux, where the full
check always applies — so **Linux coverage is unchanged**.

Evidence: `tests/test_opip_r4_b2_shadow_cadence.py` went from 2 failed / 30
passed to **32 passed, 1 skipped**.

## 3. Behavioural impact and safety statement

1. **Production behaviour that changes: none by default.**
   `OPIP_PHASE_RESOURCE_TELEMETRY` is unset and `OPIP_EARLY_WATCH_MODE` resolves
   to `active`, which is exactly today's behaviour. Both are release decisions the
   owner makes explicitly, not consequences of merging this.
2. **Trading authority, risk, qualification: unchanged.** No threshold, size,
   R:R minimum or admission rule is touched, and no alert family other than the
   automatic Early Watch one can see the new switch.
3. **Durable evidence.** Additive only. The suppression record gains a new reason
   code; nothing existing is renamed or removed, and rejected/suppressed
   observations keep being recorded.
4. **Portability.** The D-1 fix changes a check from "always asserts" to
   "asserts where the inputs exist". It weakens nothing on Linux (the production
   runtime) and makes Windows behave as designed.

## 4. What remains unverified, and the read-only procedure

This workstation has no SSH path to the trading droplet and no log export. These
are production measurements, not code defects:

```bash
# 1. Enable measurement (release decision), then read one cycle's phases.
#    OPIP_PHASE_RESOURCE_TELEMETRY=1 in the core service environment.
sudo grep -E 'OPIP_PHASE_RESOURCE=' /var/log/ohm-unified-cycle.log | tail -n 200
#    Compare EARLY_WATCH rss_bytes/duration_ms against the protection phases.
#    cgroup_memory_peak_bytes is the container peak, not the phase's own peak.

# 2. Which alert family is actually repeating?
#    Read the delivered/suppressed evidence the service already writes
#    (telegram_delivery records every emit, suppression and not-eligible with a
#    reason code); group by alert_family and reason.

# 3. Is Early Watch cadence contributing to overruns?
sudo grep -c 'OPIP_UNIFIED_CYCLE_PHASE=EARLY_WATCH' /var/log/ohm-unified-cycle.log
```

Only after (1)-(3) can "isolate expensive Early Watch work" or "change the alert
budget" be justified with evidence. Until then, changing them would be exactly
the kind of unproven tightening this mission forbids.

## 5. Rollback

No new service, secret, volume or cron entry. Both switches are environment-only,
so rollback is unsetting them; the code that reads them is inert when they are
absent.

| A suppressed notification stays auditable | `test_ac_006_*` | reason `EARLY_WATCH_EVIDENCE_ONLY` (distinct from `NOTIFICATION_POLICY` and `ALERT_NOT_ELIGIBLE`) + `OPIP_EARLY_WATCH_SUPPRESSED=` marker | PASS |
