INCREMENT:
ATDD-OPIP-V6-SPEC-v1

OWNER-APPROVED INTENT:
Record the v6 spec and the first slice queue so the automated build loop has a
single reference. Documentation only; no application, deploy, trading or
notification behavior changes.

ARCHITECTURE REFERENCES:
- docs/V6_AUDIT.md

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the v6 audit is merged
WHEN: the v6 spec is added
THEN: docs/V6_SPEC.md exists and states the operating model, owner decisions, slice rules, first slices and safety boundary

EXPLICITLY OUT OF SCOPE:
- Any code, workflow, deploy or configuration change.

FROZEN BOUNDARIES:
- No application, tool, compose or deploy file changes.
- Trading authority and fail-closed gates are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_spec_v1.py::test_spec_has_required_sections

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/V6_SPEC.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_spec_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SPEC-v1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
