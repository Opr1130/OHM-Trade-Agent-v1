# O'Pip Claude Code Contract

## Repository layout

- The GitHub repository root is a wrapper. Application code lives under `OHM-Trade-Agent-v1/`.
- `OHM` names that already exist are legacy technical identifiers. Do not bulk-rename them in unrelated work.
- New user-facing naming and new architecture documentation should use **O'Pip**.

## Engineering operating model

- Implement the smallest change that satisfies the issue or PR request.
- Reuse existing services, registries, Docker Compose, scheduler, deployment wrappers, telemetry, and data paths before creating new infrastructure.
- Do not introduce Kubernetes, Redis, a managed database, a second scheduler, a second deployment control plane, or additional droplets unless the task explicitly requires an approved architecture change.
- Keep compute replaceable and durable evidence under the existing data-plane conventions.
- Never commit credentials, tokens, private keys, `.env` values, or generated secrets.

## Trading safety boundary

- Coding automation has no trading authority.
- Do not add or widen Kraken order-placement, modification, cancellation, confirmation, or production execution permissions unless the task explicitly scopes and approves that change.
- Keep paper/dry-run execution isolated from production trading authority.
- External AI is advisory evidence. Deterministic safety and quality gates must continue to fail closed when external AI is unavailable.

## Implementation quality

- Preserve existing public contracts unless the task explicitly changes them.
- Add or update focused tests for behavioral changes.
- Run the smallest relevant pytest target first, then the full suite when practical.
- Run `python -m compileall -q app` for Python changes.
- Do not weaken tests simply to make CI pass.
- Surface assumptions and unresolved risks in the PR summary.

## AI cost discipline

- Repository-triggered Claude work is bounded by the O'Pip AI execution profile selected by the trigger.
- Prefer `@claude cheap` for repository exploration, documentation, and very small changes.
- Use plain `@claude` for normal implementation work.
- Use `@claude deep` only for unusually difficult multi-file debugging or refactoring.
- Do not bypass or raise `--max-budget-usd` or `--max-turns` inside an implementation task.
