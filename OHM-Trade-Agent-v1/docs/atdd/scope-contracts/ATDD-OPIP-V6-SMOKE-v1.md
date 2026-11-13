INCREMENT:
ATDD-OPIP-V6-SMOKE-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. Smoke test
of the v6 automated build loop: add one documentation file so the builder, the
pull request, the checks and the verification path are exercised end to end.
This increment changes no application code, workflow, deploy, trading, learning,
AI or notification behavior.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/v6/SMOKE_TEST.md: the document this increment adds.
- OHM-Trade-Agent-v1/CLAUDE.md: coding automation has no trading authority; smallest change.
- OHM-Trade-Agent-v1/docs/atdd/README.md: the scope contract, marker and traceability conventions this increment follows.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the repository after this slice is built
WHEN: the builder completes the slice
THEN: OHM-Trade-Agent-v1/docs/v6/SMOKE_TEST.md exists and contains the single line "v6 build loop smoke test"

AC-002:
GIVEN: the new ATDD-OPIP-V6-SMOKE-v1 increment and its scope contract
WHEN: the scope gate runs against this slice's changed files
THEN: it passes, because the contract maps every changed file to an approved criterion

EXPLICITLY OUT OF SCOPE:
- Any change to application code, tools, compose files, deploy scripts or workflows.
- Turning paper trading on, or changing Kraken, F11 or Telegram permissions.
- Any additional v6 documentation beyond the single smoke-test line.

FROZEN BOUNDARIES:
- No application, tool, compose, workflow or deploy file changes.
- Trading authority, F11 admissions and fail-closed gates are unchanged.
- Other increments' contracts are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_smoke_v1.py::test_smoke_doc_contains_the_single_line
AC-002 -> tests/test_v6_smoke_v1.py::test_smoke_scope_contract_maps_every_changed_file

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/v6/SMOKE_TEST.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_smoke_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_smoke_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SMOKE-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
