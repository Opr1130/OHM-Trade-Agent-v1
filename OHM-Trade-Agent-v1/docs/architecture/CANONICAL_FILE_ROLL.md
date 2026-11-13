# Canonical file roll: design note

Status: design note for owner review. Date: 2026-11-12. Source: `docs/V6_AUDIT.md`
section 9 (data and retention), owner decisions in `docs/V6_SPEC.md` section 3.

This note is design only. It changes no code, no writer, no reader, no compose file,
no deploy path, no data and no retention setting. It grants no trading, exchange,
Telegram or learning authority, and it deletes nothing. Every slice it lists needs
its own scope contract and owner approval before any file changes.

## 1. Why roll

The audit measured the live droplet on 2026-11-11. `opip/` is 5.6 GB, of which
`canonical` is 5.2 GB. The canonical ledger is **one SQLite file**,
`opip_canonical_v1.sqlite3`, about 5.5 GB, with a WAL file and a single-writer lock
and socket. There is nothing to trim by segment. Deleting rows and running `VACUUM`
is ruled out: the writer holds the lock and a rewrite needs another 5.5 GB free.

The same measurement shows 12% to 20% I/O wait over 14 samples (average 17.7%) with
about 35% idle CPU and 2.3 GB memory available. The likely cause is the 5.5 GB
database exceeding the 2.5 GB page cache. That is a hypothesis until a file roll
shows the effect; this design does not claim the root cause.

Rolling the writer to a new file per day or week bounds each file's page-cache
footprint, makes closed evidence shippable as a durable object, and gives retention
a unit smaller than the whole ledger. Closed files are shipped to the data box,
hash-verified, then deleted after the hot window, and readers span the files.

## 2. Constraints

- Advisory-only: coding automation has no trading authority; no Kraken order
  placement, modification or cancellation exists in any slice of this design.
- The canonical ledger is durable evidence. A closed file is never deleted before a
  hash-verified receipt exists on the data box.
- One writer only. The writer keeps its single-writer lock and its socket in
  `opip/canonical`; a roll must not create a second writer or a second source of
  truth. Sealed files are history, never a second write target.
- The 14-day hot window from `app/opip/data_platform/retention_policy.py`
  (`DEFAULT_HOT_WINDOW_DAYS = 14`, floor 7 days) stays the policy. Deletion stays off
  (`opip_enable_retention_delete = false`) until a separately approved slice.
- Postgres and analytics stay on the separate data box. The trading host keeps no
  Postgres footprint; it only produces sealed files and transfers them.
- Fail closed. A missing, unreadable, truncated or hash-mismatched segment stops
  deletion and stops reads that would otherwise silently return a partial history.
- Existing primitives are reused, not replaced: `app/opip/canonical/paths.py`,
  `schema.py` (`CanonicalStoreLock`, `checkpoint_wal_strict`, sidecar helpers),
  `writer.py`, `server.py`, `writer_service.py`, `backup.py`
  (`MANIFEST_SCHEMA_VERSION`, `hash_file_sha256`, `verify_backup_manifest`),
  `recovery.py`, `rebuild.py`, `healthcheck.py`, the read-only
  `app/opip/data_platform/retention_dry_run.py` preview and the checksum/off-host
  verification precedent in `app/opip/data_platform/archive_lifecycle.py`.

## 3. Rolling

State model: exactly one **active file** plus zero or more **sealed segments**.

- Active file: `opip_canonical_v1.sqlite3`, the current name, so the socket, the
  lock, `capture_gap_spool.json` and `writer_service.py` startup stay unchanged.
- Sealed segment: `opip_canonical_v1_<key>.sqlite3`, where `<key>` is a UTC day
  (`YYYYMMDD`) for daily rolls or an ISO week (`YYYY-Www`) for weekly rolls. Keys
  sort lexicographically in time order. The file is sealed by rename inside the same
  directory (atomic, no copy, no extra space), never by rewriting in place.
