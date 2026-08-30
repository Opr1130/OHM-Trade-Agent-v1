# O'Pip Signal Quality v2 — Consolidated Multi-Model Decision

Status: CONSOLIDATED DESIGN DECISION — architecture only; no production behavior change

## 1. Consolidated verdict

APPROVE WITH REQUIRED CHANGES.

The multi-model reviews converge on the same core conclusion: the product objective is correct, but v2 should be simplified and hardened before implementation. The target remains high-quality actionable signals, broad silent surveillance, global opportunity comparison, and Kraken-first position protection.

## 2. Key synthesis decisions

### Decision A — One synchronous Trade Quality Assessment, two logical sub-assessments

Keep Continuation and Entry conceptually separate because a move can be likely to continue while the current entry is poor. Do not implement them as independent asynchronous engines or queues.

Use one immutable point-in-time FeatureSnapshot and one synchronous TradeQualityAssessor:

FeatureSnapshot -> ContinuationAssessment -> EntryAssessment -> TradePlanAssessment

Both sub-assessments use the exact same snapshot/version/timestamp. If entry needs fresher evidence, the whole assessment is recomputed from a new snapshot rather than carrying forward stale continuation state.

### Decision B — No probabilistic EV ranking before calibration

V2 starts with deterministic, versioned Opportunity Utility. Inputs include:

- continuation quality
- entry quality
- target quality / net reward-to-risk
- liquidity / expected slippage
- volatility-normalized extension/exhaustion
- evidence quality
- market regime
- portfolio concentration
- opportunity cost

Raw model scores are never treated as probabilities.

Only after purged out-of-time calibration may the system compute probability-based expected utility.

### Decision C — Regime-aware normalization, not ATR-only rules

Replace regime-blind percentage thresholds where they affect signal quality, but do not force every policy threshold into ATR units.

Use a regime context combining:

- ATR / ATR percentile
- realized volatility percentile
- BTC/ETH market regime
- sector breadth
- liquidity/depth state
- asset-specific historical distribution

Safety/operational thresholds such as data freshness, identity validity, maximum spread policy, and evidence availability can remain explicit fixed/versioned gates where appropriate.

### Decision D — Kraken Exposure Resolver is the sole authority for exposure existence

Kraken read-only account truth is authoritative.

Create one ExposureResolver output consumed by protection and reconciliation. Local active-trade state may enrich lifecycle context but cannot establish that a real holding exists.

Only one component owns lifecycle terminalization after verified Kraken absence. Monitoring components consume the resolved exposure state; they do not independently close/skip using conflicting rules.

Kraken unavailable => UNKNOWN / MONITORING_DEGRADED, never assumed flat.

### Decision E — Silent discovery survives; direct watch-alert delivery does not

Keep useful Early Watch / Broad Watch discovery features as internal candidate evidence.

Remove the direct user-facing Telegram delivery path for EARLY WATCH / READY / DEVELOPING / BROAD WATCH.

All discovery channels feed the same bounded monitoring queue and compete through the same Trade Quality Assessment and global ranker.

### Decision F — One user-facing action gate

Core decision state must not live in Telegram/notifier modules.

Decision -> ActionDecision -> NotificationPolicy -> Telegram transport.

NotificationPolicy owns suppression/dedup/reservation. Telegram is a stateless delivery consumer.

Use an atomic reserve/send/confirm-or-release pattern so concurrent callers cannot double-send the same action.

### Decision G — Event/news/whale/order-flow evidence is contextual, not a direct trade trigger

Positive news/event evidence cannot independently create an actionable trade.

Market confirmation is required through available price/volume/order-flow/liquidity evidence.

Cross-venue evidence is a strong modifier when available, but absence of cross-venue coverage must not automatically reject a valid Kraken-only opportunity.

Distinguish:
- NO_RELEVANT_EVENT: neutral
- EVENT_PROVIDER_UNAVAILABLE: degraded evidence quality
- SUPPORTIVE_EVENT + MARKET_CONFIRMATION: positive modifier
- CONTRADICTORY_EVENT / NEGATIVE_EVENT: downgrade or veto depending on severity

### Decision H — Bounded monitoring queue

The silent queue must have:

- canonical candidate identity
- dedupe
- TTL / expiry
- priority
- freshness requirements
- bounded retention
- idempotent updates
- re-evaluation on scheduled market refresh and material new evidence

No queue state is user-facing.

### Decision I — Outcome labels are multi-dimensional

T1-before-stop remains one useful label but is insufficient alone.

Persist at minimum:

- T1/T2/T3-before-stop
- MFE
- MAE
- realized fee/slippage-adjusted return
- time-to-entry
- time-to-target
- time-under-water
- MFE giveback
- capital holding time
- opportunity rank at decision
- discovery lead time

Classify outcomes such as EXPLOSIVE_WINNER, EFFICIENT_WINNER, SLOW_WINNER, FAILED_CONTINUATION, STOPPED_LOSER, EXPIRED_NO_ENTRY.

Probability language remains forbidden until point-in-time leakage controls and out-of-time calibration pass.

## 3. P0 — blocking architecture changes

