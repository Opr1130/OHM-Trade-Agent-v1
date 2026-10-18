# R5 stacked-chain merge readiness: pull requests #369-#373

Increment: `ATDD-R5-STACK-MERGE-READINESS`

Authority granted by this document: `NONE`

This is verification evidence, not a merge authorization. It records, for the
stacked R5/R6/ML-dependency pull-request chain, the dependency-aware order, the
upstream dependencies, the required CI gate set, the gate evidence that actually
exists at each head, the CircleCI status, the regression evidence, and one
proven merge-order interaction with the movable ATDD pointer. Nothing here
merges, re-points, force-pushes, deploys, activates or widens any authority.

Verified on 13 October 2026 from the branch
`feature/opip-atdd-r5-stack-merge-readiness` (base `01381a0d`, stacked on PR
#373's head `dca841132b0dcb1469b94d0bdd3502483def040a`).

## 1. Method and reproduction

Read-only observation; the only writes were local scratch worktrees for the
merge-order experiment in section 5, which were removed afterwards, and no
branch ref was moved.

```bash
gh pr list --state open --limit 30 --json number,baseRefName,headRefName
gh pr view <n> --json number,headRefOid,headRefName,baseRefName,mergeStateStatus,statusCheckRollup
gh run list --workflow pytest.yml --limit 6 --json databaseId,headBranch,headSha,conclusion,createdAt
gh run list --branch <head-branch> --limit 6 --json name,event,conclusion,headSha
gh api repos/<owner>/<repo>/commits/<sha>/status
gh api repos/<owner>/<repo>/branches/main/protection
git merge-base --is-ancestor <lower-branch> <upper-branch>
git show <branch>:.github/workflows/pytest.yml
```

## 2. The chain and its dependency-aware order

Base of pull request `n` is exactly the head branch of pull request `n-1`, so the
chain is linear and has one well-defined order.

| PR | base branch | head branch | head SHA | state |
| --- | --- | --- | --- | --- |
| #362 | `main` | `feature/opip-r5-c-capital-intelligence` | `7a0fb2e357` | open, ready for review |
| #363 | `feature/opip-r5-c-capital-intelligence` | `feature/opip-r5-d-portfolio-planning` | `e3c351a4c4` | open, ready for review |
| #364 | `feature/opip-r5-d-portfolio-planning` | `feature/opip-r5-e-expectation-freeze` | `b781eda033` | open, draft |
| #365 | `feature/opip-r5-e-expectation-freeze` | `feature/opip-r5-e-canonical-reservations` | `4ef5e0e57d` | open, draft |
| #366 | `feature/opip-r5-e-canonical-reservations` | `feature/opip-r5-e-capital-admission-integration` | `12a83efba2` | open, draft |
| #367 | `feature/opip-r5-e-capital-admission-integration` | `feature/opip-r5-e-research-paper-execution` | `f2484c85b4` | open, draft |
| #368 | `feature/opip-r5-e-research-paper-execution` | `feature/opip-r5-e-portfolio-lifecycle-closure` | `798eb32fa8` | open, draft |
| #369 | `feature/opip-r5-e-portfolio-lifecycle-closure` | `feature/opip-r5-e-f11-independent-protection` | `fcf399936f` | open, draft |
| #370 | `feature/opip-r5-e-f11-independent-protection` | `feature/opip-r5-f-prospective-outcome-integration` | `fd6fa7e291` | open, draft |
| #371 | `feature/opip-r5-f-prospective-outcome-integration` | `feature/opip-r6-committee-shadow-review-readiness` | `573ab567bc` | open, draft |
| #372 | `feature/opip-r6-committee-shadow-review-readiness` | `feature/opip-release-pipeline-stack-gates` | `fba8440f47` | open, draft |
| #373 | `feature/opip-release-pipeline-stack-gates` | `feature/opip-atdd-ml-dependency-policy-v1` | `dca841132b` | open, draft |

All five pull requests in scope (#369-#373) are drafts and report
`MERGEABLE`/`UNSTABLE`: mergeable, with non-required checks failing (section 6).

Ancestry proof (each head contains the previous head; all five edges `YES`):

```text
portfolio-lifecycle-closure -> f11-independent-protection = YES
f11-independent-protection  -> r5-f-prospective-outcome-integration = YES
r5-f-prospective-outcome-integration -> r6-committee-shadow-review-readiness = YES
r6-committee-shadow-review-readiness -> release-pipeline-stack-gates = YES
release-pipeline-stack-gates -> atdd-ml-dependency-policy-v1 = YES
```

Consequence of that construction: merging pull request `n` lands its commits into
the head branch of pull request `n-1`. The chain therefore reaches `main` only
through the lower pull requests, and merge order is a chain property rather than
a per-pull-request choice.

## 3. The required gate set, and where it comes from

`main` has **no branch protection**: `gh api repos/<owner>/<repo>/branches/main/protection`
returns HTTP 404 `Branch not protected`. There is therefore no GitHub-enforced
required-context list, and "required" is a contractual property here, not a
protection setting. Two contractual sources define it:

1. `.github/workflows/pytest.yml` - the release workflow. Its jobs are `test`
   (`name: test`), `atdd-scope` (`name: atdd scope`), `architecture-gate`
   (`name: release architecture gate`), `security-gate`
   (`name: release security gate (Bandit)`) and `release-candidate`
   (`name: non-deploying main release candidate`, gated by
   `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`, so it is
   skipped on every pull request by design).
2. `docs/engineering/LOCAL_AGENT_BRIDGE_V1.md` section 12.2 - `test`, `atdd scope`
   and `semgrep/ci` are "structurally mandatory ... and cannot be removed by a
   policy"; a missing protected context "is treated as pending, never as
   satisfied"; "neutral/skipped checks do not count as success"; and advisory jobs
   (`ruff (advisory)`, `bandit (advisory)`, `pip-audit (advisory)`,
   `gitleaks (advisory)`) plus "unrelated failures such as `CircleCI Pipeline`"
   are "deliberately not required".

Required for these pull requests, therefore: `test`, `atdd scope`,
`release architecture gate`, `release security gate (Bandit)` and `semgrep/ci`.
`non-deploying main release candidate` is required on `main` only.

Not required: `CircleCI Pipeline`, `CodeRabbit`, `Gitar`, `claude`, `dispatch`,
`PR69 AI Review Dispatcher` and the external-review workflows.

## 4. Gate evidence at each head (what actually ran)

| PR | head SHA | `test` | `atdd scope` | `release architecture gate` | `release security gate (Bandit)` | `semgrep/ci` | merge readiness |
| --- | --- | --- | --- | --- | --- | --- | --- |
| #369 | `fcf399936f` | NOT RUN | NOT RUN | NOT RUN | NOT RUN | SUCCESS | NO |
| #370 | `fd6fa7e291` | NOT RUN | NOT RUN | NOT RUN | NOT RUN | SUCCESS | NO |
| #371 | `573ab567bc` | NOT RUN | NOT RUN | NOT RUN | NOT RUN | SUCCESS | NO |
| #372 | `fba8440f47` | FAILURE | SUCCESS | SUCCESS | SUCCESS | SUCCESS | NO |
| #373 | `dca841132b` | SUCCESS | SUCCESS | SUCCESS | SUCCESS | SUCCESS | YES (gate evidence) |
| #374 | `01381a0dcd` | SUCCESS | SUCCESS | SUCCESS | SUCCESS | SUCCESS | YES (gate evidence) |

Why #369-#371 have no gate evidence: the workflow's pull-request trigger only
matches `feature/**` from commit `fba8440f` onward (increment
`ATDD-RELEASE-PIPELINE-v1`, PR #372). The workflow file at those three heads is
still `branches: [main]`, so no `pytest` workflow run exists for them at all:

```text
feature/opip-r5-e-f11-independent-protection      pytest.yml trigger: branches: [main]
feature/opip-r5-f-prospective-outcome-integration pytest.yml trigger: branches: [main]
feature/opip-r6-committee-shadow-review-readiness pytest.yml trigger: branches: [main]
feature/opip-release-pipeline-stack-gates         pytest.yml trigger: branches: [main, 'feature/**']
feature/opip-atdd-ml-dependency-policy-v1         pytest.yml trigger: branches: [main, 'feature/**']
```

```text
gh run list --branch feature/opip-r5-e-f11-independent-protection     -> no pytest run
gh run list --branch feature/opip-r5-f-prospective-outcome-integration -> no pytest run
gh run list --branch feature/opip-r6-committee-shadow-review-readiness -> no pytest run
```

This is an upstream-dependency finding, not a branch defect: those three heads
are gated only once the widened trigger is present in their base chain, which
happens when the chain is landed downwards (section 7), or when their heads are
refreshed onto it.

## 5. Regression evidence

- Base red at `fba8440f47` (PR #372 head). Workflow run `38413239394`, job `test`:
  `1 failed, 8157 passed, 9 skipped, 6 warnings in 408.93s`. The other four jobs
  ran: `atdd scope` success, `release architecture gate` success,
  `release security gate (Bandit)` success, `non-deploying main release candidate`
  skipped by design. The single failure is the false-positive no-ML-dependency
  guard repaired by PR #373 (increment `ATDD-ML-DEPENDENCY-POLICY-v1`), where the
  substring check matched the ordinary words "shape", "shaped" and "reshape" in
  prose while `app/opip` had zero ML imports.
- Green at `dca841132b` (PR #373 head). Workflow run `38422747017`, job `test`:
  success, with `atdd scope`, `release architecture gate` and
  `release security gate (Bandit)` all success and the release candidate skipped
  by design. The increment's recorded full-suite result at that head on Linux CI
  is `8172 passed, 9 skipped, 6 warnings` (362.79s).
- #369-#371: no suite has ever run at those heads, so their regression evidence is
  **absent**, not green. Nothing may be inferred from the green result at #373,
  which sits 4 commits above them.
- #374 (the F10 contract-preparation increment stacked above this one,
  `01381a0d`) ran workflow run `38436089898`: `test` success, `atdd scope`
  success, `release architecture gate` success, `release security gate (Bandit)`
  success, `non-deploying main release candidate` skipped by design. Its required
  gate evidence is therefore complete and green at that head; it is still blocked
  from landing by the merge mechanics in section 7, not by a gate.
- Local Windows runs are not the authority; the repository's own triage pattern is
  `docs/atdd/evidence/R4-F8-BASELINE-TRIAGE.md`. Windows-only failures observed on
  this machine reproduce at the base commit and are not attributed to any
  increment in this chain.


## 6. CircleCI and the other non-required failures

`CircleCI Pipeline` reports `error` with the description
"No configuration was found in your project. Please refer to
https://circleci.com/docs/2.0/ to get started with your configuration." on all
five heads and on `main` itself (`54ea31b4a86128858db2e73b799a4ad6dd8629e3`):

```text
fcf399936f (PR #369 head)  -> error | No configuration was found in your project
fd6fa7e291 (PR #370 head)  -> error | No configuration was found in your project
573ab567bc (PR #371 head)  -> error | No configuration was found in your project
fba8440f47 (PR #372 head)  -> error | No configuration was found in your project
dca841132b (PR #373 head)  -> error | No configuration was found in your project
54ea31b4   (origin/main)   -> error | No configuration was found in your project
```

Classification: external project-level configuration absence, identical on
`main`, therefore pre-existing and unrelated to this chain, and contractually
"deliberately not required" (section 3). It explains the `UNSTABLE` merge state
and is not a merge blocker.

The external review workflows fail on every branch, including branches outside
this chain (`pr69-external-reviews`, `pr69-external-reviews-manual`,
`pr69-external-reviews-repository-dispatch`, `gemini-review`,
`deepseek-quant-review`; all `event=push`, `conclusion=failure`). `CodeRabbit`
reports success with the note "Review skipped: draft pull request", and `claude`
and `dispatch` are skipped. All of these are advisory or draft-related and are
not required.

## 7. Upstream dependency: merge order interacts with the movable ATDD pointer

The chain's construction (section 2) means merging pull request `n` lands its
commits into the head branch of pull request `n-1`. That head branch is the
`github.base_ref` of the pull request below, and the `atdd scope` job evaluates
`git diff --name-status origin/<base_ref>...HEAD` against the increment named by
`docs/atdd/ACTIVE_INCREMENT` **at that head**. Because every increment moves that
pointer to itself, a merge into a base branch changes the pointer under the pull
request whose head that branch is, and the pull request below then fails a gate it
had previously passed.

Reproduced locally (scratch detached worktrees; no branch ref moved, no push):

```bash
# baseline: the pull request below passes
git worktree add --detach <scratch> feature/opip-r5-e-portfolio-lifecycle-closure
cd <scratch> && cd OHM-Trade-Agent-v1
python tests/atdd_scope.py --git-base feature/opip-r5-e-research-paper-execution
#   PASS   (exit 0)

# merge PR #369's head into its base, which is PR #368's head branch
git merge --no-ff -m sim feature/opip-r5-e-f11-independent-protection
#   pointer at the head is now ATDD-R5-E-F11-independent-protection-validation
python tests/atdd_scope.py --git-base feature/opip-r5-e-research-paper-execution
#   SCOPE_CHANGE_REQUIRED (exit 2): three PR #368 files reported as
#   "changed file not mapped to an approved AC"

# symmetric check in the other direction
git checkout --detach feature/opip-release-pipeline-stack-gates
git merge --no-ff -m sim feature/opip-atdd-ml-dependency-policy-v1
python tests/atdd_scope.py --git-base feature/opip-r6-committee-shadow-review-readiness
#   SCOPE_CHANGE_REQUIRED (exit 2): PR #372 files reported as unmapped
```

Consequences that must be decided by the OWNER, because they change how the stack
is landed rather than how it is verified:

1. Merging any pull request in this chain invalidates the `atdd scope` gate of the
   pull request directly below it, because that gate reads the pointer from the
   new head and the diff from the base. The failure is a false scope violation
   caused by merge mechanics, not a real unauthorized change.
2. The chain therefore cannot be landed one pull request at a time without also
   restoring each lower head to its own increment (a re-point or rebuild per
   level), and it cannot be landed top-down for the same reason. Both the order
   and the pointer restoration are owner-gated actions.
3. Until that is decided, no pull request in this chain is "merge ready" in the
   dependency-aware sense, even where its own gate evidence is complete (#373, and
   #374 once its run finishes).
4. This is a mechanical conflict between "one increment owns the pointer" and "a
   stack lands into base branches". It is recorded, not fixed, here; changing the
   pointer mechanism or the scope checker needs its own increment.


## 8. Verdict

| PR | required-gate evidence | regression evidence | dependency-aware verdict |
| --- | --- | --- | --- |
| #369 | absent (no workflow run) | none | NOT READY |
| #370 | absent (no workflow run) | none | NOT READY |
| #371 | absent (no workflow run) | none | NOT READY |
| #372 | complete, `test` FAILED | 1 failed / 8157 passed at `fba8440f47` | NOT READY (red at its own head) |
| #373 | complete and green at `dca841132b` | 8172 passed, 9 skipped | gate-ready; blocked by the merge-order and pointer decision |
| #374 | complete and green at `01381a0d` (run `38436089898`) | not re-run locally; Linux CI is the authority | gate-ready; blocked by the merge-order and pointer decision |

Unresolved blockers reported to the OWNER:

1. The merge-order and pointer-restoration procedure for a linear stack whose
   every level moves the active-increment pointer (section 7). Until it is decided
   and authorized, no level can be landed safely.
2. #372's own head is red, so landing it as-is lands a failing `test` gate; the
   repair exists only above it, in #373's commits.
3. #369-#371 have never run the required gates, so their readiness is unproven
   rather than proven, and the widened trigger reaches them only when the chain is
   landed downwards or their heads are refreshed.
4. Whether the chain is landed as one authorized sequence (with the F10 increment
   #374 and this increment above it) or is collapsed first is an owner decision;
   this evidence does not choose.
5. `non-deploying main release candidate` is skipped by design on every pull
   request, so no pull request can ever show that receipt; it becomes evidence
   only for the `main` push that follows a merge.

## 9. What this increment does not authorize

No merge, no squash, no rebase, no force-push, no re-point of
`docs/atdd/ACTIVE_INCREMENT`, no branch-protection or ruleset change, no workflow
change, no deployment, no release-profile change, no activation and no trading,
paper, Committee, Telegram or exchange authority. It adds no gate and weakens
none.

