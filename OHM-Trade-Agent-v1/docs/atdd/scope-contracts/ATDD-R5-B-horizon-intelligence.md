INCREMENT:
ATDD-R5-B-horizon-intelligence

OWNER-APPROVED INTENT:
Deliver the R5-B Horizon Intelligence thesis-lifecycle contract (`app/opip/contracts/horizon_lifecycle.py`) that closes the one structural gap R5-A left open. R5-A classifies a horizon and records entry and exit *intent* — an entry window, an expected holding range and a time invalidation — but it ships no **first-fill anchor** and no **lifecycle clock**, so no "expected holding range" can be measured point-in-time. This increment adds the immutable first-fill anchor of one thesis revision and the three declared lifecycle clocks (`QUALIFICATION`, `HOLDING`, `INVALIDATION`) as deterministic, replayable, research-only evidence.

The adopted body (`OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, 9 October 2026) requires in section 4 that `HZ-01` freeze the thesis identity, classifier version, entry window, expected holding range and time invalidation with recorded classification uncertainty, and that `HZ-02` make the original thesis and its first-fill clock immutable: a later fill or an extension must never restart time invalidation or turn an expired tactical position into a position investment. Sections 5 and 10 add the lifecycle vocabulary (`LC-01`-`LC-03`) and the temporal canonical contracts (`DC-01`-`DC-03`) that every lifecycle observation must respect.

This increment delivers exactly that seam and nothing more: one anchor record, one clock record, one lifecycle observation, and one abstaining evaluation seam shared with a strict clock-start seam so the two can never disagree about whether a clock may be observed.

Every owner-gated number stays `OWNER_DECISION_REQUIRED` (`OQ-03`, `OQ-08`). The module ships no default qualification budget, no default holding band and no default clock duration. A policy without them abstains visibly with a recorded `LifecycleAbstentionReason` instead of guessing, borrowing or defaulting a number.

This increment grants no authority. It activates nothing, changes no runtime, risk, execution, deployment, release-profile or trading control, adds no scheduled service, no store, no allocator and no second detector, and wires no R5-B module into a runtime root.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (adopted authority, SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`) with extraction `docs/architecture/v1.5.0/ARCHITECTURE.md` and identity pin `docs/architecture/v1.5.0/SOURCE.md`; section 4 (`HZ-01`, `HZ-02` Horizon Intelligence), section 5 (`LC-01`-`LC-03` Opportunity and position lifecycles), section 10 (`DC-01`-`DC-03` canonical records and temporal contracts), section 16 (`HORIZON` row), section 17 (`AC-01`-`AC-03`, `AC-12`) and section 18 (gates G1-G6).
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` row `R5-B` (dependency `R5-A`, declared contract `SC-04`, open input `OQ-03`, gate `G3`) and rows `F4`, `F6`.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` contract `SC-04` (Horizon thesis record: identity, keys and validity, whose consumers include `R5-B`), the frozen owner table (`OQ-03` horizon boundaries and expected holding ranges, `OQ-08` R4 closure) and the frozen `OpportunityThesis` surface on `app/opip/contracts/temporal.py`.
- `docs/architecture/v1.4.4/ARCHITECTURE.md` Appendix A (`PA-02` fresh qualification, `PA-06` traceable qualification evidence, `PA-13` independent protection, `PA-17` no hindsight contamination, `PA-20` blocked Paper v2) and Appendix B (`SQ-01`-`SQ-10`), which v1.5.0 section 13 keeps applicable.
- `docs/architecture/v1.2/` contracts C (canonical writer) and D (detector) and `CODING_BOUNDARY_CONTRACT.md`.
- `docs/architecture/OPIP_R5_A_CONTINUOUS_OPPORTUNITY_ENGINE.md` (the immediately preceding R5-A conformance record, whose horizon thesis contract this increment extends) and the frozen F6 vocabulary `ForecastHorizonAnchor.FIRST_FILL` in `app/opip/contracts/forecast.py`.
- `AGENTS.md` mandatory principles, including feature branch only, no production activation, no trading authority, no secrets, single canonical writer and no new infrastructure.
- Existing owners reused unchanged: `app/opip/contracts/thesis.py` (`HorizonThesis`, `HorizonClass`, `ThesisLifecycleState`, `BOUNDED_CLASSES`, `require_horizon_class`), `app/opip/contracts/temporal.py` (`require_utc`), `app/opip/contracts/serialization.py` (`iso_z`, `stable_hash`) and this increment's new contract module `app/opip/contracts/horizon_lifecycle.py`.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
one classified thesis revision and the first fill that opened it
WHEN:
the first-fill anchor is recorded or forged
THEN:
the anchor identity is deterministic and bound to the thesis revision, its lineage and the first fill instant, the record carries every declared key with `authoritative` hard-coded `False` and no authority, sizing, reservation or order field, a rewritten or drifted `FFAN:` identity is refused, an anchor cannot precede the revision that declares the horizon or the fill it describes, and an expired revision is never anchored

