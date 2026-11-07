# O'Pip CI Optimization v1

Status: implemented on a feature branch. Not merged, not deployed, no production
configuration and no trading authority changed.

Scope: `ATDD-CI-OPTIMIZATION-M01`
([scope contract](../atdd/scope-contracts/ATDD-CI-OPTIMIZATION-M01.md)).

## 1. Problem (measured, not estimated)

Every pull request and every main push ran the complete pytest suite
unconditionally in `.github/workflows/pytest.yml`.

Baseline evidence, run
[42306770164](https://github.com/Opr1130/OHM-Trade-Agent-v1/actions/runs/42306770164)
(`pytest.yml`, `main`, SHA `c51c031f5d2a0f73b8cf07d4775df4dc3d986378`):

| Job step | Start (UTC) | End (UTC) | Duration |
| --- | --- | --- | --- |
| `Initialize containers` (postgres service) | 22:42:53 | 22:43:17 | 24 s |
| `pip install -r requirements.txt -r requirements-test.txt` | 22:43:24 | 22:43:30 | 6 s |
| `Run python -m pytest -q` | 22:43:30 | 22:52:25 | **535 s** |
| `python -m compileall -q app` | 22:52:25 | 22:52:26 | 1 s |
| `Validate production Compose` | 22:52:26 | 22:52:26 | <1 s |
| `Validate deployment shell` | 22:52:26 | 22:52:26 | <1 s |
| `Build learning image for replica E2E` | 22:52:26 | 22:52:35 | 9 s |
| `Verify production image excludes pytest` | 22:52:35 | 22:52:35 | <1 s |
| `Canonical learning-replica transfer E2E (root)` | 22:52:35 | 22:52:38 | 3 s |
| Workflow total | 22:42:46 | 22:52:54 | **~608 s** |

pytest itself reported `10033 passed, 15 skipped, 25 warnings in 530.43s`. The
full suite was therefore ~88% of the whole release run.

Second data point, run
[42333917109](https://github.com/Opr1130/OHM-Trade-Agent-v1/actions/runs/42333917109)
(`pytest.yml`, `main`, SHA `10c9e80578f4cd6f514650d598f9d066f0e24227`, the
current `main` at the time of this branch):

- `Run python -m pytest -q`: `1 failed, 10052 passed, 15 skipped, 25 warnings in
  652.39s (0:10:52)`, step failure at 03:36:39Z.
- The single failure was
  `tests/test_opip_scan_docker_fault_injection_v1.py::test_oom_killed_flag_is_observable`
  (`AssertionError: a memory-bounded workload must report OOMKilled`), i.e. a
  real-Docker OOM proof, not a financial or production-code regression. That
  test is one of the two artifacts this increment makes manual-only.

## 2. Before / after CI matrix

| Trigger | Before | After |
| --- | --- | --- |
| `pull_request` -> `main`, `feature/**` | full 10,033-test suite + compose/shell validation + learning image build + root replica E2E; ATDD scope; architecture gate; Bandit gate | focused selection (tests referencing changed modules) + mandatory safety suite (32 node ids) + the same compose/shell validation, learning image build and root replica E2E; ATDD scope; architecture gate; Bandit gate |
| `push` -> `main` | same full suite, then `non-deploying main release candidate` | same focused selection + mandatory safety suite, then `non-deploying main release candidate` |
| `push` -> `feature/**` | not triggered | not triggered (unchanged) |
| `workflow_dispatch` (`pytest-full-manual.yml`, new) | did not exist | complete suite (`--ignore` the Docker fault-injection module) plus opt-in real-Docker OOM/fault-injection proofs |
| real-Docker OOM fault injection (`tests/test_opip_scan_docker_fault_injection_v1.py`) | ran on every pull request that reached it, skipped when no daemon | manual-only; never selected automatically |
| `/deploy <sha>`, `/deploy-profile`, `/deploy-ui <sha>` exact-SHA gate | `pytest.yml` run `completed/success` only | `pytest.yml` run `completed/success` **and** job/step evidence: `test`, `atdd scope`, `release architecture gate`, `release security gate (Bandit)` succeeded, plus `Focused release selection (changed modules only)`, `Run focused release selection`, `Run mandatory safety suite`, `Verify mandatory safety receipt (fail closed)` all `success` |

Everything else is unchanged: the five pytest.yml gate jobs
(`test`, `atdd-scope`, `architecture-gate`, `security-gate`,
`release-candidate`), their names, the `pull_request` allowlist, the ATDD scope
base resolution, the architecture-gate receipt lines, the pinned Bandit version,
the production-Compose validation, the deployment shell syntax checks, the
"production image excludes pytest" probe and the root canonical learning-replica
E2E.

## 3. What automatic CI runs now

`OHM-Trade-Agent-v1/tests/ci_release_selection.py` (stdlib only, no app import):

- `--mode focused --git-base <sha>` prints one target per line: every existing
  test module that references a changed application module, changed repository
  path or changed basename. Documentation-only or workflow-only change sets fall
  back to the always-on CI contract floor, so an automatic run can never be
  vacuous.
- `--mode safety` prints the mandatory safety suite: 32 node ids in seven
  categories.

Measured selection for representative change sets (local, index build ~0.8 s):

| Change set | Selected targets |
| --- | --- |
| `app/services/protection_health.py` | 7 modules (F11 pre-cutover protection, R5-E F11 independent protection, release protection preflight, evidence-shadow readiness, R4-B2 activation, Kraken balance identity, this CI contract) |
| this pull request (3 workflows + 3 CI modules + `atdd_scope.py` + 2 docs) | 23 modules |
| documentation only | 1 module (CI contract floor) |

Mandatory safety suite (32 node ids):

| Category | Node ids |
| --- | --- |
| `f11_protection` | 5 |
| `funded_authorization` | 4 |
| `financial_calculations` | 6 |
| `portfolio_protection` | 5 |
| `configuration_validation` | 4 |
| `release_authorization` | 3 |
| `ci_release_contract` | 5 |

Each node id is resolved against the test source with `ast` in
`tests/test_opip_ci_optimization_v1.py`, so a renamed or deleted safety test
fails the CI contract instead of silently shrinking the suite.

## 4. Fail-closed rules (requirement: never "successful because not executed")

1. `Run mandatory safety suite` runs pytest with `--junitxml=mandatory-safety-junit.xml`
   over exactly the node ids printed by `--mode safety`, and the step writes
   `safety-targets.txt` first and refuses an empty file.
2. `Verify mandatory safety receipt (fail closed)` runs
   `tests/ci_mandatory_safety_receipt.py`, which fails when the report is
   missing, empty, unparseable, contains zero test cases, contains any
   `failure`/`error`, contains any `skipped`, does not contain a passing test
   case for a mandatory node id, has fewer executed cases than mandatory node
   ids, or whose declared selection is not exactly the mandatory set. A hard
   floor of 30 node ids blocks an accidentally emptied suite.
3. The step greps `MANDATORY_SAFETY=PASS` and writes the receipt to the job
   summary; the receipt and the JUnit XML are retained as artifacts with
   `if-no-files-found: error`.
4. The exact-SHA deployment gate reads the run's jobs and steps from the GitHub
   API and requires each line listed in section 2 to be `success`. A skipped,
   missing or renamed step is a refusal (`missing exact-SHA CI evidence`), never
   a pass. A missing run, a missing run id and a non-successful run are refusals
   too.

## 5. Expected runtime reduction

| Component | Before | After (expected) |
| --- | --- | --- |
| pytest execution in `test` | 535 s / 10,033 tests | focused subset (1-25 modules typical) + 32 safety node ids; well under ~120 s for this repository's module sizes |
| selection overhead | none | ~1 s (index build ~0.8 s measured locally) |
| unchanged overhead | pip install 6 s, image build 9 s, root E2E 3 s, compose/shell 1 s, service init 24 s | unchanged |
| `test` job wall clock | ~590 s | ~150-200 s (expected) |
| workflow wall clock | ~608 s | ~200-250 s (expected) |

Expected reduction: ~60-70% of `pytest.yml` wall clock, and ~88% of the pytest
part. The runtime numbers for "after" are labelled expected: the exact value is
only knowable from the first exact-SHA GitHub Actions run of this branch, and
this document must be updated with that measurement before merge.

## 6. How to run the manual full suite

```bash
gh workflow run pytest-full-manual.yml --ref <branch-or-main>
# include the real-Docker OOM/fault-injection proofs
gh workflow run pytest-full-manual.yml --ref <branch-or-main> -f include_docker_fault_injection=true
```

Nothing in that workflow deploys, reads a production secret, mutates a registry
or gains trading authority.

## 7. Verification performed for this change

- `python -m py_compile` on `tests/ci_release_selection.py`,
  `tests/ci_mandatory_safety_receipt.py`, `tests/test_opip_ci_optimization_v1.py`,
  `tests/atdd_scope.py`.
- `PyYAML` parse of `pytest.yml`, `pytest-full-manual.yml`,
  `deploy-production.yml`.
- `python -m pytest -q tests/test_opip_ci_optimization_v1.py` (5 passed).
- `python -m pytest -q tests/test_ci_stabilization_v1.py
  tests/test_production_dependency_isolation_v1.py
  tests/test_opip_feature_stack_gates_v1.py tests/test_atdd_scope_control.py`
  (29 passed) - the existing workflow/ATDD contract suites that read
  `pytest.yml`, `atdd_scope.py` and the deployment workflow.
- `python tests/atdd_scope.py <changed files>` -> `PASS`.
- `python -m tests.ci_release_selection --mode safety` -> 32 node ids;
  `--mode focused` measured for a module change, this pull request's change set
  and a documentation-only change.
- The complete 10k suite was **not** run locally (excluded by design) and no
  deployment, activation or production command was executed.

## 8. Known limitations / deferred work

- Focused selection is a reference index (module imports, path literals,
  basenames). A cross-cutting change such as `tests/conftest.py` should be
  followed by a manual `pytest-full-manual` dispatch.
- `deploy-learning.yml`, `deploy-committee.yml`, `deploy-analytics.yml` and
  `committee-shadow-activation.yml` still certify an exact SHA from the
  `pytest.yml` run conclusion only. They inherit the stricter gate only where
  they reuse `deploy-production.yml`; extending job/step evidence to them needs a
  separate approved change.
- `m07-cutover-toolkit.yml` runs `tests/scenarios/test_r5_fresh_genesis.py` and
  two more scenario modules and then the whole `tests/scenarios` tree, which
  re-executes them. That duplication is path-filtered to M07 changes and needs an
  approved `ATDD-M07-V2-cutover-toolkit-v1` amendment to remove.

