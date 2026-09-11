# PR 2 named capture boundary

PR 2 proves the canonical writer transaction pattern on **one** low-rate existing operational transition: Early Watch alert-governor CREATE / EDIT / RELEASE.

## Approved boundary

| Item | Value at pin `808a308cd274d30b55fef47b382c229b761e07df` |
| --- | --- |
| File | `app/services/alert_governor.py` |
| Evaluate | `evaluate_opportunity_alert()` |
| Durable commit | `opip-canonical-writer` over UDS (`/app/data/opip/canonical/writer.sock`) |
| Record | `record_opportunity_alert()` after durable ACK |
| Rollback | `release_opportunity_alert_reservation()` after durable ACK |
| Production caller | `app/jobs/scan_movers.py` (Early Watch only) |
| Scheduler hook | `app/jobs/run_cycle.py` → `_run_early_watch_if_due()` |
| Cron | `deploy/cron.d/ohm-unified-cycle` (`python -m app.jobs.run_cycle`) |
| Ops state (JSON) | `/app/data/alert_governor_state.json` |
| Evidence DB | `/app/data/opip/canonical/opip_canonical_v1.sqlite3` |
| Gap spool (non-authoritative) | `/app/data/opip/canonical/capture_gap_spool.json` |
| Actions | `CREATE` / `EDIT` / `SUPPRESS` |
| Rate limit | 8 new cards / 24h; 6h same-state cooldown |
| Default mode | `OPIP_CANONICAL_WRITER_MODE=off` |

Approved sequence:

`evaluate_opportunity_alert()` → Telegram outcome → canonical durable ACK → `record_opportunity_alert()` / `release_opportunity_alert_reservation()` → `CONFIRM_OPS_APPLIED`.

JSON remains operational alert-control authority. SQLite is canonical evidence authority for the named Early Watch transition.

## Ops handoff (O12)

Initial writer transaction atomically writes `event + idempotency + projection(if recorded) + watermark + PENDING handoff`.

After JSON mutation succeeds, the producer sends idempotent `CONFIRM_OPS_APPLIED(event_id)`.

At the start of each Early Watch cycle, PENDING handoffs are reconciled over UDS **before** new alert evaluation.

## Gap spool (O10)

Do **not** use a second append-only evidence journal for gaps. Unresolved writer-delivery failures live in the atomic JSON recovery spool. Their presence makes the evidence window **INCOMPLETE**. On writer recovery they are reconciled into canonical `alert_governor.capture_gap.recorded` evidence and then removed.

## Deployment independence (O11)

`ohm-trade-agent` must **not** `depends_on` the writer with `condition: service_healthy`. `deploy/remote/ohm-deploy` builds/starts/health-checks writer and core separately and validates writer resource limits.

## Not in PR 2

- Broad Watch integration
- Detector / feature bus / paper work
- Signal Quality SQ-01
- Funded trading changes
- GitHub PR #233 changes
- Production shadow activation (`mode=shadow` remains an explicit later gate)

## Authority statements

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`SIGNAL QUALITY SQ-01 STARTED = NO`

`FUNDED TRADING ENABLED = NO`

`PR 2 PRODUCTION ACTIVATION AUTHORIZED = NO`
