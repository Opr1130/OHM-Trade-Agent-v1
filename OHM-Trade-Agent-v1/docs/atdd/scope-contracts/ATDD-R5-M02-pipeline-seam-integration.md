INCREMENT:
ATDD-R5-M02-pipeline-seam-integration

OWNER-APPROVED INTENT:
This is the integration increment of the owner-directed Mission 02 work, inside frozen architecture v1.5.0. The audit increment recorded that the continuous advisor pipeline has two halves that are each verified and have never been joined: the engine (F1 coverage, EYE attention, F2 snapshot, F3 IGNITION, F4 episode and horizon thesis) proven by the R5-A and R5-B suites, and the target spine (F5 feasibility, F6 calibrated forecast, F7 selection and handoff) proven by the R4-B0 and R2-A suites. `ATDD-R4-B1-runtime-integration-dormant` records why nobody has joined them: production passes no snapshots and no composition callable, so the target path is inert. This increment closes that seam in process, with the real production callables and the frozen fixtures, and asserts what the seam is supposed to guarantee.

What changes where. No application module changes. This increment adds one acceptance-test module plus the records that accompany it, and it extends the audit's wiring vocabulary with one value so the seam's production wiring is named precisely. It adds no route, producer, schema, writer, job, scheduler entry, configuration key or authority.

What is proven. That the real engine, driven over two consumed observation intervals, emits exactly one claim carrying one episode, one horizon thesis and the sealed snapshot it evaluated; that the episode the engine opens is equal field for field to the episode the canonical lifecycle path opens for the same claim, and that an unratified lifecycle policy is refused outright so the two paths cannot diverge on their version tokens; that the real F5, F6 and F7 functions accept the engine's own episode and bind their decisions to that episode identity; that the joined traversal is decision-for-decision the traversal R4-B0 already proved; and that the refusals survive the join.

The negatives this increment covers, at the join rather than in isolation: a missing snapshot, a stale snapshot one grid step behind the due evaluation, a snapshot belonging to a foreign instrument, duplicate at-least-once delivery of an already evaluated instant, an episode requested at an instant before the claim's own cut-off, a proven F5 VETO, an untrusted calibration report, a tampered forecast policy version, and the cash alternative as a state distinct from both a selection and an abstention.

The distinction this increment keeps. `NO_PRODUCTION_SEAM_WIRING` stays true: production still supplies neither a snapshot source nor a composition callable, exactly as `ATDD-R4-B1-runtime-integration-dormant` records. Proving the in-process traversal does not wire it, does not activate it and does not make it authoritative. No production admission, execution, reservation or paper authority changes.

The correction this increment carries forward. The audit first recorded the two F4 episode-opening paths as a latent identity fork. `OpportunityLifecyclePolicy.__post_init__` refuses any unratified `episode_schema_version`, `lifecycle_version` or `policy_version`, so the agreement is guaranteed rather than coincidental. This increment asserts the guarantee - the equality, the refusal and the stability - so the corrected finding cannot silently reverse.

KEY LAW - JOINING TWO VERIFIED HALVES PROVES THE JOIN, NOT THE WHOLE. A traversal that succeeds once over a frozen fixture is evidence about the seam, not about production behaviour, and not about any page's or stage's parity.

