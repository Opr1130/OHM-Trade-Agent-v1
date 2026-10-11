# O'Pip R5-A Continuous Opportunity Engine - conformance and evidence record

Increment: `ATDD-R5-A-continuous-opportunity-engine`
Authority granted by this document: `NONE`
Grants trading, deployment, release, activation or allocation authority: `NONE`
Production posture after this increment: `EVIDENCE_SHADOW` active, `TARGET_PAPER` `BLOCKED`, Compose `OPIP_PAPER_V2_MODE=off`

This record accompanies the increment's scope contract
(`docs/atdd/scope-contracts/ATDD-R5-A-continuous-opportunity-engine.md`) and its
acceptance tests
(`tests/test_opip_r5_a_continuous_opportunity_engine.py`). It states what was
implemented, what was reused, what remains open, what was measured and what is
explicitly unproven. It is a status and evidence artifact; it changes no runtime
and ratifies no numeric policy.

## Scope amendment this increment carries

The adopted body's own delivery order places `R5-A Continuous Market Eye` before
`R5-B Horizon Intelligence`. This increment delivers the R5-A attention,
scheduling and observation slice **and** the HORIZON thesis contract that the
adopted section 4 attaches to the same opportunity lineage, because R5-A cannot
demonstrate "classify supported horizons and abstain otherwise" without a thesis
record to abstain on. That forward move is recorded here as a proposed R5-A
scope amendment; it creates no allocation, exit or management authority, leaves
R5-B's allocation and exit policy unimplemented and does not ratify `OQ-03`.

## Component traceability

| Component | Contract | Implementation | Status |
| --- | --- | --- | --- |
| F1 observation coverage and availability | `SC-01`, `ME-01`, `ME-06`, `ME-07`, `DC-01` | `app/opip/events/coverage.py` | CODE_COMPLETE, SHADOW, not wired |
| EYE attention | `SC-02`, `ME-02`-`ME-04` | `app/opip/events/attention.py` | CODE_COMPLETE, SHADOW, inert without owner policy |
| F2 feature bus | `SC-02`, `SC-03` | `app/opip/contracts/features.py`, `app/opip/features/engine.py` | REUSE_AS_IS, unmodified |
| SCHED unified coordinator | `SC-03`, `ME-04`-`ME-07` | `app/opip/streaming/coordinator.py` | CODE_COMPLETE, SHADOW, not wired |
| F3 IGNITION detector | `SC-04` | `app/opip/detectors/ignition.py` | REUSE_AS_IS, unmodified, purity re-proven |
| F4 opportunity lifecycle | `SC-05`, `LC-01`-`LC-03` | `app/opip/contracts/opportunity_phases.py` over `contracts/opportunity.py` | EXTEND: phase projection, episode opening, requalification link |
| HORIZON thesis | `SC-04`, `HZ-01`, `HZ-02` | `app/opip/contracts/thesis.py` | CODE_COMPLETE, abstains until `OQ-03` is ratified |
| Composition engine | `SC-01`-`SC-03` | `app/opip/events/opportunity_engine.py` | CODE_COMPLETE, `R5A_ENGINE_MODE = off`, no environment read |

Reuse verdicts in the R5-0 register are unchanged: `F1`, `F2`, `F4`, `F5`, `F6`,
`F7`, `F8`, `F9`, `F10`, `F12` remain `EXTEND_REQUIRED`, `F3` and `F11` remain
`CONFORMANT_BASELINE`, and `R5-A` remains `PROPOSED_NOT_FROZEN` until the OWNER
assigns an owner (`OQ-01`) and ratifies the Market Eye budgets (`OQ-04`).

## What the engine does

