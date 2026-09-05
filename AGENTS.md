# O'Pip Agent Guardrails

Repository-level instructions for all coding agents (Cursor, Claude Code, and similar).

These rules **complement** and do **not** replace:

- [`CLAUDE.md`](CLAUDE.md) — Claude Code contract (layout, engineering model, trading safety, quality, AI cost)
- Existing deployment, CI, paper/Freqtrade, analytics, and promotion contracts under `OHM-Trade-Agent-v1/` and `.github/workflows/`

Do not overwrite, bypass, or weaken those controls.

## Scope

- GitHub repository root is a wrapper; application code lives under `OHM-Trade-Agent-v1/`.
- `OHM` identifiers are legacy technical names. Do not bulk-rename them in unrelated work.
- New user-facing naming and architecture docs should use **O'Pip**.

## Mandatory principles

1. **Never commit directly to `main`.** Use a feature branch for all code changes.
2. **Never deploy to production unless explicitly authorized** by the human operator through the existing deployment control plane (exact approved + tested `main` SHA). Do not invent alternate deploy paths.
3. **Never enable live trading, live exchange execution, or increase trading authority.** Coding automation has no trading authority. Do not add or widen Kraken order placement, modification, cancellation, confirmation, or production execution permissions unless the task explicitly scopes and approves that change.
4. **Paper trading and Freqtrade dry-run must remain isolated from production authority.** They must not write to Kraken private APIs, mutate live registries/order paths, or share production execution credentials/authority.
5. **AI-generated recommendations cannot override deterministic risk rejection or execution-safety gates.** External AI is advisory evidence. Deterministic safety and quality gates must fail closed when external AI is unavailable.
6. **Preserve fixed risk sizing, minimum R:R requirements, position verification, and execution safeguards.** Do not loosen defaults such as fixed account risk and minimum reward-to-risk without an explicitly approved contract change.
7. **Never expose, print, copy into source, log, or commit secrets**, API keys, tokens, credentials, private keys, `.env` values, or production environment values. Do not read or disclose secret values merely to inspect configuration. Secret creation, rotation, replacement, or deletion is allowed only when explicitly instructed by the human operator and only through the repository's approved secret-management/control-plane mechanism. Never place secret values in source files, images, logs, prompts, or unauthorized hosts.
8. **Treat production data stores and canonical learning evidence as durable evidence.** Do not destructively mutate, truncate, rewrite, or “clean up” them without an explicitly reviewed migration. Prefer additive, fail-closed changes.
9. **Preserve separation between live production, shadow evidence, and offline learning/analytics planes.** Shadow/ML/analytics paths must not gain production admission, Telegram execution, or exchange authority by accident.
10. **Do not move PostgreSQL/analytics workloads onto the trading host.** Analytics stays on the separate learning/analytics plane; the trading droplet remains free of that database footprint.
11. **Production code changes require a feature branch, tests, review, and PR.** Do not treat local edits or agent branches as production-ready without that path.
12. **Before proposing a commit, run relevant validation:** focused pytest first, then broader suite when practical; for Python changes also `python -m compileall -q app` from `OHM-Trade-Agent-v1/`; honor other repository contract checks when touched (for example Freqtrade contract CI).
13. **Do not silently modify tests merely to make failures disappear.** Fix the product bug or update tests only with an explicit, reviewed behavior/contract change.
14. **Do not bypass failing CI, security checks, health checks, deployment locks, or rollback controls.**
15. **Prefer the smallest change that solves the proven problem.** Reuse existing services, Compose, scheduler, deployment wrappers, telemetry, and data paths before adding infrastructure. Do not introduce Kubernetes, Redis, a managed database, a second scheduler, a second deployment control plane, or additional droplets unless the task explicitly requires an approved architecture change.
16. **Distinguish observed evidence from hypotheses.** Do not claim a production root cause without durable evidence. Label assumptions and unresolved risks clearly.
17. **Do not create or push commits until explicitly instructed** by the human operator.
18. **Do not merge PRs or run deployment commands unless explicitly instructed** by the human operator.
19. **Treat generated artifacts as non-source.** Do not commit `__pycache__/`, `*.pyc`, logs, local databases, caches, virtualenvs, temporary files, or similar build/runtime output.
20. **Before any high-impact change** involving scoring, ranking, alerts, risk, execution, learning authority, production deployment, or data durability, **explicitly summarize** the proposed behavioral impact and safety implications, then wait for direction if authorization is unclear.

## Existing controls to preserve (do not weaken)

| Control | Where |
| --- | --- |
| Claude Code contract | `CLAUDE.md` |
| Production deploy: owner approval, exact `main` SHA, pytest gate, SSH forced-command, lock, health, rollback | `.github/workflows/deploy-production.yml`, `OHM-Trade-Agent-v1/deploy/remote/` |
| Paper Trade isolation | `OHM-Trade-Agent-v1/docs/PAPER_TRADING_V1.md`, paper/Freqtrade contract tests & CI |
| Freqtrade dry-run isolation | `.github/workflows/freqtrade-contract.yml`, `OHM-Trade-Agent-v1/freqtrade/`, paper compose |
| Alert-only / fixed risk / min R:R / AI cannot override risk | `OHM-Trade-Agent-v1/README.md` and runtime risk/execution gates |
| AI engineering gateway has no order authority | `OHM-Trade-Agent-v1/AI_EXECUTION_GATEWAY.md`, `.github/workflows/opip-claude-code.yml` |
| Analytics/PostgreSQL off trading host; production files remain WAL | `OHM-Trade-Agent-v1/deploy/analytics/README.md` |
| Shadow/ML non-authority & promotion gates | `OHM-Trade-Agent-v1/OHM_P1_PROMOTION_GATES.md`, ML evidence docs |
| Secrets / generated artifacts ignored | `OHM-Trade-Agent-v1/.gitignore` |

## High-impact change checklist

When principle 20 applies, state before implementing:

1. What behavior changes in production vs shadow vs offline learning.
2. Whether trading authority, risk gates, or execution safeguards change.
3. Whether durable evidence/stores are written, migrated, or deleted.
4. Which tests/CI contracts will prove the change is safe.
5. What remains explicitly out of scope.
