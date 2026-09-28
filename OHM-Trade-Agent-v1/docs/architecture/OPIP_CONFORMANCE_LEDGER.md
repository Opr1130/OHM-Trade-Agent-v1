# O’Pip conformance ledger

Historical audit base: `a416be0a068dc58543a4b6cd254d5c42fcaf4c96`

Current reconciled code/production baseline: `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`

Architecture source: v1.4.3 DOCX pinned in `docs/architecture/v1.4.3/SOURCE.md`.

Runtime observations are summarized here and detailed in `OPIP_RUNTIME_TRUTH_2026-09-28.md`, with `OPIP_RUNTIME_TRUTH_2026-09-27.md` preserved as the preceding historical observation. Code status is from the current reconciled baseline unless a row says otherwise. A green test is not production proof. The reconciliation updates status only where evidence changed; it does not convert absence of evidence into completion.

Status values: `IMPLEMENTED_VERIFIED`, `IMPLEMENTED_NOT_ACTIVE`, `IMPLEMENTED_AWAITING_RUNTIME_EVIDENCE`, `SHADOW`, `LEGACY_ACTIVE`, `PARTIAL`, `MISSING`, `SUPERSEDED`, `RETIRE_AFTER_CUTOVER`, `UNKNOWN_NEEDS_EVIDENCE`.

## F1 — Validated Market Observation

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Preserve provenance, ingestion order, freshness, and coverage. Observed market facts only. |
| CURRENT_IMPLEMENTATION | `app/scanner/market_data_validation.py`; `app/services/full_market_observation.py` writes `full_market_observations.jsonl`. Feature-bus snapshots exist separately and are not on the live cycle. |
| CURRENT_OWNER | Scanner / observation services |
| CURRENT_WRITER | Full-market observation JSONL writer |
| CURRENT_CONSUMERS | Scan, dashboard freshness inputs, learning exports |
| CURRENT_RUNTIME_AUTHORITY | Live scan path. FeatureSnapshot path is off. |
| TEST_EVIDENCE | Market-data and observation tests in the existing suite. Not re-run as a production probe. |
| IMPLEMENTATION_STATUS | `LEGACY_ACTIVE` |
| DUPLICATE_OR_OVERLAPPING_PATHS | Feature-bus observation contract vs JSONL observation log |
| TARGET_AUTHORITY | Validated observations feeding FeatureSnapshot |
| CUTOVER_GATE | Feature-bus shadow parity accepted and consumers enumerated |
| RETIREMENT_CANDIDATE | No. The observation fact remains. The JSONL writer is the retirement candidate after canonical capture. |
| DELETE_GATE | Named stop time, archive, consumer census, rollback |
| BLOCKERS | Feature bus pinned off |
| NOTES | Missing evidence must stay unknown. |

## F2 — Shared Feature Bus

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Bounded, reproducible features over retained inputs and checkpoints. Versioned feature values. PR #237 stays the Feature Bus foundation and does not absorb detector, selector, Committee, or dashboard work. |
| CURRENT_IMPLEMENTATION | `app/opip/contracts/features.py`, `app/opip/features/engine.py`, `pipeline.py`, `publisher.py`, `parity.py`, `replay.py`, `app/opip/features/r2_shadow_parity.py`, `app/jobs/run_feature_bus_pilot.py` |
| CURRENT_OWNER | Feature-bus package |
| CURRENT_WRITER | Publisher, only when feature-bus mode and canonical-writer mode are both `shadow` |
| CURRENT_CONSUMERS | Pilot and tests. `app/jobs/run_cycle.py` does not call it. |
| CURRENT_RUNTIME_AUTHORITY | **None.** Compose pins `OPIP_FEATURE_BUS_MODE=off` on the core service and `run_cycle` does not call the Feature Bus. The R2 shadow proof produced evidence only; it did not act as an authority. |
| TEST_EVIDENCE | `tests/test_opip_feature_bus_pr3.py`, `tests/test_opip_feature_bus_pr3_integrity.py`, `tests/test_opip_feature_bus_r2_shadow_parity.py` |
| IMPLEMENTATION_STATUS | `IMPLEMENTED_NOT_ACTIVE` in runtime. R2 shadow parity/replay evidence is accepted (see below); runtime authority is unchanged. |
| VERIFIED_SHADOW_EVIDENCE | R2 `ATDD-R2-feature-bus-shadow-parity` passed and merged via PR #284 (`facf8e369e1251697bf9799bc9b1c575a9cdc3ec`). Deterministic point-in-time replay/parity is proven: sealed snapshots replay byte-identically, invalid or out-of-time inputs fail closed, retained state and restart state are reproduced, and the parity report describes sealed `FeatureSnapshot` values rather than recomputing a competing value. 0 valid unresolved non-outdated review blockers at merge. |
| DUPLICATE_OR_OVERLAPPING_PATHS | `app/scanner/technical_scorer.py`, `app/scanner/short_technical_scorer.py`, `app/services/signal_features.py`, `app/services/explosion_state.py` |
| TARGET_AUTHORITY | Shared Feature Bus |
| CUTOVER_GATE | Shadow parity/replay evidence against legacy indicators (now supplied by R2), then a separate owner decision to remove the compose pin. No cutover occurred. |
| RETIREMENT_CANDIDATE | Legacy technical feature calculations, after cutover |
| DELETE_GATE | Consumer census of scorer callers, archive of parity fixtures, rollback to the pinned-off compose |
| BLOCKERS | Not scheduled. Explicitly pinned off so a stale `.env` cannot enable it when the writer is shadow. |
| NOTES | `replay.py` prepares detector replay input and does not evaluate a detector. The accepted R2 evidence does not grant the Feature Bus any production runtime authority, and `run_cycle` still does not call it. |

