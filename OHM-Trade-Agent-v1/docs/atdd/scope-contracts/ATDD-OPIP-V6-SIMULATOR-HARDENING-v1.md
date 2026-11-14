INCREMENT:
ATDD-OPIP-V6-SIMULATOR-HARDENING-v1

OWNER-APPROVED INTENT:
Close the three P2 review findings the Codex reviewer raised on merged PRs #561
and #562 so the offline simulator cannot count malformed or unreadable
candidates as qualified evidence and the parity baseline does not invent a
confidence admission rule. Simulator-only; no live scanner, gate or policy change.

ARCHITECTURE REFERENCES:
- app/opip/simulator/engine.py (fail-closed data contract)
- app/opip/simulator/parity.py (code-constants v1 reference baseline)
- app/services/recommendation_gate.py::candidate_alert_authorized (confidence is measurement evidence; read-only reference)

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: a candidate whose stop or target price is zero or negative
WHEN: it is evaluated under any policy
THEN: it is rejected with GEOMETRY_INVALID and never reports qualifying economics

AC-002:
GIVEN: a policy that blocks one or more BTC regimes
WHEN: a candidate has a missing, empty, blank or non-string btc_regime
THEN: it is rejected with DATA_MISSING:btc_regime, while the same candidate is unaffected when no regime is blocked

AC-003:
GIVEN: the code-constants v1 reference baseline
WHEN: a candidate with confidence below the production measurement boundary is evaluated
THEN: the baseline adds no confidence rejection and the parity report marks the field MEASUREMENT_ONLY_NOT_ADMISSION

EXPLICITLY OUT OF SCOPE:
- Any change to the live scanner, gates, thresholds, notifications or production policy.
- Gate threshold injection (D10), simulator use of app/opip/gates, profitability claims.

FROZEN BOUNDARIES:
- app/opip/decision/*, app/opip/gates/*, app/services/recommendation_gate.py, strategy_policy* unchanged.
- Funded execution, F11, Committee, learning unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_v6_strategy_simulator_v1.py::test_nonpositive_stop_or_target_prices_are_rejected_as_invalid_geometry
AC-002 -> tests/test_opip_v6_strategy_simulator_v1.py::test_missing_backdrop_fails_closed_only_when_the_filter_is_enabled
AC-003 -> tests/test_opip_v6_gate_parity_v1.py::test_low_confidence_candidate_is_not_rejected_by_the_production_reference_baseline

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/simulator/engine.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_v6_strategy_simulator_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SIMULATOR-HARDENING-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/opip/simulator/engine.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_v6_strategy_simulator_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/simulator/parity.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_v6_gate_parity_v1.py

DEFERRED DISCOVERIES:
- Missing slippage without a configured limit is still an explicit zero-cost assumption (flagged slippage_assumed_zero); a realistic replay must require observed cost data.

UNAPPROVED SCOPE CHANGES:
NONE
