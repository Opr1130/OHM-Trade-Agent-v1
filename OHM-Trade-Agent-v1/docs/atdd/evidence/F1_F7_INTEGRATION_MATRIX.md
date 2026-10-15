# F1-F7 continuous advisor pipeline: source-of-truth and integration matrix

Increment: `ATDD-R5-M02-source-of-truth-audit`.
Authority: audit and record only. This document implements nothing and authorizes
nothing. It records what exists, where it is wired, whether it runs and whether
any of it has production proof, for the pipeline the census calls the continuous
advisor path: F1 observation, F2 Feature Bus, F3 IGNITION, F4 candidate and
horizon lifecycle, F5 feasibility, F6 calibrated forecast, F7 selection,
canonical decision evidence and F10 read-model consumption.

The owner paths, entry points, test-module names and canonical facts recorded
here are **probed by** `tests/test_opip_r5_m02_integration_matrix.py`. A row whose
owner path or entry point stops existing, or whose wiring sentence stops being
true on disk, fails that test. Nothing here restates
`docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`; the register is referenced,
not copied, so the two cannot drift silently.

## 1. Four axes, never collapsed

Repository existence is not target wiring. Target wiring is not runtime activity.
Runtime activity is not production proof. Every row records all four, and the
`proof=` axis is `NOT_PROVEN` for every row in this file without exception.

```text
AXES:
  repo    : EXISTS
  wiring  : WIRED_INTO_DORMANT_ENGINE | WIRED_INTO_SCAN_JOB | NO_PRODUCTION_SNAPSHOT_SOURCE | NO_APP_RUNTIME_CALLER | NOT_THE_ADMISSION_AUTHORITY | LEGACY_AUTHORITATIVE | NOT_EXPOSED_BY_THE_READ_PLANE | NO_CANONICAL_PRODUCER | FILE_WAL_ON_TRADING_HOST | ABSENT_DECLARED | TWO_ENTRY_POINTS_CONSISTENT | UNPROVEN_BETWEEN_HALVES
  runtime : INERT | ACTIVE_WHEN_SCAN_RUNS | NOT_RUNNING
  proof   : NOT_PROVEN
```

`repo=EXISTS` means the named authority path is present in the repository, and the
acceptance test requires it. This axis deliberately has only one value: every row
below names a real module, so a row that could not find one would be a broken
citation rather than an audit finding. Absence claims - the five declared-absent
read-model modules and the two declared-absent symbols - are asserted directly
against the census's own declared-absence block instead, because that is where the
repository records what does not exist.

`runtime=ACTIVE_WHEN_SCAN_RUNS` means the call site sits on the scheduled job's
path, which is verifiable here. Whether that job executed on the host is a
deployment fact and is **not** verified by this increment, which is why it is not
recorded as runtime activity, let alone as production proof.

## 2. Classification vocabulary

```text
CLASSES:
  IMPLEMENTED_AND_VERIFIED
  IMPLEMENTED_NOT_INTEGRATED
  MISSING_CODE
  MISSING_CANONICAL_EVIDENCE
  BLOCKED_BY_OWNER_DECISION
  NOT_YET_TESTED
```

`IMPLEMENTED_AND_VERIFIED` means the code exists and its own acceptance tests
prove the stated behaviour. It does **not** mean production-proven, and it does
not mean integrated with the next stage. `IMPLEMENTED_NOT_INTEGRATED` means the
code exists and is tested but no production caller reaches it.
`BLOCKED_BY_OWNER_DECISION` means the missing thing is an authority or contract
decision this mission may not take. `NOT_YET_TESTED` means the behaviour is
unproven and names the increment that must prove it.

## 3. Rows

