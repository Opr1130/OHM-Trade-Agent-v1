# Tradevisor V2 — ATDD Acceptance Matrix

Controlling inventory: the 52-scenario specification from the mission brief.
Every row states the executable evidence. "PASS" means an executed test in this
repository proves the behaviour in the current worktree — it does **not** mean
production certification.

Test files:

| Short name | File |
| --- | --- |
| `ingress` | `tests/test_tradevisor_ingress_v1.py` |
| `webhook` | `tests/test_tradevisor_webhook_v1.py` |
| `inbox` | `tests/test_tradevisor_inbox_v1.py` |
| `evaluation` | `tests/test_tradevisor_evaluation_v1.py` |
| `notifier` | `tests/test_tradevisor_notifier_v1.py` |
| `learning` | `tests/test_tradevisor_learning_v1.py` |
| `recovery` | `tests/test_tradevisor_recovery_v1.py` |
| `boundaries` | `tests/test_tradevisor_boundaries_v1.py` |

## Positive scenarios

| ID | Requirement | Test | Disposition |
| --- | --- | --- | --- |
| ATDD-01 | Valid BUY accepted | `ingress::test_atdd01_buy_accepted`, `webhook::test_atdd01_valid_buy_is_durably_accepted` | PASS |
| ATDD-02 | Valid SELL accepted | `ingress::test_atdd02_sell_accepted`, `webhook::test_atdd02_valid_sell_is_accepted` | PASS |
| ATDD-03 | 1H bar-close timing | `ingress::test_atdd03_trigger_before_bar_close_refused`, `ingress::test_atdd03_trigger_just_after_close_accepted` | PASS |
| ATDD-04 | Coinbase source accepted | `ingress::test_atdd04_coinbase_source_accepted` | PASS |
| ATDD-05 | Binance source accepted | `ingress::test_atdd05_binance_source_accepted` | PASS |
| ATDD-06 | Source instrument → execution market | `ingress::test_atdd06_source_instrument_mapped_to_market`, `evaluation::test_atdd06_qualified_buy_is_shadow_only_without_f6_and_4h` | PASS (mapping and shadow plan; not actionable) |
| ATDD-07 | Native corroboration retained | `evaluation::test_atdd07_native_corroboration_is_recorded_not_invented` | PASS |
| ATDD-08 | Exact actionable Telegram content | `notifier::test_atdd08_actionable_message_contains_every_required_field` (isolated synthetic notifier contract, not a live authorized alert) | PASS (formatter only) |
| ATDD-09 | Limit entry has expiration + chase limit | `notifier::test_atdd09_limit_entry_message_carries_the_limit_price` (isolated synthetic input) | PASS (formatter only) |
| ATDD-10 | Shadow outcome evidence, no live authority | `learning::test_atdd10_shadow_observation_is_measurable_without_live_authority`, `learning::test_owner_approved_shadow_hypothesis_is_measured_without_a_fill`, `recovery::test_learning_job_measures_persisted_records` | PASS (forward-only shadow measurement; no fill) |

## Negative and security scenarios

