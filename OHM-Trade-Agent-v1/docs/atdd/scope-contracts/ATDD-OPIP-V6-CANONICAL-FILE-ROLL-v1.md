INCREMENT:
ATDD-OPIP-V6-CANONICAL-FILE-ROLL-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. V6 audit
section 9 records that the canonical ledger is one 5.5 GB SQLite file with a WAL
file and a single-writer lock and socket, that row deletion plus `VACUUM` is ruled
out, and that the retention answer is a writer that rolls to a new file per day or
week, closed files shipped to the data box, hash-verified, then deleted after the
14-day hot window, with readers that span files. This increment writes that answer
down as one design note, `docs/architecture/CANONICAL_FILE_ROLL.md`, and adds one
acceptance test per criterion. It is documentation only: no writer change, no reader
change, no schema or catalog file, no compose, Dockerfile, deploy or workflow edit,
no data moved, shipped, compressed or deleted, no retention setting enabled, and no
trading, exchange, Telegram or learning authority added or widened.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_AUDIT.md: section 9 (data and retention: the single `opip_canonical_v1.sqlite3` of 5.5 GB with a WAL file, single-writer lock and socket, the "roll to a new file per day or week" design, hash-verified shipping to the data box, deletion after the hot window, readers that span files, the ruled-out row delete plus `VACUUM`, the unchecked writer hash chain and the I/O-wait hypothesis).
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 3 (owner decision: 14-day hot window, older data ships to the data box and is deleted only after hash verification), section 6 (later slice: canonical file roll and shipping), section 4 (additive and smallest change per slice) and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-RETENTION-POLICY-v1.md: the approved 14-day hot window and minimum floor this design defers to, and its out-of-scope entry naming the canonical writer roll as a later increment.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-AUDIT-DOCS-v1.md: its deferred discovery that the rolling canonical ledger needs its own contract and owner approval.
- OHM-Trade-Agent-v1/app/opip/canonical/{paths.py,schema.py,writer.py,server.py,writer_service.py,backup.py,recovery.py,rebuild.py,healthcheck.py}: the existing primitives the design reuses and does not change.
- OHM-Trade-Agent-v1/app/opip/data_platform/{retention_policy.py,retention_dry_run.py,archive_lifecycle.py,shipper.py}: the approved retention policy, the read-only preview and the checksum/off-host verification precedent the design reuses.
- OHM-Trade-Agent-v1/AGENTS.md: principle 8 (durable evidence is never destructively mutated), principle 9 (production, shadow and offline planes stay separate), principle 10 (no Postgres on the trading host) and principle 20 (state behavioral impact before a data-durability change).
- OHM-Trade-Agent-v1/CLAUDE.md: engineering operating model (smallest change, reuse existing data-plane conventions, no new infrastructure) and trading safety boundary.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: the v6 audit section 9 facts about the single 5.5 GB canonical file, its writer and its retention
WHEN: the canonical file roll design note is added at `docs/architecture/CANONICAL_FILE_ROLL.md`
THEN: the note covers rolling, shipping to the data box, hash verification, the 14-day retention and deletion rule, the reader changes and the open questions for the owner, and lists the slices the design still needs

AC-002:
GIVEN: the repository and this increment's approved scope contract
WHEN: the implementation map and the runtime sources are inspected
THEN: the only authorized paths are the design note, the acceptance test, the contract and the active-increment pointer; no runtime, deploy, compose or workflow path is authorized, and no application module reads the design note

EXPLICITLY OUT OF SCOPE:
- Any writer, reader, schema, catalog, migration or retention-policy code change, including any roll flag, segment helper or catalog model.
- Shipping, copying, moving, compressing, truncating or deleting any data file, and any `--apply`, `--ship` or `--prune` flag.
- Enabling retention deletion, changing environment or `.env` values, or changing the configuration registry.
- Compose, Dockerfile, deploy script, scheduler, systemd timer or workflow edits.
- Postgres, analytics-plane, data-box provisioning, learning evidence and reconciliation changes.
- Trading authority, Kraken order placement, modification, cancellation or confirmation of any kind, Telegram execution, and any widening of Paper or production admission.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory and read-only: coding automation has no trading authority, no exchange permissions and no mutation authority over data.
- Deletion stays off and remains blocked without a hash-verified receipt for the segment.
- The 14-day hot window and every existing fail-closed gate keep their current behavior.
- The note is design evidence, not a second architecture or policy source, and it authorizes no slice.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior.
- No secret, credential or `.env` value is read, printed or written; the note contains only paths, sizes, day counts, hashes and state names.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_canonical_file_roll_v1.py::test_note_covers_roll_shipping_hash_retention_readers_and_open_questions
AC-002 -> tests/test_v6_canonical_file_roll_v1.py::test_increment_is_documentation_only_and_changes_nothing_else

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/architecture/CANONICAL_FILE_ROLL.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_canonical_file_roll_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-CANONICAL-FILE-ROLL-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/CANONICAL_FILE_ROLL.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_canonical_file_roll_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-CANONICAL-FILE-ROLL-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The design's nine slices each need their own owner-approved contract: pure paths and catalog, writer roll behind a default-off flag, roll observability, segment-spanning readers, shipping dry-run, shipping execution, retention eligibility and deletion, the Ops Center segment page, and data-box history.
- The audit records the writer's own hash chain as unchecked. Whether a segment hash must reconcile with a per-row chain is open question 2 and blocks any shipping slice.
- Day versus week cadence, the cutover downtime for today's 5.5 GB file, sequence continuity across segments, transfer mechanism and credentials, and the retention-deletion enablement authority are owner decisions listed in the note's open questions.
- The I/O-wait hypothesis (5.5 GB database above a 2.5 GB page cache) stays a hypothesis until the roll-observability slice measures it.
- The audit's one-time split of the existing file by date on the data box is not resolved here; the note asks whether per-segment history makes it unnecessary.

UNAPPROVED SCOPE CHANGES:
NONE
