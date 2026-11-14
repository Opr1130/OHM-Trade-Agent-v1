INCREMENT:
ATDD-OPIP-V6-GATE-PARITY-v1

OWNER-APPROVED INTENT:
Owner decision V6-D02 (2026-11-13): approve design and isolated implementation of
a shared, pure, versioned gate interface; do not change the live scanner
qualification path until S1 evidence and compatibility verification are complete.
This increment binds the offline simulator to the live gate constants so drift is
visible, and records the finding that the shared interface already exists in
app/opip/decision.

ARCHITECTURE REFERENCES:
- docs/v6/V6_SHARED_GATE_INTERFACE.md
- app/opip/decision/thresholds.py, gates.py, policy_snapshot.py (read-only)
- app/opip/simulator/parity.py

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the live gate constants
WHEN: the production-parity baseline is built
THEN: each mapped simulator field equals its production constant (with documented unit conversion)

AC-002:
GIVEN: a changed production constant
WHEN: the baseline is rebuilt
THEN: the baseline value and simulator digest change, and a change to any unmapped gate changes the full policy fingerprint, so drift is detectable; the baseline is labelled CODE_CONSTANTS_V1_REFERENCE, not live

AC-003:
GIVEN: the parity report
WHEN: it is generated
THEN: it lists the gates the simulator does not reproduce and marks approximations

AC-004:
GIVEN: the parity module
WHEN: it is imported
THEN: it reads thresholds only and imports no order, network, notification or scanner module

EXPLICITLY OUT OF SCOPE:
- Any change to the live scanner, gates, thresholds or production policy.
- Threshold injection into production gate functions (separate D2/D3 decision).
- Replay of historical decisions, profitability claims, S1/S4-dependent work.

FROZEN BOUNDARIES:
- app/opip/decision/*, app/core/strategy_policy*, app/jobs/scan_opportunities.py unchanged.
- Funded execution, F11, Committee, learning unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_v6_gate_parity_v1.py::test_production_baseline_equals_live_constants
AC-002 -> tests/test_opip_v6_gate_parity_v1.py::test_a_changed_production_constant_changes_the_baseline_and_digest
AC-002 -> tests/test_opip_v6_gate_parity_v1.py::test_unmapped_gate_change_moves_the_full_fingerprint_but_not_the_simulator_digest
AC-003 -> tests/test_opip_v6_gate_parity_v1.py::test_report_lists_unmodelled_gates_and_marks_approximations
AC-004 -> tests/test_opip_v6_gate_parity_v1.py::test_parity_module_is_read_only_and_does_not_import_execution_code

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/simulator/parity.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_v6_gate_parity_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-GATE-PARITY-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/opip/simulator/parity.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_v6_gate_parity_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/simulator/parity.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_v6_gate_parity_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/simulator/parity.py
AC-004 -> OHM-Trade-Agent-v1/docs/v6/V6_SHARED_GATE_INTERFACE.md
AC-004 -> OHM-Trade-Agent-v1/docs/v6/V6_ARCHITECTURE_DECISIONS.md

DEFERRED DISCOVERIES:
- Gate functions read module-level constants; evaluating a what-if policy through the real gates needs an injected policy parameter (live-path change, gated).
- Simulator confidence scale (0-1) differs from production (0-100); converted in parity only.

UNAPPROVED SCOPE CHANGES:
NONE
