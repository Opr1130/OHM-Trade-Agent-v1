INCREMENT:
ATDD-OPIP-EARLY-WATCH-OBSERVABILITY-v1

OWNER-APPROVED INTENT:
Owner-authorized drafting of the scope contract for early-watch observability (work package C): per-phase resource measurement and an explicit evidence-only mode, both off by default. Drafting this contract does not approve enabling either switch or tightening live alerting.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- Requirement-to-test table in the increment's engineering document listed in the implementation map.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a measured phase
WHEN:
a sample is taken
THEN:
it reports duration, CPU, RSS and cgroup memory, and an unmeasurable value is reported unknown

AC-002:
GIVEN:
resident and cgroup measurement
WHEN:
values are read
THEN:
they are measured or absent, never invented, and cgroup paths match the documented layout

AC-003:
GIVEN:
phase measurement
WHEN:
it is off unless explicitly enabled
THEN:
a disabled phase emits nothing, an enabled phase emits exactly one line, a failing phase still emits and its error propagates, and a broken emitter cannot break the workload

AC-004:
GIVEN:
the early-watch mode switch
WHEN:
it is unset or set
THEN:
the default is today's behaviour, evidence-only is the only alternative and its reason codes stay separable

AC-005:
GIVEN:
the mode switch
WHEN:
it is consulted
THEN:
only at the early-mover boundary, never for critical safety, qualified entry or command watch

AC-006:
GIVEN:
a suppressed early mover
WHEN:
evidence is recorded
THEN:
it names the identity and the mode

EXPLICITLY OUT OF SCOPE:
- Enabling the switches in production; any change to live alerting or qualification.
- Platform-test cleanup (work package D) unless the owner confirms it belongs here.

FROZEN BOUNDARIES:
- Both switches are off by default; default behaviour is unchanged.
- No funded order path, no learning activation.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_early_watch_observability_v1.py::test_ac_001_a_sample_reports_duration_cpu_rss_and_cgroup_memory
AC-001 -> tests/test_opip_early_watch_observability_v1.py::test_ac_001_an_unmeasurable_value_is_reported_as_unknown
AC-002 -> tests/test_opip_early_watch_observability_v1.py::test_ac_002_cgroup_paths_match_the_documented_layout_or_are_absent
AC-002 -> tests/test_opip_early_watch_observability_v1.py::test_ac_002_resident_bytes_is_measured_or_absent_never_invented
AC-003 -> tests/test_opip_early_watch_observability_v1.py::test_ac_003_a_broken_emitter_cannot_break_the_workload
AC-003 -> tests/test_opip_early_watch_observability_v1.py::test_ac_003_a_disabled_phase_emits_nothing
AC-003 -> tests/test_opip_early_watch_observability_v1.py::test_ac_003_a_failing_phase_still_emits_and_the_error_propagates
AC-003 -> tests/test_opip_early_watch_observability_v1.py::test_ac_003_an_enabled_phase_emits_exactly_one_line
AC-003 -> tests/test_opip_early_watch_observability_v1.py::test_ac_003_measurement_is_off_unless_explicitly_enabled
AC-004 -> tests/test_opip_early_watch_observability_v1.py::test_ac_004_evidence_only_is_honoured_and_is_the_only_alternative
AC-004 -> tests/test_opip_early_watch_observability_v1.py::test_ac_004_the_default_mode_is_todays_behaviour
AC-004 -> tests/test_opip_early_watch_observability_v1.py::test_ac_004_the_reason_codes_stay_separable
AC-005 -> tests/test_opip_early_watch_observability_v1.py::test_ac_005_the_switch_is_only_consulted_at_the_early_mover_boundary
AC-006 -> tests/test_opip_early_watch_observability_v1.py::test_ac_006_suppression_evidence_names_the_identity_and_mode

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_early_watch_observability_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-EARLY-WATCH-OBSERVABILITY-v1.md
AC-001 -> OHM-Trade-Agent-v1/app/services/phase_resources.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_EARLY_WATCH_OBSERVABILITY_V1.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_early_watch_observability_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-EARLY-WATCH-OBSERVABILITY-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/services/phase_resources.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_early_watch_observability_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-EARLY-WATCH-OBSERVABILITY-v1.md
AC-003 -> OHM-Trade-Agent-v1/app/services/phase_resources.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_early_watch_observability_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-EARLY-WATCH-OBSERVABILITY-v1.md
AC-004 -> OHM-Trade-Agent-v1/app/services/early_watch_mode.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_early_watch_observability_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-EARLY-WATCH-OBSERVABILITY-v1.md
AC-005 -> OHM-Trade-Agent-v1/app/services/early_watch_mode.py
AC-005 -> OHM-Trade-Agent-v1/app/services/movement_discovery_v2.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_early_watch_observability_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-EARLY-WATCH-OBSERVABILITY-v1.md
AC-006 -> OHM-Trade-Agent-v1/app/services/movement_discovery_v2.py

DEFERRED DISCOVERIES:
- OPEN: the PR bundles work package D platform-test cleanup; confirm it is in scope or split it.
- Enabling measurement or evidence-only mode needs evidence and explicit owner authorization.

UNAPPROVED SCOPE CHANGES:
NONE
