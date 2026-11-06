INCREMENT:
ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1

OWNER-APPROVED INTENT:
The owner directs a corrective, execution-safe increment to finish what
ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1 deferred: remove the residual historical
economic floors from ADVISORY recommendation qualification, and keep every
execution protection exactly as it is. The three optional advisory filters -
minimum estimated net profit, minimum Target 2 movement and minimum reward/risk -
are OFF by default, may be enabled only with validated bounded values, and must
never silently revert to $75, 4 percent or 2.5:1. Disabling a filter does not
remove the independent requirement that modelled net profit after fees, slippage
and margin costs be strictly positive, and does not relax liquidity capacity,
executable notional, spread, price freshness, entry/stop geometry, capital
availability, portfolio exposure, F11 protection, exchange restrictions or
funded-trading authorization. The November 6 production audit recorded four
AKTUSD LONG opportunities whose advisory rejection was reported as NO_CAPITAL
while the binding rule was the hardcoded $75 profit floor; that misreporting is
in scope. No deployment, merge, funded trading, learning, paper admission, IRR
activation or strategy-profile activation is authorized by this increment.

ARCHITECTURE REFERENCES:
- docs/atdd/scope-contracts/ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1.md: superseded scope for the action gate and Tradevisor paths.
- docs/atdd/scope-contracts/ATDD-OPIP-CONFIGURABLE-SCORING-v1.md: AC-007 advisory versus execution separation.
- app/services/economic_quality_gate.py: single owner of the advisory economic policy.
- app/core/strategy_policy.py: versioned validated advisory configuration.
- AGENTS.md: authority boundaries, fail-closed gates and learning/execution separation.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: no optional advisory economic thresholds are configured
WHEN: a candidate has valid market evidence and positive after-cost economics
THEN: no historical fixed-dollar, percentage-move or reward/risk minimum may reject it.

AC-002:
GIVEN: the optional net-profit filter is disabled
WHEN: the estimated net profit after applicable costs is tested at -0.01, 0, 0.01, 10, 25.70, 28.06, 36.86, 74.99, 75, 75.01 and 100 dollars
THEN: eligibility is exactly strict after-cost positivity with no discontinuity at $75, and zero, negative or unavailable economics are never reported as profitable.

AC-003:
GIVEN: the minimum net profit filter is explicitly enabled at $25
WHEN: the projection is $24.99, $25.00 and $25.01
THEN: $24.99 fails that optional filter, $25.00 and $25.01 pass it, and every unrelated protection still applies.

AC-004:
GIVEN: the Target 2 movement filter is disabled or explicitly set to 4 percent
WHEN: the deterministic projected movement is below 4 percent, or 3.99, 4.00 and 4.01 percent
THEN: the disabled filter rejects nothing for size alone, and the enabled filter rejects 3.99 percent while admitting 4.00 and 4.01 percent.

AC-005:
GIVEN: the reward/risk filter is disabled or explicitly set to 2.5:1
WHEN: a structurally valid plan offers 2.0:1, or 2.49, 2.50 and 2.51:1
THEN: the disabled filter rejects nothing for the old 2.5:1 bar, the enabled filter rejects 2.49 while admitting 2.50 and 2.51, and no filter authorizes invalid geometry.

AC-006:
GIVEN: an advisory filter value is missing, null, blank, zero, negative, non-numeric, NaN, infinite, extreme, unknown or carried by an incompatible policy version
WHEN: the configuration is resolved or validated
THEN: zero is the only disabled representation, every invalid form is rejected without silently resetting the policy, and the versioned profile refuses unknown keys, out-of-bound values and unsupported schema versions.

AC-007:
GIVEN: the four November 6 AKTUSD LONG opportunities with capacity-adjusted estimated net profits of 25.70, 28.06, 36.86 and 18.89 dollars
WHEN: the capital/portfolio action gate re-verifies them at the deployable capital
THEN: none is rejected solely by the retired $75 rule, the liquidity-capacity adjustment and allocation recalculations are preserved, a configured floor or a non-positive projection is still refused and truthfully classified, and all unrelated risk controls still operate.

AC-008:
GIVEN: a scan runs the real selection, pre-AI screen, Chief review, target quality, economics, ranking, capital/portfolio gate and notification boundary
WHEN: one candidate has positive after-cost economics and another is economically invalid
THEN: the valid positive opportunity reaches the notification boundary without being lost to a disabled optional filter, and the invalid opportunity remains blocked as a policy rejection.

AC-009:
GIVEN: the Tradevisor advisory evaluation qualifies an opportunity and then re-evaluates it at the F7-permitted allocation
WHEN: the final displayed economics are produced
THEN: both steps apply the labelled advisory policy, the displayed net profit is the deployable one, and no exchange, funded or execution authority is created.

AC-010:
GIVEN: an advisory recommendation passes the optional-filter policy
WHEN: authority, F11 protection, paper admission, operator mode and exchange permissions are inspected
THEN: none of them changes, the notifier still requires an authorized action gate, and no notification bypasses a risk control.

AC-011:
GIVEN: the versioned strategy configuration is default, drafted, activated, rolled back, stale, invalid or reloaded after restart
WHEN: each lifecycle step is exercised
THEN: drafts never activate runtime, stale versions and failed validation fail closed, activation is explicit and separately authorized, rollback restores the previous version, persistence is on disk, and no configuration is activated by this increment.

AC-012:
GIVEN: a rejection is recorded under the advisory policy
WHEN: its threshold metadata, policy identity and reason string are read
THEN: they match the rule actually applied, the execution-compatible constants are retained on execution-only paths, historical records are not rewritten, and a source-wide audit finds no advisory call site still referencing a historical fixed floor.

