# O'Pip Core Release Provenance Reconciliation

## Why this exists

The learning exporter publishes `production_deployed_sha` from
`/var/lib/ohm-deploy/last-good-sha`. That file is written by the ordinary
`/deploy` controller at its core release boundary.

A **qualified core-only activation** can legitimately run a new, correctly
labelled, healthy core image without executing that controller path. The
exporter then publishes an empty SHA and the learning worker fails closed
(`UNVERIFIED` / blocked compute). That is correct behaviour, but it leaves no
supported way to restore provenance without redeploying the healthy core and
risking the canonical writer.

## What the reconciler trusts

`app.services.release_provenance_reconcile` reconstructs only the missing fact,
from evidence that already exists on the host:

| Evidence | Bound fact |
| --- | --- |
| `docker inspect ohm-trade-agent` | immutable image ID + `org.opencontainers.image.revision` |
| `LIVE-ACCEPTANCE.json` | accepted app SHA, core image, writer image, authority profile, DB baseline |
| `CORE-UPGRADE-*.json` | append-only qualification, candidate image, merged main SHA, preserved writer identity |
| `release-manifest.sha256` | every deployed release-tree file checksum |
| `docker inspect opip-canonical-writer` | writer image and container identity unchanged |
| canonical SQLite (read-only) | journal mode, integrity, foreign keys, epoch and sequence continuity |

Every source must agree. A single disagreement is a refusal.

## What it never does

- never invokes `/deploy`, Docker actions, systemd actions, or migrations;
- never rewrites, truncates or deletes a historical receipt;
- never overwrites a different existing `last-good-sha`;
- never changes trading authority, Paper V2, the Committee or AI settings.

## Operation

Dry run (default, no writes):

```bash
cd /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1
PYTHONPATH=. python3 -m app.services.release_provenance_reconcile \
  --expected-sha <40-char-main-sha>
```

Apply, only after owner approval of the exact SHA:

```bash
cd /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1
PYTHONPATH=. python3 -m app.services.release_provenance_reconcile \
  --expected-sha <40-char-main-sha> --apply
```

Apply requires root and writes:

```
/var/lib/ohm-deploy/last-good-sha                              (0600, atomic)
/var/lib/ohm-deploy/provenance/reconciled/<utc>-<sha>-reconciled.json
/var/lib/ohm-deploy/provenance/rejected/<utc>-<sha>-refused.json   (on refusal)
```

Receipts are created with `O_EXCL`; they are append-only. The exporter reads
`last-good-sha` on its normal cadence, so no restart is required.

## Refusal semantics

Any failed check returns non-zero, publishes no `last-good-sha`, and records a
rejection receipt. Restore the missing evidence and re-run; never hand-write
`last-good-sha` and never edit a receipt to make a check pass.
