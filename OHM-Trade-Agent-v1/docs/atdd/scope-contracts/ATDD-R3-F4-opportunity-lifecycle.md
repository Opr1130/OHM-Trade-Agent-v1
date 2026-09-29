INCREMENT:
ATDD-R3-F4-opportunity-lifecycle

OWNER-APPROVED INTENT:
Freeze the minimum architecture-faithful contract for the second R3 slice after F3: the F4 Opportunity Lifecycle over detector claims. This increment defines and freezes the acceptance criteria and the authorized path set for a future deterministic, replayable, pure opportunity-lifecycle transition surface that consumes already-produced `DetectorClaim` evidence and manages episode identity, deduplication, deferral, deadline, expiry, and terminal reason:

```text
advance(
    claim: DetectorClaim,
    prior_episode: OpportunityEpisode | None,
    evaluation_time: datetime,
) -> tuple[OpportunityEpisode, list[LifecycleEvent]]
```

The three-argument shape above is the **proposed** pure core for OWNER review, mirroring the F3 pattern (versioned lifecycle policy is a deterministic code artifact of the implementation, not a fourth argument and not a hidden input). Architecture v1.4.3 names the `OpportunityEpisode` entity and the deferral/deadline/expiry rules but does **not** yet pin this exact Python signature; freezing the behavioural contract does not invent unratified hash formulas, deadline durations, state-token spellings, or terminal-reason taxonomies (see OWNER DECISIONS REQUIRED).

This increment is CONTRACT-FREEZE ONLY / SHADOW / evidence-only. It grants no production authority, no admission authority, no paper authority, no order authority, no risk authority and no Feature Bus activation. It does not wire any lifecycle into `run_cycle`. It authorizes this scope contract, the active-increment pointer and contract-stage acceptance-test skeletons only. Opportunity-lifecycle application code — `OpportunityEpisode` runtime types, the writer, DB migration, consumer migration and cutover — is a later, OWNER-approved implementation increment.

Frozen F4 semantics that this contract encodes:

1. Episode identity is deterministic and must not derive from wall clock, UUID, process identity, invocation order or retry count. Existing ratified identity primitives are inventoried below; inventing a new hash formula is prohibited in this freeze.
2. Deduplication is at-least-once safe: retries, restarts, deadline-extension attempts and already-terminal episodes must not create duplicate episodes or extra lifecycle work.
3. Claim→episode lineage reuses claim, snapshot, instrument, detector and policy/cutoff identities already carried by `DetectorClaim` / sealed snapshot evidence. F4 consumes `DetectorClaim`; it MUST NOT reinterpret F3 thresholds, hysteresis, debounce, persistence or transition evidence.
4. Lifecycle state covers at least active/open, deferred and terminal concepts. Exact token spellings that are not architecture-authorized remain provisional (OWNER DECISION REQUIRED).
5. Deferral is an explicit opportunity-lifecycle disposition. It is NOT detector `DORMANT`, detector reset, missing evidence, F5+ feasibility/forecast/selector, paper/trade lifecycle, or Committee advisory state.
6. Every deferred episode carries an explicit deadline bounded by validity horizon. No numeric duration is invented here (OWNER DECISION REQUIRED if no authorized duration exists).
7. Expiry is an explicit terminal disposition. Silent resume from duplicate delivery, replay, restart or stale state is forbidden. A new lifecycle requires a new eligible evaluation under future re-entry policy.
8. Terminal reasons use a canonical F4 episode vocabulary. Funnel / scan `ReasonCode` values are scan outcomes, not episode deadlines (conformance ledger). Do not blend with forecast TIMEOUT, order/fill, TARGET/STOP, RISK_EXIT, protection or learning vocabularies.
9. Terminal is terminal. A new lifecycle needs an eligible new claim under a future OWNER-approved re-entry policy; this freeze does not invent that policy.
10. The pure transition core takes claim + prior episode + explicit evaluation time + versioned lifecycle policy → next episode state and events. No network, disk, database, environment, hidden clock, random or global mutable state inside the pure core.
11. Replay: identical inputs produce identical results.
12. F3 boundary: no F3 contract, detector semantics or implementation changes; no reinterpretation of F3 evidence.
13. F5+ isolation: no feasibility, forecast, selector, paper, funded or Committee authority.
14. Current vs target authority: CURRENT = legacy fragmented clocks; TARGET = one canonical lifecycle over detector claims; CONTRACT STAGE = no authority transfer; initial implementation remains shadow unless later OWNER cutover.
15. Consumer census is recorded from a whole-repo search (below); it is evidence for future cutover, not authorization to migrate.
16. Cutover / retirement: no retirement or deletion now; freeze prerequisites and delete-gate requirements only.
17. No fake implementation: acceptance skeletons share one skip reason; a guard test proves AC count, contract mapping and no smuggled runtime.

