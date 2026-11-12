# O'Pip v6 audit (slice 0A): what to keep, migrate and retire

Status: draft for owner review. Date: 2026-11-11. Nothing in this change alters code, config or data.

This audit is the input to v6 slice 0A. It proposes dispositions; it does not delete anything. Every retirement follows the rule in section 9: disable, observe, then delete, one pull request per theme, with data archived first.

## 1. Summary

- The code base is 589 Python modules and about 239,000 lines under `app/`, plus 488 test files, 16 GitHub workflows and a React admin console (`control-center-v2`).
- A static import analysis from every entrypoint named in compose files, deploy scripts and workflows finds **105 modules (39,553 lines, 16.5%) that nothing deployed reaches**. These are retirement candidates, not confirmed dead code (see limits in section 2).
- Strongest candidates: ten single-file `app/opip/*` modules (about 5,800 lines), `app/backtesting` (6 modules), 19 job files with no scheduler reference, and the internal shadow paper simulator (the paper control switch and Freqtrade bridge stay).
- **Versioning is almost empty.** One fixed strategy version string (`OPIP-STRATEGY-V1`), and the model and feature-schema versions are explicit nulls. The Scorecard cannot compare strategies or models until v6 builds this. A threshold fingerprint (`gate_policy_fingerprint()`) already exists and should be reused to stamp every signal.
- **The Ops Center is an extension, not a new build.** A schema-driven config registry already covers scoring, scanner, safety, AI routing, retention and resource budgets, and `control-center-v2` already exists.
- **Data on the live droplet is 7.4 GB.** 5.2 GB is `opip/canonical`. Retention settings already exist as environment values; deletion is switched off by default.
- **Paper trading:** the Freqtrade containers are healthy but have 0 trades, because the master paper switch has never been turned on. It stays off until the new trade box exists.

## 2. Method and limits

- Static analysis of the repository at commit `1d5290e` (main): AST import graph over all `app/` modules, with entrypoints taken from any `app.*` module named in `docker-compose*.yml`, `Dockerfile`, `deploy/` and `.github/`, plus `app.main`.
- Production observations come from the owner's droplet on 2026-11-11: running containers, data folder sizes and the paper-trading folder.
- **Static reachability cannot see** dynamic imports, systemd timers or cron entries kept outside the repo, console scripts, or code run by hand. "Unreachable" therefore means "needs a check", never "safe to delete". Owner checks that would close the gap are in section 11.
- Tests were not counted as callers. A module used only by its own tests is still unreachable from the product.

## 3. What runs today

| Service | Source | Notes |
|---|---|---|
| `ohm-trade-agent` | `docker-compose.yml` | Main application and scheduler host |
| `opip-canonical-writer` | `docker-compose.yml` | Append-only canonical ledger writer |
| `opip-stream-worker` | `docker-compose.yml` | Streaming worker |
| `opip-scan-worker` | `docker-compose.yml` | Scanner (`app.jobs.scan_worker`) |
| `opip-irr-shadow` | `docker-compose.yml` | Shadow drain (`app.jobs.drain_irr_shadow`) |
| `ohm-dashboard-edge`, `tradevisor_edge` | `docker-compose.yml` | Dashboard edge and Tradevisor edge |
| `ohm-freqtrade-paper`, `ohm-freqtrade-paper-usdt` | `docker-compose.paper.yml` | Freqtrade paper trading, USD and USDT pairs. Both healthy on 2026-11-11 |

Names are kept as they are today; the OHM identifiers are legacy and are not renamed here.

## 4. Module groups and proposed disposition

Counts are modules and lines; "unreachable" is the number of modules in the group that the analysis could not reach.