## F3 — Stateful Detector Runtime / IGNITION

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | IGNITION is the sole detector family. `evaluate` is a pure function of FeatureSnapshot, prior DetectorState, and evaluation_time. No I/O, hidden clock, or global mutable state. |
| CURRENT_IMPLEMENTATION | Contract and fixture only: `docs/architecture/v1.2/D_DETECTOR_CONTRACT.md`, `fixtures/detector_evaluate.example.json`. No `class DetectorState` under `app/`. |
| CURRENT_OWNER | None for the target runtime |
| CURRENT_WRITER | None |
| CURRENT_CONSUMERS | None |
| CURRENT_RUNTIME_AUTHORITY | None |
| TEST_EVIDENCE | Contract fixture tests only |
| IMPLEMENTATION_STATUS | `MISSING` |
| DUPLICATE_OR_OVERLAPPING_PATHS | `app/services/explosion_state.py` uses the string `IGNITION` as a phase label. That is not DetectorState. |
| TARGET_AUTHORITY | IGNITION detector runtime |
| CUTOVER_GATE | Deterministic replay of snapshot + prior state + evaluation_time, shadow only, after Feature Bus evidence exists |
| RETIREMENT_CANDIDATE | Explosion phase classifier, after the detector owns transitions |
| DELETE_GATE | No deletion until the detector is the live claim source and consumers have moved |
| BLOCKERS | Feature Bus is off. No detector module. |
| NOTES | Do not treat the phase string as the v1.4.3 detector. |

## F4 — Opportunity Lifecycle

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | One episode lifecycle: dedup, deferral, deadline, expiry, terminal reason. |
| CURRENT_IMPLEMENTATION | Fragmented. Live walk is `app/jobs/scan_opportunities.py`. Shadow funnel is `app/opip/decision/funnel.py` and `store.py`. Other clocks: `entry_watch_queue.py`, `price_movement_radar.py`, `monitor_pending_setups.py`, `signal_quality_phase2.py` `MoveEpisode`, `app/opip/early/timing_ledger.py`. |
| CURRENT_OWNER | `scan_opportunities` for live admission. Decision engine docstring says it is not authoritative. |
| CURRENT_WRITER | Scan plus JSONL funnel store |
| CURRENT_CONSUMERS | Alerts, paper routing, dashboard funnel |
| CURRENT_RUNTIME_AUTHORITY | Legacy scan, every unified cycle |
| TEST_EVIDENCE | Decision funnel tests and scan tests |
| IMPLEMENTATION_STATUS | `PARTIAL` |
| DUPLICATE_OR_OVERLAPPING_PATHS | Listed above. Signal Quality v2 proposes `app/opip/opportunity/`, which does not exist. |
| TARGET_AUTHORITY | One opportunity lifecycle over detector claims |
| CUTOVER_GATE | One identity, one terminal-reason vocabulary, consumer census of watch/radar/pending |
| RETIREMENT_CANDIDATE | Parallel episode clocks after that owner exists |
| DELETE_GATE | Each clock names its replacement and a stop time |
| BLOCKERS | F3 is missing, so there is no detector claim to lifecycle |
| NOTES | Funnel terminal reasons are scan outcomes, not episode deadlines. |

