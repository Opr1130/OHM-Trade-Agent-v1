INCREMENT:
ATDD-R5-E-research-paper-execution

OWNER-APPROVED INTENT:
Deliver the R5-E slice that consumes one committed capital admission and dispatches exactly one F8 research-paper execution, and nothing else. The seam `execute_admitted_research_paper_opportunity` in `app/services/paper_v2_capital_admission_execution.py` reads the committed paper admission evidence of exactly one disposition identity through the existing canonical read projection (`WriterClient.get_paper_v2_execution_state`), refuses before dispatch when that evidence is absent, unusable, uncommitted or divergent from the intent being dispatched, and otherwise calls the single existing paper execution producer `app.services.paper_v2_execution.run_paper_v2_opportunity` exactly once and reports the producer's own canonical outcome verbatim. The increment exists because the increment that closed the admission write path, `ATDD-R5-E-capital-admission-integration`, records this dependency explicitly as a deferred discovery: "Nothing in the repository calls the seam yet, so the seam has no production consumption: wiring it into the R5-C capital-intelligence or R5-D selector runtime is the next R5-E integration step, and it needs its own increment, its own dormant-mode proof, its own release-profile decision and an owner-approved activation path." This increment is that step, and it is delivered by reuse only: no new `WriterIntent` kind, no new event type, no new table, no new schema version, no second store, no second producer, no second capital path, no allocator, no scheduler, no budget, no retry loop, no clock, no environment read, no secret, no funded-account path and no order or exchange surface. `grants_execution_authority` remains hard-coded `False` in every record the seam returns. The owner directive that governs the R5-E line, and that this increment obeys unchanged, is: reuse the existing reservation and execution authority, do not automatically create a new `WriterIntent` kind, fail closed on missing `OQ-05`/`OQ-06` policy rather than inventing a numerical rule, and never treat free cash alone as deployable capital. This increment claims no new authority: it is a consume-and-dispatch seam whose only canonical read is the existing committed-state projection and whose only canonical write is the existing producer's own transaction.