| Group | Modules / lines | Unreachable | Evidence | Proposal |
|---|---|---|---|---|
| `app/opip/canonical` | 16 / 11,007 | 1 (`rebuild`, 71) | Live writer service; 5.2 GB of data | **Keep.** Migrate: segment shipping and whole-segment trimming (v6 data tiers) |
| `app/opip/contracts` | 30 / 20,552 | 5 / 4,148 | Imported by 73 modules; unreachable ones are `horizon_lifecycle`, `opportunity_phases`, `paper_simulator_policy`, `portfolio_paper_handoff`, `thesis` | **Keep** core. **Verify** the five; the paper ones belong to Paper v2 |
| `app/opip/decision` | 23 / 7,243 | 8 / 1,823 | Engine, gates, thresholds and versioning are live. Unreachable: `replay`, `promotion`, `equivalence*`, `policy_snapshot`, `models_v2`, `evidence`, `serialization` | **Keep** live parts. **Do not delete** `replay` and `promotion`: slices 3 and 9 need a replay harness and promotion logic |
| `app/opip/events` | 10 / 6,685 | 3 / 2,893 | Event intelligence is a v6 must-have. Unreachable: `attention`, `coverage`, `opportunity_engine` | **Keep and migrate.** Decide in slice 4 whether the unreachable three are the event logic to activate |
| `app/opip/learning` | 14 / 8,254 | 4 / 2,861 | `canonical_replica` is run from deploy scripts | **Keep.** Migrate to the data box; check the four unreachable modules |
| `app/opip/streaming` | 25 / 5,568 | 2 / 1,036 | Live stream worker; health and activation checks run from deploy | **Keep.** Verify the two |
| `app/opip/data_platform` | 13 / 3,213 | 0 | Migrations, reconcile, health, archive lifecycle run from deploy | **Keep.** Extend for the data box and retention policy |
| `app/opip/config` | 4 / 1,644 | 0 | Schema-driven config registry (section 7) | **Keep.** Ops Center config page builds on it |
| `app/opip/cockpit`, `app/api` | 6 / 6,452 and 8 files | 0 | Feeds the admin console | **Keep.** Migrate to the Ops Center pages |
| `app/opip/committee` | 37 / 22,657 | 3 / 1,925 | Shadow-only; has its own deploy and shadow-activation workflows. Largest group | **Decide.** Keep only if the Scorecard shows it improves hit %; otherwise retire after a shadow period |
| `app/opip/features`, `early`, `discovery`, `market`, `ml`, `analytics`, `decision_intelligence`, `profit_intelligence`, `identity`, `storage` | 10 groups | 11 combined | Mostly imported by live code | **Keep.** Verify unreachable modules listed in Appendix A |
| `app/opip/risk` | 11 / 2,859 | 7 / 2,067 | One external importer | **Verify, likely retire** most of it |
| `app/opip/detectors` | 2 / 585 | 2 / 585 | Nothing deployed reaches it | **Retire candidate** |
| Ten single-file `app/opip/*` modules | 10 / 5,790 | all | `forecast`, `forecast_evaluation`, `forecast_labels`, `forecast_qualification`, `portfolio_selector`, `portfolio_comparator`, `opportunity_lifecycle`, `opportunity_persistence`, `feasibility`, `fev_evidence_reader` | **Strongest retire candidates** (theme R2) |
| `app/backtesting` | 6 / 345 | all | Not reachable | **Retire or fold into the v6 replay harness** (slice 3) |
| `app/services` | 209 / 79,411 | 24 / 10,449 | Includes the Freqtrade bridge, TradingView and Tradevisor inboxes (live), Paper v1 and Paper v2 engines, and large analysis modules | **Second pass needed** (theme R6). Group-level only here |
| `app/jobs` | 43 / 13,280 | 19 / 1,249 | See section 5 | See section 5 |

## 5. Jobs

Scheduled or run by compose, deploy scripts or workflows: `run_cycle`, `scan_worker`, `drain_tradingview_inbox`, `drain_irr_shadow`, `capture_feature_bus_shadow`, `capture_feasibility_evidence_shadow`, `run_opip_ml_capture`, `run_opip_ml_data_readiness`, `scan_explosion_learning`, `run_opportunity_intelligence_cycle`, `build_phase3c_forward_outcomes`, `preflight_protection_health`, `verify_f11_readonly_credential`, `recover_f11_readonly_auth_incident`, `reconcile_discovery_accountability_evidence`, `report_paper_v2_cutover_readiness`.

No reference found anywhere (19): `build_opportunity_accountability`, `drain_tradevisor_inbox`, `monitor_paper_trades`, `opip_analytics_maintenance`, `process_p1_shadow_outbox`, `report_analytics`, `report_daily_gainer_reconciliation`, `report_evidence_health`, `report_explosion_learning`, `report_explosion_precursor_learning`, `report_learning`, `report_movement_discovery_learning`, `report_signal_quality_phase2`, `report_signal_quality_phase3a_timing`, `report_signal_quality_phase3c`, `report_tradevisor_learning`, `report_winner_forensics`, `scan_movers_v2_2_shadow`, `scan_native_flow_shadow`.