| ID | Requirement | Test | Disposition |
| --- | --- | --- | --- |
| ATDD-11 | Missing/invalid source authentication | `webhook::test_atdd11_*` (4 cases) | PASS |
| ATDD-12 | Spoofed source identity | `webhook::test_atdd12_untrusted_origin_is_refused`, `webhook::test_atdd12_ingest_token_is_never_persisted` | PASS |
| ATDD-13 | Unsupported indicator/schema | `webhook::test_atdd13_*`, `ingress::test_atdd13_*` | PASS |
| ATDD-14 | Malformed JSON | `webhook::test_atdd14_*` | PASS |
| ATDD-15 | Missing/unknown direction | `webhook::test_atdd15_missing_direction_is_refused`, `ingress::test_atdd15_missing_direction_refused` | PASS |
| ATDD-16 | Contradictory BUY/SELL configuration | `webhook::test_atdd16_*`, `ingress::test_atdd16_*` | PASS |
| ATDD-17 | Invalid/unresolved placeholders | `ingress::test_unresolved_placeholders_are_refused`, `webhook::test_atdd17_*` | PASS |
| ATDD-18 | Oversized payload | `webhook::test_atdd18_oversized_payload_is_refused` | PASS |
| ATDD-19 | NaN / infinity / negative price | `webhook::test_atdd19_*`, `ingress::test_atdd19_*` | PASS |
| ATDD-20 | Stale source event | `webhook::test_atdd20_stale_alert_is_refused`, `ingress::test_atdd20_stale_event_refused` | PASS |
| ATDD-21 | Future-dated event | `webhook::test_atdd21_future_dated_alert_is_refused`, `ingress::test_atdd21_future_dated_event_refused` | PASS |
| ATDD-22 | Bar opening ≠ bar closing | `webhook::test_atdd22_trigger_before_bar_close_is_refused`, `ingress::test_atdd22_*`, `ingress::test_owner_alert_template_is_accepted_verbatim` | PASS |
| ATDD-23 | Duplicate alert delivery | `webhook::test_atdd23_duplicate_delivery_returns_duplicate_without_a_second_row`, `inbox::test_repeat_receipt_returns_the_same_row` | PASS |
| ATDD-24 | Replay with modified receipt timestamp | `webhook::test_atdd24_replay_with_fresh_receipt_still_collapses`, `ingress::test_atdd24_*` | PASS |
| ATDD-25 | Unknown coin pair | `webhook::test_atdd25_unknown_coin_pair_is_refused`, `ingress::test_atdd25_unknown_instrument_fails_closed` | PASS |
| ATDD-26 | Cross-venue reference-price divergence | `evaluation::test_atdd26_cross_venue_divergence_is_refused` | PASS |
| ATDD-27 | Missing required market features | `evaluation::test_atdd27_missing_native_market_evidence_is_an_abstention` | PASS |
| ATDD-28 | F5 feasibility rejection | `evaluation::test_atdd28_target_attainability_rejection_abstains` | PASS |
| ATDD-29 | F6 evidence unavailable | `evaluation::test_atdd06_qualified_buy_is_shadow_only_without_f6_and_4h`, `evaluation::test_atdd29_f6_blocks_actionable_even_when_4h_is_not_required`, `evaluation::test_atdd29_position_stays_shadow_only_without_trusted_f6`, acceptance AC-015 | **PASS for F6-driven nonactionable abstention** (G-02; no trusted model or approved fallback) |
| ATDD-30 | F7 capital / exposure rejection | `evaluation::test_atdd30_undersized_capital_is_rejected`, `evaluation::test_atdd30_exposure_capacity_rejects_a_full_book` | PASS |
| ATDD-31 | Invalid stop or target geometry | `evaluation::test_atdd31_invalid_geometry_is_rejected` | PASS |
| ATDD-32 | Economic after-cost rejection | `evaluation::test_atdd32_after_cost_economics_reject_a_thin_account` | PASS |
| ATDD-33 | SELL grants no short permission | `evaluation::test_atdd33_*` (2 cases), `evaluation::test_sell_without_native_data_still_abstains_safely` | PASS |
| ATDD-34 | Source-supplied prompt injection | `webhook::test_atdd34_source_cannot_assert_its_own_authority`, `ingress::test_atdd34_privilege_escalation_fields_refused` | PASS |
| ATDD-35 | No automated order execution | `boundaries::test_atdd35_*` (3 cases) | PASS |

## Resilience scenarios

