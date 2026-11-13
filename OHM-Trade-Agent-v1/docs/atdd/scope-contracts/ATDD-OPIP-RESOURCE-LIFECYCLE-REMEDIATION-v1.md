INCREMENT:
ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1

OWNER-APPROVED INTENT:
Owner-authorized drafting of the scope contract for resource-lifecycle remediation work package A (runtime containment, termination evidence, non-critical budget, cache footprint). Drafting this contract does not approve deployment or resolve the safety questions listed under deferred discoveries.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- Requirement-to-test table in the increment's engineering document listed in the implementation map.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a cycle launched by the scheduler
WHEN:
the bound is applied and the bound tooling is present or missing
THEN:
the bound is enforced inside the container on the real process, strictly inside the host bound, and missing tooling fails closed with a marker that cannot be read as a completion

AC-002:
GIVEN:
the release controller derives the host bound
WHEN:
containment is added
THEN:
exactly one host bound is still derived and the entry stays one canonical scheduler

AC-003:
GIVEN:
a cycle receives a shutdown signal
WHEN:
it stops
THEN:
it records durable TERMINATED evidence, releases its lock and never claims SUCCESS, DEGRADED or COMPLETED_AT; a completed cycle keeps its unchanged completion pair

AC-004:
GIVEN:
workload phases wrapped in except Exception
WHEN:
a shutdown is requested
THEN:
it is never swallowed, handlers are restored afterwards and lock contention still reads as a skip

AC-006:
GIVEN:
the Kraken transport cache
WHEN:
history grows
THEN:
an entry cap bounds retention with least-recently-used eviction and expired entries are swept first

AC-007:
GIVEN:
a few large payloads
WHEN:
they are cached
THEN:
a byte cap bounds footprint and replacing a key does not double count

AC-008:
GIVEN:
the bounded cache
WHEN:
it is used
THEN:
its TTL, clear and default semantics are unchanged and the defaults are finite and overridable

AC-009:
GIVEN:
the containment changes
WHEN:
they are inspected
THEN:
they grant no trading, learning or paper authority

AC-010:
GIVEN:
an entry containing decoy comments
WHEN:
the bound is derived by the controller or diagnostics
THEN:
comments never supply or override the real bound

AC-011:
GIVEN:
a cycle with an elapsed-time budget
WHEN:
non-critical phases are about to start
THEN:
only non-critical phases are gated, a zero budget disables the gate, bad values never mean unlimited, the default expires inside the container bound and every non-critical phase is wired

AC-012:
GIVEN:
an overdue or orphaned cycle
WHEN:
the diagnostics assess it
THEN:
both conditions are reported from existing lock and log state

EXPLICITLY OUT OF SCOPE:
- Any deployment, cron or ohm-deploy activation on a host.
- Any trading, learning, paper or F11 behaviour change.
- Resolving the open safety questions below.

FROZEN BOUNDARIES:
- Protection-critical cycle phases are never gated.
- No funded order path, no learning activation.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_001_a_missing_timeout_binary_fails_closed_instead_of_running_unbounded
AC-001 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_001_the_container_bound_is_strictly_inside_the_release_bound
AC-001 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_001_the_container_side_process_is_bounded_not_only_the_launcher
AC-001 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_001_the_fail_closed_marker_cannot_be_read_as_a_completion
AC-002 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_002_release_derivation_still_resolves_exactly_one_bound
AC-002 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_002_the_entry_is_still_one_canonical_scheduler
AC-002 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_002_the_real_derivation_function_returns_the_release_bound
AC-003 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_003_a_completed_cycle_still_emits_the_completion_pair
AC-003 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_003_a_shutdown_signal_terminates_with_durable_evidence
AC-003 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_003_a_terminated_cycle_never_claims_a_completion
AC-004 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_004_a_shutdown_cannot_be_swallowed_by_a_phase_fail_open_boundary
AC-004 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_004_handlers_are_restored_after_the_cycle
AC-004 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_004_lock_contention_is_still_a_skip_not_a_completion
AC-006 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_006_eviction_is_least_recently_used_and_a_hit_refreshes_recency
AC-006 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_006_expired_entries_are_swept_before_live_ones
AC-006 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_006_the_entry_cap_bounds_the_cache_regardless_of_history
AC-007 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_007_replacing_a_key_does_not_double_count_its_footprint
AC-007 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_007_the_byte_cap_bounds_a_small_number_of_large_payloads
AC-008 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_008_cache_semantics_are_unchanged
AC-008 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_008_defaults_are_bounded_and_overridable
AC-008 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_008_ttl_and_clear_still_behave
AC-009 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_009_containment_grants_no_trading_learning_or_paper_authority
AC-010 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_010_neither_reader_matches_the_entry_verbatim
AC-010 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_010_the_controller_derives_the_real_bound_past_a_decoy_comment
AC-011 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_011_a_zero_budget_disables_the_gate_and_bad_values_never_open_it
AC-011 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_011_every_noncritical_phase_is_actually_gated_in_the_cycle_body
AC-011 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_011_the_budget_gates_only_noncritical_phases
AC-011 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_011_the_default_budget_expires_well_inside_the_container_bound
AC-012 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_012_the_assessment_reports_overdue_and_orphan_from_existing_state
AC-012 -> tests/test_opip_resource_lifecycle_remediation_v1.py::test_ac_012_the_probe_verdict_is_available_to_the_assessment

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/cron.d/ohm-unified-cycle
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-002 -> OHM-Trade-Agent-v1/deploy/cron.d/ohm-unified-cycle
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-003 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_wave1_operational_hardening.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-004 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-006 -> OHM-Trade-Agent-v1/app/services/kraken_transport.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-007 -> OHM-Trade-Agent-v1/app/services/kraken_transport.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-008 -> OHM-Trade-Agent-v1/app/services/kraken_transport.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-009 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_RESOURCE_LIFECYCLE_REMEDIATION_V1.md
AC-009 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_RESOURCE_REMEDIATION_RELEASE_READINESS.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-010 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-010 -> OHM-Trade-Agent-v1/deploy/remote/diagnose-opip-learning.sh
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_deploy_verify_budget_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-011 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md
AC-012 -> OHM-Trade-Agent-v1/deploy/remote/diagnose-opip-learning.sh
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_unified_cycle_diagnostics_v1.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_resource_lifecycle_remediation_v1.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1.md

DEFERRED DISCOVERIES:
- OPEN SAFETY (must be verified before deploy): the shutdown handler raises a BaseException that can interrupt a canonical write mid-operation; confirm writer atomicity.
- OPEN: the cycle budget skips advisory phases EVENT_INTELLIGENCE, LEARNING, TARGET_SPINE, TRADEVISOR_ADVISORY, a behaviour change needing explicit owner acceptance.
- Deployment of cron.d and ohm-deploy changes needs explicit owner authorization.

UNAPPROVED SCOPE CHANGES:
NONE
