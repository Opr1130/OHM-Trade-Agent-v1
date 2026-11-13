# O’Pip V6 — Operations Center UX & Information Architecture

**Revision:** Executive prototype v3 · 13 November 2026 (legacy dashboard capability parity annex)  
**Source:** User-supplied four-page Operations Center wireframes, Data Governance HTML, and O’Pip repository read-only inventory.  
**Status:** Design specification; **not production evidence or a deployment request**. The accompanying interactive HTML and PDF visual atlas contain **synthetic sample records** only.

## 1. Executive decision

Build **one cohesive Operations Center**, not a new duplicate dashboard. Preserve the visual DNA of the original: emerald navigation rail, light neutral content surfaces, gold caution accents, compact charts and recognizable O’Pip branding. Expand its information architecture from four shown wireframe pages to 12 distinct functional pages, with progressive disclosure and explicit control ownership.

For a layperson, every high-value screen must answer three questions above the fold: **What happened? Why does it matter? What can I safely do next?** The highest-priority decision is whether O’Pip generates actual measurable net profitability from high-quality actionable advisory signals while remaining independently protected.

### Information principles

1. **Profit first, but distinguish evidence classes.** Real broker P&L, manually reported P&L, paper P&L, modeled backtests and signal hits are never combined. A signal reaching TP2 is not a realized trade until broker fills are reconciled.
2. **One metric, one definition, one owner and one denominator.** All cards expose period, source, maturity, cost assumption, sample size and freshness where relevant.
3. **Explain exceptions before celebrating averages.** If scanner, F11, data shipping or model telemetry is missing or stale, show **UNVERIFIED / STALE / UNAVAILABLE**; never “All systems OK.”
4. **All control changes are draft-first.** A settings page is not an alternative deployment mechanism; activation, retention deletion, learning or trading authority remain governed separately.
5. **Operator controls should reduce message noise, not hide risk.** F11 incidents and material existing-position protection cannot be suppressed by optional Telegram categories, quiet hours or daily caps.
6. **Evidence is inspectable and exportable.** Filter any sample table by period, optionally source/strategy/outcome, and export selected columns with timestamps, provenance and policy version.
7. **Design for quick reading.** Headline → 3–4 decisive metrics → trends → exceptions → records → definitions/next action. Nontechnical explanations beside specialist terms.

## 2. Revised navigation — 12 unique screens

| Group | Page | Top-level user question | Key module / action |
|---|---|---|---|
| Trading Insights | **1 Overview** | Are we safe, active and profitable? | Net paper P&L visibly separate from actual broker P&L, qualification, delivery and exception list |
| Trading Insights | **2 Signals & alerts** | Did a truly actionable signal appear and why? | Source, symbol, strategy, phase, gates, TTL, score, mature outcome, reason codes |
| Trading Insights | **3 Profit scorecard** | Are the trading results improving **after costs**? | Actual net realized P&L (when verified), net paper P&L, sample sizes, expectancy, profit factor, drawdown |
| Trading Insights | **4 Market events** | Which events are relevant, and when? | Upcoming event time in explicit zone, impact, pre/post windows, evidence provider and confirmation |
| Trading Insights | **5 Paper trading** | Are simulated results consistent with the alert plan? | Closed/open positions, fees, net P&L, entry/exit lineage; funded trading disabled |
| Operations Center | **6 Health & funnel** | Where did an event or candidate stop? | Scanner → qualification → risk → Chief → notification; cgroup, skip-rate and independent F11 |
| Operations Center | **7 AI & models** | Is AI adding value for its cost? | Stage 1/2 routing, model/version proof, provider cost and errors, committee and learning OFF |
| Operations Center | **8 Telegram center** | Which messages will I receive, at what time and why? | Category subscriptions, quiet hours, timezone, digest, delivery/suppression ledger and message preview |
| Operations Center | **9 Configuration** | What can I change safely? | Scoring/economics/scan/AI/platform registry, draft differences, validation, approval and rollback |
| Operations Center | **10 Data governance** | Where are records and can storage be reduced safely? | Hot → history → archive, copy receipts, retention floors, dry-run, never timer-only deletion |
| Operations Center | **11 Learning lab** | Is there trustworthy evidence for promotion? | Paused state, coverage and label maturity, leakage safeguards, out-of-sample comparison |
| Operations Center | **12 Audit & custom export** | Can I retrieve and explain the exact evidence? | Time-filtered, field-selected CSV/JSON, source IDs, UTC times, audit and export manifest |

