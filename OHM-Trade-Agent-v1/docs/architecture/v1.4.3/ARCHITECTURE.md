# O’Pip Profit Intelligence Platform Architecture

This file is a paragraph extraction of the owner-supplied DOCX stored beside it. The DOCX is the architecture authority. If this extraction and the DOCX disagree, the DOCX controls. Source tables are preserved as sequential lines in document order. This extraction does not redesign the architecture.

O’Pip Profit Intelligence Platform Architecture
Version 1.4.3
Authoritative architecture package • 22 September 2026
This package unifies the evidence, decision, paper execution, learning and observability design. It extends the supplied Final Architecture Baseline v1.2 with the agreed v1.3 direction, the v1.4 interactive dashboard contracts, the v1.4.1 AI Model Registry, the v1.4.2 decision-first alert contract and evaluation protocol, and the v1.4.3 Intelligence Committee paper-trade monitoring, weakness-learning registry and quantitative-trust contract. Its authority is architectural: it does not certify deployed capabilities, approve a release, or grant funded/live authority.
Executive summary
The objective remains sustainable realized net profit after all costs, subject to capital, concentration, liquidity, drawdown, data quality, execution realism and reliability constraints. Trading economics and operating economics remain separate. Paper results provide evidence; this architecture makes no profitability claim.
Deterministic and statistical O’Pip components retain all runtime authority. The Intelligence Committee receives an asynchronous copy of canonical evidence and produces structured advisory opinions. It cannot control entry, exit, sizing, risk limits, probabilities or promotion. Its contribution is tested through preregistered shadow comparisons, realistic execution assumptions and full cost attribution.
The dashboard is interactive, filterable and drillable. A versioned semantic model supplies consistent metrics, cross-filters, saved views and complete decision traces. Read-only means no trading authority. Every view exposes freshness, coverage, population and version context so attractive charts cannot hide missing or adverse evidence.
Implementation remains incremental. Protect PR #237’s Feature Bus scope; build read models and operational visibility separately, then add bounded Committee experiments. Human approval remains necessary for promotion, release and resumption. Funded trading is outside this package.
Reading guide
Sections 1–4 define authority and the inherited foundation. Sections 5–8 define Decision Intelligence, Committee measurement and learning. Sections 9–12 specify the analytical model and dashboard. Sections 13–15 define delivery gates, changes and source traceability. Sections 16–18 define the provisional AI Model Registry, bake-off and activation decision. Section 19 defines the decision-first alert contract. Sections 20–21 define Committee paper-trade monitoring, weakness learning, quantitative trust and the mandatory dashboard measurements. Appendix A retains the full v1.2 source content for clause-level reference.

1 Authority and implementation evidence
Normative words such as must and forbidden specify the target design. v1.4.3 supersedes v1.2 on unified Decision Intelligence, asynchronous advisory analysis, semantic read models, dashboard interaction and AI model selection governance. Unchanged v1.2 constraints remain binding. Where a diagram or earlier conversation implies an inline Committee gate, this text and the explicit parallel path in section 2 control.
Evidence class
What this package can establish
Supplied v1.2 baseline
Reviewed in full, including feature, storage, fidelity, disposition and PR sequence tables. It establishes design requirements, not proof of deployed behavior.
Agreed v1.3 direction
Recovered from Casual Greeting. Integrated platform, structured Committee roles, asynchronous shadow mode, measurable value and bounded routing are adopted.
Current work context
The request identifies PR #237 as current work. Prior discussion associates it with PR3 Feature Bus foundations. No commit, diff, deployment or live database was audited for this package.
Verified built state
Not established here. Existing-node, router and component statements in v1.2 are source-reported context. Availability, adequacy and conformance require pinned repository and runtime evidence.
v1.4.1 additions
All new schemas, panels, experiment paths, model routes and service contracts below are architectural targets until implementation acceptance records prove otherwise.
Release authority remains separate
The original baseline authorized PR1 documentation and contracts only; it did not authorize PR2 onward. This package neither revokes separately recorded approvals nor invents them. Before each implementation or cutover, record the applicable approval, pinned source commit, named Business Decision Owner and Technical Release Owner, and the relevant acceptance evidence.
Nonnegotiable boundary
Paper execution must be technically isolated from funded order endpoints and funded credentials. No Committee output, dashboard action, experiment result or document approval grants funded trading authority. A future change to that boundary would require a separate architecture and authorization process.

2 Unified architecture and ownership
Plane
Flow and authority
Evidence and state
Approved market inputs → validated observations → retained aggregates and checkpoints → FeatureSnapshot → deterministic detector and opportunity lifecycle.
Runtime decision path
Feasibility and calibrated statistical forecasts → economic selector → deterministic risk checks and atomic capital reservation → realistic paper execution. Only approved deterministic or statistical artifacts participate.
Advisory path
Committed candidate/evaluation evidence → bounded asynchronous work → Committee opinions → canonical advisory records → attribution and research. There is no arrow back to runtime selection or protection.
Learning
Verified exports → outcomes and calibration → observations and hypotheses → registered experiments → sealed prospective evaluation → human-governed release of an approved artifact.
Observability
Canonical history → rebuildable read projections → versioned semantic queries → interactive dashboard and evidence trace. No trading commands.
Safety
Independent protection → priority canonical intents. Safety can suspend automatically; resumption requires human approval. Protection does not depend on discovery, forecasts, economics or AI.
These are module and process boundaries in a small modular application. Keep the existing two-node topology described by v1.2, subject to measurement. The learning node reads verified exports, never the live SQLite file over the network. Committee work belongs on isolated learning capacity; any local worker must have bounded CPU, memory and queue budgets demonstrated not to impair protection.
One writer and one history
The single canonical writer commits operational events and projections in local SQLite WAL. Other components submit typed intents; they do not open alternate domain write paths. Research outputs return through validated import intents with stable identities, artifact hashes and provenance. Derived exports and analysis caches are reproducible projections, not competing evidence histories.
Dashboard saved-view metadata may live in UI configuration storage because it is user preference data, not a trading fact. It cannot become a channel for policy changes. Operational domain changes remain exclusively behind the canonical writer and release controls.

3 Canonical evidence and point in time correctness
Canonical order is (history_epoch, local_sequence). Retain event time, source sequence, receipt time, ingestion order and commit time separately. Restore from an older off-host snapshot creates a declared new epoch. Timestamp alone is not an idempotency key. Acknowledge intents only after durable commit; retry unacknowledged operations with stable keys.
Every event includes schema_version and provenance. Corrections append superseding records. Deterministic read-time conversion supports old schemas; absent fields remain unknown. Rebuild projections at an explicit watermark and compare their output with the original projection before switching readers.
Knowledge cutoff contract
Each snapshot and research input identifies evaluation_time, evidence_cutoff, consumed_input_watermark, feature_version, source availability times and a content hash. Evidence is eligible only if it was available by the cutoff, not merely dated before it. A later correction or backfilled news item cannot silently enter a historical decision input.
Committee requests freeze the same eligible snapshot available to the baseline at candidate time. New information may support a separately labeled later assessment but cannot revise the original opinion. Outcome labels mature after the declared horizon and coverage checks. Learning artifacts carry training cutoff, dataset manifest, label policy and effective version; future outcomes cannot enter earlier training or inference.
Feature Bus continuity
Retain fixed-interval aggregates sufficient for declared IGNITION features, versioned rolling-state checkpoints and every actual detector-evaluation snapshot, including no-claim evaluations. Raw trades remain bounded ephemeral inputs unless a declared replay requirement justifies retention. Tick-order features are excluded when tick order is not retained.
Use the declared evaluation grid. RESTART_WARMUP, INSUFFICIENT_HISTORY and NEW_LISTING_COLD_START are distinct states. Warm-up follows the longest required window plus estimator stability. Persist gap, reset and restart scheduling events. Feature recomputation from checkpoints and aggregate deltas differs from exact detector replay from stored snapshots.
Commit and failure isolation
Protection and execution receive reserved queue capacity and higher priority. Transactions contain no AI, network calls, feature computation or export transfers. Writer failure halts reservations and simulated fills; independent protection may detect and alert but cannot claim an uncommitted exit. Export objects are verified before committing manifests. Backups use consistent SQLite snapshots.

4 Preserved runtime and paper economics
IGNITION remains the sole active detector family. Evaluation is a pure function of FeatureSnapshot, prior DetectorState and explicit evaluation_time; it performs no I/O and reads no hidden clock or global mutable state. The detector owns transition semantics while the runtime persists them. Version hysteresis and debounce policy. Material gaps reset persistence evidence; expired claims require new evaluation. Deferred opportunities have bounded deadlines and explicit terminal reasons.
Missing evidence is never favorable. Feasibility can abstain with INSUFFICIENT_EVIDENCE. Insufficient calibration blocks approved allocation but need not block preregistered simulation in a separate research account. Forecast execution probabilities separately from conditional post-fill paths; the Committee cannot create or adjust either probability.
Paper execution contract
Represent NO_FILL, PARTIAL_FILL and FULL_FILL separately from TARGET, STOP, TIMEOUT and independently triggered RISK_EXIT. Anchor the policy horizon to first fill; later fills do not restart it. Residual orders have their own expiry. Include side-of-spread executable prices, size-sensitive depth, latency, partial fills, fees, cancellations and stop gaps. A limit touch alone is insufficient fill evidence.
A fully observed horizon expiry is TIMEOUT under the declared executable exit policy. A gap before resolution is INCOMPLETE_COVERAGE. For binary target-within-horizon labels, an observed timeout is target=0 without implying a negative return. Within-bar target/stop ambiguity cannot receive an exact clean label. Feed recovery uses first valid executable evidence under approved recovery policy and records the full gap effect.
Fidelity
Required treatment
A
Complete required feed/book coverage and supported simulation; eligible for primary paper evaluation subject to model limitations.
B
Conservative aggregate reconstruction with declared ambiguity; research and sensitivity analysis.
C
Material feed/protection gap or unknown path; incident and incomplete-outcome reporting, excluded from clean calibration but retained in full-population reports.
Portfolio objective
Optimize expected portfolio net dollars over a common evaluation window against cash/no-trade and a frozen comparator. Enforce capital, concentration/common-shock, liquidity, capacity, data quality, drawdown and loss limits. Reserve atomically against a portfolio version; release on cancellation or expiry and adjust on fills. Keep capital-hours, hit rate and profit factor as diagnostics. No Kelly sizing, leverage, covariance optimizer or dynamic risk parity is introduced.
Trading net P&L equals simulated cash economics after fees and explicit charges. Spread and slippage embedded in fills are not deducted again. Operating net equals trading net minus attributable compute, storage, data, AI and recurring costs. Do not count no-fill intents as losing trades or omit them from the intent population.

