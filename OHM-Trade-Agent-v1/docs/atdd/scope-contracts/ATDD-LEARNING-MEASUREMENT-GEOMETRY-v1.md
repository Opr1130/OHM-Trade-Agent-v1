INCREMENT:
ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1

OWNER-APPROVED INTENT:
Close the N-03 measurement-boundary defect recorded in ATDD-TRADEVISOR-V2-ADVISORY-v1 (DEFERRED DISCOVERIES, N-03): the Tradevisor prospective-measurement path validated only finiteness and `high < low`, so a finite bar whose `open` or `close` lay outside `[low, high]`, or whose `open` was non-positive, could still produce a MEASURED outcome while the native market-data owner already rejected the same bar. This increment makes the measurement path agree with the native owner, makes an all-invalid window an explicit non-measurement instead of a pending row, reports a corrupt bar as corrupt instead of as absent, and refuses a provider close time that contradicts the declared interval. It grants no authority: no trading, execution, canonical-writer, selector, release-profile, deployment or activation change, and it does not change any Tradevisor qualification decision. The learning job remains uninvoked by any deployment unit and its release gate is untouched.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/tradevisor_learning.py: the measurement owner being corrected.
- OHM-Trade-Agent-v1/app/scanner/market_data_validation.py: the native owner whose geometry policy is mirrored (numeric, finite, positive, `low <= open, close <= high`).
- OHM-Trade-Agent-v1/app/exchanges/kraken.py: `Candle` carries `open/high/low/close`, so the stricter policy is satisfiable by the production provider.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md: the increment that recorded N-03 and froze the surrounding measurement boundaries.
- OHM-Trade-Agent-v1/docs/atdd/README.md: the scope-control mechanism and its required sections.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a forward candle whose open, high, low or close is NaN or infinite, inside an otherwise complete measurement window
WHEN:
the prospective outcome is measured
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason INVALID_OHLC, never MEASURED, and the corrupt bar is reported as corrupt rather than as a missing bar

AC-002:
GIVEN:
a forward candle whose open price is a JSON boolean, a non-numeric string, null or an arbitrary object
WHEN:
the prospective outcome is measured
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason INVALID_OHLC and no price is coerced from a non-price value

AC-003:
GIVEN:
a forward candle whose close price is zero or negative
WHEN:
the prospective outcome is measured
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason INVALID_OHLC

AC-004:
GIVEN:
a forward candle whose high is below its low, or whose open or close lies above the high or below the low
WHEN:
the prospective outcome is measured
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason INVALID_OHLC and coverage_status PARTIAL

AC-005:
GIVEN:
a measurement window whose every otherwise eligible forward candle has impossible geometry
WHEN:
the prospective outcome is measured
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason INVALID_OHLC and is never MEASURED and never NO_ENTRY_TOUCH, so an all-invalid window cannot become a definitive measurement or a silently pending row

AC-006:
GIVEN:
an otherwise complete and valid measurement window, plus an impossible bar strictly before the decision instant and an impossible bar opening at the window end
WHEN:
the prospective outcome is measured
THEN:
the outcome is MEASURED with coverage_status COMPLETE, because out-of-window corruption can never change an in-window measurement

AC-007:
GIVEN:
a forward candle that reports its own close time several intervals after the open it declares
WHEN:
the prospective outcome is measured
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason CLOSE_TIME_CONTRADICTION, because full-interval coverage is not provable

AC-008:
GIVEN:
two restatements of the same bar timestamp whose open differs while the other prices agree
WHEN:
coverage is proven
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason CONFLICTING_DUPLICATE_CANDLES, because duplicate equivalence covers the whole OHLC set

AC-009:
GIVEN:
a complete, coherent window that trades through the entry zone, plus an identical restatement of one bar
WHEN:
the prospective outcome is measured
THEN:
the outcome is MEASURED with entry_touched true, fill_attribution NONE_TOUCH_INFERRED and basis TOUCH_BASED_ESTIMATE_NOT_REALIZED, so identical restatements still collapse and a touch is never a fill

AC-010:
GIVEN:
the same frozen record and the same candle history
WHEN:
the outcome is computed twice
THEN:
both results are byte-identical and deterministic, and the first is PARTIAL_COVERAGE for the injected impossible bar

AC-011:
GIVEN:
a shared, pure OHLC predicate used at runtime by the learning measurement path, the native market-data validator, and both owners evaluated over the same coherent and incoherent bars
WHEN:
each bar is classified by both owners, and the native module's imports are inspected
THEN:
the learning path uses the shared predicate at runtime, the two owners agree on every bar so a regression test detects drift, and the native validator is NOT coupled to the shared module (it keeps its own admission policy and imports nothing from it) - so the equivalence is test-enforced rather than runtime-shared on the native side

