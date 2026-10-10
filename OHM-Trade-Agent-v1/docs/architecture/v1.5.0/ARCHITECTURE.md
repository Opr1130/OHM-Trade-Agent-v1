# O’Pip Profit Intelligence Platform Architecture

This file is a paragraph extraction of the owner-supplied DOCX stored beside it. The DOCX is the architecture authority. If this extraction and the DOCX disagree, the DOCX controls. Source tables are preserved as sequential lines in document order. This extraction does not redesign the architecture.

O’Pip Profit IntelligencePlatform Architecture
Version 1.5.0
Continuous Multi Horizon Capital Intelligence
Architecture revision • 9 October 2026 • R5 delivery direction
Purpose and decision
O’Pip will continuously observe eligible markets, classify opportunities as TACTICAL, SWING or POSITION, and maintain a capital-aware portfolio plan. Approved deterministic policies will eventually manage an isolated paper portfolio through entries, holds, profit taking, time exits and capital rotation. Learning will compare frozen expectations with actual outcomes, including the time for which capital was committed.
The governing objective remains MAXIMIZE SUSTAINABLE REALIZED NET PROFIT AFTER ALL COSTS, subject to risk, liquidity, evidence quality and reliability constraints. Holding cash is a valid plan. Profit targets cannot force trades, weaken protection or promise returns.
Scope and authority
This is the next versioned architecture extension to the verified v1.4.3 authority and the v1.4.4 precision-agent amendment. The source documents remain unchanged and their unamended requirements remain binding. Sections 1 and 19 define precedence and source identity. This file does not certify implementation, modify the repository authority pin, approve a release or activate any mode.
The work requested here is documentation only. Funded trading, deployment, TARGET_PAPER activation, production configuration and live account actions are outside scope. Autonomous paper management means execution of approved deterministic rules in an isolated paper environment; it does not grant runtime authority to the AI Committee.
Delivery order
R4 evidence closure → R5 contract freeze → R5-A Continuous Market Eye → R5-B Horizon Intelligence → R5-C Capital Intelligence → R5-D Portfolio Planning and Rotation → R5-E Autonomous Paper Portfolio Management → R5-F Horizon Learning and Strategy Evaluation → separate TARGET_PAPER readiness decision.
How to use the package
Sections 1–13 define the design, records and measurements. Sections 14–18 define priorities, acceptance scenarios and release gates. Section 19 records sources and decisions still needed before implementation or activation. The companion feature tracker provides editable tracking rows. Proposed requirements and acceptance scenarios are not claims that software exists or tests have passed.

1 Authority and preserved contracts
On adoption, v1.5.0 governs only the explicit R5 amendments below. All other v1.4.3 requirements, including its inherited v1.2 clauses, remain binding. v1.4.4 precision, guardian, confirmation, expected-versus-actual, causal-confidence and usefulness requirements are carried forward. Their full PA and SQ acceptance inventories remain required where applicable. No prior source or evidence is erased. [S1, S2]
Boundary
Binding rule
Canonical ownership
One canonical writer and one history per fact. New modules submit typed intents through existing ownership; projections and research caches are not alternative authorities.
Runtime authority
F5 retains feasibility and veto/abstention. F6 owns statistical forecasts. F7 owns constrained selection and atomic reservation. F8 owns paper execution. F11 protects independently.
AI and operator surfaces
F12 is asynchronous advisory research. No AI vote, narrative or wrapper changes admission, exit, size, stops, risk or probability. Telegram and dashboards have zero trading authority.
Protection
F11 is independent of discovery, horizons, planner, forecasts, Committee and dashboard. Unsafe admission can suspend automatically; resumption remains human-governed.
Release profiles
Preserve SAFE_BASELINE, EVIDENCE_SHADOW and gated TARGET_PAPER. No hidden environment-variable activation or new profile. Document publication changes none of them.
Paper and funded accounts
Paper has no funded credentials or funded order endpoint. Funded account observation remains read-only/advisory. No leverage, Kelly sizing, dynamic risk expansion or new derivatives execution.
Learning and topology
Use verified exports and existing learning capacity. No analytics database on the trading host, extra scheduler, broker, node or competing deployment path. Preserve exact-SHA core/learning compatibility.
Evidence position
GitHub main resolved to 8b3cc2712432ca21007be4db0667301d48b89d97 during this task. Repository status documents at that revision still describe a 3 October reconciliation and pin v1.4.3. Their built-state rows are historical reports, not a fresh runtime audit. The referenced conversation reports a later EVIDENCE_SHADOW deployment and pending R4 validation; this task did not verify production. R4 closure is therefore NOT VERIFIED HERE. [S3–S6]

