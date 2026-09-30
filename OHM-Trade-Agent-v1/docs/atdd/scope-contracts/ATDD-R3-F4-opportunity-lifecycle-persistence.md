INCREMENT:
ATDD-R3-F4-opportunity-lifecycle-persistence

OWNER-APPROVED INTENT:
OWNER-authorized third R3 slice after F3 and the pure F4 contract/implementation: durable canonical persistence of F4 Opportunity Lifecycle transitions through the ONE existing canonical writer, with restart recovery, strict durable validation and a read-only projection. This increment makes one already-computed lifecycle transition durable evidence and nothing more. It grants no runtime authority, wires no consumer, activates no scheduler and does not implement F5/F6/F7, F4 orchestration, detector-to-lifecycle wiring, consumer migration or cutover.

NORMATIVE PARENT CONTRACTS (read-only; not rewritten by this increment): `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md` and `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md`. Those frozen contracts remain the normative acceptance source for F4 lifecycle semantics. This increment adds the persistence application paths and restates, without weakening, the acceptance criteria it implements. It does not rewrite, re-interpret or relax any frozen F4 or F3 contract, and it does not modify `app/opip/contracts/opportunity.py` semantics beyond adding strict durable `from_dict` constructors.

ONE WRITER. Persistence reuses the existing `app.opip.canonical.writer.CanonicalWriter` and its canonical SQLite WAL/event store. There is NO second database, NO opportunity.sqlite, NO F4 JSONL truth, NO new WAL, NO new service/daemon and NO separate persistence ownership. No physical table migration occurs: the generic `events`, `idempotency_keys` and `watermarks` tables are used as-is and `canonical/schema.py` has zero diff.

ARCHITECTURE REFERENCES:
- The F4 parent contracts (read-only): `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle.md`, `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-implementation.md`, `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F3-ignition-detector.md`.
- O'Pip Profit Intelligence Platform Architecture v1.4.3: `OHM-Trade-Agent-v1/docs/architecture/v1.4.3/ARCHITECTURE.md` and the authority DOCX, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`.
- v1.4.3 data model and canonical evidence sections: episode identity, defer deadline, terminal reason; evaluation time, cutoff, watermark and content hash.
- `OHM-Trade-Agent-v1/app/opip/canonical/writer.py`, `schema.py`, `client.py`, `server.py`: the single writer, its generic `events`/`idempotency_keys`/`watermarks` store and its read-projection and RPC conventions.
- `OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py` and `OHM-Trade-Agent-v1/app/opip/opportunity_lifecycle.py`: the pure F4 vocabulary and transition surface this increment persists.

BASE SHA: `e85875564a91154ec445ac1e037916eb91af8ce3` (`origin/main`).

DURABLE EVENT VOCABULARY (owned by `OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py`):

```text
event_type   = "opportunity_lifecycle.transition.recorded"
stream       = "opportunity_lifecycle"
schema token = "opportunity-lifecycle-transition-v1"
record_type  = "opportunity_lifecycle_transition"
priority     = "LOW"
idempotency  = "OPLT:" + OpportunityLifecycleEvent.event_id   (event.envelope = OPLT:<OPEV:digest>)
```

DURABLE PAYLOAD:

```json
{
  "record_type": "opportunity_lifecycle_transition",
  "schema_version": "opportunity-lifecycle-transition-v1",
  "episode": { "...": "OpportunityEpisode.to_dict()" },
  "event":   { "...": "OpportunityLifecycleEvent.to_dict()" }
}
```

No FeatureSnapshot payload and no detector feature values are copied. `event_time` is the canonical UTC serialization of `OpportunityLifecycleEvent.evaluation_time`; `recorded_at` remains the writer receipt time and never alters F4 semantics. `correlation_id` is the episode id; `causation_id` is the source claim id for claim-driven OPENED/DEFERRED and null for EXPIRED (never fabricated).

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the canonical writer's registered event vocabulary
WHEN:
the F4 persistence event type, stream and schema token are inspected
THEN:
they are uniquely registered in `ACCEPTED_EVENT_TYPES`, `IDEMPOTENT_PAYLOAD_EVENT_TYPES`, the writer stream mapping and the ops-handoff exemption, with LOW priority and the single schema token, and no other durable event type is introduced

AC-002:
GIVEN:
a changed F4 lifecycle result carrying exactly one lifecycle event
WHEN:
the producer seam persists it
THEN:
exactly one writer intent is produced, with the deterministic idempotency key, LOW priority, no ops handoff, the episode correlation id and the correct causation id

