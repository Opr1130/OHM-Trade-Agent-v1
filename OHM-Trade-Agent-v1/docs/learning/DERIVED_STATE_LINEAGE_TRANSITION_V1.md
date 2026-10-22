# Derived-State Lineage Transition (v1)

## Scope

This document records the corrective design for the partially committed O'Pip
learning recovery. It does not authorise a production migration by itself.

Production application release: `a26d664972075b2a220a466215cf71e389decb86`
Tooling release: the exact merged corrective PR head (CI-certified)
Current V2 replica: `gen-bd1613ddd16a449b`

The production core and canonical writer are out of scope. No timer, AI
inference, Paper V2, Committee or trading authority change is permitted.

## Observed failure

The first recovery committed `gen-bd1613ddd16a449b` and synchronized the V2
inputs, but the outcomes job failed closed:

```
SNAPSHOT_LEDGER_TRUNCATED
app/jobs/build_phase3c_forward_outcomes.py:1408
```

The worker retained legacy SQLite sidecars and local ledgers while sync replaced
the input files they referenced. The offsets were still valid numbers, but no
longer valid identities.

## Consumer dependency matrix

| Input artifact | Derived output | SQLite state / cursor | Stored checkpoint / anchor | Lineage identity | Replay / failure behavior | Pending work | Retention |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `p1_evidence_ledger.jsonl` | `phase3c_forward_outcomes.jsonl` | `.phase3c_forward_outcomes.jsonl.state.sqlite3` (`snapshot_indexed_offset`, `output_indexed_offset`) | 4 KiB anchor hash per offset | None in legacy DB | Fails `SNAPSHOT_LEDGER_TRUNCATED` / `LEDGER_CHECKPOINT_SHORT_READ`; never silently replays | `snapshot_queue`, `accountability_handoff` | append-only outputs; state DB retained |
| `opip/qualification/screening_evaluations.jsonl` | `opip/discovery/forward_outcomes.jsonl`, attributions | `opip/discovery/.forward_outcomes.jsonl.state.sqlite3` (`screening_indexed_offset`) | 4 KiB anchor hash | None in legacy DB | Fails `DISCOVERY_SCREENING_LEDGER_TRUNCATED` / `DIVERGED`; bounded job converts to an error result | `observation_queue`, attribution pending | append-only outputs; state DB retained |
| `paper_trading/events.jsonl` | accountability summary / ledger | `opip/.opportunity_accountability.jsonl.state.sqlite3` (`paper_event_offset`) | SHA-256 anchor | None in legacy DB | **Truncation silently resets offset and deletes `latest`/`signal_index`**; pending paper events are not acknowledged, but legacy rows are lost from active view | `paper_event_pending` | summary and ledger append-only |
| `opip/qualification/screening_evaluations.jsonl` | discovery screening index | `.p1_evidence_ledger.jsonl.dedup.sqlite3` (`indexed_offset`) for P1 dedup | 4 KiB anchor hash | None in legacy DB | Fails with offset/anchor errors | snapshot ids | index only |
| sync input replacement | ML capture state | `opip_ml_capture_checkpoint.json` | checkpoint line/offset | None in legacy JSON | May restart from zero or skip by stale line | capture queue | checkpoint overwritten by job |
| sync input replacement | consumer dispositions | `.learning_consumption/*.json` | summary JSON | None | Could present legacy dispositions as current | outcomes/reconcile summary | append-only summaries |
| sync input replacement | coverage handling | `.learning_coverage/*` | one-shot env/JSON | None | Could apply a legacy one-shot to V2 | coverage discontinuity | durable evidence |

## Verified legacy state on the affected host

The live worker contains legacy sidecars from 2026-09-02 through 2026-09-27.
The known mismatches at failure time were:

- P1 ledger: legacy offset `133851714`; incoming V2 ledger `0` bytes.
- Screening: legacy offset `64035638`; incoming V2 screening `47865920` bytes.
- Paper events: legacy offset `4822617`; incoming V2 paper events `0` bytes.
- `latest_outcomes` count in Phase 3C state: 85,000; discovery state: 3,800.
- `paper_event_pending` is present in the accountability sidecar.

These are identity mismatches, not arithmetic errors.

## Historical evidence status

