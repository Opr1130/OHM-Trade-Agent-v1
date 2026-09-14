# Agent governance contract (docs / CI)

This document describes the **non-PR3** instruction and structural-CI plane.
It does not change Feature Bus, trading authority, risk, ranking, Telegram,
or deployment behavior.

## Instruction consistency vs runtime enforcement

| Kind | What it does | What it does **not** do |
| --- | --- | --- |
| `AGENTS.md` + IDE adapters | Tell assistants the policy | Sandbox tokens, block merges, or revoke credentials |
| `agent-instruction-contract` job | Fail CI if required instruction files are missing or stop deferring to `AGENTS.md` | Enforce trading safety at runtime |
| `exact-head-bot-reviews` job | Fail closed unless completed Codex **and** CodeRabbit reviews exist on the **exact** PR HEAD SHA with verified bot identities/formats | Certify that substantive findings are resolved or that the PR is merge-clean |
| Deploy / pytest / paper / learning workflows | Product and release gates | Substitute for GitHub branch protection |
| GitHub branch protection / rulesets / CODEOWNERS | Machine-enforced merge controls (when configured) | Exist in this repository today until an operator applies the approved proposal |

Assistant instruction files are **guidance**. Real access control is workflow `permissions:`, secret availability, deploy forced commands, and (when enabled) branch protection.

## Canonical policy adapters

| Layer | Path | Role |
| --- | --- | --- |
| Canonical shared policy | `AGENTS.md` | Single engineering authority for all assistants |
| Claude adapter | `CLAUDE.md` | Claude Code contract; must defer to `AGENTS.md` |
| Gemini adapter | `GEMINI.md` | Entry point; must defer to `AGENTS.md` |
| Copilot / VS Code | `.github/copilot-instructions.md` | Entry point; grants no independent permission |
| Cursor always-on | `.cursor/rules/opip-shared-governance.mdc` | Points agents at `AGENTS.md` |
| Focused Cursor rules | `.cursor/rules/opip-*.mdc` | Domain supplements; must not weaken `AGENTS.md` |

## Already enforced (product / workflow)

- Deploy workflows require human-gated production / learning / analytics paths.
- `pytest.yml` and product contract workflows run on PRs.
- `quality-security.yml` is advisory (`continue-on-error`) and must not gate deploy.
- Claude Code workflow permissions are constrained; engineering AI has no order authority.
- Feature Bus default remains `OPIP_FEATURE_BUS_MODE=off` in application config (product invariant; not owned by this docs PR).

## CI added by this branch

Workflow: `.github/workflows/agent-governance-contract.yml`

### `agent-instruction-contract` (every PR → `main`)

- Runs on **every** pull request targeting `main` (no path filter), so the check always reports.
- Fails if required instruction adapters are missing.
- Fails if `AGENTS.md` drops key mandatory phrases.
- Fails if adapters stop deferring to `AGENTS.md` or contain banned authority phrases.
- Runs unit tests for `tools/ci/exact_head_bot_reviews.py`.

### `exact-head-bot-reviews` (every PR → `main`)

Script: `tools/ci/exact_head_bot_reviews.py`

**Verified identities (exact login match only):**

| Bot | GitHub login | User id (observed) |
| --- | --- | --- |
| Codex | `chatgpt-codex-connector[bot]` | `199175422` |
| CodeRabbit | `coderabbitai[bot]` | `136622811` |

**Completed review states accepted:** `COMMENTED`, `APPROVED`, `CHANGES_REQUESTED`.  
`PENDING`, `DISMISSED`, and issue-comment `@codex` / `@coderabbitai` **requests** do not count.

**Format requirements (fail closed if ambiguous):**

- Codex body must include `**Reviewed commit:** \`<sha-prefix>\`` where the prefix matches the exact HEAD.
- CodeRabbit body must be non-empty and include a recognized summary marker (`Actionable comments`, `Duplicate comments`, `Nitpick comments`, `Review details`, or `Walkthrough`).
- `commit_id` on the review object must equal the full 40-character PR HEAD SHA.
- Lookalike logins containing `codex` / `coderabbit` that are not in the allow-list fail as ambiguous.

**Explicit non-claims:**

- A green `exact-head-bot-reviews` check means **presence + identity + format** only.
- Findings disposition is always `HUMAN_TRIAGE_REQUIRED`.
- Unresolved P1 / Major inline findings, remapped historical threads, and “is this still valid?” judgment are **out of scope** for the machine gate.

## Missing operator settings (not applied by this PR)

See [`branch-protection-proposal.md`](branch-protection-proposal.md). Do not apply repository settings until that proposal is explicitly approved.
