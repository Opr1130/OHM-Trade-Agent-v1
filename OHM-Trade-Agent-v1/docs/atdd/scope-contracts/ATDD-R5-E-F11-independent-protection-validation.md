INCREMENT:
ATDD-R5-E-F11-independent-protection-validation

OWNER-APPROVED INTENT:
Prove that the protective authority the platform already has still holds over the research-paper portfolio that R5-E has now integrated, and nothing else. This increment adds no engine, no ledger, no allocator, no scheduler, no writer, no event type, no release profile, no configuration key and no trading authority. It changed no application behaviour. What it does is validate one existing chain end to end - the F11 scheduled protection sweep (`app/jobs/scan_opportunities.py` -> `app/services/paper_v2_protection_runtime.py`), the canonical writer's own atomic protection action, and the live-domain F11 read-only health decision (`app/services/protection_health.py`, `app/jobs/report_protection_health.py`) - over committed Paper-v2 exposure, and record exactly what remains unproven. It answers five questions and only those five. First, is protection operational when every other surface is not: market discovery, instrument selection, the ignition detector, the forecast engine, the feasibility gate, the portfolio selector, the dashboard and notification may all be down, and an approved stop exit must still be taken. Second, is it deterministic and prioritized: the frozen `TRIGGER_PRECEDENCE` of `STOP`, `TARGET`, `TIME` is proved through behaviour, an expired plan still takes its approved profit fraction where the target is crossed, only a book that crossed nothing falls to the time exit, and one item that cannot be advanced suspends new authority without stopping another item's approved exit. Third, is it fail-closed on unusable market data and on an unproven writer verdict: a stale book, an unreachable venue, a book whose bid is above its ask, a lost acknowledgement of the committed atomic action and an indeterminate answer each establish no trigger, fabricate no fill, release no capital and withhold new admissions, while a fresh crossing book does trigger. Fourth, is it recoverable: a restart between a trigger and its exit resumes that same committed exit once, reconciles to flat and verified, releases the reservation once and then writes nothing - including for an entry that came from the R5-C/R5-D admission authority. Fifth, is it independent of the observability plane: the target authority imports no live-domain observer, the live-domain observers cannot stop it, and a healthy target verdict can never lift a strict F11 objection. The increment prefers proving existing behaviour over writing code, and every guard is an acceptance test in one new module plus this increment's own bookkeeping.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 9 "Autonomous paper portfolio management": price/thesis/time exits, partial profit taking, approved trailing and retained exposure are the required R5-E portfolio actions alongside entries, holds and rotations; recovery must replay committed intents and fills idempotently and reconcile reserves; unsupported actions must explicitly abstain. This increment proves the protective half of that requirement over committed exposure and abstains from everything it cannot prove.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LC-01`, `LC-02`, `LC-03` and section 17 `AC-07`, `AC-09`, `AC-10`: protection precedes profit seeking and a protective close invalidates the thesis rather than being scored against it; profit targets and time invalidation are predeclared deterministic policies; the canonical writer acknowledges only committed transitions and recovery resumes from committed state; partial fills, fees and cancellations reconcile exactly with occupancy counted once; protection is independent.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `PM-01` and `PM-02`: R5-E runs in a separately identified offline/research paper environment on the existing execution components, adds no release profile and cannot turn `OPIP_PAPER_V2_MODE` on in production; `TARGET_PAPER` remains a later owner-gated cutover. This increment adds no profile and no activation.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-05` "Protective precedence and lifecycle state contract" (`FROZEN`, no open owner input): protection and admission suspension outrank any profit objective, profit targets and time invalidation stay predeclared deterministic policies rather than free parameters, and the canonical writer acknowledges only committed transitions while recovery resumes from committed state. This is the contract `AC-002`, `AC-003`, `AC-004` and `AC-005` prove through behaviour.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-03` "Scheduling, fairness, capacity and workload budgets" (`OPEN`; its numeric budgets stay `OQ-04`): scheduling stays reproducible and bounded and separates priorities instead of silently dropping work. This increment proves the structural half only - every committed position is advanced by the one existing sweep - and invents or widens no numeric latency, freshness, queue or workload budget (`AC-006`).
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-11` "Paper isolation and research environment": research paper management runs in a separately identified offline/research environment on the existing engine, with no funded write and no exchange order authority; paper cannot generate its own proof and `EVIDENCE_SHADOW` remains the production posture with `TARGET_PAPER` `BLOCKED`.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-12`: `REUSE_EXISTING_MODULE` for the canonical writer; no second writer, event store, position registry or outcome engine is added here.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-09` "Rotation group record and switching comparison": the record contract is `FROZEN` while the minimum economic advantage, uncertainty treatment, cooldown, requalification rule and turnover budget stay `OQ-05`; nothing here touches rotation from committed proceeds.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` row `F11` (safety: independent protection, fail-closed admission and human resumption, re-proven under new load; gate `G5`; `KEEP; protection precedes profit seeking and cannot starve`), row `PA-13` (`Protect independently of discovery`; `SC-05`), row `PA-14` (`Prioritize existing positions`; `SC-05`), row `R5-E` (contracts `SC-09`/`SC-11`, open input `OQ-08`, gate `G5`, authority `PAPER_RESEARCH`), gate `G5` (`Safety and isolation gate: independent protection, fail-closed admission and paper isolation under new load`, consuming `R5-E`), and the declared dependency edges `R5-F`<-`R5-A`,`R5-E`.
- `docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md`: the F11 protective contract this increment validates over the R5-E portfolio. Its ordering and single-authority guards and its one-way `target_protection_allows_admissions` input are referenced, never replaced or weakened; `AC-007` here proves the direction its own tests do not cover - that a healthy target verdict can never lift a strict objection.
- `docs/atdd/scope-contracts/ATDD-R5-E-portfolio-lifecycle-closure.md`: the increment that integrated the research-paper lifecycle. Its admission-backed entry, exit and restart guarantees are the input to `AC-005` here, and its `DEFERRED DISCOVERIES` remain unchanged by this increment.
- `docs/atdd/scope-contracts/ATDD-R5-E-research-paper-execution.md`: the F8 producer and the R5-C/R5-D admission authority this increment drives, unmodified, to create the exposure that protection is proved over.
- `AGENTS.md`: fail-closed behaviour, fixed risk sizing, minimum R:R, execution safeguards and paper isolation are preserved; external AI cannot override deterministic gates; production stores are not mutated; one scheduler, store and control plane.


APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the production scheduled protection seam and one committed, armed Paper-v2 position in the canonical store, with the market-discovery, instrument-selection, ignition-detector, forecast, feasibility, portfolio-selection, dashboard and notification entry points all replaced by doubles that raise
WHEN:
the scheduled sweep runs against a fresh public book crossed against the committed stop
THEN:
the approved stop exit is taken exactly as it would be with every one of those surfaces healthy - one `STOP` trigger at the executable price, one `EXIT` order intent for the whole remaining exposure and a `TRIGGERED` state - none of the eight doubles is ever called, and the protective authority's own import surface reaches no discovery, instrument-selection, intelligence, notification, dashboard or live-exposure module at all
AC-002:
GIVEN:
committed armed positions whose plan carries a predeclared stop, a first target and an expiry, and books that cross more than one of those levels at the same instant
WHEN:
the sweep advances each position, including one whose book cannot be trusted at all while another's stop is crossed
THEN:
the trigger is chosen by the frozen precedence of `STOP`, then `TARGET`, then `TIME` and by nothing else: a crossed stop claims the full remaining exposure even when the plan has also expired, an expired plan whose first target is crossed takes the approved profit fraction while a plan at that same instant that crossed nothing falls to its full time exit, one item that cannot be advanced suspends new admissions and is reported as retryable without stopping another item's approved exit, and no suspension releases capital, reconciles anything or fabricates a fill
AC-003:
GIVEN:
an armed position and, in turn, a book older than the frozen freshness window, an unreachable venue, and a book whose bid is above its ask
WHEN:
the sweep runs against each of them
THEN:
no trigger, no EXIT order intent, no fill and no release is produced, the position stays armed and fully exposed, its committed reservation stays ACTIVE, the reading is reported as retryable or unavailable so new admissions are withheld, and the failure is never reported as health - while a fresh book crossed against the same stop does trigger, withholds no new authority, generates no retryable or unavailable verdict, and leaves exactly one EXIT order intent
AC-004:
GIVEN:
an armed position, a crossed stop, a writer whose atomic protection action commits but whose acknowledgement is lost, and separately a writer whose answer to that action is an indeterminate verdict that commits nothing
WHEN:
the sweep runs against each of them, and then a healthy writer resumes over the same committed evidence
THEN:
neither the lost answer nor the indeterminate verdict is reported as a completed trigger or a completed exit, neither releases the reservation nor fabricates a fill, and both withhold new admissions; evidence the action durably committed is discovered exactly once and never duplicated or replaced; the resumed sweep completes that same exit once, reconciles to `FLAT_AWAITING_RECONCILIATION` and then `FINAL_VERIFIED`, releases the reservation exactly once; and a further sweep over the committed evidence adds no canonical evidence at all

