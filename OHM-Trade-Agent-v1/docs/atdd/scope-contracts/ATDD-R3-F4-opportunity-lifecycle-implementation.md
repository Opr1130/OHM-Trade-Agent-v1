INCREMENT:
ATDD-R3-F4-opportunity-lifecycle-implementation

OWNER-APPROVED INTENT:
OWNER-authorized implementation increment for the second R3 slice: the pure, deterministic, replayable Opportunity Lifecycle transition surface over frozen F3 ``DetectorClaim`` evidence.

NORMATIVE ACCEPTANCE SOURCE: `ATDD-R3-F4-opportunity-lifecycle`. That frozen contract remains the normative acceptance source for this work. This implementation increment adds the authorized application paths and restates, without weakening, the acceptance criteria it implements. It does not rewrite, re-interpret or relax the frozen contract, and it does not modify `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md`, the R3 F3 contracts, or any architecture document.

PARENT FROZEN CONTRACT: `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md`.

ARCHITECTURE AUTHORITY: O'Pip Profit Intelligence Platform Architecture v1.4.3, repository authority copy `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`, and its extraction `docs/architecture/v1.4.3/ARCHITECTURE.md`. This increment changes no architecture document.

BASE SHA: `14811e7d53774d40384024e6df369e82529904eb` (`origin/main`).

Authorized implementation surface (two pure stimuli; the historical three-argument `advance(claim, prior_episode, evaluation_time)` sketch is demoted and is neither frozen nor implemented):

```text
apply_claim(
    claim: DetectorClaim,
    prior_episode: OpportunityEpisode | None,
    evaluation_time: datetime,
    policy: OpportunityLifecyclePolicy,
    deferral_request: OpportunityDeferral | None = None,
) -> OpportunityLifecycleResult        # CLAIM-DRIVEN

evaluate_time(
    prior_episode: OpportunityEpisode,
    evaluation_time: datetime,
    policy: OpportunityLifecyclePolicy,
) -> OpportunityLifecycleResult        # TIME-DRIVEN, no DetectorClaim
```

VERSION STRINGS (deterministic code artifacts, not caller-selected inputs):

```text
OPPORTUNITY_EPISODE_SCHEMA_VERSION = "opportunity-episode-v1"
OPPORTUNITY_LIFECYCLE_VERSION      = "opportunity-lifecycle-v1"
OPPORTUNITY_POLICY_VERSION         = "opportunity-shadow-policy-v1"
```

OWNER-RATIFIED SHADOW v1 POLICY (non-permanent):

IDENTITY (Q1). One eligible F3 IGNITION positive ``DetectorClaim`` maps to one ``OpportunityEpisode``. The identity is the deterministic ``OPEP:<digest>`` of ``{episode_schema_version, DetectorClaim.claim_id}`` computed with the existing canonical serialization and ``stable_hash`` helpers; no new hashing framework is introduced. No wall clock, UUID, random value, process identity, invocation order, retry count or database sequence participates. A directly constructed episode whose identity does not match its own claim lineage fails closed.

STATES (Q4). Exactly ``ACTIVE``, ``DEFERRED``, ``TERMINAL`` as durable uppercase strings. Detector phases ``DORMANT``/``IGNITION`` are never episode states.

DEADLINES (Q6). No numeric validity duration is invented. The caller supplies explicit UTC ``defer_deadline`` and ``validity_deadline`` on an explicit deferral request, and ``evaluation_time <= defer_deadline <= validity_deadline`` is enforced. A missing deadline fails closed, a deadline beyond validity fails closed, and a duplicate delivery never extends a deadline.

TERMINAL VOCABULARY (Q8). The only v1 terminal reason is ``EXPIRED``. ``ACTIVE``/``DEFERRED`` carry no terminal reason; ``TERMINAL`` carries ``EXPIRED``. Funnel/scan ``ReasonCode``, scanner rejection, feasibility veto, forecast timeout, order/fill dispositions, TARGET/STOP, RISK_EXIT, cancellation, protection and learning labels are deliberately not reused.

RE-ENTRY (Q9). A terminal episode is immutable and a duplicate or replay against it returns the same terminal state with no new event. The same claim can never reopen a terminal episode. A different claim while the prior episode is ``ACTIVE``/``DEFERRED`` fails closed. A different valid claim with a ``TERMINAL`` prior opens a NEW episode and never mutates the old one.

