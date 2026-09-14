# Agent governance contract (docs / CI)

This document describes the **non-PR3** instruction and structural-CI plane.
It does not change Feature Bus, trading authority, risk, ranking, Telegram,
or deployment behavior.

## Instruction consistency vs runtime enforcement

| Kind | What it does | What it does **not** do |
| --- | --- | --- |
| `AGENTS.md` + IDE adapters | Tell assistants the policy | Sandbox tokens, block merges, or revoke credentials |
| `.github/workflows/agent-governance-contract.yml` | Fail CI if required instruction files are missing or stop deferring to `AGENTS.md` | Enforce trading safety at runtime or require human reviews |
| Deploy / pytest / paper / learning workflows | Product and release gates | Substitute for GitHub branch protection |
| GitHub branch protection / rulesets / CODEOWNERS | Machine-enforced merge controls (when configured) | Exist in this repository today (see below) |

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

## Missing operator settings (not applied by this PR)

These require human approval in GitHub repository settings:

1. **Branch protection or rulesets on `main`** — currently unprotected (API 404 / empty rulesets).
2. **CODEOWNERS** — none present; do not invent owners in-repo without an approved roster.
3. **Exact-head Codex/CodeRabbit SHA matching** — process language in `AGENTS.md` only; review bots are not machine-enforced merge gates unless listed as required checks.
4. **Assistant credential scopes** — org/token and Actions secret permissions; instruction files cannot revoke them.

## CI added by this branch

Workflow: `.github/workflows/agent-governance-contract.yml`

- Fails if required instruction adapters are missing.
- Fails if `AGENTS.md` drops key mandatory phrases.
- Fails if adapters stop deferring to `AGENTS.md` or contain banned authority phrases.

This is **instruction consistency** only.
