INCREMENT:
ATDD-R4-B0-spine-contract-closure

OWNER-APPROVED INTENT:
This is the OWNER-authorized R4-B0 spine contract closure. It closes the increment that converted the already-approved F3-F7 architecture into a coherent set of typed, deterministic, direction-aware contracts and proved them composable, without activating any runtime authority. It creates no new architecture and changes no production economics; it records the invariants the increment established and traces them to the production contracts and the adversarial/composition evidence.

R4-B0 ESTABLISHES A DORMANT TARGET SPINE, NOT A PRODUCTION CUTOVER. Nothing here activates Paper-v2, the Feature Bus, F7, or any funded/exchange/Committee authority, and no legacy path is retired. The production posture after this increment remains: Feature Bus `off` and pinned, `OPIP_PAPER_V2_MODE` unset, F7 dormant and unwired, Freqtrade dry-run and Paper-v1 unchanged as the legacy paper engines, Top-8 and profit-ranking unchanged as the live admission path.

STARTING SHA. `origin/main` = `900482dbc4f52c0a2ca40c7d88e162048cd291ba`. This increment is eight bounded commits on `feature/r4b-f8-paper-v2-precutover`; it is not merged or deployed by this contract.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (spine): "Approved market inputs -> validated observations -> retained aggregates and checkpoints -> FeatureSnapshot -> deterministic detector and opportunity lifecycle. Runtime decision path: Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution." R4-B0 makes that spine expressible as typed contracts; it does not put it on the live cycle.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3: "Missing evidence is never favorable." R4-B0's zero-model proof is a direct instance.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4 (paper execution contract): "Represent NO_FILL, PARTIAL_FILL and FULL_FILL separately from TARGET, STOP, TIMEOUT and independently triggered RISK_EXIT." and portfolio objective "against cash/no-trade and a frozen comparator" with no Kelly, leverage, covariance optimizer or dynamic risk parity.
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R4: "Paper v2 cutover proof ... Cutover stays blocked until all of these are true". R4-B0 prepares the contracts; it does not satisfy or claim the cutover.
- `docs/architecture/v1.2/F_ECONOMIC_PORTFOLIO_CONTRACT.md`: primary objective is expected portfolio net dollars against cash/no-trade and the frozen comparator; reservation is atomic against a portfolio version and research simulation does not share reservations with approved allocation.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F8 row: "Paper v2 has no pending-limit state machine and no short engine." R4-B0 extends the canonical representation for simulated SHORT while leaving the mode inactive.
- `docs/atdd/scope-contracts/ATDD-R4-F8-paper-v2-cutover-readiness.md` and `ATDD-R4-F8A-readiness-observability.md`: the two immediately preceding increments. Their recorded `SHORT_AUTHORITY_MISSING` result was correct for the code as it stood then and is preserved as history, not rewritten.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable; a completed increment must not permanently own it.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the R4-B0 closure increment and the repository's movable active-increment pointer
WHEN:
the closure contract and the pointer are inspected
THEN:
the contract exists and declares its own increment identity, the pointer resolves to an existing scope contract, and this increment never pins the global pointer to its own identity

AC-002:
GIVEN:
the F5 feasibility seam
WHEN:
its target input and purity are inspected
THEN:
`FeasibilityEvidence` is the canonical typed target input with a deterministic primitive-field fingerprint, F5 no longer requires the legacy scanner snapshot type, a single explicit-args adapter preserves legacy callers, the seam reads no clock, network, database or environment, and SHORT execution evidence is evaluated without a venue refresh

AC-003:
GIVEN:
the execution geometry boundary
WHEN:
its ownership is audited
THEN:
exactly one deterministic entry/exit geometry owner exists with a content-derived identity, the kernel is a pure function of primitives, and the legacy advisor delegates to that owner instead of carrying a second algorithm

AC-004:
GIVEN:
the canonical Paper-v2 admission contract
WHEN:
its direction semantics are exercised
THEN:
it defines exactly the four direction/role/side pairs, refuses every other pair, reads a historical pre-direction record narrowly as LONG, and fails closed on a direction-contract record that omits its direction

AC-005:
GIVEN:
the canonical writer's order-intent ancestry validation
WHEN:
a role/side pair is checked
THEN:
the side is derived from the admitted trade's committed direction rather than a caller-supplied field, the historical long-only pair invariant is gone, and a recovery fill uses the committed ENTRY side instead of assuming a long buy

AC-006:
GIVEN:
the direction-aware Paper-v2 contract
WHEN:
SHORT reachability, plan geometry and economics are inspected
THEN:
the protection-plan builder accepts a SHORT's descending targets with an above-entry stop, the protection runtime derives the exit side and the executable book side from the committed direction, and the canonical gross P&L is signed by the admitted direction

