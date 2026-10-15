# R5 Mission 03 coverage matrix: capital integrity, paper lifecycle and F11 independence

Increment: `ATDD-R5-M03-portfolio-execution-audit`.
Authority: audit and record only. This document implements nothing and authorizes
nothing. It records, for every item Mission 03 asked to verify, whether the
repository already proves it, and if so which test node proves it.

The point of the exercise is to avoid manufacturing work. An item is recorded
`PROVEN` only when a named acceptance test already exists and asserts it. An item
with no such test is recorded honestly as `PARTIALLY_PROVEN` or `UNPROVEN`, and the
unproven items are exactly the ones the follower increments close.

Every citation in this file is machine-probed by
`tests/test_opip_r5_m03_coverage_matrix.py`: the cited file must exist and the
cited node must appear in it, so a renamed or deleted test breaks this document
rather than silently weakening it.

## 1. Vocabulary

```text
CLASSES:
  PROVEN
  UNPROVEN
  BLOCKED_BY_OWNER_DECISION

WORKSTREAMS:
  A   capital integrity (SC-06, SC-07, SC-08, SC-09)
  B   isolated paper lifecycle (SC-08, SC-09, SC-11)
  C   F11 failure and independence (SC-11, SC-12)
```

`PROVEN` requires a cited node. `UNPROVEN` means no test asserts it.
`BLOCKED_BY_OWNER_DECISION` means the item depends on an unratified value, so no
increment may prove it.

A fourth value, `PARTIALLY_PROVEN`, was declared in this matrix's first revision and
was removed by `ATDD-R5-M03-f11-failure-independence-matrix` once a closer reading of
the existing harnesses showed the three items that used it were in fact fully
proven. A vocabulary member with no members is decorative, which `AC-301` forbids,
so the value was deleted rather than kept for appearance. The correction is recorded
in section 8.

## 2. Workstream A - capital integrity

```text
ITEM: A1 | separate-account-and-quote-currency-domains | workstream=A | class=PROVEN | contract=SC-06 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_001_there_is_exactly_one_cash_book_in_the_application
ITEM: A2 | available-versus-reserved-versus-deployed-capital | workstream=A | class=PROVEN | contract=SC-06 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_002_deployed_basis_and_marked_value_stay_outside_cash
ITEM: A3 | exact-consumption-of-reservation-identity | workstream=A | class=PROVEN | contract=SC-07 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py::test_ac_001_a_planned_reservation_becomes_exactly_one_canonical_reservation
ITEM: A4 | atomic-commitment | workstream=A | class=PROVEN | contract=SC-07 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_plan_commits_atomically_against_the_version_it_was_built_for
ITEM: A5 | candidate-panel-current-positions-and-cash-alternative | workstream=A | class=PROVEN | contract=SC-08 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py::test_ac_001_the_plan_carries_every_frozen_record_key
ITEM: A6 | rotation-advantage-subject-to-existing-policy | workstream=A | class=PROVEN | contract=SC-09 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_a_proven_advantage_records_the_policy_that_licensed_it
ITEM: A7 | requalification-before-replacement | workstream=A | class=PROVEN | contract=SC-09 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py::test_ac_007_a_state_that_claims_an_unrequalified_entry_is_refused
ITEM: A8 | actual-realized-proceeds-before-reuse | workstream=A | class=PROVEN | contract=SC-09 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py::test_ac_007_partial_fills_reconcile_proceeds_and_residual_exposure
ITEM: A9 | position-sizing-subject-to-existing-risk-caps | workstream=A | class=PROVEN | contract=SC-06 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_004_a_sleeve_limit_can_bind_independently
ITEM: A10 | validity-and-expiry-of-capital-plans | workstream=A | class=PROVEN | contract=SC-08 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_plan_outside_its_declared_window_is_refused
ITEM: A11 | durable-recovery-and-idempotent-replay | workstream=A | class=PROVEN | contract=SC-07 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py::test_ac_008_a_restart_replays_the_same_reservation_once
ITEM: A12 | explicit-incomplete-evidence-and-unavailable-capital | workstream=A | class=PROVEN | contract=SC-06 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_a_stale_or_unsupported_account_state_abstains
```

## 3. Workstream B - isolated paper lifecycle