AC-013:
GIVEN: the owner has explicitly enabled a minimum net profit of $25, a minimum Target 2 movement of 4 percent, or a minimum reward/risk of 2.5:1 in the versioned advisory profile
WHEN: the scanner, the pre-AI screen, the shadow decision engine and the Tradevisor advisory evaluation qualify a candidate
THEN: each advisory path consumes that same effective, validated, versioned policy from its owning orchestration layer, each configured threshold is enforced independently and named with its configured value, a pure evaluator never reads the profile itself, and only a malformed or unreadable profile fails closed without any path substituting the all-OFF default.

EXPLICITLY OUT OF SCOPE:
- Deployment, merge, release-profile change or strategy-profile activation.
- Funded trading, live exchange execution, learning activation, paper admission or IRR activation.
- Any change to F11 protection, fixed risk sizing, execution-path minimum reward/risk defaults, position limits or risk ceilings.
- The unrelated OpenAI ValueError issue, and any trade-quality or ranking redesign.
- Percentage-move, liquidity, spread or cost-model recalibration beyond the existing bounded cost assumption.

FROZEN BOUNDARIES:
- MIN_NET_PROFIT, MIN_TARGET_2_MOVE_PCT and MIN_REWARD_TO_RISK remain the execution-compatible defaults of the shared economic gate.
- The advisory configuration keeps its versioned schema, optimistic concurrency, audit trail and separate administrator credential.
- The notifier keeps requiring action_gate_allowed; an unevaluated gate stays refused.
- F11 protection, funded-trading authorization, paper isolation and learning separation are untouched.
- The bundled production reference profile is not modified by this increment.


ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_001_default_off_admits_positive_after_cost_candidate
AC-001 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_001_missing_optional_configuration_is_off
AC-002 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_002_net_profit_boundaries_have_no_step_at_seventy_five
AC-002 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_002_zero_and_negative_after_cost_outcomes_are_not_recommendable
AC-003 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_003_configured_dollar_floor_boundaries
AC-004 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_004_target_move_filter_off_and_configured_boundaries
AC-005 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_005_reward_risk_filter_off_and_configured_boundaries
AC-006 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_006_invalid_advisory_configuration_is_refused
AC-006 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_006_profile_validation_rejects_unknown_keys_and_versions
AC-007 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_007_november_aktusd_capacity_rejections_are_not_caused_by_the_old_floor
AC-007 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_007_negative_capacity_adjustment_still_blocks
AC-008 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_008_advisory_journey_admits_positive_opportunity
AC-008 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_008_seeded_exposure_still_blocks_the_advisory
AC-008 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_008_advisory_journey_still_blocks_invalid_opportunity
AC-009 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_009_tradevisor_final_allocation_economics_use_advisory_policy
AC-010 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_010_advisory_pass_does_not_grant_execution_or_change_authority
AC-010 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_010_notification_boundary_still_requires_an_authorized_gate
AC-011 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_011_configuration_lifecycle_is_versioned_and_not_activated
AC-012 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_012_recorded_threshold_and_policy_match_the_applied_rule
AC-012 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_012_source_wide_old_floor_call_site_audit
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_configured_minimum_profit_is_enforced_by_tradevisor
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_configured_target_move_is_enforced_by_tradevisor
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_configured_reward_risk_is_enforced_by_tradevisor
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_configured_thresholds_reach_both_tradevisor_economic_bases
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_configured_thresholds_are_enforced_by_the_shadow_engine
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_shadow_policy_reaches_the_engine_from_orchestration
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_configured_threshold_governs_the_native_scan
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_admin_ui_serializes_empty_optional_field_as_zero
AC-013 -> tests/test_advisory_economic_policy_correction_v1.py::test_ac_013_malformed_stored_configuration_fails_closed

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/economic_quality_gate.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/economic_quality_gate.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/economic_quality_gate.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/economic_quality_gate.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/economic_quality_gate.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/economic_quality_gate.py
AC-006 -> OHM-Trade-Agent-v1/app/core/strategy_policy.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/trade_action_gate.py
AC-007 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/decision/observer.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_wave9_capacity_action_gate.py
AC-008 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/decision/gates.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/decision/thresholds.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-011 -> OHM-Trade-Agent-v1/app/api/strategy_admin.html
AC-011 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/decision/gates.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/decision/thresholds.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_wave9_action_notification_reliability.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ADVISORY-ECONOMIC-POLICY-CORRECTION-v1.md
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1.md
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-013 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-013 -> OHM-Trade-Agent-v1/app/services/tradevisor_pipeline.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/decision/engine.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/decision/observer.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/decision/gates.py
AC-013 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-013 -> OHM-Trade-Agent-v1/app/api/strategy_admin.html
AC-013 -> OHM-Trade-Agent-v1/tests/test_advisory_economic_policy_correction_v1.py

DEFERRED DISCOVERIES:
The shadow decision engine cannot read the active advisory profile without
filesystem I/O, which its feasibility contract forbids, so its economic gate
applies the bounded advisory default and records
advisory_overrides_visible=false. Wiring the owner override into the shadow
engine requires an approved read-only policy-injection seam and is deferred.
A Tradevisor call site likewise records the advisory default rather than the
owner profile for the same reason. Blank and null values are refused by the
advisory resolver instead of being coerced to OFF; only the owner editor's empty
field is normalized to the approved zero representation, and the durable
disabled representation for stored profiles remains the numeric zero already in
use. Execution-path floors stay at $75 / 4% / 2.5:1 and are now labelled with
their own policy id; proving that no future execution caller silently adopts the
advisory policy needs the same call-site audit extended to execution modules in a
separate increment.

UNAPPROVED SCOPE CHANGES:
NONE