AC-007:
GIVEN:
the F7 to Paper-v2 handoff contract
WHEN:
its lineage and authority are inspected
THEN:
one typed handoff binds the full F7 lineage and the exact geometry, the execution candidate identity is derived rather than copied, only a SELECTED decision produces a handoff, and the contract exposes no writer, reservation or execution authority and takes no clock, uuid or retry counter into its identity

AC-008:
GIVEN:
the real production composition harness
WHEN:
a genuine opportunity traverses F3 to F7
THEN:
both LONG and SHORT reach a SELECTED decision and exactly one handoff, the direction and geometry are preserved end to end, and the SHORT geometry stops above and targets below its entry reference

AC-009:
GIVEN:
an environment with no admissible trusted forecast evidence
WHEN:
the real composition runs
THEN:
F6 abstains without fabricating a probability or expected return, F7 reports its own abstention which stays distinct from cash/no-trade and from selection, and no allocation, reservation plan or handoff is produced

AC-010:
GIVEN:
the repository-controlled configuration and the live cycle
WHEN:
the dormant posture is audited
THEN:
the Feature Bus is off and Paper-v2 is unset in production configuration, and neither the unified cycle nor the opportunity scan imports the target spine

AC-011:
GIVEN:
the R4-B0 production contracts
WHEN:
their imports and calls are audited
THEN:
none imports or calls a private exchange client, an order, borrow, leverage or margin-activation surface, or the Committee

AC-012:
GIVEN:
the historical R4-A paper contract and the R4-B0 extension
WHEN:
the history is inspected
THEN:
the R4-A contract and its recorded SHORT-authority blocker remain intact as history, the closure records the extension explicitly, and the historical long-only assertion was converted to a direction-contract assertion rather than deleted

