INCREMENT:
ATDD-R3-F3-ignition-detector

OWNER-APPROVED INTENT:
Freeze the minimum architecture-faithful contract for the first R3 slice: the F3 Stateful Detector Runtime for the single v1 detector family, IGNITION. This increment defines and freezes the acceptance criteria and the authorized path set for a deterministic, replayable, pure IGNITION detector that operates on an already-sealed `FeatureSnapshot`:

```text
evaluate(
    snapshot: FeatureSnapshot,
    prior_state: DetectorState,
    evaluation_time: datetime,
) -> tuple[list[DetectorClaim], DetectorState]
```

This increment is SHADOW / evidence-only. It grants no production authority, no admission authority, no paper authority, no order authority, no risk authority and no Feature Bus activation. It does not wire the detector into `run_cycle`. It is a contract freeze: it authorizes this scope contract, the active-increment pointer and contract-stage acceptance-test skeletons only. Detector application code — `DetectorState`, the evaluator and the runtime — is a later, OWNER-approved implementation increment.

Frozen F3 semantics that this contract encodes:

1. IGNITION is the sole detector family for v1.
2. `evaluate()` is pure: no network, disk, database, environment, internal-clock or global-mutable-state access.
3. `evaluation_time` is explicit; it is UTC; it sits on the declared 60-second evaluation grid; and it is exactly equal to `snapshot.evaluation_cutoff`. Detector evaluation uses the declared 60-second grid, and every actual detector-evaluation `FeatureSnapshot` corresponds to its evaluation instant, so a stale snapshot must not be reused as a later detector evaluation. `evaluated_at_utc` is a DIFFERENT boundary and may legitimately be later, because receipt/visibility latency is a separate fact from the evidence cutoff; `evaluation_time` and `evaluated_at_utc` are therefore never equated. No hidden clock is read, and a mismatch between `evaluation_time` and `snapshot.evaluation_cutoff` fails closed.
4. The detector owns transition semantics, hysteresis, debounce/persistence interpretation, claim generation and the next `DetectorState`.
5. Runtime persistence is not owned by `evaluate()`; persistence integration is outside this first slice.
6. Claim idempotency is tied to the actual detector transition and evidence, not to wall-clock invocation identity.
7. Material feed gaps: persistence evidence must not accrue through a material gap; affected persistence resets or revalidates; no favourable inference from missing evidence.
8. Expired claims cannot silently resume. A future opportunity lifecycle owns deadline and expiry semantics; F3 must not implement F4.
9. Evaluation grid: 60 seconds / one minute. Fifteen-minute inputs may be features but do not change evaluation cadence.
10. Deterministic replay: the same `FeatureSnapshot` + prior `DetectorState` + `evaluation_time` + detector policy/version produces exactly the same `DetectorClaim` sequence and next `DetectorState`.
11. No future-data contamination: the detector uses only information contained in the supplied sealed `FeatureSnapshot` and explicit inputs.
12. The inherited v1.2 fixture establishes representative logical shape only: `detector_family = IGNITION`, prior phase `DORMANT`, transition to `IGNITION`, `persistence_seconds`, `reset_reason`, deterministic `claim_id` / `idempotency_key`, and `episode_id` allowed to remain null because F4 owns episode lifecycle. The fixture is not the complete production schema; the implementation increment must derive the minimum typed interface from the checked-in `FeatureSnapshot` contract rather than copying the fixture wholesale.

TARGET INTERFACE INPUTS (current repository truth, not a new contract):

- `app/opip/contracts/features.py` owns `FeatureSnapshot`: `instrument_version_id`, `venue_instrument_id`, `feature_version`, `evaluation_cutoff` (grid-aligned), `evaluated_at_utc`, `consumed_input_watermark`, `values`, `availability`, `missingness`, `coverage`, `restart_state`, `evaluation_grid_seconds`, `feature_schema_version`, `feature_calc_version`, `feature_dag_hash`, `snapshot_id`, and `content_hash()`.
- `app/opip/features/replay.py::detector_replay_input` already defines exactly what a detector may read from a snapshot, and `detector_input_fingerprint` (`DETIN`) is the deterministic input fingerprint. F3 must reuse that surface rather than invent a competing one, and the frozen evidence/lineage distinction is:

    DETECTOR DECISION INPUT (what `evaluate()` consumes):
    - `detector_replay_input(snapshot)`;
    - the prior `DetectorState`;
    - the explicit `evaluation_time`;
    - the versioned detector policy.

    LINEAGE / TAMPER EVIDENCE (carried, not what drives the decision):
    - `snapshot_id`;
    - `snapshot.content_hash()`;
    - `detector_input_fingerprint(snapshot)` (`DETIN`).

    The detector may carry the lineage identifiers into `DetectorClaim` / `DetectorState`, but excluded `FeatureSnapshot` metadata (for example `availability`, `evaluated_at_utc`, freshness and notes) must not become favourable transition evidence. `detector_replay_input` is not expanded in this increment and `app/opip/features/replay.py` is not modified.