5 Decision Intelligence contract
Decision Intelligence is a shared evidence and comparison boundary, not a new authority. The baseline selection record and Committee assessment remain separate typed records linked by decision_context_id. A Committee recommendation is an advisory classification, not an executable order or veto. Even a deterministic adapter is forbidden from translating current Committee text or scores into runtime actions.
Record
Minimum target fields
DecisionContext
context_id, candidate_id, episode_id, evaluation_id, instrument_version, snapshot_id/hash, cutoff, watermark, policy/detector/forecast versions, candidate-set and portfolio-version references, environment, eligibility and missingness.
BaselineDecision
decision_id, context_id, decision time, disposition and reasons, forecast_id, feasibility evidence, reservation/intent references where applicable. Capture actual decision evidence; never reconstruct rationale with an LLM.
CommitteeRequest
request_id, context_id, experiment_id, cohort/selection rule, snapshot hash, route/prompt/role versions, deadline, budget reservation, enqueue time and idempotency key.
CommitteeAssessment
assessment_id, request_id, role outputs, synthesis, advisory stance, score schema, evidence references, unsupported claims, disagreement, completeness, result status, completion/commit times and model invocation references.
ComparisonRecord
comparison_id, baseline decision and assessment IDs, experiment/variant version, timeliness eligibility, common outcome horizon, attribution method, simulated-policy references, costs and uncertainty.
Delivery semantics
Schedule from durable committed evidence using an idempotent cursor over canonical history. At-least-once delivery is acceptable; deduplicate by request identity, role, route/prompt version and attempt. A local queue or spool is transport only. Record eligible, selected, skipped-budget, skipped-capacity, expired, failed, invalid and completed states so evaluation cannot silently select successful calls.
A late assessment is retained as late advisory evidence and is ineligible for an earlier feasible decision comparison. A request without a committed valid result remains pending or failed, never implicitly successful. Multiple attempts retain all costs and timestamps; use a preregistered deterministic result-selection rule rather than choosing the best hindsight answer.

6 Committee roles and bounded execution
Role
Output responsibility
Regime analyst
Explain stored movement, transition and extension evidence; identify missing or contradictory features.
Liquidity and structure analyst
Assess retained spread, depth and crypto structure evidence. Funding, OI and liquidation data are optional context only when point-in-time provenance exists; this adds no derivatives execution.
Event and sentiment analyst
Optional extraction of retained source evidence with publication and availability times. Unsupported or absent sources are unknown, never positive evidence.
Bull and Bear advocates
Independent continuation and failure theses from the same frozen evidence. One bounded rebuttal round may be registered; no open-ended debate.
Risk Critic
Independent advisory critique of liquidity, extension, stale evidence, execution uncertainty and downside. It has no risk veto or parameter authority.
Decision Synthesizer
Summarize supported agreement and disagreement, advisory SUPPORT / OPPOSE / WATCH / ABSTAIN, cited evidence and unresolved uncertainty. It cannot authorize a trade.
Each role returns a typed bounded payload: role/version, stance, thesis, risks, evidence_refs, missing_evidence, rubric score, self-reported confidence and status. Scores use a versioned 0–100 rubric; they are ordinal advisory scores, not calibrated trading probabilities. Bull, Bear and Risk scores remain distinct. Null means unavailable, never zero risk. Reject extra action fields and invalid evidence references.
Routing and budgets
Reuse a provider-neutral router where verified suitable. Define an allowlisted task-to-model route with primary and at most one approved fallback within the same total deadline and monetary/token budget. No automatic expensive escalation. Cheap routes handle extraction; stronger routes require predeclared task eligibility. OpenRouter remains optional. Model names and prices are release configuration, not architectural constants.
Before dispatch, reserve a worst-case bounded request budget across roles, retries and synthesis. Enforce per-request and daily caps, per-role token ceilings, maximum concurrency and queue age. Record input/output/cached tokens, actual or estimated billed cost, price-version, currency, provider, model version, route, attempt and reconciliation status. Unknown cost is flagged and cannot silently satisfy a spending gate.
Failure behavior
The runtime never waits for AI. Timeouts, rate limits, provider failure, invalid schemas, prompt injection or budget exhaustion yield explicit degraded/abstain states. Optional-role omission must follow a registered completeness policy. Circuit breakers stop advisory dispatch; baseline trading and independent protection continue under their own health rules. Do not feed credentials or executable tools to agents; external text is evidence, never instructions. Numeric capacity and deadline values must be ratified and measured before enabling the worker.

7 Incremental value and experiment design
Committee disagreement alone is not incremental profit. Preregister a frozen baseline, cash/no-trade comparator and a deterministic research-only mapping from advisory stance to an experimental policy. This mapping may operate only inside an isolated shadow simulator with a separate research account identifier and no route to runtime intents. It must retain the baseline risk envelope and realistic execution constraints.
Run matched portfolio comparisons over the same eligible candidate panel, capital, time window, execution model and fee policy. Account for mutually exclusive trades and capital occupancy; summing the best per-candidate counterfactual returns is invalid. Include baseline rejects, no-fills, late opinions, failures and coverage gaps. If budget sampling is used, freeze the rule and record inclusion probability; report coverage and avoid whole-population claims from a selected subset.
Two distinct timing questions
Same-cutoff analysis tests reasoning on equal information. Deployment-feasible analysis starts the shadow action no earlier than the recorded valid assessment availability time and includes execution latency. An opinion received after the opportunity expires cannot claim an earlier avoided loss or captured gain. Historical LLM reruns may contain training leakage and are exploratory only; sealed prospective evaluation is required for promotion evidence.
Measure
Definition and interpretation
Incremental trading value
Shadow portfolio trading net minus frozen baseline trading net over the common window, under matched capital and executable evidence.
Incremental operating value
Incremental trading value minus incremental AI and other attributable operating costs. Report gross benefits and cost allocation separately.
Avoided loss and missed gain
Model-based paired counterfactual differences under registered action and timing rules; labeled simulated, with coverage and uncertainty. Never substitute MFE for an achievable return.
Decision diagnostics
Disagreement, late-extension avoidance, false positives, missed opportunities and timeliness, with explicit eligible denominators and label policy.
Evidence quality
Complete/late/failed/skipped counts, A/B/C mix, unresolved outcomes, missingness, cost completeness and provider/version breakdown.
Specify meaningful effect, precision/power plan, endpoints, experiment family, stopping rules and holdout before observation. Use dependence-aware contiguous time-block resampling of the contemporaneous candidate/portfolio panel. Set block length and multiple-testing procedure from the experiment; v1 small-family screening defaults to valid family-wise control. Do not invent a universal sample threshold or guarantee success after 30–60 days. Inconclusive evidence remains inconclusive.

8 Learning and human governed promotion
The learning lifecycle is observation → hypothesis → registered experiment → sealed prospective evaluation → accepted, rejected or inconclusive research conclusion → separate human release decision. Committee reflections generate hypotheses only. They cannot edit policy, prompts used by a frozen experiment, probabilities, risk configuration, deployment or promotion state.
Every hypothesis links to canonical decisions, assessments and outcomes and states the suspected mechanism, eligible cohort, falsifiable prediction and expected economic effect. An experiment freezes dataset manifests, knowledge cutoffs, policy/route/prompt versions, endpoints, test family and stopping rule. Corrections create a new version and disclose whether the sealed evaluation was compromised.
Promotion evidence
Require an artifact hash, reproducible build and replay evidence; coverage and fidelity disclosure over the full intent population; effect and uncertainty against baseline and cash; sensitivity to costs, latency and adverse missingness; calibration reliability and proper scores for statistical forecasts; isolation and rollback tests; and named human approval with scope and effective time. An accepted research finding is not a deployed change.
Permitted promotion is an approved deterministic/statistical artifact or bounded advisory configuration through ordinary release governance. Any proposed artifact must demonstrate that no live LLM output controls runtime entry, exit, size, risk, probability or promotion. The Committee itself retains zero runtime authority even if its research has been useful.
Learning observability
Track hypothesis state, experiment status, dataset maturity, sealed evaluation state, rejected findings and reasons, approved release hash, effective policy version and first observed runtime use. A dashboard may show the chain from hypothesis to measured post-release result. It must display “not verified applied” when deployment evidence is absent. Avoid an opaque composite Intelligence Score; show named economic, calibration, timeliness and evidence diagnostics.
Suspension and retirement
Automatically suspend unsafe admissions under deterministic policy; preserve open-position protection. Resumption and promotion require explicit human approval. Retire Committee routes or roles when preregistered evidence fails the economic/reliability gate; preserve their costs and outcomes in history. Research does not justify permanent extra infrastructure or an endless parallel allocator.

9 Analytical facts and join contracts
The following logical facts are rebuildable tables or streams projected from canonical records. Storage implementation may differ, but grain, keys and semantic meaning are versioned. Each row carries source event identity, schema/projection version, source watermark and environment. Never join raw one-to-many facts directly and then sum duplicated trade P&L.
Fact
Grain and primary linkage
candidate_events
One candidate lifecycle event; candidate_event_id → candidate_id, episode_id and evaluation_id. Use distinct candidate IDs for candidate counts.
detector_evaluations
One detector/instrument evaluation, including no claim; evaluation_id → snapshot_id, detector version, prior/result state and reasons.
decisions
One baseline decision attempt; decision_id → context, candidate set, forecast, portfolio version, disposition and reservation.
committee_evaluations
One request role result or synthesis, identified by assessment_id and role/attempt. Expose a separate one-row-per-request summary to avoid role fanout.
paper_trades and paper_fills
One position lifecycle per trade_id; one execution fill per fill_id. Intents, cancellations and no-fills remain separately enumerable by intent_id.
outcomes
One entity × label-policy × horizon × revision; links to intent/trade/episode as declared. A semantic view selects one eligible revision as of its cutoff.
learning_events
One hypothesis, experiment or promotion transition; event_id links registry IDs and release artifact evidence.
incidents
One incident transition; incident_id plus transition_id. Affected-entity bridge supports many-to-many coverage without inflating counts.
ai_usage
One provider invocation attempt; invocation_id → request, role, model, tokens, timing, cost and billing status.
attribution and portfolio_marks
One registered comparison × evaluation window × variant; portfolio marks at declared times, supporting common-window P&L and drawdown.
Join evaluations → candidates → decisions through explicit identity bridges; join a trade only via its reservation/intent lineage. Use left joins for optional Committee, trade and outcome data so missing children remain visible. Aggregate fills to trade grain and AI attempts to request grain before combining. Bridge filters use EXISTS/semijoins rather than multiplying rows. All cardinality assumptions have reconciliation tests.

