# O'Pip PostgreSQL Phase 1: storage inventory and schema-gap matrix

Generated from `app/opip/data_platform/storage_inventory.py` (do not edit by hand; a test
compares this file with the module). Scope: NEW production business records after an approved
cutover. Phase 2 (historical migration) and Phase 3 (legacy retirement) are separate.

Legend: `basis=source` follows from checked-in code; `basis=path` was assigned from the file
name and still needs owner confirmation. Nothing here is `DIRECT` before an approved cutover.

## Domain matrix

| Domain | Existing storage | PostgreSQL target | Schema complete | Write integration | Read integration | Cutover readiness |
|---|---|---|---|---|---|---|
| Scanner | `opip/qualification/screening_evaluations.jsonl`, `opip/qualification/scan_summaries.jsonl`, `scan_activity.jsonl`, `full_market_observations.jsonl`, `full_market_transition_detections.jsonl`, `movement_discovery_v2_1.jsonl`, `movement_discovery_v2_2_shadow.jsonl`, `native_flow_shadow.jsonl`, `explosion_precursor_snapshots.jsonl`, `explosion_state_snapshots.jsonl`, `opip/discovery` | `market.observation`, `market.screening`, `raw.ingested_event` | PARTIAL | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | NOT_READY, SCHEMA_READY_WRITE_NOT_INTEGRATED |
| Qualification | `opip/qualification/funnel_events.jsonl`, `candidate_trace.jsonl`, `decision_telemetry.jsonl`, `opip_trade_quality_evidence_v1.jsonl`, `opip/decision/equivalence`, `opip/committee` | `lifecycle.stage_transition`, `raw.ingested_event` | PARTIAL | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | NOT_READY, SCHEMA_READY_WRITE_NOT_INTEGRATED |
| V2 scoring | `opip/qualification/scoring_comparison.jsonl` | none | NO | NONE | NONE | NOT_READY |
| AI advisory and cost | `openai_usage.jsonl`, `opip_ai_router_usage.jsonl`, `external_order_reviews.json` | `signal.ai_call` | PARTIAL | API_ONLY | NONE | NOT_READY, SCHEMA_READY_WRITE_NOT_INTEGRATED |
| Tradevisor | `tradevisor_v1_inbox.json`, `tradingview_v2_inbox.json` | none | NO | NONE | NONE | NOT_READY |
| Telegram delivery | `telegram_delivery_events.jsonl` | `signal.delivery_event` | PARTIAL | API_ONLY | NONE | SCHEMA_READY_WRITE_NOT_INTEGRATED |
| Opportunity lifecycle | `intelligence_learning/events.jsonl`, `paper_trading/events.jsonl` | `paper.trade_event`, `signal.intelligence_event` | YES | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | SCHEMA_READY_WRITE_NOT_INTEGRATED |
| Trade outcomes | `full_market_transition_outcomes.jsonl`, `movement_discovery_v2_1_outcomes.jsonl`, `explosion_state_outcomes.jsonl`, `trade_outcomes.json`, `phase3c_forward_outcomes.jsonl`, `phase3b_shadow_telemetry.jsonl`, `opip_ml_feature_snapshots_v1`, `opip_ml_capture_dead_letter.jsonl`, `opip/analytics/opip_analytics.db`, `opip/canonical`, `opip/canonical/opip_canonical_v1.sqlite3` | `learning.feature_snapshot`, `learning.signal_outcome` | PARTIAL | API_ONLY | NONE | NOT_READY, SCHEMA_READY_WRITE_NOT_INTEGRATED |
| Configuration | state files only | none | N/A | NONE | NONE | N/A |
| Operations and evidence | `p1_evidence_ledger.jsonl`, `opportunity_evidence.jsonl`, `opip/opportunity_accountability.jsonl` | `learning.opportunity_accountability_latest_v`, `raw.ingested_event` | PARTIAL | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | NOT_READY |

## Per-path appendix