Two need special care. `opip_analytics_maintenance` relates to analytics retention and `drain_tradevisor_inbox` relates to the Tradevisor input; either may be run by a timer outside the repo. Check before touching them.

## 6. Paper trading

- **Freqtrade paper is live.** Two containers read admitted signals from `data/freqtrade_bridge/signals.json` (read-only) using `OHMExternalSignalStrategy`. Each is capped at 384 MB and 0.20 CPU. Results come back through `freqtrade_result_ingest`. They have no Kraken order authority.
- **`control.json` is the master paper switch, not a v1 leftover.** `paper_trade_control` gates publishing to the Freqtrade bridge: with `enabled: false` the scanner logs `PAPER_OFF` and skips `publish_qualified_long`. On the droplet (2026-11-11) it reads `enabled: false`, `updated_by: SYSTEM_BOOTSTRAP`, never changed; `signals.json` is empty (last written 2026-10-20) and both Freqtrade dry-run databases hold 0 trades. So zero trades is by design, not an ingestion fault. Keep the control file, CLI, Telegram `/paper` command, monitor and signal bridge.
- **Internal shadow simulator** (`paper_trade_engine`, `paper_trade_registry`) is the part that duplicates Freqtrade. It is labelled shadow/control in the status output. **Retire candidate** once Freqtrade results feed the Scorecard (theme R3). The earlier note that Paper Trade v1 "looks unused" referred to this simulator; it was not a reason to touch the switch.
- **Owner decision (2026-11-11):** paper stays off on the current server to protect production. It turns on after migration, on the new trade box, as an explicit gate. Until then the Scorecard labels its evidence shadow-only. The two idle Freqtrade containers use about 557 MB combined (about 280 MB each, 0% CPU); stopping them until migration is optional and reversible.
- **Paper v2** is pinned off in compose (`OPIP_PAPER_V2_MODE: "off"`). It is built but inactive.
- **v6 task:** diagnose whether Freqtrade results are ingested into the app, and use them as the Scorecard's simulated-trade source. Keep one paper system as the source of truth.

## 7. Versioning and the Scorecard gap

`app/opip/decision/versioning.py` defines `STRATEGY_VERSION = "OPIP-STRATEGY-V1"`, `INTELLIGENCE_VERSION`, `GATE_POLICY_VERSION`, and sets `MODEL_VERSION` and `FEATURE_SCHEMA_VERSION` to `None` ("not yet versioned"). So today there is one strategy version and no model versions in the evidence.

`gate_policy_fingerprint()` hashes the live threshold values, so any threshold change is detectable even if a label is not bumped. v6 should stamp every signal with strategy version, model version and this fingerprint, and keep a strategy and model registry. The `opip/strategy-admin` data folder is 4 KB, so there is nothing to migrate.

Identifier mentions in code (not live values): `policy_version` 610, `model_version` 247, `strategy_version` 52.

## 8. Config and the Ops Center

`app/opip/config/registry.py` declares configuration metadata (what may change, bounds and authority) and never the effective value. Domains today: `V2_SCORING`, `V1_V2_ATTRIBUTION`, `SCANNER`, `F11_SAFETY`, `AI_ROUTING`, `RETENTION_RECONCILIATION`, `SQLITE_RESOURCE_BUDGETS`, `DASHBOARD`. Two rules are enforced in the validator: a setting whose activation authority is the deployment operator cannot be activated from a console, and nothing in the F11 safety domain can be edited into a state that widens trading authority.

For v6, the Config and Data governance wireframes map onto this registry and `control-center-v2`. Extend them; do not build a second config system. Write-only secret entry must keep Kraken keys off the data box and go through the guarded control path.

## 9. Data and retention

Live droplet, 2026-11-11, total 7.4 GB:

| Path | Size |
|---|---|
| `opip/` | 5.6 GB, of which `canonical` 5.2 GB, `qualification` 442 MB, `streaming` 34 MB, `events` 24 KB, `strategy-admin` 4 KB |
| `scan/` | 1.4 GB |
| `full_market_observations.jsonl` | 218 MB |
| `telegram_delivery_events.jsonl` | 90 MB |
| `movement_discovery_v2_1.jsonl` | 32 MB |
| `freqtrade/` | 16 MB |

