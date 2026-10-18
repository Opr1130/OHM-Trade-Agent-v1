# O'Pip F10 source census and semantic read contract (F10-0)

Status: `CENSUS_RECORDED_NOT_IMPLEMENTED`

Increment: `ATDD-R5-F10-source-census`

Authority granted by this document: `NONE`

Parity verdict: `NOT_VERIFIED` (0 of 8 pages verified)

Runtime integration: `RUNTIME_ACTIVE: FALSE`

Metric authority: `paper-metrics-v1` (the existing versioned registry, unchanged)

Recorded at base commit: `4d78004f` (`feature/opip-atdd-r5-stack-merge-readiness` head)

This census is the F10-0 deliverable: the source census, the capability gaps, the
page-to-fact contract and the metric/panel map that every later F10 increment
builds against. It records what exists, what is missing, what is blocked by
upstream evidence and what only the owner may decide. It changes no runtime
behaviour, adds no page, route, projection, metric, service, scheduler or store,
removes no legacy surface, and grants no trading, Telegram, admission, promotion,
deployment or production authority. It ratifies no numeric threshold. It leaves
every parity verdict exactly where `ATDD-R5-F10-dashboard-parity-contract` left
it.

## 1. Adopted requirement and what this census is

F10 is not invented here. It is the adopted requirement recorded in the R5
register, the attached architecture and the conformance ledger, whose
authoritative statements are:

- Register row `F10` (`docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`):
  "Dashboard: same eight pages and semantic model; expose plans, capital and
  horizons", source `v1.5.0`, clause `§15;§13`, status `PARTIAL`, contracts
  `SC-08, SC-12`, open input `OQ-10`, gate `G4`.
- Adopted architecture section 15 (feature table): `F10 Dashboard`, disposition
  `EXTEND`, "same eight pages and semantic model; expose plans, capital and
  horizons."
- Conformance ledger `F10` row: `CURRENT_WRITER` states that none of the current
  surfaces is a fact authority; `DUPLICATE_OR_OVERLAPPING_PATHS` is "Four
  surfaces, none covering all eight pages"; `TARGET_AUTHORITY` is "One semantic
  read service over canonical projections"; `CUTOVER_GATE` is "Fixture
  reconciliation of counts and P&L, then retire duplicate pages"; `BLOCKERS` is
  "Canonical detector, forecast, and Committee facts are not on the live path".
- `docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md` (increment
  `ATDD-R5-F10-dashboard-parity-contract`): the frozen eight-page inventory, the
  exact parity predicate, ten fail-closed negative cases and the retention
  decision. That document remains the authority for page parity state and for the
  follower increment's obligations; this census does not supersede it.

WHAT THIS CENSUS ADDS. The conformance ledger names four overlapping surfaces and
one target authority; it does not say, per page and per panel, which canonical
producer supplies which fact, what is missing, or what is blocked. This census
does, from verified repository evidence rather than from assumption, and it
classifies every capability so a later increment knows exactly what to extend,
what to build and what it must not claim.

WHAT THIS CENSUS DOES NOT DO. It does not implement a page, call a producer,
declare a projection authority per page (that is an owner-gated decision the
parity specification already records as `UNDECIDED`), verify parity, ratify a
threshold, or authorize any runtime, workflow or application path.

## 2. Source census (verified surfaces)

Every line below is a repository-root path verified to exist at the recorded base
commit, with the role this census assigns it. The confirming test asserts that
each named path exists, so a hallucinated or moved surface fails closed. Roles are
descriptive; a surface listed here is not thereby a fact authority.

```text
OHM-Trade-Agent-v1/app/opip/contracts/paper_metrics.py | versioned metric registry (paper-metrics-v1) - the only metric-semantics authority
OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py | frozen PortfolioPlan and candidate-comparison record (SC-08)
OHM-Trade-Agent-v1/app/opip/contracts/temporal.py | canonical temporal record vocabulary (SC-12)
OHM-Trade-Agent-v1/app/opip/contracts/paper_outcome.py | quote-currency vocabulary for USD and USDT portfolio separation
OHM-Trade-Agent-v1/app/opip/canonical/writer.py | the single canonical writer over durable domain records
OHM-Trade-Agent-v1/app/opip/cockpit/ledger.py | reconciled Paper-v2 ledger projection (cockpit-ledger-v1) over canonical evidence
OHM-Trade-Agent-v1/app/opip/cockpit/portfolio.py | per-quote-currency portfolio projection (cockpit-portfolio-v1): equity series, drawdown, strategy contribution, attention
OHM-Trade-Agent-v1/app/opip/cockpit/trust.py | the four independent trust dimensions and the unavailable envelope
OHM-Trade-Agent-v1/app/opip/cockpit/__init__.py | the cockpit projection export surface
OHM-Trade-Agent-v1/app/opip/profit_intelligence/semantics.py | fact-availability vocabulary, lineage stages, evidence-gap registry, metric-authority map
OHM-Trade-Agent-v1/app/opip/profit_intelligence/lineage.py | per-trade execution lineage projection over canonical rows
OHM-Trade-Agent-v1/app/opip/profit_intelligence/economics.py | per-currency economic integrity with supported cost components
OHM-Trade-Agent-v1/app/opip/profit_intelligence/read_model.py | Profit Intelligence overview envelope (profit-intelligence-read-model-v2)
OHM-Trade-Agent-v1/app/opip/data_platform/freshness.py | canonical freshness vocabulary and stream/maintenance classification (LIVE, DEGRADED, STALE, UNAVAILABLE)
OHM-Trade-Agent-v1/app/opip/data_platform/read_model.py | read-only freshness and historical snapshot read model
OHM-Trade-Agent-v1/app/opip/data_platform/reconcile.py | projection reconciliation checks over the canonical replica
OHM-Trade-Agent-v1/app/api/cockpit.py | read-only cockpit analytics API (three GET routes, own read-only secret, no write path)
OHM-Trade-Agent-v1/app/api/cockpit_service.py | analytics-plane ASGI entry point mounting only the read-only cockpit router
OHM-Trade-Agent-v1/app/api/cockpit.html | the single existing cockpit page asset
OHM-Trade-Agent-v1/app/api/dashboard.py | legacy mounted dashboard analytics and page routes
OHM-Trade-Agent-v1/app/api/dashboard.html | legacy mounted dashboard asset
OHM-Trade-Agent-v1/app/services/dashboard_read_model.py | legacy event-file dashboard read model (the second, overlapping read surface)
OHM-Trade-Agent-v1/app/services/alert_taxonomy.py | alert-plane taxonomy (SYSTEM, TRADE_PROTECTION, SIGNAL_OPPORTUNITY, EXTERNAL_OPERATOR, COMMAND)
OHM-Trade-Agent-v1/app/services/alert_v2_format.py | decision-first alert rendering
OHM-Trade-Agent-v1/app/services/notification_policy.py | the single notification eligibility and dedupe authority (r4-alert-quality-v1)
OHM-Trade-Agent-v1/app/services/telegram_delivery.py | Telegram delivery ledger and delivery-state recording
OHM-Trade-Agent-v1/app/services/telegram_callback_listener.py | Telegram callback handling over existing command authorization
OHM-Trade-Agent-v1/app/services/telegram_command_center.py | operator command surface over existing authority
OHM-Trade-Agent-v1/deploy/grafana/dashboards/opip-intelligence-cockpit-v1.json | the presentation-plane cockpit dashboard consuming the freshness view
OHM-Trade-Agent-v1/docs/architecture/bc4/BC4_COCKPIT_DATA_CONTRACT.md | frozen B/C-4 cockpit semantic and data contract, including the withheld-metric decisions
OHM-Trade-Agent-v1/docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md | F10 parity specification: frozen pages, parity predicate, negative cases, retention decision
OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md | the per-feature conformance record this census extends
OHM-Trade-Agent-v1/docs/architecture/OPIP_RETIREMENT_LEDGER.md | the retirement record governing the legacy cockpit
OHM-Trade-Agent-v1/tests/test_bc4_cockpit_ledger_v1.py | existing ledger-projection contract tests
OHM-Trade-Agent-v1/tests/test_bc4_cockpit_portfolio_v1.py | existing portfolio-projection contract tests
OHM-Trade-Agent-v1/tests/test_bc4_cockpit_api_v1.py | existing read-only cockpit API contract tests
OHM-Trade-Agent-v1/tests/test_bc4_cockpit_exposure_v1.py | existing analytics-plane exposure contract tests, including the exact mounted path set
OHM-Trade-Agent-v1/tests/test_profit_intelligence_dashboard_v2.py | existing Profit Intelligence read-plane contract tests
OHM-Trade-Agent-v1/tests/test_opip_r5_f10_dashboard_parity_contract.py | the F10 parity contract's own RED acceptance tests
```

