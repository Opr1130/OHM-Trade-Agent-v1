INCREMENT:
ATDD-M07-R5-fresh-canonical-genesis-v1

OWNER-APPROVED INTENT:
Prepare O'Pip V2 for a fresh EVIDENCE_SHADOW launch without importing historical V1 data. The owner has decided: V1 is powered off; V1 data and backups are preserved as historical archives; the 220,721 historical canonical events are not migrated; no historical learning, calibration or runtime state is imported; V2 begins a new canonical history; PAPER_V2 and funded trading remain disabled; and no live deployment is authorized by this instruction. This increment adds exactly one narrowly scoped M07 capability: an explicit, owner-authorized fresh-genesis operation that creates a brand-new empty canonical store using the CERTIFIED writer and schema initialization, publishes a verifiable genesis receipt, and extends the existing M07 writer gate to accept either the existing valid native-recovery receipt or the independently re-verified fresh-genesis receipt. It must never overwrite, adopt or silently discard an existing database, must refuse existing nonempty history, unknown database files, existing writers, stale receipts, destination mismatches and conflicting SQLite sidecars, and must leave V1 data, historical learning and old calibration outside the new V2 state. The certified application tree (`OHM-Trade-Agent-v1/app/`), the certified application SHA `c196dd674678b3b1ee91c351ee97c1c9e2b3938d`, the core Compose contract, release identity, protection and authorization controls are unchanged. The M07 runbook gains a separate fresh-start route; the historical migration route stays available but inactive.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/opip/canonical/schema.py: the certified `initialize_schema`, `CanonicalStoreLock`, sidecar, durability and validation primitives that genesis must reuse and must not duplicate.
- OHM-Trade-Agent-v1/app/opip/canonical/writer.py: `CanonicalWriter` is the single write authority; internal store ownership is an OS advisory lock, so exactly one writer can own a store.
- OHM-Trade-Agent-v1/app/opip/canonical/recovery.py: the certified native restore, epoch advance and no-replace publication pattern the fresh route mirrors.
- OHM-Trade-Agent-v1/deploy/m07/scripts/opip_recovery.py: the existing native-recovery receipt (`canonical-native-restore`) the writer gate must keep accepting.
- OHM-Trade-Agent-v1/deploy/m07/scripts/opip_rollback_guard.py: the existing M07 writer gate.
- OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh: `--mode activate-writer` and `--mode activate-scheduler`, the owner gates around the writer and scheduler.
- OHM-Trade-Agent-v1/deploy/m07/06-SINGLE-CUTOVER-RUNBOOK.md and OHM-Trade-Agent-v1/deploy/m07/08-OWNER-GATES.csv: the authoritative cutover procedure and owner gates.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md: the movable `ACTIVE_INCREMENT` rule that forbids a completed increment from pinning the global pointer.
- AGENTS.md and CLAUDE.md: smallest change that solves the proven problem; no trading authority, no production deployment, no secret handling, no durable-evidence destruction.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the certified canonical package and the M07 toolkit
WHEN:
a fresh genesis is prepared
THEN:
exactly one new toolkit module implements it, it delegates schema initialization and store ownership to the certified `CanonicalWriter` and `schema.initialize_schema`, the toolkit contains no DDL, no `executescript`, no direct canonical INSERT and no second database writer, the certified application tree and the core Compose contract are unmodified, and behavioural tests exist for it
AC-002:
GIVEN:
an owner-authorized genesis request
WHEN:
the operation runs
THEN:
owner authorization, the authoritative receipt path, the certified SHA, a trusted metadata identity endpoint, a metadata-read host identity, the certified canonical destination layout, the operator's explicit confirmation of that exact destination, the receipt location, a free receipt path, the absence of an existing store, the absence of SQLite sidecars, the absence of a live writer and an empty canonical directory are all proven before anything is created; a missing `--confirm-destination` or `--receipt-out` is refused before any mutation, so no canonical directory, database or artifact is created; host identity is read only from the pinned droplet metadata endpoint, which is not operator-selectable and accepts no self-declared endpoint substitute (a literal loopback stub is accepted only for isolated tests and never counts as production identity proof); and a failed precondition leaves the canonical database and the authoritative receipt unchanged while publishing its own separate diagnostic evidence beside (never on top of) the authoritative receipt path
AC-003:
GIVEN:
an existing canonical destination, an unknown file, a sidecar pair, an interrupted prior attempt or a repeated invocation after a successful genesis
WHEN:
a fresh genesis is attempted
THEN:
the operation refuses with the specific reason, nothing is overwritten, adopted, deleted or replaced, the canonical database and the authoritative receipt stay byte-identical across a repeated invocation while each refusal may add only its own separate diagnostic evidence file, publication uses a no-replace primitive with no replace-capable fallback, and staging cleanup is scoped to this tool's own private staging marker
AC-004:
GIVEN:
a successfully published genesis store
WHEN:
the receipt is written
THEN:
it records host identity and its provenance, the certified SHA, the certified module digests, the destination and its SHA-256, the schema version and fingerprint, the initial epoch and sequence, zero historical events, the integrity and foreign-key results, the generation provenance, and a SHA-256 self-digest, it is created without replacing an existing receipt, and a failed run writes separate failure evidence instead of a PASS
AC-005:
GIVEN:
the M07 writer gate and a store
WHEN:
writer activation is requested
THEN:
a valid native-recovery receipt and an independently re-verified fresh-genesis receipt are both accepted, a missing, unsupported, tampered, self-inconsistent, stale, future-dated, destination-mismatched, wrong-host, wrong-SHA or unverifiable receipt is refused, the gate re-derives the store bytes, schema fingerprint, coordinates, event count, integrity, foreign keys and certified source digests instead of trusting the receipt's own verdict, and a store that already contains history can never pass
AC-006:
GIVEN:
the host scripts and the certified store lock
WHEN:
the writer is activated
THEN:
exactly one writer can own the store, a second writer is refused by the certified lock, duplicate activation is refused when `WRITER_ACTIVATED.json` already exists, the started writer count must be exactly one, and the scheduler stays blocked until `WRITER_ACTIVATED.json` and a satisfied `LIVE-ACCEPTANCE.json` (HTTPS and Telegram) both exist
AC-007:
GIVEN:
the power-off V1 archives, historical learning evidence and old calibration
WHEN:
V2 genesis runs
THEN:
the genesis surface has no parameter or flag that can name a bundle, archive, learning or calibration source, the V1 archives and learning/calibration files are byte-identical afterwards, the new store has zero events and epoch 1, and the receipt declares that no history was imported and no archive was touched
AC-008:
GIVEN:
the M07 documentation and the CI qualification
WHEN:
the fresh-start route is reviewed
THEN:
a separate fresh-genesis runbook documents prerequisites, phases, refusals, retry and evidence, the cutover runbook states that the fresh-start route is selected and the historical migration route is retained but inactive, the readiness table lists the outstanding release prerequisites without claiming a PASS, and the Linux qualification job runs the focused fresh-genesis acceptance module
AC-009:
GIVEN:
the movable ATDD `ACTIVE_INCREMENT` pointer and the completed R4 increment
WHEN:
this increment is activated
THEN:
the pointer names an existing scope contract, the completed R4 increment records the handoff and no longer pins the pointer while keeping every substantive R4 assertion, and the structural pin guard covers the corrected R4 module


