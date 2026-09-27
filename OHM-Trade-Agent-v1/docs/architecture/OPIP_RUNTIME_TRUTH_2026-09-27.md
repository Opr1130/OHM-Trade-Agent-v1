# O’Pip runtime truth — 2026-09-27

## How this was observed

No SSH session was opened. The production forced command is `deploy <sha>`. A new session would be a deploy, which this audit must not run.

Evidence is from GitHub Actions logs for owner-gated workflows. Host names, users, and secret-like lines were excluded. This is the last control-plane observation, not a re-probe at audit close.

`origin/main` at audit close remained `a416be0a068dc58543a4b6cd254d5c42fcaf4c96`.

## Release identity

| Plane | Last successful control-plane SHA | When | What that proves |
| --- | --- | --- | --- |
| Production core | `a416be0a068dc58543a4b6cd254d5c42fcaf4c96` | 2026-09-27T15:04:03Z, run 36328009445 | Deploy status SUCCESS, post-commit health OK, rollback NO, paper-registry genesis OK |
| Canonical replica export from core | same SHA | same run | `OPIP_LEARNING_EXPORT_STATUS=SUCCESS`, `OPIP_LEARNING_READINESS=READY` |
| Learning worker install | `ef8b23aab4b45569bb168961abab79593865e8dc` | 2026-09-22T14:43:19Z, run 35742096231 | Last successful `/deploy-learning`. Today’s learning workflow on the core deploy comment was skipped. |
| Analytics / cockpit install | `ef8b23aab4b45569bb168961abab79593865e8dc` | 2026-09-22T14:43:34Z, run 35742314210 | Last successful `/deploy-analytics` in the queried window. Cockpit-ready for `a416be0a` was not observed. |
| Committee install | `3457d59fb80c68d5c49a6c1df9ebe1fc7c2bbf8e` | 2026-09-25T18:05:01Z, run 36170142998 | Installed with `OPIP_COMMITTEE_MODE=off`, timer disabled and inactive |
| Committee shadow activation | `86d5290b82659cfa1ed8a69f762482a8582cf4db` | 2026-09-25T23:24:31Z, run 36200844543 | `SHADOW_PROOF=PASS`, timer still disabled and inactive, `role_results=0` |

Learning-worker code and current core are not the same SHA. The core export can be ready while capture and outcomes on the worker fail closed for `RELEASE_DRIFT`. Whether the worker has failed closed since 2026-09-27T15:04Z was not re-read. Mark that live disposition `UNKNOWN_NEEDS_EVIDENCE`.

## Containers observed in the core deploy log

| Container | Observation at 2026-09-27T15:04:03Z |
| --- | --- |
| `ohm-trade-agent` | Recreated this deploy. Up, healthy. `127.0.0.1:8000->8000/tcp`. Health JSON `{"status":"ok"}`. |
| `opip-canonical-writer` | Recreated this deploy. Up, healthy. |
| `opip-stream-worker` | Up 5 days, healthy. Not recreated with this SHA. |
| `ohm-dashboard-edge` | Running. Not described as recreated in the captured lines. |
| `ohm-freqtrade-paper` | Image `freqtradeorg/freqtrade:2026.7`. Up, healthy. |
| `ohm-freqtrade-paper-usdt` | Same image. Up, healthy. |

`O'Pip stream worker reconciliation: degraded (rc=69); shadow evidence unavailable or incomplete; production core unaffected.`

`O'Pip canonical writer resource validation: PASS.`

`O'Pip P1 shadow outbox retirement: OK.`

## Scheduler observed in that log

| Unit | Observation |
| --- | --- |
| `/etc/cron.d/ohm-unified-cycle` | Present. Core cadence 1 minute. Entrypoint `app.jobs.run_cycle`. |
| Learning compute | `REMOTE_ONLY` |
| `/etc/cron.d/opip-learning-export` | Present. Cadence 2 minutes plus a 40 second offset. |
| Committee timer | Last observed 2026-09-25: disabled and inactive. Not printed by the core deploy. |

`run_cycle` calls protection, pending setups, Early Watch cadence, and `scan_opportunities`. It does not call the feature-bus pilot or the Committee.

## Mode flags

Checked-in compose at this SHA, which the deploy built:

| Flag | Checked-in core value | Live host beyond that file |
| --- | --- | --- |
| `OPIP_CANONICAL_WRITER_MODE` | `shadow` on core, `off` on the writer service env | Writer container was healthy. Mode value inside the process was not printed. |
| `OPIP_FEATURE_BUS_MODE` | `off` pinned on core | Not printed as a process value. Pin is in the deployed compose. |
| `OPIP_PAPER_V2_MODE` | Not set in compose. Settings default `off`. | `UNKNOWN_NEEDS_EVIDENCE` |
| `OPIP_COMMITTEE_MODE` | Not a core-service flag | Last committee proof: `shadow` at a different SHA, timer inactive |

Arbitrary environment was not dumped.

## Runtime data flow

| Flow | Active authority | Shadow / off / legacy | Store | Scheduler | Release | Last success | Gap |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Market discovery | `scan_opportunities` via `run_cycle` | Feature bus off | Scan and observation JSONL | Unified cycle, 1 minute | Core `a416be0a` | Cycle installed by this deploy. Last scan row not read. | Feature bus not in the cycle |
| Qualification | Same scan, Top-8 and profit ranking | Decision funnel is shadow telemetry | Qualification JSONL | Inside the scan | Core `a416be0a` | Not a separate job | No forecast, no portfolio selector |
| Alert decision | Scan trade plan plus alert v2 for system incidents | Committee text is not on this path | Delivery JSONL | Unified cycle | Core `a416be0a` | Not re-read | v1.4.2 trade-card contract is only partial |
| Active position protection | `monitor_active_trades` before discovery | Event Risk Shield default off. Paper v2 sweep only if the scan runs and mode allows work. | Active-trade registry | Unified cycle | Core `a416be0a` | Installed with the cycle | Paper v2 protection is not the one-minute owner |
| Paper simulation | Freqtrade dry-run containers healthy. Paper v1 monitor is on the cycle. | Paper v2 default off. Live mode unknown. | Freqtrade state and Paper v1 JSON | Freqtrade containers plus unified cycle | Core image this deploy. Freqtrade image tag `2026.7`, container age about 3 weeks in the log. | Containers healthy at deploy | Two paper engines plus an inactive third |
| Canonical writing | Writer container healthy. Core mode pin `shadow`. | Not the live paper ledger | SQLite canonical DB | Writer process | Recreated this deploy | Resource validation PASS | Shadow capture, not cutover |
| Feature bus | Off | Pilot manual only | Would be canonical, dual-gated | Not scheduled | Code is this SHA. Mode pin off. | Not run by this deploy | Explicitly pinned off |
| Learning export | Core export cron | Worker code is an older SHA | Replica bundle | Export every 2 minutes | Export proved this SHA | `LEARNING_READINESS=READY` at 15:04:02Z | Worker install remains `ef8b23aa` |
| Outcome maturation | Learning jobs on the worker | Trading host is `REMOTE_ONLY` | Phase3C, discovery, accountability files on the learning plane | Learning timers | Worker SHA not redeployed today | Not re-read after core deploy | Expect `RELEASE_DRIFT` fail-closed until `/deploy-learning` of this SHA |
| Committee | Last proof: shadow mode, timer inactive, zero role results | No path into `run_cycle` | Committee data dir on the committee host | Timer disabled | `86d5290b`, not `a416be0a` | Activation proof 2026-09-25 | No matured cases in that proof |
| Dashboard | `ohm-dashboard-edge` running. Legacy routes are in the core app. | Grafana and B/C-4 depend on analytics stages last installed at `ef8b23aa` | Read models | Edge container plus core | Edge not clearly rebuilt in the captured lines | Edge reported Running | Eight-page v1.4.3 dashboard is not one live surface |

## What was not observed

- Process environment values other than compose pins and the committee proof lines above
- Paper v1 `control.json`
- Whether any Paper v2 trade exists
- Learning-worker dispositions after the core deploy
- Analytics `cockpit-ready` or `reads-ready` for `a416be0a`
- A live Committee cycle after 2026-09-25T23:24:31Z

Those items stay `UNKNOWN_NEEDS_EVIDENCE` or `OWNER_ACTION_REQUIRED` if the only inspection path is a state-changing command.