```text
ROW: F1 | F1-OBSERVATION-COVERAGE-AND-FRESHNESS | class=IMPLEMENTED_AND_VERIFIED | repo=EXISTS | wiring=WIRED_INTO_DORMANT_ENGINE | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/market/observations.py | entry=app.opip.events.coverage.reconcile_coverage | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
ROW: F1 | F1-ATTENTION-AND-WORK-ADMISSION | class=IMPLEMENTED_AND_VERIFIED | repo=EXISTS | wiring=WIRED_INTO_DORMANT_ENGINE | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/events/attention.py | entry=app.opip.events.attention.advance_attention | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
ROW: F2 | F2-INCREMENTAL-FEATURE-SNAPSHOT | class=IMPLEMENTED_NOT_INTEGRATED | repo=EXISTS | wiring=NO_PRODUCTION_SNAPSHOT_SOURCE | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/features/engine.py | entry=app.opip.contracts.features.FeatureSnapshot | evidence=OHM-Trade-Agent-v1/tests/test_opip_feature_bus_pr3_integrity.py
ROW: F3 | F3-PURE-IGNITION-EVALUATION | class=IMPLEMENTED_AND_VERIFIED | repo=EXISTS | wiring=WIRED_INTO_DORMANT_ENGINE | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/detectors/ignition.py | entry=app.opip.detectors.ignition.evaluate | evidence=OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
ROW: F4 | F4-EPISODE-OPENING-AND-VALIDITY | class=IMPLEMENTED_AND_VERIFIED | repo=EXISTS | wiring=TWO_ENTRY_POINTS_CONSISTENT | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py | entry=app.opip.opportunity_lifecycle.apply_claim | evidence=OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
ROW: F4 | F4-EPISODE-IDENTITY-IS-PINNED-BY-THE-POLICY | class=IMPLEMENTED_AND_VERIFIED | repo=EXISTS | wiring=TWO_ENTRY_POINTS_CONSISTENT | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py | entry=app.opip.contracts.opportunity.OpportunityLifecyclePolicy | evidence=OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
ROW: F4 | F4-HORIZON-THESIS-CLASSIFICATION | class=IMPLEMENTED_AND_VERIFIED | repo=EXISTS | wiring=WIRED_INTO_DORMANT_ENGINE | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py | entry=app.opip.contracts.thesis.classify_horizon | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
ROW: F5 | F5-FEASIBILITY-VETO-AND-ABSTENTION | class=IMPLEMENTED_NOT_INTEGRATED | repo=EXISTS | wiring=NO_APP_RUNTIME_CALLER | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/feasibility.py | entry=app.opip.feasibility.evaluate_feasibility | evidence=OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
ROW: F5 | F5-CANONICAL-FEASIBILITY-EVIDENCE-CAPTURE | class=IMPLEMENTED_NOT_INTEGRATED | repo=EXISTS | wiring=WIRED_INTO_SCAN_JOB | runtime=ACTIVE_WHEN_SCAN_RUNS | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/jobs/capture_feasibility_evidence_shadow.py | entry=app.opip.contracts.feasibility_evidence.feasibility_evidence_from_market_snapshot | evidence=OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_evidence_reader.py
ROW: F6 | F6-CALIBRATED-FORECAST-AND-UNCERTAINTY | class=IMPLEMENTED_NOT_INTEGRATED | repo=EXISTS | wiring=NO_APP_RUNTIME_CALLER | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/forecast.py | entry=app.opip.forecast.evaluate_forecast | evidence=OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
ROW: F7 | F7-CAPITAL-AWARE-SELECTION-AND-CASH | class=IMPLEMENTED_NOT_INTEGRATED | repo=EXISTS | wiring=NOT_THE_ADMISSION_AUTHORITY | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/portfolio_selector.py | entry=app.opip.portfolio_selector.select_portfolio | evidence=OHM-Trade-Agent-v1/tests/test_opip_r3_f7_economic_portfolio_selector.py
ROW: F7 | F7-PRODUCTION-ADMISSION-AUTHORITY | class=BLOCKED_BY_OWNER_DECISION | repo=EXISTS | wiring=LEGACY_AUTHORITATIVE | runtime=ACTIVE_WHEN_SCAN_RUNS | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/services/capital_efficiency_ranking.py | entry=app.services.capital_efficiency_ranking.rank_capital_efficiency | evidence=OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
ROW: CANONICAL | CANONICAL-DECISION-CANDIDATE-AND-DETECTOR-EVIDENCE | class=MISSING_CANONICAL_EVIDENCE | repo=EXISTS | wiring=NO_CANONICAL_PRODUCER | runtime=NOT_RUNNING | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/canonical/writer_service.py | entry=app.opip.canonical.schema.SCHEMA_VERSION | evidence=OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
ROW: CANONICAL | CANONICAL-QUALIFICATION-AND-SCREENING-EVIDENCE-STORE | class=IMPLEMENTED_AND_VERIFIED | repo=EXISTS | wiring=WIRED_INTO_SCAN_JOB | runtime=ACTIVE_WHEN_SCAN_RUNS | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/decision/store.py | entry=app.opip.decision.store.append_screening_evaluations | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py
ROW: CANONICAL | CANONICAL-FEATURE-BUS-EVIDENCE | class=IMPLEMENTED_NOT_INTEGRATED | repo=EXISTS | wiring=NOT_EXPOSED_BY_THE_READ_PLANE | runtime=NOT_RUNNING | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/contracts/events.py | entry=app.opip.contracts.events.FEATURE_BUS_EVENT_TYPES | evidence=OHM-Trade-Agent-v1/tests/test_opip_feature_bus_pr3_exact_head.py
ROW: CANONICAL | CANONICAL-F5-FEASIBILITY-EVIDENCE-STREAM | class=IMPLEMENTED_NOT_INTEGRATED | repo=EXISTS | wiring=NOT_EXPOSED_BY_THE_READ_PLANE | runtime=NOT_RUNNING | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/fev_evidence_event.py | entry=app.opip.fev_evidence_event.FEASIBILITY_EVIDENCE_RECORDED | evidence=OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_evidence_event.py
ROW: F10 | F10-READ-MODEL-CONSUMPTION-OF-PIPELINE-FACTS | class=MISSING_CANONICAL_EVIDENCE | repo=EXISTS | wiring=NOT_EXPOSED_BY_THE_READ_PLANE | runtime=NOT_RUNNING | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py | entry=app.opip.cockpit.semantic.READ_MODEL_FACTS | evidence=OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
ROW: F10 | F10-DECISION-TRACE-READ-MODEL | class=MISSING_CODE | repo=EXISTS | wiring=ABSENT_DECLARED | runtime=NOT_RUNNING | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/cockpit/semantic.py | entry=app.opip.cockpit.semantic.SemanticReadService | evidence=OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_SOURCE_CENSUS.md
ROW: SEAM | SEAM-ENGINE-EPISODE-ACCEPTED-BY-THE-SPINE | class=NOT_YET_TESTED | repo=EXISTS | wiring=UNPROVEN_BETWEEN_HALVES | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/events/opportunity_engine.py | entry=app.opip.events.opportunity_engine.ContinuousOpportunityEngine.evaluate_due | evidence=OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_composition.py
ROW: SEAM | SEAM-CLAIM-OBJECT-NOT-EXPOSED-BY-THE-ENGINE | class=BLOCKED_BY_OWNER_DECISION | repo=EXISTS | wiring=UNPROVEN_BETWEEN_HALVES | runtime=INERT | proof=NOT_PROVEN | owner=OHM-Trade-Agent-v1/app/opip/events/opportunity_engine.py | entry=app.opip.events.opportunity_engine.EvaluationOutcome | evidence=OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md
```

