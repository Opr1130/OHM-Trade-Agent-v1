# O'Pip — production-readiness verification and decision (post #515 / #516 / #517 / #571)

Contract: `ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1`. Nothing here deploys, restarts,
enables or mutates anything.

## Decision

**NO-GO for deployment today — blocked on evidence and one owner decision, not on code.**

| # | What is missing | Who/how it closes |
| --- | --- | --- |
| 1 | Production row count, writer start-up observed on the host, host RAM/swap/disk | Run `tools/opip_readiness_bundle.sh` once (§6) and return the output |
| 2 | Owner decision: the writer container is capped at **0.15 CPU** and the deploy waits **60 s** for it to be healthy (§3). Either confirm the row count is below the threshold in §3, or authorise raising the writer's CPU/health budget (a production-config change) | Owner, after #1 |
| 3 | Owner acceptance that the 2,700 s cycle budget may skip advisory phases and report `DEGRADED` (§5) | Owner |

Everything already verified is not reopened: interruption safety, bounded memory,
indexed hydration and rollback are proven (§2).

## 1. Measurement-tool defects (fixed in this PR)

| Defect | Fix | Proof |
| --- | --- | --- |
| **P0-A** `shutil.copyfile` omitted committed rows living only in uncheckpointed WAL frames | `snapshot_database()` uses SQLite's online backup API; source opened `mode=ro`, never written or checkpointed; counts taken inside the same read transaction; `integrity_check` and per-table count equality required; destination removed and `SnapshotError` raised otherwise; **no file-copy fallback exists** (the module no longer imports `shutil`) | `test_opip_probe_snapshot_v1.py` AC-001..003. A mutation check (swapping the backup for a raw copy) fails 5 of the tests |
| **P0-B** `CREATE INDEX IF NOT EXISTS` could time a no-op | Plain `CREATE INDEX`; a time is reported only for an index the command created; an all-present store exits `NO_INDEX_CREATED`; `--prepare` drops target indexes **inside the snapshot only**; probe index SQL is pinned equal to production's | AC-004 (missing / present / mixed / `--prepare` / drift guard) |
| Phases conflated | `measure` reports schema initialisation, each hydration and the remaining init separately; index creation is its own command | AC-005 |

**Were the #571 numbers affected?** No. Those stores were built by the pre-#516 code and
closed by its writer (`close()` checkpoints, so the WAL was empty), and they carried
none of the new indexes (so every `CREATE INDEX` was a real build). The new tests make
that provable instead of assumed. The measurements stand.

## 2. Consolidated verification

| Area | Evidence |
| --- | --- |
| **#515 interruption** | Real SIGKILL/SIGTERM against a committing canonical writer (`test_opip_release_gates_v1.py` AC-001/002): integrity ok, events == idempotency keys, no sequence gap, every acknowledged event durable, ownership released. New here: **non-canonical** registries and JSONL — SIGKILL and SIGTERM mid `save_json_atomic` leave one complete version and a released lock; a torn JSONL tail is repaired (`test_opip_noncanonical_interruption_v1.py`) |
| **#516/#571 scale and rollback** | Peak RSS flat at ~43 MB from 100k to 1M rows (pre-#516: 299 MB → 2,652 MB); indexed hydration (3,000 rows: 0.79 s vs 179.5 s as merged); `submit()` rolls back on non-`Exception` interruption (fails without the guard) |
| **#517 defaults** | Telemetry off and unwired; Early Watch `active` unless exactly `evidence_only`; no deploy asset sets either flag |
| **F11** | Not touched by any of #515/#516/#517/#571 or this PR. The union of non-test, non-doc files changed is `run_cycle.py`, `kraken_transport.py` (cache bounds), `canonical/{schema,writer}.py`, `events/storage.py`, Early Watch/telemetry modules, `capture_feature_bus_shadow.py` (lock portability), `deploy/{cron.d,remote}` (bound/diagnostics), and the probe. No F11 module appears |
| **Trading authority** | No order-placement, modification, cancellation or confirmation path was added or widened; the diff of `kraken_transport.py` and `run_cycle.py` contains no order endpoint or execution change; the cycle budget gates only `EVENT_INTELLIGENCE`, `LEARNING`, `TARGET_SPINE`, `TRADEVISOR_ADVISORY` |
| **Learning / Committee / Early Watch** | Nothing enables them; no new environment variable is set anywhere in `deploy/` or compose |
| **Canonical evidence** | No data loss or duplicate acknowledgement under kill; a mid-transaction interruption can no longer be acknowledged as already recorded |
| **Mandatory gates** | `atdd scope`, `test` (focused + mandatory safety), Bandit, Semgrep, secret scan on the exact PR head (see the PR) |

