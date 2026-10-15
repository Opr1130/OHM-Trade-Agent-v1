INCREMENT:
ATDD-R5-M03-f11-failure-independence-matrix

OWNER-APPROVED INTENT:
This is the F11 failure-and-independence increment of the owner-directed Mission 03 work, inside frozen O'Pip v1.5.0. Mission 03 named fourteen failure scenarios and asked that two things hold in every one of them: F11's protective priority, and a fail-closed admission path that still protects open-position safety obligations.

What the audit found, and what this increment therefore is. Twelve of the fourteen scenarios are already proven by existing named tests, and the audit recorded the other two. This increment closes the one that genuinely had no test - AI and Committee unavailability - and asserts the matrix itself, so that every named scenario is accounted for. It re-proves nothing.

What changes where. No application module changes. This increment adds one acceptance-test module and its contract, edits the audit it corrects, edits the audit's acceptance test to match the corrected vocabulary, and updates the pointer and the build report. It adds no route, producer, schema, writer, job, scheduler entry, configuration key, threshold or authority, and it activates no runtime.

The finding this increment had to report honestly. Closing the Committee scenario required walking the protective authority's **transitive** import closure rather than only its direct imports. That walk found that `app.services.paper_v2_protection_runtime` transitively reaches `app.services.chief_analyst`, `app.services.entry_exit_advisor` and the `app.opip.decision_intelligence` family, through shared leaf modules - `app.opip.decision.thresholds`, `app.opip.decision.versioning` and `app.opip.canonical.writer`. Protection never calls any of them, which the behavioural test proves; but the module graph is not isolated, and the first draft of this increment asserted that it was. The assertion failed, and the response was to record the coupling rather than to relax the test.

KEY LAW - A FAILING SAFETY TEST IS NOT PERMISSION TO RELAX A GATE. The strong form of the assertion was false. The correct response is to record exactly what is true - protection never calls the surface, and the coupling set is frozen so it cannot grow - and to file the isolation gap as an owner decision. It is not to delete the census, widen the allow-list silently, or relabel the result a pass.

KEY LAW - A CORRECTION SAYS SO. Three items were misclassified in the audit's first revision. They are corrected in place, the removed vocabulary member is deleted from both the document and the audit's test, and the correction is described in the document rather than absorbed.

ARCHITECTURE REFERENCES:
- docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md sections SC-11, SC-12 and SC-13: the paper-isolation invariants, the canonical-ordering contract and the readiness/legacy-disposition contract this increment works inside. The freeze is referenced, never restated.
- docs/architecture/v1.5.0/ARCHITECTURE.md: the frozen v1.5.0 architecture, including the independent-protection clause that protection must run before and independently of the target spine.
- docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md: the audit this increment closes `C3` in and corrects.
- docs/atdd/scope-contracts/ATDD-R5-M03-portfolio-execution-audit.md: the increment whose vocabulary and totals this increment adjusts, with the adjustment declared.
- tests/test_opip_r5_e_f11_independent_protection.py: the existing F11 independence harness whose `_DownTransport`, `_arm_trades`, `_run_scheduled_sweep`, `_book_state` and `_app_imports` helpers this increment reuses rather than reimplements.
- tests/test_opip_paper_v2_protection_runtime_bc3.py: the existing protection-runtime harness whose `_Env`, `_Clock`, `_Settings` and `_transport` doubles this increment reuses.
- app/services/paper_v2_protection_runtime.py and app/services/chief_analyst.py: the authority under test and the Committee surface it is proven not to call.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-310:
GIVEN:
the fourteen failure scenarios Mission 03 named and the audit's own Workstream C rows
WHEN:
the scenario slugs and their cited evidence are compared
THEN:
every named scenario appears exactly once in the audit, the three non-scenario rows are the two independence assertions and the recorded coupling, every scenario row cites a test node that still exists, and the coupling row cites its module and is classified owner-gated

AC-311:
GIVEN:
an armed position whose approved stop is crossed, the production Committee and AI entry point raising on every call, and the protective authority's transitive application import closure
WHEN:
the scheduled production protection sweep runs, and the closure is walked
THEN:
the sweep still reads the venue, still triggers, still takes the full remaining exposure and never consulted the Committee, and the closure reaches exactly the recorded coupling set so any new edge fails closed