## 3. Classification vocabulary

Exactly five values are used for capability status and for page evidence state.
They are not interchangeable and no row may use a sixth value.

- `EXISTING_AND_VERIFIED` - the capability exists, is wired where it claims to
  be, and is covered by the existing contract tests named in `EVIDENCE`.
- `EXISTING_NEEDS_EXTENSION` - a surface exists but does not yet serve the F10
  requirement (for example a filter surface narrower than the requirement, or an
  overlapping read surface that must be reconciled rather than duplicated).
- `MISSING` - no repository surface supplies it. The row names the read model or
  API the follower increment must add.
- `BLOCKED_BY_UPSTREAM_EVIDENCE` - the capability cannot be truthfully served
  because the canonical evidence is not on the plane this read service may read.
  The row names the exact evidence gap; no later increment may synthesize the
  fact to close it.
- `OWNER_DECISION_REQUIRED` - the capability needs an owner decision (projection
  ownership, a policy value, a retirement, or an acceptance instrument) before
  any implementation, even where the implementation would be technically
  possible.

## 4. Implementation matrix

Required columns, in this order:
`REQUIREMENT_ID | ARCHITECTURE_CLAUSE | CURRENT_IMPLEMENTATION | MISSING_CAPABILITY | CANONICAL_PRODUCER | REQUIRED_READ_MODEL | API | UI | ATDD | EVIDENCE | STATUS`.

`CANONICAL_PRODUCER` names the existing authority that must supply the fact.
A row that needs a new producer is a forbidden second writer and must not be
implemented; such rows are classified `BLOCKED_BY_UPSTREAM_EVIDENCE` or
`OWNER_DECISION_REQUIRED` instead.

| REQUIREMENT_ID | ARCHITECTURE_CLAUSE | CURRENT_IMPLEMENTATION | MISSING_CAPABILITY | CANONICAL_PRODUCER | REQUIRED_READ_MODEL | API | UI | ATDD | EVIDENCE | STATUS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F10-SVC-01 | v1.5.0 §15 | Four overlapping read surfaces, none covering the eight pages | One semantic read service serving all eight pages, and the owner decision on which existing projection owns each page fact | canonical writer (unchanged) | app/opip/cockpit/semantic.py (to add) | semantic read routes (to add) | eight-page experience (to add) | PLANNED:ATDD-R5-F10-semantic-read-service/AC-001 | conformance ledger F10 row | OWNER_DECISION_REQUIRED |
| F10-SVC-02 | v1.5.0 §15 | app/opip/contracts/paper_metrics.py defines every supported metric | None; a second registry is forbidden | app.opip.contracts.paper_metrics | reuse the registry, never restate a formula | n/a | n/a | PLANNED:ATDD-R5-F10-semantic-read-service/AC-001 | tests/test_opip_paper_measurement_policy_contracts_v1.py | EXISTING_AND_VERIFIED |
| F10-SVC-03 | v1.5.0 §15 | app/api/cockpit.py filters only quote_currency and limit and fails closed on unsupported values | Typed cross-page filter allowlist, [start, end) window, environment and account scope, requested watermark | canonical replica reader | app/opip/cockpit/semantic.py + app/api/cockpit_semantic.py (to add) | semantic read routes (to add) | filter chips (to add) | PLANNED:ATDD-R5-F10-semantic-read-service/AC-002 | app/api/cockpit.py | MISSING |
| F10-SVC-04 | v1.5.0 §15 | Responses carry projection versions and a trust envelope | One semantic response envelope per metric: definition version, unit, grain, eligible population, exclusions, lineage, missingness | n/a | app/opip/cockpit/semantic.py (to add) | semantic read routes (to add) | n/a | PLANNED:ATDD-R5-F10-semantic-read-service/AC-003 | docs/architecture/bc4/BC4_COCKPIT_DATA_CONTRACT.md | MISSING |
| F10-SVC-05 | v1.5.0 §15 | A fixed MAX_TRADES bound exists; there is no cursor | Bounded limit plus stable pagination cursor and query cancellation | n/a | app/opip/cockpit/semantic.py (to add) | semantic read routes (to add) | paging (to add) | PLANNED:ATDD-R5-F10-semantic-read-service/AC-004 | app/api/cockpit.py | MISSING |
| F10-SVC-06 | BC4 §5 | app/opip/cockpit/trust.py keeps freshness, completeness, fidelity, uncertainty and correction state independent | Binding those dimensions per metric and per page rather than per response | app.opip.cockpit.trust | reuse the trust envelope | carried in the semantic response | trust presentation (to add) | PLANNED:ATDD-R5-F10-semantic-read-service/AC-005 | tests/test_bc4_cockpit_ledger_v1.py | EXISTING_NEEDS_EXTENSION |
| F10-SVC-07 | v1.5.0 §15 | No page-level counts-and-P&L reconciliation is recorded | Per-page counts-and-P&L reconciliation against one declared projection authority at one watermark | canonical replica | reuse the ledger and portfolio projections | reconciliation fields | n/a | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-010 | conformance ledger CUTOVER_GATE | MISSING |