## 3. Production resources — verified from the repository; host values still UNVERIFIED

Host access was not available (no droplet tools in the connected gateway), so the
following are read from the committed deploy configuration, **not** measured on the host:

| Item | Value (source) |
| --- | --- |
| Writer container memory | `mem_limit: 128m`, `memswap_limit: 128m` (compose; `ohm-deploy` asserts 134217728) |
| Writer container CPU | **`cpus: "0.15"`** |
| Writer health check | `start_period 20s`, `interval 15s`, `timeout 5s`, `retries 3` |
| Deploy wait | `wait_writer_health`: 30 × 2 s = **60 s** to report `healthy`, else the deploy fails |
| Start-up order | `CanonicalWriterServer.__init__` builds `CanonicalWriter` (schema, **indexes, hydration**) *before* the socket listens, so the container cannot be healthy until hydration finishes |

**What that implies (estimate, one stated assumption).** Measured on a full core the
fixed writer hydrates ~3,400 rows/s (1M rows in 322 s). Assuming throughput scales
with the CPU quota, 0.15 CPU gives ~510 rows/s, so a 60 s window (minus a few seconds
of interpreter start) covers roughly **25–28k events** — and the one-time index build
(6.4 s per million at a full core, ~43 s at 0.15) is inside the same window. The
pre-#516 writer's memory limit was reached at roughly **37k** rows
(128 MB − 32 MB baseline, ~2.6 KB/row), so the old code failed on memory at about the
size where the new code starts to fail on time.

**Threshold used for the decision:** GO on this axis only if the host shows fewer than
about **20,000 total events** (bundle §5 `EVENT_TOTAL_UPPER_BOUND`, ~25% margin; **never**
`next_local_sequence`, which a supported restore resets to 1 without deleting history, so
it is only a current-epoch position), *or* the observed
`StartedAt → "ready"` interval in bundle §4 is well under 60 s. The CPU-scaling
assumption is exactly what bundle §4 replaces with an observation. If the count is
higher, the fix is a production-config decision (raise the writer's CPU and/or the
health wait) — not made here, and not a code defect.

## 4. Non-canonical write paths under interrupted execution

| Path | Mechanism | Verdict |
| --- | --- | --- |
| Active-trade and pending-setup registries (`registry_io.save_json_atomic`) | temp file in the same directory, `fsync`, `os.replace`; kernel-released `flock` | **Safe** — proven with real SIGKILL/SIGTERM |
| Bounded JSONL evidence (`opip/storage/bounded_jsonl`) | append + `fsync`; explicit `repair_truncated_tail` | **Safe by design** — torn tail repaired (tested); a SIGKILL can lose the *unflushed* last row, not earlier ones |
| `jsonl_retention` rewrite | temp + `fsync` + `os.replace` | Safe (static) |
| Other writers (dozens of modules write files) | not individually proven | **Not claimed.** They are advisory/evidence paths; none holds protection state. Listed as open risk R-3 |

## 5. The 2,700-second cycle budget

- It gates only `EVENT_INTELLIGENCE`, `LEARNING`, `TARGET_SPINE`, `TRADEVISOR_ADVISORY`;
  protection phases are never gated.
