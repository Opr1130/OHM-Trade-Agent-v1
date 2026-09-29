INCREMENT:
ATDD-R3-F3-ignition-implementation

OWNER-APPROVED INTENT:
OWNER-authorized implementation increment for the first R3 slice: the pure, stateful, replayable IGNITION detector runtime.

NORMATIVE ACCEPTANCE SOURCE: `ATDD-R3-F3-ignition-detector`. That frozen contract remains the normative acceptance source for this work. This implementation increment adds the authorized application paths and restates, without weakening, the acceptance criteria it implements. It does not rewrite, re-interpret or relax the frozen contract, and it does not modify `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md`.

Authorized implementation surface:

```text
evaluate(
    snapshot: FeatureSnapshot,
    prior_state: DetectorState,
    evaluation_time: datetime,
) -> tuple[list[DetectorClaim], DetectorState]
```

This is a SHADOW / NON-AUTHORITATIVE research implementation. It grants no production authority, no admission authority, no allocation authority, no risk authority, no paper authority, no order authority and no funded-trading authority. It is not wired into `run_cycle`, it does not activate the Feature Bus, and it does not create, manage or infer an opportunity lifecycle.

OWNER-RATIFIED PROVISIONAL SHADOW POLICY (non-permanent):

The detector implements the OWNER-ratified provisional SHADOW policy recorded below. These numbers are a ratified research policy. They are explicitly NOT v1.4.3 architecture invariants, they are not permanent, and any future change to them requires a new versioned policy together with fresh OWNER approval. They confer no trading, admission, allocation, risk, paper or funded authority.

```text
POLICY_VERSION   = "ignition-shadow-policy-v1"
DETECTOR_VERSION = "ignition-detector-v1"
```

ENTRY — DORMANT -> IGNITION.

Required snapshot state: `coverage == COMPLETE`; `restart_state == WARM`; `evaluation_grid_seconds == 60`; `evaluation_time == snapshot.evaluation_cutoff`.

Required PRESENT features: `trend_state`, `return_5m`, `acceleration_5m_vs_15m`, `volume_expansion_5m_vs_20m`, `compression_release_score`.

Directional core: `trend_state == "UP"` AND `return_5m > 0.0`.

Additionally require at least TWO OF THREE: `acceleration_5m_vs_15m > 0.0`; `volume_expansion_5m_vs_20m >= 1.25`; `compression_release_score >= 0.35`.

Entry persistence: 2 consecutive 60-second evaluations = 120 seconds. Only on completion: `DORMANT -> IGNITION` and exactly one deterministic IGNITION `DetectorClaim`.

HOLD / HYSTERESIS while IGNITION.

Directional core: `trend_state == "UP"` AND `return_5m > 0.0`. AND at least ONE OF THREE: `acceleration_5m_vs_15m > 0.0`; `volume_expansion_5m_vs_20m >= 1.00`; `compression_release_score >= 0.15`.

If the hold predicate fails for two consecutive complete evaluations: `IGNITION -> DORMANT`. No positive IGNITION claim is emitted for the release transition.

DEBOUNCE. `debounce_intervals = 0`. The two-interval entry/release persistence and the asymmetric thresholds ARE the v1 debounce mechanism. No additional cooldown is implemented or permitted.

PERSISTENCE. One deterministic transition-evidence persistence counter:

```text
DORMANT:
  qualifying    -> +60
  nonqualifying -> 0
  120           -> IGNITION, then reset counter
IGNITION:
  hold true     -> release counter 0
  hold false    -> +60
  120 failed hold -> DORMANT, then reset counter
```

Numeric types are never silently coerced.

CLAIM IDENTITY. A claim is emitted ONLY for `DORMANT -> IGNITION`. Identity is a pure function of the detector family/version, the applied policy version, the instrument, the transition, the sealed `snapshot_id` and `detector_input_fingerprint(snapshot)` (`DETIN`). No hidden or current wall clock participates. `episode_id` remains `null`; F3 never mints an episode.

OWNER-RATIFIED RESET REASON MAPPING (part of `ignition-shadow-policy-v1`, NOT a permanent architecture invariant):

`MATERIAL_GAP`:
- `coverage != COMPLETE`
- or any explicit material-gap condition present in the sealed `FeatureSnapshot` evidence

