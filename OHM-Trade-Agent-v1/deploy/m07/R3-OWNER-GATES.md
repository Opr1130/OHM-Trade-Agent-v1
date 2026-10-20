# R3 — Additions to the Owner Gates

`08-OWNER-GATES.csv` (R2) remains the historical record. This document is the
**current** gate list; it changes L-05 (now obsolete) and adds the R3 gates.

## Engineering vs live

| Class | Gates | Status after R3 |
|---|---|---|
| Engineering (offline) | 116 tests, syntax, provenance, commit | **PASS** |
| Live-only | L-01…L-04, L-06…L-10 | **BLOCKED** — no authorisation, no host access |

## Gate table

| Gate | Owner action | Acceptance criteria (must all hold) | Evidence to attach | Blocks |
|---|---|---|---|---|
| **L-01** | Authorise one read-only SSH probe as `opipadmin` with a pre-provisioned known_hosts entry | `Invoke-V2Readiness.ps1 -Phase PreBootstrap -Execute -Authorized` exits **0**; identity = 610307364; `BACKUP_SEED_INFO` is SKIP, never a gate | readiness receipt (JSON) + SSH transcript without secrets | every later phase |
| **L-02** | Confirm the installed release at `/srv/opip-v2/releases/c196dd67…/OHM-Trade-Agent-v1` | `git rev-parse HEAD` = `c196dd67…`; `release-manifest.sha256` verifies; no local modifications | manifest digest + `git status --porcelain` (empty) | prepare |
| **L-03** | Confirm the certified image | `docker image inspect opip-v2:certified-c196dd67` returns an id whose labels contain the certified SHA | image id + labels JSON | prepare |
| **L-04** | Place `$APP_ROOT/.env` | exists, mode 600/640, owner root; **content never shared** | `stat` only | execute |
| **L-05** | ~~Upload V2 overlay + nginx config to `/tmp/opip-v2`~~ | **OBSOLETE in R3**: no overlay is used; the certified `docker-compose.yml` is the only source | — | — |
| **L-06** | Authorise the final V1 export/transfer | archive verifies (manifest + per-file digests), one canonical generation, cutoff recorded | bundle manifest + archive SHA-256 | stage/promote |
| **L-07** | Authorise one native canonical restore (disposable host first) | restore receipt is a completed PASS bound to the live DB; epoch+1, sequence reset, event count matches; integrity `ok`; 0 FK violations | `canonical-recovery-receipt.json` | `activate-writer` |
| **L-08** | Authorise DNS/TLS work | HTTPS reachable on the intended hostname, certificate present, renewal timer active, 8443 restricted | `https_ok=true` in `LIVE-ACCEPTANCE.json` | scheduler |
| **L-09** | Authorise a tracked Telegram acceptance | tracked message observed end-to-end | `telegram_ok=true` in `LIVE-ACCEPTANCE.json` | scheduler |
| **L-10** | Confirm V2 backups | schedule present, one restore rehearsal documented, retention stated | backup listing + rehearsal record | release from HOLD |
| **R3-G1** | Provide a Linux runner (native or container) | `bash -n` on both scripts exits 0 **on Linux**; `python -m pytest` = 116 passed | command transcript + exit codes | release certification |
| **R3-G2** | Provide a disposable Linux host | prepare→authorize→execute→restore→activate-writer completes with the receipt chain intact, and every negative path refuses | full receipt chain | release certification |
| **R3-G3** | Provide a container runtime | `docker compose config --quiet` exits 0 for the certified project with the real `.env` shape (no secret values in the transcript) | render output (secrets redacted) | release certification |

## What R3 already prepared for L-01/L-07 (offline only)

* L-01: readiness script, strict known-host verification, phase split
  (`PreBootstrap` / `PostConfiguration`), and the pre-bootstrap backup gate
  removed. The command is ready to run; it was **not** run.
* L-07: `restore-canonical` entrypoint, post-restore validation (epoch, sequence,
  event count, integrity, FK, destination binding), persistence of a failure
  receipt over a stale PASS, and a `writer-gate` that re-reads the live DB.
  Every guard is covered by a test; none was executed against a host.

## Standing prohibition

No gate in this table may be satisfied by editing a report. Each requires a
command, a receipt and an exit code produced by the environment named in the
"acceptance criteria" column.
