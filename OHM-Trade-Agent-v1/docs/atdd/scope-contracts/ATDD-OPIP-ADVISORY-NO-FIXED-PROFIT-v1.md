INCREMENT:
ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1

OWNER-APPROVED INTENT:
The owner directs the removal of arbitrary hard-coded economic cutoffs from
O'Pip advisory discovery: fixed $75 net profit, 4 percent Target 2 movement,
and 2.5:1 reward-to-risk must not automatically suppress otherwise positive
after-cost scanner opportunities. The already versioned advisory strategy
profile may opt into bounded stricter filters. Fees and estimated costs remain
part of the net-profit calculation, and an estimated loss after costs does not
qualify as an actionable advisory. F11 protection, risk ceilings, capital,
funded authority, and all execution policy remain independent and unchanged.
No production deployment or strategy-profile activation is authorized.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md: deterministic qualification and F11.
- docs/atdd/scope-contracts/ATDD-OPIP-CONFIGURABLE-SCORING-v1.md: AC-007.
- app/services/economic_quality_gate.py: shared execution-compatible defaults.
- app/core/strategy_policy.py: versioned validated advisory configuration.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the existing shared economic gate still defaults to $75 minimum
WHEN: the scanner evaluates a positive after-cost advisory with less than $75 potential
THEN: it may qualify without relaxing the shared execution-compatible default.

AC-002:
GIVEN: advisory economics use modelled fees and costs
WHEN: net profit after costs is negative, or owner explicitly configures a stricter minimum
THEN: the advisory is not qualified and the optional threshold is honored.

AC-003:
GIVEN: an otherwise positive after-cost advisory with less than 4 percent movement or less than 2.5:1 ratio
WHEN: no stricter advisory profile is selected
THEN: it is not rejected solely because of these historical fixed filters; an explicit owner profile can apply them.

AC-004:
GIVEN: the repository policy is a versioned non-execution configuration
WHEN: the advisory defaults are read and validated
THEN: the net, movement and reward-risk optional minimums are zero by default;
cost estimates remain positive and configurable; the V2 technical score, trading
authority and safety protections do not change.

AC-005:
GIVEN: the existing authenticated draft editor and validator
WHEN: a non-execution advisory reward-risk filter is set to zero
THEN: zero is permitted and represented; protected execution and F11 settings cannot be changed from that editor.

EXPLICITLY OUT OF SCOPE:
Activating an advisory profile or trading system, deploying production, removing
real costs, overriding liquidity/data-quality checks, disabling F11 or other hard
risk controls, changing exchange/order policy, removing the technical V2 score
threshold, funded orders, new infrastructure, and AI probability guarantees.
The owner's broader requirement for safely configurable fees, slippage,
liquidity and F11 risk settings will require a separately versioned,
guardrail-bounded specification; this change does not falsely claim it exists.

FROZEN BOUNDARIES:
This change affects only the scanner's advisory economic-quality wrapper and
reference profile defaults/validation/editor. Shared economic gate constants,
execution-compatible callers, position limits, max stop-risk, read-only
comparison, F11 independent refusal and evidence/canonical history remain
unchanged. Production retains existing authority until separately released.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_advisory_policy_v2.py::test_advisory_has_no_dollar_cutoff_while_execution_defaults_unchanged
AC-002 -> tests/test_advisory_policy_v2.py::test_negative_after_costs_is_not_qualified_and_explicit_stricter_policy_is_preserved
AC-003 -> tests/test_advisory_policy_v2.py::test_optional_move_and_reward_risk_filters_default_off_but_can_be_enabled
AC-004 -> tests/test_advisory_policy_v2.py::test_versioned_reference_policy_disables_only_advisory_filters
AC-005 -> tests/test_advisory_policy_v2.py::test_private_editor_supports_disabled_advisory_reward_risk_filter

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-002 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-003 -> OHM-Trade-Agent-v1/app/core/strategy_policy.json
AC-003 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-004 -> OHM-Trade-Agent-v1/app/core/strategy_policy.py
AC-004 -> OHM-Trade-Agent-v1/app/core/strategy_policy.json
AC-004 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-005 -> OHM-Trade-Agent-v1/app/api/strategy_admin.html
AC-005 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1.md

DEFERRED DISCOVERIES:
The scanner's distinct target-quality vetoes and postqualification alert/rank
policy remain unchanged and require evidence-backed analysis before changes.
Production effective profile state is not verified by this PR. The broader
configurability of fees, slippage, liquidity and F11 requires preserving
non-bypassable safety controls, validation, owner authorization and receipts.

UNAPPROVED SCOPE CHANGES:
NONE