ARCHITECTURE REFERENCES:
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-07 "Reservation, atomic consumption and concurrent change": the reservation this seam consumes was compared and committed atomically by the canonical writer under the existing rule, and concurrent change already fails closed there. The seam adds no second consumption, no release, no re-reservation and no concurrent-change path of its own; it reads the committed result and never re-decides it.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-12 "Canonical ordering, temporal contract and shared writer": `REUSE_EXISTING_MODULE`; exactly one canonical writer owns durable domain records. The seam is a caller of that writer's read projection and of exactly one existing execution producer, and it adds no new event store, allocator database, outcome engine, position registry or second writer.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-11 "Paper isolation and research environment": paper management is deterministic, portfolio-aware and separately identified, with no funded write and no exchange order authority, `EVIDENCE_SHADOW` remains the production posture with `TARGET_PAPER` `BLOCKED` and `OPIP_PAPER_V2_MODE=off`. The seam is dormant under that posture: no runtime module imports it, and it adds no mode key that could activate it.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-08 "Portfolio planning and rotation selection": the plan and the rotation decision stay owned by the increments that select them; this seam consumes the committed admission that those selections already produced and re-derives no selection, no rotation-group record and no plan.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-06 "One canonical capital record at one committed version": the committed admission is read as a declared value from the canonical projection; the seam re-derives no capital, mutates no capital record and holds no book of its own.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`, row `R5-E` "Deterministic portfolio paper management with single canonical writer, bounded retries and fail-closed isolation" (contracts `SC-09`/`SC-11`, open input `OQ-08`, gate `G5`, authority `PAPER_RESEARCH`); clause row `CI-04` "Compare-and-commit one reservation plan against one capital snapshot".
- `docs/architecture/v1.5.0/ARCHITECTURE.md`: one producer per concern, one canonical writer, capital committed at one atomic boundary, a consumer cannot widen its own limits, and an unresolved policy must never be treated as "no limits".
- `AGENTS.md`: fixed risk sizing, minimum R:R, execution safeguards and paper isolation are preserved; the smallest change that solves the proven problem; no second scheduler, database, store or deployment control plane; no live-trading authority for coding automation.
- Owner decision of this session: approve `ATDD-R5-E-research-paper-execution` as the R5-E F8 research-paper execution seam, consuming committed admission evidence and dispatching the existing producer, keeping the seam dormant with no trading authority and no activation path.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
one canonical paper book holding exactly one committed capital admission of one disposition identity - one admission request, one order intent, one execution attempt, one committed fill and one canonical reservation - and one research-paper opportunity for that same episode, instrument, direction and quote currency
WHEN:
the seam dispatches that opportunity
THEN:
the outcome is `EXECUTED` with `admission_consumed` true, the producer's own terminal status is surfaced verbatim as the reason code, the existing producer ran exactly once, the seam reports the producer's own committed identities (`paper_trade_id`, `reservation_id`, `entry_order_intent_id`, `fill_id`) instead of any identity it invents, the disposition identity is the one the existing producer derives (`build_disposition_id`), the canonical book still holds exactly one admission request, one execution attempt, one fill and one active reservation, and no second execution, reservation, disposition or event type was created
AC-002:
GIVEN:
a canonical book already holding a committed admission and a writer client that records every call it receives
WHEN:
the seam dispatches the admitted opportunity and is inspected at source level
THEN:
the dispatch performs no admission RPC (`admit_paper_opportunity`, `admit_planned_reservation`), no reservation write, no release and no order-intent, quote-evidence, protection or stage write of its own, every canonical event count and the whole portfolio projection (version, active reservations, reserved capital) are unchanged by the dispatch, the module imports nothing beyond `__future__`, `dataclasses`, `datetime`, `typing` and the existing execution producer, and its only canonical call sites are the read-only progress read `get_paper_v2_execution_state` and the producer call `run_paper_v2_opportunity`, so the seam holds no capital authority of its own
AC-003:
GIVEN:
four canonical states in which no committed admission of this disposition identity exists: an empty canonical book, a canonical admission the writer refused (`CAPITAL_REJECTED`), a canonical state the writer does not settle (`RETRYABLE`, `SQLITE_ERROR`), and an admitted paper trade with no committed reservation
WHEN:
the seam dispatches each of them while the market transport refuses to serve any book
THEN:
each dispatch is `BLOCKED` with the closed reason `NO_COMMITTED_ADMISSION`, `UNUSABLE_CANONICAL_STATE` or `UNCOMMITTED_RESERVATION`, no execution is reported (`execution` is unset), `admission_consumed` is false, the canonical state is surfaced verbatim (the canonical disposition, the canonical error code and the absent reservation are reported, never reinterpreted as admission or as success), no market read is performed at all, and the canonical store holds no new event of any kind
AC-004:
GIVEN:
a committed admission whose frozen request is the one the canonical authority wrote, and an opportunity whose requested reservation amount or quote currency differs from that committed evidence
WHEN:
the seam dispatches that divergent opportunity
THEN:
the dispatch is `BLOCKED` with reason `ADMISSION_INTENT_DIVERGENCE` before any dispatch happens, both the committed and the requested values are reported in the detail, the divergent canonical evidence is surfaced verbatim, `admission_consumed` is false, no execution is reported and no market read is attempted, so a committed admission can never be spent on an intent it does not describe
AC-005:
GIVEN:
a committed admission whose executed paper trade carries committed fill evidence including the trade's own fees, costs, slippage and spread, and separately a committed admission whose producer reached a zero-fill terminal with `FINAL_VERIFIED` reconciliation, no committed fill and no reserved capital
WHEN:
the seam dispatches both
THEN:
both are `EXECUTED` and report the producer's own terminal status verbatim, the fill-bearing one reports the producer's own committed `fill_id` and filled quantity read from the committed fill and carries no economic value of its own, the zero-fill one is reported as the producer's own `NO_FILL_TERMINAL` with no invented fill, no fabricated zero and no fabricated failure, the seam's outcome exposes no field for fees, slippage, spread or latency at all, and neither dispatch adds a canonical event
AC-006:
GIVEN:
a committed admission and a producer that raises during dispatch (market data unavailable), and separately a dispatch that is refused locally before it can dispatch
WHEN:
the seam attempts each of them
THEN:
the producer failure is reported as `FAILED` with reason `PRODUCER_FAILED` and claims no execution, no fill and no canonical success, the canonical store is left exactly as the producer's own transaction left it with no seam-authored rollback, repair, release or compensating event, a refusal reports no execution and performs no market read, and no failure or refusal is ever reported as a success
AC-007:
GIVEN:
a committed admission that has already been dispatched once, and the same committed admission reached again by a replay and by a canonical store that is closed and reopened
WHEN:
the seam dispatches the same opportunity a second time, and dispatches again against the reopened store
THEN:
the replay and the post-restart dispatch report the same outcome, the same producer `fill_id` and the same producer identities as the first, they report the committed admission as consumed, the canonical event counts and the portfolio projection recorded after the first dispatch are unchanged by the replay, exactly one admission consumption and one committed fill remain, and the post-restart dispatch proves the consumption is durable canonical evidence rather than process-scoped seam state
AC-008:
GIVEN:
the seam module source, its dispatch signature and its outcome record
WHEN:
they are inspected
THEN:
the module reads no environment variable, no secret and no clock, holds no fee, slippage, latency, spread, risk, size or ratio knob and no floating-point policy constant of its own, the dispatch accepts only the opportunity positionally plus `client`, `kraken_client`, `settings`, `now` and `clock` as keyword-only dependencies, the caller's instant is used because the seam reads no wall clock of its own, and every limit, tolerance and disposition stays the producer's and the canonical writer's
AC-009:
GIVEN:
the seam's records, the application module tree, and the guards that own the F11 target-domain mutating authority and the R5-C capital-record isolation
WHEN:
each is inspected alongside the seam
THEN:
every record refuses to grant execution authority (`grants_execution_authority` is false by default and a `ValueError` is raised when it is constructed true), no module under `app/` imports or references the seam so it stays dormant under `OPIP_PAPER_V2_MODE=off`, the seam is not declared in the F11 target-mutator caller census, it calls no protected target mutator, it declares no capital book of its own (`CapitalSnapshot`, `CashPartitionLedger` and `capital_rotation_intelligence` are absent), and no guard owned by another increment is weakened, silenced or reworded
AC-010:
GIVEN:
this increment, its scope contract, the active-increment pointer and the repository release-profile contract with the Compose literals
WHEN:
they are inspected
THEN:
the increment's own scope contract parses, is self-consistent, traces every criterion to its matching acceptance test and maps it to its changed files, declares `NONE` for unapproved scope changes and pins no global active-increment pointer, the pointer resolves to an existing contract and names this increment, and the `EVIDENCE_SHADOW` profile, the blocked `TARGET_PAPER` profile and the Compose `OPIP_PAPER_V2_MODE=off` posture are unchanged with no new `OPIP_R5*` mode key

