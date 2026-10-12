INCREMENT:
ATDD-R5-E-canonical-reservations

OWNER-APPROVED INTENT:
Deliver the second R5-E slice: durable canonical capital reservations, so a paper capital reservation is owned by exactly one canonical writer, survives restart, cannot be double-spent or double-released, and abstains rather than admitting capital it cannot constrain. The owner approved this increment explicitly ("Decision: APPROVE `ATDD-R5-E-canonical-reservations` within Architecture v1.5.0 ... durable canonical reservations"), and the code itself declares the dependency: `commit_reservation` states that restart, multi-worker and durable-ledger safety "are explicitly NOT claimed by this increment: the reservation witness is process-scoped and in-memory, so `CAPITAL_RESERVATION_DURABILITY_STATUS` is the honest status and canonical persistent ownership stays the declared `R5-E` dependency". This increment closes that declared dependency by *reusing* the already-existing canonical reservation authority and adding only the reservation-lifecycle read projection that is missing, plus the fail-closed abstention rule for constraint sets the frozen capital policy cannot resolve. No new `WriterIntent` kind, no new event type, no new table, no second store, no new writer and no authority change.

ARCHITECTURE REFERENCES:
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-12 "Canonical ordering, temporal contract and shared writer": `REUSE_EXISTING_MODULE`; exactly one canonical writer owns durable domain records; no new event store, allocator database, outcome engine or position registry. `DC-03` requires units, precision, rounding, enum mappings, retention, compatibility and additive migration rules to be frozen before coding the increment.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-07 "Reservation, atomic consumption and concurrent change": reservation compares the expected portfolio version and commits capital and risk usage atomically, concurrent change fails closed rather than over-committing (`CI-04`), and fixed risk sizing and minimum reward-to-risk defaults remain binding and unmodified. This is the clause the increment's reservation atomicity is delivered under.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-11 "Paper isolation and research environment": paper management is deterministic, portfolio-aware and separately identified, with no funded write and no exchange order authority, `EVIDENCE_SHADOW` remains the production posture with `TARGET_PAPER` `BLOCKED` and `OPIP_PAPER_V2_MODE=off`.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-09 "Rotation group record and switching comparison": the rotation-group record stays owned by `R5-D` inside F7/F8 and is neither written, re-derived nor evaluated by this reservation increment, so this slice makes no rotation-group claim. SC-07 governs reservation atomicity; SC-09 governs rotation groups; the two are distinct clauses.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`, row `R5-E` "Deterministic portfolio paper management with single canonical writer, bounded retries and fail-closed isolation" (contracts `SC-09`/`SC-11`, open input `OQ-08`, gate `G5`, authority `PAPER_RESEARCH`); clause row `CI-04` "Compare-and-commit one reservation plan against one capital snapshot".
- `docs/architecture/v1.5.0/ARCHITECTURE.md`: capital is committed at one atomic boundary; a producer cannot widen its own limits; an unresolved policy must never be treated as "no limits".
- `AGENTS.md`: fixed risk sizing, minimum R:R, execution safeguards and paper isolation are preserved; the smallest change that solves the proven problem; no second allocator, cash ledger, event database, writer or scheduler.
- Owner decision of this session: approve `ATDD-R5-E-canonical-reservations`; prefer reuse of the existing reservation authority; do not automatically create a new `WriterIntent` kind; fail closed on missing `OQ-05`/`OQ-06` policy; never assume free cash alone establishes deployable capital; do not invent a numerical rule or silently introduce a breaking migration.

