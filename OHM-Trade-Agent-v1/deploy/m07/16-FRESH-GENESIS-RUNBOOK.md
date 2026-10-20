# M07 R5 — Fresh canonical genesis runbook (separate fresh-start route)

Status: **HOLD — implementation only.** This route is engineering-ready and
reviewed, but nothing here is authorized. Executing any phase requires the owner
gates in `08-OWNER-GATES.csv`, the release prerequisites in
`17-FRESH-GENESIS-READINESS.csv` and an explicit owner instruction. It grants no
deployment, writer, scheduler, paper-v2 or funded authority by itself.

## Route selection (owner decision, already taken)

| Route | Canonical history | Status |
|---|---|---|
| **Fresh start (this document)** | New, empty V2 history; epoch `1`, sequence `1`, zero events | **Selected.** Inactive until the owner gates below are cleared |
| Historical migration (`06-SINGLE-CUTOVER-RUNBOOK.md` phases 4–5) | 220,721 V1 events migrated through bundle export → native recovery | **Available but inactive.** Do not execute |

Owner decisions this route implements:

* V1 is powered off; V1 data and backups are preserved as **historical archives**
  and are never imported, merged, rewritten, moved or deleted here.
* The 220,721 historical canonical events are **not** migrated.
* No historical learning, calibration or runtime state is imported.
* The V1 archive application-level restore drill is deferred for separate owner
  consideration; V1 archive preservation remains mandatory. This decision does
  **not** waive V2 backup and restore verification before genesis.
* PAPER_V2 and funded trading remain disabled (`OPIP_PAPER_V2_MODE=off`,
  funded authority absent).
* No live deployment, no service start, no writer/scheduler activation and no
  trading is part of this runbook's delivery.

The two routes are never mixed: the writer gate accepts exactly one authorising
receipt, and the fresh-start route is selected explicitly with
`--genesis-receipt <path>`. Omitting that flag keeps the historical
native-recovery route, so the migration route cannot be silently replaced.

## What "fresh genesis" is, and is not

`scripts/opip_genesis.py fresh-genesis` creates a **brand-new, empty** SQLite
canonical store at the certified destination and publishes a genesis receipt.

* It never copies, imports, merges, migrates or "cleans" a database. The tool has
  no option that can name a source bundle, archive, learning file or calibration
  file.
* Schema initialization is delegated to the **certified** writer
  (`app.opip.canonical.writer.CanonicalWriter` →
  `app.opip.canonical.schema.initialize_schema`). The toolkit adds no DDL and no
  second database writer.
* Publication is a **no-replace claim** (`os.link`). An existing store, a
  sidecar pair, an unknown file in the canonical directory, a live writer or an
  existing receipt makes the operation refuse; nothing is overwritten, adopted or
  deleted.
* The receipt is *evidence*, not authority: writer activation re-derives the
  store bytes, schema fingerprint, coordinates, integrity, foreign keys, certified
  source module digests, host identity and receipt age before accepting it.

## Preconditions (all read-only, all required)

1. `$APP_ROOT` is the verified release at the certified SHA
   `c196dd674678b3b1ee91c351ee97c1c9e2b3938d`, passing
   `--mode check-release` (`SOURCE_IMAGE` in the readiness table).
2. `--mode prepare` and `--mode authorize` passed for this host and SHA
   (`CONFIG_PROTECTION`).
3. A **verified recoverable V2 baseline** exists before the first canonical
   mutation (`RECOVERABLE_BACKUP`): a completed V2 backup or snapshot and an
   independently verified recovery on an isolated, non-trading destination.
   A configured backup schedule or a pending backup window is **not** proof of
   recovery. Preserve V2 backup coverage as new canonical history accumulates.
   The V1 historical-archive application-level restore drill is **deferred by
   owner decision** and is not a V2 fresh-start launch prerequisite; V1 stays
   powered off and its archives remain unchanged.
4. Resource limits for the V2 stack are the approved values for this droplet
   (`RESOURCE_LIMITS`); an unbounded container is a blocking prerequisite.
5. Health and evidence collection is configured and readable before the writer
   starts (`HEALTH_EVIDENCE`).
