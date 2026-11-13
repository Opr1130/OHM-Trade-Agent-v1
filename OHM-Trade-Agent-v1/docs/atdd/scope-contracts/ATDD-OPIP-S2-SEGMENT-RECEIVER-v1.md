INCREMENT:
ATDD-OPIP-S2-SEGMENT-RECEIVER-v1

OWNER-APPROVED INTENT:
On 12 November 2026 the owner approved building the S2 gzip-only receiver against the Phase 4 contract: focused ATDD tests, crash-recovery verification, no production activation. The receiver stores verified sealed gzip segments durably on the learning worker and acknowledges them only after they are durable, so off-host evidence preservation is reliable before engineering returns to MARGIN_ELIGIBILITY and measurable trade outcomes. Disk gates are in bytes: 3 GiB normal reserve pauses ingestion and a 2 GiB hard floor fails closed. This change requires review; deployment, timers, the baseline shipment and any shipper are separate approvals.

ARCHITECTURE REFERENCES:
- app/opip/storage/segment_receiver.py: library plus a one-shot, unscheduled command-line pass.
- app/opip/storage/bounded_jsonl.py: reused sha256, fsync and strict JSON-row helpers; its manifest and archives are unchanged.
- docs: S2 contract v2 sections 4a, 4b and the Phase 4 authorization package, section 4.
- tests/test_opip_s2_segment_receiver_v1.py: hermetic evidence.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: a verified segment
WHEN: the receiver accepts it
THEN: it is stored once by its sha256 with its manifest and acknowledged

AC-002:
GIVEN: a segment being accepted
WHEN: the receiver writes it
THEN: the segment and manifest are renamed into place and fsynced before the acknowledgement is written

AC-003:
GIVEN: a segment already acknowledged
WHEN: it is delivered again
THEN: the repeat is a no-op that stores nothing new (at-least-once delivery, exactly-once logical acceptance)

AC-004:
GIVEN: a segment whose size, sha256, row_count, JSON rows or manifest do not verify
WHEN: the receiver accepts it
THEN: it is rejected, nothing is stored and no acknowledgement is written; a same-size corruption is caught by the sha256 alone

AC-005:
GIVEN: a segment containing a credential-like row
WHEN: the receiver accepts it
THEN: it fails closed: rejected, nothing stored, no acknowledgement

AC-006:
GIVEN: free space measured in bytes
WHEN: projected free space after the write is below 3 GiB, below 2 GiB, or falls below 2 GiB mid-copy
THEN: ingestion pauses (PAUSED_RESERVE), fails closed (CRITICAL) or aborts, with a status record, before or without leaving any stored segment, temp file or acknowledgement; nothing is auto-deleted

AC-007:
GIVEN: a crash after a segment is stored but before its acknowledgement
WHEN: the receiver recovers
THEN: temp files are discarded and the verified segment is acknowledged exactly once; a stored segment that fails verification is left in place, reported and never acknowledged

AC-008:
GIVEN: delivered source files, accepted or rejected
WHEN: the receiver finishes
THEN: they are byte-identical and still present

AC-009:
GIVEN: the receiver module
WHEN: its imports and source are inspected
THEN: it uses no PostgreSQL, SQLite, socket, HTTP or subprocess module and contains no recursive or generic deletion

AC-010:
GIVEN: a large or hostile segment
WHEN: the receiver verifies and stores it
THEN: peak memory stays far below the 128 MiB ceiling and an oversized uncompressed stream is refused

EXPLICITLY OUT OF SCOPE:
- No shipper, sender cursor, transport, systemd unit, cron entry, timer or deployment; the receiver is not activated.
- No baseline (about 424 MB) shipment and no change to learning export, legacy sync or learning activation.
- No PostgreSQL ingestion, no unpacking of evidence, no network listener.
- No deletion of source or received evidence, ever, and no change to retention.
- No trading, Kraken, Telegram, scoring or alert changes.

FROZEN BOUNDARIES:
- Acknowledgement only after the segment is durable and verified.
- Source files are never modified or removed by the receiver.
- Credential-like content fails closed.
- Existing bounded_jsonl behavior and the 128 MiB memory ceiling are unchanged.
- Coding automation has no trading authority.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_s2_segment_receiver_v1.py::test_verified_segment_is_stored_once_and_acknowledged
AC-002 -> tests/test_opip_s2_segment_receiver_v1.py::test_acknowledgement_is_written_only_after_the_segment_is_durable
AC-003 -> tests/test_opip_s2_segment_receiver_v1.py::test_repeated_delivery_is_a_no_op
AC-004 -> tests/test_opip_s2_segment_receiver_v1.py::test_bad_checksum_size_or_rows_are_rejected_without_writes
AC-004 -> tests/test_opip_s2_segment_receiver_v1.py::test_same_size_corruption_is_caught_by_the_sha256_check
AC-005 -> tests/test_opip_s2_segment_receiver_v1.py::test_credential_like_rows_fail_closed
AC-006 -> tests/test_opip_s2_segment_receiver_v1.py::test_reserve_pauses_at_three_gib_and_fails_closed_at_two
AC-006 -> tests/test_opip_s2_segment_receiver_v1.py::test_free_space_falling_below_the_floor_mid_write_aborts_and_cleans_up
AC-007 -> tests/test_opip_s2_segment_receiver_v1.py::test_crash_before_ack_is_recovered_without_duplicates
AC-007 -> tests/test_opip_s2_segment_receiver_v1.py::test_corrupt_stored_segment_is_left_in_place_and_reported
AC-008 -> tests/test_opip_s2_segment_receiver_v1.py::test_source_files_are_never_modified_or_removed
AC-009 -> tests/test_opip_s2_segment_receiver_v1.py::test_receiver_is_files_only_with_no_database_network_or_process_imports
AC-010 -> tests/test_opip_s2_segment_receiver_v1.py::test_large_segment_is_verified_in_bounded_memory
AC-010 -> tests/test_opip_s2_segment_receiver_v1.py::test_oversized_uncompressed_stream_is_rejected

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-003 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-008 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-009 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-010 -> OHM-Trade-Agent-v1/app/opip/storage/segment_receiver.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_s2_segment_receiver_v1.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-S2-SEGMENT-RECEIVER-v1.md
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The sender (shipper with cursor, spool and discontinuity recovery) is a separate slice, S2b.
- Transport and credentials between the trading droplet and the learning worker are undecided.
- A systemd unit or cron wrapper for the one-shot pass needs its own approval.
- Orphan segments stored without a manifest after a crash are overwritten by retransmission; a report of them is deferred.
- The 16 GiB uncompressed bound and 64 MiB mid-copy recheck interval are provisional.

UNAPPROVED SCOPE CHANGES:
NONE
