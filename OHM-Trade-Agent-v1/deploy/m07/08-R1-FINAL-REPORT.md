# 08 — R1 Final Report (Release Blocker Remediation)

**Scope:** correct only the release-blocking defects found by review of Mission 07.
**Certified SHA:** `c196dd674678b3b1ee91c351ee97c1c9e2b3938d`
**Correction tree:** `C:\Users\ohmpr\opip-mission-07-r1` (Mission 07 evidence left untouched)
**Verdict:** **HOLD** — every locally provable defect is fixed and tested; production activation and live proofs remain unauthorised.

---

## 1. Defects found and corrected

| ID | Sev | Defect | Root cause (proven) | Correction |
|---|---|---|---|---|
| RC-01 | P0 | A fresh host cannot deploy | Controller reads the image from the existing container (`ohm-deploy:1347-1391`), so the preflight blocks and exits 77; its success path also starts the paper stack and retires P1 | Separate, documented **bootstrap** path that still runs+records the certified preflight, requires an explicit fresh-host acknowledgement, starts core only, and never invokes the controller/paper/P1 |
| RC-02 | P0 | Export silently truncated datasets >2000 files | R0 wrote a 50-entry sample into the manifest | Every file enumerated; `totals.files` must equal the listed count |
| RC-03 | P0 | Transferred archive not verifiable | Manifest was created after the archive and excluded from it | Archive embeds manifest + digest + hash list; `bundle.sha256` travels beside it |
| RC-04 | P0 | No canonical restore; copy reported as recovery | R0 only copied files | Explicit `RestoreCanonical` using the certified native restore, with epoch/sequence/integrity/FK/count validation and a writer-quiescence check |
| RC-05 | P0 | Rollback depended on a file the controller deletes | `cleanup_snapshot()` removes `safe-baseline-rollback.override.yml` (`:291`) | Durable generated overlay + hash verification + post-cutover write preservation + epoch guard |
| RC-06 | P1 | Readiness used `root@` and self-asserted identity | R0 trusted user input and did not enforce remote exit codes | `opipadmin` only, server-side metadata identity, per-command rc=0 + evidence, compose config on the host |
| RC-07 | P1 | TLS prerequisites unmodelled | Only static config was checked | Prerequisite gate: DNS, HTTP-01 reachability, key permissions, 8443 restriction, renewal |
| RC-08 | P1 | A transport probe counted as delivery | R0 called the raw send helper | Tracked delivery must record `DELIVERED` + message id; dedupe proven separately; transport-only reported apart |

Full mapping: `01-ROOT-CAUSE-MATRIX.csv`, `02-CHANGED-FILES.csv`.

## 2. Targeted test results (executed)

| Suite / script | Command | Exit | Result |
|---|---|---|---|
| Bundle scenarios (5,6,7,8,9 + large dataset) | `pytest tests/scenarios/test_r1_bundle.py -q` | 0 | **10 passed** (69.7 s) |
| Operations scenarios (1,2,3,4,10-15) | `pytest tests -q --ignore=…bundle.py` | 0 | **13 passed** |
| Readiness (local) | `Invoke-V2Readiness.ps1` | 2 | static PASS, host facts unproven |
| Readiness (root) / (no authorisation) | `-SshUser root` / `-Execute` | 3 / 3 | refused |
| Bootstrap (plan / incumbent / no ack / authorised) | `Invoke-V2Bootstrap.ps1` | 77 / 3 / 3 / 0 | as designed |
| Rollback (plan / newer history) | `Invoke-V2Rollback.ps1` | 77 / 3 | as designed |
| Migration dry run | `Invoke-OpipDataMigration.ps1 -Mode DryRun` | 77 | inventory + export plan |
| Dashboard (no facts / ok / missing) | `Invoke-V2DashboardValidation.ps1` | 2 / 0 / 3 | as designed |
| Telegram tracked acceptance | `telegram_tracked_acceptance.py` | 0 | tracked receipt + dedupe PASS |
| Telegram validation | `Invoke-V2TelegramValidation.ps1` | 2 | mock PASS, live pending |

All 15 mandated scenarios executed: `06-TARGETED-TEST-RESULTS.csv`. No skips except the Windows-only durability test, which is `win32`-gated by design.

## 3. What the fixes prove — and what they do not

**Proven:** the toolchain enumerates and moves >2600 files without truncation; a transferred archive alone verifies; tampering and truncation fail closed; interrupted export/import resume without duplication; promotion cannot occur without a recorded recovery; newer canonical history is refused; rollback no longer depends on a transient file; `root` and unauthorised SSH are refused; missing TLS prerequisites refuse; a send without a tracked receipt fails; duplicate writers/schedulers are refused.

**Not proven:** real V1 extraction; real transfer throughput; the Linux native restore succeeding (only its refusal is provable here); TLS issuance and external HTTP codes; live Telegram delivery; the bootstrap sequence running on a real host.

## 4. Remaining live blockers

| ID | Sev | Blocker | Unblocks with |
|---|---|---|---|
| L-01 | P1 | No authorised read-only V2 probe | `Invoke-V2Readiness.ps1 -Execute -Authorized` as `opipadmin` |
| L-02 | P1 | Certified canonical restore not executed on Linux | Migration step 5 on V2 |
| L-03 | P1 | Final V1 state not extracted | Owner authorisation for read-only disk access or controlled restart |
| L-04 | P2 | TLS/HTTPS unverified | `certbot` + the two `curl` proofs |
| L-05 | P2 | Live tracked Telegram delivery unverified | Real credentials on V2 |
| L-06 | P2 | Bootstrap not executed on real hardware | Owner-authorised first install |

## 5. GO / HOLD

**HOLD.** The remediation is complete and independently tested, but production readiness cannot be asserted from mocked, static or synthetic evidence. The release stays on HOLD until L-01…L-06 are cleared with live evidence.

## 6. Safety state

V1 OFF and untouched · no V2 container started and no V2 file written · learning worker untouched · Phase 5E branch untouched · no secrets read or written · deployment controller never invoked · no DNS or firewall change · no commit, tag, PR or merge.
