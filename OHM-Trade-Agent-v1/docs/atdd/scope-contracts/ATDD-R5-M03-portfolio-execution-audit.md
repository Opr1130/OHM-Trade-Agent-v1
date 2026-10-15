INCREMENT:
ATDD-R5-M03-portfolio-execution-audit

OWNER-APPROVED INTENT:
This is the audit increment of the owner-directed Mission 03 portfolio-execution and protection work, inside frozen O'Pip v1.5.0. It answers one question with evidence and implements nothing: of the capital-integrity, isolated-paper-lifecycle and F11-failure items Mission 03 asks to verify, which does the repository already prove, which test node proves it, and which are genuinely unproven?

Why an audit comes first. The repository already carries an unusually large amount of relevant evidence - 98 tests across the R5-C and R5-D capital and portfolio-planning suites, 22 across R5-E capital admission, 24 across R5-E research paper execution, 22 across R5-E independent protection, 256 across the paper execution contract and B/C-1/B/C-2 suites, and 38 in the B/C-3 protection runtime alone. An increment that re-proved any of that would be manufacturing work, and an increment that assumed coverage without citing it would be asserting without evidence. The audit resolves both: it promotes an item to `PROVEN` only when a named acceptance test already exists, and it records everything else honestly.

What changes where. No application module changes. This increment adds one evidence document, one acceptance-test module and the records that accompany them. It adds no route, producer, schema, writer, job, scheduler entry, configuration key, threshold or authority, and it activates no runtime.

The finding that shapes the mission. Forty-five items were assessed. Thirty-nine are already proven by a named existing test. Three are genuinely unproven, two are only partially proven, and exactly one is blocked on an owner decision. That means two follower increments close the entire real gap, and neither of them re-proves anything already proven.

The five real gaps, named. `B8` early validation and `C1` market-feed outage are partially proven - a neighbouring behaviour is proven but the item as the mission words it is not. `C3` AI or Committee unavailability and `C5` dashboard outage have no test at all. `B16` asserts that the economic identity - cash, fees, quantity, remaining obligation, fills and realized net economics - is validated after every lifecycle transition, and no existing test does that as such. `B13` rotation is owner-gated: the refusal path is proven, and the execution path cannot be ratified because `OQ-05` fixes no rotation advantage, cooldown, requalification or turnover value.

What this audit refuses. It does not promote a neighbouring test into proof of the item the mission named. It does not claim the paper runtime is active: `TARGET_PAPER` is `BLOCKED`, the production posture is `EVIDENCE_SHADOW`, and every item is proven in process on an isolated canonical store. It does not claim any item is production-proven. It does not claim rotation is implemented.

KEY LAW - AN AUDIT MAY NOT PROMOTE ITSELF. Recording that a test exists is not evidence that the behaviour is right, and a citation that does not resolve is a defect. Every cited file must exist and every cited test node must still be present, so a renamed or deleted test breaks this document rather than silently weakening it.

KEY LAW - A GAP MUST BE NAMED, NOT ABSORBED. Every item that is not `PROVEN` and not owner-gated is named by exactly one follower increment, and the acceptance test fails if a gap is orphaned or if a follower claims an item that is already proven.

ARCHITECTURE REFERENCES:
- docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md sections SC-06 through SC-13: the frozen records, owning modules, reuse statuses, open owner inputs and invariants this audit maps items onto. The freeze is referenced, never restated.
- docs/architecture/v1.5.0/ARCHITECTURE.md: the frozen v1.5.0 architecture this mission works inside.
- docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md: the R5-C, R5-D, R5-E and F11 rows this audit reports and does not alter.
- app/opip/contracts/paper_execution.py, app/opip/contracts/paper_economics.py, app/opip/contracts/portfolio.py, app/services/capital_rotation_intelligence.py, app/services/paper_v2_protection_runtime.py: the owning modules the audit cites.
- tests/test_opip_r5_c_capital_intelligence.py, tests/test_opip_r5_d_portfolio_planning.py, tests/test_opip_r5_e_capital_admission_integration.py, tests/test_opip_r5_e_portfolio_lifecycle_closure.py, tests/test_opip_r5_e_research_paper_execution.py, tests/test_opip_r5_e_f11_independent_protection.py, tests/test_opip_paper_execution_bc2.py, tests/test_opip_paper_v2_protection_runtime_bc3.py: the acceptance tests the audit cites as proof and machine-probes.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-300:
GIVEN:
the recorded coverage matrix with its declared class, workstream and contract vocabularies and its declared totals
WHEN:
every item is parsed and every field is compared with the vocabularies, and the totals are recomputed from the items
THEN:
each item carries exactly the declared keys, every class, workstream and contract value is one the document declares, every item identifier is unique, and the item, class and workstream counts equal the recomputed values

