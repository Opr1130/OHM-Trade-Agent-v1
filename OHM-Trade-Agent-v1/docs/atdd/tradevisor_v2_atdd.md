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
| R-09 | Settlement was fenced only by `status == PROCESSING`, so a worker whose lease expired could settle the row a *different* worker had already reclaimed and re-claimed | Each claim mints a persisted unique `claim_token`; `_settle` requires the exact token it holds, and reclamation voids the previous token. Whole check-and-write stays inside the existing registry lock — no new store or lock service | `inbox::test_stale_worker_cannot_settle_a_reclaimed_row`, `inbox::test_stale_worker_cannot_poison_a_reclaimed_row`, `inbox::test_reclaimed_row_is_settled_by_its_new_owner` |
| R-10 | A worker that died after claiming left the row in `PROCESSING`, which `pending_count()` does not count; reclamation only ran inside `process_queued_events()`, so the cycle skipped it entirely and the row stranded | The advisory phase always attempts at least one drain pass, which reclaims expired leases before claiming. Still bounded to four batches; healthy leases are never stolen; protection phases still run first; failures still never abort the cycle | `recovery::test_normal_cycle_recovers_an_expired_processing_row`, `recovery::test_normal_cycle_leaves_a_healthy_processing_row_alone` |
| R-11 | `build_report()` computed every outcome (invoking the candle provider) before `build_learning_report()` applied the exact-SHA gate; the gate cleared cohorts but the computed outcomes were still returned and their measured count persisted | Release compatibility is classified with the existing `classify_release_compatibility` **before** any row is measured. On `RELEASE_DRIFT`/`RELEASE_UNVERIFIED` no provider is called, no outcome is produced or exposed, and consumption records the blocked disposition with a measured count of zero | `recovery::test_release_drift_never_invokes_the_candle_provider`, `recovery::test_unverified_release_never_invokes_the_candle_provider`, `recovery::test_current_release_still_measures_outcomes` |
| R-12 | The forward-only filter admitted a candle opening exactly at `window_end`, a candle opening before but *closing* after it, and the still-forming candle Kraken returns last; their highs/lows could create touches and change MFE/MAE outside the declared window | A candle is admissible only when it opens before `window_end`, its full interval closes at or before `window_end` **and** at or before the measurement instant, and it is provably complete (explicit completion flag, or `open + interval`). Completeness that cannot be established is excluded rather than guessed; pre-decision and touch-versus-fill semantics are unchanged | `learning::test_candle_opening_at_window_end_is_excluded`, `learning::test_candle_closing_after_window_end_is_excluded`, `learning::test_unfinished_candle_is_excluded`, `learning::test_explicitly_incomplete_candle_is_excluded`, `learning::test_completed_candles_inside_the_window_are_measured_without_a_fill` |

## Post-review remediation semantics

These four corrections came from an independent PR review. They change *how*
the existing guarantees are enforced, not what the product is allowed to do.

**Lease ownership (R-09).** A claim is `status = PROCESSING` **plus** a unique
`claim_token`. `_reclaim_stale` returns an expired row to `RETRY` and clears the
token, so the claim it represented can never be settled. `_settle` therefore
requires the caller's own token: a late worker whose lease expired is a no-op,
whether it returns success, failure or poison. The read-check-write remains a
single critical section under `registry.lock`, so no new coordination service
is introduced. Retry bounding (`attempts` → `max_attempts` → `POISONED`) and the
existing notification reservation/dedup controls are unchanged, so a reclaimed
row cannot double-deliver.

**Cycle recovery (R-10).** Because `pending_count()` counts only `QUEUED` and
`RETRY`, an all-`PROCESSING` inbox looks empty. The advisory phase now always
attempts one drain pass — the only place reclamation happens — so acknowledged
evidence is recoverable without a new webhook or an operator drain. The phase
stays last, stays inert when disabled, stays bounded to four batches, and its
failures are still swallowed by the surrounding handler.

