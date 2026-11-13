# O'Pip — release-gate evidence for the merged resource remediation (#515 / #516 / #517)

Contract: `ATDD-OPIP-RELEASE-GATES-V1`. Tests: `tests/test_opip_release_gates_v1.py`.
Tool: `tools/opip_canonical_scale_probe.py`. **Nothing here deploys, enables or
activates anything.** Merged SHAs: #515 `8425018d`, #516 `0139cd33`, #517 `2789d421`.

## Result in one table

| Gate | Verdict | Basis |
| --- | --- | --- |
| #515 interruption safety | **CLOSED** (one hardening added) | Real SIGKILL and SIGTERM against a writing process; store consistent, no acknowledged event lost |
| #516 index build and memory | **CLOSED for the measured range; production row count still unknown** | Measured to 1,000,000 rows; a **P0 startup defect was found and fixed** |
| #517 default-off and isolation | **CLOSED** with one correction to the readiness report | Defaults proven unchanged; telemetry module is unwired |

## 1. #515 — interruption safety

**Architecture fact that bounds the risk.** The cycle's signal handler raises
`_CycleTerminated(BaseException)` inside the *cycle* process. The canonical writer is
a separate service (`app/opip/canonical/server.py`); the cycle owns no writing
canonical connection (`test_ac_003_the_cycle_never_constructs_a_writing_canonical_writer`
pins the only three modules that construct one). A TERM to the cycle therefore cannot
interrupt a canonical commit.

**Proof anyway, with real processes** (the writer is killed while committing in a loop):

| Scenario | Evidence |
| --- | --- |
| SIGKILL, three rounds on one store | `integrity_check=ok`, `events == idempotency_keys`, sequence `1..N` with no gap, `meta.next_local_sequence == N+1`, every acknowledged key present, a new writer takes ownership (OS advisory lock, no stale owner) and history continues |
| SIGTERM through the real handler | exit 0, `STATUS=TERMINATED` + `TERMINATED_REASON=SIGTERM`, **no** `COMPLETED_AT`, no `STATUS=SUCCESS`, same store invariants, store re-openable |
| SIGTERM mid cycle with a pre-existing log | prior `SUCCESS`/`COMPLETED_AT` lines are untouched; the terminal lines are appended after them |

**Why the commit is atomic:** `PRAGMA journal_mode=WAL`, `synchronous=FULL`, and the
event row, idempotency key, projections and watermark are written in one
`BEGIN IMMEDIATE … COMMIT`. A killed process leaves an uncommitted WAL tail that SQLite
discards on the next open.

**One real gap found and closed.** Between `BEGIN IMMEDIATE` and `COMMIT`, an
exception that is not an `Exception` (the orderly-shutdown exception, `KeyboardInterrupt`)
skipped `submit()`'s `rollback()`. The half-written event stayed visible to the writer's
*own* idempotency lookup, so a resubmission would have been acknowledged as "already
recorded" for an event that was never committed, and any later `commit()` on that
connection would have made it durable. `submit()` now rolls back on `BaseException` and
re-raises unchanged. `test_ac_003_an_interruption_mid_transaction…` fails without the
guard (`in_transaction` stays `True`) and passes with it. In production the path is
unreachable today (see the architecture fact); the guard removes the dependency on that.

**Residual (not claimed closed):** the cycle process also writes non-canonical stores
(JSON/registry files, lifecycle tables). Their interruption behaviour is outside the
canonical-write gate and was not re-proved here.

## 2. #516 — scale measurement

Method: synthetic store of *valid* opportunity-lifecycle transitions (2 rows per even
episode, 1 per odd), built on the pre-#516 schema, then opened by the pre-#516 code, by
the merged #516 code, and by the fixed code. 2 vCPU / 8 GB sandbox, one run per cell
(`tools/opip_canonical_scale_probe.py`). Times are wall-clock; memory is peak RSS.

### 2a. The defect the measurement found (P0, in #516 as merged)

| Rows | pre-#516 open | **#516 as merged** open | fixed open |
| --- | --- | --- | --- |
| 3,000 | 0.61 s | **179.5 s** | 0.79 s |
| 6,000 | 1.21 s | **>250 s (aborted)** | 1.55 s |
| 10,000 | — | **>300 s (aborted)** | — |

Cause: once bounded retention (2,048 entries) had released an episode, re-deriving it
re-read the *entire* event type and filtered in Python. Hydration itself re-derives
released episodes, so startup was quadratic in history. #516's tests never exceeded 30
rows. **Deploying #516 as merged would have made the canonical writer fail to start
within any sane health deadline once history passed ~2k episodes.**

Fix: additive `idx_events_episode_identity`, created by the writer (the generic schema is
pinned free of F4 vocabulary), **partial on the one event type** so `json_extract` is
never evaluated on any other row, and an indexed per-episode read with the type inlined
as a literal (SQLite only uses a partial index whose predicate the query states
literally). A first draft of the index that was not type-partial failed CI on two
existing tests that store non-JSON rows of other types; that is why it is typed.
Regression tests: `test_ac_004_*`.

### 2b. Fixed code against the pre-#516 code

