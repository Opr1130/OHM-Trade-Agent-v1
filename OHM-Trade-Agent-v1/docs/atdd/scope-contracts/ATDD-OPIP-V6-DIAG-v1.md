INCREMENT:
ATDD-OPIP-V6-DIAG-v1

OWNER-APPROVED INTENT:
The advance job reported no slice ready twice although a slice was eligible.
Print what it sees (queued issues, closed issues, open v6 PRs) so the cause is
visible in the run log. Logging only; no behavior change.

ARCHITECTURE REFERENCES:
- tools/v6_agent/advance.py

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the advance job runs
WHEN: it picks the next slice
THEN: it prints the queued count, closed count, open v6 PR numbers and the merged PR number before deciding

EXPLICITLY OUT OF SCOPE:
- Any behavior change, workflow, deploy file or application code.

FROZEN BOUNDARIES:
- No application, compose or deploy file changes.
- Trading authority and fail-closed gates are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_diag_v1.py::test_advance_prints_what_it_sees

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/v6_agent/advance.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_diag_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-DIAG-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
