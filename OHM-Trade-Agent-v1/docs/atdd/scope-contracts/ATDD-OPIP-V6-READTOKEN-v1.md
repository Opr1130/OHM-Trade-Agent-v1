INCREMENT:
ATDD-OPIP-V6-READTOKEN-v1

OWNER-APPROVED INTENT:
The advance job logged queued=0 because the GitHub App installation token could
not list issues. Read issues and pull requests with the workflow token and keep
the App token only for adding the build label, which must trigger the build
workflow. No application, deploy, trading or notification behavior changes.

ARCHITECTURE REFERENCES:
- .github/workflows/v6-agent.yml: the advance job.
- tools/v6_agent/advance.py

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the advance job runs
WHEN: it lists queued issues, closed issues and open pull requests
THEN: those reads use the workflow token passed as READ_TOKEN, while the label add keeps the App token

EXPLICITLY OUT OF SCOPE:
- Any other workflow, deploy file, application code or builder logic.

FROZEN BOUNDARIES:
- No application, compose or deploy file changes.
- Trading authority and fail-closed gates are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_readtoken_v1.py::test_advance_reads_with_workflow_token

IMPLEMENTATION MAP:
AC-001 -> .github/workflows/v6-agent.yml
AC-001 -> OHM-Trade-Agent-v1/tools/v6_agent/advance.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_readtoken_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-READTOKEN-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