KEY LAW - AN INERT PATH STAYS INERT. This increment reaches the real F5, F6 and F7 callables and no writer, no exchange surface, no protection surface and no scheduler. The engine remains the literal off mode with NONE authority reading no environment variable, and the test asserts it.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md sections 2, 3 and 5: the unified F1-F7 path, the single canonical writer, and the acknowledgement-after-durable-commit rule this increment's idempotency assertion relies on.
- docs/atdd/scope-contracts/ATDD-R4-B1-runtime-integration-dormant.md: the dormancy posture this increment proves the traversal of without changing, and the deferred discovery that no committed-feature-snapshot read seam exists.
- docs/atdd/scope-contracts/ATDD-R4-B0-spine-contract-closure.md: the closed spine whose composition this increment must match rather than replace.
- docs/atdd/scope-contracts/ATDD-R5-A-continuous-opportunity-engine.md and ATDD-R5-B-horizon-intelligence.md: the engine and horizon contracts whose real callables this increment drives.
- docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md: the audit rows this increment closes by evidence, and the rows it deliberately leaves unchanged.
- app/opip/events/opportunity_engine.py, app/opip/feasibility.py, app/opip/forecast.py, app/opip/portfolio_selector.py, app/opip/opportunity_lifecycle.py, app/opip/contracts/opportunity.py, app/opip/contracts/opportunity_phases.py: the production callables and contracts the seam test drives and probes.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-210:
GIVEN:
the real continuous engine, two consumed observation intervals on the frozen grid and the sealed F3 snapshot for each cut-off
WHEN:
one observation-and-attention step and one evaluation step are driven per interval, and separately a due evaluation is served with no snapshot and with a snapshot whose interval cannot yet claim
THEN:
the first interval produces no claim, the second produces exactly one claim carrying one episode, one horizon thesis, the detector phase and the snapshot it evaluated, the engine retains that episode under the same identity, a missing snapshot fails closed with a recorded reason and no episode, and a non-claiming served evaluation is recorded as NO_CLAIM

AC-211:
GIVEN:
one admissible IGNITION claim produced by the real detector, the two F4 episode-opening entry points, the lifecycle policy's version tokens, and a delivered snapshot whose cut-off or instrument does not match the due evaluation
WHEN:
the episode is opened through both entry points, an unratified lifecycle policy is constructed, an episode is requested before the claim's own cut-off, and a stale and a foreign snapshot are served
THEN:
the two episodes are equal field for field with the same opening event identity, every unratified policy token is refused so the paths cannot diverge, a pre-cut-off instant is refused rather than back-dated, and the stale and foreign snapshots fail closed naming their mismatch with no episode opened

AC-212:
GIVEN:
an episode the real continuous engine produced from its own F1, F2, F3 and F4 work, and the independently verified R4-B0 composition over the same frozen fixtures
WHEN:
F5 feasibility, F6 forecast and F7 selection are driven from the engine's own episode, and the joined traversal's identities are compared with the composition's
THEN:
F5 is feasible and bound to that episode, F6 binds its decision to the same episode and to the feasibility decision, F7 selects with an allocation and a reservation plan, and the claim, episode, snapshot, feasibility, forecast and selection identities all agree with the composition

AC-213:
GIVEN:
the joined traversal replayed twice from a fresh engine, a duplicate delivery of an already evaluated instant, the production empty model registry, a complete forecast whose expected return does not beat cash, a tampered forecast policy version, and feasibility evidence carrying an explicit structural rejection
WHEN:
the point-in-time traces are compared, the duplicate is served, and F6 and F7 are driven on each variant
THEN:
both traces are identical element for element, the duplicate produces no second claim or episode, an untrusted calibration report abstains with its own reason and no probability and F7 reports missing evidence rather than cash, the cash alternative is a distinct state with a cash reason and no abstention reason, the tampered policy version is refused, and a proven veto is reported as VETO and cannot be forecast

AC-214:
GIVEN:
the seam increment's own test module and the engine it drives
WHEN:
the engine's compiled mode, authority and environment reads are inspected and the module's imports are read
THEN:
the engine is still the literal off mode with NONE authority reading no environment variable, and this module imports no canonical writer, exchange, Telegram, registry-io, storage, job or configuration surface

AC-215:
GIVEN:
the audit's integration matrix, its declared wiring vocabulary and the audit acceptance-test module that asserts that vocabulary
WHEN:
the matrix's axes block and the test module's declared vocabulary are read
THEN:
both name the seam's production wiring precisely as NO_PRODUCTION_SEAM_WIRING, the superseded UNPROVEN_BETWEEN_HALVES value appears in neither, and every other wiring value the matrix declares is still asserted by the audit's own tests

EXPLICITLY OUT OF SCOPE:
- Any change to an application module, route, producer, schema, store, writer, job, cron unit or configuration key.
- Any production wiring of the snapshot source or the composition callable. The seam is proven in process; it is not wired.
- Any activation of the Feature Bus, Paper-v2, the target spine, the continuous engine mode or any release profile.
- Any change to F5 veto precedence, F6 calibrated authority, F7 selection authority, admission, capital reservation or paper execution.
- Any claim of parity for any page or stage, and any claim of production proof.
- Candidate requalification across the seam, which remains NOT_YET_TESTED in the audit matrix and is not asserted here.

