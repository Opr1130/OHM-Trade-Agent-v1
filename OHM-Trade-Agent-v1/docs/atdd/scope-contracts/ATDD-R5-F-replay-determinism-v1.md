INCREMENT:
ATDD-R5-F-replay-determinism-v1

OWNER-APPROVED INTENT:
Establish, by behaviour, that the frozen R5-F prospective expectation and canonical-outcome learning surfaces replay deterministically and that repeated processing of the same committed evidence adds nothing twice. This increment is test-and-documentation only: it adds no application module, no store, no writer, no scheduler, no route, no configuration key, no release-profile change and no trading authority, and it changes no application behaviour. It answers five questions and only those five. First, does independently reconstructing a prospective expectation from identical decision evidence produce the same identity, expected-payload hash and canonical serialization, and does that serialization round-trip losslessly through the frozen SC-10 record. Second, does independently evaluating the same frozen expectation against the same verified canonical terminal outcome produce identical economic facts, attachment identity, maturity and disposition. Third, does repeated delivery of one committed outcome identity add no second economic contribution and no duplicate learning disposition, using the population-grain and canonical-idempotency contracts that already exist. Fourth, where the approved contract defines order-independent semantics, do different valid delivery orders produce the same normalized economic and disposition results, while invalid temporal sequences are still refused. Fifth, does repeated processing of identical malformed, immature, conflicting or hindsight-contaminated evidence produce stable refusal classifications, and does the learning path invoke no writer, promote no policy, activate no mode and hold no authority. It records exactly what remains unproven: determinism here means that identical inputs produce identical outputs, not that the module is free of any wall-clock dependence anywhere, and no production caller is added.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `SC-10`: the prospective expectation is a frozen record captured ex ante, carrying an expected payload and a content-derived hash that can never be rewritten, only superseded through recorded correction lineage. This increment proves capture determinism and lossless round-trip of that record and nothing further.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LE-01`: retained populations are measured at a deduplicated episode grain and an immature cohort cannot be declared a success or failure. This increment proves that re-delivery of one grain contributes once and that an immature window never matures under replay.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LE-02`: realized, counterfactual and hindsight measures stay separate and one measure kind occupies one grain slot. This increment proves the realized series is reproduced identically under replay and order change.
- `docs/architecture/v1.5.0/ARCHITECTURE.md` `LE-03`: learning evidence is prospective, measurement-only and can never promote a policy. This increment proves the comparison and disposition records carry no promotion and no authority after replay.
- `docs/atdd/scope-contracts/ATDD-R5-F-prospective-outcome-integration.md`: the parent increment whose frozen behaviour this increment only observes. That contract is not modified here.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
one decision evidence record and an independently rebuilt copy of it, and separately the durable SC-10 payload of a frozen expectation
WHEN:
the production capture seam freezes an expectation from each evidence record, and the production loader rebuilds the expectation from its own payload
THEN:
the two captures carry the identical expectation identity, expected-payload hash and canonical serialization, the rebuilt record is identical to the captured one in identity, hash, payload and frozen temporal fields, neither record claims authority, and a payload whose expected net has been rewritten fails closed instead of loading
AC-002:
GIVEN:
one committed canonical terminal outcome and two independently captured expectations of the same grain
WHEN:
each expectation is joined to that outcome by the production seam and the maturity and consumption records are derived
THEN:
the attachment identity, the economic facts in their frozen units, the committed capital and the resolution are identical across the two evaluations, the single-episode population economics agree, and the consumption disposition is identical and evidence-only
AC-003:
GIVEN:
one committed outcome identity evaluated repeatedly, the disposition of one artifact, and two payloads carrying the same canonical outcome identity with different economics
WHEN:
the measure series, the population economics, the retained disposition set and the canonical idempotency key are derived again
THEN:
the repeated derivations reproduce the first exactly, the same artifact delivered twice inside one collection is refused rather than summed, a duplicated or empty disposition set is refused, and the idempotency key is stable and bound to outcome identity rather than to the payload's economics
AC-004:
GIVEN:
three matured attachments of one population whose figures are exact in the frozen unit, and separately an evaluation moment that precedes an episode's own exit plus an expectation whose window is still immature
WHEN:
the population is evaluated under every delivery permutation, and the invalid temporal cases are evaluated and re-evaluated
THEN:
every permutation yields the identical economics record, measure series and normalized dispositions, the temporal violation is refused with the same classification every time, and the immature window is never matured
AC-005:
GIVEN:
four distinct inadmissible inputs, and the release-profile contract, the frozen expectation, a consumption disposition and the learning module's own imports
WHEN:
each inadmissible input is processed twice and the authority surface is inspected after a full evaluate-and-dispose cycle
THEN:
each input produces the same refusal type and message both times, the profiles and the paper-v2 mode are unchanged, the records declare no authority, and the module imports no writer, exchange, storage, job, Telegram, network or process surface