CLAIM ELIGIBILITY. Only a valid F3 IGNITION positive claim is eligible: family ``IGNITION``, transition ``DORMANT_TO_IGNITION``, phase ``IGNITION``, F3 claim identity internally valid, F3 ``episode_id`` still null, required identity fields present and a UTC ``evaluation_cutoff``. Creation requires ``evaluation_time == claim.evaluation_cutoff``. No wall clock, filesystem, database or network time is ever read. F3 thresholds, hysteresis, debounce, persistence and transition evidence are never reinterpreted.

EVENT MODEL. Exactly one deterministic event is emitted when an episode is created: ``OPENED`` for a creation into ``ACTIVE`` and ``DEFERRED`` for a creation directly into ``DEFERRED`` (no double counting). Exactly one ``EXPIRED`` event is emitted on the actual deferral-to-terminal expiry transition. Event identities are deterministic functions of the episode, the event type, the explicit evaluation instant, the lifecycle/policy versions and the source claim where applicable; re-evaluating a completed transition emits no event.

PURITY. Both surfaces are pure: no network, HTTP, requests, ccxt, krakenex, Telegram, database, SQLite, PostgreSQL, filesystem mutation, environment read, secret, subprocess, clock, random, scheduler or module-global mutable lifecycle state.

CURRENT vs TARGET AUTHORITY. CURRENT authority remains the legacy fragmented clocks (live scan walk admission, shadow funnel, watch/radar/pending-setup clocks and the offline MoveEpisode/timing-ledger evidence). TARGET authority is one canonical lifecycle over detector claims. This implementation increment performs no authority transfer and remains SHADOW / NON-AUTHORITATIVE.

SHADOW / NON-AUTHORITATIVE STATEMENT. This increment grants no production, admission, allocation, risk, paper, order or funded authority. It is not wired into ``run_cycle``, it activates no Feature Bus, and it writes no canonical evidence.

BOUNDARIES. No cutover, no writer, no database migration, no consumer migration, no Feature Bus activation, no F5/F6/F7 work, no Paper-v2/funded/live authority, and no creation of a second architecture spine at ``app/opip/opportunity/`` (forbidden by the recovery roadmap).

RESOLVED OWNER DECISIONS: Q1 episode identity digest over ``DetectorClaim``; Q3 claim→episode lineage reuse; Q5 deferral ownership; Q7 TIME-DRIVEN expiry without a claim; Q4 state tokens; Q8 terminal vocabulary; Q9 re-entry rule; Q10 both pure API surfaces. REMAINING FUTURE DECISIONS: numeric validity-horizon source (F6), persistence/writer schema for episodes and lifecycle events, consumer migration order and per-clock stop times, cutover authorization. These are recorded in the parent frozen contract and are not resolved here.

PATHS. `OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py` (F4 vocabulary and typed contracts), `OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py` (pure transition surface), `OHM-Trade-Agent-v1/app/opip/contracts/__init__.py` (F4 vocabulary exports only), `OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py` (executable acceptance and unit tests), `OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT`, and this contract.