- Segment catalog: `opip_canonical_segments.json` in `opip/canonical`, schema-
  versioned, listing each sealed segment with key, file name, byte size, SHA-256,
  schema version, first and last event time, state (`SEALED`, `SHIPPING`, `SHIPPED`,
  `VERIFIED`, `DELETED`, `QUARANTINED`) and its receipt path. The catalog is written
  and `fsync`ed before the new active file opens.
- Roll procedure inside the one writer process: drain the writer queue, stop
  accepting new transactions for the boundary instant, `checkpoint_wal_strict`, run
  the existing sidecar cleanup and `fsync` the file and directory, append the
  catalog entry, rename the active file to its sealed key, `fsync` the directory,
  then create and validate a fresh active file and resume the queue. Because the
  writer owns the lock throughout, no second writer exists at any instant.
- Roll cadence: the boundary is a UTC period (day or week) evaluated at a transaction
  boundary, not a wall-clock timer thread. The cadence belongs in the existing
  schema-driven config registry as a canonical/retention domain value; a checked-in
  constant is the default until that slice exists.
- Cutover of today's 5.5 GB file: the first roll is a one-time step. With the writer
  stopped, checkpoint the WAL, seal the existing file under a fixed cutover key,
  record it in the catalog, then start the writer on a fresh active file. Because the
  seal is a rename, it needs no extra 5.5 GB and takes seconds rather than minutes.
- Sequence continuity: the `history_epoch`, `next_local_sequence` and
  `max_local_sequence` facts recorded in the backup manifest must remain meaningful
  across files. Continuation is an explicit design decision for the writer slice,
  not an accident of rollover.

## 4. Shipping to the data box

- A sealed segment is shippable only after its catalog entry is `SEALED` and its
  WAL and sidecars are gone. The transfer unit is the sealed file plus its catalog
  entry (hash, size, schema version, time range), never the active file.
- Transfer reuses the existing operations data path, the host-to-host copy mechanism
  the owner already uses for off-host evidence. No new control plane, no new
  scheduler, no analytics database on the trading host, and no Kraken private API on
  the data box.
- Landing layout on the data box mirrors the current dataset convention, for example
  `<data-box-root>/opip/canonical/opip_canonical_v1_<key>.sqlite3` with the catalog
  entry beside it and a per-segment receipt.
- Shipping is idempotent and resumable: a partial transfer never flips state, a retry
  compares size and hash and either finishes or starts over, and an interrupted
  transfer leaves the segment `SEALED` so retention cannot delete it.
- The data box receives evidence, not authority. It holds no writer socket, no
  production execution credential and no ability to admit, execute or mutate live
  orders. Deep-history reads and any Postgres ingest are separate later slices.
- Shipping volume is small and bounded: one file per day or week, roughly 250 MB per
  segment at the audit's estimated daily growth. A failed ship is visible and
  retryable; it never blocks the writer or the scan path.

## 5. Hash verification

- On seal, the writer computes SHA-256 over the sealed file after the final
  checkpoint, sidecar removal and `fsync`, and records hash and byte size in the
  catalog entry before the transfer starts. The checksum is evidence metadata, not a
  secret.
- On the data box, the receiver re-computes SHA-256 and byte size of the landed file
  and compares both with the catalog entry. Only an exact match writes a receipt
  (`<segment>.receipt.json` with hash, size, key, receive time and verifier version)
  and moves the state to `VERIFIED`.
- A mismatch, a short file, an unreadable file or a missing receipt is
  `QUARANTINED`: the segment stays on the trading host, the state is durable and
  deletion is refused. A quarantine is a stop condition, not a warning.
- The receipt is the only authority for deletion. No receipt, no delete, regardless
  of age or disk pressure.
- Whether the writer already keeps its own per-row hash chain is **unchecked** (audit
  section 9). If it does, the segment hash must be reconciled with that chain rather
  than replace it; that reconciliation is an open question below, not an assumption.
- Existing helpers to reuse: `hash_file_sha256`, `MANIFEST_SCHEMA_VERSION` and
  `verify_backup_manifest` in `app/opip/canonical/backup.py`, and the
  `checksum_verified` / `offhost_verified` / `cleanup_eligible` gate shape in
  `app/opip/data_platform/archive_lifecycle.py`.