2 Architecture flow and responsibility
The Continuous Market Eye adds event-driven attention to the existing F1–F7 path. Horizon classification enriches the same opportunity and decision records. Capital planning and rotation extend F7; autonomous paper management extends F8. These are logical responsibilities within the existing application, not new independent trading stacks.
Stage
Owner and output
Observe and focus
F1/F2 observation and incremental state → attention trigger with source evidence, coverage and expiry. No trade claim or alert.
Qualify an opportunity
Existing F2 snapshots → F3 IGNITION → F4 episode and horizon classification. Preserve one lifecycle and one detector family.
Evaluate feasibility and value
F5 feasibility/contradiction checks → F6 horizon-specific calibrated forecast. Mandatory missing evidence produces abstention.
Plan and select
F7 Capital Planner reads reconciled paper capital and the full eligible candidate panel; compares new entries, existing holdings and cash under approved policy.
Commit paper actions
Existing R4 tradeability/quality and deterministic safety gates → F7 atomic reservation → F8 approved paper execution. Recheck version, prices, capacity and expiry at commitment.
Communicate
R4 quality and notification policy renders committed decision state to the advisor/Telegram. Notification delivery is not order authority and does not gate protection.
Protect and learn
F11 sends priority protective intents independently. F9 evaluates outcomes through verified exports. F10 reads reconciled semantics. F12 receives an asynchronous advisory copy.
Three operating speeds
Broad perception uses cheap incremental observations. Focused intelligence evaluates meaningful state transitions within a bounded approved evaluation schedule. The existing slower scheduler remains responsible for universe refresh, long-horizon reconciliation, gap recovery and fallback. Both trigger types feed the same deduplicated evaluation path.
Existing input discipline
A trigger may request earlier attention but cannot synthesize a favorable FeatureSnapshot or bypass warm-up. R5-A preserves the existing detector evaluation grid. Any new off-grid evaluation policy must be explicitly versioned, retain every evaluation snapshot and its scheduling event, and prove replay before use. No tick-order feature is admitted unless the required order evidence is retained.

3 Continuous Market Eye
R5-A provides continuous observation coverage and timely attention without selection or trading authority. It must demonstrate earlier detection of eligible developing opportunities against a frozen scheduler baseline, within the same observed universe. It cannot claim that every market opportunity is observable. [S1 §3; S2 §3; S6]
Requirement
Contract
ME-01 Incremental observation
Maintain approved price, volume, spread, liquidity, volatility, level-distance and regime facts through F1/F2. Reuse feature definitions; do not build a second feature calculator.
ME-02 Attention states
DORMANT → WATCH → DEVELOPING → FOCUSED → COOLDOWN. STALE/RECOVERING is an explicit health overlay. Attention states are not F3 claims or F4 trade approvals.
ME-03 Trigger identity
A trigger includes instrument version, source event IDs, prior/new attention state, trigger-policy version, evaluation request time, cutoff, watermark, reason and expiry. Duplicate deliveries converge to one scheduled evaluation.
ME-04 Bounded work
Coalesce repeated attention requests by instrument and policy; preserve material transitions and suppression counts. Record eligible, evaluated, coalesced, expired and budget-skipped populations.
ME-05 Fairness and capacity
Bound universe size, queue age, per-instrument work and retries. Reserve capacity for protection, reconciliation and open positions before discovery; optional AI is last priority.
ME-06 Failure and recovery
Record stream gaps, reconnects, late/out-of-order events and snapshot divergence. Backfill may repair current state, but cannot rewrite prior evidence cutoffs or invent historical ordering.
ME-07 Reconciliation
Keep existing scheduled reconciliation and stream recovery. It submits the same typed requests and cannot become a competing detector, writer or allocator.
Acceptance evidence
Retain source-availability-to-trigger, trigger-to-evaluation and evaluation-to-actionable latency distributions, plus coverage and expired-before-evaluation counts. Freeze numeric workload, latency, freshness and queue limits before testing. Demonstrate duplicate delivery, burst load, restart, fallback overlap and gap recovery. R5-A is accepted only when it produces zero trading authority and cannot starve F11.

4 Horizon Intelligence
R5-B assigns an explicit economic intent before selection. Horizon is a versioned property of a thesis, not an after-the-fact description of how long a position happened to remain open. Typical ranges below explain intent only; exact boundaries are release policy inputs, not approved durations or return promises.
Horizon
Purpose and evidence
Management rule
TACTICAL
Minutes to a few days; immediate structure, momentum, executable liquidity and a short-lived entry window.
Validate early; harvest under approved exit policy; expire when the expected move fails to develop.
SWING
Days to weeks; sustained technical/regime development with explicit milestones.
Allow the declared path and volatility; reassess at milestones, invalidation or time expiry.
POSITION
Weeks to months; supported structural thesis, durable liquidity and declared catalyst evidence.
Use a bounded long-horizon sleeve; short-term noise alone cannot rewrite the structural plan.
Required thesis record
HZ-01: Freeze thesis_id/version, episode_id, horizon_class, classifier version, supporting/contradicting evidence, entry_window, expected_holding_range, thesis_horizon, price_invalidation, time_invalidation, profit-taking policy, expected capital occupancy, forecast reference and policy versions. Record classification uncertainty. UNKNOWN or unsupported horizons remain research/watch-only and cannot receive approved allocation.
HZ-02: The original trade thesis and first-fill clock are immutable. An extension or reclassification requires a new linked decision, new evidence cutoff and the same risk/approval gates. Never turn an expired tactical loss into a position investment by relabeling it or resetting the clock. Additional fills do not silently restart time invalidation.
Evidence and detector scope
Horizon classification does not introduce a second detector family. IGNITION remains the inherited family; R5 first classifies only supported claims and declares coverage limits. POSITION research may require new structural evidence that IGNITION cannot establish. Such cases remain unallocated research until a separately reviewed extension proves their point-in-time features, detector support and calibration.
News, social and on-chain inputs retain source, publication, availability and revision times. Missing optional context stays unknown. A transfer observation does not establish buying intent. No statement about possible 100–200% appreciation becomes an expected return without a calibrated, evidenced forecast.