- Where the applied detector policy comes from (it is not a fourth argument): the versioned detector policy named above is a versioned property of the detector implementation itself, not an argument and not a hidden input. v1 IGNITION has one detector family and one versioned policy, and that policy (hysteresis, debounce and persistence parameters together with its version token) is a deterministic, replayable code artifact exactly like any other frozen contract constant. Nothing outside the supplied snapshot, the supplied prior `DetectorState` and the explicit `evaluation_time` is consulted at run time, and no environment, clock, registry or global state selects or substitutes it. The prior `DetectorState` carries the detector/policy version that produced it; `evaluate()` fails closed when that declared version does not match the applied policy version, and every returned claim and the next state carry the applied version. Introducing a further policy version, or moving policy selection to a caller-supplied argument, changes the frozen three-argument interface and therefore requires an OWNER-approved interface change outside this freeze.
- Point-in-time eligibility of the facts inside a sealed `FeatureSnapshot` is owned by the upstream sealing boundary, not by F3. The snapshot is consumed already sealed: its `consumed_input_watermark` plus the producer's guarantees are what make its `values` cutoff-eligible, and the frozen rule `evaluation_time == evaluation_cutoff` binds an evaluation to that sealed instant. F3 therefore does not re-adjudicate visibility and must not attempt to reconstruct eligibility from excluded metadata. Re-adjudicating visibility upstream, or adding availability facts to the replay-input projection, changes the sealing boundary and `app/opip/features/replay.py`, is outside this increment, and requires its own OWNER-approved change.
- `app/opip/features/state.py` already proves the gap/persistence-reset discipline at the feature layer (`persistence_intervals`, `gap_resets`, `last_gap_epoch`). F3 mirrors that discipline for detector persistence; it does not create a second state framework.
- `app/services/explosion_state.py` and the other `IGNITION` string uses are the legacy explosion/phase taxonomy. They are not `DetectorState` and are not prior art for this contract.

AUTHORITY / REPLACEMENT STATEMENT:

- Current authority: the legacy scanner / phase-ranking path. There is no target F3 runtime authority.
- Target authority: a pure IGNITION detector over `FeatureSnapshot`.
- Cutover gate: not part of this increment; it requires later shadow replay/evidence and explicit OWNER approval.
- Retirement target: legacy explosion/phase transition behaviour may become a retirement candidate only after F3 is the proven claim source and consumers have migrated.
- Delete gate: not authorized.

PROPOSED IMPLEMENTATION MAP FOR THE DEFERRED F3 IMPLEMENTATION INCREMENT (documentation only; this contract-freeze increment authorizes no application path and creates no detector runtime):

- `OHM-Trade-Agent-v1/app/opip/contracts/detector.py` — typed `DetectorState`, `DetectorClaim`, phase/transition vocabulary and detector/policy version tokens, owned by the shared contracts package.
- `OHM-Trade-Agent-v1/app/opip/contracts/__init__.py` — re-export the detector vocabulary in the existing contracts-package style.
- `OHM-Trade-Agent-v1/app/opip/detectors/__init__.py` — the single detector package.
- `OHM-Trade-Agent-v1/app/opip/detectors/ignition.py` — the pure `evaluate(snapshot, prior_state, evaluation_time)` IGNITION evaluator.
- `OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py` — the acceptance and unit tests; the contract-stage skeletons in this increment are completed here rather than replaced by a second test file.

The proposed set adds no second Feature Bus, no second state framework, no duplicate snapshot contract, no parallel opportunity package and no compatibility wrapper. Detector vocabulary belongs with the shared contracts package; the pure evaluator belongs in one detectors package.

This increment remains CONTRACT-FREEZE ONLY. The proposed application paths are advisory for OWNER review only, and they are deliberately NOT added to the active `IMPLEMENTATION MAP`. A separate, OWNER-approved ATDD implementation increment will authorize them, and this contract becomes the normative acceptance source for that implementation increment.

