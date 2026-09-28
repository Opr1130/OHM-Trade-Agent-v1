# O'Pip runtime truth - 2026-09-28

This file is the current post-R2 runtime observation. `OPIP_RUNTIME_TRUTH_2026-09-27.md` is preserved unchanged as the preceding historical observation. This file records only what the owner-gated control plane actually printed. It does not re-probe the host, and it does not infer values that were never observed.

## How this was observed

No SSH session was opened. The production forced command is `deploy <sha>`. A new session would be a deploy, which this reconciliation must not run.

Evidence is the owner-gated GitHub Actions production deploy run for the current `main` SHA, `36473910247` ("OHM Production Deployment Approvals"). Host names, users, and secret-like lines are excluded. This is the last control-plane observation, not a re-probe at reconciliation close.

`origin/main` at reconciliation close is `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`.

## Release identity

| Plane | Last successful control-plane SHA | When | What that proves |
| --- | --- | --- | --- |
| Production core | `facf8e369e1251697bf9799bc9b1c575a9cdc3ec` | 2026-09-28T19:47:02Z, run 36473910247 | Deploy job `deploy` SUCCESS. `OPIP_CORE_DEPLOY_STATUS=SUCCESS`, `OPIP_CORE_POSTCOMMIT_HEALTH=OK`, rollback NO. |
| Canonical replica export from core | same SHA | same run | `OPIP_LEARNING_EXPORT_STATUS=SUCCESS`, `OPIP_LEARNING_READINESS=READY` (`LEARNING_EXPORT_READY`), `OPIP_LEARNING_EXPORT_ATTEMPTS=1`. |
| Paper registry genesis | same SHA | same run | `OPIP_PAPER_REGISTRY_GENESIS_STATUS=OK`, `ALREADY_INITIALIZED` (`PAPER_REGISTRY_STATE_PRESENT_VALID`); initialization marker VALID and preserved; `state.json` present and valid. |
| Scheduler reconciliation | same SHA | same run | `O'Pip scheduler reconciliation: OK`. |
| Stream worker reconciliation | same SHA | same run | `O'Pip stream worker activation check failed` and `O'Pip stream worker reconciliation: degraded (rc=1); shadow evidence unavailable or incomplete; production core unaffected`. Recorded as an ops/deferred observation; not erased. |
| P1 shadow outbox retirement | same SHA | same run | `OPIP_P1_RETIREMENT_STATUS=SUCCESS`. |

The learning-worker code and the current core are not asserted here to be the same SHA. This observation did not read a new `/deploy-learning` result for `facf8e36`. Whether the worker has failed closed since the deploy was not re-read. That live disposition stays `UNKNOWN_NEEDS_EVIDENCE`.

## Containers observed at the end of the deploy log

| Container | Observation at 2026-09-28T19:46:59Z |
| --- | --- |
| `ohm-trade-agent` | Up, healthy. `127.0.0.1:8000->8000/tcp`. Final health JSON clean. |
| `opip-canonical-writer` | Up, healthy. |
| `opip-stream-worker` | Up, healthy at this final container observation (reconciliation for it still reported degraded rc=1). |
| `ohm-freqtrade-paper` | Image `freqtradeorg/freqtrade:2026.7`. Up, healthy. |
| `ohm-freqtrade-paper-usdt` | Same image. Up, healthy. |

## Mode flags

Checked-in compose at this SHA, which the deploy built:

| Flag | Checked-in core value | Live host beyond that file |
| --- | --- | --- |
| `OPIP_FEATURE_BUS_MODE` | `off` pinned on core (`docker-compose.yml`) | Not printed as a process value. Pin is in the deployed compose. No Feature Bus activation occurred. |
| `OPIP_CANONICAL_WRITER_MODE` | `shadow` on core | Writer container healthy. Mode value inside the process was not printed. |
| `OPIP_PAPER_V2_MODE` | Not set in compose. Settings default `off`. | `UNKNOWN_NEEDS_EVIDENCE` |
| `OPIP_COMMITTEE_MODE` | Not a core-service flag | Not re-observed by this deploy. |

Arbitrary environment was not dumped.

## What was not observed

- Process environment values other than the compose pins above.
- Paper v1 `control.json`.
- Whether any Paper v2 trade exists.
- Learning-worker dispositions after this core deploy.
- Analytics `cockpit-ready` or `reads-ready` for `facf8e36`.
- A live Committee cycle since 2026-09-25.

Those items stay `UNKNOWN_NEEDS_EVIDENCE` or `OWNER_ACTION_REQUIRED` if the only inspection path is a state-changing command.

## Explicit statements

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`FUNDED TRADING ENABLED = NO`

`FEATURE BUS ACTIVATED = NO`

`PAPER V2 ACTIVATED = NO`

`COMMITTEE ACTIVATED = NO`