| ID | Requirement | Test | Disposition |
| --- | --- | --- | --- |
| ATDD-36 | Crash before durable acknowledgement / mid-processing | `inbox::test_crash_during_processing_is_reclaimed_not_lost` | PASS |
| ATDD-37 | Crash after receipt, before handoff | `inbox::test_durable_receipt_survives_a_crash_before_handoff` | PASS |
| ATDD-38 | Canonical writer unavailable | `boundaries::test_every_tradevisor_module_parses_and_imports_no_execution_surface` (no `canonical` import), `recovery::test_inbox_declares_its_evidence_class_and_consumer` | **OWNER-APPROVED SHADOW EXCEPTION — NOT EXERCISED** (no canonical handoff, no outage test; G-03) |
| ATDD-39 | Queue at capacity / backpressure | `webhook::test_atdd39_inbox_capacity_pressure_backs_off_with_503`, `inbox::test_capacity_is_bounded_and_raises_backpressure` | PASS |
| ATDD-40 | Duplicate worker processing | `inbox::test_settle_is_a_noop_when_a_duplicate_worker_lost_the_lease` | PASS |
| ATDD-41 | Telegram delivery failure and retry | `notifier::test_atdd41_failed_delivery_releases_the_reservation_for_retry` (isolated synthetic transport test), `recovery::test_retry_re_evaluates_and_withholds_delivery_without_f6_and_4h` | PASS (retry + policy preservation; no production send) |
| ATDD-42 | Plan expiry during notification retry | `notifier::test_atdd42_expired_plan_is_never_delivered` | PASS |
| ATDD-43 | F11 protection independent under overload | `boundaries::test_atdd43_*` (3 cases: phase ordering, inert-when-disabled, failure containment) | **PARTIAL — STRUCTURAL ONLY; OVERLOAD/SOAK NOT CERTIFIED** (G-05) |
| ATDD-44 | Learning worker SHA mismatch fails closed | `learning::test_atdd44_release_drift_fails_closed_and_publishes_no_metrics`, `learning::test_release_compatibility_mirrors_the_learning_job_contract`, `learning::test_unverified_release_also_fails_closed` | PASS |
| ATDD-45 | Poison payload reaches a terminal disposition | `inbox::test_repeated_failure_becomes_an_explicit_poisoned_disposition`, `inbox::test_malformed_row_is_quarantined_not_evaluated` | PASS |

## Regression and integration

| ID | Requirement | Test | Disposition |
| --- | --- | --- | --- |
| ATDD-46 | Original TradingView v2 endpoint unchanged | `boundaries::test_atdd46_wave_8_2_route_and_schema_are_untouched` + 30 Wave 8.2 bridge tests | PASS |
| ATDD-47 | Existing native alerts unchanged | `boundaries::test_atdd47_*` (2 cases) + Wave 9 notification suites | PASS |
| ATDD-48 | Existing notification dedup remains effective | `notifier::test_second_identical_recommendation_is_suppressed_by_policy`, `notifier::test_atdd48_native_material_alert_suppresses_the_tradevisor_duplicate` | PASS |
| ATDD-49 | Risk and geometry kernels remain authoritative | `boundaries::test_atdd49_geometry_and_capital_owners_remain_authoritative` | PASS |
| ATDD-50 | Release-profile validator rejects activation | `boundaries::test_atdd50_release_profiles_cannot_activate_tradevisor` | PASS |
| ATDD-51 | Replay reproduces the same decision | `evaluation::test_atdd51_replay_of_the_same_frozen_inputs_is_identical` | PASS |
| ATDD-52 | Default installation is inert | `webhook::test_atdd52_*`, `boundaries::test_atdd52_no_committed_environment_enables_the_ingress` | PASS |

## Additional scenarios added from repository analysis

| ID | Requirement | Test | Disposition |
| --- | --- | --- | --- |
| ATDD-53 | Owner alert template accepted verbatim (string-typed `{{close}}`) | `ingress::test_owner_alert_template_is_accepted_verbatim` | PASS |
| ATDD-54 | "Exposure not evaluated" is recorded, never treated as clear | `evaluation::test_exposure_not_evaluated_is_recorded_as_unknown` | PASS |
| ATDD-55 | Horizon is decided by volatility, not by the source timeframe | `evaluation::test_swing_classification_uses_volatility_not_the_source_timeframe` | PASS |
| ATDD-56 | No evaluator configured cannot imply approval | `inbox::test_missing_evaluator_never_implies_approval` | PASS |
| ATDD-57 | Entry, stop and target ordering validated independently of the gate | `evaluation::test_atdd06_qualified_buy_is_shadow_only_without_f6_and_4h` | PASS (frozen shadow hypothesis) |
| ATDD-58 | Learning cannot fabricate an outcome when candles are unreadable | `learning::test_outcome_failure_to_read_candles_never_fabricates_a_result` | PASS |
| ATDD-59 | Measured outcome excludes pre-decision bars (no look-ahead) | `learning::test_outcome_measures_forward_path_only` | PASS |
| ATDD-60 | A touch is recorded as a touch, never a fill | `learning::test_outcome_measures_forward_path_only` (`fill_attribution`) | PASS |

## Corrections made during independent pre-PR review