ARCHITECTURE REFERENCES:
- O'Pip Profit Intelligence Platform Architecture v1.4.3: repository authority copy `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`, and its extraction `docs/architecture/v1.4.3/ARCHITECTURE.md`.
- v1.4.3, F3 Stateful Detector Runtime (feature set): identify IGNITION transitions from explicit state and feature snapshots; claims only, not episodes.
- v1.4.3, section 4 (preserved runtime): IGNITION remains the sole active detector family; evaluation is a pure function of `FeatureSnapshot`, prior `DetectorState` and explicit `evaluation_time`; the detector owns transition semantics while the runtime persists them; hysteresis and debounce policy are versioned; material gaps reset persistence evidence; expired claims require a new evaluation; missing evidence is never favourable.
- v1.4.3, section 8 (detector and opportunity contract): the exact `evaluate` signature; no network, disk, database, internal clock or global mutable state inside `evaluate()`; deferred opportunities carry a bounded deadline and an explicit terminal reason; cross-sectional cold-start substitution remains a separate shadow-only hypothesis.
- v1.4.3, section 3 (canonical evidence and point-in-time correctness): each snapshot and research input identifies evaluation time, evidence cutoff, consumed-input watermark, feature version, source availability times and a content hash; evidence is eligible only if it was available by the cutoff.
- v1.4.3, section 7 (feature-bus contract): persist every actual detector-evaluation `FeatureSnapshot`, including evaluations that produce no claim; use a declared evaluation-time grid; restart/warm-up states are distinct facts.
- v1.4.3, section 6 (data model): `DetectorState` carries detector/instrument/version, state, persistence timers and reset reason; `DetectorClaim` carries episode, snapshot, detector version, phase and reasons.
- v1.4.3, section 13 (acceptance scenarios): replay the same snapshot/state/time and obtain the same detector result.
- docs/architecture/v1.2/D_DETECTOR_CONTRACT.md: the inherited detector interface, purity, ownership table, hysteresis/debounce, deferral/expiry and one-minute evaluation cadence.
- docs/architecture/v1.2/fixtures/detector_evaluate.example.json and docs/architecture/v1.2/fixtures/feature_snapshot.example.json: representative logical shape.
- docs/architecture/OPIP_CONFORMANCE_LEDGER.md: the F3 row records `IMPLEMENTATION_STATUS = MISSING`, `CURRENT_IMPLEMENTATION = Contract and fixture only`, and ``DUPLICATE_OR_OVERLAPPING_PATHS = `app/services/explosion_state.py` uses the string `IGNITION` as a phase label. That is not DetectorState.``
- docs/architecture/OPIP_RECOVERY_ROADMAP.md: R3 is the single next implementation increment and starts with the IGNITION pure detector runtime; the explosion-phase string is not this detector.
- docs/architecture/CURRENT_ARCHITECTURE_STATUS.md: `R3 has not started`; no IGNITION detector module exists.
- docs/atdd/scope-contracts/ATDD-000-scope-control.md: ATDD is subordinate to approved architecture and does not authorize implementation, grant trading authority or change runtime behaviour.
- Checked-in contracts to reuse rather than duplicate: `OHM-Trade-Agent-v1/app/opip/contracts/features.py`, `OHM-Trade-Agent-v1/app/opip/features/replay.py`, `OHM-Trade-Agent-v1/app/opip/features/state.py`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a sealed FeatureSnapshot, a prior DetectorState, an explicit evaluation_time and a versioned detector policy
WHEN:
the IGNITION detector evaluates them
THEN:
the evaluation performs no network, disk, database, environment, internal-clock or global-mutable-state access, and it observes no input outside the supplied snapshot and the explicit arguments

AC-002:
GIVEN:
an identical sealed FeatureSnapshot, prior DetectorState, evaluation_time and detector policy/version
WHEN:
evaluate() runs more than once, including from a fresh state in a new process
THEN:
the DetectorClaim sequence and the returned next DetectorState are structurally and field-equivalent on every run

AC-003:
GIVEN:
a detector evaluation on the declared 60-second evaluation grid and a sealed snapshot that also carries a longer-cadence feature such as a 15-minute range
WHEN:
evaluate() interprets the snapshot
THEN:
evaluation_time is explicit, sits on the declared 60-second grid and is exactly equal to the snapshot's evaluation cutoff, and the longer-cadence feature is consumed only as a value and never redefines detector evaluation cadence