AC-005:
GIVEN:
one committed armed position whose stop is crossed and whose exit is outstanding, and separately an entry that the R5-C/R5-D capital authority admitted and the F8 seam dispatched into exactly one canonical execution
WHEN:
the canonical store is closed and reopened over the same durable evidence and the sweep runs again
THEN:
the same committed trigger is resumed and its same EXIT order intent is filled exactly once, the position reconciles to `FLAT_AWAITING_RECONCILIATION` and then `FINAL_VERIFIED`, the committed reservation is released exactly once, every canonical row count is unchanged by the replay, no canonical evidence is rewritten, deleted or backfilled, and once terminal every further sweep and restart leaves the store fingerprint unchanged
AC-006:
GIVEN:
one canonical store holding several committed armed positions across different instruments, one of them with an exit the offered depth cannot fill
WHEN:
a single sweep advances all of them
THEN:
every committed position is advanced by that one sweep - each instrument read once, none skipped and none read twice, all crossed stops triggered together - so the admission workload cannot starve protection; the producer's own capacity bound independently refuses a further concurrent entry with `CAPACITY_REJECTED` before it can become an unprotected exposure, so protection never has to find one; and capital is released only once an exit is committed and verified, so the position whose exit is still outstanding keeps its reservation ACTIVE and withholds new admissions while the closed position's capital returns exactly once and never twice
AC-007:
GIVEN:
the target protection authority, the live (Kraken/operator) exposure domain and the F11 read-only protection-health decision, whose evaluator already accepts an optional target-domain verdict
WHEN:
the live registry, the incident store, the F11 evaluator and the F11 report are each replaced by doubles that raise and the scheduled sweep runs, and separately the strict decision is evaluated with a healthy target verdict alongside unmanaged, uncertain, incomplete-coverage and unproven-incident evidence
THEN:
protection is unaffected and reaches none of those observers: the sweep takes its approved exit with none of the four doubles called, and the authority's import surface contains no live-domain observer module; and the strict verdict still fails closed and still suspends admissions on its own evidence, so a target verdict can only add a suspension - an objection always suspends and never lifts one - and only a fully proven set with no target objection is healthy
AC-008:
GIVEN:
this contract, the movable `ACTIVE_INCREMENT` pointer, the acceptance-test index and the release-profile surface
WHEN:
this increment's own bookkeeping is inspected
THEN:
the contract parses with every criterion traceable to this module's acceptance tests and every traceability node resolving to an existing acceptance-marked test, the pointer resolves to exactly one declared contract instead of being pinned to this increment, the increment this one validates stays declared, the implementation map covers every path this increment changes, `UNAPPROVED SCOPE CHANGES` is `NONE`, the release profiles are unchanged with `EVIDENCE_SHADOW` `ACTIVE`, `TARGET_PAPER` `BLOCKED` and `OPIP_PAPER_V2_MODE` `off`, and application code contains exactly one scheduled sweep caller and exactly one composer of the writer's atomic protection action, with no new scheduler, engine or second authority