6. `/var/lib/opip-bootstrap/` is owned `root:root` mode `0700`; the receipt path
   must live outside the canonical store directory.
7. The M07 toolkit is provisioned at `/opt/opip-m07-toolkit` and passes
   `--mode check-toolkit`; `opip_genesis.py` is a required toolkit helper and is
   covered by `TOOLKIT-MANIFEST.sha256`.
8. The canonical directory `$APP_ROOT/data/opip/canonical/` either does not exist
   or is empty. Any prior store, `-wal`/`-shm`/`-journal` file, export or unknown
   file must be investigated by the owner first — genesis refuses to touch it.
9. The learning plane is deployed at the **exact same SHA** as production
   (exact-SHA compatibility) and does not expect a populated canonical history
   (`LEARNING_EXACT_SHA`). A learning worker that requires historical canonical
   rows, historical learning or old calibration is a blocking prerequisite: the
   new V2 store starts empty by owner decision, so nothing may wait on V1 data.


## Phase A — Prove the destination is fresh (read-only)

```bash
sudo -n /opt/opip-m07-toolkit/scripts/v2/bootstrap-v2.sh --mode check-toolkit
sudo -n /opt/opip-m07-toolkit/scripts/v2/bootstrap-v2.sh --mode check-release
sudo -n ls -la "$APP_ROOT/data/opip/canonical/"    # expect: absent or empty
sudo -n ss -ltnp | grep -F 'writer.sock' || true    # expect: no live writer
```

A non-empty canonical directory stops here. Do not delete anything: an existing
database is either the V1-derived store (migration route) or an interrupted
operation that the owner must disposition.

## Phase B — Owner-authorized fresh genesis (single mutation)

```bash
sudo -n python3 /opt/opip-m07-toolkit/scripts/opip_genesis.py fresh-genesis \
  --app-root "$APP_ROOT" \
  --live-db "$APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3" \
  --confirm-destination "$APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3" \
  --certified-sha c196dd674678b3b1ee91c351ee97c1c9e2b3938d \
  --expected-host 610307364 \
  --authorize \
  --receipt-out /var/lib/opip-bootstrap/canonical-fresh-genesis-receipt.json
```

Exit codes: `0` PASS · `1` FAIL (initialization failure; no store published) ·
`2` PLATFORM_BLOCKED / INSUFFICIENT · `3` REFUSED (precondition not met).

`--confirm-destination` and `--receipt-out` are **mandatory**. Omitting either one
is a refusal *before any mutation*: the canonical directory, the database and
every evidence file stay absent, because a store created without an operator
confirmation or without authoritative evidence cannot be admitted by the writer
gate and cannot be replaced by a retry. The confirmation must name the exact
`--live-db` path; a merely well-shaped path is not a confirmation.

The metadata identity endpoint is not a command-line option. The tool always
reads `http://169.254.169.254/metadata/v1/id`, so nobody can point the identity
read at a service they control. `--expected-host 610307364` is the value that
pinned endpoint must return.

The command refuses, with the failing check named in its JSON output:

| Check | Meaning |
|---|---|
| `AUTHORIZATION` | `--authorize` missing |
| `RECEIPT_REQUIRED` | `--receipt-out` missing (refused before any mutation) |
| `CERTIFIED_SHA` | not the certified application SHA |
| `HOST_IDENTITY_ENDPOINT` | the identity endpoint is not the pinned metadata service |
| `HOST_IDENTITY` | metadata identity unreadable or not `610307364` |
| `DESTINATION_LAYOUT` | not `.../data/opip/canonical/opip_canonical_v1.sqlite3` |
| `DESTINATION_CONFIRMED` | `--confirm-destination` missing or naming another path |
| `RECEIPT_LOCATION` / `RECEIPT_PRESENT` | receipt inside the store dir or already present |
| `EXISTING_CANONICAL_STORE` | a destination database (or symlink) already exists |
| `SQLITE_SIDECARS` | `-wal` / `-shm` / `-journal` present |
| `EXISTING_WRITER` | writer socket present or the store lock is held |
| `INTERRUPTED_GENESIS_ARTIFACT` | a previous genesis staging artifact survived |
| `UNKNOWN_DATABASE_FILE` | any other file in the canonical directory |
| `PLATFORM_DURABILITY` | file/directory durability cannot be proven here |

