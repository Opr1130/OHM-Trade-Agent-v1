INCREMENT:
ATDD-R6-committee-shadow-review-readiness

OWNER-APPROVED INTENT:
Answer the roadmap's R6 question over the Committee package that already exists: is it ready to be put in front of an owner influence review, and what precisely is still missing? The increment adds no Committee feature, role, persona, transport, provider, timer, scheduler, store, event type, release profile, configuration key, order path or trading authority, and it changes no application behaviour: the surface it adds is dormant evidence code with no runtime consumer. It answers six questions and only those six, one per prerequisite the roadmap names. First, is the Committee release under review exactly the core release under test, so a drifted release is blocked and named as drift instead of being reported as a generic failure. Second, is the schedule policy an explicit recorded owner decision, with the timer disabled as the 2026-09-25 proof left it, and can this increment enable it in no case. Third, are the prospective cases real cases sealed by the Committee's own sealing seam, so a stand-in or mock case is refused rather than counted, a case whose own committed horizon has not elapsed is never matured early, and an excluded case is reported with its reason instead of being silently dropped. Fourth, are weakness findings durable, with the in-process registry correctly reported as insufficient evidence while no durable stream declares those findings. Fifth, is a trust report present and measured, with an unknown cost left unknown so that a missing population or an unknown cost can never yield a pass. Sixth, does the Committee review surface still reach no job or API authority plane, measured from the modules themselves rather than asserted. Every prerequisite reports PASSED, BLOCKED or INSUFFICIENT_EVIDENCE with a reason from a closed vocabulary, the overall verdict is a blocked-if-any-blocked projection, and a passing verdict means ready to be reviewed rather than authorized to act.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 13 (`Committee Value and AI Cost`): retain trust stages, role disagreement, timely coverage, paired incremental net and all attempt costs, with opinions remaining advisory. The increment requires a trust report whose gates and costs are measured, and it refuses a pass that rests on an unknown cost.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 15 (`F12 AI research`, P2 retained R6): advisory-only Committee, governed registry, quantitative trust and cost evidence. The increment proves the advisory-only and governed-registry halves without granting influence.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` section 18 (`Readiness gates migration and rollback`): every gate returns PASS, FAIL or INSUFFICIENT_EVIDENCE with a dated evidence manifest, and an unratified numeric threshold, missing population, stale required stream or unknown cost cannot yield PASS. This is the vocabulary the increment reuses (`trust.GateStatus`) and the rule its cost and population findings enforce.
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` `R6 - Committee shadow evidence`: no new Committee feature; the release SHA must equal the core SHA under test; the timer or oneshot policy is an explicit owner decision left disabled by the 2026-09-25 proof; real prospective cases rather than mocks; durable weakness findings rather than an in-process registry that is not a JSONL stream; a trust report emitted from those cases with unknown cost left unknown; and still no import from `app/jobs` or `app/api`.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` row `R6`: `SHADOW` authority, `SC-10` contract, gate `G6`, no open-input identifier, and the retained disposition that AI remains advisory and cannot override a risk rejection.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` `SC-10` and `docs/architecture/OPIP_CONFORMANCE_LEDGER.md`: the Committee plane is measurement-only evidence; it owns no fact that a risk, execution or capital plane consumes, and it may not reach one.
- Existing owning seams the increment consumes without restating: `app/opip/committee/prospective.py` `assert_release_matches` and `SealedPrediction.outcome_eligible_at`, `app/opip/committee/trust.py` `GateResult`/`GateStatus`/`CommitteeInvestment`/`MINIMUM_MATURED_CASES`, `app/opip/committee/weakness.py` `WeaknessRegistry`, and `app/opip/committee/store.py`'s declared durable JSONL streams.


APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the Committee release identity under review, the core release identity under test, and a population of cases the Committee sealed with one of those identities
WHEN:
the release identity prerequisite is assessed
THEN:
a sealed population that matches the core release under test and a Committee release equal to it are reported as satisfied, a case sealed with any other release blocks the prerequisite with `RELEASE_DRIFT` naming both identities, a drifted population is never reported as a generic failure or as insufficient evidence, and a population with no sealed case, or a missing core identity, is insufficient evidence instead of a pass
AC-002:
GIVEN:
the recorded owner decision for the shadow cycle schedule, in turn absent, declared as disabled, declared as oneshot or a timer, and malformed
WHEN:
the timer policy prerequisite is assessed
THEN:
an absent decision is insufficient evidence with `TIMER_POLICY_UNDECIDED` and states that the proof left the timer disabled, a declared decision is satisfied only with a known policy and a named deciding owner, a decision without a named owner or with an unknown policy is refused rather than accepted, and the readiness record reports the timer as not enabled for every policy including oneshot and timer
AC-003:
GIVEN:
the T0 records the Committee sealed, their own committed horizons, the ineligibility records the Committee wrote, and a stand-in object that resembles a sealed case
WHEN:
the prospective case population is measured and its prerequisite assessed
THEN:
only a genuinely sealed prediction is counted as a prospective case, a case whose own horizon has not elapsed is reported as unmatured with its reason and is never counted as matured evidence, an ineligible case is reported with the reason the Committee itself recorded rather than being dropped, a stand-in, mapping or mock case is refused instead of counted, and a matured count below the package's existing support convention is insufficient evidence rather than a pass
AC-004:
GIVEN:
the in-process weakness finding registry, the durable JSONL evidence streams the Committee store declares, and a stream name that is a path or a traversal
WHEN:
the durability prerequisite is assessed
THEN:
findings reachable only through the in-process registry are insufficient evidence with `WEAKNESS_FINDINGS_NOT_DURABLE`, a durable stream is accepted only as one bounded JSONL stream name inside the evidence root, a path, absolute location or traversal segment is refused, and the repository's own evidence store is proved to declare no weakness-findings stream so the honest verdict today is insufficient evidence
AC-005:
GIVEN:
the trust gates the package's own gate seam produced, the investment the report carries, and in turn a measured attributable cost and an unknown one
WHEN:
the trust evidence prerequisite is assessed
THEN:
a blocked gate blocks with `TRUST_GATE_BLOCKED`, an unmeasurable gate is insufficient evidence with `TRUST_GATE_INSUFFICIENT_EVIDENCE`, no gate result at all is insufficient evidence with `TRUST_EVIDENCE_UNMEASURED`, an unknown attributable cost blocks with `COST_UNKNOWN` and is never read as zero or as free, a cost gate that passed while no cost was measured is refused as a contradiction, and only a measured cost with clear gates is satisfied
AC-006:
GIVEN:
the modules that make up the Committee review surface, and separately a module that imports a job or API plane
WHEN:
the import boundary is measured and its prerequisite assessed
THEN:
the measured boundary of the real review surface reports no violation, a module importing `app.jobs` or `app.api` blocks with `FORBIDDEN_IMPORT_PRESENT` naming the module and the import, an unmeasured boundary is insufficient evidence with `IMPORT_BOUNDARY_UNMEASURED` rather than a pass, and the measurement is taken from the module sources instead of being asserted by a caller
AC-007:
GIVEN:
a readiness record whose prerequisites all passed, the readiness module, and the repository's release posture
WHEN:
the record and the module are inspected
THEN:
the record measures only and can influence nothing: it reports no authority granted, no trading authority changed, no automatic promotion and no timer enabled, it cannot be constructed with any of those set, a passing verdict is readiness for review rather than authorization, the readiness module reaches no order, network, storage, clock, randomness or scheduler surface, no application module consumes it, and the release profiles, Compose literals and paper-v2 mode are unchanged
AC-008:
GIVEN:
this increment, its scope contract, the movable pointer, the acceptance-test index, the readiness module's declared surface and the autonomous build report
WHEN:
the contract is parsed and checked against that index and the report
THEN:
every criterion is traced to a matching acceptance-marked test and mapped to a changed path, the pointer resolves to a declared contract instead of being pinned, `UNAPPROVED SCOPE CHANGES` is `NONE`, the module declares exactly its intended public surface, and the build report records this increment's status and the increments still outstanding without claiming unproven completion

EXPLICITLY OUT OF SCOPE:
- No Committee feature, role, persona, provider, transport, prompt, seat, scheduler, timer, cycle or case is added, changed or enabled, and no Committee opinion, weakness type, transport or trust stage vocabulary is extended.
- No influence review is performed and no influence is granted: the increment reports readiness for a review and nothing else. Committee opinions stay advisory and cannot override a risk rejection, an execution safeguard or a capital admission.
- No durable weakness-findings store, archive, index or migration is added, and no existing durable stream is rewritten. The increment reports the durability gap; closing it is a later governed change.
- No provider call is made or modelled, no real case is created, and no Committee cycle is scheduled or run.
- No threshold, support figure, cost ceiling, latency budget or gate threshold is invented. The supportive support convention is the one the trust report already declares.
- The Committee timer decision itself is not taken here: it remains an owner decision and this increment can neither enable nor disable it.
- `OQ-10` and the Committee's remaining owner inputs stay open, and the legacy paper-engine coexistence window named by `OQ-08` is untouched.