| F10-P1-01 | v1.5.0 §15 | app/opip/data_platform/freshness.py classifies stream and maintenance status; app/opip/data_platform/read_model.py serves freshness snapshots | A System Pulse panel binding overall state and coverage to that evidence | app.opip.data_platform.freshness | reuse the freshness read model | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-020 | tests/test_opip_dashboard_freshness_v1.py | EXISTING_NEEDS_EXTENSION |
| F10-P1-02 | v1.5.0 §13 | app/services/release_profiles.py and app/services/release_runtime_verifier.py hold release identity and profile evidence on the trading host | Release-identity and profile-alignment panel | app.services.release_profiles | not published to the read plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-021 | docs/architecture/OPIP_RUNTIME_TRUTH_2026-09-28.md | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P1-03 | v1.5.0 §15 | Canonical-writer health and queue pressure are runtime facts of the trading host | Writer-health and queue-pressure panel | app.opip.canonical.writer (runtime only) | not observable from the read plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-022 | conformance ledger F10 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P1-04 | BC4 §5 | app/opip/data_platform/reconcile.py and the cockpit trust envelope expose freshness, completeness and reconciliation posture | Projection-lag and read-model-freshness panel bound per page | app.opip.data_platform.reconcile | reuse reconcile and freshness | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-023 | tests/test_bc4_cockpit_replica_rotation_v1.py | EXISTING_NEEDS_EXTENSION |
| F10-P1-05 | v1.5.0 §15 | Incident transitions are trading-host runtime facts; only derived attention codes are projected | Open-incident inventory and recent verified system events | trading-host incident records (no projection on the replica) | no incident projection exists | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-024 | conformance ledger F11 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P2-01 | v1.5.0 §15 | Screening and coverage rows are appended to the trading host file WAL only | Eligible-universe and covered-versus-missed-observation panel | app.opip.decision.store (file WAL) | not on the canonical replica | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-030 | QUALIFICATION_FUNNEL_PROGRESSION | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P2-02 | v1.5.0 §15 | IGNITION detector evaluations are not on the live canonical path | Detector-evaluation inventory including explicit no-claim | app.opip.detectors (not live) | canonical detector facts absent | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-031 | conformance ledger F10 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P2-03 | v1.5.0 §15 | Candidate, episode and disposition identities exist only on reconciled paper rows | Candidate-lifecycle funnel and episode-lineage panels | app.opip.discovery | no funnel projection on the replica | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-032 | QUALIFICATION_FUNNEL_PROGRESSION | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P2-04 | v1.5.0 §15 | Rejection and gate attribution exist only in trading-host funnel rows | Rejection, deferral and expiry reason panels | app/opip/decision/summary.py (file WAL) | no canonical rejection record | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-033 | REJECTION_REASON_AND_GATE_ATTRIBUTION | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P2-05 | v1.5.0 §15 | Discovery-to-decision latencies are computed per reconciled paper row | Discovery-to-decision and alert-latency panel across the funnel | app.opip.cockpit.ledger (partial) | funnel timing absent | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-034 | app/opip/cockpit/ledger.py | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P2-06 | v1.5.0 §15 | No canonical regime label or phase evidence is recorded | Regime, phase and horizon-classification panels | none (no canonical regime evidence exists) | none permitted | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-035 | REGIME_AND_CAPITAL_UTILIZATION_GROUPING | BLOCKED_BY_UPSTREAM_EVIDENCE |

| F10-P3-01 | SC-12 | Reconciled rows carry decision_context_id, evaluation_at, policy version and fingerprint | Per-decision frozen-snapshot, evidence-cutoff and source-watermark presentation | canonical writer | reuse ledger identity and temporal fields | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-040 | app/opip/contracts/temporal.py | EXISTING_NEEDS_EXTENSION |
| F10-P3-02 | v1.5.0 §15 | F5 feasibility, F6 forecast and F7 ranking evidence is not on the live canonical path | Feasibility, forecast and ranking sections of the decision trace | app.opip.risk, app.opip.ml and app.opip.decision (not live) | canonical decision-stage facts absent | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-041 | conformance ledger F10 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P3-03 | SC-08 | Reconciled rows expose planned stop, planned targets, max hold and reserved capital | Frozen PortfolioPlan alternatives including the cash and no-trade alternative | app.opip.contracts.portfolio | reuse the ledger planned fields | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-042 | tests/test_bc4_cockpit_ledger_v1.py | EXISTING_NEEDS_EXTENSION |
| F10-P3-04 | v1.5.0 §21 | Committee assessments are not on the live path and hold no decision authority | Optional Committee review and role-disagreement section | app.opip.committee (advisory only) | canonical Committee facts absent | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-043 | conformance ledger F10 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P3-05 | v1.5.0 §15 | app/opip/profit_intelligence/lineage.py builds per-trade lineage stages INTENT through TRACEABILITY | None for the execution lineage; the contract exists | app.opip.profit_intelligence.lineage | reuse the lineage projection | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-044 | tests/test_profit_intelligence_dashboard_v2.py | EXISTING_AND_VERIFIED |
| F10-P3-06 | v1.5.0 §15 | Actual-versus-expected attribution needs frozen expectations and matured outcomes | Expected-versus-actual attribution and learning links in the trace | learning worker | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-045 | FORWARD_OUTCOME_HORIZON_EVIDENCE | BLOCKED_BY_UPSTREAM_EVIDENCE |