| ID | Defect found | Correction | Test |
| --- | --- | --- | --- |
| R-01 | `SEND_FAILED` was recorded but nothing re-attempted delivery: ATDD-41 was only reservation-level | Bounded retry pass in the cycle; retries **re-evaluate** instead of re-sending a frozen plan, and cannot bypass new F6/4H blockers | `recovery::test_retry_re_evaluates_and_withholds_delivery_without_f6_and_4h`, `test_retry_records_a_non_actionable_outcome_without_sending` |
| R-02 | A retry could have delivered a plan frozen up to 90 minutes earlier | Retry candidates are excluded once the frozen plan expires | `recovery::test_retry_is_bounded_and_skips_expired_rows`, `test_undelivered_rows_are_retry_candidates_only_while_unexpired` |
| R-03 | Retrying could have rewritten the decision or its evidence | Only the `notification` sub-record is mutable; the frozen record and terminal status are immutable | `recovery::test_retry_leaves_the_frozen_decision_immutable` |
| R-04 | Persisted decision evidence had **no consumer** (orphaned learning artifact, governance principle 21) | `app/jobs/report_tradevisor_learning.py` consumes it under the exact-SHA gate | `recovery::test_learning_job_*` (5 cases) |
| R-05 | Missing F6/4H evidence was recorded but not disclosed to the human | Message now prints an explicit `Evidence:` line naming what is unavailable | `notifier::test_message_names_the_evidence_it_does_not_have` |
| R-06 | The store's status did not state that it is not the canonical ledger | `inbox_status()` publishes `evidence_class`, `canonical_handoff`, `consumption` | `recovery::test_inbox_declares_its_evidence_class_and_consumer` |
| R-07 | The ack-path import test asserted source text, not the import graph | Strengthened to an AST allowlist that names the single reviewed coupling | `boundaries::test_atdd35_webhook_ack_path_performs_no_downstream_io` |
| R-08 | The exact-SHA classifier was **re-implemented** although `app.opip.learning.job_disposition` already owns it | Re-exported the owner's function object; consumption now writes the learning plane's own `CONSUMED_OK`/`CONSUMED_EMPTY`/`BLOCKED_RELEASE_DRIFT` summary | `learning::test_release_classification_is_the_learning_plane_owner_not_a_copy`, `recovery::test_consumption_summary_is_written_with_no_policy_authority` |

## Reconciled test totals

No group is double-counted. The 52-scenario matrix maps onto **156** distinct
Tradevisor tests (some scenarios have more than one test; none share a test):

| File | Tests |
| --- | --- |
| `test_tradevisor_ingress_v1.py` | 29 |
| `test_tradevisor_webhook_v1.py` | 32 |
| `test_tradevisor_inbox_v1.py` | 12 |
| `test_tradevisor_evaluation_v1.py` | 18 |
| `test_tradevisor_notifier_v1.py` | 12 |
| `test_tradevisor_learning_v1.py` | 16 |
| `test_tradevisor_recovery_v1.py` | 14 |
| `test_tradevisor_boundaries_v1.py` | 23 |
| **Total** | **156** |


## Approved policy dispositions and remaining deviations (2026-10-23)

The owner resolved G-02/G-03/G-04 on 2026-10-23. F6 unavailability now blocks
actionable qualification; SWING also requires completed trusted 4H context.
The existing separate inbox remains explicitly noncanonical for shadow
observation and learning. Only ATDD-29's tested abstention property is now
satisfied. An approved exception is **not** an outage test, and structural
isolation is **not** overload certification.

| ID | Originally required | Current disposition | Qualification |
| --- | --- | --- | --- |
| ATDD-29 | Abstain when **F6** (forecast) evidence is unavailable | `WATCH` / `ADVISORY_EVIDENCE_INCOMPLETE`; retained `shadow_proposed_action`, no expiry or actionable notification; AC-015 covers forward-only `SHADOW_HYPOTHESIS` measurement without fills | **PASS for F6-driven abstention only**, not for model calibration, fallback approval or activation |
| ATDD-38 | Fail closed when the **canonical writer** is unavailable, i.e. a canonical handoff exists and its outage is handled | Owner-approved `ADVISORY_NON_CANONICAL` shadow-only exception, `canonical_handoff = NOT_PERFORMED` | **NOT EXERCISED — APPROVED EXCEPTION.** There is no canonical dependency to fail, hence no outage claim or execution authority |
| ATDD-43 | F11 protection stays independent **under overload** | Confirmed structurally only: phase ordering (runs last), inert-when-disabled, failure containment | No overload, burst or soak test was run. Structural ordering does not show that protection holds at 10x load, under latency injection or with a saturated queue. Overload behaviour is **uncertified** |

