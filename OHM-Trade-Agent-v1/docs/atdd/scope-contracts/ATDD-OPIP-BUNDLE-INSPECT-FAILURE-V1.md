INCREMENT:
ATDD-OPIP-BUNDLE-INSPECT-FAILURE-V1

OWNER-APPROVED INTENT:
Resolve the remaining P2 on merged PR #580: section 9 of the read-only readiness bundle printed every flag as UNSET when `docker inspect` itself failed, so an unavailable observation looked like an absent variable. Diagnostic tooling only: no runtime, deploy-script, compose, trading, learning, Committee or F11 change, and no redeploy.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md: diagnostic bundle.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a core container whose `docker inspect` fails, and separately one whose inspection succeeds with some flags absent
WHEN:
section 9 reports runtime configuration
THEN:
the failed inspection prints UNAVAILABLE and no flag line (never <UNSET>, never a value), and a successful inspection still prints values, explicit UNSET lines and redactions exactly as before

EXPLICITLY OUT OF SCOPE:
- Any change under app/, deploy/, cron or compose; any deployment or service restart.
- Sections 1 to 8 of the bundle.

FROZEN BOUNDARIES:
- The bundle writes nothing, locks nothing, signals nothing and invokes no other repository script.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_readiness_bundle_diagnostics_v1.py::test_ac_004_a_failed_inspect_is_unavailable_never_unset_or_off

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/opip_readiness_bundle.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_readiness_bundle_diagnostics_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-BUNDLE-INSPECT-FAILURE-V1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
