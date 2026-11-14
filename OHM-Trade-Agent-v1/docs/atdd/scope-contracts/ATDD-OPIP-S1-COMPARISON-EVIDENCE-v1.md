INCREMENT:
ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1

OWNER-APPROVED INTENT:
Protect the V1/V2 scoring-comparison evidence from silent loss before the
PostgreSQL migration exists, and record the Chief output cap per AI call so
truncations can be diagnosed from telemetry. Rotation of the comparison ledger
must never overwrite an existing file, must be safe with concurrent writers and
crashes, and any dropped record must be counted. This is a DRAFT for owner
review: not merged, not deployed, no behavior change to scoring, alerts,
reconciliation, F11, trading or learning.

ARCHITECTURE REFERENCES:
- app/services/technical_scoring_comparison.py: lock-protected, durable,
  no-overwrite append and rotation (reuses app/services/registry_io.registry_lock).
- app/services/opip_ai_router.py: additive max_output_tokens field in the
  existing router usage row.
- OPIP S1/S4 correction package: P0-B rotation design rev 2 and AI telemetry item.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the comparison ledger rotates more than once, or a legacy .1.jsonl exists
WHEN: a record is appended and the size limit is exceeded
THEN: the hot file moves to a unique archive name, no existing file is ever
overwritten or altered, and every record remains readable

AC-002:
GIVEN: several writer processes append concurrently, or a rotation or write
was interrupted by a crash
WHEN: the ledger rotates and appends continue
THEN: each record is present exactly once, a half-finished rotation is
completed on the next append, and a torn partial line is preserved on its own line

AC-003:
GIVEN: a write is short or interrupted, the lock is held, or the disk reports an error
WHEN: a comparison record is written
THEN: a full record is written or the failure is counted, bounded by the lock
timeout, logged, and never raised into the scanner

AC-004:
GIVEN: the Chief router records usage for a successful or failed provider call
WHEN: the usage row is appended
THEN: the row carries the output cap in force (or null) and every existing
field is unchanged

EXPLICITLY OUT OF SCOPE:
- Any change to scoring, selection, thresholds, alerts, Telegram, reconciliation, F11, trading or learning.
- The ENTER_NOW reconciliation gate and any H2 design.
- Chief configuration values, retries, fail-open behavior, provider or model changes.
- PostgreSQL importer, labeler, lineage fields, retrospective plan synthesis.
- Deployment, merge, migration or deletion of any existing file.

FROZEN BOUNDARIES:
- The comparison record content and the 256 KiB record limit are unchanged.
- The dashboard read path (read_recent_comparisons) is unchanged.
- Chief fail-closed behavior and budget gates are unchanged.
- No new service, scheduler, database or dependency.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_s1_comparison_rotation_v1.py::test_rotation_keeps_every_archive_and_never_overwrites
AC-001 -> tests/test_opip_s1_comparison_rotation_v1.py::test_legacy_dot_one_file_is_never_touched
AC-001 -> tests/test_opip_s1_comparison_rotation_v1.py::test_name_collision_gets_a_new_suffix_and_never_replaces
AC-001 -> tests/test_opip_s1_comparison_rotation_v1.py::test_link_failure_never_renames_over_an_intruder_and_keeps_every_record
AC-002 -> tests/test_opip_s1_comparison_rotation_v1.py::test_concurrent_writers_lose_no_record_across_rotations
AC-002 -> tests/test_opip_s1_comparison_rotation_v1.py::test_crash_between_link_and_unlink_recovers_without_loss
AC-002 -> tests/test_opip_s1_comparison_rotation_v1.py::test_torn_tail_is_preserved_on_its_own_line
AC-003 -> tests/test_opip_s1_comparison_rotation_v1.py::test_short_writes_and_eintr_still_write_the_whole_record
AC-003 -> tests/test_opip_s1_comparison_rotation_v1.py::test_write_failure_is_counted_and_never_raises
AC-003 -> tests/test_opip_s1_comparison_rotation_v1.py::test_lock_timeout_is_bounded_and_counted
AC-003 -> tests/test_opip_s1_comparison_rotation_v1.py::test_archive_stats_report_count_and_bytes
AC-004 -> tests/test_opip_s1_router_cap_telemetry_v1.py::test_router_usage_record_carries_the_output_cap

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/technical_scoring_comparison.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_s1_comparison_rotation_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/services/technical_scoring_comparison.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_s1_comparison_rotation_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/technical_scoring_comparison.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_s1_comparison_rotation_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_s1_router_cap_telemetry_v1.py

DEFERRED DISCOVERIES:
- Whether more than one production process writes the ledger is UNVERIFIED; the lock makes this irrelevant.
- The lock works across containers only on a shared local bind mount; verify before deploy.
- The analytics ingest reads only the hot file and must read archives (separate PostgreSQL-first work).
- Preserve a copy of the current hot file and legacy .1.jsonl with a sha256 manifest before deploy (owner step).
- A persistent write failure is counted and logged only; whether it should trigger the admission pause is an owner decision.

UNAPPROVED SCOPE CHANGES:
NONE
