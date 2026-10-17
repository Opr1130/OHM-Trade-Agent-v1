INCREMENT:
ATDD-R5-E-portfolio-lifecycle-closure

OWNER-APPROVED INTENT:
Close the R5-E F8 research-paper portfolio lifecycle to the extent the existing platform already supports it, and record precisely what remains unproven, unimplemented or blocked on an owner policy input. This increment is a closure increment, not a new capability: it adds no engine, no ledger, no allocator, no scheduler, no writer, no event type, no release profile and no trading authority. It does two things and nothing else. First, it closes one fail-closed gap found while reviewing the delivered seam `ATDD-R5-E-research-paper-execution` (`app/services/paper_v2_capital_admission_execution.py`): the seam compared the committed admission's quote currency and requested reservation amount against the execution intent only when the committed projection actually declared them, so a committed admission that declared neither was dispatched as if it described any intent. An undeclared committed value is now refused before dispatch under the existing closed reason `ADMISSION_INTENT_DIVERGENCE`; the canonical writer's authority, the seam's reason vocabulary, the refusal order and the producer are unchanged. Second, it proves the lifecycle behaviour the increment's own requirements demand, through integration tests over the real canonical writer, the real R5-C/R5-D capital-admission authority (`app/services/capital_admission_integration.admit_planned_reservation`), the real F8 producer (`app/services/paper_v2_execution.run_paper_v2_opportunity`) and the real protection runtime (`app/services/paper_v2_protection_runtime.run_protection_sweep`): one planned reservation becomes one committed canonical admission, the seam's dispatch executes the entry, the protection runtime takes the approved exits, residual exposure is retained and counted once, the reservation is released exactly once, retained cash reconciles, and a restart replays without new evidence. The increment reuses these components exactly as the frozen boundaries require and prefers proving existing behaviour over writing new code. Anything the platform does not yet implement - above all rotation from committed proceeds and every numeric rotation policy - is recorded as `NOT_IMPLEMENTED`, `NOT_INTEGRATED` or `BLOCKED_ON_OWNER_POLICY` in this contract rather than invented, and nothing here authorizes activation, runtime wiring, a second engine or a release-profile change. The owner directive that governs the R5-E line is obeyed unchanged: reuse the existing reservation and execution authority, do not create a new `WriterIntent` kind, and fail closed on missing `OQ-05`/`OQ-06`/`OQ-08` owner policy instead of inventing a numerical rule.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 9 "Autonomous paper portfolio management": entries, holds, partial profit taking, approved trailing, price/thesis/time exits, rotations and retained cash are the required R5-E portfolio actions; executable side-of-spread prices, depth, latency, partial/no fills, fees, cancellations and stop gaps are the required execution realism; recovery must replay committed intents and fills idempotently, reconcile reserves and expose incomplete evidence; unsupported actions must explicitly abstain.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `PM-01` and `PM-02`: R5-E is built and evaluated in a separately identified offline/research paper environment on the existing execution components, adds no release profile and cannot turn `OPIP_PAPER_V2_MODE` on in production; `TARGET_PAPER` remains a later owner-gated cutover. This increment adds no profile and no activation.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LC-02`, `LC-03` and section 17 `AC-07`, `AC-09`, `AC-10`: profit targets, trailing and time invalidation are predeclared deterministic policies; the canonical writer acknowledges only committed transitions, crash recovery resumes from committed state and stale commands are rejected; partial fills, fees and cancellations must reconcile exactly with occupancy counted once; a partial rotation exit funds only committed proceeds and retains residual exposure; recovery is idempotent with protection independent.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-09` "Rotation group record and switching comparison": the rotation record's keys and invariants are `FROZEN` while the minimum economic advantage, uncertainty treatment, cooldown, requalification rule and turnover budget stay `OQ-05`. This increment therefore proves what exists (the frozen `RotationComparison`/`RotationSwitchAssessment` contracts and their fail-closed abstention without ratified policy) and declares the executable rotation lifecycle `NOT_IMPLEMENTED`; it invents no advantage or turnover number.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-11` "Paper isolation and research environment": research paper management runs in a separately identified offline/research environment on the existing engine, with no funded write and no exchange order authority; paper cannot generate its own proof and `EVIDENCE_SHADOW` remains the production posture with `TARGET_PAPER` `BLOCKED`.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-06`/`SC-07`: one canonical capital record per committed version, with reservation compared and committed atomically by the canonical writer; this increment reads that record and releases nothing of its own.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-12`: `REUSE_EXISTING_MODULE` for the canonical writer; no second writer, event store, position registry or outcome engine is added here.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` row `R5-E` (contracts `SC-09`/`SC-11`, open input `OQ-08`, gate `G5`, authority `PAPER_RESEARCH`), row `R5-D` (`SC-08`/`SC-09`, `OQ-05`), row `R5-C` (`SC-06`/`SC-07`, `OQ-06`) and the declared dependency edges `R5-E`<-`R5-D`, `R5-F`<-`R5-A`,`R5-E`.
- `docs/atdd/scope-contracts/ATDD-R5-E-research-paper-execution.md`: the parent increment this contract closes. Its `AC-004` guarantee ("a committed admission can never be spent on an intent it does not describe") is strengthened by `AC-001` here, and its `DEFERRED DISCOVERIES` are the input to the closure audit below.
- `AGENTS.md`: fail-closed behaviour, fixed risk sizing, minimum R:R, execution safeguards and paper isolation are preserved; external AI cannot override deterministic gates; production stores are not mutated; one scheduler, store and control plane.
CLOSURE AUDIT:
Requirement-to-implementation status for the R5-E capabilities this increment audits, using four distinct labels. `CODE_COMPLETE` means the code exists and satisfies its frozen contract. `INTEGRATED` means an integration test drives it through real components. `RUNTIME_ACTIVE` means a runtime root or scheduler calls it in a running posture; nothing in this increment is `RUNTIME_ACTIVE` and the seam remains dormant. `PROVEN` means an executing test demonstrates the behaviour end to end.
- `SC-08`/`SC-09` portfolio plan and reservation plan (R5-D): `CODE_COMPLETE` and `INTEGRATED` in the parent `ATDD-R5-D-portfolio-planning` increment (`app/opip/contracts/portfolio.py`: `PortfolioReservationPlan`, `PortfolioPlan`, `RotationComparison`, `RotationSwitchAssessment`, `unratified_plan_constraints`). `PROVEN` for plan construction, deterministic alternative ranking and fail-closed abstention without ratified policy. `NOT_IMPLEMENTED`: no executable rotation-group lifecycle (PROPOSED, EXIT_PENDING, PROCEEDS_COMMITTED, REQUALIFY, ENTER or HOLD_CASH) exists as a runtime obligation with stable both-leg identifiers; the record contract is frozen but no producer advances it. `BLOCKED_ON_OWNER_POLICY`: `OQ-05` minimum advantage, uncertainty treatment, cooldown, requalification rule and turnover budget.
- `SC-06`/`SC-07` capital snapshot and atomic reservation (R5-C and the two R5-E capital increments): `CODE_COMPLETE`, `INTEGRATED` and `PROVEN` in `ATDD-R5-E-canonical-reservations` and `ATDD-R5-E-capital-admission-integration`; one reservation per committed version is compared and committed atomically, replays idempotently and refuses a stale version.
- `SC-11` plus the section 9 admission-to-execution seam (R5-E, parent increment): `CODE_COMPLETE` and `INTEGRATED`. The seam consumes committed admission evidence and dispatches the single producer. `PROVEN` for the refusal paths, the single dispatch, replay and zero authority. The audit finding of this increment (`AC-001`) is that the intent comparison was fail-open when the committed projection declared neither value; that is fixed here and `PROVEN` by `AC-001`'s tests. Still `NOT_IMPLEMENTED`: the seam is called by no runtime root, has no retry or recovery automation, and has no supersession or expiry policy for a committed admission.
- Section 9 entries, holds, approved exits, residual exposure, retained cash, capital reconciliation and restart (F8 producer plus F11 protection runtime): `CODE_COMPLETE` and `INTEGRATED` in the BC3 and protection-runtime suites (`run_paper_v2_opportunity`, `run_protection_sweep`). `PROVEN` for entry fills at the executable side of the spread, deterministic protection plans, stop, target and time exits, immutable residual re-arming after a partial target exit, insufficient-depth abstention without a fabricated fill, lost-acknowledgement idempotency, flat reconciliation releasing the reservation, and restart behaviour. This increment newly `PROVES` the connected chain plan -> committed admission -> seam dispatch -> entry -> approved exit -> release, which no earlier increment proved end to end.
- Section 9 execution realism (partial entry fills, latency, cancellations, stop gaps): `NOT_IMPLEMENTED` for partial entry fills - the engine deliberately requires top-of-book depth to support the whole approved entry and abstains otherwise rather than fabricating a partial execution. Partial exit fills are `CODE_COMPLETE`, `INTEGRATED` and `PROVEN`. Latency, cancellation and stop-gap modelling remains `NOT_IMPLEMENTED`, and no latency, slippage or gap value is invented here.
- Rotation from committed proceeds: `NOT_IMPLEMENTED` and `BLOCKED_ON_OWNER_POLICY` (`OQ-05`); no code path in this increment may release capital to fund a second leg.
- R5-F learning, F11 independent protection, R6 committee, F10 dashboard and R7 retirement: untouched here and preserved in their own increments; `R5-F` remains the next learning increment and its expectations are unchanged.
- Conclusion: one dispatch seam is not an autonomous portfolio lifecycle. `PROVEN` here is a deterministic, dormant, zero-authority research-paper lifecycle for one opportunity - admitted capital, executed entry, approved exit, released reservation, reconciled cash, restart. `NOT_IMPLEMENTED` or `BLOCKED`: rotation, partial entry fills, latency realism, automated recovery, runtime scheduling, activation and every target-profile transition.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
one committed canonical admission of one disposition identity whose committed projection declares no quote currency, and separately one whose committed projection declares no requested reservation amount - the shape a transport produces when it carries the writer's projection without that field (a writer revision that does not emit it, or a non-conforming store), including a real committed admission read back through such a transport
WHEN:
the seam dispatches the matching opportunity
THEN:
each dispatch is `BLOCKED` with the existing reason `ADMISSION_INTENT_DIVERGENCE` before any dispatch happens, the absent committed value and the opportunity's own value are both stated in the detail, the opportunity's own value is never substituted for the missing committed evidence, `admission_consumed` is false, no execution is reported, no market read is attempted and the canonical store gains no event, so an unprovable committed admission is refused rather than spent
AC-002:
GIVEN:
one real canonical book, the deterministic prerequisites of one research-paper opportunity (registered instrument version, durable decision snapshot and eligible paper decision context) and one planned reservation admitted through the existing R5-C/R5-D authority `admit_planned_reservation` under the opportunity's own disposition identity
WHEN:
the seam dispatches that opportunity against a capable public book
THEN:
the outcome is `EXECUTED` with the producer's own status verbatim, exactly one entry order intent, quote evidence, execution attempt, fill and protection plan are committed for that trade, the reported identities are the producer's own (`paper_trade_id`, `reservation_id`, `entry_order_intent_id`, `fill_id`), the committed admission and reservation are unchanged by the dispatch (one admission request, one disposition, one active reservation and the same reserved capital), and the trade's canonical ancestry is the committed decision context rather than anything the seam invented
AC-003:
GIVEN:
that committed entry with its committed protection plan and the real protection runtime
WHEN:
the existing protection sweep takes an approved stop exit, and separately takes an approved partial target exit followed by the residual's approved stop exit
THEN:
the exits are the engine's own committed fills at executable prices with their own costs, a partial exit retains the residual exposure and re-protects it under an immutable residual plan with occupancy counted once, the flat reconciliation releases the reservation exactly once, reserved capital returns to free cash only after the committed exit fills, and the resulting canonical positions, reservations and costs reconcile under the writer's own projections
AC-004:
GIVEN:
the fully committed trade after the seam's dispatch and a canonical store that is closed and reopened
WHEN:
the seam dispatches the same opportunity again and the post-restart dispatch is inspected
THEN:
the outcome and every reported identity are identical to the first dispatch, the admission is reported as consumed, the canonical event counts, the portfolio projection and every committed stage of the trade are unchanged by the replay, the canonical store's schema version, history epoch and sequence are unchanged, and no canonical evidence is rewritten, deleted or backfilled
AC-005:
GIVEN:
a public book whose top-of-book depth cannot support the whole approved entry size, and separately a producer failure during dispatch
WHEN:
the seam dispatches against each of them
THEN:
no partial entry fill and no fill is fabricated, no success is reported for an unexecuted entry, the outcome carries the producer's own status or `PRODUCER_FAILED` with no execution claimed, the canonical store is left exactly as the producer's own transaction left it with no seam-authored repair or release, and the reservation is not silently released by the seam
AC-006:
GIVEN:
the frozen rotation comparison and switch assessment contracts and the frozen unratified plan constraints, with `OQ-05` still an open owner input
WHEN:
a rotation switch is assessed and the seam itself is inspected
THEN:
the assessment is deterministic and refuses or abstains rather than inventing an advantage, cooldown, requalification rule or turnover budget, the seam exposes no rotation, release, re-reservation or second-leg surface, and rotation from committed proceeds is recorded as `NOT_IMPLEMENTED` and `BLOCKED_ON_OWNER_POLICY` rather than demonstrated or implied
AC-007:
GIVEN:
this increment's own contract and pointer, the seam module, the release posture and the parent increment
WHEN:
they are inspected
THEN:
`EVIDENCE_SHADOW` remains the production posture with `TARGET_PAPER` blocked and the Compose pin unchanged at `OPIP_PAPER_V2_MODE: "off"` with no new mode key, the seam grants no execution authority and no module under `app/` imports it, this contract is self-consistent with exactly the tests that cite it, and the parent increment's contract, tests and pointer-based scope control remain satisfied

