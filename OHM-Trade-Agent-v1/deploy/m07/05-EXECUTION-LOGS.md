# Mission 07 R2 — Execution Logs

All commands were executed on the Windows workstation in
`C:\Users\ohmpr\opip-mission-07-r1`. No SSH, container runtime, database, DNS,
firewall or Git remote operation was performed. `docker` and `wsl` are **not
installed** on this workstation, which is why the Linux-native checks are marked
BLOCKED rather than PASS.

## Environment

| Item | Value |
|---|---|
| Python | 3.13.15 |
| pytest root | `tests/` (conftest adds `scripts`, certified source, `tests`) |
| Certified source | `C:\Users\ohmpr\opip-mission-07\src\OHM-Trade-Agent-v1` |
| Certified SHA | `c196dd674678b3b1ee91c351ee97c1c9e2b3938d` (exists as a commit; `git cat-file -t` = commit) |
| Shell | Git-Bash (MSYS) for `bash -n`; PowerShell 7 for orchestration |
| Docker / WSL | absent |

## 1. Baseline and provenance

```
# certified extract integrity (checksums file from the M07 evidence bundle)
checked=1217  mismatches=0

# worktree isolation
git rev-parse HEAD            -> c196dd674678b3b1ee91c351ee97c1c9e2b3938d
git branch --show-current     -> feature/opip-v2-phase5e-resource-scheduler-reliability
```

Pre-change baseline for this correction remains available at
`C:\Users\ohmpr\opip-mission-07-r1-baseline` (28 files, hash-verified earlier).

## 2. Syntax

```
bash -n scripts/v2/bootstrap-v2.sh   -> rc=0
bash -n scripts/v2/rollback-v2.sh    -> rc=0
python -m compileall -q scripts tests -> exit 0
PowerShell Parser::ParseFile(scripts/Invoke-V2Readiness.ps1) -> 0 errors
```

Byte-level line-ending check (PowerShell over raw bytes):

```
bootstrap-v2.sh: crlf=0  (was 270)
rollback-v2.sh : crlf=0  (was 209)
```

## 3. Full suite (authoritative)

Because the harness occasionally backgrounds a single long invocation, the suite was
run in two foreground halves; both exited 0 and the totals are additive (no test
appears in both).

```
cd C:\Users\ohmpr\opip-mission-07-r1
$env:PYTHONPATH="$PWD\scripts"; $env:PYTHONDONTWRITEBYTECODE='1'

python -m pytest tests/scenarios/test_hf02_recovery_gate.py tests/scenarios/test_r1_operations.py -q
  -> 24 passed in 111.26 s

python -m pytest tests/scenarios/test_r1_bundle.py tests/scenarios/test_hf01_archive_safety.py \
                 tests/scenarios/test_hf03_hf04_host_scripts.py tests/scenarios/test_hf06_provenance.py \
                 tests/scenarios/test_r2_audit_fixes.py -q
  -> 76 passed in 79.36 s
```

**Total: 100 passed, 0 failed, 0 skipped, 0 errors, exit code 0, 190.6 s.**
Raw log of the single-invocation run (99 tests, before the R2-05 regression was
added): `evidence/r2/full-suite.log` (99 passed in 161.26 s).

| Module | Tests |
|---|---:|
| `test_hf01_archive_safety.py` | 8 |
| `test_hf02_recovery_gate.py` | 11 |
| `test_hf03_hf04_host_scripts.py` | 11 |
| `test_hf06_provenance.py` | 11 |
| `test_r1_bundle.py` | 10 |
| `test_r1_operations.py` | 13 |
| `test_r2_audit_fixes.py` (new) | 36 |
| **Total** | **100** |

The R2 module alone: `36 passed in 0.39 s`.
R2 + host-script module together: `47 passed`.


## 4. Package verification on the delivered bytes

Performed on a fresh extraction of `OPIP-M07-R2-CANDIDATE.zip` (2731 files):

| Check | Result |
|---|---|
| CRLF byte scan over every `*.sh` in `scripts/` | **0 CRLF pairs** |
| `bash -n scripts/v2/bootstrap-v2.sh` (Git-Bash, absolute path) | rc=0 |
| `bash -n scripts/v2/rollback-v2.sh` | rc=0 |
| `python -m compileall -q scripts tests` | rc=0 |
| R2 + HF03/04 + HF01 + HF06 | **66 passed in 17.27 s** |
| HF02 | **11 passed in 89.87 s** |
| R1 operations | **13 passed in 0.07 s** |
| R1 bundle | **10 passed in 58.07 s** |
| **Extracted-artifact total** | **100 passed, 0 failed, 0 skipped** |

The artifact SHA-256 is recorded in `PACKAGE-SHA256.txt` next to the ZIP. Only
documentation changed after this verification; the code bytes were re-checked by
hash comparison and the fast 66-test group was re-run on the final bytes.


## 5. What is NOT proven here

| Check | Status | Why |
|---|---|---|
| Linux-native `bash -n` of the shipped bytes | BLOCKED | no WSL/Docker on this workstation; MSYS bash is tolerant of CRLF, so the byte scan is the primary evidence |
| Docker Compose render of the real project | BLOCKED | no container runtime; `test_overlay_pair_guards` already requires the certified Compose file, not a live daemon |
| Fresh-host rehearsal, native canonical restore, rollback rehearsal | BLOCKED | owner-authorised live gates L-01…L-10 |
| Readiness against the real droplet | BLOCKED | requires the read-only `opipadmin` probe authorisation |