## F5 — Feasibility and Safety

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Market, data, liquidity, and execution constraints. Veto or abstain. Missing evidence is never favorable. `INSUFFICIENT_EVIDENCE` is an allowed abstention. |
| CURRENT_IMPLEMENTATION | Spread across `market_data_validation.py`, margin and short tradeability checks, `execution_validation.py`, `target_attainability.py`, `economic_quality_gate.py`, `portfolio_risk.py`, `trade_action_gate.py`, `app/services/risk.py`. `app/opip/decision/gates.py` adapts the same evaluators in shadow. Chase risk is advisory and is not on the scan admission path. |
| CURRENT_OWNER | Scanner and service gates |
| CURRENT_WRITER | Scan decisions and funnel telemetry |
| CURRENT_CONSUMERS | Alert and paper admission |
| CURRENT_RUNTIME_AUTHORITY | Legacy scan |
| TEST_EVIDENCE | Gate and scan tests |
| IMPLEMENTATION_STATUS | `LEGACY_ACTIVE` |
| DUPLICATE_OR_OVERLAPPING_PATHS | Shadow decision gates re-call production evaluators |
| TARGET_AUTHORITY | One feasibility seam in front of the forecast |
| CUTOVER_GATE | Same veto results as the live gates on a frozen candidate set, including abstention |
| RETIREMENT_CANDIDATE | Duplicate gate adapters only after the seam is the admission path |
| DELETE_GATE | Do not delete the live vetoes first |
| BLOCKERS | No target seam module |
| NOTES | v1.4.3 does not add a new risk veto for the Committee. |

## F6 — Forecast Engine

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Execution-aware probability, expected return, uncertainty, and validity horizon. No allocation. LLM confidence is not a probability. |
| CURRENT_IMPLEMENTATION | No `ForecastEngine`. Decision records set `calibrated_probability: False`. Economic gate is pass/fail on move, net profit, and reward-to-risk. Committee confidence is an ordinal 0–100. |
| CURRENT_OWNER | None |
| CURRENT_WRITER | None |
| CURRENT_CONSUMERS | None |
| CURRENT_RUNTIME_AUTHORITY | None |
| TEST_EVIDENCE | Decision records assert probability is not calibrated |
| IMPLEMENTATION_STATUS | `MISSING` |
| DUPLICATE_OR_OVERLAPPING_PATHS | Ranking score, alert confidence, Committee rubric score |
| TARGET_AUTHORITY | Forecast engine |
| CUTOVER_GATE | Proper scores and a declared horizon, sealed from future labels |
| RETIREMENT_CANDIDATE | Any display that presents a score as a win probability, after the forecast exists |
| DELETE_GATE | Not applicable until a forecast owner exists |
| BLOCKERS | No probability model and no execution-aware path model |
| NOTES | Do not promote an ordinal score into this feature. |

## F7 — Economic / Portfolio Selector

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Constrained paper capital toward portfolio net dollars. Atomic reservation. No Kelly, leverage, or covariance optimizer. |
| CURRENT_IMPLEMENTATION | `profit_ranking.py` sorts a weighted score. `candidates.py` keeps Top-8 at technical score ≥ 80. `economic_quality_gate.py` is pass/fail. `portfolio_risk.py` vetoes count and exposure. Paper v2 reservation exists only on the inactive Paper v2 path. |
| CURRENT_OWNER | Profit ranking plus portfolio-risk veto |
| CURRENT_WRITER | Scan ranking |
| CURRENT_CONSUMERS | Alerts and paper routing |
| CURRENT_RUNTIME_AUTHORITY | Legacy ranking on the live scan |
| TEST_EVIDENCE | Profit-ranking and portfolio-risk tests. `CLEANUP_MATRIX.md` marks the comparator KEEP (live). |
| IMPLEMENTATION_STATUS | `LEGACY_ACTIVE` |
| DUPLICATE_OR_OVERLAPPING_PATHS | Early cohort selector (promotion default false). Paper v2 reservation. Signal Quality v2 capital allocator (not built). |
| TARGET_AUTHORITY | Constrained portfolio selector |
| CUTOVER_GATE | Matched comparison against cash and the frozen ranking comparator, on the same candidate panel |
| RETIREMENT_CANDIDATE | Top-8 technical gate and profit-ranking comparator, after that comparison |
| DELETE_GATE | Technical cutover recorded, consumers moved, rollback to the ranking path available |
| BLOCKERS | F6 is missing. The live path does not maximize portfolio net dollars globally. |
| NOTES | `OPIP_GLOBAL_CAPITAL_RANKING_ENABLED` default true is a ranking flag, not the target selector. |