EXPLICITLY OUT OF SCOPE:
- Activating Paper v2, adding a release profile, adding a mode key, wiring the seam into any runtime root, scheduler or router, and any `TARGET_PAPER` transition.
- Any trading, order or exchange authority; any funded or live account path; any Kraken private or order endpoint.
- Inventing `OQ-05` (rotation advantage, cooldown, requalification, turnover), `OQ-06` (sleeve caps), `OQ-08` (legacy paper coexistence) or any other numeric policy value.
- A second engine, profitability engine, allocation ledger, position registry, event store, outcome engine, database, scheduler or deployment control plane; any change to the canonical writer's records, schema or decision rules.
- Implementing the executable rotation-group lifecycle, automated retry or recovery, cross-identity reconciliation, supersession or expiry of committed admissions, partial entry fills, and latency or cancellation realism.
- Editing `docs/atdd/scope-contracts/ATDD-R5-E-research-paper-execution.md` or its tests to make this increment fit; the parent increment is closed, not rewritten.

FROZEN BOUNDARIES:
- One capital authority: `WriterClient.admit_paper_opportunity`, reached through the existing R5-C/R5-D `admit_planned_reservation`. This increment adds no `WriterIntent` kind, no second reservation path and no release path.
- One execution producer: `app.services.paper_v2_execution.run_paper_v2_opportunity`. One protection runtime: `app.services.paper_v2_protection_runtime.run_protection_sweep`. The seam dispatches the first and never the second.
- One canonical writer: `app/opip/canonical/writer.py`. The canonical records, their contracts, the schema version, the sequence and the writer's decisions are unchanged.
- The seam's refusal vocabulary stays closed and unchanged: `UNUSABLE_CANONICAL_STATE`, `NO_COMMITTED_ADMISSION`, `UNCOMMITTED_RESERVATION`, `ADMISSION_INTENT_DIVERGENCE` and `PRODUCER_FAILED`. No new reason code is added, and `ADMISSION_INTENT_DIVERGENCE` keeps its meaning: the committed admission does not describe the execution intent.
- `grants_execution_authority` stays hard-coded `False` in every record the seam returns.
- No environment read, secret, clock, economic knob, fee, slippage, spread or latency value is added to the seam.
- Production posture: `EVIDENCE_SHADOW` `ACTIVE`, `TARGET_PAPER` `BLOCKED`, Compose pinned `OPIP_PAPER_V2_MODE: "off"`, no `OPIP_R5*` key.
- Additive and reversible: one seam behaviour fix, one new test module, one new scope contract and one pointer update. Reverting this increment reverts a proof and one refusal, not a production capability.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_001_a_projection_without_a_quote_currency_is_refused_before_dispatch
AC-001 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_001_a_projection_without_a_reservation_amount_is_refused_before_dispatch
AC-001 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_001_a_real_committed_admission_without_a_declared_amount_is_refused
AC-001 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_001_a_real_committed_admission_without_a_declared_currency_is_refused
AC-002 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_002_a_planned_reservation_is_executed_by_the_seam_as_one_entry
AC-002 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_002_the_dispatch_keeps_the_committed_admission_and_reservation_intact
AC-003 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_003_an_approved_stop_exit_releases_the_reservation_and_reconciles_the_book
AC-003 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_003_a_partial_target_exit_retains_the_residual_exposure_once
AC-004 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_004_a_restart_replay_reports_the_same_outcome_without_new_evidence
AC-005 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_005_a_book_that_cannot_support_the_entry_fabricates_no_fill
AC-005 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_005_a_producer_failure_is_reported_without_a_seam_authored_release
AC-006 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_006_an_unratified_rotation_policy_refuses_deterministically
AC-006 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_006_the_seam_exposes_no_rotation_release_or_second_leg_surface
AC-007 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_007_the_contract_pointer_and_parent_increment_are_self_consistent
AC-007 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_007_the_posture_and_release_surface_are_unchanged
AC-007 -> tests/test_opip_r5_e_portfolio_lifecycle_closure.py::test_ac_007_the_seam_grants_no_authority_and_no_application_module_imports_it

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py
AC-005 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py
AC-007 -> OHM-Trade-Agent-v1/app/services/paper_v2_capital_admission_execution.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-E-portfolio-lifecycle-closure.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_portfolio_lifecycle_closure.py

