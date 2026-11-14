INCREMENT:
ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1

OWNER-APPROVED INTENT:
Repair three misleading sections of the read-only production readiness bundle that the first post-deployment run exposed. Diagnostic tooling only: no runtime, deploy-script, compose, trading, learning, Committee or F11 change, and no redeploy.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md: diagnostic bundle.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a core container image that has no ps command
WHEN:
section 7 counts run_cycle processes
THEN:
the count comes from /proc, a real run_cycle process is counted, no process is reported as 0, and a failed or missing container is reported UNAVAILABLE and never as 0

AC-002:
GIVEN:
an installed deploy controller that either defines WRITER_HEALTH_TIMEOUT_SECONDS or still uses a fixed seq poll loop
WHEN:
section 8 reports the health deadline
THEN:
the installed controller, which is what a deploy executes, is reported with verdict NEW and its timeout, or LEGACY, or UNVERIFIED, and an unreadable file is reported UNAVAILABLE rather than silently omitted

AC-003:
GIVEN:
a core container whose environment pins some of the release flags and omits others
WHEN:
section 9 reports runtime configuration
THEN:
each named variable prints its value or an explicit UNSET line stating that unset is not evidence of OFF, a disabled feature shows its off value, secrets are redacted, and a missing container is UNAVAILABLE

EXPLICITLY OUT OF SCOPE:
- Any change under app/, deploy/, cron or compose; any deployment or service restart.
- Sections 1 to 6 of the bundle.

FROZEN BOUNDARIES:
- The bundle writes nothing, locks nothing, signals nothing and invokes no other repository script.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_readiness_bundle_diagnostics_v1.py::test_ac_001_a_real_run_cycle_process_is_counted_from_proc
AC-001 -> tests/test_opip_readiness_bundle_diagnostics_v1.py::test_ac_001_no_process_is_zero_and_a_failed_exec_is_unavailable_not_zero
AC-002 -> tests/test_opip_readiness_bundle_diagnostics_v1.py::test_ac_002_new_legacy_and_unreadable_controllers_are_told_apart
AC-003 -> tests/test_opip_readiness_bundle_diagnostics_v1.py::test_ac_003_unset_is_distinguished_from_disabled_and_secrets_are_redacted

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/opip_readiness_bundle.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_readiness_bundle_diagnostics_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1.md
AC-002 -> OHM-Trade-Agent-v1/tools/opip_readiness_bundle.sh
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_readiness_bundle_diagnostics_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1.md
AC-003 -> OHM-Trade-Agent-v1/tools/opip_readiness_bundle.sh
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_readiness_bundle_diagnostics_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-BUNDLE-DIAGNOSTICS-V1.md

DEFERRED DISCOVERIES:
- NOTED: the section 6 status-count window (last 1000 log lines) is shorter than the listed outcomes, so the two can disagree; not changed here.

UNAPPROVED SCOPE CHANGES:
NONE
