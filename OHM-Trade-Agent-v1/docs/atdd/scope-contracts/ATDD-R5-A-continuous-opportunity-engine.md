INCREMENT:
ATDD-R5-A-continuous-opportunity-engine

OWNER-APPROVED INTENT:
Deliver the R5-A Continuous Opportunity Engine of the adopted v1.5.0 architecture as a feature-disabled, deterministic, replayable composition of the existing F1 observation, F2 feature bus, F3 IGNITION detector and F4 opportunity lifecycle owners, together with the new attention, scheduling, horizon-thesis and phase-projection responsibilities that the adopted body assigns to R5-A, and prove zero trading authority, zero activation and zero hidden configuration.

The adopted body (`OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, 9 October 2026) declares in its section 14 that `R5-A Continuous Market Eye` follows the R5 contract freeze, and its section 3 requires continuous observation coverage and timely attention *without selection or trading authority*, acceptance evidence for latency, coverage, capacity and duplicate delivery, and the explicit statement that R5-A is accepted only when it produces zero trading authority and cannot starve F11. The R5-0 register records row `R5-A` as `PROPOSED_NOT_FROZEN` with `SHADOW` authority, contracts `SC-01`, `SC-02`, `SC-03` and open input `OQ-04`; the R5-0 freeze declares `SC-03` `OPEN` and ratifies no numeric policy.

This increment delivers the determinable part of R5-A inside the frozen boundaries: F1 coverage and availability evidence, the EYE attention layer, one deduplicated evaluation coordinator shared by the stream and the scheduler, the HORIZON thesis contract, the R5-A opportunity-phase projection onto the existing F4 enums, and one composition engine whose mode is the literal `off`. Every owner-gated number stays `OWNER_DECISION_REQUIRED`; the code ships no default for it and is inert without it.

This increment grants no authority. It activates nothing, changes no runtime, risk, execution, deployment, release-profile or trading control, adds no scheduled service, no store, no allocator and no second detector. It wires no R5-A module into a runtime root.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority, SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`) with extraction `docs/architecture/v1.5.0/ARCHITECTURE.md` and identity pin `docs/architecture/v1.5.0/SOURCE.md`. Section 2 (architecture flow and three operating speeds), section 3 (`ME-01`-`ME-07` Continuous Market Eye), section 4 (`HZ-01`, `HZ-02` Horizon Intelligence), section 5 (`LC-01`-`LC-03` Opportunity and position lifecycles), section 10 (`DC-01`-`DC-03` canonical records and temporal contracts), section 16 (`EYE`, `HORIZON`, `SCHED` rows), section 17 (`AC-01`-`AC-03`, `AC-12`) and section 18 (gates G1-G6).
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` row `R5-A` and rows `F1`, `F2`, `F3`, `F4`; `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` contracts `SC-01`, `SC-02`, `SC-03`, `SC-04`, `SC-05` and the frozen owner table.
- `docs/architecture/v1.4.4/ARCHITECTURE.md` Appendix A (`PA-01`-`PA-20`) and Appendix B (`SQ-01`-`SQ-10`), which v1.5.0 section 13 keeps applicable; in particular `PA-02` fresh qualification, `PA-06` traceable qualification evidence, `PA-13` independent protection, `PA-17` no hindsight contamination and `PA-20` blocked Paper v2.
- `docs/architecture/v1.2/` contracts B (market data), C (canonical writer), D (detector) and `CODING_BOUNDARY_CONTRACT.md`.
- `AGENTS.md` mandatory principles, including feature branch only, no production activation, no trading authority, no secrets, single canonical writer and no new infrastructure.
- Existing owners reused unchanged: `app/opip/contracts/{observation,features,detector,opportunity,enums,identity,temporal}.py`, `app/opip/detectors/ignition.py`, `app/opip/features/engine.py`, `app/opip/market/observations.py`, `app/opip/events/contract.py`, `app/opip/streaming/*`, `app/services/release_profiles.py`.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
observed market intervals and a consumed source watermark
WHEN:
coverage is reconciled for one instrument window
THEN:
availability evidence, expected-versus-usable counts and every unusable reason are retained, expectations derive from the watermark rather than a clock, gaps and late or clock-skewed arrivals block a favourable decision, and unusable coverage fails closed

AC-002:
GIVEN:
the declared attention vocabulary and an owner-gated attention policy
WHEN:
attention is evaluated or a state is requested as evidence
THEN:
attention states are the closed five-member set with health overlays kept separate from detector phases, lifecycle states and horizon classes, an unratified policy is inert, and no attention record carries claim or episode identity

AC-003:
GIVEN:
one attention transition and the evidence behind it
WHEN:
the trigger record is built, serialized or forged
THEN:
its identity is deterministic and evidence-bound, it carries every frozen `SC-02` key with no authority, and a rewritten identity, a mismatched relationship or an undeclared reason token is refused

AC-004:
GIVEN:
a configured attention policy and repeated or competing evidence
WHEN:
attention advances, regresses, coalesces, expires or is budgeted
THEN:
movement is bounded to one rank per evaluation behind the owner-gated debounce and oscillation guard, a focus loss enters a bounded cooldown, duplicate deliveries converge on one evaluation, cycle budgets bound work without dropping requests, and the populations reconcile exactly under a mixed workload

AC-005:
GIVEN:
a stream-triggered request and a scheduler reconciliation request
WHEN:
the same instrument, cut-off and policy are requested through both paths
THEN:
the evaluation identity is identical and the duplicate coalesces, while a different instrument, cut-off or policy is a different evaluation that cannot coalesce

AC-006:
GIVEN:
a coordinator with owner-gated limits under load
WHEN:
protection, discovery and optional AI work compete for capacity
THEN:
protection is never starved or evicted, optional AI is dropped first, saturated discovery is shed visibly with its counts reported, a per-instrument budget bounds a hot instrument, stale requests expire, retries end in terminal failure evidence, and one identity cannot produce a second served transition

AC-007:
GIVEN:
the frozen F2 feature bus and F3 IGNITION detector
WHEN:
the engine evaluates a request
THEN:
the detector stays pure, clock-free and grid compliant, replay of identical inputs is identical, attention state never reaches the detector, and a missing, mismatched or differently cut-off snapshot fails closed instead of being synthesized

AC-008:
GIVEN:
a detector claim and its canonical episode lineage
WHEN:
episodes are opened, deferred, expired, requalified or projected
THEN:
identity is deterministic from the claim, a duplicate never opens a second episode, a bounded wait ends in a terminal expiry that never resumes, requalification links a new decision without resetting the clock, and every R5-A phase maps onto the existing F4 enums with the downstream-only phases refused

AC-009:
GIVEN:
horizon evidence and an owner-gated horizon policy
WHEN:
a thesis is classified, revised or expired
THEN:
an unconfigured policy, missing holding evidence, a contradiction, an unbanded horizon, missing liquidity or thin structural support abstains to research-only `UNKNOWN`, a supported class carries the declared band with zero allocation authority, the original revision is immutable, and an expired tactical thesis is never relabelled as a long-term investment


AC-010:
GIVEN:
the R5-A module set, the runtime roots and the release-profile contract
WHEN:
imports, sources and profile literals are inspected
THEN:
no R5-A module imports or names an execution, sizing, reservation or credential surface, no runtime root imports an R5-A module, `EVIDENCE_SHADOW` stays the active shadow profile with `TARGET_PAPER` blocked and paper-v2 off, and the two frozen R3 isolation scans that enumerate every module under `app/` are widened by exactly the two authorized R5-A consumers with every substantive assertion of those scans still in force

AC-011:
GIVEN:
the compiled engine mode and an activation attempt
WHEN:
the mode is read or a caller asks for another mode
THEN:
the mode is the literal `off`, no module reads the environment, no `OPIP_` key is introduced, an activation attempt is refused, and unratified policies keep the engine inert

AC-012:
GIVEN:
explicit instants, explicit evidence and the R5-A module set
WHEN:
the same cycle is replayed
THEN:
visibility is receipt time rather than source time, evidence order does not change the coverage record, no module reads a clock or randomness, and two identical runs produce identical reports and thesis evidence

AC-013:
GIVEN:
a restart, a replay and an unknown version token
WHEN:
requests are rebuilt, an evaluation is acknowledged twice, a prior record is revisited or a corrupt version is supplied
THEN:
identities are reproduced exactly, the second acknowledgement is refused, a backfill appends rather than rewriting an earlier record, and an undeclared policy or version token fails closed

AC-014:
GIVEN:
the documented R5-A failure-mode matrix
WHEN:
each scenario is executed
THEN:
warm-up shortfalls, missing features, invalid rows, unsupported instruments, clock skew, duplicates, trigger storms, conflicting detector state, expired claims, re-observed claims, horizon expiry, absent AI or notification paths and concurrent thesis revisions each produce their documented deterministic outcome

AC-015:
GIVEN:
the increment's own scope contract, conformance record and an engineering load scenario
WHEN:
the contract is checked against the acceptance tests, the conformance document is inspected and a labelled burst is replayed
THEN:
the contract's traceability matches the acceptance tests exactly, the document records the component traceability, open owner inputs and activation blockers, and the burst stays bounded, protection-first, reconciling and deterministic


EXPLICITLY OUT OF SCOPE:
- Any activation of the engine, of `shadow` mode, of the Feature Bus, of the canonical writer, of the target spine, of Paper v2 or of TARGET_PAPER, and any change to `SAFE_BASELINE`, `EVIDENCE_SHADOW` or `TARGET_PAPER` or to a Compose literal.
- Production deployment, runtime wiring, a new scheduled service, timer, worker, container, database, event store, allocator database, position registry, second detector, second outcome engine, second scheduler or second deployment control plane.
- Trading, allocation, sizing, reservation, order placement, modification, cancellation, funded or exchange authority, and any R5-A transition into the downstream `SELECTED` or `RESERVED` phases.
- Ratifying `OQ-01`-`OQ-10`: owner assignment, instrument-identity census, horizon boundaries, Market Eye workload, freshness, queue and latency budgets, rotation advantage, sleeve caps, readiness thresholds, R4 closure, learning dispositions and cockpit retention all stay owner decisions.
- R5-B horizon allocation and exit policy, R5-C capital accounting, R5-D portfolio planning and rotation, R5-E autonomous paper management, R5-F learning, R6 committee proof and R7 retirement.
- R4 alert, notification, Telegram, quality-gate and Paper/Freqtrade behaviour, and the canonical writer's write path.
- Changing the IGNITION detector, the F2 feature computation, the F4 episode records or any existing contract module.
- Measuring production effectiveness, latency or profitability: this increment produces no production evidence and claims none.

FROZEN BOUNDARIES:
- Adopted source identity is unchanged: `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`, with its extraction and pin unmodified.
- One canonical writer remains the single domain write path; this increment writes no durable record and registers no consumer.
- One detector family remains IGNITION; the detector module is not modified.
- F11 protection and critical recovery outrank discovery; the coordinator reserves protection and reconciliation capacity ahead of every discovery class.
- Fixed risk sizing, minimum reward-to-risk, position verification and execution safeguards are untouched.
- The production posture stays `EVIDENCE_SHADOW` with `TARGET_PAPER` `BLOCKED` and the Compose core service literal `OPIP_PAPER_V2_MODE=off`.
- No secret, credential, funded endpoint or private key is read, named or logged; no funded credential name appears in any new module.
- The R5-0 register and freeze documents, the R4 contracts and the existing ATDD contracts are not modified or weakened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_a_delayed_event_recovers_only_at_a_later_reconciled_cutoff
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_complete_window_retains_availability_evidence
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_duplicate_and_out_of_order_arrivals_are_non_blocking
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_expectations_derive_from_the_consumed_watermark
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_late_and_clock_skewed_arrivals_are_recorded
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_missing_and_invalid_evidence_is_recorded_not_repaired
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_per_row_gap_reasons_are_catalogued
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_source_and_instrument_failures_block_the_whole_window
AC-001 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_001_unconsumed_window_is_not_usable_evidence
AC-002 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_002_attention_is_not_a_detector_phase_or_a_lifecycle_state
AC-002 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_002_attention_states_and_overlays_are_closed_declared_sets
AC-002 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_002_health_overlays_are_overlays_and_recovery_is_explicit
AC-002 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_002_no_policy_means_inert_attention
AC-003 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_003_a_forged_trigger_identity_or_relationship_is_refused
AC-003 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_003_evidence_rejects_unknown_reason_and_a_mismatched_instrument
AC-003 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_003_trigger_identity_is_deterministic_and_evidence_bound
AC-003 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_003_trigger_record_carries_every_frozen_key
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_cycle_budget_bounds_work_without_dropping_requests
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_debounce_holds_then_steps_exactly_one_rank
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_duplicate_deliveries_coalesce_to_one_evaluation
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_expired_requests_leave_the_queue_and_are_counted
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_fairness_serves_every_instrument_before_repeating_one
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_oscillation_guard_holds_a_progression_inside_minimum_dwell
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_populations_reconcile_under_a_mixed_workload
AC-004 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_004_regression_enters_a_bounded_cooldown_and_never_restarts_focus
AC-005 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_005_a_different_cutoff_or_policy_is_a_different_evaluation
AC-005 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_005_a_duplicate_with_higher_priority_raises_the_queued_priority
AC-005 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_005_stream_and_scheduler_requests_share_one_identity
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_an_accounting_record_that_does_not_reconcile_is_refused
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_both_accounting_populations_reconcile_across_every_path
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_optional_ai_work_is_dropped_first
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_per_instrument_budget_bounds_a_hot_instrument
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_protection_is_never_starved_by_discovery
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_saturation_sheds_discovery_and_is_reported
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_stale_requests_expire_and_retries_are_bounded
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_the_burst_shed_total_separates_refusals_from_evictions
AC-006 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_006_the_empty_record_reconciles
AC-007 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_007_attention_state_never_reaches_the_detector
AC-007 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_007_detector_replay_is_identical_for_identical_inputs
AC-007 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_007_engine_refuses_to_synthesize_or_mismatch_a_snapshot
AC-007 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_007_the_ignition_detector_stays_pure_and_grid_compliant
AC-008 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_008_a_duplicate_delivery_never_opens_a_second_episode
AC-008 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_008_a_wait_is_bounded_and_an_expired_episode_never_resumes
AC-008 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_008_episode_identity_is_deterministic_and_claim_bound
AC-008 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_008_phase_projection_maps_onto_existing_enums_without_authority
AC-008 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_008_requalification_links_a_new_decision_without_resetting_the_clock
AC-009 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_009_a_thesis_revision_is_immutable_and_a_relabel_is_refused
AC-009 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_009_an_unconfigured_horizon_policy_abstains
AC-009 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_009_horizon_bands_are_validated_and_may_not_overlap
AC-009 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_009_missing_or_contradictory_evidence_abstains
AC-009 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_009_position_requires_structural_evidence_not_ignition_alone
AC-010 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_010_no_runtime_root_imports_the_new_modules
AC-010 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_010_release_profile_safeguards_are_unchanged
AC-010 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_010_the_new_modules_carry_no_execution_or_credential_surface
AC-010 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_010_the_r3_isolation_allowlists_were_widened_without_weakening_them
AC-011 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_011_an_activation_attempt_is_refused
AC-011 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_011_an_unratified_policy_keeps_the_engine_inert
AC-011 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_011_the_engine_mode_is_off_and_reads_no_environment
AC-012 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_012_a_full_cycle_replays_identically
AC-012 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_012_evidence_permutation_reproduces_the_same_coverage_record
AC-012 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_012_the_new_modules_hold_no_hidden_clock
AC-012 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_012_visibility_is_receipt_time_not_source_time
AC-013 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_013_a_backfill_cannot_rewrite_a_prior_coverage_record
AC-013 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_013_a_replayed_engine_reproduces_the_same_request_identities
AC-013 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_013_a_restart_after_evaluation_before_acknowledgement_is_idempotent
AC-013 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_013_an_unknown_policy_version_fails_closed
AC-013 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_013_restart_recovery_is_replay_only_and_owns_no_store
AC-014 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_014_edge_case_matrix_fails_closed_as_documented
AC-015 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_015_a_labelled_scheduler_baseline_harness_compares_like_for_like
AC-015 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_015_an_engineering_load_scenario_is_bounded_and_protection_first
AC-015 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_015_the_conformance_record_documents_traceability_and_blockers
AC-015 -> tests/test_opip_r5_a_continuous_opportunity_engine.py::test_ac_015_the_increment_scope_contract_is_self_consistent
IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/events/coverage.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/events/attention.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/events/attention.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/events/attention.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/streaming/coordinator.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/streaming/coordinator.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/events/opportunity_engine.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_phases.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/contracts/thesis.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-010 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_A_CONTINUOUS_OPPORTUNITY_ENGINE.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/events/opportunity_engine.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/events/opportunity_engine.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/events/coverage.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/events/opportunity_engine.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-A-continuous-opportunity-engine.md
AC-015 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_A_CONTINUOUS_OPPORTUNITY_ENGINE.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r5_a_continuous_opportunity_engine.py

DEFERRED DISCOVERIES:
- Owner input for Market Eye workload, freshness, queue and latency budgets (`OQ-04`) is unratified, so `AttentionPolicy` and `CoordinatorLimits` ship no numbers, the engine is inert without them, and every measurement in the conformance record is labelled an engineering scenario rather than an accepted budget.
- Horizon boundary durations (`OQ-03`) are unratified, so the horizon classifier abstains to `UNKNOWN` under the shipped policy; any supported classification requires an owner-ratified band.
- Owner assignment (`OQ-01`) is unresolved; the feature tracker still marks owners `UNASSIGNED` and this increment assigns none.
- R4 closure (`OQ-08`) is not established by this increment; the R5-A rows that depend on it stay conditional.
- The F4 transition surface in `app/` previously created episodes only through the canonical identity functions; the requalification link and the phase projection are new research records with no durable store, and their persistence is a later governed decision.
- Supersession has no v1 canonical terminal token, so `SUPERSEDED` remains a research phase with no canonical record until an owner-approved mapping exists.
- Cross-process recovery, a durable request ledger and stream-worker integration are not implemented: this increment proves in-process determinism and restart replay only.
- No production evidence exists for latency, coverage or usefulness. The engineering load scenario is synthetic and the conformance record states `NOT_PROVEN` for runtime effectiveness.

UNAPPROVED SCOPE CHANGES:
NONE

