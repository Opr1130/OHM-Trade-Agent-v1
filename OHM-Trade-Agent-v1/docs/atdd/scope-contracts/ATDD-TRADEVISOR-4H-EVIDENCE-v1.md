INCREMENT:
ATDD-TRADEVISOR-4H-EVIDENCE-v1

OWNER-APPROVED INTENT:
Implement the *classification* half of owner policy G-04: a deterministic, pure owner that decides whether a single candidate 4H bar may be used as completed 4H context at a given evaluation instant. Tradevisor SWING qualification requires independently trusted completed 4H context and none exists, so `regime_4h` stays the constant UNAVAILABLE and every SWING stays WATCH. This increment therefore adds a classifier, a duplicate-reconciliation rule and a provenance gate - and nothing else: no provider, no network client, no trust promotion, no wiring into the advisory evaluator, no change to qualification. An `AVAILABLE` classification is structural usability only (`trusted=False` by construction).

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py: records `regime_4h=UNAVAILABLE`, appends `SWING_4H_CLOSED_CONTEXT`, and is deliberately unchanged.
- OHM-Trade-Agent-v1/app/exchanges/kraken.py: the approved native public OHLC provider whose bar shape this contract mirrors.
- OHM-Trade-Agent-v1/app/scanner/market_data_validation.py: the native geometry policy (numeric, finite, positive, `low <= open, close <= high`).
- OHM-Trade-Agent-v1/TRADEVISOR_V2.md: the G-04 statement that 1H is never a substitute for completed 4H.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md: the increment whose G-04 policy this serves.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a native 240-minute bar from an approved provider, aligned on the UTC epoch grid, closed before the evaluation instant, with coherent OHLC and provider provenance
WHEN:
the completed-4H classifier evaluates it
THEN:
it reports AVAILABLE with interval, open, close and observation stamps recorded, and the evidence block states trusted false and trade_authority_changed false, because structural usability is never production trust

AC-002:
GIVEN:
a candidate bar whose interval is not exactly 240 minutes or is only close to it, whose open time is off the 4-hour grid, whose reported close contradicts the interval, or whose market, instrument or venue does not match the evaluated one
WHEN:
the classifier evaluates it
THEN:
it reports UNAVAILABLE or INVALID with the specific reason, never AVAILABLE, and nothing is rescaled, imputed or re-mapped to fit

AC-003:
GIVEN:
a candidate bar that has not closed yet, that closes after the evaluation instant, or that closed longer ago than the freshness bound
WHEN:
the classifier evaluates it
THEN:
it reports UNAVAILABLE with STILL_FORMING, FUTURE_BAR or STALE_BAR respectively, so a forming, future or stale bar can never be completed context

AC-004:
GIVEN:
a candidate bar with a non-finite, non-numeric, boolean, zero or negative price, or with high below low, high below open or close, or low above open or close
WHEN:
the classifier evaluates it
THEN:
it reports INVALID with INVALID_OHLC

AC-005:
GIVEN:
a candidate bar whose provenance is absent, names no provider, is declared synthetic, imputed or derived-from-1H, is marked as coming from an alert timeframe, or whose timestamps are naive
WHEN:
the classifier evaluates it
THEN:
it reports UNAVAILABLE or INVALID with the matching reason, so no synthetic, imputed, 1H-derived, alert-derived or timezone-ambiguous evidence can enter the 4H trust path

AC-006:
GIVEN:
several records of the same 4H bar, a repeated evaluation of the same candidate, a 1-hour bar offered as 4H context, and an alert claiming a 240-minute timeframe
WHEN:
duplicates are reconciled and the classifier re-evaluates
THEN:
identical restatements collapse to one AVAILABLE result, conflicting restatements are refused, replay is byte-identical, and neither a 1H bar nor an alert timeframe ever yields AVAILABLE 4H context

AC-007:
GIVEN:
a sequence of candidate 4H bars offered as one evidence window, covering a complete grid, a missing bar, an identical restatement, a conflicting restatement, a forming latest bar, a stale bar, an incoherent bar, and a provider that raises or returns nothing
WHEN:
the window is classified
THEN:
only a window whose every expected bar is present exactly once, coherent, from the matched venue and instrument and closed before the evaluation instant is AVAILABLE; every other case is UNAVAILABLE or INVALID with the specific reason and the missing open times listed, and a provider fault is a refusal rather than an exception