| Artifact | Status |
| --- | --- |
| LINEAGE-A canonical replica `gen-b1f2c171c5c64601` | Preserved in the verified copy-only archive |
| Legacy P1 ledger (`133,851,714` bytes, SHA-256 `4999baafa610d656fa92e4abde22883a370755b754c2bce9900fa8152fb6b36d7` per pre-recovery export) | **Overwritten by the schema-4 empty V2 ledger during the failed recovery; original bytes are not present on the worker.** Record as an explicit evidence gap; do not reconstruct from offsets |
| Legacy Phase 3C output (`phase3c_forward_outcomes.jsonl`, 218,698,467 bytes) | Still present; must be archived before retirement |
| Legacy Phase 3C state DB (352,153,600 bytes) | Still present; contains 85,000 latest outcomes and offsets |
| Legacy discovery output/state | Still present; 41,657,805-byte output and 46,252,032-byte state |
| Legacy accountability state/summary | Still present; 62,222,336-byte state and 1,486-byte summary |
| Legacy consumption/coverage/checkpoint files | Still present |
| Screening `47,865,920`-byte V2 input | Present; current V2 input |
| Paper events `0`-byte V2 input | Present; current V2 input |

The canonical archive does **not** contain the P1 ledger. No claim is made that
the lost P1 bytes can be recovered from offsets, counts or the canonical
replica.

## Corrective architecture

`app/services/learning_derived_state.py` is the single archive/retire boundary.

- Explicit reviewed inventory, not a recursive copy of the data root.
- SQLite snapshots use `Connection.backup()`; WAL/SHM presence and hashes are
  recorded but raw WAL is not treated as a standalone archive.
- Copy to unique staging, hash, verify, atomically publish, then write an
  `O_EXCL` `VERIFIED` receipt with fsync of files and directories.
- `retire` removes only derived active artifacts after the verified receipt;
  inputs are archived only for preservation and remain until sync replaces them.
- `data/.lineage.json` binds the active source release and replica generation.
- Consumer sidecars persist `lineage_id`, `lineage_source_release_sha` and
  `lineage_generation_id`. A populated legacy sidecar without identity fails
  closed; a mismatched identity fails closed.

`deploy/learning/opip-learning-sync.sh` invokes `retire` before replacing inputs
when the incoming source release differs from the marker, and writes
`mark-lineage` only after the data manifest and replica pointer are committed.
It refuses a partially committed host that has V2 inputs but unbound legacy
derived state.

## Partially committed resume (owner-gated)

Do not run this during corrective PR review. After merge and an exact-SHA image
are available, the owner should:

1. Confirm timers disabled, no learning containers, and the V2 replica current.
2. Run `learning_derived_state inspect` and record the inventory.
3. Run `retire` against the already-current V2 replica:
   - data root `/var/lib/opip-learning/data`
   - archive root under `/var/lib/opip-learning/state/derived-lineages/LINEAGE-A-LEGACY/`

## Verified legacy state on the affected host

The live worker contains legacy sidecars from 2026-09-02 through 2026-09-27.
The known mismatches at failure time were:

- P1 ledger: legacy offset `133851714`; incoming V2 ledger `0` bytes.
- Screening: legacy offset `64035638`; incoming V2 screening `47865920` bytes.
- Paper events: legacy offset `4822617`; incoming V2 paper events `0` bytes.
- `latest_outcomes` count in Phase 3C state: 85,000; discovery state: 3,800.
- `paper_event_pending` is present in the accountability sidecar.

These are identity mismatches, not arithmetic errors.

## Historical evidence status

| Artifact | Status |
| --- | --- |
| LINEAGE-A canonical replica `gen-b1f2c171c5c64601` | Preserved in the verified copy-only archive |
| Legacy P1 ledger (`133,851,714` bytes, SHA-256 `4999baafa610d656fa92e4abde22883a370755b754c2bce9900fa8152fb6b36d7` per pre-recovery export) | **Overwritten during the failed recovery; original bytes are not present on the worker.** Record as an explicit gap; do not reconstruct from offsets |
| Legacy Phase 3C output (`phase3c_forward_outcomes.jsonl`, 218,698,467 bytes) | Still present; archive before retirement |
| Legacy Phase 3C state DB (352,153,600 bytes) | Still present; 85,000 latest outcomes and offsets |
| Legacy discovery output/state | Still present; 41,657,805-byte output and 46,252,032-byte state |
| Legacy accountability state/summary | Still present; 62,222,336-byte state and 1,486-byte summary |
| Legacy consumption/coverage/checkpoint files | Still present |
| Screening `47,865,920`-byte V2 input | Present; current V2 input |
| Paper events `0`-byte V2 input | Present; current V2 input |

The canonical archive does **not** contain the P1 ledger. No claim is made that
the lost P1 bytes can be recovered from offsets, counts or the canonical
replica.

## Corrective architecture

`app/services/learning_derived_state.py` is the single archive/retire boundary.

