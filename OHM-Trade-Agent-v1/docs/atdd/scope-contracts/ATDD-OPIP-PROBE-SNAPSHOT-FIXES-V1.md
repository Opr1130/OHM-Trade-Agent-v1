INCREMENT:
ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1

OWNER-APPROVED INTENT:
Owner-directed correction of two measurement-tool defects in the canonical scale probe (consistent SQLite snapshots; honest index-build timing) and the production-readiness report that depends on them. Measurement tooling only: no runtime, deploy, trading, learning, Committee, Early Watch or F11 change, and no deployment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md: verification matrix and diagnostic bundle.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a canonical store whose committed rows exist only in uncheckpointed WAL frames
WHEN:
the probe snapshots it
THEN:
the snapshot contains every committed row where a raw file copy does not, and is a single self-contained verified file

AC-002:
GIVEN:
a source that is open by live writers
WHEN:
the probe snapshots it
THEN:
the source database and WAL are byte-identical afterwards, nothing is checkpointed, writers keep committing, and the copy is one consistent view

AC-003:
GIVEN:
a missing, non-canonical or corrupt source, an existing destination, or any count or integrity mismatch
WHEN:
a snapshot is requested
THEN:
it fails closed with no snapshot left behind, never falls back to a file copy, and refuses live-looking paths

AC-004:
GIVEN:
a store whose target indexes are missing, present or mixed
WHEN:
index creation is measured
THEN:
a build time is reported only for an index actually created, an all-present store is refused, --prepare drops only inside the snapshot, and the probe definitions equal production's

AC-005:
GIVEN:
a writer open on a snapshot
WHEN:
it is measured
THEN:
schema initialization, each hydration and the remaining initialization are reported as separate timings and the source is untouched

AC-006:
GIVEN:
a process that writes a non-canonical protection registry or JSONL evidence log
WHEN:
it is killed with SIGKILL or SIGTERM mid-write
THEN:
the registry file is one complete version, its advisory lock is released, and a torn JSONL tail is repaired rather than trusted

AC-007:
GIVEN:
the production-readiness diagnostic bundle
WHEN:
it is reviewed statically
THEN:
it is valid shell with no mutating command, opens the database read-only, performs no unbounded scan and copies no database

EXPLICITLY OUT OF SCOPE:
- Any change under app/, deploy/, cron or compose; any deployment or service restart.
- Any new worker, poller or scheduler.
- Any copy of a production database to another host.

FROZEN BOUNDARIES:
- The probe writes only inside a private snapshot directory it creates.
- No funded order path, no learning, Committee or Early Watch activation, F11 independent.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_probe_snapshot_v1.py::test_ac_001_a_raw_copy_loses_uncheckpointed_commits_the_snapshot_keeps_them
AC-001 -> tests/test_opip_probe_snapshot_v1.py::test_ac_001_the_snapshot_is_a_single_self_contained_file
AC-002 -> tests/test_opip_probe_snapshot_v1.py::test_ac_002_the_source_database_and_wal_are_byte_identical_afterwards
AC-002 -> tests/test_opip_probe_snapshot_v1.py::test_ac_002_a_live_writer_keeps_committing_during_and_after_the_snapshot
AC-003 -> tests/test_opip_probe_snapshot_v1.py::test_ac_003_a_count_mismatch_fails_closed_and_leaves_no_snapshot
AC-003 -> tests/test_opip_probe_snapshot_v1.py::test_ac_003_unreliable_sources_are_refused_without_a_file_copy_fallback
AC-004 -> tests/test_opip_probe_snapshot_v1.py::test_ac_004_missing_indexes_are_created_and_timed
AC-004 -> tests/test_opip_probe_snapshot_v1.py::test_ac_004_existing_indexes_never_produce_a_build_time
AC-004 -> tests/test_opip_probe_snapshot_v1.py::test_ac_004_a_partly_indexed_store_times_only_what_it_built
AC-004 -> tests/test_opip_probe_snapshot_v1.py::test_ac_004_prepare_drops_only_in_the_snapshot_never_in_the_source
AC-004 -> tests/test_opip_probe_snapshot_v1.py::test_ac_004_the_probe_index_definitions_match_production
AC-005 -> tests/test_opip_probe_snapshot_v1.py::test_ac_005_measure_reports_each_phase_separately
AC-006 -> tests/test_opip_noncanonical_interruption_v1.py::test_ac_006_a_registry_write_interrupted_by_a_real_signal_leaves_a_complete_file
AC-006 -> tests/test_opip_noncanonical_interruption_v1.py::test_ac_006_a_torn_jsonl_tail_is_repaired_not_trusted
AC-007 -> tests/test_opip_noncanonical_interruption_v1.py::test_ac_007_the_readiness_bundle_is_read_only_and_syntactically_valid

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/opip_canonical_scale_probe.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_probe_snapshot_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1.md
AC-002 -> OHM-Trade-Agent-v1/tools/opip_canonical_scale_probe.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_probe_snapshot_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1.md
AC-003 -> OHM-Trade-Agent-v1/tools/opip_canonical_scale_probe.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_probe_snapshot_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1.md
AC-004 -> OHM-Trade-Agent-v1/tools/opip_canonical_scale_probe.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_probe_snapshot_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_release_gates_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1.md
AC-005 -> OHM-Trade-Agent-v1/tools/opip_canonical_scale_probe.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_probe_snapshot_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1.md

AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_noncanonical_interruption_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1.md
AC-007 -> OHM-Trade-Agent-v1/tools/opip_readiness_bundle.sh
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_noncanonical_interruption_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_PRODUCTION_READINESS_V1.md
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1.md

DEFERRED DISCOVERIES:
- OPEN: production measurements need host access; a bounded read-only bundle is provided in the readiness document.
- Earlier release-gate timings used a store built by the pre-#516 code and closed by its writer (WAL checkpointed, indexes absent), so they remain valid; the new tests make that provable rather than assumed.

UNAPPROVED SCOPE CHANGES:
NONE
