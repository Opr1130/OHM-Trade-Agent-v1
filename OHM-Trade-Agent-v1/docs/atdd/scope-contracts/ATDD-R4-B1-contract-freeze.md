INCREMENT:
ATDD-R4-B1-contract-freeze

OWNER-APPROVED INTENT:
This is the OWNER-authorized R4-B1 architecture-and-scope freeze. R4-B0 closed the target F3-F7 spine as a set of typed, deterministic, direction-aware contracts and proved them composable in process. It closed with an explicitly deferred discovery: the resume-versus-requalification semantics of the canonical Paper-v2 disposition identity. R4-B1 is the runtime-integration increment that wires the already-proven target spine into the real production orchestration while every target mode stays OFF. This increment freezes the contract R4-B1 must implement; it writes no production runtime behavior, activates nothing, and changes no economics.

This freeze exists because the R4-B0 closure deliberately refused to "solve" the deferred semantics while writing implementation. The rules recorded here are the frozen R4-B1 contract. The runtime-integration increment that implements them is a separate, later increment and is not authorized by this contract.

AUTHORIZATION PROVENANCE. This increment is authorized by the owner's standing master-orchestrator directive to discover, design, independently review and freeze the canonical R4-B1 ATDD contract before any R4-B1 production code is written. No implementation authority for R4-B1 runtime wiring is granted here.

