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
it reads only committed FEATURE_SNAPSHOT_RECORDED records through a read-only canonical surface, reconstructs the canonical FeatureSnapshot contract, re-derives and validates snapshot_id and content_hash, rejects malformed or identity-inconsistent payloads (including a malformed watermark and unknown enum values) as SnapshotRecordError rather than repairing them or escaping a raw error, returns records in canonical commit order (history_epoch, local_sequence) with the exclusive cursor advancing deterministically across bounded batches, holds no process-lifetime dedupe set (the exclusive cursor is the dedupe authority, so a re-read from an earlier cursor deterministically returns the same committed records), exposes the cursor so a consumer owns resume persistence across restart, and never mutates or quarantines canonical production state
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
AC-018:
GIVEN:
the canonical `feasibility.evidence.recorded` event and its single canonical writer
WHEN:
one committed F5 feasibility-evidence record is persisted and replayed
THEN:
it is a separate LOW-priority canonical evidence class with its own event vocabulary, watermark stream and idempotency prefix, owned by one canonical writer, deliberately NOT part of FEATURE_BUS_EVENT_TYPES, and grants no trading, admission, reservation, execution or exchange authority; it carries no ops handoff; the event is registered in the writer's EventType vocabulary and admitted into ACCEPTED_EVENT_TYPES and the full-payload conflict set; the writer validates the record at the persistence boundary (exact payload keys and discriminator, the frozen codec validator recomputing both identities, LOW priority, no ops handoff, and an idempotency key, event_time, correlation id and causation id that must match what the validated record itself implies, so a forger cannot decouple the envelope from the record); the idempotency key binds the record's evaluation identity (instrument version, venue instrument, direction, evaluation and cutoff instants, source snapshot id) together with its exact payload_hash and contains no recorded-at wall clock, receipt time, random envelope id, process id, retry count or database sequence, while the F5 evidence_fingerprint is retained separately for F5 lineage; a byte/content-equivalent retry reaches DUPLICATE_OK leaving exactly one canonical row, an equivalent non-canonical instant normalizes to the same durable bytes and key, materially different durable evidence yields a different key and a distinct record that never silently collapses into the first, a key from one record presented with a different record's payload fails closed, and a restarted writer over the same store treats an exact replay as DUPLICATE_OK
AC-019:
GIVEN:
the read seam over committed F5 feasibility evidence
WHEN:
a consumer reads committed feasibility.evidence.recorded records
THEN:
it opens the canonical store read-only through the existing read-only connection (no store lock, no schema initialization) and can never mutate, quarantine or rewrite canonical evidence; it reads only committed feasibility.evidence.recorded records in canonical (history_epoch, local_sequence) order and exposes a bounded, exclusive cursor so a consumer processes each record once, deterministically, and resumes after a restart without reclassifying old records as new; it validates each durable payload through the frozen event trust boundary and reconstructs the exact typed FeasibilityEvidence, recomputing both the F5 evidence_fingerprint and the exact-content payload_hash, so a payload whose declared identities do not match its content is rejected; it fails closed per record without aborting the batch (a malformed, tampered or corrupt record is counted and reported as rejected while unaffected valid records are still returned, and no error escapes as an unhandled exception); it keeps no process-lifetime dedupe set - the exclusive canonical cursor is the dedupe authority, so a re-read from an earlier cursor deterministically returns the same committed records; it fabricates nothing (an empty store yields an empty batch); and it holds no trading, admission, reservation, execution or exchange authority and performs no market read
AC-020:
GIVEN:
the R4-B2 shadow cadence bridge over the bounded Feature Bus SHADOW capture
WHEN:
the capture is scheduled and one pass is exercised
THEN:
the capture runs on the frozen F3 60-second evaluation grid so that consecutive passes commit FeatureSnapshots for the same instrument at consecutive cutoffs exactly 60 seconds apart, because one pass materializes exactly one snapshot per instrument at the current closed cutoff and never a backdated catch-up series; the produced snapshots are consumable by the frozen F3 IGNITION detector at each snapshot's own evaluation cutoff; the pass uses a bounded acquisition concurrency (a finite worker pool, never unbounded) with one bounded public Kraken OHLC request per instrument per minute (no bulk multi-pair endpoint exists), the acquisition is parallel while materialization stays strictly sequential on the single canonical writer connection, and the run tolerates a genuine two-per-instrument pair without overlap; the whole pass is bounded strictly inside its 60-second slot by an internal wall-clock budget (default 45, capped at 50) plus an enforceable in-container termination bound, and non-overlap is guaranteed at the process level INSIDE the container: the capture holds a process-level advisory lock for its whole pass, so at most one capture_feature_bus_shadow process executes inside the container at any instant even if the host Docker client dies independently and leaves a surviving in-container process (a refused invocation records an explicit SKIPPED_LOCK_HELD disposition rather than silently exiting); the enforceable timeout runs on the container side, directly supervising the Python process (timeout --signal=TERM --kill-after=5s 50, so the process is dead by ~55s, strictly below 60), and no host-side timeout wraps the docker exec call that could release the only host lock while the in-container process may continue (the host flock is retained only as a cheap first-line guard, never as the enforceable one), so two passes cannot overlap and the protected 60-second unified cycle is never contended, delayed or skipped; a missing or delayed minute yields INCOMPLETE coverage and fails closed rather than fabricating contiguous evidence; a 15-minute gap, a replayed cutoff and a non-adjacent evaluation never advance F3 persistence; the run grants no trading, ranking, admission, allocation, order or exchange authority, keeps Feature Bus production `off` and Paper-v2 `off`/unset, changes no authority, and adds no second market-data truth system
AC-021:
GIVEN:
the bounded PROSPECTIVE SHADOW feasibility-evidence producer (Slice 3A)
WHEN:
committed FeatureSnapshots are converted into F5 feasibility evidence
THEN:
it is a prospective collector that never backfills history: a missing read cursor cold-starts at the current committed snapshot head (a deterministic, persisted action) and collects only NEW records after activation rather than replaying committed history, and before any live market read it compares the snapshot's evaluation/availability boundary with the real acquisition instant and, when the snapshot is outside a frozen contemporaneous window (two 60-second evaluation intervals), refuses to fetch current market evidence against it and records an explicit stale disposition instead; the evidence epoch is the snapshot's own evaluation cutoff and the source cutoff truthfully describes the source evidence cutoff (the close of the latest completed source candle), with current receipt/visibility never backdated, and the builder fails closed when the source cutoff would fall after the evaluation epoch rather than stamping newer data onto an older epoch; it produces exactly one genuine feasibility.evidence.recorded record per contemporaneous committed snapshot, keyed to the F3 IGNITION detector evaluation with direction supplied through an injectable seam (default LONG for the long-biased IGNITION route, so the R4-B2 SHORT route can drive the same producer with genuine BTNL evidence), and it rejects evidence whose direction does not match the requested direction (a SHORT request can never be satisfied by LONG/spot evidence); it preserves negative evidence: a present REJECT market record and a present INVALID execution record remain present typed evidence (F5 turns them into its existing hard VETO), and missingness/availability are reserved for genuinely absent or unavailable evidence rather than unfavourable evidence; it assembles the evidence from the proven scanner primitives (validate_market_data and evaluate_execution) with an explicit acquisition instant so no hidden clock is read, with no new evidence math and no second market-data authority, and a SHORT request without genuine BTNL margin evidence fails closed (spot evidence is never serialized as BTNL evidence); it advances its persisted read cursor only across a contiguous prefix of snapshots that reached a TERMINAL disposition and halts advancement at the first RETRYABLE snapshot, and the read seam returns one ordered record per committed row each carrying its own canonical event_id and (history_epoch, local_sequence) cursor, so progress is persisted after EVERY terminal row (a malformed committed row is a deterministic terminal rejection that advances to that row's own cursor and is counted and surfaced, never silently skipped and never a livelock; a retryable row halts advancement so it is retried and is never skipped); before each valid snapshot invokes the live evidence builder it proves enough of the internal budget remains for one complete bounded acquisition and, when it does not, sets budget-exhausted and stops with the cursor left at the last terminal row rather than depending on the process timeout to interrupt normal control flow; a transient source failure or unexpected assembly exception is retryable and does not advance past the row; it is authorized only for exactly Feature Bus shadow AND canonical writer shadow (active does not authorize it), and an unauthorized run opens nothing (no canonical store, no market read), with its scheduled module entrypoint executing and emitting a machine-readable inert/refused result; the shadow validation notional is an explicitly configured value, never derived from live account equity; a duplicate replay reaches the writer's DUPLICATE_OK and is counted as a duplicate rather than a second record; it is bounded to a configured limit and internal wall-clock budget and stops cleanly with explicit budget-exhausted evidence; it holds its OWN process-level non-overlap lock (a distinct identity from the Feature Bus capture, so neither producer can suppress the other; two feasibility captures never overlap and two Feature Bus captures never overlap, while a Feature Bus capture and a feasibility capture may run concurrently) and runs from its own bounded scheduler entry with an in-container timeout below the minute, never from inside the protected unified cycle; and it grants no trading, ranking, admission, allocation, order or exchange authority
AC-022:
GIVEN:
the SHORT / BTNL feasibility-evidence route of the bounded SHADOW producer (Slice 3A)
WHEN:
a SHORT direction is requested for a committed snapshot
THEN:
margin eligibility is discovered from Kraken's Bitnomial execution venue (the same authority the live scanner uses) by delegating to the live scanner's validate_short_margin_eligibility rather than re-implementing venue/leverage policy, resolving a margin_venue_symbol carrying the `:BTNL` provenance F5 requires for a trusted SHORT margin book and a genuine leverage tier bounded by the account ceiling, with a pair absent from the venue recorded INELIGIBLE and an unavailable discovery recorded UNAVAILABLE (never fabricated eligible); the SHORT execution liquidity evidence is built from the BTNL margin book (get_pre_trade/get_post_trade with the `:BTNL` margin venue symbol) and never from the spot book, and when the pair is ineligible or the BTNL book is unavailable the execution record is explicitly UNAVAILABLE present evidence (so F5 abstains with INSUFFICIENT_EVIDENCE) rather than spot evidence substituted as BTNL; market data is validated on the spot candles because it is direction-agnostic, while margin and execution are strictly BTNL-scoped; the evidence carries the `:BTNL` provenance so F5 trusts the margin book, and the same honest evaluation_time/source_cutoff contract (with the fail-closed epoch check) applies as for LONG; the producer dispatches the requested direction to the direction-appropriate builder and rejects evidence whose direction does not match the request (a SHORT request is never satisfied by LONG/spot evidence); it generates no new evidence math or market-data authority beyond the proven scanner primitives (validate_market_data, discover_short_margin, evaluate_execution); and it grants no trading, ranking, admission, allocation, order or exchange authority

AC-023:
GIVEN:
the prospective F6 evidence contract and the ONE committed canonical paper outcome authority (the `paper_execution` attempt/fill records, the `paper_execution.opportunity_disposition.recorded` admission disposition, and the `paper_outcome.terminal.recorded` terminal record)
WHEN:
one economic opportunity's committed canonical evidence is projected onto the F6 entry-execution family (Slice 3B prospective label projection)
THEN:
the projection resolves the exact frozen F6 entry token `NO_FILL`/`PARTIAL_FILL`/`FULL_FILL` and never invents one; a `FILLED` execution whose accepted quantity reached the intended quantity resolves `FULL_FILL`, a `PARTIALLY_FILLED` (or `FILLED` short of intended) positive-but-below-intended quantity resolves `PARTIAL_FILL`, and a terminal no-fill execution (`REJECTED`/`CANCELLED`/`EXPIRED` with zero accepted quantity) or the `NO_FILL_EXPIRED` admission disposition resolves `NO_FILL`; a deliberately unadmitted opportunity (`CAPACITY_REJECTED`, `CAPITAL_REJECTED`, `NOT_ACTIONABLE`, `UNSUPPORTED`, `DISABLED`, `EVIDENCE_INCOMPLETE`, `ALREADY_TRACKED`, `CANCELLED`, `DO_NOT_CHASE`, `UNRESOLVED`) is retained explicitly as the cash/no-trade population with no entry outcome and no path, never scored as a fill; an ambiguous or unsupported entry that the canonical vocabulary cannot unambiguously support (a `FILLED`/`PARTIALLY_FILLED` entry with no positive accepted quantity, a `PARTIALLY_FILLED` entry that in fact reached the intended quantity, a non-terminal state, an unknown execution-state token, or an expired admission that also accepted quantity) fails closed to `UNRESOLVED` with no fabricated fill and no favorable default; a malformed token fails closed per record without raising, so one bad field cannot abort a batch; it reuses the frozen F6 `EntryExecutionOutcome` enum rather than defining a second vocabulary; and it grants no trading, admission, reservation, execution or exchange authority

AC-024:
GIVEN:
the prospective F6 evidence contract and the committed canonical post-fill path evidence (the `paper_outcome.terminal.recorded` `exit_reason` and the `paper_protection.trigger.recorded` `trigger_type`)
WHEN:
the same opportunity's committed canonical evidence is projected onto the F6 conditional post-fill path family
THEN:
the projection resolves the exact frozen F6 path token `TARGET`/`STOP`/`TIMEOUT`/`RISK_EXIT` and never invents one, using a frozen canonical-to-F6 mapping table that only ever yields a member of the frozen F6 `PostFillPathOutcome` enum or an explicit non-label: `TARGET_2` resolves `TARGET`, `STOP` and `ENTRY_CANDLE_STOP` resolve `STOP`, `TIME_EXIT` resolves `TIMEOUT`, and an independently triggered risk exit (a protection `EMERGENCY` trigger) resolves `RISK_EXIT`; a `NO_FILL` entry carries NO post-fill path label (its path state is `INSUFFICIENT_EVIDENCE` and its path outcome is `None`) and never receives a fabricated `STOP` or `TIMEOUT`; an ambiguous within-bar path (`OHLC_GAP`) is `INCOMPLETE_COVERAGE` and is never a clean exact path label and never a negative; a non-market termination (`OPERATOR_OFF`), a genuinely `UNRESOLVED` reason, an unmapped exit reason and a terminal `UNRESOLVED` status all fail closed to `UNRESOLVED` with no path outcome; a `TIMEOUT` is scored from its recorded realized return and is never equated with a negative return; the entry-execution and post-fill path families are never mixed and are never collapsed into a single win probability; and the projection grants no trading, admission, reservation, execution or exchange authority

AC-025:
GIVEN:
a projected F6 label and the committed canonical economics
WHEN:
the realized net return and the simulation-fidelity grade are derived
THEN:
the realized net return is the dimensionless net-over-capital ratio (`net_pnl / capital_committed`, so `0.0125` means plus one point two five percent) and never a percent, a score, a probability or a scaled-by-one-hundred value; an opportunity that deployed no capital (a `NO_FILL` entry, or a deliberately unadmitted / cash-no-trade decision) has a realized net return of exactly zero and is never recorded as a loss; an absent or non-finite profit or capital figure leaves the return `UNRESOLVED` rather than guessed; and the fidelity grade is never inferred as `A` (no current canonical record attests exact-replay grade-A fidelity), native paper resolves `B` only when the canonical lineage attestation is exactly `COMPLETE`, and an ambiguous path (`INCOMPLETE_COVERAGE`), an incomplete lineage token, or an absent or unknown lineage value resolves `C` rather than silently resolving `B`; a terminal status outside the frozen canonical set (`CLOSED`/`CANCELLED`/`UNRESOLVED`) fails closed to `UNRESOLVED`

AC-026:
GIVEN:
the F6 prospective label-projection module
WHEN:
its purity, imports, durable effects and authority are audited
THEN:
it is a pure, deterministic function of its declared inputs (identical inputs yield an identical label) that reads no clock (`datetime.now`/`utcnow`/`time.time`), no environment, no filesystem, no database and no network, and opens nothing; it writes no store, table, JSONL stream or canonical event type and creates no second outcome truth, second outcome engine or parallel calibration spine (it is a read-only projection over the canonical writer's committed evidence, which remains the single outcome authority); it imports no exchange, order, Committee, AI or allocation surface and holds no trading, ranking, admission, reservation, sizing, execution or exchange authority; it reads no ordinal score, confidence or Committee rubric as a probability; the label-resolution and fidelity vocabulary it uses lives in the F6 vocabulary module `app/opip/contracts/forecast.py` (re-exported unchanged by `app/opip/forecast_evaluation.py`), so it is consumable without importing the F6 engine or evaluation module and without duplicating the vocabulary; and it changes no F3-F7 behaviour

AC-027:
GIVEN:
the prospective F6 evidence contract required before any calibrated forecast can legitimately be evaluated
WHEN:
the frozen contract document is inspected
THEN:
it exists as a repository artifact and freezes, before any outcome is observed, the FULL POPULATION (every eligible prospective evaluation unit, including selected, rejected, abstained, no-trade, no-fill, incomplete and unresolved cases), the CALIBRATION-ELIGIBLE SUBSET (only cases whose required outcome is cleanly resolved under the frozen label/fidelity policy), the retained UNRESOLVED/INCOMPLETE population (never silently recoded negative), the separately-reported LONG and SHORT populations, and the retained CASH/NO-TRADE population for matched comparison; it keeps the entry-execution family (`NO_FILL`/`PARTIAL_FILL`/`FULL_FILL`) and the post-fill path family (`TARGET`/`STOP`/`TIMEOUT`/`RISK_EXIT`) separate, states that a `NO_FILL` has no post-fill path label and that a `TIMEOUT` is an observed horizon expiry scored from its recorded return rather than an automatic negative; it freezes population inclusion, the feature/input schema, the evaluation instant, the evidence cutoff, the availability rule, the forecast horizon, the entry deadline, the FIRST_FILL post-fill path anchor, the label policy, the missingness policy, the fidelity policy, the fee policy, the correction policy, the maturity policy, the sealing policy, the training cutoff and the dataset-manifest identity, and explicitly records that none of these is chosen after viewing future outcomes; and it states the dependence-aware maturation gate and the `REAL_EVIDENCE_MATURATION_REQUIRED` disposition, without inventing a universal sample-count or calibration-pass threshold

AC-028:
GIVEN:
the future OWNER control-plane actions for the F6 evidence path (SHADOW evidence enablement) and the later R4-B2 controlled Paper-v2 cutover
WHEN:
the prepared owner packets are inspected
THEN:
both packets exist as repository artifacts and are PREPARED ONLY (they execute no authority change); each names the exact SHA, the exact modes to change, the exact protected-environment/control-plane mechanism, the prerequisites, the preflight commands, the rollback, the post-deploy verification, the authority-collision proof, the protection-health proof, the legacy-drain requirements and the current blockers; the SHADOW packet states that SHADOW runtime creates no new-entry authority, and the cutover packet states that activation is a distinct owner-controlled switch that is not set by any artifact here and that exactly one new-entry paper authority must exist throughout; and neither packet claims activation, changes a production mode, or grants funded/exchange/Committee authority

AC-029:
GIVEN:
the stale architecture status/recovery documents and the current accepted code
WHEN:
the status reconciliation is inspected
THEN:
the conformance ledger, the recovery roadmap and the current-architecture-status document are reconciled to the current accepted code baseline **without modifying architecture authority** (the v1.4.3 DOCX and `ARCHITECTURE.md` are untouched); each preserves its superseded statements identifiably as history (the prior wording and its baseline SHA remain present and are labelled historical); each uses the repository's existing status vocabulary consistently (`SHADOW` for an implemented non-authoritative module, `LEGACY_ACTIVE` for a live legacy authority, `PARTIAL` for a split implementation) rather than inventing new tokens; no document claims a runtime activation, a production mode change, a paper cutover or any funded/trading authority from code implementation alone; and the reconciliation records both the reconciled code baseline and the separately observed (not re-probed) production deploy baseline

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
Bounded-memory reader dedupe (Phase C hardening, same increment; OWNER-authorized contract amendment). The two read seams - the AC-015 committed-FeatureSnapshot reader (`app/opip/features/committed_snapshot_reader.py`) and the AC-019 feasibility-evidence reader (`app/opip/fev_evidence_reader.py`) - keep no process-lifetime dedupe set. Dedupe is owned by the exclusive canonical `(history_epoch, local_sequence)` cursor: the reader exposes the cursor and the consumer persists it, so a restart neither skips nor reclassifies committed evidence, while the reader's memory stays bounded however long the process lives. A caller that deliberately re-reads from an earlier cursor deterministically sees the same committed rows again; that is the explicit, tested contract, and the reader exposes no write, quarantine or mutation path. This amendment supersedes the earlier per-identity (AC-015) and per-payload-hash (AC-019) dedupe wording; every other property of those criteria is unchanged.

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
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_pre_acquisition_phases_are_independently_attributable
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_pre_acquisition_delay_rejects_first_wave
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_healthy_setup_within_envelope_reaches_acquisition
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_setup_bound_refresh_deadline_is_setup_budget_exhaustion
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_injected_refresh_deadline_is_ordinary_failure
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_ordinary_refresh_failure_is_not_budget_exhaustion
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_setup_bound_writer_deadline_is_setup_budget_exhaustion
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_unbound_writer_deadline_is_ordinary_failure
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_ordinary_publication_failure_preserves_behavior
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_continuity_deadline_is_setup_budget_exhaustion
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_setup_deadline_uses_actual_wave_bound
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_first_wave_boundary_equality_is_admissible
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_sqlite_progress_handler_interrupts_at_deadline
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_unrelated_sqlite_error_is_not_translated
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_restore_pilot_continuity_legacy_loaders_without_deadline
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_restore_pilot_continuity_forwards_deadline_when_supplied
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_checkpoint_row_processing_observes_deadline
AC-016 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_revision_ledger_row_processing_observes_deadline
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
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_event_is_separate_low_priority_accepted_class
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_payload_round_trips_exact_evidence
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_idempotency_binds_evaluation_and_exact_content
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_malformed_or_naive_instant_is_rejected
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_equivalent_instant_normalizes_to_same_identity
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_exact_replay_is_duplicate_ok_with_one_row
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_materially_different_evidence_is_not_collapsed
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_envelope_cannot_decouple_from_record
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_writer_rejects_wrong_priority_and_ops_handoff
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_validator_rejects_forged_or_tampered_payload
AC-018 -> tests/test_opip_r4_b2_feasibility_evidence_event.py::test_ac_018_restart_rehydrates_and_replays_as_duplicate
AC-019 -> tests/test_opip_r4_b2_feasibility_evidence_reader.py::test_ac_019_reader_is_read_only_and_reconstructs_exact_evidence
AC-019 -> tests/test_opip_r4_b2_feasibility_evidence_reader.py::test_ac_019_bounded_cursor_dedupes_and_preserves_order
AC-019 -> tests/test_opip_r4_b2_feasibility_evidence_reader.py::test_ac_019_malformed_record_is_rejected_without_aborting_batch
AC-019 -> tests/test_opip_r4_b2_feasibility_evidence_reader.py::test_ac_019_tampered_record_is_rejected
AC-019 -> tests/test_opip_r4_b2_feasibility_evidence_reader.py::test_ac_019_non_json_committed_row_is_rejected_without_aborting_batch
AC-019 -> tests/test_opip_r4_b2_feasibility_evidence_reader.py::test_ac_019_reader_holds_no_authority_and_never_mutates
AC-019 -> tests/test_opip_r4_b2_feasibility_evidence_reader.py::test_ac_019_empty_store_yields_empty_batch
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_absolute_deadline_cut_emits_a_durable_materialize_incomplete
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_absolute_writer_deadline_bounds_every_recv_of_a_multichunk_roundtrip
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_acquisition_concurrency_is_bounded
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_capture_client_declares_a_bounded_public_only_request_budget
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_config_bounds_are_within_the_minute_slot
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_consecutive_passes_commit_snapshots_60s_apart
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_cycle_whose_declared_maximum_cannot_fit_is_not_started
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_deadline_exhaustion_emits_a_durable_disposition_marker
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_deadline_stops_further_waves
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_f3_consumes_produced_snapshots_at_their_own_cutoff
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_failed_acquisition_emits_a_durable_disposition_marker
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_fifteen_minute_gap_does_not_count_as_persistence
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_first_attempt_rate_limit_wait_is_inside_the_declared_wave_bound
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_inner_timeout_is_container_side_and_no_outer_lock_release
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_inner_timeout_terminates_workload_and_releases_lock
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_materialization_admission_bounds_every_submit_of_a_cycle
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_materialization_is_sequential
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_materialization_reserve_is_retained_for_phase_b
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_missing_minute_is_incomplete_coverage
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_no_deadline_roundtrip_keeps_the_full_per_operation_timeout
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_no_historical_catch_up_is_materialized
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_phase_b_binds_one_absolute_deadline_shared_by_every_submit
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_process_lock_serializes_capture_bodies
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_production_publisher_resolves_one_client_and_keeps_the_timeout_clamp
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_replayed_cutoff_does_not_advance_persistence
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_retry_and_backoff_never_start_without_remaining_budget
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_run_capture_locked_skips_when_lock_held
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_slow_persistent_writer_cannot_exceed_the_materialize_deadline
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_stalled_request_cannot_consume_the_complete_pass_budget
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_two_consecutive_qualifying_evaluations_produce_claim
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_two_minute_passes_satisfy_the_runtime_verifier
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_unified_cycle_runs_none_of_the_capture_path
AC-020 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_zero_materialization_emits_a_durable_disposition_marker
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_all_reader_rejected_batch_advances_and_surfaces
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_all_rejected_batch_advances_and_persists_cursor
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_analytical_horizon_and_fresh_anchor_are_separately_acquired
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_anchor_beyond_the_max_source_age_fails_closed
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_budget_expires_after_first_row_persists_first_only
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_builder_rejects_source_cutoff_after_epoch
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_canonical_rejected_is_retryable_not_terminal
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_cold_start_initializes_at_head_and_does_not_refetch_history
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_configured_notional_is_required_and_bounded
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_cursor_advances_across_passes_and_does_not_reread
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_delayed_snapshot_is_not_stamped_with_current_market
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_deterministic_rejection_is_terminal_and_advances
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_direction_mismatch_rejected
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_duplicate_replay_is_counted_not_rejected
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_evidence_lineage_points_to_the_exact_source_snapshot
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_exact_replay_after_restart_produces_no_duplicate_and_advances
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_exact_shadow_gate_matrix
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_execution_invalid_stays_present_and_f5_vetoes
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_feature_bus_lock_does_not_block_feasibility
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_freshness_anchor_holds_anywhere_within_the_hour
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_freshness_dispositions_are_flushed_durable_markers
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_future_visible_snapshot_fails_closed_without_advancing
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_has_a_bounded_non_overlapping_scheduler_entry
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_hourly_cutoff_cannot_satisfy_the_verifier_but_the_fresh_anchor_does
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_in_epoch_live_reads_are_admitted_with_their_own_cutoff
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_point_in_time_audit_refuses_a_post_epoch_input
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_post_epoch_live_reads_are_not_published_as_point_in_time_support
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_locks_are_structurally_distinct
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_malformed_then_valid_row_processes_valid_and_advances
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_market_reject_stays_present_and_f5_vetoes
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_missing_builder_records_unavailable_and_does_not_advance
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_module_entrypoint_runs_and_reports_inert
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_no_protected_cycle_dependency
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_out_of_epoch_source_cutoff_fails_closed
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_pending_anchor_retains_the_cursor_then_publishes_once_it_publishes
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_point_in_time_violation_is_terminal_and_never_published
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_present_veto_reaches_full_f5_decision
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_reader_seam_exposes_per_record_provenance
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_real_builder_builds_genuine_contemporaneous_evidence
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_records_one_genuine_record_per_contemporaneous_snapshot
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_retryable_record_halts_cursor_and_is_retried_next_pass
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_run_capture_locked_uses_own_lock_identity
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_scheduler_comment_names_the_feasibility_lock
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_scheduler_reconciliation_installs_capture_once_and_can_roll_back
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_stale_freshness_anchor_fails_closed_without_synthetic_freshness
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_transient_builder_exception_does_not_advance_cursor
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_unavailable_evidence_is_insufficient_not_veto
AC-021 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_unsupported_direction_rejected
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_margin_discovery_uses_bitnomial_venue_and_marks_btnl
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_leverage_is_bounded_by_account_ceiling
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_pair_absent_from_venue_is_ineligible_not_fabricated
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_execution_uses_btnl_book_not_spot
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_ineligible_pair_yields_unavailable_execution_not_spot_as_btnl
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_btnl_book_failure_is_unavailable_present_evidence
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_short_execution_carries_btnl_provenance_for_f5
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_invalid_status_sort_order_constant_is_importable
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_point_in_time_provenance_round_trips_epoch_invariance
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_producer_dispatches_short_direction
AC-022 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_short_margin_venue_provenance_survives_the_durable_audit
AC-023 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_023_full_and_partial_fill_resolve
AC-023 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_023_no_fill_variants_resolve
AC-023 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_023_non_admission_is_cash_no_trade
AC-023 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_023_ambiguous_entry_fails_closed
AC-024 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_024_path_family_maps_exactly
AC-024 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_024_no_fill_has_no_path_label
AC-024 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_024_unresolved_and_incomplete_are_never_negative
AC-024 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_024_no_family_token_is_invented
AC-025 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_025_fidelity_is_never_inferred_as_a
AC-025 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_025_realized_return_is_dimensionless_and_zero_without_capital
AC-025 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_025_timeout_scores_its_recorded_return
AC-026 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_026_projection_is_pure_and_deterministic
AC-026 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_026_malformed_token_fails_closed_per_record
AC-026 -> tests/test_opip_r4_b2_f6_label_projection.py::test_ac_026_no_favorable_label_without_supporting_evidence
AC-027 -> tests/test_opip_r4_b2_f6_prospective_contract.py::test_ac_027_contract_freezes_population_and_policies
AC-028 -> tests/test_opip_r4_b2_f6_prospective_contract.py::test_ac_028_owner_packets_are_prepared_only
AC-029 -> tests/test_opip_r4_b2_status_reconciliation.py::test_ac_029_status_docs_are_reconciled_without_activation
AC-029 -> tests/test_opip_r4_b2_status_reconciliation.py::test_ac_029_architecture_authority_is_untouched
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
AC-016 -> OHM-Trade-Agent-v1/app/jobs/run_feature_bus_pilot.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/features/checkpoint_store.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/features/revision_ledger.py
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
AC-018 -> OHM-Trade-Agent-v1/app/opip/fev_evidence_event.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/canonical/models.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_evidence_event.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/fev_evidence_reader.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-019 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_evidence_reader.py
AC-020 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-020 -> OHM-Trade-Agent-v1/app/opip/canonical/client.py
AC-020 -> OHM-Trade-Agent-v1/app/opip/canonical/protocol.py
AC-020 -> OHM-Trade-Agent-v1/app/opip/features/pipeline.py
AC-020 -> OHM-Trade-Agent-v1/app/opip/features/publisher.py
AC-020 -> OHM-Trade-Agent-v1/app/services/kraken_transport.py
AC-020 -> OHM-Trade-Agent-v1/app/services/opip_feature_bus_market_source.py
AC-020 -> OHM-Trade-Agent-v1/app/exchanges/kraken.py
AC-020 -> OHM-Trade-Agent-v1/app/core/config.py
AC-020 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-feature-bus-capture
AC-020 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-020 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_cadence.py
AC-020 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_capture.py
AC-021 -> OHM-Trade-Agent-v1/app/jobs/capture_feasibility_evidence_shadow.py
AC-021 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-021 -> OHM-Trade-Agent-v1/app/services/kraken_transport.py
AC-021 -> OHM-Trade-Agent-v1/app/services/opip_feature_bus_market_source.py
AC-021 -> OHM-Trade-Agent-v1/app/exchanges/kraken.py
AC-021 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-021 -> OHM-Trade-Agent-v1/app/opip/features/committed_snapshot_reader.py
AC-021 -> OHM-Trade-Agent-v1/app/core/config.py
AC-021 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-feasibility-evidence-capture
AC-021 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-021 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-021 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_producer.py
AC-021 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_shadow_activation_v1.py
AC-022 -> OHM-Trade-Agent-v1/app/jobs/capture_feasibility_evidence_shadow.py
AC-022 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_short.py
AC-023 -> OHM-Trade-Agent-v1/app/opip/forecast_labels.py
AC-023 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_f6_label_projection.py
AC-024 -> OHM-Trade-Agent-v1/app/opip/forecast_labels.py
AC-024 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_f6_label_projection.py
AC-025 -> OHM-Trade-Agent-v1/app/opip/forecast_labels.py
AC-025 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_f6_label_projection.py
AC-026 -> OHM-Trade-Agent-v1/app/opip/forecast_labels.py
AC-026 -> OHM-Trade-Agent-v1/app/opip/contracts/forecast.py
AC-026 -> OHM-Trade-Agent-v1/app/opip/contracts/__init__.py
AC-026 -> OHM-Trade-Agent-v1/app/opip/forecast_evaluation.py
AC-026 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_f6_label_projection.py
AC-027 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F6_PROSPECTIVE_EVIDENCE_CONTRACT.md
AC-027 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_f6_prospective_contract.py
AC-027 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-028 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md
AC-028 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_f6_prospective_contract.py
AC-028 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-029 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md
AC-029 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_RECOVERY_ROADMAP.md
AC-029 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-029 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_status_reconciliation.py
AC-029 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md

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