F6 has **no registered trusted production model**, and no deterministic
actionable fallback is approved. The evaluator explicitly fails closed after
building an otherwise qualified shadow hypothesis; a forecast status change
alone cannot bypass this blocker. 4H cannot be inferred from the 1H series.

## Explicitly NOT VERIFIED / BLOCKED

| Item | Why | Owner action |
| --- | --- | --- |
| A genuine TradingView → O'Pip delivery | No live webhook was sent; no alert was created in TradingView | Create one per-symbol alert against a staging host and verify the receipt |
| **TradingView client-certificate authentication** | TradingView documents an SSL client certificate on HTTPS deliveries, verified by the recipient. This implementation neither requests nor validates it (see §"Certificate authentication" in `TRADEVISOR_V2.md`) | Add recipient-side certificate verification at the edge and prove it on staging before relying on it |
| **Body-token credential handling** | The ingestion token travels in the alert message body, which TradingView's own guidance discourages; no staging check has confirmed that no proxy, APM or log retains the body | Run the staging verification checklist (certificate observation, log redaction, authenticity-negative, rotation drill) |
| Watchlist-wide alert coverage | TradingView documentation does not establish multi-symbol delivery for this indicator | Use per-symbol alerts; re-test before assuming otherwise |
| Production secret values | Generating or rotating production credentials is owner-gated | Generate the ingestion token and edge verification value through the approved secret mechanism |
| Cross-venue execution fidelity on live books | All execution evidence in tests is synthetic | Observe with `OPIP_TRADEVISOR_ENABLED=true` against a staging deployment |
| **Overload / soak / latency behaviour** | No soak environment; F11 independence is structural only | Run a burst (≥10x) and a sustained soak against staging and record p99 |
| F6 trusted forecast / actionable fallback | No trusted forecast model registered; no fallback approved; `WATCH` required | Policy decided G-02; model/fallback implementation still deferred |
| Completed trusted 4H regime context | Only completed 1H used; `regime_4h` is `UNAVAILABLE`; SWING `WATCH` required | Policy decided G-04; 4H evidence integration deferred |
| Canonical promotion of advisory evidence | No canonical handoff; explicitly noncanonical and shadow-only | G-03 exception approved; canonical promotion remains out of scope |

## Recorded gaps

G-01 and G-05 remain open evidence/activation gates. G-02, G-03 and G-04 have
explicit owner policy decisions dated 2026-10-23; their unbuilt dependencies
stay visible and no trading or production authority follows from approval.

- **G-01 — Genuine source-to-production certification.** *Status: OPEN
  (evidence).* All evidence here is synthetic. Source→production certification
  requires a real Tradevisor alert delivered through the approved deployment
  channel.
- **G-02 — F6 actionable qualification.** *Status: POLICY RESOLVED;
  implementation dependency OPEN.* A missing trusted model or approved fallback
  always yields `WATCH`. A hypothetical entry remains available for shadow
  learning only. ATDD-29 abstention is tested, and an actionable fallback is
  not implied or authorized.
- **G-03 — Noncanonical shadow evidence.** *Status: POLICY RESOLVED;
  approved exception.* The Wave 8.2 precedent and `ADVISORY_NON_CANONICAL` /
  `NOT_PERFORMED` / `app.jobs.report_tradevisor_learning` disposition remain.
  No canonical admission, execution rights or writer change is granted. The
  original ATDD-38 outage scenario is **not exercised** and **not PASS**.
- **G-04 — Completed 4H SWING regime.** *Status: POLICY RESOLVED;
  evidence integration OPEN.* The native 1H basis stays unchanged and `regime_4h`
  remains `UNAVAILABLE`; any otherwise qualified SWING is `WATCH` pending
  separately approved completed trusted higher-timeframe evidence.
- **G-05 — Load/soak evidence.** *Status: OPEN (evidence).* F11 independence is
  proven structurally (phase ordering, inert-when-disabled, failure
  containment), not by a 10x burst soak test. No soak environment was available,
  so ATDD-43 is **partial**, not a pass.


