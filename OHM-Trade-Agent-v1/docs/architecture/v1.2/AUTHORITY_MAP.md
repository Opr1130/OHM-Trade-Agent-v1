# Authority map (as-built at pin)

Pinned commit: `808a308cd274d30b55fef47b382c229b761e07df`.

This map records **current** writers and consumers. It does not change production behavior.

## Scheduler

| Item | Path / symbol |
| --- | --- |
| Canonical cron | `deploy/cron.d/ohm-unified-cycle` |
| Entrypoint | `python -m app.jobs.run_cycle` every minute |
| Compose service | `ohm-trade-agent` in `docker-compose.yml` |
| Paper compose | `docker-compose.paper.yml` (Freqtrade dry-run; isolated) |

`run_cycle` order (relevant):

1. `reconcile_kraken_account()` — private Kraken read-only
2. `monitor_active_main()` — live position protection
3. Broad discovery when due → `scan_opportunities.main()`
4. Pending-setup monitor
5. `_run_early_watch_if_due()` → `scan_movers.main()`
6. `_run_paper_monitor_fail_open()` → `run_paper_trade_monitor()`
7. External order review
8. Fail-open local learning cycle

## Production authorities

| Authority | Writer file | Function / class | Job |
| --- | --- | --- | --- |
| Technical score | `app/scanner/technical_scorer.py` | `score_snapshot()` | `scan_opportunities.main` |
| Short technical score | `app/scanner/short_technical_scorer.py` | `score_short_snapshot()` | same |
| Top-8 | `app/scanner/directional_candidates.py` | `select_directional_candidates()` | same |
| Thresholds | `app/scanner/candidates.py` | `MIN_TECHNICAL_SCORE=80`, `MAX_CANDIDATES=8` | same |
| Indicators | `app/indicators/technical.py` + `app/scanner/market_scanner.py` | `analyze_symbol()` | same |
| Universe | `app/scanner/universe.py` | `build_kraken_asset_universe()` | same |
| Profit ranking (legacy comparator) | `app/services/profit_ranking.py` | `evaluate_profit_ranking()`, `rank_profit_opportunities()` | same |
| Economic gate | `app/services/economic_quality_gate.py` | `evaluate_economic_quality()` | same |
| Action gate | `app/services/trade_action_gate.py` | `apply_action_gate()` | same |
| Movement discovery v2.1 | `app/services/movement_discovery_v2.py` | `scan_early_movers()` | `scan_movers.main` |
| Early Watch orchestration | `app/jobs/scan_movers.py` | `main()` | `_run_early_watch_if_due` |
| Alert governor | `app/services/alert_governor.py` | `evaluate_opportunity_alert()`, `record_opportunity_alert()`, `release_opportunity_alert_reservation()` | `scan_movers.main` |
| Price movement radar | `app/services/price_movement_radar.py` | `evaluate_price_movement()` | inside `scan_opportunities` |
| Signal Quality composite | `app/services/signal_scoring.py` | `evaluate_universe()` | `scan_movers` when flag on (default off) |
| Explosion / precursor | `app/services/explosion_state.py`, `explosion_precursor.py` | `build_explosion_state_vector()`, `evaluate_explosion_precursor()` | `scan_explosion_learning` (10 min) |
| Native paper enroll | `app/services/paper_trade_engine.py` | `enroll_paper_opportunity()` | end of `scan_opportunities.main` |
| Native paper monitor | `app/services/paper_trade_monitor.py` | `run_paper_trade_monitor()` | after live protection |
| Freqtrade bridge | `app/services/freqtrade_signal_bridge.py` | `publish_qualified_long()` | scan path; isolated compose |
| Position protection | `app/jobs/monitor_active_trades.py`, `app/services/trade_monitor.py` | `run_active_trade_monitor()`, `monitor_trade()` | first after reconcile |
| Pending protection | `app/services/pending_setup_monitor.py` | pending monitor | `run_cycle` |
| Kraken public | `app/exchanges/kraken.py` | `KrakenClient` | scan / monitor / paper OHLC |
| Kraken private | `app/exchanges/kraken_private.py` | `KrakenPrivateClient` (read-only) | `reconcile_kraken_account` |
| Chief / AI router | `app/services/chief_analyst.py`, `app/services/opip_ai_router.py` | `review_candidates()`, `invoke_chief_review()` | qualification (advisory) |
| Dashboard | `app/services/dashboard_read_model.py` | `build_dashboard_read_model()` | `GET /dashboard` |

## Learning-plane authorities (not on trading-host scheduler)

| Authority | Writer | Job | Host |
| --- | --- | --- | --- |
| Phase3C outcomes | `app/jobs/build_phase3c_forward_outcomes.py` | `run_opportunity_intelligence_cycle` | learning worker ~10 min |
| Discovery outcomes / attribution | `app/opip/discovery/maturation.py`, `attribution.py` | same | learning worker |
| Opportunity Accountability | `app/services/opportunity_accountability.py` | `build_incremental_from_outcomes()` | learning worker |
| EF-01 reconcile | `app/opip/discovery/reconciliation.py` | `reconcile_discovery_accountability_evidence` | learning worker |

Production **must not** schedule `build_phase3c_forward_outcomes` or `run_opip_ml_capture`.

## Frozen live thresholds (do not change in PR 1)

| Constant | Value | Note |
| --- | --- | --- |
| `MIN_TECHNICAL_SCORE` | 80 | Freeze; retire at technical cutover |
| `MAX_CANDIDATES` | 8 | Freeze; retire at technical cutover |
| Economic min R:R | 2.5 | Legacy comparator freeze; not a permanent PI invariant |
| `risk_per_trade_pct` | 0.35% | Legacy comparator freeze; not a permanent PI invariant |

## Event Risk Shield (implemented, not scheduled)

`app/opip/risk/alert_state.py` `AlertStateManager.evaluate()` / `commit()` exists at the pin and is **not** invoked from `run_cycle`. It is not the named PR 2 boundary.

## Authority statements

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`SIGNAL QUALITY SQ-01 STARTED = NO`

`FUNDED TRADING ENABLED = NO`