TARGET INTERFACE INPUTS (current repository truth, not a new identity contract):

- F3 `DetectorClaim` (`app/opip/contracts/detector.py`): claim identity is already a pure function of detector family/version, policy version, instrument, transition and sealed snapshot evidence; `episode_id` is forced `None` by F3. F4 is the owner that may later bind an episode identity onto claims without changing F3 transition semantics.
- Sealed snapshot / feature lineage already available to reuse: `snapshot_id`, `content_hash()`, `detector_input_fingerprint` (`DETIN`), `instrument_version_id`, `venue_instrument_id`, `evaluation_cutoff`, detector/policy version tokens.
- Legacy scan episode identity `canonical_episode_id(schema_version, cohort_id, symbol)` in `app/opip/contracts/episode_snapshot.py` / `app/services/canonical_episode_capture.py` is the **scan-cohort** episode id (`EP:` digest). It is ratified for canonical episode-snapshot capture in the legacy scan path. It is **not** ratified as the F4 OpportunityEpisode identity over `DetectorClaim` (different inputs: cohort/symbol vs claim/snapshot/detector evidence).
- Architecture entity `OpportunityEpisode`: "Episode identity, lifecycle, defer deadline, terminal reason" (v1.4.3 §6). No fixture pins the hash formula or state/terminal enums for F4.
- Validity horizon: architecture binds deferred deadlines to "validity horizon" (v1.4.3 §8 / D_DETECTOR_CONTRACT). Validity horizon itself is an F6 forecast concern in the feature table; no F4-authorized numeric deadline exists in architecture.

AUTHORITY / REPLACEMENT STATEMENT:

- Current authority: fragmented legacy clocks — live walk `app/jobs/scan_opportunities.py`; shadow funnel `app/opip/decision/funnel.py` + `store.py`; other clocks `entry_watch_queue.py`, `price_movement_radar.py`, `monitor_pending_setups.py` / pending-setup registry, `signal_quality_phase2.py` `MoveEpisode`, `app/opip/early/timing_ledger.py`. Conformance ledger: CURRENT_OWNER = `scan_opportunities` for live admission; decision engine is not authoritative.
- Target authority: one opportunity lifecycle over detector claims.
- Contract stage: no authority transfer. Shadow/evidence only until a later OWNER cutover increment.
- Cutover gate (recorded, not executed): one identity, one terminal-reason vocabulary, consumer census of watch/radar/pending (and the additional consumers listed below).
- Retirement candidate: parallel episode clocks after that owner exists.
- Delete gate: each clock names its replacement and a stop time. Not authorized now.

CONSUMER CENSUS (repo search at base SHA `3068fce5bd3cdfa4fab567283a768a3472223b38`; evidence only):

Known overlapping clocks (conformance ledger + verified present):

