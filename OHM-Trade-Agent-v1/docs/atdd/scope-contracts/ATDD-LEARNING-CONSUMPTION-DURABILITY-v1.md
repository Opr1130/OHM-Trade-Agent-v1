INCREMENT:
ATDD-LEARNING-CONSUMPTION-DURABILITY-v1

OWNER-APPROVED INTENT:
Close the durability half of N-01 (`ATDD-TRADEVISOR-V2-ADVISORY-v1` DEFERRED DISCOVERIES): the consumption write path was idempotent for an identical replay but silently overwrote a *valid* acknowledgement when a later claim named different evidence, a different definitive measurement status or a different disposition. That made durable consumption evidence rewritable, which is exactly what the retention contract relies on. This increment makes a valid acknowledgement immutable, keeps the identical replay idempotent, keeps a corrupt or foreign-release record repairable, and proves the acknowledgement gate rejects everything that is not terminal and definitive. It adds no deployment unit, no scheduler, no network channel and no new writer: the remaining N-01 dependency (a cross-VM acknowledgement producer) stays owner-gated.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py: `record_consumption_ack` (the only consumption write path), `_consumption_ack_is_valid`, `_retention_eligible`, `_prune`.
- OHM-Trade-Agent-v1/app/jobs/report_tradevisor_learning.py: the measurement/consumption producer that would call the write path.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md: the increment that defined the acknowledgement vocabulary, the definitive-status set and N-01.
- OHM-Trade-Agent-v1/docs/atdd/README.md: the scope-control mechanism.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a terminal Tradevisor row that already carries a valid acknowledgement, and a later claim naming a different measurement status, evidence identity or disposition
WHEN:
the acknowledgement is recorded
THEN:
the write is refused with no persisted change, because a valid acknowledgement is durable evidence that a retry, a second worker or a superseded measurement must never rewrite

AC-002:
GIVEN:
a terminal row that already carries a valid acknowledgement, and an identical replay of the same acknowledgement
WHEN:
the replay is recorded
THEN:
it succeeds without rewriting the record, and the original acknowledged-at timestamp is preserved so a crash after the write and before cleanup cannot create a second logical consumption

AC-003:
GIVEN:
a terminal row whose consumption record is corrupt, truncated or written by a different release
WHEN:
a valid acknowledgement is recorded
THEN:
the record is repaired, because a record that is not valid evidence cannot be treated as durable evidence

AC-004:
GIVEN:
an unknown dedup identity and a row that is still queued rather than terminal
WHEN:
an acknowledgement is recorded
THEN:
both are refused with no persisted consumption field, so consumption is never fabricated

AC-005:
GIVEN:
a terminal row and an acknowledgement that is missing its evidence identity, names an unknown disposition, names a non-definitive measurement status such as PARTIAL_COVERAGE or PENDING, or claims a release that is not the current one
WHEN:
the acknowledgement is recorded
THEN:
every case is refused with no persisted consumption field, so a partial, pending or cross-release measurement can never consume evidence

AC-006:
GIVEN:
an aged terminal row with no acknowledgement, then the same row with a corrupt acknowledgement, then a valid acknowledgement followed by a later enqueue
WHEN:
retention runs
THEN:
the unacknowledged and corrupt-acknowledged rows are retained despite their age and cannot be evicted, while the validly acknowledged row becomes retention-eligible and is pruned, so incomplete evidence is never silently dropped

AC-007:
GIVEN:
a terminal row whose consumption record is not valid evidence, and a valid acknowledgement that repairs it, followed by an identical replay and then repeated repairs beyond the history bound
WHEN:
each acknowledgement is recorded
THEN:
the rejected record is preserved in a bounded audit history with its supersede timestamp and reason, an identical replay appends nothing, and the history never grows past its limit, so repair cannot erase provenance and cannot grow without limit

AC-008:
GIVEN:
a terminal row and an acknowledgement that names the terminal non-measurable disposition while supplying a non-definitive measurement status such as PENDING or PARTIAL_COVERAGE
WHEN:
the acknowledgement is recorded
THEN:
it is refused with no persisted consumption field and the row stays retained, so a non-measurable row is never fabricated into a measurement to satisfy retention; making that disposition first-class remains an owner decision and the current behaviour fails closed

