INCREMENT:
ATDD-OPIP-V6-QUALIFICATION-STAMP-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. V6 slice D
labels new qualification records with the slice A version stamp so every piece
of O'Pip evidence names the strategy, model and gate policy that produced it.
The funnel and scan-summary builders already stamp their own rows; the two
streams built outside a builder - screening evaluations and early timing
milestones - gain the stamp at the existing writer, through one additive helper
(`app/opip/decision/store.py::stamp_qualification_record`). Nothing else moves:
no gate, threshold, ranking, alert, admission or trading behavior changes, the
store stays dark by default, and records written before the stamp existed keep
loading exactly as recorded.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 5 slice D (stamp qualification records with the version stamp, additive field, gating unchanged), slice A dependency, and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-STAMP-v1.md: the approved version stamp slice and its DEFERRED DISCOVERIES entry naming this increment.
- OHM-Trade-Agent-v1/app/opip/decision/versioning.py: `version_stamp()`, the only version source, reused unchanged.
- OHM-Trade-Agent-v1/app/opip/decision/store.py: the append-only qualification evidence writer, its bounded archive and its fail-soft append contract.
- OHM-Trade-Agent-v1/app/opip/decision/funnel.py and OHM-Trade-Agent-v1/app/opip/decision/summary.py: the builders that already stamp funnel rows and scan summaries.
- OHM-Trade-Agent-v1/AGENTS.md: principle 8 (durable evidence is extended additively) and principle 20 (behavioral impact stated before the change).

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a screening evaluation row, an early timing milestone row, a funnel row and a scan summary produced by the existing builders
WHEN: each new qualification record is written through the qualification store
THEN: the persisted record carries the version stamp (strategy, model as an explicit None, and the gate policy fingerprint matching gate_policy_fingerprint()) while keeping every value it already had

AC-002:
GIVEN: qualification records written before the stamp existed, with no version fields
WHEN: those records are read back from the qualification streams
THEN: they still load exactly as recorded, with no field injected on read, and a newly stamped record appended after them leaves the legacy record unchanged

EXPLICITLY OUT OF SCOPE:
- Changing, defining or loosening any gate, threshold, ranking, admission, risk, alert or paper-trading behavior.
- Consuming the stamp (scorecard, learning, analytics, Telegram) - later slices.
- Backfilling, rewriting or migrating existing evidence files and archives.
- Adding the stamp to dead-letter diagnostics, configuration, scheduler, compose or deploy paths.
- Kraken order placement, modification, cancellation or confirmation of any kind.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory-only: coding automation has no trading authority and no exchange permissions.
- Deterministic gates keep failing closed; external AI stays advisory evidence.
- The telemetry flag still gates all persistence and remains dark by default.
- The stamp is additive: a value the caller already captured always wins, and reads never inject a field.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior.
- No secret, credential or `.env` value is read, printed or written.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_qualification_stamp_v1.py::test_written_qualification_records_carry_the_version_stamp
AC-002 -> tests/test_v6_qualification_stamp_v1.py::test_records_written_without_the_stamp_still_load

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/decision/store.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_qualification_stamp_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-QUALIFICATION-STAMP-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/decision/store.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_qualification_stamp_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-QUALIFICATION-STAMP-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- `append_funnel_events` and `append_scan_summary` are not stamped at the writer, because their rows arrive already stamped and their existing store tests pin exact row round-tripping; a future contract can make the writer the only stamping point once those row-level expectations are reviewed.
- Dead-letter diagnostics (`append_qualification_dead_letter`) stay unstamped: they describe failed writes rather than a qualification decision.
- Scorecard consumption of the stamp is v6 slice E and needs its own owner-approved contract.

UNAPPROVED SCOPE CHANGES:
NONE
