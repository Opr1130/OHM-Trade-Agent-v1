INCREMENT:
ATDD-R5-C-deployable-bound-closure

OWNER-APPROVED INTENT:
Close the one seam in which the deployable-capital claim of the frozen R5-C contract is not implemented, and do it inside the existing capital owner (`EXTEND_EXISTING_MODULE`). Today `commit_reservation` consults only the account-evidence table and free cash, so a reservation whose amount exceeds the deployable capital established by the *same* declared snapshot under the *same* bound policy is never refused, and the successor snapshot carries the predecessor's usage rows unchanged (or replaces them from caller arguments), so the capacity a commit consumed is reusable. This increment closes that gap by (a) requiring an explicit bound policy and establishing the numeric deployable bound inside the existing compare-and-commit lock and version regime, (b) refusing an amount above that bound while preserving every existing refusal and its precedence, and (c) making the successor usage exactly the snapshot's declared usage plus independently evidenced, additive, non-reducible deltas.

The adopted body (`OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, 9 October 2026) requires in section 6 that `CI-02` bound deployable capital by free cash and the remaining approved risk, concentration, liquidity and horizon-sleeve limits - and that missing evidence is never favourable evidence - and that `CI-04` compare the expected portfolio version and commit capital and risk usage atomically.

SHADOW-ONLY POSTURE (declared, not concealed):
This increment delivers *structural capital safety* and nothing else. Only engineering-scenario policies exist (`OQ-06` remains `OWNER_DECISION_REQUIRED`), and an engineering-scenario policy is a deterministic code artifact, not an owner ratification. Therefore `commit_reservation` **fails closed** with `DEPLOYABLE_BOUND_UNAVAILABLE` until an owner-ratified funding policy version and trusted upstream consumption evidence exist, and the module declares that absence explicitly (`OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS` is empty, `CAPITAL_COMMIT_AUTHORIZATION_STATUS` is `FAIL_CLOSED_NO_OWNER_RATIFIED_CAPITAL_POLICY`). No fabricated approval flag, signing mechanism, synthetic ratification record or caller-controlled bypass is introduced, and no engineering fixture is treated as funding authority. The deployable-bound arithmetic, the binding-limit selection, the exact-bound equality, the derived successor usage and the sequential depletion of declared capacity are proved by pure, side-effect-free functions so the accounting laws are testable without any capital commit.

LEGACY-ACCEPTANCE RECONCILIATION (Checkpoint 24, owner-authorised):
The owner explicitly authorized reconciliation of the existing R5-C acceptance tests
against this shadow-only capital-safety contract. Thirteen historical tests became
incompatible with the required deployable policy and the fail-closed authorization
posture. Each is reconciled *in place* by this increment, with its original criterion
citation, its original test function name and its original acceptance-test traceability
preserved. Only the expectation changed, and only where the new contract makes the old
expectation unreachable: those positive obligations are recorded here as named *pending
integration proofs* instead of being deleted, skipped, weakened or re-expressed as
something unrelated. Every other assertion of every reconciled test - plan validity,
rejection precedence, approval-window and expiry behaviour, staleness, non-mutation and
non-authority - is preserved and executable. The frozen
`ATDD-R5-C-capital-intelligence` contract and architecture v1.5.0 are NOT edited: the
supersession is versioned here, so the historical criterion and the current posture can
be read side by side and reviewed as one change. No test is marked skipped or
expected-failure, no policy is fabricated, no monkeypatch or caller-controlled bypass of
the authorization gate is introduced, and no fault injection weakens the gate.

This increment grants no authority. It adds no order, sizing, allocation, execution, store, writer, scheduler, ledger, Telegram, dashboard, deployment or release surface, reads no environment variable and no wall clock, adds no runtime consumer, changes no durable schema version, record key or policy value, wires nothing into a runtime root, and keeps every record `authoritative = False`.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority) with extraction `docs/architecture/v1.5.0/ARCHITECTURE.md`: section 6 `CI-02` (deployable capital is the minimum of free cash and the remaining approved risk, concentration, liquidity and horizon-sleeve limits; an unresolved negative balance, a stale account state, missing fees, a currency mismatch or a failed reconciliation blocks new allocations) and `CI-04` (reservation compares the expected portfolio version and commits capital *and risk usage* atomically; at most one competing decision consumes contested cash; duplicate fills, retries and restart must not create or release capital twice).
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`: `SC-06` (one canonical capital record at one committed version), `SC-07` (reservation, atomic consumption and concurrent change), `SC-12` (one canonical writer; no new store, allocator database, event store or position registry).
- `docs/atdd/scope-contracts/ATDD-R5-C-capital-intelligence.md`: the predecessor increment that owns `SC-06`/`SC-07` in the same module; its `evaluate_deployable_capital`, `commit_reservation`, `CapitalSnapshot` and `assert_snapshot_recomputes_from` seams are reused and extended, never re-implemented or replaced.
- `docs/atdd/scope-contracts/ATDD-R5-D-portfolio-planning.md` (PR #363, unmerged): its `AC-008` clause is the cross-component claim this increment implements at the seam. The R5-D acceptance-test module does not exist at the approved base `0dba4d05`, so the R5-D integration is recorded as a pending gate rather than imported.
- `docs/atdd/scope-contracts/ATDD-R5-E-canonical-reservations.md`: posture parity only. That increment owns the *durable* canonical reservation authority and its `POLICY_UNAVAILABLE` abstention for constraint sets the frozen canonical policy cannot resolve. This increment adds no durable surface and does not touch it; its in-memory witness stays process-scoped and `R5-E` stays the declared durable dependency.
- `AGENTS.md`: prefer the smallest change that solves the proven problem; no second ledger, authority, writer or scheduler; do not invent numerical thresholds or approval markers; do not move evidence stores; automation has no trading authority.

APPROVED ACCEPTANCE CRITERIA:
AC-013:
GIVEN:
a capital snapshot with declared usage, a reservation plan bound to its version, and a deployable-capital policy
WHEN:
the plan is committed, and when an omitted policy, an explicit `None`, a non-policy object, an identity-mismatched policy or an engineering-scenario policy is offered
THEN:
the commit requires the policy argument explicitly, so an omitted `deployable_policy` is refused by Python itself with `TypeError`, a non-policy object is refused with `CapitalIntelligenceError`, and an explicit `None`, an identity-mismatched or an unratified policy establishes no bound so the commit is refused with `DEPLOYABLE_BOUND_UNAVAILABLE` *after* every earlier applicable check has been evaluated; the numeric ladder keeps positions ten (`NON_POSITIVE_AMOUNT`) and eleven (`INSUFFICIENT_FREE_CASH`) ahead of the new positions twelve (`DEPLOYABLE_BOUND_UNAVAILABLE`) and thirteen (`EXCEEDS_DEPLOYABLE_CAPITAL`); missing usage evidence leaves the bound unestablished and is refused at position twelve, while malformed usage evidence is refused structurally without mutating cash, snapshot, current version or witness; malformed usage evidence never preempts a refusal the seam would have reached first, because it is validated only under the witness lock, after every higher-priority position and before any successor is constructed; an amount equal to an established bound is admissible while one cent above it is refused with `EXCEEDS_DEPLOYABLE_CAPITAL`; an unestablished or abstaining evaluation is never misrepresented as a numeric limit breach; and every refusal leaves no successor, no committed amount and no changed current version

AC-014:
GIVEN:
a snapshot whose own declared limit and sleeve usage rows are authoritative, and caller-supplied `ReservationUsageDelta` evidence
WHEN:
the successor usage is derived, and when a delta is forged, foreign, stale, duplicated, negative, off the money grid, or names a row the snapshot does not declare
THEN:
the successor usage equals the snapshot's declared usage plus the validated deltas, additively and never reducibly, with a delta that cannot be validated refused structurally before the successor is constructed so the snapshot and the witness stay unchanged, a declared zero delta is accepted as declared evidence and not as a missing row, the derivation is deterministic and preserves row order, no delta can create a previously undeclared usage row, a missing required delta for a declared row fails closed, and the sequential depletion of declared capacity is proved as a *pure derivation* - a simulated chain in which each successor usage is derived from the previous step's declared usage plus its deltas - which depletes the declared capacity one step at a time until the bound is exactly consumed and the next cent is refused with `EXCEEDS_DEPLOYABLE_CAPITAL`; that pure chain is not a successful `commit_reservation` integration and is never reported as one, because no commit can succeed while the owner-ratified policy registry is empty

AC-015:
GIVEN:
a policy that configures a sleeve cap, a snapshot that declares no usage row for that sleeve, and the declared bound vocabulary
WHEN:
deployable capital is evaluated
THEN:
the evaluation abstains with `SLEEVE_USAGE_NOT_DECLARED` and publishes no number, the new reason is appended to the closed abstention-precedence vocabulary so its declared set and length stay exactly equal to the reason enumeration, the existing `LIMIT_USAGE_NOT_DECLARED`, `LIMIT_NOT_CONFIGURED`, `SLEEVE_LIMIT_NOT_CONFIGURED`, identity, currency, freshness, trust and reconciliation refusals are preserved unchanged, `CAPITAL_BOUND_PRECEDENCE` keeps exactly its six declared members, and the `HORIZON` dimension is documented as represented only by named policy sleeves with no independent numeric bound, so no seventh bound is invented and no independent horizon enforcement is certified

AC-016:
GIVEN:
a plan amount crossing the frozen portfolio owner's float boundary and the declared rejection vocabulary
WHEN:
the amount is read onto the money grid and the rejection vocabulary is inspected
THEN:
`_money_from_float` keeps its declared implementation - `Decimal(str(value))`, `ROUND_HALF_UP`, `MONEY_QUANTUM` of `0.01` and `_FLOAT_TOLERANCE` of `0.000001` - and is proved to adjust in both directions inside that tolerance and to refuse a value beyond it, no second rounding rule exists, and the commit rejection vocabulary is the closed declared set in the declared order with the two new members appended after `INSUFFICIENT_FREE_CASH`

AC-017:
GIVEN:
the amended module, its records, its consumers and the repository posture
WHEN:
the funding-authority gate, the module source, the runtime roots and the release-profile contract are inspected
THEN:
no engineering-scenario policy grants funding authority, the shadow-only posture and its owner dependency are declared constants rather than inferred behaviour, no fabricated approval flag, signature or ratification record exists, no runtime root or application module consumes the seam, no durable schema version, record key, policy value or `PaperCapitalPolicy` value changed, the resolution witness stays process-scoped and in-memory with `R5-E` as the declared durable dependency, `EVIDENCE_SHADOW` remains the active profile with `TARGET_PAPER` blocked and `OPIP_PAPER_V2_MODE` off, every record stays `authoritative = False`, and this increment's scope contract is self-consistent with its acceptance tests and its changed files

AC-018:
GIVEN:
the thirteen legacy R5-C acceptance tests whose historical expectations this shadow-only contract supersedes, and this increment's reconciliation record
WHEN:
the legacy test module, both test modules, the whole test tree and this contract are inspected
THEN:
every reconciled test still exists under its original name and still cites its original R5-C criterion, exactly the reconciled tests that carry a superseded positive obligation carry the `PENDING INTEGRATION PROOF` marker and no other test does, every superseded obligation is named in this increment's reconciliation registry together with its dependency on an owner-ratified funding policy (`OQ-06`), trusted upstream consumption evidence or `R5-E`, no test or fixture in either module is skipped or marked expected-failure, no test assigns or patches the owner-ratified policy registry, the funding-authority predicate or the capital owner, the frozen `ATDD-R5-C-capital-intelligence` contract keeps its original `AC-006`/`AC-007` text and its `UNAPPROVED SCOPE CHANGES` of `NONE`, and the supersession is versioned in this contract rather than silently reinterpreting the old criteria

EXPLICITLY OUT OF SCOPE:
- Any owner ratification: `OQ-05`, `OQ-06`, `OQ-07`, `OQ-08` and `OQ-09` stay open, and no numeric cap, sleeve limit, cooldown, budget or threshold is ratified, defaulted, implied or inherited
- Any funded, exchange, order, modification, cancellation, confirmation or live-trading authority, and any paper-v2 or `TARGET_PAPER` activation
- Any durable reservation record, canonical write path, database table, migration, backfill, event type, `WriterIntent` kind, second ledger, second writer, allocator or scheduler, and any wiring of this in-memory seam to the canonical writer or to `R5-E`
- Any runtime wiring: no job, API route, scheduler entry, Feature Bus topic or cockpit page consumes the amended seams
- Any change to `app/opip/contracts/portfolio.py`, `app/opip/contracts/paper_execution.py`, `app/opip/canonical/**`, `docs/architecture/**`, the release profile, Compose, `.env`, workflows, or the R5-D/R5-E scope contracts and test modules
- Any re-expression of the frozen R5-C criteria themselves: the owner-authorised Checkpoint 24 reconciliation is recorded inside this contract (`LEGACY-ACCEPTANCE RECONCILIATION` and `SUPERSESSION RECORD`) and `ATDD-R5-C-capital-intelligence` stays byte-unchanged, so no historical criterion is rewritten, retired, relocated or moved
- Retiring the historical float-based `CapitalUtilization`/`evaluate_rotation` vocabulary or any other legacy surface

FROZEN BOUNDARIES:
- One owner per concern: `app/services/capital_rotation_intelligence.py` stays the only capital owner, and no second capital book, authority, ledger or reservation family is created.
- The frozen `SC-06`/`SC-07` record keys, schema versions, cash laws, recompute triggers, portfolio-version lineage and the process-scoped witness semantics are preserved exactly; `_money_from_float` is preserved unchanged.
- The rejection ladder keeps positions one to eleven exactly as declared; positions twelve and thirteen are appended below them, and structural misuse still raises instead of returning a result.
- Bound and usage checks run inside the existing witness lock and version-validation regime; no second lock, ledger or financial authority is introduced.
- A missing or unresolvable bound is never reported as a numeric breach, and missing evidence is never read as favourable evidence.
- Engineering-scenario policy fixtures prove arithmetic only; they never establish funding authority and are never treated as an approved policy.
- Fixed risk sizing, minimum reward-to-risk, F5 feasibility and veto authority, F6 forecast ownership, F7 constrained selection, F8 paper execution and F11 independent protection all remain binding and unmodified.

ACCEPTANCE TEST TRACEABILITY:
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_a_refusal_leaves_the_book_and_witness_unchanged
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_a_valid_bound_admits_equality_and_refuses_one_cent_over
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_an_identity_mismatched_policy_establishes_no_bound
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_an_unratified_policy_cannot_establish_a_numeric_bound
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_malformed_or_incomplete_usage_evidence_is_refused_at_its_own_position
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_malformed_usage_evidence_never_preempts_an_earlier_refusal
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_the_commit_requires_an_explicit_bound_policy
AC-013 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_013_the_numeric_ladder_preserves_positions_ten_and_eleven
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_a_declared_zero_delta_is_evidence_not_a_missing_row
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_a_missing_required_delta_fails_closed
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_an_undeclared_usage_row_cannot_be_created_by_a_delta
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_sequential_reservations_deplete_declared_capacity
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_the_commit_leaves_no_trace_when_usage_cannot_be_validated
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_the_derivation_is_deterministic_and_preserves_row_order
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_the_delta_contract_refuses_forged_and_duplicate_rows
AC-014 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_014_the_successor_usage_equals_declared_usage_plus_validated_deltas
AC-015 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_015_a_configured_sleeve_without_declared_usage_abstains
AC-015 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_015_no_deployable_number_is_published_when_usage_is_missing
AC-015 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_015_the_abstention_vocabulary_stays_closed_and_ordered
AC-015 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_015_the_horizon_dimension_is_a_declared_named_sleeve_mapping
AC-015 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_015_the_limit_and_identity_refusals_are_preserved
AC-016 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_016_an_amount_beyond_the_declared_tolerance_is_refused
AC-016 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_016_the_money_crossing_rounds_up_and_down_within_tolerance
AC-016 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_016_the_money_grid_constants_are_the_declared_ones
AC-016 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_016_the_rejection_vocabulary_is_closed_and_ordered
AC-017 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_017_no_engineering_policy_grants_funding_authority
AC-017 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_017_the_increment_scope_contract_is_self_consistent
AC-017 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_017_the_release_profile_posture_is_unchanged
AC-017 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_017_the_seam_gains_no_runtime_consumer_or_authority_surface
AC-017 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_017_the_shadow_only_posture_is_declared_in_the_module
AC-018 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_018_no_skip_xfail_or_authorization_bypass_exists
AC-018 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_018_the_reconciled_legacy_criteria_are_named_and_traced
AC-018 -> tests/test_opip_r5_c_deployable_bound_closure.py::test_ac_018_the_supersession_is_versioned_in_this_contract

IMPLEMENTATION MAP:
AC-013 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_deployable_bound_closure.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-C-deployable-bound-closure.md
AC-014 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_deployable_bound_closure.py
AC-015 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_deployable_bound_closure.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-016 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_deployable_bound_closure.py
AC-017 -> OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_deployable_bound_closure.py
AC-017 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_deployable_bound_closure.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_r5_c_capital_intelligence.py
AC-018 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-C-deployable-bound-closure.md

DEFERRED DISCOVERIES:
- LEGACY-ACCEPTANCE RECONCILIATION (owner-authorised at Checkpoint 24; completed, not pending): the existing R5-C acceptance contract expects `CapitalCommitStatus.COMMITTED` (and `IDEMPOTENT_REPLAY`) from commits that supply no policy, and the amended seam now requires an explicit `deployable_policy` and refuses every currently declarable policy with `DEPLOYABLE_BOUND_UNAVAILABLE` because no owner-ratified funding policy exists. All thirteen historical failures are reconciled *in place* by this increment, with each original criterion citation, test function name and acceptance-test traceability preserved, so no historical test is left failing, none is skipped or marked expected-failure, and no assertion is diluted. The frozen `ATDD-R5-C-capital-intelligence` contract itself remains byte-unchanged; the supersession is versioned in this contract's `SUPERSESSION RECORD`, and the ten positive obligations reconciliation cannot yet satisfy are registered as `PENDING INTEGRATION PROOF` and machine-checked by AC-018. Successful commit, replay and concurrency certification is therefore **not** complete and is not claimed.
- The approved vocabulary change *requires* exactly one mechanical test edit: the positional pin `CAPITAL_DEPLOYABLE_ABSTENTION_PRECEDENCE[-1] is SLEEVE_LIMIT_NOT_CONFIGURED`, because the owner-approved contract appends `SLEEVE_USAGE_NOT_DECLARED` to that closed tuple. That edit is **not** the only change this increment makes to the historical R5-C acceptance tests. The owner-approved Checkpoint 24 reconciliation updates **thirteen** historical tests, each in place and each retaining its original criterion citation, test function name and acceptance-test traceability; the frozen `ATDD-R5-C-capital-intelligence` contract stays byte-unchanged; and the **ten** positive obligations that reconciliation cannot yet satisfy are recorded as `PENDING INTEGRATION PROOF` and machine-checked by AC-018 (see `LEGACY-ACCEPTANCE RECONCILIATION` and `SUPERSESSION RECORD`).
- Delta provenance and structural validity are NOT economic proof: nothing here verifies that a declared delta equals the plan's true incremental risk, concentration, liquidity or sleeve consumption, and committed capital is never assumed to equal incremental usage in any category. A zero delta is a declared assertion, not evidence that the true incremental exposure is zero. The correct-and-complete consumption model belongs to the sizing/risk owner and remains unproven.
- The owner-ratified funding policy and trusted upstream consumption evidence are prerequisites for a real `EXCEEDS_DEPLOYABLE_CAPITAL` event in production and do not exist yet; this increment therefore proves structural capital safety only and claims no full F-008 certification.
- R5-D `AC-008` cross-component integration is a pending gate: PR #363 is unmerged and its acceptance-test module is absent from the approved base, so the cross-component proof that a plan's declared consumption is bounded at the commit boundary could not be exercised here.
- `R5-E` persistence and replay: the amended seam remains process-scoped and in-memory. Durable refusal replay, restart safety and multi-worker safety stay with the canonical writer and are not claimed here.
- `HORIZON` has no independent numeric bound. `CI-02`'s horizon-sleeve limit is represented only by named policy sleeves (`CapitalBound.SLEEVE`), and a real independent horizon cap would need a new policy field, a new limit kind and a new evaluator branch, which is a separate contract.
- Commit-level idempotency and replay (`IDEMPOTENT_REPLAY`) are unreachable while no policy is ratified: the seam refuses every commit at position twelve before a successor can exist, so a recorded successor, an idempotent replay, a concurrency winner and a consecutive committed reservation cannot be produced here at all. The reconciled historical criteria that used to assert those outcomes are therefore recorded as `PENDING INTEGRATION PROOF` in the SUPERSESSION RECORD instead of being re-expressed, and the same dependency gates them: `OQ-05` (trusted upstream consumption evidence) and `OQ-06` (owner-ratified funding policy) are both unratified, `R5-D` `AC-008` cross-component integration still awaits PR #363, and `R5-E` durability remains a separate increment.
- Engineering-scenario policies exist only so the arithmetic can be exercised in isolation; the module's declared policy-version registry is not evidence of owner ratification and is never treated as such.

SUPERSESSION RECORD (Checkpoint 24 reconciliation, explicit and reviewable):
- Superseded expectations, by historical criterion. `ATDD-R5-C-capital-intelligence` `AC-005` (the closing "trusted evidence still commits" step), `AC-006` (five tests: atomic commitment, "a later valid plan still commits", the boundary-window and whole two-leg commits, and the deadline-carrying successor of a committed reservation), `AC-007` (five tests: idempotent replay of a recorded successor, `PLAN_ALREADY_CONSUMED`, exactly one concurrency winner plus the winner-replan, and consecutive committed reservations following the current version), and `AC-011` (one test: the record of a committed reservation). Thirteen tests in total, each reconciled in place with its original name and citation preserved.
- What replaced each superseded expectation, and why. A commit under a currently declarable policy cannot succeed, because `OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS` is empty (`OQ-06`), so every one of those tests now asserts `REJECTED` / `DEPLOYABLE_BOUND_UNAVAILABLE` plus non-mutation, while the behaviour each test independently owns - rejection precedence, window and expiry ordering, staleness, plan currency, non-authority and witness scope - is preserved and still asserted.
- Retained assertion versus unproven obligation (the two are not equivalent and must not be read as if they were). A *retained assertion* is a claim this increment proves locally today: the refusal that was actually returned, its position in the ladder, its non-mutation, and the accounting laws proved by pure functions. A *superseded positive expectation* is an obligation this increment does **not** prove at all - there is no successful `commit_reservation`, no committed successor, no partition move, no recorded plan reference and no replay anywhere in this increment - so it is deferred as a `PENDING INTEGRATION PROOF` until an owner-ratified funding policy version and trusted upstream consumption evidence exist, and, for anything claimed durable, restart-safe or multi-worker, until `R5-E`. Reconciliation therefore converts a previously proven positive claim into an explicitly named *unproven* obligation: it never converts an obligation into evidence, it never weakens a retained assertion, and a refused attempt may never be reported as proof that a superseded expectation now holds.
- Positive obligations that remain unproven (`PENDING INTEGRATION PROOF`), owned here: "trusted account evidence commits"; "a reservation moves free cash into order reserves, records the plan reference and advances the portfolio version by one step"; "a valid plan on an untouched version commits after earlier rejections"; "a plan inside its window commits and a retry of that committed plan replays"; "a committed reservation cannot extend the snapshot validity deadline"; "a replay of a committed plan returns the recorded successor and consumes nothing twice"; "a plan consumed at one version is refused as already consumed rather than replayed onto another version"; "exactly one of two concurrent racers consumes the contested capacity and the loser can replan against the committed version"; "consecutive reservations consume the version that is actually current for the book"; and "a committed reservation record stays non-authoritative and non-durable". Each is registered in `tests/test_opip_r5_c_deployable_bound_closure.py` as a `LegacyReconciliation` entry and is checked by AC-018.
- Dependency of every pending proof: an owner-ratified funding policy version registered in `OWNER_RATIFIED_CAPITAL_POLICY_VERSIONS` (currently empty; `OQ-06` remains unratified), trusted upstream consumption evidence for the committed amount in each usage family, and - for anything claimed durable, restart-safe or multi-worker - the `R5-E` canonical writer. None of these exists today, and this increment neither fabricates nor simulates any of them.
- Governance note: the supersession is representable without editing a frozen criterion, because the R5-C contract is left byte-unchanged and this increment carries the reconciliation as its own accepted criteria, registry and traceability. If a future change requires the historical criteria themselves to be rewritten, that is a separate owner governance decision and not something this contract may do.
UNAPPROVED SCOPE CHANGES:
NONE