| Path | Domain | Kind | Target | Schema | Write | Read | Basis | Note |
|---|---|---|---|---|---|---|---|---|
| `early_watch_scheduler_state.json` | Scanner | STATE | - | NO | NONE | NONE | path | - |
| `explosion_precursor_snapshots.jsonl` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | shadow only |
| `explosion_state_snapshots.jsonl` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | shadow only |
| `full_market_observation_alert_governor_state.json` | Scanner | STATE | - | NO | NONE | NONE | path | - |
| `full_market_observation_state.json` | Scanner | STATE | - | NO | NONE | NONE | path | - |
| `full_market_observations.jsonl` | Scanner | BUSINESS_RECORD | market.observation | YES | SHIPPED_FROM_FILE | NONE | source | typed projection by the shipper |
| `full_market_transition_capture_state.json` | Scanner | STATE | - | NO | NONE | NONE | path | - |
| `full_market_transition_detections.jsonl` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | shadow detections; not a stream |
| `learning_scheduler_state.json` | Scanner | STATE | - | NO | NONE | NONE | path | - |
| `movement_discovery_v2_1.jsonl` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | discovery research stream |
| `movement_discovery_v2_1_capture_state.json` | Scanner | STATE | - | NO | NONE | NONE | path | - |
| `movement_discovery_v2_2_shadow.jsonl` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | shadow only |
| `native_flow_shadow.jsonl` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | shadow only |
| `opip/discovery` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | attributions / forward_outcomes / dead letter jsonl |
| `opip/events` | Scanner | STATE | - | NO | NONE | NONE | path | event-calendar provider state |
| `opip/qualification/scan_summaries.jsonl` | Scanner | BUSINESS_RECORD | raw.ingested_event | PARTIAL | SHIPPED_FROM_FILE | NONE | source | raw JSON only; no typed scan table |
| `opip/qualification/screening_evaluations.jsonl` | Scanner | BUSINESS_RECORD | market.screening | YES | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | source | typed projection by the shipper |
| `opip/qualification/screening_evaluations_archive` | Scanner | ARCHIVE | - | NO | NONE | NONE | path | rotated history; Phase 2 |
| `opip/streaming` | Scanner | STATE | - | NO | NONE | NONE | path | - |
| `scan` | Scanner | CACHE | - | NO | NONE | NONE | path | scan working artifacts |
| `scan_activity.jsonl` | Scanner | BUSINESS_RECORD | - | NO | NONE | NONE | path | not a registered stream; no PostgreSQL home |
| `candidate_trace.jsonl` | Qualification | BUSINESS_RECORD | raw.ingested_event | PARTIAL | SHIPPED_FROM_FILE | NONE | source | raw JSON only |
| `decision_telemetry.jsonl` | Qualification | BUSINESS_RECORD | raw.ingested_event | PARTIAL | SHIPPED_FROM_FILE | NONE | source | raw JSON only |
| `opip/committee` | Qualification | BUSINESS_RECORD | - | NO | NONE | NONE | path | Committee is OFF; evidence only |
| `opip/decision/equivalence` | Qualification | BUSINESS_RECORD | - | NO | NONE | NONE | path | decision equivalence evidence |
| `opip/qualification` | Qualification | STATE | - | NO | NONE | NONE | path | directory only; each file is classified individually |
| `opip/qualification/early_timing_ledger_state.json` | Qualification | STATE | - | NO | NONE | NONE | path | - |
| `opip/qualification/funnel_events.jsonl` | Qualification | BUSINESS_RECORD | lifecycle.stage_transition | YES | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | source | also projects lifecycle.episode and lifecycle.candidate |
| `opip/qualification/funnel_events_archive` | Qualification | ARCHIVE | - | NO | NONE | NONE | path | rotated history; Phase 2 |
| `opip_trade_quality_evidence_v1.jsonl` | Qualification | BUSINESS_RECORD | raw.ingested_event | PARTIAL | SHIPPED_FROM_FILE | NONE | source | raw JSON only |
| `opip/qualification/scoring_comparison.jsonl` | V2 scoring | BUSINESS_RECORD | - | NO | NONE | NONE | source | V1/V2 shadow comparison; NOT a registered stream (confirmed gap) |
| `opip/release/runtime.json` | V2 scoring | STATE | - | NO | NONE | NONE | path | release provenance stamp |
| `chief_review_cache.json` | AI advisory and cost | CACHE | - | NO | NONE | NONE | path | - |
| `external_order_reviews.json` | AI advisory and cost | BUSINESS_RECORD | - | NO | NONE | NONE | path | external AI is advisory evidence |
| `openai_usage.jsonl` | AI advisory and cost | BUSINESS_RECORD | signal.ai_call | PARTIAL | API_ONLY | NONE | path | tokens only; no USD or pricing provenance in the source rows |
| `opip_ai_router_usage.jsonl` | AI advisory and cost | BUSINESS_RECORD | signal.ai_call | PARTIAL | API_ONLY | NONE | path | tokens only; no USD or pricing provenance in the source rows |
| `tradevisor_v1_inbox.json` | Tradevisor | BUSINESS_RECORD | - | NO | NONE | NONE | source | webhook inbox; events + metrics |
| `tradingview_v2_inbox.json` | Tradevisor | BUSINESS_RECORD | - | NO | NONE | NONE | path | - |
| `alert_governor_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `alert_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `attention_budget_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `emergency_alert_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `notification_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `pending_setup_alert_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `pending_setup_terminal_alert_outbox.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `qualified_alert_outbox.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | retry record; mutable |
| `telegram_delivery_events.jsonl` | Telegram delivery | BUSINESS_RECORD | signal.delivery_event | PARTIAL | API_ONLY | NONE | source | also shipped raw to raw.ingested_event; typed table added in migration 007 |
| `telegram_delivery_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `telegram_market_watches.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `trade_monitor_state.json` | Telegram delivery | STATE | - | NO | NONE | NONE | path | - |
| `active_trades.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | mutable registry |
| `freqtrade_bridge` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `intelligence_learning/events.jsonl` | Opportunity lifecycle | BUSINESS_RECORD | signal.intelligence_event | YES | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | source | typed projection by the shipper |
| `intelligence_learning/freqtrade_ingest_dedup.sqlite` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `intelligence_learning/freqtrade_ingest_state.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `intelligence_learning/journeys.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `intelligence_learning/profile.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `opip_entry_watch.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `opportunity_monitor_queue.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `order_intents.json` | Opportunity lifecycle | SAFETY_CRITICAL | - | NO | NONE | NONE | path | order authority: owner-gated, never PostgreSQL-gated |
| `paper_trading/control.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `paper_trading/events.jsonl` | Opportunity lifecycle | BUSINESS_RECORD | paper.trade_event | YES | SHIPPED_FROM_FILE | NONE | source | typed projection by the shipper |
| `paper_trading/evidence_gap_spool.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `paper_trading/state.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | - |
| `pending_setups.json` | Opportunity lifecycle | STATE | - | NO | NONE | NONE | path | mutable registry |
| `execution_learning.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `explosion_state_outcome_state.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `explosion_state_outcomes.jsonl` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | shadow only |
| `full_market_transition_outcome_state.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `full_market_transition_outcomes.jsonl` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | hit_2/5/10/20pct are MFE-based |
| `movement_discovery_v2_1_outcome_state.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `movement_discovery_v2_1_outcomes.jsonl` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | - |
| `opip/analytics/opip_analytics.db` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | SQLite analytics store |
| `opip/canonical` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | canonical SQLite writer (paper/quote evidence) |
| `opip/canonical/opip_canonical_v1.sqlite3` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | canonical SQLite writer |
| `opip_ml_capture_checkpoint.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `opip_ml_capture_dead_letter.jsonl` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | - |
| `opip_ml_capture_health.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `opip_ml_data_readiness_v1.json` | Trade outcomes | CACHE | - | NO | NONE | NONE | path | - |
| `opip_ml_feature_snapshots_v1` | Trade outcomes | BUSINESS_RECORD | learning.feature_snapshot | PARTIAL | NONE | NONE | path | table exists in 001; nothing writes it |
| `phase3b_shadow_telemetry.jsonl` | Trade outcomes | BUSINESS_RECORD | - | NO | NONE | NONE | path | shadow only |
| `phase3c_forward_outcomes.jsonl` | Trade outcomes | BUSINESS_RECORD | learning.signal_outcome | PARTIAL | API_ONLY | NONE | path | forward outcomes; outcome class must be labelled |
| `phase3c_verified_edge_report.json` | Trade outcomes | CACHE | - | NO | NONE | NONE | path | - |
| `price_movement_learning.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `shadow_learning.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `strategy_calibration_profile.json` | Trade outcomes | STATE | - | NO | NONE | NONE | path | - |
| `trade_outcomes.json` | Trade outcomes | BUSINESS_RECORD | learning.signal_outcome | PARTIAL | API_ONLY | NONE | path | scorecard does not read this file |
| `asset_identity_registry.json` | Configuration | STATE | - | NO | NONE | NONE | path | - |
| `coinmarketcal_coin_map.json` | Configuration | CACHE | - | NO | NONE | NONE | path | - |
| `operator_control.json` | Configuration | STATE | - | NO | NONE | NONE | path | operator pause/quiet-hours state |
| `opip/events/coinmarketcal_identity_map.json` | Configuration | CACHE | - | NO | NONE | NONE | path | - |
| `opip/events/ingest_state.json` | Configuration | STATE | - | NO | NONE | NONE | path | - |
| `opip/events/provider_health.json` | Configuration | STATE | - | NO | NONE | NONE | path | - |
| `opip/protection/protection_state.json` | Configuration | SAFETY_CRITICAL | - | NO | NONE | NONE | path | F11 protection state |
| `opip/risk` | Configuration | SAFETY_CRITICAL | - | NO | NONE | NONE | path | - |
| `opip/strategy-admin` | Configuration | STATE | - | NO | NONE | NONE | path | - |
| `opip/strategy-admin/active.json` | Configuration | STATE | - | NO | NONE | NONE | path | - |
| `protection_admission_stamp.json` | Configuration | SAFETY_CRITICAL | - | NO | NONE | NONE | path | F11 admission stamp: independent of PostgreSQL |
| `protection_admission_stamp_history.json` | Configuration | SAFETY_CRITICAL | - | NO | NONE | NONE | path | - |
| `.unified_cycle.lock` | Operations and evidence | LOCK | - | NO | NONE | NONE | path | - |
| `irr` | Operations and evidence | STATE | - | NO | NONE | NONE | path | - |
| `learning` | Operations and evidence | STATE | - | NO | NONE | NONE | path | - |
| `opip/discovery/ef01_reconciliation_report.json` | Operations and evidence | STATE | - | NO | NONE | NONE | path | - |
| `opip/learning/telemetry.json` | Operations and evidence | STATE | - | NO | NONE | NONE | path | - |
| `opip/opportunity_accountability.jsonl` | Operations and evidence | BUSINESS_RECORD | learning.opportunity_accountability_latest_v | PARTIAL | SHIPPED_FROM_FILE | DASHBOARD_READ_MODEL | source | raw JSON plus read-model views |
| `opip/opportunity_accountability_summary.json` | Operations and evidence | CACHE | - | NO | NONE | NONE | path | - |
| `opportunity_evidence.jsonl` | Operations and evidence | BUSINESS_RECORD | - | NO | NONE | NONE | path | - |
| `p1_evidence_ledger.jsonl` | Operations and evidence | BUSINESS_RECORD | raw.ingested_event | PARTIAL | SHIPPED_FROM_FILE | NONE | source | raw JSON only |
| `p1_shadow_outbox.jsonl` | Operations and evidence | ARCHIVE | - | NO | NONE | NONE | path | retired stream tombstone |
| `p1_shadow_outbox_checkpoint.json` | Operations and evidence | ARCHIVE | - | NO | NONE | NONE | path | - |
| `p1_shadow_outbox_dead_letter.jsonl` | Operations and evidence | ARCHIVE | - | NO | NONE | NONE | path | - |
| `system_incidents.json` | Operations and evidence | STATE | - | NO | NONE | NONE | path | - |

## Fail-closed contract and the remaining FILES-writer gap

- `OPIP_BUSINESS_STORE_AUTHORITY` unset, empty or `FILES` is the only legitimate pre-cutover state.
  Any other value that is not `POSTGRES` (for example a typo) makes `new_admissions_allowed` refuse
  (`INVALID_AUTHORITY_CONFIG`); it never falls back to the file admission path.
- `ops.append_business_event` treats a replay as a duplicate only when the aggregate, event type,
  occurrence time and payload hash are all identical. `occurred_at` must be the business fact's own
  time, never the retry time.
- A typed row is accepted only in the same transaction that appended its ledger event, for the matching
  aggregate and event type, under the current epoch.
- **Not yet true:** FILES writers are not fenced by the database. Until each production writer calls
  `new_admissions_allowed` (Phase 2 call-site integration), a node still running the file path can write
  after the authority flips. That is why the cutover remains OFF and requires separate owner approval.
