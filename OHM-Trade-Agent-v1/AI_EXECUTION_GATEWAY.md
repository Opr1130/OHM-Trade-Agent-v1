# O'Pip AI Execution Gateway v1

## Purpose

Phase A provides a low-cost, bounded engineering-AI control plane for Claude Code without adding a second O'Pip runtime stack. Phase C makes that control plane independent of the production droplet so the existing DigitalOcean migration can proceed without rebuilding Claude integration.

This gateway is for **software engineering automation**. It is not the trading-runtime Chief Analyst and has no order authority.

## Boundary

```text
ChatGPT / operator
        |
        | GitHub issue or PR comment
        v
GitHub Actions: opip-claude-code
        |
        | bounded execution profile
        v
Claude Code / Anthropic API
        |
        | branch, commit, PR, comments
        v
GitHub CI
        |
        v
existing production deployment control plane
        |
        | exact approved + tested main SHA
        v
DigitalOcean Docker Compose runtime
```

The Anthropic credential remains a GitHub Actions secret. It is not added to `.env`, Docker Compose, the application image, the canonical scheduler, or the production deployment user's credential set.

## Cost profiles

| Trigger | Model | Max turns | Hard API budget |
| --- | --- | ---: | ---: |
| `@claude cheap ...` | `haiku` | 6 | $0.40 |
| `@claude ...` | `sonnet` | 12 | $1.50 |
| `@claude standard ...` | `sonnet` | 12 | $1.50 |
| `@claude deep ...` | `sonnet` | 20 | $3.50 |

The profile resolver is versioned in `tools/ai_gateway/profiles.py`. The workflow passes both `--max-turns` and `--max-budget-usd` to Claude Code. It also uses `--exclude-dynamic-system-prompt-sections` to improve prompt-cache reuse across ephemeral GitHub runners.

Only the repository owner can trigger the workflow. This is required because the repository is public and each successful trigger can consume paid API capacity.

## Telemetry

Each run writes a non-sensitive JSON receipt containing:

- provider and selected profile;
- model and configured hard limits;
- observed cost and turn count when Claude Code exposes a result record;
- token/cache counters when present in the execution log;
- GitHub repository, run ID, event type, and actor.

The receipt intentionally excludes prompts, source contents, API keys, and Claude's full execution transcript. It is retained as a GitHub Actions artifact rather than written into the production data volume. That keeps engineering-AI telemetry durable across droplet replacement without creating a second database or contaminating trading telemetry.

## Phase C: infrastructure-move behavior

The gateway is deliberately outside the DigitalOcean application runtime:

- no new container;
- no new cron entry or scheduler;
- no Redis, PostgreSQL, queue, or managed service;
- no new public port;
- no Anthropic SDK in production requirements;
- no Anthropic secret on the droplet;
- no change to the existing forced-SSH production deployment boundary.

A future DigitalOcean replacement therefore only needs the existing repository, Docker Compose application, durable O'Pip data plane, and deployment bootstrap. Claude engineering automation continues to operate from GitHub unchanged.

## One-time activation

Repository code can be merged before credentials exist. To activate paid Claude execution, configure exactly one GitHub Actions repository secret:

`ANTHROPIC_API_KEY`

The workflow uses the built-in `GITHUB_TOKEN` for repository writes, so this design does not require a separate long-lived GitHub personal access token. The Anthropic key must never be copied into O'Pip production `.env`.

## Operating flow

1. Create or identify a GitHub issue/PR.
2. The repository owner posts `@claude cheap`, `@claude`, or `@claude deep` followed by the implementation request.
3. Claude Code works under the selected hard dollar/turn limits and the repository `CLAUDE.md` contract.
4. Existing pytest CI validates the resulting PR.
5. Human/ChatGPT review occurs in GitHub.
6. Production deployment remains a separate explicit approval through the existing deployment workflow.