| Clock / owner | Path | Role (as-built) |
| --- | --- | --- |
| Live scan walk | `app/jobs/scan_opportunities.py` | Live admission walk; mints `canonical_episode_id`; drives alerts/paper routing |
| Shadow funnel | `app/opip/decision/funnel.py`, `store.py`, `observer.py` | Shadow qualification funnel; scan `ReasonCode` terminals |
| Entry watch | `app/services/entry_watch_queue.py`, `entry_watch_recheck.py` | Watch queue with `DEFAULT_TTL_SECONDS = 30 * 60` |
| Price movement radar | `app/services/price_movement_radar.py` (+ notifier/learning) | Movement stages including `EXPIRED`; config `price_movement_expiry_hours` default 12 |
| Pending setups | `app/jobs/monitor_pending_setups.py`, `pending_setup_registry.py`, `pending_setup_monitor.py`, `pending_setup_notifier.py`, `confirm_entry.py`, `chief_alert_notifier.py` | Pending-setup TTL (`PENDING_SETUP_TTL_HOURS` / `paper_trade_pending_ttl_hours` default 24) |
| Signal-quality MoveEpisode | `app/services/signal_quality_phase2.py`, `signal_timing_v2.py` | Offline/research explosive-run episodes; not F4 OpportunityEpisode |
| Early timing ledger | `app/opip/early/timing_ledger.py` | Early-watch timing evidence |

Additional consumers / lineage carriers discovered beyond the ledger shortlist (non-exhaustive of every `episode_id` mention; focused on lifecycle ownership or durable consumption):

| Consumer | Path | Notes |
| --- | --- | --- |
| Canonical episode capture | `app/services/canonical_episode_capture.py`, `app/opip/contracts/episode_snapshot.py` | Produces `EP:` / `SNAP:` for scan cohorts |
| Opportunity accountability | `app/services/opportunity_accountability.py`, `app/jobs/build_opportunity_accountability.py` | Consumes funnel terminal reasons + episode ids |
| Paper / Freqtrade lineage | `paper_trade_engine.py`, `paper_trade_registry.py`, `paper_outcome_outbox.py`, `freqtrade_signal_bridge.py` | Join on legacy `episode_id` |
| ML / phase3c / learning | `opip_ml_evidence_capture.py`, `build_phase3c_forward_outcomes.py`, `phase3c_outcomes.py`, `app/opip/learning/linkage.py` | Outcome/lineage join |
| Decision telemetry / shadow | `decision_telemetry.py`, `shadow_decision_capture.py`, `p1_shadow_outbox.py` | Evidence mirrors |
| Dashboard / cockpit / analytics | `dashboard_read_model.py`, `app/opip/cockpit/ledger.py`, `operations_analytics.py`, data-platform read models | Read projections |
| Cycle orchestration | `app/jobs/run_cycle.py` | Schedules pending-setup monitor among other jobs |
| Design-only (absent code) | `OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md` proposes `app/opip/opportunity/` | Package does **not** exist; must not become a second spine |

OVERLAPPING CLOCKS / TTL EVIDENCE (legacy only; not F4 policy):

| Source | Observed duration / terminal | Authority for F4? |
| --- | --- | --- |
| `entry_watch_queue.DEFAULT_TTL_SECONDS` | 1800 s (30 min) | No — watch-queue TTL |
| `price_movement_expiry_hours` | default 12 h | No — radar expiry |
| `paper_trade_pending_ttl_hours` / `PENDING_SETUP_TTL_HOURS` | default 24 h | No — pending-setup TTL |
| Funnel `ReasonCode` | scan/gate/AI outcomes | No — scan terminals, not episode deadlines |
| Architecture "validity horizon" | qualitative bound; F6 owns horizon as a forecast concept | Bounds deferred deadlines; no numeric F4 duration ratified |

PROPOSED IMPLEMENTATION MAP FOR THE DEFERRED F4 IMPLEMENTATION INCREMENT (documentation only; this contract-freeze increment authorizes no application path and creates no lifecycle runtime):

- `OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py` — typed `OpportunityEpisode`, lifecycle events, provisional state/terminal vocabularies once OWNER-ratified.
- `OHM-Trade-Agent-v1/app/opip/opportunity/lifecycle.py` — pure `advance(claim, prior_episode, evaluation_time)` core (package name subject to OWNER confirmation that it does not fork Signal Quality v2 into a second spine).
- `OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py` — acceptance and unit tests; contract-stage skeletons in this increment are completed here rather than replaced by a second test file.

This increment remains CONTRACT-FREEZE ONLY. Proposed application paths are advisory for OWNER review only and are deliberately NOT added to the active `IMPLEMENTATION MAP`. A separate, OWNER-approved ATDD implementation increment will authorize them.