1. Implement one synchronous TradeQualityAssessor over one immutable FeatureSnapshot; keep ContinuationAssessment and EntryAssessment logically separate but not independently scheduled.
2. Replace probability/EV-based pre-calibration ranking with deterministic Opportunity Utility normalized for volatility, liquidity, exhaustion and evidence quality.
3. Introduce canonical regime context and remove regime-blind fixed percentage thresholds from continuation/entry/protection decisions where inappropriate.
4. Establish Kraken-first ExposureResolver as the only authority for exposure existence and assign exactly one owner for terminalization on verified absence.
5. Remove direct Early Watch/Broad Watch/READY Telegram delivery; retain their detectors as silent discovery inputs.
6. Centralize user-facing alert eligibility through ActionDecision + NotificationPolicy; Telegram cannot own lifecycle state. Make notification reservation atomic.
7. Enforce market confirmation for positive event/news promotion and explicitly distinguish missing evidence from neutral evidence.
8. Define multi-dimensional outcome labels and prohibit probability rendering until calibrated out-of-time.

## 4. P1 — required before broad production cutover

1. Broaden discovery incrementally across leaderboard/rank velocity, pre-breakout compression, relative-volume anomaly, pullback continuation, order-flow/whale and event-driven channels.
2. Implement bounded/deduped silent monitoring queue with TTL, priority, freshness and idempotent updates.
3. Add global deterministic ranking across all actionable candidates, including liquidity capacity, concentration, correlation proxy and opportunity cost.
4. Add existing-position deterioration intelligence: MFE giveback, relative-strength decay, regime change, liquidity degradation, thesis deterioration and capital-time cost.
5. Wire Event Intelligence into the canonical feature snapshot; keep event sources point-in-time and revision-aware.
6. Build calibration/outcome store with model/feature/strategy versioning and purged walk-forward validation.
7. Add scheduler/candidate-state idempotency and race tests across discovery and qualification.
8. Make degraded protection state observable when Kraken or Telegram dependencies are unavailable.

## 5. P2 — defer until evidence justifies complexity

1. Historical analogue scoring. Keep raw episode data now; activate analogue influence only after calibration labels and leakage controls are mature.
2. Full probabilistic expected-value ranking.
3. Full covariance/portfolio optimizer. Start with deterministic correlation/concentration penalties.
4. Advanced multi-model/ML continuation probability. First prove deterministic signal-quality lift in shadow.
5. Broad module cleanup unrelated to measurable signal quality or operational resilience.

## 6. Review recommendations explicitly modified or rejected

### Do not fully merge continuation and entry semantics

Rejected as a semantic merge. Accepted as a runtime simplification.

A candidate may have strong continuation but no valid current entry. The system must preserve that distinction. The fix for stale state is same-snapshot synchronous evaluation, not deleting the distinction.

### Do not make ATR the sole normalization mechanism

ATR is necessary but insufficient. Regime-relative logic uses ATR plus realized volatility, liquidity, market/sector regime and asset distribution.

### Do not require cross-venue evidence as a universal hard gate

Cross-venue confirmation improves confidence when available. Requiring it universally would reduce recall on Kraken-only or poorly covered assets.

### Do not perform a big-bang scanner rewrite

Discovery channels are migrated incrementally into the silent queue and shadow-ranked before alert cutover.

### Do not undertake cleanup for cleanliness alone

State/scheduler/module consolidation is performed only where it prevents duplicate alerts, stale state, missed candidates, unprotected holdings, calibration errors or materially worse capital allocation.

## 7. Revised target path

Kraken eligible universe
  -> multiple lightweight discovery detectors
  -> bounded silent candidate queue
  -> canonical point-in-time FeatureSnapshot
  -> synchronous TradeQualityAssessor
       -> ContinuationAssessment
       -> EntryAssessment
       -> TradePlanAssessment
  -> deterministic Global Opportunity Ranker
  -> portfolio/capital utility gate
  -> ActionDecision
  -> NotificationPolicy
  -> NEW TRADE SIGNAL

Existing holdings:

Kraken account truth
  -> ExposureResolver
  -> lifecycle context attachment
  -> PositionQualityAssessment
  -> HEALTHY / DETERIORATING / RECOVERING / PROFIT_PROTECT / EXIT_REVIEW / INVALIDATED / UNKNOWN
  -> ActionDecision only when attention/action is justified
  -> NotificationPolicy
  -> EXISTING TRADE ACTION

Both flows -> canonical outcome path -> learning/calibration.

## 8. Production acceptance gates

No production alert cutover until:

- no informational watch alert can reach Telegram
- every NEW TRADE SIGNAL came through the unified ranker and contains entry/stop/targets/do-not-chase
- Kraken-first resolver accounts for every verified holding or explicitly reports UNKNOWN/degraded
- stable local state cannot create an exposure that Kraken does not confirm
- no uncalibrated probability language can render
- high-volatility/low-liquidity synthetic candidates do not dominate ranking purely due to expected move
- future major movers can be discovered by at least one non-leaderboard channel in replay tests
- positive news alone cannot promote a candidate
- existing-position slow deterioration and MFE giveback can trigger review without requiring a sharp price shock
- shadow V2 materially improves actionable precision without unacceptable major-mover recall loss
- rollback returns to the prior production decision path without corrupting candidate/position state

## 9. Implementation principle

Do not optimize for architectural elegance. Every change must improve at least one of:

- signal precision
- major-mover recall
- entry timing
- capital efficiency
- existing-position protection
- calibration integrity
- operational resilience

If a proposed refactor does not move one of these, defer it.