AC-004:
GIVEN:
a detector evaluation that changes phase, and a snapshot carrying prior persistence evidence
WHEN:
the transition, the claim and the returned next DetectorState are inspected
THEN:
transition semantics, hysteresis, debounce/persistence interpretation and claim generation are owned by the IGNITION detector logic, and evaluate() performs no persistence, storage, episode or deadline work

AC-005:
GIVEN:
prior DetectorState persistence evidence and a sealed FeatureSnapshot whose covered window contains a material feed gap
WHEN:
evaluate() interprets that snapshot
THEN:
persistence evidence does not accrue across the material gap, the affected persistence resets or requires revalidation deterministically, and no claim is produced from the missing evidence

AC-006:
GIVEN:
a snapshot whose required evidence is missing, stale, inconsistent with the declared evaluation_time, or unsupported
WHEN:
evaluate() validates and interprets that snapshot
THEN:
the evaluation fails closed: the defective evidence cannot become favourable detector evidence and no claim is fabricated to fill the gap

AC-007:
GIVEN:
the same detector transition reached by two separate invocations at different wall-clock instants
WHEN:
claim identity and idempotency identity are computed
THEN:
they derive from the actual transition and the sealed snapshot evidence identity, they are identical across both invocations, and they never derive from the invocation wall-clock time

AC-008:
GIVEN:
a DetectorClaim produced from a sealed FeatureSnapshot
WHEN:
the claim and the returned DetectorState are inspected
THEN:
no opportunity episode, deferral, deadline, expiry or terminal lifecycle reason is created, managed or inferred, because the opportunity lifecycle is a separate later feature

AC-009:
GIVEN:
a supplied valid prior DetectorState, including one restored after a restart
WHEN:
evaluate() continues from it
THEN:
the continuation is deterministic and the prior state is used exactly as supplied, with no hidden reconstruction, defaulting or substitution, and an absent or invalid prior state fails closed instead of being invented

AC-010:
GIVEN:
the completed contract-freeze increment and its later implementation increment
WHEN:
repository and runtime activation surfaces are inspected
THEN:
run_cycle does not call the detector, the Feature Bus remains inactive, and no scanner authority, paper routing, selector, Committee or deployment behaviour changes

AC-011:
GIVEN:
a sealed FeatureSnapshot supplied to evaluate()
WHEN:
the evaluation binds its claims and next state to that snapshot
THEN:
the detector decision input is exactly detector_replay_input(snapshot) plus the prior DetectorState, the explicit evaluation_time and the versioned detector policy; the detector may carry the lineage and tamper-evidence identifiers snapshot_id, snapshot.content_hash() and detector_input_fingerprint(snapshot) into DetectorClaim and DetectorState, but excluded FeatureSnapshot metadata must not become favourable transition evidence; eligibility of the facts inside that sealed snapshot is taken as guaranteed by the upstream sealing boundary, so F3 adds no visibility re-adjudication and derives no transition evidence from evidence-receipt metadata; and a snapshot whose identity is absent or not bound to the supplied evidence fails closed

AC-012:
GIVEN:
a prior DetectorState and an applied detector policy that each declare a detector/policy version
WHEN:
evaluate() runs
THEN:
every returned claim and the next DetectorState carry that version, the applied policy version is the one bound to the detector implementation rather than a caller-supplied argument (consistent with the frozen three-argument interface), and a prior state whose declared version does not match the applied policy fails closed rather than continuing under a silently substituted policy

AC-013:
GIVEN:
a prior DetectorState for one instrument identity and a sealed FeatureSnapshot for a different instrument identity
WHEN:
evaluate() validates its inputs
THEN:
the mismatch fails closed, and the detector never applies one instrument's phase or persistence evidence to another

AC-014:
GIVEN:
a sealed FeatureSnapshot and an explicit evaluation_time
WHEN:
evaluate() validates the evaluation instant
THEN:
evaluation_time is explicit, UTC, sits on the declared 60-second grid and is exactly equal to the snapshot's evaluation_cutoff; evaluated_at_utc is a separate receipt/visibility boundary and is never equated with evaluation_time; no hidden clock is read; and a missing, off-grid or cutoff-mismatched evaluation_time fails closed

