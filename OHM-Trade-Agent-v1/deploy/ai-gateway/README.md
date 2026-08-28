# O'Pip AI Gateway Infrastructure Contract

This directory records the Phase C deployment boundary for engineering AI.

## Control plane versus data plane

Claude Code runs in GitHub Actions. The production DigitalOcean droplet does **not** run Claude Code and does not need `ANTHROPIC_API_KEY`.

This keeps the existing O'Pip infrastructure move lean:

- the current Docker Compose application remains the runtime unit;
- the canonical scheduler remains the only application scheduler;
- `/app/data` remains the production persistent data plane;
- existing forced-SSH GitHub deployment remains the production mutation path;
- engineering-AI usage receipts remain GitHub Actions artifacts and therefore survive droplet replacement.

## Secret placement

| Secret | Location | Production container |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | GitHub Actions repository secret | **Never** |
| O'Pip runtime secrets | production `.env` / existing deployment practice | existing behavior only |
| GitHub deploy SSH material | existing GitHub deployment secret + forced server key | existing behavior only |

Do not merge the Anthropic credential into `.env`, Docker Compose, bootstrap output, or deployment SSH credentials.

## Non-overlap invariant

If a future change proposes a Claude sidecar, AI scheduler, AI database, AI queue, or second deployment plane, it requires a separate architecture decision. None is necessary for Phase A or Phase C.
