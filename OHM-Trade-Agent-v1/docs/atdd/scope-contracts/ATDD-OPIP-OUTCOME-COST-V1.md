INCREMENT:
ATDD-OPIP-OUTCOME-COST-V1

OWNER-APPROVED INTENT:
Add two pure, additive measurement modules so profitability and AI spend can be stated with their evidence limits: (1) a deterministic signal outcome adapter that reports which of stop or target a price path reached first and returns INSUFFICIENT_EVIDENCE whenever ordering, coverage or inputs are missing; (2) a non-authoritative USD estimate of AI spend computed from recorded tokens and caller-supplied, provenance-stamped price entries. Neither module is wired into a runtime path, and neither grants any authority.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/opip/analytics/scorecard.py: hit-rate core that refuses a record without a boolean hit.
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory; no trading authority.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a signal with direction, entry, stop, target, time and horizon, and a price path covering the horizon
WHEN:
the outcome is measured
THEN:
the first level reached decides TARGET_HIT or STOP_HIT for LONG and SHORT, a path whose observations cover the whole horizon (last observation at the horizon, or a later one within the gap tolerance) with neither level is EXPIRED_NO_HIT with no stale price used as an exit, fees and slippage change only the net return, and the horizon edge is inclusive

AC-002:
GIVEN:
missing or invalid direction, levels, level order, signal time, horizon or observations, a candle or equal-timestamp observations spanning both levels, a gap in the path before the deciding observation, or a path ending before the horizon
WHEN:
the outcome is measured
THEN:
the result is INSUFFICIENT_EVIDENCE with a stated reason and no hit, and no Scorecard record is produced for it

AC-003:
GIVEN:
a price path
WHEN:
hypothetical excursion is requested
THEN:
MFE and MAE are reported under a separate evidence class, with hit null, and never feed the Scorecard

AC-004:
GIVEN:
recorded token usage and a price catalog with effective dates, source and verification date
WHEN:
a call is estimated
THEN:
the USD estimate is exact decimal arithmetic using the entry effective on the call date, carries price provenance, is marked estimate and not invoice, charges cached input at the cached rate or flags the uncached rate, and does not charge reasoning tokens twice

AC-005:
GIVEN:
a call with no effective price entry, missing or invalid tokens, or a missing timestamp
WHEN:
spend is summarized
THEN:
the call is UNPRICED, contributes nothing, is never costed at zero, the summary states it is incomplete, and malformed or float rates and overlapping periods are refused

EXPLICITLY OUT OF SCOPE:
- Wiring either module into a scan, scheduler, alert, admission, ranking, Scorecard job or API.
- Shipping any provider price, changing model routing or budgets, or any invoice reconciliation.
- Realized or paper returns, fill modelling, order or Kraken authority, deployment, restarts, runtime configuration, PostgreSQL, F11, Learning and Committee.

FROZEN BOUNDARIES:
- scorecard.py is unchanged and still refuses a record without a boolean hit.
- No trading, order or Kraken authority is added or widened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_outcome_cost_v1.py::test_ac_001_first_level_reached_decides_long_and_short
AC-001 -> tests/test_opip_outcome_cost_v1.py::test_ac_001_a_expiry_needs_full_horizon_coverage_and_net_costs_apply
AC-001 -> tests/test_opip_outcome_cost_v1.py::test_ac_001_b_hit_exactly_on_boundary_and_horizon_edge
AC-001 -> tests/test_opip_outcome_cost_v1.py::test_ac_001_c_expiry_requires_observed_coverage_through_the_horizon
AC-002 -> tests/test_opip_outcome_cost_v1.py::test_ac_002_missing_ordering_is_insufficient_evidence_never_a_hit
AC-002 -> tests/test_opip_outcome_cost_v1.py::test_ac_002_b_equal_timestamps_touching_both_levels_are_ambiguous_in_either_order
AC-002 -> tests/test_opip_outcome_cost_v1.py::test_ac_002_a_scorecard_never_receives_an_insufficient_record
AC-003 -> tests/test_opip_outcome_cost_v1.py::test_ac_003_excursion_is_separate_and_cannot_produce_a_hit
AC-004 -> tests/test_opip_outcome_cost_v1.py::test_ac_004_priced_estimate_is_exact_with_provenance_and_cache_handling
AC-004 -> tests/test_opip_outcome_cost_v1.py::test_ac_004_a_effective_dates_select_the_price
AC-005 -> tests/test_opip_outcome_cost_v1.py::test_ac_005_no_price_means_no_estimate_never_zero

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/outcome_adapter.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/outcome_adapter.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/analytics/outcome_adapter.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/analytics/ai_cost_estimate.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/analytics/ai_cost_estimate.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_outcome_cost_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-OUTCOME-COST-V1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
