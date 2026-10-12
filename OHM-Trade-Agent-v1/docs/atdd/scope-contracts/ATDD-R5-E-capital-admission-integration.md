INCREMENT:
ATDD-R5-E-capital-admission-integration

OWNER-APPROVED INTENT:
Deliver the R5-E integration slice that gives one R5-D planned reservation one durable canonical owner, and nothing else. The seam `admit_planned_reservation` in `app/services/capital_admission_integration.py` translates exactly one active planned reservation of one `PortfolioReservationPlan` into exactly one frozen `PaperAdmissionRequest` and calls the existing canonical authority `WriterClient.admit_paper_opportunity` exactly once, then reports the canonical writer's own decision verbatim. The increment exists because the increment that closed the reservation authority, `ATDD-R5-E-canonical-reservations`, records the dependency explicitly as a deferred discovery: "Wiring the R5-C capital-intelligence planner to the canonical reservation authority is a later R5-E integration slice: it would give a service a durable write path, which this increment deliberately does not do." This increment is that slice. It delivers the service write path only by reuse: no new `WriterIntent` kind, no new event type, no new table, no second store, no allocator, no scheduler, no environment read, no order or exchange surface, and `grants_execution_authority` remains hard-coded `False` in every record. The owner directive that governs the R5-E line, and that this increment obeys unchanged, is the one recorded in `ATDD-R5-E-canonical-reservations`: reuse the existing reservation authority, do not automatically create a new `WriterIntent` kind, fail closed on missing `OQ-05`/`OQ-06` policy rather than inventing a numerical rule, and never treat free cash alone as deployable capital. This increment claims no new authority: it is a translation and reporting seam whose every write is the existing atomic admission RPC and whose every read is the existing portfolio projection.