FROZEN BOUNDARIES:
- The F1-F7 ownership recorded in docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md is unchanged.
- The single canonical writer remains the only authority that commits operational domain events and projections, and this increment writes none.
- The dormancy posture of ATDD-R4-B1-runtime-integration-dormant is unchanged: the target spine stays non-authoritative and the legacy paper engines stay authoritative.
- The audit's own matrix is extended by evidence only: no row's proof axis changes and no F1-F7 row moves.
- The audit's acceptance tests are not weakened; one vocabulary value is renamed to name the seam's production wiring precisely, and no assertion is removed.
- No new detector family, statistical forecast authority, selector authority or scheduler topology is introduced.

ACCEPTANCE TEST TRACEABILITY:
AC-210 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_210_the_engine_carries_one_claim_one_episode_and_one_thesis
AC-210 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_210_a_missing_detector_evaluation_fails_closed_without_a_claim
AC-211 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_211_the_engine_and_the_lifecycle_path_produce_one_episode
AC-211 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_211_an_unratified_lifecycle_policy_is_refused_not_forked
AC-211 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_211_a_cutoff_before_the_claim_is_refused
AC-211 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_211_a_stale_snapshot_is_refused_before_the_spine_is_reached
AC-211 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_211_a_foreign_instrument_snapshot_is_refused
AC-212 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_212_the_spine_consumes_the_engines_own_episode
AC-212 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_212_the_joined_traversal_matches_the_spine_composition
AC-213 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_213_the_whole_traversal_replays_to_one_identical_trace
AC-213 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_213_an_unsupported_calibrated_probability_abstains
AC-213 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_213_the_cash_alternative_is_distinct_from_abstention
AC-213 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_213_a_tampered_forecast_policy_version_is_refused
AC-213 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_213_a_proven_veto_cannot_be_forecast
AC-214 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_214_the_seam_activates_no_mode_and_reaches_no_writer
AC-215 -> tests/test_opip_r5_m02_pipeline_seam.py::test_ac_215_the_audit_vocabulary_names_the_seam_wiring

IMPLEMENTATION MAP:
AC-210 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M02-pipeline-seam-integration.md
AC-210 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_pipeline_seam.py
AC-211 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_pipeline_seam.py
AC-212 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_pipeline_seam.py
AC-213 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_pipeline_seam.py
AC-214 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_pipeline_seam.py
AC-210 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-211 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-212 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-213 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-215 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F1_F7_INTEGRATION_MATRIX.md
AC-215 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_integration_matrix.py
AC-215 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m02_pipeline_seam.py
AC-210 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-210 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The joined traversal proves the seam in process only. Production still passes no snapshot source and no composition callable, so `NO_PRODUCTION_SEAM_WIRING` stays true and the target path stays inert until an owner decides otherwise, exactly as ATDD-R4-B1 records.
- An untrusted calibration report and a missing calibrated model are different abstentions. With an artifact present and an empty registry, F6 abstains with `CALIBRATION_REPORT_UNTRUSTED` rather than `NO_CALIBRATED_MODEL`; the seam test asserts the first and this note records the distinction so neither reason is assumed to be the other.
- The engine's committed outcome still carries the claim identity but not the claim object, so the seam test obtains the claim from the frozen detector sequence rather than from the engine's public output. That gap is unchanged and remains an owner decision recorded in the audit's proposal.
- Candidate requalification across the seam is not proven here. F4 requalification is covered by its own increment and by the R5-A suite, but the interaction between a requalified episode and a candidate already carrying a forecast is left `NOT_YET_TESTED` rather than assumed, because F7's requalification semantics belong to `ATDD-R5-D`.
- The seam test reaches the real F5, F6 and F7 callables through the same in-process composition callable the dormant wiring injects in tests. It does not exercise the unified cycle, the scheduler or any job entry point, and it makes no claim about them.
- No headless browser harness exists in this repository, and this increment makes no browser end-to-end claim.

UNAPPROVED SCOPE CHANGES:
NONE


