INCREMENT:
ATDD-OPIP-V6-AUDIT-DOCS-v1

OWNER-APPROVED INTENT:
Record the v6 codebase audit as a documentation-only increment. Proposed for
owner approval; merging this contract is that approval. The audit lists
retirement candidates and measured droplet facts so the owner can review them
before any retirement or v6 build work starts. This increment changes no
runtime code, configuration, trading, learning, AI or notification behavior,
and deletes nothing.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_AUDIT.md: the audit document this increment adds.
- OHM-Trade-Agent-v1/CLAUDE.md: coding automation has no trading authority; smallest change.

APPROVED ACCEPTANCE CRITERIA:

AC-601:
GIVEN: the v6 audit document is checked in
WHEN: the owner or a later v6 slice opens it
THEN: it states the limits of its method and describes retirement as a disable, observe, then delete sequence, so candidates are not mistaken for decisions

EXPLICITLY OUT OF SCOPE:
- Deleting, disabling or moving any module, job, workflow or data file.
- Any change to runtime code, compose files, deploy scripts or live host configuration.
- Turning paper trading on, or changing Kraken, F11 or Telegram permissions.
- Defining the scope contracts for later v6 slices; each needs its own owner approval.

FROZEN BOUNDARIES:
- No application, tool, compose or workflow file changes.
- Trading authority, F11 admissions and fail-closed gates are unchanged.
- Other increments' contracts are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-601 -> tests/test_v6_audit_docs_v1.py::test_audit_states_method_limits_and_staged_retirement

IMPLEMENTATION MAP:
AC-601 -> OHM-Trade-Agent-v1/docs/V6_AUDIT.md
AC-601 -> OHM-Trade-Agent-v1/tests/test_v6_audit_docs_v1.py
AC-601 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-601 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-AUDIT-DOCS-v1.md

DEFERRED DISCOVERIES:
- Retirement themes R1 to R6 each need their own scope contract before any deletion.
- The rolling canonical ledger change needs its own contract and owner approval.
- Paper trading stays off until the new trade box exists.

UNAPPROVED SCOPE CHANGES:
NONE
