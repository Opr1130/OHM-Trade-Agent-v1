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
activation is gated on F11 protection health proven, legacy drain READY (zero Freqtrade exposure, zero Paper-v1 pending/open exposure, zero unresolved or quarantined Paper-v1 lifecycle, and zero retained legacy reserved capital), universe metadata present, direction coverage resolved (SHORT supported with equivalent proofs or an explicit owner LONG-only mandate), and the target spine reachable; a missing, unreadable or unproven gate withholds activation rather than defaulting favourable
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
rollback restores exactly one authority, stops new target admissions immediately but withholds legacy new-entry authority until the rollback-ready gate proves no cross-authority collision, never runs two allocation authorities, needs no canonical-data migration, and retains the former comparator artifacts without deleting obsolete code
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
AC-012:
GIVEN:
the fail-closed rollback transition
WHEN:
the rollback scenarios are enumerated
THEN:
the contract enumerates an open target position during rollback, a pending target reservation during rollback, a target terminal/reconciliation not complete during rollback, a clean fully-drained rollback, and proof that exactly one new-entry authority exists throughout the transition, and states that legacy new-entry authority resumes only after the rollback-ready gate proves the target exposure has drained or a proven collision mechanism makes it safe
AC-013:
GIVEN:
the human-approved resumption requirement after a safety suspension
WHEN:
the resumption authority is inspected
THEN:
the contract states that stopping target admissions on UNSAFE/UNAVAILABLE is immediate while resumption is not, that protection continues during suspension, that a later healthy result alone does not resume new admissions, that resumption requires an explicit authorized resume gate, and that the owning authority is the incident lifecycle's owner-recovery-cycle policy rather than F11
AC-014:
GIVEN:
the owner SHORT mandate for the target paper route and the frozen R4-B0 SHORT machinery
WHEN:
direction authority is inspected
THEN:
the target route supports LONG and SHORT, the readiness probe reports both directions covered with no SHORT_AUTHORITY_MISSING, the SHORT_AUTHORITY_MISSING blocker is resolved by implemented and tested authority rather than by suppression or relabelling, the historical long-only assertions are converted to direction-coverage assertions rather than deleted, and the existing R4-B0 PCAND to OPIPC bridge is reused rather than duplicated
AC-015:
GIVEN:
the read-only committed-snapshot seam that feeds the non-authoritative target spine
WHEN:
the reader is inspected and exercised
THEN:
it reads only committed FEATURE_SNAPSHOT_RECORDED records through a read-only canonical surface, reconstructs the canonical FeatureSnapshot contract, re-derives and validates snapshot_id and content_hash, rejects malformed or identity-inconsistent payloads (including a malformed watermark and unknown enum values) as SnapshotRecordError rather than repairing them or escaping a raw error, returns records in canonical commit order (history_epoch, local_sequence) with the exclusive cursor advancing deterministically across bounded batches, dedupes deterministically by identity, exposes the cursor so a consumer owns resume persistence across restart, and never mutates or quarantines canonical production state
AC-016:
GIVEN:
the bounded Feature Bus SHADOW capture that produces the evidence the reader consumes
WHEN:
the producer and its scheduling are inspected and exercised
THEN:
it reuses the proven Feature Bus components (the Kraken source and instrument provider the manual pilot uses, the fixed evaluation grid, rolling-state/checkpoint continuity, the revision ledger and the FeatureBusPublisher) with no new feature math, schema or second market-data authority; it is authorized only when the Feature Bus is in exactly `shadow` mode AND the canonical writer is in exactly `shadow` mode (Feature Bus `active` does not authorize this SHADOW producer, and the shared publisher helper's broader semantics are unchanged); it is bounded to a configured instrument limit and a total wall-clock budget; it publishes only canonical FEATURE_SNAPSHOT_RECORDED evidence through the existing writer, using the completed fetch time (not the pre-fetch start) as the decision availability and preserving snapshot identity, cutoffs, availability times, consumed-input watermark and gap/restart state; it isolates an unexpected per-instrument source failure so other instruments still complete, records the failure, and never fabricates a batch or a snapshot; a batch carrying a source error publishes no fresh snapshot for that instrument while the error stays observable and unaffected instruments continue; once the remaining budget cannot fit one bounded request it stops requesting more instruments and records explicit budget-exhausted evidence; it grants no trading, ranking, admission, allocation, order or exchange authority; and it runs from its own bounded, non-overlapping scheduler entry on the single existing scheduler, never from inside the protected unified cycle, so it can never delay or skip a protection cycle
AC-017:
GIVEN:
the durable FeasibilityEvidence record codec (Slice 3A)
WHEN:
the durable wire record is built, validated and reconstructed
THEN:
the durable schema is explicit and frozen (not derived from the dataclass), persisting every substantive top-level field and every field of the nested MarketDataValidation and ExecutionValidation records so the exact typed F5 evidence can be reconstructed; the record carries the frozen F5 semantic `evidence_fingerprint` (FEV, unchanged, covering only the F5-normalized subset) AND an independent exact-content `payload_hash` (FEVH) computed over the complete canonical durable body excluding the hash itself, mirroring the Paper-v2 semantic-identity plus content-hash precedent; building requires a real FeasibilityEvidence, serializes all ratified fields and both nested typed records, and emits one deterministic wrapper; validation requires exact field sets (wrapper and body and both nested records), rejecting unknown fields, missing fields, wrong types, invalid enum/status tokens, malformed or naive datetimes, non-canonical nested structures, non-finite numbers and an invalid fingerprint or payload_hash, with no favorable defaults; reconstruction strictly rebuilds MarketDataValidation, ExecutionValidation and FeasibilityEvidence and performs three independent checks - the reconstructed F5 fingerprint, the rebuilt exact payload_hash, and canonical wrapper equality - where the third does not replace the second; a mutation of a field outside the F5 summary (for example ExecutionValidation.best_bid, mid_price, a depth field, a `*_complete` boolean or buy_vwap) leaves the F5 fingerprint unchanged yet changes payload_hash and is rejected; and the codec grants no trading, admission, reservation, execution or exchange authority, performs no market read and holds no clock

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
The activated route must have an explicit direction disposition. At R4-F8 the router was LONG-only (`paper_v2_scan_router.py` `SUPPORTED_DIRECTION`) and the readiness probe reported the missing SHORT authority as a hard blocker (`SHORT_AUTHORITY_MISSING`). R4-B2 resolved this under an explicit owner SHORT mandate: the router now supports both directions (`SUPPORTED_DIRECTIONS = {LONG, SHORT}`), the readiness probe reports both covered, and the historical `SHORT_AUTHORITY_MISSING` fact is preserved as history (see AC-014). A silently LONG-only activated route remains unacceptable; a route that lacks a direction is still refused rather than mapped onto another.

Rollback (fail-closed transition).
Stopping new Paper-v2 admissions is immediate: setting the mode to `off` (or an unreadable mode) stops new target entries at once. Restoring legacy new-entry authority is NOT immediate. While the target authority still owns any exposure - an open target position, a pending target admission or order, a committed target reservation, or an in-progress terminal reconciliation - legacy new-entry authority must remain withheld and the target path must keep managing, protecting and reconciling what it owns. Legacy new-entry authority may resume only after a deterministic rollback-ready gate proves that no cross-authority collision can occur: the target exposure has safely drained (no open position, no pending admission/order, no committed reservation, no in-progress terminal reconciliation), or a proven shared exposure/collision mechanism makes concurrent legacy admission safe. At every instant of the transition exactly one new-entry authority exists (none, or the draining target, or legacy) - never two. Rollback is completed by this switch and a code revert; it needs no canonical-data migration and does not delete the former comparator artifacts.

Required rollback acceptance scenarios: an open target position during rollback; a pending target reservation during rollback; a target terminal/reconciliation not yet complete during rollback; a clean fully-drained rollback; and proof that exactly one new-entry authority exists throughout the transition.

Cutover preconditions (fail closed).
Activation requires, all objectively observed and failing closed when absent, unreadable or unproven: F11 protection health proven (no silent or unmanaged holding, coverage complete, protection-incident health proven, target protection not withholding); legacy drain READY (zero Freqtrade open trades and outstanding signals, zero Paper-v1 pending entries and open positions, zero unresolved or quarantined Paper-v1 lifecycle, and zero retained legacy reserved capital); universe metadata present; direction coverage resolved per the rule above; and the target spine reachable. A missing or unreadable gate withholds activation. The drain is proven only when every legacy obligation class is cleared AND no legacy capital remains reserved: a retained legacy reservation with no counted obligation (for example an unresolved or quarantined lifecycle) is not drained.

Human-approved resumption after a safety suspension.
A suspended safety state is not cleared by an instantaneous healthy observation. When an already-active target route becomes suspended because protection is UNSAFE or UNAVAILABLE, target admissions are withheld, existing exposure keeps being protected, and a later healthy result alone does NOT resume new admissions. Resumption requires an explicit, authorized resume gate. F11 owns no such authority: the durable suspension and human-resumption authority is the incident lifecycle's owner-recovery-cycle policy (`app/services/system_incidents`, `requires_owner_recovery_cycles`).

Required resumption acceptance scenario: unsafe/unavailable -> target admissions suspended -> protection continues -> subsequent health recovery alone does NOT resume new admissions -> explicit authorized resume gate -> admissions may resume only when all other gates are also healthy.

Pre-cutover comparison evidence.
Before the target authority replaces the legacy admission source, the increment must produce matched-window comparison evidence of the target selector against cash/no-trade and the frozen profit-ranking comparator, under matched capital, timing, execution model and fee policy, over the full intent population including no-fills and rejects. This is the evidence the recovery roadmap and retirement ledger name as the gate to flip admission; it is recorded before the legacy admission source is replaced, not assumed.

Execution and economic proofs.
The activated target path must reproduce the frozen Paper-v2 execution contract: realistic entry and exit side; size-sensitive depth and liquidity binding; fees, slippage and latency; NO_FILL, PARTIAL_FILL and FULL_FILL separate from TARGET, STOP, TIMEOUT and independent RISK_EXIT; a limit touch alone insufficient for a fill; restart and terminal reconciliation; capacity release on cancellation, expiry or terminal reconciliation; idempotency for stale or replayed input; and direction-aware P&L for every direction the activated route authorizes (at minimum LONG, and SHORT where SHORT is authorized per the direction-coverage rule). A direction the route does not authorize is never silently dropped: it carries the explicit disposition required above.

Retry and requalification.
The activated path preserves the R4-B1 freeze in full: an exact retry is idempotent; a materially changed requalification is refused as a conflicting decision; no duplicate trade, reservation or disposition is created; committed ancestry and economics are immutable; ambiguity fails closed.

Rollback.
Rollback is fail-closed and is a switch plus code revert: setting the mode back to `off` stops new target admissions immediately, but the legacy path does not resume new entries until the rollback-ready gate proves no cross-authority collision can occur (see the fail-closed rollback transition above). Rollback must never leave two allocation authorities running and must preserve the former comparator artifacts; obsolete code is not deleted by this increment.

Authority boundary.
R4-B2 must not create, widen or imply: funded trading; funded exchange order authority; Kraken order placement, modification, cancellation or confirmation; margin, asset borrow or leverage; Committee runtime authority; dashboard or Telegram trading authority; a second scheduler; or a second paper/admission/reservation authority. Risk, strategy, execution and protection authority beyond the target paper path are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_001_contract_and_pointer_are_consistent
AC-002 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_002_activation_sequence_and_vocabulary
AC-003 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_003_single_new_entry_authority
AC-004 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_004_cutover_preconditions_fail_closed
AC-004 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_004_drain_requires_no_unresolved_or_reserved_legacy
AC-005 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_005_authority_collision_test_defined
AC-006 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_006_execution_proofs_enumerated
AC-007 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_007_retry_semantics_preserved
AC-008 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_008_rollback_restores_one_authority
AC-008 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_008_rollback_holds_legacy_until_drained
AC-009 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_009_authority_boundary_and_exclusions
AC-010 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_010_freeze_sets_no_mode_and_no_authority_import
AC-011 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_011_comparator_evidence_required
AC-012 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_012_rollback_transition_scenarios
AC-013 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_013_resume_requires_authorized_gate
AC-014 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_014_target_route_supports_long_and_short
AC-014 -> tests/test_opip_r4_b2_controlled_paper_activation.py::test_ac_014_short_direction_mechanics_and_identity
AC-014 -> tests/test_opip_paper_v2_increment6b_bc3.py::test_short_2x_alert_executes_at_1x_with_sell_entry
AC-015 -> tests/test_opip_r4_b2_committed_snapshot_reader.py::test_ac_015_roundtrip_identity_and_ordering
AC-015 -> tests/test_opip_r4_b2_committed_snapshot_reader.py::test_ac_015_cursor_is_commit_order_across_batches
AC-015 -> tests/test_opip_r4_b2_committed_snapshot_reader.py::test_ac_015_bounded_cursor_and_dedupe
AC-015 -> tests/test_opip_r4_b2_committed_snapshot_reader.py::test_ac_015_rejects_malformed_and_tampered_payloads
AC-015 -> tests/test_opip_r4_b2_committed_snapshot_reader.py::test_ac_015_reader_never_mutates_or_quarantines
AC-015 -> tests/test_opip_r4_b2_committed_snapshot_reader.py::test_ac_015_batch_survives_a_corrupt_stored_record
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_exact_shadow_gate_matrix
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_evaluated_at_is_post_fetch
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_per_instrument_fault_isolation
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_errored_batch_publishes_no_fresh_snapshot
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_internal_budget_stops_further_requests
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_configured_limit_and_budget_reach_capture
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_configured_budget_is_read_from_settings
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_unified_cycle_does_not_run_capture
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_capture_has_a_bounded_non_overlapping_cron_entry
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_scheduler_reconciliation_installs_capture_once
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_long_round_trip_is_exact
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_short_round_trip_is_exact
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_fingerprint_survives_round_trip
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_non_finite_ticker_last_round_trips
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_payload_hash_catches_summary_excluded_mutation
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_fingerprint_ignores_best_bid_but_hash_does_not
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_tampered_margin_evidence_rejected
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_missing_required_field_rejected
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_unknown_field_rejected
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_wrong_type_and_non_finite_rejected
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_invalid_enum_and_status_tokens_rejected
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_bad_fingerprint_and_bad_hash_rejected
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_bad_datetime_rejected
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_non_canonical_timestamp_rejected_consistently
AC-017 -> tests/test_opip_r4_b2_feasibility_evidence_codec.py::test_ac_017_reconstruction_fails_closed_on_hash_mismatch
IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-004 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
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
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-F8-paper-v2-cutover-readiness.md
AC-014 -> OHM-Trade-Agent-v1/app/services/paper_v2_scan_router.py
AC-014 -> OHM-Trade-Agent-v1/app/services/paper_v2_cutover_readiness.py
AC-014 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/contracts/paper_execution_runtime.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_controlled_paper_activation.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r4_f8_cutover_readiness.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_increment6b_bc3.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_cutover_bc3.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_paper_v2_cross_scan_simulation_bc3.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-015 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/features/committed_snapshot_reader.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_committed_snapshot_reader.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-016 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-016 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-016 -> OHM-Trade-Agent-v1/app/core/config.py
AC-016 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-feature-bus-capture
AC-016 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_capture.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R2-feature-bus-shadow-parity.md
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_shadow_activation_v1.py
AC-017 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-017 -> OHM-Trade-Agent-v1/app/opip/feasibility_evidence_record.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_evidence_codec.py

DEFERRED DISCOVERIES:
- The activation implementation (wiring the target F7 selector as the admission source, the mode/cutover sequence and their behavioral acceptance criteria and implementation map) is a later commit of this same increment and is not authorized by this freeze.
- The current router papers the legacy-ranked cohort; making the target F7 selector the admission source is the substantive R4-B2 change and must be reconciled against the frozen R4-B0 handoff and R4-B1 retry semantics.
- Direction coverage is frozen as a precondition and now resolved: R4-B2 implemented SHORT on the target route under an explicit owner mandate (AC-014). The historical fact that the router was LONG-only (`SUPPORTED_DIRECTION`) and the probe reported `SHORT_AUTHORITY_MISSING` is preserved as history, not deleted.
- The comparator and direction-coverage evidence are frozen as pre-cutover requirements (AC-011 and AC-004). Producing them is part of the activation implementation, not this freeze. The comparator evidence is recorded as a durable artifact under the repository's `docs/atdd/evidence/` convention with an explicit consumption disposition, in the activation implementation commit.
- The PCAND -> OPIPC candidate-identity bridge between the F7 selection and the Paper-v2 handoff (named in `ATDD-R4-F8-paper-v2-cutover-readiness.md` as required for R4-B wiring) is reconciled explicitly in the activation implementation commit; this freeze references it only through the frozen R4-B0 handoff contract.
- A dual-run comparison of the activated target authority against the legacy comparator for the same opportunity is separate evidence and is not required by this freeze; the freeze requires the collision proof (at most one execution per opportunity), not a dual-writing comparator.
- Whether the activated paper authority requires a production mode deploy is an owner control-plane action; this freeze authorizes no deploy.
- F11 remains a prerequisite gate and is not bypassed; its read-only decision is consumed, not replaced.

UNAPPROVED SCOPE CHANGES:
NONE