AC-015:
GIVEN:
a prior or returned DetectorState carrying phase, persistence-timer and reset-reason fields
WHEN:
that state is validated or serialized
THEN:
every phase, persistence and reset value satisfies its declared canonical type exactly, and an invalid or fractional value is refused rather than truncated or coerced into an apparently valid state

AC-016:
GIVEN:
a sealed FeatureSnapshot that satisfies no IGNITION transition condition
WHEN:
evaluate() runs
THEN:
it returns an empty claim list and a valid next DetectorState without fabricating a transition, and the no-claim outcome is deterministic and replayable

EXPLICITLY OUT OF SCOPE:
- F4 opportunity lifecycle implementation
- F5 feasibility integration
- F6 forecast engine
- F7 economic or portfolio selector
- run_cycle integration of any kind
- scan_opportunities modifications
- Feature Bus activation
- canonical writer integration
- detector-state persistence wiring
- Paper v1, Paper v2, or Freqtrade changes
- protection, risk, sizing, or execution changes
- dashboard work
- Committee work
- alert changes
- deployment
- legacy retirement or deletion
- funded trading
- changing any architecture document, including the v1.4.3 DOCX
- changing R2 evidence or its contracts
- modifying the ATDD checker, the scope-control contract, workflows, or any existing test
- weakening any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off`.
- Production `run_cycle` does not call the Feature Bus and does not call any F3 detector.
- No `DetectorState`, evaluator or detector runtime is created by this increment.
- The canonical writer remains the single domain write path, and this increment writes no canonical evidence.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled.
- Paper execution stays isolated from funded order endpoints.
- Committee authority is unchanged and remains shadow-only.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- No merge, deploy, activation, or implementation change occurs under this increment.
- A normal push of the contract-only review branch is permitted solely for OWNER review, and OWNER review and merge of this contract-only governance PR are permitted.
- Reviewing, pushing or merging this contract grants no implementation or runtime authority, and does not authorize F3 implementation.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_001_evaluation_is_pure
AC-002 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_002_repeated_evaluation_is_deterministic
AC-003 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_003_explicit_evaluation_time_uses_declared_grid
AC-004 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_004_transition_owned_by_detector_not_storage
AC-005 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_005_material_gap_resets_persistence
AC-006 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_006_missing_or_invalid_evidence_is_not_favourable
AC-007 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_007_transition_identity_is_not_wall_clock
AC-008 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_008_claims_do_not_create_opportunity_lifecycles
AC-009 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_009_supplied_prior_state_continues_deterministically
AC-010 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_010_shadow_isolation_grants_no_authority
AC-011 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_011_evaluation_is_bound_to_the_sealed_snapshot_identity
AC-012 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_012_detector_policy_version_is_carried_and_checked
AC-013 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_013_instrument_identity_must_match_prior_state
AC-014 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_014_evaluation_time_is_explicit_and_valid
AC-015 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_015_persistence_and_reset_state_is_typed
AC-016 -> tests/test_opip_r3_f3_ignition_detector.py::test_ac_016_no_claim_evaluation_is_valid_and_deterministic

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py

DEFERRED DISCOVERIES:
- `DetectorClaim` and `DetectorState` canonical event vocabulary and writer idempotency keys are a persistence-integration decision, not part of this freeze; `evaluate()` owns none of it.
- `episode_id` remains null in the inherited fixture because F4 owns episode identity; the F3 contract deliberately does not mint one.
- The exact hysteresis and debounce parameter values are versioned policy and are not frozen numerically here; only the versioning requirement is.
- Whether a no-claim evaluation is persisted per grid instant is an F2/F3/canonical-writer retention decision already stated by v1.4.3 section 7 and is not redefined by this contract.
- Cross-sectional cold-start substitution remains a separate shadow-only research hypothesis and is not part of F3.
- Late-arrival eligibility adjudication (whether evidence received after a cutoff could be distinguished inside the sealed snapshot) was raised in review of this contract. F3 consumes an already-sealed snapshot and adds no eligibility check, because visibility eligibility is owned by the upstream sealing boundary and `consumed_input_watermark`. If that boundary later needs to expose per-fact availability for a stronger check, it is a separate OWNER-approved change to the sealing contract and to `app/opip/features/replay.py`, not an F3 acceptance criterion under this freeze.
- R3 slices F4 through F7, and v1.4.3 section 13 platform acceptance scenarios, remain future increments.

UNAPPROVED SCOPE CHANGES:
NONE