**SHA fail-closed ordering (R-11).** The exact-SHA gate is a *precondition* of
measurement. A drifted or unverifiable worker performs no candle read at all, so
it cannot compute, return or persist outcome evidence. The classifier is the
learning plane's own `classify_release_compatibility` — there is still exactly
one release-classification implementation.

**Candle-window semantics (R-12).** Measurement is bounded to completed candles
whose whole interval lies inside `[decided_at, window_end]`. Concretely:
a candle opening at or after `window_end` is excluded; a candle closing after
`window_end` is excluded; a candle that has not finished forming (closing after
the measurement instant, or flagged incomplete by its source) is excluded; when
completeness cannot be established the candle is excluded rather than assumed.
The frozen decision and the entry/stop/target geometry are untouched, and a
price touch remains evidence about price — never a fill
(`fill_attribution = NONE_TOUCH_INFERRED`).


## Reconciled test totals

No group is double-counted. The 52-scenario matrix maps onto **217** distinct
Tradevisor tests (some scenarios have more than one test; none share a test).
``test_tradevisor_v2_advisory_acceptance_v1.py`` is the ATDD acceptance
traceability module for the scope contract, not a duplicate of the scenario
suite.

| File | Tests |
| --- | --- |
| `test_tradevisor_ingress_v1.py` | 29 |
| `test_tradevisor_webhook_v1.py` | 32 |
| `test_tradevisor_inbox_v1.py` | 30 |
| `test_tradevisor_evaluation_v1.py` | 20 |
| `test_tradevisor_notifier_v1.py` | 12 |
| `test_tradevisor_learning_v1.py` | 36 |
| `test_tradevisor_recovery_v1.py` | 20 |
| `test_tradevisor_boundaries_v1.py` | 23 |
| `test_tradevisor_v2_advisory_acceptance_v1.py` | 15 |
| **Total** | **217** |



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




## Post-review remediation — N-01 / N-02 (2026-10-24)

Two independent review findings about learning integrity. Both were reproduced
with failing tests before any production change.

### N-01 — durable evidence retention

**Defect.** `_prune()` treated transport/evaluation *terminal status* as proof of
learning consumption. A `PROCESSED` row can still owe a 24-hour prospective
measurement, and the configuration legally permits a 1-hour retention interval,
so eligible evidence could be deleted before it was ever measured.

**Lifecycle events are distinct and must not be conflated:**

| Event | Where it is recorded | Proves |
| --- | --- | --- |
| Transport receipt | `enqueue()` → row exists | durable receipt only |
| Evaluation completion | `status = PROCESSED/REJECTED` | the pipeline ran |
| Learning eligibility | frozen `decision_record` present | the row can become an outcome |
| Measurement completion | outcome `MEASURED`/`NO_ENTRY_TOUCH` | a definitive outcome exists |
| Durable learning consumption | `row["consumption"]` acknowledgement | this record is discharged |
| Safe retention eligibility | `_retention_eligible()` | deletion is allowed |

**Correction (fail-closed retention).** Retention removes a row only when it is
either (a) in a non-learning terminal state (`POISONED`, which never produced a
frozen decision) or (b) carrying a *valid* durable acknowledgement. An
acknowledgement is accepted only when it names the exact `dedup_id`, carries an
allowed disposition, is backed by a **definitive** measurement status, and was
recorded against the **current** release SHA. Missing, malformed, drifted,
unverified or non-definitive acknowledgements all fail closed. Duplicate webhook
delivery never touches consumption state. Capacity stays bounded and now enforces
the existing 503 backpressure instead of evicting unacknowledged evidence.

**No acknowledgement writer exists in this change.** No deployment unit invokes
the learning report job, and the learning worker has no reverse-write authority
into production storage, so no production caller records an acknowledgement.
Consequence, stated plainly: eligible evidence is retained indefinitely until an
authorized consumer acknowledges it, and the inbox will eventually reach capacity
and return 503. The backlog is observable through `inbox_status()`:
`consumed`, `unconsumed`, `retention_blocked`, `oldest_unconsumed_age_seconds`,
`capacity_backpressure_events` (aggregate only; no identifiers or secrets).