ARCHITECTURE REFERENCES:
- O'Pip Profit Intelligence Platform Architecture v1.4.3: repository authority copy `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`, and its extraction `docs/architecture/v1.4.3/ARCHITECTURE.md`.
- v1.4.3 F4 Opportunity Lifecycle: deduplicate episodes; manage deferrals, deadlines, expiry, and terminal reasons; owned artifact = episode lifecycle.
- v1.4.3 §6 data model: `OpportunityEpisode` — episode identity, lifecycle, defer deadline, terminal reason; `DetectorClaim` carries episode, snapshot, detector version, phase, reasons.
- v1.4.3 §8 detector and opportunity contract: deferred opportunities carry a deadline bounded by validity horizon and terminate with an explicit reason; expired claims cannot resume without a new evaluation.
- v1.4.3 §3 canonical evidence / point-in-time: evaluation time, cutoff, watermark, versions, content hash.
- docs/architecture/v1.2/D_DETECTOR_CONTRACT.md: ownership table assigns episode identity, deferral deadline and terminal reason to opportunity lifecycle; deferral/expiry clauses match §8.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md F4 row: fragmented current implementation; target = one lifecycle over detector claims; funnel terminals are scan outcomes not episode deadlines; cutover/retirement/delete gates recorded.
- docs/architecture/OPIP_RECOVERY_ROADMAP.md: R3 order is F3 then opportunity lifecycle; fold watch/radar/pending/signal-quality clocks only after consumer census; do not add `app/opip/opportunity/` beside the bus as a second architecture.
- docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md and ATDD-R3-F3-ignition-implementation.md: F3 frozen; `episode_id` remains null; F4 owns episode lifecycle; this increment must not modify F3.
- docs/atdd/scope-contracts/ATDD-000-scope-control.md: ATDD is subordinate to approved architecture.

OWNER DECISIONS REQUIRED (discovery answers 1–12; unresolved items stay open — no invented policy):

1. Episode identity formula over `DetectorClaim`?
   Evidence: F3 forces `episode_id=None`; legacy `canonical_episode_id` is `schema_version|cohort_id|symbol`; architecture names episode identity without a claim-based digest.
   Status: OWNER DECISION REQUIRED. Safest options without inventing policy: (a) ratify a claim-lineage digest from already-frozen claim/snapshot/instrument/detector/policy/cutoff fields; (b) explicitly reuse/adapt `EP:` only with a written mapping from claim→cohort (likely insufficient); (c) defer minting until a dedicated identity ADR. This freeze records the requirement for determinism and forbids wall-clock/UUID/process/order/retry inputs.

2. Deduplication key / at-least-once rule?
   Evidence: architecture requires deduplicate episodes; F3 claim identity is already transition+evidence keyed.
   Status: Partially answered — behavioural AC frozen (no duplicate episodes / restart / deadline-extend / clear-terminal / extra work). Exact key composition follows identity decision (Q1).

3. Required lineage fields from claim→episode?
   Evidence: DetectorClaim + FeatureSnapshot already carry snapshot, instrument, detector/policy, cutoff identities.
   Status: Answered for contract purposes — reuse those; do not reinterpret F3 thresholds/evidence.

4. Lifecycle state token vocabulary?
   Evidence: architecture requires lifecycle with deferral and terminal; Signal Quality v2 design lists OBSERVED/MONITORING/QUALIFYING/ACTIONABLE/REJECTED/EXPIRED but is non-authoritative and its package is absent.
   Status: OWNER DECISION REQUIRED for exact tokens. This freeze requires at least the concepts active/open, deferred and terminal; provisional labels `ACTIVE`, `DEFERRED`, `TERMINAL` are documentation placeholders only until OWNER ratification.

5. What counts as deferral vs non-deferral?
   Evidence: D_DETECTOR_CONTRACT / §8 assign deferral to opportunity lifecycle; detector phases are DORMANT/IGNITION only.
   Status: Answered — deferral is explicit F4 disposition; not detector DORMANT/reset, not missing evidence, not F5+/forecast/selector/paper/trade/Committee.