| F10-P4-01 | v1.5.0 §15 | app/opip/cockpit/ledger.py groups rows by quote currency; USD and USDT stay separate | Page-level per-currency capital panel | app.opip.cockpit.ledger | reuse by_quote_currency | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-050 | app/opip/contracts/paper_outcome.py | EXISTING_AND_VERIFIED |
| F10-P4-02 | v1.5.0 §15 | app/opip/cockpit/portfolio.py publishes realized net P&L and execution costs per currency | Page binding for paper.realized_net_pnl and paper.execution_cost | app.opip.cockpit.portfolio | reuse the portfolio projection | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-051 | tests/test_bc4_cockpit_portfolio_v1.py | EXISTING_AND_VERIFIED |
| F10-P4-03 | v1.5.0 §15 | Reconciled rows expose open positions, entry and exit quantities and fills | Page-level open-position, partial-fill and unfilled-intent panel | app.opip.cockpit.ledger | reuse reconciled rows | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-052 | tests/test_bc4_cockpit_ledger_v1.py | EXISTING_NEEDS_EXTENSION |
| F10-P4-04 | v1.5.0 §15 | Equity series and drawdown are published on a realized basis only | Marked-valuation-safe equity and drawdown panel | app.opip.cockpit.portfolio | reuse drawdown and equity series | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-053 | app/opip/cockpit/portfolio.py | EXISTING_NEEDS_EXTENSION |
| F10-P4-05 | v1.5.0 §15 | Unrealized mark-to-market P&L cannot be derived | Unrealized P&L panel, which must stay withheld | none (no canonical mark evidence exists) | none permitted | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-054 | UNREALIZED_MARK_TO_MARKET_PNL | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P4-06 | v1.5.0 §15 | Reconciled rows expose remaining quantity and exit mechanisms | Rotation-leg and residual-exposure panel | app.opip.cockpit.ledger | reuse reconciled rows | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-055 | app/opip/cockpit/ledger.py | EXISTING_NEEDS_EXTENSION |
| F10-P4-07 | v1.5.0 §15 | No canonical capital-occupancy or capital-hours series is recorded | Capital-occupancy, capital-hours and efficiency diagnostics | none (no canonical occupancy evidence exists) | none permitted | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-056 | REGIME_AND_CAPITAL_UTILIZATION_GROUPING | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P5-01 | v1.5.0 §15 | The full candidate population is not published to the replica | Complete candidate-population panel retaining exclusions and no-claims | app.opip.decision.store (file WAL) | not on the canonical replica | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-060 | QUALIFICATION_FUNNEL_PROGRESSION | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P5-02 | v1.5.0 §15 | Missed and late opportunities are classified only on the learning plane | Missed, late and expired opportunity panel | app.services.opportunity_accountability | not readable on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-061 | MISSED_PROFITABLE_OPPORTUNITY | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P5-03 | v1.5.0 §15 | Forecast calibration needs forecast and maturity evidence that is not on this plane | Calibration and expected-versus-actual panel | learning worker | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-062 | FORWARD_OUTCOME_HORIZON_EVIDENCE | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P5-04 | v1.5.0 §15 | Strategy contribution and execution costs are published per currency | Page binding for trading net after costs by strategy | app.opip.cockpit.portfolio | reuse strategy contribution | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-063 | app/opip/cockpit/portfolio.py | EXISTING_NEEDS_EXTENSION |
| F10-P5-05 | v1.5.0 §15 | paper.in_position_mfe_pct and paper.in_position_mae_pct are registered but their retained path evidence is not on this plane | MFE and MAE diagnostic panel | retained market path (absent here) | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-064 | FORWARD_OUTCOME_HORIZON_EVIDENCE | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P5-06 | v1.5.0 §15 | paper.capture_efficiency is registered with an explicit counterfactual population and exclusions | Valid regret and counterfactual panel, gated on declared-policy replay | canonical actual outcome plus versioned policy replay | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-065 | MISSED_PROFITABLE_OPPORTUNITY | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P5-07 | v1.5.0 §15 | Reconciled rows carry terminal_reconciliation_state; paper.unresolved_count is registered | Pending, censored and incomplete-outcome panel | app.opip.cockpit.ledger | reuse reconciled rows | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-066 | tests/test_bc4_cockpit_ledger_v1.py | EXISTING_NEEDS_EXTENSION |

| F10-P6-01 | v1.5.0 §21 | Committee trust state is computed by the existing Committee trust calculation | Page binding for the current trust stage and blockers | app.opip.committee (advisory only) | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-070 | conformance ledger F10 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P6-02 | v1.5.0 §21 | Prospective paired comparison evidence is matured on the learning plane | Four-way correctness comparison panel | learning worker | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-071 | docs/architecture/OPIP_F6_PROSPECTIVE_EVIDENCE_CONTRACT.md | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P6-03 | v1.5.0 §21 | No matched-policy trading-net comparison is readable here | Incremental trading-net panel that must withhold rather than infer | learning worker | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-072 | docs/architecture/OPIP_F6_PROSPECTIVE_EVIDENCE_CONTRACT.md | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P6-04 | v1.5.0 §21 | No AI provider, compute, token or billing evidence is on this plane | Attributable AI cost panel, including unknown-cost withholding | none (no canonical AI cost evidence exists) | none permitted | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-073 | app/opip/contracts/paper_metrics.py | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P6-05 | v1.5.0 §21 | Role, model and provider versions live with Committee runtime evidence | Timeliness, retry and version panel | app.opip.committee (advisory only) | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-074 | conformance ledger F10 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P7-01 | v1.5.0 §15 | Frozen expected outcomes live with the expectation-freeze evidence | Expected-outcome section of the learning page | app.opip.decision_intelligence (freeze evidence) | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-080 | FORWARD_OUTCOME_HORIZON_EVIDENCE | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P7-02 | v1.5.0 §15 | Durable weakness findings are produced on the learning plane | Weakness registry, severity and recurrence panel | learning worker | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-081 | conformance ledger F9 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P7-03 | v1.5.0 §15 | Hypothesis, experiment and champion/challenger evidence is not on this plane | Experiment and champion/challenger section | learning worker | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-082 | conformance ledger F9 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P7-04 | v1.5.0 §13 | Release SHA, profile and compatibility are trading-host release facts | Release identity, compatibility and adoption section | app.services.release_profiles | not published to the read plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-083 | docs/architecture/OPIP_RUNTIME_TRUTH_2026-09-28.md | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P7-05 | v1.5.0 §15 | Per-currency economics and supported cost components are published | Before-and-after economic impact section, which must stay attribution-free until expectations mature | app.opip.cockpit.portfolio | reuse the portfolio projection | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-084 | app/opip/profit_intelligence/economics.py | EXISTING_NEEDS_EXTENSION |
| F10-P7-06 | v1.5.0 §15 | No F10 page exists, so no promotion control is exposed; the read plane holds no promotion authority | A page-level invariant and tests proving the page cannot promote, release or activate anything | n/a | n/a | n/a | n/a | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-085 | conformance ledger F10 row | MISSING |