5 Opportunity and position lifecycles
The following are logical state contracts to map onto existing F4/F8 enums during contract freeze. They are not instructions to create parallel state machines or replace canonical identifiers. Every transition includes reason, prior state/version, event identity, policy and cutoff.
Lifecycle
Transitions and invariants
Opportunity in F4
OBSERVED → QUALIFYING → ELIGIBLE or WAIT/REJECTED. ELIGIBLE → SELECTED/RESERVED or EXPIRED/SUPERSEDED. WAIT has a bounded deadline. Requalification uses a new decision linked to the existing episode.
Entry in F8
RESERVED → SUBMITTED → NO_FILL/PARTIAL_FILL/FULL_FILL. Residual quantity has its own expiry. Fills update cash, position and remaining reservation atomically; retries do not duplicate them.
Open paper position
OPEN → EARLY_VALIDATION → MANAGED → EXIT_PENDING → CLOSED. Hold, reduce, approved add, profit taking and time exit are policy decisions on this record, not separate ledgers.
Terminal outcome
Preserve TARGET, STOP, TIMEOUT and independent RISK_EXIT semantics. Attach TIME_INVALIDATION, THESIS_INVALIDATION or ROTATION as versioned reasons/subtypes without silently changing historical labels.
Uncertain outcome
Feed/protection gaps mark fidelity and INCOMPLETE_COVERAGE. Preserve open obligations and escalate. No invented stop fill, assumed close or automatic clean outcome on recovery.
Decision precedence
LC-01: F11 protection and admission suspension take precedence over profit seeking. A protective close invalidates any conflicting discretionary action through position/portfolio version checks. Once protection is committed, the planner replans from the resulting state; it cannot resurrect the previous plan.
LC-02: Profit targets, trailing rules, partial exits and time invalidation must be predeclared deterministic policies. Position monitoring cannot call a live LLM to decide a hold, add or exit. F12 may record a separate critique for later research.
Recovery and corrections
LC-03: The canonical writer acknowledges only committed transitions. Crash recovery resumes from committed state and stable intent IDs. Stale commands are rejected and recomputed. Corrections append supersession evidence; no source is deleted to make the lifecycle reconcile. Missing ownership or conflicting terminal facts blocks new admission until resolved.

6 Capital Intelligence and accounting
R5-C computes available capital from the existing paper ledger and approved risk policy. It is a rebuildable capital view used by F7, not a second cash book. Account, environment, currency and simulation engine are mandatory identity fields. Live observations never fund a paper account implicitly.
Capital partition
CI-01: At a committed portfolio version, reconcile cash_total into free_cash, order_reserves, safety_reserve and other_blocked_cash. All partitions are disjoint. Deployed position basis and marked value are reported separately from cash. Equity equals cash plus declared marked position value, adjusted for liabilities/receivables where the engine contract supports them. Unsupported instruments or accounting states block planning.
CI-02: Deployable capital is bounded by free_cash and the remaining approved risk, concentration, liquidity and horizon-sleeve limits. An unresolved negative balance, stale account state, missing fees, currency mismatch or failed reconciliation blocks new allocations. Policy sleeves constrain one shared ledger; they are not extra balances that can be spent twice.
Illustrative paper ledger
Amount and interpretation
Cash total
10,000 = 2,000 reserved + 1,000 safety reserve + 500 blocked + 6,500 free. Numbers are an accounting example, not allocation policy.
Positions
8,000 historical basis; 8,400 declared marked value. Equity is 18,400 if liabilities/receivables are zero. The 400 mark gain is not available cash.
A committed sale
A 2,000-basis lot sells for 2,160 gross with 10 exit fee. Cash rises by 2,150; position basis falls by 2,000; trading net is 150 if there are no other unallocated costs.
Availability after sale
With other cash partitions unchanged, free cash rises to 8,650 only after the sale/fee commitment and reconciliation. Expected proceeds cannot fund a new reservation.
Update and concurrency contract
CI-03: Recompute on committed fills, cancellations, expiries, deposits/withdrawals if supported, fees, marks and approved policy changes; reconcile periodically through the existing scheduler. Use exact decimal/currency precision, rounding rules and source watermarks. A snapshot has a validity limit and cannot outlive its required evidence.
CI-04: Reservation compares the expected portfolio version and commits capital and risk usage atomically. If concurrent decisions compete for the same cash, at most one can consume the contested capacity. The loser rereads and replans. Duplicate fills, retries and restart must not create or release capital twice.

7 Portfolio planning and selection
R5-D extends the existing F7 constrained selector into a continuously revised portfolio plan. It asks how the current eligible panel, existing positions and cash can best support expected portfolio net dollars over a common comparison window. Fixed risk sizing and all existing vetoes remain binding.
Plan contract
PP-01: A PortfolioPlan stores plan_id/revision, parent revision, account/environment, as_of and expiry, portfolio version, candidate-panel manifest, horizon sleeves, cash retained, proposed actions and order, expected trading/operating net, uncertainty, constraints, rejected alternatives and reason codes. Plan creation is not an order or reservation.
PP-02: Include HOLD_CASH, WAIT, HOLD_POSITION, NEW_ENTRY, REDUCE, EXIT and ROTATE as eligible policy dispositions. ADD is disabled unless an existing approved policy supports it and aggregate risk remains within the original envelope. A daily or periodic profit objective is a planning reference; shortfall cannot increase risk or create a minimum trade count.
Comparison discipline
Compare choices on the same point-in-time capital/risk state and common evaluation window. Use horizon-specific forecasts, realistic execution probabilities, fees, latency, liquidity and cost uncertainty. A short tactical opportunity and a long position thesis need explicit remaining-horizon and terminal-mark assumptions. Unknown comparability produces abstention, not a fabricated score.
Do not multiply ordinal quality or Committee confidence into expected profit. F6 alone owns calibrated statistical forecasts. Insufficient calibration blocks approved allocation; a preregistered research account may evaluate the hypothesis without influencing target paper decisions.
Sleeves and replanning
PP-03: Tactical, swing, position and reserve limits are owner-approved constraints on shared capital. Freeze whether caps are hard limits or soft planning preferences; no automatic borrowing from the safety reserve. Replan on material market changes, position/fee events, thesis expiry or risk changes, using debounce and plan expiry to prevent churn.
PP-04: Record deterministic tie-breaking, candidate eligibility, exclusions and cash comparison. “Best” means best among the observed eligible alternatives under the frozen policy. The plan cannot claim market-wide optimality or assume that future opportunities will appear and be repeatedly reinvestable.