AC-012:
GIVEN:
a forward candle missing its open, high, low or close value, and candles whose prices are impossible, non-positive or non-finite
WHEN:
the prospective outcome is measured
THEN:
the outcome is PARTIAL_COVERAGE with coverage_reason INVALID_OHLC and an explicit ohlc_problem classifying the failure as MISSING_FIELD, IMPOSSIBLE_RANGE, NON_POSITIVE or NON_FINITE, so absent data is never disguised as an ordinary missing interval and no incomplete bar is measured

EXPLICITLY OUT OF SCOPE:
- Any change to Tradevisor qualification, actionable status, notification, canonical handoff or execution authority
- Any change to F5 feasibility, F7 allocation, F4 lifecycle or F11 protection semantics
- Any change to the learning release gate, job scheduling, deployment units or consumption acknowledgement protocol
- Any change to retention, capacity, backpressure or pruning behaviour
- Any change to thresholds, risk defaults, geometry formulas or capital algorithms
- Any change to the native scanner's own validation policy, or importing scanner modules into the learning path
- Any change to provider selection, candle interval configuration or the measurement window length
- Activating, scheduling or deploying the learning job

FROZEN BOUNDARIES:
- `DEFINITIVE_MEASUREMENT_STATUSES` stays `{"MEASURED", "NO_ENTRY_TOUCH"}`: only those two statuses can support a definitive outcome.
- A touch is never a fill: `fill_attribution` stays `NONE_TOUCH_INFERRED` and `basis` stays `TOUCH_BASED_ESTIMATE_NOT_REALIZED`.
- Measurement is strictly forward of the decision instant and never reads a pre-decision bar.
- Incomplete coverage never discards evidence: rows stay durable and retryable; only a definitive outcome can support retention pruning.
- The exact-release gate continues to run before any candle is read.
- No policy promotion is produced: `policy_promotion` stays `NONE` and `trade_authority_changed` stays false.
- The set of coverage reasons may be extended (this increment adds `CLOSE_TIME_CONTRADICTION`); existing reasons keep their meaning.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_001_non_finite_prices_are_never_measured
AC-002 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_002_non_numeric_and_boolean_prices_are_never_measured
AC-003 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_003_non_positive_prices_are_never_measured
AC-004 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_004_impossible_geometry_is_never_measured
AC-005 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_005_every_candle_invalid_is_a_partial_coverage_non_measurement
AC-006 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_006_invalid_out_of_window_data_cannot_corrupt_a_valid_measurement
AC-007 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_007_contradictory_provider_close_time_is_refused
AC-008 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_008_conflicting_open_restatement_is_a_conflict
AC-009 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_009_identical_restatement_still_measures_without_a_fill
AC-010 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_010_replay_is_deterministic
AC-011 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_011_shared_predicate_agrees_with_the_native_owner
AC-011 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_011_the_native_owner_is_not_coupled_to_the_shared_predicate
AC-012 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_012_a_missing_price_field_is_reported_as_absent
AC-012 -> tests/test_tradevisor_learning_geometry_v1.py::test_ac_012_the_problem_class_distinguishes_absent_from_corrupt

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-008 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-010 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1.md
AC-011 -> OHM-Trade-Agent-v1/app/services/price_geometry.py
AC-011 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-012 -> OHM-Trade-Agent-v1/app/services/price_geometry.py
AC-012 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_geometry_v1.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-012 -> OHM-Trade-Agent-v1/tests/test_tradevisor_recovery_v1.py

DEFERRED DISCOVERIES:
- Sharing status: the learning path shares pp/services/price_geometry.validated_ohlc at runtime; the native validator does not import it, so its equivalence with the measurement path is enforced by an acceptance test (which fails on drift) rather than by coupling. Adopting the shared predicate inside the native owner would change scanner admission code and is deliberately left to a separate increment.
- `_finite_number` still accepts a numeric string (`"100.0"`). That leniency predates this increment and was left unchanged; only non-numeric values are refused.
- Provider close times are compared with a 60-second tolerance. A provider with a different bar-close convention would be classified `CLOSE_TIME_CONTRADICTION`; the production Kraken provider reports no close time at all, so the check is inert there today.
- Coverage diagnostics still describe the `expected/observed` grid from the declared interval (N-02 of the previous increment); this increment does not change that contract.

UNAPPROVED SCOPE CHANGES:
NONE

