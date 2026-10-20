# 15 — R1-HF Final Report

**Scope:** correct only the release-toolchain defects (deployment, migration, bootstrap, recovery, tests).
**Baseline:** `c196dd674678b3b1ee91c351ee97c1c9e2b3938d`
**Workspace:** `C:\Users\ohmpr\opip-mission-07-r1` · pre-change baseline preserved at `…-r1-baseline` (28 files)
**Verdict:** **HOLD** — toolchain hardened and tested; no live runtime, real V1 history, backup protection, HTTPS or Telegram acceptance exists yet.

---

## 1. What was corrected

| ID | Defect | Correction | Tests |
|---|---|---|---|
| HF-01 | Archive verified after extraction, no member safety, existing verify dir deleted | Digest-first abort; member validation (absolute/`..`/links/devices/dupes); fresh extraction dir; source-symlink guard | 8 |
| HF-02 | `-CanonicalRecovered` was sufficient evidence | Persisted receipt re-verified against the **live** DB at promotion; failed runs overwrite PASS; `-Authorize` mandatory | 11 |
| HF-03 | Printed bootstrap; clone/`\|\| true`; tracked nginx overwritten; preflight ignored | One guarded script, three receipts, host identity, installed-release reuse, external nginx mount, independent preflight evaluation, no paper/P1 | 10 |
| HF-04 | Rollback depended on a file the controller deletes; owner-entered epochs | Host-generated durable overlay, hash+mode re-verified, DB-derived epochs, kind separation, preserve+validate before switch | 4 |
| HF-05 | One readiness phase requiring a configured host | `PreBootstrap` / `PostConfiguration`, `opipadmin` + `sudo -n`, host-only identity | 2 |
| HF-06 | Identity from CLI defaults | Export refuses non-evidence identity; provenance verifies identity+cutoff+manifest+archive+datasets together | 11 |

Details: `10-HF-DEFECT-EVIDENCE.md`. Inventory: `09-HF-CHANGED-FILES.csv` (9 added, 7 modified, 0 removed).

## 2. Exact test results

| Suite | Command | Exit | Result |
|---|---|---|---|
| HF-01 + HF-02 + HF-03/04 + HF-06 + R1 operations | `pytest … -q` | **0** | **54 passed** (66.9 s) |
| Bundle scenarios (large dataset, interruption, corruption) | `pytest tests/scenarios/test_r1_bundle.py -q` | **0** | **10 passed** (62.4 s) |
| **Total** | — | **0** | **64 passed, 0 failed, 0 skipped** |
| Shell syntax | `bash -n scripts/v2/bootstrap-v2.sh` | **0** | clean |
| Shell syntax | `bash -n scripts/v2/rollback-v2.sh` | **0** | clean |
| PowerShell | parser over all `.ps1` | — | all clean |

Mandatory regressions (both pass):
* invalid-checksum archive containing `../../outside-proof.txt` → FAIL, **no file created outside**;
* staging receipt `canonical_recovered=false` + caller `true` → refused, **target untouched**.

## 3. Behaviour of the corrected entrypoints (executed)

| Invocation | Exit | Meaning |
|---|---|---|
| `Invoke-V2Readiness.ps1 -Phase PreBootstrap` | 2 | offline: remote probes not yet authorised |
| `Invoke-V2Readiness.ps1 -Phase PreBootstrap -SshUser root` | 3 | wrong account refused |
| `Invoke-V2Bootstrap.ps1 -Phase Prepare` | 2 | authorised plan printed; no shell opened |
| `Invoke-V2Bootstrap.ps1 -Phase Execute` (no `-Authorize`) | 3 | refused |
| `Invoke-V2Rollback.ps1 -Kind data` | 3 | data recovery is not a code rollback |
| `Invoke-V2Rollback.ps1 -Kind code` (no image) | 3 | immutable image required |
| `Invoke-OpipDataMigration.ps1 -Mode Promote` (no `-Authorize`) | 3 | refused |

## 4. Go/Hold matrix

| Area | Verdict |
|---|---|
| HF-01 archive verification | **PASS** |
| HF-02 promotion gate | **PASS** |
| HF-03 bootstrap toolchain | **PASS** (syntax + structural; not executed on a host) |
| HF-04 rollback toolchain | **PASS** (syntax + structural; not executed on a host) |
| HF-05 readiness phases | **PASS** (offline behaviour verified) |
| HF-06 provenance | **PASS** |
| HF-07 evidence + review | **PASS** |
| V2 runtime / final V1 history / backups / HTTPS / Telegram | **HOLD** (L-01…L-09) |
| **Release** | **HOLD** |

Full matrix with per-requirement evidence: `14-HF-CERTIFICATION-MATRIX.csv`.

## 5. Remaining live blockers

`L-01` read-only V2 access · `L-02` installed release · `L-03` certified image · `L-04` `.env` · `L-05` preflight verdict · `L-06` final V1 generation · `L-07` Linux native restore · `L-08` TLS · `L-09` Telegram · `L-10` backups. Commands and owner actions: `12-HF-LIVE-PREREQUISITES.csv`.

## 6. Adversarial review

15 challenges documented in `13-HF-INDEPENDENT-REVIEW.md`; four were real defects in this work (staging-receipt misuse, stale PASS receipt, broken indentation during the bulk edit, orphaned function) and were fixed before delivery. Two residual risks are explicitly accepted: script execution is unproven (syntax/structural only), and digest trust is integrity, not authenticity.

## 7. Safety state

No production SSH mutation · no V1 restart · no V2 service activation · no DNS/firewall change · no secrets read or written · no merge, tag or deploy. Mission 07 evidence and the Phase 5E branch are untouched; the certified tree was only read.


