INCREMENT:
ATDD-OPIP-RELEASE-GATES-V1

OWNER-APPROVED INTENT:
Owner-directed closure of the three pre-deployment release gates for the merged resource-remediation work (#515, #516, #517): interruption safety evidence, canonical-writer scale measurement and the defect it exposed, and default-off verification. This contract does not approve deployment, activation of Early Watch changes, learning, Committee or funded trading, and does not touch F11.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/docs/engineering/OPIP_RELEASE_GATES_EVIDENCE_V1.md: requirement-to-test table and measurements.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a canonical writer process that is killed with SIGKILL while committing in a loop
WHEN:
its store is reopened
THEN:
integrity holds, events and idempotency keys agree, the sequence has no gap, every acknowledged event is durable, and a new writer can take ownership

AC-002:
GIVEN:
a process running the orderly-shutdown handler that receives SIGTERM mid write loop
WHEN:
it unwinds
THEN:
it exits in order with TERMINATED evidence and no completion marker, the cycle log keeps its prior lines, and the store is consistent and re-openable

AC-003:
GIVEN:
an interruption that is not an Exception raised between BEGIN and COMMIT
WHEN:
submit unwinds
THEN:
the transaction is rolled back, nothing half-written is visible or acknowledged, and a resubmission commits as a first write; and no cycle code owns a writing canonical connection

AC-004:
GIVEN:
a history far larger than the projection retention bound
WHEN:
the writer hydrates
THEN:
the event type is read once rather than once per released episode, and each released episode is re-derived by an index seek, so startup is not quadratic in history

AC-005:
GIVEN:
a bounded projection under pressure
WHEN:
an entry is refreshed or released
THEN:
retention is FIFO by first insertion, a refresh does not extend an entry's life, and a released episode re-derives to the identical state so the policy is a latency choice only

AC-006:
GIVEN:
the merged observability and Early Watch controls with nothing configured
WHEN:
the runtime starts
THEN:
per-phase telemetry is off and unwired, Early Watch alerting is unchanged, only the explicit evidence_only value withholds and only for EARLY_MOVER, and no deploy asset enables either control

AC-007:
GIVEN:
the lock portability cleanup bundled in the Early Watch work
WHEN:
a lock file is group-writable or hard-linked on POSIX
THEN:
it is still refused, so the cleanup relaxes nothing on the Linux runtime

AC-008:
GIVEN:
the canonical scale probe
WHEN:
it is pointed at a live-looking path or asked to measure
THEN:
it refuses the live path and measures a copy, leaving the original untouched

EXPLICITLY OUT OF SCOPE:
- Any deployment, cron or ohm-deploy activation on a host.
- Enabling OPIP_EARLY_WATCH_MODE or OPIP_PHASE_RESOURCE_TELEMETRY, wiring phase telemetry, or any trading, learning, Committee or F11 behaviour change.
- Changing the retention policy from FIFO.

FROZEN BOUNDARIES:
- Protection-critical cycle phases are never gated.
- No funded order path, no learning activation, F11 independent.
- The only runtime changes are one additive writer-created index, one indexed re-derivation read, and one rollback guard.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_release_gates_v1.py::test_ac_001_sigkill_mid_write_never_loses_an_acknowledged_event
AC-002 -> tests/test_opip_release_gates_v1.py::test_ac_002_sigterm_through_the_real_handler_stops_in_order
AC-002 -> tests/test_opip_release_gates_v1.py::test_ac_002_a_real_signal_mid_cycle_keeps_prior_log_evidence
AC-003 -> tests/test_opip_release_gates_v1.py::test_ac_003_an_interruption_mid_transaction_is_rolled_back_not_acknowledged
AC-003 -> tests/test_opip_release_gates_v1.py::test_ac_003_the_cycle_never_constructs_a_writing_canonical_writer
AC-004 -> tests/test_opip_release_gates_v1.py::test_ac_004_hydration_does_not_rescan_the_event_type_per_released_episode
AC-004 -> tests/test_opip_release_gates_v1.py::test_ac_004_the_episode_lookup_is_planned_as_an_index_seek
AC-004 -> tests/test_opip_release_gates_v1.py::test_ac_004_the_index_is_additive_partial_and_outside_the_generic_schema
AC-005 -> tests/test_opip_release_gates_v1.py::test_ac_005_retention_is_first_in_first_out_and_a_refresh_does_not_reorder
AC-005 -> tests/test_opip_release_gates_v1.py::test_ac_005_a_release_changes_latency_never_the_answer
AC-006 -> tests/test_opip_release_gates_v1.py::test_ac_006_telemetry_is_off_by_default_and_unwired
AC-006 -> tests/test_opip_release_gates_v1.py::test_ac_006_early_watch_defaults_to_the_unchanged_active_behaviour
AC-006 -> tests/test_opip_release_gates_v1.py::test_ac_006_no_deploy_asset_enables_either_new_flag
AC-007 -> tests/test_opip_release_gates_v1.py::test_ac_007_the_posix_lock_invariants_are_still_enforced
AC-008 -> tests/test_opip_release_gates_v1.py::test_ac_008_the_scale_probe_refuses_live_looking_paths_and_copies_for_measuring

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md
AC-003 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md
AC-004 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md
AC-008 -> OHM-Trade-Agent-v1/tools/opip_canonical_scale_probe.py
AC-008 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_RELEASE_GATES_EVIDENCE_V1.md
AC-008 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_RESOURCE_REMEDIATION_RELEASE_READINESS.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELEASE-GATES-V1.md

DEFERRED DISCOVERIES:
- OPEN (before deploy): production row counts and a production-size copy measurement; the probe is provided but the production store was not reachable from here.
- OPEN: phase telemetry is unwired, so enabling OPIP_PHASE_RESOURCE_TELEMETRY alone measures nothing; wiring it is a separate runtime change.
- Deployment needs explicit owner authorization.

UNAPPROVED SCOPE CHANGES:
NONE