10 Dimensions and semantic metrics
Version dimensions for time/session/timezone, venue/instrument identity, regime and phase, candidate/opportunity/decision/outcome status, rejection/exit reason, strategy and policy, detector/feature/forecast, model/provider/route/prompt, experiment/hypothesis, environment/research account, fidelity and data quality. Instruments and regimes use effective intervals and known-at timestamps where corrections are possible. Historical joins select the version available at the evaluation cutoff, not today’s latest attributes.
Metric
Semantic contract
Funnel conversion
Distinct entities at successive declared lifecycle stages within a fixed candidate cohort. Display denominator, cohort time basis and as-of watermark; event counts are a separate metric.
Realized trading net
Closed-position realized cash result after fees/explicit charges from the canonical paper ledger. Open unrealized P&L is separate and uses the declared executable mark policy.
Operating net
Trading net less allocated compute/storage/data/AI/recurring costs for the same window. Show allocation version and unallocated costs.
MFE and MAE
Maximum favorable/adverse marked excursion from the declared fill basis during the observed holding horizon, in dollars and/or percentage with explicit sign convention. Path diagnostics only; incomplete paths flagged.
Win rate and target hit
Positive-net closed trades / eligible closed trades; target-hit labels / fully observed eligible labels. Show zero denominator as unavailable, not zero.
Drawdown
Peak-to-trough decline of portfolio equity including open positions under the declared mark policy; disclose capital flows and coverage gaps.
Forecast quality
Proper scoring rules and reliability by execution event and conditional post-fill outcome, with sample counts and uncertainty. LLM confidence is not forecast probability.
Committee comparison
Registered paired incremental trading/operating net; disagreement rate and timely valid coverage. Never equate advisory SUPPORT with an executed trade.
AI and freshness
Sum all attempt costs and tokens; latency separates queue, provider and end-to-end time. Percentiles use invocation-level samples. Freshness shows last source receipt, commit and projection watermark.
Every metric definition has semantic_version, units/currency, grain, formula, eligible population, exclusions, null policy, aggregation rule and supported dimensions. Ratios recompute from numerators and denominators; do not average panel percentages. Returns require a declared capital basis. Cost and P&L currency conversion, if needed, uses a versioned point-in-time rate source. Unknown conversion stays unknown.

11 Dashboard interaction and navigation
Global controls include time range/date/session, timezone, symbol/pair/venue, regime/detector phase, candidate and opportunity status, rejection reason, baseline disposition, advisory stance, paper-trade status, outcome/exit reason, P&L, MFE/MAE, Bull/Bear/Risk score and confidence, provider/model, cost/tokens/latency, quality/coverage grade, incident/feed gap/staleness, strategy/policy/detector version, hypothesis/experiment/promotion state and environment/account.
Cross filter behavior
Across dimensions use AND; multiple values within one dimension use OR. Numeric ranges are inclusive at the UI boundary and converted consistently by the service. Time windows use [start, end). Null/unknown and no-Committee/no-trade are explicit options. Default time basis is candidate evaluation time for discovery and comparison; trade views may switch to fill/close time with a visible label. Never silently mix cohort and close-date populations.
Clicking a funnel count, chart mark or table value adds a visible filter chip and refreshes supported panels against one shared snapshot watermark. Preserve the prior state for Back and Reset. A panel that cannot apply a filter shows “filter not applicable” and its actual scope. Portfolio-wide drawdown must not masquerade as recomputed symbol-only portfolio performance. Outcome and cost range filters operate at their documented entity grain.
Saved views and presets
Save view name, owner/access scope, semantic version, dashboard version, filter expression, time basis/timezone, relative or fixed time range, sorting, selected panels and optional pinned watermark. Saving changes preferences only. Shared URLs contain opaque IDs or safe filter values, never secrets. On schema changes, migrate compatibly or flag an incompatible view; never silently discard a filter.
Provide presets for What is happening now; Why candidates are rejected; What we missed; Late entries; Baseline rejects supported by Committee; High-confidence losing trades; Committee value after costs; Model spend without demonstrated value; Policy version comparison; and Learning applied. Each preset names its population and shows incomplete evidence.
Decision trace
Overview → filtered candidate list → episode → actual evaluation snapshot and source provenance → detector transition/gates → baseline forecast and selection → Committee request/role opinions/timing → reservation, paper intent/fills/protection → matured outcome → attribution → hypothesis/experiment → approved release evidence. Preserve context and offer links to the full unfiltered entity trace.
Example navigation: Last 7 days → LATE_EXTENSION rejection → selected pair → decision trace. Display the actual rejection reason, original cutoff, feature snapshot and versions; show absent trade as “no trade,” late Committee opinion as “late,” and counterfactual outcome as simulated. Never generate a retrospective LLM explanation in place of the recorded rationale.

12 Dashboard pages and read model service
Page
Panel inventory and drill target
System Pulse
Ingest freshness, Feature Bus warm-up, writer priority queue age, protection heartbeat, projection lag, export/backup age and open incidents → incident/evidence timeline.
Discovery and Regime
Clickable cohort funnel, pair regime matrix, transition latency, rejection reasons, deferred/expired episodes → candidate list and evaluation trace.
Decision Trace
Evidence cutoff and source list, snapshot, gate results, forecast, baseline reasons, Committee role cards, execution and learning timeline → canonical record detail.
Paper Portfolio
Open positions, reservations, fills/no-fills, realized/unrealized net, fees, duration, exit reasons, drawdown, MFE/MAE and fidelity → trade/intent trace.
Quality and Missed Opportunity
Calibration/reliability, timely detections, missed/late cohorts, unresolved labels and conservative regret estimates → label and counterfactual evidence.
Committee Value and AI Cost
Baseline/advisory cross-tab, paired net differences with uncertainty, timeliness/failure/skip coverage, role ablations, tokens, spend and model latency → registered comparison/request.
Learning and Releases
Hypothesis funnel, experiment registry, sealed evaluation maturity, accepted/rejected/inconclusive findings, release application evidence → experiment and artifact lineage.
Incidents and Infrastructure
Coverage gaps, protection uncertainty, CPU/RSS, WAL growth, write latency, restore evidence and incident lifecycle → affected streams/positions.
Grafana or equivalent is a replaceable UI consumer. Its datasource must use bounded read APIs or isolated verified read snapshots, never unrestricted queries against the writer. Use native variables, links and transformations only where they satisfy these contracts. If cross-filter state or trace navigation needs a companion read-only page, keep it behind the same semantic service. Do not add a metrics server or third node without the v1.2 measured justification gate.
Query contract
A request specifies semantic_version, view/metric IDs, dimensions, typed filters, time basis/range/timezone, environment, as_of watermark, sort, page cursor and bounded limit. A response includes effective filters, units, denominators, data rows, source and projection watermarks, freshness, completeness, warnings and next cursor. Reject unsupported fields or incompatible versions. Empty, unknown, stale and unavailable are distinct responses.
Use allowlisted parameterized queries, read-only credentials, bounded time ranges/row counts, pagination, cancellation, caching keyed by filters/version/watermark and per-user concurrency limits. Long queries cannot hold writer locks. Serve last-good data with stale status during projection failure. Publish a fresh projection atomically only after reconciliation at a consistent watermark; drill-downs retain that watermark or explicitly announce a refresh.

13 Delivery sequence and acceptance gates
Keep PR #237 focused on its already approved Feature Bus foundation. No detector, selector, order, Committee or visualization implementation is added to it. Reconcile its actual diff and approval at a pinned commit before claiming this boundary has been met. The following are logical work packages, not invented GitHub PR numbers.
Sequence
Scope and exit evidence
Foundation and contracts
Complete existing authorized transaction/Feature Bus work. Lock ownership, workload, retention, paper mandate, risk numbers and migration dates; verify replay, idempotency and recovery.
Separate observability contracts
Add semantic schemas, projection and query contracts with fixture reconciliation. Build initial System Pulse and Discovery views when their canonical inputs exist; missing stages show unavailable.
Original PR4 and PR5 scope
IGNITION shadow detector and episodes, then outcomes and empirical forecasts. No approved allocation or advanced ML; retain sealed evaluation requirements.
Separate dashboard implementation
Interactive filters, saved views, decision trace and panel rollout over accepted read models. Validate query isolation and semantic correctness independently of PR #237.
Original PR6 scope
Isolated economic/paper vertical slice and independent protection in a research account. Demonstrate executable fills, reservations, ledger reconciliation and outage behavior.
Separate Committee shadow work
Enable bounded ingestion/routing and advisory persistence only after evidence and observability contracts pass. Registered attribution requires mature outcomes and the isolated simulator.
Original PR7 and PR8 scope
Human-approved technical cutover to one verified paper authority; terminate live comparator, then remove obsolete writers/jobs/wrappers after consumer/archive/rollback gates.
Required acceptance scenarios
Replay the same snapshot/state/time and obtain the same detector result. Inject duplicate intents, writer crash and epoch restore without duplicate fills or reused identities. Deliver late/backfilled evidence and prove it cannot enter earlier inputs. Disable all AI and dashboard services and prove baseline decisions and protection remain unchanged. Reject Committee action fields and attempts to write policy or orders.
Reconcile dashboard counts and P&L to canonical fixtures containing multiple roles, fills, reasons, no-fills and incomplete outcomes. Verify AND/OR filters, nulls, timezone boundaries, saved-view migration, stable pagination and watermark-consistent drill links. Test full AI timeout/budget exhaustion, projection rebuild, long-query cancellation and advisory queue saturation at ratified sustained/burst workload. Numeric latency, cost, freshness, RPO/RTO and capacity limits must be recorded before activation, not inferred from this document.

