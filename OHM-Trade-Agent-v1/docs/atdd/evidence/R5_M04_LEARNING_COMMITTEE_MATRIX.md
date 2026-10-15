# R5 Mission 04 coverage matrix: expected-versus-actual learning, governance and the AI Committee

Increment: `ATDD-R5-M04-learning-committee-audit`.
Authority: audit and record only. This document implements nothing and authorizes
nothing. It records, for every item Mission 04 asked to prove, whether the repository
already proves it and which named test proves it.

An item is recorded `PROVEN` only when a named acceptance test exists. Anything else is
recorded `PARTIAL`, `ABSENT` or `OWNER_GATED`, and every such item is named by exactly
one follower increment.

Every citation is machine-probed by `tests/test_opip_r5_m04_coverage_matrix.py`: the
cited file must exist and the cited node must appear in it, so a renamed or deleted test
breaks this document rather than silently weakening it.

## 1. Vocabulary

```text
CLASSES:
  PROVEN
  OWNER_GATED

WORKSTREAMS:
  A   expected versus actual (F9, R5-F, SC-10)
  B   learning governance (R5-F, R6, SC-10)
  C   AI Committee (R6, F12, SC-12)
  D   AI economics (R6, F12)
  E   read-only operator evidence (F10, SC-12)

NEGATIVE:
  N1..N14   the mandatory negative tests Mission 04 named
```

Frozen ownership this audit does not disturb: **F6** statistical forecast ownership,
**F9** learning ownership, and **F12** advisory-only status.

## 2. Workstream A - expected versus actual

```text
ITEM: A1 | immutable-pre-outcome-expectation-freeze | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py::test_ac_001_the_frozen_expectation_is_complete_and_immutable
ITEM: A2 | exact-prediction-and-decision-identity | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_prospective_v1.py::test_the_sealed_horizon_participates_in_the_prediction_identity
ITEM: A3 | horizon-class-and-policy-identity | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_prospective_v1.py::test_the_sealed_release_participates_in_the_prediction_identity
ITEM: A4 | original-evidence-cutoff | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_001_an_ex_ante_decision_capture_is_frozen_at_or_before_its_cutoff
ITEM: A5 | matured-versus-immature-outcomes | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py::test_ac_004_an_immature_window_can_never_mature
ITEM: A6 | correct-pending-and-censored-handling | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_005_an_unresolved_or_censored_outcome_is_never_matured
ITEM: A7 | expected-versus-realized-result | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py::test_ac_005_the_three_measure_kinds_stay_separate
ITEM: A8 | fees-and-costs-deducted-exactly-once | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_002_a_payload_whose_net_disagrees_with_gross_minus_fees_is_refused
ITEM: A9 | capital-occupancy-and-elapsed-holding-time-when-supported | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_003_capital_time_is_occupied_capital_times_realized_duration
ITEM: A10 | time-invalidation-versus-price-invalidation | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_b_horizon_lifecycle.py::test_ac_004_advancing_to_the_deadline_expires_the_clock_exactly_once
ITEM: A11 | rejected-and-no-fill-opportunity-accountability | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py::test_ac_003_a_duplicate_grain_is_refused_and_no_population_is_dropped
ITEM: A12 | weakness-classification-and-root-cause-lineage | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_a_hypothesis_requires_a_validated_weakness_to_be_grounded
ITEM: A13 | model-and-release-identity | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_001_a_drifted_release_blocks_and_is_named_as_drift
ITEM: A14 | no-retrospective-label-contamination | workstream=A | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_001_a_decision_clock_after_the_cutoff_is_refused_as_hindsight
```

Three further A-side facts are proven and worth naming because they carry the
"never invent, never revise" discipline this workstream depends on:
`test_ac_001_a_mutated_payload_or_forged_hash_fails_closed` and
`test_ac_002_a_refused_attachment_leaves_the_expectation_byte_identical` (a refused
attachment cannot rewrite the frozen expectation), and
`test_ac_002_realized_measures_never_carry_a_counterfactual_or_hindsight_kind`.

## 3. Workstream B - the full governed chain

Mission 04 named the chain weakness → hypothesis → experiment → evaluation → reviewed
artifact → owner approval → observed adoption → post-change evaluation. It is
implemented as one assembled lineage in `app/opip/committee/learning.py` and proven link
by link.

