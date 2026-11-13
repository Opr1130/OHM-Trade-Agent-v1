INCREMENT:
ATDD-OPIP-V6-KEY-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. The v6
build loop uses its own DeepSeek credential, V6_DEEPSEEK_API_KEY, so v6 spend is
isolated and revocable without affecting other workflows. No application code,
deploy workflow, trading, learning or notification behavior changes.

ARCHITECTURE REFERENCES:
- .github/workflows/v6-agent.yml: passes the credential to the builder action only.
- .github/actions/v6-run/action.yml: gives the credential to builder steps only, never to verification.

APPROVED ACCEPTANCE CRITERIA:

AC-901:
GIVEN: the v6-agent workflow
WHEN: it supplies the builder credential
THEN: it reads only the V6_DEEPSEEK_API_KEY secret and never the shared DEEPSEEK_API_KEY secret

EXPLICITLY OUT OF SCOPE:
- Creating or rotating the secret itself (owner action in repository settings).
- Changing deepseek-quant-review.yml or any other workflow.

FROZEN BOUNDARIES:
- No application, tool, compose or deploy file changes.
- Verification steps still receive no AI credential.
- Trading authority and fail-closed gates are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-901 -> tests/test_v6_key_split_v1.py::test_v6_agent_uses_only_the_v6_key

IMPLEMENTATION MAP:
AC-901 -> .github/workflows/v6-agent.yml
AC-901 -> OHM-Trade-Agent-v1/tests/test_v6_key_split_v1.py
AC-901 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-901 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-KEY-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