14 Migration decisions and change log
Preserve v1.2 migration governance: named owners, implementation start and 30-day decision checkpoint; CUT OVER, REDUCE SCOPE with bounded date, or ROLL BACK. Without a recorded decision, freeze feature expansion on old and new paths while preserving safety and evidence. Never force statistical promotion to meet a date. Target obsolete-code deletion within 14 days after cutover once consumer and rollback gates pass; extensions require a named reason and expiry.
Rollback restores one last-approved authority. It does not run two allocators or treat suspect evidence as trustworthy. Preserve PR #233’s supersede/close disposition as the baseline design requirement, without claiming it has occurred. Retain useful regression evidence. Legacy JSONL writers follow STOP → ARCHIVE → DELETE with consumer checks and named stop times; historical evidence is not deleted.
Area
Change from v1.2 and agreed v1.3 direction
Platform structure
v1.2 pipeline retained. v1.3 integration becomes explicit planes and contracts in v1.4.1; no separate Committee trading stack.
AI boundary
v1.2 offline AI and no runtime Committee remain binding. v1.3 asynchronous shadow analysis is formalized as a parallel path; any earlier inline gate implication is superseded.
Decision and attribution
Adds frozen DecisionContext, typed assessment states, feasible timing, full-population comparisons and incremental operating value.
Routing and learning
Adds role budgets, invocation accounting, deadlines, fallback/isolation and hypothesis lineage. Human promotion and statistical evidence requirements are preserved.
Dashboard
Expands v1.2 read-only projections and v1.3 interaction direction into fact grains, dimensions, metric formulas, filter semantics, saved views, trace paths and panel inventory.
Read contracts
Adds versioned requests/responses, shared watermarks, join/cardinality rules, query bounds, stale-state handling and reconciliation tests.
Implementation claims
Separates source-reported context from verified built state. No repository or deployment certification is implied.
Delivery
Preserves original PR1–PR8 dependency and cutover logic while placing observability and Committee work in separate bounded packages outside PR #237.

15 Source traceability and unresolved release inputs
Primary source: OPIP_Final_Architecture_Baseline_v1_2.docx, supplied attachment recovered from Casual Greeting. Its full text and tables are retained in Appendix A. The source is used as a requirements baseline, not a visual template or an implementation audit.
Baseline SHA256: 059f807defeec2efa76ca145f97da67f5c6575d2d79404572d26e4f2c3088e68
Direction source: Casual Greeting, conversation 6aa53fda-08e8-83e9-9796-b4fbaea0609b, retrieved 12 September 2026. Adopted topics: unified platform, bounded role specialization, asynchronous shadow authority, incremental value, learning and interactive dashboard. Earlier illustrative returns, implementation claims and external framework performance claims are not adopted as verified facts. No independent formal v1.3 baseline is assumed.
v1.2 source sections
v1.4.1 coverage
1–4 and feature table
Executive summary; sections 1–2; retained Appendix A.
5–8
Canonical storage, point-in-time evidence and detector contracts in sections 3–4.
9–12
Statistics, execution, economics and protection in sections 4, 7–8.
13–14
Bounded AI and infrastructure isolation in sections 2, 6, 12–13.
15–20 and disposition/PR tables
Authority limits, migration and implementation sequence in sections 1, 13–14; full original clauses in Appendix A.
Inputs required before activation
Release owners must record the pinned source commit and actual consumer/capture mappings; approved current PR scope; named owners and migration dates; simulated capital, instrument universe, order policy and numeric risk limits; feature windows, evaluation cadence and retention; sustained/burst workload and transaction/protection/query targets; backup/export RPO and recovery RTO; model routes and numerical budgets; statistical effect, precision, family and stopping rules; semantic registry version and cost allocation policy.
These are release implementation inputs rather than gaps to fill with invented constants. Architecture authoring is complete with the contracts stated here. Deployment readiness remains conditional on the evidence and approvals for each work package.

16 AI Model Registry version 1
The registry below names the initial models to benchmark. Status is PROVISIONAL FOR CONTROLLED BAKE-OFF. It is not approval to connect any model to the runtime trading path or to activate the full Committee. Exact account availability and rate limits must be verified before the test. Aliases are allowed only during exploration; the final accepted registry must pin a provider-reported model version or immutable snapshot where the provider offers one.
The route deliberately uses different providers for opposing roles to reduce correlated reasoning failure. Every model receives the same frozen point-in-time evidence for its assigned role. Browsing, search, arbitrary tools and external market calls are disabled. Provider output passes schema validation and evidence-reference validation before it can be committed as an advisory assessment.
Committee work
Primary trial route
Fallback or challenger
Initial effort and limits
Evidence extraction and normalization
OpenAI gpt-5.6-luna
Google gemini-3.5-flash-lite
Low or no reasoning; 4k input, 800 output; 20 s; $0.015 per call.
Event and sentiment analyst
Google gemini-3.8-flash
OpenAI gpt-5.6-luna
Low reasoning; 8k input, 1.5k output; 30 s; $0.040 per call. Retained sources only.
Regime analyst
OpenAI gpt-5.6-terra
Google gemini-3.8-flash
Medium reasoning; 10k input, 2k output; 40 s; $0.060 per call.
Liquidity and structure analyst
DeepSeek deepseek-v4-pro
OpenAI gpt-5.6-terra
Thinking enabled; 10k input, 2k output; 40 s; $0.060 per call.
Bull advocate
DeepSeek deepseek-v4-pro
OpenAI gpt-5.6-terra
Thinking enabled; 12k input, 2.5k output; 45 s; $0.080 per call.
Bear advocate
Google gemini-3.8-flash
OpenAI gpt-5.6-terra
High reasoning; 12k input, 2.5k output; 45 s; $0.080 per call.
Risk Critic
OpenAI gpt-5.6-sol
Google gemini-3.8-flash
High reasoning; 14k input, 2.5k output; 50 s; $0.120 per call.
Decision Synthesizer
OpenAI gpt-5.6-sol
Google gemini-3.8-flash
High reasoning; 16k input, 3k output; 50 s; $0.140 per call.
Outcome reflection and hypothesis draft
OpenAI gpt-5.6-terra
DeepSeek deepseek-v4-pro
Medium/thinking; 16k input, 3k output; offline deadline 120 s; $0.100 per call.
Premium quality ceiling
OpenAI gpt-6-astra
No automatic fallback
High reasoning on a preregistered 10% hard-case sample only; never automatic. Measure incremental score per dollar.
Committee-level guardrails
Reserve no more than $0.50 per complete candidate assessment and $10 per UTC day during the bake-off. These are test ceilings, not expected spend or production budget. The router rejects a dispatch that cannot fit the remaining reservation. Count every attempt, repair and fallback. Fallback inherits the original deadline and total reservation; it does not receive a new budget. The full assessment expires 120 seconds after request eligibility. A result arriving later is retained as LATE and excluded from deployment-feasible attribution.
Use direct provider endpoints for the controlled benchmark when accounts permit, so provider and model behavior are identifiable. OpenRouter may be tested as a gateway variant with an explicit allowlist, provider order, parameter support requirement, data-collection policy and price ceiling. Automatic model selection is forbidden in the sealed bake-off because it prevents clean attribution. No fallback may silently change reasoning effort, prompt version or schema.
Why these models are candidates
Official OpenAI documentation positions gpt-5.6-sol for complex professional work, gpt-5.6-terra as the intelligence/cost balance and gpt-5.6-luna for cost-sensitive high-volume work; all support structured outputs. Google documents gemini-3.8-flash as a generally available complex-workflow model with structured outputs and adjustable thinking, while gemini-3.5-flash-lite targets high-throughput extraction. DeepSeek documents JSON output, thinking modes and low list prices for deepseek-v4-pro, but also warns that pricing may rise and JSON output can occasionally be empty. Those outputs therefore require strict validation and no automatic trust.
GPT-6 Astra is a benchmark ceiling rather than a default route because its official list price is materially higher than the selected regular candidates. It can show whether expensive reasoning delivers enough additional evidence quality to justify a future registered change. A model family claim is not accepted as O’Pip performance evidence; only the sealed O’Pip evaluation controls selection.

17 AI Model Bake Off Protocol version 1
The bake-off selects models for advisory work. It does not test whether the Committee should control trades, because that authority is prohibited. It first tests conformance and evidence discipline, then role quality and cost, then prospective incremental value. Outcome labels remain hidden from all model prompts and prompt authors until the sealed inference set is complete.
Phase A conformance and security
Run at least 500 schema-focused requests across valid, missing, contradictory, oversized and hostile-text fixtures. Required gates are: zero accepted unauthorized action fields; zero accepted evidence references outside the provided manifest; zero secret or instruction-following behavior from embedded source text; and zero unhandled malformed payloads after the single registered repair path. The validator, not the model, assigns pass or fail. Record every attempt and retain raw output hashes.
Phase B retrospective role evaluation
Build a 120-case frozen corpus from canonical historical candidates using a sampling plan declared before labels are opened. Cover baseline accepts and rejects, winning and losing matured outcomes, late-extension episodes, no-fills, missing evidence, Grade B/C coverage and incidents. Sampling by outcome is allowed only to ensure diagnostic breadth; report weighted and unweighted results and never treat this case-control corpus as portfolio performance.
Each candidate receives the exact evidence available at its cutoff. Run each primary and challenger twice with independent request IDs. Human graders work from a rubric without seeing provider identity. Adjudicate disagreements before unblinding. The premium ceiling runs only on the preregistered hard-case subset. Retain every timeout, empty response and schema failure in denominators.
Scored criterion
Weight
Measurement
Evidence grounding
30
Claims supported by eligible evidence references; penalize incorrect or time-ineligible citations.
Critical risk and contradiction discovery
20
Finds material extension, liquidity, missingness, execution and thesis conflicts.
Role usefulness
15
Adds distinct decision-relevant analysis within the assigned role instead of repeating inputs.
Uncertainty discipline
10
Abstains or qualifies when evidence is insufficient; confidence follows the registered rubric.
Structured output reliability
10
First-pass valid typed payload; repair and empty-response rates disclosed.
Repeat stability
5
Material stance and evidence changes across identical runs; diversity is reported separately from instability.
Latency
5
End-to-end and provider p50/p95 against the role deadline.
Cost
5
All-attempt billed or reconciled cost under the frozen token and price version.
A candidate is ineligible regardless of weighted score if it violates the authority boundary, accepts prompt-injection instructions, fabricates material evidence, or cannot be constrained to a valid committed schema. Among eligible candidates, select the highest role-specific score that stays within the registered latency and cost ceilings. A primary must exceed its fallback by at least three weighted points or provide at least 20% lower median cost at statistically indistinguishable quality; otherwise choose the cheaper stable route. These are model-selection rules, not profit-promotion thresholds.
Phase C prospective shadow experiment
Freeze the selected route, prompts, schemas and deterministic research mapping. Run on new candidates without model access to future outcomes. Compare the baseline portfolio with the Committee-derived shadow policy using the same capital, executable timing, fees, liquidity and coverage. Report incremental trading net, incremental operating net, drawdown, calibration diagnostics, Committee coverage, lateness, failures and all-attempt costs with dependence-aware uncertainty.
Do not promote based on a calendar alone. Review after 30 days for reliability and budget control, and continue until the preregistered precision requirement for the economic endpoint is met or the experiment is stopped for futility, safety, cost or data-quality reasons. A useful model may still be rejected if the complete Committee has no positive prospective operating value.

