INCREMENT:
ATDD-R5-D-portfolio-planning

OWNER-APPROVED INTENT:
Deliver the R5-D Portfolio Planning and Rotation subincrement inside the two existing owners the frozen R5-0 contract assigns to it: the `PortfolioPlan` record and common-window comparison (`SC-08`) in `OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py`, and the linked-intent `RotationGroup` record (`SC-09`) in `OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py`. Both modules are *extended*, not replaced (`EXTEND_EXISTING_MODULE`): the frozen R5-B/R5-C vocabulary above each R5-D banner is untouched, no second plan store, no second position registry, no allocator database and no second selector is introduced, and no funded or exchange authority is created.

The adopted body (`OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, 9 October 2026) requires in section 7 that a plan record stores the frozen `PP-01` keys and that plan creation is *not* an order or a reservation; that `PP-02` admits `HOLD_CASH`, `WAIT`, `HOLD_POSITION`, `NEW_ENTRY`, `REDUCE`, `EXIT` and `ROTATE` while `ADD` stays disabled, that a periodic profit objective cannot increase risk or create a minimum trade count, and that alternatives are compared on one point-in-time capital/risk state over one common window with deterministic tie-breaking, eligibility, exclusions and a recorded cash comparison (`PP-04`) where "best" means best among the *observed eligible* alternatives and never a market-wide optimum. It requires in section 8 that `RO-01` freezes a common-window comparison of `HOLD`, `EXIT_TO_CASH` and `EXIT_THEN_ENTER` with exit and entry fees and spread counted once and the past acquisition cost treated as sunk, that `RO-02` refuses a discretionary switch until a versioned minimum economic advantage, uncertainty treatment, cooldown, turnover budget and expiry are ratified (a cooldown never delays an F11 protective exit), and that `RO-03`/`RO-04` record a stable two-leg group where a partial exit can only fund policy-allowed affordable entry while counting residual old exposure, default to waiting when the policy is unspecified, and never repeat a fill on crash recovery.

This increment delivers exactly the structural seam and nothing more: one durable non-authoritative plan revision with its frozen keys and content identity, the declared disposition vocabulary with its per-disposition capital laws, the deterministic observed-alternative ranking with recorded rejections, the matched common-window comparison with counted-once round-trip costs, the closed abstention precedence of the discretionary switch gate, the dormant two-leg rotation-group record with its partial-fill accounting, and one cross-component acceptance proof that a plan's declared capital consumption is bounded by what the *existing* capital owner says is actually deployable at the commit boundary.

Every owner-gated number stays `OWNER_DECISION_REQUIRED`: this increment ratifies no minimum rotation advantage, no cooldown, no turnover budget, no sleeve cap and no hard-versus-soft limit behaviour (`OQ-05`, `OQ-06`). A plan built without those inputs abstains with `POLICY_NOT_BOUND` and cannot carry a `ROTATE` action at all; a missing cap abstains visibly instead of being read as unlimited. No threshold, duration, budget, cap or calibration value is chosen, implied, defaulted or inherited.

This increment grants no authority. It adds no order, sizing, allocation, execution, store, scheduler, writer, Telegram, dashboard, deployment or release surface, reads no environment variable and no wall clock, wires nothing into a runtime root, calls no venue, and keeps every record `authoritative = False`. `ROTATION_GROUP_RECORDING_ENABLED` stays `False`.

PROVISIONAL IMPLEMENTATION NOTE (declared, not hidden):
The R5-D implementation in the two owning modules was written before this scope contract was frozen, so this contract does *not* claim that its acceptance tests preceded those edits. The provisional implementation is treated as provisional evidence: this increment's acceptance tests were written against the frozen contract and the adopted clauses, they are executed as the acceptance authority for the seam, and any behaviour they cannot prove is recorded under DEFERRED DISCOVERIES rather than asserted as complete. It was subsequently reviewed against the frozen clauses: three financial-integrity findings were corroborated by reproduction, and the corrections plus their new negative acceptance tests were delivered as a declared remediation recorded under DEFERRED DISCOVERIES.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority) with extraction `docs/architecture/v1.5.0/ARCHITECTURE.md` and identity pin `docs/architecture/v1.5.0/SOURCE.md`; section 1 (canonical ownership, F7 owns constrained selection and atomic reservation, F8 owns paper execution, F11 protects independently), section 6 (`CI-01`-`CI-04`: the capital partitions, the deployable bound and the compare-and-commit reservation this increment's cross-component proof reuses), section 7 (`PP-01`, `PP-02`, `PP-03`, `PP-04`), section 8 (`RO-01`, `RO-02`, `RO-03`, `RO-04`), section 10 (record lineage), section 16 (the R5-D row), section 17 (`AC-08`, `AC-09`), section 18 (`G1`, `G3`, `G5`).
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`: `SC-08` (owning module `app/opip/contracts/portfolio.py`, open owner input `OQ-06`) and `SC-09` (owning module `app/opip/contracts/paper_execution.py`, open owner input `OQ-05`); the frozen owner table names exactly one owner per logical record, and no new store, allocator, registry, outcome engine or scheduler may be introduced.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`: row `F7` (`SC-06`, `SC-07`, `SC-08`, `SC-09`, `OQ-05`, gate `G3`) and row `F8` (`SC-08`, `SC-09`, `SC-11`, `OQ-08`, gate `G5`).
- `docs/atdd/scope-contracts/ATDD-R5-C-capital-intelligence.md`: the predecessor increment that owns `SC-06`/`SC-07`; its `commit_reservation`, `assert_reservation_plan_current` and `evaluate_deployable_capital` seams are *reused* by the cross-component proof and are not re-implemented here.
- `docs/architecture/v1.2/CODING_BOUNDARY_CONTRACT.md` and `AGENTS.md`: contract tests stay outside trading runtime ownership; the smallest change that solves the proven problem; no new infrastructure; no trading authority for coding automation.
- `.github/workflows/pytest.yml`: the `atdd scope` job compares the pull-request diff against `docs/atdd/ACTIVE_INCREMENT` only, and the `architecture-gate` job re-proves the pinned `EVIDENCE_SHADOW` profile with `TARGET_PAPER` off.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the extended `PortfolioPlan` record of the frozen `SC-08` contract
WHEN:
a plan revision is built, serialized and reloaded
THEN:
it stores exactly the frozen record keys in order, its `portfolio_version` is the ratified plan schema version, its panel manifest belongs to its own evaluation window, its identity is a content hash so a mutated, dropped, renamed or reordered key fails closed, and every record declares `authoritative = False` because plan creation is evidence and never an order, a reservation or an authority

AC-002:
GIVEN:
the declared plan dispositions of `PP-02`
WHEN:
a plan declares its action sequence
THEN:
only `HOLD_CASH`, `WAIT`, `HOLD_POSITION`, `NEW_ENTRY`, `REDUCE`, `EXIT` and `ROTATE` are eligible and `ADD` is refused, every disposition obeys its declared capital sign so a `WAIT` cannot move capital and an `EXIT` cannot be a hold, a `ROTATE` action requires a ratified switch policy *and* a common-window comparison built from the plan's own compared legs *and* the plan's own recorded evidence to already admit the switch (the named alternative is the best observed eligible alternative and every gate branch the plan itself settles passes), and no profit objective, minimum trade count or target mode can be supplied to force a trade or widen risk

AC-003:
GIVEN:
the observed eligible alternatives of one plan revision
WHEN:
the plan's selection and its recorded exclusions are inspected
THEN:
"best" is the head of a deterministic ranking over the alternatives the plan actually observed (expected net descending, then the declared hold-cash-switch preference, then the alternative id), an exact tie prefers holding over churning, a plan cannot select or claim an alternative it never compared, and every compared alternative but the selected one is recorded exactly once with a reason code from the declared vocabulary

AC-004:
GIVEN:
the matched common-window comparison of `HOLD`, `EXIT_TO_CASH` and `EXIT_THEN_ENTER` (`RO-01`)
WHEN:
the three legs are compared over the single declared window
THEN:
the exit leg's exit fee is carried into the composed leg exactly once and the composed leg never understates the exit leg's own spread, latency or attributable operating cost, the old position's acquisition cost is retained for accounting only and never enters either leg's economics, a missing attributable operating cost or a missing uncertainty band refuses instead of looking favourable, and the conservative advantage reads the replacement's lower bound against the competitor's upper bound

AC-005:
GIVEN:
the discretionary switching gate of `RO-02` and an owner policy that is still open (`OQ-05`, `OQ-06`)
WHEN:
a switch is evaluated for a plan revision
THEN:
an unratified or unbound policy abstains with `POLICY_NOT_BOUND` before any numeric input is considered, a superseded or expired plan abstains with `PLAN_NOT_CURRENT` or `PLAN_EXPIRED`, the remaining gaps are reported in the frozen closed precedence (`CASH_COMPARISON_UNAVAILABLE`, `COOLDOWN_ACTIVE`, `TURNOVER_BUDGET_EXHAUSTED`, `UNCERTAINTY_BAND_UNCLEAR`, `ADVANTAGE_BELOW_MINIMUM`), only a comparison the plan itself recorded is evaluated so a caller cannot substitute more favourable economics, a proven advantage records the advantage together with the ratified minimum and uncertainty treatment that licensed it, and the discretionary gate never delays or weakens an independent F11 protective exit

AC-006:
GIVEN:
the linked-intent `RotationGroup` record of the frozen `SC-09` contract
WHEN:
a rotation group is recorded and reloaded
THEN:
its payload carries the eight frozen keys in order (`old position`, `replacement thesis`, `both-leg intent/fill IDs`, `committed proceeds`, `requalification decision`, `residual exposure`, `terminal state`, `reasons`), its identity is a content hash, its legs obey the two-leg laws (first leg exit, at most one entry leg, distinct intent ids, entry leg carrying the thesis' declared policy-allowed entry forecast, no third leg), the record is dormant and non-authoritative with `ROTATION_GROUP_RECORDING_ENABLED` `False`, and a partial exit funds only policy-allowed affordable entry while the residual old exposure is counted

AC-007:
GIVEN:
the partial-fill, failed-second-leg, replay and recovery paths of `RO-03`
WHEN:
an exit only partially fills, the second leg is cancelled or expires, or a payload is replayed or forged
THEN:
the committed proceeds equal what the exit leg's committed fills actually released and split exactly into what is committed to the entry and what is released to cash without creating or releasing capital twice, the entry leg's committed fills never cost more than the proceeds actually committed to the entry so a rotation can never invent capital, the residual exposure reconciles to the old position less what the exit actually committed, a cancelled or expired second leg retains its costs, terminal state and reason, a terminal state that claims an entry the record does not contain or a requalification that cannot accompany its terminal state fails closed, and a replayed or forged payload (mutated leg, reordered or extra key, forged identity) is refused rather than loaded

AC-008:
GIVEN:
a valid F7 plan whose declared action would consume capital, and the existing capital owner's deployable-capital and reservation-commit seams of `SC-06`/`SC-07`
WHEN:
the plan's proposed consumption is validated at the commit boundary
THEN:
the plan cannot consume more capital than is actually deployable under the approved risk, concentration, liquidity and sleeve constraints, the commit boundary validates the plan's policy identity, its snapshot/portfolio version currency and the reservation amount before anything is committed so a stale or competing plan is refused without a partial write, and where the owner-approved policy, sleeve caps or durable reservation authority are unavailable the composition fails closed and records the dependency instead of inventing a limit, defaulting a cap or introducing a new cash ledger

AC-009:
GIVEN:
this increment, its two extended owning modules and its scope contract
WHEN:
the increment boundary is inspected
THEN:
the increment is self-consistent (the movable active pointer resolves to an existing scope contract, every criterion is traced to a matching acceptance test and every changed file is mapped), neither owning module gains an order, sizing, execution, storage, scheduler, environment, Telegram or deployment surface or a second source of truth, no runtime root or application module consumes the plan or rotation-group records, and every record keeps `authoritative = False` while the release profile stays `EVIDENCE_SHADOW` with `TARGET_PAPER` `BLOCKED` and `OPIP_PAPER_V2_MODE=off`

EXPLICITLY OUT OF SCOPE:
- Any change to the frozen `SC-06`/`SC-07` capital owner (`app/services/capital_rotation_intelligence.py`) or to the `SC-11` paper-isolation surface; the cross-component proof *reuses* those seams read-only
- Any durable plan, reservation or rotation-group ledger, and any multi-worker or restart-safe persistence: that is the deferred `R5-E` integration dependency owned by the existing F8 module family
- Any ratified numeric policy: minimum rotation advantage, cooldown, turnover budget, uncertainty-treatment default, sleeve cap, hard-versus-soft limit behaviour, plan-expiry default and profit objective stay `OWNER_DECISION_REQUIRED`
- Autonomous paper portfolio management (`R5-E`), horizon learning (`R5-F`), `F10`, `R6`, `R7`, `TARGET_PAPER` activation or readiness, release-profile, Compose, Telegram and dashboard work
- Any order placement, modification, cancellation, confirmation, sizing, allocation or funded/exchange authority, and any live paper-engine wiring or runtime producer of the rotation-group record
- Replacing or retiring the historical float-based `CapitalUtilization`/`evaluate_rotation` vocabulary or any other legacy surface; the R5-D sections are additive above their banners
- A second selector, a second plan store, a second position registry, an allocator database, a new event store, a new scheduler, a new deployment control plane or a BDD/SonarQube/tooling change
- `RO-04` realized-effect measurement (turnover, time out of market, missed replacement entries) beyond the costs recorded here: that belongs to the register's `F9`/`R5-F` rows using the `SC-10` outcome record

FROZEN BOUNDARIES:
- One owner per record: `PortfolioPlan` stays in `app/opip/contracts/portfolio.py` and `RotationGroup` stays in `app/opip/contracts/paper_execution.py`.
- The frozen `SC-08` and `SC-09` record keys, their order and their semantics are preserved exactly; no frozen R5-0 document is edited by this increment.
- No authority transition: `EVIDENCE_SHADOW` remains the release profile, `TARGET_PAPER` remains `BLOCKED`, `OPIP_PAPER_V2_MODE` stays the literal `off`, and paper/research surfaces stay isolated from funded authority.
- Fixed risk sizing, minimum reward-to-risk, F5 feasibility and veto authority, F6 forecast ownership, F7 constrained selection, F8 paper execution and F11 independent protection all remain binding and unmodified.
- `OQ-05` and `OQ-06` stay open: no numeric value is ratified, defaulted or implied, and an unratified input abstains visibly.
- Everything added is deterministic and hermetic: no clock read, no network, no database, no environment read, no randomness, and no process-scoped writable state that could become a second authority.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_001_an_unbound_panel_window_or_version_is_refused
AC-001 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_001_the_plan_carries_every_frozen_record_key
AC-001 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_001_the_plan_round_trips_and_a_forgery_fails_closed
AC-002 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_002_a_rotate_action_requires_a_proven_comparison_and_a_ratified_policy
AC-002 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_002_a_rotate_step_must_be_admitted_by_the_plans_own_evidence
AC-002 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_002_a_rotate_step_must_name_the_plans_best_observed_alternative
AC-002 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_002_a_substituted_comparison_cannot_license_a_rotate_step
AC-002 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_002_every_disposition_obeys_its_declared_capital_sign
AC-002 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_002_no_profit_target_or_trade_count_can_force_a_trade
AC-002 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_002_only_the_declared_dispositions_are_eligible
AC-003 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_003_a_plan_cannot_select_an_alternative_it_never_compared
AC-003 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_003_an_exact_tie_prefers_holding_over_churning
AC-003 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_003_best_means_best_among_the_observed_alternatives
AC-003 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_003_every_passed_over_alternative_is_recorded_exactly_once
AC-004 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_004_a_sunk_acquisition_cost_never_enters_the_comparison
AC-004 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_004_exit_and_entry_costs_are_counted_once
AC-004 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_004_missing_evidence_refuses_instead_of_looking_favourable
AC-004 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_004_the_conservative_advantage_reads_the_unfavourable_side
AC-004 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_004_the_exit_spread_latency_and_operating_cost_cannot_be_dropped
AC-005 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_a_caller_cannot_substitute_a_more_favourable_comparison
AC-005 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_a_proven_advantage_records_the_policy_that_licensed_it
AC-005 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_a_stale_or_expired_plan_abstains
AC-005 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_an_unratified_policy_abstains_before_anything_numeric
AC-005 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_the_abstention_precedence_is_closed_and_ordered
AC-005 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_005_the_discretionary_gate_never_delays_a_protective_exit
AC-006 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_006_a_partial_exit_funds_only_policy_allowed_entries
AC-006 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_006_the_group_is_dormant_and_non_authoritative
AC-006 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_006_the_group_payload_carries_the_frozen_keys_in_order
AC-006 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_006_the_linked_legs_obey_the_two_leg_laws
AC-007 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_007_a_failed_or_expired_second_leg_retains_its_costs_and_reason
AC-007 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_007_a_replayed_or_forged_group_payload_fails_closed
AC-007 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_007_a_state_that_claims_an_unrequalified_entry_is_refused
AC-007 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_007_an_entry_leg_cannot_spend_more_than_was_committed_to_it
AC-007 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_007_partial_fills_reconcile_proceeds_and_residual_exposure
AC-008 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_008_a_plan_cannot_consume_more_than_is_deployable
AC-008 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_008_an_unratified_policy_fails_closed_and_records_the_dependency
AC-008 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_008_the_commit_boundary_validates_identity_and_snapshot_version
AC-009 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_009_no_runtime_root_consumes_the_plan_or_rotation_records
AC-009 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_009_release_profile_and_non_authority_posture_are_unchanged
AC-009 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_009_the_increment_scope_contract_is_self_consistent
AC-009 -> tests/test_opip_r5_d_portfolio_planning.py::test_ac_009_the_owning_modules_gain_no_authority_or_storage_surface

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-D-portfolio-planning.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_d_portfolio_planning.py

DEFERRED DISCOVERIES:
- Provisional implementation ordering is declared, not concealed: the R5-D code in the two owning modules was written before this contract was frozen, so this contract does not claim that its acceptance tests preceded those edits. The acceptance tests are the frozen authority for the seam from this commit forward, and any later R5-D behaviour change must follow this contract.
- No ratified numeric policy exists for the discretionary switch: `min_switch_advantage_dollars`, `uncertainty_treatment`, `cooldown_hours` and `turnover_budget_dollars` stay unratified (`OQ-05`), so the shipped constraints record declares no number and every plan under it abstains with `POLICY_NOT_BOUND`. The remaining gate branches (`COOLDOWN_ACTIVE`, `TURNOVER_BUDGET_EXHAUSTED`, `UNCERTAINTY_BAND_UNCLEAR`, `ADVANTAGE_BELOW_MINIMUM`) are proved by tests that construct a labelled *engineering-scenario* constraint set; those numbers are test fixtures and are not an accepted policy. Hard-versus-soft sleeve behaviour (`OQ-06`) is likewise not implemented: every configured cap behaves as a hard bound.
- Durable, multi-worker and restart-safe persistence of the plan, reservation and rotation-group records is **not** claimed by this increment. `ROTATION_GROUP_RECORDING_ENABLED` is `False`, no runtime producer writes the record, and the cross-component proof relies on the existing process-scoped capital witness. Canonical persistent ownership is the `R5-E` dependency inside the existing F8 ownership (single canonical writer); adding a store, writer or scheduler here is out of scope.
- The cross-component deployable-capital proof can only exercise labelled engineering-scenario caps, because the shipped policy ratifies none: under the shipped policy `evaluate_deployable_capital` abstains with `LIMIT_NOT_CONFIGURED`/`SLEEVE_LIMIT_NOT_CONFIGURED` and returns no number, so the affordability of a capital-consuming action is unprovable rather than assumed, and the increment records that dependency instead of inventing a cap. A ratified policy (`OQ-06`) is required before any production affordability claim is possible.
- The plan record's `portfolio_version` field is the ratified *plan schema* version required by the frozen `SC-08` key of that name, not a capital snapshot version. The binding to a live capital snapshot happens at the commit boundary through the existing `SC-07` seams (expected portfolio version, reservation currency and `assert_reservation_plan_current`); carrying a capital-snapshot identity inside the plan record itself would change the frozen `SC-08` record keys and is a separate owner decision.

- `RO-04` (realized net effect, turnover, time out of market and missed replacement entries measured against a preregistered feasible-hold policy) is not delivered here: it is an outcomes/learning measurement owned by the register's `F9`/`R5-F` rows using the `SC-10` expectation-and-outcome record. This increment records the switching decision and its costs; it measures no realized effect.
- The plan and rotation-group records are unconsumed research artifacts: no runtime root, dashboard page, Telegram surface or learning path consumes them yet, so their consumption disposition is still to be governed and their runtime usefulness stays `NOT_PROVEN`.
- Relational-validation discovery: the content-hash identities (`PPLAN:`, `PMANI:`, `PALT:`, `PREJ:`, `PRCMP:`, `PRSW:`, `PPLP:`, `PRG:`) prove only that a record is internally consistent. They do not prove that a claimed comparison was really observed, that a claimed fill was really committed by the paper engine, or that a claimed deployable bound was computed from the live book. Those relationships are enforced by the reviewer-visible seams (the two-leg laws, the fill-measured proceeds and residual reconciliation, and the `SC-07` commit boundary) rather than assumed from the identities.
- The historical `CapitalUtilization`/`evaluate_rotation` rotation vocabulary in the R5-C owner module, and any legacy vocabulary above the R5-D banners, remain untouched and are not reconciled with `PortfolioPlan`/`RotationGroup`. Retiring them is a separate increment.
- Whether the plan record should carry an explicit capital-snapshot identity, and whether the discretionary gate should gain a durable cooldown ledger, are recorded above as `R5-E`/owner-decision dependencies and are not implemented here.

- Review remediation of the three confirmed R5-D financial-integrity findings was delivered *after* the provisional implementation and is declared here as a correction, not as a tests-first claim: (1) a `ROTATE` step can no longer be declared while the plan's own gate abstains - the clock-free build-time admission (`PortfolioPlan._assert_static_switch_admission`, which reads the same `_switch_gate_evidence_reasons` branches the decision-time gate reads, so no second copy of the numeric policy exists) refuses a `ROTATE` whose comparison leaves `ADVANTAGE_BELOW_MINIMUM`, `UNCERTAINTY_BAND_UNCLEAR` or `TURNOVER_BUDGET_EXHAUSTED`, refuses a `ROTATE` that does not name the plan's best observed eligible alternative, refuses a comparison whose legs are not the plan's own compared alternatives, and no longer evaluates a caller-supplied comparison the plan did not record (that abstains with `CASH_COMPARISON_UNAVAILABLE` instead of buying an unpreregistered advantage); (2) the composed `EXIT_THEN_ENTER` leg may no longer understate the exit leg's spread, latency or attributable operating cost, not merely its fee; (3) a rotation group's entry leg may no longer commit fills costing more than the proceeds committed to the entry.
- `PLAN_EXPIRED` and `COOLDOWN_ACTIVE` remain decision-time-only branches: the plan record carries no decision clock and no durable switch history, so neither can be settled at build time and a recorded `ROTATE` step is still not an authorization. The build-time check is a *necessary* condition only, and `assess_rotation_switch` remains the authority over the switch.
- The entry-bound law bounds the entry leg by the proceeds committed to the entry and does not require equality: a commitment is an authorization, so an entry that committed less leaves an unspent remainder that the caller's own proceeds record reconciles. Whether an unspent remainder must be visibly re-released to cash is an `R5-E` execution-reconciliation question, not a `SC-09` record law.
- The exit cost stack is enforced asymmetrically and deliberately: the exit fee is an exact carry, while spread, latency and attributable operating cost are floors, because the frozen `SC-08` alternative keys do not declare the entry-side split of the composed leg. Understatement is the falsification direction and is refused; an overstatement only makes the switch less attractive and can never license a switch the after-cost comparison does not support.
- The R5-D fixture was corrected so its rotation commitment is cash-accurate: the entry leg's committed fills cost 40.00 of notional plus 0.50 of fee, so the entry commitment is 40.50 and 18.50 is released to cash. The previous 40.00 commitment could never have afforded the entry it funded, and the corresponding assertions were updated rather than the fee being deleted.

UNAPPROVED SCOPE CHANGES:
NONE