DEFERRED DISCOVERIES:
- Reachability of `AC-001`: through the current canonical writer's own write path an `ADMITTED` disposition always carries its quote currency and requested reservation amount, because `admit_paper_opportunity` validates the request against the disposition contract (`validate_admission_request`) and commits request and disposition in one transaction. The fail-open path is therefore reachable at the seam's input boundary rather than by writing a non-conforming admission through the supported API: `WriterClient.get_paper_v2_execution_state` deserializes the projection with `PaperV2ExecutionState.from_dict`, whose two fields are optional, and the writer builds the projection's `requested_reservation_amount` from a raw, unvalidated admission-request payload row. A writer revision that does not emit the field, a socket client talking to such a writer, or a non-conforming store therefore yields a projection that declares nothing, and before this increment the seam dispatched it. The fix refuses it; the writer's authority and the refusal vocabulary are untouched.
- Rotation from committed proceeds needs the executable rotation-group lifecycle and the `OQ-05` policy values (minimum advantage, uncertainty treatment, cooldown, requalification rule, turnover budget). Both are absent, so this increment proves the frozen contracts' fail-closed abstention and declares the lifecycle `NOT_IMPLEMENTED` instead of inventing the pipeline.
- Partial entry fills are not supported by design: the engine refuses an entry whose top-of-book depth cannot support the whole approved size (`no approved depth or partial-fill model exists`) rather than fabricating a partial execution. Partial exit fills, residual re-arming and flat reconciliation are supported and are proven by `AC-003`.
- `AC-002` commits the instrument registration, durable decision snapshot and eligible paper decision context with the same public canonical helpers the execution path uses, because the canonical admission requires committed ancestry; the admission itself is committed only through the R5-C/R5-D authority `admit_planned_reservation`, so no test invents a disposition, a reservation or a capital decision.
- The seam remains dormant and unscheduled: no runtime root calls it, there is no automated recovery from a `FAILED` producer, there is no supersession or expiry policy for a committed admission, and there is no cross-identity reconciliation. These stay deferred from the parent increment and unchanged.
- Integration contract discovered while closing the chain: the canonical writer refuses an `ENTRY` order intent whose `decision_context_id` does not match the committed reservation's, so a planned reservation admitted by the R5-C/R5-D authority is only executable if that authority was given the *execution* decision context (the schema-v2 context the F8 path derives from the opportunity). `admit_planned_reservation` already accepts `decision_context_id` as an input, so no code changed; what does not exist is the runtime wiring that would hand the execution context to the admission authority. `AC-002` composes the two authorities on the shared context explicitly and says so, and no test invents a second context, disposition, reservation or capital decision.
- `OQ-05`, `OQ-06` and `OQ-08` remain owner inputs. `OQ-08` also governs the legacy paper-engine coexistence window and the R5-F handoff, both untouched here.
- Latency, cancellation and stop-gap modelling is `NOT_IMPLEMENTED`; no latency, spread, slippage or gap value is added by this increment.

UNAPPROVED SCOPE CHANGES:
NONE