EXPLICITLY OUT OF SCOPE:
- Any change to `OHM-Trade-Agent-v1/app/`, the Dockerfile, `docker-compose.yml` or the certified release identity
- Migrating, reading, rewriting, moving or deleting the 220,721 V1 canonical events, V1 backups or V1 runtime state
- Importing historical learning, calibration or runtime state, or changing any learning-plane compatibility rule
- Activating PAPER_V2, funded authority, live exchange keys, Telegram execution or any trading authority
- Deploying, SSH to production, starting or stopping services, restoring data, powering on V1, or activating the writer or scheduler
- A second database writer, a second DDL, a second release-profile mechanism or a second deployment control plane
- Weakening, skipping or deleting any existing test, owner gate, CI job or protection control

FROZEN BOUNDARIES:
- `app.opip.canonical` remains the single canonical writer and schema authority; the toolkit executes no DDL.
- The certified application SHA `c196dd674678b3b1ee91c351ee97c1c9e2b3938d` and the `DROPLET_ID`/metadata identity gate are unchanged.
- The native-recovery receipt semantics and the `writer-gate` checks for it are unchanged; the genesis receipt is additive.
- `--mode activate-scheduler` keeps requiring `WRITER_ACTIVATED.json` plus `https_ok` and `telegram_ok`.
- No secret, token, credential or `.env` value is read, copied or written by this increment.
- Production data stores and canonical evidence are never destructively mutated; genesis only creates a new store where none exists.
- Every historical increment's contract, acceptance traceability and substantive assertions remain intact; only the recorded R4 global-pointer ownership pin is removed.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_001_one_bounded_capability_reuses_the_certified_initialization
AC-002 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_002_owner_identity_and_destination_are_proven_before_any_mutation
AC-003 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_003_an_existing_database_is_never_overwritten_adopted_or_discarded
AC-004 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_004_the_receipt_records_the_full_genesis_identity
AC-005 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_005_writer_activation_accepts_either_receipt_and_refuses_forgeries
AC-006 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_006_one_writer_duplicate_activation_refused_scheduler_still_gated
AC-007 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_007_no_historical_state_can_enter_the_new_v2_history
AC-008 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_008_fresh_start_route_is_separate_and_prerequisites_are_honest
AC-009 -> tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py::test_ac_009_governance_handoff_is_lifecycle_safe


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_genesis.py
AC-001 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r5_fresh_genesis.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_genesis.py
AC-002 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r5_fresh_genesis.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_genesis.py
AC-003 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r5_fresh_genesis.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_genesis.py
AC-004 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r5_fresh_genesis.py
AC-005 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_rollback_guard.py
AC-005 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r5_fresh_genesis.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_layout.py
AC-006 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r5_fresh_genesis.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py
AC-007 -> OHM-Trade-Agent-v1/deploy/m07/scripts/opip_genesis.py
AC-007 -> OHM-Trade-Agent-v1/deploy/m07/tests/scenarios/test_r5_fresh_genesis.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py
AC-008 -> .github/workflows/m07-cutover-toolkit.yml
AC-008 -> OHM-Trade-Agent-v1/deploy/m07/06-SINGLE-CUTOVER-RUNBOOK.md
AC-008 -> OHM-Trade-Agent-v1/deploy/m07/16-FRESH-GENESIS-RUNBOOK.md
AC-008 -> OHM-Trade-Agent-v1/deploy/m07/17-FRESH-GENESIS-READINESS.csv
AC-008 -> OHM-Trade-Agent-v1/deploy/m07/README.md
AC-008 -> OHM-Trade-Agent-v1/deploy/m07/TEST-COMMANDS.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-M07-R4-readiness-recovery-v1.md
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-M07-R5-fresh-canonical-genesis-v1.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r4_readiness_recovery_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_atdd_m07_r5_fresh_canonical_genesis_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py

DEFERRED DISCOVERIES:
- Writer activation remains a one-shot first-start gate for both receipt kinds, exactly as the certified native-recovery contract already behaves: once the writer commits anything, the store bytes no longer match the receipt. Container restarts are Compose's responsibility. Whether an explicit owner-authorized "re-activation on a non-empty store" operation is desirable is a separate architecture decision and is not authorized here.
- The genesis receipt proves that the live store *is* an empty certified-schema genesis store; it is not a cryptographic signature over who ran the tool. Authorship stays bounded by owner authorization, the metadata host identity, the certified module digests, the toolkit manifest and the receipt age.
- The exact V2 resource limits, the recoverable-backup evidence and the learning-plane exact-SHA compatibility are recorded as outstanding prerequisites in `17-FRESH-GENESIS-READINESS.csv`; none is claimed as proven by this increment.
- The disposable Linux rehearsal still cannot reach `execute`/`activate-writer` because the protection preflight is BLOCKED without production exchange credentials, so a live writer start on V2 remains owner-gated and UNPROVEN.

UNAPPROVED SCOPE CHANGES:
NONE