8 Capital rotation
Rotation is a linked sell-and-reallocate decision inside F7/F8. A profitable position need not be held indefinitely, but lower recent performance alone is not sufficient reason to rotate. Compare the remaining expected value of holding with switching at executable current conditions.
Rotation contract
RO-01: Freeze a common-window comparison of HOLD, EXIT_TO_CASH and EXIT_THEN_ENTER. Include exit and entry fees, spread/slippage once, latency, fill uncertainty, residual exposure, risk, new holding time and incremental operating costs. Past acquisition cost informs realized accounting; the rotation comparison uses future economics from now and does not count sunk costs as avoidable.
RO-02: Require a versioned minimum economic advantage, uncertainty treatment, cooldown, turnover budget and expiry before discretionary switching. These numeric inputs remain OPEN until ratified. A cooldown never delays an F11 protective exit.
Step
Required result
PROPOSED
Bind old position/version, target candidate/decision, cash alternative, economics and approval-policy version. No capital is released.
EXIT_PENDING
Revalidate old position and invoke the approved paper exit. Keep unsold exposure protected. Never assume the proposed sell will fill.
PROCEEDSCOMMITTED
Commit actual partial/full fills and costs. Release only reconciled available proceeds. Invalidate the old capital snapshot.
REQUALIFY
Recheck target entry window, forecast, liquidity, F5, F7 and R4 applicable quality rules against the new portfolio version.
ENTER or HOLD_CASH
Atomically reserve affordable capital, then execute through F8. If the target expired or edge disappeared, retain cash. This is an explicit result, not a failed obligation to buy.
Partial failure and replay
RO-03: A rotation group has stable IDs linking both legs and all revisions. A partial exit can fund only policy-allowed affordable entry while counting residual old exposure. Default to waiting where the policy is unspecified. Crash recovery cannot repeat either fill. Cancelled or expired second legs retain their reasons and costs.
RO-04: Measure realized net effect, turnover, time out of market and missed replacement entries against a preregistered feasible hold policy. Hindsight best prices and incompatible simultaneous trades are not rotation benefits. No funded sale or purchase is authorized by this contract.

9 Autonomous paper portfolio management
R5-E manages an entire isolated simulated portfolio, including cash and rejected opportunities, through the existing paper engine. Autonomy means repeatable execution of a human-approved deterministic policy and mandate. Strategy invention, risk expansion, model promotion and funded execution remain outside that autonomy.
Capability
Acceptance requirement
Mandate
Pin paper account, engine/accounting version, initial capital, permitted instruments/sides, size/risk limits, sleeves, execution assumptions and approved policy versions.
Portfolio actions
Demonstrate entries, holds, partial profit taking, approved trailing, price/thesis/time exits, rotations and retained cash. Unsupported actions explicitly abstain.
Execution realism
Preserve executable side-of-spread prices, depth, latency, partial/no fills, fees, cancellations and stop gaps. A price touch is insufficient evidence of a fill.
Protection independence
F11 must continue when the Eye, F6, F7, AI, dashboard or discovery scheduler fails. Reserve writer capacity and prove failure drills before any target cutover.
Persistent ownership
One position/cash history for the chosen engine. Different engines, accounts and accounting versions remain separate populations. Never merge their trades to manufacture performance.
Recovery
Replay committed intents and fills idempotently, preserve first-fill timing, reconcile reserves, and expose incomplete evidence. Writer failure halts new fills/reservations; no uncommitted exit is reported.
Paper proof before target activation
PM-01: Build and evaluate R5-E in a separately identified offline/research paper environment using the existing execution components. This adds no release profile and cannot turn OPIP_PAPER_V2_MODE on in production. Research simulation is labeled as such and does not establish TARGET_PAPER runtime proof.
PM-02: TARGET_PAPER remains a later owner-gated cutover. It requires the intended F3–F7 admission path, mature forecast evidence, independent F11 proof, registered portfolio and signal usefulness, metadata readiness, legacy drain, exact release checks and rollback. No circular dependency is resolved by silently activating the target engine to generate proof.
Future funded operation
Successful paper evidence may support a future governance discussion. It cannot authorize a restricted funded trader or autonomous funded capital manager. Either would require a separate mandate, architecture, controls and explicit human authorization beyond v1.5.0.