OWNERSHIP AUDIT (pre-implementation, evidence-based, performed before any code change):
- Finding 1 - a durable reservation authority already exists and is complete for reserve. `CanonicalWriter.admit_paper_opportunity` is a writer-owned atomic RPC: it resolves the authoritative capital policy (`resolve_capital_policy`, which fails closed on an unknown version rather than treating it as "no limits"), refuses a producer-supplied limit that does not match that policy (`CAPITAL_POLICY_MISMATCH`), validates the frozen admission request, checks canonical idempotency (`_existing_admission_result`, including `IDEMPOTENCY_PAYLOAD_CONFLICT` for the same identity with a different payload), opens `BEGIN IMMEDIATE`, verifies the canonical schema version, requires a canonically committed eligible decision context, compares the expected portfolio version (`STALE_PORTFOLIO_VERSION`), and writes the disposition event, the watermark and the sequence in one transaction. An `ADMITTED` disposition carries the canonical `paper_trade_id` and `reservation_id` identities minted by `admission_result_identities` and the `requested_reservation_amount` it reserved.
- Finding 2 - release already exists and is evidence-derived, not a second write path. `CanonicalWriter._portfolio_state` derives version, reserved capital and active reservation count from committed `ADMITTED` dispositions minus the trades released by `_verified_paper_trade_ids`, which admits release only for a committed reconciliation whose `terminal_reconciliation_state` is `FINAL_VERIFIED`, whose `position_state` is `FLAT` or `NO_POSITION`, and whose remaining quantity is zero within the existing quantity tolerance. Partial fills, unverified flat states, attempts and triggers therefore keep the reservation active, and the version stays the cumulative admission count so it remains a monotone concurrency token.
- Finding 3 - exclusivity is store-level and already stronger than per-process. `CanonicalWriter.__init__` acquires `CanonicalStoreLock`, an OS advisory lock held for the writer's whole lifetime, and refuses a second writer with `CanonicalStoreBusyError`; acquisition fails closed with no wait-and-retry.
- Finding 4 - the frozen capital policy binds only two constraints. `PaperCapitalPolicy` (`paper-capital-v1`) declares exactly `portfolio_equity_limit` and `portfolio_position_limit`. Risk, concentration, liquidity and sleeve caps are not frozen anywhere in the canonical policy registry, and `OQ-06` (sleeve caps) and `OQ-05` (turnover semantics) remain owner inputs. Therefore no durable surface may claim those constraints are enforced, and any question about capacity under them must abstain.
- Finding 5 - what is genuinely missing, and all that this increment adds. There is no per-reservation lifecycle read projection (only aggregate reserved-capital and active-reservation counts), and there is no declared abstention when a caller asks about a constraint the frozen policy cannot resolve. Both are additive reads.
- Audit decision: REUSE the existing admission-disposition reservation authority (the `ADMITTED` disposition as the reserve record, `_verified_paper_trade_ids` as the release evidence, `CanonicalStoreLock` plus `BEGIN IMMEDIATE` plus the canonical idempotency table as the atomicity and idempotency mechanism). Do NOT add a new `WriterIntent` kind: the audit proves it is unnecessary, and adding one would risk establishing a competing capital commitment path. No new write of any kind is introduced by this increment.