FROZEN BOUNDARIES:
- Committee authority stays `SHADOW`; the release posture is unchanged with `EVIDENCE_SHADOW` active, `TARGET_PAPER` blocked and `OPIP_PAPER_V2_MODE` off, and funded trading stays disabled.
- The new surface is measurement-only evidence with no runtime consumer: it grants no authority, changes no risk, sizing, execution or capital gate, and cannot enable a scheduler or timer.
- The increment reuses the owning seams for release identity, gate vocabulary, support convention, weakness registry and durable stream names instead of restating any of them, so no fact gains a second source of truth.
- The increment reads module sources to measure the import boundary and reads caller-supplied records; it writes nothing, opens no network connection, reads no clock and consumes no randomness.
- Every unsatisfied prerequisite names a reason from a closed vocabulary; a satisfied prerequisite cannot carry one.


ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_001_a_matching_committee_release_is_the_only_release_identity
AC-001 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_001_a_drifted_release_blocks_and_is_named_as_drift
AC-002 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_002_an_undecided_timer_policy_is_insufficient_evidence
AC-002 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_002_a_recorded_owner_decision_never_enables_the_timer
AC-002 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_002_an_owner_decision_without_a_named_owner_is_refused
AC-003 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_003_only_matured_sealed_cases_count_as_prospective_evidence
AC-003 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_003_an_immature_or_ineligible_case_is_reported_not_dropped
AC-003 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_003_a_stand_in_case_is_refused_rather_than_counted
AC-004 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_004_findings_held_only_in_process_are_insufficient_evidence
AC-004 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_004_no_durable_weakness_stream_is_declared_by_the_evidence_store
AC-005 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_005_a_measured_cost_with_clear_gates_satisfies_trust_evidence
AC-005 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_005_an_unknown_cost_can_never_yield_a_pass
AC-006 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_006_the_real_review_surface_imports_no_job_or_api_plane
AC-006 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_006_a_forbidden_import_blocks_the_boundary
AC-007 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_007_a_passed_readiness_still_grants_nothing
AC-007 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_007_the_readiness_surface_is_dormant_and_the_posture_is_unchanged
AC-008 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_008_the_increment_contract_pointer_and_build_report_are_self_consistent
AC-008 -> tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_008_the_module_declares_its_whole_public_surface

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py
AC-008 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-008 -> OHM-Trade-Agent-v1/app/opip/committee/shadow_review.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R6-committee-shadow-review-readiness.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py

DEFERRED DISCOVERIES:
- The durability gap this increment reports is real: weakness findings are appended to an in-process `WeaknessRegistry` and the Committee evidence store declares nine durable JSONL streams, none of which holds weakness findings. Where that stream should live, which finding facts are review-critical, and how an existing in-process registry is migrated without rewriting durable evidence stay owner-governed and are not decided here.
- The schedule policy remains an owner decision. This increment can report that no decision is recorded; it neither takes that decision nor enables, disables or schedules anything.
- Real prospective cases do not yet exist in production: the population this increment measures is the Committee's own sealed records, and today that population is empty in the deployed shadow plane, which is why the case prerequisite reports insufficient evidence until real cases mature. Creating cases, scheduling cycles or running providers stays outside this increment.
- An unknown attributable cost stays unknown. What the attributable cost of a provider attempt should include, and how it is priced, stays with the Committee economics module and its owner inputs; no cost model is introduced here.
- `OQ-10` and the other Committee owner inputs remain open, and the interface between a passing readiness verdict and an actual influence review is an owner decision that this increment does not pre-empt.
- The increment reads module sources to measure the import boundary. A broader repository-wide authority-reachability proof remains with the conformance ledger and the increments that own those planes.
- Windows-only full-suite failures observed while validating this increment reproduce at the parent commit and are not attributed to this increment; Linux CI remains the final-validation prerequisite. Ruff is not installed in the local Windows toolchain, so Linux CI stays authoritative for lint.

UNAPPROVED SCOPE CHANGES:
NONE