```text
ITEM: B1 | approved-intent | workstream=B | class=PROVEN | contract=SC-09 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt4_stop_crossed_commits_atomic_trigger_and_exact_exit_intent
ITEM: B2 | entry-reservation | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_002_a_planned_reservation_is_executed_by_the_seam_as_one_entry
ITEM: B3 | submitted-order | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt5_stop_full_exit_reconciles_and_releases_reservation
ITEM: B4 | no-fill | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_execution_bc2.py::test_every_terminal_attempt_state_releases_capacity
ITEM: B5 | partial-fill | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_execution_bc2.py::test_partial_fill_then_later_terminal_attempt_keeps_remainder_reserved
ITEM: B6 | full-fill | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt5_stop_full_exit_reconciles_and_releases_reservation
ITEM: B7 | open-position | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt1_fresh_fill_activates_the_committed_plan
ITEM: B8 | early-validation | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_pretrade_bc3.py::test_fetch_converts_a_kraken_read_failure_to_fail_closed
ITEM: B9 | hold-and-reduce | workstream=B | class=PROVEN | contract=SC-09 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt7_tp1_crossed_exits_the_configured_fraction
ITEM: B10 | profit-taking | workstream=B | class=PROVEN | contract=SC-09 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt11_tp2_closes_the_residual_after_a_partial_target
ITEM: B11 | time-exit | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt13_time_at_expiry_closes_the_full_remaining_exposure
ITEM: B12 | risk-exit | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_002_a_crossed_stop_outranks_the_target_and_the_expired_plan
ITEM: B13 | rotation | workstream=B | class=BLOCKED_BY_OWNER_DECISION | contract=SC-09 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_an_unratified_policy_abstains_before_anything_numeric
ITEM: B14 | pending-reconciliation | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt20_crash_after_the_flat_fill_completes_reconciliation_on_restart
ITEM: B15 | closed-position-with-supported-terminal-outcome | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt22_final_verified_releases_capacity_exactly_once
ITEM: B16 | economic-identity-after-every-transition | workstream=B | class=UNPROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/app/opip/contracts/paper_economics.py
ITEM: B17 | immutable-thesis-and-first-fill-clock | workstream=B | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt14_time_after_partial_target_uses_the_original_expiry
```

## 4. Workstream C - F11 failure and independence

```text
ITEM: C1 | market-feed-outage | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_003_an_unavailable_venue_neither_triggers_nor_grants_new_admissions
ITEM: C2 | decision-engine-failure | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_007_the_protection_authority_reads_no_live_domain_observer
ITEM: C3 | ai-or-committee-unavailability | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_m03_f11_failure_matrix.py::test_ac_311_the_committee_surface_cannot_stop_the_exit
ITEM: C4 | telegram-outage | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_001_a_broken_discovery_and_notification_surface_cannot_stop_the_exit
ITEM: C5 | dashboard-outage | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_001_a_broken_discovery_and_notification_surface_cannot_stop_the_exit
ITEM: C6 | duplicate-order-intent | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt17_lost_action_ack_creates_no_duplicate_trigger_or_order
ITEM: C7 | application-restart | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt23_restart_after_terminal_reads_no_market_and_writes_nothing
ITEM: C8 | crash-between-intent-and-fill | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt20_crash_after_the_flat_fill_completes_reconciliation_on_restart
ITEM: C9 | reconciliation-gap | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt21_crash_after_flat_awaiting_verifies_exactly_once
ITEM: C10 | reservation-race | workstream=C | class=PROVEN | contract=SC-07 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_plan_commits_atomically_against_the_version_it_was_built_for
ITEM: C11 | stale-account-balance | workstream=C | class=PROVEN | contract=SC-06 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_a_stale_or_unsupported_account_state_abstains
ITEM: C12 | partial-close | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_prt11_tp2_closes_the_residual_after_a_partial_target
ITEM: C13 | invalid-or-conflicting-protection-evidence | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_003_a_contradictory_book_neither_triggers_nor_grants_new_admissions
ITEM: C14 | unexpected-open-obligation | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_006_a_saturated_portfolio_is_fully_protected_in_one_sweep
ITEM: C15 | f11-independence-from-other-subsystems | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py::test_ac_001_the_protective_authority_imports_no_other_subsystem
ITEM: C16 | unsafe-admission-fails-closed-while-obligations-protected | workstream=C | class=PROVEN | contract=SC-11 | evidence=OHM-Trade-Agent-v1/tests/test_opip_paper_v2_protection_runtime_bc3.py::test_an_unavailable_protection_sweep_withholds_new_authority_and_never_falls_back
ITEM: C17 | f11-transitive-import-closure-is-isolated | workstream=C | class=BLOCKED_BY_OWNER_DECISION | contract=SC-11 | evidence=OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
```

## 5. Totals