`ContinuousOpportunityEngine.observe(...)` reconciles F1 coverage evidence
against the consumed source watermark, evaluates EYE attention with the applied
policy, records one trigger per evaluation and submits at most one deduplicated
evaluation request to the unified coordinator.
`ContinuousOpportunityEngine.evaluate_due(...)` serves a bounded,
priority-ordered batch: it consumes the canonical F2 snapshot it is handed,
refuses a snapshot that is missing, belongs to another instrument or carries a
different cut-off, calls the frozen F3 detector, opens the F4 episode for a
`DORMANT -> IGNITION` claim, and classifies a HORIZON thesis in shadow evidence.
Nothing is persisted, nothing is wired into a runtime root, and the engine's mode
constant is `off`.

## Owner inputs still open (activation blockers)

- `OQ-01` owner assignment for every R5 requirement and gate: `UNASSIGNED`.
- `OQ-03` horizon boundaries and expected holding ranges: unratified, so the
  classifier abstains to `UNKNOWN` with `POLICY_NOT_CONFIGURED` under the shipped
  policy and no band is invented.
- `OQ-04` Market Eye workload, freshness, queue and latency budgets: unratified,
  so `AttentionPolicy` and `CoordinatorLimits` ship no numbers, `configured` stays
  `False` and the engine cannot advance attention or admit work.
- `OQ-08` R4 closure and the legacy paper coexistence window: not established by
  this increment; R4 behaviour is untouched and unassessed here.
- `OQ-09` review-critical persisted facts and learning dispositions: unchanged;
  this increment persists nothing.
- Activation of the engine, of `shadow` mode or of any release profile remains an
  owner release act. The code offers no activation path: `R5A_ENGINE_MODE` is the
  literal `off`, the module reads no environment variable, and a constructor call
  requesting another mode raises.

## Engineering load scenario (labelled, synthetic, not an accepted budget)

Scenario `r5a-engineering-burst-v1`, deterministic and replayed twice with
identical results: 40 instruments x 4 discovery requests staged one per
evaluation interval, plus one protection request, evaluated with
`queue_capacity=64`, `per_instrument_budget_per_cycle=2`,
`protection_reserved_slots=2`, `reconciliation_reserved_slots=2` and a batch of
20.

Measured with the test fixture (assertions in
`test_ac_015_an_engineering_load_scenario_is_bounded_and_protection_first`):

| Measurement | Value |
| --- | --- |
| Submitted requests | 161 (160 discovery, 1 protection) |
| Submission outcomes | 65 admitted + 96 refused + 0 coalesced + 0 stale-rejected = 161 |
| Admitted | 65 (64 of them still queued at that instant, 1 later evicted) |
| Refused at admission | 96 (discovery submissions refused while the queue was saturated) |
| Evicted under backpressure | 1 (an admitted discovery request expelled to make room for protection) |
| Visible shed total | 97 = 96 refusals + 1 eviction |
| Preempted to admit protection | 1 (equal to the eviction count) |
| Backlog high-water mark | 64 (equal to the declared capacity, never exceeded) |
| Served in one cycle | 20 |
| Logical delay p50 / p95 (evaluation intervals) | 4.0 / 4.0 (the labelled budget) |
| Protection served first | yes (`PROTECTION` precedes `DISCOVERY` in the batch) |
| F11 interference | none observed; protection is admitted ahead of discovery |

Interpretation limits: these are in-process, synthetic numbers with no wall-clock
profiling, no database, no network and no production market data. They prove
bounded work, fairness, protection precedence, deterministic replay and
population reconciliation. They do **not** establish production latency,
throughput or capacity: runtime effectiveness is `NOT_PROVEN`.

### Load accounting is two disjoint populations, not one

An earlier draft of this record reported "161 submitted, 65 admitted, 97 shed" as
one partition. Those numbers do not sum (`65 + 97 = 162`) because they mix two
different event populations, and that statement is corrected here:

* **submission outcomes** - exactly one per `submit` call, so
  `submitted == admitted + coalesced + refused_backpressure + rejected_stale`
  (`161 == 65 + 0 + 96 + 0`);
