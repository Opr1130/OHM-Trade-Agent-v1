# 10 — HF Defect Evidence (before → after)

> **HISTORICAL EVIDENCE (R2) — not an executable procedure.** It records the
> defects that R3 corrected (`R3-FINAL-VERDICT.md`,
> `R3-RELEASE-QUALIFICATION.md`). Where it quotes an obsolete overlay path, that
> path is evidence of the defect, not an instruction.

Every defect was reproduced with a synthetic fixture **before** correction and is covered by an executable test **after**. "Before" = the pre-change baseline at `C:\Users\ohmpr\opip-mission-07-r1-baseline` (28 files, snapshotted before any edit).

## HF-01 — Unsafe bundle verification

| | Before (R1 baseline) | After (HF-01) |
|---|---|---|
| Digest timing | compared, then the workdir was **wiped** and `tar.extractall()` ran | digest validated first; on failure the function returns before creating anything |
| Member safety | none (Python default extraction) | absolute paths, drive letters, `..`, control chars, duplicates, symlinks, hardlinks, devices, FIFOs rejected |
| Extraction | `shutil.rmtree` of an existing `_verify` directory | fresh directory only; an existing non-empty directory is refused, never deleted |
| Source symlinks | `rglob` could follow escaping links | `os.walk(followlinks=False)` + resolved containment + `assert_source_tree` |
| Manifest paths | not validated | `MANIFEST_PATHS_SAFE` rejects absolute/`..`/NUL |

**Mandatory regression:** archive containing `../../outside-proof.txt` with an invalid `bundle.sha256` → `FAIL`, `ARCHIVE_DIGEST=FAIL`, `extraction_skipped`, and **no `outside-proof.txt`** anywhere. A second variant with a *valid* digest but the same traversal member fails `ARCHIVE_MEMBERS_SAFE` and still writes nothing outside.

Evidence: `tests/scenarios/test_hf01_archive_safety.py` (8 tests).

## HF-02 — Caller-controlled recovery assertion

| | Before | After |
|---|---|---|
| Gate | `canonical_recovered=True` was sufficient | persisted receipt required; the flag is ignored as evidence |
| Receipt | none | bundle manifest SHA-256, resolved destination DB, source SHA, generation SHA-256, epoch, sequence, event count, integrity, FK count, `completed` |
| Freshness | none | destination DB re-read immediately before promotion; epoch/sequence/count must match |
| Failure | a failed restore left any earlier PASS receipt | every terminal path rewrites the receipt with `completed=false` |
| Authorization | promote/restore could run without `-Authorize` | both refuse without it |

**Mandatory regression:** staging receipt `canonical_recovered=false` + caller `true` + `authorize=True` → `SystemExit` citing the missing receipt, target directory empty. Also refused: missing, forged destination, wrong bundle identity, stale epoch, `completed=false`, no authorization. Accepted: a receipt matching the live DB.

Evidence: `tests/scenarios/test_hf02_recovery_gate.py` (11 tests).

## HF-03 — Printed bootstrap instead of an executed install

| | Before | After |
|---|---|---|
| Execution | printed shell lines | one guarded `scripts/v2/bootstrap-v2.sh` with `prepare → authorize → execute` |
| Identity | caller-supplied | DigitalOcean metadata read on the host (`610307364`) |
| Source | `git clone … \|\| true` | installed release `/srv/opip-v2/releases/<sha>/…` reused; no clone path |
| Image | tag assumed | image id + label inspected; label must carry the SHA |
| Nginx | tracked file overwritten | bind-mounted via `/etc/opip/v2/nginx-mount.override.yml` |
| Setup failures | `\|\| true` on required steps | `run()` aborts on any non-zero exit; only two documented probes fall back |
| Preflight | verdict ignored | executed, exit code captured, JSON re-evaluated; blocking incidents abort; acknowledgement cannot convert an unsafe verdict |
| Paper / P1 | inherited from the controller | never started / never run |
| Scheduler | implicit | only `--activate-scheduler` after `BOOTSTRAP_AUTHORIZED` |
| Receipts | one narrative | `BOOTSTRAP_PREPARED`, `BOOTSTRAP_AUTHORIZED`, `BOOTSTRAP_EXECUTED` |

Evidence: `tests/scenarios/test_hf03_hf04_host_scripts.py` + `bash -n`.

## HF-04 — Rollback that could not work after success

| | Before | After |
|---|---|---|
| Overlay origin | operator's Windows filesystem | generated **on the host** in `/var/lib/opip-rollback/<sha>/` |
| Verification | `test -s` on a controller file the controller deletes | existence + `640 root:root` + SHA-256 re-verified on the host |
| Epoch source | owner-entered numbers | read from the destination SQLite database |
| Kinds | one blended procedure | `safe-baseline`, `code` (immutable image), `data` (refused → native restore) |
| Newer data | compared owner numbers | refuses when live epoch > plan epoch |
| Post-cutover writes | copied, unvalidated | copied **and** SHA-256 compared before any switch |
| Code rollback | not modelled | requires `--previous-image` whose label carries the previous SHA |

## HF-05 — Readiness required a configured host

| | Before | After |
|---|---|---|
| Phases | one phase requiring `/opt`, `.env`, overlay | `PreBootstrap` (identity, installed release, image, storage, backups, container count) and `PostConfiguration` (checkout, `.env`, effective compose, volumes, modes) |
| Privilege | `root` SSH | `opipadmin` + `sudo -n` for Docker reads |
| Identity | local parameter | host metadata read over SSH |

## HF-06 — Provenance from CLI defaults

| | Before | After |
|---|---|---|
| Identity | `--droplet-id/--ip` defaults accepted | export refuses unless `identity_proof=host-evidence` (or `fixture` with the rehearsal flag) |
| Cutoff | recorded only | present, not in the future, within `--max-age-hours`, near the evidence sample |
| Combined | none | identity + manifest + cutoff + archive digest + dataset completeness together |
| Synthetic | blocked only by an export flag | additionally rejected as final state |

Evidence: `tests/scenarios/test_hf06_provenance.py` (11 tests).

## Still unproven

No live host was contacted. Bootstrap/rollback are syntax-checked and structurally asserted, not executed. Real V1 extraction, transfer, Linux restore success, TLS and Telegram remain live gates (`12-HF-LIVE-PREREQUISITES.md`).