| F10-P8-01 | v1.5.0 §15 | Incident transitions are trading-host runtime facts, not replica evidence | Incident inventory, severity and lifecycle panel | trading-host incident records | no incident projection exists | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-090 | conformance ledger F11 row | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P8-02 | v1.5.0 §15 | app/opip/data_platform/freshness.py classifies each stream and maintenance window | Feed-coverage-gap and missing-interval panel | app.opip.data_platform.freshness | reuse the freshness read model | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-091 | app/opip/data_platform/freshness.py | EXISTING_NEEDS_EXTENSION |
| F10-P8-03 | BC4 §5 | app/opip/data_platform/reconcile.py and the cockpit attention codes expose reconciliation posture | Projection-failure and reconciliation-mismatch panel | app.opip.data_platform.reconcile | reuse reconcile and attention | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-092 | tests/test_bc4_cockpit_portfolio_v1.py | EXISTING_NEEDS_EXTENSION |
| F10-P8-04 | v1.5.0 §13 | Release identity mismatch is a trading-host release fact | Release-identity-mismatch panel that must expose rather than join incompatible identities | app.services.release_runtime_verifier | not published to the read plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-093 | docs/architecture/OPIP_RUNTIME_TRUTH_2026-09-28.md | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P8-05 | v1.5.0 §13 | F11 protection runs independently of the read plane and Telegram | F11 protection-state and degradation panel, which must never gate protection | app.opip.risk (F11, independent) | not on this plane | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-094 | .github/workflows/pytest.yml release architecture gate | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-P8-06 | v1.5.0 §15 | app/services/telegram_delivery.py records delivery attempts and failure states | Telegram delivery-failure and retry panel | app.services.telegram_delivery | reuse the delivery ledger | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-095 | tests/test_telegram_delivery_closure_v2.py | EXISTING_NEEDS_EXTENSION |
| F10-P8-07 | v1.5.0 §15 | Host CPU, memory, storage and WAL indicators are not on the read plane, and analytics must not move to the trading host | Verified host-resource panel, or an explicit withheld state | none (no host metrics on this plane) | none permitted | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-096 | deploy/analytics/README.md | BLOCKED_BY_UPSTREAM_EVIDENCE |
| F10-FLT-01 | v1.5.0 §15 | No typed cross-page filter contract exists; the cockpit filters only currency and limit | Typed filter allowlist with AND across dimensions, OR within a dimension and [start, end) windows | n/a | app/opip/cockpit/semantic.py (to add) | semantic read routes (to add) | filter controls (to add) | PLANNED:ATDD-R5-F10-semantic-read-service/AC-002 | app/api/cockpit.py | MISSING |
| F10-FLT-02 | v1.5.0 §15 | No cross-filtering or drill-down contract exists | Context-preserving cross-filter, drill-down and safe deep links | n/a | to add | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-100 | app/api/cockpit.html | MISSING |
| F10-FLT-03 | v1.5.0 §15 | No saved-view mechanism exists, and none may be added outside an approved non-trading preference boundary | Saved views with explicit migration failure and no domain-state mutation | n/a | to add | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-101 | SAVED_VIEW_STORE | MISSING |
| F10-FLT-04 | v1.5.0 §15 | No analysis presets exist | Evidence-backed presets that report insufficiency rather than assert a result | n/a | to add | to add | to add | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-102 | app/api/cockpit.html | MISSING |
| F10-FLT-05 | v1.5.0 §15 | No accessibility or viewport evidence exists for an eight-page experience | Accessibility checks and inspectable operator-usefulness evidence | n/a | to add | to add | to add | PLANNED:ATDD-R5-F10-G/AC-110 | docs/atdd/evidence | MISSING |

| F10-TG-01 | v1.5.0 §15 | app/services/alert_v2_format.py renders decision-first alert text; app/services/alert_taxonomy.py separates the notification planes | Proof that every actionable label is bound to a current committed eligible canonical decision | app.opip.decision (canonical decisions) | reuse alert rendering and the decision identity | reuse the delivery path | n/a | PLANNED:ATDD-R5-F10-F/AC-120 | tests/test_alert_v2_presentation_taxonomy_v1.py | EXISTING_NEEDS_EXTENSION |
| F10-TG-02 | v1.5.0 §15 | Entry-card fields exist in the renderer, including prices and sizing context | Fail-closed actionability: no guessed price, no uncalibrated score presented as calibrated confidence | canonical decision plus committed price evidence | reuse the renderer | reuse the delivery path | n/a | PLANNED:ATDD-R5-F10-F/AC-121 | app/services/alert_v2_format.py | EXISTING_NEEDS_EXTENSION |
| F10-TG-03 | v1.5.0 §15 | app/services/notification_policy.py revalidates cooldown, budget and reservation before emission | None; the single eligibility authority exists | app.services.notification_policy | reuse authorize and should_emit paths | reuse the delivery path | n/a | PLANNED:ATDD-R5-F10-F/AC-122 | tests/test_opip_r4_alert_notification_quality.py | EXISTING_AND_VERIFIED |
| F10-TG-04 | v1.5.0 §15 | Notification policy state plus the Telegram delivery ledger carry emission identity | None for the existing states; a second dedupe authority is forbidden | app.services.notification_policy | reuse policy state and the delivery ledger | reuse the delivery path | n/a | PLANNED:ATDD-R5-F10-F/AC-123 | app/services/notification_policy.py | EXISTING_AND_VERIFIED |
| F10-TG-05 | v1.5.0 §15 | Delivery states DELIVERED, EDITED, SEND_FAILED and related outcomes are recorded | Callback and command audit fields within the existing boundaries, or a flagged limitation | app.services.telegram_delivery | reuse the delivery ledger | reuse the delivery path | n/a | PLANNED:ATDD-R5-F10-F/AC-124 | tests/test_telegram_delivery_closure_v2.py | EXISTING_NEEDS_EXTENSION |
| F10-TG-06 | v1.5.0 §13 | Protection does not depend on Telegram and the release architecture gate asserts PROTECTION=INDEPENDENT | Proof that a Telegram outage produces only delivery/incident facts | app.opip.risk (F11, independent) | reuse existing protection evidence | n/a | n/a | PLANNED:ATDD-R5-F10-F/AC-125 | app/services/telegram_delivery.py | EXISTING_NEEDS_EXTENSION |
| F10-TG-07 | v1.5.0 §15 | Notification volume, cooldown and budget limits are owner-set policy values | Any change to a notification threshold | app.services.notification_policy | none | none | none | PLANNED:ATDD-R5-F10-F/AC-126 | app/services/notification_policy.py | OWNER_DECISION_REQUIRED |
| F10-SEC-01 | BC4 §10 | app/api/cockpit.py fails closed with 401 when its read-only secret is unset or wrong | None for the existing routes; the same model must cover the semantic routes | app.services.secret_auth | reuse the auth model | reuse the auth model | n/a | PLANNED:ATDD-R5-F10-semantic-read-service/AC-006 | tests/test_bc4_cockpit_exposure_v1.py | EXISTING_AND_VERIFIED |
| F10-SEC-02 | BC4 §10 | app/api/cockpit_service.py mounts only the read-only cockpit router and the container is hardened | Isolation must be re-proved for any widened read surface | app.api.cockpit_service | reuse the analytics-plane entry point | reuse the analytics-plane entry point | n/a | PLANNED:ATDD-R5-F10-semantic-read-service/AC-006 | tests/test_bc4_cockpit_exposure_v1.py | EXISTING_AND_VERIFIED |
| F10-SEC-03 | v1.5.0 §15 | A fixed MAX_TRADES bound exists; there is no cancellation or cache-key contract | Bounded semantic queries, cancellation and watermark-aware cache keys | n/a | app/opip/cockpit/semantic.py (to add) | semantic read routes (to add) | n/a | PLANNED:ATDD-R5-F10-semantic-read-service/AC-004 | app/api/cockpit.py | EXISTING_NEEDS_EXTENSION |
| F10-SEC-04 | v1.5.0 §15 | Existing surfaces expose no order, mode or policy control | A page-level invariant and tests proving UI absence cannot impair detection, risk or protection | n/a | n/a | n/a | n/a | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-105 | app/api/cockpit.html | EXISTING_NEEDS_EXTENSION |
| F10-SEC-05 | BC4 §10 | No secret, credential or token appears in the cockpit page, the analytics env or the responses | None; the boundary is tested | deploy/analytics/bootstrap-opip-data-platform.sh | reuse the filtered-environment contract | reuse the filtered-environment contract | n/a | PLANNED:ATDD-R5-F10-semantic-read-service/AC-006 | tests/test_bc4_cockpit_exposure_v1.py | EXISTING_AND_VERIFIED |