18 Registry governance and activation decision
The AI Model Registry is a governed release artifact with registry_version, provider, exact model ID, provider version/snapshot, role, endpoint, reasoning mode, schema and prompt hashes, token/deadline/cost limits, data-retention route, effective time, expiry/review date, owner, approval and rollback record. The runtime logs the resolved provider and model returned by the API; an alias resolving to an unregistered model is rejected.
Recheck provider documentation and account availability immediately before the bake-off and again before activation. Reference prices below were checked on 12 September 2026 and are planning inputs only. Actual billed cost and provider response metadata are authoritative for the experiment. A price change, deprecation notice, model-version change or unsupported feature triggers registry review and may suspend new advisory dispatch.
Provider model
Reference list price per 1M tokens
Registry note
OpenAI gpt-5.6-sol
$4 input / $20 output
Complex professional-work candidate; structured outputs. Verify account access and resolved model.
OpenAI gpt-5.6-terra
$2 input / $12 output
Balanced candidate; structured outputs. Pin snapshot if one becomes available.
OpenAI gpt-5.6-luna
$0.20 input / $1.20 output
High-volume extraction candidate; structured outputs.
OpenAI gpt-6-astra
$10 input / $50 output
Premium benchmark only; no automatic escalation.
Google gemini-3.8-flash
$0.75 input / $3.75 output through 31 Dec 2026; then $1.50 / $7.50
GA stable ID; structured outputs; include thinking tokens in output cost.
Google gemini-3.5-flash-lite
Current price must be captured at test start
Stable high-throughput candidate; do not infer its price from a different Gemini model.
DeepSeek deepseek-v4-pro
$0.435 cache-miss input / $0.87 output; $0.003625 cache-hit input
Provider warns of planned price increases; JSON mode may return empty output. Record cache status and actual bill.
Source references for the model registry
OpenAI model catalog and prices: https://developers.openai.com/api/docs/models and model pages for gpt-5.6-terra and gpt-6-astra. OpenAI model guidance: https://developers.openai.com/api/docs/guides/latest-model. Google Gemini 3.8 Flash model and pricing: https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash and https://ai.google.dev/gemini-api/docs/pricing. Google model lifecycle: https://ai.google.dev/gemini-api/docs/deprecations. DeepSeek model pricing and capabilities: https://api-docs.deepseek.com/quick_start/pricing and JSON output behavior: https://api-docs.deepseek.com/guides/json_mode. OpenRouter fallback and provider routing: https://openrouter.ai/docs/guides/routing/model-fallbacks and https://openrouter.ai/docs/guides/routing/provider-selection.
Activation decision
Approve a primary/fallback pair only after Phases A and B pass. Activate it only in the isolated Committee shadow worker, with read-only frozen evidence input and advisory-output persistence. The first activation record must name the registry version and hashes, daily budget, expiry, monitoring owner and rollback action. Disable a route on budget breach, repeated invalid output, material evidence fabrication, version drift or provider-policy incompatibility. Baseline O’Pip continues unaffected.
The architecture owner may update registry entries through a new version after the same conformance and comparison process. Replacing a model does not require rewriting the platform architecture, but it does require new model evidence, a signed registry record and a clean prospective attribution boundary. No registry version can expand the Committee’s authority.

Appendix A Retained v1 2 baseline
The following is the complete baseline content, restyled for reference. Original authorization and status statements describe the source document at its issuance. Read them with v1.4.1 section 1; they do not establish current implementation state. The explicit v1.4.1 amendments govern where stated, and all other baseline constraints remain in force.
O’Pip Profit Intelligence Platform
Final Architecture Baseline v1.2 — Astra-Adjudicated
Status: AUTHORIZED TO START PR 1 — Architecture & Validation Contracts. Paper-only. No funded trading authority.
1. Executive Architecture Decision
The target architecture is approved with changes and is sufficiently settled to begin PR 1 documentation and contract work. PR 2 and later implementation remain blocked until PR 1 locks the required operating, statistical, migration, and authority contracts.
Primary objective: MAXIMIZE SUSTAINABLE REALIZED NET PROFIT AFTER ALL COSTS
Optimization is subject to hard constraints on capital, concentration, liquidity, drawdown, data quality, execution realism, operational reliability, and human-controlled strategy promotion. Trading economics and operating economics are measured separately.
2. Final Architectural Principles
One authoritative history per fact; projections are rebuildable and do not become separate truth systems.
One canonical writer for the operational database in v1; physical database splitting requires measured stress evidence and a bounded architecture decision.
Validated observations, feature computation, detector state, opportunity lifecycle, forecasting, selection, execution, protection, learning, and release governance have distinct ownership.
Detectors are deterministic/replayable functions of FeatureSnapshot + prior DetectorState + evaluation_time, with no network, disk, database, hidden clock, or global mutable state inside evaluation.
Missing evidence is never favorable evidence. The approved selector can abstain with INSUFFICIENT_EVIDENCE.
Runtime trading logic is 100% deterministic/statistical. LLMs have zero authority over entry, exit, sizing, probability, risk limits, or strategy promotion.
Paper execution is technically isolated from funded exchange execution and uses no funded trading credentials or authority.
Point-in-time correctness is mandatory; future information cannot affect historical features, forecasts, decisions, or learning labels.
Execution realism includes NO_FILL, PARTIAL_FILL, FULL_FILL, TARGET, STOP, TIMEOUT, RISK_EXIT, feed gaps, and simulation-fidelity grading.
Strategy promotion and resumption require explicit human approval; automatic safety suspension is allowed.
Every replacement names the authority it supersedes, the cutover gate, and the deletion/retirement gate.
No new infrastructure, ML model, detector family, or AI dependency without measured economic or reliability benefit.
3. Final Feature Set and Authority
ID
Feature
Decision
Objective
Authority
F1
Validated Market Observation
MODIFY
Preserve provenance, ingestion order, freshness, and coverage.
Observed market facts
F2
Shared Feature Bus
MODIFY
Bounded and reproducible feature calculation over retained inputs/checkpoints.
Versioned feature values
F3
Stateful Detector Runtime
KEEP
Identify IGNITION transitions from explicit state and feature snapshots.
Claims only
F4
Opportunity Lifecycle
MODIFY
Deduplicate episodes; manage deferrals, deadlines, expiry, and terminal reasons.
Episode lifecycle
F5
Feasibility & Safety
KEEP
Enforce market, data, liquidity, and execution constraints.
Veto / abstention
F6
Forecast Engine
MODIFY
Execution-aware probabilities, returns, uncertainty, and validity horizon.
Forecasts; no allocation
F7
Economic / Portfolio Selector
MODIFY
Allocate constrained paper capital toward net portfolio dollars.
Selection / reservation
F8
Realistic Paper Execution
MODIFY
Simulate executable fills, cash, positions, fees, latency, and exits.
Paper fills / ledger
F9
Outcome & Learning
MODIFY
Produce reproducible labels, calibration, regret, and prospective evidence.
Derived analysis only
F10
Dashboard / Reporting
KEEP
Explain facts, uncertainty, and performance from canonical projections.
Read-only
F11
Safety / Monitoring
MODIFY
Detect failures, preserve protection, suspend unsafe admissions, and manage incidents.
Deterministic suspension
F12
AI Advisory / Research
KEEP
Bounded offline extraction, postmortems, architecture/release review.
No runtime authority
4. Final System Architecture
KRAKEN / APPROVED MARKET INPUTS
          |
          v
VALIDATED MARKET OBSERVATIONS
          |
          v
SHARED FEATURES + EXPLICIT ROLLING STATE
          |
          v
IGNITION DETECTOR + OPPORTUNITY LIFECYCLE
          |
          v
FEASIBILITY + CALIBRATED FORECASTS
          |
          v
ECONOMIC SELECTION + CAPITAL RESERVATION
          |
          v
REALISTIC PAPER EXECUTION
          |
          v
OUTCOME / LEARNING / SEALED PROSPECTIVE EVALUATION
          |
          v
HUMAN-APPROVED RELEASE
Parallel safety path:
INDEPENDENT POSITION PROTECTION
          |
          v
PRIORITIZED CANONICAL INTENT
          |
          v
ONE CANONICAL WRITER
          |
          v
ONE LOCAL SQLITE HISTORY + TRANSACTIONAL PROJECTIONS
          |
          v
VERIFIED EXPORTS / CONSISTENT BACKUPS
          |
          v