10 Canonical records and temporal contracts
Names below describe logical records; implementation should extend existing schemas and ownership after a consumer census. Every durable domain record enters through the canonical writer. No new event store, allocator database, outcome engine or position registry is introduced.
Record and grain
Minimum new lineage
AttentionTriggerOne transition request
instrument/version, trigger ID, policy, source event IDs, prior/new state, event/receipt/availability time, request time, cutoff, watermark, expiry and suppression disposition.
OpportunityThesisOne thesis revision
episode/claim/decision IDs, horizon class, classifier version, entry window, first-fill anchor if any, holding range, price/time invalidators and superseded revision.
CapitalSnapshotOne portfolio version
account/environment/engine, currency, cash partitions, positions, liabilities if supported, marks, sleeve/risk usage, fee state, source watermark, reconciliation and freshness.
PortfolioPlanOne plan revision
candidate-panel manifest, portfolio version, plan expiry, policy/forecast IDs, alternatives including cash, action sequence, expected economics and constraints.
RotationGroupOne linked intent group
old position, replacement thesis, both-leg intent/fill IDs, committed proceeds, requalification decision, residual exposure, terminal state and reasons.
Expectation and OutcomeEntity × policy × horizon × revision
immutable expected payload/hash and cutoff; actual fill/return/duration/cost facts; maturity, coverage, attribution, experiment and correction lineage.
Ordering and replay
DC-01: Preserve (history_epoch, local_sequence) order and separate source event, receipt, availability, evaluation and commit times. Timestamp alone is not an idempotency key. Persist chosen evaluation times, prior/result states, scheduling and gap events so event-driven processing replays without a hidden wall clock.
DC-02: All linked decisions use eligible evidence at their own cutoff and consumed watermark. Later revisions produce later linked assessments. Immutable exports include schema, content hashes and manifest verification; learning cannot read the live SQLite file over the network.
DC-03: Freeze units, precision, rounding, enum mappings, retention, compatibility and additive migration rules before coding each increment. Unknown historical fields stay unknown. Rebuild and reconcile derived views at the same watermark before switching readers; retain source history and rollback compatibility.

11 Expected versus actual learning
R5-F extends F9 and the existing Weakness Finding Registry. Collection starts with R5-A, and expectation schemas must exist before the first evaluated decision; statistical evaluation is the final increment after outcomes mature. Learning does not wait until the end to define what should have been recorded.
Stage
Required evidence
Freeze expectation
Before outcomes: candidate panel, entry/exit and holding policy, horizon, capital, costs, forecast distribution/uncertainty, expected occupancy, evidence cutoff and model/policy versions.
Observe actual
Append fills/no-fills, entry/exit times, realized trading/operating net, drawdown, path excursions, time-to-target, capital occupancy, rotation results and protection interventions.
Measure error
Compare the same units, policy and horizon. Separate classification, forecast, timing, sizing, execution, exit and holding-time errors. Missing original expectations cannot be reconstructed as prospective facts.
Attribute
Record evidence, alternative explanations and contradictions. Use PROVEN, STRONGLY_SUPPORTED, HYPOTHESIS or UNKNOWN for each causal claim; AI narrative alone cannot prove causality.
Test improvement
Link a finding to a falsifiable hypothesis, registered experiment, frozen comparator, cost model, stopping rule and prospective holdout. Preserve accepted, rejected and inconclusive results.
Govern change
A separate human promotion/release decision selects a validated artifact. Record first observed use and post-change economic/recurrence evidence. Learning cannot edit active policy autonomously.
Population and maturity
LE-01: Retain TAKEN, REJECTED, MISSED, EXPIRED, NO_FILL and NO_TRADE populations at a deduplicated episode/intent grain. Outcomes are PENDING, MATURED, CENSORED or INSUFFICIENT_EVIDENCE with explicit reasons. Open POSITION cohorts cannot be declared failures or successes using immature tactical windows.
LE-02: Use separate measures for realized paper outcomes, declared-policy feasible counterfactuals and hindsight MFE/MAE. Compare portfolios with the same capital and timing; do not sum mutually incompatible “missed winners.” Legacy manual DOGE/MANA/AVAX experiences can motivate hypotheses but lack frozen expectations unless contemporaneous records prove otherwise.
LE-03: Evidence of learning means prospective improvement against a frozen baseline after operating costs, with coverage, cohort size and uncertainty. Also measure calibration, weakness recurrence and post-fix economic harm. More stored cases, explanations or trades do not establish improved intelligence.

12 Economics and capital time metrics
Net portfolio economics remains primary. Capital-time measures how productively capital was occupied, while explicit risk, liquidity, uncertainty and horizon limits prevent short-duration noise from dominating selection. Every metric has a versioned definition, units, grain, eligible population, null policy and watermark.
Measure
Definition and interpretation
Trading and operating net
Trading net is realized ledger proceeds less basis and explicit trading costs under the engine contract. Do not deduct embedded spread/slippage twice. Operating net subtracts attributable AI, data, compute and recurring costs; unknown cost stays unknown.
Capital occupancy
Committed capital K(t) equals allocated position capital plus outstanding reserved cash, with no overlap after fills. Capital-hours is the time integral of K(t), expressed in currency-hours. Freeze basis, reserve treatment, partial-fill rules and endpoint policy.
Capital-time efficiency
For a fully resolved cohort: realized trading net divided by its capital-hours. Report operating-net variant separately. Units are return per hour; zero/unknown occupancy yields unavailable. Aggregate by ratio of sums, not average of trade ratios.
Open portfolio comparison
Show realized and unrealized net separately, equity/drawdown under a declared executable mark policy, capital flows and open-horizon maturity. Prevent apparent gains from realizing winners while leaving losses open.
Timeliness
Measure first eligible source availability → trigger → evaluation → qualification → alert and valid delivery. Report lateness, entry expiry, missed coverage and denominators; no future move threshold chosen after observing outcomes.
Horizon quality
Compare predicted holding range and time-to-target with matured/censored outcomes. Any “correct classification” label needs a registered reference rule; the largest hindsight return does not define the true horizon.
Rotation and regret
Matched feasible hold/cash/replacement policies on the same portfolio panel, after costs and latency. Report uncertainty, residual positions, failed second legs and coverage.
Illustrative capital time calculation
A completed paper position occupying $2,000 for 12 hours and realizing $60 trading net uses 24,000 dollar-hours. Its diagnostic efficiency is 0.0025 per hour, or 0.25% per hour. This is not an annualized return or evidence that the opportunity can be repeated. A longer-horizon position may still be preferable under the portfolio objective and risk constraints.