EXPLICITLY OUT OF SCOPE:
- Any application module, including `app/opip/learning/evaluation.py`. This increment observes that module and does not modify it.
- Any modification of `ATDD-R5-F-prospective-outcome-integration.md` or any other existing scope contract.
- Any change to `tests/atdd_scope.py`, the acceptance index, the release profiles, Compose, the release-profile gate or any workflow.
- Any durable store, writer call, scheduler, route, event type, configuration key or migration.
- Wall-clock or network access. Every instant is an explicit frozen timestamp.
- Any threshold, support minimum, horizon boundary or economic constant that the parent contract did not already freeze.

FROZEN BOUNDARIES:
- `EVIDENCE_SHADOW` stays the only valid runtime profile; `TARGET_PAPER` stays blocked; `OPIP_PAPER_V2_MODE` stays `off`.
- The canonical writer remains the single writer and is not invoked by the learning path.
- F11 independent protection, fixed risk sizing, minimum reward-to-risk and every execution safeguard are untouched.
- No policy promotion, no automatic promotion and no trade authority is introduced; `can_promote`, `automatic_promotion` and `trade_authority_changed` remain false.
- Existing frozen temporal and financial units are preserved; no economic zero is inferred from absent evidence.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_001_independent_capture_reconstructs_one_identical_expectation
AC-001 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_001_the_frozen_record_round_trips_through_its_durable_payload
AC-001 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_001_a_mutated_payload_fails_closed_instead_of_loading
AC-002 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_002_independent_evaluation_produces_one_identical_attachment
AC-002 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_002_maturity_and_disposition_are_identical_across_replay
AC-003 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_003_a_repeated_outcome_identity_adds_no_second_contribution
AC-003 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_003_a_duplicate_disposition_is_refused_not_registered_twice
AC-003 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_003_the_canonical_idempotency_key_is_bound_to_outcome_identity
AC-004 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_004_economics_are_invariant_under_valid_delivery_order
AC-004 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_004_an_invalid_temporal_sequence_is_refused_whatever_the_order
AC-005 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_005_repeated_refusals_are_stable_across_classifications
AC-005 -> tests/test_opip_r5_f_replay_determinism_v1.py::test_ac_005_the_learning_path_grants_no_authority_and_activates_no_mode

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_replay_determinism_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_replay_determinism_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_replay_determinism_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_replay_determinism_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f_replay_determinism_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F-replay-determinism-v1.md
AC-005 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- Determinism here is behavioural: identical inputs produce identical outputs. Any claim that the module holds no wall-clock dependence anywhere would need a separate static review and is not made.
- The canonical writer's conflict policy for one outcome identity carrying two different payloads is exercised only at the key-derivation boundary here; the writer's own refusal of a conflicting payload belongs to the canonical contract and is not re-proved.
- Population economics for a single-episode population reports a support below any approved minimum; that sufficiency question belongs to the parent increment's thresholds and no new threshold is invented here.
- The evaluator refuses a declared minimum support below two; this increment passes two, and no policy meaning is attached to that floor.
- No production caller is added, so replay determinism is proved for the callable surface and not for a deployed consumer.

UNAPPROVED SCOPE CHANGES:
NONE