AC-312:
GIVEN:
two armed positions and a venue transport that raises on every call
WHEN:
the scheduled protection sweep runs
THEN:
the production seam really tried the venue, nothing triggered, nothing was exited, every obligation stayed armed and reserved with its entry fill as its only fill, no exit order was fabricated, and the canonical book is unchanged

AC-315:
GIVEN:
the audit this increment corrected and the audit acceptance test that asserts its vocabulary
WHEN:
the declared vocabulary, the corrected classifications and the correction sections are read
THEN:
the removed class is absent from the declared vocabulary and from the audit test, the corrected items carry their new classes, and both the correction and the uncovered coupling are described in the document

EXPLICITLY OUT OF SCOPE:
- Any change to an application module, contract, route, producer, schema, store, writer, job, cron unit, configuration key or release profile.
- Any change to the protection runtime, the canonical writer's atomic protection action, the live-domain health decision or the scheduler topology.
- Any new protection rule, trigger, precedence, threshold or ordering.
- Any activation of Paper-v2, `TARGET_PAPER`, the Feature Bus or any paper runtime.
- Any live order, funded credential or exchange authority.
- Any refactor of `app.opip.decision.thresholds`, `app.opip.decision.versioning` or `app.opip.canonical.writer` to remove the recorded transitive coupling.
- Re-proving any scenario an existing named test already proves.

FROZEN BOUNDARIES:
- `TARGET_PAPER` remains `BLOCKED` and the production posture remains `EVIDENCE_SHADOW`.
- The legacy paper engine's ownership and coexistence window are unchanged.
- F11's protective priority, the writer's atomic protection action and the live-domain health decision are unchanged and unweakened.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.
- The audit's own totals and vocabulary are adjusted by declaration, not silently; every `PROVEN` item keeps the node that proved it.
- No new canonical writer, event store, allocator database, outcome engine or position registry is introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-310 -> tests/test_opip_r5_m03_f11_failure_matrix.py::test_ac_310_every_named_scenario_is_in_the_matrix_with_a_resolving_node
AC-311 -> tests/test_opip_r5_m03_f11_failure_matrix.py::test_ac_311_the_committee_surface_cannot_stop_the_exit
AC-311 -> tests/test_opip_r5_m03_f11_failure_matrix.py::test_ac_311_the_protective_authority_cannot_reach_the_committee_or_ai_surface
AC-312 -> tests/test_opip_r5_m03_f11_failure_matrix.py::test_ac_312_a_dead_feed_changes_no_book_and_admits_nothing
AC-315 -> tests/test_opip_r5_m03_f11_failure_matrix.py::test_ac_315_the_audit_correction_is_recorded_in_both_directions

IMPLEMENTATION MAP:
AC-310 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M03-f11-failure-independence-matrix.md
AC-310 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_f11_failure_matrix.py
AC-311 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_f11_failure_matrix.py
AC-312 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_f11_failure_matrix.py
AC-315 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_f11_failure_matrix.py
AC-310 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-311 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-315 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-315 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_coverage_matrix.py
AC-310 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-310 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The protective authority's transitive import closure reaches `app.services.chief_analyst`, `app.services.entry_exit_advisor` and the `app.opip.decision_intelligence` family through shared leaf modules. Protection never calls them, and the behavioural proof is stronger than the import proof, but the module graph is not isolated. Removing the edges means changing shared modules that the writer, thresholds and versioning surfaces all depend on, so it is an owner decision recorded as `C17` rather than an engineering gap.
- The audit's first revision misclassified three items because it classified from the mission's wording rather than from the existing harness. The harness contained an unavailable-venue transport that raises, a broken-surface matrix that already includes the dashboard, and a pre-trade suite that already proves early validation fails closed. All three are corrected and the correction is recorded in the audit's section 8.
- No headless browser harness exists in this repository, and no dashboard-rendering claim is made. The dashboard scenario is proven at the seam the mission asked about: its breakage cannot stop a protective exit.
- The failure matrix asserts its scenarios through the audit rather than by re-driving all fourteen, so that a scenario already proven is cited once and proven once.
- `C3` was the only genuinely unproven scenario and this increment closes it. `B16` remains, to be closed by `ATDD-R5-M03-paper-lifecycle-economic-invariants`.

UNAPPROVED SCOPE CHANGES:
NONE