ARCHITECTURE REFERENCES:
- `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md`: the normative acceptance source for this increment. Read-only under this increment.
- O'Pip Profit Intelligence Platform Architecture v1.4.3: `docs/architecture/v1.4.3/ARCHITECTURE.md` and the authority DOCX `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`. Not modified by this increment.
- v1.4.3 F4 Opportunity Lifecycle: deduplicate episodes; manage deferrals, deadlines, expiry and terminal reasons; owned artifact is the episode lifecycle.
- v1.4.3 section 6 (data model): `OpportunityEpisode` carries episode identity, lifecycle, defer deadline and terminal reason; `DetectorClaim` carries episode, snapshot, detector version, phase and reasons.
- v1.4.3 section 8 (detector and opportunity contract): deferred opportunities carry a deadline bounded by a validity horizon and terminate with an explicit reason; expired claims cannot resume without a new evaluation.
- v1.4.3 section 3 (canonical evidence and point-in-time correctness): evaluation time, cutoff, watermark, versions and content hash.
- `OHM-Trade-Agent-v1/docs/architecture/v1.2/D_DETECTOR_CONTRACT.md`: ownership table assigns episode identity, deferral deadline and terminal reason to the opportunity lifecycle.
- `OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md` F4 row and `OHM-Trade-Agent-v1/docs/architecture/OPIP_RECOVERY_ROADMAP.md`: fragmented current implementation; target one lifecycle over detector claims; do not add `app/opip/opportunity/` beside the bus as a second architecture.
- `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md` and `ATDD-R3-F3-ignition-implementation.md`: F3 frozen; `episode_id` remains null; F4 owns episode lifecycle; this increment must not modify F3.
- Reused rather than duplicated: `OHM-Trade-Agent-v1/app/opip/contracts/detector.py` (`DetectorClaim`, `DetectorFamily`, `DetectorPhase`, `DetectorTransition`, `detector_claim_identity`), `OHM-Trade-Agent-v1/app/opip/contracts/serialization.py` (`stable_hash`, `iso_z`), `OHM-Trade-Agent-v1/app/opip/contracts/temporal.py` (`require_utc`, `TemporalIntegrityError`).

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a valid F3 DetectorClaim carrying its claim identity, and the versioned episode schema version
WHEN:
an opportunity episode identity is derived for lifecycle use
THEN:
the identity is a deterministic function of the episode schema version and DetectorClaim.claim_id only, it never derives from wall-clock time, UUID, process identity, invocation order or retry count, and a forged or mismatched episode identity fails closed

AC-002:
GIVEN:
at-least-once delivery of the same eligible claim against an existing active, deferred or terminal episode, including restart and deadline-extension attempts
WHEN:
the lifecycle transition surface applies the duplicate
THEN:
no duplicate episode is created, the outcome is idempotent and unchanged, no event is emitted, no deadline is extended, no expiry is triggered and no extra lifecycle work is performed

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
the state is exactly one of ACTIVE, DEFERRED or TERMINAL, and detector phases such as DORMANT or IGNITION are never used as episode lifecycle states, with malformed or coercible state tokens refused

AC-005:
GIVEN:
an explicit F4 deferral request for an eligible claim or active episode
WHEN:
the resulting episode state is inspected
THEN:
deferral is recorded as an opportunity-lifecycle disposition with explicit deadlines, and it is not equivalent to detector DORMANT, detector reset, missing evidence, F5+ feasibility/forecast/selector outcomes, paper/trade lifecycle states or Committee advisory state

AC-006:
GIVEN:
a deferred opportunity episode
WHEN:
its deadline fields are validated
THEN:
both deadlines are present, they are explicit caller-supplied UTC instants, the evaluation time satisfies evaluation_time <= defer_deadline <= validity_deadline, and no numeric deadline duration invented by this increment is treated as policy

AC-007:
GIVEN:
a deferred episode with an explicit deadline
WHEN:
the TIME-DRIVEN surface is evaluated at an explicit evaluation_time greater than or equal to that deadline
THEN:
the episode terminates with an explicit EXPIRED reason without requiring a new DetectorClaim, emitting exactly one deterministic event on the actual transition, while a duplicate claim delivery cannot substitute for the timer, cannot extend the deadline, cannot create a new episode, cannot duplicate the expiry and cannot clear terminality

AC-008:
GIVEN:
a terminal opportunity episode
WHEN:
its terminal reason is inspected
THEN:
the reason is EXPIRED from the canonical F4 episode terminal vocabulary, and it is not a funnel/scan ReasonCode, forecast timeout, order/fill disposition, TARGET/STOP, RISK_EXIT, protection, cancellation or learning code

AC-009:
GIVEN:
an episode that has already reached a terminal state, or an unresolved active/deferred episode
WHEN:
further claims or retries arrive
THEN:
terminal remains terminal and a duplicate returns the same terminal state, a different claim fails closed while the prior episode is unresolved, and a different valid claim with a terminal prior opens a new episode without mutating the old one

AC-010:
GIVEN:
either CLAIM-DRIVEN inputs, a DetectorClaim plus a prior OpportunityEpisode or explicit absence plus an explicit evaluation_time and a versioned lifecycle policy, or TIME-DRIVEN inputs, a prior OpportunityEpisode plus an explicit evaluation_time and a versioned lifecycle policy with no new DetectorClaim
WHEN:
the pure F4 transition core runs for that stimulus class
THEN:
it is deterministic and replayable, performs no network, disk, database, environment, hidden-clock, random or global-mutable-state access, and observes no input outside the supplied inputs for that stimulus class

