INCREMENT:
ATDD-OPIP-V6-ADVANCE-v1

OWNER-APPROVED INTENT:
After the spec PR merged, the advance job reported no slice ready because the
just-merged PR could still be listed as open. Exclude the merged PR from the
open-PR count so the next queued slice starts. No application, deploy, trading
or notification behavior changes.

ARCHITECTURE REFERENCES:
- .github/workflows/v6-agent.yml: the advance job.
- tools/v6_agent/advance.py

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a v6 PR has just merged and the open-PR list still includes it
WHEN: the advance job picks the next slice
THEN: the merged PR number is passed as MERGED_PR and excluded from the open v6 PR count

EXPLICITLY OUT OF SCOPE:
- Any other workflow, deploy file, application code or builder logic.

FROZEN BOUNDARIES:
- No application, compose or deploy file changes.
- The builder still never pushes to main or touches protected paths.
- Trading authority and fail-closed gates are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_advance_v1.py::test_merged_pr_excluded_from_open_count

IMPLEMENTATION MAP:
AC-001 -> .github/workflows/v6-agent.yml
AC-001 -> OHM-Trade-Agent-v1/tools/v6_agent/advance.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_advance_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-ADVANCE-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