6. Deferred deadline duration / validity-horizon binding?
   Evidence: §8 requires deadline bounded by validity horizon; F6 owns validity horizon as forecast concept; legacy TTLs 30m/12h/24h are other clocks.
   Status: OWNER DECISION REQUIRED. Safest options: (a) bind deadline to an F6-supplied horizon once F6 exists, with F4 only enforcing presence+bound check; (b) OWNER-ratify a temporary shadow constant with explicit expiry of that ratification; (c) fail closed on missing horizon rather than inventing a number. This freeze forbids inventing a number.

7. Expiry / silent-resume prohibition?
   Evidence: §8 "Expired claims cannot resume without a new evaluation."
   Status: Answered — expiry is explicit terminal; no silent resume from duplicate/replay/restart/stale; new lifecycle needs new evaluation.

8. Canonical F4 terminal-reason vocabulary?
   Evidence: conformance ledger warns funnel terminals are scan outcomes; architecture requires explicit terminal reason; no F4 enum is checked in.
   Status: OWNER DECISION REQUIRED for the token set. This freeze requires a distinct F4 vocabulary and forbids blending with scan/feasibility/forecast/selector/order/fill/TARGET/STOP/TIMEOUT/RISK_EXIT/protection/learning codes. A minimum conceptual terminal for deadline expiry must exist once OWNER names it.

9. Re-entry policy after terminal?
   Evidence: new evaluation required; no ratified re-entry eligibility matrix.
   Status: OWNER DECISION REQUIRED for eligibility rules. This freeze only requires terminality and that a new lifecycle needs an eligible new claim under a future policy.

10. Exact pure `advance(...)` signature and policy-version carriage?
    Evidence: F3 froze `evaluate(snapshot, prior_state, evaluation_time)`; architecture does not pin an F4 callable.
    Status: OWNER DECISION REQUIRED to ratify or amend the proposed three-argument surface. Behavioural purity/determinism/replay are frozen regardless.

11. Consumer census completeness / migration order?
    Evidence: census table above from whole-repo search at the pinned SHA.
    Status: Answered as contract-stage census evidence. OWNER must still approve any migration/cutover order in a later increment; this freeze does not migrate consumers.

12. Cutover / retirement / delete gates?
    Evidence: conformance ledger cutover = one identity + one terminal vocabulary + consumer census; delete gate = each clock names replacement + stop time; recovery roadmap forbids premature `app/opip/opportunity/` second spine.
    Status: Answered as recorded prerequisites — no retirement, no deletion, no authority transfer in this increment.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
detector-claim evidence, instrument/snapshot/detector/policy/cutoff lineage already present on the claim and sealed snapshot, and a future F4 episode-identity function once OWNER-ratified
WHEN:
an opportunity episode identity is derived for lifecycle use
THEN:
the identity is a deterministic function of ratified inputs only, and it never derives from wall-clock time, UUID, process identity, invocation order or retry count; until OWNER ratifies the formula, the implementation increment must not invent a substitute hash

AC-002:
GIVEN:
at-least-once delivery of the same eligible claim against an existing active, deferred or terminal episode, including restart and deadline-extension attempts
WHEN:
the lifecycle transition surface is applied
THEN:
no duplicate episode is created, a clear terminal episode is not revived by the duplicate, and no extra lifecycle work is performed beyond the idempotent recorded outcome

AC-003:
GIVEN:
a DetectorClaim produced by the frozen F3 IGNITION detector together with its sealed snapshot lineage
WHEN:
F4 binds or advances an episode from that claim
THEN:
the episode reuses claim, snapshot, instrument, detector and policy/cutoff identities already present, and F4 does not reinterpret F3 thresholds, hysteresis, debounce, persistence timers or transition evidence

AC-004:
GIVEN:
an opportunity episode under F4 ownership
WHEN:
its lifecycle state is inspected
THEN:
the state is exactly one of the OWNER-ratified lifecycle vocabulary tokens covering at least the concepts active/open, deferred and terminal, and detector phases such as DORMANT/IGNITION are never used as episode lifecycle states