13 Cockpit alerts and inherited precision
Extend the existing eight-page F10 semantic experience. Reuse canonical projections, typed filters, cross-filtering, saved views and evidence drill-down. Keep aggregate/detail populations and watermarks aligned; preaggregate fills and AI attempts before joins to avoid multiplying P&L. No UI or Telegram action changes execution or policy.
Existing page
R5 addition
System Pulse
Observation coverage, queue age, Eye health, reconciliation, F11 heartbeat and exact core/learning release alignment.
Discovery and Regime
Developing-opportunity timeline, trigger reason, horizon, entry-window validity and scheduler-versus-event detection latency.
Decision Trace
Original thesis, horizon changes, candidate panel, cash alternative, F5/F6/F7 evidence and capital/plan versions.
Paper Portfolio
Cash partition, deployed basis/marks, reserves, sleeve usage, plan actions, open thesis clocks, rotation legs and reconciled costs.
Quality and Missed Opportunity
Expected-versus-actual by horizon, capital-hours, late entries, time exits, no-trade and feasible counterfactual cohorts.
Committee Value and AI Cost
Retain trust stages, role disagreement, timely coverage, paired incremental net and all attempt costs; opinions remain advisory.
Learning and Releases
Weakness → hypothesis → experiment → result → human-approved artifact → observed use → post-change measurement.
Incidents and Infrastructure
Gaps, restart/recovery, writer pressure, unsupported positions, incomplete labels, halted admissions and human resumption state.
R4 presentation contract
UI-01: Preserve canonical action/disposition, dedupe, cooldown, alert budget, retry and final-delivery evidence. Add horizon, expected holding range, time invalidation, capital priority and a plan/decision trace link. Revalidate actionability at emission; stale or expired entry cards cannot appear actionable. Confidence is shown only with supported calibration and semantics.
UI-02: AI review state and stance remain separate optional metadata. Missing AI is never favorable analysis; its absence does not create a new inline approval or veto. Existing-position protection and committed paper state never depend on successful Telegram delivery.
Requirements carried from version 1 4 4
Preserve continuous read-only account guardian coverage, mandatory/optional evidence policy, contradiction checks, selective budgeted Committee analysis, causal-confidence records and prospective operator usefulness. PA-01–PA-20 and SQ-01–SQ-10 remain applicable; R5 scenarios supplement them. Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states. [S2]

14 Delivery sequence and roadmap continuity
R5 now means Continuous Multi-Horizon Capital Intelligence. The earlier repository roadmap used R5 for outcome consolidation and the dashboard. Preserve that older milestone as R5-LEGACY-OUTCOMES-COCKPIT in tracking; its F9 consolidation work is a prerequisite for trustworthy R5-C/E/F, and its F10 work accompanies each evidence increment. R6 Committee shadow proof and R7 governed retirement remain separate retained obligations. [S4]
Order
Increment and priority
Exit evidence
0
R4 closure • P0
Assess the existing real EVIDENCE_SHADOW alert-quality evidence against its frozen contract. Status here: not verified. Do not mix R5 behavior into that cohort.
1
R5-0 contracts • P0
Pin sources and clause map; name owners; freeze records, horizon policy, accounting, performance budgets and ATDD scope. No implementation or activation follows from publication alone.
2
R5-A Market Eye • P1
Event/state-driven attention, coverage and replay under bounded load, same F2/F3 path, zero trading authority, F11 non-interference.
3
R5-B Horizons • P1
One episode/thesis identity with declared horizon, validity and immutable first-fill timing; supported evidence/calibration scope.
4
R5-C Capital Intelligence • P1
Reconciled capital views, sleeves and occupancy; atomic reservation concurrency; frozen economics and expectation records.
5
R5-D Portfolio Planning • P1
Matched hold/cash/new-entry/rotation comparisons, realistic two-leg handling, anti-churn policy and requalification.
6
R5-E Autonomous Paper • P1
Isolated portfolio-level research proof of entries, exits, reserves, recovery and independent protection using the existing paper engine.
7
R5-F Horizon Learning • P1
Mature prospective cohorts, calibration and economic improvement versus frozen baseline/cash, full population and cost accounting.
8
TARGET_PAPER decision • gated
All inherited and new readiness gates pass, with separate exact-release owner approval, legacy drain and rollback. No target activation in this task.
Priority labels express dependency and importance, not permission. P0 protects/finalizes prerequisites; P1 is the ordered R5 build; P2 covers supporting extensions after their canonical inputs exist. Instrumentation and outcome-schema work start early even though the final learning verdict comes last.