## 5. Logical fact families and required grains

The grains below are the adopted ones. A read model may only join facts at these
grains, and each aggregate must reconcile to its drill-down population at one
watermark.

| FACTS | REQUIRED_GRAIN | AVAILABILITY_ON_THIS_PLANE |
| --- | --- | --- |
| Candidate lifecycle events | one candidate lifecycle event | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Detector evaluations | one instrument evaluation including no-claim | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Decisions | one frozen baseline decision | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Committee assessments | one assessment and its bounded role attempts | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Paper trades | one position lifecycle | EXISTING_AND_VERIFIED |
| Paper fills | one execution fill | EXISTING_AND_VERIFIED |
| Outcomes | one entity, policy, horizon and revision | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Learning events | one experiment or learning transition | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Incidents | one incident transition | BLOCKED_BY_UPSTREAM_EVIDENCE |
| AI usage | one provider invocation attempt | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Portfolio marks | one declared valuation observation | BLOCKED_BY_UPSTREAM_EVIDENCE |
| Attribution | one registered comparison and evaluation window | BLOCKED_BY_UPSTREAM_EVIDENCE |

## 6. Semantic response contract

Every supported metric response must carry: metric identifier and semantic
version; unit and currency; grain and formula reference; eligible population and
exclusions; numerator and denominator where applicable; the effective typed
filters; observation and as-of timestamps; source and projection watermarks;
environment and account; completeness, freshness and reconciliation states; the
missingness or withholding reason; source lineage and evidence references; and a
pagination cursor where the population is paged. The formula reference names the
registry entry; the formula is never restated in a read model.

## 7. Page identity and operator questions

`PAGE_ID` values are the frozen inventory of
`docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md`. Adding, removing, renaming,
splitting or merging a page needs its own increment. `EVIDENCE_STATE` uses the
classification vocabulary of section 3.

| PAGE_ID | TITLE | OPERATOR_QUESTION | PRIMARY_FACTS | EVIDENCE_STATE |
| --- | --- | --- | --- | --- |
| P1_SYSTEM_PULSE | System Pulse | Can I trust the system right now | freshness and stream status, maintenance state, reconciliation posture, attention codes | EXISTING_NEEDS_EXTENSION |
| P2_DISCOVERY_REGIME | Discovery and Regime | What did O'Pip observe and what opportunities developed | screening coverage, detector evaluations, candidate funnel and dispositions | BLOCKED_BY_UPSTREAM_EVIDENCE |
| P3_DECISION_TRACE | Decision Trace | What did O'Pip know when it decided | decision context identity, temporal fields, planned economics, execution lineage | BLOCKED_BY_UPSTREAM_EVIDENCE |
| P4_PAPER_PORTFOLIO | Paper Portfolio | What is the isolated paper account's economics and capital usage | per-currency capital, realized net P&L, execution costs, open positions, equity series | EXISTING_NEEDS_EXTENSION |
| P5_QUALITY_MISSED_OPPORTUNITY | Quality and Missed Opportunity | Does O'Pip find useful opportunities in time | candidate population, missed and late opportunities, calibration, MFE and MAE, counterfactuals | BLOCKED_BY_UPSTREAM_EVIDENCE |
| P6_COMMITTEE_VALUE_AI_COST | Committee Value and AI Cost | Did advisory AI create prospective value after cost | Committee trust stage, paired comparisons, incremental net, AI cost | BLOCKED_BY_UPSTREAM_EVIDENCE |
| P7_LEARNING_RELEASES | Learning and Releases | Is O'Pip improving on evidence | frozen expectations, matured outcomes, weakness findings, experiments, release adoption | BLOCKED_BY_UPSTREAM_EVIDENCE |
| P8_INCIDENTS_INFRASTRUCTURE | Incidents and Infrastructure | What failed, what is affected and what recovery completed | incident lifecycle, coverage gaps, reconciliation failures, delivery failures, recovery state | BLOCKED_BY_UPSTREAM_EVIDENCE |

## 8. Panel map

`PANEL_ID | OPERATOR_QUESTION | METRIC_OR_FACT | PRODUCER | FILTERS | TRUST_RULE | DRILL_TARGET | ACCEPTANCE_TEST`.

`METRIC_OR_FACT` names either a registered metric id from
`paper-metrics-v1` or a declared fact; `ACCEPTANCE_TEST` names the planned
criterion of the follower increment that will prove the panel, because no panel
exists yet. A panel whose producer is blocked is not a panel the follower may
approximate.

| PANEL_ID | OPERATOR_QUESTION | METRIC_OR_FACT | PRODUCER | FILTERS | TRUST_RULE | DRILL_TARGET | ACCEPTANCE_TEST |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P1-PNL-01 | Is the system trustworthy now | fact: system state, release identity and profile alignment | app.services.release_profiles | environment, account | no aggregate green status while mandatory evidence is missing or contradictory | release and profile evidence | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-020 |
| P1-PNL-02 | Are observations trustworthy | fact: observation coverage and feed freshness | app.opip.data_platform.freshness | source, venue, instrument, time window | stale evidence is never presented as current | missing intervals and source history | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-021 |
| P1-PNL-03 | Is the read path healthy | fact: projection lag, reconciliation state and read-model freshness | app.opip.data_platform.reconcile | environment, time window | an unevaluated trust dimension is not healthy | reconciliation detail | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-023 |
| P1-PNL-04 | Is protection operational | fact: F11 protection state and open incidents | app.opip.risk (F11, independent) | severity, state | protection never depends on this panel or Telegram | incident and source history | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-024 |
| P2-PNL-01 | What was observed | fact: eligible universe and coverage against eligibility | app.opip.decision.store (file WAL) | instrument, venue, time window | exclusions and no-claims are retained, never dropped | instrument evaluation | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-030 |
| P2-PNL-02 | What did the detectors evaluate | fact: detector evaluations including explicit no-claim | app.opip.detectors (not live) | detector phase, instrument, time window | an absent evaluation is not a negative claim | evaluation detail | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-031 |
| P2-PNL-03 | How did candidates progress | fact: candidate lifecycle funnel and episode lineage | app.opip.discovery | status, horizon class, time window | a candidate is never presented as an authorized trade | candidate and episode | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-032 |
| P2-PNL-04 | Why was a candidate rejected | fact: rejection, deferral and expiry reasons with latency | app/opip/decision/summary.py (file WAL) | reason, disposition, horizon class, time window | an unattributable reason is withheld rather than invented | rejection record | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-033 |
| P3-PNL-01 | What did O'Pip know | fact: decision identity, evaluation time, evidence cutoff and watermark | canonical writer | candidate, episode, policy version, time window | later corrections never modify the original explanation | frozen snapshot | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-040 |
| P3-PNL-02 | Why did it pass the gates | fact: F5 feasibility, F6 forecast and F7 ranking evidence | app.opip.risk, app.opip.ml and app.opip.decision (not live) | policy version, horizon class | deterministic fact, statistical estimate and AI opinion stay distinct | stage evidence | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-041 |
| P3-PNL-03 | What were the alternatives | fact: planned economics, capital plan and the cash alternative | app.opip.contracts.portfolio | policy version, horizon class | a plan is not an outcome | portfolio plan record | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-042 |
| P3-PNL-04 | What happened afterwards | fact: fills, protections, exits and outcome lineage | app.opip.profit_intelligence.lineage | economic result, execution result, exit mechanism | a missing Committee opinion never implies a favorable review | execution lineage | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-044 |

