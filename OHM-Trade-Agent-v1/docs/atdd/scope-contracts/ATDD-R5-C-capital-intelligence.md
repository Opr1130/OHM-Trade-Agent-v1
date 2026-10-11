INCREMENT:
ATDD-R5-C-capital-intelligence

OWNER-APPROVED INTENT:
Deliver the R5-C Capital Intelligence subincrement inside the existing capital owner: the one canonical capital record (`CapitalSnapshot`) that the frozen `SC-06` contract assigns to `OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py`, the exact accounting laws over it, the atomic compare-and-commit reservation path of `SC-07`, and the reconciled capital-time view the R5-C register row requires. The module is *extended*, not replaced (`EXTEND_EXISTING_MODULE`): the frozen R5-B rotation vocabulary above the R5-C banner is untouched, and no second capital book, reservation family or writer is created.

The adopted body (`OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, 9 October 2026) requires in section 6 that `CI-01` reconcile a committed portfolio version's cash total into four disjoint partitions while reporting deployed position basis and marked value separately from cash and blocking unsupported instruments or accounting states, that `CI-02` bound deployable capital by free cash and the remaining approved risk, concentration, liquidity and horizon-sleeve limits while blocking an unresolved negative balance, a stale account state, missing fees, a currency mismatch or a failed reconciliation, that `CI-03` recompute only on describable events with exact decimal precision, source watermarks and a declared validity limit that a snapshot may not outlive, and that `CI-04` compare the expected portfolio version and commit capital and risk usage atomically so that at most one competing decision consumes contested cash, the loser rereads and replans, and duplicate fills, retries or restart never create or release capital twice. Section 12 adds the capital-time metrics this increment reports as diagnostics: capital occupancy is the time integral of committed capital expressed in currency-hours under a frozen basis, reserve treatment and endpoint policy, capital-time efficiency is a fully resolved cohort's realized trading net divided by its capital-hours, zero or unknown occupancy yields unavailable, and an aggregate is a ratio of sums rather than an average of ratios.

This increment delivers exactly that seam and nothing more: one capital record with its durable round trip, one recompute-provenance and version-lineage law, one deployable-capital evaluation seam with a closed abstention precedence, one compare-and-commit reservation seam with its concurrency disposition, one capital-hours and capital-time efficiency diagnostic with its aggregate, and the declared policy vocabulary those seams run under.

Every owner-gated number stays `OWNER_DECISION_REQUIRED` (`OQ-06`). The deployable-capital policy ships no sleeve cap and no limit cap, and the capital-hours policy ships no endpoint policy and no occupancy basis of its own: a missing cap abstains visibly instead of being read as unlimited, and a missing endpoint policy abstains instead of being chosen. No numeric policy is ratified here.

This increment grants no authority. It adds no order, sizing, allocation, execution, store, scheduler, Telegram, deployment or release surface, reads no environment variable and no wall clock, wires nothing into a runtime root, and keeps every record `authoritative = False`.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority) with extraction `docs/architecture/v1.5.0/ARCHITECTURE.md` and identity pin `docs/architecture/v1.5.0/SOURCE.md`; section 6 (`CI-01`-`CI-04` capital accounting, deployable bounds, recompute and reservation), section 7 (portfolio planning and selection: fixed risk sizing and every existing veto remain binding), section 12 (economics and capital-time metrics: capital occupancy, capital-time efficiency, aggregate by ratio of sums), section 16 (the R5-C row: reconciled capital and capital-time view within existing F7/F8 ownership, proving disjoint balances and atomic consumption), section 17 (acceptance scenarios cited exactly as the adopted extraction labels them; this increment is governed by the capital clauses of `AC-05 / B` — an input that lacks evidence takes a research-only disposition with no approved allocation and no invented confidence, which is the disposition every abstention here records) and section 18 (gates `G1`-`G6`; this increment feeds `G3` economic usefulness, whose evidence names capital-time across matured horizon cohorts).
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` row `R5-C` (dependency `R5-B`, declared contracts `SC-06`, `SC-07`, `SC-12`, open input `OQ-06`, gate `G3`, evidence path `OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py`) and rows `F7` and `F8` (the ownership this increment extends and does not replace); the `R5-C` row keeps its `PROPOSED_NOT_FROZEN` status and its `revise ownership, add no allocator database` obligation, which this increment records rather than claims.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` contract `SC-06` (Capital snapshot and accounting partitions: identical record keys, owning module and `EXTEND_EXISTING_MODULE` reuse status) and contract `SC-07` (Reservation, atomic consumption and concurrent change: compare the expected portfolio version, commit capital and risk usage atomically, fail closed on concurrent change, reservation state with the capital services rather than a new store, and fixed risk sizing and minimum reward-to-risk defaults left binding and unmodified); contract `SC-12` is respected by validating and serializing instants and identities through the frozen temporal and serialization owners rather than inventing a second convention.
- `OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py` (the frozen F7 owner whose `PortfolioCapitalState`, `PortfolioReservationPlan`, `PortfolioContractError` and `assert_reservation_plan_current` this increment reuses rather than forks) and `OHM-Trade-Agent-v1/CLAUDE.md` (trading safety, shadow isolation and no-authority rules this increment preserves).

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
one committed portfolio version and the frozen `SC-06` capital snapshot record
WHEN:
the snapshot is built, exported to its durable form, rebuilt from that form and tampered with
THEN:
the record carries every frozen record key with `liabilities` and `receivables_supported` declared unsupported, the four cash partitions are disjoint and reconstruct the declared cash total exactly, an incomplete, unknown or non-reconstructing partition set is refused, the durable payload round-trips to an identical record and identity, a tampered key set or forged `CPSN:` identity is refused, the `CPSN:` identity is the declared deterministic prefix and is never authoritative, and exactly one module in the application declares the capital record while the seam reuses the frozen portfolio owner instead of forking a second capital book

AC-002:
GIVEN:
a supported paper account with declared positions, declared marks and no liability surface
WHEN:
equity, deployed basis, the marked aggregate and the money quantum are applied
THEN:
equity is cash plus the declared marked position value with the unsupported liability and receivable surfaces contributing exactly zero, deployed basis and declared marked value are reported separately from cash and never derived from it, the declared marked aggregate reconciles to the declared positions and a mismatched mark instant, an orphan aggregate or a duplicated symbol is refused, and a float, a boolean, an integer, a non-finite value or a value carrying more precision than the declared money quantum is refused rather than rounded, coerced or defaulted

AC-003:
GIVEN:
a committed snapshot, the declared recompute reasons and the declared cash law of each reason
WHEN:
a successor is derived, asserted, rewound, re-identified or asserted against the wrong reason
THEN:
only describable recompute reasons exist and an external deposit, a withdrawal or a liability movement is refused, the portfolio version moves forward by exactly one declared step and a fork, a repeat, a skip or a backwards version is refused, one predecessor has exactly one asserted successor per book and a second competing successor is refused by identity rather than by a global version string, a recompute must reference the witnessed immediate predecessor, keep the same account, environment, engine and currency and observe a strictly newer `as_of`, a reservation or reconciliation recompute preserves the cash total, a fee recompute may only reduce the total by exactly its declared fee movement against a newer source and fee watermark, a mark recompute preserves the partitions and must actually re-mark, and a reconciliation recompute that reports an unreconciled state is refused

AC-004:
GIVEN:
a declared deployable-capital policy and a snapshot with declared risk, concentration, liquidity and sleeve usage
WHEN:
deployable capital is evaluated
THEN:
it is the tightest declared remaining bound and never a sum and never larger than free cash, every contributing bound is published, a tie is resolved by the declared bound precedence, the tightest sleeve participates and names its sleeve, an exceeded cap is reported as zero with the over-limit bound named rather than a negative number, a limit or sleeve with usage but no configured cap abstains, a configured cap whose own usage row is not declared abstains and names that row instead of being charged with another limit family's usage or read as the whole cap, an explicitly declared zero usage is declared evidence and not a missing row, a negative declared usage is refused, a limit with neither a declared cap nor declared usage is not evaluated at all, and the arithmetic is exact to the cent

AC-005:
GIVEN:
an unbound or mismatched identity, a stale or undetermined account state, an unsupported accounting state, a failed or skipped reconciliation, an unresolved negative balance, an undeclared fee state, an outlived or incomplete snapshot and a live observation
WHEN:
deployable capital is evaluated against a paper-ledger policy
THEN:
each condition abstains with exactly one declared reason in the declared precedence order, the reason published is the first applicable one, a configured cap without its own declared usage reason sits between the missing-cap and missing-sleeve-cap reasons, the deployable seam publishes that specific reason and the reservation commit refuses the same record with its one declared untrusted disposition, an abstention publishes no number, no bound, no binding bound, no binding sleeve and no over-limit list, the unbound inputs and the missing caps are named, a live observation can never fund a paper account, and the status vocabulary is exactly the declared pair

AC-006:
GIVEN:
a current capital snapshot and a reservation plan bound to its version
WHEN:
the plan is committed and when a stale expected version, a plan for another version, a plan created in the future, a plan whose declared reservation window has closed or which a release has partially consumed, an untrusted account state, an underfunded amount, a non-positive amount, an undescribable reason, a non-fundable environment, an outlived snapshot or a rewound instant is offered
THEN:
the commit is all-or-nothing against the version the plan was built for, the committed capital moves from free cash into order reserves with the declared cash total preserved, the committed amount, the successor version and the plan reference are recorded, the successor obeys the same recompute laws any other successor does and carries the predecessor's validity deadline unchanged so a reservation can never extend how long the evidence may be read as current, the plan's own approval window is enforced at the commit boundary so a future-dated, closed or partially released plan is refused instead of committing less than it declares, the same account-evidence table the deployable seam abstains under also refuses the commit as untrusted, only the reservation reason commits and only the paper ledger may be funded, every refusal is rejected with its declared reason and writes nothing at all, a structural misuse of the seam raises instead of returning a result, and the frozen owner's own capital-state seam follows the committed record

AC-007:
GIVEN:
a committed reservation and two decisions that compete for the same contested cash at the same version
WHEN:
the same plan is presented again, a consumed plan is offered against another version, and the two decisions are committed concurrently
THEN:
a replay is an idempotent replay of the recorded successor that consumes nothing twice, a plan consumed at another version is refused as already consumed rather than replayed, exactly one racer consumes the contested capacity while the loser is refused without a partial write and may reread the committed version and replan, the contested partition moves exactly once, the plan-current check delegates to the frozen owner's own assertion instead of a second reservation family, a lawful recompute of any declared reason moves the version the process tracks as current for that book forward so the next reservation consumes the version that actually is current while a replaced version never becomes current again, one predecessor has exactly one successor per book so a second competing successor is refused while the identical successor replayed is not a fork and an unrelated book using similar version strings is never refuted, and the process-scoped witness stays bounded in both its record and per-version buckets with eviction declared as its retention window rather than a durable guarantee

AC-008:
GIVEN:
a declared occupancy series of committed capital on preserved instants under a declared endpoint policy
WHEN:
the series is measured, the endpoint policy is left undeclared, the series is empty, unordered, before its declared start or carries a rewound cumulative cost series or a zero occupancy, and off-grid, negative or naive values are declared
THEN:
capital-hours is the time integral of the declared basis in currency-hours including the adopted illustrative example, the endpoint policy resolves an interval exactly as declared and is never chosen, the cumulative cost channel is the declared step integral of the declared cumulative cost over the same instants under the same endpoint policy and subtracted once, so it resolves each interval by the same declared endpoint rule as the basis channel - the level carried through the interval under the declared start policy, where a one-off cost is never scaled by the interval it was observed across and the same history sampled more finely measures the same value, and the level at the interval's end under the declared end policy, where both channels move together by that declared rule, so where a sample lands relative to a step is a tested property of the declared policy rather than an unstated sampling artefact, each unresolvable series abstains with its declared reason and publishes no number, a float, an over-precise amount, a negative amount or a naive instant is refused, and the measurement records the policy and basis tokens it was taken under

AC-009:
GIVEN:
a resolved cohort with a declared realized trading net and several measured and unresolvable cohorts
WHEN:
capital-time efficiency is measured and the cohorts are aggregated
THEN:
the ratio is realized trading net divided by the measured capital-hours including the adopted illustrative example, a loss is published as a negative ratio, the operating-net variant is reported separately rather than blended, zero or unknown occupancy yields an unavailable cohort that publishes no number under the declared reason, the aggregate is the ratio of summed realized net to summed capital-hours rather than an average of ratios, the measured cohorts are named in order, each excluded cohort is named rather than dropped silently, and an aggregate with no measured cohort abstains

AC-010:
GIVEN:
the two policy versions this increment ships and the owner input it cannot decide
WHEN:
the shipped policies, the declared policy fields and a foreign policy token are inspected
THEN:
the shipped deployable-capital policy declares no limit cap, no sleeve cap and no identity and abstains, the shipped capital-hours policy declares no endpoint policy and abstains, `OQ-06` stays `OWNER_DECISION_REQUIRED` and is never defaulted, every cap, sleeve limit and endpoint policy defaults to a declared absence rather than a number, and a policy version this increment did not declare is refused rather than reinterpreted

AC-011:
GIVEN:
the R5-C section of the owning module, the runtime roots that own trading, discovery, decision and risk paths, the release-profile contract and every record the increment produces
WHEN:
its imports, source, exports, consumers, profile posture and serialized records are inspected
THEN:
it imports no network, database, process, randomness or persistence facility, names no order, secret or store surface, reads no environment variable, exports no authority-shaped name, no runtime root or other application module consumes it, `EVIDENCE_SHADOW` remains the active shadow profile with `TARGET_PAPER` blocked and paper-v2 off, it declares its reservation witness process-scoped and in-memory and states that restart, multi-worker and durable-ledger safety are not claimed, every record declares itself non-authoritative, no serialized record carries an authority or secret key, and the component records carry no authority flag at all

AC-012:
GIVEN:
this increment's scope contract, the repository acceptance index and the module's declared vocabulary and source
WHEN:
the contract is parsed and scope-checked, the declared tokens are compared and the source is scanned
THEN:
the contract's increment matches, its traceability equals the acceptance tests that cite it, every criterion has an implementation-map entry, the scope check reports no reason, the movable increment pointer resolves without being pinned, every declared schema version, quantum, prefix, record key and precedence table is the exact declared value, every declared export exists, and the module reads no wall clock, randomness, environment, filesystem or network while validating instants and identities through the frozen owners

EXPLICITLY OUT OF SCOPE:
- No authority of any kind: this increment places, modifies, cancels, confirms or authorizes no paper or funded order, and it changes no risk gate, sizing default, execution safeguard, deployment control or release profile. Fixed risk sizing and minimum reward-to-risk defaults are untouched and stay binding.
- No store, writer, database, cache, scheduler, queue, file, environment variable or network call is added. The in-process reservation witness is deliberately in-memory, bounded and process-scoped, and is not a store: it holds no authority and is never persisted.
- No new orchestrator or consumer: nothing under `app/jobs`, `app/api`, `app/opip/decision`, `app/opip/discovery` or `app/opip/risk` imports the capital record, and no application module outside the owner references it.
- No numeric policy ratification: `OQ-06` (sleeve caps and hard versus soft constraint behaviour) and the occupancy-basis and endpoint-policy inputs of section 12 stay owner decisions with no default shipped here. Sleeve caps, limit caps and endpoint policies are caller-declared engineering-scenario inputs, labelled as such, and the shipped policies abstain.
- No claim on the rest of the `R5-C` register row: the recurring reconciliation schedule, the live scheduler-driven recompute cadence and any production capital flow integration are untouched, and this increment does not implement rotation switching (that is `R5-D`), paper management (that is `R5-E`) or capital-time learning (that is `R5-F`).
- No promotion, activation or release action: this increment activates no Feature Bus, no canonical writer, no target spine and no Paper-v2 path, and it ratifies no promotion gate.

FROZEN BOUNDARIES:
- The R5-B rotation vocabulary above the `R5-C Capital Intelligence` banner in `app/services/capital_rotation_intelligence.py` is untouched: the R5-C section is append-only and the module is extended rather than replaced.
- The frozen `SC-06` record keys, owning module and `EXTEND_EXISTING_MODULE` reuse status are preserved exactly; no second capital record, partition family, reservation family or identity convention is introduced, and the frozen `portfolio.py` owner is imported rather than copied.
- The frozen R3 isolation suites, the R5-0 contract-conformance suite, the release-profile contract and the paper/Freqtrade isolation contracts are unchanged and need no widening; every existing forbidden-token scan keeps its scope.
- The existing production capital and reservation paths keep every authority they had. Nothing here widens or narrows them.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_001_the_snapshot_carries_every_frozen_record_key
AC-001 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_001_the_cash_partitions_reconstruct_the_cash_total
AC-001 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_001_the_snapshot_round_trips_and_a_forgery_fails_closed
AC-001 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_001_there_is_exactly_one_cash_book_in_the_application
AC-002 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_002_equity_is_cash_plus_declared_marked_value
AC-002 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_002_the_marked_aggregate_reconciles_to_the_positions
AC-002 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_002_deployed_basis_and_marked_value_stay_outside_cash
AC-002 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_002_float_and_over_precise_money_is_refused
AC-003 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_003_a_recompute_declares_a_supported_trigger
AC-003 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_003_version_lineage_is_monotone_and_unforked
AC-003 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_003_a_recompute_may_not_precede_or_re_identify_the_record
AC-003 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_003_each_trigger_obeys_its_declared_cash_law
AC-004 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_004_deployable_capital_is_the_tightest_declared_bound
AC-004 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_004_free_cash_is_a_hard_ceiling
AC-004 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_004_a_sleeve_limit_can_bind_independently
AC-004 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_004_a_missing_cap_abstains_instead_of_being_unlimited
AC-004 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_004_bounds_are_exact_and_never_negative
AC-004 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_004_a_capped_limit_without_declared_usage_abstains
AC-005 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_a_stale_or_unsupported_account_state_abstains
AC-005 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_reconciliation_and_balance_failures_abstain
AC-005 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_an_outlived_or_incomplete_snapshot_abstains
AC-005 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_identity_must_be_bound_and_match
AC-005 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_the_declared_precedence_decides_and_publishes_nothing
AC-005 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_005_one_trust_table_gates_the_deployable_and_commit_seams
AC-006 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_plan_commits_atomically_against_the_version_it_was_built_for
AC-006 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_rejected_attempt_changes_nothing_at_all
AC-006 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_live_book_or_an_undescribable_commit_is_refused
AC-006 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_plan_outside_its_declared_window_is_refused
AC-006 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_006_a_reservation_never_extends_the_snapshot_validity
AC-007 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_007_a_replay_of_a_committed_plan_consumes_nothing_twice
AC-007 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_007_a_plan_consumed_at_another_version_is_not_replayed
AC-007 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_007_a_losing_racer_never_double_spends
AC-007 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_007_the_plan_current_check_reuses_the_frozen_owner
AC-007 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_007_a_lawful_recompute_moves_the_tracked_current_version
AC-007 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_007_a_competing_successor_is_refused_but_a_replay_is_not
AC-007 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_007_the_witness_is_bounded_and_declares_its_retention_window
AC-008 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_008_capital_hours_is_the_declared_basis_integral
AC-008 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_008_the_endpoint_policy_is_declared_never_assumed
AC-008 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_008_an_unresolvable_series_abstains_rather_than_guessing
AC-008 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_008_the_cost_channel_is_measured_not_estimated
AC-008 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_008_off_grid_or_unusable_occupancy_values_are_refused
AC-009 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_009_return_per_hour_uses_the_measured_occupancy
AC-009 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_009_zero_or_unknown_occupancy_yields_unavailable
AC-009 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_009_the_aggregate_is_a_ratio_of_sums_not_an_average_of_ratios
AC-010 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_010_the_declared_policy_versions_and_owner_input_are_exact
AC-010 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_010_the_engine_ships_no_unratified_cap_or_band
AC-011 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_011_the_module_carries_no_authority_storage_or_environment_surface
AC-011 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_011_no_runtime_root_or_application_module_consumes_the_record
AC-011 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_011_release_profile_and_paper_posture_are_unchanged
AC-011 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_011_every_record_declares_itself_non_authoritative
AC-011 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_011_the_increment_claims_no_durable_or_restart_safe_allocation
AC-012 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_012_the_increment_scope_contract_is_self_consistent
AC-012 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_012_the_declared_schema_versions_tokens_and_prefixes_are_exact
AC-012 -> tests/test_opip_r5_c_capital_intelligence.py::test_ac_012_the_module_holds_no_hidden_clock_randomness_or_environment_read

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-002 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-003 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-004 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-005 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-006 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-007 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-008 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-009 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-010 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-011 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-012 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-C-capital-intelligence.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py

DEFERRED DISCOVERIES:
- `OQ-06` (sleeve caps and hard versus soft constraint behaviour) is unratified, so the shipped deployable-capital policy declares no limit cap, no sleeve cap and no identity, every evaluation under it abstains, and every cap assertion in the suite is labelled an engineering scenario rather than an accepted limit. The hard versus soft distinction is not implemented at all: every configured cap behaves as a hard bound, and a soft bound needs the owner decision before it can be added.
- The occupancy basis, reserve treatment, partial-fill rules and endpoint policy of section 12 are unratified, so the shipped capital-hours policy declares no endpoint policy, every measurement under it abstains, and the measured basis is declared per point by the caller under a labelled engineering-scenario basis token rather than a ratified production metric.
- The reservation witness is process-scoped and in-memory, so it proves atomicity and idempotency inside one process and deliberately claims nothing about a restarted process, a second worker or a durable reservation ledger. A durable reservation record is an open owner decision, and a later increment that adds one must keep the compare-and-commit semantics proved here.
- The witness bound (`_CAPITAL_WITNESS_LIMIT`) is a retention window, not a guarantee: a bounded witness cannot grow into an unbounded store, but once an entry is evicted the lineage claim that entry could have refuted is no longer refutable by this process, and the fork proof is retained-window scoped with it. That is declared in the module and proved by the boundedness acceptance test rather than left implicit; the capital-creating path is separately guarded by the tracked current version, the account-evidence table and free cash, so a lost fork proof cannot by itself create or release capital.
- Restart-, multi-worker- and durable-ledger safety are **not** claimed by this increment and are not marked complete. The increment declares `CAPITAL_RESERVATION_DURABILITY_STATUS = "PROCESS_SCOPED_IN_MEMORY_WITNESS"` and classifies canonical persistent reservation ownership as an explicit `R5-E` integration dependency inside the existing F8 ownership (register row `R5-E`: deterministic portfolio paper management with a single canonical writer); no store, writer or scheduler is added here to satisfy it.
- `net_capital_hours` (the declared basis integral less the declared cumulative-cost integral) is a laboratory diagnostic, not the adopted section 12 metric: the adopted clause defines capital-time efficiency as realized trading net divided by capital-hours, which the efficiency seam implements separately and which does not consume this channel. The economic interpretation of subtracting a cumulative-cost integral from an occupied-capital integral is unratified, so it is published beside the occupancy it was measured over and is never an objective, threshold or promotion input. The channel resolves each interval under the increment's declared endpoint policy exactly as the basis channel does, so where a sample lands relative to a step is a declared property of that policy (tested under both declared policies) and not a ratified sampling rule; no sampling cadence, reserve treatment or basis definition is chosen by this increment.
- The legacy `CapitalUtilization`/`evaluate_rotation` rotation vocabulary above the R5-C banner still carries the historical float-based utilization estimate that predates this record; it is untouched by this increment and is not reconciled with `CapitalSnapshot`. Replacing or retiring it is a separate increment.
- This increment consumes no capital evidence and produces none into any store: every record is an unconsumed research artifact whose disposition is still to be governed, and runtime usefulness stays `NOT_PROVEN`. It measures no live account, reconciles no live ledger and schedules no recompute.
- Relational-validation discovery: the deterministic `CPSN:`, `CCMT:`, `CHRS:` and `PCAP:` identities prove only that a record is internally consistent. They do not establish that a claimed predecessor was really observed by this process, that a committed reservation really moved the contested partition, or that a measured occupancy series describes the position set it is attributed to. Those relationships are therefore enforced directly by the reviewer-visible seams (the witness checks, the all-or-nothing commit, the declared-instants measurement) rather than assumed from the identities.

UNAPPROVED SCOPE CHANGES:
NONE