**Traceability:** N01-AC01…AC22 → `inbox::test_processed_evidence_is_not_pruned_without_a_consumption_ack`,
`test_one_hour_retention_cannot_erase_a_24h_hypothesis`,
`test_extended_learning_outage_does_not_erase_eligible_records`,
`test_poisoned_row_is_prunable_after_retention`,
`test_acknowledged_row_is_prunable_after_retention`,
`test_release_drift_acknowledgement_does_not_authorize_pruning`,
`test_nondefinitive_measurement_does_not_authorize_pruning`,
`test_mismatched_acknowledgement_identity_fails_closed`,
`test_malformed_consumption_metadata_fails_closed`,
`test_legacy_row_without_consumption_fields_is_retained`,
`test_duplicate_delivery_cannot_reset_a_consumption_ack`,
`test_consumption_acknowledgement_is_idempotent`,
`test_consumption_bookkeeping_preserves_processing_state`,
`test_capacity_backpressure_is_preserved_and_counted`,
`test_inbox_status_reports_the_unconsumed_backlog`.


### N-02 — complete candle coverage

**Defect.** `evaluate_outcome()` only required *any* admissible candle, so a
single bar could produce `MEASURED` or the definitive claim `NO_ENTRY_TOUCH`
("the entry zone was never traded"), biasing learning evidence.

**Eligible interval, defined.** With `t0` = frozen decision time, `H` =
measurement duration, `t1 = t0 + H`, `Δ` = candle interval:

- expected opens are the UTC epoch multiples of `Δ` starting strictly after `t0`;
- a decision at 10:23 UTC makes 11:00 the first eligible bar; the 10:00–11:00 bar
  carries pre-decision prices and is never eligible;
- a bar is expected only if `open + Δ ≤ t1`;
- `excluded_lead_seconds` / `excluded_tail_seconds` record the honest boundary
  remainder. This is **not** continuous 24-hour price coverage — it is every
  complete post-decision closed bar.

**Correction.** Every expected open must be present exactly once, grid-aligned,
complete and inside the window. Missing first/middle/last bars, truncated
history, misaligned timestamps, conflicting duplicates and non-finite OHLC all
produce the new non-definitive status `PARTIAL_COVERAGE` with bounded diagnostics
(`coverage_status`, `coverage_reason`, `expected_candles`,
`observed_valid_candles`, `missing_candles`, `missing_candle_opens` (≤6),
`first_expected_open`, `last_expected_close`, `observation_window_end`,
`excluded_lead_seconds`, `excluded_tail_seconds`). Identical duplicates collapse;
conflicting ones fail closed; unordered responses are sorted deterministically.
`PARTIAL_COVERAGE` never contributes to measured counts, false-positive rates or
estimated profitability. A touch is still never a fill
(`fill_attribution = NONE_TOUCH_INFERRED`).

**Traceability:** N02-AC01…AC30 → `learning::test_single_candle_cannot_produce_a_measured_outcome`,
`test_missing_middle_candle_is_nondefinitive`,
`test_missing_final_candle_is_nondefinitive`,
`test_truncated_history_is_nondefinitive`,
`test_conflicting_duplicate_candles_fail_closed`,
`test_identical_duplicate_candles_collapse`,
`test_out_of_order_candles_are_ordered_deterministically`,
`test_misaligned_candle_timestamp_is_nondefinitive`,
`test_non_finite_ohlc_is_not_measured`,
`test_partial_coverage_is_excluded_from_cohort_metrics`,
`test_complete_coverage_measures_and_keeps_touch_semantics`,
`test_complete_window_without_entry_touch_is_definitive`,
`test_coverage_diagnostics_describe_the_boundaries`,
`test_incomplete_window_is_not_part_of_the_measured_count`.

### Requirement → AC → test map (no new AC identifiers)

The active scope contract's ACs already own these paths (AC-010 inbox durability
and terminal disposition, AC-012 exact-SHA learning, AC-014 noncanonical
evidence). N-01/N-02 refine *how* those ACs are satisfied, so no new AC ids were
created and the acceptance traceability set is unchanged; the contract records
the amended invariants under FROZEN BOUNDARIES.

