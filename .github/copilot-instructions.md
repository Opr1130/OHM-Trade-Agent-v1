# Copilot review instructions

Copilot review is advisory only. It must never replace deterministic architecture and deployment gates.

Review specifically for:

- duplicate authority
- second source of truth
- second scheduler
- hidden config activation
- stale `.env` activation
- authority widening
- Paper-v2 activation
- funded/exchange leakage
- point-in-time leakage
- missing fail-closed behavior
- missing rollback
- broken idempotency
- cursor/replay errors
- protection coupling
- unbounded process state
- missing provenance
- tests claiming production state without runtime evidence

Require the release pipeline to remain conservative: no funded/live authority, no second paper engine, and no implicit authority transition. The deterministic gates must still be the final authority.