`INSUFFICIENT_EVIDENCE`:
- `restart_state != WARM`
- any required feature that is absent, unsupported, `NOT_RETAINED`, or otherwise unusable
- any other epistemic insufficiency that is not a structural contract violation

Structural contract violations remain hard fail-closed validation errors (they raise) and MUST NOT become either reset reason.

Both reset reasons produce NO CLAIM, reset persistence to 0, return a safe DORMANT state, and never preserve favourable evidence.

IMPLEMENTATION INTERPRETATIONS RECORDED FOR OWNER REVIEW:

These are the resolutions the implementation had to make where the ratified policy statement determines the outcome but does not spell out the mechanism. They are recorded here so OWNER can review them. They do not add or change any policy parameter.

1. Reset-reason precedence. When a snapshot is simultaneously material-gap and epistemically insufficient, the recorded `reset_reason` is `MATERIAL_GAP`. Both outcomes are identical in effect (no claim, persistence 0, safe DORMANT, no favourable evidence preserved); only the deterministic reason token differs.
2. Phase-change token on a reset. The returned state's `transition` is derived from the actual phase change of that evaluation. A reset while `DORMANT` yields `NONE`; a reset while `IGNITION` yields `IGNITION_TO_DORMANT`. `reset_reason` remains the field that distinguishes a reset from the hysteresis release, so the two mechanisms are never conflated.
3. `reset_reason` is per-evaluation, not accumulated. The returned state carries the reason of the current evaluation only, and `NONE` when that evaluation was not a reset. This prevents a stale reason from being read as favourable or unfavourable current evidence.
4. "Absent" versus "malformed". A required feature whose key is missing, whose `missingness` stamp is not `PRESENT`, or whose value is `None` is *absent* and therefore `INSUFFICIENT_EVIDENCE`. A required feature that is present but not representable (a non-numeric value where a number is required, a bool where a number is required, a non-finite number, or a value that is not a member of the declared vocabulary) is *malformed* and therefore a structural fail-closed raise. Both paths fail closed; neither can become favourable evidence.
5. `trend_state == "UNKNOWN"` is a declared vocabulary token that carries no usable direction, so it is treated as an unusable required feature and maps to `INSUFFICIENT_EVIDENCE` rather than a raise. A `trend_state` value outside the declared vocabulary is malformed and raises.
6. A required feature present with `missingness` absent from the snapshot maps to `INSUFFICIENT_EVIDENCE`, consistent with "absent".
7. The prior state is used exactly as supplied. The detector does not require, infer or reconstruct grid adjacency from `last_evaluation_cutoff`; the frozen `evaluation_time == snapshot.evaluation_cutoff` rule is what prevents a stale snapshot from being reused as a later evaluation.
8. `evaluation_time` must be timezone-aware UTC, sub-second-free and on the declared 60-second grid, in addition to equalling `snapshot.evaluation_cutoff`.
9. Claim identity is enforced at construction. A `DetectorClaim` built directly with identifiers that do not match its own evidence is refused, so an invalid-looking claim cannot be created outside `DetectorClaim.create()`. This enforces the deterministic-identity requirement rather than adding a policy parameter.

