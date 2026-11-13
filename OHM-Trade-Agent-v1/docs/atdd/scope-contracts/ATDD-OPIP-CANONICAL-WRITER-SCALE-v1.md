INCREMENT:
ATDD-OPIP-CANONICAL-WRITER-SCALE-v1

OWNER-APPROVED INTENT:
Owner-authorized drafting of the scope contract for canonical writer scale (work package B): paged hydration, bounded projections, identity indexes. Drafting this contract does not approve deployment or resolve the performance questions listed under deferred discoveries.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- Requirement-to-test table in the increment's engineering document listed in the implementation map.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a canonical writer hydrating history
WHEN:
it starts
THEN:
every hydration is a bounded paged range seek and no fold materialises a type's whole history

AC-002:
GIVEN:
history spanning several pages
WHEN:
it is folded page by page
THEN:
the result equals the single-page fold in commit order and paging terminates

AC-003:
GIVEN:
a bounded projection
WHEN:
it passes its limit
THEN:
the oldest entry is released

AC-004:
GIVEN:
a released identity
WHEN:
it is queried
THEN:
it answers exactly as the retained one would, re-derived from durable rows and never defaulted

AC-005:
GIVEN:
per-identity lookups
WHEN:
they run
THEN:
there is no resident per-result map, the identity indexes are declared and SQLite plans an index seek

AC-006:
GIVEN:
dead-letter retention
WHEN:
it bounds the file
THEN:
it reads only the tail

AC-007:
GIVEN:
history that keeps growing
WHEN:
projections are folded
THEN:
resident entries stay at the bound and a released identity is re-derived from durable rows

EXPLICITLY OUT OF SCOPE:
- Any deployment or schema migration run on production data.
- Any change to canonical event content, ordering or write authority semantics.

FROZEN BOUNDARIES:
- The canonical writer remains the single write authority; commit order and fold results are unchanged.
- Index changes are additive (IF NOT EXISTS) and rewrite no data.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_001_hydration_reads_one_page_at_a_time
AC-001 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_001_no_projection_fold_materialises_its_type_history
AC-002 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_002_paging_preserves_commit_order_and_terminates
AC-003 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_003_retention_releases_its_oldest_entry_past_the_bound
AC-004 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_004_a_released_episode_still_answers_exactly_as_the_retained_one
AC-004 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_004_a_released_request_state_is_re_derived_not_defaulted
AC-005 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_005_role_result_identity_has_no_resident_map
AC-005 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_005_the_identity_indexes_are_declared
AC-005 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_005_the_identity_query_is_planned_as_an_index_seek
AC-006 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_006_dead_letter_retention_reads_only_the_tail
AC-007 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_007_a_released_identity_is_re_derived_from_the_durable_rows
AC-007 -> tests/test_opip_canonical_writer_scale_v1.py::test_ac_007_retained_entries_stay_bounded_while_history_grows

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_scale_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CANONICAL-WRITER-SCALE-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_CANONICAL_WRITER_SCALE_V1.md
AC-002 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_scale_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CANONICAL-WRITER-SCALE-v1.md
AC-003 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_scale_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CANONICAL-WRITER-SCALE-v1.md
AC-004 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_scale_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CANONICAL-WRITER-SCALE-v1.md
AC-005 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_scale_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CANONICAL-WRITER-SCALE-v1.md
AC-005 -> OHM-Trade-Agent-v1/app/opip/canonical/schema.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_scale_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CANONICAL-WRITER-SCALE-v1.md
AC-006 -> OHM-Trade-Agent-v1/app/opip/events/storage.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_scale_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CANONICAL-WRITER-SCALE-v1.md

DEFERRED DISCOVERIES:
- OPEN (before deploy): measure startup memory and index build time and write-lock duration on a copy of production-size history.
- OPEN: confirm FIFO (not LRU) eviction is acceptable for hot identities.
- Deployment needs explicit owner authorization.

UNAPPROVED SCOPE CHANGES:
NONE