**Consolidation:** Don't implement a second `/cockpit` or a parallel admin/config system. Reconcile legacy `/dashboard`, `/cockpit`, and `control-center-v2` with one authoritative read-model/navigation and preserve safe rollout/rollback.

## 3. Metric dictionary — definitions and mistakes to prevent

| Metric | Definition / denominator | Mandatory context and source | Display rule |
|---|---|---|---|
| **Actual realized net P&L** | Reconciled realized broker exit proceeds minus entry cost, trading fees and attributable execution costs. | Broker trade/fill IDs, paired entry/exit, fees, timestamp and audit owner. | **UNAVAILABLE** until external fills reconcile. Never infer from alerts. |
| Net **paper** P&L | Sum of closed simulated trade P&L minus paper-estimated fees/impact. | Paper trade identity, strategy version, open/closed state and frozen cost policy. | Label **PAPER** prominently and exclude unrealized. |
| Signal hit rate | Mature eligible signal episodes that touch frozen target before frozen stop inside horizon **÷ matured eligible episodes**. | Plan version, target/stop, horizon, same-bar ambiguity handling, sample count, censored count. | Never count unresolved/pending as misses or hits. |
| Paper win rate | Closed positive **net** paper trades ÷ total closed paper trades. | Fill-based paper ledger; fee policy. | Separate from hit rate; include trade count. |
| Net expectancy | Mean after-cost net R across an eligible matched cohort; R = signed net result ÷ frozen initial risk. | Sample size, drawdown, dispersion, direction, size and cohort period. | No fabricated “confidence” percentages. |
| Net profit factor | Positive realized net P&L total ÷ absolute negative realized P&L total. | Same period/cohort, excluding paper unless page explicitly says paper. | Undefined without losses; show `—`, not infinity as a win claim. |
| Max drawdown | Largest peak-to-trough decline of cumulative net P&L in the cohort. | Period, valuation method and starting capital. | Must be consistent across versions. |
| Alert actionability | Alerts passing all deterministic safety, quality, market-depth, cost and current-evidence checks ÷ evaluated candidate episodes. | Reason-coded funnel, stable candidate identity. | AI cannot rescue rejected candidates. |
| Notification delivery rate | Telegram API-confirmed sends ÷ eligible send attempts. | Confirmed response ID, retries, timestamp and channel. | Suppressed are separate counts and explanations. |
| Alert suppression | Number and reasons skipped at policy boundary. | `QUIET_HOURS`, `STRICT_GATE`, `DUPLICATE`, `CAP`, `EXPIRED`, `STALE`, `SAFETY_BLOCKED`. | Never silently omit evidence. |
| AI cost per qualified signal | Sum **all billable** AI attempts (including errors/retries) ÷ qualified signal count; display absolute spend too. | Router request ID, model, provider receipt, token and cost. | Never suppress cost of unsuccessful calls. |
| Scan skip rate | Skipped scheduled/eligible attempts by reason ÷ due attempts; distinguish disabled/suspended/non-due. | Scheduler attempt ID, worker trace, resource checks, timeout. | If attempt denominator unknown → unavailable. |
| Data freshness | Current reference UTC time − last **source-observed-at**, separately for event time and ingestion time. | Source SLA and timestamp; never invent quote time. | Fresh/STALE/UNAVAILABLE determined by contract. |
| Protection status | Latest independent F11 proof with explicit check age and pass/fail result. | Independent F11 source, not dashboard/core assumptions. | Missing proof → **UNVERIFIED**, never green. |
| Data recovery completeness | Verified downstream segments / source sealed segments eligible for verification, with restore-test proof. | Manifest hash, row count, durable ACK, backed-up index and recovery result. | Unverified copies cannot justify retention deletion. |

### Primary measures and guardrails

**Primary outcomes:** actual realized after-cost P&L (once verifiable), signal-level after-cost expectancy, and mature actionable-signal precision. **Drivers:** quality funnel conversion, alert admissibility, book capacity and freshness. **Guardrails:** F11 isolation; no uncontrolled funded orders; max drawdown; false-alert noise; AI cost; missing data and stalled workers.

Avoid “profit” cards based only on **target hit %**, hypothetical compounding, modeled AI moves, or unverified exchange market quotes.

## 4. Telegram Center — full operator contract (new V6 feature)

### 4.1 Explicit notification catalog

