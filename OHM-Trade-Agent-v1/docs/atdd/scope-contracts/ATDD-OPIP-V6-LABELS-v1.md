INCREMENT:
ATDD-OPIP-V6-LABELS-v1

OWNER-APPROVED INTENT:
The first v6 smoke run built and opened a PR correctly, then failed on label
cleanup because the bot token cannot use GitHub's GraphQL label mutation.
Switch the label swap to REST calls so a successful build is reported as
successful. No application, deploy, trading or notification behavior changes.

ARCHITECTURE REFERENCES:
- .github/workflows/v6-agent.yml: the "Open PR and report" step.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the v6 builder has opened its PR
WHEN: it marks the issue as built
THEN: it removes the build label and adds the built label through REST API calls only, with no gh issue edit, so the run succeeds with the bot token

EXPLICITLY OUT OF SCOPE:
- Any other workflow, deploy file, application code or the builder logic itself.

FROZEN BOUNDARIES:
- No application, tool, compose or deploy file changes.
- The builder still never pushes to main or touches protected paths.
- Trading authority and fail-closed gates are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_labels_v1.py::test_label_swap_uses_rest_not_graphql

IMPLEMENTATION MAP:
AC-001 -> .github/workflows/v6-agent.yml
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_labels_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-LABELS-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