Rough growth estimate: the paper folder dates from 21 Oct, so about 250 MB a day if canonical accumulated over three weeks. This is an estimate; a second `du` reading on a later day gives the real rate.

Retention settings that already exist (environment values): events go cold after 30 days (`opip_event_cold_after_days`); analytics retention is 7, 15, 30, 60 or 90 days (`opip_analytics_retention_days`, default 30); retention deletion is off (`opip_enable_retention_delete = false`); the TradingView inbox keeps 7 days; JSONL ledgers can be compacted by size (`jsonl_retention.py`). The v6 retention policy table puts these behind the Operations Center with a dry-run preview and a minimum floor per dataset.

Measured on the same droplet: the root disk is 47 GB used of 77 GB, so about 40 GB sits outside `data`. Docker images are 8.2 GB (6.9 GB reclaimable) and build cache 6.9 GB (6.5 GB reclaimable); the remainder is not yet accounted for. Keep older images as the rollback path.

**`canonical` is one SQLite file, not segments.** `opip_canonical_v1.sqlite3` is 5.5 GB with a WAL file and a single-writer lock and socket. There is nothing to trim by segment. Retention design: the writer rolls to a new file per day or week; closed files are shipped to the data box, hash-verified, then deleted after the hot window; the existing file is split by date once on the data box. Deleting rows then running VACUUM is ruled out because the writer locks and it needs another 5.5 GB free. This needs a small writer change and readers that can span files. Whether the writer keeps its own hash chain is still unchecked.

**I/O wait.** `sar` shows 12% to 20% I/O wait over 14 samples (average 17.7%), with about 35% idle CPU and 2.3 GB memory available. The likely cause is the 5.5 GB database exceeding the 2.5 GB page cache; this is a hypothesis until a file roll shows the effect.

## 10. Docs and workflows

- About 28 design notes sit at the repository root (signal-quality phases, wave reports, decision engine v1, v2 and 5_2A, and similar). Proposed: move to `docs/archive/` with an index (theme R1); keep the README, the safety contracts and anything the code or tests cite.
- 16 workflows. Keep deploy, test, review and security workflows. Verify `m07-cutover-toolkit.yml` and `deploy-learning-recovery.yml`, which look like one-time migration tools.

## 11. Retirement method and sequence

Method for every theme: disable behind a flag, observe for at least two weeks (shadow period), then delete code, tests and config together. Archive affected data first. Tag the commit before each deletion so it can be restored. One pull request per theme; no retirement is bundled with new features.

| Theme | Scope | Gate |
|---|---|---|
| R1 | Move root design notes to `docs/archive/` | Owner review |
| R2 | Ten single-file `app/opip/*` modules and `app/backtesting` | No test or doc depends on them; replay code kept |
| R3 | Internal shadow paper simulator (not the control switch, CLI or bridge) | Freqtrade results feed the Scorecard, after paper is live on the new trade box |
| R4 | The 19 unreferenced jobs | Systemd and cron confirmed clear (2026-11-11); check in-container schedulers |
| R5 | `risk`, `detectors`, `committee` decision | Scorecard evidence for the committee |
| R6 | `app/services` second pass | Per-module owner notes |

## 12. Open questions for the owner

1. ~~Is Paper Trade v1 switched off?~~ Answered: the master switch is off and has never been on (see section 6).
2. ~~Are any of the 19 unreferenced jobs run outside the repo?~~ Answered: `systemctl list-timers` shows only OS timers and `crontab -l` is empty, so jobs run inside Docker. The `opip_analytics_maintenance` and `drain_tradevisor_inbox` caution still stands for in-container schedulers.
3. ~~Is `opip/canonical` segmented?~~ Answered: single SQLite file (section 9).
4. How many strategies and models are actually live? The registry is empty, so this needs the owner's knowledge.
5. Does the committee earn its cost? Decide after the Scorecard exists.
6. What is the daily data growth? Re-run `du -sh data` a day later.

## Appendix A: modules the analysis could not reach

Line counts in parentheses. Candidates only; see section 2.

**app.backtesting** (6 modules, 345 lines): `backtesting` (1), `engine` (127), `historical_data` (50), `metrics` (43), `models` (53), `walk_forward` (71)