- It is far inside the 3,540 s in-container and 3,600 s host bounds, so it can only
  pre-empt a tail that is already running long.
- A cycle that skips a phase returns `DEGRADED` (never `SUCCESS`) and prints an
  explicit per-phase skip marker; an unparseable value falls back to the default, `0`
  disables it. Whether 2,700 s is the *right* number depends on actual cycle durations
  (bundle §6): if normal cycles take well under a minute the budget never fires.
- **Not owner-approved.** Recommendation: accept as shipped (it only ever removes
  advisory work from an already-overlong cycle and reports it honestly), after bundle
  §6 shows the real duration distribution. Authorisation is required either way.

## 6. The one-paste read-only bundle

`tools/opip_readiness_bundle.sh` — run `sudo bash opip_readiness_bundle.sh | tee /tmp/opip-readiness.txt`.
It reports: host RAM/swap/CPU/disk; the writer's limits, restarts, OOM flag and
health configuration; one `docker stats` sample; the **observed writer start-up
interval**; database/WAL sizes; `history_epoch` and `next_local_sequence` labelled as a current-epoch
position; `EVENT_TOTAL_UPPER_BOUND`, the sum over every epoch of the per-epoch maximum
`local_sequence` (O(epochs × log n) index seeks, never below the true total, or
`UNVERIFIED` — which keeps this axis NO-GO); one niced, 20-second-boxed count of the
opportunity event type; cycle bound, recent statuses, orphans (`ps`) and the cycle-lock
holder read from `/proc/locks`; the deployed health deadline; and the safety-relevant
environment variables. It copies no database, scans nothing unbounded, and **invokes no
other repository script**: the learning diagnostic it originally called reaches
`registry_io.load_json`, which can quarantine a malformed registry with `os.replace`, and
filtering a callee's output cannot undo its side effect. Tests (probe `AC-007`, and
`ATDD-OPIP-READINESS-BUNDLE-FIX-V1` AC-001/AC-002) run the bundle against a booby-trapped
callee, a corrupt registry and a multi-epoch store and compare bytes, names and mtimes.
SQLite's volatile `-shm` index is the one file excluded from that comparison.

## 7. Open risks (exact conditions)

| ID | Condition | Closes when |
| --- | --- | --- |
| R-1 | Writer start-up may exceed the 60 s deploy wait at 0.15 CPU (§3) | Bundle §4/§5 shows count < threshold or an observed start-up well under 60 s; otherwise owner approves a resource/health change |
| R-2 | Host RAM/swap/disk, observed memory, and orphans are unmeasured | Bundle §1–§3, §7 |
| R-3 | dozens of other file writers not individually interruption-tested | Accepted as advisory, or a follow-up audit is requested |
| R-4 | 2,700 s budget not owner-approved | Owner decision |
| R-5 | The CPU-throttling estimate in §3 is an assumption | Replaced by the observation in bundle §4 |

## 8. Rollback

| Trigger | Procedure | Evidence required |
| --- | --- | --- |
| Writer not `healthy` within the deploy wait, or `OOMKilled=true`, or `RestartCount` rising | `ohm-deploy` already fails the deploy and keeps the retained release; redeploy the previous release | `docker inspect` health and start-up timestamps; writer logs |
| Any canonical-writer test fails on the candidate | Do not deploy; revert #571 and this PR | CI at the exact head |
| First open slower than the health deadline on the host | Revert, or (owner) raise writer CPU/health budget | Bundle §4 |
| Unexpected alert behaviour | Unset `OPIP_EARLY_WATCH_MODE` / telemetry flags | Delivered-evidence record |
| Cycle reports `DEGRADED` repeatedly | Set `OPIP_UNIFIED_CYCLE_BUDGET_SECONDS=0` to disable the gate | Cycle log phase-skip markers |

The new indexes are additive: an older build ignores them and no data is migrated.
