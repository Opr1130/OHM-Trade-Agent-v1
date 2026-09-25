# Proposed `main` branch protection (awaiting human approval)

**Status:** proposal only. Do **not** apply until an operator explicitly approves
this document’s concrete values. This file grants no access-control change by itself.

Observed baseline (2026-09-14): `main` unprotected (API 404), rulesets `[]`,
no `CODEOWNERS`.

## Required status checks (exact names)

These are the GitHub check **names** as reported on PRs:

| Required? | Check name | Workflow / job | Notes |
| --- | --- | --- | --- |
| **Yes** | `test` | `.github/workflows/pytest.yml` → job `test` | Primary product gate |
| **Yes** | `agent-instruction-contract` | `.github/workflows/agent-governance-contract.yml` | Always reports on PRs to `main` |
| **Yes** | `exact-head-bot-reviews` | same workflow | Exact-HEAD Codex + CodeRabbit presence |
| No | `SonarCloud Code Analysis` | SonarCloud app | Visible; not machine-required unless separately approved |
| No | `ruff (advisory)` / `bandit (advisory)` / `pip-audit (advisory)` / `gitleaks (advisory)` | `quality-security.yml` | Advisory / continue-on-error |
| No | CircleCI Pipeline | external | Broken “No configuration”; do not require |
| No | `claude` / `dispatch` | optional / skipped | Do not require |

## Pull-request and approval settings

| Setting | Proposed value |
| --- | --- |
| Require a pull request before merging | **Yes** |
| Required number of approving reviews | **1** |
| Dismiss stale pull request approvals when new commits are pushed | **Yes** |
| Require approval of the most recent reviewable push | **Yes** |
| Require conversation resolution before merging | **Yes** (human finding triage) |
| Require status checks to pass before merging | **Yes** |
| Require branches to be up to date before merging | **Yes** |
| Require signed commits | No (unless separately mandated) |
| Require linear history | Optional; not required by this proposal |

## Direct-push / force-push / deletion

| Setting | Proposed value |
| --- | --- |
| Restrict who can push to matching branches | **Yes** — only repository administrators listed below may bypass via explicit admin override; no routine direct pushes |
| Allow force pushes | **No** (include administrators) |
| Allow deletions | **No** (include administrators) |

## Bypass permissions (explicit denials)

| Actor class | Bypass PR + required checks? | Force-push / delete `main`? |
| --- | --- | --- |
| Repository administrators | **No** for routine merges — use PR path; break-glass only via temporary settings change with recorded operator approval | **No** |
| Organization owners | Same as administrators | **No** |
| GitHub Apps / bots (`chatgpt-codex-connector`, `coderabbitai`, Claude, Dependabot, SonarCloud, Actions `GITHUB_TOKEN`) | **No** bypass of required checks or PR requirement | **No** |
| Deploy / release automation | **No** merge bypass; deploys remain the existing human-gated control plane on exact `main` SHA | **No** |
| CODEOWNERS | **Not configured** — do not invent owners in this proposal | n/a |

Do **not** enable “Allow specified actors to bypass required pull requests” for bots.

## How exact-HEAD Codex / CodeRabbit are enforced

1. **Machine:** required check `exact-head-bot-reviews` fails closed unless both bots have completed reviews with `commit_id ==` current PR HEAD and verified identity/format (`tools/ci/exact_head_bot_reviews.py`).
2. **Human:** conversation resolution + approving review after triage. A green bot-presence check **never** means findings are clean.
3. **Process still required:** judging whether remapped historical threads still apply; deciding whether Sonar QG must block merge; pairing `/deploy` with `/deploy-learning`.

## Still process-only (even after applying this proposal)

- Substantive finding disposition (Codex P1 / CodeRabbit Major, etc.)
- SonarCloud Quality Gate (unless later added as required)
- Advisory quality-security jobs
- Learning exact-SHA `/deploy-learning` pairing
- Assistant credential scopes / org token permissions
- CODEOWNERS (until an approved roster exists)

## Out of scope

- Applying these settings without explicit human approval
- Changing repository collaborator roles
- Merging PR #237 or PR #238
- Enabling Feature Bus / shadow mode or any trading authority
