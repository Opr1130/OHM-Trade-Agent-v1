# Consumer census

Pinned commit: `808a308cd274d30b55fef47b382c229b761e07df`.

Legacy JSONL is the current production write-ahead log. It is **non-authoritative** for new calibration and promotion after the named stop-writing timestamp (set at technical cutover, not invented in PR 1).

## Export streams (production → learning, schema 4)

Copy-only export: `deploy/remote/export-opip-learning-evidence.sh`, cron `deploy/cron.d/opip-learning-export` (every 2 minutes). Destination `/var/lib/opip-learning-export`. Consumers read committed `manifest.env` only.

| Path under `/app/data/` | Writer | Required export? | Consumers |
| --- | --- | --- | --- |
| `full_market_observations.jsonl` | `app/services/full_market_observation.py` | yes | Signal Quality replay, learning, data platform |
| `p1_evidence_ledger.jsonl` | legacy P1 producer | optional | Phase3C, accountability, ML readiness |
| `intelligence_learning/events.jsonl` | `app/services/intelligence_journey.py` | yes | dashboard, Freqtrade ingest |
| `opip/qualification/screening_evaluations.jsonl` | `app/opip/decision/store.py` | yes | funnel, OA, discovery, export |
| `opip/qualification/funnel_events.jsonl` | `app/opip/decision/store.py` | yes | same |
| `opip/qualification/scan_summaries.jsonl` | `app/opip/decision/store.py` | yes | same |
| matching `*_archive/` trees | bounded JSONL archive | yes | learning replica |
| `paper_trading/events.jsonl` | `app/services/paper_trade_registry.py` | optional | dashboard, data platform |
| `telegram_delivery_events.jsonl` | `app/services/telegram_delivery.py` | optional | dashboard |
| `decision_telemetry.jsonl` | `app/services/decision_telemetry.py` | optional | offline analysis |
| `opip_trade_quality_evidence_v1.jsonl` | `app/services/trade_quality_evidence_registry.py` | optional | learning export |
| `candidate_trace.jsonl` | `app/services/candidate_trace.py` | optional | debug/trace |
| `manifest.env` | export script | yes | learning sync admission |

`p1_shadow_outbox.jsonl` is **retired** and must not be recreated or exported.

## Bounded JSONL (canonical helper)

Implementation: `app/opip/storage/bounded_jsonl.py` (`BoundedJsonlArchive`).

Additional bounded paths: `opip/events/events.jsonl`, `opip/risk/{family}/assessments.jsonl`, `opip/qualification/early_timing_milestones.jsonl`, discovery forward outcomes/attributions (learning-side writes).

## Other production or shadow ledgers (not all exported)

| Path | Writer | Consumer note |
| --- | --- | --- |
| `alert_governor_state.json` | `alert_governor.py` | PR 2 capture boundary state today |
| `paper_trading/control.json` | `paper_trade_control.py` | operator ON/OFF |
| `paper_trading/state.json` | paper registry | native paper ledger |
| `movement_discovery_v2_1.jsonl` | `movement_discovery_learning_capture.py` | learning |
| `price_movement_learning.json` | `price_movement_learning.py` | shadow radar |
| `explosion_state_snapshots.jsonl` | `explosion_learning.py` | shadow |
| `shadow_learning.json` | shadow decision capture | research |
| `scan_activity.jsonl` | `operations_analytics.py` | ops |

## Learning-side stores (not trading-host writers)

| Path | Writer | Note |
| --- | --- | --- |
| `phase3c_forward_outcomes.jsonl` + `.phase3c_forward_outcomes.jsonl.state.sqlite3` | Phase3C job | forbidden on production scheduler |
| `opip/discovery/forward_outcomes.jsonl` + `.forward_outcomes.jsonl.state.sqlite3` | discovery maturation | learning worker |
| `opip/opportunity_accountability.jsonl` + `.opportunity_accountability.jsonl.state.sqlite3` | OA incremental | learning worker |

## SQLite already present (not the v1 canonical operational DB)

| Path | Purpose |
| --- | --- |
| `/app/freqtrade_paper/tradesv3.ohm_dry_run_usd.sqlite` | Freqtrade dry-run USD |
| `/app/freqtrade_paper/tradesv3.ohm_dry_run_usdt.sqlite` | Freqtrade dry-run USDT |
| intelligence / ledger / maturation `*.sqlite3` helpers | dedup and queues |

The v1 canonical operational SQLite WAL does **not** exist yet and is not created in PR 1.

## Stop / archive rule

A dependency-checked stop-writing timestamp will be named at technical cutover after a consumer census refresh. PR 1 does not set that timestamp to a fabricated calendar date.