* **backlog releases** - exactly one per admitted request that left the queue, so
  `admitted == backlog + served_from_queue + expired + evicted_backpressure +
  terminal_failures_from_queue` (`65 == 64 + 0 + 0 + 1 + 0`).

An eviction is a *release* of work that an earlier submission had already had
admitted, so it is not a submission outcome. `dropped_backpressure` is the sum of
the two back-pressure populations (`97 = 96 refused + 1 evicted`) and
`submitted == admitted + dropped_backpressure` is therefore **false** here by
exactly one eviction. `CoordinatorTelemetry` cannot be constructed unless both
populations reconcile, so an unreconciled report is refused rather than published;
`RequestAccounting` exposes both partitions in one record. Regression coverage:
`test_ac_006_the_burst_shed_total_separates_refusals_from_evictions`,
`test_ac_006_both_accounting_populations_reconcile_across_every_path`,
`test_ac_006_an_accounting_record_that_does_not_reconcile_is_refused` and
`test_ac_006_the_empty_record_reconciles`.

### Scheduler-versus-event baseline comparison (structural, `NOT_PROVEN`)

No retained R5-A observation exists to compare against: the engine is unwired,
`R5A_ENGINE_MODE = off`, nothing is persisted, and the repository holds no
production coverage/latency sample for this component. A real-world
scheduler-versus-event comparison is therefore **not feasible** in this
increment.

Instead the harness
`test_ac_015_a_labelled_scheduler_baseline_harness_compares_like_for_like` defines
the comparison and tests it on identical synthetic inputs: one universe
(`IV:000`-`IV:007`), one set of evidence cut-offs (4 intervals) and one
eligibility rule are shared by both arms.

| Arm | Detector evaluations | Ordinal of the trigger identity's first evaluation | Deferred work |
| --- | --- | --- | --- |
| Pre-R5-A grid baseline (every eligible instrument, every interval) | 32 | 8 | 0 |
| R5-A event path (reserved-priority trigger + bounded batch of 1) | 1 | 1 | 31 queued, 0 refused |

Both arms draw on the identical eligible identity set, the event path reaches the
trigger identity's first evaluation with strictly less detector work, and the
unselected discovery stays queued and counted rather than dropped. The harness
proves structure, coverage parity and deferral only; the real-world latency,
throughput and freshness improvement of the event path remains explicitly
`NOT_PROVEN`, and no production result is claimed or fabricated.

### Restart durability and ownership (what actually survives a crash)

Recovery in this slice is **replay-derived, not persisted**. Verified facts:

- No R5-A module owns a store. The module scan in
  `test_ac_013_restart_recovery_is_replay_only_and_owns_no_store` proves that none
  of `thesis.py`, `opportunity_phases.py`, `coverage.py`, `attention.py`,
  `coordinator.py` or `opportunity_engine.py` contains a database, file,
  serialization or writer-client token, so this increment creates no second ledger
  and no second storage authority.
- The coordinator is an in-process ledger. After a crash its queued, served and
  terminal sets are gone. A cold coordinator rebuilt from the same requests
  reproduces the same evaluation identities, request ids, admission outcome and
  telemetry, because identity is content-derived from (instrument version, cut-off,
  policy version). That is deterministic replay, not recovery from durable state.
- Duplicate convergence **within one process** is structural: an identity that was
  served cannot be re-admitted and a repeat `complete` returns `False`. **Across a
  restart** the cold ledger is unaware of the committed served set, so suppression
  then rests on identity equality plus the canonical writer's identity-keyed
  dedupe. R5-A neither implements nor calls that writer, so this record does not
  claim cross-process suppression as proven here.
- A completion or failure reported for an identity that was never queued in this
  process (the restart-before-rebuild path) is still exactly one transition, and it
  is counted separately as `served_unmatched` / `terminal_failures_unmatched` so it
  cannot silently inflate the queue partition.