| Category | Proposed default | User may mute? | Quiet hours? | Reason |
|---|---|---|---|---|
| **F11 unsafe exposure / protection incident** | Mandatory ON | **No** | **Bypass** | Risk protection is independent of optional preferences. |
| **Critical canonical writer/data integrity** | Mandatory ON | **No** | **Bypass** | Operator needs immediate actionable failure notice. |
| **Critical existing-position protection warning** | Mandatory ON | **No** | **Bypass** | A live hazard is not discretionary market commentary. |
| Actionable BUY entries | ON | Yes | Queue only if still valid; otherwise expire | Must pass all safety/economic/quality/liquidity gates. |
| Existing-position advisory updates | ON | Yes (noncritical) | Configurable | Keep noncritical informational follow-ups distinct from hazards. |
| TP1/TP2 reached / plan expiration | ON | Yes (noncritical) | Digest possible | Show plan/position provenance; no false “auto sell.” |
| Strict Early Watch | **OFF** initial | Yes | Yes | Owner wants high confidence without watch floods. |
| Generic price movements / market radar | **OFF** | Yes | Yes | Preserve evidence internally without Telegram flooding. |
| Calendar/event reminders | OFF / digest | Yes | Yes | Lead-time and event timezone configurable. |
| Scanner degradation | ON for sustained incident | Yes for noncritical summaries | Yes except critical | Avoid per-cycle messages; only actionable exception. |
| AI model/cost budget warnings | ON for material issues | Yes for low severity | Yes except critical budget outage | One alert per incident or summary window. |
| Paper-trading activity | OFF | Yes | Yes | Never confuse with funded trade fills. |
| Weekly scorecard | ON | Yes | Weekly scheduled digest | Show period, sample counts and net measures. |
| Broad market-watch digest | OFF | Yes | Yes | One consolidated message, not per-symbol spam. |

### 4.2 Time adjustment requirements

- Separate **chart display timezone** and **notification schedule timezone**. Use IANA IDs such as `America/Indiana/Indianapolis`, `America/New_York`, `UTC` — never a fixed UTC offset for quiet-hour policy. Timestamps are recorded as ISO-8601 UTC; format the same instant for user display.
- Editable quiet-hour start and end (`23:00` to `05:00` is the initial **design suggestion**, not asserted active runtime), day-of-week selection, digest wall-clock and weekly digest weekday/time.
- Daylight-saving transitions: explicitly resolve skipped/duplicate local times, persist choice, test both spring-forward and fall-back. A quiet-hour span crossing midnight must function correctly.
- After quiet hours, **do not deliver expired actionable entry plans**; include expired notice only in historical/suppressed evidence or an optional summary.
- No expiration can suppress independent F11 or other mandatory critical hazards; such bypasses require logged reason code.
- Rate limits per category and per plan identity; a global cap must not become an implicit safety mute.

### 4.3 Early Watch and actionability design direction

- Owner's proposed thresholds: **≥ $500K observed executable-order-book depth for the narrowly gated Early Watch candidate**, **≥ $600K for actionable order book**, calibrated evidence confidence **≥ 90% if a probabilistic confidence metric exists**. These are **owner proposals requiring code/runtime reconciliation**; the existing production registry also has market-turnover, spread, sized slippage and depth variables with different semantics.
- BTC regime, fresh quote and book, spread, sized slippage, anti-chase and early-phase qualification remain independent deterministic gates. Order-book depth is **not** interchangeable with 24-hour market turnover.
- Mandatory reason codes for suppressed alerts: unverified depth, BTC backdrop, chase, thin book, size impact, outdated evidence, quality threshold, cooldown, user subscription, quiet hours, daily cap, missing verification and expiration. Preserve all suppression evidence.
- Telegram headline should contain **Tradevisor Alerts**, direction, pair, observed timestamp, strategy/version, actionable entry/stop/targets if present, freshness, cost-adjusted R and invalidation, and never an invented entry or stale price. “No entry recommended” alone is not an actionable alert.

### 4.4 Security and governance

The notification UI stages a versioned policy; it never stores/exposes provider tokens, never grants exchange order authority, and never bypasses F11 or hard deterministic qualification. A production test message must be a separate opt-in read-only/send-test action, not an implicit side effect of saving a form. A preview is not a real delivery. Give every policy version an actor, reason, expected routing difference, exact dry-run count and rollback receipt.

## 5. Config registry — reviewed against current repository metadata

The code currently declares these **config metadata keys**, not necessarily live effective values. The prototype maps them into understandable groups, with bounds tooltips and read-only proof of active runtime values still required:

| Group | Existing key examples | UX presentation and decision |
|---|---|---|
| Scoring | `v2.threshold`, `v2.adx_min`, `v2.rvol_zero`, `v2.rvol_full`, `v2.weights.*`, `v2.production_selector` | Qualification score, trend and volume scoring, component weights; production selector requires independent promotion/activation authorization. |
| Attribution | `attribution.outcome_maturity_hours`, `attribution.min_sample_size`, `attribution.v1_threshold`, `attribution.round_trip_cost_pct` | Frozen outcome horizon, minimum sample and fair V1/V2 comparison with after-cost assumptions. |
| Scanner | `scanner.execution_mode`, `scanner.max_candidates`, `scanner.core_memory_ceiling_pct`, `scanner.dispatch_token_seconds` | Isolation, candidate budget, resource preflight and bounded dispatch lifetime. |
| Economics and delivery gates | `opip_actionable_min_net_reward_to_risk`, `opip_actionable_round_trip_fee_pct`, `opip_actionable_min_market_liquidity_usd`, `opip_actionable_max_spread_bps`, `opip_actionable_max_size_slippage_pct`, depth guards in runtime settings | Label as **source-code/runtime settings needing registry reconciliation**; distinguish market-turnover, executable depth, spread, sized impact, and notification-only gates. |
| AI | `ai.provider`, `ai.model`, `ai.mode`, `ai.daily_budget_usd`, `ai.timeout_seconds`, `ai.api_key` | Per-provider routing and spend, provider key only as masked credential reference; no token echo or model authority creep. |
| Data and resources | `retention.days`, `retention.delete_enabled`, `ingestion.batch_records`, `ingestion.max_bytes`, `sqlite.busy_timeout_ms`, `sqlite.query_timeout_seconds`, `sqlite.page_size_max` | Retention dry-run, ingestion limit, query budget; deletion separate owner gate. |
| Dashboard | `dashboard.refresh_seconds`, `dashboard.stale_after_seconds`, `dashboard.default_window_days`, `dashboard.export_row_limit` | Data freshness, default period and export limits with clear units. |
| F11 | `f11.state`, `f11.new_admissions_allowed`, `f11.allow_funded_orders` | **Read-only status/authority display** in this UI; no dashboard override of independent protection. |
| Telegram **new** | `notification.preferences.*`, `quiet_hours.*`, `schedule.*`, `routing.*`, `delivery.*` (proposed namespace) | New versioned policy and auditable API required; do not claim already supported by code registry. |

**Specific original-wireframe corrections:** “Changing hit definition re-scores history” needs an immutable metric-definition version and explicit recomputation, not silent overwrite; model promotion must not be presented as one-click activation; “Trade box 80 GB” was a placeholder, not actual capacity; config cannot display secret values or implicitly test credentials; “All systems OK” must depend on verified independent evidence.

## 6. Filtering, custom data pulls and export UX

### Universal filters

A permanent top-level period filter offers **Last 24 hours / 7 days / 30 days / 90 days / Custom start–end**. It must be consistent across page cards, charts, tables and exports. Search accepts symbol, strategy/version and evidence IDs. Page-level filters add source (`Native`, `Tradevisor`, `Paper`, `Manual` where applicable), direction, stage, policy, maturity and outcome. Every filtered view should expose count of rows actually included.

### Custom extraction builder

The **Audit & custom export** page must support: dataset selection (signals/outcomes, paper fills, events, AI usage/cost, Telegram delivery/suppression, audit entries), field selection, UTC timestamp filter, preview of first rows, row count, CSV/JSON download and a **data-provenance manifest**. The HTML prototype implements these functions **on synthetic in-browser records**; the production implementation requires governed server-side read APIs.

Server-side export contract (proposed; not existing API routes):

```json
{
  "dataset": "signal_episodes",
  "period_utc": {"from": "2026-11-01T00:00:00Z", "through": "2026-11-13T23:59:59Z"},
  "filters": {"source": ["Native", "Tradevisor"], "strategy_versions": [], "outcomes": ["HIT", "MISS"]},
  "columns": ["observed_at", "signal_id", "strategy_version", "score", "matured_outcome", "after_cost_r"],
  "format": "csv",
  "row_limit": 5000
}
```

The server should respond with exact query parameters, dataset/read-model version, source freshness, count, observed-at watermark, generated-at UTC, checksum and safe file link or streamed output. **Never** grant the frontend direct database credentials. Enforce row and byte limits, pagination, request audit and CSV formula injection protection.

### Time handling

Store and export the canonical UTC instant, present local timezone (e.g., Indiana/ET) in the viewer, avoid silent conversion of a historical `observed_at` to “last seen,” and label whether time refers to **event occurrence, data ingestion, scan attempt, admission, Telegram send or broker fill**.