STARTING SHA. `origin/main` = `27f6a2f87429eebded2d2e507b99fba5ce7d9d42` (R4-B0 spine contract closure, PR #307). `docs/atdd/ACTIVE_INCREMENT` named `ATDD-R4-B0-spine-contract-closure` at the start of this increment.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (unified architecture): "Feasibility and calibrated statistical forecasts -> economic selector -> deterministic risk checks and atomic capital reservation -> realistic paper execution." R4-B1 puts that already-approved decision path into the real orchestration graph without granting it authority.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 2 (Learning plane): "human-governed release of an approved artifact". Wiring is not activation, and activation is not authorization.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3: "Acknowledge intents only after durable commit; retry unacknowledged operations with stable keys." and "Timestamp alone is not an idempotency key." This is the authority for the frozen retry semantics.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 3 (Commit and failure isolation): "Writer failure halts reservations and simulated fills." An unreadable canonical state therefore fails closed.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 4: "Missing evidence is never favorable." and the paper execution contract's separation of NO_FILL, PARTIAL_FILL and FULL_FILL from TARGET, STOP, TIMEOUT and independently triggered RISK_EXIT.
- `docs/architecture/v1.4.3/ARCHITECTURE.md` section 5: "A single canonical writer process is the only authority that commits operational domain events and projections", and the reserved-capacity priority rule that protection and execution intents outrank discovery and telemetry intents.
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R4: "Cutover stays blocked until all of these are true" and "Do not start a later phase, enable Paper v2, enable the Committee timer, activate the Feature Bus, or redesign the dashboard in that increment."
- `docs/atdd/scope-contracts/ATDD-R4-B0-spine-contract-closure.md` `DEFERRED DISCOVERIES`: "Deciding whether such a re-qualification must resume under the committed context or be refused as a conflicting decision is an R4-B1 contract decision" and "R4-B1 runtime wiring of the F3-F7 spine into the live cycle is a separate OWNER increment".
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable and a completed increment must not permanently own it.
- `docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture and `UNAPPROVED SCOPE CHANGES` must be exactly `NONE`.
- `deploy/cron.d/ohm-unified-cycle`: the single host scheduler entry that owns the unified cycle. This is the only orchestration path R4-B1 may modify.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the R4-B1 contract-freeze increment and the repository's movable active-increment pointer
WHEN:
the scope contract and the pointer are inspected
THEN:
the contract exists, declares its own increment identity, the pointer resolves to an existing scope contract, and this increment never pins the global pointer to its own identity
AC-002:
GIVEN:
the deferred R4-B0 resume-semantics question and the enumerated retry-versus-requalification trigger cases
WHEN:
the frozen R4-B1 disposition semantics are inspected
THEN:
the contract distinguishes EXACT_RETRY, REQUALIFIED_NEW_DECISION and CONFLICTING_ATTEMPT as three separate dispositions, states a disposition for every enumerated trigger case, and states that committed admission ancestry and committed economics are immutable and that ambiguity fails closed
AC-003:
GIVEN:
the production orchestration boundary
WHEN:
it is inspected
THEN:
exactly one existing scheduler entry owns the unified cycle, the contract names that single orchestration path as the only path R4-B1 may modify, and the contract forbids a second scheduler, timer, cron entry or orchestration loop
AC-004:
GIVEN:
the R4-B1 runtime activation posture and authority boundary
WHEN:
they are inspected
THEN:
the frozen posture is wiring-present, target path non-authoritative, Feature Bus off, Paper-v2 off and legacy authoritative, and the contract enumerates each authority that must remain unchanged and each authority class R4-B1 may not create, including funded trading, exchange order authority and Committee runtime authority
AC-005:
GIVEN:
the rollback and exclusion requirements
WHEN:
they are inspected
THEN:
the contract names an exact rollback to the pre-R4-B1 orchestration and lists explicit exclusions including no R4-B2 activation, no funded or exchange authority, no Committee runtime authority, no legacy retirement, no F11 bypass and no hidden mode activation
AC-006:
GIVEN:
the repository-controlled production configuration and the scheduler set
WHEN:
the freeze posture is audited
THEN:
the Feature Bus remains off and Paper-v2 remains unset in the repository compose configuration, exactly one scheduler entry invokes the unified cycle, and this increment introduces no funded, exchange, order-placement or Committee authority import

EXPLICITLY OUT OF SCOPE:
- Implementing any R4-B1 runtime wiring, or wiring any part of the F3-F7 spine into the unified cycle, the opportunity scan, Telegram, alerts, paper v1, Freqtrade or any dashboard
- Activating Paper-v2, the Feature Bus production mode, F7 runtime admission, or the Committee
- Funded or live trading, exchange order placement, modification, cancellation or confirmation, margin, asset borrow or leverage
- Retiring, deleting or re-weighting the legacy selector, Top-8, profit-ranking, Freqtrade dry-run or Paper-v1
- A second scheduler, timer, cron entry, orchestration loop, Feature Bus, detector authority, opportunity lifecycle, forecast authority, allocation authority, reservation authority, paper engine, outcome truth system or evidence store
- Changing F3-F7 economics, selector behavior, forecast behavior, geometry behavior, P&L sign, protection semantics, handoff semantics, feature flags or deployment behavior
- Modifying architecture documents, the v1.4.3 DOCX, the ATDD checker, workflows or `pyproject.toml`
- Weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
Runtime orchestration boundary.
Exactly one host scheduler entry owns the unified cycle: `deploy/cron.d/ohm-unified-cycle` invokes `app.jobs.run_cycle` once per minute, and `run_cycle` owns the internal ordering (Kraken reconciliation, operator decision, active-position protection plus Paper-v2 protection, discovery, then non-authoritative workloads). R4-B1 may modify that single boundary only. It must not add a second scheduler, timer, cron entry, daemon or orchestration loop, and `app.opip.features.pipeline.run_cycle` is a distinct per-instrument feature-bus function, not a second orchestration boundary.

R4-B1 activation posture.
The frozen R4-B1 posture is: runtime wiring PRESENT; target F3-F7 path NON-AUTHORITATIVE; `OPIP_FEATURE_BUS_MODE` OFF; `OPIP_PAPER_V2_MODE` OFF/unset; F7 not the admission authority; Top-8 plus profit-ranking and Freqtrade dry-run plus Paper-v1 remain the authoritative paper engines. R4-B1 may not activate a target mode merely because wiring exists.

Frozen disposition semantics.
The canonical Paper-v2 disposition identity is the economic-opportunity identity, and it keys only the admission request: `stable_hash("PDISP", {episode_id, native_symbol, engine})` with `direction` added to the payload **only for a non-LONG direction**. A LONG keeps the historical three-key payload, so every pre-direction LONG identity stays stable across the upgrade and an already-admitted LONG is still found on a retry; a SHORT gets a direction-distinct identity. The identity is therefore direction-distinguishing for SHORT but not for LONG, and this asymmetry is frozen rather than replaced by a symmetric four-key identity.

The identity keys ONLY the admission request. Only admission-request facts participate: the decision context, `requested_capital`, `requested_reservation_amount` and `direction`. Facts owned by later stages - `requested_quantity`, `requested_notional`, the stop, the targets and the execution geometry - are NOT admission-identity facts. They live on the ENTRY order intent (whose identity is fixed by the paper trade) and on the immutable protection plan, both of which are reused verbatim once committed. A change to one of them therefore cannot create a second admission and cannot silently re-price committed economics.

The decision-context identity is a separate, decision-facts identity. The writer keys admission on the disposition identity and refuses a retry whose admission-request payload is not identical through `_existing_admission_result` (`IDEMPOTENCY_PAYLOAD_CONFLICT`). The following three dispositions are distinct and are never collapsed:

- EXACT_RETRY - the same disposition identity with a byte- or semantically identical admission request, replayed after a restart or a lost acknowledgement. It is idempotent: the writer answers with the already-committed result and produces no new event, no new reservation and no duplicate trade.
- REQUALIFIED_NEW_DECISION - the same episode, symbol and direction re-qualified with materially changed decision facts. It derives a different decision context (a different `decision_context_id`, which is an admission-request fact) but the same disposition identity, so the admission request payload no longer matches and the attempt is refused as a conflicting decision. It does not resume, re-admit, re-size or re-price the committed trade.
- CONFLICTING_ATTEMPT - any attempt that reuses a committed disposition identity with different admission-request facts. It is refused fail-closed by `_existing_admission_result` and never mutates committed state.

Every enumerated trigger case names its owning stage and its frozen disposition, with any conditional resolution fully specified:

| Trigger case | Owning stage | Frozen disposition | Frozen rule |
| --- | --- | --- | --- |
| the disposition identity already exists | admission | EXACT_RETRY if the admission request is identical, otherwise CONFLICTING_ATTEMPT | The committed admission request payload is compared byte-identically, ignoring only the `direction_contract_version` format marker. Identical => idempotent replay. Different => `IDEMPOTENCY_PAYLOAD_CONFLICT`, no mutation. |
| an admission already exists | admission | EXACT_RETRY | Admission is never re-run into a second trade. The already-committed admission is authoritative and its ancestry is immutable. |
| retry facts are byte or semantically identical | admission | EXACT_RETRY | Idempotent. No new event, reservation, disposition, trade or fill identity. |
| qualification facts differ | decision context | REQUALIFIED_NEW_DECISION | The changed qualification derives a different `decision_context_id`, which is an admission-request fact, so the attempt is refused as a conflicting decision. |
| snapshot changes | decision context | REQUALIFIED_NEW_DECISION | A different evidence snapshot changes `decision_context_id`; the committed disposition is not re-admitted or re-priced. |
| evidence cutoff changes | decision context | REQUALIFIED_NEW_DECISION | The cutoff is a decision-context identity input, so a shifted cutoff is refused. |
| policy or version changes | decision context | REQUALIFIED_NEW_DECISION | The policy version and fingerprint are decision-context identity inputs, so a policy change is refused rather than re-applied. |
| requested capital changes | admission | CONFLICTING_ATTEMPT | `requested_capital` is an admission-request fact, so a changed amount is a payload conflict. |
| requested reservation amount changes | admission | CONFLICTING_ATTEMPT | `requested_reservation_amount` is an admission-request fact, so a changed amount is a payload conflict. |
| requested notional changes | entry intent | not an admission conflict | `requested_notional` is an ENTRY order-intent fact. A committed intent is reused verbatim; a notional above the committed reservation is refused by the writer's order-intent ancestry check. It cannot create a second admission. |
| quantity changes | entry intent | not an admission conflict | `requested_quantity` is an ENTRY order-intent fact, reused verbatim once committed. It cannot re-price a committed trade or create a second admission. |
| stop changes | protection plan | not an admission conflict | The immutable committed protection plan is reused verbatim; a changed stop cannot mutate committed exposure. |
| targets change | protection plan | not an admission conflict | The immutable committed protection plan is reused verbatim; changed targets cannot mutate committed exposure. |
| execution geometry changes | protection plan | not an admission conflict | The committed execution-geometry plan is reused verbatim; it cannot be rebuilt from changed configuration. |
| decision context ancestry changes | admission | CONFLICTING_ATTEMPT | `decision_context_id` is an admission-request fact, so a changed context is refused by `_existing_admission_result`. The reservation-ancestry check applies to later order-intent, attempt and fill events, not to this refusal. |
| direction changes | disposition identity | a distinct opportunity | `direction` participates in the disposition identity only for a non-LONG direction, so an opposite direction is a different economic opportunity (a new disposition) rather than a conflict; a LONG keeps the historical identity. |
| an earlier terminal stop already exists | execution | EXACT_RETRY only | A terminal decision is not silently reopened. A later attempt resolves idempotently to the terminal record; it never opens exposure. |
| a committed reservation exists | admission | EXACT_RETRY only | The reservation is not duplicated or released by a conflicting attempt; release remains the writer's terminal-reconciliation authority. |
| canonical progress is temporarily unreadable | - | fail closed | No admission, no release and no reopening. The attempt is retryable only after canonical state is readable. |

Immutability and fail-closed rules.
Only admission-request facts participate in the disposition identity (decision context, capital, reservation amount, direction). Facts owned by later stages - quantity, notional, stop, targets and execution geometry - are not admission-identity facts, so they cannot create a second admission or silently re-price committed economics; the committed ENTRY order intent and the committed immutable protection plan are reused verbatim. Committed admission ancestry is immutable. An exact retry is idempotent. A changed qualification is a conflicting decision, not a resume. No duplicate trade, duplicate reservation or duplicate disposition is created. Ambiguity fails closed. Restart is deterministic and reconstructs the same identities. Terminal decisions are not silently reopened. Canonical state remains authoritative.

A genuinely new trade for the same episode and direction requires a new episode identity, which F4 owns through its dedup, deferral, deadline and expiry semantics. Adding a decision sequence to the disposition identity is a contract change, not a cleanup, and is out of scope here.

Authority boundary.
R4-B1 must not create, widen or imply: funded trading; funded exchange order authority; Kraken order placement, modification, cancellation or confirmation; margin, asset borrow or leverage; Committee runtime authority; dashboard trading authority; Telegram trading authority; or a second reservation, allocation, execution or evidence authority. Risk, strategy, execution and protection authority are unchanged. Everything frozen in R4-B0 remains frozen.

Rollback.
The rollback for R4-B1 is exact: remove the R4-B1 orchestration hook and the R4-B1 composition module, restoring the pre-R4-B1 `app.jobs.run_cycle` sequence with all modes still off. Rollback restores exactly one authority (the legacy path) and must never leave two allocation authorities running. Because R4-B1's target path is non-authoritative, rollback is a code revert with no canonical-data migration.

Exclusions.
R4-B1 explicitly excludes: R4-B2 activation; any funded or exchange authority; any Committee runtime authority; any legacy retirement or deletion; any bypass of the F11 protection gate; any hidden mode activation; any second scheduler; and any change to F3-F7 economics.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_b1_contract_freeze.py::test_ac_001_contract_and_pointer_are_consistent
AC-002 -> tests/test_opip_r4_b1_contract_freeze.py::test_ac_002_retry_and_requalification_semantics_are_frozen
AC-003 -> tests/test_opip_r4_b1_contract_freeze.py::test_ac_003_single_orchestration_boundary
AC-004 -> tests/test_opip_r4_b1_contract_freeze.py::test_ac_004_dormant_posture_and_authority_boundary
AC-005 -> tests/test_opip_r4_b1_contract_freeze.py::test_ac_005_rollback_and_exclusions_are_explicit
AC-006 -> tests/test_opip_r4_b1_contract_freeze.py::test_ac_006_no_authority_and_single_scheduler_entry

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_contract_freeze.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_contract_freeze.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_contract_freeze.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_contract_freeze.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_contract_freeze.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B1-contract-freeze.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b1_contract_freeze.py

DEFERRED DISCOVERIES:
- The runtime-integration increment that implements the wiring frozen here is not authorized by this contract. It must be its own OWNER-frozen increment and it must reconcile against the accepted `main` SHA at that time before integration.
- The frozen retry semantics are unreachable while `OPIP_PAPER_V2_MODE` is off, because `run_paper_v2_opportunity` refuses at the activation gate before any canonical interaction. They constrain R4-B2 and later.
- A future increment that legitimately needs a second, later trade for the same episode and direction must introduce a new episode identity in F4. Adding a decision sequence to the disposition identity is a contract change, not a cleanup.
- R4-B1 runtime wiring will change the R4-B0 assertion `test_composition_authority_targets_are_unchanged_on_disk`, which asserts that `run_cycle` and `scan_opportunities` do not mention the target spine. That assertion must be reconciled by the runtime-integration increment as an explicitly recorded posture change (wiring present, authority unchanged), not silently deleted.
- The production Feature Bus market-data source is not scheduled today; `app.jobs.run_feature_bus_pilot` is manual-only. Whether R4-B1 or a later increment activates a scheduled snapshot source is an owner decision, because activating a source widens evidence capture.
- F11 protection ordering is a prerequisite for R4-B2 activation, not for R4-B1 wiring. R4-B1 must not bypass it.
- The decision-context identity includes `evaluation_time` (`app/opip/decision_intelligence/events.py`, `context_identity_v2`). A retry that presents the same material qualification facts but a later `evaluation_time` therefore derives a different `context_id` and is refused as a payload conflict rather than resuming. The freeze does not give `evaluation_time` its own trigger row, so whether it is a material decision fact (refused) or a retry artifact (resumed) must be settled by R4-B2. Unreachable while Paper-v2 is off.
- `run_paper_v2_opportunity` commits the decision snapshot and the decision context before it reads canonical progress and admits (`app/services/paper_v2_execution.py`), so a changed-qualification attempt that will never be admitted still appends a new snapshot and context to canonical evidence, and repeated attempts grow that evidence. The freeze does not state whether that pre-admission evidence append is permitted; R4-B2 must decide the gating order (for example, resolving the disposition/context before appending new context evidence). Unreachable while Paper-v2 is off.
- The freeze's disposition-identity description and the admission-request fact list are cross-checked against the frozen code by `tests/test_opip_r4_b1_contract_freeze.py`. If a later increment changes `build_disposition_id` or the `PaperAdmissionRequest` field set, that test fails and the freeze must be re-derived rather than silently drifted.

UNAPPROVED SCOPE CHANGES:
NONE