## F8 — Realistic Paper Execution

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Executable fills, cash, positions, fees, latency, and exits. `NO_FILL`, `PARTIAL_FILL`, `FULL_FILL`, `TARGET`, `STOP`, `TIMEOUT`, `RISK_EXIT`. Isolated from funded credentials. |
| CURRENT_IMPLEMENTATION | Paper v1 registry and monitor. Freqtrade dry-run containers. Paper v2 execution, quote, fill, and ledger contracts under `app/services/paper_v2_*.py` and `app/opip/contracts/paper_execution*.py`. |
| CURRENT_OWNER | When Paper v2 is not requested, scan treats Freqtrade dry-run as the paper engine and Paper v1 as the shadow simulator. |
| CURRENT_WRITER | Paper v1 JSON ledger. Freqtrade dry-run state. Canonical SQLite for Paper v2 events if that mode is active. |
| CURRENT_CONSUMERS | Dashboard, learning export, cockpit derivation |
| CURRENT_RUNTIME_AUTHORITY | Freqtrade paper containers were healthy in the 2026-09-28 deploy log. Paper v2 mode was not printed. Settings default is `off`. Host `.env` was not read. |
| TEST_EVIDENCE | `tests/test_paper_trading_v1_core.py`, `tests/test_opip_paper_v2_*bc3.py` |
| IMPLEMENTATION_STATUS | Paper v1 `LEGACY_ACTIVE`. Freqtrade dry-run `LEGACY_ACTIVE` as the current paper engine in code when v2 is off. Paper v2 `IMPLEMENTED_NOT_ACTIVE`. Live Paper v2 mode `UNKNOWN_NEEDS_EVIDENCE`. |
| DUPLICATE_OR_OVERLAPPING_PATHS | Paper v1, Freqtrade dry-run, Paper v2 |
| TARGET_AUTHORITY | Paper v2 on the canonical ledger, after the target selector exists |
| CUTOVER_GATE | `OPIP_PAPER_V2_MODE=active`, legacy drain `READY`, healthy protection sweep, universe metadata. Architecture still wants this after economic selection, not before F6/F7. |
| RETIREMENT_CANDIDATE | Paper v1 new enrollments, then the monitor, then Freqtrade as the O’Pip paper engine |
| DELETE_GATE | See retirement ledger. Do not delete while any open obligation remains. |
| BLOCKERS | Mode default off. Drain requires zero Freqtrade and Paper v1 exposure. Activating v2 now would paper the legacy selector. |
| NOTES | Paper v2 has no pending-limit state machine and no short engine. |

## F9 — Outcome and Learning

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Reproducible labels, calibration, regret, and prospective evidence. Derived analysis only. Human promotion. |
| CURRENT_IMPLEMENTATION | See the learning map below. Several label stores exist. None is the single outcome authority. |
| CURRENT_OWNER | Split. Canonical terminal paper outcomes vs Phase3C vs discovery vs trade-outcome journal. |
| CURRENT_WRITER | Multiple jobs, mostly on the learning plane |
| CURRENT_CONSUMERS | Reports, dashboard, profit intelligence, accountability |
| CURRENT_RUNTIME_AUTHORITY | Learning worker last successful control-plane install is not this core SHA. See runtime truth. |
| TEST_EVIDENCE | Phase3C, discovery, accountability, and learning-replica tests |
| IMPLEMENTATION_STATUS | `PARTIAL` |
| DUPLICATE_OR_OVERLAPPING_PATHS | Phase3C, discovery outcomes, movement-discovery outcomes, trade outcome registry, profitability learning, decision learning |
| TARGET_AUTHORITY | One canonical outcome history, with rebuildable projections |
| CUTOVER_GATE | Each fact names one writer. Projections reconcile to it. |
| RETIREMENT_CANDIDATE | Duplicate forward-label jobs after the canonical outcome grain exists |
| DELETE_GATE | Do not create another outcome engine. Delete only after consumers move. |
| BLOCKERS | Learning worker SHA alignment is an owner deploy, not a new engine. |
| NOTES | Missing labels stay unresolved. |

