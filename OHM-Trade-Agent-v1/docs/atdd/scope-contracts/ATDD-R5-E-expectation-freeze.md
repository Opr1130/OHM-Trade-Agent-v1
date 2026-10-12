INCREMENT:
ATDD-R5-E-expectation-freeze

OWNER-APPROVED INTENT:
Deliver the first R5-E slice: immutable prospective expectation capture for evaluated paper decisions, so no outcome can be recorded before the expectation it is measured against is frozen. The owner mission directive fixes the order inside R5-E -- "start R5-E with immutable SC-10 prospective expectation capture before evaluated paper outcomes, followed by durable canonical capital reservations, F5/F6/F7 admission, realistic F8 paper execution and F11 independent protection" -- and this increment delivers only the first item. It extends the frozen SC-10 owning module (`EXTEND_EXISTING_MODULE`) with the expectation record that module does not yet have, introduces no second outcome engine, no store, no runtime producer and no runtime consumer, and grants no authority to anything.

ARCHITECTURE REFERENCES:
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-10 "Expectation and outcome learning record": frozen record keys (immutable expected payload/hash, cutoff, actual fill/return/duration/cost facts, maturity, coverage, attribution, experiment, correction lineage), owning module `OHM-Trade-Agent-v1/app/opip/learning/evaluation.py`, reuse status `EXTEND_EXISTING_MODULE`, one record per entity/policy/horizon/revision with the expected payload and its hash immutable and the cutoff preserved (`LE-02`), populations kept separate at deduplicated episode/intent grain (`LE-01`), learning cannot edit active policy autonomously, and this contract introduces no second outcome engine.
- `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`, row `R5-E` "Deterministic portfolio paper management with single canonical writer, bounded retries and fail-closed isolation" (v1.5.0 section 9 and section 10, contracts `SC-09` and `SC-11`, open input `OQ-08`, gate `G5`, authority `PAPER_RESEARCH`); row `F9` and clause rows `PA-09` "Freeze expectations before outcomes", `PA-10` "Append actual outcomes safely" and `PA-12` "Retain the full opportunity population" (`SC-10`).
- `docs/architecture/v1.5.0/ARCHITECTURE.md`, clause `LE-01`: TAKEN, REJECTED, MISSED, EXPIRED, NO_FILL and NO_TRADE populations are retained at a deduplicated episode/intent grain; outcomes are PENDING, MATURED, CENSORED or INSUFFICIENT_EVIDENCE with explicit reasons; an open position cohort cannot be declared a failure or a success using an immature tactical window.
- `docs/architecture/v1.5.0/ARCHITECTURE.md`, clause `LE-02`: realized paper outcomes, declared-policy feasible counterfactuals and hindsight excursion measures use separate measures, portfolios are compared with the same capital and timing, mutually incompatible "missed winners" are never summed, and legacy experience is not a frozen expectation.
- `docs/architecture/v1.5.0/ARCHITECTURE.md`, clause `LE-03`: evidence of learning means prospective improvement against a frozen baseline after operating costs with coverage, cohort size and uncertainty; storing more cases does not establish improved intelligence.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-11 "Paper isolation and research environment": research paper management is deterministic and portfolio-aware with no funded write and no exchange order authority, paper cannot generate its own proof, and `EVIDENCE_SHADOW` remains the production posture with `TARGET_PAPER` `BLOCKED`.
- `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`, SC-12 "Canonical ordering, temporal contract and shared writer": `REUSE_EXISTING_MODULE`; exactly one canonical writer owns durable domain records and no new event store, allocator database, outcome engine or position registry is introduced. This increment adds no durability path at all.
- `docs/atdd/README.md` and `AGENTS.md`: the scope-contract mechanism, no trading authority, no unratified policy, and the smallest change that solves the proven problem.
- Owner mission directive of 2026-10-12 (R5-E resume, first slice = SC-10 prospective expectation capture).

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a paper-research decision whose prospective expectation is being captured for one entity, policy, horizon and revision
WHEN:
the expectation record is frozen
THEN:
exactly one immutable record exists for that entity, policy, horizon and revision, its expected payload and its content hash are stored together, its cutoff is preserved unchanged, and a mutated payload, a forged or foreign hash, a dropped frozen key or an extra key fails closed instead of being accepted

AC-002:
GIVEN:
a frozen expectation with a cutoff and a maturity horizon
WHEN:
an outcome attachment is attempted at a recorded time
THEN:
an attachment at or before the cutoff is refused, an attachment before maturity is refused, an attachment carrying no outcome facts is refused, and a refused attachment leaves the frozen expectation byte-identical

AC-003:
GIVEN:
the retained populations of one evaluated decision span
WHEN:
expectations are captured for taken, rejected, missed, expired, no-fill and no-trade episodes
THEN:
each expectation names exactly one population and one deduplicated episode or intent grain, a second expectation for the same grain and population is refused, a population the span declared it would retain and did not capture fails closed, and no population is merged, renamed or silently dropped

AC-004:
GIVEN:
a frozen expectation whose outcome has or has not matured
WHEN:
its outcome status is resolved
THEN:
the status is one of PENDING, MATURED, CENSORED or INSUFFICIENT_EVIDENCE, MATURED requires at least one recorded outcome fact and a coverage figure, CENSORED and INSUFFICIENT_EVIDENCE require at least one explicit reason, and an expectation inside an immature window can never resolve to MATURED

