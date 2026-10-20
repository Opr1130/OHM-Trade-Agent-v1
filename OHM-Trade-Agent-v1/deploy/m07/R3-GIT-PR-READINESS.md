# R3 — Git and Pull-Request Readiness

## Branch and commit

| Item | Value |
|---|---|
| Worktree | `C:\Users\ohmpr\opip-m07-r2-deep-audit\source-worktree` |
| Branch | `audit/m07-r2-corrective-20261019` (isolated, based on `c196dd67`) |
| Base | `c196dd67` (certified SHA, detached-clean before this work) |
| Commit 1 (toolkit) | `c3337d51` |
| Commit 1 tree | `6280481096d13d3023988b5fe059f672e25b17de` |
| Diffstat | 66 files, +8655 lines, 0 deletions, 0 existing files modified |
| Push | **not performed** (prohibited) |
| Merge | **not performed** (prohibited) |

The commit adds only `OHM-Trade-Agent-v1/deploy/m07/**`. No application module,
Compose file, workflow, dependency, or configuration file was touched, so the
runtime behaviour of the shipped app is unchanged.

Commit 2 (this evidence set: `README.md`, `R3-*`) is documentation-only and is
committed separately so the reviewable code change stays minimal. Because a
commit cannot contain its own hash, the final documentation-commit hash and the
resulting `HEAD^{tree}` are recorded in the owner-side sidecar
`C:\Users\ohmpr\opip-m07-r3-qualification\COMMIT-STATE.txt` (not committed).

## Changed-file inventory

* 62 package files under `deploy/m07` (scripts, tests, drafts, historical R1/R2
  evidence), of which 57 are byte-identical to the R2 candidate, 5 are corrected
  (bootstrap, rollback, conftest, `test_r2_audit_fixes`, `test_hf03_hf04_host_scripts`).
* 4 new files: `.gitignore`, `README.md`, `test_r3_controller_parity.py`,
  `test_r3_concurrency_and_compose.py` (plus the R3 evidence documents in commit 2).
* `R3-SOURCE-RECONCILIATION.csv` lists every path with bytes and SHA-256.

## What a reviewer must check

1. `deploy/m07/scripts/v2/bootstrap-v2.sh` — no writer, no scheduler, no overlay;
   `compose()` equals the controller's invocation.
2. `deploy/m07/scripts/opip_receipts.py` — digest, expiry, nonce, binding, consume.
3. `deploy/m07/scripts/opip_rollback_guard.py` — snapshot API, history guard,
   writer gate.
4. `deploy/m07/scripts/opip_bundle.py` — two-phase promotion, containment.
5. `deploy/m07/tests/scenarios/test_r3_*.py` — the contracts that pin the above
   to `deploy/remote/ohm-deploy` and to real SQLite behaviour.

## Suggested PR description

> **Add the Mission 07 V2 cutover toolkit (`deploy/m07`)**
>
> Adds the offline release-engineering tooling for the V1→V2 cutover: archive and
> bundle verification, two-phase canonical promotion, native restore validation,
> provenance gates, digest-bound/expiring/single-use receipts, WAL-safe rollback
> snapshots and a receipt-gated writer activation.
>
> The host scripts deliberately invoke Compose exactly as `deploy/remote/ohm-deploy`
> does (`cd "$APP_ROOT"; docker compose …`). They add no overlay, generate no
> nginx config, start no writer and no scheduler, and reach no network or
> exchange.
>
> 116 offline regressions pass from a clean `git archive` of this commit
> (no network, no daemon, no host). No existing application file is modified.
> Live gates L-01…L-10 remain open and are documented in
> `deploy/m07/R3-OWNER-GATES.md`.

## CI verification checklist

| Check | Command | Expected | Status |
|---|---|---|---|
| Python syntax | `python -m compileall -q scripts tests` | exit 0 | PASS (local) |
| Suite | `python -m pytest tests/scenarios -q` | 116 passed | PASS (local, Linux **pending**) |
| Shell syntax (Linux) | `bash -n scripts/v2/bootstrap-v2.sh scripts/v2/rollback-v2.sh` | exit 0 | **BLOCKED locally** — must run in CI |
| Line endings | `git ls-files --eol '*/deploy/m07/scripts/v2/*.sh'` | `i/lf w/lf` | PASS |
| No secrets | repo secret scan / gitleaks | no findings | PASS (targeted scan) |
| No authority change | review `deploy/m07/**` only | no execution/order path | PASS |
| Compose render | `docker compose config --quiet` in CI | exit 0 | **BLOCKED locally** |

## Merge prerequisites (owner)

1. CI provides the Linux and Compose checks above (R3-G1/G3).
2. A second reviewer signs the security review (`R3-SECURITY-REVIEW.md`).
3. Owner confirms L-02/L-03 on the host.
4. No production deployment is implied by merging: the toolkit is inert until an
   authorised operator runs it.