| Rows | pre-#516 open / peak RSS | fixed open / peak RSS | RSS ratio |
| --- | --- | --- | --- |
| 100,000 | 20.5 s / 299 MB | 29.1 s / 42.6 MB | 7× lower |
| 500,000 | 107.4 s / 1,344 MB | 148.1 s / 42.8 MB | 31× lower |
| 1,000,000 | 220.2 s / 2,652 MB | 322.2 s / 42.6 MB | 62× lower |

- **Memory is now flat** (~43 MB) at every size; before it grew linearly (~2.6 KB per row).
- **Time is linear and ~1.45× the old time.** The cost of bounded memory is re-deriving
  released episodes (p50 0.41 ms, p95 0.55 ms, max 1.2 ms per re-derivation, independent
  of store size). Writer start-up is therefore **minutes at ≥ 500k rows**, in both old
  and new code. That is a pre-existing scaling property, now documented, not introduced
  here — but the production row count decides whether it matters (see §5).

### 2c. One-time index build at first open after deploy

| Rows | `idx_events_episode_identity` | `…role_result_identity` | `…di_request_state` | total | DB growth |
| --- | --- | --- | --- | --- | --- |
| 100,000 | 0.50 s | 0.05 s | 0.06 s | 0.6 s | 4.8 MB |
| 1,000,000 | 3.45 s | 2.31 s | 0.65 s | **6.4 s** | 48.9 MB |

The build runs inside the writer's own schema initialisation before it accepts work, so
the "write-lock duration" is these seconds of a writer that is not yet serving. Peak
RSS during the build is 19 MB. Nothing is rewritten; rollback needs no migration (an
older build ignores the extra indexes).

Note: the 100k–1M measurements were taken with the first-draft predicate
(`episode_id IS NOT NULL`); on this dataset every row is of the indexed type, so the
typed predicate indexes exactly the same rows. A 6,000-row re-run with the final code
opens in 1.45 s (re-derivation p50 0.41 ms).

### 2d. FIFO versus LRU — the intended policy

The merged code is **FIFO by first insertion**, deliberately: a refresh does not move an
entry (`_BoundedProjection.__setitem__` comment: "release order stays deterministic").
`test_ac_005_*` pins that. Because a released episode re-derives to the **identical**
state (`test_ac_005_a_release_changes_latency_never_the_answer`), the choice changes
*latency only*: a hot episode that FIFO releases costs one ~0.4 ms indexed re-derivation
on its next touch. LRU would avoid that for hot identities at the price of
order-dependent eviction and a reorder on every read. **Confirmed intended: FIFO.**
Switching to LRU is a possible later optimisation and is out of scope; it is not
needed for correctness or for the measured memory bound.

## 3. #517 — default-off and isolation

| Control | Default | Evidence |
| --- | --- | --- |
| `OPIP_PHASE_RESOURCE_TELEMETRY` | **off** | `telemetry_enabled()` is false unless set; **and no production module imports `phase_resources`**, so setting it today changes nothing (pinned: the test fails if it is later wired, forcing re-verification) |
| `OPIP_EARLY_WATCH_MODE` | **`active` = pre-#517 alerting** | unset, empty and every unrecognised value resolve to `active`; only `evidence_only` withholds, only at the `EARLY_MOVER` automatic delivery, after the eligibility and policy gates, with a distinct reason and a durable marker |
| Deploy assets | neither flag appears in `deploy/`, cron or compose | `test_ac_006_no_deploy_asset_enables_either_new_flag` |

**Correction to the earlier readiness report:** §2/§5 said to "enable
`OPIP_PHASE_RESOURCE_TELEMETRY=1`, then compare per-phase cost". That would produce no
data: the module is delivered but not called. Measuring Early Watch's memory cost needs a
separate, approved change that wires it.

**Bundled cleanup isolated.** The Windows lock-portability change
(`CaptureProcessLock`) is the only runtime edit outside Early Watch. It is guarded by
`os.name == "posix"`, so on the Linux runtime the full invariant still applies;
`test_ac_007_*` proves group-writable and hard-linked lock files are still refused.

## 4. Runtime changes in the release-gates PR (the whole list)

1. `writer.py`: one additive writer-created partial index; per-episode re-derivation
   uses it; `submit()` rolls back on a non-`Exception` interruption. (`schema.py` is
   unchanged.)
3. No change to `run_cycle.py`, `deploy/`, cron, compose, trading, learning, Early Watch,
   Committee or F11.

## 5. What is still unverified (cannot be closed from here)

- **Production canonical row counts.** The measured range stops at 1M rows. If production
  is far below ~100k rows, start-up is seconds; if it is in the millions, start-up is
  minutes and the deploy health deadline must be checked against §2b first.
- The droplet's RAM and the writer container's memory limit, to compare against §2b.
- §5 host diagnosis in the readiness report (orphans, cycle duration vs the 2700 s budget).
- Non-canonical stores' behaviour under interruption (§1 residual).

## 6. Rollback conditions

- **Revert** the release-gates PR if any canonical-writer test fails on the deploy
  candidate, or if first open exceeds the health deadline measured against §2b.
- The new indexes are additive: an older build ignores them; no data migration to undo.
- `evidence_only` / telemetry: unset the variables.
- #515 entry and image remain compatible in both directions (readiness report §4).
