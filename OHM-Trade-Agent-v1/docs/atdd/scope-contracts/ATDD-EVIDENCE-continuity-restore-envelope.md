INCREMENT:
ATDD-EVIDENCE-continuity-restore-envelope

OWNER-APPROVED INTENT:
Bound production continuity restoration to the requested instrument batch and restored horizon so EVIDENCE_SHADOW setup finishes inside the existing Feature Bus envelope.

ARCHITECTURE REFERENCES:
- AGENTS.md release profile: EVIDENCE_SHADOW stays shadow for the feature bus, canonical writer, and target spine. Paper-v2, committee, and funded authority stay off.
- Feature Bus capture budget remains 45 seconds, with a 50 second ceiling, a 15 second acquisition wave, and a 10 second materialization reserve.
- The canonical writer remains the only domain write path. Continuity restore stays read-only.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
canonical history contains a large unrelated event family and an eight-instrument batch
WHEN:
batch continuity restoration reads checkpoints and revision ledgers
THEN:
rows returned stay fixed for the requested instruments and do not grow with unrelated history

AC-002:
GIVEN:
the same committed checkpoints and in-horizon observations
WHEN:
the bounded batch loader and the single-instrument loader restore one requested instrument
THEN:
rolling state and in-horizon ledger entries match

AC-003:
GIVEN:
a restored checkpoint whose tip is behind the current time
WHEN:
batch continuity builds the source watermark
THEN:
the watermark is that tip plus one interval

AC-004:
GIVEN:
the setup deadline is already exhausted
WHEN:
continuity restoration is attempted
THEN:
restoration fails closed and acquisition does not run

AC-005:
GIVEN:
eight requested instruments
WHEN:
batch continuity reads checkpoints and observations
THEN:
each event family is queried once

AC-006:
GIVEN:
the additive instrument order index and the Feature Bus budget constants
WHEN:
a bounded observation read is planned
THEN:
the plan uses that index and the 45, 50, 15, and 10 second budgets are unchanged

EXPLICITLY OUT OF SCOPE:
- Deploying this change or merging it
- TARGET_PAPER, Paper-v2, committee, or funded authority
- Raising the Feature Bus budget, materialization reserve, or acquisition wave
- Weakening setup-deadline fail-closed behavior or the runtime verifier
- A new store or scheduler

FROZEN BOUNDARIES:
- One canonical writer. Continuity restore does not write.
- No partial continuity is returned when the deadline expires.
- No fabricated snapshot or feasibility evidence.
- The single-instrument loader remains the historical full-family seam.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_continuity_restore_envelope.py::test_unrelated_history_does_not_increase_batch_rows
AC-002 -> tests/test_opip_continuity_restore_envelope.py::test_requested_continuity_matches_single_instrument_loader
AC-003 -> tests/test_opip_continuity_restore_envelope.py::test_restored_watermark_stays_at_the_checkpoint_tip
AC-004 -> tests/test_opip_continuity_restore_envelope.py::test_deadline_expiry_fails_closed_before_acquisition
AC-005 -> tests/test_opip_continuity_restore_envelope.py::test_batch_restore_issues_one_query_per_family
AC-006 -> tests/test_opip_continuity_restore_envelope.py::test_batch_queries_use_the_instrument_index_and_budgets_stay_fixed

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/canonical/schema.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/features/checkpoint_store.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/features/revision_ledger.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_continuity_restore_envelope.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/features/checkpoint_store.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/features/revision_ledger.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_continuity_batch.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_continuity_restore_envelope.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/run_feature_bus_pilot.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_continuity_restore_envelope.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/run_feature_bus_pilot.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_continuity_restore_envelope.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/features/checkpoint_store.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/features/revision_ledger.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_continuity_restore_envelope.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/canonical/schema.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_evidence_reader.py
AC-006 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-EVIDENCE-continuity-restore-envelope.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_continuity_restore_envelope.py

DEFERRED DISCOVERIES:
- The first writer schema open builds the new index. That one-time build is outside the read-only restore, and a database that has not been opened by the writer yet still filters in SQL but cannot seek.

UNAPPROVED SCOPE CHANGES:
NONE
