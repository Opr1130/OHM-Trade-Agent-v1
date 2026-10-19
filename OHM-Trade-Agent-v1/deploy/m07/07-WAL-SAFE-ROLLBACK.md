# Mission 07 R2 — WAL-Safe Rollback

## Why the old approach was wrong

The delivered rollback copied `data/opip/canonical/` with `cp -a` after stopping
the container. With SQLite in WAL mode that is not a snapshot:

* the `-wal` file may hold committed transactions not yet checkpointed;
* `-shm` is a shared-memory index whose lifetime is not tied to the main file;
* `cp` order is not atomic across the three files, so a crash mid-copy leaves a
  directory that looks complete but is not restorable.

Stopping a container is also not proof of quiescence: another process, a cron
job or a second container can still hold a write lock.

## What R2 does instead

`scripts/opip_rollback_guard.py` provides two operations and a gate.

### 1. `snapshot` — consistent copy under quiescence

```
python3 scripts/opip_rollback_guard.py snapshot \
  --db   /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1/data/opip/canonical/opip_canonical_v1.sqlite3 \
  --out  /var/lib/opip-cutover-evidence/<utc>/opip_canonical_v1.sqlite3
```

* opens the source read-only and copies through SQLite's **online backup API**,
  which checkpoints WAL content into a self-consistent destination;
* runs `PRAGMA integrity_check` on the **snapshot**, not the source;
* runs `PRAGMA foreign_key_check` and requires zero violations;
* refuses an existing destination (never overwrite evidence);
* records `sha256`, `history_epoch`, `next_local_sequence`, `event_count`.

The shell wrapper (`rollback-v2.sh`) additionally:

1. stops `opip-canonical-writer` and asserts the running count is exactly `0`;
2. only then calls `snapshot`;
3. writes `snapshot-facts.json` next to the snapshot;
4. refuses to continue if the snapshot file is missing.

### 2. `compare` — refuse newer history, not just a newer epoch

```
python3 scripts/opip_rollback_guard.py compare --plan <ROLLBACK_PLAN.json> --db <live db>
```

Exit 3 and `verdict=REFUSED` if the live database advanced in **any** of:

| Dimension | Example refused |
|---|---|
| `history_epoch` | live 3 > plan 2 |
| `next_local_sequence` | live 4 > plan 3 at the same epoch |
| `event_count` | live 5 > plan 4 at the same epoch/sequence |
| `integrity_check` | anything other than `ok` |
| `foreign_key_violations` | non-zero |

The plan is produced by `--mode prepare`, which reads those values from the
**destination database on the host** (never from an owner-typed number).

### 3. `writer-gate` — only a bound PASS receipt starts a writer

```
python3 scripts/opip_rollback_guard.py writer-gate --receipt <recovery receipt> --db <live db>
```

PASS requires: `completed=true`, `verdict=PASS`, `destination_db` equal to the
live path, `restored.*` equal to the live epoch/sequence/event-count, receipt
integrity `ok`, zero FK violations, and an age within 24 hours. Anything else
returns REFUSED with the specific mismatch.

## Rollback kinds stay separate

| Kind | Changes | Not allowed |
|---|---|---|
| `safe-baseline` | release profile overlay only | no data change |
| `code` | previous **immutable image id** only | no data change |
| `data` | refused in this script | must use the native canonical restore + receipt |

## Remaining limitation (BLOCKED)

Restoring a snapshot on a disposable Linux host has **not** been rehearsed here
(no container runtime / no authorised live access). Until L-07 is cleared, this
document describes a locally unit-tested implementation, not a certified
recovery procedure.