## 7. Visual and interaction specification

### Official O’Pip brand identity (revision 3)

- Use the **owner-supplied original emerald/gold hexagonal O’Pip parrot crest** in the primary navigation rail, atlas cover and sign-in/brand identity surfaces. Do not substitute a generic lettermark or an invented stock logo.
- Use the **flying green-and-gold parrot** as a restrained navigation bridge between Trading Insights and Operations Center, a subtle marker on every page heading, and a small chapter-end divider in the visual reference. The parrot is a visual signature, **not** a green health/status indication.
- Preserve the bird’s natural emerald, gold and white colors; use transparent image edges; retain accessible alt text for the crest and mark the repeated decorative parrot as presentation-only.
- When implementation starts, move the exact source artwork to controlled/static public brand assets rather than embedding large images inline in application bundles. The portable HTML design prototype embeds assets deliberately to work offline.



- **Consistent hierarchy:** 27–30 px title, ~12 px body, 10–11 px footnotes and labels, 4 decisive metric cards above fold, 2-column comparison zones, then full-width evidence tables. Tables are responsive and horizontally scrollable without truncating export values.
- **Semantic color:** green only for verified pass or positive numeric result, amber for warning/paused/stale/unverified, red for failure or blocked protection, gray for unconfigured/unknown, blue for informational. Never use color alone as the state label.
- **UX literacy:** plain-English microcopy beside specialized terms; each chart clarifies what is counted, denominator, comparison period and what conclusions cannot be drawn.
- **Page-level action placement:** one primary decision action per page; “Preview draft,” “Export filtered,” “Inspect evidence” instead of ambiguous “Run.”
- **No deceptive clicks:** in the demo, interactions only change local browser state or download marked DEMO files. Disabled production actions visibly explain the authorization or API missing.
- **Accessibility:** readable contrast, focus-visible controls, explicit form labels, state text beside color, responsive 12-column layout, keyboard-accessible menus, semantic tables and alt text for charts.

## 8. Production data/read API contracts — proposed boundaries

**Read endpoints** should return typed source metadata, `observed_at`, `ingested_at`, freshness/stale status, row/episode identity and versioned definitions. Candidate areas (names illustrative): `/ops/v6/summary`, `/ops/v6/signals`, `/ops/v6/scorecard`, `/ops/v6/health`, `/ops/v6/ai-usage`, `/ops/v6/telegram-deliveries`, `/ops/v6/events`, `/ops/v6/data-catalog`, `/ops/v6/audit`, `/ops/v6/export`.

**Write endpoints** for Telegram and strategy drafts should be distinct from activation and hard-protected. They require explicit operator identity, schema and bounds validation, expected active config revision, dry-run impact, durable audit, anti-CSRF/session protection and rollback semantics. No call to trading keys, funded execution or direct DB writes from the frontend.

**Do not fabricate data** to make the UI feel complete. Use loading, unavailable, stale, forbidden, zero and partial states distinctly. If a source returns empty after a successful read, display `0 with source watermark`; otherwise use `unavailable`.

## 9. Delivery sequence for Claude — risk-ordered, minimal handoffs

**P0 · Information contracts and UX skeleton:** Freeze this navigation, data dictionary and status semantics; compare against existing Control Center V2 and APIs; identify existing reusable components; no duplicate dashboard. Tests cover freshness/denominator/period conversions before adding charts.

**P1 · Profitability and signal evidence:** Build Overview, Signals, Scorecard, Paper and Health on read-only evidence. Enforce actual/paper/alert separation, lineage and no invented profitability, with one consistent cross-screen filter.

**P2 · Telegram control:** Build explicit preference matrix, quiet hours, time zone, weekly digest, suppression ledger and preview; no live changes by default. First demonstrate draft → validation → audit → owner-approved activation with forced safety categories.

**P3 · AI, events and config:** Add model/spend evidence, source freshness, schema-driven config catalog, strict selection and owner promotion gates. Reuse existing backend controls.

**P4 · Data governance, learning and audit:** Retention dry-run and proof, non-destructive data catalog, Learning Lab paused view, custom CSV/JSON export and audit manifest. Any deletion/learning activation separately authorized.

**P5 · Acceptance and release:** Review the design against this specification, verify responsive/keyboard UX, execute focused ATDD and exact-head CI, provide deployment architecture/rollback evidence, then request separate owner authorization. **No funded trading, F11 changes or production deployment from this design task.**