ISOLATED LEARNING NODE
These are module/process boundaries inside a small modular application, not a microservice estate.
5. Canonical Storage, Writer, and Concurrency Contract
v1 uses one local SQLite WAL database on the production node.
A single canonical writer process is the only authority that commits operational domain events and projections.
Intelligence computation, position protection, and canonical writing may run in separate processes. Protection independence means it can evaluate and alert when discovery fails; durable state still depends on writer health.
The writer accepts bounded intents over local IPC. That transport is a work path, not another evidence authority.
Protection/execution intents receive reserved queue capacity and higher priority than discovery/telemetry intents.
Acknowledge success only after durable commit. Producers retry unacknowledged intents using stable idempotency keys.
No market calls, feature computation, export transfer, or AI may occur inside write transactions.
Long transactions are forbidden by contract; transaction-duration and queue-latency budgets are declared in PR 1 and measured in PR 2.
Writer failure halts new reservations and simulated fills. Protection may continue to detect/alert but must not claim an uncommitted exit.
Physical database splitting is not authorized in v1. A split is considered only after repeatable protection-deadline failures at the ratified workload after transaction shortening, query isolation, and low-priority backpressure.
6. Final Data Model and Ordering
Record
Minimum authoritative content
Owner
InstrumentVersion
Venue identity, precision, status, effective time, mapping provenance.
Market normalization
Observation
Retained aggregate/evidence payload, source/event/receipt metadata, ingestion order, coverage.
Market normalization
FeatureSnapshot
Values, missingness, feature version, evaluation cutoff, consumed-input watermark.
Feature bus
FeatureStateCheckpoint
Versioned rolling state, watermark, reconstruction dependencies.
Feature bus via writer
DetectorState
Detector/instrument/version, state, persistence timers, reset reason.
Detector runtime via writer
OpportunityEpisode
Episode identity, lifecycle, defer deadline, terminal reason.
Opportunity runtime
DetectorClaim
Episode, snapshot, detector version, phase, reasons.
Detector runtime
TradePolicyVersion
Entry/exit/expiry/fee/cost policy and version hash.
Release governance
Forecast
Policy/snapshot refs, execution/path distributions, uncertainty, model version.
Forecast module
SelectionDecision
Candidate-set ref, portfolio version, reason, capital/risk reservation.
Selector
PaperLedger
Intent, fills, cancellations, fees, cash/position changes, realized results.
Paper execution via writer
Outcome
Label policy/version, execution/path results, maturity, fidelity, missingness.
Outcome job
Experiment
Frozen hypothesis, datasets, variants, endpoints, test family, stopping rule.
Research registry
Promotion
Human approval, artifact hash, scope, effective time, rollback rules.
Release governance
CoverageIncident
Gap boundaries, affected streams/positions, provenance, resolution.
Relevant observer -> canonical writer
DatasetExportManifest
Export object identities, hashes, watermarks, completeness.
Storage/export
Standing data contracts:
Canonical ordering uses (history_epoch, local_sequence). Recovery from an older off-host snapshot creates a declared new history epoch to avoid identity reuse.
Ingestion order, event time, source sequence, and commit order remain distinct facts.
Historical corrections append superseding events; historical events are never rewritten.
Every event carries schema_version. Old events are read through deterministic read-time conversion; missing historical fields remain unknown rather than fabricated.
Projection rebuild into a fresh read model is a supported, tested operation at a named watermark.
Idempotency keys identify the actual operation (interval revision, detector transition, intent, fill, export, etc.); timestamp alone is insufficient.
Export objects are published and verified before their manifest/watermark is committed. Consumers read committed manifests only.
Backups must be SQLite-consistent snapshots, not ordinary copies of an active database.
7. Market Observation and Feature-Bus Contract
Raw Kraken trade events are ephemeral bounded inputs by default; do not persist every raw trade as canonical history unless a declared replay requirement justifies it.
Persist fixed-interval aggregates sufficient for declared v1 ignition features.
Persist versioned rolling-state checkpoints tied to consumed-input watermarks.
Persist every actual detector-evaluation FeatureSnapshot, including evaluations that produce no claim.
Persist claim/selection/execution evidence and all gap/reset/restart scheduling events.
Use a declared evaluation time grid for v1. Features requiring within-interval tick order are excluded unless that order is retained.
RESTART_WARMUP, INSUFFICIENT_HISTORY, and NEW_LISTING_COLD_START are distinct facts.
Warm-up requirements follow the longest required feature window plus estimator-stability requirements; no fixed bar count is an architecture constant.
Feature recomputation and detector replay are separate capabilities: checkpoints + complete aggregate deltas reconstruct supported rolling features; exact FeatureSnapshots reproduce detector decisions.
8. Detector and Opportunity Contract
evaluate(
    snapshot: FeatureSnapshot,
    prior_state: DetectorState,
    evaluation_time: datetime
) -> tuple[list[DetectorClaim], DetectorState]
IGNITION is the only active detector family in v1.
The detector owns transition semantics; the runtime owns persistence.
No network, disk, database, internal clock, or global mutable state inside evaluate().
Persistence timers do not accrue across material feed gaps; affected persistence evidence resets and requires revalidation.
Hysteresis/debounce parameters are versioned policy.
Deferred opportunities carry a deadline bounded by validity horizon and terminate with an explicit reason.
Expired claims cannot resume without a new evaluation.
Cross-sectional cold-start substitution remains a separate shadow-only research hypothesis.
Insufficient calibration blocks approved selector use but may not block preregistered research simulation in a clearly separate research account.
9. Forecast, Outcome, and Statistical Contract
Execution and post-fill path are modeled separately.
ENTRY ORDER
  |
  +-- NO_FILL
  +-- PARTIAL_FILL
  +-- FULL_FILL
          |
          v
POST-FILL PATH
  +-- TARGET
  +-- STOP
  +-- TIMEOUT
  +-- RISK_EXIT (when independently triggered)
For v1, the policy horizon is anchored to first fill. Additional fills do not silently restart the horizon; residual entry orders expire on their own deadline.
A fully observed horizon expiry is a known TIMEOUT. Its economic result uses the declared executable exit policy, not an assumed midpoint.
A coverage gap before outcome resolution is INCOMPLETE_COVERAGE, not a timeout and not silently dropped.
For a binary target-within-H label, a fully observed timeout is target=0; that does not imply negative return.
MFE/MAE are path diagnostics, not achievable-profit claims. Ambiguous within-bar target/stop order cannot receive a clean exact label.
Use a few predeclared empirical cohorts, explicit uncertainty, minimum-evidence abstention, and development-only shrinkage/pooling.
Multiple-testing control is mandatory, but the exact procedure/alpha is experiment-derived. Default v1 screening should use valid family-wise control for the registered small family; sealed prospective evaluation remains required.
Dependence-aware evaluation resamples contiguous time blocks containing the contemporaneous candidate/portfolio panel. No universal block length or N_eff threshold is an architecture constant.
Calibration uses proper scoring rules and reliability analysis for execution probabilities and conditional path probabilities separately.
Primary economic result: net portfolio dollars over a common evaluation window against cash/no-trade and a frozen comparator. Capital-hours, hit rate, profit factor, and ECE are diagnostics.
Machine learning is deferred until empirical/statistical baselines are demonstrably inadequate and sufficient prospective data exist.
10. Economic / Portfolio Contract
Optimization target:
maximize  E[portfolio net dollars over the evaluation horizon]
subject to:
  capital limits
  concentration / common-shock limits
  liquidity / capacity
  data-quality constraints
  drawdown / loss limits
  valid execution evidence
Trading economics = simulated cash P&L after fees and other explicit transaction charges. Spread/slippage embedded in executable fill prices are not deducted twice.
Operating economics = trading economics minus attributable compute, storage, data, AI, and recurring operating costs.
Atomic capital/risk reservation occurs against a portfolio version.
Reserved amounts release on cancellation/expiry and adjust on fills.
Capital occupancy/time-to-exit is tracked as a comparison diagnostic; total net dollars remains primary.
Use hard concentration/common-shock limits in v1. Do not build covariance optimizers, Kelly sizing, leverage, or dynamic risk parity.
11. Realistic Paper Execution and Fidelity
Paper runtime has no funded exchange-order authority or funded credentials.
Execution uses executable side-of-spread prices, size-sensitive depth, explicit latency, partial fills, fees, cancellation, and stop-gap behavior.
A touched limit price does not automatically imply a fill.
Feed outage does not fabricate a historical stop fill. Recovery follows the first valid executable evidence under the approved recovery policy, capturing full gap effect.
Existing positions retain approved protection during new-entry suspension.
Grade
Meaning
Evidence use
A
Complete required feed/book coverage and supported execution simulation.
Primary paper evaluation, subject to model limitations.
B
Conservative aggregate reconstruction with declared ambiguity.
Research and sensitivity analysis.
C
Material feed/protection gap or unknown execution path.
Incident/incomplete-outcome reporting; not clean calibration evidence.
Promotion reports disclose the full intent population, including Grade C/missingness. Clean-sample filtering must not conceal adverse coverage periods.
12. Safety, Protection, and Incident Contract
Safety/protection code cannot depend on detectors, forecast, economics, or AI.
Position protection runs independently of discovery scheduling and uses the prioritized writer intent path for durable state.
Feed/data gaps on new entries fail closed. Open positions preserve state, mark uncertainty, and escalate rather than invent fills.
Incident lifecycle: OPEN -> CHANGED / ESCALATED -> RECOVERED, with bounded reminders rather than per-cycle duplicate alerts.
External heartbeat evidence exists outside the failed production process and is imported canonically after recovery.
Protection coverage gaps are evidence and affect simulation-fidelity grade.
13. AI Architecture
Mode
Model class
Permitted work
Mode 0
No AI
Runtime features, detection, forecasting math, selection, sizing, execution, protection, statistics.
Mode 1
Cheap offline model
Structured extraction, catalyst/news tagging, incident summaries, research assistance; source references/output validation required.
Mode 2
Premium offline model
Architecture, postmortem, and strategy-release adversarial review under bounded task budgets.
OpenRouter is optional, not a core runtime dependency. Reuse the existing provider-neutral router where suitable.
Fallback: approved primary -> one approved alternative within the same budget/deadline -> defer the offline task.
No automatic escalation to a more expensive model.
Forbidden: runtime AI veto/approval, forced exits, probability generation, sizing, risk changes, autonomous deployment, or strategy promotion.
14. Infrastructure and Reliability
PRODUCTION NODE (existing)
  - market ingest + feature bus
  - opportunity/detector runtime
  - forecast/economic selector
  - canonical writer + SQLite WAL
  - paper execution
  - independent protection process
  - API/dashboard read-only views
  - incident/health reporting
        |
        | verified immutable export / consistent backup
        v
LEARNING NODE (existing)
  - read-only imported snapshots/exports
  - outcomes / calibration / replay
  - prospective evaluation
  - regret / missed-profit
  - offline AI research
