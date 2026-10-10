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
| Admitted | 65 |
| Shed under backpressure | 97 (all discovery) |
| Preempted to admit protection | 1 |
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

## Verification performed for this increment

- Focused acceptance suite: `tests/test_opip_r5_a_continuous_opportunity_engine.py`
  (15 criteria, 81 test cases) passes.
- ATDD scope gate: `python tests/atdd_scope.py --increment ATDD-R5-A-continuous-opportunity-engine --git-base origin/main`.
- Repository contract suites that this increment could affect: ATDD scope control,
  R5-0 conformance, release profiles and Compose literal checks.
- Static checks: `python -m compileall -q app`, plus the import, purity, clock and
  authority scans asserted inside the acceptance suite itself.

## Remaining risks and honest limits

- The attention, coordinator and coverage modules are pure and unwired, so no
  production latency, backlog or freshness behaviour is demonstrated.
- The F4 episode and requalification records have no durable store; persistence,
  canonical writing and consumer registration remain later governed work.
- The requalification link, the superseded phase and the phase projection are
  research-only; the canonical writer's consumer census has not been moved.
- Detector `DORMANT -> IGNITION` semantics are the inherited frozen policy; the
  R5-A path adds no detector claim, no confidence change and no second family.
- The horizon classifier deliberately abstains under the shipped policy, so no
  supported POSITION thesis is produced in this increment.