AC-009:
GIVEN:
two concurrent claims for the same terminal row that name different definitive measurement statuses
WHEN:
both are recorded through the existing registry lock
THEN:
at most one claim is accepted, the persisted record is a single valid acknowledgement naming one status, and the loser is refused rather than merged, so a race can never produce a hybrid or double consumption

EXPLICITLY OUT OF SCOPE:
- Any new deployment unit, systemd timer, cron entry, Compose service or workflow step
- Any new privileged channel, network ingress, remote-write path or credential
- Any change to the acknowledgement vocabulary, the definitive-status set or the retention rule
- Any change to measurement semantics, candle validation or the release gate
- Any change to Tradevisor evaluation, qualification, notification or canonical handling
- Activating, scheduling or deploying the learning job
- Reading, migrating or pruning production evidence

FROZEN BOUNDARIES:
- `record_consumption_ack` remains the only consumption write path; no second writer is introduced.
- `CONSUMPTION_DISPOSITIONS` and `DEFINITIVE_MEASUREMENT_STATUSES` keep their current members.
- Exact-release gating is unchanged: an acknowledgement requires `RELEASE_CURRENT`.
- A row is retention-eligible only when it is `POISONED` or carries a valid acknowledgement.
- Capacity pressure continues to return explicit backpressure instead of evicting unacknowledged evidence.
- No learning compute runs on the trading host and no trading authority is granted to the learning plane.
- `LEARNING_TERMINAL_NON_MEASURABLE` remains unrepresentable through the acknowledgement path (it requires a definitive measurement status); that gap is recorded, not widened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_consumption_v1.py::test_ac_001_conflicting_acknowledgement_never_rewrites_valid_evidence
AC-002 -> tests/test_tradevisor_consumption_v1.py::test_ac_002_identical_acknowledgement_is_idempotent
AC-003 -> tests/test_tradevisor_consumption_v1.py::test_ac_003_corrupt_acknowledgement_is_repairable
AC-004 -> tests/test_tradevisor_consumption_v1.py::test_ac_004_unknown_or_non_terminal_rows_are_never_acknowledged
AC-005 -> tests/test_tradevisor_consumption_v1.py::test_ac_005_non_definitive_or_foreign_acknowledgements_are_refused
AC-006 -> tests/test_tradevisor_consumption_v1.py::test_ac_006_retention_requires_a_valid_acknowledgement
AC-007 -> tests/test_tradevisor_consumption_v1.py::test_ac_007_repairing_an_invalid_record_keeps_an_audit_trail
AC-007 -> tests/test_tradevisor_consumption_v1.py::test_ac_007_history_is_bounded
AC-008 -> tests/test_tradevisor_consumption_v1.py::test_ac_008_terminal_non_measurable_cannot_be_acked_by_fabricating_a_measurement
AC-009 -> tests/test_tradevisor_consumption_v1.py::test_ac_009_concurrent_acknowledgements_settle_on_one_valid_record

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-LEARNING-CONSUMPTION-DURABILITY-v1.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-008 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_consumption_v1.py

DEFERRED DISCOVERIES:
- `LEARNING_TERMINAL_NON_MEASURABLE` cannot currently be acknowledged truthfully, because every acknowledgement requires a definitive measurement status. A poisoned or non-measurable row is instead pruned through the `POISONED` branch. Making that disposition first-class needs an owner decision about the measurement-status vocabulary.
- A valid acknowledgement from a *previous* release becomes invalid under the current release and is therefore repairable. No consumption history is kept beyond the single record; an append-only consumption ledger would be a new persistence surface and is not created here.
- There is still no production producer of acknowledgements: no deployment unit invokes `report_tradevisor_learning`, and `record_consumption_ack` has no production caller. That cross-VM handoff remains BLOCKED_OWNER.
- Concurrency is serialized by the existing registry lock; the increment proves sequential conflicting claims, not multi-process race behaviour on a shared volume.

UNAPPROVED SCOPE CHANGES:
NONE