AC-003:
GIVEN:
an unchanged F4 lifecycle result carrying zero events
WHEN:
the producer seam is asked to persist it
THEN:
no writer intent and no canonical write occur

AC-004:
GIVEN:
a durable F4 payload built from an episode and its event
WHEN:
the payload is validated and reconstituted through strict deserialization
THEN:
it reconstructs the exact episode and event, re-running the same constructor invariants and identity checks, and the persistence boundary returns a canonical re-serialization rebuilt from the reconstituted objects so equivalent UTC timestamp spellings produce identical durable bytes

AC-005:
GIVEN:
a deterministic F4 lifecycle event identity
WHEN:
the durable idempotency key is derived
THEN:
it is a pure function of that event identity alone, with no receipt time, random envelope id, process id, retry count, database sequence or current clock, and a malformed identity fails closed

AC-006:
GIVEN:
an exact resubmission of a previously committed F4 transition
WHEN:
the writer commits it
THEN:
the outcome is DUPLICATE_OK, the same canonical envelope event id is returned, and exactly one canonical F4 row exists

AC-007:
GIVEN:
the same deterministic idempotency key carrying a semantically different payload
WHEN:
the writer validates and commits it
THEN:
it fails closed with an integrity conflict, is never DUPLICATE_OK, and never silently keeps first or last

AC-008:
GIVEN:
an OPENED transition resulting in an ACTIVE episode
WHEN:
it is persisted
THEN:
it commits and the read projection reports an ACTIVE episode with exactly one event

AC-009:
GIVEN:
a DEFERRED creation
WHEN:
it is persisted
THEN:
it commits and the read projection reports a DEFERRED episode carrying its explicit deadlines

AC-010:
GIVEN:
a DEFERRED episode evaluated at its deadline
WHEN:
the EXPIRED transition is persisted
THEN:
it commits and the read projection reports TERMINAL/EXPIRED with exactly two canonical rows

AC-011:
GIVEN:
an EXPIRED transition with no preceding DEFERRED record
WHEN:
the durable history is reconstructed or the writer commits it
THEN:
it fails closed and no row is written

AC-012:
GIVEN:
a terminal episode
WHEN:
any further record or replay is applied
THEN:
the terminal state is never advanced, the canonical sequence is unchanged, and an exact replay is DUPLICATE_OK

AC-013:
GIVEN:
a committed ACTIVE episode and a writer restarted on the same database
WHEN:
the episode is recovered and the original transition is resubmitted
THEN:
the recovered episode equals the original, the resubmission is DUPLICATE_OK and exactly one canonical row exists

AC-014:
GIVEN:
a committed DEFERRED episode and a writer restarted on the same database
WHEN:
the episode is recovered
THEN:
it is DEFERRED with identical deadlines preserved, and it can be expired purely from its persisted deadline

AC-015:
GIVEN:
a committed TERMINAL/EXPIRED episode and a writer restarted on the same database
WHEN:
the episode is recovered and the expiry is replayed
THEN:
it is TERMINAL/EXPIRED with identical fields, the replay is DUPLICATE_OK and exactly two canonical rows exist

AC-016:
GIVEN:
a durable payload with a forged episode identity, forged event identity, mismatched event/episode identity, tampered claim lineage, or a claim-driven creation whose evaluation instant does not equal the claim evaluation cutoff
WHEN:
it is validated at the persistence trust boundary
THEN:
it is rejected with no canonical row

AC-017:
GIVEN:
a committed F4 transition
WHEN:
a duplicate, a rejected submission or a failed SQLite commit is attempted
THEN:
the in-memory projection never leads durable transaction state, never advances twice and never mutates for a rejection

AC-018:
GIVEN:
the F4 persistence increment
WHEN:
storage is inspected
THEN:
only the existing `CanonicalWriter` and the generic `events`, `idempotency_keys` and `watermarks` tables are used, with no second database, table, writer or JSONL store, and `canonical/schema.py` has zero diff

AC-019:
GIVEN:
a committed F4 transition
WHEN:
alert state is inspected
THEN:
no `alert_ops_handoffs` row and no alert identity projection mutation exist, and the event never routes through the alert governor

AC-020:
GIVEN:
the completed F4 persistence increment
WHEN:
runtime and activation surfaces are inspected
THEN:
no production runtime caller imports or invokes F4 persistence, the Feature Bus remains OFF, and no consumer is migrated

AC-021:
GIVEN:
the frozen F3 detector contracts and the pure F4 lifecycle semantics
WHEN:
this persistence increment is applied
THEN:
F3 and the pure F4 lifecycle semantics are unchanged, the pure lifecycle module imports no canonical or persistence code, and the F4 isolation guard is widened only for the authorized persistence paths without removing any assertion