### Fixture corrections (contract changes, not weakened assertions)

Four learning fixtures and two job-level fixtures previously asserted definitive
outcomes from two bars across a 24-hour window — precisely the N-02 defect. They
now declare a matching 3-hour window (one acceptance fixture uses 2 hours), so
the semantic intent is unchanged while coverage is genuinely complete. One
retention test named for the old behaviour was narrowed to the poisoned case,
because `PROCESSED` alone no longer authorises deletion. One acceptance fixture
used a wall-clock trigger that lands inside the 60-second close tolerance at the
top of an hour; it now triggers at the bar open, which is deterministic and still
exercises the unfinished-bar refusal.


## Addendum - advisory observability, provenance, native context (TV2-FR-01)

This addendum adds eleven criteria and seventeen tests. It changes no policy:
F6 and completed-4H still block actionable output, SELL is still exit evidence
only, the ingress is still dark by default, and the new observation digest is a
separate opt-in class that can never carry an entry, stop, target or size.

| AC | Scenario | Test |
| --- | --- | --- |
| AC-016 | Provenance records external identity; algorithm version unverified | `test_ac_016_provenance_records_external_identity_and_unverified_algorithm` |
| AC-017 | Provenance excluded from replay identity; bad values refused | `test_ac_017_provenance_never_changes_replay_identity_and_bad_values_fail_closed` |
| AC-018 | BTC/native context sourced, stamped, availability-honest | `test_ac_018_native_btc_context_carries_source_timestamp_and_availability`, `test_ac_018_unavailable_btc_context_is_never_reported_as_a_pass` |
| AC-019 | Chase risk reused from native owner, advisory only, inputs disclosed | `test_ac_019_chase_risk_is_reused_from_the_native_owner_as_context_only` |
| AC-020 | Instrument coverage SUPPORTED/UNSUPPORTED/UNVERIFIED | `test_ac_020_instrument_coverage_distinguishes_supported_unsupported_unknown` |
| AC-021 | Economic PASS uses the F7-permitted allocation; reject/pass/determinism/no-book | four `test_ac_021_*` cases |
| AC-022 | Withheld-but-substantive candidate yields only an observation | `test_ac_022_*` (2 cases) |
| AC-023 | Digest bounded, deduplicated, honest on transport failure | `test_ac_023_*` (2 cases) |
| AC-024 | Observation digest disabled by default | `test_ac_024_observation_digest_is_disabled_by_default` |
| AC-025 | Approved format states timing, venue, capital basis, horizon | `test_ac_025_actionable_alert_states_timing_venue_and_capital_basis` |
| AC-026 | Actionable and observation delivery are mutually exclusive | `test_ac_026_observation_and_actionable_delivery_are_mutually_exclusive` |

### Observed defect corrected in this addendum

`tools/tradevisor_alert_preview.py` could not run at the previous head: it fed a
`WATCH` decision to `format_tradevisor_alert`, which correctly refuses to render
anything non-actionable. The preview now derives an explicitly labelled
synthetic actionable object **only** for decisions that actually reached the
observation cohort, and otherwise prints the real decision and rejection code.
The tool remains banner-labelled synthetic and performs no market read.

## Addendum - instruction leakage, allocation bounds, discovery coverage

| AC | Scenario | Test |
| --- | --- | --- |
| AC-027 | No internal instruction token (`ENTER_NOW`, `SET_LIMIT_ENTRY`, `ACTIONABLE`) in operator-facing WATCH text; shadow evidence byte-identical; contradictory text refused | three `test_ac_027_*` cases |
| AC-028 | Final F5 call bounded by the unchanged position cap and risk budget; oversized / NaN / infinite / negative / zero / below-minimum allocations fail closed without raising and without an economic PASS | four `test_ac_028_*` cases |
| AC-029 | Discovery coverage separates native-discovered, TradingView-configured, actually-observed and uncovered markets; no invented signals; ranking reuse declared unavailable | four `test_ac_029_*` cases |