- Explicit reviewed inventory, not a recursive copy of the data root.
- SQLite snapshots use `Connection.backup()`; WAL/SHM presence and hashes are
  recorded but raw WAL is not treated as a standalone archive.
- Copy to unique staging, hash, verify, atomically publish, then write an
  `O_EXCL` `VERIFIED` receipt with fsync of files and directories.
- `retire` removes only derived active artifacts after the verified receipt;
  inputs are archived only for preservation and remain until sync replaces them.
- `data/.lineage.json` binds the active source release and replica generation.
- Consumer sidecars persist `lineage_id`, `lineage_source_release_sha` and
  `lineage_generation_id`. A populated legacy sidecar without identity fails
  closed; a mismatched identity fails closed.

`deploy/learning/opip-learning-sync.sh` invokes `retire` before replacing inputs
when the incoming source release differs from the marker, and writes
`mark-lineage` only after the data manifest and replica pointer are committed.
It refuses a partially committed host that has V2 inputs but unbound legacy
derived state.

## Partially committed resume (owner-gated)

Do not run this during corrective PR review. After merge and an exact-SHA image
are available, the owner should:

1. Confirm timers disabled, no learning containers, and the V2 replica current.
2. Run `learning_derived_state inspect` and record the inventory.
3. Run `retire` against the already-current V2 replica with archive root under
   `/var/lib/opip-learning/state/derived-lineages/LINEAGE-A-LEGACY/`, lineage
   `LINEAGE-A-LEGACY`, source `8b3cc2712432ca21007be4db0667301d48b89d97`,
   generation `gen-b1f2c171c5c64601`, active lineage
   `LINEAGE-B-V2-GENESIS`, reason `PARTIALLY_COMMITTED_RECOVERY`, and a
   known-gap record for the overwritten P1 ledger.
4. Verify the archive read-only; confirm derived files are gone while V2 inputs
   and the canonical replica remain.
5. Write the V2 marker with source `a26d6649…` and generation
   `gen-bd1613ddd16a449b`.
6. Re-run `/deploy-learning-recovery a26d6649…`; outcomes must reach
   `CONSUMED_OK`/`CONSUMED_EMPTY` with `CURRENT` before timers are enabled.

Never hand-edit a SQLite offset, fabricate a ledger, or mark the P1 gap as
verified.

## Binding receipt correction

`app/services/learning_recovery_receipt.py` writes a `PREPARED` intent with
`O_EXCL` before the environment references it. Success writes a separate
`CERTIFIED` terminal; failure writes a separate `BLOCKED` terminal linked to
`failure.json`. A prior missing binding is materialized only as `BLOCKED` from
existing failure evidence. Historical receipts are never edited.

## Test and CI evidence

Focused tests cover offset truncation, equal-size/different-lineage content,
anchors, empty V2 streams, pending accountability, unknown lineage, missing
manifest, interrupted and partial archives, checksums, insufficient disk, WAL
snapshot, consumer binding, crash boundaries, idempotency, and receipt ordering.
Linux CI is the authoritative full-suite gate.

   - lineage `LINEAGE-A-LEGACY`, source `8b3cc2712432ca21007be4db0667301d48b89d97`
   - generation `gen-b1f2c171c5c64601`
   - active lineage `LINEAGE-B-V2-GENESIS`, reason `PARTIALLY_COMMITTED_RECOVERY`
   - a known-gap record for the overwritten P1 ledger
4. Verify the archive read-only; confirm derived files are gone while V2 inputs
   and the canonical replica remain.
5. Write the V2 marker with source `a26d6649…` and generation
   `gen-bd1613ddd16a449b`.
6. Re-run `/deploy-learning-recovery a26d6649…`; outcomes must reach
   `CONSUMED_OK`/`CONSUMED_EMPTY` with `CURRENT` before timers are enabled.

Never hand-edit a SQLite offset, fabricate a ledger, or mark the P1 gap as
verified.

## Binding receipt correction

`app/services/learning_recovery_receipt.py` writes a `PREPARED` intent with
`O_EXCL` before the environment references it. Success writes a separate
`CERTIFIED` terminal; failure writes a separate `BLOCKED` terminal linked to
`failure.json`. A prior missing binding is materialized only as `BLOCKED` from
existing failure evidence. Historical receipts are never edited.

## Test and CI evidence

Focused tests cover offset truncation, equal-size/different-lineage content,
anchors, empty V2 streams, pending accountability, unknown lineage, missing
manifest, interrupted and partial archives, checksums, insufficient disk, WAL
snapshot, consumer binding, crash boundaries, idempotency, and receipt ordering.
Linux CI is the authoritative full-suite gate.