**app.jobs** (19 modules, 1,249 lines): `build_opportunity_accountability` (66), `drain_tradevisor_inbox` (67), `monitor_paper_trades` (24), `opip_analytics_maintenance` (118), `process_p1_shadow_outbox` (23), `report_analytics` (20), `report_daily_gainer_reconciliation` (39), `report_evidence_health` (27), `report_explosion_learning` (73), `report_explosion_precursor_learning` (43), `report_learning` (159), `report_movement_discovery_learning` (49), `report_signal_quality_phase2` (110), `report_signal_quality_phase3a_timing` (52), `report_signal_quality_phase3c` (75), `report_tradevisor_learning` (207), `report_winner_forensics` (29), `scan_movers_v2_2_shadow` (33), `scan_native_flow_shadow` (35)

**app.opip.canonical** (1 modules, 71 lines): `rebuild` (71)

**app.opip.committee** (3 modules, 1,925 lines): `conformance` (931), `fakes` (192), `shadow_review` (802)

**app.opip.contracts** (5 modules, 4,148 lines): `horizon_lifecycle` (1648), `opportunity_phases` (480), `paper_simulator_policy` (177), `portfolio_paper_handoff` (858), `thesis` (985)

**app.opip.decision** (8 modules, 1,823 lines): `equivalence` (463), `equivalence_store` (212), `evidence` (298), `models_v2` (221), `policy_snapshot` (140), `promotion` (317), `replay` (112), `serialization` (60)

**app.opip.detectors** (2 modules, 585 lines): `detectors` (36), `ignition` (549)

**app.opip.discovery** (1 modules, 56 lines): `replay` (56)

**app.opip.early** (2 modules, 1,063 lines): `promotion` (434), `replay` (629)

**app.opip.events** (3 modules, 2,893 lines): `attention` (1485), `coverage` (741), `opportunity_engine` (667)

**app.opip.feasibility** (1 modules, 861 lines): `feasibility` (861)

**app.opip.features** (3 modules, 2,440 lines): `ml_bridge` (128), `r2_shadow_parity` (2136), `replay` (176)

**app.opip.fev_evidence_reader** (1 modules, 212 lines): `fev_evidence_reader` (212)

**app.opip.forecast** (1 modules, 510 lines): `forecast` (510)

**app.opip.forecast_evaluation** (1 modules, 1,448 lines): `forecast_evaluation` (1448)

**app.opip.forecast_labels** (1 modules, 619 lines): `forecast_labels` (619)

**app.opip.forecast_qualification** (1 modules, 387 lines): `forecast_qualification` (387)

**app.opip.learning** (4 modules, 2,861 lines): `diagnostics` (185), `establish_coverage_discontinuity` (79), `evaluation` (2375), `governance` (222)

**app.opip.ml** (5 modules, 752 lines): `dataset` (198), `labels` (315), `registry` (120), `runtime` (31), `validation` (88)

**app.opip.opportunity_lifecycle** (1 modules, 394 lines): `opportunity_lifecycle` (394)

**app.opip.opportunity_persistence** (1 modules, 107 lines): `opportunity_persistence` (107)

**app.opip.portfolio_comparator** (1 modules, 593 lines): `portfolio_comparator` (593)

**app.opip.portfolio_selector** (1 modules, 659 lines): `portfolio_selector` (659)

**app.opip.risk** (7 modules, 2,067 lines): `alert_state` (274), `config` (115), `exposure_matcher` (371), `notifier` (88), `observer` (730), `policy` (374), `relevance` (115)

**app.opip.streaming** (2 modules, 1,036 lines): `coordinator` (1001), `read_model` (35)

**app.services** (24 modules, 10,449 lines): `alert_decision_binding` (349), `autonomy_readiness` (34), `capital_admission_integration` (820), `capital_rotation_intelligence` (4836), `counterfactual_gate_audit` (180), `daily_gainer_reconciliation` (185), `historical_analytics` (67), `liquidity_verification` (178), `paper_trade_cli` (83), `paper_v2_capital_admission_execution` (332), `price_geometry` (101), `production_scaling` (88), `prospective_signal_evaluation` (208), `register_trade` (32), `release_provenance_reconcile` (925), `self_calibration` (125), `signal_evidence_contracts` (128), `signal_quality_audit` (189), `target_v2_coverage_summary` (103), `trade_cli` (85), `tradevisor_four_hour` (505), `tradevisor_learning` (746), `tradingview_evidence_diagnostics` (87), `winner_forensics` (63)