```text
ITEM: B1 | weakness | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_weakness_v1.py::test_the_summary_reports_every_state_even_at_zero
ITEM: B2 | hypothesis | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_a_hypothesis_records_mechanism_cohort_prediction_and_effect
ITEM: B3 | experiment | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_an_experiment_freezes_every_choice_on_every_axis
ITEM: B4 | evaluation | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_a_resolved_conclusion_must_reference_its_evaluation
ITEM: B5 | reviewed-artifact | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_a_release_cannot_exist_without_a_conclusion
ITEM: B6 | owner-approval | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_a_release_must_name_the_exact_approved_sha
ITEM: B7 | observed-adoption | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_effectiveness_reports_the_observed_delta
ITEM: B8 | post-change-evaluation | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_an_incomparable_cohort_must_state_why_and_report_no_metric
ITEM: B9 | no-autonomous-active-policy-mutation | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_a_conclusion_never_authorises_a_policy_change
ITEM: B10 | no-unsupported-causal-improvement-claims | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_an_unmeasured_effect_stays_null
ITEM: B11 | no-historical-hindsight-reclassified-as-prospective | workstream=B | class=PROVEN | contract=SC-10 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_003_only_matured_sealed_cases_count_as_prospective_evidence
```

The chain is proven as a *chain*, not only link by link, by three further tests:
`test_a_chain_assembles_the_full_lineage_in_order`,
`test_the_stage_reports_the_furthest_completed_link` and
`test_the_chain_can_be_looked_up_by_hypothesis`. A release must also record **who
decided** (`test_a_release_records_who_decided`), and human review is the only terminal
outcome available to the legacy learning path
(`tests/test_wave4_learning_governance.py::test_learning_can_only_reach_human_review`).

## 4. Workstream C - AI Committee

```text
ITEM: C1 | advisory-only-operation | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_safety_v1.py::test_committee_declares_non_authority
ITEM: C2 | role-model-and-provider-identity | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_attribution_v1.py::test_the_provider_sample_threshold_participates_in_the_identity
ITEM: C3 | trust-stages-and-blockers | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_latency_v1.py::test_the_trust_report_reports_unknown_latency_as_null
ITEM: C4 | prospective-paired-comparison | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_prospective_v1.py::test_the_sealed_horizon_survives_a_storage_round_trip
ITEM: C5 | committee-correctness-and-false-interventions | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_attribution_v1.py::test_added_information_honors_the_reports_declared_threshold
ITEM: C6 | timely-versus-late-review | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_latency_v1.py::test_latency_is_measured_not_inferred_from_timestamps
ITEM: C7 | no-response-and-retry-handling | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_latency_v1.py::test_failover_records_a_duration_for_each_attempt
ITEM: C8 | bounded-optional-ai-workload | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_safety_v1.py::test_no_committee_module_schedules_or_calls_itself
ITEM: C9 | no-ai-override-of-f5-f7-f8-or-f11 | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_safety_v1.py::test_no_application_module_outside_the_package_imports_the_committee
ITEM: C10 | no-committee-timer-activation | workstream=C | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_002_a_recorded_owner_decision_never_enables_the_timer
```

C1 and C9 are carried by more than one assertion, and the reinforcement matters:
`test_committee_contracts_expose_no_authority_field`,
`test_committee_reports_are_never_wired_to_a_production_consumer`,
`test_no_runtime_root_reaches_into_the_committee_plane`,
`test_bake_off_can_never_promote_or_rank_for_action`,
`test_evaluation_module_names_contain_no_promotion_surface`,
`test_attribution_carries_an_explicit_non_promotion_disposition` and
`test_committee_prompt_asserts_non_authority`. C10 is also carried by
`test_committee_ships_dark_and_only_shadow_is_permitted` and
`test_ac_002_an_undecided_timer_policy_is_insufficient_evidence`.

## 5. Workstream D - AI economics

The mission asked whether canonical invocation-attempt evidence already exists for each
of these facts. The answer is item by item. Most of it is proven by the
decision-intelligence plane's `ModelInvocation` rather than by the committee plane's
smaller `ProviderCallOutcome`, which is a distinction the audit's first pass missed.

