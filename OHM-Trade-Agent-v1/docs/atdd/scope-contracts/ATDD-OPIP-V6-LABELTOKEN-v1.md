INCREMENT:
ATDD-OPIP-V6-LABELTOKEN-v1

OWNER-APPROVED INTENT:
The slice A run opened its PR, then the label swap failed with HTTP 403 even over
REST, because the GitHub App installation token cannot edit issue labels. Do the
label swap with the workflow token, which has issues: write. No application,
deploy, trading or notification behavior changes.

ARCHITECTURE REFERENCES:
- .github/workflows/v6-agent.yml: the "Open PR and report" step.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the v6 builder has opened its PR
WHEN: it marks the issue as built
THEN: the label calls run with the workflow token (github.token) over REST, so the run succeeds

EXPLICITLY OUT OF SCOPE:
- Any other workflow step, deploy file, application code or builder logic.

FROZEN BOUNDARIES:
- No application, tool, compose or deploy file changes.
- The builder still never pushes to main or touches protected paths.
- Trading authority and fail-closed gates are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_labeltoken_v1.py::test_label_swap_uses_workflow_token

IMPLEMENTATION MAP:
AC-001 -> .github/workflows/v6-agent.yml
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_labeltoken_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-LABELTOKEN-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
