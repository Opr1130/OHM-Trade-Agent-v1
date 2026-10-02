INCREMENT:
ATDD-R4-F11-precutover-protection

OWNER-APPROVED INTENT:
This is the OWNER-authorized F11 pre-cutover safety and protection increment. It must pass before R4-B2 (controlled Paper-v2 activation). The v1.4.3 architecture makes safety an independent plane: "Independent protection -> priority canonical intents. Safety can suspend automatically" and "Protection does not depend on discovery, forecasts, economics or AI." The recovery roadmap R4 requires a healthy protection sweep before new admissions and that protection still runs when discovery is down.

DELIVERABLE. F11 preserves the existing, safe, Kraken-first reconciliation and protection path while the target protection is proven, and adds only the missing target-safety delta: one explicit, deterministic, read-only protection-health decision that detects silent holdings, exposes uncertainty, and withholds new admissions when protection cannot be proven. It introduces no second protection authority, mutates nothing, activates no mode, and grants no funded, exchange, Committee or trading authority.

THIS INCREMENT IS READ-ONLY AND NON-AUTHORITATIVE. It does not remove or replace the existing Kraken-first protection, does not activate the Feature Bus or Paper-v2, does not start R4-B2, and does not place, modify, cancel or confirm any order.

STARTING SHA. `origin/main` = `8e5e2bb57a8d130c9e0ebadbd671509d199c96cd` (R4-B1 dormant runtime integration, PR #311). `docs/atdd/ACTIVE_INCREMENT` named `ATDD-R4-B1-runtime-integration-dormant` at the start of this increment.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (Safety plane): "Independent protection -> priority canonical intents. Safety can suspend automatically; resumption requires human approval. Protection does not depend on discovery, forecasts, economics or AI."
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4: "Missing evidence is never favorable." and the paper execution contract's separation of NO_FILL, PARTIAL_FILL and FULL_FILL from TARGET, STOP, TIMEOUT and independently triggered RISK_EXIT.
- `docs/architecture/v1.2/I_SAFETY_MONITORING_CONTRACT.md`: "Safety and protection code cannot depend on detectors, forecast, economics, or AI."; "New entries fail closed on feed or data gaps."; "Open positions preserve state, mark uncertainty, and escalate rather than invent fills."; "Existing positions retain approved protection during new-entry suspension."; incident lifecycle "OPEN -> CHANGED / ESCALATED -> RECOVERED".
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F11 row: "Independent protection, deterministic suspension, human resumption."; "Active-trade protection LEGACY_ACTIVE"; "Paper v2 sweep is not on the one-minute slot" (already closed by R4-A, which the cycle slot now satisfies).
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R4: "Protection sweep is healthy before new admissions"; "R4 must show protection still runs when discovery is down."
- `docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md` and `ATDD-R4-B1-runtime-integration-dormant.md`: the R4-B1 contract and wiring this increment must not disturb.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable; a completed increment must not permanently own it.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the F11 pre-cutover protection increment and the repository's movable active-increment pointer
WHEN:
the scope contract and the pointer are inspected
THEN:
the contract exists, declares its own increment identity, the pointer resolves to an existing scope contract, and this increment never pins the global pointer to its own identity
AC-002:
GIVEN:
the protection authority census
WHEN:
the mutating protection surfaces are inspected
THEN:
the mutating live- and target-exposure callers are confined to the declared protection-authority set, the F11 evaluator and its report are read-only and are not among them, and F11 introduces no new mutating protection authority
AC-003:
GIVEN:
a verified managed exposure that carries no protection plan
WHEN:
protection health is evaluated
THEN:
the exposure is reported as a silent holding, the state is UNSAFE, admissions are suspended, and the reason code names the silent holding rather than treating missing evidence as favorable
AC-004:
GIVEN:
uncertain or unreadable exposure evidence
WHEN:
protection health is evaluated
THEN:
an unmanaged open exposure, an unreadable or degraded position, or incomplete coverage each withholds admissions (UNSAFE or UNAVAILABLE), the uncertainty is exposed explicitly (unmanaged, uncertain, coverage), and a fully proven exposure set with no target-protection objection is the only HEALTHY outcome
AC-005:
GIVEN:
the F11 report job
WHEN:
it runs
THEN:
it prints one machine-readable protection-health decision, performs no mutation, activates no mode, and holds no order, exchange, funded or Committee authority
AC-006:
GIVEN:
the protection ordering and the live authority
WHEN:
the posture is audited
THEN:
protection still precedes discovery and admission, the Kraken-first reconciliation and active-trade monitor remain the live protection authority, and this increment activates no Feature Bus, Paper-v2, Committee or funded/exchange authority and introduces no second scheduler

AC-007:
GIVEN:
the read-only observation requirement and a corrupt or malformed registry
WHEN:
the observer's read seam is used
THEN:
the observer reads through a non-mutating seam that never quarantines, moves or rewrites the source, so a corrupt registry - including a non-object row - makes coverage unproven rather than being moved or silently dropped, and no lock file is created
AC-008:
GIVEN:
a positive unmanaged holding below the resolver's default materiality floor
WHEN:
the observer resolves exposure
THEN:
it resolves with the materiality floor disabled so no economically positive holding is hidden, a positive holding that the default floor suppresses is surfaced and reported rather than silently dropped, and the override can only lower the floor - a non-finite, negative or oversized override resolves to zero and a positive override is clamped to the default - without raising
AC-009:
GIVEN:
a verified managed holding and the canonical direction-bound protection geometry
WHEN:
protection health is evaluated
THEN:
a LONG protection plan is proven only when a positive, finite stop sits strictly below a positive, finite entry and a SHORT only when it sits strictly above, a stop or entry that is missing, non-finite or non-positive is a silent holding rather than geometry-invalid, a positive plan on the wrong side of, or equal to, the entry is UNSAFE with the geometry-invalid reason, and an unknown direction is uncertain rather than healthy
AC-010:
GIVEN:
an oversized integer stop and the report command
WHEN:
numbers are validated and the command runs
THEN:
the oversized integer fails closed without raising OverflowError, and the command writes exactly one valid JSON document to stdout with human-readable banners on stderr
AC-011:
GIVEN:
the protection-incident verdict and the ownership boundary
WHEN:
protection health is evaluated and the contract is inspected
THEN:
protection health is proven only when the incident verdict is True, an open incident, an unproven verdict or a structurally corrupt incident record withholds admissions, F11 owns NO suspension latch, and the owning authority is the incident lifecycle and the target new_admissions_allowed gate

EXPLICITLY OUT OF SCOPE:
- Removing, replacing or re-weighting the Kraken-first reconciliation, active-trade monitor, Paper-v2 protection, or any legacy protection path
- Activating the Feature Bus, Paper-v2, F7 runtime admission, or the Committee
- Funded or live trading, exchange order placement, modification, cancellation or confirmation, margin, asset borrow or leverage
- A second mutating protection authority, a second scheduler/reservation/execution authority, or a new evidence store
- Wiring the read-only F11 evaluator into the live legacy admission path (a production behavior change); the target Paper-v2 admission already fails closed on `new_admissions_allowed`
- Changing F3-F7 economics, detector/forecast/selector/geometry behavior, or protection routing of existing exposure
- Modifying architecture documents, the v1.4.3 DOCX, the ATDD checker, workflows, scheduler units or `pyproject.toml`
- Weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
Protection authority census (one mutating authority per domain).
- Live (Kraken/operator) exposure is mutated only by the callers of the live registry mutators (`close_trade`, `update_trade_remaining_quantity`, `mark_order_filled`, `bind_exchange_order_txid`): `app/services/kraken_reconciliation.py` (in `apply` mode), `app/api/routes.py` and `app/services/trade_cli.py` (operator surfaces). `app/services/active_trade_monitor_runner.py` is an observer: it verifies positions, writes outcome observations and emits alerts, but never calls a registry mutator or an order surface.
- Target (Paper-v2) exposure is mutated only by the callers of the canonical Paper-v2 mutators (`admit_paper_opportunity`, `trigger_paper_protection_action`): `app/services/paper_v2_execution.py`, `app/services/paper_v2_protection_runtime.py` and the canonical server dispatch. The target runtime withholds new entries through `new_admissions_allowed`.
- The F11 evaluator (`app/services/protection_health.py`) and its report (`app/jobs/report_protection_health.py`) are READ-ONLY. They mutate nothing and constitute no protection authority. They are not among either mutator set.

Protection ordering.
Protection continues to run before discovery and admission: `run_cycle` runs Kraken reconciliation, then the active-position monitor and the Paper-v2 protection sweep, before discovery; the opportunity scan runs the Paper-v2 protection sweep before admission routing. Protection never depends on discovery, forecasts, economics or AI.

Zero silent holdings.
No positive exposure may exist without a proven protection plan. A verified managed holding whose protection plan is missing, non-finite or non-positive is a silent holding and is reported UNSAFE. A protection plan is proven only when it matches the canonical direction-bound geometry the live and Paper-v2 protection contracts require: a positive, finite stop strictly below the entry for a LONG, and strictly above the entry for a SHORT. A stop that is merely finite and positive but on the wrong side, or equal to the entry, is not protection and is reported UNSAFE with `PROTECTION_GEOMETRY_INVALID`. An open position that Kraken reports without a matching lifecycle context is an unmanaged exposure and is surfaced explicitly. The read-only observer resolves exposure with the materiality floor disabled (`0.0`), so an economically positive holding is never hidden below the resolver's default notional floor; the live alerting monitor keeps its own materiality floor unchanged.

Genuinely read-only observation.
The observer must not be able to mutate the durable state it inspects. It therefore does not use the quarantining `registry_io.load_json` path, which moves a corrupt registry aside via `os.replace`. It reads through `registry_io.read_json_without_quarantine` and the non-mutating `active_trade_registry.read_active_trades_without_mutation`, so a corrupt registry - including a non-object row - makes coverage unproven (UNAVAILABLE) rather than being quarantined, rewritten or silently dropped. The exposure resolver's materiality override is an additive, non-mutating option that may only *lower* the floor (the result is the minimum of the production default and the override), so an observer can surface more exposure but never hide more than production already does.

Protection-incident health.
Protection health cannot be claimed `HEALTHY` unless the protection-incident verdict is proven healthy. The observer derives that verdict read-only from the incident store, using the same non-mutating seam: an open incident in a Kraken connectivity, read-only, rate-limit, held-asset-pricing or position-verification scope withholds admissions; an unreadable incident store is unproven and also withholds. The verdict is supplied to the evaluator, which does not itself own incident detection.

Ownership: observation, not a latch.
F11 owns NO suspension latch and NO human-resume decision. It reports an instantaneous, deterministic health observation and returns a decision; it writes nothing and cannot grant authority it does not own. The durable suspension and human-resumption authority remains the incident lifecycle (`app/services/system_incidents`) and the target admission gate (`new_admissions_allowed`). New admissions are withheld unless protection is proven `HEALTHY`.

Uncertainty and degraded state.
Incomplete coverage, an unreadable or degraded position verification, or an unrecognized exposure status is explicit uncertainty, never favorable evidence. Uncertainty withholds admissions. Stale-price, feed-gap and data-unavailability handling remain the responsibility of the existing producers that raise the corresponding incidents; F11 reports their effect as protection uncertainty rather than inventing a feed.

Admission suspension.
`evaluate_protection_health` yields a fail-closed decision: admissions are suspended unless protection is proven HEALTHY. This is the safety signal; it is consumed read-only, and the target admission seam continues to enforce its own `new_admissions_allowed` gate. Human resumption of a suspended safety state is required, not automatic.

Determinism and independence.
The evaluator is pure: no clock, network, database, environment or AI. Identical inputs produce an identical decision. It reads no detector, forecast, economic or Committee output.

Rollback.
Rollback is exact and is a code revert: remove `app/services/protection_health.py`, `app/jobs/report_protection_health.py` and this increment's tests. Because the increment is read-only and mutates nothing and activates nothing, rollback requires no canonical-data migration and leaves exactly one protection authority per domain (the existing legacy and target paths).

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_f11_precutover_protection.py::test_ac_001_contract_and_pointer_are_consistent
AC-002 -> tests/test_opip_f11_precutover_protection.py::test_ac_002_one_mutating_authority_and_read_only_evaluator
AC-003 -> tests/test_opip_f11_precutover_protection.py::test_ac_003_silent_holding_is_unsafe_and_suspends
AC-003 -> tests/test_opip_f11_precutover_protection.py::test_ac_003_non_finite_or_non_positive_stop_is_a_silent_holding
AC-003 -> tests/test_opip_f11_precutover_protection.py::test_ac_003_missing_coverage_fails_closed
AC-004 -> tests/test_opip_f11_precutover_protection.py::test_ac_004_uncertainty_fails_closed
AC-005 -> tests/test_opip_f11_precutover_protection.py::test_ac_005_report_is_read_only_and_authority_free
AC-006 -> tests/test_opip_f11_precutover_protection.py::test_ac_006_protection_ordering_and_no_new_authority
AC-007 -> tests/test_opip_f11_precutover_protection.py::test_ac_007_read_json_without_quarantine_does_not_move_corrupt_file
AC-007 -> tests/test_opip_f11_precutover_protection.py::test_ac_007_active_trade_loader_is_non_mutating
AC-008 -> tests/test_opip_f11_precutover_protection.py::test_ac_008_observer_disables_the_materiality_floor
AC-008 -> tests/test_opip_f11_precutover_protection.py::test_ac_008_materiality_override_can_only_lower
AC-008 -> tests/test_opip_f11_precutover_protection.py::test_ac_008_materiality_override_cannot_widen_behavior
AC-009 -> tests/test_opip_f11_precutover_protection.py::test_ac_009_long_stop_must_be_below_entry
AC-009 -> tests/test_opip_f11_precutover_protection.py::test_ac_009_short_stop_must_be_above_entry
AC-009 -> tests/test_opip_f11_precutover_protection.py::test_ac_009_managed_without_valid_entry_is_silent_not_geometry
AC-010 -> tests/test_opip_f11_precutover_protection.py::test_ac_010_oversized_int_fails_closed
AC-010 -> tests/test_opip_f11_precutover_protection.py::test_ac_010_cli_stdout_is_one_json_document
AC-011 -> tests/test_opip_f11_precutover_protection.py::test_ac_011_incident_health_required_for_healthy
AC-011 -> tests/test_opip_f11_precutover_protection.py::test_ac_011_corrupt_incident_row_is_unproven
AC-011 -> tests/test_opip_f11_precutover_protection.py::test_ac_011_no_suspension_latch_or_write_surface

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-002 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-003 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-004 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-005 -> OHM-Trade-Agent-v1/app/jobs/report_protection_health.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-007 -> OHM-Trade-Agent-v1/app/services/registry_io.py
AC-007 -> OHM-Trade-Agent-v1/app/services/active_trade_registry.py
AC-007 -> OHM-Trade-Agent-v1/app/jobs/report_protection_health.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-008 -> OHM-Trade-Agent-v1/app/services/kraken_exposure_resolver.py
AC-008 -> OHM-Trade-Agent-v1/app/jobs/report_protection_health.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-009 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-010 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-010 -> OHM-Trade-Agent-v1/app/jobs/report_protection_health.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md
AC-011 -> OHM-Trade-Agent-v1/app/services/protection_health.py
AC-011 -> OHM-Trade-Agent-v1/app/jobs/report_protection_health.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py

DEFERRED DISCOVERIES:
- Wiring the read-only F11 decision into the live legacy admission path is a production behavior change and is deliberately not done here; the target Paper-v2 admission already fails closed on its own `new_admissions_allowed`.
- A dual-run comparison harness that records legacy protection actions against target protection actions for the same position is not implemented; the increment proves the target behaviours and the single mutating authority instead. Adding a comparator that writes no new truth is a later, separately frozen increment.
- Stale-price/feed-gap detection remains owned by the existing incident producers; F11 reports their effect as uncertainty and does not add a feed or a second incident store.
- A SHORT operator (margin) position is not auto-terminalized by reconciliation until OHM can bind the Kraken position txid directly; that binding remains a later owner decision.
- R4-B2 remains blocked until this increment passes and the R4-B1 dormant deployment gate closes.

UNAPPROVED SCOPE CHANGES:
NONE