```text
ITEM: D1 | provider-and-model | workstream=D | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_bake_off_v1.py::test_provider_failures_are_counted_but_never_scored_as_calls
ITEM: D2 | input-output-and-cache-tokens | workstream=D | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_decision_intelligence_p1a.py::test_writer_rejects_non_integer_values_across_remaining_di_integer_fields
ITEM: D3 | billed-versus-estimated-cost | workstream=D | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_decision_intelligence_evidence_reader_v1.py::test_invocation_cost_evidence_is_raw_and_never_totalled
ITEM: D4 | retry-cost | workstream=D | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_410_a_retry_carries_its_own_attributable_cost
ITEM: D5 | failed-attempt-cost | workstream=D | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_411_a_failed_attempt_retains_its_cost_and_its_completeness
ITEM: D6 | latency-and-timeout | workstream=D | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_latency_v1.py::test_latency_is_measured_not_inferred_from_timestamps
ITEM: D7 | complete-attributable-cost-basis-is-canonical | workstream=D | class=OWNER_GATED | contract=SC-12 | evidence=OHM-Trade-Agent-v1/app/opip/committee/store.py
ITEM: D8 | matched-policy-incremental-economic-value | workstream=D | class=PROVEN | contract=SC-12 | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_economics_v1.py::test_incremental_operating_net_subtracts_attributable_cost
```

What this means, stated precisely - and it corrects the audit's own first pass:

- **Present, durable and measured:** the decision-intelligence plane's `ModelInvocation`
  is the richest invocation-attempt contract in the repository. It carries
  `provider`, `model`, `provider_model_version`, `route_version`, `prompt_version`,
  `reasoning_mode`, `attempt`, `input_tokens`, `output_tokens`, **`cached_tokens`**,
  `latency_micros`, `queue_time_micros`, `provider_time_micros`,
  **`billed_cost_microunits`** and `estimated_cost_microunits`, `price_version`,
  `currency`, `reconciliation_status`, `cost_completeness` and provenance, and it fails
  closed when an invocation has no cost at all but is not marked `UNKNOWN` or
  `INCOMPLETE`. `app/opip/decision_intelligence/evidence_reader.py` reconstructs
  invocations from it. The committee plane's `ProviderCallOutcome` carries the
  per-attempt subset that the Committee's own bake-off needs.
- **Declared, not measured, but only in the committee plane:**
  `app/opip/committee/registry.py` sets `CACHE_SAVINGS_CREDITED = False` with the
  recorded reason that cache usage is not measured *there*, so Committee cost is
  accounted at uncached rates. Cache tokens **are** captured on the
  decision-intelligence invocation record, so this is a per-plane accounting choice
  rather than a repository-wide absence.
- **Proven:** billed and estimated cost are distinct fields, are never conflated, and
  cost evidence is read raw and **never totalled**
  (`test_invocation_cost_evidence_is_raw_and_never_totalled`), with an unknown cost and a
  superseded-billing reconciliation covered by
  `test_acceptance_unknown_cost_and_superseded_billing_reconciliation`. Matched-policy
  incremental economic value is proven, including that an unknown operating cost makes
  the operating net **unknown** rather than zero.
- **Still not canonical as a whole:** the Committee's own archive is a bounded JSONL
  archive rather than the canonical writer, which is why the F10 read plane withholds
  the attributable-AI-cost panel (section 6).

**Correction recorded.** The audit's first pass searched only the committee package and
concluded that no billed cost existed anywhere and that cache tokens were unmeasured. It
was wrong on both counts: both are present in the decision-intelligence plane. The rows
were corrected in place, and the `DECLARED_NOT_MEASURED` class was deleted from the
vocabulary because nothing uses it any more.


## 6. Workstream E - read-only operator evidence

```text
ITEM: E1 | integrate-ai-and-learning-facts-into-the-f10-semantic-service | workstream=E | class=OWNER_GATED | contract=SC-12 | evidence=OHM-Trade-Agent-v1/app/opip/cockpit/semantic_pages.py
```

The read path already declares this precisely. `app/opip/cockpit/semantic.py` defines
`REASON_NO_CANONICAL_AI_COST = "NO_CANONICAL_AI_COST_EVIDENCE"`, and
`semantic_pages.py` renders the panel *"attributable AI cost by provider, model and
attempt"* against criterion `AC-073` with exactly that reason. The census independently
records `AI usage | one provider invocation attempt | BLOCKED_BY_UPSTREAM_EVIDENCE`, and
`app/opip/cockpit/ai_cost.py` is a declared absence.

