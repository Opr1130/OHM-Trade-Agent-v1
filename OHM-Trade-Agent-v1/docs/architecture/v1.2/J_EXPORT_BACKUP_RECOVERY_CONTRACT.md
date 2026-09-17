# J. Export / backup / recovery contract

## Export

- Export objects are published and verified **before** their manifest / watermark is committed.
- Consumers read **committed manifests only**.
- Export retry is idempotent.
- Current as-built path: `deploy/remote/export-opip-learning-evidence.sh` every 2 minutes to `/var/lib/opip-learning-export`.

See [fixtures/export_manifest.example.json](fixtures/export_manifest.example.json).

## Backup

- When the operational SQLite exists, backups must be **SQLite-consistent snapshots**, not ordinary copies of an active database.
- No specific backup vendor is mandatory in PR 1.
- Litestream is optional and is selected later from RPO/RTO, restore testing, and operating cost.
- Existing PostgreSQL dump/restore on the analytics plane does **not** satisfy the operational SQLite backup requirement.

## Provisional recovery targets (N9)

| Target | Provisional value | Status |
| --- | --- | --- |
| RPO | ≤ 5 minutes | Subject to PR 2 restore drill |
| RTO | ≤ 30 minutes | Subject to PR 2 restore drill |

## Measurement plan (PR 2+)

Measure and record:

- export object age vs committed manifest
- backup snapshot age
- restore-drill elapsed time
- writer transaction p99 and protection queue age (N8)
- WAL / disk growth

## Projection rebuild

Rebuild of a fresh read model from a named watermark is a supported, tested operation (implemented after the writer exists).

## Learning node

The learning node reads verified immutable exports only. It never opens the live operational SQLite file over the network.

## Canonical learning replica (PR-A)

Terminal paper economic outcomes reach the learning plane as a verified
copy-only **read-only replica**, carried inside the same export generation as
the other artifacts.

- The snapshot is produced with the PR-A0 SQLite online-backup path and
  normalized to a self-contained rollback-journal artifact. A raw copy of the
  live WAL store is never used, because its completeness would depend on a
  sidecar.
- `manifest.env` remains the generation commit marker and carries
  `canonical_learning_replica_version=1` with a tree byte count and digest. It is
  written last; the marker is absent when no authoritative deployed SHA exists,
  so provenance is never minted for an unknown release.
- The forced reader holds its shared publish lock across the marker inspection
  and the tar emission, so the bundle and `manifest.env` always originate from
  one committed generation.
- On the learning node, the outer transport digest and the inner evidence
  provenance are validated as **independent** layers: a bundle can be internally
  consistent at the tar layer and still be rejected as invalid evidence. Inner
  verification delegates to the canonical replica module inside the configured
  learning image.
- Installations are immutable generations addressed by an atomically replaced
  `current` pointer, retaining the active generation plus one previous
  known-good. A failed install leaves the previous `current` intact.
- Authority is bounded: production canonical SQLite remains the only economic
  evidence authority. `paper_trading/state.json` and
  `evidence_gap_spool.json` travel as **completeness companions** and must come
  from the same generation. The learning replica is never a second authority and
  never grants execution authority.

Cadence: export ≈ 2 minutes, sync ≈ 2 minutes, freshness limit 1800 seconds.

See [deploy/learning/README.md](../../../deploy/learning/README.md) for the
operational detail.
