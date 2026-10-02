INCREMENT:
ATDD-R4-B2-controlled-paper-activation

OWNER-APPROVED INTENT:
This is the OWNER-authorized R4-B2 controlled target paper activation. R4-B1 wired the proven F3-F7 target spine into the unified cycle dormant and non-authoritative; F11 established the read-only protection/safety gate. R4-B2 is the increment that may activate the target Paper-v2 execution authority as the sole new-entry paper authority, after every prerequisite gate has objectively passed, in strict paper-only isolation.

FUNDED TRADING REMAINS OUT OF SCOPE. R4-B2 activates a PAPER execution authority only. It grants no funded or exchange order authority, no margin, borrow or leverage, and no Committee runtime authority. Paper execution stays technically isolated from funded order endpoints and funded credentials.

THIS PR IS THE CONTRACT FREEZE FOR THE INCREMENT. It writes the scope contract, its freeze-provable acceptance criteria and the movable pointer. It writes no production runtime behavior and activates nothing. The activation implementation (the F7-as-admission-source wiring, the mode/cutover sequence and their behavioral acceptance criteria) is a later commit of this same increment, at which point this contract's acceptance criteria and implementation map are extended. No activation is authorized by this freeze.

AUTHORIZATION PROVENANCE. Authorized by the owner's standing master-orchestrator directive to proceed, after R4-B1 Gate B closed and F11 merged, to the R4-B2 contract freeze and controlled PAPER activation.