AC-005:
GIVEN:
an explicit F4 deferral decision for an eligible claim or active episode
WHEN:
the resulting episode state is inspected
THEN:
deferral is recorded as an opportunity-lifecycle disposition with an explicit deadline, and it is not equivalent to detector DORMANT, detector reset, missing evidence, F5+ feasibility/forecast/selector outcomes, paper/trade lifecycle states or Committee advisory state

AC-006:
GIVEN:
a deferred opportunity episode
WHEN:
its deadline fields are validated against architecture
THEN:
a deadline is present, the deadline is bounded by the validity horizon once that horizon is an authorized input, and no numeric deadline duration invented by this contract is treated as architecture law

AC-007:
GIVEN:
a deferred episode whose deadline has been reached under an explicit evaluation time
WHEN:
the lifecycle transition surface evaluates expiry
THEN:
the episode terminates with an explicit expiry terminal reason, and it cannot silently resume from duplicate delivery, replay, restart or stale prior state; a new lifecycle requires a new eligible evaluation

AC-008:
GIVEN:
a terminal opportunity episode
WHEN:
its terminal reason is inspected
THEN:
the reason is drawn from the canonical F4 episode terminal vocabulary (once OWNER-ratified), and it is not a funnel/scan ReasonCode, forecast TIMEOUT, order/fill disposition, TARGET/STOP, RISK_EXIT, protection or learning code

AC-009:
GIVEN:
an episode that has already reached a terminal state
WHEN:
further claims or retries arrive before a new eligible lifecycle is authorized
THEN:
terminal remains terminal, and opening a new lifecycle requires an eligible new claim under a future OWNER-approved re-entry policy rather than mutating the terminal episode back to active or deferred

AC-010:
GIVEN:
a DetectorClaim, a prior OpportunityEpisode or explicit absence, an explicit evaluation_time and a versioned lifecycle policy
WHEN:
the pure F4 transition core runs
THEN:
it performs no network, disk, database, environment, hidden-clock, random or global-mutable-state access, and it observes no input outside the supplied claim, prior episode, explicit evaluation_time and the versioned policy bound to the implementation

AC-011:
GIVEN:
identical claim, prior episode, evaluation_time and lifecycle policy/version inputs
WHEN:
the pure transition core runs more than once, including from a fresh process
THEN:
the next episode state and emitted lifecycle events are structurally and field-equivalent on every run

AC-012:
GIVEN:
the frozen F3 IGNITION detector contracts and implementation
WHEN:
this F4 contract-freeze increment is applied
THEN:
no F3 detector semantics, thresholds, claim identity rules, DetectorState/DetectorClaim schemas or F3 acceptance contracts are modified or reinterpreted by F4

AC-013:
GIVEN:
the completed F4 contract-freeze increment and any later shadow lifecycle implementation authorized separately
WHEN:
repository and runtime activation surfaces are inspected
THEN:
F5 feasibility, F6 forecast, F7 selector, Paper-v2/funded trading, Committee authority and Feature Bus mode are unchanged by F4, and F4 grants none of those authorities

AC-014:
GIVEN:
current production runtime versus the F4 target stated by architecture and the conformance ledger
WHEN:
authority is described for this increment
THEN:
CURRENT authority remains the legacy fragmented clocks, TARGET authority is one canonical lifecycle over detector claims, and this CONTRACT STAGE performs no authority transfer; any initial implementation remains shadow unless a later OWNER cutover explicitly transfers authority

AC-015:
GIVEN:
the consumer census recorded in this contract from a whole-repository search at the pinned base SHA
WHEN:
future cutover or retirement work is planned
THEN:
that work must account for the census owners and consumers (scan, funnel/store, entry watch, radar, pending setups, MoveEpisode/signal-quality, timing ledger, and the additional consumers listed) and must not assume the ledger shortlist alone is complete

AC-016:
GIVEN:
the F4 cutover, retirement and delete gates recorded by the conformance ledger and recovery roadmap
WHEN:
this contract-freeze increment completes
THEN:
no legacy clock is retired or deleted, no consumer is force-migrated, and the freeze only records prerequisites (one identity, one terminal vocabulary, consumer census, named replacement and stop time per clock) for a later OWNER-approved cutover