Unresolved durable-ledger decisions are recorded here rather than assumed: whether
R5-A needs a durable request ledger at all; who owns it and what its schema is;
whether episode recovery is replayed from canonical evidence or restored from a
ledger; the request and episode retention window; and how a cross-process duplicate
is suppressed when the canonical writer is unavailable. All of these are owner
decisions (`OQ-04` and the open durable-ledger item); none is implemented,
defaulted or invented by this increment. Until they are ratified, R5-A remains an
in-process, replay-proven shadow component and **not** a restart-durable service.

## Status vocabulary for this increment

- `ARCH_COVERAGE`: covered for the deliverable parts of R5-A and for the HORIZON
  thesis contract; `PARTIAL` for R5-A as a whole because `OQ-01`, `OQ-03` and
  `OQ-04` remain owner decisions, and because the adopted section 3 acceptance
  evidence cannot be produced without a ratified budget.
- `CODE_COMPLETE`: yes for the eight components above.
- `TARGET_WIRED`: no. No runtime root imports an R5-A module and no Compose,
  workflow, service, timer or environment key is added or changed.
- `RUNTIME_ACTIVE`: no. `R5A_ENGINE_MODE = off`, no environment variable can
  change it, and the engine refuses any other mode.
- `PROD_PROVEN`: no. No production evidence exists.
- `USER_VALUE_PROVEN`: no. `AC-01`-`AC-03` and `AC-12` of the adopted section 17
  require frozen numeric budgets; they remain `INSUFFICIENT_EVIDENCE`.

## Test-scoping adjustment (widen-only, no assertion removed)

Two frozen R3 isolation suites enumerate every module under `app/` and assert that
no module outside their own allowlist mentions their vocabulary
(`opip.detectors`, `detectors.ignition`, `contracts.detector` in
`tests/test_opip_r3_f3_ignition_detector.py`; `contracts.opportunity`,
`opportunity_lifecycle`, `apply_claim` in
`tests/test_opip_r3_f4_opportunity_lifecycle.py`). R5-A legitimately consumes
those frozen contracts: the composition engine calls the frozen
`ignition.evaluate(...)` and reads the F3 claim contract, and the phase
projection maps onto the existing F4 enums and opens the canonical episode.

This increment therefore widens each allowlist by exactly the two authorized R5-A
consumers (`app/opip/events/opportunity_engine.py`,
`app/opip/contracts/opportunity_phases.py`) and changes nothing else in those
suites. The precedent is already in the F4 suite, which widened its allowlist for
the R3 F4 persistence increment in the same way. No assertion is deleted or
relaxed: the Compose literal check, the forbidden import roots, the detector
purity checks, the run-cycle isolation checks and the authority-token checks all
remain, and acceptance test
`test_ac_010_the_r3_isolation_allowlists_were_widened_without_weakening_them`
re-proves that both suites kept them.

## Final pre-merge architecture review

Reviewed on the exact content head against `docs/architecture/v1.5.0/ARCHITECTURE.md`
(the adopted body, SHA256 pinned in the scope contract), the inherited R3/R4
contracts and the frozen R5-A ATDD scope.