AC-002:
GIVEN:
a recorded first-fill anchor and a later fill of the same revision
WHEN:
the later fill is accumulated
THEN:
the returned record preserves `anchor_id` and `first_fill_at` exactly, quantity accumulates with a weighted average price and the fill count and source references grow, a back-filled or out-of-order fill is refused rather than merged, and the holding deadline is unchanged by the later fill

AC-003:
GIVEN:
a configured lifecycle policy and a thesis revision with preserved instants
WHEN:
a declared clock is started and compared with its deadline
THEN:
the declared clock set is the closed, ordered `QUALIFICATION`, `HOLDING`, `INVALIDATION` vocabulary, each deadline is exactly `started_at + expected_seconds` with the start instant taken from preserved evidence (`classified_at` or the anchor's `first_fill_at`), a clock identity excludes the observation instant and state so advancement reproduces the same `TCLK:` identity, the qualification clock requires the declared entry window and the declared budget to agree, and a clock the revision or the policy cannot support is refused instead of defaulted

AC-004:
GIVEN:
a running clock and an explicit observation instant
WHEN:
the clock is advanced to, at or beyond its deadline
THEN:
expiry is recorded once with the instant expiry was observed, an expired clock is terminal and never resumes, a clock cannot be observed before it started or received out of order, a forged deadline, a running clock carrying an expiry instant and an expiry observed before the deadline are refused, and the clock state vocabulary is the closed, declared set

AC-005:
GIVEN:
a lifecycle observation of one thesis revision
WHEN:
the observation is built, serialized or forged
THEN:
a supported observation carries exactly the declared clock set in the declared order bound to that revision, an abstained observation carries no clocks at all, a partial, reordered, duplicated or foreign clock set is refused, the evaluation and receipt instants must be ordered against the instants they describe, a forged `HLFC:` identity is refused, and `authoritative` is hard-coded `False`

AC-006:
GIVEN:
an unconfigured or partially configured lifecycle policy and a revision with missing horizon evidence
WHEN:
the lifecycle is evaluated or a clock is started directly
THEN:
an unconfigured policy abstains with `POLICY_NOT_CONFIGURED`, an `UNKNOWN` class abstains rather than guessing, a missing anchor, a missing entry window, a missing time invalidation, an unbanded class and a qualification budget that disagrees with the declared window each abstain with their declared reason and start no clock, the evaluation seam and the strict clock seam report the same reason, and `OQ-03`/`OQ-08` stay owner decisions recorded as `OWNER_DECISION_REQUIRED`

AC-007:
GIVEN:
a lifecycle evaluation whose instants could describe the future
WHEN:
the instants are compared with the evidence they claim to describe
THEN:
an anchor before the revision's classification instant, an anchor at or after the declared time invalidation, an observation before a clock could start, evidence received before the instant it describes and a naive or foreign-revision record all fail closed as errors instead of producing favourable evidence

AC-008:
GIVEN:
one thesis revision, one lifecycle policy and one first-fill anchor
WHEN:
the lifecycle is observed again at a later instant, or the applied policy token changes
THEN:
re-observing reproduces the same `HLFC:` lifecycle identity and the same `TCLK:` clock identities, an identical evaluation replays identically key for key, the applied policy version is part of the identity while the preserved start instants are unchanged by the token, and an abstention is a distinct observation from supported evidence

AC-009:
GIVEN:
a lifecycle observation and an explicit read instant
WHEN:
the remaining lifecycle time is read for one clock
THEN:
the remaining time is whole seconds derived from the preserved deadline and the supplied instant, it is floored at zero and never negative, an abstained lifecycle or a clock the observation does not carry reports unknown rather than zero, an undeclared clock name is refused, and a read before that clock started fails closed

AC-010:
GIVEN:
a thesis revision whose first fill arrives as evidence
WHEN:
the anchor is attached and the lifecycle observed in one step
THEN:
the anchor and the observation share one receipt instant, the already recorded anchor of the revision is reused so re-attaching the same first fill is idempotent and never discards accumulated fills, and attaching a different first fill, a previous lifecycle of another revision or a previous lifecycle without an anchor is refused

AC-011:
GIVEN:
the R5-B contract module, the runtime roots and the release-profile contract
WHEN:
imports, sources, exports and profile literals are inspected
THEN:
the module imports no network, database, filesystem, subprocess, clock, randomness, UUID or persistence facility, names no order, sizing, reservation, credential or store surface, reads no environment variable, exposes no authority-shaped export, no runtime root or other application module consumes it, the two frozen R3 isolation scans that enumerate every module under `app/` keep every substantive assertion and need no widening for this module, `EVIDENCE_SHADOW` stays the active shadow profile with `TARGET_PAPER` blocked and paper-v2 off, and every record declares itself non-authoritative

AC-012:
GIVEN:
this increment's scope contract, tests and declared vocabulary
WHEN:
they are parsed and checked
THEN:
the contract's increment matches, its declared traceability equals the acceptance tests that cite it, every criterion has an implementation-map entry and the scope check reports no reason, the schema versions, policy tokens, identity prefixes, clock order and owner-input status are the exact declared values, and the module holds no hidden clock, randomness, environment read or default owner number

EXPLICITLY OUT OF SCOPE:
- Any trading, admission, allocation, reservation, sizing or execution authority: no order, cancel, modify or confirmation path exists in this increment.
- Any durable store, canonical-writer write, database table, parquet or JSON artifact, or retention policy for anchors, clocks or lifecycle observations. Persistence is a later governed decision.
- Any runtime wiring: no scheduler entry, job, API route, Telegram surface, Compose service, feature flag or release-profile mode key.
- The R5-B horizon *classifier* itself: classification already exists in the frozen `app/opip/contracts/thesis.py` and is reused, not rewritten; this increment adds only the first-fill anchor and the lifecycle clocks.
- Owner numbers (`OQ-03` horizon boundaries and expected holding ranges, `OQ-08` R4 closure), owner assignment (`OQ-01`) and any numeric default for a budget, band or duration.
- Later increments (capital-time view, usefulness gates, paper-v2 activation), F11/F12 protection changes and committee or AI interfaces.
- Realised holding-period measurement, outcome maturation and calibration beyond reading remaining time from preserved instants.

FROZEN BOUNDARIES:
- No production activation, no deploy, no release-profile change and no new infrastructure (no Kubernetes, Redis, managed database, second scheduler or additional droplet).
- No change to fixed risk sizing, minimum reward-to-risk, execution safeguards or any risk/execution gate, and no widening of any Kraken or exchange authority.
- No secret, credential or environment value is read, logged, copied or committed; the module reads no environment at all.
- The canonical writer remains the single writer and is not imported by this module; no live SQLite file, private API or network path is touched.
- The frozen F3/F4/R5-A isolation suites and the canonical, committee, decision, streaming and BC4 safety suites are preserved unweakened; this increment removes or relaxes no assertion of them.
- `docs/atdd/ACTIVE_INCREMENT` advances only to an owner-authorized increment whose scope contract exists, and no test of this increment pins the movable pointer to its own identity.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_001_the_anchor_identity_is_deterministic_and_lineage_bound
AC-001 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_001_the_anchor_record_carries_every_declared_key_without_authority
AC-001 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_001_a_forged_or_drifted_anchor_identity_is_refused
AC-001 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_001_an_anchor_cannot_precede_the_revision_or_its_own_fill
AC-002 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_002_a_later_fill_keeps_the_anchor_identity_and_the_first_fill_instant
AC-002 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_002_a_later_fill_accumulates_quantity_and_weighted_price
AC-002 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_002_a_back_filled_or_out_of_order_fill_is_refused
AC-002 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_002_the_holding_deadline_is_unchanged_by_a_later_fill
AC-003 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_003_the_declared_clock_set_and_order_is_closed
AC-003 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_003_every_deadline_is_the_start_instant_plus_the_owner_gated_duration
AC-003 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_003_a_clock_identity_excludes_the_observation_and_survives_advancement
AC-003 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_003_the_qualification_clock_requires_the_declared_window_and_budget
AC-003 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_003_a_clock_the_revision_cannot_support_is_refused_rather_than_defaulted
AC-004 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_004_advancing_to_the_deadline_expires_the_clock_exactly_once
AC-004 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_004_an_expired_clock_is_terminal_and_never_resumes
AC-004 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_004_a_clock_cannot_be_observed_before_it_started_or_received_out_of_order
AC-004 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_004_a_clock_rejects_a_forged_deadline_or_expiry_instant
AC-004 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_004_the_clock_state_vocabulary_is_closed
AC-005 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_005_a_supported_lifecycle_carries_exactly_the_declared_clocks
AC-005 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_005_a_partial_misordered_or_foreign_clock_set_is_refused
AC-005 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_005_an_abstained_lifecycle_carries_no_clocks_and_stays_identity_bound
AC-005 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_005_lifecycle_instants_must_follow_the_instants_they_describe
AC-005 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_005_a_forged_lifecycle_identity_is_refused
AC-006 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_006_an_unconfigured_policy_abstains_and_starts_no_clock
AC-006 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_006_a_missing_anchor_or_window_abstains_without_guessing_a_clock
AC-006 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_006_a_missing_time_invalidation_abstains
AC-006 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_006_an_unbanded_class_or_a_band_mismatch_abstains
AC-006 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_006_the_owner_input_and_abstention_vocabulary_is_declared
AC-007 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_007_no_anchor_before_classification_or_at_the_declared_invalidation
AC-007 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_007_no_observation_before_a_clock_could_start
AC-007 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_007_no_evidence_received_before_the_instant_it_describes
AC-007 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_007_naive_instants_and_foreign_anchors_are_refused
AC-008 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_008_re_observing_reproduces_the_lifecycle_and_clock_identities
AC-008 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_008_an_identical_replay_is_identical_key_for_key
AC-008 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_008_the_applied_policy_token_is_part_of_the_identity
AC-008 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_008_an_abstention_is_a_distinct_observation
AC-009 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_009_remaining_seconds_derive_from_preserved_instants
AC-009 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_009_remaining_seconds_is_floored_at_zero
AC-009 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_009_an_abstention_or_unknown_clock_reports_unknown
AC-009 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_009_reading_before_a_clock_started_fails_closed
AC-010 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_010_attach_records_the_anchor_and_the_observation_together
AC-010 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_010_re_attaching_the_same_first_fill_reuses_the_recorded_anchor
AC-010 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_010_a_different_first_fill_or_a_foreign_previous_is_refused
AC-011 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_011_the_module_carries_no_authority_storage_or_environment_surface
AC-011 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_011_no_runtime_root_or_application_module_consumes_the_contract
AC-011 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_011_the_frozen_r3_isolation_scans_need_no_widening
AC-011 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_011_release_profile_and_paper_posture_are_unchanged
AC-011 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_011_every_record_declares_itself_non_authoritative
AC-012 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_012_the_increment_scope_contract_is_self_consistent
AC-012 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_012_the_declared_schema_versions_tokens_and_prefixes_are_exact
AC-012 -> tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_012_the_module_holds_no_hidden_clock_randomness_or_environment_read

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/contracts/horizon_lifecycle.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-B-horizon-intelligence.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py

DEFERRED DISCOVERIES:
- Horizon boundary durations and expected holding ranges (`OQ-03`) are unratified, so the shipped `ThesisLifecyclePolicy` defaults to no numbers, every evaluation under the shipped policy abstains, and every band assertion in the suite is labelled an engineering scenario rather than an accepted budget.
- Owner assignment (`OQ-01`) is unresolved; the feature tracker still marks owners `UNASSIGNED` and this increment assigns none.
- R4 closure (`OQ-08`) is not established by this increment; the legacy paper-engine coexistence window and any calibration of realised holding periods stay open.
- No durable store exists for anchors, clocks or lifecycle observations. The records are in-memory research evidence and their persistence, retention and reconciliation are a later governed decision.
- The qualification clock derives its start from `classified_at` and its duration from the declared entry window, so a revision whose window is expressed only relative to a receipt time cannot yet be qualified; that mapping is an owner input.
- Nothing consumes the contract yet: no advisory projection, scheduler or report reads remaining lifecycle time, so runtime usefulness of the lifecycle evidence is `NOT_PROVEN`.
- No production or paper evidence exists for holding-period accuracy; the scenario instants in this increment's tests are synthetic and prove structure and point-in-time integrity only.
- Brittle-test technical debt (recorded, not cleaned up here): the frozen decision-safety scan `tests/test_opip_decision_safety_v1.py::test_no_ml_dependency_is_introduced` lowercases every `app/opip/**/*.py` file and asserts that the four-letter sequence `shap` does not occur anywhere in it, so its real intent (no SHAP ML dependency) is enforced by an over-broad substring that also rejects an ordinary English word. This increment satisfied the frozen scan by re-wording one docstring line in `app/opip/contracts/horizon_lifecycle.py`, wrote no new source into `app/opip`, and touched neither that frozen test nor any allowlist. Narrowing the scan to an import/identifier check is a separate reviewed test change and is deliberately not attempted here.

UNAPPROVED SCOPE CHANGES:
NONE






