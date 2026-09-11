# PR 2 named capture boundary

PR 2 will prove the canonical writer transaction pattern on **one** low-rate existing operational transition. PR 1 does not implement the writer.

## Approved boundary

| Item | Value at pin `7df7d6e588a6ff8e9ecade7cc7220548d187a45d` |
| --- | --- |
| File | `app/services/alert_governor.py` |
| Evaluate | `evaluate_opportunity_alert()` |
| Durable commit (later) | writer transaction (not implemented in PR 1) |
| Record | `record_opportunity_alert()` |
| Rollback | `release_opportunity_alert_reservation()` |
| Production caller | `app/jobs/scan_movers.py` |
| Scheduler hook | `app/jobs/run_cycle.py` → `_run_early_watch_if_due()` |
| Cron | `deploy/cron.d/ohm-unified-cycle` (`python -m app.jobs.run_cycle`) |
| State today | `/app/data/alert_governor_state.json` |
| Actions | `CREATE` / `EDIT` / `SUPPRESS` |
| Rate limit | 8 new cards / 24h; 6h same-state cooldown |

Approved sequence:

`evaluate_opportunity_alert()` → writer transaction → `record_opportunity_alert()` / `release_opportunity_alert_reservation()`.

## Why this boundary

- Already scheduled on the trading host at the pinned commit.
- Low rate by construction.
- Already separates evaluate from record/release, which is the transaction pattern PR 2 must prove.

## Not the named PR 2 boundary

| Candidate | Why not named |
| --- | --- |
| `app/opip/risk/alert_state.py` `AlertStateManager.evaluate` / `commit` | Cleaner token pattern; **not scheduled** in `run_cycle` at the pin |
| Phase3C `.phase3c_forward_outcomes.jsonl.state.sqlite3` handoff | Learning-plane maturation, not an operational incident |

PR 2 may copy the Event Risk Shield evaluate/commit token pattern when wrapping governor transitions. PR 1 does not schedule the shield and does not add SQLite.

## Authority statements

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`SIGNAL QUALITY SQ-01 STARTED = NO`

`FUNDED TRADING ENABLED = NO`