```text
TOTALS:
  ITEMS 46
  PROVEN 43
  UNPROVEN 1
  BLOCKED_BY_OWNER_DECISION 2
  WORKSTREAM_A 12
  WORKSTREAM_B 17
  WORKSTREAM_C 17
```

One `UNPROVEN` item and two owner-gated items remain. The single `UNPROVEN` item
is the entire justification for the remaining follower increment, and no `PROVEN`
item is re-proved by a later increment.

## 6. Follower increments and what each closes

```text
FOLLOWER: ATDD-R5-M03-paper-lifecycle-economic-invariants | closes B16 | asserts the economic identity - cash, fees, quantity, remaining obligation, fills and realized net economics - after every lifecycle transition
```

## 7. What this matrix refuses to say

- It does not say the paper runtime is active. `TARGET_PAPER` is `BLOCKED` and the
  production posture is `EVIDENCE_SHADOW`; every item above is proven in process,
  under test, on an isolated canonical store.
- It does not say any item is production-proven. No item here has production
  evidence, and none claims it.
- It does not say rotation is implemented. `B13` is owner-gated on `OQ-05`: the
  refusal path is proven and the execution path is not ratified.
- It does not treat a neighbouring test as proof of the item the mission named, and
  it does not retain a diminished class once the evidence no longer supports one.

## 8. Corrections this mission made to its own audit

Three items were first recorded as `PARTIALLY_PROVEN` or `UNPROVEN` and are in fact
fully proven. All three were corrected in place by
`ATDD-R5-M03-f11-failure-independence-matrix`, and the corrections are listed here
rather than absorbed silently.

- `B8` early validation was recorded as partially proven. The pre-trade suite
  `tests/test_opip_paper_v2_pretrade_bc3.py` (41 tests) already proves the early
  validation step fails closed, including
  `test_fetch_converts_a_kraken_read_failure_to_fail_closed`.
- `C1` market-feed outage was recorded as partially proven on the reasoning that
  only an unusable book was proven, not a transport that fails. The harness already
  contains `_DownTransport(KrakenTransportError(...))`, and
  `test_ac_003_an_unavailable_venue_neither_triggers_nor_grants_new_admissions`
  drives exactly that. The outage was already proven.
- `C5` dashboard outage was recorded as unproven. The existing matrix of broken
  surfaces in `tests/test_opip_r5_e_f11_independent_protection.py` already includes
  `app.api.dashboard.analytics_summary`, so
  `test_ac_001_a_broken_discovery_and_notification_surface_cannot_stop_the_exit`
  breaks the dashboard at the same instant every other surface is broken and the
  approved stop exit still executes.

The lesson is recorded because it is the one this audit exists to prevent: the first
revision classified from the mission's wording rather than from the harness, and the
harness contained more than the wording implied.

`C3` AI and Committee unavailability was the one scenario with no test at all, and
`ATDD-R5-M03-f11-failure-independence-matrix` closed it: with the production
Committee and AI entry point raising on every call, the scheduled production
protection sweep still reads the venue, still triggers, still takes the full
remaining exposure, and never consults the Committee.

## 9. A coupling this mission found and did not hide

Closing `C3` required walking the protective authority's **transitive** application
import closure rather than only its direct imports, and that walk found something the
direct-import census cannot see. `app.services.paper_v2_protection_runtime`
transitively reaches:

```text
app.services.chief_analyst            <- paper_v2_execution -> decision.versioning -> decision.thresholds
app.services.entry_exit_advisor       <- ... -> decision.thresholds -> economic_quality_gate
app.opip.decision_intelligence.*      <- paper_v2_execution -> canonical.decision_context_bridge
                                         and -> features.publisher -> canonical.writer
```

What this does and does not mean, stated precisely:

- Protection **never calls** any of them. The behavioural test proves a raising
  Committee surface cannot prevent a triggered exit, and the direct-import census in
  the existing F11 increment proves protection imports none of them directly.
- Protection **does reach** them transitively, through shared leaf modules
  (`app.opip.decision.thresholds`, `app.opip.decision.versioning`,
  `app.opip.canonical.writer`). Independence of behaviour is proven; isolation of the
  module graph is not.

This is recorded as `C17` and classified `BLOCKED_BY_OWNER_DECISION`, because
removing the edges means changing shared leaf modules that the writer, the
thresholds and the versioning surface all depend on - an approved module-layering
change, not an engineering gap. Until then the follower increment's test asserts the
coupling set **exactly**, so any *new* edge fails closed and must be reviewed rather
than absorbed. The coupling is frozen, not blessed.




