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