AC-011:
GIVEN:
identical inputs for a given stimulus class, including evaluation_time and the lifecycle policy version
WHEN:
the pure transition core runs more than once, including from a fresh process
THEN:
the next episode state and emitted lifecycle events are structurally and field-equivalent on every run

AC-012:
GIVEN:
the frozen F3 IGNITION detector contracts and implementation
WHEN:
this F4 implementation increment is applied
THEN:
no F3 detector semantics, thresholds, claim identity rules, DetectorState/DetectorClaim schemas or F3 acceptance contracts are modified or reinterpreted by F4

AC-013:
GIVEN:
the completed F4 implementation increment
WHEN:
repository and runtime activation surfaces are inspected
THEN:
F5 feasibility, F6 forecast, F7 selector, Paper-v2/funded trading, Committee authority and Feature Bus mode are unchanged, no runtime path imports the F4 lifecycle, and F4 grants none of those authorities

AC-014:
GIVEN:
current production runtime versus the F4 target stated by architecture and the conformance ledger, and the active-increment pointer
WHEN:
authority is described for this increment
THEN:
CURRENT authority remains the legacy fragmented clocks, TARGET authority is one canonical lifecycle over detector claims, the active increment is this implementation increment, and no authority transfer occurs

AC-015:
GIVEN:
the consumer census recorded in the parent frozen contract from a whole-repository search
WHEN:
future cutover or retirement work is planned
THEN:
that work must account for the recorded census owners and consumers, and the census remains recorded and unaltered by this increment

AC-016:
GIVEN:
the F4 cutover, retirement and delete gates recorded by the conformance ledger and recovery roadmap
WHEN:
this implementation increment completes
THEN:
no legacy clock is retired or deleted, no consumer is force-migrated, the legacy owners remain present, and only the recorded prerequisites are preserved

AC-017:
GIVEN:
this implementation increment's acceptance suite
WHEN:
the suite is collected and executed
THEN:
every AC-001..AC-017 acceptance body executes for real, no acceptance skeleton skips or fabricates a lifecycle result, and no F4 application runtime is smuggled into an unauthorized path or a forbidden second spine

EXPLICITLY OUT OF SCOPE:
- F4 opportunity-lifecycle writer, persistence schema, database migration and consumer migration
- creating `app/opip/opportunity/` or any second architecture spine
- inventing numeric deadline durations, alternative state tokens or alternative terminal-reason taxonomies
- F3 detector contract or implementation changes
- F5 feasibility integration, F6 forecast engine, F7 economic or portfolio selector
- run_cycle integration of any kind, and any scan_opportunities modification
- Feature Bus activation or OPIP_FEATURE_BUS_MODE changes
- Paper v1, Paper v2, Freqtrade, protection, risk, sizing, execution or alert changes
- dashboard work as a product change, Committee work and deployment
- legacy retirement or deletion
- funded trading
- changing any architecture document, including the v1.4.3 DOCX
- changing the frozen ATDD-R3-F4-opportunity-lifecycle contract, the R3 F3 contracts, the ATDD checker, the scope-control contract, workflows or pyproject.toml
- weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off` in docker-compose.yml.
- Production `run_cycle` does not call an F4 lifecycle and does not call any F3-to-F4 wiring.
- No `OpportunityEpisode` writer, persistence schema, database migration or consumer migration is created by this increment.
- F3 detector contracts and implementation remain frozen and unmodified.
- The canonical writer remains the single domain write path, and this increment writes no canonical evidence.
- `app/opip/opportunity/` is not created (recovery-roadmap prohibition).
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled.
- Paper execution stays isolated from funded order endpoints.
- Committee authority is unchanged and remains shadow-only.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- No merge, deploy, activation or cutover occurs under this increment by itself. A normal push of this implementation branch is permitted solely for review.

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
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-017 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/contracts/__init__.py

DEFERRED DISCOVERIES:
- Numeric validity-horizon source (Q6); may require the F6 forecast increment.
- Persistence/writer schema for OpportunityEpisode and lifecycle events remains a future increment.
- Consumer-migration order and per-clock stop-writing timestamps remain a future cutover increment.
- Whether a future ACTIVE-episode terminal reason is required beyond EXPIRED is not authorized here.
- F5 through F7 remain future increments.

UNAPPROVED SCOPE CHANGES:
NONE