EXPLICITLY OUT OF SCOPE:
- Wiring any part of the F3-F7 spine into `run_cycle`, `scan_opportunities`, Telegram, alerts, paper v1, Freqtrade or any dashboard
- Activating Paper-v2, the Feature Bus production mode, F7 runtime admission, or the Committee
- Funded or live trading, exchange order placement, modification, cancellation or confirmation, margin, asset borrow or leverage
- Retiring, deleting or re-weighting the legacy selector, Top-8, profit-ranking, Freqtrade dry-run or Paper-v1
- A Paper-v2 pending-limit state machine, a second short engine, a second geometry engine, a second paper ledger, a second reservation writer or a second evidence store
- Changing F3-F7 economics, selector behavior, forecast behavior, geometry behavior, P&L sign, protection semantics, handoff semantics, feature flags or deployment behavior
- Starting R4-B1 or any later increment, merging, or deploying this branch
- Modifying architecture documents, the v1.4.3 DOCX, the ATDD checker, workflows or `pyproject.toml`
- Weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off` in the repository-controlled production compose.
- `OPIP_PAPER_V2_MODE` remains unset in production configuration; Paper-v2 is not activated.
- F7 remains dormant and is not the admission authority; Top-8 and profit-ranking remain the live admission path.
- Freqtrade dry-run and Paper-v1 remain the legacy paper engines and are not retired.
- FeatureSnapshot remains feature/detector evidence; it does not become a universal execution or risk record.
- F6 remains the sole owner of forecast probability, expected return, uncertainty and validity horizon, and never fabricates them.
- The F7 allocation remains a plan and a ceiling; the canonical Paper-v2 writer remains the sole reservation authority.
- Paper-v2 direction is bound to committed admitted ancestry and cannot be forged by a caller.
- Paper-v2 SHORT is simulation only: no borrow, margin, leverage, funded credentials or exchange order authority.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled.
- Merging and deploying this branch are not authorized by this contract.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_001_closure_contract_and_pointer_are_consistent
AC-002 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_002_f5_target_evidence_is_typed_and_pure
AC-003 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_003_one_execution_geometry_owner
AC-004 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_004_direction_bound_admission_contract
AC-005 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_005_writer_derives_direction_from_ancestry
AC-006 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_006_short_is_reachable_and_direction_correct
AC-007 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_007_deterministic_handoff_and_candidate_bridge
AC-008 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_008_real_spine_composition_selects_for_long_and_short
AC-009 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_009_zero_model_yields_no_trade_with_distinct_states
AC-010 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_010_dormant_posture_unchanged_by_closure
AC-011 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_011_no_funded_exchange_or_committee_authority
AC-012 -> tests/test_opip_r4_b0_spine_contract_closure.py::test_ac_012_historical_long_only_fact_is_preserved

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B0-spine-contract-closure.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_contract_closure.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/contracts/feasibility_evidence.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/feasibility.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/contracts/execution_geometry.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/execution_geometry.py
AC-003 -> OHM-Trade-Agent-v1/app/services/entry_exit_advisor.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/paper_execution_runtime.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/canonical/models.py
AC-006 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_plan.py
AC-006 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-006 -> OHM-Trade-Agent-v1/app/services/paper_v2_execution.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/contracts/paper_execution_events.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/contracts/portfolio_paper_handoff.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_portfolio_paper_handoff.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_composition.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_composition.py
AC-010 -> OHM-Trade-Agent-v1/app/core/config.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/features/publisher.py
AC-010 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-010 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-010 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_short_direction_matrix.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F8-paper-v2-cutover-readiness.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_increment6a_bc3.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_cross_scan_simulation_bc3.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_execution_bc1.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_execution_bc2.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_execution_bc3.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_remediation_bc3.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_portfolio_read_bc3.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_decision_context_v2_bc3a.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_review_remediation.py

DEFERRED DISCOVERIES:
- The R4-B0 review loop raised ten correctness findings against these contracts; all were fixed and regression-proven in `tests/test_opip_r4_b0_review_remediation.py`: a tautological handoff causality guard, quantity sized off an unbound reference, a non-actionable geometry bridging, an un-cross-checked instrument symbol, an under-validated handoff, opposite directions sharing a disposition identity, a format marker breaking idempotent legacy admission retries, admission direction defaulting to LONG, the F5 adapter pre-empting its own ordered checks, and the geometry reason being excluded from its identity.
- A later review raised a migration concern: a LONG admitted under a temporary direction-qualified release would be missed once the historical LONG payload was restored. Bounded historical proof showed that state could not exist. The temporary identity was live in production only while `fb1b8a57` was deployed - the sole deployed SHA that carried it - and Paper-v2 was off for that entire window. `run_paper_v2_opportunity` is the sole producer of Paper-v2 disposition records and refuses at the activation gate, which precedes the identity derivation and every canonical interaction; `paper_v2_active` requires the exact mode `active` and is fail-closed otherwise. The `ohm-trade-agent` service loads an untracked `.env` (`env_file: .env`) as well as the Compose `environment:` block, so the repository alone cannot prove the environment, but the sanctioned readiness probe runs *inside that container* (`docker exec ohm-trade-agent python -m app.jobs.report_paper_v2_cutover_readiness`) and therefore observes exactly the application environment. That probe, captured immediately after `fb1b8a57` deployed and inside the window, reported `production_sha=fb1b8a57...` with `"mode": null` and `PAPER_V2_MODE_EVIDENCE_UNAVAILABLE`, i.e. no explicit mode was present, so the mode resolved to `off` and the gate was closed. An untracked `.env` carrying the mode would have made it explicit and the probe would have reported `mode: "active"`. The scan additionally resolves paper authority to `LEGACY` when inactive, so the only new-disposition caller is never invoked, and the recovery/protection seams only advance already-committed state. The alternate-identity compatibility layer was therefore removed and replaced by an invariant test (`test_only_one_disposition_identity_derivation_exists`, `test_paper_v2_inactive_blocks_execution_before_any_canonical_write`). The interim migration state is counterfactual for this installation; no migration mechanism was added inside R4-B0. Limitation: exhaustive canonical-record enumeration is not exposed by the sanctioned control plane (the read-only command reports readiness gates, not record inventories), so the proof is structural plus in-container runtime observation rather than a full ledger dump. A genuine future migration need would require its own explicit reconciliation contract, which exceeds this increment.
- Open resume-semantics question for R4-B1: the decision-context identity deliberately excludes `emitted_at`, `process_instance_id` and `artifact_or_build_id`, so an exact retry of one opportunity reproduces the identical `context_id` and resumes correctly. A *re-qualification* of the same episode/symbol/direction with changed decision facts would instead derive a different context while reusing the disposition, and the writer's reservation-ancestry check would reject the new entry intent. Deciding whether such a re-qualification must resume under the committed context or be refused as a conflicting decision is an R4-B1 contract decision, not an R4-B0 cleanup; it is unreachable while Paper-v2 is dormant.
- R4-B1 runtime wiring of the F3-F7 spine into the live cycle is a separate OWNER increment; this contract neither starts nor authorizes it.
- The Feature Bus `active` mode is defined but remains off; the owner decision that activates it is a later increment.
- Paper-v2 SHORT is simulation-only and inactive; activating the mode is a later owner action, so the earlier `SHORT_AUTHORITY_MISSING` cutover blocker is now an activation blocker rather than a missing-capability blocker.
- Item 4e found two long-only assumptions in production code (protection-plan target ordering and canonical gross P&L signing). Both were implementation defects local to the paper contract, not architecture changes; they are fixed with regression coverage in the SHORT safety matrix.
- Whether the F7 reservation plan should ever be applied by a production reservation writer remains an owner decision; this increment keeps it a plan.

UNAPPROVED SCOPE CHANGES:
NONE