| P4-PNL-01 | How much capital is committed | fact: cash, reserved capital, deployed basis and available capital per currency | app.opip.cockpit.portfolio | quote currency, account, time window | USD and USDT are never summed or joined | paper trade | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-050 |
| P4-PNL-02 | What did the paper account earn after costs | paper.realized_net_pnl, paper.execution_cost | app.opip.cockpit.portfolio | quote currency, policy version, outcome status, time window | a missing cost is never a zero cost, and an open mark is never realized | paper trade and fills | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-051 |
| P4-PNL-03 | What positions exist and how were they filled | fact: open positions, fills, partial fills, cancellations and unfilled intents | app.opip.cockpit.ledger | instrument, lifecycle status, time window | a no-fill is not a losing trade | fills and order intents | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-052 |
| P4-PNL-04 | How did equity and drawdown move | paper.realized_equity_drawdown_pct | app.opip.cockpit.portfolio | quote currency, time window | no marked-equity drawdown without a defensible executable mark | equity series | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-053 |
| P4-PNL-05 | Did planned rotations complete | fact: remaining quantity, exit mechanism and residual exposure | app.opip.cockpit.ledger | instrument, lifecycle status, exit mechanism | a planned rotation is not a completed rotation | exit and residual evidence | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-055 |
| P5-PNL-01 | What opportunity population existed | fact: candidate population with exclusions and no-claims | app.opip.decision.store (file WAL) | instrument, status, time window | the eligible and excluded cohorts are both retained | candidate | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-060 |
| P5-PNL-02 | What did O'Pip miss or find late | fact: missed, late and expired opportunities | app.services.opportunity_accountability | status, horizon class, time window | no best-hindsight price is presented as realizable profit | accountability record | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-061 |
| P5-PNL-03 | Is the forecast calibrated | fact: calibration, reliability and expected-versus-actual outcomes | learning worker | model version, horizon class, time window | insufficient support is not a favorable conclusion | frozen expectation | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-062 |
| P5-PNL-04 | Does trading net survive costs by strategy | paper.realized_net_pnl, paper.net_expectancy, paper.in_position_mfe_pct, paper.in_position_mae_pct | app.opip.cockpit.portfolio | strategy, policy version, regime, horizon class | a censored or incomplete outcome is shown as incomplete | strategy contribution and trade | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-063 |
| P5-PNL-05 | Was a better alternative available | paper.capture_efficiency | canonical actual outcome plus versioned policy replay | policy version, time window | a counterfactual is never realized profit, and an ordinal score is not a probability | comparison record | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-065 |
| P6-PNL-01 | What has the Committee contributed | fact: trust stage, measured cases and exclusions | app.opip.committee (advisory only) | stage, role, model, provider, time window | trust state comes from the existing calculation, never from this page | assessment record | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-070 |
| P6-PNL-02 | Did the Committee help or harm | fact: four-way correctness and false interventions | learning worker | role, model, policy version, time window | an AI opinion is not a statistical forecast | paired comparison | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-071 |
| P6-PNL-03 | What is the incremental net after AI cost | fact: baseline net, matched-policy net, incremental net and attributable AI cost | learning worker | policy version, model, provider, time window | unknown cost blocks any favorable economics claim | matched comparison | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-072 |
| P6-PNL-04 | Where is the spend going | fact: tokens, retries, latency and billed or estimated cost by provider and model | none (no canonical AI cost evidence exists) | model, provider, role, time window | estimated and billed cost are never conflated | invocation attempt | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-073 |

| P7-PNL-01 | Did the frozen expectation hold | fact: frozen expected outcomes against matured actual outcomes | learning worker | policy version, horizon class, time window | an expectation is never rewritten after the fact | frozen expectation | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-080 |
| P7-PNL-02 | What weaknesses are durable | fact: weakness findings with severity, category and recurrence | learning worker | category, severity, time window | a hypothesis is not a validated improvement | weakness finding | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-081 |
| P7-PNL-03 | What did experiments prove | fact: experiments, champion and challenger evaluations with approval identity | learning worker | status, cohort, time window | the page never exposes promotion or release controls | experiment record | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-082 |
| P7-PNL-04 | Did an approved change actually help | fact: release identity, compatibility, adoption and observed before-and-after economics | app.services.release_profiles | release, policy version, time window | an approved artifact is not an adopted artifact, and a learning change has no trading authority | release evidence | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-084 |
| P8-PNL-01 | What failed and when | fact: incident inventory, severity, lifecycle and affected systems | trading-host incident records | severity, state, system, time window | an unproven incident claim is not asserted | incident record | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-090 |
| P8-PNL-02 | Which evidence is affected | fact: coverage gaps, freshness state and reconciliation failures | app.opip.data_platform.reconcile | component, state, time window | a failed projection never makes stale evidence look current | affected evidence | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-091 |
| P8-PNL-03 | Is anything untrusted or degraded | fact: release identity mismatch, protection degradation and incomplete outcome labels | app.services.release_runtime_verifier | release, system, state | incompatible release identities are exposed, never silently joined | release and protection evidence | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-093 |
| P8-PNL-04 | What recovery completed | fact: delivery failures, retries, backup and restore state | app.services.telegram_delivery | component, state, time window | operational recovery is not authorization to resume trading | recovery evidence | PLANNED:ATDD-R5-F10-dashboard-parity-implementation/AC-095 |