EXPLICITLY OUT OF SCOPE:
- No new capability: no engine, portfolio manager, allocator, ledger, event store, position registry, writer, scheduler, release profile, configuration key, feature flag or trading authority is added, and `OPIP_PAPER_V2_MODE` is not turned on anywhere.
- No production activation, no cutover and no funded path: this increment proves existing behaviour in the research-paper domain only, and `TARGET_PAPER` stays `BLOCKED`.
- No new protection policy: `STOP`/`TARGET`/`TIME`, the predeclared stop, targets and expiry, the partial-exit fraction, the trailing rule and the fee/slippage model are read from committed evidence exactly as they are; no threshold, fraction, budget, timeout or retry policy is added, changed or widened.
- No rotation from committed proceeds, no rotation-group lifecycle and no `OQ-05` policy values.
- No partial entry fills, latency, cancellation or stop-gap modelling: no partial-entry support and no latency, spread, slippage or gap value is added by this increment.
- No live (Kraken/operator) domain change: the live registry, the incident store, the F11 evaluator, the F11 report job and the legacy paper engine are untouched and unchanged.
- No new observability, dashboard or notification surface, and no change to the existing ordering guards of `ATDD-R4-F11-precutover-protection`.
- No numeric scheduling, fairness, freshness or workload budget: `SC-03` stays `OPEN` and `OQ-04` stays an owner input.

FROZEN BOUNDARIES:
- Components: this increment reuses the existing F11/O'Pip protective authority (`app/services/paper_v2_protection_runtime.py`), the existing scheduled seam (`app/jobs/scan_opportunities.py` -> `_run_paper_v2_protection_sweep`), the existing canonical writer and its own atomic protection action (`app/opip/canonical/client.py`, `server.py`, `writer.py`), the existing F8 producer (`app/services/paper_v2_execution.py`), the existing R5-C/R5-D admission authority (`app/services/capital_admission_integration.admit_planned_reservation`) and the existing live-domain F11 decision (`app/services/protection_health.py`, `app/jobs/report_protection_health.py`). `REUSE_EXISTING_MODULE` (`SC-12`): no second writer, event store, position registry or outcome engine.
- Authority: the canonical writer remains the only mutator of protection evidence; its reason vocabulary, its acknowledgement semantics (`OK`/`DUPLICATE_OK`/`COMMITTED`), its atomicity, its refusal order and the `PaperProtectionActionAck` contract are unchanged, and `evaluate_protection_health`'s one-way `target_protection_allows_admissions` input is unchanged. No suspend latch, veto or authority is added to any component.
- Evidence: only committed canonical evidence is read; no test invents a disposition, a reservation, a plan, a trigger, a fill or a reconciliation, and no store is rewritten, truncated or backfilled. `AC-005` asserts the store fingerprint is unchanged by replay.
- Precedence: `TRIGGER_PRECEDENCE == ("STOP", "TARGET", "TIME")` and the full-remaining-exposure rule for protective exits are asserted, never modified.
- Failure behaviour: unreadable, stale, crossed, contradictory or unavailable evidence and unproven writer verdicts stay retryable or unavailable and withhold new admissions; they never become health, never release capital and never fall back to a legacy authority.
- Isolation: research paper remains isolated from production authority; no funded write, no exchange order placement, modification, cancellation or confirmation, and no production store is read or written by this increment.


ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_001_a_broken_discovery_and_notification_surface_cannot_stop_the_exit
AC-001 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_001_the_protective_authority_imports_no_other_subsystem
AC-002 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_002_a_crossed_stop_outranks_the_target_and_the_expired_plan
AC-002 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_002_an_expired_plan_takes_its_approved_target_before_time
AC-002 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_002_an_unreadable_item_withholds_new_admissions_and_protects_others
AC-003 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_003_a_stale_book_neither_triggers_nor_grants_new_admissions
AC-003 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_003_an_unavailable_venue_neither_triggers_nor_grants_new_admissions
AC-003 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_003_a_contradictory_book_neither_triggers_nor_grants_new_admissions
AC-003 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_003_a_fresh_crossing_book_still_triggers
AC-004 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_004_a_lost_action_acknowledgement_is_never_a_completed_exit
AC-004 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_004_the_action_behind_a_lost_acknowledgement_is_resumed_once
AC-004 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_004_an_indeterminate_writer_verdict_is_never_a_cached_success
AC-005 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_005_a_restart_between_the_trigger_and_its_exit_finishes_it_once
AC-005 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_005_an_admission_backed_entry_is_protected_across_a_restart
AC-006 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_006_a_saturated_portfolio_is_fully_protected_in_one_sweep
AC-006 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_006_capacity_is_released_only_from_committed_exits
AC-007 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_007_the_protection_authority_reads_no_live_domain_observer
AC-007 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_007_a_healthy_target_verdict_can_never_lift_a_strict_f11_objection
AC-008 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_008_the_contract_pointer_and_validated_increment_are_consistent
AC-008 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_008_the_posture_and_release_surface_are_unchanged
AC-008 -> tests/test_opip_r5_e_f11_independent_protection.py::test_ac_008_the_increment_adds_no_scheduler_and_no_second_authority


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py
AC-002 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py
AC-003 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py
AC-004 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py
AC-005 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py
AC-006 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-006 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py
AC-007 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-007 -> OHM-Trade-Agent-v1/app/jobs/report_protection_health.py
AC-007 -> OHM-Trade-Agent-v1/app/services/paper_v2_protection_runtime.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-E-F11-independent-protection-validation.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_f11_independent_protection.py

