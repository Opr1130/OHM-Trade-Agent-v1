# O'Pip — resource-remediation release-readiness report

Consolidated report for the resource-remediation mission. **Nothing was merged,
deployed or activated.** This is the final release authorization gate.

| PR | Branch | Work package |
| --- | --- | --- |
| [#515](https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/515) | `fix/opip-resource-lifecycle-remediation-v1` | A — scheduler and resource lifecycle |
| [#516](https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/516) | `fix/opip-canonical-writer-scale-v1` | B — canonical writer and data scalability |
| [#517](https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/517) | `fix/opip-early-watch-observability-v1` | C — Early Watch and notification reliability · D — platform-test cleanup |

PR #514 (`fix/opip-runtime-authority-failclosed-v1`) and the unrelated in-flight
work in `wt-opip-pointer-gov` were left untouched: `git status` there is
byte-identical to the pre-mission state.

## 1. Defect disposition

| ID | Sev | Defect | Disposition | Evidence |
| --- | --- | --- | --- | --- |
| A-1 | P0 | The runtime bound was enforced only on the host launcher; an over-bound cycle orphaned its container-side process holding `/app/data/.unified_cycle.lock`, so every following minute logged `LOCK_CONTENTION_SKIPPED` while the host lock looked free. | **Fixed** — inner `timeout -s TERM -k 5s 3540` in the container, strictly inside the host bound | #515 · `test_ac_001_*` |
| A-2 | P0 | A missing `timeout` binary degraded **open** into an unguarded `exec` — the exact unbounded run above. | **Fixed** — both layers fail closed with `STATUS=CONTAINMENT_UNAVAILABLE` and exit 97/98; the release observer accepts only `SUCCESS\|DEGRADED` | #515 · `test_ac_001_the_fail_closed_marker_cannot_be_read_as_a_completion` |
| A-3 | P0 | A bound-killed cycle left no truthful terminal evidence (default `SIGTERM` skips every `finally`). | **Fixed** — `_CycleTerminated(BaseException)`, `STATUS=TERMINATED`, no fabricated `COMPLETED_AT` | #515 · `test_ac_003_*`, `test_ac_004_*` |
| A-4 | P1 | The release controller and the diagnostics derived the bound from the **whole** entry, so a comment quoting the long-form flags became the authority; `grep -m1` also carried a second match into the trailing-integer read. | **Fixed** — comment/assignment stripping, exactly-one-match, and the match must sit on the `python -m app.jobs.run_cycle` line | #515 · `test_ac_010_*` |
| A-5 | P1 | Non-critical workloads were bounded only by the container's hard bound, so a slow tail could starve the next protection pass. | **Fixed** — cycle budget (default 2700 s, `0` disables, unparseable never means unlimited); protection phases never gated; a skip is `DEGRADED` + an explicit marker | #515 · `test_ac_011_*` |
| A-6 | P1 | Overdue / orphaned cycles were not identifiable from the receipt. | **Fixed** — `unified_cycle_lock_state`, `holder_elapsed_seconds`, `holder_overdue`, `in_flight`, `orphan_suspected`, from the existing read-only probe and log tail | #515 · `test_ac_012_*` |
| A-7 | P2 | Transport caching was bounded by freshness only, never by footprint. | **Fixed** (carried from the in-flight branch) — entry + byte caps, LRU, telemetry | #515 · `test_ac_006/007/008_*` |
| B-1 | P1 | Every projection hydration/refresh ran one `fetchall()` over the whole per-type history; three hydrations run in `__init__`. | **Fixed** — `_iter_event_rows()` pages with an exclusive cursor over `idx_events_type_cursor` | #516 · `test_ac_001_*`, `test_ac_002_*` |
| B-2 | P1 | `_role_result_idempotency_by_id` retained one entry per role result ever recorded. | **Fixed** — indexed anti-join on `idx_events_role_result_identity`; no resident map | #516 · `test_ac_005_*` |
| B-3 | P1 | The opportunity projection retained every episode ever recorded, closed ones included. | **Fixed** — bounded retention + re-derivation from the episode's own rows | #516 · `test_ac_003_*`, `test_ac_004_*`, `test_ac_007` |
| B-4 | P1 | The request projection retained every request id ever seen. | **Fixed** — bounded retention + indexed `idx_events_di_request_state` | #516 · `test_ac_004_a_released_request_state_is_re_derived_not_defaulted` |
| B-5 | P1 | Dead-letter retention read the whole JSONL file **twice** to bound it. | **Fixed** — seek-based `read_tail_lines()` | #516 · `test_ac_006_*` |
| D-1 | P2 | `CaptureProcessLock` asserted the POSIX link/ownership/mode invariant unconditionally; Windows synthesizes `0o666`, so **every** lock file was rejected and the producer lock was unusable. | **Fixed** — regular-file check everywhere, POSIX-only checks where their inputs exist; Linux coverage unchanged | #517 · `test_opip_r4_b2_shadow_cadence.py` 2 failed → **32 passed** |
| C-1 | P1 | Early Watch reported no per-phase cost, so "is it the memory problem?" was unanswerable. | **Instrumented** (opt-in, default off) | #517 · `test_ac_001/002/003_*` |

## 2. Explicitly NOT resolved — with the reason

| Item | Why it is open | What would close it |
| --- | --- | --- |
| Does Early Watch meaningfully contribute to core memory pressure? | Requires per-phase measurement **on the host**. This workstation has no SSH path to the droplet and no log export. | Enable `OPIP_PHASE_RESOURCE_TELEMETRY=1` (release decision, #517), then compare `EARLY_WATCH rss_bytes/duration_ms` with the protection phases (§5). |
| Isolate expensive Early Watch work | No measurement justifies it. Isolating on a guess changes production work for no proven reason. | The measurement above, if — and only if — Early Watch dominates. |
| Actual sources of repeated Telegram alerts | Requires reading the host's delivered/suppressed evidence grouped by `alert_family`/`reason`. | §5 step 2. |
| New episode-dedup / material-transition / notification-budget behaviour | The existing governor (`notification_policy.should_emit`: cooldown + fingerprint) **is** the dedup authority; changing it without attribution would tighten live alerting on a guess. | The attribution above. |
| AI-invocation work for unchanged episodes | Same: an optimisation of a path whose cost is currently unmeasured. | The attribution above. |
| Production liveness (is an orphan running *right now*?) | Read-only host observation; nothing in the code path can answer it from here. | §5 step 2-3. |

These are **production measurements, not code defects**. Nothing above is closed by
a guess, and no threshold, risk default or alert-suppression default was changed to
make a number look better.

## 3. Required CI status at the exact PR heads

| Suite | Result | Where |
| --- | --- | --- |
| `tests/test_opip_resource_lifecycle_remediation_v1.py` | 28 passed, 2 skipped (bash-executed; run in Linux CI) | #515 |
| `test_production_scheduler_v1`, `test_opip_unified_cycle_diagnostics_v1`, `test_opip_deploy_verify_budget_v1` | pass | #515 |
| `tests/test_opip_canonical_writer_scale_v1.py` | 12 passed | #516 |
| F4 persistence + lifecycle suites | 113 passed | #516 |
| Canonical writer PR2 + cursor read plan + store safety | 192 passed, 5 skipped | #516 |
| `tests/test_opip_early_watch_observability_v1.py` | 13 passed, 1 skipped (no cgroup v2 here) | #517 |
| `tests/test_opip_r4_b2_shadow_cadence.py` | 32 passed, 1 skipped (was 2 failed) | #517 |
| Mandatory safety suite (`--mode safety`, 32 cases) | 37 passed, 1 skipped — run on the #515 and #516 trees | #515, #516 |
| `python -m compileall -q app` | clean | all three |

**Local vs CI:** the two skipped cases in #515 and the bash/fork-dependent skips in
the deploy-budget suite execute real `bash`; this workstation has no usable `bash`
(the only `bash.exe` present is the WSL launcher stub, which cannot fork). They are
not suppressed — they run in Linux CI. Authoritative green status is CI at the exact
PR heads, which the owner should confirm before merging.

## 4. Rollback and deployment dependencies

- **No new daemon, scheduler, service, volume, secret, cron entry or config** is
  introduced by any PR. All three ship in the existing core image through the
  existing `/deploy` path.
- **#515** alone touches `deploy/`. Its entry and image are compatible both ways:
  *new entry + old image* still bounds the process (SIGKILL after 5 s) and only
  loses the extra evidence; *new image + old entry* behaves as before plus the
  cycle budget.
- **#516** adds two `CREATE INDEX IF NOT EXISTS` indexes — harmless to an older
  build, no migration on rollback, and no row, table, column or file is written,
  rewritten or deleted.
- **#517** is environment-only; rollback is unsetting `OPIP_PHASE_RESOURCE_TELEMETRY`
  / `OPIP_EARLY_WATCH_MODE`. The D-1 fix is strictly narrower on Windows and
  identical on Linux.
- **Merge order:** independent — A, B and C touch disjoint files.


## 5. Read-only production diagnosis (to be run before authorization)

```bash
# 1. Is the container-side bound actually installed?
sudo grep -n 'timeout' /etc/cron.d/ohm-unified-cycle
#    expect exactly ONE long-form and ONE short-form (-s TERM -k 5s) occurrence.

# 2. Is a cycle stuck right now, and is anything orphaned?
sudo bash <app-root>/deploy/remote/diagnose-opip-learning.sh | grep -E \
 'unified_cycle_(host_lock|lock_state|holder_elapsed_seconds|holder_overdue|in_flight|orphan_suspected|log_latest_status|log_age_seconds)'

# 3. Is anything inside the container still running a cycle?
sudo docker compose -f <app-root>/../docker-compose.yml exec -T ohm-trade-agent \
  sh -c 'ps -eo pid,etimes,args | grep -c "[r]un_cycle"'

# 4. How long do cycles actually take? (needed to judge the 2700 s budget)
sudo grep -E 'OPIP_UNIFIED_CYCLE_(STATUS|COMPLETED_AT|PHASE=)' \
  /var/log/ohm-unified-cycle.log | tail -n 200

# 5. (after enabling OPIP_PHASE_RESOURCE_TELEMETRY=1) per-phase resource cost
sudo grep -E 'OPIP_PHASE_RESOURCE=' /var/log/ohm-unified-cycle.log | tail -n 200
```

None of these starts, signals, restarts, locks or writes anything.

## 6. Final acceptance check

| Criterion | Status |
| --- | --- |
| All identified defects addressed or shown already correct | **Yes** — A-1…A-7, B-1…B-5, D-1 fixed; C-1/C-2 delivered; the five C items in §2 are open **with reasons**, not claimed |
| No confirmed unbounded memory-retention path remains | **Yes in the writer** (paged hydration + bounded retention + indexed identity lookups); the transport cache is footprint-bounded (A-7) |
| Scheduler timeout and release parsing are robust | **Yes** — in-container bound, fail-closed tooling, one authoritative prose-insensitive bound |
| Worker lifecycle and resource limits are enforced | **Yes** — orderly shutdown, truthful termination evidence, cycle budget. Host liveness still **UNVERIFIED** (§2/§5) |
| Canonical history can grow without proportional resident growth | **Yes** — proven by `test_ac_007_*` against a growing synthetic history |
| Early Watch notification behaviour is demonstrably controlled | **Controllable and auditable**; the *tuning* decision (dedup / budget / AI work) is open pending measurement (§2) |
| Mandatory CI passes at the exact PR heads | **Locally green**; CI confirmation at the PR heads is the owner's merge gate |
| Rollback and deployment dependencies documented | **Yes** (§4) |
| Outstanding production-only measurements clearly identified | **Yes** (§2) |

**Verdict: engineering work is complete for A, B, C-1, C-2 and D-1. The
measurement-dependent tuning of Early Watch remains open and is listed, not
guessed. The release is at the authorization gate — nothing has been merged,
deployed or activated.**


## 7. Blocking CI condition — `atdd scope` (owner-sequenced, not bypassed)

CI reports `atdd scope=FAILURE` on all three PRs. This is a **governance
prerequisite, not a code defect**, and it was deliberately not bypassed:

```
SCOPE_CHANGE_REQUIRED
changed file not mapped to an approved AC: .../app/jobs/run_cycle.py
... (one line per changed file) ...
```

`.github/workflows/pytest.yml` reads the single global pointer
`OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT` (currently
`ATDD-OPIP-V2-ADVISORY-PARITY-M01`) and requires every changed file to be mapped
to an approved AC of that increment. To go green, each PR needs:

1. a scope contract at `docs/atdd/scope-contracts/ATDD-<INCREMENT>.md` following
   the existing format (INCREMENT / OWNER-APPROVED INTENT / ARCHITECTURE
   REFERENCES / APPROVED ACCEPTANCE CRITERIA / EXPLICITLY OUT OF SCOPE / FROZEN
   BOUNDARIES / ACCEPTANCE TEST TRACEABILITY / IMPLEMENTATION MAP / DEFERRED
   DISCOVERIES / UNAPPROVED SCOPE CHANGES), mapping each changed file to an AC;
2. `docs/atdd/ACTIVE_INCREMENT` moved to that increment.

**Why it was not simply done here.** `ACTIVE_INCREMENT` is a *single repository-wide
pointer*. With #513, #514, #515, #516 and #517 all open, three of them cannot each
own it without conflicting, and moving it displaces the increment that the other
in-flight PRs are currently validated against. That is a sequencing decision for
the owner (merge order across the open PR set), and `AGENTS.md` principle 20 makes
it a high-impact governance change rather than a routine edit.

**Remedy:** add the contract and move the pointer in whichever PR the owner merges
first, then rebase the others onto it (contracts are additive per-PR files; only
the pointer line conflicts).

The other non-green checks are explicitly **advisory**
(`ruff (advisory)`, `bandit (advisory)`, `gitleaks (advisory)`) and do not gate
the merge; `test` is green on #516 (the other two were still running when this
report was written). The substantive suites that matter for these changes are
green locally (§3).

| C-2 | P1 | Automatic `EARLY_MOVER` alerting had no explicit, auditable release switch separate from subscribed `COMMAND_WATCH` and critical safety alerts. | **Delivered** — `OPIP_EARLY_WATCH_MODE=active\|evidence_only`, fail-closed, single boundary, distinct suppression reason | #517 · `test_ac_004/005/006_*` |