A refusal writes **nothing** to the store and publishes diagnostic evidence as
`<receipt>.…failed.json` beside the receipt path; the authoritative receipt path
stays free for a corrected retry. A repeated invocation after a successful
genesis therefore refuses on `RECEIPT_PRESENT` and adds one new
`*.failed.json` per attempt, while the canonical database and the authoritative
receipt stay byte-identical — evidence accumulates, canonical state never moves.

## Phase C — Independent verification (read-only)

```bash
sudo -n python3 /opt/opip-m07-toolkit/scripts/opip_genesis.py verify \
  --receipt /var/lib/opip-bootstrap/canonical-fresh-genesis-receipt.json \
  --db "$APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3" \
  --app-root "$APP_ROOT" \
  --expected-host 610307364
```

Expected receipt facts: `history_epoch = 1`, `next_local_sequence = 1`,
`historical_events = 0`, `events_table_rows = 0`, `integrity_check = "ok"`,
`foreign_key_violations = 0`, `history_imported = false`, a schema fingerprint,
the certified module digests, and a `receipt_sha256` self-digest.

## Phase D — Exactly one writer (owner gate)

```bash
sudo -n /opt/opip-m07-toolkit/scripts/v2/bootstrap-v2.sh --mode activate-writer \
  --genesis-receipt /var/lib/opip-bootstrap/canonical-fresh-genesis-receipt.json
```

Refused unless: `BOOTSTRAP_EXECUTED` exists, no writer is running, no
`WRITER_ACTIVATED.json` exists yet, and the gate re-verifies the genesis receipt
against the live store, the certified source module digests, the host identity
and the receipt age (≤ 24 h). Afterwards exactly one running writer is asserted
and `WRITER_ACTIVATED.json` records `activation_route: "fresh-genesis"` plus the
authorising receipt digest.

**Activation is a one-shot first-start gate.** The genesis receipt is bound to
the exact empty store bytes; once the writer commits anything, that receipt no
longer authorises a start (by design, exactly like the native-recovery receipt).
Container restarts are Compose's responsibility (`restart` policy), never a
re-activation. If the writer container is removed and the store is no longer
empty, stop: that is an owner decision, not a genesis.

## Phase E — TLS and tracked Telegram acceptance, then the scheduler

Identical to the historical route: record
`/var/lib/opip-bootstrap/LIVE-ACCEPTANCE.json` with
`{"https_ok": true, "telegram_ok": true}` only after a tracked end-to-end
acceptance, then

```bash
sudo -n /opt/opip-m07-toolkit/scripts/v2/bootstrap-v2.sh --mode activate-scheduler
```

The scheduler stays OFF until `WRITER_ACTIVATED.json` **and** a satisfied
`LIVE-ACCEPTANCE.json` exist. This gate is unchanged by this increment.

## Abort, retry and rollback

* Refusal before Phase B: nothing changed; fix the precondition.
* Failure inside Phase B (`FAIL` / `PLATFORM_BLOCKED`): no store is published.
  Remove only the private `.opip-genesis.*` staging artifact the tool names, the
  `<db>.writer.lock` if it is left behind, then retry.
* Interruption (process killed): a surviving `.opip-genesis.*` artifact blocks a
  retry by design. Inspect it, then remove it explicitly. Genesis never deletes a
  database.
* After a successful genesis but before activation: delete the store and the
  receipt **only with owner authorization**, then restart at Phase A. The
  preserved V1 archives are untouched either way.
* Rollback of an activated writer/scheduler is the existing
  `rollback-v2.sh` / `05-REVISED-ROLLBACK-PROCEDURE.md` path; a canonical
  rollback to V1 history is the historical migration route, not this one.

## Evidence to retain

`canonical-fresh-genesis-receipt.json`, any `*.failed.json`, the check-toolkit
and check-release output, `WRITER_ACTIVATED.json`, `LIVE-ACCEPTANCE.json`,
`SCHEDULER_ACTIVATED.json`, and the SHA-256 of the certified toolkit tree. No
secret, token or credential is copied into any receipt.