AC-301:
GIVEN:
the evidence path and the named test node each item cites
WHEN:
each path is resolved against the repository root and each node name is looked for in the file it is attributed to
THEN:
every path exists and every node is present, every class is used by at least one item, and no item is recorded PROVEN on a module citation without a test node

AC-302:
GIVEN:
the declared follower increments, the items that are not PROVEN, and the items that are owner-gated
WHEN:
the closed sets are compared with the gap set
THEN:
every non-PROVEN, non-owner-gated item is named by exactly one follower, no follower claims an already-proven item or an owner-gated item, and the follower identifiers are exactly the declared ones in order

AC-303:
GIVEN:
the matrix's own prose and the application modules it cites
WHEN:
its refusal section is read, its rotation item is inspected, and every application citation is checked
THEN:
the document states that the paper runtime is not active, that no item is production-proven and that rotation is owner-gated, and every application citation is read-only evidence for an existing module rather than a change made by this increment

EXPLICITLY OUT OF SCOPE:
- Any change to an application module, contract, route, producer, schema, store, writer, job, cron unit, configuration key or release profile.
- Any activation of Paper-v2, `TARGET_PAPER`, the Feature Bus or any paper runtime.
- Any threshold, sleeve cap, cooldown, profit target, rotation advantage, requalification rule or turnover budget.
- Any live order, funded credential, exchange authority or production protection rule.
- Re-proving an item that an existing named test already proves.
- Any change to the R5-0 contract freeze, the requirements register or any SC-* invariant.

FROZEN BOUNDARIES:
- `TARGET_PAPER` remains `BLOCKED` and the production posture remains `EVIDENCE_SHADOW`.
- The legacy paper engine's ownership and coexistence window are unchanged.
- F11's protective priority, the canonical writer's atomic protection action and the live-domain health decision are unchanged and unweakened.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.
- Rotation execution stays `OQ-05`-gated; this increment records the gate and ratifies no value.
- No new canonical writer, event store, allocator database, outcome engine or position registry is introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-300 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_300_every_item_uses_the_declared_vocabulary
AC-300 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_300_the_declared_totals_recompute_from_the_items
AC-301 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_301_every_cited_evidence_path_exists
AC-301 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_301_every_cited_test_node_still_exists
AC-301 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_301_a_proven_item_and_a_diminished_item_are_treated_differently
AC-302 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_302_every_follower_closes_named_items_and_covers_every_gap
AC-303 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_303_the_matrix_claims_no_activation_and_no_production_proof
AC-303 -> tests/test_opip_r5_m03_coverage_matrix.py::test_ac_303_the_matrix_touches_no_application_module

IMPLEMENTATION MAP:
AC-300 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M03-portfolio-execution-audit.md
AC-300 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-300 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_coverage_matrix.py
AC-301 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-301 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_coverage_matrix.py
AC-302 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-302 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_coverage_matrix.py
AC-303 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-303 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_coverage_matrix.py
AC-300 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-300 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- Ninety-eight tests already exist across R5-C and R5-D, 256 across the paper execution contract and B/C-1/B/C-2 suites, 38 in the B/C-3 protection runtime, and 22 each across R5-E capital admission and R5-E independent protection. Coverage of this mission's checklist is therefore far higher than the checklist implies, and the honest finding is that only five items need work.
- The B/C-3 protection runtime already proves the crash, restart, lost-acknowledgement, insufficient-depth, clock-regression and terminal-capacity behaviours that Mission 03's failure list names. The follower failure-matrix increment therefore adds the scenarios that are genuinely absent rather than re-walking the twelve that are present.
- `test_existing_exposure_stays_protected_after_the_mode_is_turned_off` and `test_existing_exposure_stays_protected_when_cutover_readiness_is_unavailable` already assert F11 independence at the runtime level; the follower matrix extends that to the named subsystem outages.
- A rotation execution path cannot be proven while `OQ-05` is open, because no advantage value, cooldown, requalification rule or turnover budget is ratified. The item is recorded as owner-gated rather than as an engineering gap.
- No headless browser harness exists in this repository, and no browser or dashboard-rendering claim is made by this increment.

UNAPPROVED SCOPE CHANGES:
NONE