### Defect record

The observation previously echoed the internal decision reason verbatim, which
rendered `Assessment: Shadow-only swing LONG hypothesis (ENTER_NOW); ...` to an
operator. The guard list only contained the spaced form (`ENTER NOW`), so the
underscore token passed. AC-027 covers both forms, the neutralised presentation
and the refusal of a hand-built contradictory message.

The final-allocation economic call previously had no bound on the allocation it
accepted, and a non-finite allocation escaped as a `ValueError` from a
downstream owner. AC-028 adds the consistency tripwires and the fail-closed
rejection.

## Addendum - six independent-review findings (AC-030 .. AC-035)

| AC | Finding | Correction | Tests |
| --- | --- | --- | --- |
| AC-030 | Advisory delivery lived only in memory between settlement and send, so a crash, refusal, exception or suppression lost the report | Delivery is re-derived from durable rows; a confirmed send writes a per-row durable acknowledgement; bounded remainder rolls forward after cooldown | 5 `test_ac_030_*` |
| AC-031 | The inbox serializer dropped `signal.provenance`, so a reloaded row lost it | Serializer persists provenance; legacy rows read as unknown; reclamation preserves it | 2 `test_ac_031_*` |
| AC-032 | BTC context was labelled AVAILABLE on any returned trend, stamped with evaluation time | Identity match + qualified market data + source candle timestamp required, otherwise UNAVAILABLE with a reason | 1 `test_ac_032_*` |
| AC-033 | Partial chase-risk inputs produced a confident LOW band | Input completeness exposed; conclusion indeterminate; band indicative-only | 1 `test_ac_033_*` |
| AC-034 | The configured observation interval was dead code and suppression was opaque | Configured interval applied, stricter of config/policy wins, retry delay reported, suppression marks nothing | 3 `test_ac_034_*` |
| AC-035 | Negative risk, malformed/None/non-numeric owner output and inconsistent position percentages could pass or raise | Every unusable result fails closed with a reason code, no exception escapes, no economic PASS | 3 `test_ac_035_*` |

All six were reproduced RED before implementation (15 failing tests, 0 passing in
the remediation selection), then GREEN (15 passing). No learning file, F6/4H
policy, canonical path, execution path or risk threshold was modified.

## Addendum - delivery reliability review (AC-036 .. AC-038)

| AC | Scope | Scenarios | Tests |
| --- | --- | --- | --- |
| AC-036 | Guarantee and every ambiguous outcome | crash before transport; acknowledgement lost after delivery; transport timeout with unknown outcome; refusal; concurrent worker and expired reservation | 6 `test_ac_036_*` |
| AC-037 | Bounded backlog | horizon sizing and aged-out accounting; per-call cap without deletion; sustained ingress drained across cooldowns; inbox capacity backpressure with pending evidence intact | 4 `test_ac_037_*` |
| AC-038 | Marker lifecycle and priority | compaction of ancient markers; compaction on the write path; 5 000 markers plus backlog do not disturb protection; 20 000 markers keep lookup bounded | 4 `test_ac_038_*` |

### Defects found by this review

1. **Horizon shorter than the drain (P1).** `2 x cooldown + 1h` = 13h could not
   cover a 12-row backlog needing three digests = 14h, so the tail aged out
   silently. Fixed by sizing the horizon to `DRAIN_CAPACITY_CYCLES` cooldowns and
   by adding `observation_backlog()` aged-out accounting.
2. **Stranded evidence after a lost acknowledgement (P1).** A confirmed emission
   whose per-row marker was lost left rows pending forever: the digest
   fingerprint matched permanently, so `should_emit` refused and nothing ever
   acknowledged them. Fixed with the `ALREADY_DELIVERED` repair path.
3. **Unbounded marker growth (P2).** Every acknowledged row added a state entry
   with no compaction. Fixed by pruning markers older than 30 days on the write
   path and by `compact_markers()`.

All three were reproduced RED before the correction.