AC-005:
GIVEN:
realized paper outcomes, declared-policy feasible counterfactuals and hindsight excursion measures
WHEN:
they are recorded against a frozen expectation
THEN:
each measure is recorded in its own kind slot with its own grain, one record cannot carry two kinds in the same slot, no total is produced across measure kinds, and an attempt to sum incompatible "missed winner" measures is refused

AC-006:
GIVEN:
the expectation surface added by this increment
WHEN:
the owning module and the repository runtime roots are inspected
THEN:
the record is non-authoritative and non-promotable, it writes no store and publishes no event, no runtime root imports or consumes it, and no second outcome engine, event store, allocator database or position registry is introduced

AC-007:
GIVEN:
this increment's contract, the ATDD pointer, its acceptance tests and the release profiles
WHEN:
the scope gate and the profile checks run
THEN:
the contract declares this increment and its traceability is exactly the scanned acceptance index for this increment, the pointer resolves to an existing scope contract, and `EVIDENCE_SHADOW` stays the resolved profile with `TARGET_PAPER` still blocked

EXPLICITLY OUT OF SCOPE:
- Durable canonical capital reservations, F5/F6/F7 admission, realistic F8 paper execution and F11 independent protection: later R5-E slices with their own increments and contracts
- Any actual outcome engine change, `paper_outcome.py` terminal-outcome schema change, `paper_outcome_reader.py` change, or any write to a canonical store or SQLite file
- Any runtime wiring: no job, API route, scheduler entry, Feature Bus topic or cockpit page may consume these records in this increment
- Any policy, threshold, advantage, cooldown, horizon boundary, coverage minimum or promotion rule: `OQ-05`, `OQ-07`, `OQ-09` and `OQ-10` stay open and nothing here ratifies them
- Any promotion, champion/challenger promotion decision, model training, or autonomous policy edit, and any change to the existing champion/challenger evaluation behaviour
- Any funded, exchange, order, reservation or paper-execution authority, and any release-profile change
- Retirement of the legacy paper engine or any R7 legacy disposition

FROZEN BOUNDARIES:
- `app/opip/learning/evaluation.py` stays the single SC-10 owner; no second expectations or outcomes module is created and the existing champion/challenger evaluation keeps its current behaviour.
- The canonical writer remains the only durable domain write path, and this increment writes nothing durably.
- Paper and research remain isolated from funded order endpoints, funded credentials and exchange authority; `EVIDENCE_SHADOW` remains the production posture.
- A frozen expectation is evidence, never an authorization: it cannot place, modify, cancel or confirm an order and cannot change policy.
- Learning recall of a record never rewrites the expected payload or its hash; corrections are recorded as lineage, not as mutation.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_001_the_frozen_expectation_is_complete_and_immutable
AC-001 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_001_a_mutated_payload_or_forged_hash_fails_closed
AC-001 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_001_a_replayed_payload_with_a_dropped_or_extra_key_fails_closed
AC-002 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_002_an_outcome_before_the_cutoff_or_maturity_is_refused
AC-002 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_002_a_refused_attachment_leaves_the_expectation_byte_identical
AC-003 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_003_each_population_is_retained_at_a_deduplicated_grain
AC-003 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_003_a_duplicate_grain_is_refused_and_no_population_is_dropped
AC-004 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_004_the_outcome_status_vocabulary_is_closed_and_reasoned
AC-004 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_004_an_immature_window_can_never_mature
AC-005 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_005_the_three_measure_kinds_stay_separate
AC-005 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_005_a_missed_winner_is_never_summed_into_a_realized_total
AC-006 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_006_the_expectation_surface_grants_no_authority_or_storage
AC-006 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_006_no_runtime_root_consumes_the_expectation_records
AC-007 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_007_the_increment_scope_contract_is_self_consistent
AC-007 -> tests/test_opip_r5_e_expectation_freeze.py::test_ac_007_release_profile_and_non_authority_posture_are_unchanged

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/learning/evaluation.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-E-expectation-freeze.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py

DEFERRED DISCOVERIES:
- The owner mission continues after this slice with durable canonical capital reservations, F5/F6/F7 admission, realistic F8 paper execution and F11 independent protection. Each is a separate increment with its own contract; none is authorized here, and this contract's criteria validate none of them.
- The candidate-panel and F6 forecast-identity binding raised by the PR #363 round-5 review as a `P2` finding is still open and belongs to the forecast-identity work those slices carry. This increment binds an expectation to its entity, policy, horizon and revision and to the engine's declared baseline; it deliberately invents no forecast identity.
- Whether a frozen expectation should be registered through the canonical writer inside the SC-12 owner, or through a separate learning-plane durable path, is `OQ-09`/`OQ-10` territory and is not decided here. This increment stores nothing durably, so the question stays open rather than silently answered.
- Attaching a matured paper outcome from the canonical outcome reader to a frozen expectation is an integration slice of its own; `paper_outcome.py` and `paper_outcome_reader.py` are untouched here.
- Coverage minima, censoring thresholds, horizon boundaries and the operating-cost definition a future prospective comparison needs remain owner inputs (`OQ-07`, `OQ-09`); no number is ratified, defaulted or implied.

UNAPPROVED SCOPE CHANGES:
NONE

