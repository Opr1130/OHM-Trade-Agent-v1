# R3 — Release Qualification Report

**Subject:** O'Pip Mission 07 R2 corrected candidate, qualified offline against
the certified application tree at `c196dd674678b3b1ee91c351ee97c1c9e2b3938d`.

**Bottom line:** engineering qualification **PASS**; Linux qualification
**BLOCKED** (no Linux runtime available); production readiness **HOLD**;
authorised deployment **NO**.

---

## 1. What was verified

### 1.1 Provenance

| Fact | Evidence |
|---|---|
| R2 ZIP matches its sidecar | SHA-256 `9e66dcbc8d1018704c068d23ed726443a15504541cfa66fea9775be3fa6ae8f2`, 949,399 bytes, 2,731 files |
| R2 ZIP is reproducible from the R1 tree | fresh extraction vs `opip-mission-07-r1`: **2,731 files, 0 differences** |
| The toolkit existed in no commit before R3 | `git ls-tree -r c196dd67` has no `opip_bundle/opip_recovery/…`, no `bootstrap-v2.sh` |
| The audit worktree is isolated | `source-worktree` at `c196dd67`, branch `audit/m07-r2-corrective-20261019`, only `_m07_audit_inputs/` untracked (never staged) |
| Certified application importable | `import app.opip.canonical` succeeded from the worktree |

### 1.2 Source reconciliation

66 files were compared byte-for-byte against the R2 package:

* **57 identical** — committed as-is,
* **5 corrected in R3** — `bootstrap-v2.sh`, `rollback-v2.sh`, `tests/conftest.py`,
  `test_r2_audit_fixes.py`, `test_hf03_hf04_host_scripts.py`,
* **4 new** — `.gitignore`, `README.md`, `test_r3_controller_parity.py`,
  `test_r3_concurrency_and_compose.py`.

Full inventory with SHA-256: `R3-SOURCE-RECONCILIATION.csv`.

### 1.3 The decisive R3 correction — Compose parity

The R2 bootstrap used an **owner-staged overlay**:

```
docker compose -f "$APP_ROOT/docker-compose.yml" -f "$ETC/docker-compose.v2.yml" \
               -f "$ETC/nginx-mount.override.yml"
```

The certified controller (`deploy/remote/ohm-deploy`) uses:

```
cd "$APP_ROOT" && docker compose …
```

Two consequences, both verified:

1. **Duplicate authority / second source of truth.** An owner-provided file could
   inject arbitrary services, volumes or capabilities into the trading stack, and
   the bootstrap project would diverge from the one the controller manages.
2. **A meaningless integrity control.** `config_digest_value` hashed
   `/tmp/opip-v2/*`, which often does not exist, so the "configuration digest" in
   every receipt was the constant hash of `MISSING`.

R3 removes the overlay, aligns both scripts with the controller, and hashes only
`docker-compose.yml` + `.env`. `test_r3_controller_parity.py` reads the real
controller file and fails if the scripts diverge again.

### 1.4 Compose contract (offline)

Verified against the certified `docker-compose.yml` (PyYAML parse, no daemon):

| Contract | Result |
|---|---|
| Four services declared with matching `container_name` | PASS |
| Core consumes `.env`; writer/stream use explicit `environment` (a stale `.env` cannot activate capture) | PASS |
| Tracked nginx config mounted `:ro` from `./deploy/nginx/dashboard-sidecar.conf` | PASS |
| Core has no `depends_on` writer | PASS |
| Writer publishes no port | PASS |
| Every service name referenced by the scripts exists | PASS |
| Every path the scripts require exists in the release | PASS |

### 1.5 Data integrity and concurrency

| Scenario | Result |
|---|---|
| Snapshot of a WAL database with uncheckpointed rows | 5/5 rows, integrity `ok` |
| Snapshot while a second connection inserts 200 rows | integrity `ok`, 0 FK violations, writer unharmed (200/200 committed) |
| Live writer lock held by another process | detected as not quiescent; released → quiescent |
| Equal epoch + newer sequence / event count | REFUSED |
| Corrupt live database | REFUSED |
| Snapshot onto an existing file | refused, evidence preserved |

### 1.6 Test totals

| Run | Result |
|---|---|
| Worktree (`deploy/m07`) | **116 passed**, 0 failed, 0 skipped, 286.46 s |
| Clean `git archive` of commit `c3337d51` | **116 passed**, 0 failed, 0 skipped, 291.15 s |
| R2 baseline (unchanged) | 100 passed |

Per-module and per-command detail: `R3-TEST-RESULTS.csv`.

---

## 2. What is blocked, and exactly why

| Gate | Blocker | Exact resolution required |
|---|---|---|
| Linux-native `bash -n` (R3-G1) | this workstation has **no WSL distro** and **no container runtime** (`docker` absent; `wsl -e` reports WSL not installed) | run `bash -n` on both scripts in any Linux environment and attach the transcript |
| Compose render (R3-G3) | no daemon | `docker compose config --quiet` in CI |
| Readiness (L-01) | no host authorisation | `Invoke-V2Readiness.ps1 -Phase PreBootstrap -Execute -Authorized` → exit 0 |
| Restore rehearsal (L-07) | no host, no V1 export | one native restore producing a PASS receipt |
| TLS / Telegram (L-08/L-09) | no authorisation | tracked acceptance evidence |

Nothing in this report should be read as evidence about the live host.

---

## 3. Residual engineering observations (non-blocking, out of scope)

1. **V2 resource overlay is uncommitted.** The Phase 5E `docker-compose.v2.yml`
   (memory/CPU ceilings, selected by `/etc/opip/host-profile`) exists only as an
   untracked file in another worktree. With R3's controller parity the bootstrap
   will not apply it, so a V2 host would run with V1 limits until that work is
   merged. This toolkit must not paper over it with its own overlay.
2. `ohm-deploy` expects `docker-compose.paper.yml`; absent means the paper stack
   stays off, which is the desired default.
3. `writer-gate` proves *equality* with the live database, not authorship of the
   receipt; signing is not implemented.

---

## 4. Owner decision table

| Question | Answer |
|---|---|
| Is the corrected code reviewable and reproducible? | **Yes** — commit `c3337d51`, tree `6280481096d13d3023988b5fe059f672e25b17de`, 116 tests from a clean export |
| Is the engineering qualification complete? | **Yes**, offline: PASS |
| Is Linux qualification complete? | **No** — BLOCKED (no Linux runtime) |
| Can V2 be deployed now? | **No** — production readiness HOLD |
| What is the smallest next step? | Give the toolkit a Linux runner (CI is sufficient) for R3-G1/G3 |
| What must owner authorisation cover? | L-01 read-only probe; L-06 final V1 export; L-07 one native restore rehearsal |

---

## 5. Safety attestation

No production SSH, no host access, no V1/V2 mutation, no live database, no DNS,
firewall, certificate, scheduler or Telegram action, no Git push or merge, and no
secret read. All work happened in
`C:\Users\ohmpr\opip-m07-r2-deep-audit\source-worktree` and
`C:\Users\ohmpr\opip-m07-r3-qualification`.