EXPLICITLY OUT OF SCOPE:
- Admitting capital. The seam never calls `admit_paper_opportunity`, `admit_planned_reservation` or any other reservation authority; producing the committed admission it consumes stays owned by `ATDD-R5-E-capital-admission-integration` and the canonical writer.
- Selecting, planning or rotating. Opportunity selection, portfolio planning and rotation-group switching stay owned by the existing R5-C/R5-D increments; the seam re-derives no selection and evaluates no rotation comparison.
- Changing the execution producer. `app.services.paper_v2_execution` is consumed as-is: no new stage, event type, disposition, terminal state, retry, bound, budget or tolerance is added to it, and no second producer is created.
- Any funded-account, exchange-order, credential or private-API surface. The seam places no order, cancels nothing, reads no funded balance and holds no secret.
- Activation. No scheduler, worker, telegram path, feature flag, mode key or Compose literal wires the seam into runtime; runtime wiring needs its own increment, its own dormant-mode proof, its own release-profile decision and an owner-approved activation path.
- Reconciliation and recovery automation. An operator-driven recovery of a failed or divergent dispatch is not automated here; the seam performs no in-seam retry, no backfill, no compensating write and no state repair.
- Changing `OQ-05`, `OQ-06` or `OQ-08`, any risk default, any minimum R:R, any sizing rule, the canonical schema, any retention rule, or any other increment's contract, guard or test.