15 Existing feature change map
KEEP preserves responsibility and contract; it does not claim completion. EXTEND adds requirements within the existing owner. REVISIT calls for explicit reconciliation or migration before proceeding. NEW identifies a new logical capability using the existing platform. These labels are architectural disposition, not current production status.
Feature
Disposition
R5 change and priority
F1 Observation
EXTEND
P1 / A: continuous coverage, material event triggers and retained availability evidence.
F2 Feature Bus
EXTEND
P1 / A: incremental attention inputs and reproducible scheduling; preserve budgets and snapshots.
F3 Detector
KEEP
P1 / B: retain pure IGNITION evaluation; separately gate unsupported horizon research.
F4 Lifecycle
EXTEND
P1 / B: horizon thesis, validity, time invalidation and one episode lineage.
F5 Feasibility
EXTEND
P1 / B–C: horizon/notional-aware evidence, liquidity and capacity; existing veto authority unchanged.
F6 Forecast
EXTEND
P1 / B–C: calibrated horizon/time outcomes and uncertainty; abstain without evidence.
F7 Selector
EXTEND
P1 / C–D: capital-aware portfolio plan, cash comparison, reservation and rotation.
F8 Paper execution
EXTEND
P1 / E: deterministic portfolio management using the existing isolated engine.
F9 Outcomes and learning
EXTEND
P0 schema / P1 F: canonical expectations and labels, capital-time and horizon evaluation.
F10 Dashboard
EXTEND
P2 alongside A–F: same eight pages and semantic model; expose plans, capital and horizons.
F11 Safety
KEEP
P0 throughout: independent protection, fail-closed admission and human resumption; re-prove under new load.
F12 AI research
KEEP
P2 retained R6: advisory-only Committee, governed registry, quantitative trust and cost evidence.
Tracking evidence without false completion
For each row retain ARCH_COVERAGE, CODE_COMPLETE, TARGET_WIRED, RUNTIME_ACTIVE, PROD_PROVEN and USER_VALUE_PROVEN separately, with date, exact SHA/configuration, evidence link and owner. v1.5 records architecture requirements; the other dimensions are NOT_ASSESSED in this task. Historical conformance rows cannot be promoted to present-day completion by copying them. The earlier 25–35% change estimate is discussion-level judgment, not a measured completion or code-churn figure.

16 New capabilities and work to revisit
ID and order
Disposition
Deliverable and closure
R5-0 / 1
NEW
Architecture/ATDD contract: owners, numeric policy inputs, source adoption and bounded editable scope.
EYE / 2
NEW
Continuous Market Eye; new attention responsibility within F1/F2/F4. Prove latency, coverage, capacity and zero authority.
HORIZON / 3
NEW
Horizon classifier and thesis contract attached to F4. Prove immutable timing, class uncertainty and no accidental strategy proliferation.
CAPITAL / 4
NEW
Reconciled capital and capital-time view within existing F7/F8 ownership. Prove disjoint balances and atomic consumption.
ROTATE / 5
NEW
Portfolio plan/rotation contract inside F7/F8. Prove switching advantage, actual proceeds and requalification.
SCHED / 2
REVISIT
Shift scheduler to reconciliation/fallback after event-path proof. Preserve necessary jobs until mapped consumers and recovery parity exist.
OVERLAP / 1 and 8
REVISIT
Census lifecycle clocks, outcome writers and legacy paper engines. Carry old R5 consolidation into current F9/F10; retain R7 retirement gates.
CUTOVER / 8
REVISIT
Place R5 portfolio/learning proof before TARGET_PAPER. Preserve all existing R4, F11, release and legacy-drain prerequisites.
Tracker use
The companion Markdown and CSV contain 22 rows with stable IDs, category, priority, order, dependency, status and closure evidence. Assigned owners are UNASSIGNED until named by the operator. The CSV also has separate code, wiring, runtime, production and user-value fields; none are inferred from the architecture category.
What has changed in this revision
Discovery becomes driven by recorded market events and state, with a retained reconciliation schedule. Decisions gain explicit time horizons. Capital and existing positions become first-class selection inputs. Paper execution gains a portfolio mandate. Learning evaluates prediction, action and capital occupancy together. Single-writer ownership, F11 independence, advisory AI, paper isolation and owner-controlled releases continue unchanged.

17 Acceptance scenarios for R5
The proposed suite ID is ATDD-OPIP-R5-CMCI-v1. These are requirements to implement and run within separately scoped increments; no runtime tests were executed for this documentation task. Existing PA/SQ, canonical, paper, F11 and release suites remain required.
ID
Given and when
Required result
AC-01 / A
Duplicate triggers and scheduler requests target the same instrument/state.
One idempotent evaluation identity; all suppression and source evidence retained.
AC-02 / A
Burst discovery saturates capacity while protection and reconciliation are due.
F11 and reconciliation meet frozen budgets; discovery sheds/degrades visibly.
AC-03 / A
Restart, sequence gaps or late corrections affect observation state.
Replay matches retained snapshots; warm-up/gaps block favorable decisions; no hindsight insertion.
AC-04 / B
A tactical thesis times out and a later position thesis is suggested.
Original clock and outcome remain; new linked decision must pass all gates before reclassification.
AC-05 / B
A horizon lacks evidence, calibration or supported detector semantics.
UNKNOWN/research-only disposition; no approved allocation or invented confidence.
AC-06 / C
Two candidates concurrently reserve the same remaining capital.
Versioned transaction admits only affordable usage; retry cannot double spend.
AC-07 / C
Partial fills, fees, cancellation and duplicate delivery alter balances.
Cash/position/reserve partitions reconcile exactly under declared precision; occupancy counted once.
AC-08 / D
Replacement expected gross gain is higher but switching costs remove its advantage.
HOLD or cash wins; no forced rotation from gross upside or profit target.
AC-09 / D
Rotation exit partially fills or replacement expires after cash is released.
Residual protected; only committed proceeds usable; requalification may retain cash.
AC-10 / E
Process crashes between intents/fills and Eye/AI/forecast becomes unavailable.
Recovery is idempotent; F11 operates independently; no fabricated fills or capital.
AC-11 / F
Expectations are missing, long horizons immature, or outcome coverage incomplete.
Explicit unknown/pending/censored state; no retrospective expectations or clean win/loss label.
AC-12 / gates
Dashboard, Telegram or AI submits action-like content, or stale configuration requests target mode.
No authority change; existing release and deterministic gates reject unauthorized actions.

