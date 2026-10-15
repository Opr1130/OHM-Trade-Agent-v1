INCREMENT:
ATDD-R5-M06-recovery-rehearsal

OWNER-APPROVED INTENT:
This is the isolated recovery rehearsal Mission 06 asks for as P0-1: a fixture-based backup, restore and rollback rehearsal that proves the recovery path actually works, rather than proving that scripts exist.

Where it runs. Entirely inside pytest `tmp_path`. It performs no production write, no service change, no mode change, no destructive cleanup and touches no repository state. It does not start a container, contact an endpoint, hold a credential or change a release profile.

Why it drives the real primitives. Backup and restore correctness lives in the durability layer, not in a rehearsal harness, so the tests call the actual `app.opip.storage.bounded_jsonl` functions — `write_atomic_lines` (temp file, fsync, `os.replace`, directory fsync), `sha256_file`, `read_lines` and `repair_truncated_tail` (quarantine then truncate) — rather than re-implementing them. Re-implementing them would prove only that the rehearsal is self-consistent.

The paper book is a named fixture modelling the row shapes a paper book has: cash, positions, fills and an outstanding obligation. The fixture is explicitly a model; nothing here claims to be the production Paper-v2 ledger file, and no claim is made about that file's on-disk format.

KEY LAW - RECOVERY MUST PRESERVE EVIDENCE, NOT TIDY IT. An interrupted append is repaired by quarantining the unterminated fragment byte-for-byte and truncating to the last complete row boundary. The rehearsal asserts the fragment survives intact, because a recovery that silently discards the interrupted bytes destroys exactly the evidence an operator needs.

KEY LAW - RECOVERY MUST BE IDEMPOTENT. A second restore from the same verified snapshot must change nothing: same digest, no second quarantine file, no duplicated fill and no double-counted obligation.

KEY LAW - A SNAPSHOT IS IMMUTABLE DURING RESTORE. The rehearsal asserts the snapshot's digest is unchanged by a restore that reads it, so restoring cannot corrupt the thing being restored from.

KEY LAW - RECOVERY CANNOT DEPEND ON THE AI PLANE. The storage module is asserted to import no committee, AI, network or notification surface, so a protective recovery can always complete while those components are unavailable.

ARCHITECTURE REFERENCES:
- app/opip/storage/bounded_jsonl.py: the durability primitives under test, including the quarantine-then-truncate recovery of an interrupted append.
- docs/architecture/OPIP_RETIREMENT_LEDGER.md: the archive-checksum and consumer-sign-off prerequisites a real deletion would require; this increment satisfies the checksum half for the modelled book and authorizes no deletion.
- docs/architecture/OPIP_OWNER_DECISIONS_REGISTER.md: records that no numeric policy is ratified here either.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-430:
GIVEN:
a paper book written through the atomic writer, then snapshotted
WHEN:
both files are checksummed and the snapshot is restored
THEN:
the snapshot digest equals the live digest, the snapshot verifies before restore, the restored row count equals the written row count, and the snapshot round-trips byte-identically

AC-431:
GIVEN:
a book carrying cash, positions, fills and an outstanding obligation
WHEN:
it is snapshotted and restored
THEN:
cash, positions, fills and open obligations all recover exactly, and the reconciled notional computed from fills is identical before and after restoration

AC-432:
GIVEN:
a book whose final append was cut mid-row, leaving an unterminated fragment
WHEN:
the tail is repaired
THEN:
every complete row survives with the prefix digest unchanged, the file ends at a row boundary, the fragment is preserved byte-for-byte in exactly one quarantine file rather than discarded, and the repaired book reconciles to the same economic state

AC-433:
GIVEN:
a book restored once from a verified snapshot
WHEN:
the restore and the tail repair are each performed again
THEN:
the digest is unchanged, no second quarantine file appears, the row set is unchanged, no fill is duplicated and no obligation is double-counted

AC-434:
GIVEN:
a verified snapshot, a restored live file and the storage module that implements recovery
WHEN:
the post-restore state and the module's imports are inspected
THEN:
the snapshot digest is unchanged, no temporary file survives the atomic replacement, every line of the live file parses as a complete row, and the module imports no committee, AI, network or notification surface

EXPLICITLY OUT OF SCOPE:
- Any production write, service restart, container change, deployment or mode change.
- Any deletion, archive compaction, drain or retirement, and any destructive cleanup.
- Any change to an application module, contract, storage format, configuration key or release profile.
- Any claim about the real Paper-v2 ledger file's on-disk format or its live contents.
- Any activation of `TARGET_PAPER`, Paper v2 or the Committee.
- Any numeric threshold, budget, retention window or checksum policy ratification.

FROZEN BOUNDARIES:
- `TARGET_PAPER` remains `BLOCKED`; the release posture remains `EVIDENCE_SHADOW`; Paper v2 and Committee modes remain off.
- No legacy component is retired, no writer is stopped and no archive is deleted.
- The single canonical writer remains the only authority that commits operational domain events, and this increment writes none.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.
- The Committee remains advisory and shadow-only.

ACCEPTANCE TEST TRACEABILITY:
AC-430 -> tests/test_opip_r5_m06_recovery_rehearsal.py::test_ac_430_the_snapshot_is_complete_and_checksum_verified
AC-431 -> tests/test_opip_r5_m06_recovery_rehearsal.py::test_ac_431_the_book_recovers_intact_and_reconciles_before_and_after
AC-432 -> tests/test_opip_r5_m06_recovery_rehearsal.py::test_ac_432_an_interrupted_append_is_recovered_and_quarantined
AC-433 -> tests/test_opip_r5_m06_recovery_rehearsal.py::test_ac_433_restoring_twice_is_idempotent_and_duplicates_nothing
AC-434 -> tests/test_opip_r5_m06_recovery_rehearsal.py::test_ac_434_restore_preserves_the_snapshot_and_leaves_no_partial_file
AC-434 -> tests/test_opip_r5_m06_recovery_rehearsal.py::test_ac_434_the_recovery_path_has_no_ai_or_notification_dependency

IMPLEMENTATION MAP:
AC-430 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M06-recovery-rehearsal.md
AC-430 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_recovery_rehearsal.py
AC-431 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_recovery_rehearsal.py
AC-432 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_recovery_rehearsal.py
AC-433 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_recovery_rehearsal.py
AC-434 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m06_recovery_rehearsal.py
AC-430 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-430 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The rehearsal proves the recovery *primitives* against a modelled book. It does not prove recovery of the real Paper-v2 ledger file, because that file's live contents cannot be read safely from this worktree; that remains an observation-only item for the runtime authority inventory.
- `repair_truncated_tail` both quarantines and truncates, so an operator must treat the quarantine file as required evidence rather than as debris. A retention policy for quarantine files would be an owner decision, not an engineering default.
- Rollback configuration was inspected read-only and not exercised, because exercising it would require a mode change, which the mission forbids.
- No headless browser harness exists in this repository, and no rendered-surface claim is made.

UNAPPROVED SCOPE CHANGES:
NONE