ARCHITECTURE REFERENCES:
- `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md`: the normative acceptance source for this increment. Read-only under this increment.
- O'Pip Profit Intelligence Platform Architecture v1.4.3: `docs/architecture/v1.4.3/ARCHITECTURE.md` and the authority DOCX `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`. Not modified by this increment.
- v1.4.3 section 4 (preserved runtime) and section 8 (detector and opportunity contract): the exact `evaluate` signature; IGNITION as the sole active detector family; purity inside `evaluate()`; the detector owns transition semantics while the runtime persists them; versioned hysteresis and debounce policy; material gaps reset persistence; missing evidence is never favourable.
- v1.4.3 section 3 (canonical evidence and point-in-time correctness) and section 7 (feature-bus contract): evaluation-time grid, distinct restart/warm-up facts, and every actual detector-evaluation snapshot persisted including no-claim evaluations.
- v1.4.3 section 6 (data model): `DetectorState` carries detector/instrument/version, state, persistence timers and reset reason; `DetectorClaim` carries episode, snapshot, detector version, phase and reasons.
- v1.4.3 section 13 (acceptance scenarios): replaying the same snapshot/state/time yields the same detector result.
- `OHM-Trade-Agent-v1/docs/architecture/v1.2/D_DETECTOR_CONTRACT.md`: inherited detector interface, purity, ownership, hysteresis/debounce, deferral/expiry and one-minute cadence.
- `OHM-Trade-Agent-v1/docs/atdd/README.md` and `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-000-scope-control.md`: ATDD is subordinate to approved architecture and does not itself authorize implementation.
- Reused rather than duplicated: `OHM-Trade-Agent-v1/app/opip/contracts/features.py` (`FeatureSnapshot`), `OHM-Trade-Agent-v1/app/opip/contracts/enums.py` (`CoverageState`, `Missingness`, `RestartState`, `TrendState`), `OHM-Trade-Agent-v1/app/opip/features/replay.py` (`detector_replay_input`, `detector_input_fingerprint`), `OHM-Trade-Agent-v1/app/opip/contracts/serialization.py` (`stable_hash`), `OHM-Trade-Agent-v1/app/opip/contracts/temporal.py` (`require_utc`).

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a sealed FeatureSnapshot, a prior DetectorState, an explicit evaluation_time and the versioned detector policy
WHEN:
the IGNITION detector evaluates them
THEN:
the evaluation performs no network, disk, database, environment, internal-clock or global-mutable-state access, and it observes no input outside the supplied snapshot, the supplied prior state and the explicit arguments

AC-002:
GIVEN:
an identical sealed FeatureSnapshot, prior DetectorState, evaluation_time and detector policy/version
WHEN:
evaluate() runs more than once, including from a fresh state
THEN:
the DetectorClaim sequence and the returned next DetectorState are field-equivalent on every run

AC-003:
GIVEN:
a detector evaluation on the declared 60-second evaluation grid and a sealed snapshot that also carries a longer-cadence feature
WHEN:
evaluate() interprets the snapshot
THEN:
evaluation_time is explicit, sits on the declared 60-second grid and is exactly equal to the snapshot evaluation cutoff, and the longer-cadence feature is consumed only as a value and never redefines detector evaluation cadence

AC-004:
GIVEN:
a detector evaluation that changes phase, and a snapshot carrying prior persistence evidence
WHEN:
the transition, the claim and the returned next DetectorState are inspected
THEN:
transition semantics, hysteresis, persistence interpretation and claim generation are owned by the IGNITION detector logic, and evaluate() performs no persistence, storage, episode or deadline work

AC-005:
GIVEN:
prior DetectorState persistence evidence and a sealed FeatureSnapshot whose covered window contains a material feed gap
WHEN:
evaluate() interprets that snapshot
THEN:
persistence evidence does not accrue across the material gap, the affected persistence resets deterministically to zero, the reason MATERIAL_GAP is recorded, and no claim is produced from the missing evidence

AC-006:
GIVEN:
a snapshot whose required evidence is absent, not retained, unsupported or otherwise unusable
WHEN:
evaluate() validates and interprets that snapshot
THEN:
the evaluation returns no claim, a safe DORMANT state, a zeroed persistence counter and the reason INSUFFICIENT_EVIDENCE, so the defective evidence cannot become favourable detector evidence

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
no opportunity episode, deferral, deadline, expiry or terminal lifecycle reason is created, managed or inferred, and the claim carries a null episode identity

AC-009:
GIVEN:
a supplied valid prior DetectorState, including one restored after a restart
WHEN:
evaluate() continues from it
THEN:
the continuation is deterministic and the prior state is used exactly as supplied, with no hidden reconstruction, defaulting or substitution, and an absent or invalid prior state fails closed instead of being invented

AC-010:
GIVEN:
the implementation increment and its runtime activation surfaces
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
the detector decision input is exactly detector_replay_input(snapshot) plus the prior DetectorState, the explicit evaluation_time and the versioned detector policy, the lineage identifiers snapshot_id and detector_input_fingerprint are carried, and excluded FeatureSnapshot metadata does not become favourable transition evidence

AC-012:
GIVEN:
a prior DetectorState and an applied detector policy that each declare a detector/policy version
WHEN:
evaluate() runs
THEN:
every returned claim and the next DetectorState carry the applied version, and a prior state whose declared version does not match the applied policy fails closed rather than continuing under a silently substituted policy

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
evaluation_time must be UTC, grid-aligned and exactly equal to the snapshot evaluation_cutoff, evaluated_at_utc is never used as the evaluation instant, and a naive, off-grid or cutoff-mismatched evaluation_time fails closed