FROZEN DECISIONS (the eleven pre-implementation freeze items required by the owner decision):
1. Record identity. A reservation is identified by the canonical `reservation_id` minted by `admission_result_identities` from the admission `disposition_id`; the ledger entry additionally carries the `disposition_id`, the `paper_trade_id`, the `quote_currency`, the reserved `amount`, and the canonical coordinates of the disposition that created it (`history_epoch`, `local_sequence`). Account, environment, engine and currency identity are not new fields: they are inherited from the admission lineage (the `decision_context_id`, the decision context's environment and eligibility, and the quote currency), and cross-lineage records can never share canonical identity because the identity is derived from the committed disposition rather than supplied by the reader.
2. Ownership of reservations and capital mutations. `CanonicalWriter` is the only owner. Reserve happens only in `admit_paper_opportunity`; release happens only as a derivation from committed terminal-reconciliation evidence in `_verified_paper_trade_ids`; no module, service, job or API route may hold, mutate or release capital. The R5-C `commit_reservation` witness stays what it declares itself to be: process-scoped in-memory evidence with `CAPITAL_RESERVATION_DURABILITY_STATUS = PROCESS_SCOPED_IN_MEMORY_WITNESS`. It is not promoted to authority by this increment and this increment does not call it.
3. Money units, precision and rounding. Amounts are quote-currency amounts in the units the frozen admission contract already uses for `requested_reservation_amount` and `portfolio_equity_limit`; the ledger reports the committed value verbatim and never re-rounds, rescales or converts. The policy comparison keeps the existing `+ 1e-9` tolerance, and the existing quantity tolerance is used only for the release predicate it already governs. No new tolerance, unit or rounding rule is introduced.
4. Allowed reservation and release transitions. `ACTIVE -> RELEASED` only, and only from committed terminal-reconciliation evidence meeting the existing `FINAL_VERIFIED` plus `FLAT`/`NO_POSITION` plus zero-remaining predicate. There is no `RELEASED -> ACTIVE`, no partial release, no manual release, no expiry-driven release and no release on partial fills, attempts, triggers or unverified flat states. A released reservation stays in the ledger with its release basis; it is never deleted.
5. Enum and schema compatibility. No new event type, no new table, no `SCHEMA_VERSION` bump and no persisted-format change. The ledger and capacity projections are additive read models; every existing reader of the canonical store, including `paper_portfolio_state`, `paper_v2_active_exposures`, `paper_v2_ledger`, the cockpit readers and the analytics replica, keeps its current behaviour and payload.
6. Historical replay and additive migration. The projections are re-derived from committed events on every read, so a restored replica, a `history_epoch` advance or a replay reproduces the same reservation state; migration is additive because nothing is persisted by this increment. Existing event history remains valid and no historical row is rewritten, backfilled or reinterpreted.
7. Retention. The existing canonical retention rules apply unchanged. No new retention period, pruning, compaction or archival rule is invented, and a released reservation is retained exactly as long as the disposition and reconciliation evidence that justifies it.
8. Read-projection semantics and watermark consistency. Both new surfaces are pure reads: they hold the writer lock, write no event, create no idempotency row, consume no local sequence, advance no watermark and change no history epoch. The ledger reports the watermark it observed (`history_epoch`, `local_sequence`) so two reads at the same watermark are byte-identical, and its reserved capital, active reservation count and portfolio version reconcile exactly with `paper_portfolio_state` for the same currency at the same watermark.
9. Multi-process atomicity and rollback behaviour. Exclusivity is `CanonicalStoreLock` (a second writer is refused with `CanonicalStoreBusyError`), atomicity is `BEGIN IMMEDIATE` with the existing rollback on any error, and idempotency is the canonical `idempotency_keys` table. A refusal, a lock failure, a schema mismatch or an interrupted transaction must leave the store unchanged; the ledger read itself is never part of a transaction that can write.
10. Isolation from funded accounts and production data. Only the paper admission lineage can create a reservation; the projections read the canonical paper evidence and a caller-supplied quote currency and nothing else. No funded account, credential, exchange endpoint, order surface or production analytics store is reachable from the new code, paper and research paths gain no order authority, and this increment adds no database on the trading host.
11. Fail-closed treatment of missing `OQ-05`/`OQ-06` policies. The ledger declares `policy_binding_complete = False` and names the unbound constraint set (risk, concentration, liquidity, sleeve) so a durable record can never be read as proof that the R5-C binding constraint set was enforced. A capacity question whose constraint the frozen policy cannot resolve returns `ABSTAINED` with `POLICY_UNAVAILABLE` and no capacity figure (`remaining_capacity is None`) - never a number, never "unlimited". An unresolvable capital policy version continues to make admission impossible (`UNSUPPORTED_CAPITAL_POLICY_VERSION`) and makes the capacity projection abstain as well.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
one eligible paper decision context, one quote currency and one admission identity
WHEN:
a valid reservation is admitted and then retried with the same identity
THEN:
the first admission commits exactly one reservation, the retry returns that same canonical result, the retry creates no second reservation and no second debit, and the ledger reports exactly one entry for that reservation identity

AC-002:
GIVEN:
a reservation identity that is already committed
WHEN:
the same idempotency key is presented with a different economic payload
THEN:
the attempt is rejected with `IDEMPOTENCY_PAYLOAD_CONFLICT`, nothing is written, and the ledger, the reserved capital and the portfolio version are unchanged

AC-003:
GIVEN:
one canonical store, its reservation state and a competing or stale plan
WHEN:
a second writer opens the same store, or a reservation is attempted against a superseded or mismatched identity
THEN:
the second writer is refused with `CanonicalStoreBusyError`, a stale expected portfolio version is refused with `STALE_PORTFOLIO_VERSION`, an unknown reservation identity is not resolvable to any reservation, and every refusal mutates no capital and no reservation: no reservation is created, the reserved capital, the active reservation count and the portfolio version are unchanged, and the canonical admission-request evidence that records the refusal is the only committed consequence

AC-004:
GIVEN:
a frozen capital policy with an equity limit of 10,000 and a position limit of 3
WHEN:
6,000 is already reserved and a further reservation is attempted beyond the equity limit, or beyond the position limit
THEN:
the attempt is refused atomically with a capital-limit or capacity-limit rejection and reason code, no reservation is created, and the ledger still reconciles to the previously committed reserved capital

AC-005:
GIVEN:
a constraint the frozen capital policy cannot resolve (risk, concentration, liquidity or sleeve) and an unresolvable capital policy version
WHEN:
capacity is queried for that constraint, or admission is attempted under an unknown policy version
THEN:
the capacity projection abstains with `POLICY_UNAVAILABLE` and no capacity figure, the ledger declares policy binding incomplete and names the unbound constraint set, admission stays refused, and no surface ever reports an unlimited or default figure

AC-006:
GIVEN:
committed reservations in several lifecycle states and the writer's own portfolio projection
WHEN:
the ledger is read repeatedly at the same watermark and compared with the existing projection
THEN:
reserved capital, active reservation count and portfolio version reconcile exactly with `paper_portfolio_state`, the sum of active reservation amounts equals the reserved capital, a released reservation is reported once with its release basis, partial-fill and unverified states keep their reservation active, and no reservation is ever counted or released twice

AC-007:
GIVEN:
two quote currencies, an unknown currency and a caller-supplied reservation identity
WHEN:
the ledger and capacity projections are queried for each
THEN:
each currency reports only its own reservations, an unsupported or malformed currency fails closed, a foreign reservation identity resolves to nothing and cannot alter another lineage's capacity, and the new surfaces expose no funded account, exchange or order authority

AC-008:
GIVEN:
the projection surfaces added by this increment, the existing canonical readers and the release profiles
WHEN:
the reads run under snapshot inspection and the repository checks run
THEN:
each read writes zero events, creates no idempotency row, consumes no local sequence and advances no watermark or history epoch, the existing projections keep their behaviour and schema version, `EVIDENCE_SHADOW` stays the resolved profile with `TARGET_PAPER` blocked and `OPIP_PAPER_V2_MODE=off`, and the increment introduces no second writer, allocator, ledger, store, table or event type

EXPLICITLY OUT OF SCOPE:
- F5/F6/F7 admission, realistic F8 paper execution, F11 independent protection, R5-F learning, R6 Committee, F10 dashboard, alerts and R7 readiness: later increments with their own contracts
- Any new event type, database table, `SCHEMA_VERSION` bump, migration, backfill or rewrite of historical canonical evidence
- Any new `WriterIntent` kind, reservation write path, allocator, cash ledger, position registry, scheduler or second canonical writer
- Any change to the frozen `PaperCapitalPolicy` values, any new policy version, and any ratification of `OQ-05`, `OQ-06`, `OQ-07`, `OQ-08` or `OQ-09`
- Any promotion of the R5-C in-memory reservation witness to durable authority, and any wiring of the capital-intelligence service to the canonical writer
- Any runtime wiring: no job, API route, scheduler entry, Feature Bus topic or cockpit page consumes the new projections in this increment
- Any funded, exchange, order, modification, cancellation or confirmation authority, any production deployment, and any release-profile or `TARGET_PAPER` change

FROZEN BOUNDARIES:
- `app/opip/canonical/writer.py` stays the single canonical writer and the single reservation authority; the reserve path stays `admit_paper_opportunity` and the release path stays the committed terminal-reconciliation derivation. Neither is bypassed, duplicated or reimplemented.
- `app/opip/canonical/models.py` gains additive read models only; the persisted schema, event vocabulary and payload validators are untouched.
- Research and paper paths remain isolated from funded credentials, funded accounts and exchange endpoints, and `EVIDENCE_SHADOW` remains the production posture.
- A reservation is evidence of committed paper capital, never an authorization: it cannot place, modify, cancel or confirm an order and cannot change policy or widen a limit.
- An unresolved policy abstains instead of defaulting, and unresolved free cash never establishes deployable capital.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_001_a_valid_reservation_commits_exactly_once
AC-001 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_001_a_retry_with_the_same_identity_returns_the_canonical_result
AC-002 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_002_the_same_key_with_a_different_payload_is_rejected
AC-002 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_002_a_rejected_conflict_writes_nothing
AC-003 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_003_a_second_writer_cannot_own_the_store
AC-003 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_003_a_stale_portfolio_version_is_refused_without_a_partial_write
AC-003 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_003_an_unknown_reservation_identity_resolves_to_nothing
AC-004 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_004_a_reservation_beyond_the_equity_limit_is_refused_atomically
AC-004 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_004_a_reservation_beyond_the_position_limit_is_refused
AC-005 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_005_an_unbound_constraint_abstains_instead_of_becoming_unlimited
AC-005 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_005_an_unresolvable_policy_version_abstains_and_admission_stays_refused
AC-006 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_006_the_ledger_reconciles_with_the_existing_projection
AC-006 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_006_two_reads_at_the_same_watermark_are_identical
AC-006 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_006_a_fill_that_is_not_terminal_keeps_the_reservation_active
AC-007 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_007_each_currency_reports_only_its_own_reservations
AC-007 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_007_an_unsupported_currency_fails_closed
AC-008 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_008_the_reservation_reads_write_nothing_at_all
AC-008 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_008_existing_readers_and_schema_are_unchanged
AC-008 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_008_the_increment_contract_and_pointer_are_self_consistent
AC-008 -> tests/test_opip_r5_e_canonical_reservations.py::test_ac_008_release_profile_and_posture_are_unchanged

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/canonical/models.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/canonical/models.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/canonical/models.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/canonical/models.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-E-canonical-reservations.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_canonical_reservations.py

DEFERRED DISCOVERIES:
- Binding enforcement of the approved risk, concentration, liquidity and sleeve constraints at the same atomic financial decision boundary requires a frozen sleeve/risk policy, which does not exist yet. `OQ-06` (sleeve caps) and `OQ-05` (turnover semantics) remain owner inputs, so this increment abstains instead of ratifying a number and states that abstention in the ledger rather than inventing a binding rule.
- Wiring the R5-C capital-intelligence planner to the canonical reservation authority is a later R5-E integration slice: it would give a service a durable write path, which this increment deliberately does not do. The witness keeps its declared `PROCESS_SCOPED_IN_MEMORY_WITNESS` status.
- Terminal reconciliation-driven release accounting is owned by the existing B/C-2 execution-evidence path. This increment proves the reservation projection accounts for that evidence exactly once and adds no release writer; instrument-level partial-fill reserve adjustment therefore stays with its existing owner.
- Revising the identity of the R5-C `PortfolioReservationPlan` against the canonical reservation identity, and any expiry or supersession policy for a plan, remain open; this increment refuses a stale plan rather than defining one.
- Legacy paper-engine coexistence (`OQ-08`) is unchanged by this increment and stays open.
- The candidate-panel and forecast-identity binding raised as a `P2` finding on PR #363 still belongs to the R5-E/R5-F forecast-identity slices and is not touched here.

UNAPPROVED SCOPE CHANGES:
NONE