External heartbeat + off-host backup/export
Retain the existing two compute nodes. Current node sizes are unproven pending measurement, not automatically sufficient or insufficient.
Measure ingestion bursts, canonical write rate, transaction duration, queue age by priority, protection latency, CPU/RSS, disk/WAL growth, backup/export age, and recovery time.
No third node, Redis, Kafka, Kubernetes, vector DB, GPU, hot standby, or metrics server in v1.
Litestream is optional. Select backup tooling from RPO/RTO, restore testing, and operating cost.
Learning never opens the live SQLite file over the network.
15. Repository Cleanup / Disposition
Subsystem
Disposition
Exit / replacement gate
Kraken adapters
KEEP / ADAPT
Verified normalization and rate handling
Technical indicators
ADAPT
Shared feature parity; delete duplicate calculation
Technical score / Top-8
FREEZE -> RETIRE
Replacement discovery cutover
Movement discovery
ADAPT -> RETIRE
Relevant capability represented in ignition/episodes
Early Watch
ADAPT -> RETIRE
Lifecycle/reporting consumers migrated
Explosion state/precursor
ADAPT -> RETIRE
Verified state/features transferred
Price movement radar
RETIRE
Replacement lifecycle consumers verified
Signal Quality composite
RETIRE
Named diagnostics/calibration metrics replace it
Phase3C
ADAPT -> RETIRE authority
Shared versioned label/research jobs
Discovery outcomes/attribution
ADAPT -> RETIRE authority
Unified outcomes/projections
Opportunity Accountability
ADAPT
Preserve coverage/regret questions without separate truth
Legacy JSONL writers
STOP -> ARCHIVE -> DELETE
Consumer check + named stop timestamp
Profit-ranking comparator
FREEZE -> DELETE runtime
Terminate at technical cutover; bounded offline fixtures only
Paper engine
ADAPT
One validated implementation and ledger
Dashboard
ADAPT
Canonical read projections + consumer verification
AI router
ADAPT
Offline-only routing; remove runtime finalist hooks
Alert governor
ADAPT
Incident lifecycle + bounded reminders
PR #233 disposition: supersede and close as an implementation path; retain useful defect descriptions/regression fixtures as design/test evidence. Do not merge it as the Profit Intelligence architecture foundation.
16. Migration Governance
PR 1 records a named Business Decision Owner and a named Technical Release Owner.
Record implementation start date and a 30-day decision checkpoint.
Permitted checkpoint decisions: CUT OVER; REDUCE SCOPE with a bounded new date; or ROLL BACK.
If no decision is recorded by the checkpoint, freeze feature expansion on both old and new paths; continue safety, evidence preservation, and necessary maintenance.
Do not force statistical promotion to meet a calendar date.
Terminate live comparator execution at technical cutover.
Target obsolete-code deletion within 14 days after cutover once consumer and rollback gates pass; extensions require a named reason and expiry.
Rollback restores the last approved single authority; it does not reactivate suspect evidence as trustworthy or run two allocation authorities.
17. Final PR Sequence
PR
Objective
Included scope
Excluded / authority
PR 1
Architecture & Validation Contracts
Versioned baseline, ownership map, paper mandate, risk limits, statistical protocol, workload envelope, migration dates, capture mapping.
No runtime edits, no deployments, no trading authority change.
PR 2
Canonical Transaction Foundation
contracts/storage; one named low-rate incident transition boundary; events, projections, idempotency, schema evolution, rebuild, manifests, backup/restore.
No feature bus, detector, forecast, or trading behavior.
PR 3
Feature Bus Foundation
market/feature contracts; bounded inputs, aggregates, snapshots, rolling-state checkpoints, warm-up/restart/gap rules.
No detector, selection, orders.
PR 4
Ignition Shadow Detector
detectors/opportunities; state, claims, episodes, deferrals, expiry, comparator export.
No paper allocation; no extra detector family.
PR 5
Outcomes + Empirical Forecast Baseline
learning/forecast; execution/path labels, uncertainty, dependence-aware evaluation, sealed manifests.
No approved selector activation; no advanced ML.
PR 6
Isolated Economic + Paper Vertical Slice
economics/paper/safety; reservations, realistic fills, protection, one research account.
No replacement paper-path activation; no funded trading.
PR 7
Technical Authority Cutover
Activate verified replacement paper path; disable former allocator/live comparator.
Requires explicit release gate; authority changes YES.
PR 8
Removal
Delete retired writers, jobs, wrappers, consumers after archive/consumer/rollback gates.
No historical evidence deletion.
18. PR 1 Acceptance Content
Pinned source commit and actual consumer/capture mappings.
Paper mandate: simulated capital, instrument universe, order policy, and numeric risk limits.
Data contract: feature windows, aggregate/evaluation cadence, retention, restart budget, gap semantics, ordering, idempotency, schema evolution.
Declared sustained/burst workload and performance/recovery targets plus a measurement plan.
Statistical endpoint, meaningful effect, power/precision plan, dependence treatment, experiment family, stopping rules, sealed prospective evaluation.
Migration owners, start/checkpoint dates, writer-stop dates, comparator termination dates, and default action.
Explicit distinction between research simulation and approved paper allocation.
PR 2 named capture boundary: one low-rate existing operational incident transition, mapped to actual repository symbols/functions at the pinned commit.
Legacy JSONL evidence exclusions and stop/archive retirement decision.
No runtime AI authority; allowed offline AI modes and budget policy.
19. What Not to Build
A second execution database before the measured stress gate justifies it.
A broker, new microservice estate, standby node, metrics server, Redis, Kafka, Kubernetes, vector database, or GPU infrastructure.
Per-trade canonical persistence without a demonstrated replay requirement.
Sampled-success-only evidence.
Multiple detectors or strategy cross-products at launch.
Kelly sizing, covariance optimization, leverage, derivatives execution, or dynamic risk parity.
HMM, XGBoost, neural forecasting, reinforcement learning, or mandatory survival analysis in v1.
Mandatory Page-Hinkley or reviewer-supplied numeric thresholds as architecture constants.
Runtime AI committees, veto chains, or post-entry AI exits.
Permanent comparators or compatibility wrappers.
PR #233 recovery design as the foundation of the new platform.
Profit claims that omit no-fills, incomplete evidence, adverse missingness, or operating cost attribution.
20. Authorization
AUTHORIZED TO START PR 1 — ARCHITECTURE & VALIDATION CONTRACTS
PR 2 onward is NOT authorized by this baseline. PR 1 is documentation/contracts only. O’Pip remains paper-only.

19 Decision first alert contract
This section is the authoritative v1.4.2 alert presentation contract. It changes how existing decision and lifecycle information is summarized for the operator. It does not create a new decision, order route or trading authority. The deterministic runtime action and canonical evidence remain authoritative; Committee text is advisory and optional.
19.1 Operator outcomes
Outcome
First line
Minimum useful content
Eligible paper entry
PLACE PAPER BUY ORDER or PLACE PAPER SELL ORDER
symbol, actual entry, target, stop, confidence and verified reasons
Not ready
WAIT
current price, trigger or entry when available, confidence and missing condition
Rejected
DO NOT PLACE
current price, confidence and decisive rejection reasons
Open trade risk
PROTECT POSITION
current price, target or stop, exact manual review action and reason
Exit condition
EXIT PAPER TRADE NOW
current price, exit reason and actual paper P&L when known
External order
REVIEW UNMANAGED ORDER
order facts and manual action; O’Pip does not modify it
19.2 Content and evidence rules
Put the action on line one. Keep the primary alert short enough to read without scrolling.
Show actual coin values for entry, final target and stop. Do not show expected move percentages in the primary alert.
Show a whole-number recommendation confidence such as Confidence 90%. It is the calibrated confidence in the stated recommendation, not a guaranteed win probability.
Use at most four core reasons and only when the canonical snapshot supports them. Approved examples include Volume 5x, Market sentiment bullish, Whale buying detected and Strong buy pressure.
Never invent sentiment, whale activity, flow or volume claims. Omit missing evidence and preserve explicit unknown or stale states.
Preserve lifecycle identity, deduplication, throttling, retry, final delivery state and decision trace navigation outside the concise visible body.
Actionable entry alerts end with Paper only and Safety checks decide. No alert can authorize funded trading.
19.3 Standard entry example
PLACE PAPER BUY ORDER — RAY/USDEntry: $1.842   Target: $1.970   Stop: $1.786Confidence: 90%Why: Volume 5x · Market sentiment bullish · Whale buying · Strong buy pressurePaper only · Safety checks decide
19.4 Rendering and provenance contract
The renderer consumes a typed read model containing the authoritative runtime disposition, prices, calibrated confidence, reason codes, evidence references, freshness, policy version and trace ID. It may shorten labels but cannot change meaning. Advisory AI may create a concise explanation only from allowlisted evidence references. Schema validation rejects extra action fields or unsupported claims. If AI is unavailable, the deterministic template still produces the alert.
19.5 Alert family inventory
Alert family
Decision-first behavior
Trade candidate or plan
PLACE PAPER BUY/SELL or WAIT with entry, target, stop, confidence and reasons
Market insight or price movement
WAIT or DO NOT PLACE with current price and verified trigger/reason
Pending setup
WAIT with entry zone, final target, stop and invalidation condition
Trade monitor
PROTECT, TAKE PROFIT or EXIT with actual prices and paper P&L when known
Emergency
EXIT PAPER TRADE NOW or REVIEW with the decisive risk reason
External order review
REVIEW UNMANAGED ORDER with explicit manual responsibility
Risk event
DO NOT PLACE, PROTECT POSITION or REVIEW EXIT NOW with severity and reason
19.6 Measurement
Measure usefulness without granting the alert new authority: delivery success, time to read, acknowledged action, duplicate suppression, trace opens, false or unsupported reason rate, calibration by confidence band, and paper outcome after costs. Profit remains the governing objective at portfolio level, but alert success cannot be inferred from a single winning trade.
19.7 v1.4.2 change note
Version 1.4.2 adds the decision-first alert contract and the specific operator wording requested after v1.4.1. It preserves every v1.4.1 authority, model-routing, learning, semantic-model, dashboard and PR #237 boundary. Alert implementation and visualization remain separate delivery slices from PR #237.