So Workstream E cannot be implemented within this mission's authority: the facts exist
in a committee-local archive, and exposing them through the F10 semantic service would
require a **new producer or publication boundary**, which the mission forbids without
owner authorization. No producer, registry, store, scheduler or publication boundary was
created.

## 7. The mandatory negative tests

```text
NEG: N1 | incomplete-cost | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_economics_v1.py::test_an_unknown_operating_cost_makes_the_operating_net_unknown
NEG: N2 | immature-outcome | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py::test_ac_004_an_immature_window_can_never_mature
NEG: N3 | censored-cohort | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_f_prospective_outcome_integration.py::test_ac_005_an_unresolved_or_censored_outcome_is_never_matured
NEG: N4 | missing-expectation | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_e_expectation_freeze.py::test_ac_002_an_outcome_before_the_cutoff_or_maturity_is_refused
NEG: N5 | missing-policy-reference | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_a_release_must_reference_its_conclusion
NEG: N6 | missing-committee-review | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_r6_committee_shadow_review_readiness.py::test_ac_003_a_stand_in_case_is_refused_rather_than_counted
NEG: N7 | wrong-model-version | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_prospective_v1.py::test_evaluation_refuses_a_drifted_release_explicitly
NEG: N8 | incorrect-paired-cohort | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_an_incomparable_cohort_must_state_why_and_report_no_metric
NEG: N9 | duplicate-ai-attempt | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_412_a_duplicate_ai_attempt_collapses_to_one_identity
NEG: N10 | missing-failed-attempt-billing | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_413_a_missing_failed_attempt_billing_is_incomplete_never_zero
NEG: N11 | causal-attribution-without-evidence | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_an_unmeasured_effect_stays_null
NEG: N12 | rejected-learning-artifact | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_changing_a_sealed_experiment_fails_closed
NEG: N13 | approved-but-not-adopted-artifact | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_learning_v1.py::test_effectiveness_requires_a_release_to_measure
NEG: N14 | committee-output-suggesting-unauthorized-action | class=PROVEN | evidence=OHM-Trade-Agent-v1/tests/test_opip_committee_safety_v1.py::test_committee_contracts_expose_no_authority_field
```

Twelve of the fourteen are already proven by an existing named negative test. `N9`
duplicate AI attempt and `N10` missing failed-attempt billing are only partially proven
today: per-attempt failure telemetry and per-provider failure accounting exist, but no
test asserts that a **retry** and a **failed attempt** each carry their own attributable
cost. Both are closed by `ATDD-R5-M04-ai-economics-evidence`.

## 8. Totals

```text
TOTALS:
  ITEMS 44
  NEGATIVE 14
  ROWS 58
  PROVEN 56
  OWNER_GATED 2
  WORKSTREAM_A 14
  WORKSTREAM_B 11
  WORKSTREAM_C 10
  WORKSTREAM_D 8
  WORKSTREAM_E 1
```

Fifty-six of the fifty-eight rows are proven by a named acceptance test and only two
remain owner-gated, so nothing is left unproven.

## 9. Follower increments and what each closes

```text
FOLLOWER: NONE | every row this audit could close has been closed by ATDD-R5-M04-ai-economics-evidence
```

`ATDD-R5-M04-ai-economics-evidence` closed `D4`, `D5`, `N9` and `N10`, and its
`AC-414` records the Workstream D and E determination. The two remaining `OWNER_GATED`
rows (`D7` canonical cost basis, `E1` F10 read integration) are not closable by any
increment without owner authorization.

## 10. What this matrix refuses to say

- It does not say AI net value is favourable, or unfavourable. The mission requires
  `INSUFFICIENT_EVIDENCE` rather than an estimate where the cost basis is incomplete, and
  the canonical-basis item is exactly that.
- It does not conflate Cline/DeepSeek engineering-development spend with Committee
  trading-operating expense. Nothing in this matrix treats development cost as an
  operating cost of the Committee.
- It does not say the Committee has any authority. Every C-side row is carried by tests
  that assert the opposite.
- It does not treat a refused or inconclusive artifact as a failure. Refusal is the
  proven behaviour.




