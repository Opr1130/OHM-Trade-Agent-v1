INCREMENT:
ATDD-R5-M03-paper-lifecycle-economic-invariants

OWNER-APPROVED INTENT:
This is the final increment of the owner-directed Mission 03 work, inside frozen O'Pip v1.5.0. The audit left exactly one genuinely unproven item: `B16`, the requirement that the economic identity - cash, fees, quantity, remaining obligation, fills and realized net economics - be validated after every lifecycle transition. The existing B/C-3 suite proves the transitions and the states, but it does not recompute the economics from the frozen model at each step, so nothing asserted that the committed numbers are the frozen model's numbers.

What changes where. No application module changes. This increment adds one acceptance-test module and its contract, closes the audit's last item, and updates the pointer and the build report. It adds no coefficient, threshold, route, producer, schema, writer, job, scheduler entry or configuration key, and it activates no runtime.

What it proves. Driving the real production protection runtime through entry fill, activation, a crossed stop, the exit fill, the flat reconciliation and final verification, and recomputing after every step: that each committed fill's four cost components equal the frozen registry's `cost_components(quantity, price)` for that fill's own quantity and price and that each fill names the frozen economic model version rather than the live settings; that entry quantity minus exit quantity equals the item's remaining obligation at every step and reaches exactly zero only when flat; that each committed reconciliation states the signed notional gross, the summed four-component cost, and a net that is exactly gross minus cost, with filled and remaining quantities equal to the committed population; and that a further sweep after final verification invents no fill and no reconciliation.

The coefficients are read, not restated. The fee rate and the slippage bound come from `app.opip.contracts.paper_economics` at test time. The test never hardcodes 0.4% or 10 bps, so a change to the frozen registry changes what the test demands rather than being silently accepted.

KEY LAW - THE ECONOMIC IDENTITY IS RECOMPUTED, NOT TRUSTED. The runtime's own reconciliation is compared against a recomputation from the committed fills and the frozen registry. A test that merely read the runtime's own totals back would prove nothing, because it would agree with itself.

KEY LAW - A FROZEN REGISTRY MAY NOT DRIFT SILENTLY. An unknown economic model version is refused rather than falling back to current settings, so a historical execution cannot be reinterpreted under today's coefficients. This increment asserts that refusal.

ARCHITECTURE REFERENCES:
- docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md sections SC-10 and SC-11: the expectation and outcome learning record's frozen-cutoff and immutable-payload invariants, and the paper-isolation invariants this increment works inside.
- docs/architecture/v1.5.0/ARCHITECTURE.md: the frozen v1.5.0 architecture, including the paper-plane isolation clause.
- docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md: the audit whose last item this increment closes.
- app/opip/contracts/paper_economics.py: the frozen economic registry and `cost_components` this increment recomputes against.
- app/opip/contracts/paper_execution.py: `PAPER_ECONOMIC_MODEL_VERSION` and the lifecycle states the traversal passes through.
- app/services/paper_v2_protection_runtime.py: the production runtime under test, whose real sweep commits every transition.
- tests/test_opip_paper_v2_protection_runtime_bc3.py: the existing harness whose `_Env`, `_Clock`, `_open_entry`, `_sweep`, `_next_scan`, `_transport`, `_rows` and `_item` doubles this increment reuses.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-320:
GIVEN:
the real production protection runtime driving the whole lifecycle over an isolated canonical store, and the frozen economic registry
WHEN:
the committed fills are recomputed from that registry after the entry fill and after the exit fill, and a further sweep runs after final verification
THEN:
every committed fill's fee, slippage, spread and other cost equal the frozen model applied to that fill's own quantity and price, every fill names the frozen economic model version rather than the live settings, the traversal passes through activation, exit and final verification with one fill then two, and the further sweep adds no fill

AC-321:
GIVEN:
the same traversal's committed fill quantities and the item's recorded remaining quantity
WHEN:
entry minus exit is compared with the remaining obligation after every step
THEN:
they are equal at every step, the difference never goes negative, and it reaches exactly zero only when the position is flat, with the entry and exit quantities equal at termination

AC-322:
GIVEN:
a finalised position with a committed exit and its committed reconciliations
WHEN:
each reconciliation is recomputed from the committed fills and the frozen registry
THEN:
the gross is the signed notional sum, the recorded cost is the summed four-component cost, the net is exactly gross minus cost, the filled entry and exit quantities and the remaining quantity state the committed population, every reconciliation names the frozen model version, both reconciliation states are stated in order, and the net is strictly worse than the gross for this losing long

AC-323:
GIVEN:
the frozen economic registry
WHEN:
an unknown or empty economic model version is requested
THEN:
the registry refuses rather than falling back to live settings, so a historical execution cannot be reinterpreted under today's coefficients

EXPLICITLY OUT OF SCOPE:
- Any change to an application module, contract, route, producer, schema, store, writer, job, cron unit, configuration key or release profile.
- Any change to the fee rate, the slippage bound, the cost components or the economic model version.
- Any new cost, fee, funding, borrow or financing component.
- Any activation of Paper-v2, `TARGET_PAPER`, the Feature Bus or any paper runtime.
- Any live order, funded credential or exchange authority.
- Any change to the protection runtime's triggers, precedence, thresholds or ordering.
- Re-proving any transition, state or invariant an existing named test already proves.

FROZEN BOUNDARIES:
- `TARGET_PAPER` remains `BLOCKED` and the production posture remains `EVIDENCE_SHADOW`.
- The legacy paper engine's ownership and coexistence window are unchanged.
- F11's protective priority, the writer's atomic protection action and the live-domain health decision are unchanged and unweakened.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.
- The audit's totals and vocabulary are adjusted by declaration; the audit's last open item becomes proven and its follower list becomes empty.
- No new canonical writer, event store, allocator database, outcome engine or position registry is introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-320 -> tests/test_opip_r5_m03_paper_economic_invariants.py::test_ac_320_every_fill_after_every_transition_matches_the_frozen_model
AC-321 -> tests/test_opip_r5_m03_paper_economic_invariants.py::test_ac_321_entry_minus_exit_equals_the_remaining_obligation
AC-322 -> tests/test_opip_r5_m03_paper_economic_invariants.py::test_ac_322_the_reconciliation_states_exactly_the_committed_totals
AC-323 -> tests/test_opip_r5_m03_paper_economic_invariants.py::test_ac_323_the_frozen_economic_model_refuses_an_unknown_version

IMPLEMENTATION MAP:
AC-320 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M03-paper-lifecycle-economic-invariants.md
AC-320 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_paper_economic_invariants.py
AC-321 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_paper_economic_invariants.py
AC-322 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_paper_economic_invariants.py
AC-323 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_paper_economic_invariants.py
AC-320 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md
AC-320 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_coverage_matrix.py
AC-320 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m03_f11_failure_matrix.py
AC-320 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-320 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The runtime records four cost components per fill and charges fees and slippage on both legs, so a round trip's net is strictly worse than its gross. This increment asserts that relationship rather than a P&L target.
- The frozen registry's refusal of an unknown version is the mechanism that keeps a historical execution bound to the coefficients it was executed under. It is asserted here rather than assumed.
- `B13` rotation and `C17` the protective authority's transitive import coupling remain owner-gated. This increment closes neither and claims neither.
- The economic invariant is proven in process on an isolated canonical store. It is not production evidence, and no production figure is asserted anywhere in this increment.
- No headless browser harness exists in this repository, and no claim is made about any rendered surface.

UNAPPROVED SCOPE CHANGES:
NONE