ARCHITECTURE REFERENCES:
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-07 "Reservation, atomic consumption and concurrent change": reservation compares the expected portfolio version and commits capital and risk usage atomically, and concurrent change fails closed rather than over-committing (`CI-04`). This is the clause the seam is delivered under: it supplies the expected portfolio version (read fresh, or reused verbatim when re-attempting a recorded identity) and the existing writer compares it and refuses atomically.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-12 "Canonical ordering, temporal contract and shared writer": `REUSE_EXISTING_MODULE`; exactly one canonical writer owns durable domain records, and no new event store, allocator database, outcome engine or position registry may be added. This increment adds exactly one caller of that writer and no new owner.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-11 "Paper isolation and research environment": paper management is deterministic, portfolio-aware and separately identified, with no funded write and no exchange order authority, `EVIDENCE_SHADOW` remains the production posture with `TARGET_PAPER` `BLOCKED` and `OPIP_PAPER_V2_MODE=off`. The seam is dormant under that posture: no runtime module imports it.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-06 "One canonical capital record at one committed version": the `CapitalSnapshot` that names the book is the only capital record this seam reads, and it is read as a declared value, never re-derived and never mutated.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-08 "Portfolio planning and rotation selection": the `PortfolioReservationPlan` is the plan this seam consumes; it is treated as a *plan*, and a plan that is not the plan of the named book is refused instead of reinterpreted.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`, row `R5-E` "Deterministic portfolio paper management with single canonical writer, bounded retries and fail-closed isolation" (contracts `SC-09`/`SC-11`, open input `OQ-08`, gate `G5`, authority `PAPER_RESEARCH`); clause row `CI-04` "Compare-and-commit one reservation plan against one capital snapshot".
- `docs/architecture/v1.5.0/ARCHITECTURE.md`: capital is committed at one atomic boundary; a producer cannot widen its own limits; an unresolved policy must never be treated as "no limits".
- `AGENTS.md`: preserve fixed risk sizing, minimum R:R, execution safeguards and paper isolation; prefer the smallest change that solves the proven problem; no second allocator, cash ledger, event database, writer or scheduler; automation has no trading authority.
- Dependency declarations in the increment that owns the authority: `docs/atdd/scope-contracts/ATDD-R5-E-canonical-reservations.md`, `DEFERRED DISCOVERIES` (the wiring slice) and `FROZEN BOUNDARIES` clause 10 (isolation from funded accounts and production data), which this increment inherits verbatim.
- Owner decision of this session: author the R5-E capital-admission-integration scope contract and its acceptance tests for the seam that already exists in the worktree, prove the seam's frozen attempt order and outcome semantics against the *real* canonical writer, declare any defect found in the seam rather than widening the increment, and make no commit, push, PR or deployment as part of authoring.

OWNERSHIP AUDIT (pre-implementation, evidence-based, performed before any code change):
- Finding 1 - the seam already exists and is dormant. `app/services/capital_admission_integration.py` is an untracked module in this branch's worktree (`git status --porcelain` reports `?? OHM-Trade-Agent-v1/app/services/capital_admission_integration.py` at base `4ef5e0e5`), and a search over `app/` finds no importer: the only occurrence of the name `admit_planned_reservation` outside its own definition is the definition itself. No runtime path, scheduler, Compose service or deployment unit calls it. This increment therefore adds acceptance evidence and one declared defect fix, not a new runtime path.
- Finding 2 - the authority is not duplicated. The seam contains no SQL, no `CREATE TABLE`, no `WriterIntent`, no allocator, no cash ledger and no event store: it imports the existing `WriterClient`, calls `admit_paper_opportunity` exactly once per attempt, and calls `get_paper_portfolio_state` for the freshness guard and the read back. The only identity it mints is the request identity the caller supplies; the canonical `reservation_id` and `paper_trade_id` come from the writer's own `admission_result_identities`.
- Finding 3 - the frozen attempt order is local first and canonical second, and it is the order the acceptance criteria pin: (1) identity and binding normalization; (2) type guards on plan and snapshot; (3) paper-ledger environment and canonical quote-currency refusal; (4) plan-freshness refusal through `assert_reservation_plan_current` against the snapshot's `portfolio_version`; (5) abstention when a declared constraint is canonical-unbound or when `resolve_capital_policy` does not resolve - before the client is touched at all; (6) selection of exactly one active reservation; (7) the freshness guard (the caller's `expected_portfolio_version` verbatim, otherwise exactly one canonical read); (8) one `validate_admission_request`; (9) exactly one `admit_paper_opportunity`; (10) read back only after a proven commit.
- Finding 4 - outcomes are one-way and none aliases another: `ADMITTED` (committed, read back), `ABSTAINED` (nothing was written because the frozen surfaces cannot prove the attempt safe: an unbound declared constraint, an unresolved policy, or an unreadable book), `REFUSED` (the canonical authority, the identity binding or the plan refused it, with the canonical tokens surfaced verbatim and never reinterpreted), `INDETERMINATE` (the canonical outcome could not be read back, so no assumption is made either way). A replay is reported as `ADMITTED` with `replay=True` and `canonical_status="DUPLICATE_OK"`, never as a second reservation.
- Finding 5 - first declared defect found during this audit and fixed inside this increment: the record's own field `reservation_committed` was never set to `True`, so a committed reservation was reported with `reservation_committed=False`, which a consumer would read as proof that no reservation exists. The seam is untracked and part of this uncommitted increment, so the fix is inside the increment's own surface and not a change to a frozen owner: it is one keyword argument on the committed branch (`reservation_committed=True` in the `CAPITAL_ADMISSION_ADMITTED` record). It changes no decision, no write, no read and no authority, and AC-001 pins it. A degraded read back still leaves the counted facts (`active_reservations`, `reserved_capital`) unset rather than downgrading a committed reservation into an unknown outcome.
- Finding 6 - second declared defect found by this increment's acceptance tests and fixed inside this increment: the seam classified every canonical status except `OK` as a refusal, so `DUPLICATE_OK` - the writer's own proof that this disposition identity already holds a committed reservation - was reported as `REFUSED` with a detail claiming the writer "left the reservation book unchanged", and the seam's own replay branch (`replay=True`, `canonical_status=DUPLICATE_OK`) was unreachable dead code. `RETRYABLE`, which both the writer (`INTEGRITY_CONFLICT`, `SQLITE_ERROR`) and the client-facing server (`WORKER_UNHEALTHY`) return for an admission whose outcome they explicitly do not settle, was misreported the same way. The fix is local and decision-preserving: only the writer's own refusal status (`REJECTED`) is a refusal, `OK` and `DUPLICATE_OK` are classified by disposition and are the only statuses that can prove a commit, and every other status fails closed into `INDETERMINATE` with the canonical tokens verbatim and nothing assumed. No write, no read, no limit and no authority changes; AC-002 pins the replay and AC-007 pins the unsettled case.
- Finding 7 - what is genuinely missing, and all that this increment adds: there was no acceptance proof that the seam behaves as its own frozen documentation claims, and no scope contract tracing that proof. The tests are hermetic (no clock, no network, no database beyond a `tmp_path` canonical store, no environment read) and drive the seam through the real `WriterClient` protocol over the in-process canonical server, so every canonical token asserted is the writer's own.
- Finding 8 - cross-increment ratification, owner-approved when asked: because this increment is the first approved consumer of the R5-C capital record and the first new caller of the canonical admission RPC outside the declared set, the module's mere presence trips three ratified guards owned by other increments - `tests/test_opip_f11_precutover_protection.py::test_ac_002_one_mutating_authority_and_read_only_evaluator` (`DECLARED_TARGET_MUTATOR_CALLERS`), `tests/test_opip_r5_c_capital_intelligence.py::test_ac_001_there_is_exactly_one_cash_book_in_the_application` (`DECLARED_SNAPSHOT_READERS`) and `tests/test_opip_r5_c_capital_intelligence.py::test_ac_011_no_runtime_root_or_application_module_consumes_the_record` (`DECLARED_RECORD_CONSUMERS`). Measured evidence: with the module moved aside those three guards pass and only the environment-dependent `test_release_scheduler_lock_verifier.py::test_ac_012` keeps failing (`bash` cannot fork in this local environment, process exit `0xC0000142`). The owner directed that the ratification happen *inside* this increment rather than in a later one, so each guard now names this seam explicitly and keeps its teeth: the F11 caller set must still match its declaration exactly, a ratified snapshot reader must declare neither `CapitalSnapshot` nor `CashPartitionLedger` and every other carrier remains a violation, and no runtime root may import the record's owner. AC-010 pins the ratification, and no guard was weakened, silenced or deleted.
- Audit decision: RATIFY the seam as delivered, with the two declared defect fixes of Findings 5 and 6 and the cross-increment ratifications of Finding 8, and prove it. Do NOT add a mode flag, a scheduler hook, a runtime caller, a new `WriterIntent` kind, a new event type, a new table, an in-seam retry loop, a numeric limit, or a second capital surface. Do NOT weaken another owner's guard: a ratification may name this seam explicitly and must keep the guard's equality or emptiness assertion. Do NOT widen the declared binding set: the conservative default (every R5-C measured constraint, including the four canonical-unbound ones) still abstains, and only an explicit narrower declaration by the caller can admit.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
one committed eligible paper decision context, one fresh canonical paper book, one `CapitalSnapshot` of that book and one `PortfolioReservationPlan` holding exactly one active planned reservation
WHEN:
the seam is asked to admit that reservation under one disposition identity and the explicit book version
THEN:
exactly one canonical reservation is committed for that disposition identity, the record reports `ADMITTED` with `replay=False`, `reservation_committed=True`, `reason=CAPITAL_CANONICAL_ACCEPTED` and `policy_binding_complete=True` for the declared canonical-bound constraint set, the frozen request is the planned reservation translated exactly once (identity, quote currency, direction, requested capital and requested reservation amount equal to the planned `reserved_capital`, expected version, and the limit pair resolved from the frozen capital policy rather than from the plan, the snapshot or the caller), the canonical `reservation_id` and `paper_trade_id` and the read-back `reserved_capital` and `active_reservations` are reported, `grants_execution_authority` is `False`, exactly one `admit_paper_opportunity` call and exactly one read back occurred, and the canonical ledger shows one `ACTIVE` reservation of the planned amount under that identity
AC-002:
GIVEN:
an already committed reservation of one disposition identity
WHEN:
the identical request is attempted again through the seam
THEN:
the re-attempt is reported `ADMITTED` with `replay=True` and `canonical_status=DUPLICATE_OK`, carries the same canonical `reservation_id`, `paper_trade_id` and disposition identity as the first attempt, and the canonical store is not written again (no new event, no new idempotency row, no sequence or watermark advance), so the book still holds exactly one reservation of the planned amount at the same portfolio version
AC-003:
GIVEN:
an already committed reservation of one disposition identity
WHEN:
a mutated attempt reuses that identical disposition identity with a different requested reservation amount
THEN:
the writer's own conflict is surfaced verbatim as `status=REFUSED`, `reason=CAPITAL_CANONICAL_REFUSAL`, `canonical_status=REJECTED` and `canonical_error_code=IDEMPOTENCY_PAYLOAD_CONFLICT`, no canonical identity is reported (`reservation_id` and `paper_trade_id` are `None` and `reservation_committed` is `False`), the refused mutation writes nothing to the canonical store, and the committed reservation is unchanged in identity, state and amount
AC-004:
GIVEN:
a fresh canonical paper book at portfolio version `0` and one eligible planned reservation
WHEN:
the seam is asked to admit it against an expected portfolio version the book has already moved past
THEN:
the refusal is atomic and canonical: `status=REFUSED` with `canonical_error_code=STALE_PORTFOLIO_VERSION`, the canonical observed version surfaced, no canonical identity reported and no committed-reservation claim, and the canonical book still holds zero reservations at version `0` with zero reserved capital, so no partial reservation, no released reservation and no version advance exist
AC-005:
GIVEN:
one eligible planned reservation whose requested amount is below, exactly equal to and one cent above the equity limit of the frozen capital policy, and a book that already holds the policy's maximum number of active reservations
WHEN:
the seam attempts each of those admissions
THEN:
the writer's own decisions are surfaced verbatim and without reinterpretation (admitted at and below the frozen equity limit, `canonical_disposition=CAPITAL_REJECTED` for the attempt above it and `canonical_disposition=CAPACITY_REJECTED` for the attempt above the position count, each refusal reported `REFUSED` with `reason=CAPITAL_CANONICAL_REFUSAL` and no canonical identity), the writer's own `reason_code` recorded in its disposition evidence names the limit that decided it, the seam invents no limit of its own and takes none from the plan, the snapshot or the caller, and every refused attempt leaves the reservation book and its portfolio version unchanged
AC-006:
GIVEN:
a client that records every call and answers nothing, one eligible planned reservation, and four attempts the frozen local surfaces cannot prove safe: the conservative default binding that includes the canonical-unbound constraints, an unresolvable capital policy version, a plan that is not the plan of the named book, and a snapshot of another environment or of a quote currency outside the canonical paper quote currencies
WHEN:
the seam attempts each of them
THEN:
each unbound-constraint and unresolved-policy attempt is reported `ABSTAINED` with `reason=CAPITAL_CONSTRAINT_UNBOUND` or `reason=CAPITAL_POLICY_VERSION_UNRESOLVED`, each environment, currency and plan attempt is reported `REFUSED` with `reason=CAPITAL_ENVIRONMENT_NOT_PAPER_LEDGER`, `reason=CAPITAL_QUOTE_CURRENCY_UNSUPPORTED` or `reason=CAPITAL_PLAN_PORTFOLIO_VERSION_STALE`, every record declares no canonical identity, no canonical status and no expected version, an unresolvable policy is never read as "no limits" and an unbound constraint is never read as unlimited, and the recording client was never called at all (zero reads and zero writes) while the canonical store is byte-identical: an unprovable attempt abstains or refuses before it touches any canonical surface
AC-007:
GIVEN:
a real canonical writer whose acknowledgement is lost after the commit, so the canonical outcome cannot be read back
WHEN:
the seam attempts the reservation and then re-attempts the same disposition identity, first by reusing exactly the recorded identity inputs and then by substituting a fresh portfolio version
THEN:
the first attempt is reported `INDETERMINATE` with `reason=CAPITAL_CANONICAL_OUTCOME_UNKNOWN`, no canonical status, no canonical identity and no committed-reservation claim while the exact frozen identity a retry must reuse is echoed, the canonical book nevertheless holds exactly one reservation because the writer committed before the acknowledgement was lost, the honest re-attempt that reuses the recorded version and instant is reported `ADMITTED` with `replay=True` and the same canonical `reservation_id` while still holding exactly one reservation, and the substituted fresh version is refused by the writer with `IDEMPOTENCY_PAYLOAD_CONFLICT` and reserves nothing, so a re-attempt can never double-reserve, while an acknowledgement the writer does not settle (its own `RETRYABLE`, for example `WORKER_UNHEALTHY` or `SQLITE_ERROR`) is reported `INDETERMINATE` with the canonical tokens verbatim and nothing assumed rather than as a refusal or an admission, and the commit the writer already made is never described as "left unchanged" by that path
AC-008:
GIVEN:
an admitted reservation and a canonical store that is closed and reopened
WHEN:
the process boundary is crossed and the same disposition identity is re-attempted against the reopened store
THEN:
the commit survives the restart, the reopened canonical book still reports the same reservation identity and amount at the same portfolio version, the re-attempt is reported `ADMITTED` with `replay=True` and the same canonical `reservation_id`, and no second reservation exists, so the reservation is durable canonical evidence and not process-scoped state
AC-009:
GIVEN:
a reservation committed through the seam whose canonical trade has no committed terminal reconciliation evidence
WHEN:
the canonical release projection, the reservation ledger and the active-exposure read are inspected
THEN:
the reservation is still `ACTIVE` with no release basis, the read-back facts the seam reported still count it once, the release derivation is empty because only committed terminal reconciliation evidence releases capital, and no partial fill, attempt, trigger or expiry can free the reserved capital
AC-010:
GIVEN:
the seam module, the canonical schema, the increment's own scope contract and pointer, and the release-profile contract with the Compose literals
WHEN:
the increment's surfaces are inspected
THEN:
the seam adds no table, no event type and no writer intent kind, reads no environment variable and no secret and imports no exchange or order surface, no runtime module imports it so it stays dormant while `OPIP_PAPER_V2_MODE=off`, every record refuses to grant execution authority and the outcome vocabulary is exactly `ADMITTED`, `ABSTAINED`, `REFUSED` and `INDETERMINATE`, the increment's own scope contract is self-consistent and pins no global active-increment pointer, the ratified consumer and caller lists owned by the R5-C capital-record isolation guards and the F11 target-domain mutating-authority guard name this seam explicitly (one declared target-mutator caller with exactly one canonical admission call site and no protected-action call, one declared snapshot reader that declares no book of its own, one declared record consumer) rather than being weakened or silenced, and the `EVIDENCE_SHADOW` profile, the blocked `TARGET_PAPER` profile and the Compose `OPIP_PAPER_V2_MODE=off` posture are unchanged with no new `OPIP_R5*` mode key

EXPLICITLY OUT OF SCOPE:
- No second admission, reservation, release or capital authority. The seam never writes SQL, never opens a transaction of its own, never mints a canonical identity, never releases or adjusts a reservation, and never adds a `WriterIntent` kind, an event type, a table, a store, a queue, a scheduler, an allocator, a cash ledger or a retry loop. Release stays exactly where the reservation-authority increment left it: committed terminal reconciliation evidence read by the existing writer.
- No runtime activation and no wiring. The seam stays dormant: no runtime module, Compose service, scheduler job, Telegram path, deployment unit or environment variable is added or switched, no mode key is introduced, and `OPIP_PAPER_V2_MODE` stays `off`. `TARGET_PAPER` stays `BLOCKED`.
- No trading, order, exchange or funded authority. Nothing is placed, modified, cancelled or confirmed; no exchange or order client, credential, balance or private endpoint is imported, read or reachable; `grants_execution_authority` is `False` and the record constructor refuses to build one that grants authority.
- No new limit, no invented number and no policy ratification. `OQ-05` (turnover semantics) and `OQ-06` (sleeve caps) stay owner inputs, the four canonical-unbound constraints stay declared unbound, and the seam takes its equity and position limits only from the frozen writer policy.
- No change to the canonical store, the schema, the reservation lifecycle projection, the existing readers or any frozen contract. Traceability, retention, migration, replay and read semantics are untouched because this increment persists nothing of its own.
- No change to the plan, the snapshot, the decision context or any upstream artifact: they are read as declared values, and a stale plan is refused rather than adjusted.
- No commit, push, pull request, merge, promotion or deployment is part of this increment's authoring step.

FROZEN BOUNDARIES:
1. One writer, one write call. The only write is exactly one `WriterClient.admit_paper_opportunity` call per attempt, carrying a `PaperAdmissionRequest` that passed `validate_admission_request`; the seam holds no retry, no fallback and no local decision. There is no second capital path, and no code outside the seam is invoked for capital.
2. Local-first attempt order. Identity and binding normalization, type guards, environment and currency refusal, plan-freshness refusal, unbound-constraint abstention and unresolved-policy abstention all happen before any client call, so an unprovable attempt cannot read or write the canonical store.
3. The freshness guard is exactly one canonical read or one caller-supplied version, never both and never a guess. A caller-supplied version is used verbatim because the writer proves idempotency by comparing the whole frozen payload; a version the book has moved past is refused by the writer, atomically, as `STALE_PORTFOLIO_VERSION`.
4. Outcome vocabulary is closed and one-way: `ADMITTED`, `ABSTAINED`, `REFUSED`, `INDETERMINATE`. A replay is `ADMITTED` with `replay=True` and `canonical_status=DUPLICATE_OK`. Only the writer's own refusal status is a refusal: the canonical tokens are surfaced verbatim, an out-of-vocabulary admission disposition becomes `INDETERMINATE` rather than an assumed outcome, and an acknowledgement the writer does not settle (`RETRYABLE`, for example `WORKER_UNHEALTHY`, `INTEGRITY_CONFLICT` or `SQLITE_ERROR`) is `INDETERMINATE` because it is not proof that nothing was committed.
5. Nothing is retried inside the seam. A transport failure or a lost acknowledgement is `INDETERMINATE` with the exact identity a re-attempt must reuse echoed back; the writer's own idempotency then makes the honest re-attempt a replay instead of a second reservation, and refuses a mutated one.
6. Fail-closed abstention. An unbound declared constraint, an unresolvable capital policy version or an unreadable canonical book abstains and writes nothing. An unresolved policy is never "no limits"; an unbound constraint is never unlimited; an unreadable book is never empty.
7. The declared binding set can only be narrowed by an explicit caller declaration, never widened: the default is every R5-C measured constraint, of which four are canonical-unbound, so the default outcome is abstention.
8. Read back after a commit is supplementary, not authority. A degraded read back leaves `active_reservations` and `reserved_capital` unset and never downgrades a proven commit; a refused or abstained attempt is never read back at all.
9. No authority, ever. `grants_execution_authority` is `False`, the dataclass refuses to be constructed with it `True`, the record cannot reserve, release, widen or authorize anything, and the module reads no environment variable, no secret and no clock.
10. Isolation from funded accounts and production data. Only the canonical paper admission lineage can create the reservation; the seam reads committed canonical paper evidence and a caller-supplied quote currency and nothing else. No funded account, credential, exchange endpoint, order surface or production analytics store is reachable, and no database, service or droplet is added.
11. Determinism. The caller supplies the disposition instant and identity; the seam reads no wall clock, so the same inputs produce the same frozen payload and the same canonical outcome.
12. Additive and reversible. The increment adds acceptance tests, one documentation contract, one pointer update, two explicit cross-increment ratifications that each keep their guard's assertion strength, and two declared defect fixes inside the seam's own uncommitted module; it changes no frozen behavior, no persisted format, no limit and no reader, so reverting the increment reverts the proof rather than a production capability.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_001_a_planned_reservation_becomes_exactly_one_canonical_reservation
AC-001 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_001_each_candidate_keeps_its_own_reservation_identity
AC-002 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_002_an_exact_re_attempt_replays_as_one_reservation
AC-002 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_002_a_replay_writes_nothing_new_to_the_canonical_store
AC-003 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_003_a_mutated_re_attempt_under_one_identity_is_refused
AC-003 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_003_the_conflicting_mutation_leaves_the_reserved_book_unchanged
AC-004 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_004_a_stale_expected_version_is_refused_atomically
AC-005 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_005_a_capital_limit_refusal_surfaces_only_the_canonical_tokens
AC-005 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_005_a_capacity_limit_refusal_surfaces_only_the_canonical_tokens
AC-005 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_005_the_boundary_is_the_writers_frozen_policy
AC-006 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_006_an_unbound_declared_constraint_abstains_before_touching_the_client
AC-006 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_006_an_unresolved_policy_abstains_before_reading_or_writing
AC-006 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_006_local_refusals_write_nothing_and_touch_no_client
AC-007 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_007_a_lost_acknowledgement_is_indeterminate_and_reserves_once
AC-007 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_007_a_retry_must_reuse_the_recorded_version_not_a_fresh_one
AC-007 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_007_an_unsettled_acknowledgement_is_never_reported_as_a_refusal
AC-008 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_008_a_restart_replays_the_same_reservation_once
AC-009 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_009_a_reservation_without_terminal_evidence_stays_active
AC-010 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_010_the_seam_is_dormant_and_grants_no_authority
AC-010 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_010_the_increment_contract_and_pointer_are_self_consistent
AC-010 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_010_the_release_profile_and_posture_are_unchanged
AC-010 -> tests/test_opip_r5_e_capital_admission_integration.py::test_ac_010_the_ratified_consumer_and_caller_lists_name_this_seam

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-002 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-003 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-004 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-005 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-006 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-007 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-008 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-009 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py
AC-010 -> OHM-Trade-Agent-v1/app/services/capital_admission_integration.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-E-capital-admission-integration.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_capital_admission_integration.py

DEFERRED DISCOVERIES:
- Nothing in the repository calls the seam yet, so the seam has no production consumption: wiring it into the R5-C capital-intelligence or R5-D selector runtime is the next R5-E integration step, and it needs its own increment, its own dormant-mode proof, its own release-profile decision and an owner-approved activation path. Until then the seam stays dormant under `OPIP_PAPER_V2_MODE=off`.
- The reservation identity of the R5-D `PortfolioReservationPlan` and the canonical reservation identity are still two different identities: the seam reports both and equates neither. Reconciling them, and any plan expiry or supersession policy for a plan, remain open.
- `OQ-05` (turnover semantics) and `OQ-06` (sleeve caps) remain owner inputs, so the four canonical-unbound constraints stay unbound and a caller that can only be bound by them still abstains. Ratifying those numbers is a separate owner decision and is not attempted here.
- Legacy paper-engine coexistence (`OQ-08`) is unchanged by this increment and stays open.
- A caller that supplies an explicit `expected_portfolio_version` is trusted to be honest about the version it is re-attempting: the seam echoes the caller's version into the record rather than re-deriving it. Detecting a caller that supplies a *wrong but not stale* version is not attempted, because the writer's whole-payload idempotency comparison already refuses any re-attempt that mutates a recorded identity, and the version itself is compared by the writer against the live book.
- The seam reads the canonical book back only when the writer acknowledged a commit, and it never re-reads to reconcile an `INDETERMINATE` outcome, because that would be an in-seam retry and a second source of truth. An operator-driven re-attempt carrying the recorded identity is the only supported recovery, and it is not automated here.
- The candidate-panel and forecast-identity binding raised as a `P2` finding on PR #363 belongs to the R5-E/R5-F forecast-identity slices and is untouched here.
- Cross-increment ratification, resolved by owner decision inside this increment (details and measured evidence in Finding 8): landing this module trips three guards owned by other increments, because the increment is the first approved consumer of the R5-C capital record and the first new caller of the canonical admission RPC outside the declared set - (i) `tests/test_opip_f11_precutover_protection.py::test_ac_002_one_mutating_authority_and_read_only_evaluator` now names this seam in `DECLARED_TARGET_MUTATOR_CALLERS` (the seam calls `admit_paper_opportunity` exactly once, which is exactly what this increment is for), (ii) `tests/test_opip_r5_c_capital_intelligence.py::test_ac_001_there_is_exactly_one_cash_book_in_the_application` now names it in `DECLARED_SNAPSHOT_READERS` (the seam must name the snapshot because the capital snapshot is its declared input, while still declaring no book of its own), and (iii) `tests/test_opip_r5_c_capital_intelligence.py::test_ac_011_no_runtime_root_or_application_module_consumes_the_record` now names it in `DECLARED_RECORD_CONSUMERS` (which is the wiring the R5-C increment itself deferred to "a later R5-E integration slice"). Each guard keeps its assertion strength; none was weakened, silenced or deleted.
- Environment observation from the same runs, not caused by this increment: `tests/test_release_scheduler_lock_verifier.py::test_ac_012_verifier_accepts_the_real_producers_and_fails_on_weakening` fails in this local environment because `bash` cannot fork (process exit `0xC0000142`), which is an environment limitation rather than a repository defect.

UNAPPROVED SCOPE CHANGES:
NONE






