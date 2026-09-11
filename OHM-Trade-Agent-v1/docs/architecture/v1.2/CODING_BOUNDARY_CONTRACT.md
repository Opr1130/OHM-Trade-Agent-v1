# Coding-boundary contract

Planned CI enforcement (documentation in PR 1; import-linter enforcement is **not** required to merge PR 1).

| Layer | Allowed dependencies | Forbidden |
| --- | --- | --- |
| `contracts/` | none of the runtime infrastructure | storage, exchanges, Telegram, AI, jobs |
| `detectors/` | contracts and math only | network, disk, DB, clocks, globals, AI |
| `safety/` | market facts, positions, coverage, writer intents | detector, forecast, economics, AI |
| `forecast/` | snapshots, policy versions, math | writing execution state |
| `ai/` | offline research tools | import by runtime decision modules |
| `storage/` | explicit transaction boundaries | market calls, feature compute, export, AI inside write txns |

Additional rules:

- New authority requires a written ownership decision.
- No new pipeline without a named replacement and a retirement gate.
- Engineering AI (Claude gateway / Cursor) must not gain order, Telegram execution, or exchange authority.

PR 1 fixture tests read docs and JSON only. They do not import `app.services` trading modules.