## 10. Acceptance checks the finished UI must pass

1. Switching period to 24h / 7d / 30d / custom changes KPIs, tables, charts and export rows consistently, without incorrect timezone boundaries.
2. Clicking “Actual realized P&L” without broker evidence shows **UNAVAILABLE**, never a sample/paper profit.
3. A pending signal is never scored as hit or miss. Confidence percentages appear only from calibrated evidence.
4. F11 unhealthy/unverified is not green; Telegram safety alerts bypass quiet hours and discretionary preferences.
5. Turning off Early Watch leaves underlying internal evidence untouched and does not affect deterministic trade eligibility.
6. Owner can choose information categories, quiet start/end, time zone, days, digest time, entry cap and cooldown, preview expected notifications, and read suppressions with reason codes.
7. Saving notification preferences with insufficient authorization cannot change live routing; failed send is not counted as delivered.
8. Every strategy configuration proposal shows current/effective version, draft, impacted gate, validation, approver and rollback; protected settings remain read-only.
9. Hot-data deletion is impossible without verified downstream receipt, restore evidence, matching floors, independent eligibility and owner approval.
10. Custom export uses the selected fields and filter period and includes source, UTC timestamps, row count and manifest. Formula-like spreadsheet values are escaped.
11. All 12 screens share one information architecture; no duplication with existing Control Center routes.
12. Each page remains usable to a layperson without understanding cgroups, R-multiples, policy hashes or journal manifests before opening detail.

## 11. Prototype limitations and engineering handoff

The delivered **interactive HTML is a frontend-only synthetic demonstration**; it supports navigation, time-range filters, search, signal filters, notification toggles, timing controls, config draft comparison, row inspections and downloading **sample** CSV/JSON. It has **no live data connection, authentication, persistence, credential handling, Telegram send, broker order, configuration write, deletion, learning promotion or deployment capability**. The PDF visual atlas is a set of static views, including supplementary Telegram and Config tabs. It should be used for layout acceptance and stakeholder review rather than runtime validation.

**Source verification performed:** the four-page uploaded PDF visibly included Home, Scorecard, Config and Data Governance. The uploaded HTML corresponds to a Data Governance prototype. The repository's `app/opip/config/registry.py` provides schema-like metadata for several settings but does not establish current effective values; `app/core/config.py` includes actionable-only and Early Watch notification flags. Extended Telegram preferences, CSV/JSON extraction and the new screen read APIs are **requirements**, not verified existing features.

---

## 12. Existing-dashboard migration and retirement contract — revised handover

**Source distinction:** The findings below are based on the O’Pip GitHub repository's read/API definitions and the previously supplied four-page wireframes, **not** a verified inventory of production uptime, mounted services, runtime data, or current traffic. The new 12-screen HTML remains synthetic. Route presence in source does not prove that an endpoint is deployed or has sufficient evidence. Require runtime verification before cutover.

**Decision:** Consolidate the *user experience* into one V6 Operations Center. **Reuse existing trusted services and analytical contracts; do not fork them into new scoring, config, trading, Cockpit or retention engines.** Defer retirement of old UI/containers/jobs until V6 source parity, security parity and production rollback tests pass. Never delete canonical evidence or remove independently required F11, scanner, paper, notifier, writer or analytical replication services as part of UI cleanup.

### 12.1 Existing route and feature inventory → V6 destination

