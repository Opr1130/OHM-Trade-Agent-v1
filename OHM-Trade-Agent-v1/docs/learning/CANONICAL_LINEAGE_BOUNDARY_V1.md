# O'Pip Canonical Lineage Boundary and Archival Policy (v1)

## Purpose

Two canonical event histories exist on the learning plane. They must never be
merged, relabelled or silently deleted. This policy defines the boundary, the
only permitted archival procedure, and how consumers must treat each lineage.

## Lineages

| Lineage ID | Origin | Evidence |
| --- | --- | --- |
| `LINEAGE-A-LEGACY` | Pre-V2 production store, source release `8b3cc2712432ca21007be4db0667301d48b89d97` | Learning replica generation `gen-b1f2c171c5c64601`, 220,721 events, 1.22 GB, snapshot `2026-10-19T04:46:15Z` |
| `LINEAGE-B-V2-GENESIS` | V2 production store created from fresh genesis, source release `a26d664972075b2a220a466215cf71e389decb86` | Production canonical epoch 1, sequence starting at 1, live since `2026-10-21T02:26:52Z` |

The boundary is the V2 fresh genesis. Sequence numbers, event counts and
receipts from one lineage are **not comparable** to the other.

## Prohibitions

1. Never merge, append, backfill or reconcile events across lineages.
2. Never relabel a `LINEAGE-A` event, sequence or receipt as current.
3. Never delete, truncate, rewrite or garbage-collect the last generation of a
   non-current lineage.
4. Never let the worker's `current` pointer claim a lineage different from the
   generation it names.
5. Never join outcome or accountability records across lineages.

## Archival policy

Before a generation from `LINEAGE-A` can be pruned by ordinary retention, it
must be archived:

1. Owner approval names the exact generation ID and its manifest SHA-256.
2. Copy (never move) the generation into
   `/var/lib/opip-learning/canonical-replica/archive/<lineage-id>/<generation-id>/`.
3. Write `archive_manifest.json` containing lineage ID, source release SHA,
   generation ID, every file's size and SHA-256, and the original
   `replica_manifest.json` digest.
4. Write an append-only receipt under
   `/var/lib/opip-learning/state/recovery-archives/` with `0600` permissions,
   `O_EXCL` creation, file and directory `fsync`, and
   `historical_evidence_modified=false`.
5. Verify the archive by re-hashing every file; only then may the owner approve
   pruning the live generation.

An interrupted archive is never treated as complete and never authorises
pruning.

## Consumer rules

- Every replica generation carries `lineage_id` and `source_release_sha`.
- `current` may point only within the active lineage; a cross-lineage pointer
  change requires a new owner-approved migration receipt.
- Learning jobs must record the lineage ID in their dispositions and must fail
  closed if the lineage of the current generation is unknown.
- Analytics and trust reports must present lineage-separated counts; a combined
  count must be labelled as such and never used as a continuous time series.