| Area | Verdict | Basis |
| --- | --- | --- |
| F1 observation coverage and availability | CONFORMANT (shadow, unwired) | coverage reconciles against the consumed watermark, visibility is receipt time, missing/invalid/late/skewed rows are recorded and never repaired, and a delayed event recovers only at a later reconciled cut-off |
| EYE attention | CONFORMANT, inert without owner policy | closed state and overlay sets, one-rank hysteresis with a dwell guard, explicit recovery, and no detector or lifecycle vocabulary |
| F2 feature bus | REUSE_AS_IS | unmodified; the engine consumes the canonical snapshot it is handed and refuses a missing, foreign or differently cut-off one instead of synthesizing it |
| SCHED coordinator | CONFORMANT (shadow, unwired) | one identity for stream and scheduler; reserved protection/reconciliation capacity ahead of discovery; visible shedding with reconciled counters; per-instrument fairness; bounded retries |
| F3 IGNITION detector | REUSE_AS_IS, purity re-proven | the detector receives only the canonical snapshot, its prior state and the explicit cut-off; attention never reaches it; replay of identical inputs is identical |
| F4 opportunity lifecycle | EXTEND, research-only additions | episodes are claim-bound and idempotent, a wait is bounded, an expiry never resumes, requalification links a new decision without resetting the clock, and every R5-A phase maps onto an existing F4 enum |
| HORIZON thesis | CONFORMANT, abstains | unknown policy, missing holding evidence, contradiction, unbanded horizon, thin liquidity or thin structure abstain to research-only `UNKNOWN`; revisions are immutable and a failed tactical thesis is never relabelled as a long-term investment |

Cross-cutting checks:

- **Replay** - no new module holds a clock or a random source and two identical runs
  produce identical cycle reports, telemetry and thesis evidence (`AC-012`).
- **Concurrency** - the ledger is single-threaded by construction and owns no
  thread, lock, async runtime or worker, so this increment adds no concurrency
  surface and no cross-process coordination (`AC-010`, `AC-013`).
- **Availability timing** - expectations derive from the consumed watermark, so an
  unconsumed window is never usable evidence and a late event cannot be treated as
  available earlier than it was received (`AC-001`, `AC-012`).
- **Overload behaviour** - saturation refuses or evicts honestly with reconciled
  counts instead of silently, and protection is served first (`AC-006`).
- **F11 independence** - the reserved protection and reconciliation classes outrank
  every discovery class, protection is never the evicted class, and no R5-A path can
  starve F11 protection (`AC-006`).
- **Disabled-mode isolation** - `R5A_ENGINE_MODE` is the literal `off`, no module
  reads the environment, no `OPIP_` key is introduced, a constructor requesting
  another mode raises, and no runtime root imports an R5-A module (`AC-010`,
  `AC-011`).
- **Zero trading authority** - no order, sizing, reservation, allocation or
  credential surface exists in the new modules, and `SELECTED`/`RESERVED` are
  refused as unreachable (`AC-010`).

Residual items, recorded and unresolved rather than treated as defects of this
slice: the durable-ledger decisions above, `OQ-01`/`OQ-03`/`OQ-04`/`OQ-08`
ownership and numeric inputs, and the absence of production evidence. None can be
closed by code in this increment, and none blocks the merge of a feature-disabled,
unwired slice.

## Verification performed for this increment (final head)