STARTING SHA. `origin/main` = `50c68adaf643155099ab63b99bd0b61237c9b751` (F11 pre-cutover protection, PR #312). `docs/atdd/ACTIVE_INCREMENT` named `ATDD-R4-F11-precutover-protection` at the start of this increment.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (Runtime decision path): "Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution. Only approved deterministic or statistical artifacts participate." R4-B2 makes the target selector the live *new-entry* paper admission source.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (Safety plane) and section 12: protection runs independently and outranks new admissions; safety can suspend automatically and resumption requires human approval.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4 (Paper execution contract): separate NO_FILL, PARTIAL_FILL and FULL_FILL from TARGET, STOP, TIMEOUT and independently triggered RISK_EXIT; the policy horizon anchors to first fill; residual orders have their own expiry; a limit touch alone is insufficient fill evidence.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4 (Portfolio objective): reserve atomically against a portfolio version; release on cancellation or expiry and adjust on fills; no Kelly, leverage, covariance optimizer or dynamic risk parity.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 5: one canonical writer is the only authority that commits operational events; writer failure halts reservations and simulated fills.
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R4: cutover stays blocked until the R3 selector is the admission source being papered, `OPIP_PAPER_V2_MODE=active`, legacy drain is READY, the protection sweep is healthy before new admissions, and universe metadata is present; "R4 must show protection still runs when discovery is down."
- `docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md` and `ATDD-R4-B1-runtime-integration-dormant.md`: the frozen target spine, boundary, posture and retry/requalification semantics R4-B2 must preserve.
- `docs/atdd/scope-contracts/ATDD-R4-F11-precutover-protection.md`: the read-only protection/safety gate that must pass before activation.
- `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F8 row and `docs/atdd/scope-contracts/ATDD-R4-F8-paper-v2-cutover-readiness.md`: the readiness probe, the drain evaluator and the `SHORT_AUTHORITY_MISSING` history.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture; `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the R4-B2 controlled-paper-activation increment and the repository's movable active-increment pointer
WHEN:
the scope contract and the pointer are inspected
THEN:
the contract exists, declares its own increment identity, the pointer resolves to an existing scope contract, and this increment never pins the global pointer to its own identity
AC-002:
GIVEN:
the activation sequence and the repository's canonical mode vocabulary
WHEN:
the contract is inspected
THEN:
the contract freezes the ordered sequence off -> shadow/comparator -> controlled PAPER target path -> verified target paper authority, uses the repository vocabulary (`off`/`active`) without inventing modes, forbids skipping an architecture-required state, and states that activation is a distinct owner-controlled switch that this freeze does not set
AC-003:
GIVEN:
the single new-entry paper authority requirement
WHEN:
admission authority is inspected
THEN:
the contract requires exactly one new-entry paper authority (the target Paper-v2 route) with the legacy Top-8/profit-ranking and Freqtrade/Paper-v1 paths retained as comparator/rollback only, requires the target F7 selector to be the admission source being papered rather than the legacy selector, and forbids any second admission, allocation or reservation authority
AC-004:
GIVEN:
the cutover preconditions
WHEN:
activation is evaluated
THEN:
activation is gated on F11 protection health proven, legacy drain READY (zero Freqtrade and Paper-v1 exposure), universe metadata present, direction coverage resolved (SHORT supported with equivalent proofs or an explicit owner LONG-only mandate), and the target spine reachable; a missing, unreadable or unproven gate withholds activation rather than defaulting favourable
AC-005:
GIVEN:
the authority-collision requirement
WHEN:
the collision test is defined
THEN:
the contract requires a test proving it is impossible for the legacy admission path (Top-8/profit-ranking) and the target admission path (F7 -> Paper-v2), or the two paper engines, to create two independent executions for one economic opportunity, states that F7 and Paper-v2 are one authority, and requires that one selected opportunity yields at most one admission
AC-006:
GIVEN:
the Paper-v2 execution proofs
WHEN:
the required proofs are enumerated
THEN:
the contract enumerates realistic entry and exit side, liquidity/depth binding, fees/slippage/latency, partial fill, no-fill, target, stop, timeout, independent risk exit, restart, terminal reconciliation, capacity release, stale/replayed-input idempotency, and direction-aware P&L for every direction the activated route authorizes, with a direction the route does not authorize carrying an explicit disposition rather than being silently dropped
AC-007:
GIVEN:
the frozen retry/requalification semantics
WHEN:
the activation preserves them
THEN:
the contract requires the activated path to preserve the R4-B1 freeze: an exact retry is idempotent, a materially changed requalification is refused as a conflicting decision, no duplicate trade/reservation/disposition is created, and the one reservation authority remains the canonical writer
AC-008:
GIVEN:
the rollback requirement
WHEN:
rollback is inspected
THEN:
rollback restores exactly one authority (restore the mode to `off` so the legacy path resumes), never runs two allocation authorities, needs no canonical-data migration, and retains the former comparator artifacts without deleting obsolete code
AC-009:
GIVEN:
the authority boundary and exclusions
WHEN:
they are inspected
THEN:
the contract forbids funded trading, exchange order authority, margin/borrow/leverage, Committee runtime authority, dashboard/Telegram trading authority, a second scheduler, a second paper engine, and legacy deletion; and states that F11 is not bypassed
AC-010:
GIVEN:
the freeze artifact and the repository's default posture
WHEN:
the freeze posture is audited
THEN:
this freeze sets no production mode (the repository default remains `off`), its artifacts assign no mode and import no funded, exchange, order-placement or Committee authority, and the current repository posture remains asserted exactly by the dedicated current-runtime-posture guard
AC-011:
GIVEN:
the pre-cutover comparison requirement
WHEN:
the evidence gate is inspected
THEN:
the contract requires matched-window comparison evidence of the target selector against cash/no-trade and the frozen profit-ranking comparator, under matched capital, timing, execution model and fee policy over the full intent population, recorded before the legacy admission source is replaced rather than assumed

EXPLICITLY OUT OF SCOPE:
- Setting `OPIP_PAPER_V2_MODE=active` in production, or any activation, in this freeze PR
- Funded or live trading, exchange order placement, modification, cancellation or confirmation, margin, asset borrow or leverage
- Retiring, deleting or re-weighting the legacy selector, Top-8, profit-ranking, Freqtrade dry-run or Paper-v1
- A second scheduler, admission authority, allocation authority, reservation authority, paper engine, outcome truth system or evidence store
- Activating the Feature Bus, the Committee, or any AI runtime authority
- Bypassing the F11 protection gate
- Changing F3-F7 economics, detector/forecast/selector/geometry behavior, P&L sign or protection semantics
- Modifying architecture documents, the v1.4.3 DOCX, the ATDD checker, workflows or `pyproject.toml`
- Weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
Activation posture.
R4-B2 is the increment that enables the target Paper-v2 execution authority as the sole NEW-ENTRY paper authority, in strict paper-only isolation. Activation is an explicit owner-controlled switch (`OPIP_PAPER_V2_MODE=active`); this freeze defines the gates and proofs it must satisfy and sets nothing. The repository default remains `off`.

Activation sequence.
The architecture-permitted progression, stated conceptually, is `off` -> `shadow/comparator` -> controlled PAPER target path -> verified target paper authority. These are conceptual stages, not new mode tokens: the repository's canonical vocabulary remains `off` and `active`, and neither the architecture nor the roadmap defines a four-state mode machine. A later stage may not be entered until the earlier stage's evidence passes. No new mode token is invented.

Single authority and collision safety.
Exactly one authority may create a new paper entry: the target Paper-v2 route, with the target F7 selector as the admission source being papered. The target F7 selector and the target Paper-v2 engine are ONE authority (source and sink), not two. Legacy Top-8/profit-ranking and Freqtrade dry-run/Paper-v1 remain as comparator and rollback only and may not create new entries while the target authority is active. There is one reservation authority: the canonical writer. It must be impossible for the legacy admission path (Top-8/profit-ranking) and the target admission path (F7 -> Paper-v2), or for the two paper engines, to create two independent executions for one economic opportunity.

Direction coverage (no silent LONG-only).
The activated route must have an explicit direction disposition. The current router is LONG-only (`paper_v2_scan_router.py` `SUPPORTED_DIRECTION`) and the readiness probe reports the missing SHORT authority as a hard blocker (`SHORT_AUTHORITY_MISSING`). R4-B2 therefore requires that direction coverage be resolved, not assumed: either SHORT is supported on the activated route with equivalent proofs, or an explicit owner LONG-only paper mandate is recorded. A silently LONG-only activated route is not acceptable, and the `SHORT_AUTHORITY_MISSING` readiness blocker must be resolved or explicitly dispositioned before activation.

Cutover preconditions (fail closed).
Activation requires, all objectively observed and failing closed when absent, unreadable or unproven: F11 protection health proven (no silent or unmanaged holding, coverage complete, protection-incident health proven, target protection not withholding); legacy drain READY (zero Freqtrade open trades and outstanding signals, zero Paper-v1 pending entries and open positions); universe metadata present; direction coverage resolved per the rule above; and the target spine reachable. A missing or unreadable gate withholds activation.

Pre-cutover comparison evidence.
Before the target authority replaces the legacy admission source, the increment must produce matched-window comparison evidence of the target selector against cash/no-trade and the frozen profit-ranking comparator, under matched capital, timing, execution model and fee policy, over the full intent population including no-fills and rejects. This is the evidence the recovery roadmap and retirement ledger name as the gate to flip admission; it is recorded before the legacy admission source is replaced, not assumed.

Execution and economic proofs.
The activated target path must reproduce the frozen Paper-v2 execution contract: realistic entry and exit side; size-sensitive depth and liquidity binding; fees, slippage and latency; NO_FILL, PARTIAL_FILL and FULL_FILL separate from TARGET, STOP, TIMEOUT and independent RISK_EXIT; a limit touch alone insufficient for a fill; restart and terminal reconciliation; capacity release on cancellation, expiry or terminal reconciliation; idempotency for stale or replayed input; and direction-aware P&L for every direction the activated route authorizes (at minimum LONG, and SHORT where SHORT is authorized per the direction-coverage rule). A direction the route does not authorize is never silently dropped: it carries the explicit disposition required above.

Retry and requalification.
The activated path preserves the R4-B1 freeze in full: an exact retry is idempotent; a materially changed requalification is refused as a conflicting decision; no duplicate trade, reservation or disposition is created; committed ancestry and economics are immutable; ambiguity fails closed.

Rollback.
Rollback is exact and is a switch plus code revert: set the mode back to `off`, so the legacy path resumes as the single authority with no canonical-data migration. Rollback must never leave two allocation authorities running and must preserve the former comparator artifacts; obsolete code is not deleted by this increment.

Authority boundary.
R4-B2 must not create, widen or imply: funded trading; funded exchange order authority; Kraken order placement, modification, cancellation or confirmation; margin, asset borrow or leverage; Committee runtime authority; dashboard or Telegram trading authority; a second scheduler; or a second paper/admission/reservation authority. Risk, strategy, execution and protection authority beyond the target paper path are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_001_contract_and_pointer_are_consistent
AC-002 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_002_activation_sequence_and_vocabulary
AC-003 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_003_single_new_entry_authority
AC-004 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_004_cutover_preconditions_fail_closed
AC-005 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_005_authority_collision_test_defined
AC-006 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_006_execution_proofs_enumerated
AC-007 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_007_retry_semantics_preserved
AC-008 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_008_rollback_restores_one_authority
AC-009 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_009_authority_boundary_and_exclusions
AC-010 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_010_freeze_sets_no_mode_and_no_authority_import
AC-011 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_011_comparator_evidence_required

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py

DEFERRED DISCOVERIES:
- The activation implementation (wiring the target F7 selector as the admission source, the mode/cutover sequence and their behavioral acceptance criteria and implementation map) is a later commit of this same increment and is not authorized by this freeze.
- The current router papers the legacy-ranked cohort; making the target F7 selector the admission source is the substantive R4-B2 change and must be reconciled against the frozen R4-B0 handoff and R4-B1 retry semantics.
- SHORT coverage is now a frozen precondition, not an assumption: the activation route's direction coverage must be resolved (SHORT supported with equivalent proofs, or an explicit owner LONG-only mandate). The current router is LONG-only (`paper_v2_scan_router.py` `SUPPORTED_DIRECTION`) and the readiness probe reports `SHORT_AUTHORITY_MISSING`; R4-B0 made SHORT reachable in the canonical engine, so supporting it is a router change that must be scoped, not silently skipped.
- The comparator and direction-coverage evidence are frozen as pre-cutover requirements (AC-011 and AC-004). Producing them is part of the activation implementation, not this freeze. The comparator evidence is recorded as a durable artifact under the repository's `docs/atdd/evidence/` convention with an explicit consumption disposition, in the activation implementation commit.
- The PCAND -> OPIPC candidate-identity bridge between the F7 selection and the Paper-v2 handoff (named in `ATDD-R4-F8-paper-v2-cutover-readiness.md` as required for R4-B wiring) is reconciled explicitly in the activation implementation commit; this freeze references it only through the frozen R4-B0 handoff contract.
- A dual-run comparison of the activated target authority against the legacy comparator for the same opportunity is separate evidence and is not required by this freeze; the freeze requires the collision proof (at most one execution per opportunity), not a dual-writing comparator.
- Whether the activated paper authority requires a production mode deploy is an owner control-plane action; this freeze authorizes no deploy.
- F11 remains a prerequisite gate and is not bypassed; its read-only decision is consumed, not replaced.

UNAPPROVED SCOPE CHANGES:
NONE