## 4. Movement this mission makes

The two `SEAM` rows are the only rows this mission moves.
`ATDD-R5-M02-pipeline-seam-integration` closes
`SEAM-ENGINE-EPISODE-ACCEPTED-BY-THE-SPINE` by driving the real continuous engine
into the real F5/F6/F7 spine with one snapshot and asserting that one claim, one
episode and one thesis come out of it. It does **not** move
`SEAM-CLAIM-OBJECT-NOT-EXPOSED-BY-THE-ENGINE`, which needs an owner decision, and
it does not move a single `F1`-`F7` row: no wiring changed, so no wiring claim may
change.

### 4.1 A finding this audit first recorded wrongly, and corrected

While reading the two F4 episode-opening entry points it appeared that
`app/opip/opportunity_lifecycle._create_episode` derived the episode identity from
`policy.episode_schema_version` while
`app/opip/contracts/opportunity_phases.episode_from_claim` omitted it and took the
module default. That reads as a latent fork, and it was briefly recorded as one.

It is not one. `OpportunityLifecyclePolicy` fails closed on all three of its
version tokens: its `__post_init__` refuses any `episode_schema_version`,
`lifecycle_version` or `policy_version` that is not the ratified one, so a
lifecycle policy carrying an unratified schema version cannot be constructed at
all. Both entry points therefore resolve to the same ratified token by
construction. An executed probe confirms the consequence: for one admissible
IGNITION claim the two paths produce the identical episode identity
`OPEP:084327d4571400a96c1bbe79fd0de0e5` with all twelve recorded identity fields
equal, and an attempt to build a policy with `opportunity-episode-v2` raises
`OpportunityContractError` rather than forking anything.

The corrected finding is the stronger one, and it is recorded as the row
`F4-EPISODE-IDENTITY-IS-PINNED-BY-THE-POLICY`: consistency here is a guarantee of
the policy's fail-closed validation, not a coincidence of the current constants.
No owner decision is requested for it. The test in
`ATDD-R5-M02-pipeline-seam-integration` asserts the guarantee - the agreement, the
refusal and the identity - so the correction cannot silently reverse.

## 5. What this matrix refuses to say

- It does not say any stage is production-proven. The `proof=` axis is
  `NOT_PROVEN` on every row, and the acceptance test asserts exactly that.
- It does not say the target spine is the production authority. It is dormant,
  and `F7-PRODUCTION-ADMISSION-AUTHORITY` records that the legacy service path
  still is.
- It does not call an unwired module a defect. `IMPLEMENTED_NOT_INTEGRATED` is
  the approved posture of `ATDD-R4-B1-runtime-integration-dormant`, which states
  that activating a real source is an owner decision and out of scope.
- It does not treat an empty read model as a healthy one, or a missing canonical
  family as a zero.

## 6. Totals

```text
TOTALS:
  ROWS 20
  IMPLEMENTED_AND_VERIFIED 7
  IMPLEMENTED_NOT_INTEGRATED 7
  MISSING_CODE 1
  MISSING_CANONICAL_EVIDENCE 2
  BLOCKED_BY_OWNER_DECISION 2
  NOT_YET_TESTED 1
  PROOF_NOT_PROVEN 20
```

Every one of the six classes is used, so no class is a decorative row in a
vocabulary table. The acceptance test recomputes these six totals from the rows
and fails if the document and its rows disagree.


```

