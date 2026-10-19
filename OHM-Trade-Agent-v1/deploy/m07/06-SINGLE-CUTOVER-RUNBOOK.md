# Mission 07 R2 — Single Cutover Runbook (LF/CRLF-safe, receipt-bound)

Status: **HOLD — do not execute phases 3+ until the owner clears the gates in
`08-OWNER-GATES.csv`.** This document supersedes
`03-REVISED-DEPLOYMENT-PROCEDURE.md` and `11-HF-CUTOVER-PROCEDURE.md` wherever
they disagree with the guarded entrypoints.

## Host layout (single contract)

| Item | Path |
|---|---|
| Certified release source (immutable) | `/srv/opip-v2/releases/<sha>/OHM-Trade-Agent-v1` |
| Repository root on the host | `/opt/OHM-Trade-Agent-v1` |
| Runtime app root | `/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1` |
| Compose project | `$APP_ROOT/docker-compose.yml` + `/etc/opip/v2/docker-compose.v2.yml` + `/etc/opip/v2/nginx-mount.override.yml` |
| Environment file | `$APP_ROOT/.env` (the **only** `.env`; never inside the release) |
| Data / canonical DB | `$APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3` |
| Owner-staged inputs | `/tmp/opip-v2/docker-compose.v2.yml`, `/tmp/opip-v2/nginx-v2.conf` |
| Bootstrap receipts | `/var/lib/opip-bootstrap/` |
| Recovery receipt | `/var/lib/opip-migration/canonical-recovery-receipt.json` |

`resolve_src` accepts exactly two release layouts (compose at the release root,
or under `OHM-Trade-Agent-v1/`) and fails closed on anything else. The release is
installed into `$APP_ROOT` with `rsync` and verified against
`/var/lib/opip-bootstrap/release-manifest.sha256`; no second checkout is created.

## Phase 0 — Owner prerequisites (no mutation)

L-01 read-only `opipadmin` probe · L-02 installed release at the exact SHA ·
L-03 certified image present with the SHA label · L-04 `.env` at `$APP_ROOT`
mode 600/640 root · L-05 overlay + nginx staged at `/tmp/opip-v2`.

## Phase 1 — Prepare (read-only)

```
sudo -n bash /srv/opip-v2/releases/c196dd67.../OHM-Trade-Agent-v1/scripts/v2/bootstrap-v2.sh \
  --mode prepare
```

Writes `BOOTSTRAP_PREPARED.json` (host, SHA, image id, config digest, preflight
digest, 60-minute expiry, nonce). Blocking protection incidents are recorded and
can never be acknowledged away.

## Phase 2 — Authorize (owner gate)

```
sudo -n bash .../bootstrap-v2.sh --mode authorize \
  --confirm-sha c196dd674678b3b1ee91c351ee97c1c9e2b3938d --confirm-host 610307364 \
  [--acknowledge-fresh-host]
```

Refused if: prepared digest invalid/expired, blocking incidents present, SHA or
host mismatch, configuration digest changed since prepare.

## Phase 3 — Execute (mutates; no writer, no scheduler)

```
sudo -n bash .../bootstrap-v2.sh --mode execute
```

Order: verify receipts → install/verify release → dirs → install overlay+nginx →
re-render Compose → core only → `/health` ok → release-profile gate
(`EVIDENCE_SHADOW`) → assert no writer running → write `BOOTSTRAP_EXECUTED`
(`writer_started=false`, `scheduler_started=false`) → consume the authorization.

## Phase 4 — Final V1 generation (owner, L-06)

Export on V1, transfer to V2, then on V2:

```
python3 scripts/opip_bundle.py verify --archive <bundle.zip> --workdir <scratch>
python3 scripts/opip_bundle.py stage --bundle <bundle> --staging <staging>
```

`stage` records a per-file hash index; staging alone is **not** recovery.

## Phase 5 — Quiesce, native recovery, prove (L-07)

1. stop every writer (`docker compose ... stop opip-canonical-writer`) and prove
   zero running;
2. `python3 scripts/opip_recovery.py restore-canonical --bundle <bundle>
   --app-root $APP_ROOT --live-db $APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3
   --authorize --receipt-out /var/lib/opip-migration/canonical-recovery-receipt.json`;
3. the receipt must be a completed PASS bound to that destination, with
   epoch+sequence+event-count, integrity `ok` and zero FK violations;
4. promote only with that receipt:
   `python3 scripts/opip_bundle.py promote --staging <staging> --target $APP_ROOT
    --recovery-receipt /var/lib/opip-migration/canonical-recovery-receipt.json --authorize`
   (two-phase preflight; a conflict writes nothing);
5. reconcile: `python3 scripts/opip_bundle.py reconcile --bundle <bundle> --target $APP_ROOT`.

## Phase 6 — Exactly one writer (owner gate)

```
sudo -n bash .../bootstrap-v2.sh --mode activate-writer
```

Refused unless: `BOOTSTRAP_EXECUTED` exists, no writer is running, the recovery
receipt passes `writer-gate` (completed PASS, destination match, epoch+sequence+
event-count match, integrity/FK, age ≤ 24 h). Afterwards exactly one running
writer is asserted and `WRITER_ACTIVATED.json` is written.

## Phase 7 — TLS and tracked Telegram acceptance

Record `/var/lib/opip-bootstrap/LIVE-ACCEPTANCE.json` with
`{"https_ok": true, "telegram_ok": true}` **only after** a tracked end-to-end
acceptance (no secrets in the receipt).

## Phase 8 — Scheduler (owner gate)

```
sudo -n bash .../bootstrap-v2.sh --mode activate-scheduler
```

Refused without `WRITER_ACTIVATED.json` and a satisfied `LIVE-ACCEPTANCE.json`.

## Abort behaviour

Any refusal exits non-zero before `compose up`. Writer and scheduler remain OFF,
V1 keeps running, V2 data is untouched, and the receipts stay as evidence.