18 Readiness gates migration and rollback
Every gate returns PASS, FAIL or INSUFFICIENT_EVIDENCE with a dated evidence manifest. An unratified numeric threshold, missing population, stale required stream or unknown cost cannot yield PASS. Thresholds must be frozen before evaluating results; changing them starts a new evaluation version.
Gate
Evidence required before target cutover
G1 Integrity
One owner per fact, point-in-time lineage, complete populations, canonical idempotency, deterministic replay and reconciled derived views.
G2 Timeliness
Eligible event detection and actionable-at-alert evidence versus frozen scheduler/baseline, with coverage, expiry and latency distributions.
G3 Economic usefulness
Matched target, legacy and cash portfolios; trading and operating net, risk/drawdown, realistic fills, capital-time and uncertainty across matured horizon cohorts.
G4 Operator inspection
Usable decision/plan/rotation trace, explicit horizon and invalidation, cash accounting, alternatives and expected-versus-actual evidence.
G5 Protection and runtime
F11 independent failure drills, capacity isolation, target F3–F7 path, calibrated forecasts, account/notional evidence and exact release compatibility.
G6 Governance and cutover
Named owner acceptance plus separate exact-release authorization; intended single paper authority, legacy drain, metadata readiness, backup/restore and rollback evidence.
Migration controls
MG-01: Inventory producers, consumers, identities, writers and historical semantics before migration. Use additive schema changes and explicit engine/accounting-version cutovers. Preserve legacy open positions, pending orders, costs and outcome clocks until reconciled drain; never transfer them by inference.
MG-02: Stop a legacy writer only after its replacement is proven, consumers have moved, the archive is verified and the stop time is recorded. Deletion is a later owner decision. The historical 30-day migration checkpoint and bounded retirement requirements remain binding where applicable; they never force statistical promotion.
Rollback and halt
MG-03: Define an exact last-approved release/configuration, compatible data reader and rollback trigger set before activation. On integrity, capital or protection failure, suspend new admissions and preserve open obligations. Restore one approved authority; do not run two allocators, delete evidence or treat suspect history as trustworthy. Human resumption and existing rollback controls remain required.
MG-04: R4 validation remains scoped to its existing contract. This document performs no deploy, shadow activation, timer change, learning-worker rollout, production probe, commit, push or merge. It supplies the architecture and tracking artifacts for the next decision.

19 Sources clause mapping and open inputs
Source identities were checked during document preparation. Main was resolved through the GitHub connector; source documents were read at that revision. Older status observations remain dated history. The source DOCX files were not edited, moved or replaced.
Source
Identity and use
S1 Authority
Owner file OPIP_Profit_Intelligence_Architecture_v1_4_3.docx; body dated 22 September 2026; 84,643 bytes. SHA256: ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83.
S2 Amendment
Owner file OPIP_Profit_Intelligence_Architecture_v1_4_4.docx; 4 October 2026; 70,601 bytes. SHA256: 9eb48784b540611e2eb538866365d506b1f0e07207ef3ea33502b796f8057768.
S3 Governance/status
AGENTS.md and docs/architecture/CURRENT_ARCHITECTURE_STATUS.md at repository commit 8b3cc2712432ca21007be4db0667301d48b89d97. The status file still pins v1.4.3.
S4 Roadmap
docs/architecture/OPIP_RECOVERY_ROADMAP.md at the same commit. Earlier R5 outcomes/cockpit, R6 Committee and R7 retirement obligations preserved.
S5 Conformance
docs/architecture/OPIP_CONFORMANCE_LEDGER.md at the same commit. Reports a 3 October baseline; used for component mapping only, not new runtime or completion certification.
S6 Discussion
O’Pip Deploy Watch, conversation 6ac5ca3a-1084-83ea-a126-0839f5fe8bf0, retrieved for this task. User direction and R5 sequence are design context; prior assistant claims are not execution proof.
Pinned source links: governance • architecture status • roadmap • conformance
Explicit amendment map
v1.4.3 §§2–4 and Appendix A §§7–11 → v1.5 §§2–10: event attention, horizon metadata and capital planning extend existing owners; default evaluation-grid semantics remain. v1.4.3 §§7–12 and 20–21 plus v1.4.4 §§6–9 → v1.5 §§11–13: horizon/capital-time learning and cockpit extensions. v1.4.3 §13, v1.4.4 §§13–15 and the recovery roadmap → v1.5 §§14–18: revised R5 order with retained gates. Unlisted clauses remain unchanged; conflicting weaker authority language never overrides §1.
Inputs still to freeze
Name Business Decision Owner and Technical Release Owner. Ratify workload/freshness/latency limits; exact horizon and time-exit policy; capital sleeves and risk limits; rotation advantage, uncertainty, cooldown and turnover policy; forecast/sample/maturity/effect thresholds; cost allocation and occupancy basis; schema/enum mappings; migration dates and rollback acceptance. These block the affected implementation or activation gate, not delivery of this architecture revision.
Document status: COMPLETE AS A VERSIONED DESIGN REVISION. Repository adoption, executable ATDD evidence, runtime conformance and user-value proof are separate records. No production or funded authority changes are made.