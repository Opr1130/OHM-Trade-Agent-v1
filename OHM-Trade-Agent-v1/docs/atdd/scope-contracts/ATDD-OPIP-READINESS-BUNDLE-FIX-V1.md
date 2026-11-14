INCREMENT:
ATDD-OPIP-READINESS-BUNDLE-FIX-V1

OWNER-APPROVED INTENT:
Correct two confirmed defects in the merged production-readiness diagnostic bundle (#573) found in independent post-merge review: it invoked a script whose callees can mutate a malformed registry, and it reported a current-epoch counter as a total event count. Diagnostic tooling and its documentation only. No runtime, deploy, trading, learning, Committee, Early Watch or F11 change, and no deployment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md: verification matrix and diagnostic bundle.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the readiness bundle run against a sandbox that holds a booby-trapped diagnostic script, a corrupt registry and a cycle lock file (or none)
WHEN:
the bundle runs to completion
THEN:
it invokes no other repository script, creates, opens, locks or probes no lock file, and no byte, file name or modification time changes anywhere in the sandbox

AC-002:
GIVEN:
a canonical store that has been restored so that history_epoch is greater than 1 and next_local_sequence has restarted at 1 while earlier events remain
WHEN:
the bundle reports the event evidence
THEN:
it labels next_local_sequence as a current-epoch position and never a total, reports an upper bound that is the sum over every epoch, and reports UNVERIFIED when the store cannot be read or the bound cannot be proven inside its time budget

EXPLICITLY OUT OF SCOPE:
- Any change under app/, deploy/, cron or compose; any deployment or service restart.
- Any other advisory file writer, new worker, poller or scheduler.
- Any change to the snapshot or index-timing fixes merged in #573.

FROZEN BOUNDARIES:
- The bundle writes nothing, locks nothing and signals nothing.
- No funded order path, no learning, Committee or Early Watch activation, F11 independent.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_readiness_bundle_v1.py::test_ac_001_the_bundle_invokes_no_other_repository_script
AC-001 -> tests/test_opip_readiness_bundle_v1.py::test_ac_001_a_booby_trapped_callee_and_corrupt_registry_are_untouched
AC-001 -> tests/test_opip_readiness_bundle_v1.py::test_ac_001_the_cycle_lock_is_reported_without_being_opened_or_created
AC-002 -> tests/test_opip_readiness_bundle_v1.py::test_ac_002_a_restored_store_is_not_reported_as_a_total_and_gets_a_true_upper_bound
AC-002 -> tests/test_opip_readiness_bundle_v1.py::test_ac_002_an_unreadable_store_is_unverified_never_zero
AC-002 -> tests/test_opip_readiness_bundle_v1.py::test_ac_002_a_supported_restore_keeps_old_events_and_resets_the_sequence

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/opip_readiness_bundle.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_readiness_bundle_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-READINESS-BUNDLE-FIX-V1.md
AC-002 -> OHM-Trade-Agent-v1/tools/opip_readiness_bundle.sh
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_readiness_bundle_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-READINESS-BUNDLE-FIX-V1.md

DEFERRED DISCOVERIES:
- OPEN: dozens of other advisory file writers are not individually interruption-tested (readiness document R-3); deliberately not expanded here.

UNAPPROVED SCOPE CHANGES:
NONE
