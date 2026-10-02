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
The canonical Paper-v2 disposition identity is the economic-opportunity identity: `PDISP` over (episode_id, native_symbol, direction). The decision-context identity is a separate, decision-facts identity. The writer keys admission on the disposition identity and requires the committed decision context and reservation ancestry to agree. The following three dispositions are distinct and are never collapsed:

- EXACT_RETRY - the same disposition identity with a byte- or semantically identical admission request, replayed after a restart or a lost acknowledgement. It is idempotent: the writer answers with the already-committed result and produces no new event, no new reservation and no duplicate trade.
- REQUALIFIED_NEW_DECISION - the same episode, symbol and direction re-qualified with materially changed decision facts. It derives a different decision context but the same disposition identity. It does not resume, re-admit, re-size or re-price the committed trade; it is refused as a conflicting decision.
- CONFLICTING_ATTEMPT - any attempt that reuses a committed disposition identity with different admission-request facts, or that presents changed context, geometry or economics for a committed disposition. It is refused fail-closed and never mutates committed state.

Every enumerated trigger case is frozen to exactly one disposition:

| Trigger case | Frozen disposition | Frozen rule |
| --- | --- | --- |
| the disposition identity already exists | EXACT_RETRY when the request is identical, otherwise CONFLICTING_ATTEMPT | The committed admission request payload is compared byte-identically, ignoring only the `direction_contract_version` format marker. Identical => idempotent replay. Different => refused, no mutation. |
| an admission already exists | EXACT_RETRY or CONFLICTING_ATTEMPT | Admission is never re-run into a second trade. The already-committed admission is authoritative and its ancestry is immutable. |
| retry facts are byte or semantically identical | EXACT_RETRY | Idempotent. No new event, reservation, disposition, trade or fill identity. |
| qualification facts differ | REQUALIFIED_NEW_DECISION | The committed qualification is not silently replaced. The changed qualification is refused as a conflicting decision. |
| snapshot changes | REQUALIFIED_NEW_DECISION | A different evidence snapshot for a committed disposition is not re-admitted or re-priced; it is refused. |
| evidence cutoff changes | REQUALIFIED_NEW_DECISION | The committed decision boundary is immutable; a shifted cutoff is refused. |
| policy or version changes | REQUALIFIED_NEW_DECISION | The committed qualification policy is immutable for the committed disposition; a policy change is refused rather than re-applied. |
| quantity changes | CONFLICTING_ATTEMPT | Committed economics cannot silently mutate; a different quantity is refused. |
| requested capital or notional changes | CONFLICTING_ATTEMPT | Committed reservation economics cannot silently mutate; a different amount is refused. |
| stop changes | CONFLICTING_ATTEMPT | The committed protection geometry cannot silently mutate; a different stop is refused. |
| targets change | CONFLICTING_ATTEMPT | The committed exit geometry cannot silently mutate; different targets are refused. |
| execution geometry changes | CONFLICTING_ATTEMPT | The committed execution geometry identity cannot silently mutate; a different geometry is refused. |
| decision context ancestry changes | CONFLICTING_ATTEMPT | A context that does not match the committed admission ancestry is refused by the reservation-ancestry check; committed ancestry is never rewritten. |
| an earlier terminal stop already exists | EXACT_RETRY only | A terminal decision is not silently reopened. A later attempt resolves idempotently to the terminal record or is refused; it never opens exposure. |
| a committed reservation exists | EXACT_RETRY only | The reservation is not duplicated or released by a conflicting attempt; release remains the writer's terminal-reconciliation authority. |
| canonical progress is temporarily unreadable | fail closed | No admission, no release and no reopening. The attempt is retryable only after canonical state is readable. |

Immutability and fail-closed rules.
Committed admission ancestry is immutable. Committed economics (capital, notional, quantity, stop, targets, execution geometry) cannot silently mutate. An exact retry is idempotent. A changed qualification is a conflicting decision, not a resume. No duplicate trade, duplicate reservation or duplicate disposition is created. Ambiguity fails closed. Restart is deterministic and reconstructs the same identities. Terminal decisions are not silently reopened. Canonical state remains authoritative.

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

UNAPPROVED SCOPE CHANGES:
NONE
