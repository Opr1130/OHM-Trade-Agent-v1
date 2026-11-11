INCREMENT:
ATDD-OPIP-NATIVE-ALERT-SIZING-v1

OWNER-APPROVED INTENT:
On 2026-11-11 the owner directed end-to-end completion of the native V2 actionable-alert capacity-sizing fix. The action gate already caps recommended position notional against venue depth; the scanner passed the larger economic-validation envelope to notification admission instead. This scoped change uses final approved notional without weakening any gates, plus corrects legacy positive target-quality test fixtures that omitted execution evidence. The branch-only ATDD pointer selects this hotfix scope without changing the existing stability contract. The historic three alert suppressions are not independently proven resolved. No merge or production activation is authorized.

ARCHITECTURE REFERENCES:
- app/services/trade_action_gate.py owns final recommended_position_notional and portfolio approval (unchanged)
- app/jobs/scan_opportunities.py passes final notional to existing native actionable admission
- app/services/actionable_admission.py owns unchanged market-capacity, depth, spread and cost vetoes
- tests/test_opip_alert_actionable_only_regressions_v1.py pins handoff and existing veto behavior
- tests/test_target_attainability.py defines explicit, synthetic execution facts for positive test snapshots
- tests/atdd_scope.py and docs/atdd/ACTIVE_INCREMENT enforce an approved active scope

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: a native qualified opportunity with its final action-gate approved position smaller than the original economics envelope
WHEN: execution evidence is passed to actionable-notification admission
THEN: order_size_usd uses the approved recommended_position_notional rather than the pre-cap economic notional

AC-002:
GIVEN: a small action-approved notional, a larger pre-cap envelope and verified liquidity and depth evidence
WHEN: the unchanged actionable-admission execution checks run
THEN: the small approved order may pass capacity, the oversized order is refused, and uncovered depth is independently refused

AC-003:
GIVEN: positive target-quality tests whose synthetic snapshots previously omitted mandatory LONG execution evidence
WHEN: the tests run under conservative production-equivalent validation
THEN: positive tests supply explicit synthetic spread and execution drag instead of weakening the production missing-evidence refusal

EXPLICITLY OUT OF SCOPE:
No allocation formula, liquidity threshold, position minimum, F11 control, admission authority, funded or paper execution, learning activation, API models, Tradevisor ingress, deployment, merge, production scan, data deletion or notification trigger. No change to existing stability or prior alert scope contracts.

FROZEN BOUNDARIES:
- The action gate remains sole authority for approved capital/notional.
- Capacity, market liquidity, slippage, depth, net economics and portfolio-risk vetoes remain fail-closed.
- Positive synthetic test fixtures never enter production.
- The ATDD pointer changes only on this unmerged feature branch.
- F11 UNSAFE/unmanaged holdings require separate owner safety review.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_native_scan_uses_post_action_notional_for_notification_capacity
AC-002 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_post_action_notional_passes_only_within_verified_capacity
AC-003 -> tests/test_target_attainability.py::test_clean_target_scores_well_and_passes

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_regressions_v1.py
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_regressions_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_target_attainability.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-NATIVE-ALERT-SIZING-v1.md

DEFERRED DISCOVERIES:
Before any owner-approved production rollout, verify the exact measured book validation notional and slippage/depth support for the final recommended size; historic suppression ledger did not persist both capacity operands. F11 is independently UNSAFE with 20 unmanaged holdings. Do not claim alert delivery or trading profitability from CI alone.

UNAPPROVED SCOPE CHANGES:
NONE