## 9. Declared evidence gaps (never rendered favorable)

These are the registered gaps. A page panel whose fact is one of them must render
an explicit unavailable or insufficient-evidence state. The names are exactly the
registered gap facts, so a census that invents a gap fails closed.

```text
GAP: QUALIFICATION_FUNNEL_PROGRESSION | screening and funnel gate history is appended to the trading host file WAL only | TRADING_HOST_FILE_WAL | not published to the canonical replica
GAP: REJECTION_REASON_AND_GATE_ATTRIBUTION | per-gate decisions and terminal rejection reasons exist only in trading-host funnel rows | TRADING_HOST_FILE_WAL | no canonical rejection record exists
GAP: MISSED_PROFITABLE_OPPORTUNITY | counterfactual and missed-winner classification is built on the learning plane | LEARNING_PLANE | the accountability ledger is not readable on this plane
GAP: FORWARD_OUTCOME_HORIZON_EVIDENCE | forward outcome windows mature on the learning worker | LEARNING_PLANE | decision-time and future evidence must not be mixed
GAP: UNREALIZED_MARK_TO_MARKET_PNL | no canonical mark evidence is bound to open paper positions | NONE | an unrealized figure would require an assumed price
GAP: REGIME_AND_CAPITAL_UTILIZATION_GROUPING | no canonical regime label or capital-occupancy series is recorded | NONE | grouping by regime or exposure would be a presentation-layer invention
```

## 10. Declared absences

Each line is a capability this census asserts does **not** exist in the repository
at the recorded base commit, with the probe that proves it. `ABSENT` resolves a
repository-root path and asserts it does not exist; `ABSENT_SYMBOL` scans the
application package and asserts the symbol appears nowhere. An increment that
supplies one of these capabilities must also re-record the corresponding line,
which is exactly the fail-closed behaviour intended: the census cannot silently
go stale.

```text
ABSENT | OHM-Trade-Agent-v1/app/opip/cockpit/saved_views.py | no saved-view persistence module exists
ABSENT | OHM-Trade-Agent-v1/app/opip/cockpit/decision_trace.py | no decision-trace read model exists on this plane
ABSENT | OHM-Trade-Agent-v1/app/opip/cockpit/incidents.py | no incident read model exists on this plane
ABSENT | OHM-Trade-Agent-v1/app/opip/cockpit/committee_value.py | no Committee-value read model exists on this plane
ABSENT | OHM-Trade-Agent-v1/app/opip/cockpit/ai_cost.py | no AI-cost read model exists on this plane
ABSENT_SYMBOL | SAVED_VIEW_STORE | no saved-view store exists anywhere in the application package
ABSENT_SYMBOL | CROSS_CURRENCY_TOTAL | no cross-currency total exists anywhere in the application package
```

## 11. Integrity rules (fail-closed)

These rules are binding on every later increment. They are the census's reading
of the adopted evidence clause and the B/C-4 invariants, not a new policy.

- `UNKNOWN` is not zero, and a missing cost is not a zero cost.
- No evidence is not a favorable result, and an absent incident set is not a
  healthy month.
- Unobserved exits are not completed wins, and a no-fill is not a losing trade.
- An AI opinion is not a statistical forecast, and an ordinal score is not a
  probability.
- An open-position mark is not a realized gain, and a plan is not an outcome.
- USD and USDT portfolios remain distinct and are never summed or joined.
- Aggregate counts must reconcile with their drill-down population at one
  watermark; a ratio must derive from valid numerator and denominator.
- Stale data is never presented as current, and incompatible release identities
  are exposed rather than silently joined.
- A projection failure cannot make stale evidence appear current, and an
  unevaluated trust dimension is not healthy.
- No recommendation, on any surface, may override deterministic risk rejection or
  an execution-safety gate.

## 12. Threshold policy

```text
THRESHOLD_POLICY: OWNER_DECISION_REQUIRED
RATIFIED_THRESHOLDS: NONE
```

This census ratifies no gate threshold, freshness bound, latency bound, tolerance,
capacity limit, sample size, cost bound or notification volume. A resource guard
that already exists in code (for example the existing request row bound in
`app/api/cockpit.py`) is an implementation guard, not a ratified measurement, and
this census does not change it.

## 13. Owner-gated open items

- Which existing canonical projection authority owns each page fact, and whether a
  page needs an additional projection: `OWNER_DECISION_REQUIRED`. The parity
  specification records it as `UNDECIDED`; this census repeats that and decides
  nothing.
- `OQ-10` operator usefulness questionnaire and legacy cockpit retention:
  retention is decided, usefulness is not measured.
- `OQ-07` gate thresholds, `OQ-06` sleeve caps and `OQ-08` legacy disposition:
  open, owner only.
- `G4` operator usefulness and trace acceptance: `INSUFFICIENT_EVIDENCE`.
- Any widening of the externally reachable analytics read surface: the existing
  exposure contract asserts an exact mounted path set, so a widened surface is a
  deliberate, owner-visible change and not a side effect of this census.
- Any notification threshold, paper-authority, release-profile, committee-timer or
  production change: `NOT REQUESTED` and not authorized here.

## 14. Follower increments

The identifiers below are the increments this census prepares. Recording them
authorizes none of them; each needs its own contract, its own focused evidence and
its own acceptance tests. The parity specification's own follower identifier is
adopted unchanged and remains `PROPOSED_NOT_AUTHORIZED` until the owner approves
it.

```text
FOLLOWER: ATDD-R5-F10-semantic-read-service | F10-A | one versioned semantic read service over the existing projections plus its read routes and tests
FOLLOWER: ATDD-R5-F10-dashboard-parity-implementation | F10-B..F10-E | page implementation, interaction contract and per-page parity proof, subject to the parity specification's own obligations
FOLLOWER: ATDD-R5-F10-F | F10-F | decision-first Telegram parity, delivery lifecycle and audit tests
FOLLOWER: ATDD-R5-F10-G | F10-G | integrated certification, accessibility checks and operator acceptance materials
```

## 15. How this census is validated, and its completion state

Structure, source paths, status vocabulary, page inventory, panel map, declared
gaps, declared absences, the unratified threshold policy and the preserved parity
state are asserted by `tests/test_opip_r5_f10_source_census.py`, whose tests map
to `AC-001`-`AC-006` of
`docs/atdd/scope-contracts/ATDD-R5-F10-source-census.md`.

```text
ARCH_COVERAGE: CENSUS_RECORDED (F10-0 only)
CODE_COMPLETE: FALSE
TARGET_WIRED: FALSE
RUNTIME_ACTIVE: FALSE
PROD_PROVEN: FALSE
USER_VALUE_PROVEN: FALSE
PARITY_STATUS: NOT_VERIFIED
PARITY_VERIFIED_PAGES: 0 of 8
```

This census claims no production root cause, no parity, and no trading,
deployment, promotion or release authority.