DEFERRED DISCOVERIES:
- Discovered while proving `AC-006`: the F8 producer itself bounds concurrent Paper-v2 exposure and refuses a further concurrent position with the committed disposition `CAPACITY_REJECTED` (`app/opip/contracts/paper_execution.py`, `QualifiedOpportunityDisposition`), so a saturated portfolio cannot grow an unprotected tail that protection would have to find. The bound's numeric value is a producer/owner policy question and is neither asserted nor changed here; the sweep is proved over a portfolio at its committed capacity, and the refused opportunity is shown to produce no protection item of its own.
- The same discovery means protection is not the capacity authority: it advances what the producer committed and releases only what an exit proved. Where an owner-approved capacity policy should sit, and whether it should be visible to the sweep, is not decided here.
- The strict F11 report job deliberately does not read the Paper-v2 protection runtime, so a target-domain protection objection is not observed by `app/jobs/report_protection_health.py`; the target admission seam fails closed on its own `new_admissions_allowed` gate instead. `AC-007` therefore proves the one-way direction that exists (a healthy target verdict can never lift a strict objection, and an objection always suspends) and does not add the wiring.
- `SC-03` fairness and capacity budgets are `OPEN` (`OQ-04`). This increment proves the structural half only: every committed position is advanced by the one sweep, each instrument is read once, and nothing is silently dropped. No numeric latency, freshness, queue or workload budget is invented, asserted or widened.
- A per-item failure is aggregated as `retryable`/`unavailable` with `new_admissions_allowed=False`, and the sweep names the failing trade in `details`; there is still no durable per-item protection-incident record, no dedicated alert for a retryable protection item and no automated recovery beyond the next scheduled scan.
- The protection sweep has no scheduler of its own: it is reached only from the scheduled scan (`app/jobs/scan_opportunities.py`), so protection cadence is the scan cadence and a stopped scan stops protection just as it stops everything else. `AC-001` proves that nothing *inside* the scan can stop it; the scheduler itself is out of scope.
- Windows-only full-suite failures observed while validating this increment reproduce at the parent commit `798eb32f` and are not attributed to this increment; Linux CI remains the final-validation prerequisite.
- Partial entry fills, latency, cancellation and stop-gap modelling stay `NOT_IMPLEMENTED`; no value is added by this increment.
- `OQ-04`, `OQ-05`, `OQ-06` and `OQ-08` remain owner inputs. `OQ-08` still governs the legacy paper-engine coexistence window and the R5-F handoff, both untouched here.

UNAPPROVED SCOPE CHANGES:
NONE