AC-015:
GIVEN:
a prior or returned DetectorState carrying phase, persistence-timer and reset-reason fields
WHEN:
that state is validated or serialized
THEN:
every phase, persistence and reset value satisfies its declared canonical type exactly, and an invalid, fractional or boolean value is refused rather than truncated or coerced into an apparently valid state

AC-016:
GIVEN:
a sealed FeatureSnapshot that satisfies no IGNITION transition condition
WHEN:
evaluate() runs
THEN:
it returns an empty claim list and a valid next DetectorState without fabricating a transition, and the no-claim outcome is deterministic and replayable

EXPLICITLY OUT OF SCOPE:
- F4 opportunity lifecycle implementation, deferral, deadline, expiry or terminal reason
- F5 feasibility integration, F6 forecast engine, F7 economic or portfolio selector
- run_cycle integration of any kind, and any scan_opportunities modification
- Feature Bus activation, canonical writer integration, detector-state persistence wiring
- Paper v1, Paper v2, Freqtrade, protection, risk, sizing, execution or alert changes
- dashboard work, Committee work, deployment
- legacy retirement or deletion, including app/services/explosion_state.py
- funded trading
- changing any architecture document, including the v1.4.3 DOCX
- changing R2 evidence or its contracts, or the frozen ATDD-R3-F3-ignition-detector contract
- modifying the ATDD checker, scope-control contract, workflows or pyproject.toml
- weakening, deleting or skipping any existing test

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off` in docker-compose.yml.
- Production `run_cycle` does not call the Feature Bus and does not call the F3 detector.
- The canonical writer remains the single domain write path; this increment writes no canonical evidence.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled.
- Paper execution stays isolated from funded order endpoints.
- Committee authority is unchanged and remains shadow-only.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- `app/opip/features/replay.py`, `app/opip/features/state.py` and `app/opip/contracts/features.py` are read-only under this increment.
- No merge and no deployment occur under this increment. A normal push of this implementation branch is permitted solely for review.

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
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/contracts/detector.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/contracts/detector.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/contracts/detector.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/detectors/ignition.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/contracts/detector.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-implementation.md
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/contracts/__init__.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/detectors/__init__.py

DEFERRED DISCOVERIES:
- Detector claim/state canonical event vocabulary and writer idempotency keys are a persistence-integration decision and remain outside F3.
- Whether a no-claim evaluation is persisted per grid instant is an F2/F3/canonical-writer retention decision already stated by v1.4.3 section 7.
- Cross-sectional cold-start substitution remains a separate shadow-only research hypothesis.
- Late-arrival eligibility adjudication belongs to the upstream sealing boundary, not to F3.
- R3 slices F4 through F7, and the v1.4.3 section 13 platform acceptance scenarios, remain future increments.
- The observed value ranges of the provisional policy thresholds have not yet been calibrated against recorded shadow evidence; recalibration is a future versioned policy decision.
- Cross-checks of `feature_version` / `feature_dag_hash` against the prior state are not required by the frozen contract, so they were deliberately not added; introducing one would be a new versioned policy requirement.
- Raised in review of this implementation PR: because the ratified persistence rule increments unconditionally, a caller that re-feeds the identical snapshot with the returned intermediate state (a redelivery or retry) can accrue 60 -> 120 and complete entry from one distinct interval, and a caller that supplies a non-adjacent complete-window snapshot can bridge a skipped grid step. This implementation applies the ratified rule exactly as specified and adds no adjacency or duplicate guard, because doing so would be a new policy rule requiring a new versioned policy and OWNER approval. A genuine gap is expected to surface from the feature bus as `coverage != COMPLETE` (already implemented as `MATERIAL_GAP`), and duplicate snapshot delivery is deduplicated by `snapshot_id` at the canonical/persistence layer, which is outside F3. Proposed future criterion for OWNER ruling: require `evaluation_time == prior_state.last_evaluation_cutoff + 60s` before accruing persistence, and treat a repeated or non-adjacent evaluation as a reset. Recorded as a proposal only; it is not approved and not implemented.

UNAPPROVED SCOPE CHANGES:
NONE