## F10 — Dashboard / Reporting

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Eight pages: System Pulse, Discovery and Regime, Decision Trace, Paper Portfolio, Quality and Missed Opportunity, Committee Value and AI Cost, Learning and Releases, Incidents and Infrastructure. Shared semantic model, typed filters, cross-filters, explicit unknown, saved views, watermark, decision trace, no trading authority. Section 21 adds Committee trust panels. |
| CURRENT_IMPLEMENTATION | Legacy `/dashboard` and `dashboard_read_model.py`. Grafana cockpit JSON. B/C-4 cockpit API. Profit Intelligence library with no route. |
| CURRENT_OWNER | Split read models |
| CURRENT_WRITER | None of these are fact authorities |
| CURRENT_CONSUMERS | Operators |
| CURRENT_RUNTIME_AUTHORITY | Legacy dashboard is mounted in `app/main.py`. Grafana and B/C-4 depend on analytics deploy stages not proven for this SHA. |
| TEST_EVIDENCE | `tests/test_bc4_cockpit_data_contract_v1.py` and dashboard tests |
| IMPLEMENTATION_STATUS | `PARTIAL` |
| DUPLICATE_OR_OVERLAPPING_PATHS | Four surfaces, none covering all eight pages |
| TARGET_AUTHORITY | One semantic read service over canonical projections |
| CUTOVER_GATE | Fixture reconciliation of counts and P&L, then retire duplicate pages |
| RETIREMENT_CANDIDATE | Legacy `/dashboard` after the semantic pages exist |
| DELETE_GATE | Consumers of `/api/analytics/*` enumerated |
| BLOCKERS | Canonical detector, forecast, and Committee facts are not on the live path |
| NOTES | Do not redesign the dashboard in the recovery increment that follows this audit. |

## F11 — Safety / Monitoring

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Independent protection, deterministic suspension, human resumption. Protection does not depend on discovery, forecasts, economics, or AI. |
| CURRENT_IMPLEMENTATION | `monitor_active_trades.py` runs from `run_cycle` before discovery. Paper v2 protection sweep runs only inside the opportunity scan. Event Risk Shield defaults off and is not called by `run_cycle`. |
| CURRENT_OWNER | Active-trade monitor for live advisory protection |
| CURRENT_WRITER | Active-trade and incident records |
| CURRENT_CONSUMERS | Alert v2 incident text, operator status |
| CURRENT_RUNTIME_AUTHORITY | Unified cycle every minute, observed in the deploy log |
| TEST_EVIDENCE | Active-trade, alert v2, and event-risk-shield tests |
| IMPLEMENTATION_STATUS | Active-trade protection `LEGACY_ACTIVE`. Event Risk Shield `IMPLEMENTED_NOT_ACTIVE`. Paper v2 protection `IMPLEMENTED_NOT_ACTIVE`. |
| DUPLICATE_OR_OVERLAPPING_PATHS | Active-trade monitor, Paper v1 monitor, Paper v2 sweep |
| TARGET_AUTHORITY | Independent protection with reserved writer priority |
| CUTOVER_GATE | Protection still runs when discovery, forecast, and AI are down |
| RETIREMENT_CANDIDATE | None until one protection owner covers open paper and operator positions |
| DELETE_GATE | Open-position drill |
| BLOCKERS | Paper v2 sweep is not on the one-minute slot |
| NOTES | This path does not place funded orders. |

## F12 — AI Advisory / Research