FROZEN BOUNDARIES:
1. One producer, one dispatch. The only execution is exactly one `run_paper_v2_opportunity` call per dispatch, carrying the caller's own opportunity; the seam holds no retry, no loop, no fallback and no second call site. The only other canonical call site is the read-only committed-state read `get_paper_v2_execution_state`, which happens before the dispatch decision and never after it.
2. Read first, refuse locally, dispatch second. Identity derivation, canonical-state usability, committed-admission presence, reservation commitment and intent divergence are all decided from the committed projection before any producer or market access, so an unprovable or divergent dispatch cannot reach the producer at all.
3. Closed refusal vocabulary, verbatim reporting. The blocked reasons are exactly `NO_COMMITTED_ADMISSION`, `UNUSABLE_CANONICAL_STATE`, `UNCOMMITTED_RESERVATION` and `ADMISSION_INTENT_DIVERGENCE`, the outcomes are exactly `BLOCKED`, `EXECUTED` and `FAILED`, and every canonical value the seam reports - disposition, status, error code, identity, reason code - is surfaced verbatim and never reinterpreted, downgraded to success or upgraded from failure.
4. No reinterpretation of a canonical refusal as admission, and no reinterpretation of an unsettled acknowledgement as a refusal: a `RETRYABLE` canonical state is `UNUSABLE_CANONICAL_STATE`, never "nothing was committed".
5. The committed admission is the only capital input. The seam reads the admission request the canonical authority wrote and compares the intent being dispatched against it; it never re-derives the reservation amount, the quote currency, the policy version, the limit or the tolerance, and free cash is never treated as deployable capital.
6. Fixture and identity authority. The disposition identity is the existing producer's own derivation (`build_disposition_id`); the seam invents no second identity, no hash, no salt, no sequence and no generator.
7. No clock, no environment, no secret, no policy knob. The caller supplies the instant and the callable dependencies; the module reads no wall clock, no environment variable, no secret and no floating point constant of its own.
8. Dormant by construction. No module under `app/` imports the seam, no release profile or Compose literal names it, and it adds no mode key; under `EVIDENCE_SHADOW` with `OPIP_PAPER_V2_MODE=off` the seam is unreachable from runtime.
9. Zero authority. `grants_execution_authority` is `False` in every returned record, the record refuses to be constructed with it `True`, and no record can reserve, release, widen, authorize or dispatch anything by itself.
10. Isolation from funded accounts and production data. Only the canonical paper lineage is read, no funded account or exchange credential is reachable, no order is placed, no analytics store or database is added, and no production data is mutated.
11. Determinism and idempotent consumption. The same inputs against the same committed state produce the same outcome and the same reported identities; a replay or a post-restart dispatch consumes the one committed admission without adding canonical evidence, and nothing is deleted, backfilled or rewritten.
12. Additive and reversible. The increment adds one dormant module, its acceptance tests, one documentation contract, one active-increment pointer update and no ratified-guard change; it changes no frozen behavior, no persisted format, no limit and no reader, so reverting the increment reverts the proof rather than a production capability.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_001_a_committed_admission_is_dispatched_into_one_execution
AC-001 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_001_the_seam_adds_no_identity_of_its_own
AC-002 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_002_a_dispatch_writes_no_admission_reservation_or_stage
AC-002 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_002_the_seam_has_no_admission_or_write_surface
AC-003 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_003_a_store_without_a_committed_admission_blocks_execution
AC-003 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_003_a_refused_canonical_disposition_blocks_execution
AC-003 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_003_an_unusable_canonical_state_blocks_execution
AC-003 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_003_an_admitted_trade_without_a_committed_reservation_blocks_execution
AC-004 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_004_a_divergent_reservation_amount_is_refused_before_dispatch
AC-004 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_004_a_divergent_quote_currency_is_refused_before_dispatch
AC-005 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_005_a_committed_fill_is_reported_from_committed_evidence
AC-005 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_005_a_committed_zero_fill_terminal_is_reported_verbatim
AC-006 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_006_a_producer_failure_fails_closed_and_claims_no_execution
AC-006 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_006_a_blocked_dispatch_reads_no_market_and_writes_nothing
AC-007 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_007_a_replay_of_the_committed_admission_consumes_it_once
AC-007 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_007_a_restart_after_execution_resumes_without_new_evidence
AC-008 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_008_the_seam_owns_no_policy_clock_secret_or_environment
AC-008 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_008_the_dispatch_accepts_no_economic_knob
AC-009 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_009_the_seam_grants_no_authority_and_refuses_to
AC-009 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_009_no_application_module_imports_the_seam
AC-009 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_009_the_frozen_guards_do_not_declare_this_seam
AC-010 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_010_the_increment_contract_and_pointer_are_self_consistent
AC-010 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_010_the_release_profile_and_posture_are_unchanged
AC-010 -> tests/test_opip_r5_e_research_paper_execution.py::test_ac_010_no_release_or_runtime_surface_names_the_seam

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-002 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-003 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-004 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-005 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-006 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-007 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-008 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-009 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py
AC-010 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-E-research-paper-execution.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_research_paper_execution.py

DEFERRED DISCOVERIES:
- Nothing in the repository calls this seam yet, so the increment has no production consumption: wiring it into a scheduler, worker or runtime root is a later R5-E step that needs its own increment, its own dormant-mode proof, its own release-profile decision and an owner-approved activation path. Until then the seam stays dormant under `OPIP_PAPER_V2_MODE=off` and no module under `app/` names it.
- The committed admission is consumed by disposition identity, and the identity is the producer's own derivation (`build_disposition_id`), so an opportunity whose episode, instrument or direction differs from the committed admission is not refused as a divergence but simply has no committed admission of its own and is blocked as `NO_COMMITTED_ADMISSION`. Any cross-identity reconciliation policy is left open and is deliberately not invented here.
- The seam treats a committed admission as valid evidence without re-reading the capital policy version, the equity limit or the live portfolio: re-verifying them would duplicate the writer's authority and make this seam a second source of truth. A committed admission that is later superseded or released upstream is therefore still consumed as evidence, and any supersession or expiry policy for committed admissions remains open.
- Recovery from a `FAILED` producer or an already-consumed admission is not automated: the seam performs no retry, no backfill and no compensating canonical write. An operator-driven re-attempt remains the only supported recovery path.
- `OQ-05` (turnover semantics), `OQ-06` (sleeve caps) and `OQ-08` (legacy paper-engine coexistence) remain owner inputs and are unchanged by this increment; the seam adds no limit, tolerance or policy that could stand in for them.

UNAPPROVED SCOPE CHANGES:
NONE
