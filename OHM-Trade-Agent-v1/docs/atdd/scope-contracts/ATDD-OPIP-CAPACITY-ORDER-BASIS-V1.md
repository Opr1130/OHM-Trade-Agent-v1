INCREMENT:
ATDD-OPIP-CAPACITY-ORDER-BASIS-V1

OWNER-APPROVED INTENT:
The actionable admission gate's position-capacity test must judge the order the card actually recommends. Today the action gate caps the allocation to the visible-book capacity ceiling and records the capped notional, but the scan hands the admission gate the earlier pre-cap economic notional, so every capacity-capped candidate is refused for exceeding the ceiling it was just capped to; cent rounding of the cap can also push the capped order above the ceiling. Correct the order basis and the rounding direction. The liquidity ceiling formula, utilization limit and every liquidity threshold are unchanged and an order that exceeds the ceiling is still refused. OWNER/CHIEF-ARCHITECT REVIEW REQUIRED: this changes which candidates are admitted.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/app/services/trade_action_gate.py: capacity cap and recommended_position_notional.
- OHM-Trade-Agent-v1/app/services/actionable_admission.py: C3 capacity test.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a candidate whose allocation the action gate capped to the capacity ceiling, and separately candidates with a missing, non-finite, non-positive, boolean or non-numeric recommended notional
WHEN:
the order size for the capacity test is chosen
THEN:
the capped candidate is judged at its recommended notional and is not refused for capacity, an order still above the ceiling is refused, and an unusable recommended notional falls back to the economic notional or, when none exists, to ORDER_SIZE_UNUSABLE

AC-002:
GIVEN:
any capacity ceiling and leverage
WHEN:
the action gate caps the allocation
THEN:
the capped capital and the recommended notional are rounded toward zero to whole cents and never exceed the ceiling

AC-003:
GIVEN:
the scan's two uses of the order notional
WHEN:
the source is inspected
THEN:
both read the contemplated order notional helper and neither reads the pre-cap economic notional directly

AC-004:
GIVEN:
a sweep of requested capital, capacity ceiling and leverage run through the real action gate and the real capacity check
WHEN:
orders are judged on the recommended basis and on the old pre-cap basis
THEN:
an uncapped order is judged identically, an order is admitted exactly when it fits the ceiling times the utilization limit, no previously admitted candidate becomes refused, only capped candidates change decision (refused to admitted), and every other gate (market liquidity, spread, slippage, depth coverage, missing or non-finite ceiling or order size, a book thinner than the order) still refuses

EXPLICITLY OUT OF SCOPE:
- Any change to the capacity ceiling formula, MAX_CAPACITY_UTILIZATION, minimum market liquidity, spread, slippage or depth limits.
- Deployment, restarts, runtime configuration, PostgreSQL, F11, Learning, Committee and order authority.

FROZEN BOUNDARIES:
- No liquidity limit is relaxed; the economic gate's pre-cap economic_position_notional field is not modified.
- No trading, order or Kraken authority is added or widened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_001_a_capacity_capped_order_is_judged_at_its_capped_size
AC-001 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_001_b_limits_are_not_relaxed
AC-001 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_001_c_basis_selection_fails_closed
AC-002 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_002_capped_notional_never_exceeds_the_ceiling
AC-003 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_003_scan_call_sites_use_the_contemplated_order_basis
AC-004 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_004_a_uncapped_orders_are_judged_exactly_as_before
AC-004 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_004_b_no_false_admission_across_the_capacity_grid
AC-004 -> tests/test_opip_capacity_order_basis_v1.py::test_ac_004_c_thin_books_and_missing_evidence_still_refuse_a_capped_order

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-002 -> OHM-Trade-Agent-v1/app/services/trade_action_gate.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_capacity_order_basis_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CAPACITY-ORDER-BASIS-V1.md
AC-004 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