| Field | Evidence |
| --- | --- |
| ARCHITECTURE_REQUIREMENT | Bounded advisory research. No runtime authority. Sections 16–18 and 20–21 specify the Committee, registry, bake-off, weakness learning, and quantitative trust. |
| CURRENT_IMPLEMENTATION | `app/opip/committee/` with `AUTHORITATIVE = False` and `CAN_PLACE_ORDERS = False`. No import from `app/jobs` or `app/api`. |
| CURRENT_OWNER | Committee package, shadow only |
| CURRENT_WRITER | Committee JSONL store when a cycle runs |
| CURRENT_CONSUMERS | Trust report inside the committee package |
| CURRENT_RUNTIME_AUTHORITY | Last control-plane observation 2026-09-25: mode shadow at SHA `86d5290b`, timer disabled, zero role results. Not this core SHA. Committee still has zero runtime trading authority. |
| MODEL_ROUTE_DRIFT | Implementation drift, recorded without inventing conformance. The v1.4.3 registry (sections 16–18) names *provisional benchmark candidates* across OpenAI, Google, and DeepSeek (for example `gpt-5.6-terra`, `gemini-3.8-flash`, `deepseek-v4-pro`). The approved in-code shadow registry routes only OpenAI (`gpt-5.6-terra`) and Anthropic (`claude-sonnet-5`). Anthropic is not in the v1.4.3 provisional candidate table, and the Google and DeepSeek candidates are not implemented. This is release-specific routing, not conformance. Treating either set as the other would overstate conformance; owner review or a registry change is required before committee evidence is claimed against the v1.4.3 bake-off. |
| TEST_EVIDENCE | `tests/test_opip_committee_safety_v1.py` and IC contract tests |
| IMPLEMENTATION_STATUS | `SHADOW` in code and in the last activation proof. Live cases `IMPLEMENTED_AWAITING_RUNTIME_EVIDENCE`. |
| DUPLICATE_OR_OVERLAPPING_PATHS | None back into the trading cycle |
| TARGET_AUTHORITY | Asynchronous advisory path |
| CUTOVER_GATE | v1.4.3 forbids influence until prospective trust gates pass and a separate human approval exists |
| RETIREMENT_CANDIDATE | None |
| DELETE_GATE | Not a deletion candidate |
| BLOCKERS | Release SHA is behind `main`. Timer was inactive. Weakness registry is in-process, not a durable JSONL stream. |
| NOTES | No additional Committee feature work is required to start the next implementation increment. The Committee remains advisory and shadow-only with no runtime trading authority; the model-route drift above is recorded, not resolved, by this reconciliation. |

## Adjacent authorities

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical writer | `SHADOW` | Core compose pin `OPIP_CANONICAL_WRITER_MODE=shadow`. Writer container mode pin `off` is unused by the service. Deploy log showed `opip-canonical-writer` healthy. Not the primary paper ledger while Paper v1/Freqtrade remain the live engines. |
| Decision Intelligence | `SHADOW` | `app/opip/decision/` records what the legacy scan already did. Engine is not the admission authority. |
| Signal Quality v2 | `MISSING` as modules | Design doc only. `app/opip/opportunity/`, `app/opip/protection/`, and `app/opip/calibration/` are absent. Do not build them as a second spine. |
| Alerts | `LEGACY_ACTIVE` | Decision-first wording exists for system incidents in `alert_v2_format.py`. Trade cards still use the scan plan path. v1.4.2 contract is not fully the live trade alert. |
| Protection | `LEGACY_ACTIVE` | See F11. |
| Learning replica | `IMPLEMENTED_AWAITING_RUNTIME_EVIDENCE` | Core export for this SHA reported ready. Learning worker code was not redeployed with this core SHA. |
| Deployment | `IMPLEMENTED_VERIFIED` for the core control plane on this SHA | See runtime truth. Learning, analytics, and Committee releases are different SHAs. |

## Intelligence Committee IC-001–IC-045

The in-tree `docs/MODULE2_INTELLIGENCE_COMMITTEE.md` IC-042 sentence that says the deploy workflow has never been run is stale. GitHub Actions shows a successful `/deploy-committee` on 2026-09-25 for `3457d59f` and a successful `/shadow-committee` the same day for `86d5290b`. Those runs are control-plane history. They are not evidence of matured cases.

| IDs | Code status at this SHA | Runtime |
| --- | --- | --- |
| IC-001–IC-004, IC-006, IC-007, IC-009–IC-018, IC-023, IC-024, IC-028–IC-040, IC-044, IC-045 | `IMPLEMENTED_NOT_ACTIVE` or `SHADOW` contract present. No trading path. | Timer last observed disabled |
| IC-005, IC-008, IC-019–IC-022, IC-025–IC-027, IC-041, IC-043 | `IMPLEMENTED_AWAITING_RUNTIME_EVIDENCE` | No real provider results in the activation proof (`role_results=0`) |
| IC-042 | Deployment artifacts exist. Last activation proved shadow mode and an inactive timer at `86d5290b`. | Not aligned to `facf8e36` |

Weakness registry, trust report, economics, and matched-baseline types exist in code. They are not populated by production baseline results. A test passing is not a real-provider result.
