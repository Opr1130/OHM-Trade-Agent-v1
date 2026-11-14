INCREMENT:
ATDD-OPIP-PG-ADMISSION-GATE-V1

OWNER-APPROVED INTENT:
Close architecture gap V6-ARCH-007 by connecting the scan's NEW-opportunity notification decision to the business-store authority check from ATDD-OPIP-POSTGRES-PHASE1-V1. The connection is inert while the authority is FILES (the default and only pre-cutover state): no database import, no connection, no behavioural change. When PostgreSQL is the configured authority, or the authority configuration is invalid, a NEW opportunity notification is refused unless the database proves it is authoritative at the configured epoch. This does not perform or authorize a cutover, an apply of migration 007, a historical migration or any runtime configuration change. Stacked on the PostgreSQL Phase 1 branch; requires that branch's review first.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py: new_admissions_allowed and the authority configuration.
- Issue #589, V6-ARCH-007.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the authority is unset, empty or FILES
WHEN:
the scan asks whether a NEW admission may proceed
THEN:
the answer is yes, no database driver or connection is touched, and the notification decision is identical to the decision without the new argument

AC-002:
GIVEN:
an invalid authority value, or POSTGRES with a missing URL or epoch, an unreachable database, a non-POSTGRES database authority, an epoch mismatch, or any unexpected error while PostgreSQL is configured
WHEN:
the scan asks whether a NEW admission may proceed
THEN:
the answer is a stable refusal reason prefixed BUSINESS_STORE_ and the notification is suppressed with that reason; the adapter never raises

AC-003:
GIVEN:
the scan source
WHEN:
it is inspected
THEN:
the refusal is evaluated once per scan strictly after the protection sweep and passed to the notification decision, and no module other than the scan imports the adapter, so F11, protection, exits and emergency alerts cannot depend on PostgreSQL

EXPLICITLY OUT OF SCOPE:
- Any cutover, authority change, apply of migration 007, deployment, restart or runtime configuration change.
- Writing delivery, AI-call or outcome records to PostgreSQL (separate increment), historical migration and legacy-file retirement.
- F11, Learning, Committee and order authority.

FROZEN BOUNDARIES:
- Behaviour with the authority unset or FILES is byte-identical for every existing caller.
- No trading, order or Kraken authority is added or widened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_pg_admission_gate_v1.py::test_ac_001_files_authority_is_inert_and_touches_no_database
AC-001 -> tests/test_opip_pg_admission_gate_v1.py::test_ac_001_notification_decision_is_unchanged_without_a_store_reason
AC-002 -> tests/test_opip_pg_admission_gate_v1.py::test_ac_002_every_unproven_postgres_state_refuses_with_a_stable_reason
AC-002 -> tests/test_opip_pg_admission_gate_v1.py::test_ac_002_unexpected_errors_refuse_and_never_raise
AC-002 -> tests/test_opip_pg_admission_gate_v1.py::test_ac_002_store_reason_suppresses_the_notification_but_never_hides_a_quality_reason
AC-003 -> tests/test_opip_pg_admission_gate_v1.py::test_ac_003_scan_evaluates_the_gate_once_after_the_protection_sweep
AC-003 -> tests/test_opip_pg_admission_gate_v1.py::test_ac_003_only_the_scan_imports_the_adapter

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/data_platform/admission_gate.py
AC-001 -> OHM-Trade-Agent-v1/app/services/notification_policy.py
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_pg_admission_gate_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PG-ADMISSION-GATE-V1.md
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/admission_gate.py
AC-002 -> OHM-Trade-Agent-v1/app/services/notification_policy.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_pg_admission_gate_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PG-ADMISSION-GATE-V1.md
AC-003 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_pg_admission_gate_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PG-ADMISSION-GATE-V1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