EXPLICITLY OUT OF SCOPE:
- Any market-data provider, HTTP client, credential or subscription
- Any wiring into `tradevisor_evaluation`, SWING qualification or notification
- Any promotion of 4H evidence to trusted, or any change to `regime_4h`
- Any new persistent store, cache, scheduler or deployment unit
- Any change to F6, F5, F7, F11, canonical-writer or release-profile behaviour
- Any activation of Tradevisor, paper or funded trading

FROZEN BOUNDARIES:
- `regime_4h` remains the constant `UNAVAILABLE` in the advisory evaluator, and SWING continues to list `SWING_4H_CLOSED_CONTEXT`.
- `AVAILABLE` never implies trust: `trusted` is always false in the returned evidence.
- 1H is never a substitute for completed 4H.
- The classifier is pure: no clock read (the caller supplies `now`), no filesystem, no network, no mutable global state.
- The native geometry policy is mirrored, not loosened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_four_hour_v1.py::test_ac_001_a_native_completed_bar_is_structurally_available
AC-002 -> tests/test_tradevisor_four_hour_v1.py::test_ac_002_structural_mismatches_are_refused
AC-003 -> tests/test_tradevisor_four_hour_v1.py::test_ac_003_forming_future_and_stale_bars_are_refused
AC-003 -> tests/test_tradevisor_four_hour_v1.py::test_ac_003_a_forming_bar_can_never_be_completed_context
AC-004 -> tests/test_tradevisor_four_hour_v1.py::test_ac_004_impossible_ohlc_is_invalid
AC-005 -> tests/test_tradevisor_four_hour_v1.py::test_ac_005_provenance_must_be_genuine
AC-005 -> tests/test_tradevisor_four_hour_v1.py::test_ac_005_naive_datetimes_are_refused
AC-006 -> tests/test_tradevisor_four_hour_v1.py::test_ac_006_identical_restatements_collapse_and_conflicts_are_refused
AC-006 -> tests/test_tradevisor_four_hour_v1.py::test_ac_006_replay_is_deterministic
AC-006 -> tests/test_tradevisor_four_hour_v1.py::test_ac_006_a_1h_bar_or_alert_never_stands_in_for_4h
AC-007 -> tests/test_tradevisor_four_hour_v1.py::test_ac_007_a_complete_window_is_structurally_available
AC-007 -> tests/test_tradevisor_four_hour_v1.py::test_ac_007_a_missing_bar_makes_the_window_unavailable
AC-007 -> tests/test_tradevisor_four_hour_v1.py::test_ac_007_identical_restatements_collapse_and_conflicts_refuse
AC-007 -> tests/test_tradevisor_four_hour_v1.py::test_ac_007_a_partial_latest_bar_refuses_the_window
AC-007 -> tests/test_tradevisor_four_hour_v1.py::test_ac_007_a_stale_or_incoherent_bar_refuses_the_window
AC-007 -> tests/test_tradevisor_four_hour_v1.py::test_ac_007_a_provider_fault_is_an_unavailable_window
AC-007 -> tests/test_tradevisor_four_hour_v1.py::test_ac_007_window_replay_is_deterministic

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/tradevisor_four_hour.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_four_hour_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/tradevisor_four_hour.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_four_hour_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/tradevisor_four_hour.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_four_hour_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/tradevisor_four_hour.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_four_hour_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/tradevisor_four_hour.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_four_hour_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/tradevisor_four_hour.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_four_hour_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-4H-EVIDENCE-v1.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_four_hour.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_four_hour_v1.py

DEFERRED DISCOVERIES:
- No provider is wired, so nothing can call the classifier in production. Integration requires an owner-approved provider decision and the evaluator change that consumes `AVAILABLE` context; neither is authorized here.
- Insufficient lookback, source-schema drift and source disagreement are properties of a *sequence* of bars; this increment classifies one bar plus its restatements. A window-level owner is deliberately not created.
- Provider timeouts and exceptions are the caller's concern; the classifier performs no I/O, so there is nothing to time out.
- The freshness bound is a parameter (default 4 hours) with no owner-approved value; a consumer must choose it explicitly.

UNAPPROVED SCOPE CHANGES:
NONE

