INCREMENT:
ATDD-RUFF-IMPORT-CLEANUP-M01

OWNER-APPROVED INTENT:
Implement a first deliberately small batch of six existing Ruff F401 unused import corrections on identified non-release-path modules, with no behavior changes and separately validated regression evidence. Preserve the active scanner increment pointer.

ARCHITECTURE REFERENCES:
- Architecture v1.5.0, EVIDENCE_SHADOW and frozen authorities
- quality-security.yml Ruff advisory scan baseline: 87 findings
- ATDD-CI-STABILIZATION-M01 and tests/atdd_scope.py fail-closed scope governance

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
six imports are identified as unused by Ruff 0.15.16
WHEN:
each module is modified only to remove its unused import binding
THEN:
the module remains syntactically valid with no unrelated behavior or imports changed

AC-002:
GIVEN:
the scanner reliability increment remains the active ATDD pointer
WHEN:
this cleanup change set is checked
THEN:
only strictly mapped source and acceptance files qualify and mixed changes fail closed

EXPLICITLY OUT OF SCOPE:
- core trading, risk assessment behavior, canonical writer or notifier logic changes
- changing Ruff policy, suppressions or advisory reporting
- general broad lint auto-fix, release or deployment

FROZEN BOUNDARIES:
- EVIDENCE_SHADOW; F11 independent protection; canonical writer ownership
- funded trading absent; PAPER_V2 OFF, Committee OFF, learning frozen
- no merge, deployment or activation

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_ruff_import_cleanup_v1.py::test_six_unneeded_import_bindings_absent_and_syntax_valid
AC-002 -> tests/test_ruff_import_cleanup_v1.py::test_ruff_change_set_remains_strictly_whitelisted

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/events/identity.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/events/provider_health.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/streaming/telemetry.py
AC-001 -> OHM-Trade-Agent-v1/app/services/opportunity_monitor_queue.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/risk/notifier.py
AC-001 -> OHM-Trade-Agent-v1/app/services/freqtrade_result_ingest.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_ruff_import_cleanup_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RUFF-IMPORT-CLEANUP-M01.md
AC-002 -> OHM-Trade-Agent-v1/tests/atdd_scope.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_ruff_import_cleanup_v1.py

DEFERRED DISCOVERIES:
- Exact-head CI and advisory Ruff count verification when stacked PR is retargeted after CI-01
- Remaining 81 expected Ruff findings tracked in the quality-debt issue; count not independently certified yet

UNAPPROVED SCOPE CHANGES:
NONE