## 6. Retention

Deletion becomes possible only when **all** of these hold for one sealed segment:

1. state is `VERIFIED` with a durable receipt from the data box,
2. the segment is older than the dataset hot window (14 days by default, floor 7,
   from `retention_policy.py`),
3. the segment is not the active file and not the newest sealed segment a reader
   still needs for continuity,
4. the read-only dry-run preview lists it as a candidate on the same host, and
5. retention deletion is explicitly enabled by an owner-approved slice
   (`opip_enable_retention_delete` today is `false`).

Retention rules that do not change:

- No row deletion and no `VACUUM` on the active file. The unit of retention becomes
  the sealed segment.
- The active file is never a retention candidate, whatever its age or size.
- A quarantined, shipping or unverified segment is never a candidate.
- The dry-run report (`app/opip/data_platform/retention_dry_run.py`) stays read-only
  and must be extended to show segment states, ages and blockers before any deletion
  slice is proposed.
- Catalog entries and receipts are tombstoned, never removed. Deleting a segment
  leaves the durable record of what existed, its hash and where it was verified.
- The data box copy is the retention target, not a cache: deleting the trading-host
  copy is safe only because the verified copy exists.

## 7. Reader changes

Readers must span the active file plus the sealed, still-present segments.

- One resolver, one order: a read enumerates the catalog in key order, appends the
  active file last, opens each file read-only with the existing connection helpers,
  and merges rows in event-time and sequence order. File order is deterministic, so
  a repeated read returns the same order.
- Reads never write: no reader opens a sealed segment for writing, runs
  `checkpoint_wal_strict`, migrates a schema or repairs a file.
- Point-in-time reads must not straddle a roll boundary silently: a query pinned to
  an instant includes only segments whose recorded time range covers it, and the
  answer states which segments it read.
- Missing segment, unreadable segment or catalog/file disagreement fails closed with
  an explicit error rather than returning a shorter history. A segment in `DELETED`
  state with a receipt is the only accepted absence, and only for history older than
  the hot window.
- Consumers that must be audited in the reader slice (currently single-file
  assumptions): `app/opip/canonical/client.py`, `decision_context_bridge.py`,
  `app/api/cockpit.py`, `app/api/control_center.py`,
  `app/opip/learning/canonical_replica.py`,
  `app/opip/learning/paper_outcome_reader.py`,
  `app/jobs/capture_feasibility_evidence_shadow.py` (`CommittedSnapshotReader`),
  `app/services/release_runtime_verifier.py` (`CanonicalWriter.for_reads`) and
  `app/opip/decision_intelligence/evidence_reader.py`.
- Writer-side reads (`for_reads`), backups, rebuild, recovery and healthcheck stay on
  the active file unless a slice proves they need sealed segments; each one is
  listed with its disposition so no consumer is left silently reading only the
  newest file.
- Operator surface: `app/api/control_center.py` reports database size today; the Ops
  Center page for segment states and retention is a later slice.

## 8. Failure modes and rollback

| Failure | Behavior |
|---|---|
| Crash before the sealed rename | Active file and WAL are intact; the catalog has no entry; recovery opens the active file unchanged. |
| Crash after the sealed rename, before the fresh active file | Recovery finds a catalog entry marked `SEALED`, validates the sealed file and creates an empty active file; it never reopens the sealed file for writing. |
| Crash during transfer | The segment stays `SEALED`; retention refuses deletion; retry is idempotent. |
| Hash mismatch on the data box | `QUARANTINED`; the local copy is kept; no deletion; evidence is preserved for review. |
| Disk pressure during a roll | The roll aborts before the rename; the active file keeps serving; the failure is logged and surfaced. |
| Data box unavailable | Shipping fails closed; segments stay `SEALED`; deletion stays blocked; the writer is unaffected. |
| Clock skew at the UTC boundary | The boundary is evaluated at a transaction boundary against UTC; a skewed host can delay a roll but cannot create two active files or lose a transaction. |
| A reader meets a missing segment | Explicit error naming the segment and catalog state; no partial result. |

