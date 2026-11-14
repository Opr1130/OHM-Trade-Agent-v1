INCREMENT:
ATDD-OPIP-CAPACITY-EVIDENCE-V1

OWNER-APPROVED INTENT:
Record, as additive audit evidence, the numbers the actionable admission gate compared when it refuses a card on position capacity (proposed order notional, the action gate's recommended notional, 24h liquidity, weaker-side visible depth, capacity ceiling, allowed notional and their ratio), so a thin-book refusal can be told apart from a stale-size or unit defect without re-running a scan. Evidence only: no verdict, threshold, liquidity limit, ranking, delivery or trading behavior changes.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/app/services/actionable_admission.py: C3 capacity test (position_capacity_usd versus order_size_usd).
- OHM-Trade-Agent-v1/app/services/telegram_delivery.py: suppression ledger.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
an admission evaluation whose order exceeds, equals or is within the capacity ceiling, or whose evidence is missing
WHEN:
the verdict is computed
THEN:
the verdict, reason and reasons are identical to those without the new evidence fields, and the capacity record states order, recommended notional, ceiling, utilization, allowed notional, ratio and whether the order basis matches the recommended notional (null when unknown)

AC-002:
GIVEN:
a capacity refusal recorded through the Telegram suppression ledger
WHEN:
the row is written
THEN:
the row carries a bounded flat scalar evidence object, and a suppression written without evidence is byte-identical to the previous format

EXPLICITLY OUT OF SCOPE:
- Any change to admission verdicts, the capacity ceiling formula, utilization or liquidity limits, ranking, the action gate, or alert delivery.
- Deployment, restarts, runtime configuration, PostgreSQL, F11, Learning, Committee and order authority.

FROZEN BOUNDARIES:
- ActionableAdmission.evidence_payload() keys are unchanged.
- No trading, order or Kraken authority is added or widened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_capacity_evidence_v1.py::test_ac_001_capacity_record_never_changes_the_verdict
AC-001 -> tests/test_opip_capacity_evidence_v1.py::test_ac_001_a_boundary_and_missing_data
AC-001 -> tests/test_opip_capacity_evidence_v1.py::test_ac_001_b_stale_size_signature_is_recorded
AC-002 -> tests/test_opip_capacity_evidence_v1.py::test_ac_002_ledger_row_carries_bounded_evidence

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-002 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-002 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_capacity_evidence_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CAPACITY-EVIDENCE-V1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