AC-022:
GIVEN:
the completed F4 persistence increment
WHEN:
import surfaces and packages are inspected
THEN:
F5 feasibility, F6 forecast, F7 selector, Paper-v2/funded authority and any second architecture spine are absent

EXPLICITLY OUT OF SCOPE:
- any second database, table, writer, WAL, JSONL store, daemon or scheduler
- a physical `opportunity_episode` / `opportunity_lifecycle_event` table or a canonical schema version bump
- F4 orchestration, detector-to-lifecycle live wiring, scheduled deadline evaluation, consumer migration and cutover
- Telegram, alert filtering, signal-quality cutover, dashboard product work
- F5 feasibility, F6 forecast, F7 selector, Paper-v2, funded trading, Committee authority
- run_cycle integration, scan_opportunities modification, Feature Bus activation
- legacy clock retirement or deletion, and any change to scan authority or stop times
- changing any architecture document, the frozen F3/F4 contracts, the ATDD checker or workflows
- weakening, deleting or skipping any existing test
- PR #294 (bridge) in any way

FROZEN BOUNDARIES:
- `OPIP_FEATURE_BUS_MODE` remains `off` in docker-compose.yml.
- Production `run_cycle` does not call F4 persistence, the F4 lifecycle or any F3-to-F4 wiring.
- The canonical writer remains the single domain write path; F4 uses its generic store and no new physical table.
- The F4 pure lifecycle module and its constructors are unchanged; only strict durable `from_dict` constructors are added.
- Risk, strategy, execution and trading authority are unchanged; funded trading remains disabled.
- Paper execution stays isolated from funded order endpoints; Committee authority remains shadow-only.
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- No merge, deploy, activation or cutover occurs under this increment by itself.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_001_event_type_stream_and_schema_are_uniquely_registered
AC-002 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_002_changed_result_yields_exactly_one_writer_intent
AC-003 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_003_unchanged_result_yields_no_write
AC-004 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_004_payload_round_trip_reconstructs_exact_episode_and_event
AC-005 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_005_idempotency_derives_only_from_lifecycle_event_identity
AC-006 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_006_exact_replay_is_duplicate_ok_with_one_row
AC-007 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_007_same_key_different_payload_fails_closed
AC-008 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_008_opened_to_active_is_valid
AC-009 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_009_deferred_to_deferred_is_valid
AC-010 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_010_deferred_to_expired_terminal_is_valid
AC-011 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_011_expired_without_deferred_fails_closed
AC-012 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_012_terminal_cannot_advance
AC-013 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_013_restart_rehydrates_active
AC-014 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_014_restart_rehydrates_deferred_preserving_deadlines
AC-015 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_015_restart_rehydrates_terminal_expired
AC-016 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_016_forged_identity_or_lineage_durable_payload_is_rejected
AC-017 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_017_projection_cannot_lead_durable_transaction_state
AC-018 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_018_uses_existing_canonical_writer_and_generic_store
AC-019 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_019_no_ops_handoff_and_no_alert_mutation
AC-020 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_020_no_runtime_caller_no_feature_bus_activation_no_consumer_migration
AC-021 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_021_f3_and_pure_f4_semantics_unchanged
AC-022 -> tests/test_opip_r3_f4_opportunity_persistence.py::test_ac_022_f5_plus_absent

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/canonical/models.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/opportunity_persistence.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/opportunity_persistence.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-010 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-011 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-012 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/canonical/client.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/canonical/server.py
AC-013 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-014 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-015 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-016 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-017 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-018 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/canonical/writer.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-019 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-019 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-020 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-020 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-020 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-021 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py
AC-021 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-021 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-021 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-022 -> OHM-Trade-Agent-v1/app/opip/contracts/opportunity_persistence.py
AC-022 -> OHM-Trade-Agent-v1/app/opip/opportunity_persistence.py
AC-022 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R3-F4-opportunity-lifecycle-persistence.md
AC-022 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py

DEFERRED DISCOVERIES:
- F4 orchestration, detector-to-lifecycle live wiring and scheduled deadline evaluation remain future increments.
- Consumer migration, per-clock stop times and cutover remain a future, separately authorized increment.
- Numeric validity-horizon source (Q6) remains an F6 concern.
- A read RPC is provided for the projection but no runtime consumer is wired to it in this increment.
- F5/F6/F7, Paper-v2 and funded authority remain future increments.

UNAPPROVED SCOPE CHANGES:
NONE