Exact-SHA traceability: the review fixes recorded in this document are commit
`d48fd0deb34adbc8459bc5644d1a45b813065838` ("R5-A: reconcile the load accounting
and prove restart recovery is replay-only"), followed by the whitespace-only
follow-up commit that contains this record and changes no behaviour. The pull
request records the exact branch head SHA after all three commits.

- Focused acceptance suite: `tests/test_opip_r5_a_continuous_opportunity_engine.py`
  (15 criteria, 93 test cases) passes. The increment added 12 cases in this review:
  three accounting regressions, one restart-durability regression and one labelled
  baseline harness.
- ATDD scope gate, CI-exact form:
  `python tests/atdd_scope.py --increment ATDD-R5-A-continuous-opportunity-engine --git-base origin/main`
  -> `PASS`.
- Regression batch over the touched families (R3 F3 ignition, R3 F4 lifecycle and
  persistence, R3 F5/F6/F7, R5-0 architecture adoption and contract conformance,
  R5-A): **371 passed, 0 failed**.
- Repo-wide acceptance suite: **953 passed, 4 skipped, 0 failed**. The 29
  deployment/learning/analytics/committee/platform suites that execute `bash`
  directly were deselected for that local run only, because this Windows shell
  cannot fork bash (`0xC0000142`); those files are untouched by this increment, are
  green on `main` and in CI, and execute normally on ubuntu-latest.
- `python -m compileall -q app` -> clean.
- Release architecture gate: `ARCHITECTURE_GATE=PASS`,
  `PROFILE=EVIDENCE_SHADOW`, `PAPER_V2=OFF`, `FUNDED_AUTHORITY=ABSENT`,
  `PROTECTION=INDEPENDENT`.
- Adversarial mutation probe on five critical invariants (one-step attention
  progression, late-arrival blocking, the horizon structural gate, the engine
  snapshot cut-off check and coordinator priority ordering): 5 detected, 0
  survivors. The first probe found the horizon structural gate under-covered
  because the milestone-count gate refused the same fixture for a different
  reason; the POSITION test now also covers claimed milestones without real
  structural support, and that mutation is detected. The accounting invariants
  added in this review are self-covering: deleting either partition check makes
  `test_ac_006_an_accounting_record_that_does_not_reconcile_is_refused` fail.
- CI on the exact head: `test`, `atdd scope`, `release architecture gate`,
  `release security gate (Bandit)`, `semgrep/ci` and `gitleaks` are the applicable
  gates and are recorded on the pull request for this head. The advisory
  `ruff`/`bandit`/`pip-audit` job still fails with pre-existing repo-wide findings
  that are **not** in the new modules, and the external `CircleCI` check still
  fails because the project ships no `.circleci/config.yml`; both fail identically
  on `main` and neither is a required gate for this increment.

- Release architecture gate: `ARCHITECTURE_GATE=PASS`,
  `PROFILE=EVIDENCE_SHADOW`, `PAPER_V2=OFF`, `FUNDED_AUTHORITY=ABSENT`,
  `PROTECTION=INDEPENDENT`.
- Adversarial mutation probe on five critical invariants (one-step attention
  progression, late-arrival blocking, the horizon structural gate, the engine
  snapshot cut-off check and coordinator priority ordering): 5 detected, 0
  survivors. The first probe found the horizon structural gate under-covered
  because the milestone-count gate refused the same fixture for a different
  reason; the POSITION test now also covers claimed milestones without real
  structural support, and that mutation is detected.
- CI on this exact head: `test` pass, `atdd scope` pass, `release architecture
  gate` pass, `release security gate (Bandit)` pass, `semgrep/ci` pass,
  `gitleaks` pass. The advisory `ruff`/`bandit`/`pip-audit` job still fails with
  **86 pre-existing repo-wide findings, none of them in the new modules**, and
  the external `CircleCI` check still fails because the project has no
  `.circleci/config.yml`; both fail identically on `main` and neither is a
  required gate for this increment.


## Remaining risks and honest limits

- The attention, coordinator and coverage modules are pure and unwired, so no
  production latency, backlog or freshness behaviour is demonstrated.
- An earlier draft of this record published the engineering burst as a single shed
  partition (`161 submitted, 65 admitted, 97 shed`). That arithmetic mixed
  submission refusals with a backlog eviction; the two-population partition above
  is the corrected statement and is now enforced by `CoordinatorTelemetry`, so an
  unreconciled report cannot be published silently again.
- Restart recovery is replay-derived only. No durable request or episode ledger
  exists, so cross-process duplicate suppression is not proven here and the
  durable-ledger decisions remain open owner inputs.
- No retained production observation exists, so the scheduler-versus-event latency
  improvement is `NOT_PROVEN`; only the structural harness result is reproducible.
- The F4 episode and requalification records have no durable store; persistence,
  canonical writing and consumer registration remain later governed work.
- The requalification link, the superseded phase and the phase projection are
  research-only; the canonical writer's consumer census has not been moved.
- Detector `DORMANT -> IGNITION` semantics are the inherited frozen policy; the
  R5-A path adds no detector claim, no confidence change and no second family.
- The horizon classifier deliberately abstains under the shipped policy, so no
  supported POSITION thesis is produced in this increment.