| Existing surface / code path | Verified code-level capability | V6 destination | Required preservation | Verification still needed |
|---|---|---|---|---|
| Legacy intelligence dashboard: `/dashboard`, `app/api/dashboard.py` | `/api/analytics/summary`, `/api/analytics/intelligence`, `/api/opip/zero-trade-explanation` | **Overview**, **Health & funnel**, **AI & models** | Zero-BUY root-cause description, terminal gate, record-backed intelligence and explicit missing states | Actual runtime routing, source age, response shape and nonduplicative rendering |
| Legacy comparison: `/dashboard/scoring` | `/api/opip/scoring-comparison` returns read-only V1/V2 comparison evidence | **Signals & alerts**, **Profit scorecard** | Score versions, differences, per-attempt provenance; avoid treating comparison scores as realized P&L | Matched outcome maturity, same period/cohort and source completeness |
| Legacy strategy admin: `/dashboard/strategy-admin` | `/api/opip/strategy-admin/draft`, `/active`, `/activate`, `/rollback` with protected authority | **Configuration**, **Audit** | Draft/effective identity, expected digest, approver, guarded activation and rollback | Current rights, protected workflow and deployment-specific feasibility |
| `control-center-v2` `/dashboard/v2/` — Signals | `/api/opip/control-center/signals`, `/forensics` with V1/V2 evidence, sort/search/paging, source hash and export | **Signals & alerts**, **Audit & custom export** | Existing forensics drawer, record hash, scan/attempt ID, source/maturity and bounded pagination | Expand to cross-source exports only through approved, typed read APIs |
| `control-center-v2` — Qualification | `/api/opip/control-center/scanner` and zero-trade view | **Health & funnel**, **Signals & alerts** | Attempt-level scanner funnel, skips and rejection evidence; distinguish unregistered stages from 0 | End-to-end scanner→gates→AI→Telegram join and denominators |
| `control-center-v2` — Intelligence/Operations | `/api/opip/control-center/ai`, `/health`; model, F11 and source freshness claims are evidence-gated | **AI & models**, **Health & funnel**, **Overview** | AI invoked/not-invoked/errors and latency; independent F11 proof; never hard-code green | Verify latest available telemetry and independent health proofs |
| `control-center-v2` — Config/Data | `/api/opip/control-center/config`, `/config/preview`, `/config/stage`, `/data`, `/maintenance/*` | **Configuration**, **Data governance**, **Audit** | Existing eight-domain schema registry, staging with audit and optimistic concurrency; maintenance and retention safety boundaries | Confirm effective vs staged settings, user authority, read-only placement and storage attribution |
| Read-only paper Cockpit: `/cockpit` | `/api/cockpit/overview`, `/trades`, `/trades/{paper_trade_id}` backed by **separate analytical-plane canonical replica** | **Paper trading**, **Profit scorecard**, **Overview** | Portfolio by currency, paper ledger, trade drill-down, verified cost/state/provenance, trust envelope | Reconcile available population, expense model and replication freshness; **never treat paper as actual fills** |
| F10 semantic Cockpit: `/cockpit/f10` | `/api/cockpit/semantic/pages`, `/pages/{page_id}`, `/panels/{panel_id}`, `/metrics/{metric_id}`, `/facts/{fact_id}` | **Health & funnel**, **Signals**, **Paper**, **Scorecard**, **AI**, **Learning**, **Audit** | Versioned fact/metric/panel semantics, declared filters, source trust and withheld/unavailable labels | Page-by-page count/metric/fact lineage and exact filter compatibility |
| Legacy Telegram command center (`app/services/telegram_command_center.py`) and notification policy (`app/core/config.py`) | Read-only Telegram commands, existing market-watch management, actionable-only/Early Watch flags, notification thresholds; no evidenced comprehensive GUI preference API | **Telegram center** | Current operator authentication, compulsory safety notifications, command/routing isolation and existing fail-closed gates | Build governed category/timing/quiet-hour preferences and delivery/suppression query API; do not claim already implemented |

### 12.2 F10 eight semantic views — explicit mapping, not a second UI

| F10 page ID in `app/opip/cockpit/semantic.py` | V6 consumer | What to retain |
|---|---|---|
| `P1_SYSTEM_PULSE` | Health & funnel / Overview | Source freshness, trust/reconciliation state and system readiness |
| `P2_DISCOVERY_REGIME` | Signals & alerts / Market events | Discovery/regime evidence with declared populations; **do not manufacture a funnel** from row identities |
| `P3_DECISION_TRACE` | Signals & alerts / Audit | Decision evidence identity and attributed reasons |
| `P4_PAPER_PORTFOLIO` | Paper trading / Scorecard | Reconciled Paper-v2 ledger, cash/portfolio and lifecycle evidence where actually available |
| `P5_QUALITY_MISSED_OPPORTUNITY` | Scorecard / Signals | Quality and missed-opportunity evidence only when supported; retain withheld sections |
| `P6_COMMITTEE_VALUE_AI_COST` | AI & models | AI cost and committee value comparisons with OFF/shadow labels; do not imply committee activation |
| `P7_LEARNING_RELEASES` | Learning lab / Audit | Learning and release evidence with learning paused and owner-gated promotion |
| `P8_INCIDENTS_INFRASTRUCTURE` | Health & funnel / Audit | Incidents, infrastructure and delivery/reconciliation gaps |

F10's **UNAVAILABLE**, **WITHHELD**, **INSUFFICIENT DATA**, **STALE** and **unsupported filter** results are contractual states, not defects to paper over with a placeholder value. Read-only APIs must remain read-only and separate from any command/write surface.

### 12.3 New functionality not provided by parity alone