Rollback: a checked-in mode flag (default `off`) restores today's single-file writer
behavior; readers accept both the single-file and segmented layouts; sealed segments
remain readable. No rollback step deletes evidence, and no rollback step requires
rewriting a sealed file.

## 9. Open questions for the owner

1. Day or week cadence for the roll, and does the cadence differ from the 14-day hot
   window policy?
2. Does the writer already keep its own per-row hash chain, and must the segment hash
   reconcile with it? The audit records this as **unchecked**.
3. How are `history_epoch`, `next_local_sequence` and `max_local_sequence` continued
   across segments and across the cutover of today's 5.5 GB file?
4. Is the one-time cutover allowed a short writer stop, or must it be online? A
   stopped-writer rename takes seconds; an online roll needs a second durable copy.
5. Is the segment file, the whole `opip/` dataset, or both the transfer unit, and
   which existing off-host path and credentials carry it?
6. Who may enable retention deletion, and does that need a separate owner enablement
   packet like the existing F6 pattern?
7. Is "split the existing 5.5 GB file by date on the data box" still required, or is
   per-segment history enough once the cutover file is sealed?
8. What is the allowed read amplification when a query must merge many segments, and
   is there a bounded query window for the trading host?
9. Do backups keep taking only the active file, or must a backup include sealed
   segments that are still inside the hot window?
10. Does the release-provenance and drift check need to see per-segment schema
    versions, or is the catalog's schema version enough?
11. Where does the catalog live if the canonical directory is lost: is the data-box
    receipt set the recovery source of truth for sealed history?
12. After a roll, what evidence proves the I/O-wait hypothesis (page-cache pressure)
    and what threshold decides whether rolling stays daily or moves to weekly?

## 10. Slices this design needs

Each slice needs its own scope contract, acceptance tests, owner approval and the
existing pytest and `compileall` gates. No slice in this list is authorized by this
note.

1. **Paths and catalog (additive, pure).** A pure segment-key/catalog model plus a
   resolver for the active file and sealed names, with no writer or reader wiring and
   no file mutation. Proves key ordering, schema version and state transitions.
2. **Writer roll behind a default-off flag.** Queue drain, checkpoint, sidecar
   removal, `fsync`, catalog append, atomic rename, fresh active file, and the
   one-time cutover of today's file. Flag off keeps today's behavior byte-identical.
3. **Roll observability.** Read-only reports of active and sealed sizes, roll
   durations and I/O wait, so the page-cache hypothesis is measured rather than
   assumed.
4. **Segment-spanning readers.** The single resolver, deterministic order,
   pinned-time semantics and fail-closed absence rules, applied to the audited
   consumer list.
5. **Shipping dry-run.** Compute, display and compare hashes and sizes, and write
   receipts to a temporary location only; no transfer, no deletion, no state change.
6. **Shipping execution.** Transfer sealed segments, verify on the data box, record
   receipts and quarantine mismatches. Data movement; needs explicit owner approval.
7. **Retention eligibility and deletion.** Extend the read-only dry-run to segment
   states and blockers, then, in a separate owner-enabled slice, delete only
   `VERIFIED` segments past the hot window.
8. **Ops Center segment page.** Surface catalog states, receipts, hot-window ages and
   blockers through the existing configuration registry and `control-center-v2`.
   Extend them; do not build a second config system.
9. **Data-box history.** One-time split of the cutover file and deeper-than-hot-window
   queries on the data box, including whether Postgres ingest is worth it.

Dependencies: 2 needs 1; 4 needs 2; 5 needs 2; 6 needs 5; 7 needs 1, 4, 5 and 6; 8
needs 1 and 2; 3 runs beside 2 and informs question 12.

## 11. What this note does not do

- It does not change the writer, the readers, the schema, the catalog, the retention
  policy, the dry-run report, compose, deploy or any workflow.
- It does not ship, move, compress or delete any byte of canonical data.
- It does not enable retention deletion or widen paper or production admission.
- It does not replace the audit, the v6 spec or any approved scope contract; those
  remain the authority for what may be built.