AC-017:
GIVEN:
this contract-freeze increment's acceptance suite
WHEN:
the suite is collected
THEN:
every AC-001..AC-017 acceptance body that awaits runtime is an explicit non-executing skeleton sharing one stated skip reason that defers F4 runtime to a separate OWNER-approved implementation increment, and no skeleton fabricates lifecycle results or smuggles F4 application runtime

EXPLICITLY OUT OF SCOPE:
- F4 opportunity lifecycle runtime / writer / DB migration / consumer migration
- Inventing episode-identity hashes, deadline durations, state-token spellings or terminal-reason taxonomies beyond provisional documentation placeholders
- F3 detector contract or implementation changes
- F5 feasibility integration
- F6 forecast engine
- F7 economic or portfolio selector
- run_cycle integration of any kind
- scan_opportunities modifications
- Feature Bus activation or OPIP_FEATURE_BUS_MODE changes
- Paper v1, Paper v2, or Freqtrade changes
- protection, risk, sizing, or execution changes
- dashboard work as a product change
- Committee work
- alert changes
- deployment
- legacy retirement or deletion
- funded trading
- changing any architecture document, including the v1.4.3 DOCX
- changing R2 evidence or its contracts
- modifying the ATDD checker, the scope-control contract, workflows, pyproject.toml, or any existing F3 test
- weakening any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off`.
- Production `run_cycle` does not call an F4 lifecycle and does not call any F3→F4 wiring.
- No `OpportunityEpisode` runtime, writer, DB migration or consumer migration is created by this increment.
- F3 detector contracts and implementation remain frozen and unmodified.
- The canonical writer remains the single domain write path, and this increment writes no canonical evidence.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled.
- Paper execution stays isolated from funded order endpoints.
- Committee authority is unchanged and remains shadow-only.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- No merge, deploy, activation, cutover, or implementation change occurs under this increment.
- A normal push of the contract-only review branch is permitted solely for OWNER review, and OWNER review and merge of this contract-only governance PR are permitted.
- Reviewing, pushing or merging this contract grants no implementation or runtime authority, and does not authorize F4 implementation.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_001_episode_identity_is_deterministic
AC-002 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_002_deduplication_is_at_least_once_safe
AC-003 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_003_claim_episode_lineage_reuses_identities
AC-004 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_004_lifecycle_state_vocabulary
AC-005 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_005_deferral_is_explicit_and_not_detector_dormant
AC-006 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_006_deferred_deadlines_are_bounded
AC-007 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_007_expiry_is_explicit_terminal
AC-008 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_008_terminal_reasons_are_f4_vocabulary
AC-009 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_009_terminality_requires_new_lifecycle
AC-010 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_010_transition_core_is_pure
AC-011 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_011_replay_is_deterministic
AC-012 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_012_f3_boundary_unchanged
AC-013 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_013_f5_plus_isolation
AC-014 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_014_current_vs_target_authority
AC-015 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_015_consumer_census_recorded
AC-016 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_016_cutover_retirement_boundary
AC-017 -> tests/test_opip_r3_f4_opportunity_lifecycle.py::test_ac_017_no_fake_implementation

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-017 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py

DEFERRED DISCOVERIES:
- Exact OWNER-ratified episode-identity digest over DetectorClaim (Q1).
- Exact OWNER-ratified lifecycle state tokens and F4 terminal-reason enum (Q4, Q8).
- Numeric deferred deadline / validity-horizon binding source (Q6); may require F6.
- Re-entry eligibility matrix after terminal (Q9).
- Ratification of the proposed `advance(claim, prior_episode, evaluation_time)` signature (Q10).
- Persistence/writer schema for OpportunityEpisode and LifecycleEvent (implementation increment).
- Consumer migration order and stop-writing timestamps per overlapping clock (cutover increment).
- Whether package path `app/opip/opportunity/` is acceptable given recovery-roadmap warning against a Signal Quality second spine (OWNER naming decision).
- F5 through F7 remain future increments.

UNAPPROVED SCOPE CHANGES:
NONE