- **Telegram management:** subscription matrix for actionable BUY, manual position/SELL/TP/SL changes, gated Early Watch, market events, summaries and operating alerts; safe mandatory alerts cannot be muted. Per-category enablement, daily caps, cooldown, sender/recipient scope, quiet hours by explicit IANA time zone, weekday schedules and digest timing. DST overlaps/gaps and expired signals require tests. Record `ELIGIBLE`, `SUPPRESSED`, `RESERVED`, `SENT`, `CONFIRMED`, `FAILED`, `RETRIED` distinctly. Existing config flags must be reconciled; do not silently create a second notification policy.
- **Period-consistent evidence drill-down:** 24h/7d/30d/90d/custom boundaries, UTC source timestamps alongside local display, correct denominators, mature/pending distinctions and result reconciliation across overview cards, charts, tables and exports.
- **Source-backed custom exports:** dataset + columns + source/strategy/version/market/outcome filters, row/byte bounds, auditable CSV/JSON with watermark, read-model version, checksum and CSV injection hardening. Reuse existing V2 bounded export and F10 semantic metrics rather than copying query engines.
- **Net-profit clarity:** actual realized broker P&L, paper net P&L, and modeled signal hit rates in distinct cards with source, cohort, fees, slippage, net R, currency and sample-size provenance. Never display alert target hits as booked profit.
- **High-value operator views:** scanner job/run ID, skipped scans/cgroup reason, actionable liquidity/book-depth evidence, AI request/provider budget and errors, retention/shipping/recovery status, independent F11 proof and genuine end-to-end alert delivery lineage.

### 12.4 Migration acceptance gates (must be evidenced before legacy UI retirement)

1. **Inventory exact route consumers:** enumerate live vhost/nginx routes, compose services, reverse-proxy allowlists, authentication boundaries, scheduled jobs, dependent tests and telemetry. Separate UI/static files from read services, canonical pipeline and Cockpit replica.
2. **Create a route-to-metric parity manifest:** `legacy_route`, `legacy_panel`, `V6_page`, `source/API`, `grain`, `formula`, `filters`, `status semantic`, `role`, `parity test`, `runtime proof`, `cutover status`. Explicitly mark source fields missing, demo-only or not deployed.
3. **Prove metric parity:** compare same exact UTC window, data watermark, cohort and strategy version. Counts, paper net P&L, V1/V2 evidence, funnel verdicts and AI spend must reconcile within documented tolerances; any difference is explained, not hidden.
4. **Prove deep-link and export parity:** maintain existing forensics drill-down, paper trade details, F10 fact/metric drill-down and bounded CSV/JSON without weakening authentication.
5. **Prove isolation and failure semantics:** `UNAVAILABLE` ≠ zero, F11 `UNVERIFIED` ≠ healthy, no frontend access to canonical writer or trade credentials; no leakage of protected keys to analytics plane.
6. **Verify performance and actual savings:** measure per-container CPU peak/mean, RSS, disk footprint, projection refresh load and disk IO **before/after**. Do not promise RAM savings from deleting static dashboard HTML; only retired duplicate services/jobs reduce recurring compute.
7. **Cut over cautiously:** choose one canonical V6 URL; run old UI read-only in parallel, use a reversible route/flag change, verify audit/rollback and only then retire redundant UI/static assets. Preserve independent F11, trading, writer, paper, Telegram and analytic replica.
8. **Governance:** focused ATDD tests, exact-head CI, owner gate for release, separate later owner gate for destructive cleanup. No automatic trading, learning activation or committee activation.

### 12.5 Claude-specific handover / implementation order

- **First:** inventory source-backed legacy capabilities and actual route state, then return a signed-off feature-parity sheet (existing / reuse / new / deferred / unavailable); do not assume the dashboard currently deployed equals the repository.
- **Second:** revise the 12-page V6 design **without building another dashboard service**. Extend the existing Control Center V2 shell and semantic/read APIs or propose one minimal consolidated read facade; preserve separate Cockpit analytics-plane authority and explicit data trust.
- **Third:** define new Telegram preferences, time control, filter consistency and custom exports as API contracts and UI acceptance criteria. Show operator permission levels and audit flows before connecting any live write surface.
- **Fourth:** implement only approved read-only slices behind UI-only release gates; revalidate all historic dashboards before recommending retirement. Present a phased migration/rollback proposal with measured cost before switching off anything.

**Authority:** This is design/implementation planning only. **No merge, core redeploy, F11 change, funded trading, learning activation, data deletion or dashboard retirement is authorized by this specification.**