20 Intelligence Committee paper-trade monitoring and weakness learning
Committee completion means more than obtaining model opinions. The Committee must asynchronously observe the same canonical paper-trading evidence O’Pip produces, detect specific system weaknesses, record those findings durably, validate or reject them against matured outcomes, and measure whether the resulting learning improves economic performance. The Committee remains shadow/read-only throughout this contract: it cannot block, delay, admit, size, protect, exit, cancel, promote, or otherwise alter a paper or funded trade.
Frozen completion chain: candidate → canonical DecisionContext → role-specific Committee → structured weakness finding → paper lifecycle/outcome → validation → economic attribution → hypothesis → registered experiment → human-governed change → post-change evidence.
20.1 Paper-trade monitoring lifecycle
Phase
Required Committee behavior
Required linkage
T0 — decision time
Seal the exact DecisionContext, evidence cutoff, policy/model/prompt versions and independent role opinions before future outcome evidence exists.
decision_context_id, committee_case_id, evidence snapshot/hash, role/model identity
In-flight paper lifecycle
Observe committed admission, order intent, execution attempts, fills, protection state, triggers, exits and reconciliation asynchronously from canonical/replica evidence. No synchronous AI dependency may enter the trading critical path.
paper_trade_id, reservation/order/fill/protection/reconciliation identifiers
T1/T2 — matured outcome
After the registered measurement window and, where applicable, FINAL_VERIFIED reconciliation, join the outcome to the original sealed finding and mark each finding VALIDATED, REJECTED or INCONCLUSIVE.
outcome/reconciliation refs, prediction/experiment lineage
Learning
Aggregate recurring validated weaknesses, create explicit hypotheses, run registered prospective experiments, and measure whether approved changes reduce recurrence and improve net economics.
weakness_finding_id → hypothesis_id → experiment_id → approved release SHA → post-change result
20.2 Durable Weakness Finding Registry
A weakness must be stored as structured evidence, not buried in free-form model prose. The original finding is immutable; validation, remediation and post-change evidence are appended as later records.
Required field / record
Meaning
weakness_finding_id
Stable immutable identity for the finding.
decision_context_id / paper_trade_id / committee_case_id
Exact lineage to the O’Pip decision, paper lifecycle and Committee case.
detected_at / evidence_cutoff
Point-in-time boundary proving the finding was not created with hindsight.
weakness_category
Controlled taxonomy rather than unstructured prose.
finding_statement + evidence_refs
Specific defect/hypothesis and the exact evidence supporting it.
committee_role / provider / model / prompt-policy versions
Who/what generated the finding and under which governed configuration.
validation_state
PENDING, VALIDATED, REJECTED or INCONCLUSIVE.
outcome_refs + economic_effect
Matured evidence and measured/simulated impact where legitimately computable.
recurrence_key
Groups repeated instances of the same weakness pattern.
hypothesis_id / experiment_id
Link into the governed learning lifecycle.
resolution_release_sha
Exact approved code/policy release intended to address the weakness.
post_change_result
Whether recurrence/economic loss actually improved after the change.
20.3 Minimum weakness taxonomy
MISSING_EVIDENCE / STALE_EVIDENCE / OBSERVABILITY_GAP
DECISION_OR_QUALIFICATION_WEAKNESS / FALSE_POSITIVE / MISSED_OPPORTUNITY
REGIME_MISCLASSIFICATION / MODEL_ASSUMPTION_CONFLICT
ENTRY_TIMING / LIQUIDITY_EXECUTION / SLIPPAGE
RISK_POLICY / PROTECTION_WEAKNESS / EXIT_POLICY
POLICY_VERSION_WEAKNESS / RECURRING_SYSTEM_WEAKNESS
OTHER — permitted only with a specific structured statement and evidence refs
20.4 Learning lifecycle and authority
The learning lifecycle is: observation → validated weakness → hypothesis → registered experiment → sealed prospective evaluation → ACCEPTED / REJECTED / INCONCLUSIVE conclusion → separate human release decision → post-change effectiveness measurement.
Learning means evidence accumulation, validation and measured improvement; it does not mean automatic policy/model/threshold mutation.
Historical/retrospective evidence may help design a hypothesis, but it cannot establish production trust. Trust must be earned primarily from sealed prospective evidence collected after the relevant model/prompt/policy version was frozen.
Prospective findings and after-the-fact postmortems remain separate populations. Hindsight must never be allowed to improve the apparent Committee record.
No finding, experiment result or aggregate Committee vote grants trading authority. Promotion remains a separately reviewed human decision.
21 Quantitative trust, investment benefit and dashboard contract
Hard governance rule: no Committee influence on production signals, paper policy, or funded/live trading may be considered until quantitative trust is demonstrated prospectively under frozen acceptance rules. A green CI run or plausible model narrative is not trust evidence.
21.1 Quantitative trust dimensions
Dimension
Metric / evidence
Gate principle
Evidence integrity
Hindsight violations, missing lineage, release/SHA drift, corrupted identity
Zero integrity violations in the trusted cohort.
Coverage
Eligible cases vs actually evaluated; matured vs pending
High and stable; missing cases remain explicit.
Baseline comparison
Committee-right/O’Pip-wrong; O’Pip-right/Committee-wrong; both right; both wrong
Incremental information must be demonstrated on paired cases.
Trading economics
Committee-policy net result − deterministic baseline net result
Prospective incremental trading net must be positive.
Operating economics
Incremental trading net − AI/API/compute/data attributable cost
Prospective incremental operating net must be positive.
False intervention risk
Cases where O’Pip was right and Committee-derived policy would have harmed the result
Bounded and stable; never hidden inside aggregate accuracy.
Weakness precision
Validated weakness findings ÷ matured findings; rejected/inconclusive shown separately
Must be measured by category and time period.
Stability
Results by regime, strategy, pair/case class and independent time window
Value must not depend on one tiny or cherry-picked slice.
Role/model reliability
Incremental correctness/economic contribution, failures, latency and cost by role/model
Useful roles/models identified; noisy/expensive routes remain visible.
Learning effectiveness
Recurrence and economic loss before vs after an approved fix
Improvement must be observed after the change, not assumed.
21.2 Governing economic metrics
Incremental Trading Net = Committee-derived research-policy economic result − deterministic O’Pip baseline economic result.
Incremental Operating Net = Incremental Trading Net − attributable AI/API/compute/data/operating cost.
Comparisons must use matched candidate population, capital, timing, execution model, fees/slippage/liquidity assumptions and capital occupancy. Directional accuracy alone is insufficient.
Unknown cost, missing execution evidence or incomplete population coverage is never converted to zero or treated as favorable.
21.3 Trust stages
Stage
Meaning
Authority
T0 — UNTRUSTED / SHADOW
Committee observes, records findings and accumulates prospective evidence.
Zero influence on signals or trades.
T1 — MEASURED
Enough matured prospective cases exist to calculate baseline comparison, weakness validation, costs, latency and economic contribution.
Measurement only.
T2 — SIGNAL-ELIGIBLE REVIEW
Prospective net value, integrity, false-intervention risk and stability meet frozen statistical/economic gates.
May be reviewed for advisory signal use; no automatic influence.
T3 — PAPER-INFLUENCE ELIGIBLE REVIEW
A preregistered paper experiment demonstrates repeatable benefit of a Committee-informed policy under matched economics.
Separate human approval required for any paper-policy influence.
T4 — LIVE ELIGIBILITY REVIEW
Repeated prospective evidence supports net benefit, stability, operational reliability and controlled downside.
Still no automatic activation. Funded/live use requires separate architecture, risk and owner approval.
21.4 Mandatory Dashboard/Cockpit metrics
The Dashboard must display authoritative Committee metrics produced by the Committee/learning semantic layer. The UI must not invent, infer or recompute trust, profitability or weakness-validation logic.
Dashboard panel
Mandatory content
Trust
Current trust stage; prospective sample/matured counts; evidence integrity; statistical/economic gate status; insufficiency reasons.
Economic Value
Baseline net; Committee-policy net; incremental trading net; AI/operating cost; incremental operating net; avoided losses; missed gains recovered where valid.
Committee vs O’Pip
Committee-right/O’Pip-wrong; O’Pip-right/Committee-wrong; both-right; both-wrong; false-intervention rate.
Weaknesses
Findings by category; validated/rejected/inconclusive; recurrence; economic impact; unresolved high-impact weaknesses.
Learning
Weakness → hypothesis → registered experiment → conclusion → approved release SHA → post-change effectiveness.
Roles & Models
Useful weakness discovery, paired-case contribution, provider/model failures, latency, tokens and cost.
Stability
Performance/value by regime, strategy, pair/case class, model/policy version and independent time window.
Paper linkage
Every aggregate drills to the exact paper trade, DecisionContext, Committee evidence, finding, outcome and economic attribution.
21.5 Required drill-down and progress report
Drill-down chain: metric → Committee case → paper trade → original O’Pip decision → role/model opinion → weakness finding → matured outcome → validation → economic effect → hypothesis/experiment → approved fix → post-fix evidence.
Before the full Dashboard exists, the learning plane must emit a durable Committee Trust Report after each evaluation cycle with at least: eligible/prospective/matured case counts; four-way Committee-vs-baseline counts; incremental trading net; AI/operating cost; incremental operating net; weakness finding/validation counts; recurring/resolved weakness counts; post-fix verified improvements; current trust stage; and explicit reasons for INSUFFICIENT_EVIDENCE or any blocked gate.
21.6 Intelligence Committee completion acceptance
Role-based Committee is operational with governed role → provider/model → prompt/schema/budget routing; generic provider duplication is not sufficient.
Real provider transports, bounded fallbacks, cost/deadline/concurrency controls and shadow-mode enforcement are operational without placing model calls in the trading critical path.
Paper-trade observer consumes committed canonical/replica evidence and links findings to DecisionContext and paper_trade_id.
Weakness Finding Registry, validation lifecycle, recurrence, economic impact, hypothesis/experiment linkage and post-change measurement are durable and tested.
Prospective T0/T1/T2 anti-hindsight protocol is operational and release/SHA compatibility is proven.
Matched economic comparison against deterministic O’Pip baseline exists; directional accuracy alone does not satisfy completion.
Learning is visibly progressing from findings to validated weaknesses to experiments and measured post-change outcomes.
Committee remains advisory/shadow unless and until a separate governed promotion decision is explicitly approved.
21.7 v1.4.3 change note
v1.4.3 freezes the Intelligence Committee as a paper-trade monitoring, weakness-discovery, quantitative-trust and measurable-learning system. It also freezes the Committee metrics that Cockpit v2 must later expose. This change does not authorize funded/live trading, automatic signal influence, automatic promotion, Paper-v2 activation, or any relaxation of deterministic runtime authority.